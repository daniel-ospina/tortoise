"""Shared anchor-href extraction for the public-site guards (#3950).

One implementation, used by BOTH layers that must agree about what a "way in" is:

  * ``tests/test_website_docs_consistency.py`` — the static guard, and
  * ``tests/e2e/test_legal_pages.py`` — the production check.

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

from html.parser import HTMLParser


class _AnchorHrefParser(HTMLParser):
    """Collect the ``href`` of every ``<a>`` in RENDERED markup.

    A real parser, not a regex, because both failure modes that matter come from
    a regex guessing at attribute structure:

      * markup a browser does not render as a link must NOT count — comments,
        and ``<script>``/``<style>`` bodies (HTMLParser already treats both as
        CDATA; they are tracked explicitly as well so a malformed document
        cannot leak through);
      * an ``href=`` appearing INSIDE another attribute's value is not this
        tag's href. ``<a title="see href=/blog">`` has no href at all, yet a
        regex with an optional quote reported one — a false positive of exactly
        the silent-pass class #3950 fixes (review finding, PR #3962).

    Only ``<a>`` is collected, so ``<link rel="prefetch" href="/blog">`` is not
    a way in. An ``href`` with no value, or an empty one, is not a link either.
    """

    _CDATA = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self._in_cdata = 0

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._in_cdata:
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._CDATA:
            self._in_cdata += 1
            return
        self._collect(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<script />` is empty — never enter CDATA state for it.
        if tag in self._CDATA:
            return
        self._collect(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._CDATA and self._in_cdata:
            self._in_cdata -= 1


def extract_anchor_hrefs(html: str) -> list[str]:
    """Hrefs of the real ``<a>`` anchors in ``html``, in document order.

    Accepts a full document or a fragment (the hero-section slice is passed in
    as a fragment). Quoted, single-quoted, and unquoted attribute values are all
    accepted, since HTML5 permits all three and an unquoted ``<a href=/blog>``
    is a working link in every browser.
    """
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    return parser.hrefs
