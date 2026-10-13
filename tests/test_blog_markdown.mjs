// Behavioral tests for the blog SSR markdown renderer (#4481).
//
// Runs the zero-dependency renderMarkdown() from the edge helper under Node
// with --experimental-strip-types — real assertions on the markdown subset the
// blog actually ships.
//
// Regression: the canonical body comes from the TipTap editor, whose serializer
// (prosemirror-markdown `esc()`) escapes ASCII punctuation — notably `~`, since
// `~~` is GFM strikethrough. `\~` is CORRECT CommonMark, but the SSR renderer
// implemented no backslash-escape processing, so the escape leaked into the
// published page as a literal "\~" (seen on /blog/types-of-agent-memory, where
// `~` is the "partial capability" legend marker in the summary table).
//
// Run (Node >= 22.6):
//   node --experimental-strip-types tests/test_blog_markdown.mjs

import { readFileSync } from "node:fs";

import { renderMarkdown, extractFaq, faqPageJsonLd, articleJsonLd, jsonLd, extractVideo, validHref } from "../website/functions/blog/_lib.ts";
import { VIDEO_EMBED_ORIGINS } from "../website/functions/blog/_shared/video.ts";
// The marketing project's policy — the one `blog/_lib.ts` `ok()` stamps on every
// article response, so it is the policy that decides whether the embed displays.
import { RELAXED_CSP } from "../website/functions/_shared/security-headers.ts";

let failures = 0;
const ok = (msg) => console.log(`  ✓ ${msg}`);
const fail = (msg) => { failures += 1; console.error(`  ✗ ${msg}`); };

function assert(cond, msg) {
  if (cond) ok(msg); else fail(msg);
}

function assertEq(actual, expected, msg) {
  if (actual === expected) ok(msg);
  else fail(`${msg}\n      expected: ${JSON.stringify(expected)}\n      actual:   ${JSON.stringify(actual)}`);
}

// ── The reported symptom: `~` in a table renders as `\~` ───────────────────
console.log("summary table (#4481 — the reported page)");
{
  const md = [
    "| Type | Identity | Epistemic |",
    "|---|---|---|",
    "| Consumer agents | ✓ | \\~ |",
    "| Coding harnesses | ✓ | \\~ |",
  ].join("\n");
  const html = renderMarkdown(md);
  assertEq(
    html,
    '<div class="table-wrap"><table><thead><tr><th>Type</th><th>Identity</th><th>Epistemic</th></tr></thead>' +
      "<tbody><tr><td>Consumer agents</td><td>✓</td><td>~</td></tr>" +
      "<tr><td>Coding harnesses</td><td>✓</td><td>~</td></tr></tbody></table></div>",
    "a table cell containing \\~ renders the bare tilde",
  );
  assert(!html.includes("\\~"), "no escape backslash survives into the table HTML");
}

console.log("legend list (#4481 — the literal line on the page)");
{
  assertEq(
    renderMarkdown("- \\~ = some providers / partial capability"),
    "<ul><li>~ = some providers / partial capability</li></ul>",
    "an escaped tilde in a list item renders the bare tilde",
  );
}

// ── Every punctuation the editor serializer escapes ────────────────────────
console.log("CommonMark backslash escapes (the editor's esc() set)");
{
  assertEq(renderMarkdown("\\~"), "<p>~</p>", "\\~ → ~");
  assertEq(renderMarkdown("a \\* b"), "<p>a * b</p>", "\\* → *");
  assertEq(renderMarkdown("snake\\_case"), "<p>snake_case</p>", "\\_ → _");
  assertEq(renderMarkdown("\\[draft\\]"), "<p>[draft]</p>", "\\[ \\] → [ ]");
  assertEq(renderMarkdown("C:\\\\path"), "<p>C:\\path</p>", "\\\\ → \\");
  assertEq(renderMarkdown("\\a"), "<p>\\a</p>", "a backslash before a non-punctuation char is literal");
}

console.log("an escaped delimiter must not open inline markup");
{
  const html = renderMarkdown("\\*not emphasis\\*");
  assertEq(html, "<p>*not emphasis*</p>", "\\*…\\* renders literal asterisks, not <em>");
  assert(!html.includes("<em>"), "no emphasis tag is emitted from escaped delimiters");
}

// The editor escapes a literal `*` INSIDE an em/strong run (`*a*b*` → `*a\*b*`),
// so span content must consume the escape pair — a `[^*]`-style class truncates
// the match at the escaped star and leaks a stray backslash (V1 review).
console.log("an escaped delimiter inside a span is consumed by the span");
{
  assertEq(renderMarkdown("*a\\*b*"), "<p><em>a*b</em></p>", "*a\\*b* → <em>a*b</em>");
  assertEq(renderMarkdown("**a\\*b**"), "<p><strong>a*b</strong></p>", "**a\\*b** → <strong>a*b</strong>");
  assertEq(renderMarkdown("*snake\\_case*"), "<p><em>snake_case</em></p>", "escaped underscore inside <em>");
  assertEq(
    renderMarkdown("[a\\]b](https://tortoise.premiselabs.co)"),
    '<p><a href="https://tortoise.premiselabs.co" target="_blank" rel="noopener">a]b</a></p>',
    "escaped bracket inside link text",
  );
}

console.log("bounded span content stays linear on adversarial input");
{
  const pathological = "*" + "\\".repeat(20000) + "*" + "[".repeat(2000);
  const t0 = Date.now();
  renderMarkdown(pathological);
  const ms = Date.now() - t0;
  assert(ms < 1000, `pathological emphasis run completes fast (${ms}ms)`);

  // The new entry points share plainText(): slugifyHeading() runs on EVERY
  // heading and extractFaq() on EVERY FAQ answer. Unbounded link/image
  // quantifiers re-scanned to end-of-string at each `[` → O(n²) (pre-fix,
  // 20000 `[`: heading ≈6.8s, FAQ answer ≈6.4s — a CPU DoS at BODY_MAX=100KB).
  //
  // Bounds are wall-clock, and this harness runs inside `welcome-e2e` on a
  // shared, self-hosted runner (the fleet host), so they are deliberately wide
  // (#8294 review): the previous 1500ms bound was measured at 1146ms under
  // ordinary fleet load — a flake generator. At 40 000 `[` the fixed path
  // measures ≈0.4s (heading) / ≈0.1s (FAQ) and the pre-fix quadratic path
  // ≈27s, so a 4s bound keeps an order of magnitude of margin in each
  // direction while still failing loud on the pre-fix code.
  const t1 = Date.now();
  renderMarkdown("## " + "[".repeat(40000));
  const headMs = Date.now() - t1;
  assert(headMs < 4000, `a '['-heavy heading stays linear (${headMs}ms; pre-fix ≈27s)`);

  const t2 = Date.now();
  extractFaq("## FAQ\n\n### Q?\n\n" + "[".repeat(40000));
  const faqMs = Date.now() - t2;
  assert(faqMs < 4000, `a '['-heavy FAQ answer stays linear (${faqMs}ms; pre-fix ≈27s)`);

  // Duplicate headings must allocate anchors in O(1) amortised per heading.
  // The scanned-from-`-1` form was O(N²): 8000 `## A` (48KB — well under
  // BODY_MAX=100KB) took 23.9s standalone, and 16000 (96KB) 131s. Post-fix on
  // this machine the same 8000 renders in ≈0.3–0.6s (16000 ≈0.9s), so the bound
  // sits well above the measured time and far below the pre-fix blowup.
  const dupMd = Array.from({ length: 8000 }, () => "## A").join("\n\n");
  const t3 = Date.now();
  const dupHtml = renderMarkdown(dupMd);
  const dupMs = Date.now() - t3;
  assert(
    dupMs < 5000,
    `8000 duplicate headings stay linear (${dupMs}ms; pre-fix ≈23.2s, post-fix ≈0.3–0.6s)`,
  );
  const dupIds = [...dupHtml.matchAll(/id="([^"]*)"/g)].map((mm) => mm[1]);
  assertEq(dupIds.length, 8000, "every duplicate heading still emits an anchor id");
  assertEq(new Set(dupIds).size, 8000, "every one of the 8000 anchor ids is distinct");
  assertEq(dupIds[0], "a", "the first duplicate heading keeps the bare base id");
  assertEq(dupIds[7999], "a-7999", "the last duplicate heading resumes the suffix cursor");
}

console.log("code spans are exempt from unescaping (CommonMark)");
{
  assertEq(
    renderMarkdown("`\\~`"),
    "<p><code>\\~</code></p>",
    "a backslash inside a code span is preserved verbatim",
  );
  assertEq(
    renderMarkdown("```\n\\~\n```"),
    "<pre><code>\\~</code></pre>",
    "a backslash inside a fenced block is preserved",
  );
}

// ── No regression on the constructs that already worked ───────────────────
console.log("regression — existing inline constructs still render");
{
  assertEq(renderMarkdown("**bold**"), "<p><strong>bold</strong></p>", "**bold** → <strong>");
  assertEq(renderMarkdown("*em*"), "<p><em>em</em></p>", "*em* → <em>");
  assertEq(
    renderMarkdown("[site](https://tortoise.premiselabs.co)"),
    '<p><a href="https://tortoise.premiselabs.co" target="_blank" rel="noopener">site</a></p>',
    "[text](url) → <a>",
  );
  assertEq(renderMarkdown("`code`"), "<p><code>code</code></p>", "`code` → <code>");
}

console.log("regression — raw HTML stays escaped (stored-XSS contract)");
{
  const html = renderMarkdown("<script>alert(1)</script>");
  assert(!html.includes("<script>"), "no <script> element is emitted from body text");
  assert(html.includes("&lt;script&gt;"), "raw HTML is entity-escaped");
}

// ── Heading anchors, table of contents, callouts (#8291) ──────────────────
console.log("heading anchors (#8291 — a TOC link needs a real target)");
{
  assertEq(
    renderMarkdown("## What is agent memory—and what isn't it?"),
    '<h2 id="what-is-agent-memory-and-what-isnt-it">What is agent memory—and what isn&#39;t it?</h2>',
    "a heading gets a slug id derived from its text",
  );
  assertEq(
    renderMarkdown("### Cost & latency"),
    '<h3 id="cost-and-latency">Cost &amp; latency</h3>',
    "punctuation collapses to a single dash and `&` becomes `and`",
  );
  assertEq(
    renderMarkdown("## One\n\n## One"),
    '<h2 id="one">One</h2>\n<h2 id="one-1">One</h2>',
    "duplicate headings get unique ids",
  );
  assertEq(
    renderMarkdown("## **Bold** and `code`"),
    '<h2 id="bold-and-code"><strong>Bold</strong> and <code>code</code></h2>',
    "the slug comes from heading TEXT, the heading keeps its markup",
  );
}

console.log("heading ids cannot carry markup out of the attribute");
{
  const html = renderMarkdown("## <img src=x onerror=alert(1)>");
  assertEq(
    html,
    '<h2 id="img-src-x-onerror-alert-1">&lt;img src=x onerror=alert(1)&gt;</h2>',
    "a heading containing raw HTML yields an inert id and an escaped body",
  );
  assert(!/id="[^"]*<img/.test(html), "no raw markup is interpolated into the id attribute");
}

console.log("table of contents (#8291 — cannot drift from the headings)");
{
  const md = ["[[toc]]", "", "## First section", "", "### A sub-heading", "", "## Second section"].join("\n");
  const html = renderMarkdown(md);
  assertEq(
    html.split("\n")[0],
    '<nav class="toc" aria-label="Table of contents"><ol>' +
      '<li><a href="#first-section">First section</a></li>' +
      '<li><a href="#second-section">Second section</a></li></ol></nav>',
    "[[toc]] expands to a linked list of the H2 headings",
  );
  assert(!html.includes("#a-sub-heading"), "sub-headings stay out of the top-level list");
  assert(html.includes('<h3 id="a-sub-heading">'), "sub-headings still get an anchor of their own");
  assert(
    renderMarkdown("\\[\\[toc\\]\\]\n\n## One").includes('<nav class="toc"'),
    "the editor's backslash-escaped form of the marker is recognised too",
  );
  assert(!renderMarkdown("## One").includes('<nav class="toc"'), "no marker means no TOC is injected");
}

console.log("TOC labels are plain, escaped text (#8294 — no nested anchor, no injected markup)");
{
  const tocOf = (html) => html.slice(html.indexOf("<nav"), html.indexOf("</nav>") + 6);

  const raw = renderMarkdown("[[toc]]\n\n## <img src=x onerror=alert(1)>");
  assert(!raw.includes("<img src=x"), "raw markup in a heading injects no raw <img> into the page");
  assert(tocOf(raw).includes("&lt;img src=x onerror=alert(1)&gt;"), "the TOC label carries the entity-escaped form");

  const markedUp = renderMarkdown("[[toc]]\n\n## Hi ![shot](https://e.com/i.png)");
  assert(!tocOf(markedUp).includes("<img"), "a markdown image in a heading injects no <img> into the TOC");
  assert(tocOf(markedUp).includes(">Hi shot</a>"), "the image contributes its alt text to the label");

  const linked = renderMarkdown("[[toc]]\n\n## Read the [docs](https://x.com)");
  assertEq((tocOf(linked).match(/<a /g) ?? []).length, 1, "the TOC label has exactly one anchor (no nested <a>)");
  assert(tocOf(linked).includes('href="#read-the-docs"'), "the TOC still links to the heading's anchor");
  assert(linked.includes('<h2 id="read-the-docs">Read the <a href="https://x.com"'), "the heading itself keeps its rendered link");
}

console.log("TOC labels match the rendered heading text (#8294 — no second stripper)");
{
  const tocLabel = (html) => (html.match(/<li><a href="#([^"]*)">([\s\S]*?)<\/a><\/li>/) ?? []).slice(1);
  // inline() implements only `**…**` and `*…*`; `_`, `__` and `~~` are literal
  // text it renders. The label/id must be derived from the SAME visible text, or
  // the TOC entry points at a heading whose text differs from the label.
  assertEq(
    tocLabel(renderMarkdown("[[toc]]\n\n## A _B_ C")).join(" → "),
    "a-b-c → A _B_ C",
    "an underscored heading keeps the underscores in the label and slug",
  );
  assertEq(
    tocLabel(renderMarkdown("[[toc]]\n\n## A __B__ C")).join(" → "),
    "a-b-c → A __B__ C",
    "a double-underscore heading is not stripped (inline() does not implement it)",
  );
  assertEq(
    tocLabel(renderMarkdown("[[toc]]\n\n## A ~~B~~ C")).join(" → "),
    "a-b-c → A ~~B~~ C",
    "a strikethrough heading is not stripped (inline() does not implement it)",
  );
  assertEq(
    tocLabel(renderMarkdown("[[toc]]\n\n## **Bold** and `code`")).join(" → "),
    "bold-and-code → Bold and code",
    "markup inline() DOES implement is still stripped from the label",
  );
  // A heading is not a list item: inline() renders a leading `3.` / `-` as
  // literal text, so the label and slug must keep it (a hand-rolled stripper
  // deleted it, which is #8294 review finding 2).
  assertEq(
    tocLabel(renderMarkdown("[[toc]]\n\n## 3. Hybrid retrieval")).join(" → "),
    "3-hybrid-retrieval → 3. Hybrid retrieval",
    "a numbered heading keeps its number in the label and slug",
  );
  assertEq(
    tocLabel(renderMarkdown("[[toc]]\n\n## - A dash heading")).join(" → "),
    "a-dash-heading → - A dash heading",
    "a leading dash is literal heading text, not a list marker to strip",
  );
  // The editor backslash-escapes ASCII punctuation; inline() treats `\*` / `` \` ``
  // as literal, so the label must not strip them (the same review's adjacent find).
  assertEq(
    tocLabel(renderMarkdown("[[toc]]\n\n## Does \\*fair use\\* apply?")).join(" → "),
    "does-fair-use-apply → Does *fair use* apply?",
    "an escaped asterisk pair stays literal in the label",
  );
  assertEq(
    tocLabel(renderMarkdown("[[toc]]\n\n## Use \\`code\\` fence")).join(" → "),
    "use-code-fence → Use `code` fence",
    "an escaped code span stays literal in the label",
  );
}

console.log("heading ids are unique across dedup suffixes (#8294)");
{
  const idsOf = (html) => [...html.matchAll(/id="([^"]*)"/g)].map((m) => m[1]);

  const mixed = idsOf(renderMarkdown("## A\n\n## A\n\n## A 1"));
  assertEq(mixed.join(","), "a,a-1,a-1-1", "A, A, A 1 → a, a-1, a-1-1 (no collision)");
  assertEq(new Set(mixed).size, mixed.length, "every emitted id is distinct");

  const four = idsOf(renderMarkdown("## A\n\n## A\n\n## A 1\n\n## A"));
  assertEq(new Set(four).size, four.length, `four headings stay distinct (${four.join(", ")})`);

  const three = idsOf(renderMarkdown("## One\n\n## One\n\n## One"));
  assertEq(three.join(","), "one,one-1,one-2", "three identical headings get one, one-1, one-2");

  const hrefs = [...renderMarkdown("[[toc]]\n\n## A\n\n## A\n\n## A 1").matchAll(/href="#([^"]*)"/g)].map((m) => m[1]);
  assertEq(new Set(hrefs).size, hrefs.length, "every TOC anchor target is distinct");
}

console.log("callouts (#8291 — the answer box needs a real primitive)");
{
  assertEq(
    renderMarkdown("> [!ANSWER] Agent memory is any system that retains information."),
    '<aside class="callout callout-answer"><p class="callout-label">Quick answer</p>' +
      "<p>Agent memory is any system that retains information.</p></aside>",
    "> [!ANSWER] renders an answer box, not a quote",
  );
  assert(
    renderMarkdown("> \\[!ANSWER\\] Short.").includes('callout-answer'),
    "the editor's escaped alert marker is recognised",
  );
  assert(
    renderMarkdown("> [!WARNING] Careful.").includes('callout callout-warning'),
    "[!WARNING] gets its own modifier class",
  );
  assertEq(
    renderMarkdown("> [!NOPE] Just a quote."),
    "<blockquote>[!NOPE] Just a quote.</blockquote>",
    "an unrecognised marker degrades to an ordinary blockquote",
  );
  const multi = renderMarkdown("> [!NOTE] Line one\n> line two");
  assert(multi.includes("Line one line two"), "a multi-line alert body stays one paragraph");
  assert(
    renderMarkdown("> [!ANSWER] **Bold** claim.").includes("<strong>Bold</strong>"),
    "inline markdown inside an alert body still renders",
  );
}

console.log("callout bodies carry lists (#8291 — the Key-takeaways furniture)");
{
  const key = renderMarkdown("> [!KEY] Five things\n> - first claim\n> - second claim\n> - third claim");
  assert(key.includes("<p>Five things</p>"), "the label line stays a paragraph");
  assert(
    key.includes("<ul><li>first claim</li><li>second claim</li><li>third claim</li></ul>"),
    "a bullet run becomes a real <ul>",
  );
  assert(!key.includes("- first claim"), "literal bullet markers do not survive into the text");

  const ordered = renderMarkdown("> [!ANSWER] Steps\n> 1. do this\n> 2. then that");
  assert(
    ordered.includes("<ol><li>do this</li><li>then that</li></ol>"),
    "an ordered run becomes a real <ol>",
  );

  // The pre-callout shape must not regress: non-list lines stay one paragraph.
  const prose = renderMarkdown("> [!NOTE] Line one\n> line two");
  assert(prose.includes("<p>Line one line two</p>"), "non-list lines stay a single paragraph");
  assertEq(
    renderMarkdown("> [!NOPE] Just a quote."),
    "<blockquote>[!NOPE] Just a quote.</blockquote>",
    "an unknown marker still degrades to a blockquote (no list support leaks into it)",
  );
}

console.log("regression — a plain blockquote is untouched by the callout path");
{
  assertEq(
    renderMarkdown("> Just a quote."),
    "<blockquote>Just a quote.</blockquote>",
    "an ordinary blockquote still renders as <blockquote>",
  );
}

// ── FAQ structured data (#8292) ───────────────────────────────────────────
console.log("FAQ extraction (#8292 — the schema cannot claim what the page hides)");
const FAQ_MD = [
  "## Some section",
  "",
  "Body text.",
  "",
  "## Frequently asked questions",
  "",
  "### What is LLM memory?",
  "",
  "LLM memory preserves information across calls. It is distinct from weights.",
  "",
  "### Do I need a graph database?",
  "",
  "No. Files and relational stores can work too.",
  "",
  "### Can markdown work?",
  "",
  "- Yes, and it is easy to inspect",
  "- but test how evidence survives a rewrite",
].join("\n");
{
  const faqs = extractFaq(FAQ_MD);
  assertEq(faqs.length, 3, "every H3 under the FAQ heading becomes a question");
  assertEq(faqs[0].question, "What is LLM memory?", "the question is the heading text");
  assertEq(
    faqs[0].answer,
    "LLM memory preserves information across calls. It is distinct from weights.",
    "the answer is the prose beneath the question, joined",
  );
  assertEq(faqs[2].answer, "Yes, and it is easy to inspect but test how evidence survives a rewrite", "list markers are stripped from the answer");
  assert(faqs.every((f) => f.answer.length > 0), "no empty answers are emitted");
  assert(
    !faqs.some((f) => f.question === "Some section"), "a non-FAQ H2 contributes nothing",
  );

  assert(extractFaq("## Just a section\n\nText.").length === 0, "no FAQ section → no entries");
  assert(extractFaq("").length === 0, "empty body → no entries");
  assertEq(
    extractFaq("## 5 FAQs about memory\n\n### Q?\n\nA.").length,
    1,
    "a heading naming FAQs opens a section",
  );

  const schema = faqPageJsonLd(faqs);
  assertEq(schema["@type"], "FAQPage", "the node is an FAQPage");
  assertEq(schema.mainEntity.length, 3, "one Question per pair");
  assertEq(schema.mainEntity[0].name, faqs[0].question, "Question.name is the question");
  assertEq(
    schema.mainEntity[0].acceptedAnswer.text,
    faqs[0].answer,
    "acceptedAnswer.text is the answer",
  );
  assertEq(faqPageJsonLd([]), null, "no FAQ → no schema node (the caller skips it)");
}

console.log("FAQ answers are harvested from the same markdown the page renders");
{
  // Fenced code is VISIBLE on the page (<pre><code>), so its content is part of
  // the answer; its lines are never scanned for headings.
  const fenced = extractFaq(
    "## FAQ\n\n### How?\n\nProse.\n\n```\nsecret();\n```\n",
  );
  assertEq(fenced[0].answer, "Prose. secret();", "a fenced code block's visible content is part of the answer");
  // A heading deeper than h3 stays inside the answer rather than opening a question.
  assertEq(
    extractFaq("## FAQ\n\n### Q?\n\nIntro.\n\n#### Detail\n\nMore.")[0].answer,
    "Intro. Detail More.",
    "an h4 inside an answer does not start a new question",
  );
}

console.log("FAQ answers publish the text the page shows (#8294)");
{
  const blockquote = extractFaq("## FAQ\n\n### Is it fast?\n\n> Yes, extremely fast.");
  assertEq(blockquote[0].answer, "Yes, extremely fast.", "a blockquote answer drops the `> ` marker");

  const table = extractFaq(
    "## FAQ\n\n### Which plan?\n\n| Plan | Price |\n|---|---|\n| Free | $0 |",
  );
  assertEq(table[0].answer, "Plan Price Free $0", "a table answer drops pipes and the invisible separator row");

  const callout = extractFaq("## FAQ\n\n### Fast?\n\n> [!NOTE] It is fast.");
  assertEq(callout[0].answer, "It is fast.", "a callout answer drops the [!NOTE] marker");

  const escaped = extractFaq("## FAQ\n\n### Fast?\n\n> \\[!TIP\\] Try it.");
  assertEq(escaped[0].answer, "Try it.", "the editor's escaped alert marker is dropped too");

  const codeOnly = extractFaq("## FAQ\n\n### How?\n\n```sh\nnpm i x\n```");
  assertEq(codeOnly.length, 1, "a code-only answer keeps its question");
  assertEq(codeOnly[0].answer, "npm i x", "the code block's visible text is the answer");
  assert(!codeOnly.some((f) => f.answer.includes("`")), "fence markers never reach the schema");
}

console.log("FAQ answer text equals the rendered answer text (#8292 — fails if either side changes alone)");
{
  // Derive the visible text INDEPENDENTLY in the test: render the answer's
  // markdown with renderMarkdown, strip the allowlisted tags, decode the
  // entities. If the extraction side (or the renderer) drifts, this reddens.
  // The pre-fix extractor failed this on underscores, tildes, `---`/`***`,
  // a lone pipe line, an unknown `[!WORD]` and `[[toc]]`.
  const visible = (md) =>
    renderMarkdown(md)
      .replace(/<p class="callout-label">[\s\S]*?<\/p>/g, " ")
      .replace(/<img[^>]*\balt="([^"]*)"[^>]*>/g, " $1 ")
      .replace(/<[^>]*>/g, " ")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&#39;/g, "'")
      .replace(/&quot;/g, '"')
      .replace(/&amp;/g, "&")
      .replace(/\s+/g, " ")
      .trim();
  const faqAnswer = (md) => extractFaq(`## FAQ\n\n### Q?\n\n${md}`)[0]?.answer;

  for (const [name, answerMd] of [
    ["underscored identifiers survive", "Use snake_case_naming."],
    ["strikethrough is literal text inline() does not implement", "~~deprecated~~ API is out."],
    ["a `---` thematic break contributes no answer text", "A paragraph.\n\n---\n\nAnother."],
    ["a `***` thematic break contributes no answer text", "Before.\n\n***\n\nAfter."],
    ["a lone pipe line is a paragraph, not a table", "| not a table |"],
    ["a real table contributes its cell text", "| Plan | Price |\n|---|---|\n| Free | $0 |"],
    ["an unknown `[!WORD]` marker is literal", "> [!NOPE] Just a quote."],
    ["a known callout drops its chrome label", "> [!NOTE] It is fast."],
    ["a `[[toc]]` marker contributes no answer text", "[[toc]]\n\nA."],
    ["a fence contributes its visible code", "```\nsecret();\n```"],
    // An h4 inside an answer is re-rendered from its WHOLE line, so a heading
    // whose text starts with a block marker is not reclassified (review finding).
    ["an h4 whose text starts with a list marker", "Intro.\n\n#### - item\n\nMore."],
    ["an h4 whose text starts with an ordered marker", "Intro.\n\n#### 2026. Plan\n\nAfter."],
    ["an h4 whose text starts with a quote marker", "Intro.\n\n#### > quote\n\nAfter."],
    ["an h4 whose text is a thematic break", "Intro.\n\n#### ---\n\nAfter."],
  ]) {
    assertEq(faqAnswer(answerMd), visible(answerMd), `answer text equals the rendered text: ${name}`);
  }
}

console.log("FAQ questions equal the rendered heading text (#8294 — one classifier)");
{
  // The question half of the same invariant: `Question.name` must be the text
  // the reader sees in the <h3>. The answer half is asserted above; this pins
  // the label path too, on the constructs a second stripper got wrong.
  const questionOf = (q) => extractFaq(`## FAQ\n\n### ${q}\n\nA.`)[0].question;
  const renderedHeading = (q) => {
    const m = /<h3[^>]*>([\s\S]*?)<\/h3>/.exec(renderMarkdown(`### ${q}`));
    return m
      ? m[1]
          .replace(/<[^>]*>/g, "")
          .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
          .replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&")
      : "";
  };
  for (const [name, q] of [
    ["a numbered question keeps its number", "1. Is it fast?"],
    ["a leading dash is literal", "- A dash question"],
    ["an escaped asterisk pair stays literal", "Does \\*fair use\\* apply?"],
    ["an escaped code span stays literal", "Use \\`code\\` fence"],
    ["an underscored identifier survives", "Is snake_case_naming ok?"],
    ["a strikethrough is literal text", "Is ~~old~~ gone?"],
    ["inline markup is stripped as the renderer renders it", "**Bold** question"],
  ]) {
    assertEq(questionOf(q), renderedHeading(q), `FAQ question equals the rendered heading: ${name}`);
  }
}

console.log("article JSON-LD wiring (#8294 — BlogPosting + BreadcrumbList, FAQPage only when present)");
{
  const post = (body) => ({
    id: "1",
    slug: "an-article",
    title: "An article",
    body,
    excerpt: "An excerpt",
    cover_image_url: null,
    tags: [],
    author: null,
    meta_title: null,
    meta_description: null,
    published_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  });
  const url = "https://tortoise.premiselabs.co/blog/an-article";
  const withFaq = articleJsonLd(post("## FAQ\n\n### Q one?\n\nA one.\n\n### Q two?\n\nA two."), url);
  assertEq(withFaq.length, 3, "an FAQ article emits three schema nodes");
  assertEq(withFaq[0]["@type"], "BlogPosting", "node 1 is BlogPosting");
  assertEq(withFaq[1]["@type"], "BreadcrumbList", "node 2 is BreadcrumbList");
  assertEq(withFaq[2]["@type"], "FAQPage", "node 3 is FAQPage");
  assertEq(withFaq[2].mainEntity.length, 2, "the FAQPage carries one Question per pair");
  assertEq(withFaq[2].mainEntity[0].name, "Q one?", "Question.name is the FAQ question");
  assertEq(withFaq[2].mainEntity[0].acceptedAnswer.text, "A one.", "acceptedAnswer.text is the visible answer");

  const noFaq = articleJsonLd(post("## Just a section\n\nText."), url);
  assertEq(noFaq.length, 2, "a post with no FAQ emits only two nodes");
  assert(!noFaq.some((n) => n["@type"] === "FAQPage"), "no FAQPage node without an FAQ section");
  assert(noFaq.some((n) => n["@type"] === "BlogPosting"), "BlogPosting is still present");
  assert(noFaq.some((n) => n["@type"] === "BreadcrumbList"), "BreadcrumbList is still present");
}

console.log("JSON-LD cannot break out of its script element");
{
  const tag = jsonLd(faqPageJsonLd(extractFaq("## FAQ\n\n### Q?</script><script>alert(1)</script>\n\nA.")));
  assert(!tag.includes("</script><script>"), "a `</script>` in the content is neutralised");
  assert(tag.includes("\\u003c"), "angle brackets are escaped as unicode");
  assert(tag.startsWith('<script type="application/ld+json">'), "the tag is a JSON-LD script");
}

// ── House style: the shapes the already-published articles use ────────────
// Recognised so an existing article gains real links and a styled answer box
// with NO body edit — the CMS has no revision history and no read path
// (tortoise#8002), so rewriting a body to introduce the markers would be an
// unrecoverable write performed blind.
console.log("house-style table of contents");
{
  const md = [
    "**Table of contents**",
    "",
    "1. First section",
    "2. Second section",
    "",
    "## First section",
    "",
    "## Second section",
  ].join("\n");
  const html = renderMarkdown(md);
  assertEq((html.match(/<ol>/g) ?? []).length, 1, "the hand-written list is replaced, not duplicated");
  assert(html.includes('<nav class="toc"'), "a linked TOC is generated from the house-style marker");
  assert(html.includes('<a href="#first-section">First section</a>'), "the generated entries link to the heading anchors");
  // Assert the shape the marker ACTUALLY renders pre-fix. `"Table of contents
  // </p>"` was inert: the paragraph renders `<p><strong>Table of contents
  // </strong></p>`, so `</strong>` sits between the text and `</p>` and the
  // substring never matched — the old assertion passed on the broken renderer.
  assert(
    !html.includes("<strong>Table of contents</strong>"),
    "the marker paragraph itself is not emitted",
  );

  // The guard: a marker with no list under it stays ordinary prose, so a
  // sentence that happens to read "Table of contents" is not eaten.
  const bare = renderMarkdown("**Table of contents**\n\nJust prose after it.");
  assert(!bare.includes('<nav class="toc"'), "a marker with no list generates no TOC");
  assert(bare.includes("Table of contents"), "...and the paragraph is left alone");

  // This input MUST carry a list: without one the list lookahead rejects it and
  // the `^…$` anchor is never exercised at all.
  assert(
    !renderMarkdown("See the Table of contents for details.\n\n1. A\n\n## A").includes('<nav class="toc"'),
    "the phrase inside a sentence does not trigger a TOC",
  );

  // TipTap writes the bold as `\*\*…\*\*`; matching on the raw source (rather
  // than on plainText, where the asterisks stay literal) recognises it.
  assert(
    renderMarkdown("\\*\\*Table of contents\\*\\*\n\n1. A\n\n## A").includes('<nav class="toc"'),
    "the editor's backslash-escaped marker spelling is recognised too",
  );

  // Authored markup that merely RENDERS as "Table of contents" is not a marker.
  assert(
    !renderMarkdown("[Table of contents](#x)\n\n1. A\n\n## A").includes('<nav class="toc"'),
    "a link whose text is 'Table of contents' is not a marker",
  );
  assert(
    !renderMarkdown("![Table of contents](cover.png)\n\n1. A\n\n## A").includes('<nav class="toc"'),
    "an image whose alt is 'Table of contents' is not a marker",
  );
  // Markdown does not break a paragraph at a newline, so a wrapped sentence's
  // tail line is not its own paragraph — and must not be eaten.
  const wrapped = renderMarkdown(
    "This guide explains the whole model.\nTable of contents\n\n1. Setup\n\n## Setup",
  );
  assert(!wrapped.includes('<nav class="toc"'), "a wrapped paragraph's tail line is not a marker");
  assert(wrapped.includes("<ol><li>Setup</li></ol>"), "...and its list is left intact");
  // The BOLDED tail spelling reaches the regex (the unbolded one is rejected for
  // an unrelated reason), so the `^…$` anchor alone does not stop it: it must
  // not START a block either. Pre-fix this split the paragraph in two and ate the
  // list beneath it, because the tail was tested without checking `paragraphBuf`.
  const boldWrapped = renderMarkdown(
    "Every section below is listed in the\n**Table of contents**\n\n1. Setup\n\n## Setup",
  );
  assert(!boldWrapped.includes('<nav class="toc"'), "a wrapped paragraph's bolded tail line is not a marker");
  assert(boldWrapped.includes("<ol><li>Setup</li></ol>"), "...and its list is left intact");
  assert(
    boldWrapped.includes("<p>Every section below is listed in the\n<strong>Table of contents</strong></p>"),
    "...and the wrapped paragraph stays one paragraph",
  );

  // No H2 ⇒ the generated nav would be empty, so the author's label AND list
  // must survive rather than be deleted in exchange for nothing.
  const noH2 = renderMarkdown(
    "**Table of contents**\n\n1. Alpha\n2. Beta\n\n### Alpha\n\n### Beta",
  );
  assert(!noH2.includes('<nav class="toc"'), "a marker with no H2 generates no TOC");
  assert(
    noH2.includes("<strong>Table of contents</strong>") &&
      noH2.includes("<ol><li>Alpha</li><li>Beta</li></ol>"),
    "with no H2 the author's label and list are preserved",
  );

  // A loose (blank-separated) hand-written list is ONE list; the generated nav
  // must replace all of it, not just the first contiguous run.
  const loose = renderMarkdown(
    "**Table of contents**\n\n1. First\n\n2. Second\n\n## First\n\n## Second",
  );
  assertEq((loose.match(/<ol>/g) ?? []).length, 1, "a loose hand-written list is fully replaced (no stray tail)");
  assert(!loose.includes("<li>Second</li>"), "no leftover list item survives the marker");

  // Indented nested items belong to the list too.
  const nested = renderMarkdown("**Table of contents**\n\n- First\n  - Nested\n- Third\n\n## H");
  assert(!nested.includes("Nested"), "indented nested TOC items are consumed with the list");
  assert(!nested.includes("<p>  -"), "no raw list marker leaks into a paragraph");

  // A top-level item of a DIFFERENT marker type under the marker is a SEPARATE
  // list — the renderer's ordered branch ends at `-` just as its bullet branch
  // ends at `1.` — so the nav must not swallow it. Pre-fix `consumeTocList`
  // crossed the blank line by item shape alone and deleted the authored list.
  const mixed = renderMarkdown(
    "**Table of contents**\n\n1. Alpha\n2. Beta\n\n- Real bullet one\n- Real bullet two\n\n## Alpha",
  );
  assert(mixed.includes('<nav class="toc"'), "the marker still generates its nav above a mixed list pair");
  assert(
    mixed.includes("<ul><li>Real bullet one</li><li>Real bullet two</li></ul>"),
    "a bulleted list after the TOC's ordered list is not consumed",
  );
  const mixedReverse = renderMarkdown(
    "**Table of contents**\n\n- Alpha\n- Beta\n\n1. Real item one\n2. Real item two\n\n## Alpha",
  );
  assert(
    mixedReverse.includes("<ol><li>Real item one</li><li>Real item two</li></ol>"),
    "an ordered list after the TOC's bullet list is not consumed",
  );

  // Two markers must both be filled, not have the first overwritten by the second.
  const twoMarkers = renderMarkdown(
    "**Table of contents**\n\n1. A\n\n## A\n\n**Table of contents**\n\n1. B\n\n## B",
  );
  assertEq((twoMarkers.match(/<nav class="toc"/g) ?? []).length, 2, "two markers each render a nav");
}

console.log("house-style answer box");
{
  assertEq(
    renderMarkdown("> **Quick answer:** Agent memory retains information."),
    '<aside class="callout callout-answer"><p class="callout-label">Quick answer</p>' +
      "<p>Agent memory retains information.</p></aside>",
    "`> **Quick answer:** …` renders as the answer box, with the label not duplicated",
  );
  assert(
    renderMarkdown("> \\*\\*Quick answer:\\*\\* Short.").includes("callout-answer"),
    "the editor's backslash-escaped spelling is recognised too",
  );
  assert(
    renderMarkdown("> Quick answer: no bold.").includes("callout-answer"),
    "the bold is optional — the label is the marker",
  );
  // Exact render, not `!includes("Quick answer:</p>")`: that substring never
  // matched even when the marker DID survive (the emitted label is
  // `<p class="callout-label">Quick answer</p>`, no colon), so the old assertion
  // passed on the pre-fix renderer too and proved nothing.
  assertEq(
    renderMarkdown("> **Quick answer:** x"),
    '<aside class="callout callout-answer"><p class="callout-label">Quick answer</p><p>x</p></aside>',
    "the stripped marker text does not survive into the body",
  );
  // The colon bounds the label. A word that merely STARTS with "answer" is
  // prose: the pre-fix regex matched the prefix and sliced it off the body.
  assertEq(
    renderMarkdown("> Quick answers are hard."),
    "<blockquote>Quick answers are hard.</blockquote>",
    "the word 'answers' is not amputated and stays prose",
  );
  for (const prose of [
    "> Quick answering takes practice.",
    "> Quick answerable claims need care.",
    "> Quick answer is hard.",
    "> **Quick answers** are hard.",
  ]) {
    const rendered = renderMarkdown(prose);
    assert(
      rendered.startsWith("<blockquote>") && !rendered.includes("callout-answer"),
      `prose beginning with "Quick answer" is not the answer box: ${prose}`,
    );
  }
  // A body-leading bold/underscore run must not be consumed as the label's
  // closing delimiter — the delimiter is bound to the opening one.
  assertEq(
    renderMarkdown("> Quick answer: **Bold** start of the body."),
    '<aside class="callout callout-answer"><p class="callout-label">Quick answer</p>' +
      "<p><strong>Bold</strong> start of the body.</p></aside>",
    "a body-leading bold delimiter is not consumed as the label's closing bold",
  );
  assert(
    renderMarkdown("> Quick answer: __init__ runs first.").includes("<p>__init__ runs first.</p>"),
    "a body-leading underscore run survives the colon-only spelling",
  );
  assert(
    renderMarkdown("> **Quick answer:** **Bold** start.").includes("<p><strong>Bold</strong> start.</p>"),
    "the bolded spelling still leaves a body-leading bold intact",
  );
  // The closing delimiter glyph is consumed in EXACTLY ONE position, or the
  // second copy reaches past the colon and eats a BODY-leading `**` / `__`:
  // the pre-fix regex published `> **Quick answer**: **Bold** start.` as
  // `Bold** start.` (first word amputated, literal delimiter leaked). These
  // colon-outside-the-bold spellings are the regression cases for that.
  assertEq(
    renderMarkdown("> **Quick answer**: **Bold** start."),
    '<aside class="callout callout-answer"><p class="callout-label">Quick answer</p>' +
      "<p><strong>Bold</strong> start.</p></aside>",
    "the colon-outside-bold spelling does not eat the body's leading bold",
  );
  assertEq(
    renderMarkdown("> **Quick answer** : **Bold** start."),
    '<aside class="callout callout-answer"><p class="callout-label">Quick answer</p>' +
      "<p><strong>Bold</strong> start.</p></aside>",
    "a space before the colon does not widen the consumed span",
  );
  assertEq(
    renderMarkdown("> __Quick answer__: __init__ runs first."),
    '<aside class="callout callout-answer"><p class="callout-label">Quick answer</p>' +
      "<p>__init__ runs first.</p></aside>",
    "the underscore spelling does not eat the body's leading underscore run",
  );
  assertEq(
    renderMarkdown("> \\*\\*Quick answer\\*\\*: \\*\\*Bold\\*\\* start."),
    '<aside class="callout callout-answer"><p class="callout-label">Quick answer</p>' +
      "<p>**Bold** start.</p></aside>",
    "the escaped spelling does not amputate the body's first word",
  );
  // The marker must be the START of the blockquote, not anywhere inside it.
  assertEq(
    renderMarkdown("> Some context first.\n> **Quick answer:** x"),
    "<blockquote>Some context first. <strong>Quick answer:</strong> x</blockquote>",
    "a marker mid-quote is left as an ordinary blockquote",
  );
  assertEq(
    renderMarkdown("> Just a quote."),
    "<blockquote>Just a quote.</blockquote>",
    "a plain blockquote is still untouched",
  );
  assert(
    renderMarkdown("> [!ANSWER] Explicit marker.").includes('class="callout callout-answer"'),
    "the explicit [!ANSWER] marker still works (no regression)",
  );
}

// ── #7751: video embed + VideoObject ───────────────────────────────────────
console.log("");
console.log("video embed marker + VideoObject structured data (#7751)");
{
  const YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

  // The marker renders an iframe and never leaks as literal text.
  const out = renderMarkdown(`Before.\n\n[[video: ${YT}]]\n\nAfter.`);
  assert(out.includes("<iframe"), "the video marker renders an iframe");
  assert(out.includes("video-embed"), "the embed is wrapped for responsive layout");
  assert(
    out.includes("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
    "the src is the privacy-preserving constructed embed URL",
  );
  assert(!out.includes("[[video"), "the marker text is not left in the output");
  assert(out.includes("Before.") && out.includes("After."), "surrounding prose survives");

  // Escaped brackets, as TipTap writes them.
  const escaped = renderMarkdown(`\\[\\[video: ${YT}\\]\\]`);
  assert(escaped.includes("<iframe"), "TipTap-escaped brackets are recognised too");

  // ⛔ THE SECURITY PROPERTY: the src is CONSTRUCTED from a validated id, so an
  // author cannot point the iframe at another origin, whatever they type.
  for (const hostile of [
    "[[video: javascript:alert(1)]]",
    "[[video: https://evil.example.com/watch?v=dQw4w9WgXcQ]]",
    "[[video: https://www.youtube.com/watch?v=<script>]]",
    "[[video: data:text/html,<script>alert(1)</script>]]",
    "[[video: //evil.example.com/embed/x]]",
  ]) {
    const h = renderMarkdown(hostile);
    assert(!h.includes("<iframe"), `no iframe for a rejected payload: ${hostile}`);
    // The rejected marker falls through as ORDINARY TEXT (like an unknown
    // `[!X]` alert), so the URL string legitimately appears as visible prose.
    // What must never happen is an iframe POINTING somewhere off-site — so the
    // assertion is on the src, not on the presence of the text.
    const src = /<iframe[^>]*src="([^"]*)"/.exec(h);
    assert(src === null, `no off-site iframe src for: ${hostile}`);
  }

  // Providers: every accepted YouTube shape, plus Vimeo.
  for (const [url, id] of [
    ["https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"],
  ]) {
    const refs = extractVideo(`[[video: ${url}]]`);
    assertEq(refs.length, 1, `recognised: ${url}`);
    assertEq(refs[0]?.id, id, `id extracted from ${url}`);
  }
  const vimeo = extractVideo("[[video: https://vimeo.com/123456789]]");
  assertEq(vimeo[0]?.provider, "vimeo", "Vimeo is recognised");
  assertEq(
    vimeo[0]?.embedUrl,
    "https://player.vimeo.com/video/123456789",
    "Vimeo embed URL is constructed",
  );
  assertEq(vimeo[0]?.thumbnailUrl, null, "Vimeo has no derivable thumbnail (not fabricated)");
  assertEq(
    extractVideo(`[[video: ${YT}]]`)[0]?.thumbnailUrl,
    "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    "YouTube thumbnail is hqdefault, the poster every uploaded video has",
  );
  assert(
    !extractVideo(`[[video: ${YT}]]`)[0]?.thumbnailUrl.includes("maxresdefault"),
    "never maxresdefault — it 404s for SD-only videos, and thumbnailUrl is required",
  );

  // Optional metadata, recognised by SHAPE so the common case needs no ceremony.
  const withMeta = extractVideo(`[[video: ${YT} | My interview | PT12M34S | 2026-10-10]]`);
  assertEq(withMeta[0]?.title, "My interview", "an explicit video title is picked up");
  assertEq(withMeta[0]?.duration, "PT12M34S", "the ISO duration is picked up");
  assertEq(withMeta[0]?.uploadDate, "2026-10-10", "the upload date is picked up");
  // Out of order, and a non-ISO duration is not mistaken for one.
  const shuffled = extractVideo(`[[video: ${YT} | 2026-10-10 | 12 minutes]]`);
  assertEq(shuffled[0]?.uploadDate, "2026-10-10", "fields are order-independent");
  assertEq(shuffled[0]?.title, "12 minutes", "a non-ISO duration is treated as the title");
  assertEq(shuffled[0]?.duration, null, "a non-ISO duration is not accepted");

  // A marker inside a FENCE is code, not an embed.
  assertEq(
    extractVideo("```\n[[video: " + YT + "]]\n```").length,
    0,
    "a marker inside a fenced code block is not an embed",
  );
  assertEq(extractVideo("no marker here").length, 0, "a body with no marker yields no video");

  // ── the embed and the structured data cannot disagree about what is code ──
  // (#8379 finding 4: `renderMarkdown` opened a fence on the RAW line while
  // `extractVideo` opened it on the TRIMMED line, so a 1–3 space indented fence
  // — valid CommonMark, the shape a list item produces — rendered a live iframe
  // with NO VideoObject. Each body below asserts the two agree, which is the
  // property the old suite never checked.)
  const indented = "   ```\n[[video: " + YT + "]]\n   ```";
  assertEq(
    (renderMarkdown(indented).match(/<iframe/g) || []).length,
    0,
    "a 1–3 space indented fence renders as code, not as an embed",
  );
  assertEq(extractVideo(indented).length, 0, "...and its marker is not a video ref either");

  // The structured data joins the existing builders rather than replacing them.
  const post = {
    id: "1",
    slug: "a-post",
    title: "The State of Agentic Memory",
    body: `[[video: ${YT}]]\n\nProse.`,
    excerpt: "An excerpt.",
    cover_image_url: null,
    tags: ["agent-memory"],
    author: null,
    meta_title: null,
    meta_description: "A description.",
    published_at: "2026-10-12T00:00:00Z",
    updated_at: "2026-10-12T00:00:00Z",
  };
  const nodes = articleJsonLd(post, "https://tortoise.premiselabs.co/blog/a-post");
  const types = nodes.map((n) => n["@type"]);
  assertEq(types.includes("VideoObject"), true, "VideoObject is emitted for an article with a video");
  assertEq(types.includes("BlogPosting"), true, "BlogPosting is still emitted alongside it");
  assertEq(types.includes("BreadcrumbList"), true, "BreadcrumbList is still emitted");

  const video = nodes.find((n) => n["@type"] === "VideoObject");
  assertEq(video.name, post.title, "name falls back to the article title");
  assertEq(video.description, "A description.", "description falls back to meta_description");
  assertEq(video.uploadDate, "2026-10-12T00:00:00Z", "uploadDate falls back to published_at");
  assertEq(video.embedUrl, "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "embedUrl is the constructed one");
  assertEq(
    Array.isArray(video.thumbnailUrl) && video.thumbnailUrl[0] === "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    true,
    "thumbnailUrl is derived for YouTube (hqdefault, the guaranteed poster)",
  );

  // ⛔ #8379 finding 1: the marker's URL and title are AUTHOR text, and TipTap
  // escapes the characters that would re-parse as markup — `_` (a legal
  // YouTube id character), `*`, `~`, `[`. Every other author-facing text path in
  // `_lib.ts` unescapes; the marker did not, so an escaped title reached the
  // iframe `title` and `VideoObject.name` with its backslashes intact, and an id
  // containing `_` was not recognised at all. This is the shape the blog-admin
  // serializer actually writes (`\[\[video: … \]\]`, `abc\_def\_gh1`).
  const escapedMarker =
    "\\[\\[video: https://youtu.be/abc\\_def\\_gh1 | Episode 2: \\*the\\* finale | PT12M34S\\]\\]";
  const escRef = extractVideo(escapedMarker);
  assertEq(escRef.length, 1, "an escaped marker whose id contains `_` is still recognised");
  assertEq(escRef[0]?.id, "abc_def_gh1", "the escaped underscore in the id is undone");
  assertEq(escRef[0]?.title, "Episode 2: *the* finale", "the escaped title is unescaped");
  assertEq(
    escRef[0]?.duration,
    "PT12M34S",
    "the duration is still recognised beside an escaped title",
  );
  const escHtml = renderMarkdown(escapedMarker);
  assert(escHtml.includes("<iframe"), "the escaped marker embeds");
  assert(
    escHtml.includes('title="Episode 2: *the* finale"'),
    "the iframe title is the author's title, not its escaped form",
  );
  assert(!escHtml.includes("\\*the\\*"), "no escape backslash leaks into the iframe title");
  const escVideo = articleJsonLd(
    { ...post, body: `${escapedMarker}\n\nProse.` },
    "https://x/blog/a",
  ).find((n) => n["@type"] === "VideoObject");
  assertEq(
    escVideo?.name,
    "Episode 2: *the* finale",
    "VideoObject.name is the unescaped title, not the editor's escaped form",
  );

  // ── the embed and the structured data cannot disagree about what is code ──
  // (#8379 finding 4: `renderMarkdown` opened a fence on the RAW line while
  // `extractVideo` opened it on the TRIMMED line, so a 1–3 space indented fence
  // — valid CommonMark, the shape a list item produces — rendered a live iframe
  // with NO VideoObject. Each body below asserts the two agree, which is the
  // property the old suite never checked.)
  for (const body of [`[[video: ${YT}]]`, "```\n[[video: " + YT + "]]\n```", indented]) {
    const embeds = (renderMarkdown(body).match(/<iframe/g) || []).length;
    const refs = extractVideo(body).length;
    assertEq(refs, embeds, `embed count and extracted-ref count agree for: ${JSON.stringify(body)}`);
    const videoNodes = articleJsonLd({ ...post, body }, "https://x/blog/a").filter(
      (n) => n["@type"] === "VideoObject",
    );
    assertEq(
      videoNodes.length,
      embeds,
      `one VideoObject per rendered embed for: ${JSON.stringify(body)}`,
    );
  }

  // Two markers render two iframes, so BOTH videos need structured data — the
  // old code took only `[video]` (the first) and silently dropped the rest.
  const twoVideos = articleJsonLd(
    { ...post, body: `[[video: ${YT}]]\n\n[[video: https://vimeo.com/123456789]]` },
    "https://x/blog/a",
  ).filter((n) => n["@type"] === "VideoObject");
  assertEq(twoVideos.length, 2, "one VideoObject per embedded video, not one for the body");
  assertEq(
    twoVideos.map((n) => n.embedUrl).join(" "),
    "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ https://player.vimeo.com/video/123456789",
    "both videos are described, in embed order",
  );
  // The same video twice embeds twice but is described ONCE (no duplicate node).
  const sameTwice = articleJsonLd(
    { ...post, body: `[[video: ${YT}]]\n\n[[video: ${YT}]]` },
    "https://x/blog/a",
  ).filter((n) => n["@type"] === "VideoObject");
  assertEq(sameTwice.length, 1, "a repeated marker does not duplicate its VideoObject");

  // No video → no node. Absence must not be invented.
  const noVideo = articleJsonLd({ ...post, body: "Just prose." }, "https://x/blog/a");
  assertEq(
    noVideo.some((n) => n["@type"] === "VideoObject"),
    false,
    "an article with no video emits no VideoObject",
  );

  // The schema cannot break out of its <script>.
  const hostileTitle = articleJsonLd(
    { ...post, title: "Bad </script><script>alert(1)</script>" },
    "https://x/blog/a",
  );
  const rendered = hostileTitle.map(jsonLd).join("");
  assertEq(rendered.includes("</script><script>alert(1)"), false, "a hostile title cannot close the script tag");
}

// ── the served CSP must admit every embed origin the renderer can emit ─────
// (#8379 finding 1: a restrictive `frame-src` — not `default-src` — governs the
// iframe, so an embed origin missing here is CSP-BLOCKED on the live article
// while every string-level assertion stays green. The origins come from the
// renderer itself, so a new provider reddens this until the policy admits it.)
console.log("");
console.log("every embed origin the renderer can emit is admitted by the served CSP");
{
  const frameSrc = RELAXED_CSP.split("; ").find(
    (d) => d === "frame-src" || d.startsWith("frame-src "),
  );
  assert(frameSrc !== undefined, "RELAXED_CSP declares a frame-src directive (not just default-src)");
  const admitted = new Set(frameSrc.replace(/^frame-src\s*/, "").split(/\s+/).filter(Boolean));
  const origins = Object.values(VIDEO_EMBED_ORIGINS);
  assert(origins.length >= 2, "the renderer advertises its embed origins at runtime");
  for (const origin of origins) {
    assert(
      admitted.has(origin),
      `${origin} is admitted by RELAXED_CSP frame-src — otherwise the embed is CSP-blocked`,
    );
  }
}

// ── #8313: relative links, without weakening the scheme rejection ──────────
console.log("");
console.log("relative + internal links resolve; non-http schemes stay rejected (#8313)");
{
  const hrefOf = (md) => (/<a href="([^"]*)"/.exec(renderMarkdown(md)) ?? [])[1];

  // The defect: every relative reference became a literal "#" — silently. The
  // stored markdown was correct, so it was invisible in the CMS.
  assertEq(hrefOf("[x](#a-heading)"), "#a-heading", "an in-page anchor keeps its fragment");
  assertEq(hrefOf("[x](/blog/other)"), "/blog/other", "an absolute-path internal link resolves");
  assertEq(
    hrefOf("[x](/blog/state-of-agentic-memory-2026-transcript)"),
    "/blog/state-of-agentic-memory-2026-transcript",
    "the transcript link that #7751's convention depends on resolves",
  );
  assertEq(hrefOf("[x](relative/page)"), "relative/page", "a bare relative path resolves");

  // An internal link must NOT be treated as external (it was, on every link).
  assertEq(
    renderMarkdown("[x](/blog/other)").includes("target=\"_blank\""),
    false,
    "an internal link does not open a new tab",
  );
  assertEq(
    renderMarkdown("[x](https://example.com/a)").includes("target=\"_blank\""),
    true,
    "an external link still opens a new tab",
  );
  assertEq(
    renderMarkdown("[x](https://example.com/a)").includes("rel=\"noopener\""),
    true,
    "an external link still carries rel=noopener",
  );

  // ⛔ NON-NEGOTIABLE: the fix must NOT be a fail-open. The scheme check that
  // made this defect also blocked these, and it has to keep blocking them.
  for (const bad of [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "vbscript:msgbox(1)",
    "data:text/html,<script>alert(1)</script>",
    "//evil.example.com/x",
    "file:///etc/passwd",
    // Backslashes are path separators to WHATWG URL parsing for special
    // schemes, so these resolve off-origin exactly like `//evil.example.com/x`
    // while failing a literal `startsWith("//")` (#8379 finding 1).
    "\\/\\/evil.example.com/x",
    "\\\\evil.example.com/x",
  ]) {
    assertEq(hrefOf(`[x](${bad})`), "#", `still rejected: ${bad}`);
  }

  // A backslash spelling of an ABSOLUTE https link is the same link to a
  // browser (WHATWG URL treats `\` as `/` for special schemes), so it is
  // rejected outright rather than normalised — and a rejected reference must
  // never be presented as an external link.
  const backslashedAbsolute = renderMarkdown("[x](https:\\/\\/example.com/a)");
  assertEq(
    /<a href="([^"]*)"/.exec(backslashedAbsolute)?.[1],
    "#",
    "a backslash-escaped absolute href is rejected, not normalised",
  );
  assert(
    backslashedAbsolute.includes('target="_blank"') === false,
    "a rejected backslash reference does not get the external shape",
  );
  // …and the predicate rejects both protocol-relative backslash spellings.
  assertEq(validHref("\\/\\/evil.example.com/x"), false, "validHref rejects `\\/\\/host`");
  assertEq(validHref("\\\\evil.example.com/x"), false, "validHref rejects `\\\\host`");
  // Control characters cannot be smuggled into an href. Asserted on the
  // predicate directly: a NUL is stripped by `escapeHtml` before the link regex
  // runs, so there is no anchor to inspect at all — the property is that the
  // href is never ACCEPTED, not that it is replaced with "#".
  assertEq(validHref("/blog/a\u0000b"), false, "a NUL byte in an href is rejected");
  assertEq(validHref("/blog/a b"), false, "a space in an href is rejected");
  assertEq(validHref("/blog/a\nb"), false, "a newline in an href is rejected");
  assertEq(validHref(""), false, "an empty href is rejected");
}

// ── The authoring contract's own examples render as documented (#8379) ─────
// docs/blog-authoring.md makes a promise per example, and the renderer makes
// those promises executable. A placeholder id that is not a real provider id,
// or a heading rule the renderer does not implement, is a defect in the
// contract even when every other assertion in this suite passes.
console.log("");
console.log("docs/blog-authoring.md examples render as the page claims");
{
  const doc = readFileSync(new URL("../docs/blog-authoring.md", import.meta.url), "utf8");
  const blocks = [...doc.matchAll(/^```[^\n]*\n([\s\S]*?)^```\s*$/gm)].map((m) => m[1].trim());
  const videoExamples = blocks.filter((b) => b.includes("[[video:"));

  assert(videoExamples.length >= 2, "the contract documents at least two video examples");
  for (const example of videoExamples) {
    const firstLine = example.split("\n")[0];
    const html = renderMarkdown(example);
    assert(html.includes("<iframe"), `the documented video example embeds: ${firstLine}`);
    assert(
      !html.includes("[[video"),
      `the documented video example's marker is consumed, not published as text: ${firstLine}`,
    );
    assert(
      extractVideo(example).length >= 1,
      `the documented video example yields a VideoObject source: ${firstLine}`,
    );
  }

  // The Basics table's heading row (`##`–`####` → `<h2>`–`<h4>`; the post title
  // supplies the page's <h1>). The page previously claimed `#`–`####` →
  // `<h1>`–`<h4>`, which the renderer falsifies (a lone `#` is not a heading)
  // and which contradicted the page's own anchors paragraph.
  assert(/`##`–`####` headings/.test(doc), "the Basics table documents `##`–`####`, not `#`–`####`");
  assert(renderMarkdown("## Two").includes('<h2 id="two">Two</h2>'), "`##` renders an <h2>");
  assert(renderMarkdown("#### Four").includes('<h4 id="four">Four</h4>'), "`####` renders an <h4>");
  assert(!renderMarkdown("# One").includes("<h1"), "a lone `#` is not a heading");
}

console.log("");
if (failures > 0) {
  console.error(`${failures} blog markdown assertion(s) failed`);
  process.exitCode = 1;
} else {
  console.log("all blog markdown assertions passed");
}
