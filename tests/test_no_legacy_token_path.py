"""The BFF invariant gate — #3501.

THE INVARIANT
-------------
Under the BFF, the browser holds exactly one credential: an HttpOnly `__Host-session`
cookie it cannot read. Therefore:

  (A) No browser-reachable surface may READ or HOLD session material — no JS-readable
      session cookie, no localStorage/sessionStorage token, no `supabase.auth` client session.
  (B) Every client data call must go through the same-origin BFF (`/api/...`), because the
      BFF is the only thing that holds the access token.

WHY THIS TEST EXISTS
--------------------
W1's suite passes 20/20 while `welcome.html` and the blog-admin data layer are still wired to
the deleted token. Unit tests exercise the layer being edited; they cannot see that a *consumer*
was left pointing at a credential that no longer exists. Three user-facing surfaces were broken
under a fully green suite.

This is a STATIC gate. It is deliberately blunt: it asserts absence, repo-wide, so that a
surface cannot be silently left behind. Absence-by-pointer cannot be gamed by paraphrasing,
which is why it is a grep and not a review.

NON-VACUITY
-----------
A gate that scans nothing passes. Every scan asserts it inspected a plausible number of files,
so a broken glob (or a moved directory) fails loudly instead of reporting clean.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEBSITE = REPO / "website"

# Directories that are not browser-served source.
SKIP_DIRS = {"node_modules", "dist", ".git", "archive", ".worktrees", "vendor", ".wrangler"}

# The legacy cross-subdomain bridge: a JS-readable, parent-domain cookie holding a session.
LEGACY_TOKEN_MARKERS = (
    "sb-tortoise-auth-token",
    "createTortoiseSupabaseClient",
)

# Browser surfaces that are MIGRATED (or must be). These are the files the BFF owns.
#
# #4054: `website/signup.html` is the /auth page — the FRONT DOOR — and it was missing
# from this list, so the gate could not see that it was still on the legacy client
# (it loads /assets/supabase-session.js and calls supabaseClient.auth.* in 5 places).
# Listing it is what makes the gate able to fail on the file that matters most.
MIGRATED_SURFACES = (
    "website/apps/dashboard/public/welcome.html",
    "website/apps/dashboard/public/signup.html",
    "website/apps/blog-admin/src/lib/blog-api.ts",
    "website/apps/blog-admin/src/hooks/useAuth.ts",
    "website/apps/dashboard/src/main.jsx",
)

# The proxy endpoint every migrated client data call must route through.
PROXY_PREFIX = "/api/v1"

# Server-side Pages Functions trees. Browser-reachable source and server source are
# DIFFERENT trust domains: the BFF legitimately builds `Authorization: Bearer …`
# (that is how it talks to GoTrue) and it contains PROXY_PREFIX because it IS the
# proxy. Both facts are defects in a browser file and correct in a server file, so
# these trees are excluded from the browser-source assertions.
#
# #4054: the BFF moved from the marketing project (`website/functions/`) to the app
# project (`website/apps/dashboard/functions/`). Both roots are listed because
# `blog/**` deliberately remains in the former (and #4171 moved `admin/**` to the
# latter with the console) — dropping either root would start scanning those
# server functions as browser code.
SERVER_FUNCTION_ROOTS = (
    "website/functions/",
    "website/apps/dashboard/functions/",
)


def _is_server_source(p: Path) -> bool:
    """True when the file is server-side Functions code, not browser source."""
    return str(p.relative_to(REPO)).startswith(SERVER_FUNCTION_ROOTS)


# Copy/display modules: they contain `Authorization: Bearer …` strings because they SHOW
# the user how to configure *their own* agent — the harness install snippets, and a
# truncated `curl` example rendered in a <code> element. The credential in those strings
# is the user's agent key (`TORTOISE_API_KEY`), not the dashboard session, and none of
# these files performs network I/O, so none can be a credential path.
#
# This is NOT a blanket escape from the assertion. The exemption is guarded by
# `test_copy_only_exemptions_perform_no_network_io`, which is deliberately UNMARKED (no
# xfail): it asserts every file listed here performs no network I/O, so the moment one
# gains a `fetch` the suite turns red and the exemption must be removed. It USED to be a
# loop inside `test_no_client_holds_a_bearer_token`, which is `xfail(strict=False)` — a
# non-strict xfail reports the guard's assertion failure as XFAIL, so the run stayed green
# (appending `fetch("/x")` to harnesses.js yielded `1 xfailed`, exit 0). A guard that
# cannot fail is not a guard. A new offender anywhere else still fails normally.
COPY_ONLY_SOURCES = (
    "website/apps/dashboard/src/harnesses.js",
    "website/apps/dashboard/src/harnesses.test.js",
    "website/apps/dashboard/src/overviewEmptyAction.js",
    # docs.html RENDERS install/config snippets for the user's own agent (in <code>
    # blocks, with no <script> at all). The `${TORTOISE_API_KEY}` /
    # `${env:TORTOISE_API_KEY}` forms are the config-file syntax Claude Code, Cursor and
    # Pi require verbatim — so they must be shown, not rewritten. The file performs no
    # network I/O, so it cannot be a credential path (guarded below).
    "website/docs.html",
)

_NETWORK_IO = re.compile(
    r"\b(?:fetch|XMLHttpRequest|axios|sendBeacon)\s*[(.]|\bcredentials\s*:|\bnavigator\.sendBeacon"
)

# Client-surface migration is tracked in #3559. TWO invariants still fail because the
# remaining browser surfaces have not migrated; they are xfail — NOT deleted and NOT
# skipped — so the obligation stays visible and the gate keeps naming the offenders.
#
# The other three checks in this file whose surfaces DID migrate carry NO marker:
# a non-strict xfail cannot fail, so it is not a gate — the moment a regression
# appears it flips XFAIL and CI stays green. Removing the marker is the only state in
# which the check can actually redden. Do the same for each remaining marker when its
# invariant passes; do NOT "flip to strict" — a strict xfail still reports a passing
# test as XPASS and still cannot gate a regression.
CLIENT_MIGRATION = "client-surface migration outstanding — see #3559"


def _browser_sources():
    """Yield browser-reachable source files (html/js/ts/tsx/jsx), excluding build output."""
    out = []
    for p in WEBSITE.rglob("*"):
        if not p.is_file():
            continue
        # Skip on the path RELATIVE to WEBSITE. Using p.parts here would match `.worktrees`
        # in the absolute prefix (the repo lives under .worktrees/ in this worktree) and
        # silently discard every file — a broken gate that reports clean.
        if any(part in SKIP_DIRS for part in p.relative_to(WEBSITE).parts):
            continue
        if p.suffix.lower() not in {".html", ".js", ".ts", ".tsx", ".jsx", ".mjs"}:
            continue
        out.append(p)
    return sorted(out)


@pytest.fixture(scope="module")
def sources():
    srcs = _browser_sources()
    # NON-VACUITY: a broken glob must not read as "clean".
    assert len(srcs) > 40, (
        f"scan found only {len(srcs)} browser sources under {WEBSITE} — the glob is broken, "
        "so every assertion below would pass vacuously"
    )
    return srcs


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _strip_comments(text: str, suffix: str) -> str:
    """Remove comments before scanning for markers.

    A marker NAMED in a comment is documentation, not a live path — and this gate
    itself is the reason those comments exist (each says what was removed and
    why). Matching prose made the gate report a file whose only remaining mention
    was the explanation of its own removal.
    """
    if suffix.lower() == ".html":
        return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    out = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Line comments, but NOT the `//` in `https://`.
    return re.sub(r"(?<!:)//[^\n]*", "", out)


def _code(p: Path) -> str:
    """File contents with comments removed."""
    return _strip_comments(_read(p), p.suffix)


@pytest.mark.xfail(reason=CLIENT_MIGRATION, strict=False)
def test_no_legacy_js_readable_token_anywhere(sources):
    """(A) The legacy JS-readable session cookie must be gone from every browser surface.

    It is parent-domain and non-HttpOnly by construction — the exact artifact §1 exists to
    remove. A single surviving reference re-opens the cross-subdomain bridge.
    """
    offenders = []
    for p in sources:
        text = _code(p)
        for marker in LEGACY_TOKEN_MARKERS:
            if marker in text:
                rel = p.relative_to(REPO)
                lines = [i for i, ln in enumerate(text.splitlines(), 1) if marker in ln]
                offenders.append(f"  {rel}:{lines} references {marker!r}")
    assert not offenders, (
        "legacy JS-readable token path still present in browser source:\n"
        + "\n".join(offenders)
        + "\n\nUnder the BFF nothing writes this token, so every consumer of it is broken."
    )


def test_migrated_surfaces_exist():
    """Every surface the BFF owns must still exist, so the migration gate cannot pass vacuously.

    `test_migrated_surfaces_do_not_use_supabase_auth_client` records a missing surface as an
    offender too, but this dedicated test keeps the failure independent of the scan
    patterns and names the vanished surface directly, so a renamed or deleted surface is a
    hard failure rather than a silently empty migration check.
    """
    missing = [rel for rel in MIGRATED_SURFACES if not (REPO / rel).exists()]
    assert not missing, (
        "the BFF-owned surfaces below are missing, so the migration gate would pass "
        "vacuously (a surface that no longer exists cannot be checked for a legacy client):\n"
        + "\n".join(f"  {rel}" for rel in missing)
    )


def test_migrated_surfaces_do_not_use_supabase_auth_client():
    """(A) No `supabase.auth` client session in a migrated surface.

    The BFF holds the session server-side; a client that still calls `getSession()` will get
    `null` and — worse — `onAuthStateChange` will never fire, so the UI cannot react to auth
    state it can no longer observe.
    """
    patterns = (
        re.compile(r"supabaseClient\.auth\.|supabase\.auth\.(getSession|onAuthStateChange)"),
    )
    offenders = []
    for rel in MIGRATED_SURFACES:
        p = REPO / rel
        if not p.exists():
            offenders.append(f"  {rel}: MISSING (expected a migrated surface)")
            continue
        text = _code(p)
        for pat in patterns:
            for m in pat.finditer(text):
                line = text[: m.start()].count("\n") + 1
                offenders.append(f"  {rel}:{line} uses {m.group(0)}")
    assert not offenders, (
        "client-side auth session still referenced in a migrated surface:\n"
        + "\n".join(offenders)
    )


def test_copy_only_exemptions_perform_no_network_io(sources):
    """The COPY_ONLY_SOURCES exemption must be able to FAIL.

    This test is deliberately UNMARKED. The same assertion used to live inside
    `test_no_client_holds_a_bearer_token` while that test was `xfail(strict=False)` — so a
    copy-only file gaining a network call made it fail *as expected* and the suite stayed
    green. A non-strict xfail turns the guard's failure into XFAIL, and a guard that cannot
    fail is not a guard (the exact class this PR exists to close). Keeping this guard in a
    test that carries no marker means it can redden even while the two remaining #3559
    invariants are still xfail.

    Taking `sources` here also pins the module-level non-vacuity assertion (the
    browser-source scan found >40 files) to a test that cannot be xfailed, so a broken glob
    can no longer hide behind the two remaining xfail markers either.
    """
    scanned = {str(p.relative_to(REPO)) for p in sources}
    for rel in COPY_ONLY_SOURCES:
        p = REPO / rel
        assert p.exists(), f"copy-only exemption lists a missing file: {rel}"
        assert rel in scanned, (
            f"{rel} is exempt from the Bearer check, but the browser-source scan never "
            "sees it — a dead exemption leaves the file silently unguarded"
        )
        hit = _NETWORK_IO.search(_code(p))
        assert not hit, (
            f"{rel} is exempt from the Bearer check as a copy-only module, but it now "
            f"performs network I/O (`{hit.group(0)}`) — the exemption is no longer safe. "
            "Migrate the call to the BFF and remove it from COPY_ONLY_SOURCES."
        )


def test_no_client_holds_a_bearer_token(sources):
    """(A) No browser surface may construct an Authorization header from client state.

    The proxy strips + re-sets Authorization server-side. A client that builds one is either
    holding a token it should not have, or sending a header that will be discarded — both
    indicate the surface was not migrated.
    """
    # Non-vacuity for the COPY_ONLY exemption lives in the UNMARKED
    # `test_copy_only_exemptions_perform_no_network_io` — it must not sit inside a test
    # that cannot fail, where its failure would be swallowed as XFAIL.
    pat = re.compile(r"Bearer\s*\$\{")

    offenders = []
    for p in sources:
        # Server code is ALLOWED to build a Bearer header — that is how the BFF talks to
        # GoTrue/api.premiselabs.co, and it is the whole point of the proxy. Only a
        # *browser* building one is a defect, because it means the browser holds a token.
        if _is_server_source(p):
            continue
        # Copy-only display modules (guarded above).
        if str(p.relative_to(REPO)) in COPY_ONLY_SOURCES:
            continue
        text = _read(p)
        for m in pat.finditer(text):
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"  {p.relative_to(REPO)}:{line}")
    assert not offenders, "client-constructed Bearer headers remain:\n" + "\n".join(offenders)


def test_the_proxy_has_a_caller(sources):
    """(B) The BFF proxy must actually be used by a client.

    A proxy with no caller is scaffolding. This asserts the migration reached the data layer,
    which is precisely where W2 stopped: the three `Bearer` sites were converted while the
    underlying `supabase.storage` calls still rode the legacy client.
    """
    callers = [
        p.relative_to(REPO)
        for p in sources
        if PROXY_PREFIX in _read(p)
        # Server code is not a caller. The proxy source itself contains the string, so
        # scanning it would let the proxy "prove" its own existence — a false pass.
        and not _is_server_source(p)
    ]
    assert callers, (
        f"no client surface calls {PROXY_PREFIX} — the W6 proxy has no caller, so every "
        "client data call is still going somewhere else (probably a JS-readable token path)"
    )


@pytest.mark.xfail(reason=CLIENT_MIGRATION, strict=False)
def test_client_data_layer_does_not_import_the_legacy_supabase_client():
    """(B) The blog-admin data layer must not reach for the legacy client.

    `@/lib/supabase` stores its session in the JS-readable parent-domain cookie. Any call
    through it is unauthenticated under the BFF and will fail RLS.
    """
    p = REPO / "website/apps/blog-admin/src/lib/blog-api.ts"
    assert p.exists(), f"{p} missing"
    text = _read(p)
    offenders = []
    for m in re.finditer(r"from\s+'@/lib/supabase'", text):
        line = text[: m.start()].count("\n") + 1
        offenders.append(f"  blog-api.ts:{line}")
    for m in re.finditer(r"\bsupabase\.(storage|from)\(", text):
        line = text[: m.start()].count("\n") + 1
        offenders.append(f"  blog-api.ts:{line} calls {m.group(0)} via the legacy client")
    assert not offenders, (
        "blog-admin data layer still uses the legacy Supabase client:\n"
        + "\n".join(offenders)
    )


def test_welcome_page_does_not_load_a_client_auth_library():
    """(A) `/welcome` must not load supabase-js or build a client.

    /welcome is gated SERVER-SIDE by functions/welcome.ts. The page must render, not
    authenticate. It previously called `window.createTortoiseSupabaseClient` — a factory
    defined in a script the page never loaded — so every successful login fell into the
    "temporarily unavailable" branch. That is the #3485 loop's sibling.
    """
    # #4054: /welcome moved to the APP origin with the rest of the BFF surfaces
    # (it is served by `functions/welcome.ts` from the dashboard project).
    p = REPO / "website/apps/dashboard/public/welcome.html"
    assert p.exists(), f"{p} missing"
    text = _code(p)
    offenders = []
    if "@supabase/supabase-js" in text:
        line = next(i for i, ln in enumerate(text.splitlines(), 1) if "@supabase/supabase-js" in ln)
        offenders.append(f"  welcome.html:{line} loads supabase-js")
    if "createTortoiseSupabaseClient" in text:
        line = next(
            i for i, ln in enumerate(text.splitlines(), 1) if "createTortoiseSupabaseClient" in ln
        )
        offenders.append(f"  welcome.html:{line} builds a client session")
    assert not offenders, "/welcome still authenticates client-side:\n" + "\n".join(offenders)


def test_error_semantics_never_conflate_store_fault_with_signed_out(sources):
    """A D1/store fault must not be reported as "not signed in".

    This is the #3485 bug class: a transient fault answered as 401 tells the browser to clear
    state, so an outage signs the user out and then loops. `admin-auth.ts` documents the
    distinction as load-bearing and then erases it.
    """
    p = REPO / "website/functions/blog/_shared/admin-auth.ts"
    assert p.exists(), f"{p} missing"
    text = _read(p)
    # A resolver that collapses "unavailable" into the same value as "no session" erases the
    # distinction its own callers depend on.
    bad = re.search(r'if\s*\(resolved\s*&&\s*"unavailable"\s+in\s+resolved\)\s*return\s+null', text)
    assert not bad, (
        "admin-auth.ts collapses `unavailable` into `null`, which its callers emit as 401 — "
        "the #3485 class. A store fault must surface as 503, never as signed-out."
    )


# ── The docs must not silently reverse the topology decision (#4239) ─────────────────
#
# The parent-domain cookie is not a bug in the code; it is a DECISION (the auth-topology ruling
# on #3501/#4054; full rationale in the private premise-labs `engineering/auth/SCOPE.md` §3,
# §4 W6, §13) that a reader without the history re-derives as "the obvious way to share a
# session across subdomains" — §1.3 of docs/auth-architecture.md sets out exactly that standard.
# Two docs restated it as the live design after #4054 moved the session, and no gate noticed,
# because a docs claim has no test to fail. These tests are that test.
#
# THE DOC SET IS DECLARED, NOT GLOBBED — deliberately, and this is the gate's known limit.
# The repo holds ~550 markdown files, and well over a dozen legitimately describe the
# parent-domain cookie as the design OF THEIR TIME (docs/epics/2026-08-07-tortoise-user-journeys/,
# docs/plans/2026-08-19-1511-auth-unification.md, docs/scoping/*, docs/plans/2026-09-10-1701-*).
# Globbing `docs/**` would fail on the historical RECORD rather than on a regression — so every
# doc that describes the CURRENT web/auth architecture is listed here instead:
# ADD A DOC TO THIS TUPLE WHEN YOU ADD ONE THAT DESCRIBES THE CURRENT DESIGN.

ARCH_DOCS = (
    REPO / "website/website_architecture.md",
    REPO / "docs/auth-architecture.md",
)

# A line names OUR cross-subdomain cookie/domain …
#
# Broad by design. The first cut matched only `parent-domain` / `Domain=.premiselabs.co`, so a
# restatement naming the ACTUAL legacy artifact — `sb-tortoise-auth-token` on `.premiselabs.co`,
# or the same thing as a bare table row "cookie on `.premiselabs.co`, shared by all subdomains" —
# walked straight through. Proved by mutation during review (#4239).
#
# A bare `\.premiselabs\.co` is deliberately NOT a trigger: `_SHARED_SESSION` below matches the
# ubiquitous word `session`, so the bare domain would fire on legitimate prose naming
# `app.premiselabs.co`, the session origin we WANT documented. The sharing is what is rejected.
_PARENT_DOMAIN_COOKIE = re.compile(
    r"parent[ -]domain"
    r"|Domain\s*=\s*\.?premiselabs\.co"
    r"|cross-subdomain"
    r"|across\s+(?:all\s+)?subdomains"
    r"|\b(?:all|any|every|each|both)\s+hosts?"
    r"|\b(?:all|any|every|each|both)\s+subdomains?"
    r"|\b(?:any|all of)\s+[\w.'’-]{2,}\s+hosts?"
    r"|root domain"
    r"|apex domain"
    r"|naked domain"
    r"|top-level domain"
    r"|whole domain"
    r"|registrable domain"
    r"|site-wide"
    r"|for the (?:whole|entire) (?:site|domain|product|app)"
    r"|domain attribute"
    r"|(?:no|without|lacks?|drops?|dropped|removes?|removed)\s+(?:the\s+)?`?__Host-`?\s*prefix"
    r"|sibling host"
    r"|under\s+\.?premiselabs\.co"
    r"|api\.premiselabs\.co"
    r"|sb-tortoise-auth-token",
    re.IGNORECASE,
)
# A NEGATION, claim-local. This is deliberately NOT the marker list: a marker says "this is the
# rejected design", a negation says "and it is absent", which is a correct statement of the
# shipped model. Both are scoped to the sentence carrying the trigger, and the cues are phrases
# that negate the SHARING — not bare `no`/`not`/`without`, which appear inside affirmations of the
# bad design ("shared across all subdomains without any host-only restriction"). Both the
# over-broad and the too-narrow versions were proved wrong during review (#4239).
#
# PHRASES, NOT VERBS. `unusable`, `cannot`, `can't`, `forfeits?`, `prevents` and `only the app`
# were cues here and are NOT: each is a verb that sits happily in a SUBORDINATE premise, and the
# scope is the sentence, so a restatement was excused by its own rationale clause —
#   "Because a host-only cookie cannot authenticate a sibling host, the session cookie is shared
#    across all subdomains via a parent-domain cookie."
# passed this gate (proved during review, #4239). `unusable` survives only as a predicate ON the
# credential (`session cookie unusable`), which is a statement about the cookie, not about a
# premise. The residual limit — a sentence that both denies and asserts sharing — is named in
# `_claim_lines`.
_NEGATION = re.compile(
    r"no cookie"
    r"|no session cookie"
    r"|not shared"
    r"|never shared"
    r"|no `domain`"
    r"|no domain attribute"
    r"|(?:cookie|session)\s+(?:is\s+|are\s+|be\s+)?unusable",
    re.IGNORECASE,
)

# A parent-domain SCOPE, however it is spelled: the `Domain` attribute itself (any case, any
# spacing), or a bare `.premiselabs.co` host scope (the qualified `app.premiselabs.co` and
# `tortoise.premiselabs.co` are the origins we WANT documented, so the lookbehind excludes them).
# This is the structural half of the positive check, and it must catch the MECHANISM rather than a
# vocabulary — the prose tripwire is a named-phrase list and can never be complete. BOTH spellings
# were holes in the first cut: `domain=` slipped past a case-sensitive `Domain\s*=`, and a bare
# `.premiselabs.co` scope needs no `Domain=` at all (both proved during review, #4239).
#
# `Domain\s*=` REQUIRES A DOMAIN VALUE. Matching the bare keyword flagged prose (`"In this doc,
# domain = the app origin."`) as an assignment (proved during review, #4239).
#
# A host scope needs a CREDENTIAL in the same claim, because the bare apex appears all over these
# docs as an origin and in URLs — `premiselabs.co/sitemap-company.xml` is not a cookie scope. The
# LEADING DOT is required: without it, `premiselabs.co` in ordinary prose fired on three correct
# units of `website_architecture.md` (proved during review, #4239). A dotless apex scope is a
# documented non-goal, named in `_claim_lines` and in the doc-level test.
_PARENT_SCOPE_ASSIGNMENT = re.compile(
    r"Domain\s*=\s*[`\"']?\.?[\w-]*premiselabs\.co"
    r"|(?<![\w.-])\.premiselabs\.co",
    re.IGNORECASE,
)
_SCOPE_CREDENTIAL = re.compile(r"cookie|session|token|scope|set-cookie", re.IGNORECASE)
# The `Domain` alternative carries its own evidence — naming the apex IS the mechanism — so it is
# checked on its own. Requiring a credential for it too let
# `"Set `Domain=.premiselabs.co` on the response."` through BOTH the structural check and the prose
# tripwire (whose trigger is the same phrase and whose side B needs a credential word), so the
# mechanism was restatable with no credential noun at all (proved during review, #4239). The
# credential gate applies only to the bare-scope alternative, where the apex alone is ambiguous.
_DOMAIN_ASSIGNMENT = re.compile(
    r"Domain\s*=\s*(?:[`\"']?\.?[\w-]*premiselabs\.co|[$<{])", re.IGNORECASE
)

# The `__Host-` prefix described as ABSENT — the other structural half. `removed` is BOTH a
# side-A trigger and a `_REJECTION_MARKERS` word, so `"The `__Host-` prefix is removed so the
# session cookie is shared across all subdomains"` excused ITSELF and passed the prose tripwire
# (proved during review, #4239). This matches the assertion about the PREFIX itself, so the two
# subjects cannot be confused.
#
# A PREDICATE, NOT A WORD LIST — and the negation is scanned, not a fixed-width lookbehind. A
# `(?<!not )` only tolerates the negation IMMEDIATELY before the verb, so the module's own correct
# example `"the `__Host-` prefix must never be removed while the BFF ships"` was flagged (proved
# during review, #4239).
_HOST_ABSENT_VERBS = (
    "dropped", "removed", "absent", "missing", "forfeited", "omitted", "retired",
    "sacrificed", "gone", "lost", "stripped", "abandoned",
)
_ABSENT_VERBS_RE = "|".join(_HOST_ABSENT_VERBS)
_HOST_ABSENT = re.compile(
    rf"\b(?:no longer\b[^\n]{{0,24}}?\b(?:enforced|applied|used|required|present|set|sent|in\s+place))"
    rf"|\b(?:{_ABSENT_VERBS_RE})\b",
    re.IGNORECASE,
)
_HOST_NEGATED = re.compile(r"\b(?:not|never|cannot|can't|isn't|is not|without)\b", re.IGNORECASE)


def _host_prefix_dropped(text: str) -> bool:
    """True when `text` says the shipped `__Host-` prefix is ABSENT.

    Scans the window between `__Host-` and the absence predicate, AND the mirrored window BEFORE it
    (the natural pre-position form is `"the session cookie is missing the `__Host-` prefix"`, which
    a forward-only scan never saw — proved during review, #4239). An intervening negation
    (`"must never be removed while the BFF ships"`) is honoured without a fixed-width lookbehind.
    Each window is 80 characters — a floor, not a claim of completeness: an absence predicate
    further from `__Host-` than that, or one not in `_HOST_ABSENT`, is a documented non-goal.

    The two directions are NOT symmetric, and the asymmetry is deliberate: the BACKWARD window
    must guard against a verb belonging to another subject, because there the subject may precede
    the predicate (`"Unlike the removed bridge, the `__Host-session` cookie…"`), so it additionally
    requires a determiner/`of`/`from` bridge between predicate and token. The FORWARD window needs
    no such guard: the predicate follows the token directly, so there is no room for a foreign
    subject. A forward-direction false positive was searched for across BOTH real artifacts and
    found none (proved during review, #4239).
    """
    for m in re.finditer(r"__Host-", text):
        forward = text[m.start() : m.start() + 80]
        hit = _HOST_ABSENT.search(forward)
        if hit and not _HOST_NEGATED.search(forward[: hit.start()]):
            return True
        backward = text[max(0, m.start() - 80) : m.start()]
        for h in _HOST_ABSENT.finditer(backward):
            # A verb in the backward window may belong to a DIFFERENT subject —
            # "Unlike the removed bridge, the `__Host-session` cookie keeps its `__Host-` prefix"
            # would otherwise read as "the prefix is absent". Require the material between the
            # predicate and the token to be nothing but an optional determiner/possessive bridge.
            bridge = backward[h.end() :]
            if not re.fullmatch(
                r"\s*(?:(?:of|from)\s+)?(?:(?:the|its|a|an)\s+)?[`\"']?", bridge, re.IGNORECASE
            ):
                continue
            # Negation may precede the predicate — "is NOT missing the `__Host-` prefix".
            if _HOST_NEGATED.search(backward[: h.start()]):
                continue
            return True
    return False
# … and names a CREDENTIAL on the same line — the two halves together are the claim. Broadening
# side A alone (adding `cross-subdomain`) fired on a legitimate changelog line,
# "#1225 (post-signup cross-subdomain gap) …", which shares no credential and asserts nothing
# about the design. Requiring both halves keeps the recall the broadening bought and drops that
# false positive.
_SHARED_SESSION = re.compile(r"cookie|token|session", re.IGNORECASE)
# … and carries NO marker that this is the rejected/legacy design: that is the regression.
#
# CO-OCCURRENCE, not a pinned sentence. The first cut of this gate matched one verbatim string,
# and a reworded restatement of the SAME defect passed it — proved by mutation during review
# (#4239). The marker list is what keeps §2.1's legacy-cohort bullet from reading as a live
# claim. Matched case-insensitively: an all-caps `REJECTED:` is the most obvious way an author
# marks the rejected design, and failing on it would be a false positive.
#
# `"standard"` was a marker in the first cut and is NOT one now: it blinded the detector to any
# live claim that happened to use the word ("the standard parent-domain cookie"), and nothing in
# the scanned region needed it — §1.3, which describes the field's standard, is out of region.
_REJECTION_MARKERS = (
    "overrides",
    "reject",
    "removed",
    "legacy",
    "no longer",
    "not the session",
    "superseded",
    "historical",
    "retained",
    "inert",
    "pre-#4054",
    "deprecated",
    "obsolete",
    "former",
    "pre-bff",
)


_ITEM_START = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_HEADING = re.compile(r"^#{1,6}\s")


def _items(pairs: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Group `(lineno, line)` into CLAIM UNITS: one bullet, one table row, one heading, or one
    run of plain paragraph lines.

    The unit matters twice over, and a blank-line block was wrong both ways — both proved by
    mutation during review (#4239):

    - TOO COARSE: §2.1's five bullets are one blank-line block, so the `OVERRIDES` bullet excused
      a restatement added as a sixth bullet.
    - TOO FINE: a bullet whose marker sits on a different WRAPPED line failed as a false positive.

    A bullet together with its continuation lines — indented OR a lazy unindented wrap — is
    exactly the right unit: one claim, one marker. A table row is its own unit, which is what
    makes the row rule below fall out of the structure instead of needing a special case. (Note
    that a lazy continuation and a second sentence on the same line DO join the unit: the unit is
    a structural boundary, and the sentence — see `_sentence_around` — is the claim boundary.)
    """
    out: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    for n, ln in pairs:
        if not ln.strip():
            if cur:
                out.append(cur)
                cur = []
            continue
        starts = bool(_ITEM_START.match(ln) or _HEADING.match(ln)) or ln.lstrip().startswith("|")
        if starts and cur:
            out.append(cur)
            cur = []
        cur.append((n, ln))
    if cur:
        out.append(cur)
    return out


_ABBREV = frozenset(("i.e", "e.g", "vs", "cf", "fig", "eq"))


def _is_abbrev(text: str, dot_space: int) -> bool:
    """True when the `". "` at `dot_space` terminates an abbreviation, not the sentence."""
    token = text[max(0, dot_space - 12) : dot_space].rsplit(None, 1)[-1].strip("(\"'`*")
    return token.lower() in _ABBREV


# Markers that may excuse an ABSENCE claim. `removed`/`dropped`/`no longer` are NOT among them:
# they are the words that make the check fire, and `removed` is also in `_PARENT_DOMAIN_COOKIE`'s
# `__Host-` alternative — so `"the `__Host-` prefix is removed so the session cookie is shared
# across all subdomains"` sat on BOTH sides of the check and excused ITSELF (proved during review,
# #4239). The ordinary record-markers ARE honoured here (`legacy`, `historical`, `retained`, `inert`,
# `former`, `obsolete`): a correctly labelled line that says the LEGACY cookie lacks the prefix is a
# true statement about the legacy cookie, not a regression.
_NOT_AN_ESCAPE = tuple(m for m in _REJECTION_MARKERS if m not in ("removed", "dropped", "no longer"))

# Markers that may mark a whole BULLET, as opposed to one sentence. NARROWER than `_NOT_AN_ESCAPE`:
# only an EXPLICIT DISPOSITION of the sharing decision qualifies — exactly the five words
# `OVERRIDES`, `REJECTED`, `Superseded`, `Deprecated`, `pre-BFF`. An ordinary-English marker does
# not: `- **Legacy note:** the bridge is removed. The session cookie is shared across all
# subdomains.` was fully excused by `legacy` alone until this set replaced `_REJECTION_MARKERS` for
# the whole-unit escape, and `not the session` — an ordinary clarification, and a
# `_REJECTION_MARKERS` member for SENTENCE scope, where it belongs — re-opened the SAME hole when it
# was listed here (both proved during review, #4239).
#
# SCOPE OF ITS OWN LIMIT: this is a SUBSTRING test on the bullet's first sentence, so one of these
# five words used incidentally mid-label also marks the bullet. A LEADING-token anchor was
# considered and rejected: the real label is `- **OVERRIDES:**`, so an anchor must allow the `**`
# and the colon, and no input was found where the difference changes the verdict. `pre-#4054` is a
# `_REJECTION_MARKERS` member for SENTENCE scope and is deliberately NOT listed here: it is a
# temporal label of the same ordinary-English kind as `legacy`/`historical`, so a bullet labelled
# with it would be excused whole and a live restatement in its second sentence would ride along.
_DISPOSITION = (
    "overrides",
    "reject",
    "superseded",
    "deprecated",
    "pre-bff",
)


def _sentence_around(text: str, at: int) -> str:
    """The sentence containing offset `at` — the scope a marker or a negation may cover.

    Boundaries are `". "`, `"; "` and newlines, NOT a bare `.`: these docs are full of dotted
    filenames and domains (`supabase-session.js`, `.premiselabs.co`), and splitting on a bare dot
    truncated the sentence mid-path so the legacy bullet's own `Legacy cohort:` label fell outside
    its window. Scoping matters because a marker in one sentence must not excuse the NEXT —
    `"The legacy bridge was removed. The session cookie is shared across all subdomains."` was a
    false negative until the scope became claim-local (proved during review, #4239).

    A `". "` AFTER A KNOWN ABBREVIATION is not a boundary either: `"…retained for the consent-page
    port, i.e. a parent-domain cookie shared across all subdomains"` reddened as a live claim
    because the `Legacy cohort:` label before `i.e.` fell out of the window — a false positive on
    a correctly marked line (proved during review, #4239).
    """

    def _prev(limit: int) -> int:
        best = -1
        for needle in (". ", "; ", "\n"):
            i = text.rfind(needle, 0, limit)
            while needle == ". " and i != -1 and _is_abbrev(text, i):
                i = text.rfind(needle, 0, i)
            if i > best:
                best = i
        return best

    def _next(start: int) -> int:
        best = -1
        for needle in (". ", "; ", "\n"):
            i = text.find(needle, start)
            while needle == ". " and i != -1 and _is_abbrev(text, i):
                i = text.find(needle, i + 1)
            if i != -1 and (best == -1 or i < best):
                best = i
        return best

    end = _next(at)
    return text[_prev(at) + 1 : end if end != -1 else len(text)]


def _claim_lines(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """CLAIMS asserting our session is shared cross-subdomain, minus the rejected/legacy ones.

    The two halves of the pattern are matched over the whole CLAIM UNIT, so a sentence wrapped
    across two lines is still one claim — matching per line let "The session cookie is shared
    across\\nsubdomains and hosts." through, proved during review (#4239). The marker and negation
    scope is claim-local (see SCOPE in WHAT THIS DOES NOT DO below): a unit-global marker excused
    a restatement in the next sentence, and a bare `without` inside an affirmation excused the
    affirmation itself. Both were proved holes during review (#4239).

    A table row is self-contained by construction, so its marker must be ON the row. The reported
    line is the unit's FIRST line, which is the line a reader has to fix.

    WHAT THIS DOES NOT DO — the boundary, stated so the next reader can judge it. This is a
    named-phrase tripwire, and three evasions survive it by construction:
      (a) a paraphrase using none of `_PARENT_DOMAIN_COOKIE`'s terms and no transport mechanism;
      (b) one sentence that both denies and asserts sharing (`"no cookie is shared today, so the
          session cookie is shared across all subdomains via a parent-domain cookie"`);
      (c) a parent scope spelled without a dot and without the `Domain` keyword, in a claim that
          names no credential.
    The check that does not depend on this vocabulary is the POSITIVE one in
    `test_architecture_docs_do_not_restate_...`: no `Domain` assignment *with a value* and no
    parent scope *in a claim that names a credential* may appear in the current region, and
    `__Host-` must be present and not described as absent. Do not read it as covering (c).

    SCOPE: the window is the sentence carrying the trigger, EXCEPT for a list item whose FIRST
    sentence carries a `_DISPOSITION` term (a label governs its own continuation) and for a table
    row, which is always self-contained.
    """
    hits: list[tuple[int, str]] = []
    for item in _items(pairs):
        joined = " ".join(ln for _, ln in item)
        if not _SHARED_SESSION.search(joined):
            continue
        is_row = item[0][1].lstrip().startswith("|")
        # A MARKED LABEL GOVERNS ITS OWN BULLET. `- **OVERRIDES:** … is **rejected**. It is
        # JS-reachable from any subdomain …` explains a decision it already labelled, so the
        # second sentence is part of the marked claim — judging it alone reddened a correct doc
        # (proved during review, #4239). A bullet WITHOUT a marking label, a paragraph and a
        # blockquote are still judged sentence by sentence, which is what catches a marker in one
        # sentence excusing the next.
        label = _sentence_around(joined, 0).lower()
        unit_marked = bool(_ITEM_START.match(item[0][1])) and any(m in label for m in _DISPOSITION)
        # EVERY occurrence, not just the first. A unit can hold BOTH the historical record and a
        # live restatement — the §2 preamble is one blockquote whose first sentence is the marker
        # (`…has been REMOVED.`) and whose appended `The browser session is a parent-domain cookie
        # shared across all subdomains.` is the defect. Checking only the first trigger excused the
        # unit on the marker and never looked at the restatement (proved during review, #4239).
        for trigger in _PARENT_DOMAIN_COOKIE.finditer(joined):
            window = joined if (is_row or unit_marked) else _sentence_around(joined, trigger.start())
            low = window.lower()
            if any(m in low for m in _REJECTION_MARKERS) or _NEGATION.search(window):
                continue
            hits.append((item[0][0], item[0][1].strip()))
            break
    return hits


def _parent_domain_session_lines(text: str) -> list[tuple[int, str]]:
    """`_claim_lines` over a whole string, for the detector's own tests."""
    return _claim_lines(list(enumerate(text.splitlines(), 1)))


def _section_span(text: str, marker: str, end_marker: str | None = None) -> tuple[int, int]:
    """Character span of the block starting at `marker`, ending at `end_marker` or the next
    heading/quote/rule.

    Scoping matters: the first cut asserted its markers FILE-WIDE, and `503` occurs elsewhere in
    docs/auth-architecture.md (§2.3's dashboard row, the error-code prose), so the `/welcome`
    store-fault row could be deleted with the gate still green — proved during review (#4239).

    An explicit `end_marker` is AUTHORITATIVE, not merely a second bound. Taking `min()` with the
    structural bound silently truncated a region at its first sub-heading: the auth doc's
    `("### 2.1 The session", "### 2.4 What was wrong")` range stopped at §2.2, so §2.2 and §2.3 —
    the auth surfaces and the gates table, where a restatement is most likely to be written — were
    never scanned, while the comment claimed they were. Proved by mutation during review (#4239).
    """
    start = text.find(marker)
    assert start != -1, f"section marker {marker!r} is gone from the doc"
    start += len(marker)
    if end_marker is not None:
        end = text.find(end_marker, start)
        # A missing end marker is a hard failure: falling back to the structural bound would
        # quietly scan a different (usually much larger) region than the one declared.
        assert end != -1, f"end marker {end_marker!r} is gone, so this scan cannot be bounded"
        return start, end
    end = len(text)
    for term in ("\n> ", "\n### ", "\n## ", "\n# ", "\n---"):
        i = text.find(term, start)
        if i != -1:
            end = min(end, i)
    return start, end


def _section(text: str, marker: str, end_marker: str | None = None) -> str:
    """`_section_span` as a string."""
    a, b = _section_span(text, marker, end_marker)
    return text[a:b]


def test_the_parent_domain_session_detector_is_not_vacuous():
    """Falsifiability for the detector itself: it must see the defect in words we did not write.

    Without this, the docs test below could be green because `_parent_domain_session_lines`
    returns nothing for everything — including the exact regression it exists to catch.
    """
    reworded = (
        "Session is shared across subdomains via a parent-domain cookie.",
        "The session lives in a parent-domain cookie (`Domain=.premiselabs.co`).",
        "A parent-domain cookie shares the auth token across every subdomain.",
        "Sessions are shared cross-subdomain through Domain=.premiselabs.co.",
        # The forms the first cut MISSED — a restatement naming the real legacy artifact, or a
        # bare table row. Each was proved to pass before `_PARENT_DOMAIN_COOKIE` was broadened.
        "The browser holds a JS-readable `sb-tortoise-auth-token` cookie on `.premiselabs.co`, "
        "shared across subdomains.",
        "| Session | cookie on `.premiselabs.co`, shared by all subdomains |",
        "Sessions are scoped to the registrable domain so every subdomain sees them.",
        "One cookie on the root domain covers all subdomains.",
        "A session shared across all subdomains keeps every app logged in.",
    )
    for claim in reworded:
        assert _parent_domain_session_lines(claim), f"detector MISSED a reworded claim: {claim!r}"

    # …and it must not fire on the forms the docs legitimately carry, or the gate would demand
    # the record be deleted rather than corrected. All of these are lines the docs contain, or
    # sentences that correctly state the SHIPPED model; each was a false positive in an earlier
    # cut (proved during review, #4239).
    tolerated = (
        "- **OVERRIDES:** the standard cross-subdomain session — a `Domain=.premiselabs.co` cookie",
        "> parent-domain cookie described in the original note has been REMOVED",
        "- **Legacy cohort:** the JS-readable parent-domain bridge",
        "- **Session (current, #4054):** the browser holds only an opaque `__Host-session` cookie",
        "- **REJECTED:** a `Domain=.premiselabs.co` cookie shared across all subdomains.",
        # Negations and contrasts — correct statements of the shipped model, not the defect.
        "The root domain `premiselabs.co` carries no session cookie — only the app origin does.",
        "The `__Host-` prefix makes this session cookie unusable cross-subdomain, by design.",
        "The deprecated `Domain=.premiselabs.co` cookie is shared across all subdomains.",
        "This is why no cookie is shared across both hosts.",
        # A `cannot` in a PREMISE is not a statement that sharing is absent — but a `cannot` that
        # IS the assertion about the credential still is. Kept so the reduced cue set does not lose
        # the legitimate case it was meant to cover.
        "The `__Host-` prefix means no cookie is shared across subdomains, by design.",
        "The `__Host-session` cookie is scoped to the app origin alone; no session is shared.",
    )
    for ok in tolerated:
        assert not _parent_domain_session_lines(ok), f"detector FALSE-POSITIVED on: {ok!r}"

    # Non-vacuity for the BROADENING: `_SHARED_SESSION` alone must not be enough, or the bare
    # domain in legitimate prose (`app.premiselabs.co`, the origin we WANT documented) would fire.
    assert not _parent_domain_session_lines(
        "The session is minted on `app.premiselabs.co` and is host-only."
    ), "the detector now fires on the session origin itself — the trigger is too broad"

    # The phrasings the previous cut MISSED (proved HOLES during review, #4239). These are the
    # forms a re-regression would plausibly take, so each must fire.
    previously_missed = (
        "The login cookie is readable from every host under premiselabs.co.",
        "A `Domain` attribute on the session cookie shares it with every host.",
        "Sessions are scoped to the apex domain so all hosts see them.",
        "One cookie for the whole site keeps every app logged in.",
        "The `__Host-` prefix is dropped so the session cookie is shared across both hosts.",
        "The session cookie is shared across\nsubdomains and hosts.",  # wrapped across two lines
        "Sessions are shared across subdomains via a `Domain=premiselabs.co` cookie.",  # no dot
        "A parent domain cookie carries the session to sibling hosts.",
        # The `__Host-` prefix DROPPED is not in either list below — see `_host_prefix_dropped`:
        # `removed` is both a side-A trigger and a marker, so that claim excused itself in the
        # prose detector and is caught structurally instead (proved during review, #4239).
        # Paraphrases that avoid every earlier token (proved HOLES during review, #4239).
        "The session cookie is scoped to the whole domain, so the session is visible everywhere.",
        "The session cookie is set on the naked domain.",
        "The session cookie has no `__Host-` prefix.",
        "The session is visible to `api.premiselabs.co` as well.",
        "The session cookie is readable from all of premiselabs.co's hosts.",
        "The cookie is written with a Domain attribute covering the site.",
        "One session for the entire product.",
        "The login cookie is readable from any host on premiselabs.co.",
        # A marker in the PREVIOUS sentence must not excuse this one, and a bare `without` inside an
        # affirmation must not excuse the affirmation (both were holes, proved during review).
        "The legacy bridge was removed. The session cookie is shared across all subdomains.",
        "The session cookie is shared across all subdomains without any host-only restriction.",
        # A PREMISE verb in a subordinate clause must not excuse the main clause's affirmation, and
        # a marker word must not excuse a claim about a different subject. Both used to be excused
        # by the `_NEGATION`/`_REJECTION_MARKERS` word lists (proved during review, #4239).
        "Because a host-only cookie cannot authenticate a sibling host, the session cookie is "
        "shared across all subdomains via a parent-domain cookie.",
        # An UNLABELLED bullet's continuation is judged sentence by sentence, so the defect in
        # sentence 2 is caught even though sentence 1 was harmless (proved during review, #4239).
        "- **Transport:** some prose here.\n  The session cookie is shared across all subdomains.",
    )
    for claim in previously_missed:
        assert _parent_domain_session_lines(claim), f"detector MISSED a claim: {claim!r}"

    # …and a MARKED LABEL GOVERNS ITS OWN BULLET. `- **OVERRIDES:** … is rejected. It is
    # JS-reachable from any subdomain.` is ONE marked claim whose continuation explains the
    # decision, so it must pass; judging that continuation alone reddened a correct doc (proved
    # during review, #4239).
    assert not _parent_domain_session_lines(
        "- **OVERRIDES:** we reject the parent-domain session.\n"
        "  It was JS-reachable from any subdomain and forfeits the `__Host-` prefix."
    ), "a marked bullet's own continuation was reported as a live claim"

    # The STRUCTURAL half: a parent-domain scope spelled without `Domain=` and without any
    # credential word the prose list knows. `_parent_domain_session_lines` is a named-phrase
    # tripwire and MISSES these — the doc-level test catches them with `_PARENT_SCOPE_ASSIGNMENT`,
    # which matches the MECHANISM instead of a vocabulary. Both halves exist for exactly this
    # reason, and claiming the prose half covers it would be the overclaim this guard prevents
    # (proved during review, #4239). The qualified origins we WANT documented must not fire it.
    for mechanism in (
        "Set `domain=premiselabs.co` on the response.",
        "Set `DOMAIN = .premiselabs.co` on the cookie.",
    ):
        assert _DOMAIN_ASSIGNMENT.search(mechanism), f"structural check MISSED: {mechanism!r}"
    for mechanism in (
        "The `__Host-session` cookie is set on `.premiselabs.co` so the app and the API share it.",
        "The session cookie is scoped to a domain of `.premiselabs.co`.",
    ):
        assert _PARENT_SCOPE_ASSIGNMENT.search(mechanism), f"structural check MISSED: {mechanism!r}"
    # The composed PREDICATE the doc-level check actually applies — a credential is required for the
    # bare-scope alternative but not for `Domain=`. Asserting the regex alone let
    # `"Set `Domain=.premiselabs.co` on the response."` pass both checks (proved during review,
    # #4239), which is why the predicate itself is asserted here.
    def _flagged(claim: str) -> bool:
        return bool(
            _DOMAIN_ASSIGNMENT.search(claim)
            or (_PARENT_SCOPE_ASSIGNMENT.search(claim) and _SCOPE_CREDENTIAL.search(claim))
        )

    assert _flagged("Set `Domain=.premiselabs.co` on the response."), "gate MISSED a bare `Domain=`"
    assert _flagged("Set `Domain = \"premiselabs.co\"` on the session cookie."), "gate MISSED a quoted value"
    assert _flagged("The `__Host-session` cookie is set on `.premiselabs.co`."), "gate MISSED a scoped cookie"
    assert not _flagged("See `premiselabs.co/sitemap-company.xml`."), "gate FIRED on a URL"
    for absent in (
        "The `__Host-` prefix is removed so the session cookie is shared across all subdomains.",
        "The `__Host-` prefix is dropped, so the session is shared across subdomains.",
        "`__Host-` was forfeited to allow the cross-subdomain cookie.",
        "The `__Host-` prefix is no longer enforced, so the session cookie is shared.",
        "The `__Host-` prefix was retired; the session is shared across all subdomains.",
        # The PRE-POSITION form — the absence predicate comes first. The backward window was added
        # untested, and then fired on correct prose that merely mentioned another subject's removal
        # (proved during review, #4239), so both directions are pinned here.
        "The session cookie is missing the `__Host-` prefix, so a sibling host can read it.",
        # Phrasal objects: the bridge must admit the connective the verb subcategorises for,
        # or the bridge fix trades one miss for another (proved during review, #4239).
        "The session cookie has been stripped of the `__Host-` prefix.",
        "The cookie was stripped of its `__Host-` prefix.",
    ):
        assert _host_prefix_dropped(absent), f"prefix check MISSED: {absent!r}"
    for kept in (
        "The `__Host-` prefix was never dropped, so the session is host-only.",
        "The `__Host-` prefix is not removed by the BFF.",
        "The session cookie is not missing the `__Host-` prefix.",
        "The problem was not a missing `__Host-` prefix; the cookie was on the wrong host.",
        # A verb in the backward window belonging to a DIFFERENT subject must not fire it.
        "Unlike the removed bridge, the `__Host-session` cookie keeps its `__Host-` prefix.",
        "The parent-domain cookie was removed; the browser now holds only `__Host-session`.",
        # The negation is scanned, not a fixed-width lookbehind, so an intervening word is fine.
        "The `__Host-` prefix must never be removed while the BFF ships.",
        "The `__Host-` prefix cannot be dropped without breaking host-only isolation.",
        "The `__Host-` prefix is not to be dropped.",
    ):
        assert not _host_prefix_dropped(kept), f"prefix check FALSE-POSITIVED: {kept!r}"
    for origin in (
        "The session is minted on `app.premiselabs.co` and is host-only.",
        "The marketing host `tortoise.premiselabs.co` 301s to the app origin.",
    ):
        assert not _PARENT_SCOPE_ASSIGNMENT.search(origin), (
            f"structural check FALSE-POSITIVED on a qualified origin: {origin!r}"
        )

    # A wrapped bullet's marker counts for the whole bullet …
    wrapped = (
        "- **Legacy cohort:** the JS-readable parent-domain bridge, cookie\n"
        "  `sb-tortoise-auth-token` on `.premiselabs.co` — RETAINED"
    )
    assert not _parent_domain_session_lines(wrapped), (
        "a marker elsewhere in the bullet did not cover its wrapped claim — the gate would fail "
        "on the legitimate record"
    )
    # …but a marker on a DIFFERENT bullet must not excuse this one. A blank-line block made
    # §2.1's five bullets one unit, so the OVERRIDES bullet excused a restatement added as a
    # sixth (proved during review, #4239).
    siblings = (
        "- **OVERRIDES:** the standard cross-subdomain session is rejected.\n"
        "- **Sharing:** a parent-domain cookie shared across all subdomains carries the session."
    )
    assert _parent_domain_session_lines(siblings), (
        "a marker on one bullet excused a restatement on the NEXT bullet — the unit is too coarse"
    )
    # A TABLE row is self-contained: an adjacent row's marker must not excuse it.
    table = (
        "| Session | cookie on `.premiselabs.co`, shared by all subdomains |\n"
        "| Note | the old bridge was removed |"
    )
    assert _parent_domain_session_lines(table), (
        "an adjacent table row's marker excused a restatement — table rows are self-contained"
    )
    assert not _parent_domain_session_lines("| Legacy | parent-domain cookie, REMOVED |"), (
        "a table row marked ON the row was not tolerated"
    )


def test_the_section_slicer_actually_slices():
    """Non-vacuity for `_section`: a miss must fail loudly, and the slice must be a slice."""
    doc = "intro\n\n`/welcome`'s four outcomes:\n\n| a |\n| b |\n\n> next\ntail\n"
    block = _section(doc, "`/welcome`'s four outcomes")
    assert "| a |" in block and "| b |" in block
    assert "tail" not in block and "intro" not in block, "_section did not bound the block"
    with pytest.raises(AssertionError):
        _section(doc, "a marker that is not there")

    # An `end_marker` that has moved must ALSO fail loudly, not silently widen the scan.
    with pytest.raises(AssertionError):
        _section(doc, "`/welcome`'s four outcomes", end_marker="a marker that is not there")

    # The end bound must be AUTHORITATIVE, not merely a second bound. In `doc` above the
    # structural `\n> ` term already cuts before `tail`, so asserting on it proved nothing —
    # the assertion held with the whole `end_marker` branch deleted. Here the end marker sits
    # BEFORE any structural term, so only an authoritative bound can exclude `| b |`.
    doc2 = "m\n\n| a |\nSTOP\n| b |\n\n> next\n"
    bounded = _section(doc2, "| a |", end_marker="STOP")
    assert "STOP" not in bounded and "| b |" not in bounded, (
        "end_marker was not authoritative — the slice ran past it"
    )


# Each doc's CURRENT-architecture region — where a parent-domain session claim is a live claim.
#
# docs/auth-architecture.md declares its own scope, in its §2 preamble: "§2.1-§2.3 and §5.5 below
# are the current state; §2.4, §3, §4 and §5.1-§5.4 are the historical record of the pre-BFF
# design". So the scan is exactly those two ranges — §2.1-§2.3 (NOT the whole file, and NOT
# `docs/**`; see the declared-set note above) and §5.5. §1 is deliberately outside: it describes
# the field's standard (parent-domain cookies included) rather than this system, and flagging it
# would demand the background be deleted instead of read.
#
# §5.5 is included because the doc itself calls it current. It was originally omitted while its
# heading read "historical", which contradicted the preamble; the heading was corrected to match
# (the shared helper file IS retained — only the dashboard copy went), and the region follows.
#
# website_architecture.md is a live surface map with no historical region, so it is scanned whole
# — a bare `None` rather than an empty tuple, so "no ranges declared" can never read as "clean".
_CURRENT_REGION: dict[str, tuple[tuple[str, str], ...] | None] = {
    "website/website_architecture.md": None,
    "docs/auth-architecture.md": (
        # From the `## 2.` H2 — NOT `### 2.1` — so the section's own preamble counts. That preamble
        # is headed "CURRENT ARCHITECTURE (#4054)" and states the shipped model; starting at
        # `### 2.1` left it outside the scan, so a restatement written there passed (proved during
        # review, #4239). Its `REMOVED` line is marker-excused, so the wider range is still clean.
        #
        # `\n## 2.` — the leading newline ANCHORS it. As a bare `"## 2."` the substring also
        # matches inside `### 2.1`, so renaming the H2 would silently start the scan at §2.1 and
        # drop the preamble, while every vacuity guard still passed (proved during review, #4239).
        ("\n## 2.", "### 2.4"),
        ("### 5.5", "### 5.6"),
    ),
}


def _current_lines(rel: str, text: str) -> list[tuple[int, str]]:
    """`(lineno, line)` for every line of the doc's CURRENT-architecture regions.

    Line numbers are the FILE's, not the slice's: a joined slice reported "line 23" for a claim
    that sits on line 88, sending the reader to the wrong place (caught in review, #4239).
    """
    ranges = _CURRENT_REGION[rel]
    if ranges is None:
        return list(enumerate(text.splitlines(), 1))
    assert ranges, f"{rel} declares no region — that reads as clean, and is not"
    out: list[tuple[int, str]] = []
    for start, end in ranges:
        a, b = _section_span(text, start, end)
        base = text.count("\n", 0, a)
        out.extend((base + n, ln) for n, ln in enumerate(text[a:b].splitlines(), 1))
    return out


# Every dotted number is captured, so `### 2.5:`, `#### 2.2.1` and `### 2.4.1` are all seen. A
# three-component number is classified by its two-component PREFIX (`2.2.1` inherits `2.2`), which
# is why this is not `([25]\.\d+)(?=\s|$)` — that form silently skipped a colon-suffixed heading
# and every sub-subsection, leaving a restatement under them unscanned AND unflagged (proved during
# review, #4239). Fenced code is stripped before matching: a heading shown as a markdown SAMPLE is
# not a document section.
_SUBSECTION_NUM = r"^\s{0,3}#{2,6}\s*([25](?:\.\d+)+)"
# The other heading SPELLINGS markdown permits. ATX-only matching meant a setext or HTML heading
# was neither scanned nor flagged, so a restatement under it landed outside every declared range
# (proved during review, #4239). The ATX form above allows up to 3 leading spaces — CommonMark
# permits them, and without the allowance an INDENTED `### 2.5` was invisible too.
_SUBSECTION_SETEXT = r"^\s{0,3}([25](?:\.\d+)+)[^\n]*\n[-=]{3,}\s*$"
_SUBSECTION_HTML = r"<h[2-6][^>]*>\s*([25](?:\.\d+)+)"
_FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def _without_fences(text: str) -> str:
    """`text` with fenced code blocks removed, so a sample heading is not read as a section."""
    return _FENCE.sub("", text)


def _subsections(text: str) -> list[str]:
    """Every §2/§5 subsection number, across the heading spellings these docs use."""
    body = _without_fences(text)
    return (
        re.findall(_SUBSECTION_NUM, body, re.MULTILINE)
        + re.findall(_SUBSECTION_SETEXT, body, re.MULTILINE)
        + re.findall(_SUBSECTION_HTML, body)
    )


def _classify(num: str) -> str | None:
    """The classification of `num`, or of its two-component prefix (`2.4.1` -> `2.4`)."""
    if num in _CLASSIFIED_SUBSECTIONS:
        return _CLASSIFIED_SUBSECTIONS[num]
    prefix = num.rsplit(".", 1)[0]
    return _CLASSIFIED_SUBSECTIONS.get(prefix)


# Every subsection of §2 and §5, CLASSIFIED — because "in §2/§5 but scanned by nothing" is the
# failure mode this gate exists to prevent, and a bare allow-list cannot tell the two apart. The
# value is the DECISION: `current` (inside a `_CURRENT_REGION` range), `historical` (the doc's own
# record of the pre-BFF design, deliberately out of scan), or `excluded:<reason>`. A NEW `### 2.5`
# has no entry and is a hard failure until someone classifies it.
#
# §5.6 is `excluded`, NOT `historical`, and NOT scanned: it is a TEST INVENTORY whose own text
# names `tests/test_cross_subdomain_cookie_sync.py` — that hyphenated filename satisfies both
# halves of the prose trigger, so scanning §5.6 would red on a CORRECT line. That is a recorded
# decision, not a silent gap; the test below asserts every `historical` entry is really unscanned
# and every `current` entry really is.
_CLASSIFIED_SUBSECTIONS: dict[str, str] = {
    "2.1": "current",
    "2.2": "current",
    "2.3": "current",
    "2.4": "historical",
    "5.1": "historical",
    "5.2": "historical",
    "5.3": "historical",
    "5.4": "historical",
    "5.5": "current",
    "5.6": "excluded: test inventory (its text names the cross-subdomain test FILE)",
}


def test_the_subsection_matcher_sees_every_heading_shape():
    r"""A restatement must not hide behind a heading SPELLING, and a sample must not fake one.

    `### 2.5:` (colon) and `#### 2.2.1` were invisible to the earlier `([25]\.\d+)(?=\s|$)` form:
    neither `found` nor any range, so a restatement under them landed unscanned AND unflagged,
    while the comment claimed the opposite (proved during review, #4239).
    """
    for heading in (
        "### 2.5 The session",
        "### 2.5: The session",
        "## 2.5 The session",
        "#### 2.2.1 Deeper",
        "### 2.4.1 Deeper",
    ):
        assert re.match(_SUBSECTION_NUM, heading), (
            f"the matcher MISSED {heading!r} — it would be neither scanned nor flagged"
        )
    # A three-component number inherits its two-component prefix; a new two-component one does not.
    assert _classify("2.4.1") == _CLASSIFIED_SUBSECTIONS["2.4"]
    assert _classify("2.2.1") == _CLASSIFIED_SUBSECTIONS["2.2"]
    assert _classify("2.5") is None
    assert _classify("2.5.1") is None
    # A heading inside a fence is a markdown SAMPLE, not a section — and the fence stripper must
    # really strip, or this assertion passes vacuously.
    fenced = "intro\n```\n### 2.9 Sample\n```\noutro\n"
    assert "### 2.9" in fenced
    assert "### 2.9" not in _without_fences(fenced)
    assert "intro" in _without_fences(fenced) and "outro" in _without_fences(fenced)
    # …and the same for the OTHER heading spellings, through `_subsections` itself. Asserting only
    # `_SUBSECTION_NUM` left the setext and HTML patterns unexercised — either could be broken with
    # the suite still green (proved during review, #4239).
    assert _subsections("intro\n\n2.5 The session again\n=====\n\nx\n") == ["2.5"]
    assert _subsections("intro\n\n2.5 The session again\n-----\n\nx\n") == ["2.5"]
    assert _subsections('x <h3 class="a">2.5 The session again</h3> y') == ["2.5"]
    assert _subsections("```\n2.5 Sample\n=====\n```\n") == []
    # An INDENTED ATX heading counts (CommonMark allows up to 3 spaces) — an indented `### 2.5`
    # was invisible to the classification guard (proved during review, #4239).
    assert _subsections("   ### 2.5 Indented\n") == ["2.5"]


def test_the_current_region_declaration_is_not_vacuous():
    """Every declared doc must be covered, and each bounded region must really be bounded."""
    assert {p.relative_to(REPO).as_posix() for p in ARCH_DOCS} == set(_CURRENT_REGION), (
        "_CURRENT_REGION and ARCH_DOCS disagree — a doc would be scanned in the wrong scope"
    )
    for rel, ranges in _CURRENT_REGION.items():
        text = _read(REPO / rel)
        if ranges is None:
            continue
        for start, end in ranges:
            region = _section(text, start, end)
            assert 0 < len(region) < len(text), f"{rel}: region {start!r} is the whole file or empty"
        scanned = "\n".join(ln for _, ln in _current_lines(rel, text))
        assert "__Host-session" in scanned, (
            f"{rel}: the declared current regions do not carry the shipped session — the "
            "markers have drifted, so the scan is looking at the wrong part of the doc"
        )
        # Pin the region to the doc's own declaration: the sections it calls CURRENT are in, and
        # the ones it calls the historical record or the field's background are out. Without this,
        # a marker that drifted would silently shrink the scan and only a prose re-read would
        # notice — which is exactly how §5.5 went unscanned while the doc called it current.
        #
        # By SECTION NUMBER, not headline text: a harmless retitle reddened this gate during
        # review (#4239). A range's own start marker is consumed by its slice, so the assertion
        # that the range EXISTS is `_section` not raising, above; `tt_claim_pending` is a
        # distinctive §5.5 BODY string, proving the second range carries §5.5's text.
        scanned_nums = set(_subsections(scanned))
        for current in ("2.2", "2.3"):
            assert current in scanned_nums, f"{rel}: declared-current §{current} is NOT scanned"
        for historical in ("1.1", "2.4", "5.3"):
            assert historical not in scanned_nums, (
                f"{rel}: §{historical} is the background/historical record and must not be scanned"
            )
        # …and the classification table has to agree with the scan, BOTH ways. Without this a
        # number could be listed as `historical` yet sit inside a range (scanned, so the record is
        # held to the current-architecture standard), or be `current` yet sit outside every range
        # (trusted to be scanned and not) — the two ways the earlier allow-list lied. A range's own
        # START marker is consumed by its slice, so it counts as scanned here.
        range_starts = {
            n for start, _ in ranges for n in re.findall(_SUBSECTION_NUM, start, re.MULTILINE)
        }
        mislabelled: list[str] = []
        for num, kind in _CLASSIFIED_SUBSECTIONS.items():
            here = num in scanned_nums or num in range_starts
            if kind == "historical" or kind.startswith("excluded"):
                if here:
                    mislabelled.append(num)
            elif not here:
                mislabelled.append(num)
        assert not mislabelled, (
            f"{rel}: §{mislabelled} is classified other than how it is scanned — a `historical` "
            "section is INSIDE a declared current range (or a `current` one is outside every range). "
            "Fix the classification or the range."
        )
        assert "tt_claim_pending" in scanned, f"{rel}: §5.5's body is NOT scanned"
        # …and nothing in §2/§5 may be left unclassified. A live restatement that lands in a NEW
        # subsection is the exact failure this gate exists to prevent, so a new heading has to be
        # classified (as current, or as historical) before it can be trusted to be scanned.
        found = sorted(set(_subsections(text)))
        unclassified = [n for n in found if _classify(n) is None]
        assert not unclassified, (
            f"{rel}: §2/§5 subsection(s) {unclassified} are classified neither as current (a range "
            "in `_CURRENT_REGION`) nor as historical (`_CLASSIFIED_SUBSECTIONS`). Classify it — a "
            "new section must not land outside the scan."
        )
        stale = [
            n for n in _CLASSIFIED_SUBSECTIONS
            if not any(f == n or f.startswith(n + ".") for f in found)
        ]
        assert not stale, (
            f"{rel}: §{stale} is listed in `_CLASSIFIED_SUBSECTIONS` but is not in the doc — "
            "the list has gone stale."
        )
        # A DUPLICATE number is unclassified in every sense that matters: a SECOND `### 2.2` sits
        # outside both ranges while its number is already in `_CLASSIFIED_SUBSECTIONS`, so a live
        # restatement under it landed unscanned and unflagged (proved during review, #4239).
        nums = _subsections(text)
        dupes = sorted({n for n in nums if nums.count(n) > 1})
        assert not dupes, (
            f"{rel}: §2/§5 subsection number(s) {dupes} appear more than once. Only the first is "
            "inside a declared range, so the rest are scanned by nothing."
        )


def test_architecture_docs_do_not_restate_the_rejected_parent_domain_session():
    """No current-architecture doc may present the parent-domain cookie as the live session.

    Non-vacuity: each doc must also state the SHIPPED model (`__Host-session`) and carry an
    `OVERRIDES` line naming the departure — otherwise this gate would pass on a doc that simply
    deleted its auth section, which is the other way to lose the decision.
    """
    for p in ARCH_DOCS:
        rel = p.relative_to(REPO).as_posix()
        assert p.exists(), f"{rel} missing"
        text = _read(p)

        hits = _claim_lines(_current_lines(rel, text))
        assert not hits, (
            f"{rel} asserts a cross-subdomain parent-domain session in its CURRENT-architecture "
            "region:\n"
            + "\n".join(f"  line {n}: {ln}" for n, ln in hits)
            + "\n\nThat architecture is REJECTED (issue #3501/#4054; SCOPE.md §3, §4 W6, §13): it is "
            "JS-reachable from every subdomain and forfeits the `__Host-` prefix. The shipped "
            "model is a host-only `__Host-session` minted by the BFF on the app origin. Say that "
            "instead of the cookie — or, if you are describing what was rejected, mark the line "
            "(OVERRIDES / legacy / REMOVED / historical)."
        )

        # POSITIVE structural assertion. The shipped transport is HOST-ONLY: no `Domain`
        # assignment with a VALUE and no parent-domain scope in a claim that names a CREDENTIAL.
        # Either in the current region is the rejected design restated, or a marked line naming
        # what was rejected.
        #
        # What this does NOT cover, stated so it is not mistaken for a proof: a parent scope
        # spelled without a dot and without the `Domain` keyword, in a claim that names no
        # credential (`"the cookie's scope is premiselabs.co"` passes). That is the documented
        # non-goal, stated exactly: a DOTTED parent scope in a claim that names no credential word
        # — `"_Everything under `.premiselabs.co` shares one login."` — passes. The LEADING DOT
        # requirement is what keeps `premiselabs.co/sitemap-company.xml` from firing; the credential
        # gate is what keeps an ordinary mention of `.premiselabs.co` in prose from firing. Neither
        # gate covers the other's case, and `Domain=` needs neither.
        #
        # SCOPED PER CLAIM UNIT — which is WIDER than the prose tripwire's sentence scope, so a
        # marker in one sentence of an unmarked paragraph DOES excuse a structural assertion in the
        # next. Accepted deliberately: a structural match is a mechanism, and the false-positive
        # cost on prose that merely mentions a scope is higher than this residual.
        #
        # These docs wrap at ~100 cols and the real OVERRIDES bullet is already 100, so a marked
        # bullet that WRAPS puts its marker on line 1 and its `Domain=` on line 2 — a per-LINE check
        # reddened a correctly marked doc (proved during review, #4239). `_DOMAIN_ASSIGNMENT` is
        # case-insensitive for the same reason.
        assigned = []
        for item in _items(_current_lines(rel, text)):
            joined = " ".join(ln for _, ln in item)
            # The `Domain` alternative stands alone; only the bare-scope alternative needs a
            # credential in the same claim.
            if not (
                _DOMAIN_ASSIGNMENT.search(joined)
                or (_PARENT_SCOPE_ASSIGNMENT.search(joined) and _SCOPE_CREDENTIAL.search(joined))
            ):
                continue
            if any(m in joined.lower() for m in _REJECTION_MARKERS):
                continue
            assigned.append((item[0][0], item[0][1].strip()))
        assert not assigned, (
            f"{rel} assigns a `Domain` attribute in its CURRENT-architecture region:\n"
            + "\n".join(f"  line {n}: {ln}" for n, ln in assigned)
            + "\n\nThe shipped session cookie is host-only (`__Host-session`, no `Domain`), which "
            "is what makes it unreadable from a sibling subdomain. If you are naming the REJECTED "
            "design, mark the line (OVERRIDES / legacy / REMOVED / historical)."
        )

        # POSITIVE, structural, and about the OTHER subject: the `__Host-` prefix must not be
        # described as ABSENT. Same per-unit scoping and same marker escape as `assigned` — a marked
        # historical line may say the prefix was dropped, and a real doc almost certainly will.
        dropped = []
        for item in _items(_current_lines(rel, text)):
            joined = " ".join(ln for _, ln in item)
            if not _host_prefix_dropped(joined):
                continue
            if any(m in joined.lower() for m in _NOT_AN_ESCAPE):
                continue
            dropped.append((item[0][0], item[0][1].strip()))
        assert not dropped, (
            f"{rel} describes the shipped `__Host-` prefix as absent in its CURRENT-architecture "
            "region:\n"
            + "\n".join(f"  line {n}: {ln}" for n, ln in dropped)
            + "\n\n`__Host-session` is the shipped cookie and it KEEPS the prefix, which is what "
            "makes it host-only and unforgeable from a sibling subdomain. If you are naming the "
            "REJECTED design, mark the line (OVERRIDES / legacy / REMOVED / historical)."
        )

        # Non-vacuity, SCOPED TO THE REGION. File-wide these passed on a doc whose current section
        # was emptied, because the historical record also contains both strings (proved during
        # review, #4239).
        scanned = "\n".join(ln for _, ln in _current_lines(rel, text))
        assert "__Host-session" in scanned, (
            f"{rel} does not name the shipped session cookie (`__Host-session`) in its CURRENT "
            "region, so this gate cannot tell a corrected doc from one whose auth section was "
            "deleted."
        )
        assert "OVERRIDES" in scanned, (
            f"{rel} departs, in its CURRENT region, from the standard cross-subdomain session "
            "without an `OVERRIDES` line there — so the departure reads as an accident of history "
            "and the next reader re-derives the parent-domain cookie."
        )


def test_auth_architecture_doc_keeps_the_rendered_welcome_case():
    """
    `/welcome` renders exactly ONE case, and the doc must keep saying so — inside its block.

    `welcome.ts` has four outcomes; three answer without a page. A table listing only the
    redirects and the 503 reads `/welcome` as a pure redirect — and that is how the reset-panel
    corruption survived review: the Function serves the panel via `env.ASSETS.fetch`, which
    re-enters the asset router where `_redirects` DOES apply, so the served page lost its panel
    while the decision table looked correct.

    The assertions are scoped to the `/welcome` block. File-wide they were half-vacuous: `503`
    appears elsewhere in the doc, so the store-fault row could be deleted with the gate green.
    And per-TOKEN assertions are half-vacuous too: replacing the signed-in row with a duplicate of
    the no-cookie row kept four rows and every token while deleting the outcome entirely (proved
    during review, #4239). So each outcome is asserted AS an outcome.
    """
    rel = "docs/auth-architecture.md"
    block = _section(_read(REPO / rel), "`/welcome`'s four session outcomes")

    for marker in ("302", "/auth?next=", "reset", "reset-panel", "503"):
        assert marker in block, (
            f"the /welcome block in {rel} lost the outcome marker {marker!r}. Without it the "
            "gate table describes /welcome as a pure redirect."
        )

    # The separator is detected STRUCTURALLY. A substring test (`"---" not in ln`) silently drops
    # a row whose content merely contains `---`, so `rows[-1]` stopped being the table's last row.
    rows = [
        ln
        for ln in block.splitlines()
        if ln.lstrip().startswith("|")
        and not re.fullmatch(r"\|[\s:|-]+\|", ln.strip())
        and not ln.lstrip().startswith("| Case")
    ]
    assert len(rows) >= 4, (
        f"the /welcome gate table in {rel} lists {len(rows)} outcome row(s), not the four "
        "`welcome.ts` session outcomes:\n  " + "\n  ".join(rows)
    )
    # Each outcome, asserted as a whole row — a row can be replaced by a duplicate and still keep
    # the tokens and the count.
    outcomes = (
        ("signed in", "302"),
        ("no cookie / dead session", "/auth?next="),
        ("store unreachable", "503"),
        ("reset", "reset-panel"),
    )
    for case, response in outcomes:
        assert any(case in ln and response in ln for ln in rows), (
            f"{rel}: the /welcome table has no '{case}' row answering {response!r}. The table "
            f"must list all four session outcomes; it lists:\n  " + "\n  ".join(rows)
        )

    # The prose above the table says "the first three answer without rendering a page; the fourth
    # is the only case that renders one" — and welcome.ts does test the store before its reset
    # branch (503 at :53/:60, reset at :74). Pin the order, or that sentence goes stale in the
    # other direction the moment someone re-sorts the table. NOTE: adding a new outcome row means
    # updating that prose (and this assertion) too — the ordinal is load-bearing.
    assert "reset-panel" in rows[-1], (
        f"{rel}: the rendered case is no longer the LAST row, so the prose calling it 'the fourth' "
        "is false — fix the prose and this assertion together."
    )
