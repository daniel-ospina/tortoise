// Blog client JS — issue #1799.
// Wires share-bar events (copy-link, native share) + consent-gated PostHog
// events (share_click, article_read). The consent.js + PostHog snippet are
// statically included in the SSR head (#1794) — this file does NOT add a
// second snippet; it only fires events when posthog exists (consent granted).
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

  function wireShareBar() {
    var buttons = document.querySelectorAll("[data-share]");
    Array.prototype.forEach.call(buttons, function (el) {
      el.addEventListener("click", function (ev) {
        var network = el.getAttribute("data-share");

        if (network === "copy") {
          ev.preventDefault();
          navigator.clipboard
            .writeText(post.url)
            .then(function () {
              toast("Link copied");
              track("share_click", { network: "copy" });
            })
            .catch(function () {
              toast("Copy failed — select the URL manually");
            });
          return;
        }

        // Non-copy share buttons: track the click (the anchor navigates).
        track("share_click", { network: network || "unknown" });
      });
    });
  }

  function wireNativeShare() {
    // If navigator.share is available (mobile), a long-press style native
    // share replaces the anchor default on the primary share button.
    if (typeof navigator !== "undefined" && navigator.share) {
      var primary = document.querySelector('[data-share="twitter"]');
      if (primary) {
        primary.addEventListener("click", function (ev) {
          ev.preventDefault();
          navigator
            .share({ title: post.title, url: post.url })
            .then(function () { track("share_click", { network: "native" }); })
            .catch(function () { /* user cancelled — no-op */ });
        });
      }
    }
  }

  var readFired = false;
  function wireReadSignal() {
    var threshold = Math.max(document.documentElement.scrollHeight - window.innerHeight - 200, 0);
    function onScroll() {
      if (readFired) return;
      if (window.scrollY >= threshold * 0.8 || window.scrollY >= threshold) {
        readFired = true;
        track("article_read", { depth_pct: Math.round((window.scrollY / Math.max(threshold, 1)) * 100) });
        window.removeEventListener("scroll", onScroll);
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    // Also fire if the article is shorter than the viewport
    if (threshold <= 0) {
      readFired = true;
      track("article_read", { depth_pct: 100 });
    }
  }

  function init() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
      return;
    }
    wireShareBar();
    wireNativeShare();
    wireReadSignal();
  }
  init();
})();
