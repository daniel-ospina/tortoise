// Behavioral tests for the blog SEO keyword module + the agent rate limiter (#8293).
//
// Runs the real modules from the edge helpers under Node with
// --experimental-strip-types — real assertions on the split (#8293 moved the
// keyword DATA to _shared/keywords.ts), on the primary-keyword selection, and on
// the deterministic meta repair that replaced "suggest a keyword and hope".
//
// Run (Node >= 22.6):
//   node --experimental-strip-types tests/test_blog_seo_meta.mjs

import {
  TAG_KEYWORDS,
  keywordsFor,
  primaryKeyword,
  containsKeyword,
  normalizeForMatch,
  ensureKeyword,
} from "../website/functions/blog/_shared/seo-keywords.ts";
import { rateLimited, __resetRateLimits } from "../website/functions/blog/_shared/agent-auth.ts";

let failures = 0;
const ok = (msg) => console.log(`  ✓ ${msg}`);
const fail = (msg) => { failures += 1; console.error(`  ✗ ${msg}`); };
const assert = (cond, msg) => { if (cond) ok(msg); else fail(msg); };
const assertEq = (a, e, msg) => {
  if (a === e) ok(msg);
  else fail(`${msg}\n      expected: ${JSON.stringify(e)}\n      actual:   ${JSON.stringify(a)}`);
};

// ── The split: data moved, import surface unchanged (#8293) ───────────────
console.log("the keyword split — data in keywords.ts, consumer unchanged");
{
  const tags = Object.keys(TAG_KEYWORDS);
  assertEq(tags.length, 12, "all 12 starter tags survived the split");
  assert(
    Object.values(TAG_KEYWORDS).every((v) => Array.isArray(v) && v.length > 0),
    "no tag has an empty keyword array",
  );
  const total = Object.values(TAG_KEYWORDS).reduce((n, v) => n + v.length, 0);
  assertEq(total, 147, "all 147 keyword strings survived the move");

  assert(keywordsFor("agent-memory").length > 0, "keywordsFor resolves a starter tag");
  assert(keywordsFor("self-hosting").length > 0, "keywordsFor resolves every starter tag");
  assertEq(keywordsFor("not-a-real-tag").length, 0, "an unknown tag yields [] (content fallback)");
  assertEq(keywordsFor("AGENT-MEMORY").length > 0, true, "an upper-case free-form tag still resolves");
  assertEq(keywordsFor("  mcp  ").length > 0, true, "a whitespace-padded tag still resolves");
}

// ── Primary keyword selection: deterministic ──────────────────────────────
console.log("primary keyword — ONE main keyword, deterministically chosen");
{
  const p = primaryKeyword(["agent-memory"]);
  assertEq(p, TAG_KEYWORDS["agent-memory"][0], "the primary is the FIRST keyword of the first matched tag");
  assertEq(primaryKeyword(["agent-memory"]), p, "the same tags always yield the same primary");
  assertEq(
    primaryKeyword(["unknown", "epistemic-memory"]),
    TAG_KEYWORDS["epistemic-memory"][0],
    "an unmapped tag is skipped, not treated as a failure",
  );
  assertEq(primaryKeyword([]), null, "no tags → no primary");
  assertEq(primaryKeyword(["nope"]), null, "unmapped tags → no primary (caller skips the check)");
}

// ── The contains-check is word-boundary and case-insensitive ──────────────
console.log("containsKeyword — a substring is not a word");
{
  assert(containsKeyword("Agent Memory in 2026", "agent memory"), "case-insensitive match");
  assert(containsKeyword("agent-memory systems", "agent memory"), "punctuation is normalised");
  assert(!containsKeyword("management systems", "agent memory"), "'management' does not match 'agent memory'");
  assert(!containsKeyword("agents memory", "agent memory"), "a plural does not match the singular phrase");
  assert(containsKeyword("anything", ""), "an empty keyword is trivially present");
  assertEq(normalizeForMatch("Agent Memory: A Guide!"), "agent memory a guide", "normalisation is lowercase + alnum");
}

// ── The repair: verify, don't hope (#8293) ────────────────────────────────
console.log("ensureKeyword — the primary keyword is guaranteed, within the limit");
{
  const kw = "agent memory";
  assertEq(ensureKeyword("Agent Memory in 2026", kw, 60), "Agent Memory in 2026", "an already-present keyword is left alone");
  assertEq(ensureKeyword("", kw, 60), kw, "empty text becomes the keyword");

  const missing = ensureKeyword("How belief propagation works", kw, 60);
  assert(containsKeyword(missing, kw), "a missing keyword is inserted");
  assert(missing.length <= 60, `the repair respects the field limit (${missing.length} <= 60)`);
  assert(missing.startsWith("agent memory"), "the keyword is FRONT-LOADED, not appended");

  // Property sweep: for any input and any real keyword, the result is present
  // and within budget. This is the guarantee the old "weave in" prompt lacked.
  const kws = Object.values(TAG_KEYWORDS).flat().slice(0, 40);
  // Only inputs already within budget are swept: an over-limit INPUT is the
  // length contract's job (seo-constraints.ts), and the real call site always
  // passes already-constrained text. Asserting the budget on an over-limit input
  // would be asserting a contract this function deliberately does not hold.
  const inputs = ["", "short", "x".repeat(200), "A long descriptive title about graphs and vectors and memory drift", "agent"]
    .filter((t) => t.length <= 60);
  let bad = 0;
  for (const k of kws) {
    for (const max of [60, 155]) {
      for (const text of inputs) {
        const out = ensureKeyword(text, k, max);
        if (out.length > max && k.length <= max) { bad += 1; fail(`over budget: ${JSON.stringify(out.slice(0, 80))}`); }
        if (k.length <= max && !containsKeyword(out, k)) { bad += 1; fail(`keyword lost: ${JSON.stringify(k)} -> ${JSON.stringify(out.slice(0, 80))}`); }
      }
    }
  }
  assertEq(bad, 0, `every one of ${kws.length * 2 * inputs.length} (keyword, limit, text) combinations holds both properties`);

  // A keyword longer than the field: the keyword alone wins — a truncated
  // keyword is worse than an over-long field, and the length contract decides.
  const long = "a".repeat(80);
  assertEq(ensureKeyword("short", long, 60), long, "an over-long keyword is returned whole, not truncated");
  assertEq(ensureKeyword("short", null, 60), "short", "a null primary leaves the text untouched");

  // No stray separator when the text after the keyword was cut.
  const cut = ensureKeyword("x".repeat(300), kw, 155);
  assert(!/[\s:,\-]$/.test(cut), "the repair never ends on a dangling separator");
}

// ── The limiter the generator now shares (#8293 part a) ───────────────────
console.log("rateLimited — burst and daily ceilings, isolated per caller");
{
  __resetRateLimits();
  const limit = { windowMs: 60_000, burst: 3, dayMs: 86_400_000, day: 5 };
  const r = [1, 2, 3, 4].map(() => rateLimited("agent:a", limit));
  assertEq(r.join(","), "false,false,false,true", "the burst ceiling holds and the next call is limited");
  assertEq(rateLimited("agent:b", limit), false, "a different caller has its own budget");

  __resetRateLimits();
  const dayLimit = { windowMs: 1, burst: 100, dayMs: 86_400_000, day: 2 };
  rateLimited("agent:c", dayLimit);
  rateLimited("agent:c", dayLimit);
  assertEq(rateLimited("agent:c", dayLimit), true, "the daily ceiling holds independently of the burst window");
}

console.log("");
if (failures > 0) {
  console.error(`${failures} blog SEO meta assertion(s) failed`);
  process.exitCode = 1;
} else {
  console.log("all blog SEO meta assertions passed");
}
