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


class _AnchorHrefParser(HTMLParser):
    """Collect the ``href`` of every ``<a>`` a browser would render as a link.

    The contract is "the rendered-link set", because the guard's whole purpose is
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

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # `<noscript>` is raw text only with scripting ENABLED — the browser
        # default, and the only mode this guard's question is asked in. The stdlib
        # defaults it OFF and would parse `<noscript>` as markup, counting an
        # anchor no browser renders. Set as an attribute rather than a constructor
        # argument because only `parse_starttag` consults it (and the `_RAW` entry
        # still covers the case where the attribute is absent).
        self.scripting = True
        self.hrefs: list[str] = []
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

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._suppress or self.wedged_reason:
            return
        for name, value in attrs:
            if name == "href":
                # First href wins — the tokenizer keeps the first of a duplicate
                # pair. An empty/valueless first href is treated as no link here:
                # that is this guard's policy (an empty href only self-links),
                # not the tokenizer's rule.
                if value:
                    self.hrefs.append(value)
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
            # (review finding, #3962, Chromium-verified).
            self.hrefs.clear()
            return True
        return False

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
        if not self._open(tag, attrs):
            self._collect(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # In FOREIGN content the self-closing flag IS honored, so `<svg/>`/`<math/>`
        # is an empty element that closes at once — forwarding it to
        # `handle_starttag` would increment `_foreign` with nothing to decrement
        # it, disabling `<template>` suppression for the rest of the document
        # (a false pass, review finding). In the HTML namespace the flag is
        # ignored for a non-void element, so `<template/>`/`<script/>` genuinely
        # open and swallow what follows — deleting the `/` is what a browser does.
        if tag in self._FOREIGN:
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
