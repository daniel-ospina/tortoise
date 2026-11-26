"""Guard: an own-site link's ``#fragment`` must land on an id the page declares (#11939).

THE DEFECT CLASS. ``https://premiselabs.co/docs#appeal`` is not a broken link —
it 200s. It is a link that lands at the TOP of the docs page, because that page
declares no ``id="appeal"``. The fragment is resolved by the browser, never sent
to the server, so no crawler, no status-code check and no HTML validator sees
it. ``tortoise/abuse.py`` (``appeal_url()``) is where it was found, but the site
is one instance of the class, not the class: every hand-written ``href`` and
every string literal that becomes one can carry a fragment nobody verified.

WHY THIS TEST AND NOT A LINK CHECKER. ``.github/scripts/docs-link-check.sh``
fetches URLs; a fetch cannot answer this question, because the answer lives on
the client side of the response. The only way to see it is to compare the two
halves we already have in the tree: the hrefs our sources EMIT, and the ids our
pages DECLARE. That is what this file does, statically, with no network.

WHAT IT READS (and why each shape is here):
  * ``website/*.html`` — every ``href`` naming an own host, in BOTH spellings:
    the same-page ``href="#frag"`` form (resolved against the page's own
    declared ids by ``test_same_page_fragments_resolve``) and the absolute
    ``https://premiselabs.co/tortoise#pricing-section`` form, which is how the
    terms and FAQ pages link the pricing table.
  * ``tortoise/**/*.py`` — string LITERALS that are code, not docstrings (an
    AST walk, so a URL quoted in prose is not mistaken for one we emit). The
    ``appeal_url()`` default is exactly this shape.
  * ``website/apps/dashboard/src/**/*.{js,jsx,ts,tsx}`` — every quoted own-host
    URL on a code line, not only the ones spellable inside a ``href=``
    attribute: ``href={COMPARE_PLANS_URL}`` takes its URL from a ``const``
    elsewhere in the module, so an href-only read cannot see the link that most
    often moves. Comment lines are skipped, so a URL quoted in prose about a
    past defect (the #4392 comment that quotes the dead
    ``product.html#pricing`` link it removed) is not read back as live.
  * markdown under ``tortoise/`` and ``website/apps/dashboard/public/skills/``
    — ``[text](url#frag)`` links and ``<url#frag>`` autolinks, which are how
    the onboarding copies carry ``#chatgpt``.
  * ``website/functions/**/*.ts`` — every quoted own-host URL on a non-comment
    line. The blog renderer emits the pricing link into every page it writes
    (``blog/_lib.ts``), so a tree that skipped this one would miss a link with
    as many served copies as the blog has posts.

RESOLUTION. ``website/_redirects`` + ``website/functions/_middleware.ts`` decide
which file serves a ``(host, path)`` PAIR — the host is half the answer, and a
table keyed on the path alone is a false-green machine:
``premiselabs.co/`` is the COMPANY page (``index.html``) while
``tortoise.premiselabs.co/`` 301s onto ``/tortoise`` (``product.html``), so a
host-blind ``/`` → ``product.html`` would pass a
``https://premiselabs.co/#pricing-section`` that lands on a page declaring no
such id. A browser PRESERVES the fragment across a redirect whose target carries
none — which is why ``/product.html#pricing`` was still dead after that path 301'd
away. The table below is that map, spelled out rather than derived: deriving it
means parsing a TS middleware branch, and an UNMAPPED path FAILS here (with the
page and the path in the message) instead of passing quietly, so adding a page
forces this table to be taught.

RESIDUALS, declared rather than discovered — do not read a green run as more
than it is:
  * an id rendered at runtime by ``setAttribute``/``id={…}`` is invisible to a
    static read;
  * a fragment assembled at runtime is invisible too — ``f"{SITE}/docs#appeal"``
    and ``BASE + "#appeal"`` leave a literal with no host, so ``_OWN_URL`` never
    matches it. The ``abuse_url()`` default is a whole literal, which is why this
    class is visible at all;
  * a DASHBOARD same-page link is not read. The dashboard's anchors are built
    from constants (``MEMORY_SOURCES_SECTION_ID = 'settings-memory-heading'`` in
    ``overviewEmptyAction.js`` → ``id=`` in ``main.jsx``), and mapping a
    single-page app's ``href`` to the id set of the module that renders it is a
    different mechanism. Only ``website/*.html`` gets same-page resolution;
  * ``page_ids()`` unions the parsed ids with a raw regex for legacy
    ``<a name="…">``, and a raw regex matches inside a comment, a ``<script>``
    body or an inert ``<template>``. There is no ``<a name=`` under ``website/``
    today, so the arm is dead; when one appears it is the false-pass direction.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
WEBSITE = REPO / "website"

sys.path.insert(0, str(TESTS_DIR))
from _html_links import extract_element_ids  # noqa: E402

OWN_HOSTS = ("premiselabs.co", "www.premiselabs.co", "tortoise.premiselabs.co")

#: The retired-but-permanent host. `_middleware.ts` 301s it onto the PRODUCT
#: PREFIX, so its ROOT is `/tortoise` (`product.html`) — NOT the company page
#: `premiselabs.co/` serves.
TORTOISE_HOSTS = ("tortoise.premiselabs.co",)

#: company host + path (no fragment, no query) → the page file that serves it.
#:
#: ``_middleware.ts``: ``premiselabs.co/`` is the COMPANY page (``index.html``);
#: the product document has ONE canonical address, ``premiselabs.co/tortoise``,
#: served by a rewrite onto ``product.html``; ``/docs`` and ``/docs.html`` are the
#: docs page (``_redirects`` 301s the ``.html`` spelling to the clean one).
#: ``PRODUCT_PAGES`` is also served under the ``/tortoise`` prefix, so every page
#: it names carries BOTH spellings here — a table holding only one would call the
#: other unmapped, and `website/faq.html` already links
#: ``/tortoise#pricing-section``.
PATH_TO_PAGE = {
    "/": "index.html",
    "/tortoise": "product.html",
    "/product": "product.html",
    "/product.html": "product.html",
    "/docs": "docs.html",
    "/docs.html": "docs.html",
    "/tortoise/docs": "docs.html",
    "/tortoise/docs.html": "docs.html",
    "/tortoise/faq": "faq.html",
    "/faq": "faq.html",
    "/contact": "contact.html",
    "/tos": "tos.html",
    "/privacy": "privacy.html",
    "/license": "license.html",
    "/tortoise/license": "license.html",
    "/security": "security.html",
    "/tortoise/security": "security.html",
    "/self-hosted": "self-hosted.html",
    "/tortoise/self-hosted": "self-hosted.html",
    "/investors": "investors.html",
    "/tortoise/investors": "investors.html",
    "/design-partners-intro": "design-partners-intro.html",
    "/tortoise/design-partners-intro": "design-partners-intro.html",
    "/dpa": "dpa.html",
}

_OWN_HOSTS = "|".join(re.escape(h) for h in OWN_HOSTS)

_OWN_URL = re.compile(
    rf"https?://(?P<host>{_OWN_HOSTS})/(?P<path>[^\"'\s)>`?#]*)#(?P<frag>[^\"'\s)>`#]+)"
)
_HREF_ATTR = re.compile(r"""href=(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)')""")
_JS_STR = re.compile(r"""["'`](?P<url>[^"'`]*#[^"'`]*)["'`]""")
_MD_LINK = re.compile(r"\]\((?P<url>https?://[^)\s#]+#[^)\s]+)\)")
_MD_AUTOLINK = re.compile(r"<(?P<url>https?://[^>\s#]+#[^>\s]+)>")
_SAME_PAGE = re.compile(r"""href="#(?P<frag>[^"]+)\"""")

_PAGE_ID_CACHE: dict[str, set[str]] = {}


def page_ids(name: str) -> set[str]:
    """Ids a page DECLARES, plus the legacy ``<a name>`` a browser also honours."""
    if name not in _PAGE_ID_CACHE:
        path = WEBSITE / name
        html = path.read_text(encoding="utf-8")
        ids = {i for i, _line in extract_element_ids(html)}
        ids |= set(re.findall(r"""<a\s[^>]*\bname="([^"]+)\"""", html))
        _PAGE_ID_CACHE[name] = ids
    return _PAGE_ID_CACHE[name]


def resolve(host: str, path: str) -> str | None:
    """The page file serving ``(host, path)``, or None when it is one we must learn.

    The HOST is half the answer: `premiselabs.co/` is the company page while
    `tortoise.premiselabs.co/` 301s onto `/tortoise`, so a path-only table would
    pass `https://premiselabs.co/#pricing-section` against `product.html` — a
    page a browser never shows for that URL. `_middleware.ts` moves the retired
    host onto the product prefix before the static rules run, so that is where
    this maps it too.

    The table is the ONLY answer, deliberately. Derived fallbacks (`path +
    ".html"`, then the path's last segment) were removed: they answer a
    different question — "is there a page of that name?" — so a link to
    `/anything/contact#contact` resolved to `contact.html` and passed an id
    check against a page the wrong path consulted, on a path the deploy 404s.
    An unmapped path is `None`, and the caller FAILS naming it, which is what
    makes adding a page force this table to be taught.
    """
    if host in TORTOISE_HOSTS:
        path = "/tortoise" + ("" if path == "/" else path)
    return PATH_TO_PAGE.get(path)


def own_site_targets() -> list[tuple[str, int, str, str]]:
    """``(site, line, url, fragment)`` for every own-site fragment URL we emit."""
    found: list[tuple[str, int, str, str]] = []

    for py in sorted(REPO.glob("tortoise/**/*.py")):
        source = py.read_text(encoding="utf-8", errors="replace")
        if not any(h in source for h in OWN_HOSTS):
            # `ast.parse` over every module in the package costs ~18 s on its own
            # (measured), and this file packs at the map's flat 2.0 s default.
            # A file that cannot contain an own-host URL is not parsed at all, so
            # the weight the map books is not a fiction.
            continue
        tree = ast.parse(source, filename=str(py))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            for match in _OWN_URL.finditer(node.value):
                found.append((str(py.relative_to(REPO)), node.lineno, match.group(0), match.group("frag")))

    js_suffixes = {".js", ".jsx", ".ts", ".tsx"}
    for js in sorted((WEBSITE / "apps" / "dashboard" / "src").rglob("*")):
        if js.suffix not in js_suffixes or not js.is_file():
            continue
        for lineno, line in enumerate(js.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith(("//", "*", "/*")):
                # Prose about a past defect (e.g. the #4392 comment that quotes
                # the dead `product.html#pricing` link it removed) is not a link
                # we emit; reading it back would demand an id for a URL nobody
                # renders any more.
                continue
            for literal in _JS_STR.finditer(line):
                match = _OWN_URL.search(literal.group("url"))
                if match:
                    found.append((str(js.relative_to(REPO)), lineno, match.group(0), match.group("frag")))

    for page in sorted(WEBSITE.glob("*.html")):
        for lineno, line in enumerate(page.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for attr in _HREF_ATTR.finditer(line):
                literal = attr.group("dq") or attr.group("sq")
                if literal is None:
                    continue
                match = _OWN_URL.search(literal)
                if match:
                    found.append((str(page.relative_to(REPO)), lineno, match.group(0), match.group("frag")))

    for fn in sorted((WEBSITE / "functions").rglob("*.ts")):
        if not fn.is_file():
            continue
        for lineno, line in enumerate(fn.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            for literal in _JS_STR.finditer(line):
                match = _OWN_URL.search(literal.group("url"))
                if match:
                    found.append((str(fn.relative_to(REPO)), lineno, match.group(0), match.group("frag")))

    md_roots = [REPO / "tortoise", WEBSITE / "apps" / "dashboard" / "public" / "skills"]
    for root in md_roots:
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            for lineno, line in enumerate(md.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for link in (*_MD_LINK.finditer(line), *_MD_AUTOLINK.finditer(line)):
                    match = _OWN_URL.search(link.group("url"))
                    if match:
                        found.append((str(md.relative_to(REPO)), lineno, match.group(0), match.group("frag")))

    return found


def test_emitted_own_site_fragments_resolve_to_a_declared_id():
    targets = own_site_targets()
    assert targets, "extraction found nothing — the guard is blind, not green"

    dead: list[str] = []
    unmapped: list[str] = []
    for site, lineno, url, frag in targets:
        match = _OWN_URL.search(url)
        path = "/" + match.group("path")
        page = resolve(match.group("host"), path)
        if page is None:
            unmapped.append(f"{site}:{lineno}  {url}  — no page serves {match.group('host')}{path}")
            continue
        if frag not in page_ids(page):
            dead.append(f"{site}:{lineno}  {url}  — {page} declares no id={frag!r}")

    assert not unmapped, "own-site path serving an unknown page:\n" + "\n".join(unmapped)
    assert not dead, "fragment link that lands at the top of the page:\n" + "\n".join(dead)


def test_same_page_fragments_resolve():
    dead: list[str] = []
    pages = sorted(WEBSITE.glob("*.html"))
    assert pages, "no pages found under website/"
    for page in pages:
        html = page.read_text(encoding="utf-8", errors="replace")
        declared = page_ids(page.name)
        for lineno, line in enumerate(html.splitlines(), 1):
            for match in _SAME_PAGE.finditer(line):
                frag = match.group("frag")
                if frag not in declared:
                    dead.append(f"{page.name}:{lineno}  href=#{frag}")
    assert not dead, "same-page fragment with no such id:\n" + "\n".join(dead)
