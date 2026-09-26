"""Regression gate for #3503 — a failed session write must not destroy the
only copy of the credential.

THE DEFECT
----------
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

THE SECOND CONSUMER (why the bridge-level gate was invisible in the product)
---------------------------------------------------------------------------
The bridge plugs its OWN ``supabaseStorage`` adapter into supabase-js and reads
the same hash a second time. supabase-js's ``_getSessionFromURL()`` is
redundant with the bridge, and it executes ``window.location.hash = ''``
BEFORE ``await _saveSession()`` — which hits the same cap and fails. The
fragment was therefore destroyed a second time, after the bridge had correctly
preserved it. The remedy is one fragment consumer per page:
``detectSessionInUrl: false`` wherever the bridge is present, ``true`` only on
the consent page, which loads no bridge and builds its own inline client.

``storeSession()`` must also prove THIS write. ``readValidSession()`` accepts a
still-valid PREVIOUS cookie — and falls back to the legacy localStorage keys —
so a REFUSED write used to look like success; the caller then stripped the
fragment, destroying the NEW credential while the user stayed signed in as the
OLD account (and the cross-subdomain cookie the dashboard reads was never
written). Only the cookie is now consulted, and its ``access_token`` AND
``refresh_token`` must equal the pair just written.

RESIDUAL SCOPE NOTE: the assertions below read the SOURCES, not bundles.
#3775 untracked ``website/apps/dashboard/dist/`` (it is a build artifact now),
and #4054 deleted the dashboard's session adapter, so the old note about a
committed ``dist/assets/index-*.js`` carrying the literal no longer applies —
there is no tracked bundle to drift and no client fragment consumer left in
``main.jsx``.

WHY A COOKIE-JAR SHIM
---------------------
The real browser behaviour that makes this reachable is "an over-limit
``Set-Cookie`` is dropped SILENTLY and any pre-existing value is kept". A
static string assertion cannot observe that. The harness below runs the real
``supabase-session.js`` under ``vm.runInNewContext`` with a cookie jar that
enforces Chrome's 4096-byte ``name=value`` limit, then reports what happened to
the fragment and to the cookie. The bridge's refused-write threshold is DERIVED
from the same rule (``COOKIE_LIMIT - len(COOKIE_NAME) - 1``) and pinned by
``test_refused_write_threshold_matches_the_browser_rule``, so code, harness and
browser cannot drift.

It is NOT a session-size claim: a real Google session on this path is far
smaller. The invariant under test is conditional — IF the write fails, the
fragment MUST survive.
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
BLOG_ADMIN = REPO_ROOT / "website" / "apps" / "blog-admin" / "src" / "lib" / "supabase.ts"
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"

# Chrome enforces a 4096-byte limit on the `name=value` pair. An over-limit
# write is dropped SILENTLY (no exception) and any pre-existing value is kept.
COOKIE_CAP = 4096
COOKIE_NAME = "sb-tortoise-auth-token"

# Node harness: loads the real bridge under a browser shim with a hard cookie
# cap, runs ONE scenario, prints a JSON report.
_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const scriptPath = process.argv[2];
const opts = JSON.parse(process.argv[3] || '{}');
const src = fs.readFileSync(scriptPath, 'utf8');

const COOKIE_CAP = 4096;                          // browser: name + '=' + value
const COOKIE_NAME = 'sb-tortoise-auth-token';

function makeDocument(jar) {
  const doc = {};
  Object.defineProperty(doc, 'cookie', {
    get: function () {
      const parts = [];
      jar.forEach(function (v, k) { parts.push(k + '=' + v); });
      return parts.join('; ');
    },
    set: function (raw) {
      const text = String(raw);
      const first = text.split(';')[0];
      const eq = first.indexOf('=');
      if (eq < 0) return;
      const name = first.slice(0, eq).trim();
      const value = first.slice(eq + 1);
      if (name.length + 1 + value.length > COOKIE_CAP) return; // silent drop
      const attrs = text.slice(first.length).toLowerCase();
      if (value === '' || attrs.indexOf('max-age=0') !== -1) { jar.delete(name); return; }
      jar.set(name, value);
    },
  });
  return doc;
}

const expiresAt = Math.floor(Date.now() / 1000) + 3600;

function makeSession(tokenLen, prefix, refreshLen) {
  return {
    access_token: (prefix || 'A').repeat(tokenLen),
    refresh_token: 'r'.repeat(refreshLen || 1),
    expires_at: expiresAt,
    expires_in: 3600,
    token_type: 'bearer',
  };
}

// 'A' is unreserved, so it encodes 1:1 — one correction step converges exactly.
function solveTokenLen(target) {
  let len = Math.max(1, target - 200);
  for (let i = 0; i < 4; i++) {
    const enc = encodeURIComponent(JSON.stringify(makeSession(len))).length;
    if (enc === target) break;
    len += target - enc;
    if (len < 1) return 1;
  }
  return len;
}

const tokenLen = opts.targetEncodedLen ? solveTokenLen(opts.targetEncodedLen) : (opts.tokenLen || 4000);
const session = makeSession(tokenLen, undefined, opts.newRefreshLen || 1);
const fragment = '#access_token=' + session.access_token +
  '&refresh_token=' + session.refresh_token +
  '&expires_at=' + session.expires_at +
  '&expires_in=3600&token_type=bearer';

const jar = new Map();
// Preseeds go through the SAME shim the bridge writes through, so a seed the
// browser would drop cannot set up an impossible test state.
function preseed(sessionObj) {
  const doc = makeDocument(jar);
  doc.cookie = COOKIE_NAME + '=' + encodeURIComponent(JSON.stringify(sessionObj)) + '; Path=/';
  return jar.has(COOKIE_NAME);
}
let preseededAccess = null;
let preseedFits = null;
if (opts.preseedValidCookie) {
  // A still-valid PREVIOUS session already sits in the cookie: a REFUSED write
  // must not be mistaken for success because this one is readable (#3503 P2).
  const old = makeSession(opts.preseedValidCookie, 'O');
  preseedFits = preseed(old);
  preseededAccess = old.access_token;
}

// The legacy localStorage keys readValidSession() falls back to. Seeding one
// with the SAME session as the fragment must NOT let a refused cookie write
// look successful — the cross-subdomain cookie is the artifact that matters.
const LEGACY_KEY = 'sb-ybetwichurajbfswfeqa-auth-token';
const localStore = new Map();
if (opts.preseedLegacyWithSameSession) {
  localStore.set(LEGACY_KEY, JSON.stringify(session));
}

// A prior cookie carrying the SAME access_token but a stale refresh_token:
// a refused write is still NOT this write. tokenLen is chosen small enough
// that this seed FITS; preseed() enforces the cap, so preseedFits proves it.
if (opts.preseedSameAccessToken) {
  const stale = makeSession(tokenLen, undefined, 3);
  stale.refresh_token = 'OLD';
  preseedFits = preseed(stale);
  preseededAccess = stale.access_token;
}

const logs = [];
const historyCalls = [];
const sandbox = {
  console: {
    log: function () { logs.push({ level: 'log', text: Array.prototype.join.call(arguments, ' ') }); },
    warn: function () { logs.push({ level: 'warn', text: Array.prototype.join.call(arguments, ' ') }); },
    error: function () { logs.push({ level: 'error', text: Array.prototype.join.call(arguments, ' ') }); },
  },
  URLSearchParams: URLSearchParams,
  URL: URL,
  document: makeDocument(jar),
  localStorage: {
    _d: localStore,
    getItem: function (k) { return this._d.has(k) ? this._d.get(k) : null; },
    setItem: function (k, v) { this._d.set(k, String(v)); },
    removeItem: function (k) { this._d.delete(k); },
  },
  location: {
    hostname: 'tortoise.premiselabs.co',
    origin: 'https://tortoise.premiselabs.co',
    pathname: '/auth',
    search: '?next=%2Fadmin%2F',
    hash: fragment,
    replace: function () {},
  },
  history: {
    replaceState: function (state, title, url) { historyCalls.push(url); },
  },
};
sandbox.window = sandbox;

vm.runInContext(src, vm.createContext(sandbox), { filename: scriptPath });

// Observe what the exposed helper itself reports for the same session shape.
const storeReturns = sandbox.window.storeSession(session);
const bridge = sandbox.window.__tortoiseSessionBridge;
const raw = jar.get(COOKIE_NAME);
let cookieAccess = null;
try { cookieAccess = raw ? JSON.parse(decodeURIComponent(raw)).access_token : null; } catch (e) {}

process.stdout.write(JSON.stringify({
  encoded_len: encodeURIComponent(JSON.stringify(session)).length,
  size_cap: bridge.SIZE_CAP,
  cookie_limit: bridge.COOKIE_LIMIT,
  cookie_name_len: COOKIE_NAME.length,
  fragment_after: historyCalls.length ? '' : fragment,
  replace_state_calls: historyCalls.length,
  cookie_present: jar.has(COOKIE_NAME),
  preseed_fits_the_cap: preseedFits,
  cookie_is_preseeded: preseededAccess !== null && cookieAccess === preseededAccess,
  cookie_is_new: cookieAccess !== null && cookieAccess === session.access_token,
  legacy_has_same_session: localStore.get(LEGACY_KEY) === JSON.stringify(session),
  cookie_refresh_token: (function () {
    try { return raw ? JSON.parse(decodeURIComponent(raw)).refresh_token : null; } catch (e) { return null; }
  })(),
  store_returns: storeReturns,
  logs: logs,
}));
"""


def _require_node() -> None:
    """Fail — never skip — when node is absent.

    A skipped harness is a no-op gate: the session-bridge harnesses are wired
    into the ``onboarding``/``api`` CI surfaces (this file, and
    ``test_cross_subdomain_cookie_sync.py``'s ``node --check`` since #3786), and
    a harness must not depend on whatever the runner image happens to ship. The
    contract here is deliberately FAIL-ALWAYS (stricter than the CI-only gate
    ``tests/test_pi_capture_hooks.py`` uses): it predates #4620 and is unchanged
    by it, even though that PR now provisions Node 22 in the ``python-ci``
    ``test`` job they run in. Mirrors
    tests/e2e/auth/bff_test_helpers.py::require_toolchain; set
    SESSION_BRIDGE_ALLOW_NO_TOOLCHAIN=1 only for a deliberate toolchain-free
    subset (an explicit skip, never a green no-op).
    """
    if shutil.which("node"):
        return
    if os.environ.get("SESSION_BRIDGE_ALLOW_NO_TOOLCHAIN") == "1":
        pytest.skip("node absent — SESSION_BRIDGE_ALLOW_NO_TOOLCHAIN=1")
    pytest.fail(
        "missing required toolchain: node — the session-bridge fragment "
        "harness cannot run and MUST NOT silently pass. Install Node (v20+) "
        "or set SESSION_BRIDGE_ALLOW_NO_TOOLCHAIN=1 to opt out explicitly."
    )


def _run(**opts: object) -> dict:
    _require_node()
    assert SHARED.exists(), f"missing file: {SHARED}"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(_HARNESS)
        harness = f.name
    try:
        proc = subprocess.run(
            ["node", harness, str(SHARED), json.dumps(opts)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        os.unlink(harness)
    assert proc.returncode == 0, (
        f"node harness failed (exit {proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
    )
    return json.loads(proc.stdout)


def _errors(r: dict) -> list[str]:
    return [log["text"] for log in r["logs"] if log["level"] == "error"]


def test_oversized_session_keeps_the_fragment_and_surfaces_failure() -> None:
    """The credential must survive a failed write (#3503). When the cookie is
    rejected, the fragment is the ONLY remaining copy — it must not be
    stripped, and the bridge must not pretend the write succeeded."""
    r = _run(tokenLen=4000)
    assert "#access_token=" in r["fragment_after"], (
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
    assert r["store_returns"] is False, (
        "storeSession() must report failure for a session the cookie jar rejected"
    )
    assert _errors(r), (
        "a refused write must surface a real failure (console.error), not only "
        "a console.warn that fires below the actual cap (#3503)"
    )


def test_refused_write_is_not_masked_by_a_valid_previous_session() -> None:
    """#3503 P2: ``storeSession`` returned ``readValidSession() !== null``, which
    is true for a still-valid PREVIOUS cookie — so a REFUSED write looked like
    success. The gate then stripped the fragment, destroying the NEW credential
    while the user stayed authenticated as the OLD account. The return value
    must reflect THIS write, and the prior cookie must survive untouched."""
    r = _run(tokenLen=4000, preseedValidCookie=200)
    assert r["cookie_is_preseeded"] is True, (
        "harness precondition: the pre-existing valid cookie must be kept when "
        "the new write is refused"
    )
    assert r["cookie_is_new"] is False, "the refused write must not have landed"
    assert r["store_returns"] is False, (
        "storeSession() reported success for a REFUSED write because a valid "
        "previous session was still readable — the gate then strips the "
        "fragment and the new credential is destroyed while the user remains "
        "signed in as the old account (#3503 P2)"
    )
    assert r["replace_state_calls"] == 0, (
        "the fragment must survive a refused write even when an older valid "
        "session is present (#3503 P2)"
    )

    # A prior cookie with the SAME access_token but a STALE refresh_token is a
    # refused write too — the access_token alone is not the whole identity.
    # tokenLen 3300 / newRefreshLen 800: the seed fits (preseed_fits_the_cap)
    # while the new write is over the cap.
    same = _run(tokenLen=3300, newRefreshLen=800, preseedSameAccessToken=True)
    assert same["preseed_fits_the_cap"] is True, (
        "harness precondition: the prior cookie must fit the browser cap"
    )
    assert same["cookie_refresh_token"] == "OLD", (
        "harness precondition: the prior cookie must keep its stale refresh_token"
    )
    assert same["store_returns"] is False, (
        "storeSession() reported success for a REFUSED write because the prior "
        "cookie shared the access_token but carried a stale refresh_token "
        "(#3503 P2)"
    )
    assert same["replace_state_calls"] == 0, (
        "a stale refresh_token in the prior cookie must not strip the fragment"
    )


def test_refused_write_is_not_masked_by_a_legacy_local_storage_session() -> None:
    """#3503 P2 (review fix): ``readValidSession()`` also falls back to the
    legacy localStorage keys, so comparing against it let a REFUSED cookie write
    look successful whenever a legacy key held the same session. The credential
    that the dashboard reads is the COOKIE, not the legacy key — a refused write
    is a failure even when localStorage still has a copy."""
    r = _run(tokenLen=4000, preseedLegacyWithSameSession=True)
    assert r["legacy_has_same_session"] is True, (
        "harness precondition: the legacy key must hold the same session"
    )
    assert r["cookie_is_new"] is False, (
        "harness precondition: the over-cap cookie write must have been refused"
    )
    assert r["store_returns"] is False, (
        "storeSession() reported success for a REFUSED cookie write because a "
        "legacy localStorage key held the same session — the caller then "
        "strips the fragment and the cross-subdomain cookie is never written "
        "(#3503 P2)"
    )
    assert r["replace_state_calls"] == 0, (
        "the fragment must survive: the shared cookie was NOT written (#3503 P2)"
    )


def test_refused_write_threshold_matches_the_browser_rule() -> None:
    """#3503 P3: the threshold was the literal 4077 — four bytes above what the
    browser rule allows (4096 on name + '=' + value), leaving a band where the
    code wrote, the browser dropped, and the advertised console.error never
    fired. Derive the cap from the rule and pin the boundary from both sides."""
    r = _run(tokenLen=128)
    # The cap must be DERIVED from the browser rule, not merely numerically
    # equal to it today — a hardcoded 4073 would satisfy the equality below and
    # re-introduce exactly the drift this finding is about.
    shared = SHARED.read_text(encoding="utf-8")
    assert re.search(r"SIZE_CAP\s*=\s*COOKIE_LIMIT\s*-\s*COOKIE_NAME\.length\s*-\s*1", shared), (
        "SIZE_CAP must be DERIVED from COOKIE_LIMIT and the cookie name length; "
        "a literal equal to today's value re-opens the #3503 P3 drift"
    )
    assert r["cookie_limit"] == COOKIE_CAP
    assert r["cookie_name_len"] + 1 + r["size_cap"] == COOKIE_CAP, (
        f"the refused-write threshold ({r['size_cap']}) is not derived from the "
        f"browser rule: len('{COOKIE_NAME}') + 1 + {r['size_cap']} != {COOKIE_CAP} "
        "— code and browser can drift again (#3503 P3)"
    )

    # AT the derived cap the write must be accepted, and the browser rule the
    # harness enforces must accept it too (code/harness/browser agree).
    at = _run(targetEncodedLen=r["size_cap"])
    assert at["encoded_len"] == r["size_cap"], "harness must land exactly on the cap"
    assert at["cookie_is_new"] is True, (
        "a session AT the derived cap must be written — the threshold is off by one"
    )
    assert not _errors(at), f"no refusal expected at the cap: {_errors(at)}"

    # ONE byte over must be refused WITH the advertised error, not written and
    # then silently dropped by the browser.
    over = _run(targetEncodedLen=r["size_cap"] + 1)
    assert over["replace_state_calls"] == 0
    assert over["cookie_is_new"] is False, (
        "the code wrote past the browser cap: the write is dropped silently and "
        "storeSession() cannot see it (#3503 P3)"
    )
    assert _errors(over), (
        "the refusal must surface console.error — silence is the bug (#3503 P3)"
    )


def test_small_session_writes_the_cookie_and_strips_the_fragment() -> None:
    """The success path is unchanged: a session that fits is stored and the
    fragment is then stripped so supabase-js does not re-process it."""
    r = _run(tokenLen=200)
    assert r["cookie_is_new"] is True, "a small session must be written to the cookie"
    assert r["store_returns"] is True, "storeSession() must report success"
    assert "#access_token=" not in r["fragment_after"], (
        "the fragment must be stripped once the credential is safely stored"
    )
    assert r["replace_state_calls"] == 1
    assert not _errors(r), f"no error expected on the success path: {_errors(r)}"


def test_one_fragment_consumer_per_page() -> None:
    """#3503 P1: supabase-js must not ingest the fragment on a page where the
    bridge already does. The bridge reads the same hash and writes the same
    storage, and supabase-js clears ``window.location.hash`` BEFORE awaiting
    ``_saveSession()`` — so it destroys the fragment a second time and the
    bridge-level retention never reaches the product. ``detectSessionInUrl``
    stays true only on the consent page, which loads no bridge.

    #4054 retarget: the dashboard's session adapter is deleted, so main.jsx no
    longer builds a supabase-js client at all — the old ``detectSessionInUrl:
    false`` assertion on it is replaced by the STRONGER property that it cannot
    be a fragment consumer (no client, no detectSessionInUrl). The old
    "every bridge page loads the bridge" loop is likewise inverted: no page
    loads the bridge any more, which is what makes the one-consumer property
    hold trivially. Nothing was dropped — each assertion is retargeted to the
    surface that still carries the invariant.
    """
    assert "detectSessionInUrl: false" in SHARED.read_text(encoding="utf-8"), (
        "the shared bridge's factory must not let supabase-js re-ingest the "
        "fragment its own IIFE already consumed (#3503 P1)"
    )
    # The dashboard is BFF-migrated: it builds NO supabase-js client and sets NO
    # detectSessionInUrl, so it cannot be a second fragment consumer. (The old
    # assertion required its client to set the flag false; there is no client
    # left to set it.)
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    assert "detectSessionInUrl" not in dashboard, (
        "main.jsx configures supabase-js auth again — it is BFF-migrated and "
        "must not become a second fragment consumer (#3503 P1, #4054)"
    )
    assert "createClient(" not in dashboard, (
        "main.jsx must not construct a supabase-js client (#4054)"
    )
    assert "detectSessionInUrl: false" in BLOG_ADMIN.read_text(encoding="utf-8"), (
        "blog-admin never receives the OAuth fragment (it reads the session "
        "cookie) — it must stay false so it does not become a second consumer "
        "(#3503 P1)"
    )

    oauth = OAUTH.read_text(encoding="utf-8")
    assert "detectSessionInUrl: true" in oauth, (
        "the consent page loads NO bridge and builds its own inline client — it "
        "is the sole fragment consumer there and must ingest the hash (#3503 P1)"
    )
    assert '<script src="/assets/supabase-session.js">' not in oauth, (
        "the consent page must not load the shared bridge; its inline client is "
        "the only consumer (#3503 P1)"
    )

    # #4054: the pages that used to load the bridge have all left it. The old
    # loop asserted they DID load it (so the P1 assertion was about a page where
    # the bridge ran); the retirement protocol inverts that into the absence
    # proof — an empty bridge-page set is the end state.
    for page in (
        REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "signup.html",
        REPO_ROOT / "website" / "apps" / "dashboard" / "index.html",
    ):
        assert 'src="/assets/supabase-session.js"' not in page.read_text(encoding="utf-8"), (
            f"{page.name}: must not load the shared bridge (#4054)"
        )
    # signin.html was the last legacy bridge page; it is deleted in #4054 (all
    # of /signin, /signin/, /signin.html 301 to /auth).
    assert not (REPO_ROOT / "website" / "signin.html").exists(), (
        "the retired signin.html is back — it was the last page on the legacy "
        "cross-subdomain bridge (#4054)"
    )
