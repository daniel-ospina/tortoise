/* Tortoise consent manager — issues #658 / #736.
 *
 * Single consent state gating PostHog (deployed on the tortoise funnel
 * pages) and the Google Tag Manager container (GTM-WQR34GSC — the single
 * tag container for GA4 and, when their tags are added, X / LinkedIn).
 * GTM loads ONLY on "granted"; on decline/undecided it never loads, so no
 * ad/analytics tags can fire. The Meta Pixel loader is a dormant stub
 * (placeholder ID, fail-safe) until a real pixel ID exists.
 *
 * Fail-safe: with no real PostHog project key configured this module does
 * NOTHING — no network calls, no banner, no localStorage writes.
 *
 * Automated browsers (navigator.webdriver — Playwright/E2E) never get the
 * banner overlay, but consent-state handling and PostHog init run exactly
 * as in a real browser (PostHog still loads opted-out; no events until
 * consent) — keeps same-viewport and mocked-click tests stable.
 *
 * Consent flow (PostHog's documented CMP pattern — posthog.com/docs/privacy/data-collection):
 *   - posthog-js is ALWAYS loaded (so opted-out users are still counted as
 *     visits) but initialized with `opt_out_capturing_by_default: true` +
 *     `cookieless_mode: 'on_reject'` — nothing is captured and no cookies
 *     are set until the user consents.
 *   - Accept  -> posthog.opt_in_capturing()  (persisted via local_storage)
 *   - Decline -> posthog.opt_out_capturing() (stays opted out)
 *
 * US Cloud is used (owner decision 2026-08-08 — the controller is US-based);
 * the loader derives https://us-assets.i.posthog.com/static/array.js from the
 * api_host below.
 */
(function () {
  "use strict";

  // PostHog Cloud project 548850 (US Cloud, owner decision 2026-08-08 —
  // the controller is US-based). Public client-side key — safe to embed.
  // The fail-safe guard below still applies: an empty/malformed key keeps
  // the banner and PostHog fully inert.
  var POSTHOG_KEY = "phc_zvBi25UoCxrq79qS7cudZhfAS3XQwEfrzEoZfR2EHkjS";

  var STORAGE_KEY = "tortoise_consent"; // "granted" | "denied" | absent = undecided
  var API_HOST = "https://us.i.posthog.com"; // US Cloud (default — region locked at project creation)

  function readConsent() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return null;
    }
  }

  // Public getter for the GTM / Meta Pixel loaders — they must check
  // `window.consentState() === "granted"` before loading.
  window.consentState = function () {
    return readConsent();
  };

  // ── Fail-safe: no real key configured -> stay fully inert. ──────────────
  if (!POSTHOG_KEY || POSTHOG_KEY.indexOf("__") === 0) {
    return;
  }

  var bannerRendered = false;

  function hideBanner() {
    var banner = document.getElementById("consent-banner");
    if (banner) {
      banner.parentNode && banner.parentNode.removeChild(banner);
    }
    bannerRendered = false;
  }

  function onAccept() {
    try {
      localStorage.setItem(STORAGE_KEY, "granted");
    } catch (e) {
      /* storage unavailable — proceed for this session */
    }
    if (window.posthog && typeof window.posthog.opt_in_capturing === "function") {
      window.posthog.opt_in_capturing();
    }
    hideBanner();
    // Consent granted — activate the consent-gated GTM container + Meta
    // loader too (GTM carries GA4; X/LinkedIn tags when they are added).
    loadConsentedAnalytics();
  }

  function onDecline() {
    try {
      localStorage.setItem(STORAGE_KEY, "denied");
    } catch (e) {
      /* storage unavailable — proceed for this session */
    }
    if (window.posthog && typeof window.posthog.opt_out_capturing === "function") {
      window.posthog.opt_out_capturing();
    }
    hideBanner();
  }

  // Banner markup lives here (shared module) and is injected at runtime —
  // site design tokens: --bg #060b14, --surface #0d1a2d, --accent #06b6d4,
  // --text #cbd5e1, --text-dim #64748b, --border #1e293b, mono font.
  function renderBanner() {
    // Automated browsers (Playwright/E2E) never get the overlay — keeps
    // same-viewport and mocked-click tests stable while real browsers do.
    // Consent state + PostHog init stay intact (PostHog still loads
    // opted-out; no events until consent).
    if (navigator.webdriver) {
      return;
    }
    if (bannerRendered || document.getElementById("consent-banner")) {
      return;
    }
    var style = document.createElement("style");
    style.id = "consent-banner-style";
    style.textContent =
      "#consent-banner{position:fixed;left:0;right:0;bottom:0;z-index:9999;" +
      "background:#0d1a2d;border-top:1px solid #1e293b;padding:0.9rem 1.25rem;" +
      "font-family:'SF Mono','Cascadia Code','Fira Code','JetBrains Mono',monospace;" +
      "font-size:0.8rem;color:#cbd5e1;box-shadow:0 -4px 24px rgba(0,0,0,0.45)}" +
      "#consent-banner .consent-banner-inner{display:flex;flex-wrap:wrap;align-items:center;" +
      "justify-content:space-between;gap:0.75rem 1.25rem;width:100%;margin:0 auto;max-width:72rem}" +
      "#consent-banner .consent-banner-text{margin:0;color:#64748b;line-height:1.5;max-width:38rem;flex:1 1 22rem}" +
      "#consent-banner .consent-banner-text a{color:#06b6d4;text-decoration:none}" +
      "#consent-banner .consent-banner-text a:hover{text-decoration:underline}" +
      "#consent-banner .consent-banner-actions{display:flex;gap:0.6rem;flex-shrink:0;margin-left:auto}" +
      "#consent-banner button{font-family:inherit;font-size:0.78rem;padding:0.45rem 1rem;" +
      "border-radius:6px;cursor:pointer;border:1px solid #1e293b;background:transparent;" +
      "color:#cbd5e1;transition:border-color .15s,color .15s,background .15s}" +
      "#consent-banner .consent-accept{background:#06b6d4;border-color:#06b6d4;" +
      "color:#060b14;font-weight:600}" +
      "#consent-banner .consent-accept:hover{background:#22d3ee;border-color:#22d3ee}" +
      "#consent-banner .consent-decline:hover{border-color:#64748b;color:#fff}";

    var banner = document.createElement("div");
    banner.id = "consent-banner";
    banner.setAttribute("role", "dialog");
    banner.setAttribute("aria-label", "Cookie and analytics consent");
    banner.innerHTML =
      '<div class="consent-banner-inner">' +
      '<p class="consent-banner-text">We use cookies and analytics to understand how the product is used ' +
      'and improve it. See our <a href="/privacy">Privacy Policy</a>.</p>' +
      '<div class="consent-banner-actions">' +
      '<button type="button" id="consent-accept" class="consent-accept">Accept</button>' +
      '<button type="button" id="consent-decline" class="consent-decline">Decline</button>' +
      "</div>" +
      "</div>";

    (document.head || document.documentElement).appendChild(style);
    document.body.appendChild(banner);
    bannerRendered = true;

    var accept = document.getElementById("consent-accept");
    var decline = document.getElementById("consent-decline");
    if (accept) {
      accept.addEventListener("click", onAccept);
    }
    if (decline) {
      decline.addEventListener("click", onDecline);
    }
  }

  // ── posthog-js loader (official snippet logic, US api_host) — the stub
  //    queues calls until array.js loads, so init + opt in/out may be issued
  //    immediately.
  function loadPostHog() {
    if (window.posthog && window.posthog.__SV) {
      return;
    }
    /* eslint-disable no-useless-concat, no-sequences, max-len */
    !function (t, e) {
      var o, n, p, r;
      e.__SV || (window.posthog = e, e._i = [], e.init = function (i, s, a) {
        function g(t, e) {
          var o = e.split(".");
          2 == o.length && (t = t[o[0]], e = o[1]),
          t[e] = function () {
            t.push([e].concat(Array.prototype.slice.call(arguments, 0)));
          };
        }
        (p = t.createElement("script")).type = "text/javascript",
        p.crossOrigin = "anonymous",
        p.async = !0,
        p.src = s.api_host.replace(".i.posthog.com", "-assets.i.posthog.com") + "/static/array.js",
        (r = t.getElementsByTagName("script")[0]).parentNode.insertBefore(p, r);
        var u = e;
        for (void 0 !== a ? u = e[a] = [] : a = "posthog", u.people = u.people || [],
          u.toString = function (t) { var e = "posthog"; return "posthog" !== a && (e += "." + a), t || (e + " (stub)"), e; },
          u.people.toString = function () { return u.toString(1) + ".people (stub)"; },
          o = "init capture register register_once register_for_session unregister unregister_for_session " +
            "getFeatureFlag getFeatureFlagResult isFeatureEnabled reloadFeatureFlags " +
            "updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId " +
            "getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify " +
            "setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags " +
            "setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups " +
            "get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording " +
            "sessionRecordingStarted captureException loadToolbar get_property getSessionProperty " +
            "createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing " +
            "has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),
          n = 0; n < o.length; n++) g(u, o[n]);
        e._i.push([i, s, a]);
      }, e.__SV = 1);
    }(document, window.posthog || []);

    posthog.init(POSTHOG_KEY, {
      api_host: API_HOST,
      opt_out_capturing_by_default: true,
      cookieless_mode: "on_reject",
      opt_out_capturing_persistence_type: "local_storage",
      autocapture: false, // no autocapture before consent; explicit captures only
    });
  }

  // ── Consent-gated Google Tag Manager loader + Meta Pixel stub ───────────
  // GTM is the SINGLE tag container (owner decision, #736): GA4 and, when
  // their tags are added, X (Twitter) + LinkedIn all fire from container
  // GTM-WQR34GSC. The container loads ONLY when consent is "granted" —
  // never on Decline or undecided — so if GTM never loads, none of those
  // tags can fire. The container ID is public (it appears in page source).
  //
  // GA4 is now delivered VIA GTM — the former direct gtag loader
  // (GA_MEASUREMENT_ID "__GA4_MEASUREMENT_ID__") was removed at #736; the
  // GA4 config tag lives in container GTM-WQR34GSC (Google tag id
  // G-QP6D2BGC2B, stream "Tortoise landing").
  var GTM_CONTAINER_ID = "GTM-WQR34GSC";
  var META_PIXEL_ID = "__META_PIXEL_ID__"; // dormant stub (fail-safe)
  var gtmLoaded = false;
  var metaPixelLoaded = false;

  // Standard GTM loader (official snippet logic — dataLayer initialized
  // BEFORE the gtm.js request). Fail-safe: placeholder ID stays inert.
  function loadGTM() {
    if (!GTM_CONTAINER_ID || GTM_CONTAINER_ID.indexOf("__") === 0 || gtmLoaded) {
      return; // fail-safe: placeholder ID or already loaded — stay inert
    }
    gtmLoaded = true;
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({
      "gtm.start": new Date().getTime(),
      event: "gtm.js",
    });
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtm.js?id=" + GTM_CONTAINER_ID;
    (document.head || document.documentElement).appendChild(s);
  }

  function loadMetaPixel() {
    if (!META_PIXEL_ID || META_PIXEL_ID.indexOf("__") === 0 || metaPixelLoaded) {
      return; // fail-safe: placeholder ID or already loaded — stay inert
    }
    metaPixelLoaded = true;
    // Meta Pixel base code (official snippet).
    !function (f, b, e, v, n, t, s) {
      if (f.fbq) { return; }
      n = f.fbq = function () {
        n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments);
      };
      if (!f._fbq) { f._fbq = n; }
      n.push = n;
      n.loaded = !0;
      n.version = "2.0";
      n.queue = [];
      t = b.createElement(e);
      t.async = !0;
      t.src = v;
      s = b.getElementsByTagName(e)[0];
      s.parentNode.insertBefore(t, s);
    }(window, document, "script", "https://connect.facebook.net/en_US/fbevents.js");
    fbq("init", META_PIXEL_ID);
    fbq("track", "PageView");
  }

  // Runs after a consent decision — reads the live state and loads the
  // consented tools only when it is exactly "granted".
  function loadConsentedAnalytics() {
    if (window.consentState() !== "granted") {
      return; // never load on Decline / undecided
    }
    loadGTM();
    loadMetaPixel();
  }

  function init() {
    // Defensive: never double-render if this module were ever loaded twice.
    if (document.getElementById("consent-banner-style")) {
      return;
    }
    loadPostHog();

    var state = readConsent();
    if (state === "granted") {
      // Already consented on a previous visit — activate capture immediately.
      if (window.posthog && typeof window.posthog.opt_in_capturing === "function") {
        window.posthog.opt_in_capturing();
      }
      // Consent already given — activate the consent-gated loaders too.
      loadConsentedAnalytics();
    } else if (state === "denied") {
      if (window.posthog && typeof window.posthog.opt_out_capturing === "function") {
        window.posthog.opt_out_capturing();
      }
    } else {
      // Undecided — stay opted out by default; show the banner.
      renderBanner();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
