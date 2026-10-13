// CommonMark backslash escapes (#4481) — ONE implementation, shared by the
// body renderer (`_lib.ts`) and the video-marker parser (`video.ts`).
//
// The canonical body is authored in the TipTap editor, whose serializer escapes
// ASCII punctuation — `\~`, `\*`, `\[`, `\_` … — so text that is not markup
// cannot re-parse as markup. That is correct markdown (`~` is escaped because
// `~~` is GFM strikethrough), but every consumer has to undo it: otherwise the
// escape backslash leaks into the page and a literal "~" (the summary table's
// partial-capability marker) renders as "\~". A backslash before a
// non-punctuation character is literal, per spec.
//
// Extracted from `_lib.ts` for #8379 (finding 1): the marker's title (and its
// URL, whose `_` TipTap escapes) was the one author-facing text path that did
// NOT unescape, so a title typed as `Episode 2: *the* finale` reached the
// iframe `title` and `VideoObject.name` as the raw `\*the\*`. Two copies of this
// rule would drift exactly the way the fence predicate did, so there is one.
const MD_ESCAPE_RE = /\\([!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~])/g;

/** Undo CommonMark backslash escapes: `\~` → `~`, `\\` → `\`. */
export function unescapeMd(s: string): string {
  return s.replace(MD_ESCAPE_RE, "$1");
}
