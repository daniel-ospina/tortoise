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
page's behaviour is EXECUTED by `tests/test_oauth_consent_page_step4.py` (the rendered page's inline
script under node, against a fetch/DOM shim — §9 step 4 deleted the supabase client this line used to
name), and the BFF's by the Pages gates. A guard that cannot fail is not a guard, so each one asserts
it found its subject.

⛔ ONE SECTION IS NOT STATIC — `§2 / §9 step 2, EXECUTED` at the end of this file. The sections
above pin WHERE the contract is written; that one pins THAT IT IS ENFORCED, because §9 orders the
AS half (`POST /oauth/consent` accepting a signed, bound, single-use token) to land before any
page change, and a verifier nobody exercises is the "specified, not implemented" state #3524 was
reopened for. It drives the real `hosted_api` app over the OAuth suite's `FakeControlPlane`: one
refused request per guarantee in §2.2 (`aud`, `iss`, `exp`, `bind`, `jti`), plus the two arms
proven independent of each other — the bearer path must stay live (§9: step 2 is additive), and
the token arm must never need it.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException

# The OAuth suite's own harness — the SEEDING helpers and the app, reused rather than re-derived:
# a second control plane seeded differently from the suite the endpoint ships with is a second
# thing that can be wrong. The fixtures themselves are local because a pytest fixture's
# dependencies resolve in the module that DECLARES the test, not the one that declares the fixture.
from tests._http_fixtures import patched_tortoise_sdk
from tests._verdict import verdict_client
from tests.fake_control_plane import FakeControlPlane
from tests.test_oauth_mcp import (
    _U1,
    REDIRECT,
    TEAM_FREE,
    TEAM_TEAM,
    TEST_BASE,
    _enable_supabase,
    _exchange,
    _member,
    _pkce,
    _register_client,
)
from tortoise.consent_token import mint_consent_token
from tortoise.hosted_api import app

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

#: property → (file, the token that carries it) as it was on the PAGE. §9 step 4 DELETED all four page-side
#: implementations (§7's deletion set), so these are asserted ABSENT — a property that drifted back into the
#: page would mean a second identity system again, which is the whole of what step 4 removed.
_RETIRED_PAGE_PROPERTIES = {
    "single-flight": "if (previewInFlight) return previewInFlight;",
    "refresh-once": "let staleRefreshes = 0;",
    "never-sign-out": "NEVER call the auth logout API here",
    "auto-advance": "supabaseClient.auth.onAuthStateChange(",
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
    their citation block. Until §9 step 4 the page carried its own copy of all four; step 4 deleted
    them, so the LIVE implementation is the BFF one and the page-side token must be ABSENT. Both
    halves are asserted: the BFF anchor must exist, and the page must not have reclaimed it — a
    property that drifted back into the page is a second identity system, which is exactly what
    step 4 removed.
    """
    page = OAUTH.read_text()
    for prop, token in _RETIRED_PAGE_PROPERTIES.items():
        assert token not in page, (
            f"the consent page carries {prop!r} again ({token!r}) — §9 step 4 deleted the page's own "
            "implementation of all four properties, and re-introducing one restores the second "
            "identity system the bridge exists to remove"
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
    """Task 5's origin must stay ACCOUNTED FOR — now as a recorded NON-requirement.

    The consent page used to sign in with `redirectTo = window.location.origin +
    /oauth/authorize`, so `api.premiselabs.co` had to be in the GoTrue redirect
    allowlist or provider sign-in on the only working auth flow was refused. §9
    step 4 removed the page's sign-in entirely, so the entry is no longer
    required — and the failure this guard prevents has simply changed shape: an
    operator reading a STALE "required" row, or a later lane deleting the row
    with no note, is the same unrecorded state in the other direction.

    So the record must still ADDRESS the origin, and it must say which it is:
    the row is present AND marked a non-requirement (a bare "required" row is
    now false; a deleted row is a silent reversal of task 5's finding).
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
        "the consent page's former sign-in origin (https://api.premiselabs.co/oauth/authorize) is "
        "absent from the redirect-allowlist record. §9 step 4 made it a NON-requirement rather "
        "than a dependency — record that, do not delete the row silently"
    )
    rows = [line for line in table.splitlines() if "api.premiselabs.co" in line]
    assert rows, table
    assert any("NO LONGER REQUIRED" in row for row in rows), (
        "the record still presents api.premiselabs.co as a REQUIRED redirect entry — the consent "
        "page performs no sign-in since §9 step 4, so the row is a non-requirement and must say "
        "so (task 5's finding is answered by the record, not erased by it)"
    )


# ── §7 — the deletion set: DELETED, and asserted absent ─────────────────────

#: §7's deletion set, as the tokens that WERE the items. §9 step 4 deleted every one of them, so this
#: table is now an ABSENCE assertion (the row markers are gone with the anchors, which is why the
#: tuple shape changed from `<row marker>, <token>` to just `<token>`: there is no line number left to
#: recompute, and a citation of a deleted line is the one thing this guard must not accept).
#:
#: WHY AN ABSENCE TEST AND NOT A DELETED TEST: each of these looked like a feature (a session adapter,
#: a capability probe, a retry affordance). The whole point of §7 was that the ordering mattered — the
#: page could not be half-migrated — so the durable guard is "it is gone from the page", which reds the
#: moment a later change re-introduces one instead of quietly re-creating the second identity system.
_DELETED_PAGE_ARTEFACTS = [
    "const auxStores = () => {",
    'const COOKIE_NAME = "sb-tortoise-auth-token";',
    'const COOKIE_PATH = "/";',
    'const COOKIE_DOMAIN = ".premiselabs.co";',
    "const SIZE_GUARD = 3800;",
    "const SIZE_CAP = COOKIE_LIMIT",
    'document.cookie = key + "=" + encoded',
    'document.cookie = key + "=;"',
    "window.supabase.createClient(",
    "function pkceIncapable() {",
    "supabaseClient.auth.onAuthStateChange(",
    # §7's other rows, same fate: the sign-in handlers and the fragment sanitiser.
    'id="btn-github"',
    'id="btn-google"',
    "signInWithPassword(",
    "function sanitiseUrl() {",
    "function showTerminalFallback() {",
]


def test_the_deleted_page_artefacts_are_GONE() -> None:
    """§7's deletion set is DELETED — recomputed from the source, not trusted.

    Before step 4 this guard obliged §7's rows to cite the line each item ACTUALLY lived on, because
    the table had been hand-remapped wrong twice (once advancing a block by +171, once rewritten with
    the PR BASE's numbers). That obligation is discharged by the deletion itself: there is no line
    left to cite, so the same check now runs in the only direction that is still falsifiable — the
    token must be ABSENT, and §7 must SAY it is deleted.

    The spec half is asserted too, because a deletion set that still reads as a to-do list is how a
    later lane "restores" something that was removed on purpose (each item looked like a feature).
    """
    page = OAUTH.read_text()
    present = [token for token in _DELETED_PAGE_ARTEFACTS if token in page]
    assert not present, (
        "§7's deletion set is back in the consent page: " + "; ".join(present) + " — §9 step 4 "
        "deleted these wholesale, and each one is a piece of the SECOND identity system the bridge "
        "replaced. If a real need brought one back, it belongs on the server path, not in the page."
    )

    section = SPEC.read_text().split("## 7. Deletion set", 1)
    assert len(section) == 2, (
        "the spec no longer has a '## 7. Deletion set' section — the record of WHAT was deleted and "
        "why each item was load-bearing is the part a future reader is most likely to undo"
    )
    section_text = section[1].split("## 8.", 1)[0]
    assert "DELETED by #3524 §9 step 4" in section_text, (
        "§7 no longer records that the deletion set was DELETED — it would read as outstanding work"
    )
    assert "tests/test_oauth_consent_page_step4.py" in section_text, (
        "§7 no longer names the guard that keeps the deletion deleted"
    )


# ── The spec's `hosted_api.py` citations: SYMBOLS, never line numbers ───────

#: Every form this tree writes a `hosted_api.py` LINE citation in: `:39152`, `#L39152`, a
#: space-separated `L39152`, `:L39152`, the prose `line 39152`, a space or a newline after the
#: separator (CommonMark renders a wrapped code span as one space), the fullwidth colon, an HTML
#: entity for the dot, a zero-width joiner before the digits, and the bare `:<n>` CONTINUATION the
#: §1 table uses for `session_auth.py:588` → `` (`:600`) ``. Each was green under some earlier
#: version of this guard (measured), and each was green for the same reason: the guard normalised
#: ONE spelling of one token at ONE level of the document.
#:
#: ⛔ SO THERE IS NOW EXACTLY ONE NORMALISER AND ONE SCAN. `_fold_doc` turns the WHOLE document
#: into one text — blockquote markers stripped the way the block parser strips them, HTML entities
#: decoded, NFKC-folded, zero-width/combining characters dropped WITHOUT a word break, whitespace
#: runs folded to one space, the fullwidth colon folded to ASCII — while keeping, per character,
#: the RAW LINE and TWO boundaries: the BLOCK (blank-line-separated paragraph or table, or a list
#: item) and the RUN (blank-line-separated region, list items included). Every rule is then
#: decided by POSITION on that single text:
#:
#:  * an explicit anchor inside the provenance note may only quote `_NOTE_QUOTED_ANCHORS`;
#:  * anywhere else it is a violation;
#:  * a bare anchor carries a note violation when it falls inside the note, and otherwise belongs
#:    to the nearest FILE TOKEN in its RUN — compared through `_file_basename`, which strips a
#:    regex escape and folds the case, so `tortoise/hosted_api.py`, `HOSTED_API.PY` and
#:    `hosted_api\.py` attribute like the plain name.
#:
#: ⛔ THE TWO BOUNDARIES ARE NOT INTERCHANGEABLE: a BOUNDARY MAY FILTER A MATCH'S SPAN, BUT IT MAY
#: NEVER REMOVE THE OWNER A READER WOULD INFER (#9485 review). One primitive doing both jobs is how
#: the evasion arrived — the block opened at a list item took the owner away from every anchor
#: written inside one, so `1. `:99998`` under a `hosted_api.py` paragraph was GREEN while the same
#: number with no marker was RED.
#:
#: Two pre-sliced texts with two normalisations was the defect: an anchor is a TWO-TOKEN
#: construct, so any split the slice boundary fell inside was invisible to both halves — a new
#: anchor wrapped across the note's own boundary was green while the failure message said it was
#: caught (measured).
#:
#:    ⛔ The owner token is ANY extension, NOT `*.py`: a `:<n>` belonging to a `.ts`/`.yml`/`.md`
#:    cited in the same table row (or paragraph) as a `hosted_api.py` mention was reported as a
#:    `hosted_api.py` anchor, i.e. an honest citation redded with a message naming a file the
#:    editor did not touch (§10's `Bindings` row after the `hosted_api.py` rows — measured).
_HOSTED_ANCHOR_RE = re.compile(
    r"hosted_api\\?\.py\s*(?::\s*(?:#?L)?|#?L|line)\s*(\d+)", re.IGNORECASE)
#: The NUMBER of a line anchor, in every spelling this doc family writes it: the bare `:<n>`
#: continuation, `#L<n>` / `L<n>` in their own code span (`` `hosted_api.py`, `L39432` `` — the
#: natural way to re-add one here, and green until this arm existed), and the prose `line <n>`.
#:
#: ⚠️ The `L` arms require TWO digits. Not because one digit is ambiguous HERE — this module
#: carries no `L<n>` of its own — but because in this TREE a one-digit `L1`/`L2` is a LAYER's
#: name (`projection/__init__.py` — `L1` the per-call opt-in, `L2` the non-test-graph check;
#: `commit_schema.py` — `L1` replay, `L2` adjudication), and a guard that redded the next
#: document to borrow that spelling would be reporting a label as a citation. No real anchor is
#: lost: `hosted_api.py` starts at line 1, so `L10` and up still reds, and the lookbehind keeps a
#: letter-prefixed token (`FAIL12`) out. The prose `line` arm carries the same two-digit floor,
#: for the same reason.
#:
#: ⚠️ `line` takes ZERO OR MORE spaces, not one or more — in BOTH arms. `:`/`#L`/`L` always
#: tolerated none, so they survived `_fold_doc` DELETING a zero-width character instead of
#: gapping it; `line\s+` did not, and `line\u200b39432` folded to the bare word `line39432` and
#: matched nothing (a regression the previous fold's `gap = True` had accidentally covered —
#: measured). Whitespace tolerance is the rule here everywhere else, so the separator is the one
#: place it should not have been tighter. Cost, measured: the widened arm also matches prose of
#: the form `agenda line45`, which reds only when a `hosted_api.py` token governs that block —
#: i.e. only where the number is being attributed to `hosted_api.py` anyway.
_BARE_ANCHOR_RE = re.compile(
    r":\s*(\d+)|(?<![\w#])#?L\s*(\d{2,})|(?<![\w])line\s*(\d{2,})", re.IGNORECASE)
#: The nearest FILE TOKEN to the LEFT of a bare anchor, which is what the continuation
#: belongs to. It is matched WHOLE (it may carry a directory) and compared by BASENAME, so
#: `tortoise/hosted_api.py` attributes its continuation exactly like `hosted_api.py` does. A
#: file-name scan rather than "the line mentions hosted_api.py somewhere": §10's
#: `Redemption` row names `hosted_api.py` AND carries `session_auth.py:588`, so the loose rule
#: reported the row's legitimate anchor to a DIFFERENT file (measured).
#:
#: ANY extension, not `*.py` (the same defect one level down): a table is one block, so a `:<n>`
#: belonging to a `.ts`/`.yml` cited at-or-after a `hosted_api.py` row was attributed to
#: `hosted_api.py` — a false red on an honest edit, naming a file the editor never opened
#: (measured: §10's `Bindings` row `config/required-bindings.yml` `:12`). The nearest file-like
#: token is the owner whatever its extension; only a `hosted_api.py` ownership reds, so widening
#: the token set can only REMOVE reports, never add one.
#:
#: In the BODY the scan is per RUN — not per line, and not document-wide: a continuation whose
#: governing `hosted_api.py` sits on an earlier line of the same paragraph or table IS attributed,
#: and one separated by a blank line is not. Same-LINE-only was a hole — `` …`hosted_api.py` ``
#: then `` `:39205` `` reads clean, yet CommonMark joins the pair into one sentence (measured).
#: ⚠️ The RUN, not the BLOCK: a list item interrupting a paragraph starts a new block (so a match
#: may not span into it) but stays in the same run (so an anchor inside it keeps its owner) —
#: deriving ownership from the block was a one-token evasion of this whole guard (#9485 review).
#: ⚠️ The NOTE region is the exception and is deliberate: there the search is document-wide (a
#: bare number in the note is a citation even when the file that governs it sits in the paragraph
#: above), and an UNOWNED one flags too — see `_hits`, which is the only place either rule lives.
_NEAREST_FILE_RE = re.compile(
    r"[\w./-]+\\?\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|yml|yaml|json|jsonc|md|mdx|sh|bash|sql|toml"
    r"|cfg|ini|conf|html|css|txt|lock)",
    re.IGNORECASE)


def _file_basename(token: str) -> str:
    r"""`token`'s file name, case-folded, with any regex escape removed.

    The escape is the whole point: `_NEAREST_FILE_RE` tolerates `hosted_api\.py`, and comparing
    the RAW token left the backslash in it, so `hosted_api\.py` matched and then failed equality —
    the `\\?` was inert for the continuation arm while the explicit arm closed (measured). One
    normaliser, used by both arms, so they cannot disagree again.
    """
    return token.replace("\\", "").rsplit("/", 1)[-1].lower()


#: The start of a markdown LIST ITEM on a raw line, after whatever indentation and blockquote
#: markers the line carries. A list item is its OWN BLOCK even when no blank line separates it
#: from the paragraph that introduces it, and it is its own block **exactly when CommonMark says
#: it is**:
#:
#:   * an ORDERED item interrupts a paragraph only when it starts at `1` — `2.` and up are a lazy
#:     paragraph continuation (CommonMark's own example: `The number of windows in my house is` /
#:     `14.  The number of doors is 6.` is ONE paragraph). So the marker set is `1[.)]`, never
#:     `\d+[.)]`: accepting any digit INVENTED a block the parser does not see, which made
#:     ``… `hosted_api.py`, L`` + `12. first` GREEN — a coverage REGRESSION against `main`, whose
#:     fold kept `L 12` in one block and reported it (measured, #9485 review);
#:   * a BULLET (`-`, `*`, `+`) interrupts a paragraph unconditionally, so it is an opener too.
#:     Omitting it made the constant's own claim ("a list item is its OWN BLOCK") false for the
#:     class it names — it implemented ordered items only, the instance, not the class (#9485
#:     review).
#:
#: `_fold_doc` therefore opens a BLOCK here, the same way it opens one at a blank line — without
#: that, the fold produced `… in this order: 1. first` and the continuation arm reported `: 1` as a
#: `hosted_api.py` citation the editor never wrote (measured, #9485 review: the shape the
#: always-loaded note said could not be reported).
#:
#: ⚠️ And a block boundary is the SPAN FILTER's, NOT the owner rule's — see `_fold_doc`'s RUN.
_LIST_ITEM_RE = re.compile(r"^\s*(?:1[.)]|[-*+])(?:\s|$)")


def _fold_doc(raw: str) -> tuple[str, list[int], list[int], list[int]]:
    """The ONE normaliser: `(text, raw_line_of_each_char, block_of_each_char, run_of_each_char)`.

    Folds every spelling of an anchor to one shape and remembers where each character came from,
    so a violation can be reported at a real line while the RULES are decided on normalised text.
    Blockquote markers are stripped here rather than per caller, because that is what the block
    parser does before inline parsing — a code span wrapped across two `>` lines renders as one
    sentence, so an anchor split there is one anchor, not two fragments no rule can see.

    ⛔ TWO BOUNDARIES, TWO JOBS — they are not interchangeable, and confusing them is what the
    #9485 review found. A boundary may FILTER A MATCH'S SPAN, but it may never REMOVE THE OWNER a
    reader would infer:

    * a BLOCK is a blank-line-separated paragraph or table — OR a list item (see
      `_LIST_ITEM_RE`), which starts one with no blank line to announce it. Both are boundaries a
      markdown parser agrees with, so neither is an opinion this module invented. Blocks answer
      ONE question: may a match span this change? It may not, because the join is a folding
      artifact (`_hits` filter).
    * a RUN is a contiguous region between BLANK LINES — list items do NOT start a new run. Runs
      answer the OTHER question: what does this anchor belong to? A list item interrupting a
      paragraph is a new block but the same sentence's neighbourhood, so `` `hosted_api.py` `` +
      `1. `:99998`` still has an owner. Deriving ownership from BLOCKS instead deleted it, which
      was a one-token evasion of the guard's whole purpose: a reintroduced line number was
      invisible whenever its line was prefixed `1. ` (measured — RED at `99026d7f8`, GREEN at
      `49d62d3bd`, RED again here).

    The two are deliberately kept apart rather than collapsed: using RUNS for the span filter
    resurrects the false positive this module was fixed for (the colon ending `… in this order:`
    and the `1` of the next list item share a run, so `: 1` would match again), and using BLOCKS
    for ownership is the evasion above.

    ⚠️ Blocks are joined by ONE space, and that join is a FOLDING ARTIFACT, never a citation: a
    paragraph ending in a bare `hosted_api.py` would otherwise read as one token with a `:39205`
    opening the next paragraph (or the next list item). `_hits` therefore refuses a match in EITHER
    arm whose SPAN crosses a block change — the explicit arm because its `\\s*` eats the join, and
    the continuation arm because all three of its alternatives (`:`, `L`, `line`) do (measured:
    without it, `"Read hosted_api.py"` + a blank line + `":39205 names the handler."` was reported
    as a citation the editor never wrote — GREEN on `main`, which folded per block).
    """
    out: list[str] = []
    line_of: list[int] = []
    block_of: list[int] = []
    run_of: list[int] = []
    block, run, gap = 0, 0, False
    for index, line in enumerate(raw.splitlines()):
        stripped = re.sub(r"^\s*(?:>\s?)+", "", line)
        if not stripped.strip():
            # A blank line ends BOTH: the paragraph (block) and the ownership window (run).
            block += 1
            run += 1
            gap = True
            continue
        if _LIST_ITEM_RE.match(stripped):
            # A list item opens a BLOCK with no blank line to announce it, so the previous line's
            # `:`/`L`/`line` opener must not reach into it (`gap` is already True: the join is
            # still ONE space, it is the SPAN filter that refuses it). The RUN does NOT move: the
            # item is still the neighbourhood a bare anchor in it belongs to.
            block += 1
        for char in unicodedata.normalize("NFKC", html.unescape(stripped)):
            # `Cf` covers every zero-width form this doc could carry (space, joiners, BOM) and `Mn`
            # a combining mark, and NFKC has already turned the fullwidth colon into `:`, so this
            # branch is the whole of the rule and there is no second list to drift out of date.
            #
            # ⚠️ DROPPED, never GAPPED. A zero-width character INSIDE the file name is exactly how a
            # citation survives a paste — `hosted_api.py` carrying a soft hyphen, a ZWJ or a
            # word-joiner — and it still RENDERS as `hosted_api.py:39205`. Treating it as a gap
            # re-split the token this fold exists to rejoin, so all five spellings were GREEN (the
            # `_EVASION_SHAPES` rows below pin each one). Whitespace is the only thing that gaps;
            # a combining mark never separates two words, so it needs no gap either.
            if unicodedata.category(char) in ("Cf", "Mn"):
                continue
            if char in " \t":
                gap = True
                continue
            if gap and out:
                out.append(" ")
                line_of.append(index + 1)
                block_of.append(block)
                run_of.append(run)
            gap = False
            out.append(char)
            line_of.append(index + 1)
            block_of.append(block)
            run_of.append(run)
        gap = True
    return "".join(out), line_of, block_of, run_of


def _collapse(text: str) -> str:
    """`_fold_doc`'s text without the lineage — for callers that only need the shape."""
    return _fold_doc(text)[0]


#: The ONLY anchors the `#3524` provenance note may quote — the stale issue body it quotes.
#: Compared as a SUBSET, never by equality: pruning a quotation that no longer needs quoting has
#: to stay legal (the same contract `test_auto_advance_status_matches_the_route_artifact` states —
#: a guard must not make the honest edit the expensive one), while ADDING one is a new citation.
_NOTE_QUOTED_ANCHORS = frozenset({"23409", "23284"})


def _hits(
        raw: str, note_start: int, note_end: int,
) -> tuple[list[tuple[str, int, bool]], list[tuple[str, int, bool]]]:
    """`(explicit_anchors, bare_anchors)` as `(token, raw_line, is_in_note)`.

    ONE scan over ONE folded text, and the ONLY place either rule is decided; the public helpers
    below format its result for their callers. Splitting the decision out is what stops the arms
    drifting: every previous round's defect was one arm normalising or bounding differently from
    the other.

    The owner rule differs by region, deliberately:

    * in the BODY, a bare anchor belongs to the nearest file token in its own RUN — the
      blank-line-delimited neighbourhood `_fold_doc` tracks alongside the block. NOT its own
      BLOCK: a block boundary exists to refuse a span, and letting it also decide ownership
      removed the owner a reader infers from the paragraph a list item interrupts, which was a
      one-token evasion while its line was prefixed `1. ` (measured, #9485 review). The RUN is
      the same rule stated without that leak — and it still stops at a BLANK line, which is what
      keeps `"The base is in `hosted_api.py`.\n\nAnd `:39205`."` legal, exactly as `main` did;
      * in the BODY, the anchor is flagged only when that owner is `hosted_api.py`. §10's
      `Redemption` row names `hosted_api.py` AND carries `session_auth.py:588`, so the loose
      "any file in the block" rule reported that row's legitimate anchor to a different file
      (measured);
    * in the NOTE, the search is document-wide and an UNOWNED anchor is also a violation — the
      note exists to quote anchors, so a bare number in it is a citation even when the file name
      that governs it sits in the paragraph above (measured green before). A number owned by a
      DIFFERENT file stays legal, because the note legitimately quotes `oauth.py:1425,1592`.
    """
    text, line_of, block_of, run_of = _fold_doc(raw)
    in_note = [note_start < line <= note_end for line in line_of]
    anchors: list[tuple[str, int, bool]] = []
    explicit: list[tuple[int, int]] = []
    for match in _HOSTED_ANCHOR_RE.finditer(text):
        if block_of[match.start()] != block_of[match.end() - 1]:
            # The explicit arm's `\s*` reaches across the single space that joins two BLOCKS in
            # the folded text, so `hosted_api.py` ending one paragraph and `:39205` opening the
            # next would read as one citation — a false red naming a file the editor never
            # opened. The continuation arm below refuses the same span crossing, so the two arms
            # cannot disagree about where a block ends (#9485 review).
            continue
        explicit.append(match.span())
        anchors.append((match.group(0), line_of[match.start()], in_note[match.start()]))
    bare: list[tuple[str, int, bool]] = []
    for match in _BARE_ANCHOR_RE.finditer(text):
        if block_of[match.start()] != block_of[match.end() - 1]:
            # ⛔ FILTER THE SPAN, not just the owner (#9485 review). Every alternative here has
            # its own `\s*`, so a colon (or an `L`/`line`) ending one BLOCK and the digits
            # opening the next matches across the join `_fold_doc` inserts — e.g. a paragraph
            # ending in a bare `hosted_api.py` whose next paragraph is an ordered list, where the
            # colon carries NO digits at all. The owner search then finds `hosted_api.py` in the
            # colon's own block and reports "bare continuation ': 1' belongs to `hosted_api.py`"
            # for a citation the editor never wrote — the false red `_fold_doc` exists to prevent.
            # Filtering is lossless: `main` folded per block, so it could not form this match
            # either, and every real hit lives inside ONE block.
            continue
        if any(start <= match.start() < end for start, end in explicit):
            # The number INSIDE an explicit anchor is that anchor, not a continuation.
            continue
        note = in_note[match.start()]
        run = run_of[match.start()]
        owner = None
        for candidate in _NEAREST_FILE_RE.finditer(text, 0, match.start()):
            # BODY: the ownership window is the RUN, not the block (see the docstring). NOTE: the
            # window is the whole document, and staying in the same run is not enough there.
            if note or run_of[candidate.start()] == run:
                owner = candidate.group(0)
        is_hosted = owner is not None and _file_basename(owner) == "hosted_api.py"
        if is_hosted or (note and owner is None):
            bare.append((match.group(0), line_of[match.start()], note))
    return anchors, bare


def _anchor_violations(
        raw: str, note_start: int, note_end: int,
) -> tuple[list[str], list[str], list[str]]:
    """`(new anchors in the note, bare anchors in the note, stray anchors outside it)`.

    Decided by POSITION on one folded text: a violation belongs to the note when the character it
    starts on came from a note line, and to the body otherwise. That is what makes a NEW anchor
    added across the note's own boundary visible — the previous shape sliced the document first
    and then folded each half, so the split itself was a blind spot (measured: the note gained
    `` `hosted_api.py: `` at end of line with its digits on the next quoted line, and every
    assertion stayed green while the message claimed that exact shape was caught).
    """
    anchors, bare = _hits(raw, note_start, note_end)
    note_new = sorted({_anchor_digits(token) for token, _, note in anchors
                       if note and _anchor_digits(token) not in _NOTE_QUOTED_ANCHORS})
    note_bare = sorted({token for token, _, note in bare if note})
    stray = [f"line {line}: {token!r}" for token, line, note in anchors if not note]
    stray += [f"line {line}: bare continuation {token!r} belongs to `hosted_api.py`"
              for token, line, note in bare if not note]
    return note_new, note_bare, stray


def _anchor_digits(token: str) -> str:
    """The number inside an explicit anchor token, e.g. `hosted_api.py:23409` → `23409`."""
    match = _HOSTED_ANCHOR_RE.search(token)
    return match.group(1) if match else ""


def _stray_anchors(lines: list[str], note_start: int, note_end: int) -> list[str]:
    """The violations outside the note, each reported at the line it is REALLY on.

    ⛔ This returns the BODY bucket only, so it is not on its own a guard on the document: a
    violation that STARTS inside the note is dropped here by construction. A caller that means to
    guard the whole spec must call `_anchor_violations` on the whole document and assert all THREE
    buckets. Pairing this helper with a note SLICE is what made the note's boundary a blind spot
    one edge later — an anchor opening on the note's last line and closing just below it was
    classified as a note violation by position and then discarded by both halves (measured: the
    whole-document call returned `(['39286'], [], [])` while the guard stayed green).
    """
    return _anchor_violations("\n".join(lines), note_start, note_end)[2]


def _bare_continuations(raw: str, note_start: int, note_end: int) -> list[str]:
    """Every bare anchor in `raw` the guard flags, decided on the region the CALLER names.

    Kept as a named helper because the note tests assert on this arm ALONE. `_hits` remains the
    only place the rule is written, so this cannot drift from it — but the REGION is the
    caller's, and that changes which rule bites: `(0, 0)` puts every character in the body, where
    only a `hosted_api.py` OWNER flags, while a span covering all of `raw` also applies the note
    rule (document-wide owner search, and an UNOWNED anchor is a violation). An earlier version
    hardcoded `(0, 0)` and its docstring still claimed "body or note" — a comment describing
    code it did not have (measured: an unowned `:39286` in a note returned `[]`).
    """
    _, bare = _hits(raw, note_start, note_end)
    return [token for token, _, _ in bare]


def _note_anchor_violations(note_raw: str) -> tuple[list[str], list[str]]:
    """What the provenance note does NOT have licence for: `(new_anchors, continuations)`.

    The whole fragment is treated as the note, so both arms are reported and the body bucket is
    empty by construction. ONE rule in ONE place, because both halves of the guard call it: the
    primary guard accepted a pruned quotation (subset) while
    `test_the_provenance_note_cannot_smuggle_a_bare_continuation` pinned set EQUALITY, so the
    honest cleanup the guard's own message invites redded its neighbour with a bare two-set diff
    and no instruction (measured).

    ⚠️ It sees only `note_raw`, so it can say nothing about an anchor that STRADDLES the
    fragment's edge. That is why the document-level guard in
    `test_the_spec_cites_no_hosted_api_line_numbers` does NOT build itself out of this helper plus
    `_stray_anchors`: it calls `_anchor_violations` once on the whole document and asserts all
    three buckets (#9485 review).
    """
    note_new, note_bare, _ = _anchor_violations(note_raw, 0, len(note_raw.splitlines()))
    return note_new, note_bare


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


def _spec_violations(raw: str) -> tuple[list[str], list[str], list[str]]:
    """`(new anchors in the note, bare anchors in the note, stray anchors outside it)`.

    ONE call on the WHOLE document, all three buckets returned TOGETHER — the grouping is the fix,
    not a convenience. The guard assembled its verdict from two SLICES instead:
    `_note_anchor_violations(note_raw)` folded the note alone (so digits below the quotation were
    not in the text it read) and `_stray_anchors(...)` is bucket [2] alone (so a violation that
    STARTS in the note was thrown away by construction). The note's trailing edge therefore fell
    in the gap between the two halves, and the answer the whole-document scan already held was
    discarded (measured: `(['39286'], [], [])` while the guard was green). Every document-level
    caller goes through here, so the three buckets cannot be audited separately again.
    """
    return _anchor_violations(raw, *_provenance_note_bounds(raw.splitlines()))


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
    # …and the SAME prose anchor carrying a zero-width character between the word and the digits
    # — the one separator that `_fold_doc`'s no-gap drop could not repair, because both arms wrote
    # `line\s+` while `:`/`#L`/`L` accepted none. The previous fold's `gap = True` produced
    # `line 39432` and caught it by accident; that made these two rows a REGRESSION pin, not a new
    # capability (measured at the second review round).
    ("See `hosted_api.py`, line\u200b39432.", [1]),
    ("See `hosted_api.py`, line\u00ad39432.", [1]),
    ("See `hosted_api.py` for the handler flow — the `line\u200b39432` branch.", [1]),
    # NEGATIVE (I1): the continuation's owner is the nearest FILE TOKEN of any extension, not the
    # nearest `*.py` — an honest `.ts`/`.yml` anchor after a `hosted_api.py` mention is not ours
    ("See `hosted_api.py`, and the BFF `token.ts`, `:152`.", []),
    ("| a | `hosted_api.py` |\n| b | `config/required-bindings.yml`, `:12` |", []),
    # NEGATIVE: the block boundary itself. With block tracking removed the owner search reaches
    # back across the paragraph break and CREDITES this number to the previous block's file — the
    # row is what pins the block (each revert of block tracking alone leaves the rest green once
    # the owner rule accepts any extension: §10's `.yml` row no longer depends on it).
    ("The base is in `hosted_api.py`.\n\nAnd `:39205`.", []),
    # NEGATIVE: the folded text joins BLOCKS with one space, and the explicit arm's `\s*` reached
    # across that join, so a paragraph ENDING in a bare `hosted_api.py` plus one OPENING with
    # `:39205` read as one citation. `main` folded per block, where this was impossible, so all
    # three were GREEN there and RED at the head that introduced one whole-document fold — a false
    # red naming a citation the editor never wrote. These rows pin the block filter on the
    # EXPLICIT arm (the continuation arm was already filtered).
    ("Read hosted_api.py\n\n:39205 names the handler.", []),
    ("Read hosted_api.py\n\nL39205 names the handler.", []),
    ("Read hosted_api.py\n\nline 39205 names the handler.", []),
    # …and the MIRROR the span filter above exists for (#9485 review): the `:`/`L`/`line` opener
    # ending one block and the digits opening the next, so the match's START is in the OWNER's own
    # block (which is why filtering on the owner block instead of the span leaves all three red).
    # `main` folded per block and could not form any of them, so RED here was a new false red. The
    # `:` row is the live shape: a colon ending a sentence before an ordered list reads as `: 1`.
    ("Read `hosted_api.py` in this order:\n\n1. first", []),
    ("See `hosted_api.py`, L\n\n39205 names the handler.", []),
    ("See `hosted_api.py`, line\n\n39205 names the handler.", []),
    # …and the SAME ordered list with NO blank line before it, which is the shape the block model
    # above did not know: CommonMark lets an ordered list interrupt a paragraph ONLY when it starts
    # at `1` (a `2.` and up is a lazy paragraph continuation), so `… in this order:` + `1. first`
    # is a paragraph AND a list, not one sentence. The blank line is not what makes these
    # non-citations — the BLOCK is. Without `_LIST_ITEM_RE`'s block open the fold produced
    # `… in this order: 1. first` and this row reported the list marker as a `hosted_api.py`
    # continuation (measured at the #9485 review — the very shape `MEMORY.md` says cannot be
    # reported, which is why the block MODEL grew here rather than the sentence growing a
    # qualifier).
    ("Read `hosted_api.py` in this order:\n1. first", []),
    # …and the `L` twin of it, which is GREEN for a different reason: the `L` arm requires TWO
    # digits, so `L 1` is not an anchor at all. Pinned anyway, because widening that floor to one
    # digit would bring the shape back through the span filter's blind side.
    ("See `hosted_api.py`, L\n1. first", []),
    # ⛔ THE EVASION `420bf6031` OPENED, and the row that would have caught it. A bare `:n` alone
    # on a line prefixed `1. ` is a citation a reader infers from the `hosted_api.py` paragraph
    # directly above it; opening a block at the list item took its OWNER away, so it went GREEN
    # while the same number with no marker stayed RED (measured at the #9485 review: RED at
    # `99026d7f8`, GREEN at `49d62d3bd`). The owner is now decided on the RUN — the
    # blank-line-delimited neighbourhood — so a list item interrupting a paragraph does not sever
    # it, and the SPAN filter still refuses the `: 1` the list MARKER would otherwise become.
    ("See `hosted_api.py`\n1. `:99998`.", [2]),
    # …and the same continuation one token earlier, with no marker at all, which was already RED
    # and must stay RED: the two rows are one mechanism (ownership across a boundary), and the row
    # above is the one that reds if the RUN goes back to being a BLOCK.
    ("See `hosted_api.py`\n`:99998`.", [2]),
    # ⛔ THE BULLET TWIN of the row above, and it is RED on purpose. A bullet is a list item
    # (CommonMark interrupts a paragraph with one unconditionally), so `_LIST_ITEM_RE` opens a
    # BLOCK at it — but the same fail-closed ownership applies: a bare `:n` alone in an item under
    # a `hosted_api.py` paragraph is attributed to it. A GREEN row here would pin the widening of
    # the evasion above (`- ` instead of `1. `) as expected-green.
    ("See the builder in `hosted_api.py`:\n- `:99998` is the handler.", [2]),
    # …while a BULLET whose item names its OWN file stays legal, so `_LIST_ITEM_RE`'s bullet half
    # is a block boundary and not an exemption: the nearest token to the LEFT of the anchor is the
    # `.ts`, and only a `hosted_api.py` ownership reds.
    ("- the builder `hosted_api.py`\n- then the BFF `token.ts`, `:152`.", []),
    # ⛔ RESTORED COVERAGE, not a new capability: on `main` (which had no list-item branch at all)
    # `L 12` and `line 12` are ONE folded block and red, owned by `hosted_api.py`. `_LIST_ITEM_RE`
    # accepted any `\d+` marker, so `12.` INVENTED a block CommonMark does not see (a non-`1`
    # marker is a lazy paragraph continuation), the opener landed in block N and the digits in
    # N+1, and the span filter dropped a real citation. Both rows asserted `[]` — a regression
    # pinned as expected-green, the worst kind of test. They red again from the marker set being
    # `1[.)]` (#9485 review).
    ("See `hosted_api.py`, L\n12. first", [1]),
    ("See `hosted_api.py`, line\n12. first", [1]),
    # …while a continuation INSIDE one list item is still ours: opening a block at a list item
    # must not become an exemption for the item that names the file.
    ("1. the builder `hosted_api.py`\n   then `:39205`.", [2]),
    # a WRAPPED EXPLICIT anchor inside a blockquote: the marker is stripped, so the code span and
    # its digits are one token again. Pins the `^\\s*(?:>\\s?)+` strip, which is load-bearing and
    # would otherwise be pinned only by the note test below.
    ("> See `hosted_api.py:\n> 39286`.", [1]),
    # an HTML entity for the dot, and a zero-width joiner between the colon and the digits: both
    # render as `hosted_api.py:39286` to a reader, so both are the same citation
    ("See `hosted_api&#46;py:39286`.", [1]),
    ("See `hosted_api.py:\u200b39286`.", [1]),
    # …and the same zero-width character INSIDE the name, which is the paste artifact that renders
    # as the citation. `_fold_doc` drops it WITHOUT gapping, so the token stays one word; the
    # earlier `gap = True` split it into `hosted _api.py`, and every one of these five was GREEN
    # (measured at the #9485 review). Whitespace is the only gap — a leading zero-width before the
    # digits above never showed the defect, because the arm's `\s*` happened to rescue it.
    ("See `hosted\u200b_api.py:39205`.", [1]),
    ("See `hosted\u200c_api.py:39205`.", [1]),
    ("See `hosted\u200d_api.py:39205`.", [1]),
    ("See `hosted\u2060_api.py:39205`.", [1]),
    ("See `hosted\u00ad_api.py:39205`.", [1]),
)


@pytest.mark.parametrize("fragment,expected", _EVASION_SHAPES)
def test_the_anchor_scan_catches_every_shape_at_any_offset(fragment: str, expected: list[int]) -> None:
    """The evasion table: each row was GREEN under an earlier version of this guard.

    `test_the_anchor_scan_catches_every_form_at_the_line_it_is_on` covers the shapes that were
    already caught; this covers the ones that were not, and the negatives that keep the block
    boundary and the OWNER rule honest.

    What each row can and cannot prove, measured rather than assumed. Reverting `_fold_doc`'s
    BLOCK TRACKING reds the `:39205` negative below (with the boundary gone the owner search reaches
    back across the paragraph break), reverting its `^\\s*(?:>\\s?)+` strip reds the
    wrapped-blockquote EXPLICIT row (and the note test's wrapped row) — ONE row here, not two: the
    `>`-continuation row does not depend on the strip, because the `>` sitting between the two
    tokens is not a digit and the owner search still finds `hosted_api.py` to its left (measured: a
    strip revert leaves it green at `[2]`).
    Reverting `_file_basename`'s escape strip reds the escaped-continuation row — those are
    single-mechanism rows. `html.unescape` is pinned by one row here; the `Cf` drop by SIX — a
    joiner before the digits and the same joiner, ZWNJ, ZWJ, word-joiner and soft hyphen INSIDE the
    name, which red only once the drop stops gapping the token in two (measured at the #9485
    review: with `gap = True` on the `Cf`/`Mn` branch all five INSIDE rows are GREEN);
    NFKC is pinned by the fullwidth-colon row of the form test below. The `IGNORECASE` flags and the case FOLD are NOT
    individually load-bearing, because the two arms overlap: `HOSTED_API.PY:39286` is caught by
    the explicit arm even if the continuation arm's regex is case-sensitive, and vice versa. So
    reverting either flag alone stays green — that redundancy is deliberate (each arm covers the
    other's blind spot), and it means no row here should be read as pinning one flag.

    The `L`/`line` rows and the two `.ts`/`.yml` negatives ARE single-mechanism rows, and each was
    green before its fix (measured): the four anchor rows need the widened arms (`L` after the
    colon, `#?L`/`line` as a continuation), and the two negatives need the owner scan to accept a
    non-`.py` file token — under the `*.py`-only scan both negatives reported the `hosted_api.py`
    mention to their left, naming a file the editor never touched.

    The three MIRROR rows (a `:`/`L`/`line` opener ending one block, digits opening the next) pin
    the SPAN filter on the continuation arm, and they are single-mechanism: filter on the match's
    OWNER block instead of its span and all three red again, because the opener sits in the
    `hosted_api.py` paragraph (measured — that is the shape a review found: a paragraph ending in
    a bare `hosted_api.py` followed by an ordered list was reported as a `: 1` continuation).

    The NO-BLANK-LINE list row (`… in this order:` then `1. first`) pins the BLOCK OPEN at a list
    item: reverting that one branch makes the fold join the paragraph and the marker with one
    space, and the continuation arm matches the list's own number. The row immediately after the
    list rows is the other direction of the same mechanism — a continuation INSIDE one list item
    still reds, so the block open is a boundary between items, not an exemption for lists.

    ⛔ And the boundary is the SPAN's, never the OWNER's (#9485 review). The `1. ` and `- `
    continuation rows and `test_a_body_continuation_cannot_hide_behind_a_list_item_marker` pin
    that: ownership is decided on the RUN (blank-line-delimited), so an anchor written inside a
    list item keeps the owner of the paragraph the list interrupts. Reverting `_hits` to
    `block_of` for the owner reds them, and NOTHING else — which is the point: the two primitives
    are load-bearing separately.

    The two `12.` rows are the OPPOSITE mutation: they went GREEN when `_LIST_ITEM_RE` accepted any
    `\\d+`, because `12.` invented a block CommonMark does not see and the span filter dropped a
    real `L 12`/`line 12` citation. Restoring `\\d+[.)]` reds them again (measured: `[]` → `[1]`).
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
    rest_raw = "\n".join(lines[:note_start] + lines[note_end:])

    # ⛔ ONE call on the WHOLE document, all THREE buckets. Slicing the note and taking
    # `_stray_anchors`' body bucket discarded the answer the scan already had: an anchor that
    # STARTS on the note's last line and completes on the line just below the quotation is a note
    # violation by POSITION, which `_note_anchor_violations(note_raw)` cannot see (the digits are
    # outside the slice) and `_stray_anchors` throws away (it returns bucket [2] only). Both
    # halves were blind, one edge later than the defect this PR set out to close (measured — see
    # `test_an_anchor_wrapped_across_the_notes_TRAILING_boundary_is_refused`, which drives this
    # same helper on a smuggled document and is the row that reds if this goes back to slices).
    note_new_anchors, note_bare, stray = _spec_violations(raw)
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


def test_a_body_continuation_cannot_hide_behind_a_list_item_marker() -> None:
    """A `:n` injected under the body's `hosted_api.py` mention is caught WHATEVER its marker.

    ⛔ The regression this pins (#9485 review, P1): `_fold_doc` opens a BLOCK at a list item, and
    the block is what `_hits` uses to find an anchor's OWNER — two jobs on one primitive, and only
    the span filter is justified. Every continuation written inside a list item therefore lost its
    owner, so a reintroduced line number went GREEN the moment its line was prefixed `1. ` while
    the same number with no marker stayed RED (measured: RED at `99026d7f8`, GREEN at `49d62d3bd`,
    RED again once the owner is decided on the RUN rather than the BLOCK).

    Driven on the REAL document by injecting one line under the body sentence that names
    `tortoise/hosted_api.py`, because that is the shape the whole-document guard actually sees: a
    synthetic fragment proves the rule, this proves the guard.

    ⚠️ The bullet and `2.` markers are here on purpose. A bullet interrupts a paragraph
    unconditionally, so `_LIST_ITEM_RE` opens a block at it — but the same fail-closed ownership
    has to survive, or the widening becomes a second channel for the same evasion (`- :99998`).
    `2.` is CommonMark's lazy continuation, so it never opens a block at all and the number is
    simply inside the paragraph.
    """
    lines = SPEC.read_text().splitlines()
    anchor_line = next(
        (i for i, line in enumerate(lines) if "tortoise/hosted_api.py" in line), None)
    assert anchor_line is not None, (
        "the body no longer names `tortoise/hosted_api.py` — this probe needs the mention a "
        "reader would attribute an injected continuation to, so restore it or move the probe")
    for marker in ("", "1. ", "2. ", "- "):
        injected = [*lines[:anchor_line + 1], f"{marker}`:99998`", *lines[anchor_line + 1:]]
        _, _, stray = _spec_violations("\n".join(injected))
        assert any("99998" in entry and "hosted_api.py" in entry for entry in stray), (
            f"a bare continuation injected directly under the body's `tortoise/hosted_api.py` "
            f"mention must be reported with that owner even when its line is prefixed {marker!r} "
            f"— got stray={stray}. A list item starts a BLOCK (so a match may not span into it) "
            "but it does not start a run (so the owner a reader infers survives); deriving "
            "ownership from the block is what made this evasion GREEN.")


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

    The expected numbers are the REAL document lines, and nothing is re-indexed: `_stray_anchors`
    passes the FULL `lines` and `_hits` classifies each match by the note's `[start, end)` span,
    so a violation is reported at the line it is really on. (An earlier shape REMOVED the note and
    scanned the remainder, so an index into the shortened body was reported as a file line — 2
    early here — and the one message this guard produces sent the reader to an unrelated row. The
    note is no longer removed, so that hazard is gone rather than merely avoided.)
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


#: `(note fragment appended to the live note, why)` — the shape that ADDS an anchor inside the ONE
#: region anchors are legal in. The note arm used to normalise its slice separately from the body's,
#: so the note's own boundary was a blind spot: a wrap that split `hosted_api.py:` from its digits
#: across two `>` lines was GREEN (measured) while the guard's message claimed that shape was
#: caught.
_NOTE_SMUGGLES = (
    ("`hosted_api.py:\n> 39286` is the authorize handler", "explicit anchor wrapped across `>` lines"),
)


@pytest.mark.parametrize("smuggle,why", _NOTE_SMUGGLES)
def test_the_provenance_note_rejects_an_anchor_wrapped_across_its_own_lines(
        smuggle: str, why: str) -> None:
    """The note may quote ONLY the explicit `#3524` pair — including when the anchor is wrapped."""
    lines = SPEC.read_text().splitlines()
    note_start, note_end = _provenance_note_bounds(lines)
    injected = lines[:note_end - 1] + ["> " + part if not part.startswith(">") else part
                                      for part in smuggle.splitlines()] + lines[note_end - 1:]
    # The `N` injected lines push the note's own last line from `note_end - 1` to
    # `note_end + N - 1`, so the half-open span becomes `[note_start, note_end + N)`.
    #
    # ⛔ The probed index comes from the INJECTION and the bound from that index, never the other
    # way round (#9485 review): deriving it from the bound (`shifted_end - 1`) let the bound and
    # the probe move together, so shortening the bound by one kept every assertion green —
    # re-measured here, the shortened bound left all 49 tests passing. ONE `shifted_end`, used by
    # BOTH calls below, with the assertion at the end as its constraint: it anchors the note's
    # original last line and requires it in a note bucket, which only holds while the bound still
    # covers that line. Shorten it by one and the line reads as BODY, the anchor lands in `stray`,
    # and the assertion reds (measured).
    last_note_index = note_end - 1 + len(smuggle.splitlines())
    assert injected[last_note_index] == lines[note_end - 1], (
        "the note's original last line must sit at this index after the injection, or the probe "
        "below anchors the wrong line")
    shifted_end = last_note_index + 1
    note_new, note_bare, _ = _anchor_violations(
        "\n".join(injected), note_start, shifted_end)
    assert note_new or note_bare, (
        f"a note that gains {why} must be refused: it reads as a quotation of #3524 while naming a "
        f"site this doc is citing — got note_new={note_new} note_bare={note_bare}")

    # …and the span must COVER the note's original last line, which the injection shifts to this
    # index. Pinned by placing an anchor THERE and requiring it in a note bucket: with the bound
    # one short that line reads as BODY and the anchor lands in `stray`, which this test never
    # looks at — so the off-by-one would otherwise have stayed invisible (measured).
    on_last_note_line = [
        *injected[:last_note_index],
        lines[note_end - 1] + " see `hosted_api.py:99999`",
        *injected[last_note_index + 1:],
    ]
    last_new, _, last_stray = _anchor_violations(
        "\n".join(on_last_note_line), note_start, shifted_end)
    assert "99999" in last_new and not last_stray, (
        "the note's own last line is part of the note, so an anchor it gains belongs in a note "
        f"bucket — got note_new={last_new} stray={last_stray}")


def test_an_anchor_wrapped_across_the_notes_TRAILING_boundary_is_refused() -> None:
    """The note boundary has TWO edges, and the trailing one was blind in both halves.

    `_provenance_note_bounds` returns a half-open span, so an anchor that STARTS on the note's
    last line and completes on the line just below the quotation is classified by POSITION as a
    note violation. The guard could not see it: it folded the note's SLICE (which cannot contain
    the digits, they are below it) and read `_stray_anchors`, which returns the BODY bucket only
    — so the violation landed in bucket [0] of a call nobody made, and both halves stayed green.
    Measured before the fix: `_anchor_violations(raw, note_start, note_end)` already returned
    `(['39286'], [], [])` while every assertion in the guard passed.

    Not reachable through `_NOTE_SMUGGLES`: that table injects BEFORE the note's last line, which
    can only put digits INSIDE the quotation. This row injects AFTER it — the other edge, and the
    direction a future editor copying a citation into the note's closing sentence would hit.

    Non-vacuous by construction: this drives `_spec_violations`, the SAME helper
    `test_the_spec_cites_no_hosted_api_line_numbers` uses, so restoring that guard's old
    two-slice shape (`_note_anchor_violations` + `_stray_anchors`) reds the assertion below. Both
    halves of that shape are asserted here to be blind, so the mechanism is measured, not claimed.
    """
    lines = SPEC.read_text().splitlines()
    note_start, note_end = _provenance_note_bounds(lines)
    injected = [
        *lines[:note_end - 1],
        lines[note_end - 1] + " See `hosted_api.py:",
        "39286` names the handler.",
        *lines[note_end:],
    ]
    note_new, note_bare, stray = _spec_violations("\n".join(injected))
    assert note_new == ["39286"] and not stray, (
        "an anchor that opens on the note's last line and closes on the line just below it is ONE "
        "citation, and the note may quote only the `#3524` pair it owns — it must be reported as "
        f"a NOTE violation, not dropped; got note_new={note_new} note_bare={note_bare} "
        f"stray={stray}")

    # …and the shape that dropped it, measured: the note's own SLICE never contains the digits,
    # and the body bucket never contains a violation that starts in the note — so BOTH halves of
    # the old two-call guard returned nothing here (verified at the pre-fix head).
    assert _note_anchor_violations("\n".join(injected[note_start:note_end])) == ([], []), (
        "this row is only interesting while the note SLICE cannot see the digits — the anchor "
        "closes below it, which is exactly why slicing lost the citation")
    assert _stray_anchors(injected, note_start, note_end) == [], (
        "the body bucket alone cannot report a violation that STARTS inside the note — that is "
        "the gap `_spec_violations` exists to close, not a bug in this row")


def test_a_note_anchor_is_bounded_by_position_and_owned_document_wide() -> None:
    """Three readings the note arm has to get right, each measured on the pre-fix shape.

    1. A `:<n>` in the note whose file name sits in the BODY paragraph above is still a citation:
       the note's whole reason for existing is that a number in it is quoted from #3524, so it
       cannot borrow an owner from prose outside the quotation. Pre-fix this was GREEN (the note
       slice saw no owner at all).
    2. A `:<n>` the note attributes to a DIFFERENT file stays legal — the live note quotes
       `oauth.py:1425,1592`. This is the observable difference between a document-wide search and
       a block-scoped one, which would see no owner and refuse it.
    3. A `:<n>` with NO owner anywhere is refused: the note exists to quote anchors, so an
       unmoored number in it is a citation. Dropping the unowned branch made this row pass
       (measured).
    """
    hosted_above = [
        "The handler is in `hosted_api.py`.",
        "",
        "> **Provenance (#3524)** — the stale body quoted nothing that resolves today.",
        "> Unmoored `:39286`.",
    ]
    start, end = _provenance_note_bounds(hosted_above)
    _, bare, _ = _anchor_violations("\n".join(hosted_above), start, end)
    assert bare == [":39286"], (
        "a bare anchor inside the note must be refused whatever block its owner sits in; got "
        f"{bare}")

    other = [
        "The handler is in `oauth.py`.",
        "",
        "> **Provenance (#3524)** — the stale body quoted nothing that resolves today.",
        "> Its `:1425` is retired.",
    ]
    start, end = _provenance_note_bounds(other)
    _, bare, _ = _anchor_violations("\n".join(other), start, end)
    assert bare == [], (
        "a number the note attributes to ANOTHER file must stay legal — the note quotes "
        f"`oauth.py:1425,1592` in the live document; got {bare}")

    unowned = [
        "Nothing here names a source file.",
        "",
        "> **Provenance (#3524)** — the stale body quoted nothing that resolves today.",
        "> Unmoored `:39286`.",
    ]
    start, end = _provenance_note_bounds(unowned)
    _, bare, _ = _anchor_violations("\n".join(unowned), start, end)
    assert bare == [":39286"], (
        "the note exists to quote anchors, so a bare number in it is a citation even with no file "
        f"name to attribute it to; got {bare}")


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
    assert _bare_continuations(escaping, 0, len(escaping.splitlines())) == [":39286"], (
        "the note's continuation form must be caught: `hosted_api.py` followed by a bare `:39286` "
        f"is a new citation, got {_bare_continuations(escaping, 0, len(escaping.splitlines()))}"
    )

    # …and it is the NOTE rule this helper is being asked for, not the body rule. An anchor the
    # note cannot attribute to ANY file is a citation there, while the body rule (no owner → no
    # report) says nothing about it. Hardcoding `(0, 0)` made this `[]` (measured).
    unowned_one = "> Unmoored `:39286`."
    assert _bare_continuations(unowned_one, 0, len(unowned_one.splitlines())) == [":39286"], (
        "an UNOWNED bare anchor inside the note is a citation; the body rule cannot see it, which "
        f"is why this helper must be given the note's bounds — got "
        f"{_bare_continuations(unowned_one, 0, len(unowned_one.splitlines()))}"
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
    assert _bare_continuations(note_raw, 0, len(note_raw.splitlines())) == [], (
        "the note's own quoted pair `hosted_api.py:23409` / `hosted_api.py:23284-23292` is an "
        "EXPLICIT anchor, and `oauth.py:1425,1592` has a DIFFERENT owner, so the continuation "
        "rule must leave both alone; it flagged "
        f"{_bare_continuations(note_raw, 0, len(note_raw.splitlines()))}"
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


def _section_23_jti_store(spec: str) -> str:
    """§2.3's `jti`-store paragraph, located by its own subject, and asserted found.

    Bounded by the next bold-led paragraph (`**Signing key.**`) rather than by line numbers, so a
    reflow of the section cannot silently turn this guard into a no-op on an empty string.
    """
    start = spec.find("**The `jti` store.**")
    assert start != -1, (
        "§2.3 no longer opens a 'The `jti` store.' paragraph — this guard has lost its subject, "
        "which is not the same thing as the defect it exists to catch"
    )
    end = spec.find("**Signing key.**", start)
    assert end > start, (
        "§2.3's `jti`-store paragraph is no longer followed by '**Signing key.**' — the section "
        "has been restructured and this guard's bounds need re-deriving, not deleting"
    )
    return spec[start:end]


def test_the_spec_records_the_jti_mechanism_as_DECIDED_not_open() -> None:
    """§2.3 must record the mechanism that SHIPPED, because §9 points a reader at it.

    The defect this pins (raised on this PR, and real at `e618904d3` before this fix): §2.3 kept
    its PRE-decision form — "Options, in order of preference … **(a) is preferred** … This is the
    one open *mechanism* choice in this spec, and it is deliberately recorded rather than
    assumed" — while `ControlPlaneJtiStore` shipped option **(b)**, the migration that backs it
    says so, and §9 step 1's row tells the reader "the mechanism choice is §2.3".

    The cost is not cosmetic: (a)'s argument is architectural, so a lane reading §2.3 as written
    builds option (a) — a SECOND single-use store for the same guarantee, with its own secret
    surface — and nothing in the record says the choice was taken. The fix is a RELAY, not a
    reversal: the (a)-vs-(b) comparison stays, and the historical "open" wording is allowed to
    survive as a QUOTED history (that is the lineage the next reader needs to understand why the
    paragraph changed). What must be true is that the section states the adopted option, names the
    artifact that ships it, gives the reason, and no longer ASSERTS the choice is open.
    """
    section = _section_23_jti_store(SPEC.read_text())
    flat = " ".join(section.split())

    # The relay's concrete subject: the option that shipped, and the artifact that ships it.
    assert "**(b) is DECIDED" in flat, (
        "§2.3 does not record (b) as the DECIDED mechanism — §9 step 1 points a reader here for "
        "the choice, so leaving it unstated is the stale-flag shape this guard exists for"
    )
    for artifact in ("ControlPlaneJtiStore", "20261030000001_oauth_consent_tickets.sql"):
        assert artifact in flat, (
            f"§2.3's decided record does not name {artifact!r}, so it claims a decision without "
            "naming anything that carries it"
        )
    # The artifact the section names must EXIST — a decided record citing a missing migration is
    # the same fiction as an anchor pointing at a deleted line.
    assert (REPO / "supabase/migrations/20261030000001_oauth_consent_tickets.sql").is_file(), (
        "§2.3 cites migration 20261030000001_oauth_consent_tickets.sql, which is not in the tree"
    )
    # …and the reason, because "decided" without a WHY is the state that gets re-litigated.
    assert "Why (b) and not (a)" in flat, (
        "§2.3 records the decision without its reason — the comparison has to stay readable, or "
        "the next lane re-opens a choice that was taken for a stated reason"
    )
    # …and the departure is MARKED, so a lane holding the earlier preference (or a vendor page
    # arguing for (a)) meets the ruling where the adoption would happen instead of "tidying it up".
    assert "**OVERRIDES:**" in flat, (
        "§2.3 reverses a recorded preference without the OVERRIDES marker AGENTS.md requires — an "
        "unmarked departure reads as an accident of history, not as a ruling"
    )

    # The pre-decision FRAMING must be gone: a preference ORDER is what made (a) look buildable.
    assert "in order of preference" not in flat, (
        "§2.3 still orders the two mechanisms by preference, which reads as 'not yet decided' "
    )
    # The open-claim SENTENCE must be gone. It is matched with its own opening, because the
    # historical quotation of the phrase is deliberately kept (see the docstring) — deleting the
    # lineage is not what this guard asks for.
    assert "This is the one open" not in flat, (
        "§2.3 still ASSERTS that the mechanism choice is open, while the code ships option (b)"
    )


def _section_still_open(spec: str) -> str:
    """The doc's `## Still open` list, located by its own heading, and asserted found.

    This is the ONE list a lane scopes against first, which is why a stale entry there costs more
    than the same stale entry anywhere else: it is read as the work inventory. Bounded by the next
    `## ` heading (or EOF) rather than by line numbers, so a reflow cannot turn the guard below
    into a no-op on an empty string.
    """
    start = spec.find("## Still open")
    assert start != -1, (
        "the spec no longer has a '## Still open' section — this guard has lost its subject, which "
        "is not the same thing as the defect it exists to catch"
    )
    end = spec.find("\n## ", start + 1)
    return spec[start:] if end == -1 else spec[start:end]


def test_the_still_open_list_does_not_reopen_the_DECIDED_jti_mechanism() -> None:
    """`## Still open` must not list the `jti` mechanism as unchosen after §2.3 decided it.

    The defect this pins (raised on this PR at `3f1d32ec0`): §2.3 records **(b) is DECIDED, and
    shipped** and §9 step 1's row is DELIVERED, while `## Still open` still carried

        - **The `jti` store mechanism** (§2.3) — preferred shape recorded; not chosen by an owner.

    On `origin/main` that bullet was TRUE (§2.3 there presents the choice as open) — this PR's own
    edits are what made it false, and the relay landed in §2.3/§9 and missed the one list a
    scoping lane reads first. The cost is the same one that motivated the §2.3 relay: a lane that
    trusts the open-items list re-opens a shipped mechanism choice and builds a SECOND single-use
    store for the same guarantee.

    Deleting the whole section is NOT an acceptable way to satisfy this: the section's other
    entries are real open items (`#8218`, `#3529`), so the found-assert and the positive arm below
    keep the guard anchored to a section that still says something.

    Measured non-vacuity: with the bullet re-inserted verbatim and nothing else changed, this test
    FAILS on the open-claim arm; it passes only once the bullet is gone.
    """
    section = _section_still_open(SPEC.read_text())
    flat = " ".join(section.split())

    # The section must still be a live inventory — otherwise "no jti claim here" is satisfied by
    # emptying it, and the guard would pass on a doc that lost its open-items list entirely.
    assert "#8218" in flat, (
        "`## Still open` no longer names #8218 (attested client identity) — if the section was "
        "emptied to satisfy this guard, the open items were lost rather than relayed"
    )

    # The bullet's own subject, and its own open-claim wording. Both are matched so a re-worded
    # restatement (`the jti mechanism is still open` / `not chosen by an owner`) cannot slip past.
    assert "`jti` store mechanism" not in flat, (
        "`## Still open` still lists the `jti` store mechanism — §2.3 records (b) as DECIDED and "
        "`ControlPlaneJtiStore` ships it, so a lane scoping from this list builds the second "
        "single-use store the §2.3 relay exists to prevent"
    )
    assert "not chosen by an owner" not in flat, (
        "`## Still open` still ASSERTS that a mechanism choice is unchosen — the only mechanism "
        "choice in this spec (§2.3) is decided and shipped"
    )

    # …and the cross-check that makes the pair a contradiction guard rather than two independent
    # string checks: the section §2.3 lives in must still read as DECIDED.
    assert "**(b) is DECIDED" in " ".join(_section_23_jti_store(SPEC.read_text()).split()), (
        "§2.3 no longer reads as DECIDED, so the relay has moved back — this guard's premise is "
        "gone, not satisfied"
    )


# ═══════════════════════════════════════════════════════════════════════════
# §2 / §9 step 2 — the token arm on `POST /oauth/consent`, EXECUTED
#
# §9 orders this step to land BEFORE any page change, on the argument that it is additive: the
# bearer path stays live while the AS learns to accept an assertion from the session origin. The
# tests below hold that argument to its two halves — every guarantee in §2.2 has a request that
# is REFUSED without it, and the two arms must be provably independent of each other.
#
# The token's wire format is built HERE, by hand, for the negative cases: a fixture that used the
# module's own signer to produce a bad token would pass while the signer was broken, and the
# contract in §2.1 is small enough to state independently.
# ═══════════════════════════════════════════════════════════════════════════

#: Long enough to be a key: the arm refuses a secret shorter than 32 UTF-8 BYTES (a guessable HMAC
#: key is the same forgery oracle as a reused one), so a fixture secret must clear that floor.
_TEST_SECRET = "consent-token-secret-for-tests-0123456789"
#: §2.1 — the session origin, "the minting authority". A token from anywhere else is the `iss`
#: failure §2.2 names, so the look-alike below is a real shape, not a placeholder.
_ISSUER = "https://app.premiselabs.co"
_LOOKALIKE_ISSUER = "https://app.premiselabs.co.evil.example"
#: A second LEGITIMATE origin — what a preview deployment sets `CONSENT_TOKEN_ISSUER` to (§2.1).
_STAGING_ISSUER = "https://staging.app.premiselabs.co"
#: A second redirect registered for the SAME client, so a request using it passes
#: `validate_authorize_params` and reaches the BIND check rather than being refused earlier for
#: the wrong reason (a test that reds for the wrong reason proves nothing). A different PATH, not
#: a different loopback port — the port is relaxed by `_redirect_uri_matches` (§2.2), so a port
#: variant would match the binding instead of testing it.
_ALT_REDIRECT = "http://127.0.0.1:8765/cb2"


def _seg(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _signed(payload: dict, *, secret: str = _TEST_SECRET, alg: str = "HS256",
            typ: str = "JWT", kid: str = "consent-1") -> str:
    """A compact-JWS token built to §2.1 by hand — including the shapes a mint must never emit."""
    signing_input = f"{_seg({'alg': alg, 'typ': typ, 'kid': kid})}.{_seg(payload)}"
    if alg == "none":          # RFC 7519 §6: the "unsecured" JWT's signature segment is empty
        return signing_input + "."
    mac = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def _claim_set(base_bind: dict, **overrides) -> dict:
    """A currently-valid claim set per §2.1, mutable per test.

    The bind parameter is named `base_bind` so a test can REPLACE `bind` through `overrides`
    (the malformed-bind cases) as well as populate it.
    """
    now = time.time()
    payload = {
        "iss": _ISSUER,
        "aud": "tortoise-consent",      # §2.2: NOT a Supabase audience
        "iat": now,
        "exp": now + 120,               # §2.2: CONSENT_TOKEN_TTL_S
        "jti": secrets.token_hex(32),
        "sub": _U1,
        "email": "u@example.com",
        "bind": base_bind,
    }
    payload.update(overrides)
    return payload


def _bind(client_id: str, challenge: str, *, redirect_uri: str = REDIRECT,
          state: str | None = "st-123") -> dict:
    return {"client_id": client_id, "redirect_uri": redirect_uri,
            "code_challenge": challenge, "state": state}


def _consent_payload(tc, bind: dict) -> dict:
    """The body a page sends: the flow's params, exactly as the authorize URL carried them."""
    return {
        "client_id": bind["client_id"],
        "redirect_uri": bind["redirect_uri"],
        "response_type": "code",
        "code_challenge": bind["code_challenge"],
        "code_challenge_method": "S256",
        "state": bind["state"],
        "scope": "mcp",
    }


def _post_token(tc, token: str, bind: dict, *, headers: dict | None = None,
                **overrides):
    payload = _consent_payload(tc, bind) | {"consent_token": token}
    payload.update(overrides)
    return tc.post("/oauth/consent", json=payload, headers=headers or {})


def _post_bearer(tc, bind: dict, **overrides):
    payload = _consent_payload(tc, bind)
    payload.update(overrides)
    return tc.post("/oauth/consent", json=payload,
                   headers={"Authorization": "Bearer fake-session-jwt"})


@pytest.fixture
def bridge_client(monkeypatch):
    """The real `hosted_api` app over the OAuth suite's in-memory control plane.

    The accessors below are the SAME ones `tests/test_oauth_mcp.py` uses (one seeding path, two
    suites), but the fixture lives here: a fixture's dependencies are resolved in the module that
    declares the requesting test, so importing `api_client` by name needs its whole chain imported
    too — and an imported name reused as a fixture parameter is a redefinition (ruff F811).
    """
    cp = FakeControlPlane({
        "organizations": [dict(TEAM_FREE), dict(TEAM_TEAM)],
        "org_memberships": [_member(_U1, "team-free-001")],
        "api_keys": [],
    })
    _enable_supabase(monkeypatch, cp)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "oauth.db")
        with patched_tortoise_sdk(db_path), verdict_client(app) as tc:
            yield tc, cp


@pytest.fixture(autouse=True)
def _consent_token_env(monkeypatch):
    """Start every test in this module from §9 step 1's PROVISIONED state, with an EMPTY store.

    The replay register is process-global (§2.3: single-use needs shared state), so a test that
    left a `jti` in it would make the NEXT test's "first use" a replay — a false red that depends
    on collection order.
    """
    from tortoise import consent_token

    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    monkeypatch.delenv("CONSENT_TOKEN_ISSUER", raising=False)
    consent_token.reset_consent_jti_store()
    yield
    consent_token.reset_consent_jti_store()


@pytest.fixture
def mint_spy(monkeypatch) -> list[dict]:
    """Record every `issue_auth_code` call — the ONLY place the bound params are observable.

    They are not observable in the response: the binding REQUIRES the token's values to match the
    request's, so every honest request passes the same values either way. The precedence is real
    only in the one case they legitimately differ (the RFC 8252 loopback relaxation), and this
    spy is what makes that case readable without weakening the contract for all the others.
    """
    import tortoise.oauth as oauth_module

    calls: list[dict] = []
    real = oauth_module.issue_auth_code

    def _spy(cp, **kwargs):
        calls.append(dict(kwargs))
        return real(cp, **kwargs)

    monkeypatch.setattr(oauth_module, "issue_auth_code", _spy)
    return calls


@pytest.fixture
def bearer_ok(monkeypatch):
    """The pre-bridge identity source, available to the bearer path."""
    async def _ok(request):
        return {"user_id": _U1, "email": "u@example.com"}

    monkeypatch.setattr("tortoise.hosted_api.verify_session_jwt", _ok)


@pytest.fixture
def bearer_refuses(monkeypatch):
    """Make the BEARER path unverifiable, so only a consent token can establish identity.

    This is the production shape the bridge exists for: a cold browser whose session cookie is
    `__Host-session` on `app.*` — host-only and HttpOnly, so `api.*` never receives it.
    """
    async def _refuse(request):
        raise HTTPException(status_code=401, detail="Missing session token")

    monkeypatch.setattr("tortoise.hosted_api.verify_session_jwt", _refuse)


def test_startup_installs_the_SHARED_register(bridge_client, mint_spy, bearer_refuses) -> None:
    """§9 step 1's OTHER half: the wiring, not the class.

    `ControlPlaneJtiStore` being correct proves nothing about the deployment if the startup hook
    never installs it — `consent_jti_store()` falls back to `ProcessLocalJtiStore` SILENTLY, so
    every machine answers "first use" for a nonce another machine already spent and one consent
    yields N authorization codes. The cross-machine test installs the store itself, so it proves
    the class and not the wiring; this test boots the real app and drives a redemption through it.

    MEASURED, two-sided: passes here, and FAILS at the `isinstance` below with "the AS started
    with a PER-PROCESS register — cross-machine replay is unbounded" if the
    `install_shared_consent_jti_store()` call is deleted from `_lifespan` (`tortoise/hosted_api.py`).
    The RPC assertion further down is a SECOND, independent arm and is NOT the one that fires for
    that mutation — it fires if the store is installed but the redemption never reaches the
    control plane (e.g. a store that short-circuits locally). Note that deleting the call leaves
    every other consent test green, which is exactly why this pin has to exist.
    """
    from tortoise.consent_token import ControlPlaneJtiStore, consent_jti_store

    tc, cp = bridge_client                      # entering the client runs the real `_lifespan`
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)

    assert isinstance(consent_jti_store(), ControlPlaneJtiStore), (
        "the AS started with a PER-PROCESS register — cross-machine replay is unbounded, and no "
        "other consent test notices because they install the store themselves")

    assert _post_token(tc, mint_consent_token(user_id=_U1, email="u@example.com", bind=bind),
                       bind).status_code == 200
    assert any(fn == "consume_oauth_consent_ticket" for fn, _ in cp.rpc_calls), (
        "the redemption never reached the control plane — per-process again")


def test_the_startup_hook_still_calls_the_installer() -> None:
    """The AST seam, alongside the behavioural pin above (`tests/test_8098_webhook_retry_sweep.py`
    is the precedent): the wiring must be a real statement in `_lifespan`, not a comment, and the
    behavioural test cannot tell a call from an equivalent side effect that a refactor removed.
    """
    from pathlib import Path

    import tortoise.hosted_api as ha

    tree = ast.parse(Path(ha.__file__).resolve().read_text(), filename="hosted_api.py")
    lifespan = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_lifespan")
    calls = [
        node for node in ast.walk(lifespan)
        if isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Name) and node.func.id == "install_shared_consent_jti_store")
             or (isinstance(node.func, ast.Attribute)
                 and node.func.attr == "install_shared_consent_jti_store"))
    ]
    assert len(calls) == 1, (
        "expected exactly one `install_shared_consent_jti_store()` call in `_lifespan` — zero "
        "silently restores per-process single-use across Fly machines (replay), and two is ambiguous")


def test_a_bound_token_mints_the_code_with_the_tokens_identity_and_bound_params(
        bridge_client, mint_spy, bearer_refuses) -> None:
    """§9 step 2, the whole deliverable: verify, then `issue_auth_code` with the BOUND params.

    Proven three ways, because a single one can be satisfied by accident:
      * the bearer path is unverifiable (`bearer_refuses`), so the identity CANNOT have come from
        it — the code is bound to the token's `sub` (§2.2: `sub` is "the ONLY thing the token is
        allowed to establish");
      * the call `issue_auth_code` received carries the token's `bind` values, not the request's;
      * the code is redeemed end-to-end with the PKCE verifier, so the values minted are the ones
        `/oauth/token` compares against.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    verifier, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind)

    r = _post_token(tc, token, bind)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == bind["state"]
    assert body["redirect_uri"] == bind["redirect_uri"]

    assert len(mint_spy) == 1, "one consent must mint exactly one code"
    minted = mint_spy[0]
    assert minted["user_id"] == _U1, "the identity must come from the TOKEN's sub"
    assert minted["redirect_uri"] == bind["redirect_uri"]
    assert minted["code_challenge"] == bind["code_challenge"]
    assert minted["state"] == bind["state"]

    tok = _exchange(tc, client_id=client_id, code=body["code"], verifier=verifier)
    assert tok.status_code == 200, tok.text
    assert tok.json()["token_type"] == "Bearer"


def test_the_token_arm_does_not_require_a_bearer(bridge_client, bearer_refuses) -> None:
    """No `Authorization` header exists on the bridge's path — that is the point of the token.

    §1: `api.*` can never see `__Host-session`, so a verifier that still demanded a bearer would
    have implemented nothing. The fixture makes every bearer verification a 401, so a 200 here is
    only reachable through the token arm.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind)

    r = _post_token(tc, token, bind, headers={"Authorization": "Bearer stale-and-unverifiable"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("field", ("client_id", "redirect_uri",
                                   "code_challenge", "state"))
def test_a_token_bound_to_another_flow_is_refused(bridge_client, mint_spy, bearer_refuses,
                                                  field: str) -> None:
    """§2.2 — each `bind` field is what stops a token minted in flow X being used for flow Y.

    Every mutation below is a request that is INDIVIDUALLY VALID (the client is registered, the
    redirect is registered for it, the challenge is a legal S256 challenge), so the refusal can
    only come from the binding — which is the property §2.4 says is the only half that bounds
    cross-flow substitution.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc, redirect_uris=[REDIRECT, _ALT_REDIRECT])["client_id"]
    other_client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    _, other_challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind)

    requested = {
        "client_id": other_client_id if field == "client_id" else bind["client_id"],
        "redirect_uri": _ALT_REDIRECT if field == "redirect_uri" else bind["redirect_uri"],
        "code_challenge": other_challenge if field == "code_challenge" else challenge,
        "state": "st-attacker" if field == "state" else bind["state"],
    }
    r = _post_token(tc, token, bind, **requested)
    assert r.status_code == 401, r.text
    assert f"bind.{field}" in r.json()["error_description"], r.text
    assert mint_spy == [], "a refused binding must never reach the mint"


def test_a_token_bound_to_a_state_the_request_drops_is_refused(bridge_client, mint_spy,
                                                               bearer_refuses) -> None:
    """`state` is optional on the wire, so "absent" is a value the binding has to compare.

    A `None == absent` shortcut on the verifier's side would let a token minted for a stateful
    flow be redeemed by a request that presents no state — the cross-flow substitution §2.2 names
    for `bind.state`, arriving through the one field that can legitimately be null.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge, state="st-123")
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind)

    payload = _consent_payload(tc, bind) | {"consent_token": token}
    payload.pop("state")
    r = tc.post("/oauth/consent", json=payload)
    assert r.status_code == 401, r.text
    assert "bind.state" in r.json()["error_description"], r.text
    assert mint_spy == []


@pytest.mark.parametrize("overrides,needle", [
    # §2.2 `exp` — the TTL is what bounds exposure; expiry is the whole of that half.
    ({"iat": time.time() - 900, "exp": time.time() - 600}, "expired"),
    # §2.2 `iss` — a look-alike origin must not be accepted as the session origin.
    ({"iss": _LOOKALIKE_ISSUER}, "iss"),
    # §2.2 `aud` — a Supabase session token (or any other JWT on a shared key) must never be
    # readable as a consent assertion. `authenticated` is literally that audience.
    ({"aud": "authenticated"}, "aud"),
    # …including in its list form: PyJWT's `strict_aud` exists because this spelling bypasses a
    # naive equality check.
    ({"aud": ["tortoise-consent"]}, "aud"),
    # §2.2 — `exp = iat + CONSENT_TOKEN_TTL_S`. A token that outlives the TTL is a mint bug (or
    # a rogue minter), and accepting it would silently turn a 120 s window into an arbitrary one.
    ({"iat": time.time(), "exp": time.time() + 86_400}, "ttl"),
    # §2.2 — the TTL test above bounds the SPAN (`exp - iat`), not the expiry, so a future-dated
    # `iat` passes it: `iat = now + 10_000, exp = iat + 120` is live for ~10 119 s. The future-`iat`
    # refusal is the only thing bounding that, and deleting it left this file GREEN (measured,
    # review round 2), so it needs its own row.
    ({"iat": time.time() + 10_000, "exp": time.time() + 10_120}, "future"),
    # §2.2 `jti` — single-use is unimplementable without it, so its absence must refuse.
    ({"jti": ""}, "jti"),
    # §2.2 `sub` — the identity. Without it the AS has no input at all.
    ({"sub": ""}, "sub"),
    # §2.2 `jti` — its `_MAX_JTI_CHARS` cap is the register's only bound on key size, and deleting
    # it left the suite GREEN (measured, fresh review round 5).
    ({"jti": "x" * 300}, "jti"),
    # §2.1 `email` — optional, but if present it must be a string; the check had no test.
    ({"email": 12345}, "email"),
    # §2.2 `exp` — a JSON integer literal too large to be a float. `json.loads` yields a Python int
    # with no float involved, and both `math.isfinite` and `float()` then raise `OverflowError`,
    # which answered a bare 500 (measured, convergence review). Not a privilege path (only a
    # signature holder gets here), but this module's every-refusal-is-typed claim has to be true.
    ({"exp": 10**400}, "exp"),
])
def test_a_claim_that_breaks_the_contract_is_refused(bridge_client, mint_spy, bearer_refuses,
                                                     overrides: dict, needle: str) -> None:
    """One refusal per guarantee in §2.2 — the table that would otherwise be prose."""
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = _signed(_claim_set(bind, **overrides))

    r = _post_token(tc, token, bind)
    assert r.status_code == 401, r.text
    assert needle in r.json()["error_description"], r.text
    assert mint_spy == []


@pytest.mark.parametrize("overrides", [
    ({"bind": {"client_id": "c", "redirect_uri": REDIRECT, "code_challenge": "x"}}),
    ({"bind": "not-an-object"}),
])
def test_a_malformed_bind_is_refused(bridge_client, mint_spy, bearer_refuses,
                                     overrides: dict) -> None:
    """`bind` is the security-relevant field, so its SHAPE is part of the contract (§2.1)."""
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = _signed(_claim_set(bind, **overrides))

    r = _post_token(tc, token, bind)
    assert r.status_code == 401, r.text
    assert "bind" in r.json()["error_description"], r.text
    assert mint_spy == []


def test_a_non_string_bind_state_is_refused(bridge_client, mint_spy, bearer_refuses) -> None:
    """§2.1 — `bind.state` is a string or null, and this guard is LOAD-BEARING.

    `_bind_mismatch` cannot catch this case: a `state` of `7` in the token and `7` in the request
    compare EQUAL, so the mismatch branch passes and the value reaches `issue_auth_code` and is
    echoed to the page. Deleting the type check left the suite green and minted a code (measured,
    fresh review round 5) — so it gets its own test, presenting the same non-string value on both
    sides, which is the only shape where the type guard is the sole refuser.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    confused = bind | {"state": 7}
    token = _signed(_claim_set(bind, bind=confused))

    r = _post_token(tc, token, confused)
    assert r.status_code == 401, r.text
    assert "bind.state" in r.json()["error_description"], r.text
    assert mint_spy == []


def test_a_bind_carrying_a_key_the_contract_does_not_name_is_refused(
        bridge_client, mint_spy, bearer_refuses) -> None:
    """`bind` is EXACTLY §2.1's four params — an unknown key is refused, not ignored.

    The four bound fields here all MATCH the request, so the mismatch branch cannot fire and the
    exact-set rule is the only thing that can refuse. That is why this is its own test: the earlier
    version reused a bind whose `client_id`/`code_challenge` were `"c"`/`"x"`, which the mismatch
    branch refused first, so deleting the shape check left it GREEN (measured, review round 1).
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    smuggled = bind | {"org_id": "another-team"}
    token = _signed(_claim_set(bind, bind=smuggled))

    r = _post_token(tc, token, bind)
    assert r.status_code == 401, r.text
    assert "unexpected" in r.json()["error_description"], (
        "the refusal must come from the exact-set rule (which names the unexpected key), not from "
        f"the bind mismatch: {r.text}")
    assert mint_spy == []


def test_a_header_that_does_not_say_typ_jwt_is_refused(bridge_client, mint_spy,
                                                     bearer_refuses) -> None:
    """§2.1 — `typ` is why a Supabase session JWT can never read as a consent assertion."""
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)

    for typ in ("", "at+jwt", "JWT "):
        r = _post_token(tc, _signed(_claim_set(bind), typ=typ), bind)
        assert r.status_code == 401, f"typ={typ!r}: {r.text}"
        assert "typ" in r.json()["error_description"], r.text
    assert mint_spy == []


def test_the_register_refuses_rather_than_evicting_when_it_is_full(bridge_client, mint_spy,
                                                                  bearer_refuses) -> None:
    """§2.3 — a full register answers 503; it never evicts, because evicting re-enables replay.

    Driven through the documented step-1 seam with `max_entries=1`: the SECOND distinct token must
    be refused, and the refusal must not mint. The first still mints, so the refusal is the
    capacity policy and not the arm being off.
    """
    from tortoise.consent_token import (
        ProcessLocalJtiStore,
        configure_consent_jti_store,
        reset_consent_jti_store,
    )

    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)

    configure_consent_jti_store(ProcessLocalJtiStore(max_entries=1))
    try:
        first = _post_token(tc, mint_consent_token(user_id=_U1, email="u@example.com", bind=bind),
                            bind)
        assert first.status_code == 200, first.text
        minted = len(mint_spy)

        _, other_challenge = _pkce()
        other_bind = _bind(client_id, other_challenge)
        second = _post_token(
            tc, mint_consent_token(user_id=_U1, email="u@example.com", bind=other_bind),
            other_bind)
        assert second.status_code == 503, second.text
        assert second.json()["error"] == "temporarily_unavailable", second.text
        assert "full" in second.json()["error_description"], second.text
        assert len(mint_spy) == minted, "a full register must not mint"
    finally:
        reset_consent_jti_store()


def test_a_store_that_cannot_answer_is_a_typed_503_and_never_a_500(bridge_client, mint_spy,
                                                               bearer_refuses) -> None:
    """§2.3 + #3026 — step 1's store may fail in its OWN way (D1/HTTP, not `JtiStoreUnavailable`).

    Two shapes, both of which reached a 500 `server_error` in review round 2: a store whose
    `consume` is not `async` (its body runs, records the jti, and only then fails at the `await`),
    and a store that raises outside the documented exception — the handler's #3026 net converts
    `RuntimeError` to a retryable 503 but anything else to an internal error. Neither may surface
    as one.
    """
    from tortoise.consent_token import configure_consent_jti_store, reset_consent_jti_store

    class _SyncStore:
        def consume(self, jti, expires_at):
            return True

    with pytest.raises(TypeError, match="async"):
        configure_consent_jti_store(_SyncStore())

    class _StoreBlewUp(Exception):
        """Deliberately NOT a `RuntimeError`: the handler's #3026 net maps `RuntimeError` to a
        retryable 503 and everything ELSE to a 500 `server_error`, and a store failure is never a
        bug-in-this-request. A driver error (httpx, a D1 client) is exactly this shape."""

    class _BrokenStore:
        async def consume(self, jti, expires_at):
            raise _StoreBlewUp("D1: 500 from the ticket table")

    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind)

    configure_consent_jti_store(_BrokenStore())
    try:
        r = _post_token(tc, token, bind)
        assert r.status_code == 503, r.text
        assert r.json()["error"] == "temporarily_unavailable", r.text
        assert mint_spy == [], "a store that cannot answer must not mint"
        # ⛔ #3026's no-detail rule: the store is an arbitrary object (§2.3), so its message can
        # carry an upstream URL, a query string or a schema detail, and this 503 is triggerable by
        # one unauthenticated POST. The detail belongs in the log, not the body.
        assert "ticket table" not in r.text, r.text
        assert "_StoreBlewUp" not in r.text, r.text
    finally:
        reset_consent_jti_store()


def test_an_oversized_token_is_refused_before_anything_is_parsed(bridge_client, mint_spy,
                                                                 bearer_refuses) -> None:
    """Resource exhaustion: the length cap is the only attacker-facing bound on this input."""
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)

    r = _post_token(tc, "A" * 8193, bind)
    assert r.status_code == 401, r.text
    assert "too large" in r.json()["error_description"], r.text
    assert mint_spy == []


def test_the_issuer_is_the_configurable_session_origin_and_not_a_literal(bridge_client,
                                                                        mint_spy,
                                                                        bearer_refuses,
                                                                        monkeypatch) -> None:
    """§2.1 — `iss` must equal the configured session origin, so a preview deployment can verify
    its own mint. Without this, changing the anchor is invisible to the suite (measured round 2:
    deleting the env override entirely left the file green)."""
    from tortoise.consent_token import DEFAULT_CONSENT_TOKEN_ISSUER

    monkeypatch.setenv("CONSENT_TOKEN_ISSUER", _STAGING_ISSUER)
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)

    # minted by the origin this deployment now trusts → accepted
    ok = _post_token(tc, _signed(_claim_set(bind, iss=_STAGING_ISSUER)), bind)
    assert ok.status_code == 200, ok.text

    # the DEFAULT origin is a different origin → refused, and the refusal names `iss`
    wrong = _post_token(tc, _signed(_claim_set(bind, iss=DEFAULT_CONSENT_TOKEN_ISSUER)), bind)
    assert wrong.status_code == 401, wrong.text
    assert "iss" in wrong.json()["error_description"], wrong.text


def test_a_secret_that_reuses_a_service_key_is_not_a_provisioned_arm(monkeypatch) -> None:
    """§2.3 — the secret must be DEDICATED. A reused key is treated as unprovisioned.

    Without this, `CONSENT_TOKEN_SECRET=<anon key>` makes the verifier a forgery oracle for anyone
    holding that key — it is public by construct (Supabase designs it to be shipped to browsers, so
    it is assumed held by anyone, and it is in this process's environment, which is what makes the
    collision observable; §9 step 4 removed the one page that used to embed it, which never made it
    secret) — and `sub`
    is the only identity input on the token arm, so that is every account. Unit-level on purpose: the arm's
    unavailability already has an end-to-end test, and the operator signal here is the REASON the
    503 carries, which is what distinguishes "unset" from "set to something you must not reuse".
    """
    from tortoise.consent_token import consent_token_secret, consent_token_secret_problem

    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    assert consent_token_secret() == _TEST_SECRET
    assert consent_token_secret_problem() is None

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", _TEST_SECRET)
    assert consent_token_secret() is None, "a reused key must not be accepted as the HMAC key"
    problem = consent_token_secret_problem()
    assert problem and "DEDICATED" in problem and "SUPABASE_SERVICE_ROLE_KEY" in problem, problem

    # ⛔ …and the ANON key, which is the WORSE half (§2.3): it is public by construct, because
    # Supabase designs it to be shipped to browsers, so it must be assumed held by anyone — and it
    # IS in this process's environment: the API reads `SUPABASE_ANON_KEY` for its own server-side
    # proxy calls (`hosted_api.py`, the `/api/sb` leg), NOT for the consent page. §9 step 4 deleted
    # the page's inline client and the `__SUPABASE_ANON_KEY__` placeholder with it
    # (`grep -c SUPABASE_ANON_KEY tortoise/oauth.py` at this head: 0), so no page is evidence
    # either way. An earlier version of this guard claimed the key was NOT in the API's env at all
    # and so left exactly this case unguarded (measured, review round 3).
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")
    monkeypatch.setenv("SUPABASE_ANON_KEY", _TEST_SECRET)
    assert consent_token_secret() is None, "the PUBLIC anon key must never sign assertions"
    problem = consent_token_secret_problem()
    assert problem and "SUPABASE_ANON_KEY" in problem, problem


def test_a_guessably_short_secret_is_not_a_provisioned_arm(monkeypatch) -> None:
    """§2.3 — a key short enough to guess is the same forgery oracle as a reused one.

    Measured before this guard: with `CONSENT_TOKEN_SECRET=a`, a token HMAC'd with `"a"` was
    ACCEPTED and minted an authorization code. The floor is a length check, not an entropy
    measurement — it catches the copy-paste-a-word class, which is the realistic mistake.
    """
    from tortoise.consent_token import consent_token_secret, consent_token_secret_problem

    monkeypatch.setenv("CONSENT_TOKEN_SECRET", "a")
    assert consent_token_secret() is None
    problem = consent_token_secret_problem()
    assert problem and "shorter" in problem, problem

    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    assert consent_token_secret() == _TEST_SECRET
    assert consent_token_secret_problem() is None

    # The floor is measured in UTF-8 BYTES, so a multi-byte key with FEWER than 32 characters and
    # 32+ bytes must stay ACCEPTED — the only direction the byte measure loosens, and a revert to
    # `len(secret)` re-introduces a false refusal. This is 20 characters and 40 bytes.
    multibyte = "\u00e9" * 20
    assert len(multibyte) < 32 and len(multibyte.encode()) >= 32
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", multibyte)
    assert consent_token_secret_problem() is None, (
        "a 40-byte key was refused for being 20 characters — the floor is a byte measure")


def test_a_padded_secret_is_not_a_provisioned_arm(monkeypatch) -> None:
    """§2.3 — a padded value is UNPROVISIONED, because the minter does not strip it either.

    Measured before this guard: `CONSENT_TOKEN_SECRET="  <40 k>\\n"` was accepted AND silently
    normalised — `consent_token_secret()` returned the stripped value — while step 3's minter (the
    BFF, TypeScript) reads `process.env.CONSENT_TOKEN_SECRET` verbatim. The two sides then HMAC
    DIFFERENT keys: every redemption answers `401 invalid_token: signature does not verify`, i.e.
    the whole step-4 flow is dead and the diagnosis blames the token instead of the key. Padding is
    the normal result of `fly secrets set … < file` and of a pasted secret, so this is the
    realistic operator mistake; refusing it names the fix at the first request instead of never.
    """
    from tortoise.consent_token import consent_token_secret, consent_token_secret_problem

    for padded in (" " + _TEST_SECRET, _TEST_SECRET + "\n", "\t" + _TEST_SECRET + " "):
        monkeypatch.setenv("CONSENT_TOKEN_SECRET", padded)
        assert consent_token_secret() is None, (
            f"a padded key {padded[:3]!r}… was accepted as the HMAC key — the operator's value "
            "must never be silently normalised, because the minter does not normalise it")
        problem = consent_token_secret_problem()
        assert problem and "whitespace" in problem, problem

    # …and the unpadded value is still exactly what is returned, byte-for-byte.
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    assert consent_token_secret() == _TEST_SECRET
    assert consent_token_secret_problem() is None


def test_a_padded_secret_turns_the_arm_off_end_to_end(bridge_client, mint_spy, bearer_ok,
                                                      monkeypatch) -> None:
    """The padded-secret refusal is a typed 503, not a 401 that blames the token."""
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", " " + _TEST_SECRET)
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = _signed(_claim_set(bind), secret=_TEST_SECRET)

    r = _post_token(tc, token, bind)
    assert r.status_code == 503, r.text
    assert r.json()["error"] == "temporarily_unavailable", r.text
    assert "whitespace" in r.json()["error_description"], r.text
    assert mint_spy == [], "an unprovisioned arm must not mint"

    # …and the arm being off must not take the bearer path with it.
    ok = _post_bearer(tc, bind)
    assert ok.status_code == 200, ok.text


def test_a_secret_that_is_not_utf8_encodable_is_not_a_provisioned_arm(monkeypatch) -> None:
    """The `UnicodeEncodeError` arm exists so a lone surrogate is a 503, never a 500.

    A surrogate in the ENV value (not in the token) reaches `len(secret.encode("utf-8"))`. Without
    this arm the exception escapes `consent_token_secret_problem()` → `verify_consent_token` → the
    handler's token-arm catch-all, which maps only `RuntimeError` to 503 and everything else to
    `500 server_error` — on the endpoint whose contract is "never a 500 on unauthenticated input".
    Deleting the `try/except` the suite would otherwise stay green (measured, review round 4).
    """
    from tortoise.consent_token import consent_token_secret, consent_token_secret_problem

    monkeypatch.setenv("CONSENT_TOKEN_SECRET", "\udcff" * 32)
    assert consent_token_secret() is None
    problem = consent_token_secret_problem()
    assert problem and "encodable" in problem, problem


def test_a_surrogate_secret_turns_the_arm_off_end_to_end(bridge_client, mint_spy, bearer_ok,
                                                         monkeypatch) -> None:
    """End to end: the surrogate-secret arm answers a typed 503, never a 500."""
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", "\udcff" * 32)
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)

    # The secret check precedes the parse, so no valid signature is needed to reach it.
    r = _post_token(tc, "a.b.c", bind)
    assert r.status_code == 503, r.text
    assert r.json()["error"] == "temporarily_unavailable", r.text
    assert "encodable" in r.json()["error_description"], r.text
    assert mint_spy == [], "an unprovisioned arm must not mint"


def test_a_short_secret_turns_the_arm_off_end_to_end(bridge_client, mint_spy, bearer_ok,
                                                    monkeypatch) -> None:
    """§2.3 — a guessable key is a forged-`sub` oracle, so it is UNPROVISIONED, end to end.

    Tokens are SIGNED, not encrypted: one legitimate token (any account holder can get one) is a
    complete known-plaintext HMAC pair, so a low-entropy key is brute-forceable OFFLINE and the
    attacker then mints `{"sub": <any user_id>}` — `sub` is the only identity input on this arm,
    so that is account takeover. The unit test next door covers the predicate; this covers what a
    caller actually sees: a typed 503, no mint, and the bearer path still working.
    """
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", "x")
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = _signed(_claim_set(bind), secret="x")

    r = _post_token(tc, token, bind)
    assert r.status_code == 503, r.text
    assert r.json()["error"] == "temporarily_unavailable", r.text
    assert "bytes" in r.json()["error_description"], r.text
    assert mint_spy == [], "a guessable key must not mint"

    # …and the arm being off must not take the bearer path with it.
    ok = _post_bearer(tc, bind)
    assert ok.status_code == 200, ok.text


def test_a_token_without_a_kid_is_refused(bridge_client, mint_spy, bearer_refuses) -> None:
    """§2.1 puts `kid` in the header for rotation (§2.3); a mint without one is refused."""
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)

    r = _post_token(tc, _signed(_claim_set(bind), kid=""), bind)
    assert r.status_code == 401, r.text
    assert "kid" in r.json()["error_description"], r.text
    assert mint_spy == []


@pytest.mark.parametrize("overrides", [
    # NaN: every ordering comparison is False for it, so without an explicit finiteness check
    # `iat = exp = NaN` never expires, never trips the TTL bound, AND is never pruned out of the
    # register (its stored expiry never arrives) — an immortal token, which is the one thing the
    # TTL exists to stop, plus a permanently occupied register slot.
    ({"iat": float("nan"), "exp": float("nan")}),
    ({"iat": float("inf"), "exp": float("inf")}),
])
def test_a_non_finite_timestamp_is_refused(bridge_client, mint_spy, bearer_refuses,
                                           overrides: dict) -> None:
    """§2.2's TTL bound is only a bound if the numbers are real numbers."""
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = _signed(_claim_set(bind, **overrides))

    r = _post_token(tc, token, bind)
    assert r.status_code == 401, r.text
    assert mint_spy == []


def test_a_token_that_is_not_base64url_is_refused_and_never_500s(
        bridge_client, mint_spy, bearer_refuses) -> None:
    """A LONE SURROGATE must be a typed refusal, not a 500 (#3026's class).

    `json.loads` accepts a `\\ud800` JSON escape, and encoding that string raises
    `UnicodeEncodeError`. Measured before the fix (review round 1): a `500 server_error` **plus** a
    captured Sentry event per request, from an unauthenticated caller — an error-tracker flood on a
    public endpoint, and a contradiction of this module's "never a 500" contract.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)

    # The case that reaches the SIGNATURE's own `.encode()`: a VALID header (alg/typ/kid are all
    # checked before the signature) plus a payload segment carrying the surrogate. Without a
    # charset check on every segment, that encode raises and the refusal becomes a 503 — typed, but
    # the wrong answer to a malformed token, and one captured event per unauthenticated request.
    header, payload_b64, signature = _signed(_claim_set(bind)).split(".")
    malformed_payload = f"{header}.{payload_b64}\ud800.{signature}"

    for token in ("\ud800", "a.\ud800.c", malformed_payload):
        # Sent as a JSON ESCAPE in raw bytes: httpx refuses to encode a lone surrogate itself (which
        # is why the first version of this test could not reach the server at all), while the AS's
        # `json.loads` decodes the escape into exactly the str that used to 500.
        payload = _consent_payload(tc, bind) | {"consent_token": token}
        r = tc.post("/oauth/consent", content=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"})
        assert r.status_code == 401, f"{token!r} must be a typed refusal: {r.status_code} {r.text}"
        assert r.json()["error"] == "invalid_token", r.text
    assert mint_spy == []


@pytest.mark.parametrize("build,why", [
    (lambda claims: _signed(claims, secret="not-the-configured-secret"), "wrong signing key"),
    # RFC 7519 §6 — the unsecured JWT. A verifier that read `alg` from the header instead of
    # requiring HS256 would accept a token anyone can write.
    (lambda claims: _signed(claims, alg="none"), "alg: none"),
    # Algorithm confusion: a header claiming an asymmetric alg while the signature is an HMAC.
    (lambda claims: _signed(claims, alg="RS256"), "alg confusion"),
    (lambda claims: _signed(claims, alg="HS512"), "alg substitution"),
    (lambda claims: ".".join(_signed(claims).split(".")[:2]), "two segments"),
    # The OTHER side of the same guard: the structure rule is `len == 3`, not `len >= 3`.
    # A trailing segment must be REFUSED, not ignored — a verifier that ignores it
    # disagrees with every other consumer about what the token IS, which is the
    # differential a smuggling class starts from. Measured: with the guard weakened to
    # `< 3` the whole file stayed green (`107 passed`) and `h.p.s.junk` verified, because
    # `parts[3:]` is never looked at.
    (lambda claims: _signed(claims) + ".junk", "four segments"),
])
def test_a_forged_token_is_refused(bridge_client, mint_spy, bearer_refuses, build, why: str) -> None:
    """§2.1 — HMAC-SHA256 and nothing else; the signature is checked before any claim is read."""
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = build(_claim_set(bind))

    r = _post_token(tc, token, bind)
    assert r.status_code == 401, f"{why}: {r.text}"
    # ⛔ The status alone does not prove this test's subject: with the whole token arm reverted,
    # all five cases still answered 401 — from the BEARER path (`bearer_refuses`), which never
    # looked at the token. Measured in review round 2. `invalid_token` is the arm's own code.
    assert r.json()["error"] == "invalid_token", f"{why}: {r.text}"
    assert mint_spy == []


def test_a_tampered_payload_is_refused(bridge_client, mint_spy, bearer_refuses) -> None:
    """The signature must cover the payload — flipping a claim must not survive verification."""
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    header, _payload, signature = _signed(_claim_set(bind)).split(".")
    forged = _seg(_claim_set(bind, user_id="smuggled") | {"sub": "someone-else"})
    token = f"{header}.{forged}.{signature}"

    r = _post_token(tc, token, bind)
    assert r.status_code == 401, r.text
    assert "signature" in r.json()["error_description"], r.text
    assert mint_spy == []


def test_a_replayed_jti_is_refused_and_only_one_code_is_ever_minted(bridge_client, mint_spy,
                                                                   bearer_refuses) -> None:
    """§2.2/§2.3 — `jti` is single-use, and a TTL is NOT a substitute for it.

    Without the register one consent yields N authorization codes. The second request here is
    otherwise IDENTICAL to the first (same binding, same flow), so the only thing that can refuse
    it is the spent `jti` — and the mint count is what proves no second code was issued.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind)

    first = _post_token(tc, token, bind)
    assert first.status_code == 200, first.text
    second = _post_token(tc, token, bind)
    assert second.status_code == 401, second.text
    assert "already" in second.json()["error_description"], second.text
    assert len(mint_spy) == 1, "a replay must not mint a second authorization code"


def test_a_request_refused_before_the_token_is_checked_does_not_spend_the_jti(
        bridge_client, mint_spy, bearer_refuses) -> None:
    """ORDER — the `jti` is spent at the mint, never at parse time (§2.3).

    Burning the nonce on a request that could never mint would be a denial-of-service on the real
    flow: anyone who can make the victim's browser send one malformed consent POST (an
    unregistered `redirect_uri` is enough) would invalidate the token the page is about to use.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind)

    refused = _post_token(tc, token, bind, redirect_uri="https://not-registered.example/cb")
    assert refused.status_code == 400, refused.text
    assert mint_spy == []

    # ⛔ And the case the params-stage refusal CANNOT reach: a refusal of the ASSERTION itself.
    # A verifier that spent the nonce while refusing (burn-at-verify) is invisible to the check
    # above — `validate_authorize_params` never lets it run — and it is the same denial of service,
    # from a request anyone can send: one bind-mismatch attempt would invalidate the token the page
    # is about to use. Measured: the burn-at-verify mutant passed this test before this block.
    _, other_challenge = _pkce()
    mismatched = _post_token(tc, token, bind, code_challenge=other_challenge)
    assert mismatched.status_code == 401, mismatched.text
    assert mint_spy == []

    # …and the third refusal that CAN be evaluated before the spend, but only if someone does:
    # `_resolve_org` (inside the mint) parses the RFC 8707 `resource` and refuses a malformed one.
    # That check is pure, so it runs first — a caller typo must not consume the consent.
    bad_resource = _post_token(tc, token, bind, resource=f"{TEST_BASE}/nope")
    assert bad_resource.status_code == 400, bad_resource.text
    assert mint_spy == []

    accepted = _post_token(tc, token, bind)
    assert accepted.status_code == 200, (
        "a refused assertion or resource must leave the token usable — the jti was spent too "
        f"early: {accepted.text}")


def test_a_mint_failure_after_the_spend_consumes_the_token_fail_closed(
        bridge_client, monkeypatch, bearer_refuses) -> None:
    """THE DOCUMENTED RESIDUAL, pinned so it cannot drift into an unstated claim.

    The `jti` is recorded before the code is inserted (so one consent can never yield two codes),
    and `issue_auth_code` can then still refuse — no active team, a suspended org, a transient
    control-plane failure. Those consume the nonce. The honest consequences, both asserted here:
    the first attempt reports the REAL cause, and the retry reports "already redeemed" — a
    RE-MINT, never a re-login. If a later change makes the nonce recoverable, this test reds and
    the docstring / spec §9 paragraph must be updated with it.
    """
    import tortoise.oauth as oauth_module
    from tortoise.oauth import OAuthTemporarilyUnavailable

    def _boom(cp, **kwargs):
        raise OAuthTemporarilyUnavailable(
            "Temporary control-plane failure — retry.")

    monkeypatch.setattr(oauth_module, "issue_auth_code", _boom)
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind)

    first = _post_token(tc, token, bind)
    assert first.status_code == 503, first.text
    assert first.json()["error"] == "temporarily_unavailable", first.text

    second = _post_token(tc, token, bind)
    assert second.status_code == 401, second.text
    assert "already" in second.json()["error_description"], second.text


def test_an_unset_secret_leaves_the_bearer_path_working_and_the_token_arm_unavailable(
        bridge_client, monkeypatch, mint_spy, bearer_ok) -> None:
    """§9 step 2's additivity, measured: no secret → the arm is UNAVAILABLE, the bearer is intact.

    This is the partially-provisioned state §8's checklist warns about (the BFF minting, the AS
    without the secret). The token arm must fail CLOSED and TYPED — a 503 the page can act on,
    never a 500 and never a silent fall-through to the bearer, which would answer "missing session
    token" for a request that never had one.
    """
    monkeypatch.delenv("CONSENT_TOKEN_SECRET", raising=False)
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)
    # minted while the secret existed — the token is well-formed and simply cannot be checked here
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind,
                               secret=_TEST_SECRET)

    unavailable = _post_token(tc, token, bind)
    assert unavailable.status_code == 503, unavailable.text
    assert unavailable.json()["error"] == "temporarily_unavailable", unavailable.text
    assert mint_spy == [], "an unavailable arm must not mint"

    worked = _post_bearer(tc, bind)
    assert worked.status_code == 200, worked.text
    assert mint_spy[0]["user_id"] == _U1


def test_the_bearer_path_never_consults_the_token_arm(bridge_client, monkeypatch,
                                                      mint_spy, bearer_ok) -> None:
    """§9 step 2 is ADDITIVE — the bearer request must not reach the token machinery at all.

    Proven by making the token functions explode: a bearer request that so much as calls them
    fails here. Seeded with the secret PRESENT, so "the arm is configured" is not what keeps this
    green.
    """
    from tortoise import consent_token
    from tortoise.consent_token import (
        ProcessLocalJtiStore,
        configure_consent_jti_store,
        reset_consent_jti_store,
    )

    def _boom(*args, **kwargs):
        raise AssertionError("the bearer path consulted the consent-token arm")

    monkeypatch.setattr(consent_token, "verify_consent_token", _boom)

    # ⛔ …and a SECOND, hoist-proof probe. Patching a module attribute above only works because the
    # handler imports these names inside the function: hoisting that import to module scope (a
    # behaviourally identical, lint-preferable refactor) would silently make it observe nothing —
    # measured in review round 2, where a real regression stayed GREEN after the hoist. The store
    # is resolved from the module global at CALL time, so a store that records use survives any
    # import style. Assert on the observation, not on a side effect of the import.
    consulted: list[str] = []

    class _RecordingStore(ProcessLocalJtiStore):
        async def consume(self, jti: str, expires_at: float) -> bool:
            consulted.append(jti)
            return await super().consume(jti, expires_at)

    configure_consent_jti_store(_RecordingStore())
    try:
        tc, _ = bridge_client
        client_id = _register_client(tc)["client_id"]
        verifier, challenge = _pkce()
        bind = _bind(client_id, challenge)

        r = _post_bearer(tc, bind)
        assert r.status_code == 200, r.text
    finally:
        reset_consent_jti_store()
    assert consulted == [], "the bearer path reached the jti register — §9 step 2 is additive"

    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    verifier, challenge = _pkce()
    bind = _bind(client_id, challenge)

    r = _post_bearer(tc, bind)
    assert r.status_code == 200, r.text
    tok = _exchange(tc, client_id=client_id, code=r.json()["code"], verifier=verifier)
    assert tok.status_code == 200, tok.text


@pytest.mark.parametrize("value", ["", "   ", False, 0, [], {}])
def test_a_present_but_falsy_token_never_falls_back_to_the_bearer(
        bridge_client, mint_spy, bearer_ok, value) -> None:
    """§9 step 2 — a PRESENT token arm is the only identity input, even when its value is junk.

    `consent_token = body.get("consent_token")` then `if consent_token is None:` is the only branch
    that decides which identity source authenticates the request. `bearer_ok` seeds a session that
    WOULD mint (verified below with `_post_bearer`), so a fall-through is observable as a 200
    rather than as an accident of an unverifiable bearer — and the idiomatic rewrite
    `if not consent_token:` turns every row here into exactly that fall-through: a request that
    asserts an identity gets the SESSION's code, which is the mixed-version shape §9's #3559
    window says must never happen. Asserted on the arm's own error code, because the bearer path
    also 401s.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)

    # The control: the seeded session DOES mint when no token arm is present at all.
    control = _post_bearer(tc, bind)
    assert control.status_code == 200, control.text
    assert len(mint_spy) == 1, "the bearer path must mint, or the fall-through is unobservable"
    mint_spy.clear()

    r = _post_token(tc, value, bind)
    assert r.status_code == 401, r.text
    assert r.json().get("error") == "invalid_token", (
        f"a present-but-falsy consent_token ({value!r}) was not refused BY THE TOKEN ARM: "
        f"{r.text}")
    assert mint_spy == [], (
        f"a request carrying consent_token={value!r} minted a code from the BEARER — the token "
        "arm silently fell through to the session identity")


def test_the_bound_redirect_uri_wins_over_a_relaxed_loopback_variant(bridge_client, mint_spy,
                                                                    bearer_refuses) -> None:
    """§2.2 + §9 step 2 — the two rules meet only in this case, so it is pinned.

    `bind.redirect_uri` is compared with the AS's own normalisation, and for loopback that
    normalisation IGNORES the port (RFC 8252 §7.3). When the request names a different port for
    the same destination, the comparison passes — and §9 step 2 still says the mint takes the
    **bound** value, so the code is bound to the URI the token approved rather than to whatever
    was presented last. That direction is fail-closed: `/oauth/token` compares `redirect_uri`
    exactly, so a divergent presentation can never redeem the code, and no code is ever issued
    pointing at a destination the token did not name.
    """
    tc, _ = bridge_client
    client_id = _register_client(tc)["client_id"]
    _, challenge = _pkce()
    bind = _bind(client_id, challenge)                     # …:8765/callback
    token = mint_consent_token(user_id=_U1, email="u@example.com", bind=bind)

    r = _post_token(tc, token, bind, redirect_uri="http://127.0.0.1:9999/callback")
    assert r.status_code == 200, r.text
    assert mint_spy[0]["redirect_uri"] == bind["redirect_uri"], (
        "the mint must carry the token's bound redirect_uri (§9 step 2), not the presented one")
    assert r.json()["redirect_uri"] == bind["redirect_uri"]
    # ⛔ And `redirect_target` — the field the page actually NAVIGATES the browser to — must name
    # the same URI. It is rebuilt from the registered URI, and an earlier version rebuilt it from
    # the PRESENTED value: the response then told the page two different destinations, and the code
    # (bound to the token's URI, compared EXACTLY by /oauth/token) would have been unredeemable
    # after the user approved. Both fields now derive from the bound value.
    assert r.json()["redirect_target"] == bind["redirect_uri"], (
        "the browser would have been sent to a destination the code is not bound to: "
        f"redirect_target={r.json()['redirect_target']!r} vs {bind['redirect_uri']!r}")


def test_the_register_outlives_the_tokens_last_valid_instant() -> None:
    """§2.2 — the replay window must be CLOSED at the token's last valid instant, not half-open.

    The verifier refuses an expired token only when `exp + LEEWAY < now`, so at EXACT equality the
    token still verifies. The register therefore has to keep the `jti` at that instant; dropping it
    on `<=` opens a (one-microsecond) replay hole and makes the class's own comment false. Driven
    directly on the store, because no HTTP request can land on an exact expiry instant.
    """
    import asyncio

    from tortoise.consent_token import CONSENT_TOKEN_LEEWAY_S, ProcessLocalJtiStore

    frozen = 1_000_000.0
    store = ProcessLocalJtiStore(clock=lambda: frozen)
    # The store keys the entry on `exp + LEEWAY`, so this expiry puts the entry's own expiry at
    # EXACTLY `now` — the instant the verifier still accepts the token (`exp + LEEWAY < now` is
    # false) and the instant a `<=` prune would have already forgotten it.
    expires_at = frozen - CONSENT_TOKEN_LEEWAY_S
    assert asyncio.run(store.consume("boundary-jti", expires_at)) is True
    assert asyncio.run(store.consume("boundary-jti", expires_at)) is False, (
        "the jti was pruned at the very instant the token is still valid, so it could be replayed")


def test_no_module_in_the_package_mints_a_consent_token() -> None:
    """§2.3 / §9 step 3 — the mint belongs on the SESSION origin (a BFF route), never here.

    `mint_consent_token` exists as the reference inverse so the wire format has two checkable
    sides. Shipping it from a Python route would let the service that VERIFIES the assertion also
    MINT it, which is exactly the split §2.3 exists to preserve (the mint must run where the
    session cookie is). That rule was prose in a docstring — and a docstring cannot fail — so it is
    executed here. An AST scan, not a grep: a comment or a log line naming the function is not a
    false positive, and an alias or attribute call is not a false negative.

    Its LIMIT, stated because a guard that overstates its reach is worse than none: it sees STATIC
    references — `from ... import`, `import ... as`, `module.attr`, a bare name — and not a name
    assembled at runtime (`getattr(mod, "mint_" + "consent_token")` escapes it, measured in review).
    That is the right scope for the purpose: catching an innocent route addition. A deliberate
    obfuscation is a code-review matter, not a scan's.
    """
    import ast

    offenders: list[str] = []
    for path in sorted((REPO / "tortoise").rglob("*.py")):
        # By PATH, not basename: a module that merely SHARES the name must not be exempt.
        if path == REPO / "tortoise" / "consent_token.py":
            continue                      # the reference implementation itself
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            hit = (
                (isinstance(node, ast.ImportFrom)
                 and any(a.name == "mint_consent_token" for a in node.names))
                or (isinstance(node, ast.Import)
                    and any("mint_consent_token" in a.name for a in node.names))
                or (isinstance(node, ast.Attribute) and node.attr == "mint_consent_token")
                or (isinstance(node, ast.Name) and node.id == "mint_consent_token")
            )
            if hit:
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")

    assert offenders == [], (
        "a consent token may only be minted on the session origin (§2.3/§9 step 3) — this package "
        f"must not mint one: {offenders}")


def test_the_contract_in_the_code_is_the_contract_in_the_spec() -> None:
    """The verifier and the spec must state the SAME contract, or "the spec is the authority"
    is unenforceable: a value changed in one place would leave the other describing a different
    token.

    Also pins that each guarantee's source section is cited IN THE MODULE (the brief's own
    requirement: the next reader checks the code against the contract, not against prose), so a
    rewrite cannot quietly drop the mapping.
    """
    from tortoise import consent_token as ct

    spec = SPEC.read_text()
    assert ct.BIND_FIELDS == ("client_id", "redirect_uri", "code_challenge", "state"), \
        "the bind set must be exactly the four params §2.1 names — in the spec's order"
    assert ct.CONSENT_TOKEN_AUDIENCE == "tortoise-consent"
    assert ct.CONSENT_TOKEN_TTL_S == 120
    assert ct.CONSENT_TOKEN_LEEWAY_S == 30

    # …and both halves of each fact still appear in the spec.
    for clause in ('"client_id", "redirect_uri", "code_challenge", "state"',
                   '"aud": "tortoise-consent"',
                   "CONSENT_TOKEN_TTL_S",
                   "120 s",
                   "30 s leeway",
                   "CONSENT_TOKEN_SECRET",
                   "must equal the configured session origin"):
        assert clause in spec, f"§2 no longer states {clause!r}"

    source = (REPO / "tortoise" / "consent_token.py").read_text()
    for section in ("§2.1", "§2.2", "§2.3", "§9"):
        assert section in source, (
            f"tortoise/consent_token.py no longer cites {section} — the guarantee it implements "
            "is then checkable only against the code's own prose")
