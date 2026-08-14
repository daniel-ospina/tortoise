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
    return h === 'localhost' || h === '127.0.0.1';
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
})();
