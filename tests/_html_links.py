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
      and are already handled as text by the stdlib parser; they are not listed
      here because nothing needs to be done for them.
    * **Link** — only ``<a>`` is collected, so ``<link rel="prefetch">`` is not a
      way in. Only the FIRST ``href`` on a tag counts, since the tokenizer keeps
      the first of a duplicate pair. An ``href`` with no value, or an empty one,
      counts as no link: this extractor's own policy, not the tokenizer's — an
      empty ``href`` merely self-links.
    * **Foreign content** — ``<template>`` is only the HTML inert element outside
      ``<svg>``/``<math>``; inside them it is an ordinary foreign element whose
      children do render, so suppression is skipped there.
    * **Self-closing non-void tags** — HTML5 ignores the ``/`` on a non-void HTML
      element, so ``<template/>`` and ``<script/>`` OPEN their container rather
      than being empty, and everything after them is suppressed until the matching
      end tag or EOF. Deleting the ``/`` is exactly what a browser does.
    """

    # Content is text, never markup a browser renders a link from.
    _RAW = frozenset({"script", "style", "noscript"})
    # Parsed, but never rendered (HTML namespace only — see _FOREIGN).
    _INERT = frozenset({"template"})
    # Foreign-content roots: inside these, `_INERT` does not apply.
    _FOREIGN = frozenset({"svg", "math"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        # Open elements currently suppressing collection. A stack, not a counter,
        # so nested `<template><script>…</script></template>` unwinds correctly.
        self._suppress: list[str] = []
        self._foreign = 0

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._suppress:
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

    def _open(self, tag: str) -> bool:
        """Track an opening tag. Returns True when it starts a suppressed region."""
        if tag in self._FOREIGN:
            self._foreign += 1
            return False
        if tag in self._RAW:
            self._suppress.append(tag)
            return True
        if tag in self._INERT and not self._foreign:
            self._suppress.append(tag)
            return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._open(tag):
            self._collect(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # HTML5 ignores the self-closing flag on a non-void HTML element (a parse
        # error), so `<template/>` / `<script/>` OPEN rather than being empty and
        # swallow what follows — the stdlib default would close them immediately.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._FOREIGN:
            if self._foreign:
                self._foreign -= 1
            return
        if self._suppress and self._suppress[-1] == tag:
            self._suppress.pop()


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
