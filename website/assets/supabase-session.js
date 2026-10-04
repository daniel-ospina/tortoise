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
  var SIZE_GUARD = 3800; // encoded bytes; strip provider tokens above this
  // Chrome's per-cookie limit is 4096 bytes on `name=value`. (RFC 6265's 4KB
  // minimum spans name + value + ATTRIBUTES; engines enforce the smaller
  // name=value cap, which is the rule the regression harness models.) An
  // over-limit write is dropped SILENTLY — no exception, any previous value
  // kept. Refusing above the limit is the honest signal: storeSession() then
  // reports false and the caller keeps the fragment instead of destroying the
  // only copy of the credential (#3503).
  // DERIVE the limit from the rule; do not hardcode the byte count. A literal
  // (4077) disagreed with this rule by 4 bytes, leaving an untested band where
  // the code wrote, the browser dropped, and the advertised error never fired.
  var COOKIE_LIMIT = 4096; // bytes of `name` + '=' + `value`
  var SIZE_CAP = COOKIE_LIMIT - COOKIE_NAME.length - 1; // largest value we may write

  // #3485 review (cycle 5, P1): the shape THIS bridge accepts must be the shape the
  // destination CONSUMER accepts. supabase-js's own _isValidSession additionally
  // requires a `refresh_token` KEY, and its load path _removeSession()s the stored
  // session when that key is missing. The dashboard mounts supabase-js
  // (website/apps/dashboard/src/main.jsx getSession()), so a value the gate accepts
  // but the consumer DELETES is a live redirect loop: the head gate passes, the
  // mount gate wipes the cookie, the app bounces to /auth, and the migration
  // re-creates the same cookie from the legacy key — kept precisely because the
  // unconfirmable write never dropped it. Requiring the key here makes that state
  // unreachable. (storeSession() already required it, which is why the two agree.)
  var isConsumableSession = function (v) {
    return !!v && typeof v === 'object' &&
      typeof v.access_token === 'string' && v.access_token.length > 0 &&
      typeof v.refresh_token === 'string' && v.refresh_token.length > 0;
  };

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
          // Strip large metadata bloat — identities array and user_metadata fields
          // are not needed for auth and can exceed the cookie size cap.
          if (obj.user) {
            delete obj.user.identities;
            if (obj.user.user_metadata) {
              // Keep only what the dashboard reads (display_name, avatar_url)
              var keep = {};
              if (obj.user.user_metadata.display_name) keep.display_name = obj.user.user_metadata.display_name;
              if (obj.user.user_metadata.avatar_url) keep.avatar_url = obj.user.user_metadata.avatar_url;
              if (obj.user.user_metadata.full_name) keep.full_name = obj.user.user_metadata.full_name;
              if (obj.user.user_metadata.name) keep.name = obj.user.user_metadata.name;
              obj.user.user_metadata = keep;
            }
            if (obj.user.app_metadata) {
              // app_metadata is small (provider, providers array) — keep it
            }
          }
          encoded = encodeURIComponent(JSON.stringify(obj));
        } catch (e) { /* not JSON — leave as-is */ }
        if (encoded.length > SIZE_GUARD + 100) {
          console.warn('sb-tortoise-auth-token session exceeds cookie size cap (' + encoded.length + ' bytes) — session may not bridge subdomains');
        }
        if (encoded.length > SIZE_CAP) {
          // Do NOT write past the browser's limit: the write is a silent no-op
          // there, so the caller would believe the session was stored. Refuse
          // and report — storeSession()'s read-back then returns false (#3503).
          console.error('sb-tortoise-auth-token session exceeds the browser cookie cap (' + encoded.length + ' bytes encoded) — refusing the write; the session was NOT stored');
          return;
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

  // #3951: confirm the cookie ACTUALLY carries this session after a write. The
  // size guard may TRANSFORM the value (strip provider tokens / identities /
  // metadata bloat) while still writing it, so byte-equality against the legacy
  // string asks the wrong question: it is false for every large session, and the
  // old early return read that as "nothing was stored". What matters is that the
  // cookie now durably holds a CONSUMABLE session carrying THIS session's
  // credential — the two fields the size guard preserves. The changed-cookie
  // check in `writeLegacyToCookie` is the decisive guard that a dropped write is
  // not a landing; this identity comparison is the corroborating half — it
  // rejects a changed value that is not ours (a concurrent writer), a case the
  // single-threaded test harness cannot stage, so no test exercises it directly.
  var writeLanded = function (legacySession) {
    try {
      var raw = readCookie(COOKIE_NAME);
      if (!raw) return false;
      var stored = JSON.parse(raw);
      return isConsumableSession(stored) &&
        stored.access_token === legacySession.access_token &&
        stored.refresh_token === legacySession.refresh_token;
    } catch (e) { return false; }
  };

  // #3951: a legacy copy is retained ONLY when its write was refused — the
  // session then has nowhere else to live, and destroying it would strand the
  // visitor (#3503). The stale provider tokens are the secret the hygiene step
  // exists to remove and no post-migration path reads them, so they are stripped
  // from the retained copy in place; the access/refresh credential survives, so
  // the state is recoverable rather than stranded. The raw string is left
  // byte-identical when there is nothing to strip.
  var scrubLegacySecrets = function (legacyKey, raw) {
    try {
      // Compare-and-set (#3951 review): localStorage is shared across tabs, and
      // this migration already assumes a stale cached tab can write a NEWER
      // legacy session (that is why the "cookie present + newer legacy" branch
      // exists). Act only on the value actually inspected — writing the scrubbed
      // SNAPSHOT back over a concurrent tab's newer session would destroy the
      // only copy of that credential (#3503). A value we did not read is left
      // alone; it is scrubbed by its own migration attempt.
      if (window.localStorage.getItem(legacyKey) !== raw) return;
      var obj = JSON.parse(raw);
      if (!obj || typeof obj !== 'object') return;
      var changed = false;
      if (Object.prototype.hasOwnProperty.call(obj, 'provider_token')) {
        delete obj.provider_token;
        changed = true;
      }
      if (Object.prototype.hasOwnProperty.call(obj, 'provider_refresh_token')) {
        delete obj.provider_refresh_token;
        changed = true;
      }
      if (!changed) return;
      window.localStorage.setItem(legacyKey, JSON.stringify(obj));
    } catch (e) { /* ignore */ }
  };

  // #3951: perform the legacy→cookie migration write and report whether the
  // cookie now durably holds THIS session. The write counts as landed only when
  // the cookie CHANGED — a browser enforcing a smaller per-cookie cap than the
  // code's derived limit drops an issued write and leaves the PRE-EXISTING value
  // in place, and that value can share this session's tokens while being
  // expired, so treating it as a landing would destroy the only gate-usable copy
  // (#3503) — and `writeLanded` confirms the changed value carries this
  // session's credential rather than a concurrent writer's. Every non-landed
  // outcome keeps the legacy key (it is then the only copy of the credential)
  // and scrubs its stale provider tokens — the scrub is ATTEMPTED on every
  // non-landed outcome, so a provider token is not left readable by this path (a
  // localStorage rewrite that itself fails — quota, private-mode storage limits
  // — cannot be helped, and deleting the sole credential instead is exactly what
  // #3503 forbids).
  var writeLegacyToCookie = function (legacyKey, legacy, legacySession) {
    var before = readCookie(COOKIE_NAME);
    try {
      supabaseStorage.setItem(COOKIE_NAME, legacy);
    } catch (e) {
      /* the write threw — nothing was stored; the unchanged cookie below decides */
    }
    if (readCookie(COOKIE_NAME) !== before && writeLanded(legacySession)) return true;
    scrubLegacySecrets(legacyKey, legacy);
    return false;
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
      // #3485 review: the shared cookie may only ever receive a real session,
      // and every write is confirmed by writeLegacyToCookie — the cookie CHANGED
      // and the new value carries this session's access_token/refresh_token —
      // before the legacy key is dropped. The same discipline governs
      // migrateLegacyKeysToCookie.
      var legacyOk = false, legacyExp = 0, cookieOk = false, cookieExp = 0;
      try {
        var lo = JSON.parse(legacy);
        legacyOk = isConsumableSession(lo);
        legacyExp = (lo && lo.expires_at) || 0;
      } catch (e) { /* not JSON — not a session */ }
      if (!legacyOk) {
        // Not a session and therefore never a credential — never share it with
        // the parent domain; drop the junk.
        try { window.localStorage.removeItem(legacyKey); } catch (e) { /* ignore */ }
        return;
      }
      // Host-only origins are handled correctly here too, for the same reason as
      // migrateLegacyKeysToCookie (#3485 review cycle 3): the cookie is written
      // and read on the SAME host, and the write is confirmed below before the
      // legacy key is dropped. Do not reinstate a host-only guard here — it
      // bounces preview users who are signed in and protects nothing.
      if (!alreadyShared) {
        // #3951: the write must LAND (see writeLegacyToCookie), not merely
        // round-trip byte-identically — the size guard makes the stored value a
        // different string for every large session. A lossy-but-landed write is
        // a landed write; the legacy copy is then the stale-secret copy and must
        // go. Only a REFUSED write leaves the legacy copy as the sole credential
        // (#3503), so it stays — with its stale provider tokens scrubbed.
        if (!writeLegacyToCookie(legacyKey, legacy, lo)) return;
      } else {
        // Both cookie and legacy exist (review P3-3): a stale cached tab may
        // hold a NEWER legacy session than the cookie — compare expires_at and
        // keep the newer one before clearing the legacy key.
        try {
          var co = JSON.parse(alreadyShared);
          cookieOk = isConsumableSession(co);
          cookieExp = (co && co.expires_at) || 0;
        } catch (e) { /* unusable cookie */ }
        // #3485 review P2 (cycles 3-4): an expired or absent-expiry legacy session
        // must not displace a USABLE cookie — that cookie is what the server gate
        // reads. When the cookie is unusable the legacy session is the only
        // candidate, so it is carried over regardless of freshness.
        if (!cookieOk || (legacyExp * 1000 > Date.now() && legacyExp > cookieExp)) {
          if (!writeLegacyToCookie(legacyKey, legacy, lo)) return;
        }
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
          flowType: 'implicit', // #1566: cross-origin OAuth (tortoise → app)
          // must share the flow — a pkce verifier is origin-scoped and cannot
          // cross subdomains, so the app cannot exchange a pkce code minted
          // on /auth. The #access_token fragment is consumed by this bridge's
          // load-time IIFE, not by supabase-js (see detectSessionInUrl below);
          // the raw tt_ key never leaves sessionStorage.
          storage: supabaseStorage,
          storageKey: COOKIE_NAME, // cookie name = storage key (dashboard parity)
          persistSession: true,
          autoRefreshToken: true,
          // ONE fragment consumer (#3503): the load-time IIFE above already
          // consumes #access_token and writes this same cookie. Letting
          // supabase-js ALSO ingest the fragment is fully redundant — it reads
          // the same hash and writes the same storage — and it clears
          // window.location.hash BEFORE awaiting _saveSession(), so an
          // over-cap session loses the fragment a SECOND time and the
          // bridge-level retention is invisible in the product.
          detectSessionInUrl: false,
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
    COOKIE_LIMIT: COOKIE_LIMIT,
    SIZE_CAP: SIZE_CAP,
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

  // #3485: SYNCHRONOUS legacy→cookie migration for the hardcoded LEGACY_KEYS.
  // The head gate (signup.html) calls readValidSession() BEFORE the body runs
  // createTortoiseSupabaseClient(), so migrateLegacySession() (which needs a
  // supabaseUrl to derive the key) has not run yet. A visitor whose only
  // session lives in origin-scoped localStorage therefore looked signed in to
  // THIS origin while app.premiselabs.co and the server /admin gate saw no
  // cookie — tortoise → app → tortoise, forever (#3485, 393 loads/8s).
  // LEGACY_KEYS are hardcoded, so the migration needs no supabaseUrl and can
  // run at gate time. Never throws: storage may be blocked or the value corrupt.
  var migrateLegacyKeysToCookie = function () {
    try {
      for (var i = 0; i < LEGACY_KEYS.length; i++) {
      var legacy = null;
      try { legacy = window.localStorage.getItem(LEGACY_KEYS[i]); } catch (e) { continue; }
      if (!legacy) continue;
      // Host-only origins are handled correctly here, and this was checked
      // (#3485 review cycle 3): on a host that is not premiselabs.co the cookie is
      // written AND read on the SAME host — `website/functions/` deploys with the
      // `premise-labs` Pages project, so a *.pages.dev preview runs the same gate
      // and reads this very cookie — and the write below is CONFIRMED by
      // writeLegacyToCookie (the cookie changed and it carries this session's
      // tokens), so a host-only cookie is a faithful copy rather than a lost one.
      // Declining to migrate here would instead bounce a
      // preview user who had been signed in and working, while leaving behind a
      // localStorage key that no current path reads.
      var existing = readCookie(COOKIE_NAME);
      // #3485 review P3: parse the legacy value ONCE and require a real session
      // shape (a non-empty access_token AND refresh_token — isConsumableSession)
      // in BOTH branches. Object-ness alone is
      // not a session: a {"expires_at":N} blob written to the shared cookie
      // would outrank — and cause the deletion of — a valid session under the
      // second legacy key.
      var legacyExp = 0, legacyOk = false;
      try {
        var lo = JSON.parse(legacy);
        legacyOk = isConsumableSession(lo);
        legacyExp = (lo && lo.expires_at) || 0;
      } catch (e) { /* not JSON — not a session */ }
      if (!existing) {
        if (!legacyOk) {
          // Not a session, so never a credential — never share it with the
          // parent domain; drop the junk.
          try { window.localStorage.removeItem(LEGACY_KEYS[i]); } catch (e) { /* ignore */ }
          continue;
        }
        // Copy + confirm the write BEFORE clearing the only copy. #3951: the
        // confirmation is whether the write LANDED (see writeLegacyToCookie),
        // not byte-equality — the size guard makes the stored value a different
        // string for every large session, and equality would then skip the drop
        // and leave the untransformed copy (with its provider tokens) behind. A
        // REFUSED write keeps the copy (the sole credential, #3503) but scrubs
        // its stale provider tokens.
        if (!writeLegacyToCookie(LEGACY_KEYS[i], legacy, lo)) continue;
      } else {
        // Both present (review P3-3): a stale cached tab may hold a NEWER
        // legacy session — compare expires_at and keep the newer one before
        // clearing the legacy key. #3485 review P2: the write is CONFIRMED by
        // writeLegacyToCookie before the legacy copy is dropped, and a legacy
        // value that is not a session never overwrites the cookie.
        var cookieExp = 0, cookieOk = false;
        try {
          var co = JSON.parse(existing);
          cookieOk = isConsumableSession(co);
          cookieExp = (co && co.expires_at) || 0;
        } catch (e) { /* unusable cookie */ }
        // The expiry test applies only where it would DISPLACE a usable cookie.
        // With an unusable cookie the legacy session is the only candidate and
        // must still be carried over — it may hold a valid `refresh_token`, which
        // supabase-js can exchange. Demanding freshness there deletes a
        // recoverable credential and forces a fresh sign-in (#3485 review,
        // cycle 4).
        if (legacyOk && (!cookieOk ||
            (legacyExp * 1000 > Date.now() && legacyExp > cookieExp))) {
          if (!writeLegacyToCookie(LEGACY_KEYS[i], legacy, lo)) continue;
        }
      }
      // The new client never reads the legacy key — drop it once it is shared.
      try { window.localStorage.removeItem(LEGACY_KEYS[i]); } catch (e) { /* ignore */ }
    }
    } catch (e) {
      // Never throws (#3485 review cycle 3): a corrupt legacy value — e.g. one
      // whose JSON parses but carries an unpaired surrogate, which makes
      // encodeURIComponent throw URIError — must not stop the caller
      // (readValidSession) from reading the cookie, or a visitor with a perfectly
      // valid parent-domain cookie would be reported as having no session.
    }
  };

  // #3485: trust ONLY the parent-domain cookie — the credential both
  // subdomains and the server can actually see. A localStorage-only session
  // is migrated synchronously first; if it still cannot be shared, return null
  // and let the visitor sign in again rather than report a session the
  // destination cannot see (that report IS the redirect loop).
  var readValidSession = function () {
    try {
      // #3485 review P2: ALWAYS migrate — migration is idempotent, and calling
      // it only when the cookie is ABSENT skipped the case where the cookie is
      // present but unparseable: readValidSession returned null, then the
      // body's migrateLegacySession() deleted the only valid copy from
      // localStorage. Migrating first repairs the cookie, closing that path.
      migrateLegacyKeysToCookie();
      var raw = readCookie(COOKIE_NAME);
      if (!raw) return null;
      var s = JSON.parse(raw);
      // #3485 review (cycle 5, P1): require the same shape the CONSUMER requires.
      // A cookie carrying access_token + unexpired expires_at but no refresh_token
      // is deleted by supabase-js on mount, which re-arms the app ⇄ /auth loop.
      if (!isConsumableSession(s)) return null;
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
    // The blog-admin SPA persists the session under the SAME name in
    // localStorage, and its client refreshes tokens from there — a copy the
    // server gate never saw. That SPA clears its own key on sign-out (its adapter
    // half is PR #4016); this line covers any caller on an origin where the key
    // does exist, and is a harmless no-op elsewhere — localStorage is
    // per-origin, and the dashboard SPA that calls this function runs on a
    // different origin from the console that writes that key (#3485 review,
    // cycle 4).
    try { window.localStorage.removeItem(COOKIE_NAME); } catch (e) {}
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
      if (window.__AUTH_BASE_URL) {
        // Test seam (localhost e2e) — forces the absolute target.
        target = window.__AUTH_BASE_URL + '/auth';
      } else if (window.location.origin === 'https://app.premiselabs.co') {
        // Dashboard origin → the tortoise auth page (relative /auth on the
        // app origin is a dead end).
        target = 'https://tortoise.premiselabs.co/auth';
      } else if (isPremiselabsHost()) {
        // Tortoise site (tortoise.premiselabs.co, previews of the site) —
        // relative /auth is correct.
        target = '/auth';
      } else {
        // Any OTHER origin (e.g. a dashboard Pages preview) — a relative
        // /auth would resolve against the dashboard SPA and loop forever
        // (code-review P2-7). Go absolute; the e2e __AUTH_BASE_URL seam
        // covers localhost, where the local site serves /auth.
        target = 'https://tortoise.premiselabs.co/auth';
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
    // Refuse an UNUSABLE session BEFORE writing (#3485 review, cycle 4): the write
    // would otherwise replace a valid cookie with an expired session and only
    // then return false — leaving the visitor with a dead session in place of a
    // working one, on the credential the server gate actually reads.
    if (!session.expires_at || session.expires_at * 1000 <= Date.now()) return false;
    try {
      supabaseStorage.setItem(COOKIE_NAME, JSON.stringify(session));
      // Prove THIS write — not merely that SOME valid session is readable.
      // Read the COOKIE directly, not via readValidSession(): that helper also
      // migrates legacy keys and could prefer a NEWER legacy session over the one
      // just stored (#3485 review). The write must have LANDED — the cookie must
      // now carry THIS session, not a stale value the browser kept because it
      // refused the write (an over-cap write is a silent no-op, so reading back
      // whatever was already there would report success for a session that never
      // round-tripped). refresh_token is part of the identity too: a prior cookie
      // sharing the access_token but carrying a stale refresh_token is still NOT
      // this write. And the result must be USABLE by the destination gate — the
      // same strict predicate readValidSession() applies (access_token +
      // refresh_token + unexpired) — so storeSession() ===
      // true can never promise a session the gate rejects and bounces back to
      // /auth. That bounce is the loop this change exists to remove (#3485 P1).
      var raw = readCookie(COOKIE_NAME);
      if (!raw) return false;
      var stored = JSON.parse(raw);
      return !!(stored && stored.access_token === session.access_token &&
        stored.refresh_token === session.refresh_token &&
        stored.expires_at && stored.expires_at * 1000 > Date.now());
    } catch (e) { return false; }
  };

  // #2529: supabase-js v2.112.2 may fail to process the implicit-flow
  // #access_token fragment during async initialization (the URL hash
  // is parsed correctly by xr() but _getUser() or _saveSession() can
  // fail silently). Process the fragment synchronously HERE (before
  // supabase-js loads) and store the session directly to the parent-
  // domain cookie so the dashboard's createClient -> getSession() finds
  // a session already stored. Only processes #access_token — never error
  // fragments (#error=...) which must survive for the /auth banner.
  (function () {
    try {
      var hash = window.location.hash;
      if (!hash || !/^#access_token=/.test(hash)) return;
      var p = new URLSearchParams(hash.replace(/^#/, ''));
      var accessToken = p.get('access_token');
      var refreshToken = p.get('refresh_token');
      var expiresAt = parseInt(p.get('expires_at'), 10);
      if (!accessToken || !refreshToken || !expiresAt) return;
      var session = {
        access_token: accessToken,
        refresh_token: refreshToken,
        expires_at: expiresAt,
        expires_in: parseInt(p.get('expires_in'), 10) || 3600,
        token_type: p.get('token_type') || 'bearer',
      };
      // Provider tokens — size guard in setItem strips them if oversized.
      var pt = p.get('provider_token');
      if (pt) session.provider_token = pt;
      var prt = p.get('provider_refresh_token');
      if (prt) session.provider_refresh_token = prt;
      // Strip the fragment ONLY when the credential is safely stored (#3503;
      // #3485 review cycle 2 P1): this fragment is the only copy of the NEW
      // credential, and storeSession() returns false both when the write was
      // refused (an over-cap session) and when the cookie still holds a different
      // one — erasing it then strands the user on /auth with no credential
      // anywhere, or leaves them signed in as the previous account.
      if (storeSession(session)) {
        // Strip the fragment to prevent supabase-js from redundantly
        // re-processing the same fragment (which may log console errors).
        history.replaceState(null, '', window.location.pathname + window.location.search);
      }
    } catch (e) { /* best-effort */ }
  })();

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
