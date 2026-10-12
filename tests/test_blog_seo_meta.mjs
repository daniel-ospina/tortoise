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
import { constraints } from "../website/functions/blog/_shared/seo-constraints.ts";

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
  // #8293 review: the old implementation was `normalized.includes(normalized)`,
  // so every one of these three was TRUE — the endpoint reported
  // `keyword_ok: true` for a title that did not carry the keyword as a term.
  assert(!containsKeyword("xagent memory", "agent memory"), "a prefixed word is not a match ('xagent')");
  assert(!containsKeyword("hybrid searches", "hybrid search"), "an inflected word is not a match ('searches' ≠ 'search')");
  assert(!containsKeyword("paragraph rag", "graph rag"), "'graph rag' inside 'paragraph rag' is not a word match");
  assert(containsKeyword("knowledge graph rag", "graph rag"), "a real word-boundary match still holds");
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
  const inputs = [
    "",
    "short",
    "x".repeat(200),
    "A long descriptive title about graphs and vectors and memory drift",
    "agent",
    // #8293 review: within the 60 budget, but long enough that a multi-word
    // keyword + ": " exceeds it — so the sweep actually ENTERS the truncation /
    // word-boundary branch instead of taking the `candidate.length <= max`
    // early return on every combination.
    "memory systems in production and the long tail of drift",
  ].filter((t) => t.length <= 60);
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

  // No stray separator when the text after the keyword was cut. The class now
  // includes `|` — the separator the brand suffix itself introduces (#8293).
  const cut = ensureKeyword("x".repeat(300), kw, 155);
  assert(!/[\s:,|\-]$/.test(cut), "the repair never ends on a dangling separator");

  // The truncation lands INSIDE the brand suffix for a front-loaded title: the
  // half-suffix must not survive. This is the exact shape the handler produced,
  // and the old strip class left a bare " |" behind.
  const dangling = ensureKeyword("Agent Memory in 2026 | Tortoise", "long-term memory for ai agents", 60);
  assert(!dangling.endsWith("|"), `no dangling '|' survives the cut (got ${JSON.stringify(dangling)})`);
  assert(!/[\s:,|\-]$/.test(dangling), "the strip class covers '|', the brand-suffix separator");
  assert(containsKeyword(dangling, "long-term memory for ai agents"), "the dangling-case repair still carries the keyword");

  // The second `enforce` the handler performs must be a no-op on the brand
  // suffix: `metaTitle` is idempotent (#8293 review). Pre-fix, the second call
  // returned "… | Tortoise | Tortoise".
  const once = constraints.metaTitle("A short generated title");
  assertEq(once, "A short generated title | Tortoise", "metaTitle appends the brand suffix once");
  assertEq(constraints.metaTitle(once), once, "metaTitle is idempotent under a second enforce");
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

// ── The endpoint's enforce → repair → enforce composition (#8293 review) ──
// The composition is where the doubled suffix and the dangling separator
// actually shipped, so it is driven for real: the exported `onRequestPost` runs
// with a stubbed `fetch` for both the agent-key lookup and OpenRouter. A check
// that exercised only the shared modules (as this harness used to) cannot see
// the handler's own second `constraints.enforce` pass.
console.log("generate-seo handler — one brand suffix, no dangling separator, no silent 200");
{
  const { onRequestPost } = await import("../website/functions/blog/api/generate-seo.ts");

  const drive = async (metaTitle, metaDescription) => {
    globalThis.fetch = async (url) => {
      const u = String(url);
      if (u.includes("/rest/v1/blog_agent_keys")) {
        return Response.json([{ agent_name: "pipeline", active: true }]);
      }
      if (u.includes("openrouter.ai")) {
        return Response.json({
          choices: [{
            message: {
              content: JSON.stringify({
                slug: "a-slug",
                excerpt: "A concrete excerpt that earns the click.",
                tags: ["agent-memory"],
                meta_title: metaTitle,
                meta_description: metaDescription,
              }),
            },
          }],
          usage: { prompt_tokens: 10, completion_tokens: 20 },
        });
      }
      return Response.json({});
    };
    const request = new Request("https://tortoise.premiselabs.co/blog/api/generate-seo", {
      method: "POST",
      headers: { "X-Agent-Key": "test-key", "Content-Type": "application/json" },
      body: JSON.stringify({ title: "A title", body: "Sentence. ".repeat(40), tags: ["agent-memory"] }),
    });
    const res = await onRequestPost({
      request,
      env: {
        SUPABASE_URL: "https://db.test",
        SUPABASE_SERVICE_ROLE_KEY: "svc",
        OPENROUTER_API_KEY: "or",
      },
    });
    return { status: res.status, body: await res.json() };
  };

  // The happy path the new prompt asks for — the model's title already carries
  // the primary keyword, so the repair is a no-op and the SECOND enforce runs on
  // an already-suffixed value. Pre-fix this returned the suffix twice.
  const happy = await drive(
    "Long-Term Memory for AI Agents in 2026",
    "Long-term memory for ai agents that remembers why it believes.",
  );
  assertEq(happy.status, 200, "an in-budget generated title returns 200");
  const suffixCount = (happy.body.meta_title.match(/\| Tortoise/g) || []).length;
  assertEq(suffixCount, 1, `the brand suffix is appended once, not twice (got ${JSON.stringify(happy.body.meta_title)})`);
  assertEq(happy.body.keyword_ok, true, "keyword_ok is true when the keyword is genuinely present");

  // The repair's truncation can land inside the brand suffix; the fragment must
  // not survive as a dangling " |".
  const dangling = await drive(
    "Agent Memory in 2026",
    "How agent memory works and why long-term memory for ai agents matters.",
  );
  assertEq(dangling.status, 200, "the dangling case still returns 200");
  assert(
    !dangling.body.meta_title.endsWith("|"),
    `no dangling '|' survives (got ${JSON.stringify(dangling.body.meta_title)})`,
  );
  assertEq(
    (dangling.body.meta_title.match(/\| Tortoise/g) || []).length <= 1,
    true,
    `no doubled suffix in the dangling case (got ${JSON.stringify(dangling.body.meta_title)})`,
  );

  // A failed generation must stay a failure: the repair must not invent a
  // keyword-only title and turn the pre-existing 502 into a 200.
  const incomplete = await drive("", "");
  assertEq(incomplete.status, 502, "empty meta fields still answer 502 generation_incomplete");
  assertEq(incomplete.body.error, "generation_incomplete", "the incompleteness error is named");
}

// ── #8320: the guarantee must hold for the call the PIPELINE makes ──────────
// The block above always sends `tags`, which is why this bug shipped: the
// pipeline sends title + body only, and the model GENERATES the tags. Reading
// the primary keyword from the request tags therefore yielded null for every
// real call, the repair had nothing to enforce, and `keyword_ok` returned TRUE
// while checking nothing.
console.log("");
console.log("generate-seo with NO request tags — the keyword check must not be vacuous (#8320)");
{
  const { onRequestPost } = await import("../website/functions/blog/api/generate-seo.ts");

  // The model is the only source of tags on this path.
  const driveNoTags = async (opts = {}) => {
    globalThis.fetch = async (url) => {
      const u = String(url);
      if (u.includes("/rest/v1/blog_agent_keys")) {
        return Response.json([{ agent_name: "pipeline", active: true }]);
      }
      if (u.includes("openrouter.ai")) {
        return Response.json({
          choices: [{
            message: {
              content: JSON.stringify({
                slug: "a-slug",
                excerpt: "A concrete excerpt that earns the click.",
                // The model picks from the canonical vocabulary, as it did live.
                tags: opts.generatedTags ?? ["agent-memory"],
                meta_title: opts.metaTitle ?? "Agent Memory 2026: Recall vs Learning | Tortoise",
                meta_description: opts.metaDescription ??
                  "Agent memory systems measure recall, not learning. Tortoise tracks confidence and updates beliefs when information conflicts.",
              }),
            },
          }],
          usage: { prompt_tokens: 10, completion_tokens: 20 },
        });
      }
      return Response.json({});
    };
    const request = new Request("https://tortoise.premiselabs.co/blog/api/generate-seo", {
      method: "POST",
      headers: { "X-Agent-Key": "test-key", "Content-Type": "application/json" },
      // NO `tags` — the shape the CMS ingest actually sends.
      body: JSON.stringify({ title: "A title", body: "Sentence. ".repeat(40) }),
    });
    const res = await onRequestPost({
      request,
      env: {
        SUPABASE_URL: "https://db.test",
        SUPABASE_SERVICE_ROLE_KEY: "svc",
        OPENROUTER_API_KEY: "or",
      },
    });
    return { status: res.status, body: await res.json() };
  };

  const noTags = await driveNoTags();
  assertEq(noTags.status, 200, "a tagless request still succeeds");
  assertEq(
    noTags.body.primary_keyword !== null,
    true,
    `primary_keyword is derived from the model's tags, not left null (got ${JSON.stringify(noTags.body.primary_keyword)})`,
  );
  assertEq(
    typeof noTags.body.keyword_ok,
    "boolean",
    `keyword_ok is a real verdict, not a vacuous pass (got ${JSON.stringify(noTags.body.keyword_ok)})`,
  );
  assertEq(noTags.body.keyword_ok, true, "the keyword genuinely landed, so the verdict is true");

  // The verdict must be EARNED on this path, not vacuous. When the model's own
  // text omits the keyword, the repair puts it there — so `true` here means the
  // repair ran and the keyword really is present in both fields.
  const repairedCase = await driveNoTags({
    metaTitle: "A title with no keyword in it at all",
    metaDescription: "Also nothing relevant here whatsoever, just filler prose.",
  });
  assertEq(
    repairedCase.body.primary_keyword !== null,
    true,
    "the keyword is still resolved when the model's text omits it",
  );
  assertEq(
    repairedCase.body.keyword_ok,
    true,
    "the verdict passes because the repair placed the keyword, not vacuously",
  );
  const kw = repairedCase.body.primary_keyword;
  assertEq(
    repairedCase.body.meta_title.toLowerCase().includes(kw.toLowerCase()),
    true,
    `the keyword is genuinely IN the repaired title (got ${JSON.stringify(repairedCase.body.meta_title)})`,
  );
  assertEq(
    repairedCase.body.meta_description.toLowerCase().includes(kw.toLowerCase()),
    true,
    "the keyword is genuinely IN the repaired description",
  );

  // No tag maps to the vocabulary at all → nothing was verified. That is
  // `null` (unknown), never `true` (success).
  const unmapped = await driveNoTags({ generatedTags: ["a-tag-not-in-the-vocabulary"] });
  assertEq(unmapped.body.primary_keyword, null, "an unmapped tag yields no primary keyword");
  assertEq(
    unmapped.body.keyword_ok,
    null,
    "with no primary keyword the verdict is null (unverified), NOT true (success)",
  );
}

// ── #8320 part 2: metaTitle must never end in a bare separator ─────────────
console.log("");
console.log("metaTitle never returns a value ending in a dangling separator (#8320)");
{
  const { metaTitle } = constraints;
  for (const raw of [
    "Long-term Memory for AI Agents: Recall vs Learning | Tortoise",
    "A reasonably long title that will be truncated somewhere in the middle | Tortoise",
    "Something long enough to truncate and ending in a colon: Tortoise",
    "Something long enough to truncate and ending in a comma, Tortoise",
  ]) {
    const out = metaTitle(raw);
    assertEq(
      /[\s,.;:!?|—-]$/.test(out),
      false,
      `no dangling separator: ${JSON.stringify(out)}`,
    );
    assertEq(out.length <= 60, true, `stays within 60 chars: ${JSON.stringify(out)}`);
  }
  // And the live case that produced the defect, byte for byte.
  assertEq(
    metaTitle("Long-term Memory for AI Agents: Recall vs Learning | Tortoise"),
    "Long-term Memory for AI Agents: Recall vs Learning",
    "the exact live response no longer ends in a bare pipe",
  );
}

console.log("");
if (failures > 0) {
  console.error(`${failures} blog SEO meta assertion(s) failed`);
  process.exitCode = 1;
} else {
  console.log("all blog SEO meta assertions passed");
}
