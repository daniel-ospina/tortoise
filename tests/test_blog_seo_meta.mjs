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
  canonicalKeyword,
  allKeywords,
} from "../website/functions/blog/_shared/seo-keywords.ts";
import { rateLimited, __resetRateLimits } from "../website/functions/blog/_shared/agent-auth.ts";
import { constraints } from "../website/functions/blog/_shared/seo-constraints.ts";
import {
  META_TITLE_MAX,
  META_DESCRIPTION_MAX,
} from "../website/functions/blog/_shared/meta-contract.ts";

let failures = 0;

// ⛔ THE keyword_ok contract, asserted by DERIVING the expected verdict from each
// fixture's own INPUTS rather than from a hand-written flag.
//
// Two earlier versions of this guard were wrong and both are worth naming:
//  1. A `/^[a-z0-9 ]+: /` regex silently ignores hyphens, so for the hyphenated
//     primary "long-term memory for ai agents" a prefixed title could never
//     match and the guard could not fail.
//  2. A hand-written `fired` boolean described whether the TITLE repair ran, but
//     cases like `titleOnly` repair only the DESCRIPTION — the flag was wrong,
//     so the assertion checked the wrong thing and passed by coincidence of the
//     mock's title shape (whose own "Keyword:" prefix mimicked the repair).
//
// Deriving the expectation from the inputs removes the manual flag entirely:
// `keyword_ok` is true exactly when the model's own text, unshortened by the
// length contract, carried the keyword into both returned fields.
const expectedKeywordOk = (rawTitle, rawDescription, kw) => {
  // ⛔ Mirror the CODE's real predicate, not the one that reads nicely. Two
  // subtleties the first version of this helper got wrong, both of which made it
  // agree with the code by accident:
  //   - the handler checks the ENFORCED fields (the contract normalises them),
  //     not the raw ones — identical for short input, different past the cap;
  //   - the verdict is `false` when the REPAIR supplied the keyword, because a
  //     repaired field is not the model's own text. `ensureKeyword` returns its
  //     input unchanged exactly when the text already carries the keyword, so
  //     "the repair did not fire" is exactly "the enforced text carried it".
  // Deriving it from `constraints` rather than hand-copied strings is the point:
  // if a mock default changes, this expectation changes with it.
  const enforced = constraints.enforce({ meta_title: rawTitle, meta_description: rawDescription }, "");
  return (
    rawTitle.trim().length <= META_TITLE_MAX &&
    rawDescription.trim().length <= META_DESCRIPTION_MAX &&
    containsKeyword(enforced.meta_title, kw) &&
    containsKeyword(enforced.meta_description, kw)
  );
};

// A fixture that never reached the handler proves nothing, and a 429 is an
// INFRASTRUCTURE failure rather than a test result. Fail LOUDLY here — the first
// version asserted the status and then dereferenced `body.meta_title` on the 429
// body, so the suite died with a `TypeError` that hid the real cause and silently
// skipped every later assertion. Throwing is deliberate: the run is invalid and
// continuing would report a hazy pass.
const assertLive = (probe, label) => {
  if (probe.status !== 200) {
    throw new Error(
      `fixture "${label}" never reached the handler: HTTP ${probe.status} ${JSON.stringify(probe.body)}`,
    );
  }
};

const assertKeywordOk = (label, probe, rawTitle, rawDescription) => {
  const kw = probe.body.primary_keyword;
  if (kw === null) {
    assertEq(probe.body.keyword_ok, null, `[${label}] no keyword derived, so the verdict is null (unverified)`);
    return;
  }
  const expected = expectedKeywordOk(rawTitle, rawDescription, kw);
  assertEq(
    probe.body.keyword_ok,
    expected,
    `[${label}] verdict must equal the derived expectation ` +
      `(raw=${JSON.stringify([rawTitle.length, rawDescription.length])}, kw=${JSON.stringify(kw)})`,
  );
  if (expected) {
    // A green verdict must mean the artifact is the model's own work — not a
    // repair, and not a truncated fragment.
    // ⛔ Use the module's OWN word-boundary predicate, not `String.includes`. A
    // green verdict implies a boundary match, so a substring check could never
    // fail — a non-falsifiable secondary assertion is worse than none, because it
    // reads as coverage.
    assert(
      containsKeyword(probe.body.meta_title, kw),
      `[${label}] a green verdict implies the returned title carries the keyword at a word boundary`,
    );
  }
};

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
  // A keyword that normalises to nothing would make containsKeyword() return
  // true unconditionally (empty needle = "already present") and ensureKeyword()
  // a silent no-op — the #8320 vacuity, reachable from a data-only edit to
  // keywords.ts. It must be skipped, never returned.
  assert(
    primaryKeyword(["agent-memory"]).length > 0 &&
      normalizeForMatch(primaryKeyword(["agent-memory"])) !== "",
    "the chosen primary never normalises to empty",
  );
  for (const tag of Object.keys(TAG_KEYWORDS)) {
    const first = primaryKeyword([tag]);
    assert(
      first === null || normalizeForMatch(first) !== "",
      `tag "${tag}" yields a usable primary or null, never a term-less string`,
    );
  }
  // ⛔ The guard above passes on the SHIPPED data whether or not it exists,
  //    because no current keyword normalises to empty — so it cannot fail and
  //    proves nothing (caught in review). The guard protects against a
  //    DATA-ONLY edit to keywords.ts, which is the file a keyword-research pass
  //    edits; simulate exactly that by injecting a punctuation-only first
  //    keyword and restoring the array afterwards.
  {
    const tag = "agent-memory";
    const original = TAG_KEYWORDS[tag];
    try {
      TAG_KEYWORDS[tag] = ["…", ...original];
      const picked = primaryKeyword([tag]);
      assert(
        normalizeForMatch(picked) !== "",
        `a punctuation-only first keyword is skipped, not returned (got ${JSON.stringify(picked)})`,
      );
      assertEq(
        picked,
        original[0],
        "...and the next usable keyword is chosen instead",
      );
      // The contains-check treats an empty needle as "already present", so a
      // term-less primary would make the whole guarantee vacuous.
      assertEq(
        containsKeyword("anything at all", "…"),
        true,
        "(this is why: a term-less needle makes containsKeyword vacuously true)",
      );
    } finally {
      TAG_KEYWORDS[tag] = original;
    }
    assertEq(primaryKeyword([tag]), original[0], "the vocabulary is restored after the probe");
  }
}

// ── The seeded candidate list must equal what the validator accepts ────────
console.log("allKeywords — the prompt's candidate list is the whole vocabulary");
{
  const all = allKeywords();
  assert(all.length > 60, `the default is the WHOLE vocabulary, not a capped slice (got ${all.length})`);
  // ⛔ These must measure the VOCABULARY, not `allKeywords()`'s own output.
  // `allKeywords` already dedupes on `normalizeForMatch` and skips
  // empty-normalising terms, so asserting those properties of ITS result
  // compares it against itself and passes whatever the data contains — the
  // exact class of non-falsifiable guard this block was written to catch, left
  // behind in the block itself (caught in review). Read the DATA.
  const vocab = Object.values(TAG_KEYWORDS).flat();
  assertEq(
    new Set(vocab.map(normalizeForMatch)).size,
    vocab.length,
    "the VOCABULARY DATA itself carries no duplicate normalised forms",
  );
  assert(
    vocab.every((k) => normalizeForMatch(k) !== ""),
    "...and no term in the DATA normalises to empty",
  );
  assert(all.every((k) => canonicalKeyword(k) === k),
    "every seeded candidate is a canonical vocabulary term (not merely a normalised match)");
  // Seed and validator must agree in the other direction too: every keyword
  // reachable through a tag is offered. A narrower seed makes the prompt state
  // "choose exactly ONE of these" about a list that is missing the right answer.
  for (const tag of Object.keys(TAG_KEYWORDS)) {
    for (const kw of keywordsFor(tag)) {
      assert(
        all.some((k) => normalizeForMatch(k) === normalizeForMatch(kw)),
        `"${kw}" (from tag "${tag}") is seeded, not silently dropped`,
      );
    }
  }
  // With a cap, round-robin must keep EVERY tag represented — tag insertion
  // order must not decide which tags survive.
  const capped = allKeywords(12);
  assertEq(capped.length, 12, "the cap is honoured");
  const covered = new Set(
    Object.keys(TAG_KEYWORDS).filter((tag) =>
      keywordsFor(tag).some((kw) => capped.some((k) => normalizeForMatch(k) === normalizeForMatch(kw))),
    ),
  );
  assertEq(
    covered.size,
    Object.keys(TAG_KEYWORDS).length,
    `a cap of ${capped.length} still represents all ${Object.keys(TAG_KEYWORDS).length} tags (got ${covered.size})`,
  );
  assertEq(allKeywords(0).length, all.length, "a non-positive cap means unlimited, not empty");
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

  // ⛔ The suffix check above only exercises the APPEND path (the mock writes no
  // suffix), so it cannot see the near-miss corruption: `metaTitle` used to
  // recognise only the EXACT string " | Tortoise", so any other spelling got a
  // SECOND suffix appended — user-visible as "… |Tortoise | Tortoise" while
  // `keyword_ok` stayed green, because the raw title was short (#8446 review,
  // measured at 92% of a near-miss matrix). Assert the canonicalisation directly,
  // and assert it for EVERY spelling rather than one.
  {
    const base = "long-term memory for ai agents";
    // ⛔ A HAND-WRITTEN LIST IS THE BUG — twice over. An 8-entry list of
    // spellings I thought of missed every case with trailing punctuation past a
    // single ".", and the character-list regex it tested missed "," "•" ")" "]"
    // "}" "\u201d" "*" "/" and a trailing -/\u2013/\u2014 (21.2% of the family).
    // Then fixing the regex did NOT fix this test: because the list still held
    // only the 8 handled spellings, the suite stayed GREEN on the broken code —
    // a fix whose test cannot fail is not tested. Enumerate the MATRIX.
    const separators = ["|", "\u2014", "\u2013", "-", "\u00b7", ":", "\u2022", ",", "(", "/"];
    const gaps = ["", " "];
    const casings = ["Tortoise", "tortoise", "TORTOISE"];
    const tails = ["", ".", "..", "!", ",", ";", ":", "?", "\u2026", "\u00b7", "\u2022", ")", "]", "}", "\u201d", "\u2019", "*", "/", "-", "\u2013", "\u2014"];
    const repeats = ["", " | Tortoise", " | Tortoise | Tortoise"];
    let familyChecked = 0;
    for (const sep of separators) {
      for (const gap of gaps) {
        for (const word of casings) {
          for (const tail of tails) {
            for (const repeat of repeats) {
              const variant = `${gap}${sep}${gap === "" ? " " : ""}${word}${tail}${repeat}`;
              const out = constraints.metaTitle(`${base}${variant}`);
              familyChecked++;
              assertEq(out, `${base} | Tortoise`,
                `family suffix ${JSON.stringify(variant)} canonicalises to exactly one`);
              assertEq(constraints.metaTitle(out), out,
                `...and is idempotent, so pass 1 and pass 2 agree for ${JSON.stringify(variant)}`);
            }
          }
        }
      }
    }
        // ⛔ Derive the expected count from the dimensions themselves. A floor like
    // `> 1000` let an entire dimension vanish silently (the real count is 2,520),
    // so the matrix could shrink to a sample while still asserting "it ran".
    assertEq(
      familyChecked,
      separators.length * gaps.length * casings.length * tails.length * repeats.length,
      "the suffix matrix ran EVERY combination of its dimensions, not a sample",
    );
    assert(familyChecked >= 2500, `the suffix family matrix actually ran (${familyChecked} spellings)`);
    // A bare space is NOT a separator — the subject word must SURVIVE. Counting
    // "Tortoise" occurrences would be wrong (the subject word and the brand token
    // are different things), and so is asserting the exact string, because the
    // pre-existing trailing-separator strip legitimately drops a dangling "?".
    // The property being guarded is narrower and is the one that matters: the
    // strip did not DELETE the subject word as if it were the brand suffix.
    for (const real of ["Why Tortoise", "What is Tortoise?"]) {
      const out = constraints.metaTitle(real);
      assert(out.includes(" | Tortoise"), `the canonical suffix is appended once (${JSON.stringify(real)})`);
      assert(out.split(" | Tortoise").slice(0, -1).join(" | Tortoise").includes("Tortoise"),
        `the subject word is NOT deleted as a brand suffix (${JSON.stringify(real)})`);
    }
    // The separator is REQUIRED: a title legitimately ending in the word is not
    // mistaken for the brand suffix.
    assertEq(
      constraints.metaTitle("Why Tortoise"),
      "Why Tortoise | Tortoise",
      "a title ending in the word with no separator is left alone (only the suffix is appended)",
    );
    // ⛔ Idempotency was claimed in a comment and falsified in ~0.16% of random
    // inputs: the old `endsWith` shortcut only held for the exact spelling. The
    // second `enforce` pass runs on the first one's output, so this is load-bearing.
    let nonIdempotent = 0;
    const words = ["memory", "agents", "recall", "graph", "why", "brief", "and", "in", "confidence"];
    for (let seed = 0; seed < 3000; seed++) {
      let t = "";
      let r = seed * 1103515245 + 12345;
      const next = () => (r = (r * 1103515245 + 12345) & 0x7fffffff);
      const target = 20 + (next() % 70);
      while (t.length < target) t += `${words[next() % words.length]} `;
      t = t.trim();
      if (next() % 5 === 0) t += ["|Tortoise", " | Tortoise", ";", "."][next() % 4];
      const once = constraints.metaTitle(t);
      // Count, then assert ONCE — asserting inside the loop made the suite emit
      // 3,000 assertion lines and drowned every other result in the output.
      if (constraints.metaTitle(once) !== once) {
        nonIdempotent++;
        if (nonIdempotent <= 3) console.error(`    non-idempotent input: ${JSON.stringify(t)} → ${JSON.stringify(once)}`);
      }
    }
    assertEq(nonIdempotent, 0, "metaTitle is idempotent across a 3,000-title generated corpus");
    // ⛔ Cap BOUNDARIES. The `!truncated` clause in `keyword_ok` is load-bearing
    // and only tested in the interior; an off-by-one at the cap would be invisible.
    assert(
      constraints.metaTitle("x".repeat(60)).length <= 60,
      "a 60-char title is not treated as over the cap",
    );
    assertEq(
      constraints.metaTitle(`${"a".repeat(55)}${" "}${"b".repeat(4)}`).length <= 60,
      true,
      "a title exactly at the cap stays within it",
    );
    const over = constraints.metaTitle(`${"a".repeat(58)} tail`);
    assert(over.length <= 60, `a 63-char title is shortened (got ${over.length})`);
    assertEq(constraints.metaDescription("d".repeat(155)).length, 155, "a 155-char description is NOT shortened");
    assertEq(constraints.metaDescription("d".repeat(156)).length, 155, "a 156-char description IS shortened to the cap");
  }

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
  // ⛔ `keyword_ok` reports whether the MODEL placed the keyword, so on this
  // default mock — whose title is "Agent Memory 2026: Recall vs Learning" and
  // whose keyword is the tag-derived "long-term memory for ai agents" — the
  // honest answer is FALSE. The repair puts the keyword in (the #8293 guarantee
  // below), but the model did not, and the verdict must say so. Asserting
  // `true` here was the tautology the review found: computed after the repair,
  // the value could never be anything else (#8446 review, P2).
  assertEq(
    noTags.body.keyword_ok,
    false,
    "the model's own text omitted the keyword, so the verdict is false — NOT a tautology",
  );
  assertEq(
    noTags.body.meta_title.toLowerCase().includes(String(noTags.body.primary_keyword).toLowerCase()),
    true,
    "...while the RETURNED title still carries it, because the repair is the #8293 guarantee",
  );

  // The verdict must be as EARNED as the repair. Two cases, opposite verdicts:
  // the model omits the keyword (false), and the model places it (true). If
  // both produced `true`, the field would be measuring the repair, not the model.
  const repairedCase = await driveNoTags({
    metaTitle: "A title with no keyword in it at all",
    metaDescription: "Also nothing relevant here whatsoever, just filler prose.",
  });
  assertLive(repairedCase, "repairedCase");
  assertEq(
    repairedCase.body.primary_keyword !== null,
    true,
    "the keyword is still resolved when the model's text omits it",
  );
  assertEq(
    repairedCase.body.keyword_ok,
    false,
    "the model omitted the keyword, so the repair had to fire and the verdict is false",
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
  // The SAME keyword, model-placed instead of repaired → verdict flips to true.
  // This pair is what makes `keyword_ok` falsifiable rather than tautological.
  const modelPlaced = await driveNoTags({
    metaTitle: "Long-Term Memory for AI Agents: Recall vs Learning",
    metaDescription:
      "Long-term memory for AI agents measures recall, not learning. Tortoise tracks confidence and updates beliefs when information conflicts.",
  });
  assertLive(modelPlaced, "modelPlaced");
  assertEq(
    modelPlaced.body.keyword_ok,
    true,
    "...and when the MODEL places the same keyword, the verdict is true — so it distinguishes the two",
  );
  // ⛔ The DESC conjunct is load-bearing and was untested: every `true` case in
  // this suite had the keyword in BOTH fields, so dropping the description half
  // was a no-op (0 failures — caught in review). This case has it in the title
  // only, which is what tells "the model filled both fields it was asked for"
  // apart from "it filled one".
  const titleOnly = await driveNoTags({
    metaTitle: "Long-Term Memory for AI Agents: Recall vs Learning",
    metaDescription: "A description about confidence tracking that names no keyword at all.",
  });
  assertLive(titleOnly, "titleOnly");
  assertEq(
    titleOnly.body.keyword_ok,
    false,
    "the keyword is in the title but NOT the description, so the verdict is false",
  );
  assertEq(
    titleOnly.body.primary_keyword,
    primaryKeyword(["agent-memory"]),
    "...the keyword itself is still resolved from the model's tags",
  );
  // The same invariant as the later block, over these cases: a green verdict
  // never accompanies a title the repair prefixed. This is the negative control
  // for the round-4 P1, where computing the verdict on the model's discarded raw
  // output produced exactly that pair.
  assertKeywordOk("noTags", noTags, "Agent Memory 2026: Recall vs Learning | Tortoise",
    "Agent memory systems measure recall, not learning. Tortoise tracks confidence and updates beliefs when information conflicts.");
  assertKeywordOk("repairedCase", repairedCase, "A title with no keyword in it at all",
    "Also nothing relevant here whatsoever, just filler prose.");
  assertKeywordOk("titleOnly", titleOnly, "Long-Term Memory for AI Agents: Recall vs Learning",
    "A description about confidence tracking that names no keyword at all.");
  assertKeywordOk("modelPlaced", modelPlaced, "Long-Term Memory for AI Agents: Recall vs Learning",
    "Long-term memory for AI agents measures recall, not learning. Tortoise tracks confidence and updates beliefs when information conflicts.");

  // No tag maps to the vocabulary at all → nothing was verified. That is
  // `null` (unknown), never `true` (success).
  const unmapped = await driveNoTags({ generatedTags: ["a-tag-not-in-the-vocabulary"] });
  assertEq(unmapped.body.primary_keyword, null, "an unmapped tag yields no primary keyword");
  assertLive(unmapped, "unmapped");
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

// ── #8445: the model must be ASKED for the keyword, not told nothing ───────
// On the tagless path the old prompt said "No mapped keywords — derive terms
// from the content itself", so the model wrote a fluent title with no keyword
// and the repair bolted one on the front as "<keyword>: <title>", truncating it.
console.log("");
console.log("the tagless prompt SEEDS candidate keywords and honours the model's choice (#8445)");
{
  const { onRequestPost } = await import("../website/functions/blog/api/generate-seo.ts");

  let seenPrompt = "";
  const drive = async (opts = {}) => {
    seenPrompt = "";
    globalThis.fetch = async (url, init) => {
      const u = String(url);
      if (u.includes("/rest/v1/blog_agent_keys")) {
        return Response.json([{ agent_name: "pipeline", active: true }]);
      }
      if (u.includes("openrouter.ai")) {
        try {
          seenPrompt = JSON.parse(String(init?.body ?? "{}")).messages.map((m) => m.content).join("\n");
        } catch {
          seenPrompt = "";
        }
        return Response.json({
          choices: [{
            message: {
              content: JSON.stringify({
                slug: "a-slug",
                excerpt: "A concrete excerpt that earns the click.",
                tags: opts.generatedTags ?? ["agent-memory"],
                meta_title: opts.metaTitle ?? "Agent Memory 2026: Recall vs Learning | Tortoise",
                meta_description: opts.metaDescription ??
                  "Agent memory systems measure recall, not learning. Tortoise tracks confidence and updates beliefs when information conflicts.",
                ...(opts.primaryKeyword === undefined ? {} : { primary_keyword: opts.primaryKeyword }),
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
      body: JSON.stringify({
        title: "A title",
        body: "Sentence. ".repeat(40),
        // `requestTags` is deliberately distinct from `generatedTags` (what the
        // mock MODEL returns): one is the caller's input, the other the model's
        // output, and a fixture that confuses them proves nothing.
        ...(opts.requestTags === undefined ? {} : { tags: opts.requestTags }),
      }),
    });
    const res = await onRequestPost({
      request,
      env: { SUPABASE_URL: "https://db.test", SUPABASE_SERVICE_ROLE_KEY: "svc", OPENROUTER_API_KEY: "or" },
    });
    return { status: res.status, body: await res.json() };
  };

  // 1. The prompt must SHOW the model the candidates. Asking for a keyword the
  //    model was never shown is the defect this fixes.
  await drive();
  assert(
    seenPrompt.includes("long-term memory for ai agents"),
    "the tagless prompt seeds real candidate keywords",
  );
  assert(
    /choose exactly ONE/i.test(seenPrompt),
    "the tagless prompt instructs the model to choose one keyword",
  );
  assert(
    !seenPrompt.includes("No mapped keywords"),
    "the old do-nothing instruction is gone",
  );
  // The instruction must explicitly forbid the colon-prefix shape the repair
  // used to produce, or the model will reproduce it.
  assert(
    /natural phrase rather than prefixed with a colon/i.test(seenPrompt),
    "the prompt forbids the colon-prefixed shape the repair produced",
  );

  // 2. When the model HONOURS that instruction, the repair must not fire:
  //    the title stays fluent and the verified keyword is the one it chose.
  //
  //    ⛔ The model's choice is DELIBERATELY different from the tag-derived
  //    fallback. With the same value, honouring the choice and ignoring it are
  //    indistinguishable — the whole block stayed green when the override was
  //    deleted, which is precisely the defect this PR exists to remove
  //    (caught in review). "epistemic memory" is a real keyword from another
  //    tag; the default generated tag is "agent-memory", whose primary is
  //    "long-term memory for ai agents".
  const fluent = await drive({
    primaryKeyword: "epistemic memory",
    metaTitle: "Epistemic Memory: Why Confidence Matters | Tortoise",
    metaDescription:
      "Epistemic memory records why a belief changed. Tortoise tracks confidence and updates beliefs when information conflicts.",
  });
  assertLive(fluent, "fluent");
  assertEq(fluent.body.primary_keyword, "epistemic memory", "the model's chosen keyword is the verified one");
  assert(
    fluent.body.primary_keyword !== primaryKeyword(["agent-memory"]),
    `the model's choice differs from the tag-derived fallback, so this assertion CAN fail (fallback was ${JSON.stringify(primaryKeyword(["agent-memory"]))})`,
  );
  assertEq(fluent.body.keyword_ok, true, "the keyword genuinely landed");
  assertEq(
    fluent.body.meta_title.startsWith("epistemic memory: "),
    false,
    `the repair did NOT bolt the keyword on (got ${JSON.stringify(fluent.body.meta_title)})`,
  );
  assert(
    fluent.body.meta_title.toLowerCase().includes("epistemic memory"),
    "...and the title still carries the keyword, written naturally",
  );

  // 2b. ⛔ The model's CLAIM is not evidence it USED the keyword. A model that
  //     returns a valid keyword it never wrote into either field must not have
  //     it honoured — otherwise the repair force-prefixes it and we are back to
  //     "<keyword>: <its title>" with a green verdict, which is #8445 itself
  //     reached through a different door (#8446 review, P1). Vocabulary
  //     membership proves the string is OURS; only the generated text proves the
  //     model used it.
  const claimsUnused = await drive({
    primaryKeyword: "epistemic memory",
    metaTitle: "Knowledge Graph RAG: A Practical Guide | Tortoise",
    metaDescription:
      "A practical guide to knowledge graph retrieval augmented generation for production systems.",
  });
  assertLive(claimsUnused, "claimsUnused");
  assert(
    claimsUnused.body.primary_keyword !== "epistemic memory",
    `a claimed keyword the model never wrote is NOT honoured (got ${JSON.stringify(claimsUnused.body.primary_keyword)})`,
  );
  assertEq(
    claimsUnused.body.primary_keyword,
    primaryKeyword(["agent-memory"]),
    "...and the tag-derived fallback is used instead",
  );
  assertEq(
    claimsUnused.body.keyword_ok,
    false,
    "...with an honest false verdict, not the green one a force-prefix used to produce",
  );
  assert(
    !claimsUnused.body.meta_title.startsWith("epistemic memory: "),
    `...and the unused keyword is not prefixed onto the title (got ${JSON.stringify(claimsUnused.body.meta_title)})`,
  );

  // 2c. The "used" test covers EITHER generated field, deliberately. A model
  //     that places the keyword only in the description has still used it, and
  //     treating that as "unused" would fall back to a DIFFERENT keyword and
  //     repair the title with it — two keywords in one post, one of them
  //     unverified, which is the exact failure #8445's fix direction warns about.
  //     The repair then prefixes the SAME keyword into the title, and the verdict
  //     is honestly false because the model did not put it there.
  const descriptionOnly = await drive({
    primaryKeyword: "epistemic memory",
    metaTitle: "Why Confidence Matters More Than Recall | Tortoise",
    metaDescription:
      "Epistemic memory records why a belief changed, and why that matters more than recall alone.",
  });
  assertLive(descriptionOnly, "descriptionOnly");
  assertEq(
    descriptionOnly.body.primary_keyword,
    "epistemic memory",
    "a keyword the model used in EITHER field is honoured — not swapped for a second keyword",
  );
  assertEq(
    descriptionOnly.body.keyword_ok,
    false,
    "...while the verdict stays false: the model did not put it in the title, the repair did",
  );
  assert(
    descriptionOnly.body.meta_title.startsWith("epistemic memory: "),
    `...and the repair prefixed the SAME keyword into the title (got ${JSON.stringify(descriptionOnly.body.meta_title)})`,
  );
  assert(
    !descriptionOnly.body.meta_title.includes(String(primaryKeyword(["agent-memory"]))),
    "...so the tag-derived fallback keyword appears NOWHERE: one article, one primary keyword",
  );

  // 2d. ⛔ The invariant that matters: `keyword_ok: true` must NEVER accompany a
  //     returned title that the repair prefixed — that is #8445's indicator 1,
  //     and the whole reason this field exists. Here the model places the
  //     keyword but the title is over the 60-char cap, so the length contract
  //     truncates it away and the repair re-front-loads it.
  //
  //     The verdict is FALSE, conservatively: the returned field needed help.
  //     Computing it on the model's discarded raw output instead made this case
  //     return `keyword_ok: true` next to a colon-prefixed title (~7.5% of
  //     randomised tagless requests), and THIS ASSERTION previously pinned that
  //     broken pair as the expected contract (#8446 review, round 4). A
  //     needless regeneration is cheap; publishing a broken title is not.
  const overBudget = await drive({
    primaryKeyword: "epistemic memory",
    metaTitle: "A practical guide to graph memory for teams building agents with epistemic memory",
    metaDescription: "A description placing the term epistemic memory deliberately at the very end again.",
  });
  assertLive(overBudget, "overBudget");
  assertEq(
    overBudget.body.primary_keyword,
    "epistemic memory",
    "the model placed the keyword, so its claim is honoured...",
  );
  assertEq(
    overBudget.body.keyword_ok,
    false,
    "...but the verdict is conservatively FALSE: the model's title was over the cap",
  );
  assert(
    overBudget.body.meta_title.startsWith("epistemic memory: "),
    `...the returned title is indeed repaired/truncated (got ${JSON.stringify(overBudget.body.meta_title)})`,
  );
  // ⛔ THE round-5 case, and the reason the truncation check exists. The prompt
  //    tells the model to front-load the keyword, so a model that OVERSHOOTS the
  //    cap keeps its keyword through the cut — and the `enforced` check alone
  //    stayed GREEN on a title truncated mid-phrase:
  //
  //      returned   : "Epistemic memory explains why a belief changed in | Tortoise"
  //      keyword_ok : true      repair fired: no      contract truncated: YES
  //
  //    The `overBudget` fixture above cannot catch this: it puts the keyword at
  //    the END, so truncation removes it and the enforced check fires anyway.
  //    Measured at 68% of green verdicts on randomised input before the fix.
  const frontLoaded = await drive({
    primaryKeyword: "epistemic memory",
    metaTitle: "Epistemic memory explains why a belief changed in production agent systems today",
    metaDescription: "A description within its own cap that names epistemic memory early on.",
  });
  assertLive(frontLoaded, "frontLoaded");
  assertEq(
    frontLoaded.body.primary_keyword,
    "epistemic memory",
    "...the front-loaded keyword is still the model's choice",
  );
  assertEq(
    frontLoaded.body.keyword_ok,
    false,
    "a title the length contract cut mid-phrase is NOT the model's text, so the verdict is false",
  );
  assert(
    frontLoaded.body.meta_title.toLowerCase().includes("epistemic memory"),
    "...even though the keyword DID survive the cut (this is what the enforced-only check missed)",
  );
  assert(
    frontLoaded.body.meta_title.length <= 60,
    `...and the returned title really is at the cap (got ${frontLoaded.body.meta_title.length})`,
  );

  // The description half of the truncation check needs its own fixture: a model
  // description past the 155-char cap that KEEPS the keyword through the cut.
  // Without one, dropping that half of the check was a no-op for the suite.
  const longDescription = await drive({
    primaryKeyword: "epistemic memory",
    metaTitle: "Epistemic Memory: Why Beliefs Change",
    metaDescription:
      `Epistemic memory records why a belief changed, and this sentence exists purely to push the ` +
      `description past its cap so the length contract has to cut it somewhere after the keyword.`,
  });
  assertLive(longDescription, "longDescription");
  assert(
    longDescription.body.meta_description.length <= 155,
    `...the description really is at its cap (got ${longDescription.body.meta_description.length})`,
  );
  assert(
    longDescription.body.meta_description.toLowerCase().includes("epistemic memory"),
    "...and the keyword survived the cut, so only the truncation check can catch it",
  );
  assertEq(
    longDescription.body.keyword_ok,
    false,
    "a description the length contract cut is not the model's text, so the verdict is false",
  );

  // ⛔ CAP BOUNDARIES for the `!truncated` clause. Its assertions above test
  //    `constraints` directly, so an off-by-one in `generate-seo.ts`'s own
  //    comparison was invisible: mutating `>` to `>=` (or to `> cap + 1`) left
  //    the suite green. These two fixtures sit exactly ON the caps.
  //
  //    Exactly 60 chars → NOT truncated → green; with `>=` it would be false.
  const titleAtCap = "Long-Term Memory for AI Agents Explained in Depth Right Now!";
  assertEq(titleAtCap.length, 60, "(fixture precondition: the title is exactly the cap)");
  // Reach the handler, not a 429 — a rate-limited body makes every assertion below vacuous.
  const atCap = await drive({
    primaryKeyword: "long-term memory for ai agents",
    metaTitle: titleAtCap,
    metaDescription: "A description naming long-term memory for ai agents comfortably within its own cap.",
  })
  assertLive(atCap, "atCap");
  assertEq(
    atCap.body.keyword_ok,
    true,
    "a title EXACTLY at the cap is not truncated, so the verdict stays true",
  );

  //    One char OVER the title cap → truncated → false; with `> cap + 1` it would
  //    be true. The keyword sits at the FRONT so it survives the cut and the
  //    enforced check cannot catch this — only the cap comparison can.
  const titleOverCap = "Long-Term Memory for AI Agents Explained in Depth Right Now!!";
  assertEq(titleOverCap.length, 61, "(fixture precondition: the title is one char over the cap)");
  // Reach the handler, not a 429 — a rate-limited body makes every assertion below vacuous.
  const overTitle = await drive({
    primaryKeyword: "long-term memory for ai agents",
    metaTitle: titleOverCap,
    metaDescription: "A description naming long-term memory for ai agents comfortably within its own cap.",
  })
  assertLive(overTitle, "overTitle");
  assert(
    overTitle.body.meta_title.toLowerCase().includes("long-term memory for ai agents"),
    "...the keyword really does survive the truncation, so only the cap check catches it",
  );
  assertEq(
    overTitle.body.keyword_ok,
    false,
    "a title one char OVER the cap is truncated, so the verdict is false",
  );

  //    Exactly 155 chars → NOT truncated → green; with `>=` it would be false.
  //    WITHOUT this fixture the description half of the `!truncated` clause was
  //    untested: the title fixtures masked `>` → `>=` for the description, so the
  //    combined mutation still failed — via the title only (caught by the
  //    verifier, not by me).
  const descAtCap = 'long-term memory for ai agents records why a belief changed, compared with recall-only systems that x x x x x x x x x x x x x x x x x x x x x x x x x x x x';
  assertEq(descAtCap.length, 155, "(fixture precondition: the description is exactly the cap)");
  // Reach the handler, not a 429 — a rate-limited body makes every assertion below vacuous.
  const descAtCapCase = await drive({
    primaryKeyword: "long-term memory for ai agents",
    metaTitle: "Long-Term Memory for AI Agents",
    metaDescription: descAtCap,
  })
  assertLive(descAtCapCase, "descAtCapCase");
  assertEq(
    descAtCapCase.body.keyword_ok,
    true,
    "a description EXACTLY at the cap is not truncated, so the verdict stays true",
  );

  //    Exactly 156 chars → truncated → false; with `> cap + 1` it would be true.
  const longDesc = `Long-term memory for ai agents ${"x".repeat(156 - "Long-term memory for ai agents ".length)}`;
  assertEq(longDesc.length, 156, "(fixture precondition: the description is one char over the cap)");
  // Reach the handler, not a 429 — a rate-limited body makes every assertion below vacuous.
  const overDesc = await drive({
    primaryKeyword: "long-term memory for ai agents",
    metaTitle: "Long-Term Memory for AI Agents",
    metaDescription: longDesc,
  })
  assertLive(overDesc, "overDesc");
  assertEq(
    overDesc.body.keyword_ok,
    false,
    "a description one char OVER the cap is truncated, so the verdict is false",
  );

  assertKeywordOk("fluent", fluent, "Epistemic Memory: Why Confidence Matters | Tortoise",
    "Epistemic memory records why a belief changed. Tortoise tracks confidence and updates beliefs when information conflicts.");
  assertKeywordOk("descriptionOnly", descriptionOnly, "Why Confidence Matters More Than Recall | Tortoise",
    "Epistemic memory records why a belief changed, and why that matters more than recall alone.");
  assertKeywordOk("overBudget", overBudget,
    "A practical guide to graph memory for teams building agents with epistemic memory",
    "A description placing the term epistemic memory deliberately at the very end again.");
  // A padded claim normalises onto a vocabulary term (the vocabulary check
  // normalises), so if the model also WRITES it the claim would be honoured and
  // echoed back at arbitrary length. An over-long claim is rejected outright.
  const paddedKw = `${"epistemic"}${".".repeat(200)}${"memory"}`;
  // ⛔ Reset FIRST. This fixture used to sit at exactly the 20-request burst
  // ceiling, so it was one fixture away from a silent 429 — and on a 429 body the
  // two assertions below passed anyway (String(undefined).length <= 40 ✓,
  // undefined !== paddedKw ✓). Proven: with burst=19 the over-long-claim guard
  // mutation is MISSED unless the status assertion is present. Asserting the
  // status closed the hole; resetting here keeps the fixture off the boundary so
  // the hole cannot reopen as the suite grows.
  __resetRateLimits();
  const padded = await drive({
    primaryKeyword: paddedKw,
    metaTitle: `A guide to ${paddedKw}`,
    metaDescription: `A description that mentions ${paddedKw} somewhere in it.`,
  });
  assertLive(padded, "over-long claim");
  // ⛔ The mechanism is CANONICALISATION, not rejection — the earlier comment
  // said "rejected outright", and `length <= 40` + `!== paddedKw` pass under BOTH
  // mechanisms, so they pinned neither. `canonicalKeyword` normalises the padding
  // away (`"epistemic" + 200 dots + "memory"` -> the vocabulary term), which is
  // what bounds the value. Assert the term, which only canonicalisation produces.
  assertEq(
    padded.body.primary_keyword,
    "epistemic memory",
    "an over-long claim is canonicalised to the vocabulary term it normalises to",
  );

  // ⛔ A present-but-malformed `tags` must be REFUSED, not silently become `[]`.
  // `[]` is the TAGLESS path — it seeds the whole vocabulary and lets the model's
  // own choice become the verified keyword — so a caller passing a string used to
  // be walked into the path reserved for callers who expressed no position, with
  // no way to tell from the response.
  {
    __resetRateLimits();
    // ⛔ A non-ARRAY was the first half; an ARRAY of non-strings is the same
    // mistake and was only half-closed — the filter dropped them, so `[123]`
    // became `[]` (the tagless path) and the caller earned the model its own
    // verified keyword. Every element must be a string.
    for (const bad of ["agent-memory", [123], [null], [{}], ["agent-memory", 123]]) {
      __resetRateLimits();
      const malformed = await drive({ requestTags: bad });
      assertEq(malformed.status, 400,
        `malformed \`tags\` ${JSON.stringify(bad)} is refused, not coerced to []`);
      assertEq(malformed.body.error, "validation", "...with the validation error code");
    }
  }

  // ⛔ P1-2 (round 9): the SECOND trailing-separator strip was untested. Every
  // other fixture ends in the word `Tortoise`, so `BRAND_SUFFIX_TAIL_RE` consumes
  // it and the bare-separator strip never runs — deleting that line left the
  // suite green, and a raw title ending in `" |"` came back as `"… | | Tortoise"`
  // (a doubled separator) beside `keyword_ok: true`.
  {
    __resetRateLimits();
    const bareSeparator = await drive({
      primaryKeyword: "long-term memory for ai agents",
      metaTitle: "long-term memory for ai agents guide |",
      metaDescription: "Long-term memory for ai agents measures recall, not learning.",
    });
    assertLive(bareSeparator, "bareSeparator");
    assertEq(
      bareSeparator.body.meta_title,
      "long-term memory for ai agents guide | Tortoise",
      "a title ending in a bare separator gets ONE suffix, not '| | Tortoise'",
    );
  }

  // ⛔ P2-2 (round 9): the empty-after-strip guard was untested. A title that is
  // ONLY a brand suffix strips to "" — shipping it would publish a suffix-only
  // <title>; the guard turns it into the honest 502 instead.
  {
    __resetRateLimits();
    const suffixOnly = await drive({ metaTitle: " | Tortoise" });
    assertEq(suffixOnly.status, 502, "a title that is only the brand suffix is refused, not published");
    assertEq(suffixOnly.body.error, "generation_incomplete", "...as an incomplete generation");
  }

  // ⛔ P1-1 (round 9): the TITLE branch of `chosenUsed`'s OR was untested.
  // `titleOnly` above does NOT cover it — it asserts the FALLBACK keyword, which
  // is identical whether or not the title branch exists, so deleting
  // `containsKeyword(rawTitle, chosen) ||` left the suite GREEN and reproduced
  // #8445 (the model's own choice discarded, the title repair-prefixed with a
  // DIFFERENT keyword). The distinguishing assertion is `primary_keyword`: it is
  // the model's choice ONLY because the title carried it.
  {
    __resetRateLimits();
    const titleBearing = await drive({
      primaryKeyword: "epistemic memory",
      metaTitle: "Epistemic Memory: Why Confidence Matters More Than Recall",
      metaDescription: "A description that names no keyword at all, so the repair must fire here.",
    });
    assertLive(titleBearing, "titleBearing");
    assertEq(
      titleBearing.body.primary_keyword,
      "epistemic memory",
      "a keyword the model used in the TITLE is honoured — the title branch is load-bearing",
    );
    assert(
      titleBearing.body.meta_title.toLowerCase().includes("epistemic memory"),
      "...and the model's own title is kept, not repair-prefixed with a different keyword",
    );
    assertEq(
      titleBearing.body.keyword_ok,
      false,
      "...but the description still needed the repair, so the verdict is honestly false",
    );
  }

  // ⛔ `keyword_ok` is computed on the artifact the caller RECEIVES. `enforced`
  // is the first contract pass; the response carries `repaired`, the second. A
  // non-idempotent contract made them differ and the verdict then described a
  // field the caller never saw. The `!repairFired` clause is what prevents that
  // AND keeps the check from being a tautology (the repair guarantees presence).
  // Both halves get a fixture: the keyword the model wrote, and the keyword the
  // repair had to add.
  {
    __resetRateLimits();
    const wroteIt = await drive({
      primaryKeyword: "long-term memory for ai agents",
      metaTitle: "Long-term memory for AI agents",
      metaDescription: "Long-term memory for ai agents measures recall, not learning, and tracks confidence instead.",
    });
    assertLive(wroteIt, "wroteIt");
    assertEq(wroteIt.body.keyword_ok, true, "the model's OWN placement is green");
    assertEq(wroteIt.body.meta_title, "Long-term memory for AI agents | Tortoise",
      "...and the returned title is the model's own text plus the one canonical suffix");
    // The SAME request, but the model never writes the keyword: the repair adds it.
    __resetRateLimits();
    const repairAdded = await drive({
      primaryKeyword: "long-term memory for ai agents",
      metaTitle: "A title with no keyword in it at all",
      metaDescription: "A description with no keyword in it either.",
    });
    assertLive(repairAdded, "repairAdded");
    assert(repairAdded.body.meta_title.toLowerCase().includes("long-term memory for ai agents"),
      "...the repair did add the keyword (otherwise this fixture proves nothing)");
    assertEq(repairAdded.body.keyword_ok, false,
      "but a REPAIRED field is not the model's own text, so the verdict is NOT green");
  }

  // ⛔ `primary_keyword` is documented as the keyword the response was VERIFIED
  // against, and callers compare it against TAG_KEYWORDS/tags. Vocabulary
  // membership normalises, so the model's own casing/spacing/punctuation was
  // accepted and echoed — matching nothing lookupable, and letting one keyword
  // come back two ways.
  for (const spelling of [
    "LONG-TERM MEMORY FOR AI AGENTS",
    "Long  Term  Memory  for  AI  Agents",
    "long-term memory for ai agents!!",
  ]) {
    __resetRateLimits();
    const cased = await drive({
      primaryKeyword: spelling,
      metaTitle: "Long-Term Memory for AI Agents: Recall vs Learning",
      metaDescription: "Long-term memory for ai agents measures recall, not learning.",
    });
    assertEq(cased.status, 200, `the spelling fixture reached the handler (${spelling})`);
    assertEq(cased.body.primary_keyword, "long-term memory for ai agents",
      `primary_keyword is returned in the researched spelling, not the model's (${JSON.stringify(spelling)})`);
  }

  assertKeywordOk("claimsUnused", claimsUnused, "Knowledge Graph RAG: A Practical Guide | Tortoise",
    "A practical guide to knowledge graph retrieval augmented generation for production systems.");

  // 3. ⛔ The model must not be able to introduce a keyword the research never
  //    produced. An unknown choice falls back to the tag-derived keyword.
  //
  //    Rate limiter reset for the same reason as elsewhere in this block: the
  //    fixtures above spend the per-window budget (AGENT_RATE, 20/min), and a
  //    429 surfaces here as an unrelated failure in the middle of the reasoning.
  __resetRateLimits();
  const invented = await drive({
    primaryKeyword: "make money fast with ai agents",
    generatedTags: ["agent-memory"],
    metaTitle: "Make money fast with AI agents today",
    metaDescription: "A description that does not happen to contain the real keyword either.",
  });
  assertLive(invented, "invented");
  assertEq(
    invented.body.primary_keyword,
    primaryKeyword(["agent-memory"]),
    "an unvalidated keyword is rejected and the researched one is used",
  );

  // 4. A model that says nothing usable still falls back correctly.
  //
  //    The rate limiter is reset here for the same reason as in block 5: the
  //    assertions below are about keyword resolution, and a 429 from the budget
  //    spent by the fixtures above would surface as an unrelated failure in the
  //    middle of that reasoning (it did, while the cap-boundary fixtures were
  //    being added).
  __resetRateLimits();
  const silent = await drive({ primaryKeyword: null });
  assertEq(
    silent.body.primary_keyword,
    primaryKeyword(["agent-memory"]),
    "a null choice falls back to the tag-derived keyword",
  );
  assertLive(silent, "silent");

  // 5. The tag-SUPPLIED path is unchanged: the caller's intent wins and the
  //    prompt names their keyword rather than a candidate list.
  //
  //    ⛔ Reset the agent rate limiter first. It is per-key per-window
  //    (AGENT_RATE, 20/min) and the blocks above spend most of that budget, so
  //    adding requests earlier in this file silently starved THIS block with a
  //    429 — surfacing as four unrelated assertion failures that had nothing to
  //    do with the change (found while adding the cap-boundary fixtures). The
  //    limiter is imported for exactly this; not calling it here was the bug.
  __resetRateLimits();
  let suppliedPrompt = "";
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    if (u.includes("/rest/v1/blog_agent_keys")) return Response.json([{ agent_name: "pipeline", active: true }]);
    if (u.includes("openrouter.ai")) {
      try {
        suppliedPrompt = JSON.parse(String(init?.body ?? "{}")).messages.map((m) => m.content).join("\n");
      } catch { suppliedPrompt = ""; }
      return Response.json({
        choices: [{ message: { content: JSON.stringify({
          slug: "a-slug", excerpt: "An excerpt.", tags: ["agent-memory"],
          meta_title: "Long-Term Memory for AI Agents: Recall vs Learning | Tortoise",
          meta_description: "Long-term memory for AI agents measures recall but not learning, and Tortoise tracks confidence instead.",
          // ⛔ DELIBERATELY a DIFFERENT, VALID keyword from another tag. The
          // caller's tags are an explicit statement of intent, so this must NOT
          // override them. Omitting the field here made this assertion vacuous
          // and let exactly that regression through (caught in review).
          primary_keyword: "agent memory architecture",
        }) } }],
        usage: { prompt_tokens: 10, completion_tokens: 20 },
      });
    }
    return Response.json({});
  };
  const suppliedRes = await onRequestPost({
    request: new Request("https://tortoise.premiselabs.co/blog/api/generate-seo", {
      method: "POST",
      headers: { "X-Agent-Key": "test-key", "Content-Type": "application/json" },
      body: JSON.stringify({ title: "A title", body: "Sentence. ".repeat(40), tags: ["agent-memory"] }),
    }),
    env: { SUPABASE_URL: "https://db.test", SUPABASE_SERVICE_ROLE_KEY: "svc", OPENROUTER_API_KEY: "or" },
  });
  const supplied = await suppliedRes.json();
  assert(
    /PRIMARY keyword: "long-term memory for ai agents"/.test(suppliedPrompt),
    "with tags, the prompt still names the caller's keyword explicitly",
  );
  assert(
    !/choose exactly ONE/i.test(suppliedPrompt),
    "with tags, no candidate list is seeded — the caller's intent governs",
  );
  assertEq(
    supplied.primary_keyword,
    primaryKeyword(["agent-memory"]),
    "with tags, the caller's tag-derived keyword is still the one verified",
  );
  // The override must not fire even though the model returned a VALID keyword.
  assert(
    supplied.primary_keyword !== "agent memory architecture",
    "a model-chosen keyword does NOT override the caller's tags",
  );
  assertEq(
    canonicalKeyword("agent memory architecture") !== null,
    true,
    "(the ignored keyword was itself valid — the guard is about precedence, not validity)",
  );

  // 6. ⛔ A CALLER-SUPPLIED FREE-FORM tag is not the tagless path. "kubernetes"
  //    is a legitimate tag that simply maps to nothing in the vocabulary, so
  //    `primaryKeyword` is null — but the caller HAS expressed intent, the
  //    prompt offers no candidate list, and a model-chosen keyword must not be
  //    forced into the generated title. Gating the override on
  //    `promptPrimary === null` instead of `tags.length === 0` did exactly that
  //    and reported `keyword_ok: true`, i.e. an unearned guarantee (caught in
  //    ⛔ The mock's model BOTH claims AND USES a real keyword, deliberately.
  //    With a claim it does not use, the "was it actually used" check absorbs
  //    the gate bug and this test passes either way — verified: reverting the
  //    gate to `promptPrimary === null` gave 0 failures. Exercising the two
  //    conditions independently is the only way each stays falsifiable.
  //
  //    review). The honest answer here is null: nothing was verified.
  let freeformPrompt = "";
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    if (u.includes("/rest/v1/blog_agent_keys")) return Response.json([{ agent_name: "pipeline", active: true }]);
    if (u.includes("openrouter.ai")) {
      try {
        freeformPrompt = JSON.parse(String(init?.body ?? "{}")).messages.map((m) => m.content).join("\n");
      } catch {
        freeformPrompt = "";
      }
      return Response.json({
        choices: [{ message: { content: JSON.stringify({
          slug: "a-slug",
          excerpt: "A concrete excerpt that earns the click.",
          tags: ["kubernetes"],
          meta_title: "Agent Memory Architecture Explained",
          meta_description: "Agent memory architecture for teams running Kubernetes operators.",
          primary_keyword: "agent memory architecture",
        }) } }],
        usage: { prompt_tokens: 10, completion_tokens: 20 },
      });
    }
    return Response.json({});
  };
  const freeformRes = await onRequestPost({
    request: new Request("https://tortoise.premiselabs.co/blog/api/generate-seo", {
      method: "POST",
      headers: { "X-Agent-Key": "test-key", "Content-Type": "application/json" },
      body: JSON.stringify({ title: "A title", body: "Sentence. ".repeat(40), tags: ["kubernetes"] }),
    }),
    env: { SUPABASE_URL: "https://db.test", SUPABASE_SERVICE_ROLE_KEY: "svc", OPENROUTER_API_KEY: "or" },
  });
  const freeform = await freeformRes.json();
  // ⛔ The prompt and the handler must agree about what "tagless" means. The
  //    seeding and the override are two separate expressions of one condition;
  //    if they drift, the prompt offers a choice the code then discards and the
  //    model is asked to do work that cannot matter (#8446 review, P2). Asserted
  //    on the PROMPT, not just the response, so the drift is visible.
  assert(
    !/choose exactly ONE/i.test(freeformPrompt),
    "with an unmapped CALLER tag, the prompt does NOT offer a candidate choice the handler would discard",
  );
  assert(
    /do not map to a researched keyword/i.test(freeformPrompt),
    "...it says why instead, rather than falling back to the old do-nothing line",
  );
  assertEq(
    freeform.primary_keyword,
    null,
    "an unmapped CALLER tag yields no primary keyword — the model's choice does not fill the gap",
  );
  assertEq(freeform.keyword_ok, null, "...and keyword_ok is null (nothing verified), never true");
  assertEq(
    freeform.meta_title,
    "Agent Memory Architecture Explained | Tortoise",
    "...and the generated title is left as the model wrote it — no keyword prefix, no rewrite " +
      "(the ` | Tortoise` suffix is the length contract's own, not the repair's)",
  );
}

console.log("");
if (failures > 0) {
  console.error(`${failures} blog SEO meta assertion(s) failed`);
  process.exitCode = 1;
} else {
  console.log("all blog SEO meta assertions passed");
}
