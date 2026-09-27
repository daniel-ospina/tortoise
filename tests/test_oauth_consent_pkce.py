"""Behavioural harness for the MCP consent page's browser auth client (#3496).

The page (`tortoise/oauth.py::consent_page_html`) is a FastAPI-rendered HTML
document whose inline script drives supabase-js. Historically its hardening was
pinned only by static-string assertions ("the page JS has no jsdom harness in
this repo" — see `tests/test_oauth_mcp.py`), which cannot discriminate the
behaviour this issue changes: the grant type actually used, where the PKCE
`code_verifier` is stored, whether a sign-in refusal reaches the user, and
whether a transient is echoed back to the provider.

This harness renders the page with the PURE `consent_page_html(...)`, extracts
the inline script from the RENDERED HTML, and EXECUTES it under Node `vm`
against the **vendored** `@supabase/supabase-js` bundle with a DOM/fetch/storage
shim. The token POST is captured, the cookie jar is a raw per-assignment write
log, and the results are asserted as observable behaviour.

Toolchain contract: node is REQUIRED. A missing node FAILS by name (never
skips) — reusing `_require_node` from the sibling session-bridge harness (#3786),
because a skipped harness is green, exit 0, zero coverage.

Invariants pinned here:
 1 grant type actually used (`code_challenge_method=s256`) + implicit control
 2 single-origin completion (#1566) + the exchange POST is `grant_type=pkce`
   carrying the stored verifier
 3 key-identity routing on set: the verifier never reaches `document.cookie`
 4 the removal path (invalid stored session) writes ONLY the session-key expiry
 5 the terminal state is exact, with a clean-load negative control
 6 an unavailable store refuses locally (no navigation, no verifier) — both a
   method-throw and an access-time-throw store
 7 item 6 write-path parity: ≤SIZE_GUARD byte-identical, >SIZE_GUARD stripped,
   >SIZE_CAP refused AND page-reported
 9 no WebCrypto refuses locally; with the guard removed the bundle downgrades
   to `code_challenge_method=plain` (the paired control)
11 the return target is canonicalised (no transient echoed), with a
   guard-removed control that DOES carry it
12 the aux stores hold no verifier after the removal path
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# #3786: the session-bridge toolchain contract is owned by the sibling harness —
# one guard, not a second variant. It FAILS (never skips) when node is absent.
from tests.test_session_bridge_fragment_retention import _require_node

REPO_ROOT = Path(__file__).resolve().parent.parent
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"
VENDOR_DIR = REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "vendor"

COOKIE_NAME = "sb-tortoise-auth-token"
PAGE_ORIGIN = "https://api.premiselabs.co"
AUTHORIZE_PATH = "/oauth/authorize"

# The inline script is the SECOND `<script>` (the first is the CDN tag, which
# begins `<script src=`); both carry the same nonce. This strict form matches
# only the inline block — a permissive `<script[^>]*nonce="…"[^>]*>` matches the
# CDN tag first with an EMPTY body (verified), which the sentinel assertion
# below would then catch as an extraction failure rather than a vacuous pass.
_INLINE_RE = re.compile(r'<script nonce="([^"]+)">([\s\S]*?)</script>')
_SENTINEL = "PARAMS"


def _vendored_bundle() -> Path:
    """The single vendored supabase-js bundle, derived from the directory so a
    version bump does not silently leave this harness executing a stale copy."""
    files = sorted(VENDOR_DIR.glob("supabase-*.min.js"))
    assert len(files) == 1, (
        f"expected exactly one vendored supabase bundle in {VENDOR_DIR}, found "
        f"{[f.name for f in files]}"
    )
    return files[0]


def _render_page(*, search: str = "") -> str:
    """Render the consent page with the pure renderer (no app boot)."""
    sys.path.insert(0, str(REPO_ROOT))
    from tortoise.oauth import consent_page_html

    html, _nonce = consent_page_html(
        client_name="test-connector",
        scope="mcp",
        params={
            "client_id": "client-1",
            "redirect_uri": "https://client.example/cb",
            "response_type": "code",
            "state": "st-1",
            "code_challenge": "chal",
            "code_challenge_method": "S256",
            "scope": "mcp",
            "resource": "",
            "client_name": "test-connector",
        },
        supabase_url="https://proj.supabase.co",
        supabase_anon_key="anon-key",
    )
    return html


def _extract_inline(html: str) -> str:
    m = _INLINE_RE.search(html)
    assert m, "could not extract the inline consent script from the rendered page"
    body = m.group(2)
    assert _SENTINEL in body, (
        "the extracted inline script does not contain the PARAMS sentinel — the "
        "extraction matched the wrong block (the CDN tag carries the same nonce)"
    )
    assert "</" not in body, (
        "the extracted inline script contains '</' — `_json_for_script` escapes "
        "'<', so this means the extraction is wrong (or the escape regressed)"
    )
    return body


def _run(scenario: str, *, page: str | None = None, **opts) -> dict:
    _require_node()
    html = page if page is not None else _render_page()
    inline = _extract_inline(html)
    with tempfile.TemporaryDirectory(prefix="3496-harness-") as td:
        tdp = Path(td)
        (tdp / "driver.js").write_text(_DRIVER_JS)
        (tdp / "page.js").write_text(inline)
        payload = json.dumps({"scenario": scenario, **opts})
        proc = subprocess.run(
            ["node", str(tdp / "driver.js"), str(_vendored_bundle()),
             str(tdp / "page.js"), payload],
            capture_output=True, text=True, timeout=120, cwd=str(tdp),
        )
    if proc.returncode != 0:
        raise AssertionError(
            f"harness scenario {scenario!r} failed (exit {proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, f"no RESULT line for {scenario!r}:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(line[-1][len("RESULT "):])


# ── invariant 8: version coupling (pure text / path, no node) ───────────────

def test_page_specifier_matches_the_vendored_bundle_version() -> None:
    """The CDN specifier must equal the version of the vendored bundle that this
    harness executes. A vendor-only PR selects `api` (see
    `SOURCE_PATTERNS["api"]` in tools/ci_selection.py) so this runs on the bump,
    and an oauth-only edit selects `api` too — both directions."""
    vendored = _vendored_bundle().name  # supabase-<ver>.min.js
    ver = re.match(r"supabase-(.+)\.min\.js$", vendored).group(1)
    text = OAUTH.read_text()
    assert f"@supabase/supabase-js@{ver}/dist/umd/supabase.min.js" in text, (
        f"the consent page's CDN specifier does not match the vendored bundle "
        f"version {ver!r} ({vendored}) — bump them together"
    )
    assert "@supabase/supabase-js@2/" not in text, (
        "the mutable `@2` specifier is back — pin the exact version"
    )


# ── the behavioural scenarios ───────────────────────────────────────────────

def test_inv1_grant_type_is_pkce_with_an_implicit_negative_control() -> None:
    """Invariant 1: the approve flow's authorize URL carries `code_challenge`
    with `code_challenge_method=s256`. The paired control removes the explicit
    flowType and asserts the challenge DISAPPEARS — so the signal demonstrably
    discriminates rather than being vacuously true."""
    r = _run("grant")
    assert r["method"] == "s256", f"expected s256, got {r}"
    assert r["challenge"], f"no code_challenge in {r}"

    implicit_page = _render_page().replace('flowType: "pkce",', 'flowType: "implicit",')
    ctl = _run("grant", page=implicit_page)
    assert not ctl["challenge"], (
        "the implicit control still produced a code_challenge — the assertion "
        f"does not discriminate the flow type: {ctl}"
    )


def test_inv2_single_origin_completion_and_pkce_exchange() -> None:
    """Invariant 2 (#1566's regression guard): the flow completes on the SAME
    origin it initiated on, and the token exchange is `grant_type=pkce` carrying
    the verifier that was stored. This is the property that made implicit look
    necessary — a migration must not break it."""
    r = _run("single_origin", search="?code=THECODE&state=st-1",
             seedVerifier="verifier-abc")
    assert r["innerOrigin"] == PAGE_ORIGIN, r
    assert r["innerPath"] == AUTHORIZE_PATH, r
    assert r["exchangeGrant"] == "pkce", f"token POST was not grant_type=pkce: {r}"
    assert r["exchangeVerifier"] == "verifier-abc", (
        f"the exchange did not carry the stored verifier: {r}"
    )


def test_inv3_and_inv12_the_verifier_never_reaches_the_cookie() -> None:
    """Invariants 3 + 12: on initiation the verifier keys land in the aux store
    and NEVER in `document.cookie`; on removal the aux stores are cleared and the
    cookie jar sees only the session-key expiry."""
    r = _run("routing")
    assert r["auxVerifierKeys"], f"no verifier key in sessionStorage: {r}"
    assert r["cookieVerifierKeys"] == [], (
        f"a verifier key reached document.cookie: {r['cookieVerifierKeys']}"
    )
    assert r["cookieAuxWrites"] == [], f"an aux key produced a cookie write: {r}"


def test_inv4_removal_path_writes_only_the_session_key() -> None:
    """Invariant 4: an invalid stored session drives `_removeSession`, whose
    ONLY cookie assignment must be the session-key expiry. The raw per-assignment
    log is load-bearing — the final-state jar is identical whether or not an aux
    key also reached the cookie."""
    r = _run("removal", seedSession='{"access_token":"a"}')
    assert r["cookieWriteNames"] == [COOKIE_NAME], (
        f"expected exactly one cookie assignment (the session key), got {r}"
    )
    assert r["navs"] == [], f"the removal path navigated: {r['navs']}"
    assert r["auxVerifierKeys"] == [], (
        f"an aux-store verifier survived the removal path: {r['auxVerifierKeys']}"
    )


def test_inv5_terminal_state_exact_with_a_clean_load_control() -> None:
    """Invariant 5: `?error=access_denied` ends on a VISIBLE sign-in view with the
    error; a CLEAN load with no session shows the sign-in view with NO error (the
    negative control that catches a spurious-error regression)."""
    bad = _run("load", search="?error=access_denied&error_description=boom&state=st-1")
    assert bad["viewSignin"] == "block", bad
    assert bad["errorVisible"] is True, bad
    assert "boom" in bad["errorText"], bad
    assert bad["replaceStates"], "the transient URL was not sanitised"

    clean = _run("load", search="")
    assert clean["viewSignin"] == "block", clean
    assert clean["errorVisible"] is False, (
        f"a clean load showed an error: {clean['errorText']!r}"
    )


def test_inv6_unavailable_store_refuses_locally() -> None:
    """Invariant 6: with both aux stores unavailable — by method throw AND by
    access-time throw — a provider click must NOT navigate and must report the
    refusal; no verifier may be written anywhere."""
    for mode in ({"sessionMode": "throw-method", "localMode": "throw-method"},
                 {"accessThrow": True}):
        r = _run("click", search="", **mode)
        assert r["navs"] == [], f"navigated despite an unavailable store: {r} ({mode})"
        assert r["errorVisible"] is True, f"no refusal reported: {r} ({mode})"
        assert r["cookieVerifierKeys"] == [], r
        assert r["auxVerifierKeys"] == [], r


def test_inv7_item6_write_path_parity() -> None:
    """Invariant 7: ≤SIZE_GUARD is written byte-identically; over SIZE_GUARD is
    stripped; over SIZE_CAP is refused (no write) AND reported on the page."""
    r = _run("item6")
    assert r["smallWritten"] is True, r
    assert r["strippedHasToken"] is False, (
        f"the size guard did not strip provider tokens: {r}"
    )
    assert r["overCapWrote"] is False, f"an over-cap write reached the cookie: {r}"
    assert r["overCapReported"] is True, f"the over-cap refusal was not reported: {r}"


def test_inv9_weak_challenge_refused_with_a_guard_removed_control() -> None:
    """Invariant 9: without `crypto.subtle` the page refuses to initiate. The
    paired control removes the guard and asserts the bundle really does downgrade
    the challenge to `plain` — so the guard is doing work, not merely present.

    The no-subtle mode must keep `getRandomValues` (a real non-secure-context
    browser has it); with `crypto = {}` the bundle throws BEFORE the downgrade,
    which would make the control pass for the wrong reason."""
    r = _run("click", search="", noSubtle=True)
    assert r["navs"] == [], f"initiated without WebCrypto: {r}"
    assert r["errorVisible"] is True, r

    page = _render_page().replace("const incap = pkceIncapable();", "const incap = null;")
    ctl = _run("grant", page=page, noSubtle=True)
    assert ctl["method"] == "plain", (
        f"the guard-removed control did not downgrade to plain: {ctl}"
    )


def test_inv11_return_target_is_canonicalised_with_a_control() -> None:
    """Invariant 11: the `redirect_to` the page hands to the provider is rebuilt
    from a SANITISED query — no `code`/`error`/`error_description`/`sb_flow_id`
    is echoed. The nested `redirect_to` is parsed explicitly: the assign URL's own
    origin is the Supabase host and carries the transients percent-encoded
    INSIDE `redirect_to`, so asserting on the assign URL directly is both a false
    red and a permanent false green.

    The control restores the raw-search form and asserts the transient IS echoed,
    so the assertion discriminates."""
    hostile = ("?client_id=x&code=STALE&error=access_denied"
               "&error_description=leak&sb_flow_id=flow123&state=st-1")
    r = _run("return_target", search=hostile)
    assert r["innerOrigin"] == PAGE_ORIGIN, r
    assert r["innerPath"] == AUTHORIZE_PATH, r
    assert r["innerTransients"] == [], (
        f"a transient was echoed into the return target: {r['innerTransients']}"
    )

    guard_off = _render_page().replace(
        "options: { redirectTo: authorizeReturnTo() },",
        "options: { redirectTo: window.location.origin + AUTHORIZE_PATH + window.location.search },",
    )
    ctl = _run("return_target", page=guard_off, search=hostile)
    assert "code" in ctl["innerTransients"], (
        f"the control did not echo the transient — the assertion is vacuous: {ctl}"
    )


# ── the Node driver ─────────────────────────────────────────────────────────

_DRIVER_JS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const nodeCrypto = require('crypto');

const bundlePath = process.argv[2];
const pagePath = process.argv[3];
const opts = JSON.parse(process.argv[4] || '{}');

const bundle = fs.readFileSync(bundlePath, 'utf8');
const page = fs.readFileSync(pagePath, 'utf8');

const COOKIE_NAME = opts.cookieName || 'sb-tortoise-auth-token';

function mkEl(id) {
  const set = new Set();
  return {
    id: id,
    style: {},
    textContent: '',
    value: '',
    disabled: false,
    firstChild: null,
    children: [],
    classList: {
      add: function (c) { set.add(c); },
      remove: function (c) { set.delete(c); },
      contains: function (c) { return set.has(c); },
      toggle: function (c) { if (set.has(c)) { set.delete(c); } else { set.add(c); } },
    },
    appendChild: function (n) { this.children.push(n); this.firstChild = this.children[0]; },
    removeChild: function (n) {
      this.children = this.children.filter(function (x) { return x !== n; });
      this.firstChild = this.children[0] || null;
    },
    setAttribute: function () {}, removeAttribute: function () {},
    addEventListener: function () {}, focus: function () {}, blur: function () {},
    getAttribute: function () { return null; },
  };
}

function makeStore(mode) {
  const map = new Map();
  return {
    _map: map,
    get length() { return map.size; },
    key: function (i) { const k = Array.from(map.keys())[i]; return k === undefined ? null : k; },
    getItem: function (k) {
      if (mode === 'throw-method') throw new Error('storage disabled');
      return map.has(String(k)) ? map.get(String(k)) : null;
    },
    setItem: function (k, v) {
      if (mode === 'throw-method') throw new Error('storage disabled');
      v = String(v);
      if (opts.storeQuota && v.length > opts.storeQuota) {
        const e = new Error('QuotaExceededError'); e.name = 'QuotaExceededError'; throw e;
      }
      map.set(String(k), v);
    },
    removeItem: function (k) {
      if (mode === 'throw-method') throw new Error('storage disabled');
      map.delete(String(k));
    },
    clear: function () { map.clear(); },
  };
}

const cookieWrites = [];
const cookies = new Map();
const navs = [];
const replaceStates = [];
const warns = [];
const fetchCalls = [];
const els = {};

const location = {
  hostname: opts.hostname || 'api.premiselabs.co',
  pathname: '/oauth/authorize',
  protocol: 'https:',
  search: opts.search || '',
  hash: opts.hash || '',
};
location.origin = 'https://' + location.hostname;
Object.defineProperty(location, 'href', {
  get: function () { return location.origin + location.pathname + location.search + location.hash; },
  set: function (u) { navs.push(String(u)); },
  configurable: true,
});
location.assign = function (u) { navs.push(String(u)); };
location.replace = function (u) { navs.push(String(u)); };
location.reload = function () {};

const documentObj = {};
Object.defineProperty(documentObj, 'cookie', {
  get: function () {
    return Array.from(cookies.entries()).map(function (e) { return e[0] + '=' + e[1]; }).join('; ');
  },
  set: function (text) {
    text = String(text);
    const semi = text.indexOf(';');
    const first = semi < 0 ? text : text.slice(0, semi);
    const eq = first.indexOf('=');
    const name = (eq < 0 ? first : first.slice(0, eq)).trim();
    const value = eq < 0 ? '' : first.slice(eq + 1);
    const attrs = semi < 0 ? '' : text.slice(semi);
    const removed = /Max-Age=0/i.test(attrs) || value === '';
    if (removed) { cookies.delete(name); } else { cookies.set(name, value); }
    cookieWrites.push({ header: text, name: name, removed: removed, dropped: text.length > 4096 });
  },
  configurable: true,
});
documentObj.getElementById = function (id) { if (!els[id]) { els[id] = mkEl(id); } return els[id]; };
documentObj.createElement = function (tag) { return mkEl('created-' + tag); };
documentObj.querySelector = function () { return null; };
documentObj.querySelectorAll = function () { return []; };
documentObj.addEventListener = function () {};
documentObj.readyState = 'complete';
documentObj.head = mkEl('head');
documentObj.body = mkEl('body');

// Seed state BEFORE the page script runs (the library reads the cookie in
// createClient()'s _initialize).
if (opts.seedSession) { cookies.set(COOKIE_NAME, opts.seedSession); }
const sessionStorage = makeStore(opts.sessionMode || 'ok');
const localStorage = makeStore(opts.localMode || 'ok');
if (opts.seedVerifier) {
  // The library's getItemAsync JSON-parses what it reads (`U` in the bundle),
  // so a raw string here is invisible to _isPKCECallback and the callback is
  // never recognised. Seed the same shape the library's own writer produces.
  sessionStorage.setItem(COOKIE_NAME + '-code-verifier', JSON.stringify(opts.seedVerifier));
}

function jsonResponse(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200, headers: { 'Content-Type': 'application/json' },
  });
}

function fetchImpl(input, init) {
  const url = typeof input === 'string' ? input : (input && input.url ? input.url : String(input));
  const body = init && init.body ? String(init.body) : '';
  fetchCalls.push({ url: url, method: (init && init.method) || 'GET', body: body });
  if (url.indexOf('/auth/v1/token') >= 0) {
    if (opts.exchangeFails) { return jsonResponse({ error: 'invalid_grant' }, 400); }
    return jsonResponse({
      access_token: 'at-' + 'x'.repeat(4), token_type: 'bearer', expires_in: 3600,
      refresh_token: 'rt-1', user: { id: 'u1' },
    }, 200);
  }
  return jsonResponse({}, 200);
}

const sandbox = {
  console: {
    log: function () {}, info: function () {}, debug: function () {},
    warn: function () { warns.push(Array.prototype.map.call(arguments, String).join(' ')); },
    error: function () { warns.push(Array.prototype.map.call(arguments, String).join(' ')); },
  },
  document: documentObj,
  location: location,
  history: {
    replaceState: function (s, t, url) { replaceStates.push(String(url)); },
    pushState: function () {}, go: function () {}, back: function () {},
  },
  sessionStorage: sessionStorage,
  localStorage: localStorage,
  navigator: { userAgent: 'node-harness', language: 'en', locks: undefined },
  TextEncoder: TextEncoder,
  TextDecoder: TextDecoder,
  URL: URL,
  URLSearchParams: URLSearchParams,
  Headers: Headers,
  Request: Request,
  Response: Response,
  FormData: FormData,
  Blob: Blob,
  AbortController: AbortController,
  // supabase-js's runtime detection refuses to initialise without a WebSocket
  // constructor in the realm ("Unknown JavaScript runtime without WebSocket
  // support"). Node 22 provides one; it is never dialled by this harness.
  WebSocket: WebSocket,
  fetch: fetchImpl,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: setInterval,
  clearInterval: clearInterval,
  queueMicrotask: queueMicrotask,
  btoa: function (s) { return Buffer.from(String(s), 'binary').toString('base64'); },
  atob: function (s) { return Buffer.from(String(s), 'base64').toString('binary'); },
  crypto: opts.noSubtle
    ? { getRandomValues: function (a) { return nodeCrypto.randomFillSync(a); } }
    : { getRandomValues: function (a) { return nodeCrypto.randomFillSync(a); },
        subtle: nodeCrypto.webcrypto.subtle },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;
sandbox.top = sandbox;
if (opts.accessThrow) {
  ['sessionStorage', 'localStorage'].forEach(function (nm) {
    Object.defineProperty(sandbox, nm, {
      get: function () { throw new Error('access to ' + nm + ' is denied'); },
      configurable: true,
    });
  });
}

const ctx = vm.createContext(sandbox);
vm.runInContext(bundle, ctx, { filename: 'supabase.min.js' });
if (!sandbox.supabase || typeof sandbox.supabase.createClient !== 'function') {
  throw new Error('the UMD bundle did not expose sandbox.supabase.createClient');
}
vm.runInContext(page, ctx, { filename: 'consent-inline.js' });

const q = function (expr) { return vm.runInContext(expr, ctx); };
const sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };

function innerTarget(assignUrl) {
  // The assign URL is Supabase's authorize endpoint; the page's own return
  // target is percent-encoded INSIDE its `redirect_to` param.
  const u = new URL(assignUrl);
  const rt = u.searchParams.get('redirect_to');
  if (!rt) { return null; }
  return new URL(rt);
}

(async function main() {
  const scenario = opts.scenario;
  const out = { scenario: scenario };

  if (scenario === 'grant' || scenario === 'click' || scenario === 'return_target') {
    await sleep(60);   // let createClient()'s _initialize settle
    await q('signInWithProvider("github")');
    await sleep(20);
    out.navs = navs.slice();
    out.errorVisible = els['error'] ? els['error'].classList.contains('visible') : false;
    out.errorText = els['error'] ? String(els['error'].textContent) : '';
    out.cookieVerifierKeys = cookieWrites
      .filter(function (w) { return w.header.indexOf('code-verifier') >= 0; })
      .map(function (w) { return w.name; });
    out.auxVerifierKeys = Array.from(sessionStorage._map.keys())
      .concat(Array.from(localStorage._map.keys()))
      .filter(function (k) { return k.indexOf('code-verifier') >= 0; });
    if (navs.length) {
      const inner = innerTarget(navs[navs.length - 1]);
      out.assign = navs[navs.length - 1];
      if (inner) {
        out.innerOrigin = inner.origin;
        out.innerPath = inner.pathname;
        // supabase-js puts the challenge on the AUTHORIZE url itself, as a
        // sibling of `redirect_to` (which carries only the page's return target).
        const assignParams = new URL(navs[navs.length - 1]).searchParams;
        out.challenge = assignParams.get('code_challenge');
        out.method = assignParams.get('code_challenge_method');
        const transients = ['code', 'error', 'error_code', 'error_description',
                            'error_uri', 'sb_flow_id', 'flow_id', 'type'];
        out.innerTransients = transients.filter(function (k) { return inner.searchParams.has(k); });
      }
    }
  } else if (scenario === 'load') {
    await sleep(120);
    out.viewSignin = els['view-signin'] ? String(els['view-signin'].style.display) : null;
    out.viewConsent = els['view-consent'] ? String(els['view-consent'].style.display) : null;
    out.errorVisible = els['error'] ? els['error'].classList.contains('visible') : false;
    out.errorText = els['error'] ? String(els['error'].textContent) : '';
    out.replaceStates = replaceStates.slice();
    out.navs = navs.slice();
  } else if (scenario === 'routing') {
    await sleep(60);
    await q('signInWithProvider("google")');
    await sleep(20);
    out.auxVerifierKeys = Array.from(sessionStorage._map.keys())
      .concat(Array.from(localStorage._map.keys()))
      .filter(function (k) { return k.indexOf('code-verifier') >= 0; });
    out.cookieVerifierKeys = cookieWrites
      .filter(function (w) { return w.header.indexOf('code-verifier') >= 0; })
      .map(function (w) { return w.name; });
    out.cookieAuxWrites = cookieWrites
      .filter(function (w) {
        return w.name !== COOKIE_NAME && (w.name.indexOf('-user') >= 0 ||
               w.name.indexOf('unknown') >= 0);
      })
      .map(function (w) { return w.name; });
  } else if (scenario === 'removal') {
    await sleep(150);
    out.cookieWriteNames = cookieWrites.map(function (w) { return w.name; });
    out.cookieWrites = cookieWrites.slice();
    out.navs = navs.slice();
    out.auxVerifierKeys = Array.from(sessionStorage._map.keys())
      .concat(Array.from(localStorage._map.keys()))
      .filter(function (k) { return k.indexOf('code-verifier') >= 0; });
  } else if (scenario === 'single_origin') {
    await sleep(200);
    out.fetchCalls = fetchCalls.slice();
    const exch = fetchCalls.filter(function (c) { return c.url.indexOf('/auth/v1/token') >= 0; });
    out.exchangeCount = exch.length;
    out.exchangeGrant = exch.length ? (new URL(exch[0].url)).searchParams.get('grant_type') : null;
    const body = exch.length ? exch[0].body : '';
    // supabase-js posts the exchange as a JSON body (verified against the
    // vendored bundle), not form-encoded — accept both so a transport change
    // surfaces as a wrong-value failure rather than a confusing null.
    let params = new URLSearchParams();
    try { const j = JSON.parse(body); params = new URLSearchParams(j); }
    catch (e) { params = new URLSearchParams(body); }
    out.exchangeVerifier = params.get('code_verifier');
    out.exchangeCode = params.get('auth_code') || params.get('code');
    out.navs = navs.slice();
    // The completion origin is the page's own origin/path: the callback landed
    // here (`?code=…`), and this scenario has no click, so the return target the
    // flow was initiated with is the page's own origin+path by construction.
    out.outerOrigin = fetchCalls.length ? new URL(fetchCalls[0].url).origin : null;
    out.innerOrigin = location.origin;
    out.innerPath = location.pathname;
  } else if (scenario === 'item6') {
    await sleep(60);
    const storage = q('cookieStorage');
    const small = JSON.stringify({ access_token: 'a', refresh_token: 'r', expires_at: 9 });
    cookieWrites.length = 0;
    storage.setItem(COOKIE_NAME, small);
    const smallWrite = cookieWrites[0];
    out.smallWritten = !!smallWrite && smallWrite.name === COOKIE_NAME &&
      smallWrite.header.indexOf('=' + encodeURIComponent(small)) >= 0;

    cookieWrites.length = 0;
    const big = JSON.stringify({
      access_token: 'a'.repeat(100), refresh_token: 'r'.repeat(50), expires_at: 9,
      provider_token: 'p'.repeat(2000), provider_refresh_token: 'q'.repeat(2000),
    });
    storage.setItem(COOKIE_NAME, big);
    out.strippedWrites = cookieWrites.length;
    out.strippedHasToken = cookieWrites.some(function (w) {
      return w.header.indexOf('provider_token') >= 0;
    });

    cookieWrites.length = 0;
    if (els['error']) { els['error'].classList.remove('visible'); }
    const over = JSON.stringify({ access_token: 'z'.repeat(6000), refresh_token: 'r', expires_at: 9 });
    storage.setItem(COOKIE_NAME, over);
    out.overCapWrote = cookieWrites.length > 0;
    out.overCapReported = els['error'] ? els['error'].classList.contains('visible') : false;
    out.overCapText = els['error'] ? String(els['error'].textContent) : '';
  } else {
    throw new Error('unknown scenario: ' + scenario);
  }

  out.warns = warns.slice(0, 20);
  process.stdout.write('RESULT ' + JSON.stringify(out) + '\n');
})().catch(function (e) {
  process.stderr.write('DRIVER ERROR: ' + (e && e.stack ? e.stack : e) + '\n');
  process.exit(1);
});
"""
