/**
 * The consent bridge's FAILURE FACE — a page, not a JSON blob (#10884, #10865).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `/api/consent/start` is a route a PERSON navigates to, not an endpoint a program calls: the
 * connector sends a browser here and the browser renders whatever comes back. Every REFUSAL on that
 * route used to answer `json({error, reason})`, so a visitor whose consent attempt could not be
 * carried saw `{"error":"unavailable","reason":"consent_store_unavailable"}` on a blank page — with
 * no sentence telling them what happened and no way forward. #10487's own title names the class
 * (*"both as raw JSON"*); #10884 covers the `503` store arms and #10865 arm B the `403` refusal, and
 * they are ONE root: **the refusal states of the carriage were never given a user-facing face.**
 *
 * ⛔ "REFUSAL", not "hard failure": `start.ts` still returns JSON from TWO places — the `400`
 * caller error and the terminal `200` flow state — and both reach a browser. A summary that claimed
 * otherwise would be FALSE, and it is the more dangerous kind of false: #10865's owner comment
 * records the terminal `200` as a DIFFERENT CONTRACT, so a reader who believed the absolute would
 * "finish" the job by converting an arm whose retirement is the OWNER'S DECISION (that comment
 * offers it as an option and calls it "an owner-visible API decision, not a review fix" — it does
 * not rule it out, and this comment must not say it does). The two survivors are counted and
 * classified in `tests/test_oauth_consent_bff_routes.py`.
 *
 * WHAT THIS PAGE DELIBERATELY DOES
 * --------------------------------
 *  - **It changes no status code.** `403` stays `403` and `503` stays `503`. The oracle semantics of
 *    the `403` refusal (a deliberate, recorded EXEMPTION from §2.3's no-oracle rule, scoped to the
 *    mint) are carried by the STATUS and by the header below — a page that answered `200` here would
 *    silently retire that refusal, and a caller that watched the code would see nothing to refuse.
 *  - **It keeps the machine-readable reason**, in `x-consent-refusal`, so anything that watches the
 *    flow keeps a stable signal while the human gets prose. The reason never reaches the copy: a
 *    visitor cannot act on `consent_store_unavailable` and naming it is an invitation to guess.
 *  - **It decides NOTHING.** No JavaScript, no form, no link, no external asset, and NOT ONE BYTE of
 *    the request is echoed (not the query string, not a `state`, not a client id). The only way to
 *    keep "this page decides nothing" true under adversarial input is to have no decision to make:
 *    every value in the document is a compile-time constant, so there is no interpolation an
 *    attacker could reach and no destination this page could be talked into sending a browser to.
 *    The retry affordance is therefore prose ("reload this page" / "start again from your client")
 *    rather than an `href`, because any `href` would have to echo the query or choose a target.
 *  - **It makes NO outbound request at all** (`default-src 'none'`, the same credential-refusal
 *    policy shape `website/functions/_middleware.ts` uses for its stray-authorization response).
 *    ⛔ This page's URL carries the attempt key and the `state` on the legs that reach it, so a
 *    subresource request — the platform-injected analytics beacon included — would carry them in a
 *    `Referer` into some third party's log. `Referrer-Policy: no-referrer` is stamped as well, and
 *    the two are belt-and-braces on purpose: the CSP stops the request being made, the header stops
 *    the URL being attached if anything is ever added.
 *
 * WHAT IT DOES NOT DO
 * -------------------
 * It does not diagnose the fault. WHICH store call failed, and why, is not determinable from here
 * (#10884's own body records that it needs `wrangler pages deployment tail`), so this page says
 * "temporarily unavailable" and stops. It also does not soften the refusal: it renders the outcome
 * of a refusal that has ALREADY happened, one line above in `start.ts`.
 */

/**
 * The credential-refusal policy: no outbound allowance of ANY kind.
 *
 * Declared here rather than in `_shared/security-headers.ts` because it is deliberately NOT one of
 * the audited dashboard policies — putting a beacon-less policy into that set invites the "unify
 * them" edit that would relax the loaded surface. `src/securityHeaders.test.js` pins this file's
 * right to stamp it (`STRICT_LOCAL_POLICY`) AND pins the value itself, so a future edit cannot keep
 * the name and swap in a relaxed policy with every other assertion green.
 */
const CONSENT_REFUSAL_CSP =
  "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; " +
  "frame-ancestors 'none'";

/** HSTS (#1003 §1): `_headers` does not reach Functions output, so it is stamped here. */
const HSTS = "max-age=31536000; includeSubDomains";

/**
 * The reasons this page can render. Each is the `reason` the route already published, so the header
 * keeps the exact token an existing watcher reads.
 */
export type ConsentRefusalReason =
  | "consent_store_unavailable"
  | "session_store_unavailable"
  | "as_origin_misconfigured"
  | "flow_not_yours";

interface Copy {
  title: string;
  heading: string;
  what: string;
  next: string;
}

/**
 * Two states, not four — because the VISITOR has two situations, and the reason codes are ours.
 *
 *  - the three `503` reasons (`consent_store_unavailable`, `session_store_unavailable`,
 *    `as_origin_misconfigured`) are the same thing to a person: an infrastructure or deployment
 *    fault on our side, not their doing. Rendering them distinctly would leak our topology into
 *    copy that cannot help anyone act differently.
 *    ⛔ "retryable" was the word here and NO arm earns it unconditionally: each of the three can
 *    be permanent in a state a visitor actually reaches. A genuine DDL failure in the
 *    `consent_store_unavailable` arm (`start.ts` records that no retry or restart fixes a failed
 *    `ALTER TABLE`), an ABSENT `SESSIONS` binding for `session_store_unavailable`, and the
 *    `CONSENT_AS_ORIGIN` binding itself not parsing for `as_origin_misconfigured`
 *    (`start.ts::parseAsOrigin` returns null only for a SET-but-unusable value — the default is
 *    valid) are all states a reload cannot change. The copy below therefore never PROMISES that a
 *    reload fixes it: the reload is offered as a try, and a fresh attempt from the client as the
 *    next step. That is the same care the `403` gets for the same reason — copy must not sell a
 *    retry that cannot work.
 *  - `403 flow_not_yours` is NOT retryable by reload: the row belongs to another attempt, so
 *    reloading the same URL is refused again (the bridge doc §"a DEAD row" states it). The only way
 *    forward is a new authorize request from the client, and saying "reload to retry" here would be
 *    a lie that costs the visitor another dead end.
 */
const STORE_FAULT: Copy = {
  title: "Connection not completed — try again",
  heading: "We couldn't finish setting up your connection",
  what:
    "Something on our side was temporarily unavailable, so your AI client's connection wasn't " +
    "set up. Nothing was approved, and no access was granted.",
  next:
    "Reload this page to try again. If it keeps happening, start the connection again from your " +
    "MCP client — that opens a fresh attempt rather than repeating this one.",
};

const FLOW_NOT_YOURS: Copy = {
  title: "Connection not completed — start again",
  // ⛔ Not "this attempt isn't yours": the commonest real cause is the visitor's OWN earlier
  // browser or a restarted client, and accusing them of something is both wrong and unhelpful.
  heading: "We couldn't continue this connection attempt",
  what:
    "This approval link belongs to a different browser, or a newer attempt has replaced it. " +
    "Nothing was approved, and no access was granted.",
  next:
    "Go back to your MCP client and start the connection again. Reloading this page will not help — " +
    "it leads back to the same place.",
};

/**
 * The page for one refusal. Status is the CALLER's (never this module's): the route decides what it
 * refuses, this renders it, and a page builder that chose its own status could disagree with the
 * refusal it is describing.
 */
export function consentRefusalPage(reason: ConsentRefusalReason, status: number): Response {
  const copy = reason === "flow_not_yours" ? FLOW_NOT_YOURS : STORE_FAULT;
  // No template interpolation anywhere: every value is a constant from the table above.
  const html =
    "<!DOCTYPE html>\n" +
    '<html lang="en">\n<head>\n<meta charset="utf-8">\n' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n' +
    '<meta name="robots" content="noindex">\n' +
    `<title>${copy.title}</title>\n` +
    "<style>\n" +
    ":root{color-scheme:light dark}" +
    "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;" +
    "font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;" +
    "background:#fbfaf8;color:#1c1917;padding:2rem}" +
    "@media(prefers-color-scheme:dark){body{background:#111110;color:#f5f4f2}}" +
    "main{max-width:34rem}" +
    ".brand{font-weight:600;letter-spacing:.02em;opacity:.75;margin-bottom:1.5rem}" +
    "h1{font-size:1.4rem;line-height:1.3;margin:0 0 1rem}" +
    "p{margin:0 0 1rem}" +
    ".next{font-weight:500}" +
    "</style>\n</head>\n<body>\n<main>\n" +
    '<div class="brand">tortoise</div>\n' +
    `<h1>${copy.heading}</h1>\n` +
    `<p>${copy.what}</p>\n` +
    `<p class="next">${copy.next}</p>\n` +
    "</main>\n</body>\n</html>\n";
  const headers = new Headers({
    "Content-Type": "text/html; charset=utf-8",
    // The route's own no-store discipline: a refusal is never cacheable, and this one describes a
    // request whose URL carried a per-attempt key.
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    // The machine-readable half. The token is the SAME reason the JSON body used to carry, so a
    // watcher that keyed on it keeps working; it is a header rather than a body because the body is
    // now prose for a person.
    "x-consent-refusal": reason,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": HSTS,
    "Content-Security-Policy": CONSENT_REFUSAL_CSP,
  });
  return new Response(html, { status, headers });
}
