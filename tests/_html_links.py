"""Shared anchor-href extraction for the public-site guards (#3950).

One implementation, used by BOTH layers that must agree about what a "way in" is:

  * ``tests/test_website_docs_consistency.py`` — the static guard, and
  * ``tests/e2e/test_legal_pages.py`` — the production check.

Both halves of that question live here: which hrefs a browser would render
(``extract_anchor_hrefs``) and which of them lead INTO the blog (``is_blog_entry``
/ ``blog_entry_hrefs``).

Why a shared module rather than two hand-synced copies: the E2E copy had already
drifted (it did not strip comments/``<script>``/``<style>``, so it counted an
anchor the static guard correctly rejected), and no test can detect that drift —
importing the E2E module executes its module-level ``pytest.skip``, so the
unconditional suite cannot import it to compare. Two copies of a rule whose whole
purpose is that the two layers agree is the silent-drift class this guard exists
to close.

The parser below is also the ONE place this repo asks "what does a browser
actually put in the document tree?", so the duplicate-`id` guard reads it too
(``extract_element_ids``, #4118), the docs page's attribute allowlist reads it
for attribute NAMES (``extract_element_attributes``, #14073 — a claim parked in a
`title`/`aria-*` is invisible to a visible-text golden, and a source pattern
misses the spellings HTML permits), and the docs page's section bound reads it
for the headings and for WHICH anchors sit inside its TOC block
(``extract_heading_positions``/``extract_anchors_within``, #14073 review — "inside
that element" is an ancestry, and a raw-source read both missed the spellings
HTML permits and counted blocks a reader never meets). It used to keep an `HTMLParser` subclass of its
own, which inherited the stdlib tokenizer's divergences from a browser in BOTH
directions — it missed `<svg><style><div id="dup"></div></style></svg>` (raw text
to the stdlib, markup to a browser: a SILENT miss) and counted `<template>`
content a browser keeps inert. Every fix to that copy was "declare the limit and
gate it" rather than "model what a browser does", and that does not converge:
the divergence set is not enumerable from the outside (three were found, one per
review cycle). Routing the ids through this seam removes the class instead of
containing it — the same argument as the paragraph above, one level down.

This module is pure (stdlib only, no network/browser/DB/env), so it is importable
from either suite without touching the E2E harness gates, which stay at module
scope in the E2E file.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from types import MappingProxyType
from urllib.parse import urlsplit

# Hosts this site may legitimately send a visitor to for the blog. `index.html`
# uses the ABSOLUTE tortoise URL (its own host 301s `/blog` there), so an absolute
# href has to be accepted — but only for a host the site actually owns. Accepting
# ANY host (an earlier revision) means `https://tortoise.premiselab.co/blog` — a
# one-letter typo that renders a DNS error — or `https://example.com/blog`
# satisfies the guard, and nothing else in the suite can catch it: the external
# link crawl excludes every `*.premiselabs.co` host, and `index.html` is the only
# page whose blog link is absolute (review finding, #3962).
_BLOG_HOSTS = frozenset({"premiselabs.co", "tortoise.premiselabs.co"})

# `<script` opening a nested script element, in the script-data escaped states.
_SCRIPT_IN_SCRIPT = re.compile(r"<script[\s/>]", re.IGNORECASE)

#: The ASCII whitespace a `class` attribute's tokens are split on — the
#: space-separated-tokens microsyntax, which is where HTML defines a class VALUE
#: (`\t`, `\n`, `\f`, `\r` and space, and nothing else).
#: `str.split()` is NOT used: it also splits on `\v`, which HTML does not, and a
#: `class="toc\vwide"` mysteriously counting as `toc` would let a non-container
#: contribute to the very index the container recording exists to make exact.
_CLASS_WHITESPACE = re.compile(r"[ \t\n\f\r]+")


def _class_tokens(attrs: list[tuple[str, str | None]]) -> set[str]:
    """The tokens of an element's `class` attribute — FIRST one, as a browser keeps.

    A token match and not a substring: `class="toc wide"` carries the token `toc`
    and must count as the TOC block, while `class="tocnote"` must not. Both
    spellings are legal HTML and both are rendered, which is why the check lives
    here rather than in a `<div class="toc">` string match (#14073 review).
    """
    for name, value in attrs:
        if name == "class":
            return {
                token
                for token in _CLASS_WHITESPACE.split(value or "")
                if token
            }
    return set()


class _AnchorHrefParser(HTMLParser):
    """The rendered-link set AND the document tree's element ids.

    Two answers from one parse, because they are one question asked twice: which
    anchors ``hrefs`` a browser renders, and which element ``ids`` it puts in the
    document tree (#4118) — see the "Ids" bullet below for how the second differs
    from the first. A second parser is a second answer, and the two diverge: that
    is exactly what the id guard's own `HTMLParser` subclass did, missing
    `<svg><style><div id="dup"></div></style></svg>` where a browser has two
    elements with that id.

    The link contract is "the rendered-link set", because the guard's whole purpose is
    to answer "can a visitor reach the blog from this page?" — so an anchor that
    no browser exposes is not a way in, and an anchor a browser *does* expose must
    not be missed. Both directions are enforced explicitly rather than left to
    ``HTMLParser``'s defaults, which differ from a browser in several of these
    cases:

    * **Not a link** — comments; the contents of ``<script>``/``<style>``;
      ``<noscript>`` (raw text in a scripting-enabled browser, which is the
      default, so no anchor exists there); and the contents of ``<template>``,
      which is parsed but never rendered. ``<textarea>``/``<title>`` are RCDATA
      and the stdlib parser already treats their contents as text
      (``RCDATA_CONTENT_ELEMENTS``, present on the interpreter this repo pins);
      they are listed in ``_RAW`` anyway so the answer does not depend on that
      table, and because the outcome pins cannot see the difference on a stdlib
      that has it, the RULE is pinned directly in
      ``tests/test_website_docs_consistency.py``.
    * **Link** — only ``<a>`` is collected, so ``<link rel="prefetch">`` is not a
      way in. Only the FIRST ``href`` on a tag counts, since the tokenizer keeps
      the first of a duplicate pair. An ``href`` with no value, or an empty one,
      counts as no link: this extractor's own policy, not the tokenizer's — an
      empty ``href`` merely self-links.
    * **Ids** — the ``ids``: the ``id`` of every element a browser exposes, which
      is a DIFFERENT question from "is this element's content rendered": a
      ``<style id="x">`` holds no markup and IS an element, so its own id counts
      while an ``<div id="x">`` written inside it does not. Suppression here is
      about an element's CONTENT, so an element's own id is recorded BEFORE
      ``_open`` starts its suppressed region — and an element whose content is
      inert (``<template>``) is in the tree, so it counts too. Everything the
      suppression rules get right for links then comes free: a raw-text tag
      inside ``<svg>``/``<math>`` does not suppress (its children are markup, so
      their ids count), foreign content is otherwise traversed as ordinary
      elements, and the abrupt-close comment rule in ``parse_comment`` keeps a
      later element visible. Only the FIRST ``id`` on a tag counts and an empty
      one counts as no id — both browser rules, since a browser keeps the first
      of a duplicate pair and ``getElementById("")`` matches nothing.
    * **Foreign content** — ``<template>`` is only the HTML inert element outside
      ``<svg>``/``<math>``; inside them it is an ordinary foreign element whose
      children do render, so suppression is skipped there. That context ENDS at a
      foreign-content BREAKOUT start tag (the spec's 13.2.6.5 list — ``<p>``,
      ``<div>``, ``<span>``, ``<br>``, … — or ``<font>`` carrying a presentational
      attribute) and at ``</br>``/``</p>``; after either, ``<template>`` is the
      inert HTML element again. A raw-text element is not raw text in foreign
      content either (``<script>`` inside ``<svg>`` holds markup, not text), which
      ``set_cdata_mode`` below enforces.

      **Not modelled, deliberately:** the HTML integration points (``foreignObject``,
      ``desc``, MathML's text integration points) and cross-namespace end-tag
      resolution. A document that hides its only blog link inside one of those
      constructs can be judged differently from the browser, in either direction.
      That is declared out of scope for this guard — a faithful reproduction of the
      HTML tree-construction algorithm is a parser, not a test helper — and it is
      kept honest two ways: the parser records every tag it saw ``foreign_tags``
      inside foreign content and the reason it stopped in ``wedged_reason``, and a
      test fails if a covered page reaches any of these constructs or wedges at
      all. Constructs that would otherwise fail OPEN (the script-data escaped
      states, ``<frameset>``) are detected rather than guessed at. Tracked in
      #3970.
    * **Self-closing tags** — the rule differs by namespace, and getting it
      backwards corrupts state for the rest of the document. In the HTML
      namespace the ``/`` on a NON-void element is ignored, so ``<template/>`` and
      ``<script/>`` OPEN their container and everything after them is suppressed
      until the matching end tag or EOF. In FOREIGN content the ``/`` IS honored,
      so ``<svg/>``/``<math/>`` is an empty element that closes immediately — it
      must not leave a sticky foreign depth behind, or ``<template>`` suppression
      would be disabled for every later template in the document.
    """

    # Content is text in a browser, never markup a link can be rendered from.
    # `<noscript>` qualifies: it is raw text only with scripting ENABLED — the
    # default, and the only mode this guard's question ("can a visitor reach the
    # blog from this page?") is asked in. With scripting off its contents are
    # parsed and do render, which is why it is suppressed here and not omitted.
    # The last five are the rest of HTML's raw-text family, which the stdlib covers
    # from `parse_starttag` but NOT from `handle_startendtag` — so the self-closing
    # spelling needs the entry here to reach the CDATA switch (see
    # `handle_startendtag`).
    _RAW = frozenset({
        "script", "style", "noscript", "textarea", "title",
        "xmp", "iframe", "noembed", "noframes", "plaintext",
    })
    # Parsed, but never rendered (HTML namespace only — see _FOREIGN).
    _INERT = frozenset({"template"})
    #: The one suppression whose answer depends on the SCRIPTING MODE rather than
    #: on the element's own rule, so a guard asking "could a reader meet this?"
    #: cannot clear its content.
    #:
    #: `<noscript>` is suppressed because `self.scripting` is True — correct for the
    #: anchor question (an anchor there is unreachable with JS on). But MDN,
    #: `<noscript>`: *"If scripting is disabled the `<noscript>` element represents
    #: its children as HTML content. If scripting is enabled, the `<noscript>`
    #: element represents its children as text."* So with JS off — a real reader, and
    #: a screen reader in that mode — the children are ordinary markup, and a
    #: `title`/`aria-*` there is a claim the visible-text golden cannot see.
    #:
    #: Recorded so the ATTRIBUTE guard can refuse the region outright rather than
    #: report it clean (#14073 review). A `<noscript>` nested inside an inert
    #: `<template>` never renders in either mode and is deliberately NOT recorded:
    #: the whole point of the inert rule is that template content is not markup a
    #: reader meets, and refusing it there would red a correct page.
    _MODE_DEPENDENT = frozenset({"noscript"})
    # Foreign-content roots: inside these, `_INERT` does not apply.
    _FOREIGN = frozenset({"svg", "math"})
    # HTML5's foreign-content BREAKOUT start tags (spec 13.2.6.5): inside
    # `<svg>`/`<math>` one of these ends the foreign context — the browser pops
    # back to HTML content and reprocesses the tag there. Without the rule, a
    # `<template>` after a breakout stays "foreign" — its children counted as
    # rendered — where the browser has the inert HTML element, so a link no
    # browser renders satisfies the guard (review finding, #3962,
    # Chromium-verified: `<svg><p><template><a href="/blog">`).
    _BREAKOUT = frozenset({
        "b", "big", "blockquote", "body", "br", "center", "code", "dd",
        "div", "dl", "dt", "em", "embed", "h1", "h2", "h3", "h4", "h5",
        "h6", "head", "hr", "i", "img", "li", "listing", "menu", "meta",
        "nobr", "ol", "p", "pre", "ruby", "s", "small", "span", "strong",
        "strike", "sub", "sup", "table", "tt", "u", "ul", "var",
    })
    # `<font>` breaks out only when it carries one of its presentational attrs.
    _BREAKOUT_FONT_ATTRS = frozenset({"color", "face", "size"})
    # Elements that never hold content, so no end tag is coming for them. The
    # open-element stack below must not push one — DEPTH HYGIENE, not a closure
    # failure: `_close_extent` searches the WHOLE stack for the tag it closes, so a
    # leftover `<br>` above a `<div>` is popped with it and the container still closes.
    # This comment used to claim the opposite (that a stray `<br>` would make the next
    # `</div>` a mismatch and the container would never close), which the code cannot
    # do; measured: emptying this set changed no test's answer (#14073 review round 8,
    # P2). What the set still buys is a stack that holds only elements an end tag can
    # remove, so extent and container accounting cannot drift with void tags in it.
    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    })
    #: Start tags that POP out of an open `<select>` — the living standard's "in
    #: body" rule for `<input>`: *"If the stack of open elements has a select
    #: element in scope: Parse error. Repeatedly pop the current node off the stack
    #: of open elements until a select element has been popped."* The token is then
    #: inserted in its new context, i.e. the select is closed before it.
    #:
    #: ⛔ WITHOUT THIS, "is a `<select>` open?" IS NOT THE BROWSER'S QUESTION, and
    #: the two answers diverge in the LOUD direction. Measured (#14073 review):
    #: `<select><input><a href="/blog">Blog</a></select>` — every browser pops the
    #: select at `<input>`, so the anchor is a real link, while this parser kept
    #: the select open and reported NO link where `origin/main` reported
    #: `['/blog']`. The misreading is not confined to the link set: a `<h2>` after
    #: one of these tokens is a heading a reader meets, so refusing to bound on it
    #: left the slice long for no reason. Both readings are correct once the stack
    #: is.
    #:
    #: ⛔ AND IT IS TWO TOKENS, NOT THREE — THE "in select" MODE IS GONE. This rule
    #: used to name `input`/`keygen`/`textarea`, copying the CLASSIC mode's token
    #: list; the living standard no longer has that insertion mode at all (its
    #: sequence runs "in cell" → "in template", and `grep -c 'in select'
    #: parsing.html` is 0 — the same removal this module's `_record_heading`
    #: docstring already notes). What survives is a per-token rule in "in body":
    #:
    #:   `<input>` — *"If the stack of open elements has a select element in scope:
    #:   Parse error. Repeatedly pop the current node off the stack of open
    #:   elements until a select element has been popped."* → POPS, inserted after.
    #:   `<select>` — *"Otherwise, if the stack of open elements has a select element
    #:   in scope: Parse error. **Ignore the token.** Repeatedly pop the current node
    #:   off the stack of open elements until a select element has been popped."*
    #:   → POPS **and is IGNORED**, so no element is created for it (see
    #:   `_SELECT_POP_IGNORED`; measured in Chromium, a nested `<select id=i>` leaves
    #:   only the outer element behind).
    #:   `<textarea>` — *"Insert an HTML element for the token … Switch the
    #:   tokenizer to the RCDATA state"* — NO select-scope step, so it does not pop.
    #:   `keygen` — IS in the standard (it sits in *"area, br, embed, img, keygen,
    #:   wbr"*), and that rule has NO select-scope step either, so it does not pop.
    #:   ⛔ An earlier revision of this comment claimed keygen has "no start-tag rule
    #:   at all; it is not in the living standard" — that was false.
    #:
    #: ⛔ THE STALE PAIR IS NOT HARMLESS — IT IS THE FAIL-OPEN DIRECTION. Aimed at
    #: the storage pins (#14073 review, reproduced): a `<select><textarea>` decoy
    #: inside the TOC block plus an un-advertised `<h2 id="storage-limits">` after
    #: it pops this parser's select, advertises the decoy's anchor and BOUNDS the
    #: slice early, leaving a contradicting sentence outside every pin with the
    #: golden and the vocabulary pin both green. With `textarea`/`keygen` not
    #: popping, the anchor stays no-link and the heading stays one no reader meets,
    #: which is what a browser does. So this set is measured, not copied.
    #:
    #: It is NOT the same test as `<select>` being an `_IMPLICITLY_CLOSED` element:
    #: that governs whether an EXTENT runs past a sibling, this governs whether the
    #: select is still open at all.
    _SELECT_POP = frozenset({"input", "select"})
    #: The subset of `_SELECT_POP` the spec says to IGNORE after popping, so no
    #: element is created for the token (and nothing after it is suppressed by one).
    #: Only `<select>` (its rule says *"Ignore the token"*); `<input>` is instead
    #: inserted in its new context (*"Reconstruct the active formatting elements …
    #: Insert an HTML element for the token"*).
    _SELECT_POP_IGNORED = frozenset({"select"})
    # Tags a browser closes WITHOUT an end tag — spec 13.2.6.3's implied end tags,
    # the table sections, and the tags a SIBLING START TAG closes (`<button>`,
    # `<h1>`–`<h6>`, `<a>`, `<nobr>`, `<select>`, `<table>`; 13.2.6.4's "in body"
    # rules act as an end tag for the open one). `<li>a<li>b` is two elements, so an
    # EXTENT built on end tags alone would run from the first `<li>` past the
    # second. The container helper refuses these rather than guessing (see
    # `extract_anchors_within`).
    #
    # ⛔ BOTH FAMILIES BELONG HERE, and the second was missing: the refusal used to
    # list only the implied-end-tag tags, so `extract_anchors_within(tag="button")`
    # (and `h2`, `a`, `nobr`) returned an extent that ran PAST the sibling —
    # `'<button class="toc"><a href="#a">A</a><button><a href="#b">B</a>'` gave
    # `[['#a', '#b']]` where a browser has `[['#a']]` — a larger advertised set and a
    # SHORTER slice, the fail-open direction, while the helper's own message
    # promised the opposite (#14073 review).
    #
    # ⛔ `form` IS NOT ONE OF THEM, and listing it here was a false premise that
    # REFUSED a legitimate caller. The in-body `<form>` rule is *"If the form
    # element pointer is not null … then this is a parse error; ignore the token"*
    # — it acts as an end tag for nothing; an open form is closed by its OWN
    # `</form>` and by nothing else. Measured in Chromium
    # (`<form class=toc id=f1><a href="#a">A</a><form id=f2><a href="#b">B</a>` →
    # one form, both anchors inside it), so the end-tag extent this helper builds
    # for `form` IS the browser's tree. The refusal made
    # `extract_anchors_within(tag="form")` raise on a correct question (#14073
    # review). ⚠️ Residual, left unmodelled and in the SAFE direction: a NESTED
    # `<form>` start tag is IGNORED by a browser (the same form-pointer rule), while
    # this parser pushes it — which can only make an extent SHORTER, i.e. the
    # advertised set smaller and the slice longer, never the fail-open way.
    _IMPLICITLY_CLOSED = frozenset({
        "a", "button", "caption", "colgroup", "dd", "dt", "h1", "h2",
        "h3", "h4", "h5", "h6", "li", "nobr", "optgroup", "option", "p",
        "rb", "rp", "rt", "rtc", "select", "table", "tbody", "td", "tfoot",
        "th", "thead", "tr",
    })
    #: The block-start tags whose START TAG closes an open `<p>` — spec "in body":
    #: *"If the stack of open elements has a `p` element in button scope, then close a
    #: `p` element"*, and "close a `p` element" is *"… pop the current node off the
    #: stack of open elements until a `p` element has been popped"* — the `<p>` AND
    #: everything opened inside it. `<summary>` is on this list, which is what puts the
    #: `<summary>` where a browser puts it and keeps this stack from carrying a `<p>`'s
    #: descendants past the point the browser closed them (#14073 review round 9).
    _P_CLOSERS = frozenset({
        "address", "article", "aside", "blockquote", "center", "dd", "details",
        "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure",
        "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hgroup", "hr", "li", "listing", "main", "menu", "nav", "ol", "p",
        "plaintext", "pre", "section", "summary", "table", "ul", "xmp",
    })
    #: The "button scope" boundaries for that test (spec: the scope element list —
    #: applet, caption, html, table, td, th, marquee, object, template — plus
    #: `button` for the BUTTON scope). The search stops here: a `<p>` beyond one of
    #: these is NOT in button scope, so no implied close happens.
    _P_SCOPE_BOUNDARY = frozenset({
        "applet", "button", "caption", "html", "marquee", "object", "table",
        "td", "template", "th",
    })
    #: The `_RAW` members whose CONTENT a reader DOES meet, so the visible-text
    #: recording must not drop it with the rest of the raw-text family.
    #:
    #: `<textarea>` renders its text as the control's value, and `<xmp>`/`<plaintext>`
    #: render theirs as preformatted content (13.2.5.2 RCDATA / 13.2.5.5 PLAINTEXT
    #: are tokenizer states, not rendering rules). Dropping them is the FAIL-OPEN
    #: direction for the wording golden — a contradicting sentence inside a
    #: `<textarea>` would be invisible to the pin that exists to catch exactly that,
    #: and the regex body this recording replaced DID count it (#14073 review).
    #:
    #: The rest of `_RAW` stays dropped, and for the opposite reason: `<script>`,
    #: `<style>`, `<title>` and `<noscript>` (under scripting) are never rendered in
    #: the page, and `<noembed>`/`<noframes>`/`<iframe>` hold FALLBACK content — the
    #: same supporting-browser convention as `_UNRENDERED` below.
    _RAW_RENDERS = frozenset({"textarea", "xmp", "plaintext"})
    #: Elements whose CHILD CONTENT a browser does not RENDER UNCONDITIONALLY, by
    #: ELEMENT TYPE — the second half of "does a reader meet this heading/link",
    #: after raw text, inert `<template>` content, the `in select` rule and
    #: `_UNBOUNDABLE_ANCESTORS`.
    #: `<datalist>` is `display: none` in the UA stylesheet, so its children are
    #: never rendered; the rest put their children in the FALLBACK slot, which a
    #: supporting browser renders only when the element itself cannot be used (no
    #: supported source, a failed load, no canvas support) — a condition the page
    #: does not control, so the honest answer is "a reader may or may not meet it"
    #: and the rule takes the side that keeps the slice LONG. The guard's question
    #: is asked in a supporting browser — the same convention as `<noscript>` being
    #: raw text because `self.scripting` is True (see `_RAW`).
    #
    # ⛔ MEASURED, not inferred: a `<h2 id="quickstart">` inside any of these was
    # recorded, so a claim under it left the golden, the vocabulary pin and the
    # attribute allowlist at once — the same fail-open `_record_heading` closes for
    # `<template>`/`<script>`/`<select>`, reachable without touching the TOC at all
    # (`<datalist id="x"><h2 id="quickstart">…` bound the slice; #14073 review).
    #
    # ⛔ `<rp>` IS THE SECOND `display: none` MEMBER, and it is HERE rather than in
    # `_UNBOUNDABLE_ANCESTORS` because BOTH questions agree about it — unlike
    # `<option>`, whose own text paints, NOTHING inside a ruby parenthesis is painted.
    # Its reason is NORMATIVE, not a browser quirk: the HTML Standard's rendering
    # section lists `rp` with the hidden elements, and CSS Ruby's sample UA stylesheet
    # carries `rp { display: none; }` (the parenthesis exists for engines with NO ruby
    # support). MEASURED on this box's Chrome as well: `<rp><h2>HELLO</h2></rp>` → the
    # `<rp>` computes `display: none`, the heading's rect 0x0, 0 ink px; and
    # `<rp>PAREN</rp>` → 0 ink px against 398 for a `<p>PAREN</p>` control on the same
    # harness, so its own text is dropped too and the control proves the harness can
    # see text. Before it was
    # listed, `extract_heading_positions('<rp><h2 id="qs">Q</h2></rp>')` returned the
    # heading, so a `<rp>` decoy BOUND the slice with the claim under it outside every
    # pin (#14073 review round 9, reproduced end-to-end through `_docs_section`).
    _UNRENDERED = frozenset({
        "datalist", "canvas", "video", "audio", "object", "meter", "progress",
        "rp",
    })
    #: ⛔ THIS SET ANSWERS TWO QUESTIONS, and ONE member answers them differently
    #: (#14073 review round 6, then round 7):
    #:
    #: * the BOUNDARY question (`_unmet_by_reader`) wants a LONG slice, so refusing a
    #:   heading inside a fallback slot is the loud side — that is the paragraph above,
    #:   and it is a TAG test there, so it consults the WHOLE set and is unaffected by
    #:   `_FALLBACK_RENDERS`;
    #: * the TEXT question (`extract_visible_text`) must not DROP copy a reader meets,
    #:   because a shorter text reads as a clean one. That is not a direction call —
    #:   it is a MEASUREMENT (reproduced under `_FALLBACK_RENDERS`): a supporting
    #:   browser paints NOTHING of the fallback text of `<video>`, `<canvas>`,
    #:   `<audio>`, `<datalist>`, `<meter>` and `<progress>`, so dropping THEIRS is
    #:   correct, while `<object>` paints ALL of it, so dropping that one hid a claim —
    #:   which is why it is admitted for the text question only (see
    #:   `_FALLBACK_RENDERS`). An earlier revision cited a browser build number here;
    #:   the reproduction below is what can be re-run, so it is what the paragraph
    #:   points at.
    #:
    #: ⛔ THE `<object>` MEASUREMENT HAS A NORMATIVE REASON, so the member is not a
    #: browser quirk to re-measure at the next Chromium: the resource branch is entered
    #: only "If the `data` attribute is present and its value is not the empty string",
    #: and otherwise "The object element represents the element's children. This is the
    #: element's fallback content." (HTML Standard 4.8.7, fetched at RUNG 3). The other
    #: six members need a support/failed-load condition the page often controls, which is
    #: why their rendering is an empirical question and this one is not.
    #: Elements whose content renders only WITH an attribute: `<dialog>` without
    #: `open` is not in the rendering at all, and `<details>` without `open` shows
    #: its `<summary>` and hides the rest. Presence is what counts — an empty `open`
    #: is the rendered spelling.
    #:
    #: ⛔ `_UNRENDERED` members whose FALLBACK text a browser DOES paint, measured
    #: rather than argued: an `<object>` with no usable resource renders its children,
    #: so the TEXT question must count them. The BOUNDARY question still refuses an
    #: `<object>` ancestor — that check is by TAG in `_unmet_by_reader`, not by this
    #: flag — which is the loud direction there (a heading in a fallback slot does not
    #: bound the slice), so admitting the text costs nothing on that side.
    #:
    #: ⛔ ADMITTED BY TAG, UNCONDITIONALLY — the `data`-conditional in the sentence above
    #: is the NORMATIVE REASON the member is here, not a test this code applies
    #: (#14073 review round 2, P3). `<object data="x.svg">` whose resource LOADS paints
    #: none of its children (measured: 8000 painted px for a bare `<object>`, 0 for a
    #: loadable one, 8000 again for a 404), so counting it unconditionally is an
    #: over-count in the FALSE-RED direction, inside a pin that refuses `<object data>`
    #: outright via the attribute allowlist (measured: `['<object data>']`). Named rather
    #: than implemented because a `data`-aware rule would be a second rendering model for
    #: a spelling the reviewed section cannot carry — and if that allowlist is ever
    #: widened, this is the residual the widening inherits.
    #:
    #: ⛔ REPRODUCED INDEPENDENTLY at this head, with the box's own `/usr/bin/google-chrome`
    #: driven by Playwright — a 200x40 red block as the child, red pixels counted in the
    #: screenshot: `<object>` 8 000 vs 0 without it, and 0 for each of the other six. An
    #: earlier run that compared full screenshots reported `<progress>` as painting its
    #: children; that was the indeterminate bar's ANIMATION, not the text, and the
    #: red-block method is what distinguishes the two.
    _FALLBACK_RENDERS = frozenset({"object"})
    # ⛔ THE WHOLE CLOSED SUBTREE IS REFUSED FOR THE BOUNDARY QUESTION, `<summary>`
    # INCLUDED — and that is a DELIBERATE over-refusal in the LOUD direction, not an
    # oversight: a `<summary>` IS rendered by a closed `<details>` (this line used to
    # say so while the code refused it, #14073 review), so a heading inside one does
    # not bound the slice and the extra text stays pinned. Modelling "the FIRST
    # summary of a closed details renders, a second one does not" is not attempted:
    # recording a heading in that second summary would BOUND the slice at something no
    # reader meets — the fail-open direction this whole rule exists to close.
    #
    # ⛔ BUT THE TEXT QUESTION RUNS THE OTHER WAY, AND THERE THE FLAG IS SIMPLY NOT
    # MODELLED (#14073 review rounds 6-9). `<summary>`'s text IS painted, so refusing it
    # in the TEXT recording DROPS rendered copy — a claim inside a closed
    # `<details><summary>` left every `#storage` pin green. Four review rounds tried to
    # model exactly which part of a disclosure a browser paints (the summary slot and
    # not the body), and each round found the next shape that model got wrong — every
    # one of them a fail-open: the direct-child summary, one inline wrapper deeper, an
    # intervening `<p>`/`<td>`/`<table>`, a `<p>`'s descendants, and six tokens the tree
    # builder renames or voids. So `_text_is_painted` takes the LOUD side instead: the
    # first unrendered ancestor decides, and when it is one of THESE the text is
    # COUNTED whatever the browser does with the body. A disclosure inside the pinned
    # section therefore has to match the golden — a reviewed copy change, not a hiding
    # place.
    #
    # The BOUNDARY inheritance this frozenset drives stays total for
    # `_unmet_by_reader`: the closed `<details>`'s own `False` never leaves the stack,
    # and `any(not renders …)` still refuses a heading anywhere under it, so
    # `("summary of a closed details", '<details><summary><h2 id="qs">Q</h2></summary>
    # </details>')` keeps bounding nothing.
    #: Elements that paint their TEXT CONTENT as a label, whatever wrappers sit inside
    #: them (#14073 review round 11, measured). `<select><option><audio>CLAIM` renders
    #: `CLAIM` as the option's label (the label is the option's text content, not its
    #: children's boxes), while `audio` on its own paints nothing — so the wrapper is
    #: NOT an unrendered ancestor in the ordinary sense. Guarding on the frame's OWN
    #: inherited flag is what keeps the exceptions honest: `<div hidden><select>
    #: <option>` still drops (the option inherited `False`), and so does a `<datalist>`
    #: option, because `datalist` is `_UNRENDERED` and the option inherits it.
    _LABEL_RENDERS = frozenset({"option", "optgroup"})
    _RENDERS_WITH_ATTR = frozenset({"details", "dialog"})
    #: Element types whose CHILD HEADINGS must not bound the slice, while their own
    #: DIRECT TEXT may still be counted — the third pair of answers one flag must not
    #: give, after `<summary>` and `<object>` (#14073 review, round 8).
    #:
    #: * `<select>` — the conservative side of a spec change, stated at length on
    #:   `_record_heading`; ancestry, so `<optgroup><option>` under it is covered.
    #: * `<option>` — no insertion mode involved and nothing unsettled: a `<h2>`
    #:   inside a BARE `<option>` is not rendered (`<option><h2>HELLO</h2></option>` →
    #:   heading rect 0x0, 0 ink px; an `<option>TAIL</option>` control paints 244), and
    #:   the rule that missed it (`_unmet_by_reader` asked about `<select>`,
    #:   `_UNRENDERED`, `hidden` and `<details>`) let the heading bound the slice. The
    #:   refusal is BOUNDARY-ONLY on purpose — the option's own text IS rendered, so
    #:   adding `option` to `_UNRENDERED` would DROP painted copy, the text-question
    #:   fail-open this file keeps paying for. Inside a `<select>` the heading's box is
    #:   still 0x0 but its TEXT is flattened into the rendered option label (453 ink px
    #:   for the same markup) — which the `<select>` ancestor rule already refuses, and
    #:   which is also why the TEXT recording must go on counting it.
    #:
    #: ⛔ `<optgroup>` IS NOT A MEMBER, and an earlier revision of this comment claimed
    #: it was by saying a heading inside "one of them" is not rendered. MEASURED FALSE
    #: for a BARE `<optgroup>`: `<optgroup><h2>HELLO</h2></optgroup>` gives the heading
    #: a 1000x27 rect and 798 ink px on a white UA page (an empty `<optgroup>` control:
    #: 0), and on a `docs.html` copy 812x38 — against 812x0 with no paintable box when
    #: the heading is empty. Only `<optgroup><option><h2>…` is 0x0 — and `option`
    #: already covers that, as does the `<select>` ancestor rule for the spelling a real
    #: page uses. Refusing `optgroup` therefore refused a heading a reader DOES meet:
    #: the loud direction (a wrongly long slice), and the honest fix is to drop the
    #: member rather than keep an over-refusal resting on a false sentence (#14073
    #: review rounds 9-10, measured — the harness is a Playwright element screenshot
    #: with ink = any channel < 250, so every number here is re-runnable).
    #:
    #: The end-to-end escape this set closes, measured through `_docs_section` on a page
    #: copy: a TOC entry for `#storage-limits` plus
    #: `<option><h2 id="storage-limits">Limits</h2>FalkorDB … the allowance … tier…`
    #: put the claim outside the slice with the golden, the vocabulary pin, the
    #: destinations pin and the attribute allowlist ALL green.
    _UNBOUNDABLE_ANCESTORS = frozenset({"select", "option"})
    #: Every recording of a DOCUMENT TREE, by attribute name. `_clear_recordings`
    #: iterates this tuple, and
    #: `test_the_parsers_recordings_forget_a_document_a_frameset_replaced` asserts it
    #: covers every public list-valued attribute on the parser — so a recording added
    #: later fails THAT test until it is declared here, instead of silently keeping
    #: entries from a tree a browser has thrown away.
    _RECORDINGS = (
        "hrefs", "ids", "attrs", "headings", "containers", "mode_dependent",
        "text",
    )
    #: Diagnostic ratchets, NOT document recordings: they describe what the PARSE met
    #: (`foreign_tags` is the declared-limits watchdog), so an honored `<frameset>`
    #: does not clear them — they are evidence about the read, not about the tree.
    _DIAGNOSTICS = ("foreign_tags", "foreign_unmatched_endtags", "foreign_root_mismatch")

    def __init__(self, *, container: tuple[str, str] | None = None) -> None:
        """`container` = `(tag, class token)` to collect each one's anchors for.

        Off by default because the question is only asked by the docs page's
        section bound (`extract_anchors_within`, #14073 review); the blog and id
        guards ask questions an ancestry does not answer.
        """
        super().__init__(convert_charrefs=True)
        # `<noscript>` is raw text only with scripting ENABLED — the browser
        # default, and the only mode this guard's question is asked in. The stdlib
        # defaults it OFF and would parse `<noscript>` as markup, counting an
        # anchor no browser renders. Set as an attribute rather than a constructor
        # argument because only `parse_starttag` consults it (and the `_RAW` entry
        # still covers the case where the attribute is absent).
        self.scripting = True
        self.hrefs: list[str] = []
        # `(id, line)` of every element a browser would put in the document tree,
        # in document order — see the class docstring's "Ids" bullet (#4118).
        self.ids: list[tuple[str, int]] = []
        # `(tag, attribute name, line)` for every attribute on an element in that
        # tree, in document order — the allowlist guard's input (#14073). See
        # `_record_attributes` for why this is the parser's answer and not a
        # source pattern's.
        self.attrs: list[tuple[str, str, int]] = []
        # `(tag, line)` of every suppressed region whose rendering depends on the
        # SCRIPTING MODE — see `_MODE_DEPENDENT`. The attribute allowlist's input:
        # it must refuse such a region instead of clearing it (#14073 review).
        self.mode_dependent: list[tuple[str, int]] = []
        # The TEXT NODES a reader meets, in document order — one entry per text node
        # in the rendered tree, tags gone and entities decoded by the tokenizer. A
        # DOCUMENT-TREE recording (declared in `_RECORDINGS`, so an honoured
        # `<frameset>` clears it with the rest): the wording golden's input, and the
        # one question in this module that has to be answered by the tree rather than
        # by the source, because the source cannot distinguish `<<` from a tag
        # (#14073 review, P0 — see `extract_visible_text`).
        self.text: list[str] = []
        # The `href` value of the FIRST `<base>` the browser parses, or None. The
        # document's BASE URL is not its own URL once a `<base href>` is present,
        # and every relative/fragment-only link resolves against it — so a
        # same-page question answered against the canonical URL alone is a
        # question a browser answers differently (#14073 review, P2). Recorded in
        # `_record_base` under the same suppression rule as every other answer:
        # a `<base>` inside `<template>`/`<script>` is not one the browser applies.
        self.base_href: str | None = None
        # `(id, line, column)` of every `<h2>` a browser would put in the document
        # tree, in document order — the docs page's section bound's input (#14073
        # review). A POSITION and not the tag text, because the bound has to cut
        # the raw SOURCE at a heading; `extract_heading_positions` turns the line
        # and column into an offset.
        self.headings: list[tuple[str | None, int, int]] = []
        # The container whose anchors the caller asked for, and the two recordings
        # that answer it: `containers` holds one href list per MATCHING CONTAINER
        # the tree has, in document order, and `_open_containers` holds the ones
        # still open (a stack, because an anchor belongs to every container around
        # it). See `extract_anchors_within` for why an href list alone cannot
        # answer "which anchors are inside this element".
        self._container = container
        self.containers: list[list[str]] = []
        self._open_containers: list[list[str]] = []
        # Open elements, as `(tag, opened a container, renders)`, in document
        # order — the ancestry the container recording, the `in select` rule and
        # the "does a reader meet it" rule below all read. The third field is
        # INHERITED from the ancestry (`<div hidden><div><h2>` hides the heading),
        # so a subtree hidden by an attribute carries the answer without a second
        # walk. See `_open_extent` for what it does NOT model.
        self._open_elements: list[tuple[str, bool, bool]] = []
        # Open elements currently suppressing collection. A stack, not a counter,
        # so nested `<template><script>…</script></template>` unwinds correctly.
        self._suppress: list[str] = []
        # Open foreign-content ROOTS; `_foreign` is the depth (see the property). A
        # stack rather than a bare counter so `</g>` (a nested foreign element this
        # model does not track) is distinguishable from `</math>` while an `<svg>`
        # root is open — the cross-namespace case in #3970 item 5, which a browser
        # ignores and a bare counter mis-resolved by leaving foreign content.
        self._foreign_roots: list[str] = []
        self._body_seen = False
        self.foreign_tags: list[str] = []
        self.foreign_unmatched_endtags: list[str] = []
        self.foreign_root_mismatch: list[str] = []
        self.wedged_reason: str | None = None

    @property
    def _foreign(self) -> int:
        """Foreign-content depth, derived from the open-root stack."""
        return len(self._foreign_roots)

    @_foreign.setter
    def _foreign(self, value: int) -> None:
        # Only 0 is ever assigned (leaving foreign content entirely); the depth is
        # the stack's length, so the write clears it.
        if value == 0:
            self._foreign_roots.clear()

    @property
    def _support_cdata(self) -> bool:
        """True only in FOREIGN content, where ``<![CDATA[`` really is a section.

        ``HTMLParser`` ships this as an unconditional ``True``, so it reads
        ``<![CDATA[ … ]]>`` as CDATA in HTML content too — where a browser makes it a
        BOGUS COMMENT ending at the first ``>``. With no ``]]>`` anywhere, the parser
        then swallowed the rest of the document and missed real links
        (``<div><![CDATA[</div><a href="/blog">`` is `['/blog']` in Chromium and `[]`
        here — review finding, #3962). Deriving the flag from the foreign context
        fixes both directions. A setter is defined because the stdlib assigns it
        during ``reset()``; the derived value is the authority, so the write is
        accepted and ignored.
        """
        return self._foreign > 0

    @_support_cdata.setter
    def _support_cdata(self, flag: bool) -> None:
        # The stdlib's write is accepted and ignored — the derived value above is the
        # authority. `flag` is unused by design.
        del flag

    def _record_id(self, attrs: list[tuple[str, str | None]]) -> None:
        """Record an element's OWN id, when that element is in the tree.

        Suppression in this class is about an element's CONTENT, so this is
        called before ``_open`` pushes the region — `<style id="x">` and
        `<template id="x">` are both real elements to ``getElementById``. A
        start tag inside such a region is not markup the browser parses, either
        because the tokenizer is in raw-text mode or because the content is
        inert, so the guard reads the stack as it stood for the enclosing
        element and skips those.

        A wedged parse records nothing further, for the reason ``_collect``
        stops: the model is known wrong past that point. What makes that safe is
        the page-level ratchet — `_all_website_pages()` is checked for the
        seam's declared limits in `tests/test_website_docs_consistency.py`, so a
        covered page that wedges fails loudly instead of silently reporting
        fewer ids.
        """
        if self._suppress or self.wedged_reason:
            return
        for name, value in attrs:
            if name == "id":
                # FIRST id wins and an empty one is no id: the stdlib hands over
                # both attributes of a duplicate pair while a browser keeps the
                # first, so `id="" id="x"` leaves the element with no id; and
                # `getElementById("")` matches nothing, so two `id=""` elements
                # are not a collision any script can observe.
                if value:
                    self.ids.append((value, self.getpos()[0]))
                return

    def _record_base(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        """The FIRST `<base href>` a browser applies, or leave it None.

        Only a `<base>` that is actually parsed can move the base URL, so this
        runs on the same path as `_collect` — a `<base>` inside a suppressed
        region (`<template>`, a comment, a `<script>` body) never reaches here and
        must not move the base.

        Only the FIRST one counts: a later `<base href>` is ignored by the
        tokenizer/URL rules the page is read with, so honouring it would compute a
        base no browser uses — a fail-open on the same-page question this module
        answers.

        ⛔ THE GUARD IS `self._suppress`, NOT THE CALLER'S `suppressed` FLAG. That
        flag answers "does this element's CONTENT suppress" (true for `<script>`
        itself), while the question here is "is this element INSIDE a region the
        browser never renders" — a `<base>` inside an inert `<template>` reports
        `suppressed=False` at its own start tag and would still move the base.
        Same rule, and same reasoning, as `_collect` (measured: the template case
        silently relocated the base).
        """
        if tag != "base" or self.base_href is not None:
            return
        if self._suppress or self.wedged_reason:
            return
        for name, value in attrs:
            if name == "href":
                self.base_href = value or ""
                return

    def _record_attributes(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        """Every attribute NAME on an element the browser puts in the tree.

        Same placement and same suppression rule as ``_record_id``: the element
        is in the tree, so its own attributes count even where its CONTENT is
        inert, while a start tag inside a suppressed region is not markup the
        browser parses and is skipped (an attribute name written inside a
        comment or a ``<script>`` body is not a reader-facing attribute, and a
        source pattern would count it).

        Why the parser rather than a regex over the raw source: WHATWG's HTML
        syntax allows whitespace around ``=`` and lets an attribute name hold
        digits and ``_``, so ``title = "…"``, ``data-30day="…"`` and
        ``data_del="…"`` are all real, rendered attributes — and all three
        walked past the ``\\s([a-zA-Z][a-zA-Z-]*)=`` pattern this replaced, each
        carrying a reader-facing claim the visible-text golden cannot see
        (#14073 review). The tag is kept alongside the name because an allowlist
        is per-element in practice and it costs nothing to keep.

        ⛔ `<noscript>` IS THE ONE REGION THIS CANNOT ANSWER, AND THE CALLER MUST
        REFUSE IT — not clear it. `<noscript>` is suppressed because
        `self.scripting` is True (the attribute, not a line number — this reference used
        to name one and went stale when the file grew; #14073 review round 8, P2), so an
        attribute on an element INSIDE
        it is not recorded: a browser with JS does not render it, and a `title`
        there reaches only a reader with JS DISABLED — where the children ARE
        parsed as markup (MDN, quoted on `_MODE_DEPENDENT`). The two questions
        cannot both have `<noscript>` their own way in one pass (the anchor
        contract needs the no-JS-only anchor to stay invisible), so the limit is
        DECLARED here and GATED at the seam: `extract_mode_dependent_regions`
        reports each region, and `_attributes_outside_allowlist` refuses it rather
        than reporting the page clean (#14073 review).
        """
        if self._suppress or self.wedged_reason:
            return
        line = self.getpos()[0]
        for name, _value in attrs:
            self.attrs.append((tag, name, line))

    def _record_heading(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        """The `id` of every `<h2>` the browser puts in the tree, with its position.

        Called AFTER ``_open``, because whether a heading renders depends on where
        it is: `<template><h2 id="api"></h2></template>` contributes no heading
        (the template's content is inert), and a `<h2>` written inside a `<script>`
        body is text, not markup. A raw `<h2` pattern cannot see either, and the
        section bound built on it cut the section at a heading no reader meets —
        leaving the text after it outside every pin, measured with the golden, the
        vocabulary and the attribute allowlist all green (#14073 review).

        ⛔ NOT because foreign content is skipped wholesale — `h1`–`h6` are
        HTML5's foreign-content BREAKOUT tags (spec 13.2.6.5), so
        `<svg><h2 id="x">` exits the SVG and IS a rendered heading; `_open`
        applies that rule before this runs and the heading is recorded, which is
        the browser's answer. A `<h2>` that is genuinely foreign cannot exist,
        and the tags that would be (`<svg>`/`<math>` subtrees with no breakout)
        keep `_foreign` truthy so their `<script>`/`<template>` children stay
        NON-inert — the opposite of the HTML rule, and #3962's Chromium-verified
        finding.

        ⛔ REFUSED, AND DELIBERATELY ON THE CONSERVATIVE SIDE OF A SPEC CHANGE. The
        classic token-parsing rules had an "in select" insertion mode that dropped
        every other start tag, so `<select><h2 id="quickstart">Q</h2></select>` put
        NO heading in the tree — and counting one bounded the slice at a heading no
        reader meets, measured with the claim after it outside the golden, the
        vocabulary pin and the allowlist at once (#14073 review). The current living
        standard no longer lists that mode at all (its insertion-mode sequence goes
        from "in cell" to "in template"), and under the replacing model a `<h2>`
        inside a `<select>` may well be created — so "a browser has no heading here"
        is browser-dependent, not settled. Both answers cannot be had, and only one
        of them is safe: BOUNDING at a heading the pins cannot see is the fail-open
        direction (the claim under it leaves every pin), while refusing to bound
        leaves the slice long and the extra text pinned. So a `<h2>` with an open
        `<select>` ANCESTOR is not recorded — ancestry, so `<optgroup><option>`
        inside it is covered too. The cost is declared: a rendered heading inside a
        select does not end the slice.

        ⛔ AND `<option>` REFUSES FOR A MEASURED REASON, not a spec question —
        `<select>` is the only member here whose answer is unsettled. A `<h2>` inside
        a BARE `<option>` (no select above it) had a 0x0 rect and painted 0 px in this
        box's Chrome, while the option's own direct text painted 244 px — so leaving
        `option` out let a decoy heading no reader meets bound the slice, with the
        claim under it outside the golden, the vocabulary pin, the destinations pin
        and the attribute allowlist at once (measured on a page copy, #14073 review
        round 8). Both members live in `_UNBOUNDABLE_ANCESTORS`, and that set is the
        BOUNDARY question ONLY: the option's text is rendered, so it stays in the
        TEXT recording.

        ⛔ AND `<optgroup>` WAS IN THAT SET FOR ONE ROUND AND CAME OUT — a bare
        `<optgroup>` DOES render its child heading (measured: 798 ink px on a white UA
        page against 0 for an empty `<optgroup>` control, 812x38 against 812x0 on a
        `docs.html` copy; only `<optgroup><option><h2>` is 0x0, and `option` covers
        that), so refusing it refused a heading a reader meets. An over-refusal is the
        loud direction and therefore safe, but it rested on a sentence this measurement
        falsifies, and an unmeasured member in a measured set is how the next reader is
        misled (#14073 review rounds 9-10).

        ⛔ AND "INSIDE A SELECT" IS THE SPEC'S QUESTION, NOT THIS STACK'S. The
        tokens that close the scope in the LIVING standard are `<input>` and a
        nested `<select>` — see `_SELECT_POP` for the citations, and for the two
        tokens that were copied from the removed classic mode and had to be taken
        back out. It was declared as an unmodelled residual in the previous
        revision, which is what let a `<h2>` after a `<select><input>` be treated as
        one no reader meets (#14073 review); the direction was safe there (the
        slice stayed long) but the reading was still wrong, and the same wrong
        reading dropped a real anchor from the rendered-link set, which is the LOUD
        direction.

        ⛔ AND "NO READER MEETS IT" IS WIDER THAN INSERTION MODES — the rule is
        `_unmet_by_reader`, which also refuses an `_UNRENDERED` ancestor
        (`<datalist>`, and the fallback-content elements: their children are never
        rendered by a supporting browser), a `hidden` attribute on this element or
        an ancestor, and `<details>`/`<dialog>` before an `open` arrives. Each was
        measured recording a `<h2 id="quickstart">` that then bounded the slice,
        leaving the claim under it outside all three pins (#14073 review) — the
        failure mode this docstring's first paragraph describes, reached without
        touching the TOC. What is declared and NOT modelled is CSS: a heading
        hidden by `style`, or by a stylesheet rule its `class` matches, is still
        recorded and still bounds, because this parser reads markup and a cascade
        is not markup. That residual is in the fail-open direction, and it is named
        here instead of being left for the slide after next.

        The id is kept rather than the whole tag because the caller asking this
        question is "which heading does the section end at", which is a question
        about identity; the position is kept because it is the only way to cut the
        source at the tag the tokenizer actually parsed.
        """
        if tag != "h2" or self._suppress or self.wedged_reason or self._foreign:
            return
        if self._unmet_by_reader(attrs):
            return
        heading_id = next((value for name, value in attrs if name == "id"), None)
        self.headings.append((heading_id, *self.getpos()))

    def _unmet_by_reader(self, attrs: list[tuple[str, str | None]]) -> bool:
        """True when no reader meets this start tag, so it must not bound or advertise.

        The ONE test `_record_heading` and `_collect` ask, kept in one place so the
        two cannot answer differently: a claim parked in a heading this refuses and
        an anchor parked there are the same escape (an advertised boundary, or an
        advertised entry, that the pins cannot verify).

        Three reasons, all of them "the pins cannot see what the reader also cannot
        see" — refusing is what keeps the slice LONG, i.e. the loud direction:

        * an open `_UNBOUNDABLE_ANCESTORS` ancestor — an open `<select>` (the
          conservative side of a spec change, for the reason `_record_heading` states
          at length), and `<option>`, whose child headings are MEASURED unrendered
          (0x0 rect, 0 painted px) while its own direct text paints — so it is refused
          HERE and deliberately not in `_UNRENDERED`;
        * an open `_UNRENDERED` ancestor — `<datalist>` and the conditional-fallback
          elements put their children somewhere a supporting browser does not
          render;
        * THIS element, or an open ancestor, declaring itself unrendered — the
          `hidden` attribute, or `<details>`/`<dialog>` before an `open` attribute
          arrives (`_RENDERS_WITH_ATTR`, carried on the stack as the inherited
          third field). A closed `<details>`'s `<summary>` is caught by this too:
          it IS rendered, so that is a deliberate over-refusal in the loud
          direction, stated at `_RENDERS_WITH_ATTR`.

        ⛔ THIS IS THE BOUNDARY QUESTION, and it is deliberately WIDER than the
        LINK question `extract_anchor_hrefs` asks: `_collect` applies this rule only
        to the container recording, while the rendered-link set keeps its own,
        narrower refusal. A link inside a `hidden` banner is one the page's own
        script can reveal (`website/product.html` un-hides `#oauth-error-retry` with
        `el.hidden = false`), and dropping it would blind the blog guard to a link a
        visitor can click (#14073 review, measured).

        ⛔ DECLARED RESIDUAL — CSS. A heading hidden by `style="display: none"`, by
        a stylesheet rule its `class` matches, or by anything else the cascade
        decides is still RECORDED, and therefore still BOUNDS: this parser reads
        markup and cannot compute a cascade. The direction is the fail-open one, so
        it is named rather than left implicit — the guard's own vocabulary and
        attribute pins are what a CSS-hidden heading's copy faces, and a page that
        hides copy with CSS is making a decision no markup-level guard can read.
        """
        if any(
            open_tag in self._UNBOUNDABLE_ANCESTORS
            for open_tag, _, _ in self._open_elements
        ):
            return True
        if any(open_tag in self._UNRENDERED for open_tag, _, _ in self._open_elements):
            return True
        if any(not renders for _, _, renders in self._open_elements):
            return True
        return any(name == "hidden" for name, _value in attrs)

    def _in_select(self) -> bool:
        """True when an open `<select>` ancestor makes this start tag no link at all.

        The LINK question's own test, and the one `_collect` still asks about
        `self.hrefs` — an `<a>` inside an OPEN `<select>` is not an element a
        reader can follow, in any browser, under any cascade. ⛔ "Open" is the
        load-bearing word: `_SELECT_POP` closes the scope on the token a browser
        closes it on, so this answers the browser's question and not merely this
        stack's. The BOUNDARY question is wider
        and lives in `_unmet_by_reader`; an anchor that fails only the wider test
        (inside a `hidden` banner, say) is still a link the page's own script can
        reveal, so it stays in the rendered-link set and is dropped from the
        container index only.
        """
        return any(open_tag == "select" for open_tag, _, _ in self._open_elements)

    def _close_p_scope(self, tag: str) -> None:
        """Close an open `<p>` when `tag`'s start tag closes one.

        Spec "in body", `summary` (among others, see `_P_CLOSERS`) — *"If the stack
        of open elements has a `p` element in button scope, then close a `p`
        element"* — and "close a `p` element" pops the `<p>` AND every element opened
        inside it.

        ⛔ WHY THE WHOLE CASCADE MATTERS, NOT JUST THE `<p>` ITSELF (#14073 review
        round 9, P1, measured). The stack keeps a phrasing element opened INSIDE the
        `<p>`, so in `<details><p>Tap to read more<span><summary>CLAIM</summary>` the
        `<span>` sat between the `<details>` and the `<summary>`. The painted-summary
        carve-out that was then still in place looked past only elements a browser
        never keeps, the `<span>` is not one, so the walk stopped on it and inherited
        `renders=False` — while Chromium
        pops the `<p>` and the `<span>` together and makes the `<summary>` a DIRECT
        child of the `<details>` (measured: `summary.parentElement` is `DETAILS`, rect
        1264×18, `innerText` contains the claim). `extract_visible_text` returned `''`
        for that shape and `'<p>Tap to read more<b><summary>CLAIM'` likewise: a painted
        claim invisible to every `#storage` pin.

        The same three guards the rest of the ancestry reads use: a token inside a
        suppressed region is template content or raw text, a wedged parse is already
        refusing, and foreign content is not "in body" at all.
        """
        if tag not in self._P_CLOSERS:
            return
        if self._suppress or self.wedged_reason or self._foreign:
            return
        for open_tag, _container, _renders in reversed(self._open_elements):
            if open_tag == "p":
                self._close_extent("p")
                return
            if open_tag in self._P_SCOPE_BOUNDARY:
                return

    #: The spec's "generate implied end tags" list (#14073 review round 11, P0).
    #: These are popped by an `rt`/`rp`/`rb`/`rtc` start tag, and each is ALSO closed
    #: by a sibling start tag of its own kind. Leaving one on the stack keeps an
    #: `_UNRENDERED` element above it alive, and `_text_is_painted` then DROPS text a
    #: browser paints — the fail-open direction, not the loud one.
    _IMPLIED_END_TAGS = frozenset({"dd", "dt", "li", "optgroup", "option", "p", "rb",
                                   "rp", "rt", "rtc"})

    def _close_implied(self, tag: str) -> None:
        """Pop the elements a `tag` start tag implicitly closes.

        ⛔ MODELING ONLY `<p>` WAS NOT ENOUGH (#14073 review round 11, P0, measured).
        The spec closes a whole family of siblings on their own start tag, and each
        omission left a painted claim unreadable:

        * `<ul><li><audio><li>CLAIM</li></audio></li></ul>` — the second `<li>` closes
          the first one AND the `<audio>` inside it (Chrome:
          `<li><audio></audio></li><li>CLAIM</li>`, 23 817 dark px), so the claim is
          painted; this stack kept `audio` (`_UNRENDERED`) and dropped it.
        * `<ruby><rp><rt>CLAIM</rt></rp></ruby>` — `rt`/`rp`/`rb`/`rtc` start tags run
          "generate implied end tags", which pops the `<rp>` (Chrome:
          `<ruby><rp></rp><rt>CLAIM</rt></ruby>`, 5 997 dark px), same drop here.
        * `<dl><dd><audio><dd>CLAIM`, `<ul><li><progress><li>CLAIM` — same shape.

        LOUD WHERE IT CANNOT BE EXACT: the sibling close pops through the NEAREST open
        element of that kind, whatever is above it, which is what the spec's "pop until
        an `li` has been popped" does. Over-popping only REMOVES ancestors, so it can
        turn a drop into a count and never the reverse.
        """
        if self._suppress or self.wedged_reason or self._foreign:
            return
        targets = {
            "li": ("li",),
            "dd": ("dd", "dt"),
            "dt": ("dd", "dt"),
            "option": ("option",),
            "optgroup": ("option", "optgroup"),
        }.get(tag)
        if targets:
            self._close_nearest(targets)
            return
        if tag in ("rb", "rp", "rt", "rtc"):
            # "Generate implied end tags": pop while the CURRENT node is one of them.
            while self._open_elements and self._open_elements[-1][0] in self._IMPLIED_END_TAGS:
                self._pop_top()

    #: "Clear the stack back to a table context", and its body/row variants (#14073
    #: review round 11, P0, measured). A table-structure start tag discards whatever
    #: the "in table" mode had foster parented out: `<table><audio><td>CLAIM` renders
    #: `<audio></audio><table><tbody><tr><td>CLAIM</td>…` (Chrome, `td.closest
    #: ('audio')` is null, 34 735 dark px). Without this pop the `td` INHERITED
    #: `renders=False` from the `audio` that had already been moved out of the table,
    #: and the claim was dropped. Each entry maps a start tag to the nodes that may
    #: stay: everything above the nearest of them is discarded.
    _TABLE_CONTEXT_STOPS = MappingProxyType({
        "caption": ("table",),
        "colgroup": ("table",),
        "col": ("table",),
        "tbody": ("table",),
        "tfoot": ("table",),
        "thead": ("table",),
        "tr": ("table", "tbody", "tfoot", "thead"),
        "td": ("table", "tbody", "tfoot", "thead", "tr"),
        "th": ("table", "tbody", "tfoot", "thead", "tr"),
    })

    def _clear_table_context(self, tag: str) -> None:
        """Pop back to the context a table-structure start tag requires."""
        stops = self._TABLE_CONTEXT_STOPS.get(tag)
        if stops is None or self._suppress or self.wedged_reason or self._foreign:
            return
        for index in range(len(self._open_elements) - 1, -1, -1):
            if self._open_elements[index][0] not in stops:
                continue
            if index == len(self._open_elements) - 1:
                return
            for _open_tag, opened_container, _renders in self._open_elements[index + 1:]:
                if opened_container and self._open_containers:
                    self._open_containers.pop()
            del self._open_elements[index + 1:]
            return

    def _close_nearest(self, targets: tuple[str, ...]) -> None:
        """Pop through the nearest open element named in `targets`, if there is one."""
        for index in range(len(self._open_elements) - 1, -1, -1):
            if self._open_elements[index][0] in targets:
                for _open_tag, opened_container, _renders in self._open_elements[index:]:
                    if opened_container and self._open_containers:
                        self._open_containers.pop()
                del self._open_elements[index:]
                return

    def _pop_top(self) -> None:
        """Pop the current node, keeping the container index in step."""
        _open_tag, opened_container, _renders = self._open_elements.pop()
        if opened_container and self._open_containers:
            self._open_containers.pop()

    def _close_select_scope(self, tag: str) -> bool:
        """Pop an open `<select>` for a token that pops it; `True` when IGNORED.

        Spec "in body", the `input` and `select` start-tag rules — see
        `_SELECT_POP` for the citations and the measured divergence this closes.
        Called FIRST in `handle_starttag`, before this tag's own state lands, so the
        pop reads the PRE-tag ancestry — the ancestry the browser's own step reads.

        Returns True only for a token the spec says to IGNORE after popping (a
        NESTED `<select>`): the caller must then create no element for it, or the
        inner select the browser discarded would be pushed here and every anchor
        after it would stay suppressed by an element that does not exist.

        The same three guards the rest of the ancestry reads use: a token inside a
        suppressed region is template content or raw text, whose end tags and
        start tags a browser processes against that region's own stack (the reason
        a stray `</select>` written in a `<template>` does not pop this one), and
        foreign content has no select scope to pop.
        """
        if tag not in self._SELECT_POP:
            return False
        if self._suppress or self.wedged_reason or self._foreign:
            return False
        if any(open_tag == "select" for open_tag, _, _ in self._open_elements):
            # `select` and everything opened inside it, which is the spec's "pop
            # until a select element has been popped".
            self._close_extent("select")
            return tag in self._SELECT_POP_IGNORED
        return False

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._suppress or self.wedged_reason:
            return
        if self._in_select():
            # An `<a>` inside an OPEN `<select>` is not a link a reader can follow,
            # and it is not an entry the TOC's index may be built from — the same
            # escape as a heading there, one recording over (measured: an anchor
            # inside a `<select>` in the TOC still advertised `#storage-limits` and
            # bounded the section, all pins green; #14073 review). What makes
            # "open" true is `_SELECT_POP` popping the scope, so an anchor after a
            # `<select><input>` is correctly a link again.
            return
        # The BOUNDARY answer, which is wider than "is this a link": an anchor no
        # reader meets must not advertise a heading, but it is still a link for the
        # blog guard if the page's script can reveal it. See `_unmet_by_reader`.
        meets_reader = not self._unmet_by_reader(attrs)
        for name, value in attrs:
            if name == "href":
                # First href wins — the tokenizer keeps the first of a duplicate
                # pair. An empty/valueless first href is treated as no link here:
                # that is this guard's policy (an empty href only self-links),
                # not the tokenizer's rule.
                if value:
                    self.hrefs.append(value)
                    # ...and into EVERY container the anchor sits inside, which is
                    # the question an href alone cannot answer: "the TOC block's
                    # entries" is an ancestry, not a string match.
                    if meets_reader:
                        for inside_container in self._open_containers:
                            inside_container.append(value)
                break

    def _breaks_out(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        """True when `tag` ends the foreign context (spec 13.2.6.5)."""
        if not self._foreign:
            return False
        if tag == "font":
            return any(name in self._BREAKOUT_FONT_ATTRS for name, _ in attrs)
        return tag in self._BREAKOUT

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        """Track an opening tag. Returns True when it starts a suppressed region."""
        if self._breaks_out(tag, attrs):
            # A breakout tag ends the foreign context and is then processed by the
            # HTML rules — which is what makes a following `<template>` inert.
            self._foreign = 0
        if tag in self._FOREIGN:
            self._foreign_roots.append(tag)
            return False
        if self._foreign:
            # Inside foreign content every element is itself a foreign element, and
            # HTML's inert/raw-text rules do not apply to it: a foreign `<template>`
            # renders its children and a foreign `<script>` holds markup, not text
            # (review finding, #3962, Chromium-verified).
            self.foreign_tags.append(tag)
            return False
        if tag == "body":
            # Only a body that is actually IN the document counts: `<body>` inside an
            # inert `<template>` is template content, so the browser still has no
            # body, still enters frameset mode on a later `<frameset>`, and still
            # ignores the anchors there — recording it let the false pass below
            # through (review finding, #3962, Chromium-verified).
            if not self._suppress:
                self._body_seen = True
            return False
        if tag in self._RAW or tag in self._INERT:
            if tag in self._MODE_DEPENDENT and not self._suppress:
                # NOT inside another suppressed region: a `<noscript>` inside an
                # inert `<template>` is template content and renders in no mode.
                self.mode_dependent.append((tag, self.getpos()[0]))
            self._suppress.append(tag)
            return True
        if tag == "frameset" and not self._body_seen and not self._suppress:
            # In the “in frameset” and “after frameset” insertion modes every other
            # start tag is IGNORED (13.2.6.4), so an anchor there is never in the
            # tree — Chromium renders nothing for
            # `<frameset><a href="/blog">x</a></frameset>` while this extractor
            # counted the link, a false PASS. The mode never ends for this purpose,
            # so the reason is not cleared.
            #
            # Guarded on the body/suppression state, which covers the dominant cases:
            # a `<body>` that reaches the document clears the spec's frameset-ok flag,
            # and a `<frameset>` inside an inert `<template>` is template content, so
            # in both the token IS ignored and the anchors DO render.
            #
            # What is NOT modelled: the other tokens that clear frameset-ok (a
            # non-whitespace character, `<br>`/`<img>`/`<button>`/`<table>`/`<li>`/… ,
            # a non-`hidden` `<input>`, `<template>`, …). A browser ignores the
            # `<frameset>` after one of those, and this extractor still wedges — a
            # false FAIL, the LOUD direction, on an obsolete element that no covered
            # page uses. Emulating the flag was tried and cost a regression per round
            # for corner cases that cannot reach this guard; the residual is recorded
            # in #3970 instead.
            self.wedged_reason = "frameset"
            # An honored `<frameset>` REPLACES the body (13.2.6.4), so every anchor
            # already collected is gone from the document — keeping them would report
            # a link no visitor can click. Clearing at the source matters because the
            # E2E layer asks `blog_entry_hrefs` and never looks at `wedged_reason`
            # (review finding, #3962, Chromium-verified). One method rather than a
            # clear per recording: see `_clear_recordings` (#14073 review).
            self._clear_recordings()
            return True
        return False

    def _clear_recordings(self) -> None:
        """Forget every recording, because one document tree has been replaced.

        An honored `<frameset>` REPLACES the body, so a recording kept from before
        it describes a tree a browser has thrown away. This is a METHOD over a
        DECLARED TUPLE (`_RECORDINGS`) rather than a clear per recording because the
        invariant is "every recording describes the one tree": `attrs` and
        `headings` were added beside `hrefs`/`ids` and missed the clear, so
        `extract_element_attributes('<div title="x"></div><frameset>')` still
        reported an attribute from a document that no longer exists — the exact
        invariant `ids`' own clear was introduced for, with two exceptions its
        docstrings claimed did not exist (#14073 review).

        ⛔ AND THE RATCHET IS A TEST, not this comment: a recording added later is
        invisible to a tuple nobody updated, so
        `test_the_parsers_recordings_forget_a_document_a_frameset_replaced` asserts
        `_RECORDINGS` + `_DIAGNOSTICS` is EVERY public list on the parser — a
        recording added later reds there until it is declared, and declaring it here
        is what makes the clear apply. `text` was declared here but had NO ROW in that
        test (#14073 review round 6, P2): the declaration makes the clear HAPPEN
        without making it OBSERVED. The row this fix adds is the observer — patching
        this loop to skip `text` now reds that test (measured: `1 failed, 102 passed,
        1 skipped`, and the failure is the `text` row). The diagnostics are
        deliberately not cleared: they record what the PARSE met, not what the tree
        holds.
        """
        for name in self._RECORDINGS:
            getattr(self, name).clear()
        self._open_containers.clear()
        self._open_elements.clear()

    def _open_extent(
        self, tag: str, attrs: list[tuple[str, str | None]], suppressed: bool,
    ) -> None:
        """Push a rendered element onto the open-element stack the ancestry reads.

        Only elements a browser puts in the tree are pushed: a start tag inside an
        inert/raw region is not markup (the tokenizer is in raw-text mode or the
        content is template content), and foreign content is not descended into
        here at all — the foreign ROOT is not pushed either, so `</svg>` matches
        nothing and is ignored, which is what a browser does with an end tag that
        matches no open element.

        ⛔ NOT MODELLED: the HTML parser's IMPLICIT-CLOSE rules (a `<p>`, `<li>` or
        table section closed by the NEXT tag rather than by an end tag). What that
        costs depends on the tag, and the direction is NOT the same both ways —
        which is why `extract_anchors_within` REFUSES a container tag that has an
        implicit-close rule instead of documenting a direction for it:

        * for an ordinary container (`<div>`, `<section>`, `<nav>`), a leftover
          element on this stack is harmless: `_close_extent` searches the WHOLE
          stack for the container's tag and pops everything above it, so the
          container still closes at its own `</div>`;
        * for a container whose tag a SIBLING closes (an `<li>`, `<p>`, `<td>`…),
          the extent runs past the sibling — a LARGER advertised set, i.e. a
          SHORTER slice, i.e. the fail-OPEN direction. Refusing the tag is the only
          honest answer; a comment claiming the safe direction was measured wrong
          (#14073 review).

        An end tag written inside a suppressed region (raw text, `<template>`) does
        NOT close an outer element here — `handle_endtag` skips `_close_extent`
        while `_suppress` is non-empty, because a browser processes that end tag
        against the template's own fragment stack, which never holds these
        elements. That is the browser's answer for containers (the container stays
        open and its extent MATCHES the tree) and it is what keeps the `in select`
        refusal from being popped by a stray `</select>` written in a template
        (#14073 review, both directions measured).

        The third field is the inherited "a reader meets this element" flag that
        `_unmet_by_reader` reads — the `hidden` attribute and `_RENDERS_WITH_ATTR`
        elements before their attribute arrives. It is NOT where `_UNRENDERED`
        element types are refused: that refusal is the BOUNDARY question and
        `_unmet_by_reader` reads it from the TAGS, so the flag can stay a faithful
        answer to the TEXT question for a fallback slot a page controls itself
        (#14073 review round 7, P2 — see `_UNRENDERED`).
        It is INTERNAL to this stack: `ids` and `attrs` describe the document tree
        and are recorded regardless (an element is in the DOM whether or not CSS
        shows it), which is the distinction the class docstring draws.
        """
        if suppressed or self._suppress or self.wedged_reason or self._foreign:
            return
        if tag in self._VOID:
            return
        opened_container = False
        if self._container is not None:
            wanted_tag, wanted_class = self._container
            if tag == wanted_tag and wanted_class in _class_tokens(attrs):
                # The SAME list object goes into `containers` (document order) and
                # onto the open stack (`_open_containers`), so anchors appended
                # while it is open land in the recording without a second walk.
                inside: list[str] = []
                self.containers.append(inside)
                self._open_containers.append(inside)
                opened_container = True
        names = {name for name, _value in attrs}
        # The TEXT question's flag. `_UNRENDERED` element types are refused here
        # EXCEPT the members whose fallback a browser paints — `_FALLBACK_RENDERS`,
        # where the measured answer for `<object>` has a normative reason too (see
        # `_UNRENDERED`). The set is not dropped wholesale, and it is not admitted
        # wholesale: for the other six a supporting browser paints none of the fallback
        # text, so admitting it would red the golden on copy no reader meets, while
        # `<object>` paints all of it and dropping THAT was a fail-open (#14073 review
        # round 7, P2, both measured). The BOUNDARY question is unaffected either way:
        # `_unmet_by_reader` reads the TAGS.
        renders = not (
            "hidden" in names
            or (tag in self._UNRENDERED and tag not in self._FALLBACK_RENDERS)
            or (tag in self._RENDERS_WITH_ATTR and "open" not in names)
        )
        # Inherited, so `<div hidden><section><h2>` hides the heading without
        # `_unmet_by_reader` having to walk the stack to the `hidden` div.
        #
        # ⛔ EXCEPT A `<summary>` WHOSE PARENT IS THE CLOSED `<details>`: a closed
        # `<details>` DOES render its first `<summary>`, so refusing the whole
        # subtree is right for the BOUNDARY question (longer slice = loud) and WRONG
        # for the TEXT question (dropped summary text = a claim the wording golden
        # cannot see). Measured end-to-end before that: `<details><summary>FalkorDB
        # permanently deletes…</summary>Tap to read more.</details>` inserted into
        # `#storage` left the allowlist, the destinations, the vocabulary scan and the
        # wording golden ALL green while Chromium painted the summary (round 6, P1).
        #
        # ⛔ THE TEXT SEAM NO LONGER CARVES THE DISCLOSURE OUT AT ALL. Four rounds of
        # exception lists ("only the summary", "only a direct child", "only past
        # `_SUMMARY_TRANSPARENT` members") each shipped a new fail-open, so
        # `_text_is_painted` now COUNTS the whole disclosure family and this flag is
        # inherited for the BOUNDARY question only. The over-counted copy a reader does
        # not see is the declared COST; see `_text_is_painted` for the decision and
        # `_RENDERS_WITH_ATTR` for why these two questions must not share an answer.
        if self._open_elements and not self._open_elements[-1][2]:
            # Inherited (the sentence above says why). The TEXT seam deliberately does
            # NOT inherit a closed `<details>`/`<dialog>`'s flag this way — see
            # `_text_is_painted`.
            renders = False
        self._open_elements.append((tag, opened_container, renders))

    def _close_extent(self, tag: str) -> None:
        """Pop `tag` and everything opened after it, as a browser's end tag does.

        An end tag naming no open element is IGNORED (a browser's "any other end
        tag" does nothing when the stack holds no matching element), which is what
        makes the un-pushed foreign root and the un-pushed inert region terminals
        harmless: their end tags match nothing here.

        The cascade is the browser's: an end tag pops every open element above its
        match too, so this closes the containers it passes. That is why the stack
        holds `(tag, opened a container)` rather than a bare depth.
        """
        if self.wedged_reason:
            return
        for index in range(len(self._open_elements) - 1, -1, -1):
            if self._open_elements[index][0] != tag:
                continue
            for _open_tag, opened_container, _renders in self._open_elements[index:]:
                if opened_container and self._open_containers:
                    self._open_containers.pop()
            del self._open_elements[index:]
            return

    def set_cdata_mode(self, elem: str, *args: object, **kwargs: object) -> None:
        """Keep the HTML rules' raw-text tokenizer state out of foreign content.

        ``HTMLParser`` switches to CDATA/RCDATA on tag name alone, but that switch
        is a TREE-BUILDING decision: in HTML content the tree builder inserts a
        raw-text element, whereas inside ``<svg>``/``<math>`` the same tag is a
        foreign element and its content is markup again. Chromium renders
        ``<svg><script><a href="/blog">x</a></script>`` as a real link while the
        stdlib's unconditional switch made this extractor report no link at all
        (review finding, #3962, browser-verified). `set_cdata_mode` runs AFTER
        `handle_starttag`, so `_foreign` already reflects the just-opened element.
        """
        if self._foreign:
            return
        super().set_cdata_mode(elem, *args, **kwargs)  # type: ignore[arg-type]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # FIRST, and before `_open`/`_collect`/`_record_heading`, so each pop reads
        # the PRE-tag ancestry the browser's own step reads. The spec's block-start
        # rule (implied `</p>`) runs before the per-token select rules, and they do
        # not overlap: see `_close_p_scope` and `_close_select_scope`.
        self._close_p_scope(tag)
        # The sibling/implicit closes the spec's "in body" rules also perform
        # (an `<li>`/`<dd>`/`<dt>`/`<option>` closes its own kind, and an
        # `<rt>`/`<rp>`/`<rb>`/`<rtc>` generates implied end tags) — see
        # `_close_implied`. Before `_open`/`_record_heading`/`_open_extent`, for the
        # same reason as the `<p>` rule: each pop reads the PRE-tag ancestry.
        self._close_implied(tag)
        # And the table contexts: a table-structure tag discards what the "in table"
        # mode had foster parented out — see `_clear_table_context`.
        self._clear_table_context(tag)
        # A nested `<select>` is IGNORED whole: no element, no id, no extent (see
        # `_SELECT_POP_IGNORED`).
        if self._close_select_scope(tag):
            return
        # Before `_open`, because this tag's own id/attributes count even where
        # its CONTENT does not (see `_record_id`, `_record_attributes`).
        self._record_id(attrs)
        self._record_attributes(tag, attrs)
        suppressed = self._open(tag, attrs)
        if not suppressed:
            self._collect(tag, attrs)
            self._record_base(tag, attrs)
        # After `_open`, so a heading written inside a suppressed region is not
        # recorded (see `_record_heading`).
        self._record_heading(tag, attrs)
        # Last, so this tag's own ancestry (what `_record_heading` and `_collect`
        # read) is its PARENTS': a `<div class="toc">` is not inside itself.
        self._open_extent(tag, attrs, suppressed)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # In FOREIGN content the self-closing flag IS honored, so `<svg/>`/`<math/>`
        # is an empty element that closes at once — forwarding it to
        # `handle_starttag` would increment `_foreign` with nothing to decrement
        # it, disabling `<template>` suppression for the rest of the document
        # (a false pass, review finding). In the HTML namespace the flag is
        # ignored for a non-void element, so `<template/>`/`<script/>` genuinely
        # open and swallow what follows — deleting the `/` is what a browser does.
        if tag in self._FOREIGN:
            # A self-closing foreign root is an empty element, so its own id is
            # recorded here; the non-foreign path below delegates to
            # `handle_starttag`, which records it there. Recording on BOTH paths
            # would count a single element twice — `<svg id="d"/>` is ONE
            # element, and two ids on it is a duplicate a browser does not have.
            self._record_id(attrs)
            self._record_attributes(tag, attrs)
            self._collect(tag, attrs)
            return
        self.handle_starttag(tag, attrs)
        if tag in self._RAW and not self._foreign:
            # `parse_starttag` — never `handle_startendtag` — is the stdlib's only
            # caller of `set_cdata_mode`, so on THIS path a raw-text element was
            # opened without raw-text tokenising: its content was read as markup,
            # and a nested raw-text start tag pushed a second `_suppress` entry
            # that the single end tag could never unwind. Suppression then stuck
            # and every later anchor on the page was dropped — a false NEGATIVE,
            # and a regression for the tags added to `_RAW` here (review finding,
            # #3962, Chromium-verified: `<xmp/><xmp></xmp><a href="/blog">`).
            self.set_cdata_mode(tag)

    def handle_data(self, data: str) -> None:
        """Fail closed on the script-data ESCAPED states (13.2.5).

        Inside ``<script>``, ``<!--`` moves the tokenizer to the escaped state and a
        later ``<script`` to the double-escaped state, where ``</script`` no longer
        closes the element. ``HTMLParser``'s CDATA mode has no such states — it ends
        the text at the FIRST ``</script`` — so the parser would go on to read markup
        a browser keeps as script text and count a link that is not there:
        ``<script><!--<script></script><a href="/blog">`` is `[]` in Chromium and
        `['/blog']` here (review finding, #3962). That is a false PASS, the direction
        this guard exists to prevent, and it cannot be modelled with the token states
        available here — so the extractor stops collecting instead, and a page
        carrying it fails the guard loudly rather than passing silently. The trigger
        needs BOTH markers, so the ordinary `document.write('<script …>')` idiom is
        unaffected (it never contains ``<!--``).
        """
        if (
            not self.wedged_reason
            and self.cdata_elem == "script"
            and "<!--" in data
            and _SCRIPT_IN_SCRIPT.search(data)
        ):
            self.wedged_reason = "script-data-escaped"
        # The text a reader MEETS: outside every suppressed region (raw text, inert
        # `<template>` content, `<noscript>` under scripting) and inside a subtree
        # that renders (`_open_elements`' inherited flag: `hidden`, `_UNRENDERED`
        # element types except the `_FALLBACK_RENDERS` ones, and a
        # `_RENDERS_WITH_ATTR` element before its attribute arrives).
        #
        # ⛔ `_RAW_RENDERS` IS THE OTHER HALF, and it is a fail-open if dropped: the
        # raw-text family is suppressed for the TOKENIZER's sake, and three of its
        # members (`<textarea>`, `<xmp>`, `<plaintext>`) render their text anyway —
        # so "suppressed" is not "unreadable" for this question, and the recording
        # admits those and only those. A `<textarea>` inside an inert `<template>` is
        # still dropped, because `template` is in the stack too.
        #
        # ⛔ NOT `self._foreign`-gated, unlike the anchor and heading recordings: text
        # inside `<svg>`/`<math>` IS rendered (only markup inside it is read by
        # different rules), and dropping it here would be the fail-open direction for
        # the wording golden.
        #
        # ⛔ DECLARED RESIDUAL — FOREIGN TEXT THAT IS NOT PAINTED (#14073 review
        # round 6, P2, Chromium-verified). This paragraph used to say the opposite of
        # what the code does: a foreign `<title>`, `<desc>`, `<style>` and `<script>`
        # hold text a browser paints NONE of (measured: 0 non-background pixels in the
        # `<title>/<desc>` band of an inline `<svg>`), and an HTML integration point
        # (`<foreignObject>`) re-enters HTML, where `<template>` is inert again — yet
        # all of it is recorded here, because the parser tracks foreign content as a
        # DEPTH (`_foreign_roots`) and not as a per-element stack, so `handle_data`
        # cannot tell which foreign element it is inside. The direction is the FALSE
        # RED one (text a reader never meets moves the golden), not a fail-open, and
        # no covered page puts svg/math text near `#storage` — the page-level ratchet
        # fails any covered page that reaches foreign constructs at all (see
        # `foreign_tags` and #3970). Closing it properly means a foreign element stack
        # in `_open`/`_close_extent`; until then this is named rather than implied.
        if (
            not self.wedged_reason
            and all(tag in self._RAW_RENDERS for tag in self._suppress)
            and self._text_is_painted()
        ):
            self.text.append(data)

    def _text_is_painted(self) -> bool:
        """Is the text just read inside a subtree the wording pin should READ?

        ⛔ LOUD BY DESIGN: A CLOSED `<details>`/`<dialog>` DOES NOT HIDE TEXT HERE, and
        that is a template for how this seam answers every question it cannot settle.
        Three review rounds tried to model the browser exactly — "the summary slot is
        painted, its body is not" — and each round found the next shape that model got
        wrong, every one of them a FAIL-OPEN through all three `#storage` pins:

        * round 6: the direct-child `<summary>` was dropped (``<details><summary>``);
        * round 7: one inline wrapper deeper was dropped (``<summary><span>``);
        * round 8: an intervening element was dropped (``<details><p>…<summary>``),
          because this stack keeps tags the tokenizer has already closed, ignored or
          foster-parented — ``<td>``/``<tr>``/``<caption>``/``<colgroup>``/``<table>``;
        * round 9: a `<p>`'s DESCENDANTS (a `<summary>` start tag closes the `<p>` in
          button scope and pops everything above it) and six tokens the builder renames
          or voids (``image``, ``frame``, ``head``, ``basefont``, ``bgsound``, ``keygen``)
          — plus the opposite error, twenty members of the transparency set being
          elements the browser KEEPS, which counted unpainted copy (false red).

        The lesson is not "one more shape": a faithful tree-builder is a parser, not a
        test helper, and every attempt to approximate it left a hole a claim could be
        written through. So the seam takes the LOUD side for this whole family, exactly
        as `_RENDERS_WITH_ATTR` already does for the BOUNDARY question — there the loud
        side is a longer slice, here it is MORE TEXT:

        * the first unrendered ancestor decides;
        * if that ancestor is a `<details>`/`<dialog>`, the text is COUNTED whatever the
          browser does with its body. The cost is named: copy a reader only meets after
          opening the disclosure also has to match the golden, so adding one inside the
          pinned section is a deliberate, reviewed copy change rather than a silent
          edit. That is the safe direction for a wording pin — the alternative, dropping
          rendered copy, is how three P1s shipped;
        * any OTHER unrendered ancestor (`hidden`, `_UNRENDERED`, `_FALLBACK_RENDERS`)
          still drops the text, and those are the residuals the module declares.
        """
        frames = self._foster_truncated_ancestry()
        # A painted LABEL repaints its whole text content, so no wrapper inside it can
        # be the thing that decides — see `_LABEL_RENDERS`.
        if any(tag in self._LABEL_RENDERS and renders for tag, _c, renders in frames):
            return True
        for tag, _container, renders in frames:
            if renders:
                continue
            # The first unrendered ancestor decides, and this is that ancestor.
            return tag in self._RENDERS_WITH_ATTR
        return True

    def _foster_truncated_ancestry(self):
        """The ancestry that actually holds this text, FOSTER PARENTING included.

        ⛔ A BROWSER MOVES TEXT OUT OF A TABLE, THIS STACK DOES NOT (#14073 review
        round 10, P0, measured). In the "in table" insertion mode a non-whitespace
        character token is FOSTER PARENTED: it is inserted immediately BEFORE the
        `<table>` in the tree, so its real ancestry is the table's PARENT, and every
        element between them is discarded. `<table><audio><table>CLAIM</table></audio>
        </table>` therefore paints `CLAIM` at body level (Chrome's tree:
        `<audio></audio><table></table>CLAIM<table></table>`, 194 painted px) while this
        stack sees it under `audio` (`_UNRENDERED`) and dropped it — a 7×7 grid of
        `_UNRENDERED` inside `table>…>table` reproduced it, `object` the only exception.

        Approximating the insertion modes is unnecessary: truncating the ancestry at the
        table is LOUD, because it can only REMOVE ancestors, so it turns a drop into a
        count and never the reverse. The modes that end foster parenting — "in cell"
        (`td`/`th`) and "in caption" (`caption`) — are the ones where text stays inside
        the table, so those stop the truncation. FOSTER PARENTED ELEMENTS are the other
        half and are handled structurally, by `_clear_table_context`: a table-structure
        start tag discards them, so a `<td>` under a foster-parented `<audio>` inherits
        the TABLE's render flag and not the discarded element's.
        """
        frames = self._open_elements
        for index, (tag, _container, _renders) in enumerate(frames):
            if tag != "table":
                continue
            # OUTERMOST first, not innermost: a `<table>` start tag in the "in table"
            # mode CLOSES the table already open, so `<table><audio><table>CLAIM`
            # discards the `audio` and leaves one table at body level (measured).
            #
            # "In cell"/"in caption" ends foster parenting, so a cell/caption above
            # the table means the text is inside it and the ancestry is real — the
            # elements the "in table" mode had already foster parented OUT are popped
            # by `_clear_table_context`, which is why nothing has to be dropped here.
            if any(frames[k][0] in ("td", "th", "caption") for k in range(index + 1, len(frames))):
                continue
            # Text in the "in table" mode itself: foster parented in FRONT of the
            # table, so the table is not an ancestor either.
            return frames[:index]
        return frames

    def parse_comment(self, i: int, report: bool = True) -> int:
        """End an empty comment where the spec does (review finding, #3962).

        ``<!-->`` and ``<!--->`` close a comment immediately (13.2.5.43, the
        comment-start states). ``HTMLParser`` searches for a later ``--!?>`` FIRST
        and only falls back to the abrupt close when the document has none, so one
        of these spellings swallows everything up to the next ``-->`` — including a
        ``<template>``/``<script>`` a browser opens there. The suppressing element
        then never registers and a later anchor is counted, so
        ``<!--><template>--><a href="/blog">`` is `[]` in Chromium and
        `['/blog']` here: a false PASS, the one direction this guard exists to
        prevent.
        """
        raw = self.rawdata
        abrupt = 5 if raw[i + 4 : i + 5] == ">" else (6 if raw[i + 4 : i + 6] == "->" else 0)
        if abrupt:
            if report:
                self.handle_comment("")
            return i + abrupt
        return super().parse_comment(i, report)

    def handle_endtag(self, tag: str) -> None:
        # First, and before the foreign early-returns: an end tag closes whatever
        # open element it names, and the ancestry the container recording reads has
        # to see that even where the model resolves the foreign context differently.
        #
        # ⛔ EXCEPT WHILE SUPPRESSED. Inside a raw-text element the end tag is text
        # and never arrives; inside `<template>` it is processed against the
        # template's OWN fragment stack, which never holds the outer elements. In
        # both cases the browser leaves this document's open elements alone, and
        # closing them here was a fail-open the `in select` refusal could not
        # survive: `<select><template></select></template><h2 id="qs">` popped the
        # outer `<select>`, so a heading the rule exists to refuse was recorded and
        # bounded the slice (#14073 review, measured). Nothing is pushed onto the
        # stack while suppressed (`_open_extent` returns early), so an end tag that
        # arrives here has nothing legitimate to close.
        if not self._suppress:
            self._close_extent(tag)
        if tag in ("br", "p") and self._foreign:
            # Spec 13.2.6.5: `</br>`/`</p>` in foreign content pop it and are
            # reprocessed by the HTML rules (which close nothing here). Any OTHER
            # unmatched end tag leaves the foreign context alone — `<svg></div>`
            # keeps the foreign context, `<svg></p>` does not (review finding,
            # #3962, Chromium-verified).
            self._foreign = 0
            return
        if tag in self._FOREIGN:
            if self._foreign_roots and self._foreign_roots[-1] == tag:
                self._foreign_roots.pop()
            elif self._foreign:
                # `</math>` while `<svg>` is the open root, say: a browser leaves the
                # foreign context alone (the token matches no open element), and the
                # bare-counter model used to leave foreign content here — the
                # cross-namespace class in #3970 item 5. Recorded so the watchdog
                # fails on a covered page that reaches it (review finding, #3962).
                self.foreign_root_mismatch.append(tag)
            return
        if self._foreign:
            # An end tag for a nested foreign element ("</g>", "</desc>"), which this
            # model does not track individually and resolves correctly by ignoring.
            # Recorded anyway as a deliberate RATCHET: today's covered pages use
            # only self-closing SVG children, and a page that stops doing so is one
            # edit away from the cross-namespace case above (review finding, #3962).
            self.foreign_unmatched_endtags.append(tag)
        if self._suppress and self._suppress[-1] == tag:
            self._suppress.pop()


def _normalised_path(raw: str) -> str:
    """``raw`` with ``.``/``..`` segments resolved the way the URL spec does.

    ``/blog/../docs`` (and its ``%2e`` respellings) resolves to ``/docs``, so
    accepting the raw prefix would credit a page with a route to the blog it does
    not provide. Empty segments are kept while resolving — ``/blog//../docs``
    resolves to ``/blog/docs``, because ``..`` pops the empty segment — and dropped
    only in the joined result (review finding, #3962).
    """
    decoded = raw.replace("%2e", ".").replace("%2E", ".")
    absolute = decoded.startswith("/")
    segments: list[str] = []
    for segment in decoded.split("/"):
        if segment == "..":
            if segments:
                segments.pop()
        elif segment != ".":
            segments.append(segment)
    return ("/" if absolute else "") + "/".join(s for s in segments if s)


def is_blog_entry(href: str) -> bool:
    """True when ``href`` is a way INTO the blog, for BOTH guard layers.

    ``/blog`` (the index) and ``/blog/<slug>`` (an article — its own nav links
    back to the index) both count, so a page is not faulted for linking a post
    instead of the index.

    Why this is shared rather than written twice: it is the last rule the static
    guard and the E2E check still implemented separately, and the same drift that
    motivated sharing the extractor applies to it.

    It is deliberately narrow, because a false pass here defeats the guard: the
    href must be root-relative, or an http(s) URL for a host the site owns and its
    default port; ``mailto:``/``javascript:``/``data:`` and a host the site does
    not own are not ways in, nor is a path that resolves out of ``/blog``.
    """
    # A browser skips EXTRA leading slashes and then parses the authority, so
    # `///tortoise.premiselabs.co/blog` resolves to `https://tortoise.premiselabs.co/blog`
    # while `urlsplit` sees no authority at all. Collapse 3+ opening slashes to `//`
    # first: without this, a page whose only blog link used that form was a false
    # NEGATIVE — and the comment this replaces asserted the opposite (review finding,
    # #3962). `///blog` still fails the host check below, as it should.
    probe = "//" + href.lstrip("/") if href.startswith("///") else href
    try:
        parts = urlsplit(probe)
        host = parts.hostname
        port = parts.port
    except ValueError:
        # An unterminated IPv6 literal, an out-of-range port, etc.
        return False
    scheme = parts.scheme.lower()
    if scheme not in ("", "http", "https"):
        return False
    if "\\" in href:
        # `urlsplit` does not treat `\` as a delimiter for special schemes, so
        # `https://evil.example\@tortoise.premiselabs.co/blog` reads as the
        # tortoise host here while a browser goes to evil.example (review
        # finding, #3962).
        return False
    if scheme or href.startswith("//"):
        # Absolute, or protocol-relative: an authority is required, and it must be
        # one of ours. (`///blog` and `https:///blog` have NO authority — a browser
        # resolves their host to `blog` — so they are not ways in either.)
        if host not in _BLOG_HOSTS:
            return False
        # A protocol-relative href inherits the PAGE's scheme, and every page this
        # guard runs on is https — so `//host:80/blog` resolves to `https://host:80`,
        # which fails TLS and is not a way in. Accepting it was a false PASS in
        # exactly the direction the guard exists to prevent (review finding, #3962).
        if port is not None:
            expected_port = 443 if (scheme == "https" or not scheme) else 80
            if port != expected_port:
                return False
    path = _normalised_path(parts.path)
    return path == "/blog" or path.startswith("/blog/")


def blog_entry_hrefs(html: str) -> list[str]:
    """The anchors in ``html`` that are ways into the blog, in document order."""
    return [href for href in extract_anchor_hrefs(html) if is_blog_entry(href)]


def extract_anchor_hrefs(html: str) -> list[str]:
    """Hrefs of the real ``<a>`` anchors in ``html``, in document order.

    Accepts a full document or a fragment (the hero-section slice is passed in as
    a fragment). Quoted, single-quoted, and unquoted attribute values are all
    accepted, since HTML5 permits all three and an unquoted ``<a href=/blog>`` is
    a working link in every browser.
    """
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    return parser.hrefs


def extract_element_attributes(html: str) -> list[tuple[str, str, int]]:
    """``(tag, attribute name, line)`` for every attribute in ``html``'s tree.

    The attribute-allowlist guard's input (#14073). Names come back lowercased,
    because a browser's attribute name is case-insensitive and the tokenizer
    normalises it. Duplicate attributes are all reported: the tokenizer keeps the
    first VALUE, but reporting both names cannot hide an unallowed one — the
    direction this guard must not fail in.

    Parsed from the same render-fidelity seam as ``extract_element_ids``, so an
    attribute name written inside a comment, a raw-text element, or an inert
    ``<template>`` is not counted, exactly as ``_record_id`` decides it for ids.
    """
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    return parser.attrs


def extract_mode_dependent_regions(html: str) -> list[tuple[str, int]]:
    """``(tag, line)`` for every region whose rendering depends on the MODE.

    Only `<noscript>` qualifies (see `_MODE_DEPENDENT`): its children are TEXT with
    scripting on and HTML content with scripting off, so a guard that reads the
    scripting-enabled tree cannot say whether a reader meets them. A caller whose
    question is "could a reader meet this?" must therefore REFUSE such a region
    instead of clearing it — the answer it would give ("unrendered") is only half
    the truth, and it is the fail-open half (#14073 review).

    An empty result is the only answer that clears the markup for that question.
    A region nested inside an inert `<template>` is NOT reported: template content
    renders in neither mode, so the inert rule already answers it.
    """
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    return parser.mode_dependent


def extract_heading_positions(html: str) -> list[tuple[str | None, int]]:
    """``(id, offset)`` for every rendered ``<h2>`` in ``html``, in document order.

    The docs page's section bound's input (#14073 review): the offset is where the
    parsed tag STARTS in ``html``, so a caller can slice the source at a heading
    the browser actually has, instead of at a string that looks like one.

    ``id`` is ``None`` for a heading with no id — such a heading cannot be
    advertised, but it is still a heading, and dropping it would hide it from a
    caller that bounds at any heading at all.
    """
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    line_starts = [0]
    for index, char in enumerate(html):
        if char == "\n":
            line_starts.append(index + 1)
    return [
        (heading_id, line_starts[line - 1] + column)
        for heading_id, line, column in parser.headings
    ]


def extract_anchors_within(
    html: str, *, tag: str, class_token: str,
) -> list[list[str]]:
    """Hrefs of the anchors inside each rendered ``<tag class="… class_token …">``.

    One list per matching container, in document order (an empty list for a
    container with no anchors, so the COUNT is the tree's answer to "how many
    such containers does this page have"). The docs page's section bound asks
    this to build its index — the set of headings the TOC advertises — because
    that index was the last raw-source read in the bound and a decoy
    ``<div class="toc">`` in a comment therefore REPLACED the real advertised
    set, letting an un-advertised heading bound the slice and everything under it
    leave all three pins green (#14073 review, measured).

    ⛔ "INSIDE" IS AN ANCESTRY, which an href list cannot express: the same
    question read as a regex over the document text cannot tell an anchor in the
    TOC from one in the footer, cannot tell whether the TOC is even in the tree,
    and misses the spellings HTML permits — ``href='#x'``, ``href=#x`` and
    ``class="toc wide"`` are all legal and all rendered, and each one either
    reddened the golden (the slice ran past the section) or fired the bound's own
    "no TOC block" assertion (#14073 review, all three measured).

    Limits, all inherited from the parser and declared rather than guessed. An
    IMPLICIT-CLOSE tag is REFUSED outright (`_IMPLICITLY_CLOSED`, which covers BOTH
    families: spec 13.2.6.3's implied end tags and the tags a sibling START TAG
    closes — `<button>`, `<h1>`–`<h6>`, `<a>`, `<form>`, `<nobr>`, `<select>`,
    `<table>`): for a container whose tag a sibling closes (`<li>`, `<p>`, `<td>`…)
    an extent built on end tags alone runs past the sibling, which is a LARGER
    advertised set and therefore a SHORTER slice — the fail-open direction — so the
    honest answer is to refuse the tag rather than to state a direction for it and
    hope. The refusal message says which tags ARE safe instead of implying every
    accepted tag is. A container inside an inert region is not in the tree at all,
    which is the point.

    ⛔ THE ENTRIES follow `_unmet_by_reader`; a CONTAINER is counted by the tree, not
    by the reader — the two are deliberately different answers, and this paragraph
    used to say both followed the rule. `extract_anchors_within(
    '<div class="toc" hidden><a href="#x">x</a></div>')` returns `[[]]`: the block
    IS in the tree, so it is counted, and only its entry is dropped. That direction
    is the loud one — a phantom container makes the caller's count larger, never
    smaller, so a decoy block reddens "exactly one TOC block" instead of replacing
    the real index (#14073 review, measured).

    ⛔ AND A CONTAINER THAT NEVER CLOSES IS REFUSED, for the same reason an
    IMPLICIT-CLOSE tag is: its extent is not the element, it is the REST OF THE
    DOCUMENT, so the entries it collects are every anchor on the page. Through the
    docs page's bound that is indistinguishable from a correctly-closed TOC whose
    index happens to be long, and it readmits the whole-page `href` index round 4
    removed: an `<div class="toc-group">` left open inside the TOC makes the TOC's
    own `</div>` close the INNER element, so the block stays open to EOF, every
    anchor on the page becomes an entry, a new un-advertised `<h2>` plus a claim at
    the section boundary leaves the golden, the vocabulary pin and the allowlist
    all green (measured, #14073 review). An element a browser never closed is not
    an extent, so this refuses rather than guesses.
    """
    if tag in _AnchorHrefParser._IMPLICITLY_CLOSED:
        raise ValueError(
            f"`{tag}` is closed by a browser WITHOUT an end tag — by implied end "
            f"tags, or by a following sibling start tag — so an extent built on end "
            f"tags alone would run past its next sibling: a larger advertised set "
            f"and a SHORTER slice. Ask a container tag whose end tag is the only "
            f"thing that closes it (div/section/nav/ul/…), or model the implicit "
            f"close first."
        )
    parser = _AnchorHrefParser(container=(tag, class_token))
    parser.feed(html)
    parser.close()
    if parser._open_containers:
        raise ValueError(
            f"a `<{tag} class=\"…{class_token}…\">` block is still OPEN at the end of "
            f"the document, so its extent is not the element — it is the rest of the "
            f"page, and its entries are every anchor on it. That is the whole-page "
            f"index this helper exists to replace, and from the outside it is "
            f"indistinguishable from a correctly-closed block with a long index. An "
            f"element a browser never closed is not an extent: close the block, or "
            f"ask a question that does not need one."
        )
    return parser.containers


def extract_element_ids(html: str) -> list[tuple[str, int]]:
    """``(id, line)`` for every element ``html`` puts in the document tree (#4118).

    The duplicate-`id` guard's input, and the second answer this module's
    render-fidelity seam gives (see ``_AnchorHrefParser``). Before #4118 that
    guard parsed with its own `HTMLParser` subclass, which diverged from a
    browser in both directions: it MISSED markup inside a raw-text element nested
    in foreign content (silent — the direction a duplicate-id guard must not
    have), and it counted `<template>` content a browser keeps inert. One parser
    removes the divergence class instead of declaring each instance of it.

    Empty ids are omitted (``getElementById("")`` matches nothing) and a repeated
    ``id`` attribute keeps the first, as a browser does.
    """
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    return parser.ids


def extract_first_base_href(html: str) -> str | None:
    """The `href` of the first `<base>` `html` puts in the document tree, or None.

    The document's BASE URL, which is what every relative and fragment-only link
    resolves against. Absent a `<base href>`, a browser resolves against the
    document's own URL — so a caller that assumes the document URL is the base is
    right until the day the page grows a `<base>`, and silently wrong after
    (#14073 review, P2: the same-page predicate had no way to see one).

    Parsed, never pattern-matched, for the reason the rest of this module is: a
    `<base href>` written inside a comment, a `<script>` body or an inert
    `<template>` is not one a browser applies, and the raw source cannot tell the
    difference. Only the FIRST one counts — a later `<base href>` is ignored.

    Returns the attribute's value VERBATIM (empty string for `<base href>` with no
    value); resolving it is the caller's job, because only the caller knows the
    document URL it is resolving against.
    """
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    return parser.base_href


def extract_visible_text(html: str) -> str:
    """The TEXT a reader sees in ``html``, tags removed and entities decoded.

    ⛔ A REGEX OVER THE SOURCE CANNOT ANSWER THIS, and the difference is a fail-open
    on any wording golden built on it (#14073 review, P0, measured). ``<`` starts a
    tag ONLY when the next character begins a tag name (an ASCII letter), ``/``,
    ``!`` or ``?``; for anything else the WHATWG tokenizer's tag-open state takes its
    ``anything else`` branch and emits the ``<`` as TEXT (13.2.5.6). So

        < FalkorDB holds a slice of the record; above the allowance for your tier … >

    is a line a reader meets, and ``re.sub(r"<[^>]+>", " ", …)`` deletes it whole —
    the golden matched while the page had grown a contradicting claim. Measured:
    inserting exactly that line into the `#storage` section left the wording golden,
    the vocabulary scan (which reads this same text) and the attribute allowlist all
    green.

    It fails in the OTHER direction too, which is why this is not a one-line patch to
    the pattern: a regex counts text a reader never meets as visible — a `<script>`
    body inside the section reddened the golden on legal markup (measured). Both
    directions come from the same mistake, so the answer comes from the same tree the
    rest of this module reads: raw-text and inert regions are dropped by `_suppress`
    (except the ones that DO paint their text, `_RAW_RENDERS`), and a subtree a reader
    does not meet is dropped by the inherited render flag (`hidden`, a closed
    `<dialog>` — see `_unmet_by_reader`).

    ⛔ WHERE THIS DELIBERATELY DIFFERS FROM `_unmet_by_reader`, because the two
    questions have OPPOSITE loud directions (#14073 review round 6):
    * the WHOLE SUBTREE of a closed `<details>`/`<dialog>` is recorded here — the
      disclosure family is counted rather than modelled, because four review rounds of
      modelling "which part of a closed disclosure is painted" each produced a new
      fail-open (direct-child summary, one wrapper deeper, an intervening `<p>`, a
      `<p>`'s descendants, renamed/voided tokens). The cost is named: copy a reader only
      meets after opening the disclosure also has to match the golden;
    * `<datalist>`/`<canvas>`/`<video>`/`<audio>`/`<meter>`/`<progress>` FALLBACK
      content is dropped here (`_UNRENDERED`), which is MEASURED rather than a
      direction call: a supporting browser paints none of it (0 painted px with the
      children vs 0 without, `_FALLBACK_RENDERS`' reproduction). An `<object>`'s
      fallback IS painted (8 000 painted px) and is therefore counted — see
      `_FALLBACK_RENDERS`. The residual is the OTHER branch: a READER whose browser
      falls back on a `<video>`/`<canvas>`/… meets copy this pin does not read, named
      at `handle_data` rather than left implied;
    * FOREIGN text is recorded whatever it is (see the declared residual at
      `handle_data`): a foreign `<title>`/`<desc>`/`<style>`/`<script>` therefore
      contributes text a browser never paints. That is the false-red direction, not a
      fail-open, and it is named rather than implied.
    Text nodes are returned joined by a single space, in document order, so an inline
    element still separates words the way the tags it replaced did. Whitespace is NOT
    folded here: the caller decides how to compare, and this module does not know
    whether it is being asked about a golden or a fragment.

    Raises ValueError when the parse WEDGED, rather than returning the text of a
    partial tree: partial text would read as a SHORTER section — the fail-open
    direction the wording golden exists to close.

    ⛔ THE WEDGE GUARD IS TWO MARKERS, NOT A COMPLETENESS CLAIM (#14073 review round
    11, P1). It fires on `frameset` and on the script-data escaped states — the
    divergences this module knows it cannot follow — and returns text for everything
    else. So a divergence it does not model produces a partial tree rather than a
    raise, and the pins cannot tell. What keeps that from being the 2026 round-6..11
    failure mode is that each KNOWN divergence is modelled instead of hoped away, and
    each has a test that reds when the model is removed: the disclosure family
    (`_text_is_painted`), the implied `</p>` cascade (`_P_CLOSERS` ->
    `_close_p_scope`), sibling/implicit closes (`_close_implied`), the table row &
    cell contexts (`_clear_table_context`), foster parenting
    (`_foster_truncated_ancestry`), `<select>` scope (`_close_select_scope`), and
    label rendering (`_LABEL_RENDERS`). The residual, stated rather than implied: an
    insertion-mode rule NONE of those covers would still return partial text. When one
    is found, model it and add its test — do not widen the wedge marker list, which
    would make the guard fire on legal pages and get it deleted.
    """
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    if parser.wedged_reason:
        raise ValueError(
            f"the document wedged ({parser.wedged_reason}), so its rendered text is "
            f"not knowable from here: this model cannot tell which of the remaining "
            f"bytes a browser renders. Refusing rather than returning a partial "
            f"section, which would read as missing text and not as an unreadable "
            f"page. Re-shape the markup so the tree is unambiguous."
        )
    return " ".join(parser.text)
