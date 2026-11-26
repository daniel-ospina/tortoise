"""Guard: an own-site link's ``#fragment`` must land on an id the page declares (#11939).

THE DEFECT CLASS. ``https://premiselabs.co/tortoise/docs#appeal`` WAS not a broken
link — it 200'd. It is a link that lands at the TOP of the docs page, because the
page declared no ``id="appeal"``. #11939 added that section, which is why the URL
now resolves; the class it belongs to is still open, because the fragment is
resolved by the browser and never sent to the server, so no crawler, no
status-code check and no HTML validator sees the difference. ``tortoise/abuse.py``
(``appeal_url()``) is where it was found, but the site is one instance of the
class, not the class: every hand-written ``href`` and every string literal that
becomes one can carry a fragment nobody verified.

WHY THIS TEST AND NOT A LINK CHECKER. ``.github/scripts/docs-link-check.sh``
fetches URLs; a fetch cannot answer this question, because the answer lives on
the client side of the response. The only way to see it is to compare the two
halves we already have in the tree: the hrefs our sources EMIT, and the ids our
pages DECLARE. That is what this file does, statically, with no network.

WHAT IT READS (and why each shape is here). FIVE arms, keyed by name, and
``test_every_arm_reads_something`` fails any arm that finds nothing — a single
flat list cannot tell a dead arm from a live one, which is how two of these were
inert on the first cut with the suite green. ⛔ THIS LIST IS THE DOCUMENTED
SURFACE and nothing grades it: the arm set is built from ``own_site_targets()``'s
dict, so adding an arm without adding it here is invisible to every check — keep
the two in step:
  * ``page`` — every own-site URL written in ``website/*.html``. The marketing
    and legal pages link to each other absolutely
    (``https://premiselabs.co/tortoise#pricing-section`` in ``faq.html`` and
    twice in ``tos.html``), so an ``href="#frag"``-only read misses them.
  * ``python`` — string LITERALS under ``tortoise/`` that are code, not
    docstrings (an AST walk, so a URL quoted in prose is not mistaken for one we
    emit). The ``appeal_url()`` default is exactly this shape.
  * ``dashboard`` — ``website/apps/dashboard/src/**/*.{js,jsx,ts,tsx}``:
    ``href="…"`` / ``href={'…'}`` / ``href={`…`}`` attributes AND any string
    literal carrying an own-site URL, because a constant that BECOMES an href
    (``billingCta.js``'s ``COMPARE_PLANS_URL``) is not an ``href=`` at its
    definition site. Block comments are tracked so a URL quoted in a comment
    about a past defect is not read back as a live one.
  * ``functions`` — ``website/functions/**/*.{ts,tsx}``, the Pages Functions, with
    the same comment strip. They were outside every arm while
    ``website/functions/blog/_lib.ts`` rendered the nav of every blog post with
    ``<a href="https://premiselabs.co/tortoise#pricing-section">Pricing</a>`` — a
    hand-written own-site fragment href in a marketing source, which is this
    file's own subject.
  * ``markdown`` — markdown under ``tortoise/`` and
    ``website/apps/dashboard/public/skills/``, in BOTH the ``[text](url#frag)``
    and the CommonMark autolink ``<url#frag>`` form. The onboarding copies use
    the autolink form (``tortoise/onboarding/SKILL.md`` carries
    ``<https://premiselabs.co/docs#chatgpt>``), which a ``](…)``-only read never
    matched.

RESOLUTION. ``website/_redirects`` + ``website/functions/_middleware.ts`` decide
which file serves a path (``/tortoise`` → ``product.html``, ``/docs`` and
``/tortoise/docs`` → ``docs.html``, and so on), and a browser PRESERVES the
fragment across a redirect whose target carries none — which is why
``/product.html#pricing`` was still dead after that path 301'd to ``/``. The
table below is that map, spelled out rather than derived: deriving it means
parsing a TS middleware branch, and an unmapped path FAILS here (with the path
in the message) instead of passing quietly, so adding a page forces this table to
be taught. ``resolve()`` therefore has no "last segment" fallback and no
"``<path>.html`` exists on disk" fallback: the first mapped ``/blog/docs#appeal``
onto ``docs.html``, and the second resolved ``tortoise.premiselabs.co/index#beat-cta``
onto ``index.html`` (whose ``beat-cta`` id it matched) although that host 301s
``/index`` to ``/tortoise`` — both graded a page that never serves the URL, which
is the fail-open direction this guard must not have.

⛔ FUNCTION-SERVED PREFIXES ARE DELIBERATELY OUTSIDE THE TABLE: ``/blog`` and
``/blog/**`` are served by ``website/functions/blog/``, not by a page file, so a
``/blog#frag`` link has no row to teach and returns ``None``. That is a red, not a
gap to paper over with a bogus row — the remedy is to widen this file (the
``functions`` arm already reads the nav those pages emit), and the day one is
written, this paragraph is the note that says so.

LIMIT, declared rather than discovered, and NARROWER than an earlier revision of
this paragraph claimed: an id rendered at runtime by ``setAttribute`` or an
``id={…}`` expression is invisible to a static read, and **no dashboard
same-page fragment is read at all** — the ``dashboard`` arm requires an absolute
own-host URL and the same-page arm below walks ``website/*.html`` only, so
``overviewEmptyAction.js``'s ``'#' + MEMORY_SOURCES_SECTION_ID`` is not seen.
Its subject (``#settings-memory-heading``) happens to be safe today because it
resolves to an id the dashboard renders, and nothing here watches it. Do not read
a green run as "no dynamic ids exist" — that class is open.
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
from _html_links import extract_anchor_hrefs, extract_element_ids  # noqa: E402

OWN_HOSTS = ("premiselabs.co", "www.premiselabs.co", "tortoise.premiselabs.co")

#: path (no trailing slash, no fragment, no query) → the page file that serves it
#: ON THE COMPANY HOST. ``_middleware.ts``: the product document has ONE canonical
#: address, ``premiselabs.co/tortoise``, served by a rewrite onto ``product.html``;
#: ``/product`` and ``/product.html`` collapse onto it. ``/docs`` and ``/docs.html``
#: are the docs page (``_redirects`` 301s the ``.html`` spelling to the clean one),
#: and ``/tortoise/docs`` is the same document under the product prefix.
#:
#: ⛔ ``/`` IS NOT IN THIS TABLE, because a root is per-HOST and never one document —
#: see ``HOST_ROOT_PAGE``. It was ``"/": "product.html"``, which is the TURTLE
#: host's answer, and that made the company-host link ``premiselabs.co/#pricing-section``
#: PASS against a page it never reaches (``index.html`` declares no
#: ``pricing-section``; ``product.html`` does) — the fail-open direction, hiding a
#: link of exactly the class #11939 exists for.
PATH_TO_PAGE = {
    "/tortoise": "product.html",
    "/product": "product.html",
    "/product.html": "product.html",
    "/docs": "docs.html",
    "/docs.html": "docs.html",
    "/tortoise/docs": "docs.html",
    "/tortoise/faq": "faq.html",
    "/faq": "faq.html",
    "/contact": "contact.html",
    "/tos": "tos.html",
    "/privacy": "privacy.html",
    "/license": "license.html",
    "/security": "security.html",
    "/self-hosted": "self-hosted.html",
    "/investors": "investors.html",
    "/dpa": "dpa.html",
    # Standalone company-root pages. Spelled out because `resolve()` has NO
    # filesystem fallback (below): a page is served because this table says so, and
    # `website/<path>.html` existing is not evidence that `<path>` serves it —
    # `_middleware.ts` 301s `/index` on the product host to `/tortoise`, so a
    # `tortoise.premiselabs.co/index#beat-cta` link used to resolve to `index.html`,
    # match its id, and PASS while the browser landed on `product.html`.
    "/design-partners-intro": "design-partners-intro.html",
    "/tortoise-for-product-teams": "tortoise-for-product-teams.html",
}

#: host → the page its ROOT serves. ``_middleware.ts`` header: "``premiselabs.co/``
#: → company page (``index.html``)" and "``tortoise.premiselabs.co`` → PERMANENT
#: 301 onto the paths above" (so its root lands on ``/tortoise`` → ``product.html``).
#: ``tests/test_website_static.py`` says the same: "``/`` is the COMPANY page here;
#: the product is '/tortoise'".
HOST_ROOT_PAGE = {
    "premiselabs.co": "index.html",
    "www.premiselabs.co": "index.html",
    "tortoise.premiselabs.co": "product.html",
}

#: ⛔ ``?``/``=`` are excluded from BOTH classes. A query is not part of the path
#: (``/contact?ref=appeal#form`` serves ``contact.html``), and an f-string split leaves
#: a literal tail like ``…#appeal?t=`` whose "fragment" is ``appeal?t=`` — read back
#: as a dead id nobody ever wrote.
_OWN_URL = re.compile(
    "https?://(?P<host>"
    + "|".join(re.escape(h) for h in OWN_HOSTS)
    + r")/(?P<path>[^\"'\s)>`#?]*)(?:\?[^\"'\s)>`#]*)?#(?P<frag>[^\"'\s)>`#?=]+)"
)
_MD_LINK = re.compile(r"\]\(\s*(?P<url>https?://[^)\s#]+#[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
#: CommonMark AUTOLINK — ``<https://host/page#frag>``. Not a variant spelling: it is
#: the ONLY form the onboarding copies use (``tortoise/onboarding/SKILL.md`` carries
#: ``<https://premiselabs.co/docs#chatgpt>``), so this arm found zero targets while
#: the docstring claimed it was reading exactly those copies.
_MD_AUTOLINK = re.compile(r"<(?P<url>https?://[^>\s<>#]+#[^>\s<>]+)>")

_PAGE_ID_CACHE: dict[str, set[str]] = {}


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
#: A line comment is `//` NOT preceded by `:`. Exact rather than clever in this tree:
#: every `//` a browser sees comes from `https://`, and the only other `//` is a
#: comment. A `//`-cut that ignored this truncated `'https://host/x#y'` at `https:`
#: and lost exactly the URLs this arm exists to read.
_LINE_COMMENT = re.compile(r"(?<!:)//")


def strip_js_comments(text: str) -> list[tuple[int, str]]:
    """``(lineno, code)`` for every line of ``text`` with comment spans REMOVED.

    ⛔ Comment SPANS, not comment LINES, and not a hand-rolled quote lexer. The
    previous cut skipped a line only when it *began* with ``//``, ``*`` or ``/*``,
    which missed every shape that occurs here: a JSX ``{/* #4392: … */}`` is
    preceded by ``{``, its body spans lines with no per-line marker, a ``//`` can
    trail real code, and a ``*/`` can close mid-line with emitted code AFTER it
    (skipping the whole line then discards a real link — the fail-open direction).

    A quote-tracking lexer was tried FIRST and was wrong the other way: JSX body
    text is not a string literal, so one apostrophe left the quote state open for
    the rest of the file and every later comment read as code (it re-found the
    ``#4392`` note that says ``#pricing`` is dead). These two rules cannot get
    stuck.

    Declared limit: a ``/*`` written inside a string literal opens a bogus block
    span. Measured across ``website/apps/dashboard/src/``: no production source
    does it, and the two test files that spell ``'/*'`` as a mode marker
    (``githubDisconnect.test.js``, ``securityHeaders.test.js``) carry no own-site
    URL for a span to hide.
    """
    text = _BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        cut = _LINE_COMMENT.search(line)
        out.append((lineno, line if cut is None else line[:cut.start()]))
    return out


def _first_line_with(lines: list[str], needle: str) -> int:
    """1-based line of the first line holding ``needle``, or 0 when it spans lines."""
    for lineno, line in enumerate(lines, 1):
        if needle in line:
            return lineno
    return 0


def page_ids(name: str) -> set[str]:
    """Ids a page DECLARES, plus the legacy ``<a name>`` a browser also honours."""
    if name not in _PAGE_ID_CACHE:
        path = WEBSITE / name
        html = path.read_text(encoding="utf-8")
        ids = {i for i, _line in extract_element_ids(html)}
        ids |= set(re.findall(r"""<a\s[^>]*\bname="([^"]+)\"""", html))
        _PAGE_ID_CACHE[name] = ids
    return _PAGE_ID_CACHE[name]


def resolve(url: str) -> str | None:
    """The page file serving the own-site ``url``, or None when it is one we must learn.

    ⛔ HOST-AWARE, because a root is per-HOST: ``premiselabs.co/`` is the company
    page and ``tortoise.premiselabs.co/`` 301s onto ``/tortoise`` (the product
    page). A single ``/`` row resolved both to ``product.html``, which made the
    company-host link ``premiselabs.co/#pricing-section`` PASS against a page it
    never reaches.

    ⛔ NO "LAST SEGMENT" FALLBACK. One was here, and it resolved ``/blog/docs`` and
    ``/anything/contact`` onto ``docs.html`` / ``contact.html`` — so a fragment URL
    naming a path no page serves was graded against a page that never answers it,
    and PASSED. That is the fail-open direction; the whole point of the table is
    that an unknown path is a red, not a guess.

    The trailing slash is normalised (``/docs/`` is the same 301 target as
    ``/docs``); the query is already excluded by ``_OWN_URL``.
    """
    match = _OWN_URL.search(url)
    if match is None:
        return None
    host = match.group("host")
    path = "/" + match.group("path").strip("/")
    if path == "/":
        return HOST_ROOT_PAGE[host]
    return PATH_TO_PAGE.get(path)


_TARGETS: dict[str, list[tuple[str, int, str, str]]] | None = None


def targets() -> dict[str, list[tuple[str, int, str, str]]]:
    """`own_site_targets()`, walked ONCE per process.

    The walk AST-parses every `tortoise/**/*.py`; the file is registered under BOTH
    `core` and `onboarding`, so it runs on `website/*.html`, `dashboard/src` and
    `tortoise/onboarding/` diffs too. Walking twice per run bought nothing.
    """
    global _TARGETS
    if _TARGETS is None:
        _TARGETS = own_site_targets()
    return _TARGETS


def own_site_targets() -> dict[str, list[tuple[str, int, str, str]]]:
    """``{arm: [(site, line, url, fragment), ...]}`` for every own-site fragment URL we emit.

    Per-arm rather than one flat list on purpose: ``assert targets`` is satisfied by
    ANY arm, so a flat list cannot tell a dead arm from a live one — and two of these
    five were dead while the suite was green.
    """
    found: dict[str, list[tuple[str, int, str, str]]] = {
        "page": [], "python": [], "dashboard": [], "functions": [], "markdown": []}

    for page in sorted(WEBSITE.glob("*.html")):
        # ⛔ Through the render-fidelity seam, not a raw line scan: an own-site URL
        # quoted inside an HTML comment is prose about a link, not a link we emit —
        # `website/docs.html`'s own #14013 maintainer note carries one.
        html = page.read_text(encoding="utf-8", errors="replace")
        lines = html.splitlines()
        for href in extract_anchor_hrefs(html):
            for match in _OWN_URL.finditer(href):
                found["page"].append(
                    (str(page.relative_to(REPO)), _first_line_with(lines, match.group(0)),
                     match.group(0), match.group("frag")))

    for py in sorted(REPO.glob("tortoise/**/*.py")):
        source = py.read_text(encoding="utf-8", errors="replace")
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
                found["python"].append(
                    (str(py.relative_to(REPO)), node.lineno, match.group(0), match.group("frag")))

    js_suffixes = {".js", ".jsx", ".ts", ".tsx"}
    for js in sorted((WEBSITE / "apps" / "dashboard" / "src").rglob("*")):
        if js.suffix not in js_suffixes or not js.is_file():
            continue
        # ⛔ Comments are STRIPPED, not line-skipped — see `strip_js_comments`, which
        # carries the three shapes a line-prefix test misses (`{/*`, a trailing `//`,
        # and a `*/` that closes mid-line). Prose about a past defect (e.g. the #4392
        # comment quoting the dead `product.html#pricing` link it removed) is
        # therefore not read back as a link we emit.
        #
        # ⛔ NOT ONLY `href=`: a module constant that BECOMES an href is not an
        # attribute at its definition site — `billingCta.js`'s `COMPARE_PLANS_URL` is
        # `'https://tortoise.premiselabs.co/#pricing-section'` and reaches the page as
        # the "Compare plans" link while an `href=`-only read sees nothing. Reading
        # the comment-free line covers both, and needs no second pass that would
        # append the same `href=` literal twice.
        for lineno, code in strip_js_comments(js.read_text(encoding="utf-8", errors="replace")):
            for match in _OWN_URL.finditer(code):
                found["dashboard"].append(
                    (str(js.relative_to(REPO)), lineno, match.group(0), match.group("frag")))

    # ⛔ The PAGES FUNCTIONS were outside every arm: `website/functions/blog/_lib.ts`
    # renders the nav of every blog post and its "Pricing" link is
    # `https://premiselabs.co/tortoise#pricing-section` — a hand-written own-site
    # fragment href in a marketing source, which is this file's own subject, read by
    # nothing. Same treatment as the dashboard: strip comments, then read the line.
    for ts in sorted((WEBSITE / "functions").rglob("*")):
        if ts.suffix not in {".ts", ".tsx"} or not ts.is_file():
            continue
        for lineno, code in strip_js_comments(ts.read_text(encoding="utf-8", errors="replace")):
            for match in _OWN_URL.finditer(code):
                found["functions"].append(
                    (str(ts.relative_to(REPO)), lineno, match.group(0), match.group("frag")))

    md_roots = [REPO / "tortoise", WEBSITE / "apps" / "dashboard" / "public" / "skills"]
    for root in md_roots:
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            for lineno, line in enumerate(md.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for pattern in (_MD_LINK, _MD_AUTOLINK):
                    for link in pattern.finditer(line):
                        match = _OWN_URL.search(link.group("url"))
                        if match:
                            found["markdown"].append(
                                (str(md.relative_to(REPO)), lineno, match.group(0), match.group("frag")))

    return found


def test_every_arm_reads_something():
    """A DEAD arm is a blind spot wearing a green suite.

    The first cut of this file had four arms and TWO of them extracted nothing at
    all — the markdown arm required ``](url)`` links the onboarding copies never
    write, and the dashboard arm required an ``href=`` a module constant never
    spells. ``assert targets`` on the flat union was satisfied by the Python arm
    alone, so neither was visible. This is the arm that makes that a red.
    """
    per_arm = targets()
    dead = [arm for arm, hits in per_arm.items() if not hits]
    assert not dead, (
        "these extraction arms read NOTHING — the guard is blind there, not green: "
        f"{dead}. Teach the arm the form the tree actually uses, or retire the arm "
        "and the docstring paragraph that claims it.")


def test_emitted_own_site_fragments_resolve_to_a_declared_id():
    per_arm = targets()
    every = [hit for hits in per_arm.values() for hit in hits]
    assert every, "extraction found nothing — the guard is blind, not green"

    dead: list[str] = []
    unmapped: list[str] = []
    for site, lineno, url, frag in every:
        page = resolve(url)
        if page is None:
            unmapped.append(f"{site}:{lineno}  {url}  — path not in PATH_TO_PAGE")
            continue
        if frag not in page_ids(page):
            dead.append(f"{site}:{lineno}  {url}  — {page} declares no id={frag!r}")

    assert not unmapped, "own-site path serving an unknown page:\n" + "\n".join(unmapped)
    assert not dead, "fragment link that lands at the top of the page:\n" + "\n".join(sorted(set(dead)))


def test_same_page_fragments_resolve():
    """Same-page ``href="#frag"`` links, read through the render-fidelity seam.

    ⛔ NOT A REGEX. ``_html_links.extract_anchor_hrefs`` accepts the quoted,
    single-quoted AND unquoted spellings HTML permits and skips comment/raw-text
    content — the module's own note records that a source regex for this missed
    ``href='#x'`` and ``href=#x``, which is a spellings gap this guard would have
    inherited.
    """
    dead: list[str] = []
    pages = sorted(WEBSITE.glob("*.html"))
    assert pages, "no pages found under website/"
    for page in pages:
        html = page.read_text(encoding="utf-8", errors="replace")
        declared = page_ids(page.name)
        lines = html.splitlines()
        for href in sorted(set(extract_anchor_hrefs(html))):
            if not href.startswith("#") or href == "#":
                continue
            if href[1:] not in declared:
                dead.append(f"{page.name}:{_first_line_with(lines, href)}  href={href}")
    assert not dead, "same-page fragment with no such id:\n" + "\n".join(dead)
