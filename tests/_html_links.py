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
    # open-element stack below must not push one: a `<br>` left on the stack would
    # make the NEXT `</div>` a mismatch and the container it belongs to would never
    # close.
    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    })
    # Tags a browser closes WITHOUT an end tag — spec 13.2.6.3's implied end tags,
    # the table sections, and the tags a SIBLING START TAG closes (`<button>`,
    # `<h1>`–`<h6>`, `<a>`, `<form>`, `<nobr>`, `<select>`, `<table>`; 13.2.6.4's
    # "in body" rules act as an end tag for the open one). `<li>a<li>b` is two
    # elements, so an EXTENT built on end tags alone would run from the first
    # `<li>` past the second. The container helper refuses these rather than
    # guessing (see `extract_anchors_within`).
    #
    # ⛔ BOTH FAMILIES BELONG HERE, and the second was missing: the refusal used to
    # list only the implied-end-tag tags, so `extract_anchors_within(tag="button")`
    # (and `h2`, `a`, `form`, `nobr`) returned an extent that ran PAST the sibling —
    # `'<button class="toc"><a href="#a">A</a><button><a href="#b">B</a>'` gave
    # `[['#a', '#b']]` where a browser has `[['#a']]` — a larger advertised set and a
    # SHORTER slice, the fail-open direction, while the helper's own message
    # promised the opposite (#14073 review).
    _IMPLICITLY_CLOSED = frozenset({
        "a", "button", "caption", "colgroup", "dd", "dt", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "li", "nobr", "optgroup", "option", "p",
        "rb", "rp", "rt", "rtc", "select", "table", "tbody", "td", "tfoot",
        "th", "thead", "tr",
    })
    #: Elements whose CHILD CONTENT a browser does not RENDER UNCONDITIONALLY, by
    #: ELEMENT TYPE — the second half of "does a reader meet this heading/link",
    #: after raw text, inert `<template>` content and the `in select` rule.
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
    _UNRENDERED = frozenset({
        "datalist", "canvas", "video", "audio", "object", "meter", "progress",
    })
    #: Elements whose content renders only WITH an attribute: `<dialog>` without
    #: `open` is not in the rendering at all, and `<details>` without `open` shows
    #: its `<summary>` and hides the rest. Presence is what counts — an empty `open`
    #: is the rendered spelling.
    #:
    # ⛔ THE WHOLE CLOSED SUBTREE IS REFUSED, `<summary>` INCLUDED — and that is a
    # DELIBERATE over-refusal in the LOUD direction, not an oversight: a `<summary>`
    # IS rendered by a closed `<details>` (this line used to say so while the code
    # refused it, #14073 review), so a heading inside one does not bound the slice
    # and the extra text stays pinned. Modelling "the FIRST summary of a closed
    # details renders, a second one does not" is not attempted: recording a heading
    # in that second summary would BOUND the slice at something no reader meets —
    # the fail-open direction this whole rule exists to close.
    _RENDERS_WITH_ATTR = frozenset({"details", "dialog"})
    #: Every recording of a DOCUMENT TREE, by attribute name. `_clear_recordings`
    #: iterates this tuple, and
    #: `test_the_parsers_recordings_forget_a_document_a_frameset_replaced` asserts it
    #: covers every public list-valued attribute on the parser — so a recording added
    #: later fails THAT test until it is declared here, instead of silently keeping
    #: entries from a tree a browser has thrown away.
    _RECORDINGS = ("hrefs", "ids", "attrs", "headings", "containers")
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

        ⛔ ONE DECLARED DIVERGENCE, and it is the right answer for the anchor
        question, not this one: `<noscript>` is raw text because `self.scripting`
        is True (line 180), so an attribute on an element INSIDE `<noscript>` is
        not recorded. A browser with JS — the mode this repo's guards ask in —
        does not render it either, so no claim reaches a reader; a browser with
        JS DISABLED would render it, and a `title` there is a reader-facing claim
        this allowlist cannot see. Not fixed here: the anchor contract needs the
        no-JS-only anchor to stay invisible, and the two questions cannot both
        have `<noscript>` their own way in one pass.
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
        select does not end the slice, and the tokens the classic mode used to POP
        the select for (`input`/`keygen`/`textarea`, after which a heading would be
        outside it again) are not modelled either — same direction, same reason.

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

        * an open `<select>` ANCESTOR — the conservative side of a spec change, for
          the reason `_record_heading` states at length;
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
        if any(open_tag == "select" for open_tag, _, _ in self._open_elements):
            return True
        if any(open_tag in self._UNRENDERED for open_tag, _, _ in self._open_elements):
            return True
        if any(not renders for _, _, renders in self._open_elements):
            return True
        return any(name == "hidden" for name, _value in attrs)

    def _in_select(self) -> bool:
        """True when an open `<select>` ancestor makes this start tag no link at all.

        The LINK question's own test, and the one `_collect` still asks about
        `self.hrefs` — an `<a>` inside a `<select>` is not an element a reader can
        follow, in any browser, under any cascade. The BOUNDARY question is wider
        and lives in `_unmet_by_reader`; an anchor that fails only the wider test
        (inside a `hidden` banner, say) is still a link the page's own script can
        reveal, so it stays in the rendered-link set and is dropped from the
        container index only.
        """
        return any(open_tag == "select" for open_tag, _, _ in self._open_elements)

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._suppress or self.wedged_reason:
            return
        if self._in_select():
            # An `<a>` inside a `<select>` is not a link a reader can follow, and it
            # is not an entry the TOC's index may be built from — the same escape as
            # a heading there, one recording over (measured: an anchor inside a
            # `<select>` in the TOC still advertised `#storage-limits` and bounded
            # the section, all pins green; #14073 review).
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
        `_RECORDINGS` + `_DIAGNOSTICS` is EVERY public list on the parser — a sixth
        recording reds there until it is declared, and declaring it here is what
        makes the clear apply. The diagnostics are deliberately not cleared: they
        record what the PARSE met, not what the tree holds.
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
        `_unmet_by_reader` reads — the `hidden` attribute, `_UNRENDERED` element
        types, and `_RENDERS_WITH_ATTR` elements before their attribute arrives.
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
        renders = not (
            "hidden" in names
            or tag in self._UNRENDERED
            or (tag in self._RENDERS_WITH_ATTR and "open" not in names)
        )
        if self._open_elements and not self._open_elements[-1][2]:
            # Inherited, so `<div hidden><section><h2>` hides the heading without
            # `_unmet_by_reader` having to walk the stack to the `hidden` div.
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
        # Before `_open`, because this tag's own id/attributes count even where
        # its CONTENT does not (see `_record_id`, `_record_attributes`).
        self._record_id(attrs)
        self._record_attributes(tag, attrs)
        suppressed = self._open(tag, attrs)
        if not suppressed:
            self._collect(tag, attrs)
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
    which is the point. Both the anchors collected and the container boundaries
    follow `_unmet_by_reader`: an anchor no reader meets is not an entry, and its
    own `hidden` attribute counts for that as much as a `<select>` ancestor does.
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
