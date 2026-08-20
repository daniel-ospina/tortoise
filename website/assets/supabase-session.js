// ── Cross-subdomain session bridge (#1225) ──────────────────────────────────
// Sessions created on tortoise.premiselabs.co (signup/signin/welcome) must
// land in the `.premiselabs.co` parent-domain cookie that the dashboard
// (app.premiselabs.co) already reads. supabase-js v2 defaults to origin-scoped
// localStorage, so a plain createClient() never reaches the dashboard — after
// the welcome redirect the dashboard boots with an empty session and shows the
// login wall (#969 capstone finding 2).
//
// This file mirrors the dashboard's #572 storage adapter
// (website/apps/dashboard/src/main.jsx, COOKIE_NAME/COOKIE_DOMAIN) so both
// subdomains share one session cookie. KEEP THE TWO IN SYNC — the static test
// tests/test_cross_subdomain_cookie_sync.py asserts it.
//
// Design notes:
// - FlowType stays 'implicit' (today's live behavior — the OAuth callback
//   arrives as a URL fragment and welcome.html parses it). The stored session
//   JSON shape is flowType-agnostic, so the dashboard's pkce-configured client
//   reads the same cookie with getSession().
// - Cookie expiry: 7 days, matching the dashboard's #572 policy (accepted
//   parity trade-off vs localStorage's indefinite persistence — a user absent
//   >7 days re-signs in once).
// - Domain/Secure are omitted on localhost / non-premiselabs hosts (RFC 6265
//   rejects a Domain attribute that doesn't match the request host).
// - Legacy migration: pre-#1225 sessions live in tortoise-origin localStorage
//   under the supabase-js DEFAULT key ('sb-' + <supabase-url-host[0]> +
//   '-auth-token'). Copy that into the cookie on first load so existing
//   sessions and the E2E mocked tests (which seed those keys) keep working.
(function () {
  'use strict';

  var COOKIE_NAME = 'sb-tortoise-auth-token';
  var COOKIE_DOMAIN = '.premiselabs.co';
  var COOKIE_PATH = '/';
  var EXPIRY_MS = 7 * 24 * 3600 * 1000; // 7 days — #572 parity
  var SIZE_GUARD = 3800; // encoded bytes; cookie limit is 4096

  var isLocal = function () {
    var h = window.location.hostname;
    // localhost + loopback IPs (v4/v6) + RFC1918 private ranges — no
    // Domain/Secure attributes on non-public origins (review P3-5).
    if (h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '[::1]') return true;
    if (h.startsWith('10.') || h.startsWith('192.168.')) return true;
    return /^172\.(1[6-9]|2\d|3[01])\./.test(h);
  };

  // Domain attribute only on premiselabs.co hosts — host-only cookie elsewhere
  // (localhost, *.pages.dev previews) so those origins keep working.
  var isPremiselabsHost = function () {
    var h = window.location.hostname;
    return h === 'premiselabs.co' || h.endsWith('.premiselabs.co');
  };

  var domainAttr = function () {
    return isPremiselabsHost() && !isLocal() ? '; Domain=' + COOKIE_DOMAIN : '';
  };
  var secureAttr = function () {
    return isLocal() ? '' : '; Secure';
  };

  var readCookie = function (key) {
    try {
      var m = document.cookie.match(
        new RegExp('(?:^|; )' + key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]*)')
      );
      return m ? decodeURIComponent(m[1]) : null;
    } catch (e) {
      return null;
    }
  };

  // ── Parent-domain cookie storage (supabase-js Storage interface) ──────────
  var supabaseStorage = {
    getItem: function (key) {
      return readCookie(key);
    },
    setItem: function (key, value) {
      if (!value) { this.removeItem(key); return; }
      var encoded = encodeURIComponent(value);
      // Size guard (#1225 review): a GitHub OAuth session (user_metadata +
      // identities + provider_token) can exceed the 4096-byte cookie limit.
      // provider tokens are only needed by the initiating flow — strip them
      // first; if still over the cap, attempt the write anyway with a warning.
      if (encoded.length > SIZE_GUARD) {
        try {
          var obj = JSON.parse(value);
          delete obj.provider_token;
          delete obj.provider_refresh_token;
          encoded = encodeURIComponent(JSON.stringify(obj));
        } catch (e) { /* not JSON — leave as-is */ }
        if (encoded.length > SIZE_GUARD + 100) {
          console.warn('sb-tortoise-auth-token session exceeds cookie size cap (' + encoded.length + ' bytes) — session may not bridge subdomains');
        }
      }
      var expires = new Date(Date.now() + EXPIRY_MS).toUTCString();
      document.cookie = key + '=' + encoded + domainAttr() + '; Path=' + COOKIE_PATH +
        '; SameSite=Lax' + secureAttr() + '; Expires=' + expires;
    },
    removeItem: function (key) {
      document.cookie = key + '=;' + domainAttr() + '; Path=' + COOKIE_PATH +
        '; SameSite=Lax' + secureAttr() + '; Max-Age=0';
    },
  };

  // ── Legacy localStorage migration (#1225) ─────────────────────────────────
  // supabase-js derives its DEFAULT storage key from the Supabase URL hostname:
  //   'sb-' + new URL(supabaseUrl).hostname.split('.')[0] + '-auth-token'
  // → prod: sb-ybetwichurajbfswfeqa-auth-token; local CLI: sb-127-auth-token
  // (exactly the keys tests/e2e/test_welcome_page.py _seed_local_session seeds).
  // Runs synchronously BEFORE createClient — supabase-js reads storage inside
  // its async initialize(), and a post-createClient migration can race it.
  // Never throws: storage may be blocked (SecurityError) or the value corrupt.
  var migrateLegacySession = function (supabaseUrl) {
    try {
      var legacyKey = 'sb-' + new URL(supabaseUrl).hostname.split('.')[0] + '-auth-token';
      var legacy = null;
      try { legacy = window.localStorage.getItem(legacyKey); } catch (e) { return; }
      if (!legacy) return;
      var alreadyShared = readCookie(COOKIE_NAME);
      if (!alreadyShared) {
        // Copy + confirm write before clearing (never destroy the only copy).
        // readCookie returns the DECODED value; equality holds unless the size
        // guard stripped provider tokens — in that case keep the legacy copy.
        supabaseStorage.setItem(COOKIE_NAME, legacy);
        if (readCookie(COOKIE_NAME) !== legacy) return;
      } else {
        // Both cookie and legacy exist (review P3-3): a stale cached tab may
        // hold a NEWER legacy session than the cookie — compare expires_at and
        // keep the newer one before clearing the legacy key.
        try {
          var legacyExp = JSON.parse(legacy).expires_at || 0;
          var cookieExp = JSON.parse(alreadyShared).expires_at || 0;
          if (legacyExp > cookieExp) {
            supabaseStorage.setItem(COOKIE_NAME, legacy);
          }
        } catch (e) { /* malformed JSON — keep cookie, drop legacy below */ }
      }
      // Stale-secret hygiene: the new client never reads the legacy key; drop
      // it whether or not a cookie was already present.
      try { window.localStorage.removeItem(legacyKey); } catch (e) { /* ignore */ }
    } catch (e) { /* best-effort — never break the page */ }
  };

  // ── Client factory ────────────────────────────────────────────────────────
  // Throw-safe (review V2-P2-1): returns null when the CDN script is blocked
  // or init fails — callers mirror the #527 fail-closed degradation.
  window.createTortoiseSupabaseClient = function (supabaseUrl, supabaseAnonKey) {
    try {
      migrateLegacySession(supabaseUrl);
      if (typeof window.supabase === 'undefined' || !window.supabase.createClient) {
        console.warn('supabase CDN script did not load — auth disabled');
        return null;
      }
      return window.supabase.createClient(supabaseUrl, supabaseAnonKey, {
        auth: {
          flowType: 'implicit', // matches live prod fragment callbacks (#1225)
          storage: supabaseStorage,
          storageKey: COOKIE_NAME, // cookie name = storage key (dashboard parity)
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true,
        },
      });
    } catch (err) {
      console.error('supabase client init failed:', err);
      return null;
    }
  };

  // Exposed for the static sync test.
  window.__tortoiseSessionBridge = {
    COOKIE_NAME: COOKIE_NAME,
    COOKIE_DOMAIN: COOKIE_DOMAIN,
    EXPIRY_MS: EXPIRY_MS,
  };

  // ── #1511 shared auth-gate helpers ────────────────────────────────────────
  // ONE validity predicate + clear + last-used + bounce, used by the /auth,
  // /welcome and dashboard head gates (and their async checks) — loop-safety
  // by construction: a session that fails readValidSession is cleared before
  // any bounce, so a gate can never re-bounce it. Null-safe / never throws.

  var LEGACY_KEYS = [
    'sb-ybetwichurajbfswfeqa-auth-token', // prod supabase host
    'sb-127-auth-token',                  // local CLI host (e2e seeds both)
  ];

  var readValidSession = function () {
    try {
      var raw = readCookie(COOKIE_NAME);
      if (!raw) {
        for (var i = 0; i < LEGACY_KEYS.length && !raw; i++) {
          try { raw = window.localStorage.getItem(LEGACY_KEYS[i]); } catch (e) {}
        }
      }
      if (!raw) return null;
      var s = JSON.parse(raw);
      if (!s || !s.access_token) return null;
      // Strict validity: missing or past expires_at = INVALID (presence is
      // not auth — the stale-session leak class).
      if (!s.expires_at || s.expires_at * 1000 <= Date.now()) return null;
      return s;
    } catch (e) { return null; }
  };

  var clearStoredSession = function () {
    try {
      document.cookie = COOKIE_NAME + '=;' + domainAttr() + '; Path=' + COOKIE_PATH +
        '; SameSite=Lax' + secureAttr() + '; Max-Age=0';
    } catch (e) {}
    for (var i = 0; i < LEGACY_KEYS.length; i++) {
      try { window.localStorage.removeItem(LEGACY_KEYS[i]); } catch (e) {}
    }
  };

  var LAST_AUTH_COOKIE = 'tt_last_auth_method';
  var LEGACY_LAST_AUTH = 'tortoise_last_auth_method'; // dashboard app-origin
  var lastAuthMigrated = false;

  var getLastAuthMethod = function () {
    try {
      if (!lastAuthMigrated) {
        // One-time migration from the dashboard's legacy app-origin key.
        try {
          var legacy = window.localStorage.getItem(LEGACY_LAST_AUTH);
          if (legacy && !readCookie(LAST_AUTH_COOKIE)) {
            setLastAuthMethod(legacy);
          }
        } catch (e) {}
        lastAuthMigrated = true;
      }
      return readCookie(LAST_AUTH_COOKIE) || '';
    } catch (e) { return ''; }
  };

  var setLastAuthMethod = function (method) {
    try {
      if (!method) return;
      var expires = new Date(Date.now() + 90 * 24 * 3600 * 1000).toUTCString();
      document.cookie = LAST_AUTH_COOKIE + '=' + encodeURIComponent(method) +
        domainAttr() + '; Path=' + COOKIE_PATH + '; SameSite=Lax' + secureAttr() +
        '; Expires=' + expires;
    } catch (e) { /* best-effort */ }
  };

  var bounceToAuth = function (search, hash) {
    try {
      var target;
      if (window.location.origin === 'https://app.premiselabs.co') {
        target = (window.__AUTH_BASE_URL || 'https://tortoise.premiselabs.co') + '/auth';
      } else {
        target = '/auth';
      }
      window.location.replace(target + (search || '') + (hash || ''));
    } catch (e) { /* best-effort */ }
  };

  // #1511: store a session directly into the parent-domain cookie (the
  // supabase-js session shape: access_token/refresh_token/expires_at/…).
  // The dashboard/welcome clients read it via getSession() (storage read,
  // shape-validated — no network). We do NOT use auth.setSession here: it
  // performs a network round trip (refresh if expired/unparseable, else
  // GoTrue /user) which is neither instant nor mockable in the exchange flow.
  var storeSession = function (session) {
    if (!session || !session.access_token || !session.refresh_token) return false;
    try {
      supabaseStorage.setItem(COOKIE_NAME, JSON.stringify(session));
      return readValidSession() !== null;
    } catch (e) { return false; }
  };

  window.__tortoiseSessionBridge.readValidSession = readValidSession;
  window.__tortoiseSessionBridge.clearStoredSession = clearStoredSession;
  window.__tortoiseSessionBridge.getLastAuthMethod = getLastAuthMethod;
  window.__tortoiseSessionBridge.setLastAuthMethod = setLastAuthMethod;
  window.__tortoiseSessionBridge.bounceToAuth = bounceToAuth;
  window.__tortoiseSessionBridge.storeSession = storeSession;
  // The head gates are synchronous inline scripts — expose the helpers
  // directly so a gate can call window.readValidSession() without awaiting
  // the bridge object (typeof guards in the gates cover a blocked script).
  window.readValidSession = readValidSession;
  window.clearStoredSession = clearStoredSession;
  window.getLastAuthMethod = getLastAuthMethod;
  window.setLastAuthMethod = setLastAuthMethod;
  window.bounceToAuth = bounceToAuth;
  window.storeSession = storeSession;
})();
