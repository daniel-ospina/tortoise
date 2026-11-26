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

WHAT IT READS — five arms, and each one asserts it is still NON-EMPTY.

The per-arm assertion is the point, not decoration. A single ``assert targets``
only catches TOTAL blindness; it says nothing about one arm that quietly stopped
matching (an autolink spelling the markdown regex did not expect, a source tree
that moved). A rotted arm passes silently under a global count, which is the
same false green as a fragment nobody checks — so each arm reports its own
count and a zero fails the test by name. The first draft of this file had a
``dashboard-src`` arm matching ``href=`` only, which extracted **nothing** on
every run; the global count hid it, and the per-arm grade is what surfaced it.

  * ``python-literals`` — ``tortoise/**/*.py`` string LITERALS that are code, not
    docstrings (an AST walk, so a URL quoted in prose is not mistaken for one we
    emit). The ``appeal_url()`` default is exactly this shape.
  * ``dashboard-literals`` — own-site fragments in
    ``website/apps/dashboard/src/**/*.{js,jsx,ts,tsx}`` STRING LITERALS. Not
    ``href=`` attributes: the dashboard builds its destinations in constants
    (``billingCta.js``'s ``COMPARE_PLANS_URL``), so an attribute-only arm sees
    nothing at all. Comments are removed by a QUOTE-AWARE stripper before the
    scan — see :func:`_strip_comments` for why a line-prefix test is wrong here
    in both directions.
  * ``markdown`` — ``[text](url#frag)`` AND ``<url#frag>`` autolinks under
    ``tortoise/`` and ``website/apps/dashboard/public/skills/``. Both spellings
    are here because the onboarding copies carry the fragment as an AUTOLINK
    (``<https://premiselabs.co/docs#chatgpt>``), which an inline-link-only regex
    misses entirely.
  * ``website-html`` — own-site ABSOLUTE hrefs in ``website/*.html``, read
    through the parsed ``extract_anchor_hrefs`` rather than a raw-source regex,
    in both directions the raw source gets wrong: it would skip a legal
    single-quoted or unquoted ``href`` (silent), and it would count an
    ``href="#…"`` sitting inside an HTML comment (spurious).
  * ``website-functions`` — own-site fragment URLs in ``website/functions/**/*.ts``
    string literals. The blog's ``navHtml`` is built here, so a dead fragment in
    it is dead on EVERY blog post and appears in no ``.html`` file at all.

RESOLUTION IS HOST-AWARE, and the host is not a detail. ``_middleware.ts``
serves ONE marketing host plus a permanent 301 off the old one, and the two do
NOT agree at the root:

  * ``premiselabs.co/`` is the COMPANY page (``index.html``).
  * ``tortoise.premiselabs.co/`` is a PERMANENT 301 onto ``premiselabs.co/tortoise``,
    which is the PRODUCT page (``product.html``) — so its root carries
    ``#pricing-section`` and the company root does not. A single host-blind table
    cannot say both, and getting it wrong is a false green in one direction and a
    false red in the other. A browser preserves the fragment across both hops.

``website/_redirects`` + ``website/functions/_middleware.ts`` decide which file
serves a path — ``/tortoise`` is ``product.html``, ``/docs`` and ``/tortoise/docs``
are ``docs.html`` — and a browser PRESERVES the fragment across a redirect whose
target carries none, which is why ``/product.html#pricing`` was still dead after
that path 301'd away. The table below is that map, spelled out rather than
derived: deriving it means parsing a TS middleware branch. A path the table does
not name is resolved only by the whole-path probe (``/foo`` → ``foo.html``, i.e.
a root-level page whose URL IS its file name); anything whose URL is a redirect
or a rewrite target must be taught here, and an unresolvable path FAILS with the
page and the path in the message instead of passing quietly.

DELIBERATELY NOT MODELLED, so a green run is not read as more than it is:

  * An id rendered at runtime by ``setAttribute`` / ``id={…}`` is invisible to a
    static read, and so is any DASHBOARD SAME-PAGE fragment: every arm here keys
    on an absolute ``https?://<own-host>/…#frag``, so the dashboard's
    ``href="#settings-memory-heading"`` is not graded at all (that id happens to
    be a literal ``id=`` in ``main.jsx``, which is why it looks covered — it is
    not). Only ``website/*.html`` gets the same-page treatment.
  * Whether the id is the RIGHT destination, only that it exists. ``#appeal``
    landing on a section that talks about something else would pass here.
  * The turtle host's NON-root branches. Only the root forms this tree actually
    emits are modelled (``/``, ``/index``, ``/index.html`` → ``product.html``);
    the rest of that host's 301 block is not transcribed, so a NEW fragment on
    the old host is graded by the company table and must be taught.
  * A fragment carried through a redirect chain whose FINAL page differs from
    the table's entry. The table is the map; a new 301/rewrite edge is a diff
    away from making it wrong, and this file cannot see that.

OVERLAP, stated so the two do not drift apart in silence:
``tests/test_website_docs_consistency.py::test_docs_toc_anchors_all_resolve``
already pins ``docs.html``'s ``href="#…"`` set against its ids. The
``test_same_page_fragments_resolve`` arm below is broader (every
``website/*.html``) and therefore still earns its place, but for ``docs.html``
the two guards grade the same property, and a change to that page's TOC should
expect both.
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

COMPANY_HOSTS = ("premiselabs.co", "www.premiselabs.co")
TURTLE_HOST = "tortoise.premiselabs.co"
OWN_HOSTS = (*COMPANY_HOSTS, TURTLE_HOST)

#: path (no fragment, no query) → the page file that serves it ON THE COMPANY HOST.
#:
#: ``_middleware.ts``: ``premiselabs.co/`` is the COMPANY page (``index.html``),
#: NOT the product. The product document has ONE canonical address,
#: ``premiselabs.co/tortoise``, served by a rewrite onto ``product.html``; every
#: ``/product``, ``/product.html`` spelling collapses onto it. ``/docs`` and
#: ``/docs.html`` are the docs page (``_redirects`` 301s the ``.html`` spelling to
#: the clean one), and ``/tortoise/docs`` is the same document under the product
#: prefix. The turtle host is folded into this table by :func:`resolve`.
PATH_TO_PAGE = {
    "/": "index.html",
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
}

#: Turtle-host paths that 301 onto the PRODUCT page rather than the company one.
_TURTLE_ROOT_PATHS = frozenset({"", "/", "/index", "/index.html"})

_HOSTS_ALT = "|".join(re.escape(h) for h in OWN_HOSTS)
_OWN_URL = re.compile(
    rf"https?://(?P<host>{_HOSTS_ALT})/(?P<path>[^\"'\s)>`#]*)#(?P<frag>[^\"'\s)>`#]+)"
)
_JS_STR = re.compile(r"""["'`](?P<url>[^"'`]*#[^"'`]*)["'`]""")
_MD_LINK = re.compile(r"\]\((?P<url>https?://[^)\s#]+#[^)\s]+)\)")
_MD_AUTOLINK = re.compile(r"<(?P<url>https?://[^>\s#]+#[^>\s]+)>")

#: The comment scanner's declared seam: a regex literal ending in two escaped
#: slashes exposes a `//` pair that is not a comment start. Graded by
#: ``test_no_own_site_target_is_lost_to_the_comment_scanner``.
_REGEX_END_SEAM = re.compile(r"\\/\\/")

#: Files where ``_strip_comments`` desynchronises, and how many COMMENT lines
#: survive the strip in each (``//``, ``/*`` and ``*`` all counted — the
#: ``*``-prefixed 64 of them are all in ``_lib.ts``, and an earlier revision of
#: this pin counted only ``//``, leaving ~18% of the residual invisible: a new
#: desync window enclosing a JSDoc block would have changed no count). MEASURED
#: on this tree; pinned by
#: ``test_the_comment_scanner_desynchronises_only_where_it_is_pinned``.
#:
#: Cause: a backtick inside a regex literal (``/^ {0,3}```/``, ``_lib.ts``) is
#: read as a template opener, so from there to the next backtick the machine
#: stops stripping comments. Inherited from
#: ``website/apps/dashboard/src/testSupport.js::stripComments`` — the JS original
#: behaves identically, so this is NOT a port defect — and its docstring
#: prescribes the coverage pin that test supplies.
_PINNED_COMMENT_SURVIVORS = {
    "website/apps/dashboard/src/overview.test.js": 102,
    "website/functions/blog/_lib.ts": 163,
    "website/functions/blog/api/posts/[[path]].ts": 96,
    "website/apps/dashboard/src/keyExpiryTripwire.test.js": 31,
    "website/apps/dashboard/src/distBundle.test.js": 14,
    "website/apps/dashboard/src/demandCaptureRender.test.js": 10,
    "website/apps/dashboard/src/keyTeamPinsTripwire.test.js": 7,
    "website/apps/dashboard/src/securityAlerts.test.js": 5,
    "website/apps/dashboard/src/harnessDisclosureTripwire.test.js": 1,
}

_PAGE_ID_CACHE: dict[str, set[str]] = {}

#: A target: (site, line, url, fragment, host).
Target = tuple[str, int, str, str, str]


def _dashboard_sources() -> list[Path]:
    """Every file arm 2 reads, in a stable order."""
    js_suffixes = {".js", ".jsx", ".ts", ".tsx"}
    return [
        p for p in sorted((WEBSITE / "apps" / "dashboard" / "src").rglob("*"))
        if p.is_file() and p.suffix in js_suffixes
    ]


def _function_sources() -> list[Path]:
    """Every file arm 5 reads, in a stable order."""
    functions = WEBSITE / "functions"
    return [p for p in sorted(functions.rglob("*.ts")) if p.is_file()] if functions.is_dir() else []


def page_ids(name: str) -> set[str]:
    """Ids a page DECLARES, plus the legacy ``<a name>`` a browser also honours."""
    if name not in _PAGE_ID_CACHE:
        html = (WEBSITE / name).read_text(encoding="utf-8")
        ids = {i for i, _line in extract_element_ids(html)}
        ids |= set(re.findall(r"""<a\s[^>]*\bname="([^"]+)\"""", html))
        _PAGE_ID_CACHE[name] = ids
    return _PAGE_ID_CACHE[name]


def resolve(path: str, host: str) -> str | None:
    """The page file serving ``path`` on ``host``, or None when it must be taught.

    ⛔ Only the WHOLE path may be probed. A basename fallback would map an
    unmapped multi-segment path onto an unrelated root page (``/blog/docs`` →
    ``docs.html``) and grade a fragment against the wrong document — a false
    green dressed as a resolution.
    """
    if host == TURTLE_HOST and path in _TURTLE_ROOT_PATHS:
        # _middleware.ts: this host's ROOT is the PRODUCT page; it 301s onto the
        # company host's /tortoise, and the fragment rides along.
        path = "/tortoise"
    if path in PATH_TO_PAGE:
        return PATH_TO_PAGE[path]
    candidate = f"{path.lstrip('/')}.html"
    if candidate and (WEBSITE / candidate).is_file():
        return candidate
    return None


def _line_index(html: str, hrefs: list[str]) -> list[int]:
    """The line number of each href, one entry PER OCCURRENCE.

    ``_line_of`` returned the FIRST match for every occurrence, so a page that
    carries the same href twice reported the first line both times — pointing a
    fixer at an anchor that is already correct. The positions list is expanded
    by occurrences-per-line, not by lines, because ``website/tos.html`` carries
    TWO of these anchors on one line (220); a per-line list would run out and
    fall back to 0.

    ⛔ Two spellings are searched, because ``extract_anchor_hrefs`` DECODES
    attribute values (``&amp;`` -> ``&``) while the lines here are RAW. Matching
    only the decoded form reported line 0 for every href carrying a query —
    measured: 4 today in ``website/tortoise-for-product-teams.html``, which
    ships UTM-tagged links. Those are relative paths so no graded target was
    affected, but an ABSOLUTE own-site deep link with ``?…&…#frag`` is exactly
    that shape, and a line-0 pointer beside a "not in PATH_TO_PAGE" message is a
    worse diagnosis than none — ``test_every_arm_still_finds_its_shape`` now
    fails on any line 0 for that reason.
    """
    lines = html.splitlines()
    positions: dict[str, list[int]] = {}
    out: list[int] = []
    for href in hrefs:
        if href not in positions:
            spellings = {href, href.replace("&", "&amp;")}
            positions[href] = [
                lineno for lineno, line in enumerate(lines, 1)
                for _ in range(max(line.count(s) for s in spellings))
            ]
        remaining = positions[href]
        out.append(remaining.pop(0) if remaining else 0)
    return out


def _strip_comments(source: str) -> str:
    """Remove JS/TS comments, PRESERVING line numbers so reported lines stay true.

    A quote-aware scanner, ported from ``website/apps/dashboard/src/testSupport.js``
    ``stripComments`` — the repo's pinned implementation, whose unit tests in
    ``testSupport.test.js`` are the spec for its seams.

    ⛔ WHY A SCANNER AND NOT A LINE-PREFIX TEST. This file first filtered lines
    with ``line.lstrip().startswith(("//", "*"))``, which is wrong in BOTH
    directions, and both were reachable in this tree:

      * FALSE RED — a line STARTING with a comment marker is skipped, but a
        TRAILING ``// …`` is not, so ``export const X = 1 // see "https://…"``
        grades prose as a live emission. This tree carries exactly that shape
        (``membersInviteUpsell.test.js`` survives only because its ``//`` is at
        line start).
      * FALSE GREEN — a line whose first non-space char is ``*`` is skipped
        wholesale, but ``website/functions/blog/_lib.ts`` builds HTML inside a
        BACKTICK template, where a ``*``-prefixed markdown bullet is code, not a
        JSDoc line. A dead fragment there was invisible.

    Both directions matter differently and neither is acceptable: this guard
    gates every future PR, so a false red blocks correct trees.

    Differences from the JS original, both deliberate: every newline consumed
    inside a comment is re-emitted (the original scans for tokens and does not
    care about line numbers; this file reports them), and the accepted
    limitations are the same (§ its docstring): a regex literal ending ``\\/\\/``
    and nested backticks inside ``${…}`` can expose a ``//`` that is not a
    comment, truncating the rest of that line.

    ⛔ THOSE SEAMS ARE PRESENT IN THIS TREE, not hypothetical. Measured: 21 lines
    across the files arms 2 and 5 read carry that escaped-slash pair — 17 a LIVE
    regex literal and 4 quoted in comments — including
    ``website/functions/blog/_lib.ts:360``
    (``/^https?:\\/\\//i.test(href.trim())``), the file that BUILDS ``navHtml``, so
    a same-line own-site fragment after that regex would be silently invisible.
    No target is lost today, and that is not left to trust:
    ``test_no_own_site_target_is_lost_to_the_comment_scanner`` re-measures it on
    every run and FAILS if a fragment URL ever starts sharing a line with a
    truncated one. Keep a fragment URL off a line carrying such a regex literal —
    that is the rule, and the test is what enforces it.

    ⛔ AND ONE SEAM IS INHERITED, NOT OURS. A backtick inside a REGEX literal
    (``/^ {0,3}```/``, ``_lib.ts``) is read as a template opener, and from there
    to the next backtick comments are not stripped. That is the same behaviour
    the JS original has, and its docstring prescribes the control used here: a
    spread coverage pin, ``_PINNED_COMMENT_SURVIVORS`` plus
    ``test_the_comment_scanner_desynchronises_only_where_it_is_pinned``. The
    consequence to hold in mind while reading a green run: inside those windows
    comment prose is scanned as if it were code, so this file can report a URL
    that is only a citation. The pinned counts (429 comment lines across 9 files —
    365 ``//``-prefixed plus 64 ``*``/``/*``, all of the latter in ``_lib.ts``) are
    what make that residual visible instead of assumed-away.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/" and (i == 0 or source[i - 1] != ":"):
            # line comment — to (not incl.) the newline, which the main loop keeps
            while i < n and source[i] != "\n":
                i += 1
        elif c == "/" and nxt == "*":
            # block comment — run past the closing */, preserving its newlines
            i += 2
            while i < n and not (source[i] == "*" and i + 1 < n and source[i + 1] == "/"):
                if source[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
        elif c in "\"'`":
            # string / template — copy verbatim so a `//` INSIDE it (a scheme, or
            # any URL) is never read as a comment start. A ' or " cannot span an
            # unescaped newline; ending it there stops a JSX-text apostrophe from
            # opening a phantom multi-line string that swallows real comments.
            quote = c
            out.append(c)
            i += 1
            while i < n:
                sc = source[i]
                if sc == "\\":
                    out.append(sc)
                    if i + 1 < n:
                        out.append(source[i + 1])
                    i += 2
                    continue
                if sc == "\n" and quote != "`":
                    break
                out.append(sc)
                i += 1
                if sc == quote:
                    break
        else:
            out.append(c)
            i += 1
    return "".join(out)


def own_site_targets() -> dict[str, list[Target]]:
    """``arm -> [(site, line, url, fragment, host)]`` for every own-site fragment.

    Returned per-arm (not flattened) so each arm can be graded on its own; see
    the module docstring for why a single global count is not enough.
    """
    arms: dict[str, list[Target]] = {
        "python-literals": [],
        "dashboard-literals": [],
        "markdown": [],
        "website-html": [],
        "website-functions": [],
    }

    def add(arm: str, site: str, lineno: int, match: re.Match[str]) -> None:
        arms[arm].append(
            (site, lineno, match.group(0), match.group("frag"), match.group("host")))

    # ── arm 1: tortoise/**/*.py string literals that are code, not docstrings ─
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
                add("python-literals", str(py.relative_to(REPO)), node.lineno, match)

    # ── arm 2: the dashboard's own-site destination literals ─────────────────
    for js in _dashboard_sources():
        source = _strip_comments(js.read_text(encoding="utf-8", errors="replace"))
        for lineno, line in enumerate(source.splitlines(), 1):
            for literal in _JS_STR.finditer(line):
                match = _OWN_URL.search(literal.group("url"))
                if match:
                    add("dashboard-literals", str(js.relative_to(REPO)), lineno, match)

    # ── arm 3: markdown, inline links AND angle-bracket autolinks ────────────
    md_roots = [REPO / "tortoise", WEBSITE / "apps" / "dashboard" / "public" / "skills"]
    for root in md_roots:
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            for lineno, line in enumerate(md.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                candidates = [m.group("url") for m in _MD_LINK.finditer(line)]
                candidates += [m.group("url") for m in _MD_AUTOLINK.finditer(line)]
                for url in candidates:
                    match = _OWN_URL.search(url)
                    if match:
                        add("markdown", str(md.relative_to(REPO)), lineno, match)

    # ── arm 4: own-site ABSOLUTE hrefs in the marketing pages ────────────────
    for page in sorted(WEBSITE.glob("*.html")):
        html = page.read_text(encoding="utf-8", errors="replace")
        hrefs = extract_anchor_hrefs(html)
        for href, lineno in zip(hrefs, _line_index(html, hrefs), strict=True):
            match = _OWN_URL.search(href)
            if match:
                add("website-html", page.name, lineno, match)

    # ── arm 5: the Pages Functions that BUILD html (the blog's navHtml) ───────
    functions = WEBSITE / "functions"
    if functions.is_dir():
        for ts in _function_sources():
            source = _strip_comments(ts.read_text(encoding="utf-8", errors="replace"))
            for lineno, line in enumerate(source.splitlines(), 1):
                for literal in _JS_STR.finditer(line):
                    match = _OWN_URL.search(literal.group("url"))
                    if match:
                        add("website-functions", str(ts.relative_to(REPO)), lineno, match)

    return arms


def test_every_arm_still_finds_its_shape():
    """A dead arm is a false green: it reports OK because it read nothing.

    Graded per arm, because a global count hides exactly this — one arm rots, the
    others keep the total non-zero, and the suite stays green over an unguarded
    spelling.
    """
    arms = own_site_targets()
    empty = sorted(name for name, found in arms.items() if not found)
    assert not empty, (
        "own-site fragment arm(s) extracted nothing — each is blind, not clean: "
        f"{empty}. Counts: { {k: len(v) for k, v in arms.items()} }")
    # A target reported at line 0 is unlocatable: the fixer is told the URL is
    # wrong and not where it is. See _line_index — the raw/decoded spelling split
    # is what produced it.
    unlocated = [f"{arm}: {row[0]}  {row[2]}" for arm, found in arms.items()
                 for row in found if row[1] == 0]
    assert not unlocated, "own-site target(s) with no locatable line:\n" + "\n".join(unlocated)


def test_emitted_own_site_fragments_resolve_to_a_declared_id():
    arms = own_site_targets()
    targets = [row for found in arms.values() for row in found]

    dead: list[str] = []
    unmapped: list[str] = []
    for site, lineno, url, frag, host in targets:
        path = "/" + _OWN_URL.search(url).group("path")
        page = resolve(path, host)
        if page is None:
            unmapped.append(f"{site}:{lineno}  {url}  — {host}{path} not in PATH_TO_PAGE")
            continue
        if frag not in page_ids(page):
            dead.append(f"{site}:{lineno}  {url}  — {page} declares no id={frag!r}")

    assert not unmapped, "own-site path serving an unknown page:\n" + "\n".join(unmapped)
    assert not dead, "fragment link that lands at the top of the page:\n" + "\n".join(dead)


def test_no_own_site_target_is_lost_to_the_comment_scanner():
    """The scanner's known seams must not be silently eating a real target.

    ``_strip_comments`` is a quote-aware stripper, not a tokenizer: a regex
    literal ending in two escaped slashes exposes a ``//`` that is not a comment,
    so the rest of that line is stripped. That seam is PRESENT in this tree (21
    lines across the files these arms read, including ``_lib.ts:360``), so "no
    target is lost" is a MEASUREMENT, not a property — and a measurement left in
    a docstring is the next thing to drift.

    The rule it enforces is "no fragment URL in the TRUNCATED REMAINDER of a
    marker line", comment or not. A URL BEFORE the marker is graded normally (it
    survives the strip) and is not reported here, so the rule is narrower than
    "no fragment URL on a marker line" — and a URL sitting in a genuine trailing
    ``//`` comment after the marker IS stripped and WILL be reported, which is
    intended: the remedy this names (move the URL onto its own line, or out of
    the comment) is correct either way.
    """
    lost: list[str] = []

    def check(path: Path, raw: str) -> None:
        for lineno, line in enumerate(raw.splitlines(), 1):
            if not _REGEX_END_SEAM.search(line):
                continue
            # Line-local strip on purpose: the only seam in scope is the
            # same-line truncation, and a multi-line block comment would
            # otherwise make a single line look unterminated.
            stripped = _strip_comments(line)
            for match in _OWN_URL.finditer(line):
                if match.group(0) not in stripped:
                    lost.append(f"{path.name}:{lineno}  {match.group(0)}")

    for path in (*_dashboard_sources(), *_function_sources()):
        check(path, path.read_text(encoding="utf-8", errors="replace"))

    assert not lost, (
        "an own-site fragment URL shares a line with a regex literal ending in "
        "escaped slashes, so the comment scanner truncates it and the URL is not "
        "graded — move the URL onto its own line:\n" + "\n".join(lost))


def test_the_comment_scanner_desynchronises_only_where_it_is_pinned():
    """Pin the stripper's KNOWN desynchronisation, measured rather than trusted.

    ``_strip_comments`` is quote-aware, not a tokenizer, and it inherits one
    inversion from the implementation it was ported from
    (``website/apps/dashboard/src/testSupport.js::stripComments``): a backtick
    inside a REGEX literal (``/^ {0,3}```/``, ``_lib.ts``) is read as a template
    opener, so from there to the next backtick the machine believes it is inside
    a string and stops stripping comments. The JS original has the identical
    behaviour — this is not a port defect — and its own docstring prescribes the
    control this test supplies: "Callers must therefore pin scan coverage with
    sentinels spread across the file".

    The pin is a MEASUREMENT, not a guarantee: the file set and counts below are
    this tree's, and the assertion fails when they move — a file that starts
    desynchronising (new ungraded region), a window that widens, OR a comment
    added inside an existing window (which is the common case in practice: the
    comment is not stripped BECAUSE it is inside the window, so the count moves
    on a correct tree). It cannot make the arms correct; it makes the residual
    LOUD, so nobody reads a green run as "comments are fully stripped here".

    Why this is the right size of fix: a tokenizer would close the seam, but
    that is #3102's scope for the ORIGINAL implementation, and a second,
    divergent parser here would be a worse defect than the one it fixes. What
    this file must not do is let the seam undo the two directions it was added
    for, silently.
    """
    survivors: dict[str, int] = {}
    for path in (*_dashboard_sources(), *_function_sources()):
        raw = path.read_text(encoding="utf-8", errors="replace")
        stripped = _strip_comments(raw)
        raw_lines, stripped_lines = raw.splitlines(), stripped.splitlines()
        assert len(raw_lines) == len(stripped_lines), (
            f"_strip_comments changed the line count for {path} — every reported "
            "line number in this file would be wrong")
        n = sum(
            1 for before, after in zip(raw_lines, stripped_lines, strict=True)
            if before.lstrip().startswith(("//", "/*", "*")) and before == after
        )
        if n:
            survivors[str(path.relative_to(REPO))] = n

    assert survivors == _PINNED_COMMENT_SURVIVORS, (
        "the comment scanner's desynchronised regions moved. This is a pin, not a "
        "pass/fail on correctness: re-measure, then either fix the cause or update "
        "the pin WITH the reason. Found:\n"
        + "\n".join(f"  {k}: {v}" for k, v in sorted(survivors.items()))
        + "\nPinned:\n"
        + "\n".join(f"  {k}: {v}" for k, v in sorted(_PINNED_COMMENT_SURVIVORS.items())))


def test_appeal_url_is_the_literal_this_guard_reads():
    """Pin the one emission #11939 was filed for, by name.

    Without this, deleting ``appeal_url()``'s URL literal would leave the
    ``python-literals`` arm populated by ``capacity_notice.py`` alone — the arm
    stays "non-empty", the suite stays green, and the link #11939 exists for is
    unguarded. The per-arm count cannot see a single literal disappearing.
    """
    appeal = [row for row in own_site_targets()["python-literals"] if row[3] == "appeal"]
    assert appeal, (
        "no own-site #appeal literal found in tortoise/**/*.py — appeal_url()'s "
        "default either moved or stopped being a plain string literal")
    assert any("abuse.py" in site for site, *_rest in appeal), (
        f"the #appeal literal is no longer emitted from tortoise/abuse.py: {appeal}")


def test_line_index_locates_hrefs_that_html_decoding_rewrites():
    """``_line_index`` must locate an href the parser DECODED, not only raw text.

    ``extract_anchor_hrefs`` HTML-decodes attribute values (``&amp;`` -> ``&``)
    while the page lines are RAW, so a position map built from the decoded form
    alone reports line 0. Measured before the fix: 4 hrefs in
    ``website/tortoise-for-product-teams.html``. Those are relative UTM links,
    which ``_OWN_URL`` never matches, so NO graded target was misreported — and
    that is exactly why this fixture exists: without it the fix could be
    reverted and the whole file would stay green, because the shape that was
    broken is not the shape the resolution arm grades.
    """
    html = (
        '<a href="https://premiselabs.co/docs?a=1&amp;b=2#appeal">x</a>\n'
        '<a href="/tortoise?utm_source=t&amp;utm_medium=cta">y</a>\n'
        '<a href="https://premiselabs.co/docs#appeal">z</a>\n'
    )
    hrefs = extract_anchor_hrefs(html)
    assert hrefs[0] == "https://premiselabs.co/docs?a=1&b=2#appeal", (
        "the parser no longer decodes &amp; — this fixture (and _line_index's "
        "two-spelling search) are calibrated to that behaviour")
    assert _line_index(html, hrefs) == [1, 2, 3]


def test_same_page_fragments_resolve():
    """Every ``href="#frag"`` inside ``website/*.html`` targets an id on that page.

    Parsed, not pattern-matched: ``extract_anchor_hrefs`` accepts quoted,
    single-quoted and unquoted attribute values, and it does not see an
    ``href="#…"`` sitting inside an HTML comment. A raw regex gets both wrong,
    in opposite directions (false green on the legal spellings, false red on the
    commented one).
    """
    dead: list[str] = []
    pages = sorted(WEBSITE.glob("*.html"))
    assert pages, "no pages found under website/"

    for page in pages:
        html = page.read_text(encoding="utf-8", errors="replace")
        declared = page_ids(page.name)
        hrefs = extract_anchor_hrefs(html)
        for href, lineno in zip(hrefs, _line_index(html, hrefs), strict=True):
            if not href.startswith("#"):
                continue
            frag = href[1:]
            if frag not in declared:
                dead.append(f"{page.name}:{lineno}  href=#{frag}")

    assert not dead, "same-page fragment with no such id:\n" + "\n".join(dead)
