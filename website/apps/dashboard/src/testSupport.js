// testSupport.js — shared test helpers for the dashboard static guards.
//
// stripComments(src) is a QUOTE-AWARE comment stripper: it removes /* */ block
// comments (wherever they occur) and // comments (to end of line) ONLY when
// not inside a single/double-quoted string or a backtick template (minimal
// escape handling). The strip is quote-aware because a naive regex strip turns
// `//` INSIDE string/template literals into comment text (e.g. 'https://…' →
// 'https:'), so a banned token embedded in a URL string becomes invisible to a
// code grep (evadable). Strings stay intact, so a code-form usage (string
// literal included) still fails the grep while comment-only mentions pass.
//
// Known evadable shapes (documented seams — this is a quote-aware comment
// stripper, NOT a JS tokenizer; pinned by the unit tests in
// testSupport.test.js):
//   - REGEX LITERALS whose body ends in escaped slashes — `/https?:\/\//`
//     exposes a `//` pair at the regex close, so from that pair to end-of-line
//     is stripped as a comment (accepted limitation: guards must not rely on
//     code whose regex literals end `\/\/`).
//   - TEMPLATE LITERALS with NESTED backticks inside `${...}` expressions —
//     the machine closes at the first unescaped backtick, so a nested backtick
//     ends the template early and the tail is re-scanned as plain code. The
//     shape that actually leaks is `` `a ${`//X` + y} z` `` (the `//X` is
//     stripped because the machine is no longer inside a template); a nested
//     backtick followed by a `//` in PLAIN text does NOT leak. NOT pinned by a
//     unit test today (accepted, unpinned limitation).
//
//   - STRING BRANCH vs JSX-TEXT APOSTROPHES (fixed here; the reason the
//     newline abort below exists): treating every ' as a string delimiter
//     opened a PHANTOM string at each apostrophe in JSX text (`what's left`,
//     `this Organization's graph`), running to the NEXT apostrophe — spans of
//     up to ~1.6 KB — with everything inside copied verbatim, so `/* */` and
//     `//` comments in that span were never stripped. Measured on main.jsx:
//     56 of 134 block comments survived, which silently inverted the
//     caller's contract in BOTH directions (a commented-out anchor could
//     satisfy a presence ratchet; a JSX comment could trip a residual scan).
//     A raw newline now ends a ' or " literal because those cannot span one
//     unescaped; backticks still may. `\`-continuations are unaffected (the
//     escape branch copies the escaped newline).
//
// Third seam, on the CALLER: a regex/char-class literal that exposes a `/*`
// swallows everything up to the NEXT `*/` — usually MID-FILE, not EOF, because
// a later unrelated `*/` closes the phantom comment first. (Only a genuinely
// unclosed `/*` reaches EOF.) On a whole-file vocabulary/residual scan that is
// a silent false negative: the scanned region shrinks and the guard can go
// GREEN by losing text. Callers must therefore pin scan coverage with
// sentinels spread across the file — a tail sentinel alone does not catch a
// mid-file swallow (see the head/20/40/60/80/tail pins in
// testSupport.test.js).
//
// `//` is only a comment start when it is NOT preceded by `:`, so the scheme
// separator in a BARE URL is never read as a comment. main.jsx historically
// rendered URLs in JSX TEXT (`<li>Server URL: <code>https://…</code>`); that
// literal has since been extracted to CANONICAL_MCP_URL (./harnesses.js), so
// today every `://` in main.jsx sits inside a quoted string/template or a
// quoted JSX attribute and the guard is exercised by the synthetic unit test
// in testSupport.test.js. It is kept because a bare URL in JSX text is a
// normal thing for this file to grow back.
//
// SIDE EFFECT of the `:` guard (over-permit, documented not fixed): a GENUINE
// `//` comment that begins immediately after a `:` — `case 1:// note` or
// `{ a:1, b:// note` — is NOT stripped and is scanned as code. That direction
// is a false POSITIVE for a residual scan and a false NEGATIVE for a presence
// ratchet. Not reachable in main.jsx today; fixing it needs real tokenizer
// context (a `:` inside a ternary vs a scheme separator), which is #3102's
// scope. Do not add guards that rely on `://`-adjacent comments.
export function stripComments(src) {
  let out = ''
  let i = 0
  const n = src.length
  while (i < n) {
    const c = src[i]
    const next = src[i + 1]
    if (c === '/' && next === '/' && src[i - 1] !== ':') {
      // line comment — run to (not incl.) the newline
      while (i < n && src[i] !== '\n') i++
    } else if (c === '/' && next === '*') {
      // block comment — run past the closing */
      i += 2
      while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++
      i += 2
    } else if (c === '"' || c === "'" || c === '`') {
      // string / template literal — copy verbatim to its unescaped close so
      // a `//` inside the literal is never misread as a comment start
      const quote = c
      out += c
      i++
      while (i < n) {
        const sc = src[i]
        if (sc === '\\') { out += sc + (src[i + 1] || ''); i += 2; continue }
        // A ' or " literal cannot span an unescaped newline; ending the string
        // here is what stops a JSX-text apostrophe (`what's left`) from opening
        // a phantom multi-line "string" that swallows real comments. Leave the
        // newline for the main loop to copy. Backticks may span lines.
        if (sc === '\n' && quote !== '`') break
        out += sc
        i++
        if (sc === quote) break
      }
    } else {
      out += c
      i++
    }
  }
  return out
}
