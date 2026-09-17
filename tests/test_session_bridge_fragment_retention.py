"""Regression gate for #3503 — a failed session write must not destroy the
only copy of the credential, on the REAL page composition.

THE DEFECT (original)
---------------------
``website/assets/supabase-session.js`` parses the implicit-flow OAuth fragment
(``#access_token=…``) synchronously at load time and calls ``storeSession()``,
which correctly returns ``false`` when the parent-domain cookie write did not
take. The caller DISCARDED that return value and then stripped the fragment
unconditionally::

    storeSession(session);
    history.replaceState(null, '', window.location.pathname + window.location.search);

So when the write failed (a cookie over the browser's ~4096-byte limit is
silently dropped — no exception), both copies of the credential vanished: the
cookie absent, the fragment erased. The user was stranded on ``/auth?next=…``
with a clean console. (#3559 will delete this whole legacy bridge; this gate
moves to the deletion test when it lands.)

THE DEFECT (adversarial review — the first fix was insufficient)
---------------------------------------------------------------
Gating the strip on ``storeSession()`` is invisible in the product, because the
page has TWO fragment consumers. ``website/signup.html`` (``/auth``) loads this
bridge AND builds a supabase-js client with ``detectSessionInUrl: true``.
supabase-js's ``_getSessionFromURL()`` assigns ``window.location.hash = ''``
BEFORE awaiting ``_saveSession()``, and ``_saveSession()`` calls the same
``supabaseStorage.setItem`` that hits the same cap and fails. The second
consumer therefore destroyed the fragment anyway — identical observable state
to the pre-fix code:

    { bridge_replaceState_calls: 0, hash_after_bridge: "#access_token=…",
      hash_after_supabase_ingest: "", fragment_survived_supabase: false,
      cookie_present: false }

The fix is to make the bridge the SINGLE fragment consumer on every page that
loads it (``detectSessionInUrl: false`` in the bridge factory and in the
dashboard's own client). ``test_fragment_survives_the_real_supabase_js_ingest``
runs the real bridge and the real vendored supabase-js bundle in one context
and asserts the fragment survives. A page that loads supabase-js WITHOUT the
bridge (``tortoise/oauth.py``'s server-rendered consent page) must keep
``detectSessionInUrl: true`` — pinned in the negative below.

WHY A COOKIE-JAR SHIM
---------------------
The real browser behaviour that makes this reachable is "an over-limit
``Set-Cookie`` is dropped SILENTLY and any pre-existing value is kept". A
static string assertion cannot observe that. The harness below runs the real
``supabase-session.js`` under ``vm.runInNewContext`` with a cookie jar that
enforces the 4096-byte ``name=value`` limit, then reports what happened to the
fragment and to the cookie.

The modelled limit and the code's refusal boundary must be the SAME number:
``SIZE_CAP == COOKIE_BYTE_LIMIT - COOKIE_NAME.length``, where
``COOKIE_BYTE_LIMIT`` models Chromium's ``kMaxCookieNamePlusValueSize``
(``net/cookies/parsed_cookie.h`` = 4096, enforced in ``parsed_cookie.cc`` as
``name.size() + value.size() > 4096`` — the ``=`` separator and the cookie
attributes are NOT part of the budget). A hardcoded cap above it (the earlier
literal ``4077`` vs the modelled ``4074``) leaves a dead band where the code
writes and the browser silently drops with no diagnostic.

THE DEFECT (adversarial review, round 2 — the fragment still died downstream)
-----------------------------------------------------------------------------
Making the bridge the single consumer left one destroyer standing: the
DASHBOARD mount gate. ``signup.html``'s ``redirectTo`` is ``claimRedirectTarget()``
-> the app root, so a refused write strands the live fragment on the dashboard
(``app.premiselabs.co``), not on ``/auth``. There, ``oauthErrorHash()`` returns
``''`` for a live token fragment by design (#1566), and ``bounceToAuth(search,
'')`` NAVIGATES — the browser drops the fragment on navigation. Observable
outcome identical to the original bug. The gate now renders a terminal error
state (``authUnavailable``, with the working ``window.location.reload()``
retry) instead of navigating away.

It is NOT a session-size claim: a real Google session on this path is far
smaller. The invariants under test are conditional — IF the write fails, the
fragment MUST survive, and a refused NEW write MUST NOT be reported as success
on the strength of an OLD valid cookie.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED = REPO_ROOT / "website" / "assets" / "supabase-session.js"
DASHBOARD = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"
DASHBOARD_BUNDLE_GLOB = "website/apps/dashboard/dist/assets/index-*.js"
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"
SIGNUP = REPO_ROOT / "website" / "signup.html"
VENDORED_SUPABASE = (
    REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "vendor"
    / "supabase-2.112.2.min.js"
)

COOKIE_NAME = "sb-tortoise-auth-token"
# Chromium: net/cookies/parsed_cookie.h kMaxCookieNamePlusValueSize = 4096,
# enforced as `name.size() + value.size() > 4096` in parsed_cookie.cc — the '='
# separator and the attributes are not counted. So the largest VALUE that
# survives is COOKIE_BYTE_LIMIT - len(COOKIE_NAME) = 4074.
COOKIE_BYTE_LIMIT = 4096
EXPECTED_SIZE_CAP = COOKIE_BYTE_LIMIT - len(COOKIE_NAME)  # 4074


def _require_node(node_path: str | None = None) -> str:
    """Fail (do NOT skip) when the required runtime is absent.

    A skipped gate is indistinguishable from a passing one in CI. This is the
    ``tests/e2e/auth/bff_test_helpers.require_toolchain`` pattern: the suite
    that guards prod behaviour must go RED, not green, when it cannot run.
    The python-ci ``test`` matrix job provisions node explicitly
    (``actions/setup-node`` in .github/workflows/python-ci.yml).
    """
    node = shutil.which("node")
    if node is None and os.environ.get("SESSION_BRIDGE_ALLOW_NO_NODE") != "1":
        pytest.fail(
            "node not available — the session-bridge fragment-retention gate "
            "cannot run and MUST NOT silently pass. Install node (the python-ci "
            "'test' matrix job does so via actions/setup-node) or set "
            "SESSION_BRIDGE_ALLOW_NO_NODE=1 to opt out explicitly."
        )
    assert node is not None
    return node


# Node harness. Runs the REAL bridge under a browser shim whose cookie jar
# enforces the 4096-byte `name=value` limit, then (in `e2e` mode) loads the REAL
# vendored supabase-js bundle and builds the page's client through the bridge
# factory. Prints one JSON report.
_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const bridgePath = process.argv[2];
const mode = process.argv[3] || 'bridge';
const bundlePath = process.argv[4] || '';
const dashboardPath = process.argv[5] || '';
const authPagePath = process.argv[5] || '';
const src = fs.readFileSync(bridgePath, 'utf8');

// Chrome limits a cookie's NAME + VALUE (net/cookies/parsed_cookie.h
// kMaxCookieNamePlusValueSize = 4096, enforced as name.size() + value.size()
// > 4096 in parsed_cookie.cc — the '=' separator and the attributes are NOT
// counted). An over-limit write is dropped SILENTLY (no exception) and any
// pre-existing value is kept.
const COOKIE_LIMIT = 4096;
const COOKIE_NAME = 'sb-tortoise-auth-token';

function makeEnv(opts) {
  const jar = new Map();
  if (opts && opts.preseed) jar.set(COOKIE_NAME, opts.preseed);

  const url = new URL('https://tortoise.premiselabs.co/auth?next=%2Fadmin%2F');
  if (opts && opts.hash) url.hash = opts.hash;

  const historyCalls = [];
  const replaceCalls = [];
  const navCalls = [];
  const logs = [];
  const store = {};
  const rec = (level) => (...a) => logs.push({ level, text: a.map(String).join(' ') });

  const document = {};
  Object.defineProperty(document, 'cookie', {
    get() {
      const parts = [];
      jar.forEach((v, k) => parts.push(k + '=' + v));
      return parts.join('; ');
    },
    set(raw) {
      const text = String(raw);
      const first = text.split(';')[0];
      const eq = first.indexOf('=');
      if (eq < 0) return;
      const name = first.slice(0, eq).trim();
      const value = first.slice(eq + 1);
      const attrs = text.slice(first.length).toLowerCase();
      const isDelete = value === '' || attrs.indexOf('max-age=0') !== -1;
      if (!isDelete && name.length + value.length > COOKIE_LIMIT) {
        return; // silent drop — previous value kept
      }
      if (isDelete) jar.delete(name);
      else jar.set(name, value);
    },
  });
  document.visibilityState = 'visible';
  document.addEventListener = function () {};
  document.removeEventListener = function () {};

  const location = {
    get href() { return url.toString(); },
    set href(v) { navCalls.push(String(v)); url.href = new URL(v, url).href; },
    get hash() { return url.hash; },
    set hash(v) { url.hash = v; },
    get search() { return url.search; },
    set search(v) { url.search = v; },
    get pathname() { return url.pathname; },
    set pathname(v) { url.pathname = v; },
    get origin() { return url.origin; },
    get hostname() { return url.hostname; },
    get protocol() { return url.protocol; },
    assign(v) { url.href = new URL(v, url).href; },
    replace(v) { replaceCalls.push(String(v)); navCalls.push(String(v)); url.href = new URL(v, url).href; },
    toString() { return url.toString(); },
  };
  const history = {
    state: null,
    replaceState(state, title, u) {
      historyCalls.push(u);
      if (u != null) {
        const n = new URL(u, url);
        url.hash = n.hash; url.search = n.search; url.pathname = n.pathname;
      }
    },
    pushState(state, title, u) { if (u != null) url.hash = new URL(u, url).hash; },
  };
  const localStorage = {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; },
  };
  const fetchStub = function (input) {
    const u = String(input);
    // The only network call on the fragment path is GoTrue /user.
    return Promise.resolve({
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      json: () => Promise.resolve({ id: 'u1', aud: 'authenticated', email: 'a@b.c' }),
      text: () => Promise.resolve('{"id":"u1"}'),
    });
  };

  const sandbox = {
    console: { log: rec('log'), warn: rec('warn'), error: rec('error'), info: rec('info'), debug: () => {} },
    URL: URL, URLSearchParams: URLSearchParams, Map: Map,
    TextEncoder: TextEncoder, TextDecoder: TextDecoder,
    AbortController: AbortController, Headers: globalThis.Headers,
    Request: globalThis.Request, Response: globalThis.Response,
    DOMException: globalThis.DOMException,
    WebSocket: function () { return { close() {} }; },
    crypto: globalThis.crypto,
    setTimeout: setTimeout, clearTimeout: clearTimeout,
    setInterval: () => 0, clearInterval: () => {},
    location: location, history: history, document: document,
    localStorage: localStorage, fetch: fetchStub,
    navigator: { userAgent: 'node-harness', locks: undefined },
    process: undefined,
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  return { sandbox, jar, historyCalls, replaceCalls, navCalls, logs };
}

function fragment(tokenLen, expiresAt) {
  return '#access_token=' + 'A'.repeat(tokenLen) +
    '&refresh_token=r' + 'R'.repeat(tokenLen) +
    '&expires_at=' + expiresAt +
    '&expires_in=3600&token_type=bearer&provider_token=' + 'P'.repeat(tokenLen);
}

function sessionFor(tokenLen, expiresAt) {
  return {
    access_token: 'A'.repeat(tokenLen),
    refresh_token: 'r' + 'R'.repeat(tokenLen),
    expires_at: expiresAt,
    expires_in: 3600,
    token_type: 'bearer',
  };
}

function oldCookie(expiresAt) {
  return encodeURIComponent(JSON.stringify({
    access_token: 'OLD-ACCESS-TOKEN',
    refresh_token: 'old-refresh',
    expires_at: expiresAt,
    expires_in: 3600,
    token_type: 'bearer',
  }));
}

function report(env, extra) {
  const jar = env.jar;
  const raw = jar.has(COOKIE_NAME) ? jar.get(COOKIE_NAME) : null;
  let stored = null;
  try { stored = raw ? JSON.parse(decodeURIComponent(raw)) : null; } catch (e) { stored = null; }
  return Object.assign({
    fragment_after: /access_token=/.test(extra.hash) ? 'retained' : 'stripped',
    replace_state_calls: env.historyCalls.length,
    cookie_present: jar.has(COOKIE_NAME),
    cookie_token: stored ? stored.access_token : null,
    errors: env.logs.filter((l) => l.level === 'error').map((l) => l.text),
    warns: env.logs.filter((l) => l.level === 'warn').map((l) => l.text),
  }, extra.fields || {});
}

// ── scenarios ───────────────────────────────────────────────────────────────

function runBridgeScenario(kind) {
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  const opts = { hash: kind === 'small' ? fragment(200, expiresAt) : fragment(4000, expiresAt) };
  if (kind === 'preseeded_old_cookie') opts.preseed = oldCookie(expiresAt);
  const env = makeEnv(opts);
  vm.runInContext(src, env.sandbox, { filename: bridgePath });
  const bridge = env.sandbox.window.__tortoiseSessionBridge;
  const fields = {
    size_cap: bridge.SIZE_CAP,
    cookie_byte_limit: bridge.COOKIE_BYTE_LIMIT,
  };
  return report(env, {
    hash: env.sandbox.window.location.hash,
    fields: fields,
  });
}

function runPreseededStoreScenario() {
  // The cookie jar holds a still-VALID session for a DIFFERENT access_token;
  // the page then tries to store a NEW oversized session. storeSession() must
  // NOT report success by reading the OLD cookie back.
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  const env = makeEnv({ hash: '', preseed: oldCookie(expiresAt) });
  vm.runInContext(src, env.sandbox, { filename: bridgePath });
  const returned = env.sandbox.window.storeSession(sessionFor(4000, expiresAt));
  const fields = { store_session_returned: returned };
  return report(env, { hash: env.sandbox.window.location.hash, fields: fields });
}

function sessionWithEncodedLength(target, expiresAt) {
  const make = (n) => ({
    access_token: 'A'.repeat(n),
    refresh_token: 'r',
    expires_at: expiresAt,
    expires_in: 3600,
    token_type: 'bearer',
  });
  const size = (n) => encodeURIComponent(JSON.stringify(make(n))).length;
  const pad = target - size(0);
  if (pad < 0) throw new Error('target too small: ' + target);
  const session = make(pad);
  if (size(pad) !== target) throw new Error('could not hit target ' + target);
  return session;
}

// ── the dashboard landing path (review P1, round 2) ──────────────────────
//
// The bridge and supabase-js are only the FIRST half of the chain. signup.html's
// redirectTo is the app root, so the live fragment lands on the DASHBOARD, where
// the mount gate decides whether to navigate. This runs the REAL bridge, the
// REAL vendored supabase-js and then the REAL mount-gate branch, extracted
// verbatim from main.jsx (anchor-verified, so a refactor throws instead of
// silently testing nothing) — the branch that threw the fragment away by
// navigating with oauthErrorHash()'s empty string.
function extractBraceBalanced(src, startIdx) {
  let depth = 0;
  for (let i = startIdx; i < src.length; i++) {
    const c = src[i];
    if (c === '"' || c === "'" || c === '`') {
      const quote = c;
      for (i++; i < src.length; i++) {
        if (src[i] === '\\') i++;
        else if (src[i] === quote) break;
      }
    } else if (c === '/' && src[i + 1] === '/') {
      while (i < src.length && src[i] !== '\n') i++;
    } else if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return src.slice(startIdx, i + 1); }
  }
  throw new Error('unbalanced braces from index ' + startIdx);
}

function extractFunction(src, name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('main.jsx: function ' + name + '() not found');
  const bodyStart = src.indexOf('{', start);
  // Keep the `function name()` head: a bare block would put the body's
  // `return` statements at top level ("Illegal return statement").
  return src.slice(start, bodyStart) + extractBraceBalanced(src, bodyStart);
}

async function runDashboardScenario(kind) {
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  const role = kind === 'small' ? 200 : 4000;
  const opts = { hash: kind === 'code_fragment' ? '#code=abc123' : fragment(role, expiresAt) };
  // The P2-1 precondition: a still-valid PREVIOUS cookie. `getSession()` then
  // answers with the OLD session — the case where a guard nested inside the
  // "no session" branch never runs.
  if (kind === 'preseeded_old_cookie') opts.preseed = oldCookie(expiresAt);
  const env = makeEnv(opts);
  env.sandbox.claimIntentInFlight = function () { return false; };
  vm.runInContext(src, env.sandbox, { filename: bridgePath });
  const hashAfterBridge = env.sandbox.window.location.hash;

  // The page's own client (the second consumer) + the real bundle.
  vm.runInContext(fs.readFileSync(bundlePath, 'utf8'), env.sandbox, { filename: bundlePath });
  const client = env.sandbox.window.createTortoiseSupabaseClient(
    'https://ybetwichurajbfswfeqa.supabase.co', 'anon-key'
  );
  if (client) {
    try { await client.auth.initializePromise; } catch (e) { /* recorded below */ }
    try { await client.auth.getSession(); } catch (e) { /* recorded below */ }
  }
  await new Promise((r) => setTimeout(r, 50));

  // The REAL mount-gate region, extracted from main.jsx: the live-fragment
  // guard through the end of the session-validity branch. `session`/`error` are
  // the values supabase-js's getSession() resolved — passed in so the harness
  // exercises BOTH the no-session and the still-valid-OLD-cookie cases.
  const dash = fs.readFileSync(dashboardPath, 'utf8');
  // The branch guards on src/sessionBounce.js's predicates. Load the REAL module
  // (stripping the ESM `export` keyword — the harness is CJS/vm, not a bundler).
  const helperPath = require('path').join(
    require('path').dirname(dashboardPath), 'sessionBounce.js');
  if (fs.existsSync(helperPath)) {
    vm.runInContext(
      fs.readFileSync(helperPath, 'utf8').replace(/^export\s+/gm, ''),
      env.sandbox, { filename: helperPath }
    );
  }
  const branchStart = dash.indexOf('\n', dash.indexOf(
    'const { data: { session }, error } = await supabaseClient.auth.getSession()')) + 1;
  const branchEnd = dash.indexOf('sessionTokenRef.current = session.access_token', branchStart);
  if (branchStart <= 0 || branchEnd < 0 || branchEnd < branchStart) {
    throw new Error('main.jsx: mount-gate branch anchors not found');
  }
  // Slice from the statement AFTER getSession() to the first statement after the
  // session-validity branch: brace-balanced in every revision (the round-2 tree
  // nested the guard inside that branch, the round-3 tree hoists it above).
  const branch = dash.slice(branchStart, branchEnd);
  const session = kind === 'preseeded_old_cookie'
    ? { access_token: 'OLD-ACCESS-TOKEN', refresh_token: 'old-refresh',
        expires_at: expiresAt, expires_in: 3600, token_type: 'bearer' }
    : null;
  const gateSrc =
    'const landingHash = ' + JSON.stringify(hashAfterBridge) + ';\n' +
    extractFunction(dash, 'oauthErrorHash') + '\n' +
    'const __mountGate = function (setChecking, setAuthUnavailable, setFragmentRefused, session, error) {\n' +
    branch + '\n};\n__mountGate;';
  const gate = vm.runInContext(gateSrc, env.sandbox, { filename: 'main.jsx#mountGate' });
  const calls = { checking: [], unavailable: [], refused: [] };
  gate(
    (v) => calls.checking.push(v),
    (v) => calls.unavailable.push(v),
    (v) => calls.refused.push(v),
    session,
    null,
  );

  const finalHash = env.sandbox.window.location.hash;
  return report(env, {
    hash: finalHash,
    fields: {
      hash_after_bridge_has_token: /access_token=/.test(hashAfterBridge),
      hash_after_bridge_raw: hashAfterBridge,
      hash_after_final_raw: finalHash,
      fragment_survived_supabase: /access_token=/.test(finalHash),
      navigated: env.navCalls.length > 0,
      replace_target: env.navCalls[env.navCalls.length - 1] || null,
      auth_unavailable: calls.unavailable[calls.unavailable.length - 1] || '',
      fragment_refused: calls.refused.indexOf(true) !== -1,
      checking_cleared: calls.checking.indexOf(false) !== -1,
      errors: env.logs.filter((l) => l.level === 'error').map((l) => l.text),
    },
  });
}

  // ── /auth (signup.html) head gate + async getSession bounce ────────────
  //
  // The OTHER fragment-receiving page. Its inline gate and its async bounce are
  // plain JS in signup.html (no bundler), executed here verbatim against the
  // REAL bridge — the round-4 P1 was that both navigated over a live refused
  // fragment whenever a still-valid OLD cookie was present.
  async function runAuthPageScenario(kind) {
    const expiresAt = Math.floor(Date.now() / 1000) + 3600;
    const opts = { hash: kind === 'no_fragment' ? '' : fragment(4000, expiresAt) };
    if (kind !== 'no_session') opts.preseed = oldCookie(expiresAt);
    const env = makeEnv(opts);
    // signup.html's /admin round-trip (__ADMIN_RETURN_TO): the destination the
    // gate picks when the return-to param is present, and what makes the
    // navigation observable in the report.
    env.sandbox.__ADMIN_RETURN_TO = '/admin/';
    env.sandbox.__DASHBOARD_BASE_URL = 'https://app.premiselabs.co';
    vm.runInContext(src, env.sandbox, { filename: bridgePath });
    const hashAfterBridge = env.sandbox.window.location.hash;

    const html = fs.readFileSync(authPagePath, 'utf8');
    // Anchored on the gate's own first statement so the harness runs the REAL
    // gate in ANY revision (the round-4 guard is what the predicate adds — the
    // gate itself predates it).
    const pAnchor = html.indexOf('var p = new URLSearchParams(window.location.search);');
    const gateStart = html.lastIndexOf('(function () {', pAnchor);
    const gateEnd = html.indexOf('})();', pAnchor);
    const predStart = html.indexOf('window.liveFragmentCredential = function');
    if (pAnchor < 0 || gateStart < 0 || gateEnd < 0) {
      throw new Error('signup.html: head-gate anchors not found');
    }
    if (predStart >= 0 && predStart < gateStart) {
      vm.runInContext(html.slice(predStart, gateStart), env.sandbox,
                      { filename: 'signup.html#predicate' });
    }
    vm.runInContext(html.slice(gateStart, gateEnd + 5), env.sandbox,
                    { filename: 'signup.html#headGate' });

    // The async getSession bounce from the module script, with the resolved
    // session injected (the real supabaseClient is built further down the page).
    const asyncAnchor = html.indexOf(
      'supabaseClient.auth.getSession().then(function (r) {');
    if (asyncAnchor < 0) throw new Error('signup.html: async bounce anchor not found');
    const bodyStart = html.indexOf('{', html.indexOf('function (r)', asyncAnchor));
    const asyncBody = extractBraceBalanced(html, bodyStart);
    // NOTE: at a pre-fix revision the async body does not reference the guard at
    // all, so it runs unchanged and its navigation is observed directly.
    env.sandbox.claimRedirectTarget = function () {
      // Mirrors signup.html's own precedence: the /admin return-to wins.
      return env.sandbox.__ADMIN_RETURN_TO || env.sandbox.__DASHBOARD_BASE_URL;
    };
    env.sandbox.oauthErrorParams = function () { return { error: '', code: '' }; };
    const bounce = vm.runInContext('(function (r) ' + asyncBody + ')', env.sandbox,
                                   { filename: 'signup.html#asyncBounce' });
    bounce({
      data: {
        session: kind === 'no_session' ? null : {
          access_token: 'OLD-ACCESS-TOKEN', refresh_token: 'old-refresh',
          expires_at: expiresAt, expires_in: 3600, token_type: 'bearer',
        },
      },
    });
    await new Promise((r) => setTimeout(r, 50));

    const finalHash = env.sandbox.window.location.hash;
    return report(env, {
      hash: finalHash,
      fields: {
        hash_after_bridge_has_token: /access_token=/.test(hashAfterBridge),
        fragment_survived_gate: /access_token=/.test(finalHash),
        navigated: env.navCalls.length > 0,
        replace_target: env.navCalls[env.navCalls.length - 1] || null,
        fragment_refused: env.sandbox.window.__FRAGMENT_REFUSED === true,
        errors: env.logs.filter((l) => l.level === 'error').map((l) => l.text),
      },
    });
  }

function runBoundaryScenario(kind) {
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  const env = makeEnv({ hash: '', preseed: oldCookie(Math.floor(Date.now() / 1000) + 7200) });
  vm.runInContext(src, env.sandbox, { filename: bridgePath });
  const cap = env.sandbox.window.__tortoiseSessionBridge.SIZE_CAP;
  const target = kind === 'at_cap' ? cap : cap + 1;
  // `target` is the ENCODED length of the JSON the adapter is handed; the
  // adapter's guard strips nothing here (no provider_token/user fields).
  const session = sessionWithEncodedLength(target, expiresAt);
  const returned = env.sandbox.window.storeSession(session);
  const fields = {
    target_encoded_len: target,
    size_cap: cap,
    store_session_returned: returned,
    // The modelled browser boundary: an over-budget name + value is dropped
    // (the '=' separator is not part of Chromium's budget).
    browser_would_drop: COOKIE_NAME.length + target > COOKIE_LIMIT,
  };
  const r = report(env, { hash: env.sandbox.window.location.hash, fields: fields });
  r.stored_token = env.jar.has(COOKIE_NAME)
    ? JSON.parse(decodeURIComponent(env.jar.get(COOKIE_NAME))).access_token
    : null;
  return r;
}

async function runE2EIngestScenario(kind) {
  // The REAL page composition: the bridge + the REAL vendored supabase-js UMD
  // bundle in ONE context, with the page's client built through the bridge
  // factory. This is the gate that would have caught the second consumer.
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  const opts = { hash: kind === 'small' ? fragment(200, expiresAt) : fragment(4000, expiresAt) };
  if (kind === 'preseeded_old_cookie') opts.preseed = oldCookie(expiresAt);
  const env = makeEnv(opts);
  vm.runInContext(src, env.sandbox, { filename: bridgePath });
  const afterBridgeHash = env.sandbox.window.location.hash;
  vm.runInContext(fs.readFileSync(bundlePath, 'utf8'), env.sandbox, { filename: bundlePath });
  const client = env.sandbox.window.createTortoiseSupabaseClient(
    'https://ybetwichurajbfswfeqa.supabase.co', 'anon-key'
  );
  let initError = null;
  if (client) {
    try { await client.auth.initializePromise; } catch (e) { initError = String(e && e.message); }
    try { await client.auth.getSession(); } catch (e) { initError = initError || String(e && e.message); }
  }
  await new Promise((r) => setTimeout(r, 50));
  const finalHash = env.sandbox.window.location.hash;
  return report(env, {
    hash: finalHash,
    fields: {
      client_created: !!client,
      detect_session_in_url: client && client.auth ? client.auth.detectSessionInUrl : null,
      hash_after_bridge_has_token: /access_token=/.test(afterBridgeHash),
      fragment_survived_supabase: /access_token=/.test(finalHash),
      init_error: initError,
    },
  });
}

(async function () {
  // Each scenario is isolated: a throw is recorded on that scenario only, so a
  // pre-fix tree reports WHICH invariant broke instead of aborting the harness.
  const safe = function (fn) {
    try { return fn(); } catch (e) { return { __error: String((e && e.message) || e) }; }
  };
  const safeAsync = async function (fn) {
    try { return await fn(); } catch (e) { return { __error: String((e && e.message) || e) }; }
  };
  const out = {};
  if (mode === 'bridge') {
    out.small = safe(() => runBridgeScenario('small'));
    out.oversized = safe(() => runBridgeScenario('oversized'));
    out.preseeded_old_cookie = safe(() => runBridgeScenario('preseeded_old_cookie'));
    out.store_with_old_cookie = safe(() => runPreseededStoreScenario());
    out.boundary_at_cap = safe(() => runBoundaryScenario('at_cap'));
    out.boundary_over_cap = safe(() => runBoundaryScenario('over_cap'));
  } else if (mode === 'dashboard') {
    out.dashboard_small = await safeAsync(() => runDashboardScenario('small'));
    out.dashboard_oversized = await safeAsync(() => runDashboardScenario('oversized'));
    out.dashboard_preseeded_old_cookie =
      await safeAsync(() => runDashboardScenario('preseeded_old_cookie'));
    out.dashboard_code_fragment = await safeAsync(() => runDashboardScenario('code_fragment'));
  } else if (mode === 'auth_page') {
    out.auth_page_oversized = await safeAsync(() => runAuthPageScenario('oversized'));
    out.auth_page_no_session = await safeAsync(() => runAuthPageScenario('no_session'));
    out.auth_page_no_fragment = await safeAsync(() => runAuthPageScenario('no_fragment'));
  } else {
    out.e2e_small = await safeAsync(() => runE2EIngestScenario('small'));
    out.e2e_oversized = await safeAsync(() => runE2EIngestScenario('oversized'));
    out.e2e_preseeded_old_cookie = await safeAsync(() => runE2EIngestScenario('preseeded_old_cookie'));
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => {
  process.stderr.write('HARNESS FAILURE: ' + (e && e.stack || e) + '\n');
  process.exit(1);
});
"""


def _run_harness(mode: str, bundle: Path | None = None,
                 page: Path | None = None) -> dict:
    """`page` is the surface under test for dashboard/auth_page modes:
    main.jsx for `dashboard`, signup.html for `auth_page`."""
    node = _require_node()
    assert SHARED.exists(), f"missing file: {SHARED}"
    args = [node]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(_HARNESS)
        harness = f.name
    try:
        args += [harness, str(SHARED), mode]
        if bundle is not None:
            args.append(str(bundle))
        if page is not None:
            args.append(str(page))
        proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
    finally:
        os.unlink(harness)
    assert proc.returncode == 0, (
        f"node harness failed (exit {proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
    )
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def bridge_report() -> dict:
    return _run_harness("bridge")


def _scenario(report: dict, key: str) -> dict:
    """Fetch one isolated harness scenario, surfacing any harness throw as a
    readable failure instead of a KeyError."""
    assert key in report, f"harness did not report scenario {key!r}: {sorted(report)}"
    r = report[key]
    assert "__error" not in r, f"harness scenario {key!r} threw: {r['__error']}"
    return r


@pytest.fixture(scope="module")
def e2e_report() -> dict:
    assert VENDORED_SUPABASE.exists(), (
        f"missing vendored supabase-js bundle: {VENDORED_SUPABASE} — the "
        "end-to-end ingest gate cannot run"
    )
    return _run_harness("e2e", VENDORED_SUPABASE)


@pytest.fixture(scope="module")
def dashboard_report() -> dict:
    """The FULL landing chain: real bridge + real supabase-js + the real
    mount-gate branch extracted from main.jsx."""
    assert VENDORED_SUPABASE.exists(), f"missing vendored bundle: {VENDORED_SUPABASE}"
    assert DASHBOARD.exists(), f"missing dashboard source: {DASHBOARD}"
    return _run_harness("dashboard", VENDORED_SUPABASE, DASHBOARD)


@pytest.fixture(scope="module")
def auth_page_report() -> dict:
    """The OTHER fragment-receiving page: the real bridge + signup.html's real
    inline head gate and real async getSession bounce."""
    assert VENDORED_SUPABASE.exists(), f"missing vendored bundle: {VENDORED_SUPABASE}"
    assert SIGNUP.exists(), f"missing auth page: {SIGNUP}"
    return _run_harness("auth_page", VENDORED_SUPABASE, SIGNUP)


# ── the refusal boundary IS the modelled browser limit (P3-1) ───────────────


def test_size_cap_equals_the_modelled_browser_boundary(bridge_report: dict) -> None:
    """A hardcoded cap ABOVE the real boundary is a dead band: the code writes,
    the browser silently drops, and no diagnostic fires. The earlier literal
    ``4077`` had no reproducible derivation and sat 3 bytes above the real
    boundary; the modelled one is ``4096 - len(name) = 4074`` (Chromium budgets
    ``name + value``, NOT the ``=`` separator, so the previous
    ``4096 - (name + 1) = 4073`` model was one byte too tight). Pin the derived
    value so the code, the harness and the browser cannot drift."""
    assert EXPECTED_SIZE_CAP == 4074, "the derivation itself regressed"
    r = _scenario(bridge_report, "oversized")
    assert r["cookie_byte_limit"] == COOKIE_BYTE_LIMIT
    assert r["size_cap"] == EXPECTED_SIZE_CAP, (
        f"bridge SIZE_CAP={r['size_cap']} but the modelled browser boundary is "
        f"{EXPECTED_SIZE_CAP} (COOKIE_BYTE_LIMIT - len(COOKIE_NAME)) — "
        "the code would write past the cap with no diagnostic"
    )
    # The harness's own boundary model, restated as arithmetic: exactly
    # COOKIE_NAME.length + SIZE_CAP fits Chromium's 4096 budget, one byte more
    # does not. If the shim were tightened independently of the code, the
    # boundary tests below would pass vacuously.
    assert len(COOKIE_NAME) + EXPECTED_SIZE_CAP == COOKIE_BYTE_LIMIT
    assert len(COOKIE_NAME) + EXPECTED_SIZE_CAP + 1 > COOKIE_BYTE_LIMIT


def test_write_at_the_cap_lands_and_one_byte_over_is_refused(bridge_report: dict) -> None:
    """Boundary case: the largest encodable value must be written and kept, and
    one byte more must be refused — the two bands must be adjacent, not
    overlapping."""
    at = _scenario(bridge_report, "boundary_at_cap")
    assert at["target_encoded_len"] == at["size_cap"]
    assert at["store_session_returned"] is True, (
        "a session exactly at SIZE_CAP must be stored"
    )
    assert at["browser_would_drop"] is False, (
        "harness precondition: SIZE_CAP sits inside the modelled browser budget"
    )
    assert at["stored_token"] is not None and at["stored_token"].startswith("A")

    over = _scenario(bridge_report, "boundary_over_cap")
    assert over["target_encoded_len"] == over["size_cap"] + 1
    assert over["browser_would_drop"] is True, (
        "harness precondition: SIZE_CAP + 1 is dropped by the modelled browser "
        "limit — so the code MUST refuse it or the write is a silent no-op"
    )
    assert over["store_session_returned"] is False, (
        "SIZE_CAP + 1 must be refused: the browser drops it and the caller must "
        "not be told the session was stored"
    )
    assert over["stored_token"] == "OLD-ACCESS-TOKEN", (
        "a refused write must leave the previous cookie untouched"
    )
    assert any("cap" in e for e in over["errors"]), (
        f"a refused write must surface a real failure: {over['errors']}"
    )


# ── the credential survives a failed write (the original invariant) ─────────


def test_oversized_session_keeps_the_fragment_and_surfaces_failure(
    bridge_report: dict,
) -> None:
    """When the cookie is rejected, the fragment is the ONLY remaining copy —
    it must not be stripped, and the bridge must not pretend the write
    succeeded."""
    r = _scenario(bridge_report, "oversized")
    assert r["fragment_after"] == "retained", (
        "the URL fragment was stripped even though the cookie write did not "
        "take — the credential now exists nowhere (#3503)"
    )
    assert r["replace_state_calls"] == 0, (
        "history.replaceState ran despite a failed storeSession() — the "
        "fragment strip must be gated on the write succeeding (#3503)"
    )
    assert r["cookie_present"] is False, (
        "harness precondition: the oversize cookie should have been dropped"
    )
    assert any("cap" in e for e in r["errors"]), (
        "a refused write must surface a real failure (console.error), not only "
        "a console.warn that fires below the actual cap (#3503)"
    )


def test_small_session_writes_the_cookie_and_strips_the_fragment(
    bridge_report: dict,
) -> None:
    """The success path is unchanged: a session that fits is stored and the
    fragment is then stripped so supabase-js does not re-process it."""
    r = _scenario(bridge_report, "small")
    assert r["cookie_present"] is True, "a small session must be written to the cookie"
    assert r["fragment_after"] == "stripped", (
        "the fragment must be stripped once the credential is safely stored"
    )
    assert r["replace_state_calls"] == 1
    assert not r["errors"], f"no error expected on the success path: {r['errors']}"


# ── a refused NEW write is not masked by an OLD valid cookie (P2-1) ─────────


def test_refused_write_is_not_reported_successful_by_an_old_valid_cookie(
    bridge_report: dict,
) -> None:
    """``storeSession`` must prove the value it just wrote is the value in the
    jar. A bare ``readValidSession() !== null`` reads WHATEVER session is
    present: with a still-valid PREVIOUS cookie, a REFUSED write returned true,
    the fragment gate then stripped the NEW credential, and the user was left
    authenticated as the OLD account."""
    r = _scenario(bridge_report, "store_with_old_cookie")
    assert r["cookie_token"] == "OLD-ACCESS-TOKEN", (
        "harness precondition: the pre-seeded old session must still be present"
    )
    assert r["store_session_returned"] is False, (
        "storeSession() reported success for a REFUSED write because a valid "
        "OLD cookie was still readable — the new credential is destroyed while "
        "the user stays authenticated as the old account (#3503 P2-1)"
    )
    assert r["errors"], (
        "the refused write must still be reported (console.error) even when an "
        "old cookie is present"
    )


def test_old_valid_cookie_does_not_let_the_fragment_gate_strip_the_new_token(
    bridge_report: dict,
) -> None:
    """The end-to-end consequence of the above: the bridge must NOT strip the
    fragment when the NEW write was refused, even though an OLD valid session
    makes the jar look authenticated."""
    r = _scenario(bridge_report, "preseeded_old_cookie")
    assert r["cookie_token"] == "OLD-ACCESS-TOKEN", "harness precondition"
    assert r["fragment_after"] == "retained", (
        "the fragment was stripped after a refused write because an OLD valid "
        "cookie was readable — the new credential is gone (#3503 P2-1)"
    )
    assert r["replace_state_calls"] == 0
    assert r["errors"]


# ── the REAL page composition: ONE fragment consumer (review P1) ────────────


def test_fragment_survives_the_real_supabase_js_ingest(e2e_report: dict) -> None:
    """Load the real bridge AND the real vendored supabase-js bundle in one
    context and drive the OAuth implicit-fragment return.

    With ``detectSessionInUrl: true`` supabase-js's own ``_getSessionFromURL``
    clears ``window.location.hash`` before its ``_saveSession`` hits the same
    refusing ``setItem`` — the bridge's retained fragment is destroyed and the
    end-to-end outcome is identical to the pre-fix code. The bridge must be the
    single fragment consumer on a page that loads it."""
    r = _scenario(e2e_report, "e2e_oversized")
    assert r["client_created"] is True, (
        f"harness precondition: the bridge factory must build a client: {r}"
    )
    assert r["detect_session_in_url"] is False, (
        "a page that loads the bridge must NOT let supabase-js ingest the "
        "fragment itself — two consumers race for one fragment and the second "
        "one (which clears the hash before its save can fail) destroys it"
    )
    assert r["hash_after_bridge_has_token"] is True, (
        "the bridge must retain the fragment when its write is refused"
    )
    assert r["fragment_survived_supabase"] is True, (
        "supabase-js destroyed the fragment the bridge correctly retained — the "
        "end-to-end invariant ('IF the write fails the fragment MUST survive') "
        "does not hold on the real /auth page (#3503 review P1)"
    )
    assert r["cookie_present"] is False, "harness precondition: the write was refused"


def test_real_page_success_path_is_unchanged(e2e_report: dict) -> None:
    """A session that fits is still stored, and the fragment is still cleared by
    the bridge — disabling supabase-js's redundant ingestion must not change the
    observable success path."""
    r = _scenario(e2e_report, "e2e_small")
    assert r["detect_session_in_url"] is False
    assert r["cookie_present"] is True, "a small session must be stored"
    assert r["cookie_token"] is not None and r["cookie_token"].startswith("A")
    assert r["fragment_survived_supabase"] is False, (
        "on success the BRIDGE clears the fragment (that is the success signal)"
    )
    assert r["hash_after_bridge_has_token"] is False


def test_real_page_old_cookie_does_not_destroy_the_new_fragment(
    e2e_report: dict,
) -> None:
    """The real-page consequence of P2-1: a still-valid OLD cookie in the jar
    must not make a refused NEW write look successful and let the new fragment
    be cleared — that is an account mix-up, not just a lost credential."""
    r = _scenario(e2e_report, "e2e_preseeded_old_cookie")
    assert r["cookie_token"] == "OLD-ACCESS-TOKEN", "harness precondition"
    assert r["hash_after_bridge_has_token"] is True
    assert r["fragment_survived_supabase"] is True, (
        "the new credential's fragment was destroyed while the user stayed "
        "authenticated as the OLD account (#3503 P2-1)"
    )


# ── the single-consumer rule is pinned per surface (review P1 scoping) ──────


def test_bridge_pages_disable_supabase_js_fragment_ingestion() -> None:
    """Every client built on a page that LOADS the bridge must have
    ``detectSessionInUrl: false``. The bridge factory covers signup.html +
    signin.html; the dashboard builds its own client in main.jsx (its
    index.html loads the bridge). A revert in either file reintroduces the
    second consumer."""
    shared = SHARED.read_text(encoding="utf-8")
    dash = DASHBOARD.read_text(encoding="utf-8")
    # Match ASSIGNMENT lines only (leading whitespace then the key) so an
    # explanatory comment mentioning the flipped value cannot satisfy the gate.
    off = re.compile(r"^\s*detectSessionInUrl:\s*false", re.M)
    on = re.compile(r"^\s*detectSessionInUrl:\s*true", re.M)
    for label, text in (("website/assets/supabase-session.js", shared),
                        ("website/apps/dashboard/src/main.jsx", dash)):
        assert off.search(text), (
            f"{label}: the bridge is the fragment consumer — the client must set "
            "detectSessionInUrl: false"
        )
        assert not on.search(text), (
            f"{label}: detectSessionInUrl: true reintroduces the second "
            "fragment consumer that destroyed the credential (#3503 P1)"
        )
    # The dashboard serves the committed bundle built from main.jsx — a stale
    # dist would ship the two-consumer bug behind a green source tree.
    bundles = sorted(REPO_ROOT.glob(DASHBOARD_BUNDLE_GLOB))
    assert bundles, "no built dashboard bundle found — rebuild the dashboard"
    for bundle in bundles:
        js = bundle.read_text(encoding="utf-8")
        assert "detectSessionInUrl:!1" in js, (
            f"{bundle}: built dashboard client does not disable supabase-js "
            "fragment ingestion — rebuild (npm run build) after editing main.jsx"
        )


def test_oauth_consent_page_keeps_its_own_fragment_ingestion() -> None:
    """``tortoise/oauth.py`` renders a server-side consent page that does NOT
    load /assets/supabase-session.js. There is no bridge there, so this client
    is the ONLY fragment consumer and MUST keep ``detectSessionInUrl: true`` —
    flipping it would break the provider redirect back silently."""
    text = OAUTH.read_text(encoding="utf-8")
    on = re.compile(r"^\s*detectSessionInUrl:\s*true", re.M)
    off = re.compile(r"^\s*detectSessionInUrl:\s*false", re.M)
    assert on.search(text), (
        "tortoise/oauth.py's consent page does not load the bridge — its client "
        "must keep ingesting the fragment"
    )
    assert not off.search(text), (
        "tortoise/oauth.py does not load the bridge; disabling fragment "
        "ingestion here breaks the OAuth redirect back (#3503 P1 scoping)"
    )
    # Guard the premise: if the bridge is ever added to that page, this test's
    # assumption is void and the flag must be revisited.
    assert 'src="/assets/supabase-session.js"' not in text, (
        "tortoise/oauth.py now loads the shared bridge — the detectSessionInUrl "
        "scoping decision must be revisited"
    )


# ── the dashboard landing path (review P1, round 2) ─────────────────────────


def test_dashboard_mount_gate_keeps_the_live_fragment(dashboard_report: dict) -> None:
    """The end-to-end outcome on the ORIGIN THAT ACTUALLY RECEIVES THE CALLBACK.

    signup.html's `redirectTo` is `claimRedirectTarget()` -> the app root, so the
    refused-write fragment lands on the DASHBOARD. This runs the real bridge, the
    real supabase-js bundle and the real mount-gate branch (extracted from
    main.jsx). Before the fix, the gate called
    ``bounceToAuth(search, oauthErrorHash())`` where ``oauthErrorHash()`` is ``''``
    for a live token fragment by design (#1566) — the navigation dropped the
    fragment and the credential existed nowhere: the original #3503 loss, one
    navigation later, with a clean console.
    """
    r = _scenario(dashboard_report, "dashboard_oversized")
    assert r["cookie_present"] is False, "harness precondition: the write was refused"
    assert r["hash_after_bridge_has_token"] is True, (
        "harness precondition: the bridge retained the fragment"
    )
    assert r["fragment_survived_supabase"] is True, (
        "harness precondition: supabase-js no longer ingests the fragment"
    )
    assert r["navigated"] is False, (
        "the dashboard mount gate NAVIGATED while a live OAuth token fragment was "
        "in the URL — the browser drops the fragment on navigation and the only "
        "surviving copy of the credential is destroyed (#3503 review P1)"
    )
    assert r["replace_target"] is None
    assert r["auth_unavailable"], (
        "a refused write must surface a visible failure state, not a silent "
        "'Redirecting…' shell"
    )
    assert r["fragment_refused"] is True, (
        "the terminal state must be flagged so the error card can offer an "
        "explicit route to /auth — the reload retry is a guaranteed no-op for "
        "the too-large cause (#3503 review P3)"
    )
    assert r["checking_cleared"] is True, (
        "the error card is unreachable while `checking` is true — the gate must "
        "clear it or the user sees 'Checking your session…' forever"
    )


def test_dashboard_success_path_still_navigates_when_there_is_no_fragment(
    dashboard_report: dict,
) -> None:
    """The guard must be narrow. This drives the REAL branch body directly (not
    supabase-js's session resolution — the branch is only entered when there is
    no strictly-valid session), with a fragment the BRIDGE already stripped
    because its write succeeded. The gate must still bounce to /auth there:
    a guard keyed on anything looser than a live credential would render an
    error card for every logged-out visitor."""
    r = _scenario(dashboard_report, "dashboard_small")
    assert r["cookie_present"] is True, "harness precondition: the write succeeded"
    assert r["hash_after_bridge_has_token"] is False, (
        "harness precondition: the bridge stripped the fragment on success"
    )
    assert r["navigated"] is True, (
        "with no live fragment the gate must still redirect to /auth"
    )
    assert r["replace_target"] and "/auth" in r["replace_target"]
    assert not r["auth_unavailable"]


def test_dashboard_mount_gate_survives_a_still_valid_old_cookie(
    dashboard_report: dict,
) -> None:
    """#3503 (review P2, round 3): a still-valid PREVIOUS cookie satisfies the
    session-validity branch, so a guard nested INSIDE that branch never runs —
    `getSession()` answers with the OLD identity, the dashboard proceeds, and the
    user is silently kept on the OLD account while the NEW credential sits unused
    in the URL. The guard must be hoisted above the branch and keyed on the
    fragment carrying a DIFFERENT token than the resolved session."""
    r = _scenario(dashboard_report, "dashboard_preseeded_old_cookie")
    assert r["cookie_token"] == "OLD-ACCESS-TOKEN", "harness precondition: the old session is stored"
    assert r["hash_after_bridge_has_token"] is True, (
        "harness precondition: the NEW fragment was refused and retained"
    )
    assert r["fragment_survived_supabase"] is True
    assert r["navigated"] is False
    assert r["fragment_refused"] is True, (
        "the dashboard proceeded as the OLD account: a live fragment carrying a "
        "different credential than the resolved session must surface the "
        "failure, not silently continue (#3503 P2-1 mix-up)"
    )
    assert r["auth_unavailable"], (
        "the user must see WHY they are not signed in as the account they just "
        "authenticated as"
    )
    assert r["checking_cleared"] is True


def test_dashboard_mount_gate_never_navigates_over_a_live_token_fragment() -> None:
    """Static companion to the harness above: the guard must run BEFORE the
    session-validity branch it protects (a nested guard is unreachable exactly
    when a previous cookie is valid) and before the bounce."""
    dash = DASHBOARD.read_text(encoding="utf-8")
    assert "from './sessionBounce.js'" in dash, (
        "main.jsx must import the live-fragment predicates (src/sessionBounce.js)"
    )
    anchor = dash.index("const claimIntent = claimIntentInFlight()")
    bounce = dash.index(
        "window.bounceToAuth(window.location.search, oauthErrorHash())", anchor
    )
    guard = dash.index("hasLiveTokenFragment(", anchor)
    assert guard < bounce, (
        "the live-fragment guard must run BEFORE the mount-gate bounce — "
        "otherwise the navigation destroys the only copy of the credential"
    )
    # Hoisted above `if (error || !session …)`: the guard must not be nested in
    # the no-session branch, or a valid old cookie skips it entirely.
    session_branch = dash.find("if (error || !session || !session.expires_at", anchor)
    assert session_branch != -1, (
        "the session-validity branch was not found AFTER the guard — the guard "        "is therefore nested inside it, where a still-valid old cookie makes it "        "unreachable (#3503 review P2)"
    )
    assert guard < session_branch, (
        "the live-fragment guard must run BEFORE the session-validity branch — "
        "nested inside it, a still-valid old cookie makes it unreachable and "
        "the user is silently kept on the old account (#3503 review P2)"
    )
    segment = dash[anchor:session_branch]
    assert "hasLiveTokenFragment(window.location.hash)" in segment, (
        "the guard must test the CURRENT URL fragment, not a stale snapshot"
    )
    assert "fragmentAccessToken(window.location.hash)" in segment, (
        "the guard must compare the fragment's own token with the resolved "
        "session — a bare `hasLiveTokenFragment` would refuse to continue even "
        "when the stored session IS that credential"
    )
    assert "setAuthUnavailable(" in segment and "setChecking(false)" in segment, (
        "a refused write must render a terminal error state, not a silent redirect"
    )
    assert "setFragmentRefused(true)" in segment, (
        "the terminal state must be flagged so the card can offer an explicit "
        "route to /auth (the reload retry is a no-op for the too-large cause)"
    )
    # ...and the flag must actually drive a recovery action in the error card.
    assert "{fragmentRefused ?" in dash, (
        "nothing renders off `fragmentRefused` — the flag would be dead state "
        "and the user would be left with only a retry that cannot succeed"
    )
    assert "window.bounceToAuth(window.location.search, '')" in dash, (
        "the error card needs an explicit route to /auth that deliberately "
        "discards the fragment (a user-chosen discard, never the silent #3503 one)"
    )


def test_head_gate_exempts_every_fragment_the_guard_calls_a_credential() -> None:
    """#3503 (review P3, round 3): three predicates classify a live credential.
    The pre-React head gate's `hasCallbackFragment` omitted `refresh_token`,
    so a `#refresh_token=…` fragment was bounced (and thus destroyed) by the
    gate before the mount guard could see it. All of them must agree."""
    head_gate = (REPO_ROOT / "website" / "apps" / "dashboard" / "index.html").read_text(
        encoding="utf-8"
    )
    m = re.search(r"var hasCallbackFragment = (/.*?/)\.test", head_gate)
    assert m, "index.html: hasCallbackFragment not found — the head gate changed"
    pattern = m.group(1)
    for token in ("access_token", "refresh_token", "code"):
        assert token in pattern, (
            f"the head gate's callback-fragment pattern ({pattern}) omits "
            f"{token!r} — that fragment is bounced and destroyed before the "
            "mount guard can retain it (#3503 review P3)"
        )
    # ...and the mount guard must agree with it (same three tokens).
    helper = (REPO_ROOT / "website" / "apps" / "dashboard" / "src"
              / "sessionBounce.js").read_text(encoding="utf-8")
    live = re.search(r"LIVE_TOKEN_FRAGMENT = (/.*?/)", helper)
    assert live, "sessionBounce.js: LIVE_TOKEN_FRAGMENT not found"
    for token in ("access_token", "refresh_token", "code"):
        assert token in live.group(1)
    # The committed dist must carry the fixed head gate too (it is what ships).
    dist_html = (REPO_ROOT / "website" / "apps" / "dashboard" / "dist"
                 / "index.html").read_text(encoding="utf-8")
    assert "refresh_token|code" in dist_html or "refresh_token" in dist_html, (
        "the committed dashboard dist/index.html still carries the old head "
        "gate — rebuild (npm run build) after editing index.html"
    )


def test_dashboard_lone_code_fragment_with_no_session_is_not_dropped(
    dashboard_report: dict,
) -> None:
    """#3503 (review P3, round 3): `session && session.access_token` is `null`
    when there is NO session, which compares EQUAL to a fragment carrying no
    access_token (`#code=…`, `#refresh_token=…`) — so those fragments skipped the
    guard and were destroyed by the bounce. No configured flow emits a lone
    #code fragment today (the implicit flow carries #access_token), so this is
    defence in depth: the exemption requires a RESOLVED session."""
    r = _scenario(dashboard_report, "dashboard_code_fragment")
    # The bridge itself only reads #access_token (implicit flow), so it does not
    # touch a #code fragment — but the MOUNT GATE still classified it as live and
    # bounced over it.
    assert r["hash_after_bridge_raw"] == "#code=abc123", (
        f"harness precondition: the #code fragment is intact, got "
        f"{r['hash_after_bridge_raw']!r}"
    )
    assert r["fragment_refused"] is True, (
        "a live #code fragment with no resolved session was bounced over — the "
        "browser drops it on navigation and the credential is gone (#3503 P3)"
    )
    assert r["navigated"] is False
    assert r["hash_after_final_raw"] == "#code=abc123", (
        f"the #code fragment was destroyed: {r['hash_after_final_raw']!r}"
    )
    assert r["auth_unavailable"]


# ── the /auth page gate (review P1, round 4) ────────────────────────────────


def test_auth_page_gate_keeps_a_refused_fragment_instead_of_navigating(
    auth_page_report: dict,
) -> None:
    """signup.html (`/auth`) is the other page that receives a fragment — it is
    where the bridge lands for the `/admin` round-trip (`__ADMIN_RETURN_TO`).
    Its inline head gate and its async `getSession` bounce both navigated on a
    still-valid PREVIOUS cookie, so the refused NEW credential was destroyed and
    the visitor silently continued as the OLD account: the dashboard round-3 fix
    did not cover this page."""
    r = _scenario(auth_page_report, "auth_page_oversized")
    assert r["cookie_token"] == "OLD-ACCESS-TOKEN", "harness precondition"
    assert r["hash_after_bridge_has_token"] is True, (
        "harness precondition: the bridge refused the write and kept the fragment"
    )
    assert r["navigated"] is False, (
        f"the /auth gate navigated to {r['replace_target']!r} while a live "
        "refused fragment was in the URL — the browser drops the fragment on "
        "navigation, destroying the credential and continuing as the OLD "
        "account (#3503 review P1)"
    )
    assert r["fragment_survived_gate"] is True
    assert r["fragment_refused"] is True, (
        "the refusal must be flagged so the card can explain it"
    )


def test_auth_page_gate_still_bounces_without_a_live_fragment(
    auth_page_report: dict,
) -> None:
    """Narrowness control: a signed-in visitor with NO fragment must still be
    forwarded to the /admin return-to, or the fix would strand every already
    authenticated visitor on the auth card."""
    r = _scenario(auth_page_report, "auth_page_no_fragment")
    assert r["navigated"] is True, "a live fragment is the ONLY thing that may block the bounce"
    assert r["replace_target"] == "/admin/", (
        f"the /admin return-to must be preserved, got {r['replace_target']!r}"
    )
    assert r["fragment_refused"] is False


def test_auth_page_without_a_session_keeps_the_fragment(auth_page_report: dict) -> None:
    """No cookie at all: the gate already returned early (nothing to forward),
    and the fragment must still be there afterwards — this is the pre-existing
    behaviour the guard must not regress."""
    r = _scenario(auth_page_report, "auth_page_no_session")
    assert r["navigated"] is False
    assert r["fragment_survived_gate"] is True


def test_auth_page_gate_predicate_matches_the_dashboard() -> None:
    """Static parity: the /auth gate has no bundler, so its predicate is a
    separate copy of src/sessionBounce.js's. Both must classify the same three
    tokens and both must guard BEFORE navigating."""
    html = SIGNUP.read_text(encoding="utf-8")
    helper = (REPO_ROOT / "website" / "apps" / "dashboard" / "src"
              / "sessionBounce.js").read_text(encoding="utf-8")
    live = re.search(r"LIVE_TOKEN_FRAGMENT = (/.*?/)", helper)
    assert live, "sessionBounce.js: LIVE_TOKEN_FRAGMENT not found"
    assert "window.liveFragmentCredential = function" in html, (
        "signup.html lost the shared live-credential predicate"
    )
    # The regex literal must be IDENTICAL to the dashboard's (a drift here is
    # how `refresh_token` fell out of the dashboard's head gate).
    assert live.group(1) in html, (
        f"signup.html's predicate does not use the dashboard's pattern "
        f"({live.group(1)}) — the two copies have drifted"
    )
    gate = html.index("window.liveFragmentCredential()")
    admin_bounce = html.index("window.location.replace(window.__ADMIN_RETURN_TO)")
    async_bounce = html.index("window.location.href = claimRedirectTarget()")
    assert gate < admin_bounce, (
        "the head gate must refuse BEFORE the __ADMIN_RETURN_TO navigation"
    )
    assert html.count("window.liveFragmentCredential()") >= 2, (
        "the async getSession bounce must apply the same guard — it would "
        "otherwise destroy the fragment the head gate just preserved"
    )
    assert html.index("window.liveFragmentCredential()", gate + 1) < async_bounce, (
        "the async bounce's guard must precede its navigation"
    )
    assert "__FRAGMENT_REFUSED" in html, (
        "nothing surfaces the refusal to the visitor on /auth"
    )


def test_cross_adapter_size_cap_parity() -> None:
    """P2-2/P3-3: the three adapter copies carry binding 'KEEP IN SYNC' comments.
    The hard-cap refusal was added to the bridge only, and the size guard's
    metadata stripping was missing from the consent-page adapter — an asymmetry
    that no test pinned, so a session the other two shrink to fit was REFUSED
    there. Assert BOTH the refusal/derivation AND the strip are present in all
    three (parity was previously asserted by comment-presence only)."""
    derivation = "SIZE_CAP = COOKIE_BYTE_LIMIT - COOKIE_NAME.length"
    # The strip's binding statements. `keep` is the pruned metadata object; the
    # names the dashboard reads must survive it (display_name/avatar_url).
    strip_markers = (
        "delete obj.provider_token",
        "delete obj.provider_refresh_token",
        r"delete obj\.user\.identities",
        r"obj\.user\.user_metadata = keep",
        r"keep\.display_name",
    )
    for label, path in (
        ("website/assets/supabase-session.js", SHARED),
        ("website/apps/dashboard/src/main.jsx", DASHBOARD),
        ("tortoise/oauth.py", OAUTH),
    ):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"COOKIE_BYTE_LIMIT\s*=\s*4096", text), (
            f"{label}: missing COOKIE_BYTE_LIMIT = 4096"
        )
        assert re.search(
            r"SIZE_CAP\s*=\s*COOKIE_BYTE_LIMIT\s*-\s*COOKIE_NAME\.length", text
        ) or derivation in text, (
            f"{label}: SIZE_CAP must be derived as '{derivation}' so the three "
            "copies cannot drift"
        )
        assert "refusing the write" in text, (
            f"{label}: setItem must refuse (and report) a write past the cap "
            "instead of letting the browser silently drop it"
        )
        for marker in strip_markers:
            assert re.search(marker, text), (
                f"{label}: the size-guard metadata strip is missing "
                f"({marker}) — this adapter refuses sessions the others shrink "
                "to fit (review P3-3)"
            )
