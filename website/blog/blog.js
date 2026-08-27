// Blog client JS — issue #1799.
// Wires share-bar events (copy-link, native share on mobile) + consent-gated
// PostHog events (share_click, article_read). The consent.js + PostHog
// snippet are statically included in the SSR head (#1794) — consent.js owns
// PostHog init (opted-out by default; opt-in on granted). This file does NOT
// add a second snippet; it only fires events when window.posthog exists
// (it always does; SDK-level gating decides whether events flow).
//
// Included on article pages via <script src="/blog/blog.js" defer>.

(function () {
  "use strict";
  var post = null;
  try {
    post = JSON.parse(document.getElementById("blog-config").textContent);
  } catch (e) {
    post = { slug: "", url: location.href, title: document.title };
  }

  function track(event, props) {
    if (typeof window.posthog !== "undefined" && window.posthog.capture) {
      try {
        window.posthog.capture(event, Object.assign({ post_slug: post.slug }, props));
      } catch (e) {
        /* analytics never breaks the page */
      }
    }
  }

  function toast(msg) {
    var el = document.createElement("div");
    el.textContent = msg;
    el.style.cssText =
      "position:fixed;bottom:76px;left:50%;transform:translateX(-50%);background:#0b1220;" +
      "color:#cbd5e1;border:1px solid #1e293b;padding:8px 16px;border-radius:8px;font-size:12px;z-index:60";
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 1600);
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    // Fallback: hidden textarea + execCommand (non-secure contexts, older Safari)
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }
    ta.remove();
    return ok ? Promise.resolve() : Promise.reject(new Error("copy failed"));
  }

  function isMobile() {
    return typeof window.matchMedia === "function" && window.matchMedia("(max-width: 720px)").matches;
  }

  function wireShareBar() {
    var buttons = document.querySelectorAll("[data-share]");
    Array.prototype.forEach.call(buttons, function (el) {
      el.addEventListener("click", function (ev) {
        var network = el.getAttribute("data-share");

        if (network === "copy") {
          ev.preventDefault();
          copyText(post.url)
            .then(function () {
              toast("Link copied");
              track("share_click", { network: "copy" });
            })
            .catch(function () {
              toast("Copy failed — select the URL manually");
            });
          return;
        }

        // Mobile native share replaces the primary (X) share button — ONE
        // share_click per physical click, correctly attributed.
        var useNative =
          network === "twitter" &&
          typeof navigator !== "undefined" &&
          typeof navigator.share === "function" &&
          isMobile();
        if (useNative) {
          ev.preventDefault();
          navigator
            .share({ title: post.title, url: post.url })
            .then(function () { track("share_click", { network: "native" }); })
            .catch(function () { /* user cancelled — no-op */ });
          return;
        }

        // Non-copy share buttons: track the click (the anchor navigates).
        track("share_click", { network: network || "unknown" });
      });
    });
  }

  var readFired = false;
  function measureThreshold() {
    // Recompute on load/resize — images have no reserved height, so
    // scrollHeight grows when they load late (stale-threshold fix).
    return Math.max(document.documentElement.scrollHeight - window.innerHeight - 200, 0);
  }

  function fireReadIfComplete() {
    if (readFired) return;
    var threshold = measureThreshold();
    var scrolled = window.scrollY;
    if (scrolled >= threshold * 0.8 || threshold <= 0) {
      // ≥80% scroll depth OR article shorter than ~viewport → read signal.
      // Fires once; deep-link/restored-scroll sessions also emit (initial check).
      readFired = true;
      var depth = threshold > 0 ? Math.round((scrolled / threshold) * 100) : 100;
      track("article_read", { depth_pct: depth });
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("load", fireReadIfComplete);
    }
  }

  function onScroll() {
    fireReadIfComplete();
  }

  function wireReadSignal() {
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("load", fireReadIfComplete);
    window.addEventListener("resize", fireReadIfComplete);
    fireReadIfComplete(); // initial position (deep-link / restored scroll)
  }

  function init() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
      return;
    }
    wireShareBar();
    wireReadSignal();
  }
  init();
})();
