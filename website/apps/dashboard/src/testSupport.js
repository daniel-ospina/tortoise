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
// separator in a BARE URL is never read as a comment. This matters because
// main.jsx contains URLs in JSX TEXT (`<li>Server URL: <code>https://…</code>`),
// not only inside string literals — without it the rest of such a line was
// silently deleted from the scanned view, which made live copy on that line
// invisible to the source guards. Pinned by testSupport.test.js.
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
