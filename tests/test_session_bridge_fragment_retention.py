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

WHY A COOKIE-JAR SHIM
---------------------
The real browser behaviour that makes this reachable is "an over-limit
``Set-Cookie`` is dropped SILENTLY and any pre-existing value is kept". A
static string assertion cannot observe that. The harness below runs the real
``supabase-session.js`` under ``vm.runInNewContext`` with a cookie jar that
enforces Chrome's 4096-byte name=value limit, then reports what happened to
the fragment and to the cookie.

It is NOT a session-size claim: a real Google session on this path is far
smaller. The invariant under test is conditional — IF the write fails, the
fragment MUST survive.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED = REPO_ROOT / "website" / "assets" / "supabase-session.js"

# Node harness: loads the real bridge under a browser shim with a hard cookie
# cap, runs two scenarios (oversize / control), prints a JSON report.
_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const scriptPath = process.argv[2];
const src = fs.readFileSync(scriptPath, 'utf8');

// Chrome enforces a 4096-byte limit on the `name=value` pair. An over-limit
// write is dropped SILENTLY (no exception) and any pre-existing value is kept.
const COOKIE_CAP = 4096;
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

function run(tokenLen) {
  const jar = new Map();
  const logs = [];
  const historyCalls = [];
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  const fragment = '#access_token=' + 'A'.repeat(tokenLen) +
    '&refresh_token=r&expires_at=' + expiresAt +
    '&expires_in=3600&token_type=bearer';

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
      _d: new Map(),
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
  const session = {
    access_token: 'A'.repeat(tokenLen),
    refresh_token: 'r',
    expires_at: expiresAt,
    expires_in: 3600,
    token_type: 'bearer',
  };
  const storeReturns = sandbox.window.storeSession(session);

  return {
    fragment_after: historyCalls.length ? '' : fragment,
    replace_state_calls: historyCalls.length,
    cookie_present: jar.has(COOKIE_NAME),
    cookie_len: jar.has(COOKIE_NAME) ? jar.get(COOKIE_NAME).length : 0,
    store_returns: storeReturns,
    logs: logs,
  };
}

process.stdout.write(JSON.stringify({ oversized: run(4000), small: run(200) }));
"""

_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(
    not _HAS_NODE, reason="node not available — session-bridge harness skipped"
)


def _run(case: str) -> dict:
    assert SHARED.exists(), f"missing file: {SHARED}"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(_HARNESS)
        harness = f.name
    try:
        proc = subprocess.run(
            ["node", harness, str(SHARED)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        os.unlink(harness)
    assert proc.returncode == 0, (
        f"node harness failed (exit {proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
    )
    return json.loads(proc.stdout)[case]


def test_oversized_session_keeps_the_fragment_and_surfaces_failure() -> None:
    """The credential must survive a failed write (#3503). When the cookie is
    rejected, the fragment is the ONLY remaining copy — it must not be
    stripped, and the bridge must not pretend the write succeeded."""
    r = _run("oversized")
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
    assert any(log["level"] == "error" for log in r["logs"]), (
        "a refused write must surface a real failure (console.error), not only "
        "a console.warn that fires below the actual cap (#3503)"
    )


def test_small_session_writes_the_cookie_and_strips_the_fragment() -> None:
    """The success path is unchanged: a session that fits is stored and the
    fragment is then stripped so supabase-js does not re-process it."""
    r = _run("small")
    assert r["cookie_present"] is True, "a small session must be written to the cookie"
    assert r["store_returns"] is True, "storeSession() must report success"
    assert "#access_token=" not in r["fragment_after"], (
        "the fragment must be stripped once the credential is safely stored"
    )
    assert r["replace_state_calls"] == 1
    assert not [log for log in r["logs"] if log["level"] == "error"], (
        f"no error expected on the success path: {r['logs']}"
    )
