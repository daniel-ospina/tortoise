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

import { renderMarkdown } from "../website/functions/blog/_lib.ts";

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
    "<table><thead><tr><th>Type</th><th>Identity</th><th>Epistemic</th></tr></thead>" +
      "<tbody><tr><td>Consumer agents</td><td>✓</td><td>~</td></tr>" +
      "<tr><td>Coding harnesses</td><td>✓</td><td>~</td></tr></tbody></table>",
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

console.log("");
if (failures > 0) {
  console.error(`${failures} blog markdown assertion(s) failed`);
  process.exitCode = 1;
} else {
  console.log("all blog markdown assertions passed");
}
