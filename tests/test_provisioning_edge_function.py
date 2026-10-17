"""E2E-1 source-level guard (#770 — plan Task 2): the tenant-provision Edge
Function must write the master list into Supabase ONLY.

The live E2E-1 registry assertion (registry_control_plane node count == 0
after signup) runs in the pre-deploy gate (plan Task 10). This test guards
the same contract at the source level so a regression can never ship: the
Edge Function must call provision_team (the atomic Supabase RPC), must NOT
call /internal/provision (the old registry writer) or update_user_team (the
old membership writer), must keep the data-plane REAL starter seed
(#2360: /internal/starter-seed — never the demo sample auto-seed), and must
compute lookup_hash via the shared TS mirror (parity with tortoise/auth.py).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from tests._verdict import NODE_FLOOR_STRIP_TYPES, require_node_floor

_REPO_ROOT = Path(__file__).resolve().parent.parent
EDGE_FN = _REPO_ROOT / "supabase" / "functions" / "tenant-provision" / "index.ts"
SHARED_LOOKUP = _REPO_ROOT / "supabase" / "functions" / "_shared" / "lookup.ts"


def test_edge_function_parses():
    """The Edge Function must at least PARSE as TypeScript — a syntax error
    would make every signup 500 at deploy time. Regression guard for the
    duplicate-`const pepper` P0 caught in review (PR #847): string-assertion
    tests above cannot see redeclarations, and CI has no deno/tsc step, so
    node's type-stripping parser is the cheapest gate (node >= 22.7 for
    `--experimental-strip-types`; #4916 — a present-but-too-old host SKIPs,
    it does not RED). Skips when node is absent."""
    require_node_floor(
        NODE_FLOOR_STRIP_TYPES,
        what="the tenant-provision edge-function parse check",
    )
    node = shutil.which("node")
    result = subprocess.run(
        [node, "--experimental-strip-types", "--check", str(EDGE_FN)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"tenant-provision/index.ts does not parse (exit {result.returncode}):\n"
        f"{result.stderr}"
    )


def test_edge_function_writes_supabase_only():
    """E2E-1: zero registry writes from the signup path (source contract)."""
    src = EDGE_FN.read_text()
    assert 'rpc("provision_team"' in src, (
        "Edge Function must write Supabase via the provision_team RPC"
    )
    # The string may appear in explanatory comments; the CALL must not.
    assert "fetch(`${fastApiUrl}/internal/provision`" not in src, (
        "Edge Function must not call the registry-writing /internal/provision"
    )
    assert 'rpc("update_user_team"' not in src, (
        "Edge Function must not call the removed update_user_team RPC"
    )
    # Data plane stays: the real starter seed creates the team's knowledge-graph
    # namespace (FalkorDB) — that is NOT a registry write.
    assert "/internal/starter-seed" in src, "Edge Function must keep the starter seed"
    # #2360 re-spec: the fake demo auto-seed is GONE — a fresh org must never
    # receive sample Points counted as its own data. The fetch URL is the
    # contract (a stray call must never regress).
    assert "`${fastApiUrl}/internal/demo`" not in src, (
        "Edge Function must not call the demo sample auto-seed (#2360)"
    )
    # lookup_hash must be computed via the shared TS mirror (P1-1 parity).
    assert "lookupHash" in src, "Edge Function must compute lookup_hash"


def test_starter_seed_logs_non_ok_responses():
    """#1860 (P3-1) carried to the #2360 starter seed: a non-2xx
    /internal/starter-seed response must be LOGGED, not silently swallowed.
    The old fire-and-forget `.catch()` covered only transport/abort errors —
    an HTTP 500 body resolved 'successfully', so a failed seed left the
    first-timer's graph silently missing starter data. The response must be
    bound and checked with !ok (source contract)."""
    src = EDGE_FN.read_text()
    # the starter-seed fetch binds its response (not fire-and-forget)
    assert "const starterRes = await fetch(`${fastApiUrl}/internal/starter-seed`" in src, (
        "starter-seed fetch must bind its response (starterRes) so !ok is checkable"
    )
    # transport/abort failures still log
    assert 'console.error("Starter seed failed:", e)' in src
    # HTTP errors (4xx/5xx) log the status — never silently pass
    assert "!starterRes.ok" in src, (
        "starter-seed must check !starterRes.ok — a 500 body resolving 'successfully' "
        "is the #1860 P3-1 bug"
    )
    assert "starterRes.status" in src, "non-ok starter-seed log must include the status"
    # the provision RPC has already committed — the failure must NOT fail the
    # whole provisioning (the user can still be onboarded)
    assert "return json(response, 201" in src


def test_starter_seed_carries_real_identity_data():
    """#2360: the starter seed is REAL data — the edge fn must pass the
    org name (user-confirmed), the person's own auth identity (user_id /
    email), and the user's display name when present (never a silently
    email-derived name, never a placeholder)."""
    src = EDGE_FN.read_text()
    # org_name = the wizard-typed org name (safeName) — real, confirmed
    assert "org_name: safeName" in src, "starter seed must pass the org name"
    # the person identity refs ride the VERIFIED caller (never client JSON)
    assert "person_user_id: user_id" in src
    assert "person_email: email" in src
    # display name is passed only when the user actually has one
    assert 'starterBody.person_name = display_name.trim()' in src


def test_edge_function_uses_shared_lookup_mirror():
    """The TS mirror import must exist and stay pure (runs in Deno AND node —
    the parity test imports it; Deno-only APIs would break the parity run)."""
    assert SHARED_LOOKUP.exists(), "supabase/functions/_shared/lookup.ts missing"
    assert EDGE_FN.read_text().count("_shared/lookup.ts") == 1
    src = SHARED_LOOKUP.read_text()
    assert "Deno.env" not in src and "Deno.serve" not in src, (
        "lookup.ts must be pure (no Deno API calls) — the node parity test imports it"
    )
    assert "crypto.subtle.digest" in src


def test_shared_lookup_implements_plan_construction():
    """The construction must be the plan's exact scheme: SHA-256(pepper + key)
    — pepper FIRST (plan P1-1). The Python side is locked to the same order
    by tests/test_lookup_hash.py + the parity test."""
    src = SHARED_LOOKUP.read_text()
    assert "pepper + key" in src
    # the digest input must be pepper concatenated BEFORE the key
    assert "pepper + key" in src.replace(" ", "") or "encode(pepper + key)" in src


# ── CORS regression guards (production incident 2026-08-13) ──────────────────
# The welcome page calls tenant-provision DIRECTLY from the browser (JWT path,
# #527/#802). A `Content-Type: application/json` + `Authorization: Bearer`
# POST forces a CORS preflight; the function previously returned 405 with NO
# CORS headers, so every browser signup was blocked with "No
# 'Access-Control-Allow-Origin' header". These guards pin the fix: preflight
# handled before the method gate, CORS headers on EVERY response path, and an
# origin allowlist covering the welcome page's hosts (mirrors
# waitlist-subscribe's proven pattern).

# ── #2406: onboarding-call offer email (post-provision trigger) ────────────

def test_edge_function_fires_onboarding_email_after_provision():
    """The provisioning door must fire POST /internal/onboarding-email (the
    #2406 one-time onboarding-call offer) for every NEW hosted signup."""
    src = EDGE_FN.read_text()
    assert "/internal/onboarding-email" in src, (
        "Edge Function must fire the onboarding-email internal endpoint"
    )
    # Fires after the starter-seed block, still inside the handler (provision
    # RPC already committed).
    assert "await fireOnboardingEmail(orgId, display_name);" in src, (
        "onboarding email must be fired after provisioning, passing the "
        "PERSON display_name"
    )
    assert src.index("fireOnboardingEmail(orgId, display_name)") > src.index(
        "Starter seed failed"), (
        "onboarding email must fire after the starter seed (independent of it)"
    )


def test_edge_function_onboarding_email_passes_person_name_not_safe_name():
    """The greeting must derive from the PERSON display name (caller/body) —
    NEVER the org slug (safeName): the org name is wizard-typed and
    whitespace-free; it is not a person's name (scope-doc §Personalization)."""
    src = EDGE_FN.read_text()
    # Scope the check to the onboarding POST body itself (safeName legitimately
    # appears elsewhere — e.g. the provision_team RPC body p_org_name).
    body_start = src.index("const body = JSON.stringify(")
    body_chunk = src[body_start:body_start + 400]
    assert "display_name: personDisplayName ?? undefined" in body_chunk, (
        "onboarding body must carry the person display_name key"
    )
    assert "safeName" not in body_chunk, (
        "the org slug must never be passed as the email greeting source"
    )
    assert "org_name" not in body_chunk, (
        "the onboarding body must carry org_id + display_name only"
    )
    # The call site passes the PERSON display_name — not safeName.
    assert "await fireOnboardingEmail(orgId, display_name);" in src
    assert "fireOnboardingEmail(orgId, safeName)" not in src


def test_edge_function_onboarding_email_retries_and_never_fails_provisioning():
    """The #2406 email POST is 2 attempts (0s/+2s), bounded, logs every
    non-definitive outcome, and NEVER throws — provisioning has already
    committed, so a failed offer email must never fail the signup."""
    src = EDGE_FN.read_text()
    # two attempts with a +2s retry between them
    assert "attempt < 2; attempt++" in src, (
        "onboarding email must be attempted twice (0s/+2s)"
    )
    assert "setTimeout(r, 2_000)" in src, (
        "the retry must wait +2s before the second attempt"
    )
    # bounded (never blow the hook deadline)
    assert "AbortSignal.timeout(5_000)" in src, (
        "onboarding email fetch must be time-bounded"
    )
    # independent error handling inside the helper — a failure never throws
    # into the handler's outer catch (which would 500 a committed provision)
    assert 'console.error("Onboarding email failed:", e)' in src
    assert '"Onboarding email not sent: /internal/onboarding-email "' in src, (
        "non-definitive statuses must be logged"
    )


def test_edge_function_answers_cors_preflight():
    """OPTIONS (preflight) must be answered 204 BEFORE the method gate, with
    methods/headers the welcome page actually sends."""
    src = EDGE_FN.read_text()
    assert 'if (req.method === "OPTIONS")' in src, (
        "OPTIONS preflight must be handled before the POST method gate"
    )
    assert 'if (req.method !== "POST")' in src
    # Preflight must come first (file order = runtime order).
    assert (
        src.index('req.method === "OPTIONS"')
        < src.index('req.method !== "POST"')
    ), "preflight must precede the method gate"
    assert "status: 204" in src, "preflight must return 204"
    assert '"Access-Control-Allow-Methods": "POST, OPTIONS"' in src
    # welcome.html sends Authorization: Bearer + Content-Type: json
    assert '"Access-Control-Allow-Headers": "authorization, content-type"' in src


def test_edge_function_sends_cors_headers_on_every_response():
    """Every response path must carry Access-Control-Allow-Origin + Vary so the
    browser can read success AND error bodies. Guarded via the single json()
    helper: no bare Response may remain outside it / the preflight."""
    src = EDGE_FN.read_text()
    assert "function json(" in src, "responses must go through the CORS json() helper"
    assert 'headers["Access-Control-Allow-Origin"] = corsOrigin ?? ALLOWED_ORIGINS[0];' in src
    assert 'headers["Vary"] = "Origin";' in src
    # The exact pre-incident regression: bare 405 with no CORS headers.
    assert 'new Response("Method not allowed", { status: 405 })' not in src, (
        "method gate must answer through json() (CORS headers), not a bare 405"
    )
    # Every remaining new Response must be ONE OF: the json() helper itself
    # (`{ status, headers }` — headers carries ACAO+Vary) or the preflight
    # (inline `"Access-Control-Allow-Origin"` map). A Response whose options
    # only name `corsOrigin` in a THIRD constructor position would be a
    # silent no-op — the Response constructor takes (body, init) only — so
    # any other pattern fails this check.
    import re

    for match in re.finditer(r"new Response\s*\(", src):
        # Balanced-paren statement extraction — no fixed window, so neighbor
        # text can never mask a bare Response.
        depth = 0
        end = match.start()
        for i in range(match.start(), len(src)):
            c = src[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        chunk = src[match.start():end]
        assert (
            "{ status, headers }" in chunk
            or '"Access-Control-Allow-Origin"' in chunk
        ), (
            f"Response at offset {match.start()} lacks CORS headers "
            f"(Response(body, init) only — a third corsOrigin arg is ignored):\n{chunk}"
        )
    # Deno also supports the static constructor Response.json(body, init) — a
    # CORS-free response written that way would escape a `new Response` scan.
    for match in re.finditer(r"Response\.json\s*\(", src):
        chunk = src[match.start():match.start() + 200]
        assert (
            "{ status, headers }" in chunk
            or '"Access-Control-Allow-Origin"' in chunk
        ), f"Response.json at offset {match.start()} lacks CORS headers:\n{chunk}"
def test_edge_function_allowlists_welcome_page_origins():
    """The allowlist must cover every host the welcome page is served on:
    the tortoise product host (the reported failure origin), the company host,
    Cloudflare previews, and ANY localhost/127.0.0.1 port (welcome.html's
    isLocal treats every local port as local)."""
    src = EDGE_FN.read_text()
    assert "https://tortoise.premiselabs.co" in src
    assert "https://premiselabs.co" in src
    assert "https://app.premiselabs.co" in src  # #1566: provisioning runs in-app now
    assert ".premise-labs.pages.dev" in src
    # Any-port local dev rule (replaces the 8788-only array — see the
    # divergence note in index.ts): the regex must accept localhost/127.0.0.1
    # with an optional port and nothing else.
    assert re.search(r"LOCAL_ORIGIN_RE", src), "LOCAL_ORIGIN_RE must exist"


def _allowlist_pattern(const: str) -> re.Pattern[str]:
    # Anchors the declaration and captures its opening bracket. The BODY is taken
    # by `_array_body`, not by a regex: `\[([^\]]*)\]` stops at the first `]` in
    # the file, so a `]` inside a host string (an IPv6 origin such as
    # `https://[::1]:1`) truncated the compared value and hid every host after it
    # — measured: a drifted host placed after such an entry passed. The optional
    # opening paren accepts a parenthesised literal (`= ([...] as const)`), which
    # is a literal the sweep must compare rather than report as absent.
    return re.compile(
        rf"(?:export\s+)?(?:const|let|var)\s+{const}\s*(?::[^=\n]+)?=\s*\(?\s*(\[)"
    )


def _array_body(code: str, open_idx: int) -> str | None:
    """The text between the `[` at `open_idx` and its matching `]`.

    String-aware and depth-aware: a `]` inside a quoted host does not end the
    array, and an array of arrays is captured whole. `None` when the bracket is
    never closed — the caller then has a declaration it cannot read, which is a
    RED, not a silence.
    """
    if open_idx >= len(code) or code[open_idx] != "[":
        return None
    depth = 0
    quote: str | None = None
    i = open_idx
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return code[open_idx + 1:i]
        i += 1
    return None


def _normalise_array_body(body: str) -> str:
    """The body's elements, with whitespace and separators folded away.

    Commas carry no meaning for a comparison of hosts, and folding them matters:
    a comment removed from inside the array leaves its separating comma behind, so
    comparing raw bodies would make a comment-only edit to one file look like
    drift. A comma inside a host string is folded on both sides alike, so the
    comparison is unaffected.
    """
    return re.sub(r"[\s,]+", " ", body).strip()


def _allowlist_arrays(code: str, const: str) -> list[str]:
    """Every array-literal body declared for `const`, normalised.

    `code` must already be comment-stripped: the compared values ARE URLs, so a
    comment removed by regex would collapse them.
    """
    pat = _allowlist_pattern(const)
    bodies = []
    for match in pat.finditer(code):
        body = _array_body(code, match.start(1))
        if body is not None:
            bodies.append(_normalise_array_body(body))
    return bodies

def _strip_ts_comments(text: str) -> str:
    """The file's code, with block and line comments removed.

    String-aware on purpose: the values being compared ARE URLs, so removing
    every `//` to end-of-line would collapse all four hosts to `"https:` and
    make three drifted files compare EQUAL (measured). Quotes are tracked, so
    `//` inside a string survives and only real comments go. Comments are
    removed rather than skipped so a literal inside a comment can neither
    satisfy the comparison nor break it, and a declaration sharing a line with
    a block comment is still examined.
    """
    out: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if text.startswith("//", i):
            nxt = text.find("\n", i)
            i = n if nxt < 0 else nxt
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)

def _unreadable_allowlist_declarations(code: str, const: str) -> list[str]:
    """A declaration of `const` whose right-hand side is not an array literal.

    Reading literals alone cannot see a wrapped or imported value, so a file
    could serve `Object.freeze([...])` or `makeOrigins()` while a commented
    literal satisfied the comparison — measured GREEN with a comment copy of
    the reference beside a served non-literal. A declaration the sweep cannot
    read is a RED, not a gap.

    Comments are stripped, not skipped, and the search spans the whole text:
    skipping a line left `/* <reference> */ const NAME = makeOrigins();` on
    one line entirely unexamined (measured GREEN, served value a call), while
    deciding per line made `const NAME =` followed by the array on the NEXT
    line a false RED whose remedy ("make it an array literal") was already
    satisfied. Stripping spans and searching the whole text handles both, and
    still clears the two false REDs that a raw-text match produced: a
    commented `// let NAME = makeOrigins();` and a JSDoc `@example`. A
    parenthesised literal (`= ([...] as const)`) and one broken after `=` are
    both read as the literals they are.
    """
    pat = re.compile(
        rf"(?:export\s+)?(?:const|let|var)\s+{const}\s*(?::[^=\n]+)?=(?!\s*\(*\s*\[)"
    )
    out: list[str] = []
    for hit in pat.finditer(code):
        fragment = " ".join(code[hit.start():hit.end() + 40].split())
        out.append(fragment[:80])
    return out


def test_allowlist_declaration_scan_reads_code_not_text():
    """The scan's contract, pinned on snippets.

    Three review cycles found this helper wrong in three different ways: it read
    only the FIRST declaration (a decoy could stand in for a drifted array), then
    it matched raw text (a commented example became a false RED while a decoy
    satisfied the comparison), then it decided per line (a literal whose bracket
    wrapped to the next line was refused, and a declaration sharing its line with
    a block comment was never examined). Each case below is one of those.
    """
    ref = _allowlist_pattern("ALLOWED_ORIGINS")
    literal = 'const ALLOWED_ORIGINS = [\n  "https://premiselabs.co",\n];'
    assert ref.search(literal), "a literal declaration must be read"
    # The compared values ARE URLs: comment stripping must not touch a `//` that
    # lives inside a string, or every host collapses and drifted files compare equal.
    assert _strip_ts_comments(literal).count("https://premiselabs.co") == 1
    assert not _unreadable_allowlist_declarations(
        _strip_ts_comments(literal), "ALLOWED_ORIGINS"
    )
    # A declaration the scan cannot READ is a RED, wherever the decoy sits.
    for bad in (
        "const ALLOWED_ORIGINS = makeOrigins();",
        "const ALLOWED_ORIGINS = Object.freeze([\n  \"https://a\",\n]);",
        '/* const ALLOWED_ORIGINS = ["https://a"]; */ const ALLOWED_ORIGINS = makeOrigins();',
    ):
        assert _unreadable_allowlist_declarations(
            _strip_ts_comments(bad), "ALLOWED_ORIGINS"
        ), bad
    # Neither a comment nor a line break may be read as a fault.
    for good in (
        "// let ALLOWED_ORIGINS = makeOrigins();\n" + literal,
        "/** @example const ALLOWED_ORIGINS = build(); */\n" + literal,
        "// previously: [\"https://old.example\"]\n" + literal,
        'const ALLOWED_ORIGINS =\n  ["https://a"];',
        'const ALLOWED_ORIGINS = (["https://a"] as const);',
        'const ALLOWED_ORIGINS: string[] = ["https://a"];',
    ):
        code = _strip_ts_comments(good)
        assert not _unreadable_allowlist_declarations(code, "ALLOWED_ORIGINS"), good
        assert ref.search(code), good
    # The captured VALUE must be the whole array, not the prefix before the first
    # `]` in the file. A `]` inside a quoted host (an IPv6 origin) or in a nested
    # array used to truncate it — measured: at `[^\]]*` a drift placed after such
    # an entry passed the parity check while the served value had changed.
    for src, expected in (
        (
            'const ALLOWED_ORIGINS = ["https://[::1]:1", "https://tortoise.premiselabs.co"];',
            '"https://[::1]:1" "https://tortoise.premiselabs.co"',
        ),
        (
            'const ALLOWED_ORIGINS = [["https://a"], ["https://b"]];',
            '["https://a"] ["https://b"]',
        ),
        (
            'const ALLOWED_ORIGINS = ["https://a//b", "https://c"];',
            '"https://a//b" "https://c"',
        ),
    ):
        stripped = _strip_ts_comments(src)
        assert _allowlist_arrays(stripped, "ALLOWED_ORIGINS") == [expected], src
    # An unterminated array is a declaration the scan cannot read, never a silence.
    assert (
        _allowlist_arrays('const ALLOWED_ORIGINS = ["https://a";', "ALLOWED_ORIGINS")
        == []
    )
    # A comment inside the REFERENCE array must not red an honest change either:
    # the reference is stripped like every scanned file — and the comma its removal
    # leaves behind must not be read as drift by the comparison.
    ref_src = 'const ALLOWED_ORIGINS = [\n  "https://a", // product host\n];'
    assert _allowlist_arrays(_strip_ts_comments(ref_src), "ALLOWED_ORIGINS") == [
        '"https://a"'
    ]


def test_edge_function_allowlist_parity_with_waitlist_subscribe():
    """EVERY function that declares an allowlist must agree with
    waitlist-subscribe (the keep-in-sync comment in
    tenant-provision/index.ts) so a host added for
    the welcome page in one function can never silently drift from the others
    (the #1110 failure mode). Local-origin handling deliberately diverges
    (tenant-provision: any local port; waitlist: 8788 only) and is NOT
    compared.

    Enumerated rather than named: the guard originally compared two functions,
    and a third (deck-view, #8732) joined them unguarded. Its failure mode is
    fail-SHUT — the gate answers 403 and the deck stays locked — so a host
    added to the guarded pair while deck-view drifts would lock readers out with
    nothing red.
    """
    # ONE pattern per constant does both jobs — finding the declaring files and
    # reading their values — so the enumerated set and the compared set cannot
    # diverge. When they were separate (`decl` accepting `const|let|var`,
    # `declared_lists` parsing only `const NAME = [`), a `let ALLOWED_ORIGINS =
    # [...]` was enumerated but not parsed: a comment decoy satisfied the
    # comparison while the served array rode free (measured: a comment decoy
    # above a `let` missing `app.premiselabs.co` passed). An optional type
    # annotation is accepted so a typed declaration is compared, not reported as
    # absent. Enumerated by FUNCTION, not by the files that happen to contain the
    # literal: a sweep keyed on the literal would drop a file the moment the
    # constant is renamed — and dropping it is LOUD (the `missing` check fires),
    # which is the point. Every HTTP-facing function must declare an allowlist —
    # `_shared` has no browser surface and is skipped explicitly, not by
    # accident. The glob covers the extensions Supabase accepts, not just `.ts`.

    decl = _allowlist_pattern("ALLOWED_ORIGINS")
    declaring: dict[str, str] = {}
    missing: list[str] = []
    unreadable: list[str] = []
    functions_dir = _REPO_ROOT / "supabase" / "functions"
    for fn_dir in sorted(p for p in functions_dir.iterdir() if p.is_dir()):
        if fn_dir.name == "_shared":
            continue
        files = [
            f for f in sorted(fn_dir.rglob("*"))
            if f.suffix in {".ts", ".tsx", ".mts", ".cts"}
        ]
        # Compare CODE, not text: a literal sitting inside a comment is neither a
        # declaration nor a reason to red (a comment documenting an older allowlist
        # must not fail the guard), and a declaration sharing a line with a block
        # comment must still be examined.
        code = {f: _strip_ts_comments(f.read_text()) for f in files}
        hits = [f for f in files if decl.search(code[f])]
        wrapped = [f for f in files if _unreadable_allowlist_declarations(code[f], "ALLOWED_ORIGINS")]
        if not hits and not wrapped:
            missing.append(fn_dir.name)
        unreadable.extend(
            f"{fn_dir.name}/{f.relative_to(fn_dir)}" for f in wrapped
        )
        for hit in hits:
            declaring[f"{fn_dir.name}/{hit.relative_to(fn_dir)}"] = code[hit]
    # The specific diagnosis first: a declaration the sweep cannot READ is not an
    # absent one, and the two need different remedies.
    assert not unreadable, (
        f"these declarations are not array literals: {unreadable} — the sweep "
        "reads literals, so a wrapped or imported allowlist cannot be compared "
        "(and a commented literal beside it would pass while the served value "
        "drifted). Declare it inline as a literal array, or extend the sweep."
    )
    assert not missing, (
        f"these functions declare no ALLOWED_ORIGINS: {missing} — an HTTP-facing "
        "function needs one. If it declares one the sweep cannot READ, that is "
        "the other assertion; do NOT add the function to the `_shared` skip, "
        "which would trade a loud failure for a permanent false green on a "
        "function whose drift fails SHUT."
    )
    assert len(declaring) >= 2, f"allowlist sweep found only {sorted(declaring)}"
    # The reference is stripped too: it is the anchor for every comparison, so a
    # comment in it that mentions the constant must not red an honest change —
    # measured, a trailing `// product host` inside the reference array failed the
    # guard and blamed deck-view, a file nobody had touched.
    waitlist = _strip_ts_comments(
        (
            _REPO_ROOT / "supabase" / "functions" / "waitlist-subscribe" / "handle.ts"
        ).read_text()
    )
    for const in ("ALLOWED_ORIGINS", "ORIGIN_SUFFIXES"):
        want = _allowlist_arrays(waitlist, const)
        assert len(want) == 1, f"the reference must declare {const} exactly once"
        for name, src in sorted(declaring.items()):
            have = _allowlist_arrays(src, const)
            assert have, (
                f"{name} declares no {const} the sweep can read — a declaration "
                "the sweep cannot parse is a RED, not a gap: declare it inline as "
                "an array literal, or extend the sweep. Do not skip the file."
            )
            assert all(h == want[0] for h in have), (
                f"{name} {const} must match waitlist-subscribe in EVERY "
                "declaration — change every declaring function together (see "
                "the keep-in-sync comment in tenant-provision/index.ts)"
            )


def test_edge_function_rejects_unknown_origins():
    """Non-allowlisted browser origins must get 403 with CORS headers — never
    an echoed ACAO (would grant arbitrary sites access to team minting)."""
    src = EDGE_FN.read_text()
    assert 'if (requestOrigin && !originAllowed(requestOrigin))' in src, (
        "browser requests must pass the origin allowlist gate"
    )
    assert 'json({ error: "Origin not allowed" }, 403, corsOrigin)' in src
    # No wildcard CORS anywhere — team minting must stay origin-scoped.
    assert '"Access-Control-Allow-Origin": "*"' not in src
