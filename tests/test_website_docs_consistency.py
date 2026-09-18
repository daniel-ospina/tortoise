"""Cross-page consistency guards for the public documentation surfaces (#3332).

Pins the facts the two public pages share — the Point status vocabulary, the
ontology citation, and how `confidence` is computed — so they cannot silently
drift apart again. The status vocabulary is asserted in full on `docs.html`
(the page that documents the lifecycle); `faq.html` is held to "all six or none",
since it does not document statuses at all.
#3332 was exactly this failure. `docs.html` had carried, for five weeks and through
every PR that touched it:

  * a stale ontology version citation (`v3.4` — the repo was on `v3.10`),
  * a partial Point-status list (4 of the 6 canonical values),
  * the **wrong belief mechanism** — a linear-system `grounding` solve presented as
    what `confidence` holds. `projection.compute_grounding()` writes `Point.grounding`,
    a different field entirely; `confidence` is written by the expectation-propagation
    engine (`tortoise/ep.py`, `tortoise/dream.py`) and read back by `sdk.get_confidence()`,
  * two issue references (`#7881`, `#7882`) that do not exist.

Nothing failed. The public docs simply described a system that was never shipped.

Canonical sources these tests key off:
  * `tortoise/sdk.py`  — `POINT_STATUS_VALUES`
  * `docs/ONTOLOGY.md` — the ontology (linked by both pages, never version-pinned inline)
  * `tortoise/ep.py`   — how `confidence` is computed

Unconditional (no network, no browser, no DB): plain string pins on the checked-in
HTML, matching the harness contract of `tests/test_signup_form_safety.py`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # tools/ is not an installed package
    sys.path.insert(0, str(REPO_ROOT))

from tools.ci_selection import load_manifest, select  # noqa: E402, I001
from tests._html_links import extract_anchor_hrefs  # noqa: E402

WEBSITE = REPO_ROOT / "website"
FUNCTIONS = WEBSITE / "functions"
PRODUCT = WEBSITE / "product.html"
DOCS = WEBSITE / "docs.html"
FAQ = WEBSITE / "faq.html"
REDIRECTS = WEBSITE / "_redirects"
SDK = REPO_ROOT / "tortoise" / "sdk.py"

# Both public pages, for the parametrized guards.
PAGES = pytest.mark.parametrize("page", [DOCS, FAQ], ids=["docs.html", "faq.html"])

# The external study the FAQ cites in support of keeping a verbatim source layer.
_PAPER_TITLE = "Fidelity Before Structure"
# The title this page cited before it was corrected: not a real paper title.
_FABRICATED_TITLE = "Verbatim Chunks Beat Extracted Artifacts"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing public page: {path}"
    return path.read_text(encoding="utf-8")


def _canonical_statuses() -> set[str]:
    src = _read(SDK)
    m = re.search(r"POINT_STATUS_VALUES\s*=\s*frozenset\(\{([^}]*)\}\)", src)
    assert m, "POINT_STATUS_VALUES not found in tortoise/sdk.py — did the definition move?"
    return set(re.findall(r"'([^']+)'", m.group(1)))


# ── Point status vocabulary ──────────────────────────────────────────────────


def test_sdk_status_vocabulary_is_what_the_pins_below_assume() -> None:
    """Guard the guard: if sdk.py's statuses change, the pins must be revisited."""
    assert _canonical_statuses() == {
        "draft",
        "live",
        "retracted",
        "superseded",
        "outdated",
        "archived",
    }


def test_docs_lists_every_canonical_point_status() -> None:
    """No public page may ship a partial Point status vocabulary.

    `docs.html` documents the lifecycle, so it must carry all six. `faq.html`
    does not document statuses at all — it is only required to stay internally
    consistent if it ever starts to.

    Accepts either rendering the page uses: the lifecycle list uses
    ``status: <name>`` and the legacy-flag footnote uses ``<code><name></code>``.
    """
    canonical = _canonical_statuses()
    for page in (DOCS, FAQ):
        html = _read(page)
        present = {
            s
            for s in canonical
            if f"status: {s}" in html or f"<code>{s}</code>" in html
        }
        assert present in (set(), canonical), (
            f"{page.name} lists some but not all canonical Point statuses "
            f"({sorted(present)}). Canonical set: POINT_STATUS_VALUES in "
            f"tortoise/sdk.py. Either list all six or none — a partial list is "
            f"the #3332 defect."
        )
        if page == DOCS:
            assert present == canonical, (
                f"website/docs.html omits canonical Point status(es): "
                f"{sorted(canonical - present)}."
            )


# ── The belief mechanism (the #3332 headline defect) ─────────────────────────


@PAGES
def test_no_page_describes_confidence_as_a_linear_solve(page: Path) -> None:
    """Neither public page may present `grounding` as the confidence mechanism.

    `grounding` is an internal projection field (`tortoise/projection/grounding.py`,
    a damped linear solve over an undirected adjacency matrix). It is not exposed by
    the hosted API, the SDK, or the MCP tools, and it has never been what
    `confidence` holds. It has no business appearing in consumer documentation —
    so the pin is that it appears nowhere on either page.
    """
    html = _read(page)
    for bad in ("grounding", "λM", "λ"):
        assert bad not in html, (
            f"{page.name} mentions {bad!r}. If this is the internal `grounding` "
            f"projection being described as belief, it is wrong: `confidence` is an "
            f"expectation-propagation posterior (tortoise/ep.py). If it is a deliberate "
            f"new mention, update this test with the reasoning."
        )


@PAGES
def test_both_pages_name_expectation_propagation(page: Path) -> None:
    """EP is the differentiator the product positioning rests on — both pages say so."""
    assert "expectation propagation" in _read(page), (
        f"{page.name} does not name expectation propagation. The public pages must "
        f"describe belief the way it is actually computed (tortoise/ep.py), and EP is "
        f"the claim the competitive analysis is built on."
    )


# ── Ontology citation hygiene ────────────────────────────────────────────────


@PAGES
def test_no_page_pins_an_inline_ontology_version(page: Path) -> None:
    """Both pages link the ontology instead of hardcoding its version.

    A hardcoded version is the specific drift #3332 fixed: `v3.1` (PR #222) became
    `v3.4` (PR #841, a drive-by in an unrelated fix) while the repo moved to `v3.10`,
    with nothing to catch it. Linking `docs/ONTOLOGY.md` cannot go stale.
    """
    html = _read(page)
    stale = re.findall(r"ontology\s+v?\d+\.\d+", html, flags=re.IGNORECASE)
    assert not stale, (
        f"{page.name} pins an ontology version inline: {stale}. Link "
        f"docs/ONTOLOGY.md instead — an inline version number is what drifted last time."
    )
    assert "docs/ONTOLOGY.md" in html, (
        f"{page.name} neither pins a version nor links docs/ONTOLOGY.md — a reader has "
        f"no route to the canonical schema."
    )


# ── Citation integrity on the new page ───────────────────────────────────────


def test_faq_cites_the_real_paper_title() -> None:
    """The FAQ's strongest external evidence must be cited by its actual title.

    The page previously cited a title that does not exist ("Verbatim Chunks Beat
    Extracted Artifacts: A Controlled Ablation") — on a page whose entire credibility
    claim is that its numbers are verifiable. Wrong title, wrong version bracket.
    """
    html = _read(FAQ)
    assert _FABRICATED_TITLE not in html, (
        "website/faq.html cites a paper title that does not exist. The arXiv "
        "2601.00821 record is 'Fidelity Before Structure: Verbatim Chunks Beat Lossy "
        "Artifact Extraction in Long-Conversation LLM Memory' (Tao An)."
    )
    assert _PAPER_TITLE in html, "website/faq.html lost its citation of the ablation study."
    assert "arxiv.org/abs/2601.00821" in html, (
        "website/faq.html names the study but gives no URL — the reader cannot verify "
        "the figures the page stakes its claims on."
    )


@PAGES
def test_no_dead_issue_references_in_public_copy(page: Path) -> None:
    """Public copy must not cite issue numbers, and must never cite nonexistent ones.

    `#7881`/`#7882` were on the page for weeks and resolve to nothing. AGENTS.md also
    makes internal issue numbers noise in public-facing copy.
    """
    html = _read(page)
    for dead in ("#7881", "#7882"):
        assert dead not in html, f"{page.name} cites {dead}, which does not exist."


# ── Link integrity on the new page ───────────────────────────────────────────


def test_faq_toc_anchors_all_resolve() -> None:
    """Every TOC entry must land on a real heading id."""
    html = _read(FAQ)
    hrefs = set(re.findall(r'href="#([^"]+)"', html))
    ids = set(re.findall(r'id="([^"]+)"', html))
    assert hrefs, "website/faq.html has no in-page anchors — did the TOC get removed?"
    assert hrefs <= ids, f"website/faq.html TOC anchors with no target: {sorted(hrefs - ids)}"


def _function_serves(path: str) -> bool:
    """True when a Cloudflare Pages **Function** serves this route.

    Not every route is a committed `.html` asset. `website/functions/` is the
    Pages Functions tree, and a route it handles is a real page with no sibling
    file — `/blog` (and every `/blog/<slug>`) is served by
    `functions/blog/[[path]].ts`. Without this branch the resolver below reports
    a live route as dangling, which is a false failure that would push someone
    to "fix" a correct link.

    A route is Function-served when the Functions tree holds a handler at or
    above it: `functions/<path>.ts|js`, `functions/<path>/index.ts|js`, or an
    ancestor catch-all `functions/<dir>/[[path]].ts|js` (Pages matches
    `[[path]]` against zero or more segments, so it owns the bare parent route
    too).

    Scope: this is the subset of Pages routing THIS repo uses — a deliberate
    subset, not the whole rule. Single-segment dynamic routes
    (`functions/blog/[slug].ts`), `.jsx`/`.tsx` handlers and `_routes.json` are
    not modelled, because none exist here. If one appears, this helper must
    grow with it or a correct link would read as dangling; the `..` rejection
    keeps a malformed href from escaping the tree either way.
    """
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts or ".." in parts:
        return False
    targets = [FUNCTIONS.joinpath(*parts)]
    for depth in range(len(parts), 0, -1):
        targets.append(FUNCTIONS.joinpath(*parts[:depth], "index"))
        targets.append(FUNCTIONS.joinpath(*parts[:depth], "[[path]]"))
    return any(t.with_name(t.name + ext).is_file()
               for t in targets for ext in (".ts", ".js"))


def test_faq_internal_links_point_at_real_pages() -> None:
    """A relative link on the FAQ must resolve to a real page.

    Catches a renamed or removed sibling page, and a typo'd route — the FAQ links
    out to /docs, /self-hosted, /security, /tos, /dpa, /privacy, /license and
    /blog. Fragment-bearing links are matched too (the fragment is split off and
    the path resolved), so a `/#anchor` form cannot slip through unchecked.

    "Real page" is either a committed asset (`.html`, or the extensionless form)
    or a route owned by the Pages Functions tree (`_function_serves`). `/blog` is
    the latter, and is exactly as live as any static page — a resolver that only
    knows about `.html` files would call the blog link dangling.
    """
    html = _read(FAQ)
    missing: list[str] = []
    for href in sorted(set(re.findall(r'href="(/[^"]*)"', html))):
        path = href.split("#", 1)[0]
        if path in ("", "/"):
            continue
        candidate = WEBSITE / path.lstrip("/")
        if (candidate.is_file()
                or candidate.with_suffix(".html").is_file()
                or _function_serves(path)):
            continue
        missing.append(href)
    assert not missing, (
        f"website/faq.html links to route(s) with no page: {missing}. "
        f"Add the page, fix the link, or route it through website/_redirects."
    )


def test_function_route_resolver_recognizes_both_route_kinds() -> None:
    """Guard the guard: the resolver must accept a Function route and still
    reject a typo — otherwise widening it to fix the blog link would have
    silently disabled the check it exists to perform."""
    assert _function_serves("/blog"), "the /blog Function route must resolve"
    assert _function_serves("/blog/some-post"), "a post under the catch-all must resolve"
    assert _function_serves("/welcome"), "/welcome is a Function at the route root"
    assert _function_serves("/admin/any-panel"), "a nested catch-all route must resolve"
    assert not _function_serves("/blogpost"), "/blogpost is NOT a Function route"
    assert not _function_serves("/no-such-route"), "an unknown route must not resolve"


def test_faq_avoids_root_relative_fragment_links() -> None:
    """A bare `/#anchor` link breaks on the company host — use the canonical URL.

    On `tortoise.premiselabs.co` the middleware rewrites `/` to `product.html`,
    which carries `id="pricing-section"`. On `premiselabs.co` `/` is `index.html`,
    which has no pricing content at all — so the bare root-fragment form silently
    lands on a page with a dangling anchor. The sibling E2E test pins
    `PRICING_PAGE_URL` for exactly this reason; this guard keeps the FAQ honest.
    """
    root_fragments = re.findall(r'href="/#([^"]+)"', _read(FAQ))
    assert not root_fragments, (
        f"website/faq.html uses the bare root-fragment form {root_fragments}, which "
        f"breaks on premiselabs.co. Use the absolute canonical "
        f"https://tortoise.premiselabs.co/#<anchor> instead."
    )


# ── #3950: the blog is live but unreachable — pin every public page's way in ──
#
# The blog at /blog is live and crawler-discoverable (`robots.txt` cross-submits
# `/blog/sitemap.xml`), but 0 of the site's public pages linked to it, so a
# visitor could only reach it by typing the URL. The root cause was an OWNERSHIP
# omission — the blog epic scoped discovery to crawlers and its human journey
# *started at* /blog, so nothing ever owned arrival-from-the-site. A one-off
# "add the link" fix would rot the same way, so the property is pinned here:
# every public, indexable, served page must offer a way in.


def _canonical_redirect_targets() -> dict[str, str]:
    """`website/_redirects` as {source-path: target-path}, trailing slashes stripped.

    Only `src  dst  [code]` rows are read; comments and blanks are skipped.
    """
    out: dict[str, str] = {}
    for raw in REDIRECTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        out[parts[0].rstrip("/") or "/"] = parts[1].rstrip("/") or "/"
    return out


def _is_noindex(html: str) -> bool:
    """True when the document declares `<meta name="robots" ... noindex>`.

    Blind spot, stated rather than hidden: this reads the document only. A page
    noindexed by an `X-Robots-Tag` header (`website/_headers`) rather than a meta
    tag would be held in scope, and `_canonical_redirect_targets` likewise reads
    only `website/_redirects` — a page whose route is redirected solely in
    `functions/_middleware.ts` would too. Neither case exists today, and the
    `_IN_SCOPE_AT_3950` pin turns either one into a loud failure (a pin edit with
    a reason) instead of a silent scope change.
    """
    tag = re.search(r'<meta\s+name="robots"[^>]*>', html, re.I)
    return bool(tag and "noindex" in tag.group(0).lower())


def _in_scope_pages() -> list[Path]:
    """The public, indexable, served pages — DERIVED from disk, never listed.

    In scope = public AND indexable AND served. A page declares itself out by:
      * `<meta name="robots" content="noindex...">` — `404.html`, `welcome.html`,
        `invite-accept.html`; or
      * its own clean route 301ing elsewhere in `website/_redirects` —
        `signin.html`, whose `/signin` has no canonical URL of its own
        (`/signin` → `/auth`), so a link there is unreachable by construction.

    Derivation (not enumeration) is the point: a page added later is covered
    WITHOUT editing a list, so it cannot be forgotten the way the blog was.
    `test_blog_guard_covers_every_served_indexable_page` pins the result, so the
    set cannot silently SHRINK either.

    Deliberately NOT excluded: `docs.html` / `faq.html` / `self-hosted.html` are
    `_redirects` SOURCES (their `.html` and trailing-slash forms 301 to the clean
    URL) — but their own clean route is not redirected, so they are served
    documents and stay in scope. The filter is "my route hands off to a different
    document", not "I appear in _redirects".
    """
    redirects = _canonical_redirect_targets()
    pages: list[Path] = []
    for page in sorted(WEBSITE.glob("*.html")):
        html = _read(page)
        if _is_noindex(html):
            continue
        route = f"/{page.stem}"
        if redirects.get(route, route) != route:
            continue
        pages.append(page)
    return pages


def _rendered_hrefs(html: str) -> list[str]:
    """Hrefs of real anchors in rendered markup.

    Thin wrapper over the shared extractor in `tests/_html_links.py`, which the
    E2E layer also calls. That is deliberate: the two layers must agree about what
    a way in is, and the E2E copy had already drifted (it did not strip comments,
    `<script>` or `<style>`) with no test able to detect it — importing the E2E
    module runs its module-level `pytest.skip`. One implementation removes the
    drift class rather than documenting it.
    """
    return extract_anchor_hrefs(html)


def _href_path(href: str) -> str:
    return (urlparse(href).path or "/").rstrip("/") or "/"


def _offers_blog_entry(page: Path) -> bool:
    """True when the page has a real anchor that gets a visitor INTO the blog.

    `/blog` (the index) and `/blog/<slug>` (an article — its own nav links back
    to the index) both count as a way in, so a page is not faulted for linking a
    post instead of the index. What does NOT count: a URL sitting in a comment,
    a `<script>` or a `<style>` (`_rendered_hrefs` strips those first).

    Presence, not visibility — an anchor hidden with `display:none` would still
    satisfy this. The placement that actually matters is pinned separately by
    `test_product_hero_offers_the_blog`.
    """
    return any(_href_path(h) == "/blog" or _href_path(h).startswith("/blog/")
               for h in _rendered_hrefs(_read(page)))


# The served+indexable page set as of #3950 (2026-09-17). This pins the
# DERIVATION'S OUTPUT (not an allowlist the guard consults): if a page later
# leaves the public surface, that is a decision someone must take deliberately,
# not a page that quietly disappears from coverage.
_IN_SCOPE_AT_3950 = frozenset({
    "aviso-privacidad.html", "docs.html", "dpa.html", "faq.html", "index.html",
    "license.html", "privacy.html", "product.html", "security.html",
    "self-hosted.html", "signup.html", "tos.html",
})


def test_every_in_scope_page_links_to_the_blog() -> None:
    """#3950: every public, indexable, served page must offer a way to the blog.

    A page that loses its link fails HERE — that is the whole point. Before this
    guard, deleting the link left every test green (nothing asserted its
    existence), which is how the blog became unreachable in the first place.
    """
    missing = [
        page.name
        for page in _in_scope_pages()
        if not _offers_blog_entry(page)
    ]
    assert not missing, (
        f"public page(s) with no path to the blog: {missing}. #3950 — the blog is "
        f"live but was unreachable because nothing linked it; add an "
        f'<a href="/blog"> to the page chrome (root-relative on the tortoise host, '
        f"the absolute tortoise URL on index.html, whose host 301s /blog)."
    )


def test_blog_guard_covers_every_served_indexable_page() -> None:
    """Guard the guard: `_in_scope_pages()` must not silently shrink.

    A narrow derivation would make the test above pass by covering less, so the
    derivation's output is pinned. The pin is deliberately an EQUALITY, not a
    superset: a page entering the public surface also requires touching this pin
    in the same PR, so the addition is a decision someone takes rather than a
    page that quietly changes what "covered" means. Removal is the same edit with
    a stated reason. Adding is not "free" — it is free of *derivation* work, which
    is the point of deriving the set in the first place.
    """
    got = {page.name for page in _in_scope_pages()}
    assert got == set(_IN_SCOPE_AT_3950), (
        f"the blog guard's page set changed: "
        f"missing={sorted(_IN_SCOPE_AT_3950 - got)} added={sorted(got - _IN_SCOPE_AT_3950)}. "
        f"If a page genuinely left the public surface (new noindex, or its route "
        f"now 301s elsewhere), update `_IN_SCOPE_AT_3950` in the same PR and say why."
    )


def test_every_in_scope_page_is_selectable_by_ci() -> None:
    """Close the REVERSE direction of the CI-selection ratchet (#1349/#3332).

    A page held to this guard with no matching `SOURCE_PATTERNS` entry selects no
    surface at all, so a PR touching ONLY that page never runs this file — the
    guard silently stops covering the page it was written for. That is the
    #1349/#3332 silent-drop class, and it bit this guard on arrival: 5 of the 12
    in-scope pages (`security`, `tos`, `license`, `dpa`, `aviso-privacidad`) were
    absent from the `onboarding` entry, so `--changed-files website/tos.html`
    selected `surfaces=[]`.

    `tests/test_ci_selection.py::test_every_source_pattern_is_selectable` checks
    the OPPOSITE direction (entry -> runs) and states in its own docstring that it
    does not check this one, so nothing covered it. This drives the real
    `select()` rather than re-deriving the matcher, so it tracks the selector's
    actual behaviour instead of a copy of it.
    """
    manifest = load_manifest()
    unselectable = [
        f"website/{page.name}"
        for page in sorted(_in_scope_pages())
        if not select([f"website/{page.name}"], "pull_request", manifest)["surfaces"]
    ]
    assert not unselectable, (
        f"page(s) held to the blog guard that select NO test surface: {unselectable}. "
        f"A PR touching only such a page runs nothing, so this guard cannot fail — "
        f"the #1349/#3332 silent-drop class. Add them to "
        f"SOURCE_PATTERNS['onboarding'] in tools/ci_selection.py."
    )


def test_rendered_hrefs_ignores_non_rendered_markup() -> None:
    """Guard the guard: the extractor must not be satisfiable by a non-link.

    `href="/blog"` sitting in a comment, a `<script>` string or a CSS comment is
    not a way in. If the extractor counted those, the link could be "lost" while
    the guard above stayed green.
    """
    assert _rendered_hrefs('<a href="/blog">Blog</a>') == ["/blog"]
    assert _rendered_hrefs('<a class="x" href="/blog">Blog</a>') == ["/blog"]
    # Unquoted href values are valid HTML5 and must count as links, not vanish.
    assert _rendered_hrefs("<a href=/blog>Blog</a>") == ["/blog"]
    assert _rendered_hrefs("<a href = '/blog'>Blog</a>") == ["/blog"]
    assert _rendered_hrefs('<A HREF="/blog">Blog</A>') == ["/blog"]
    assert _rendered_hrefs('<!-- <a href="/blog">Blog</a> -->') == []
    # These MUST carry a real anchor: without one they return [] even with the
    # stripping removed, so they would prove nothing (review finding, #3962).
    assert _rendered_hrefs('<script>const t = \'<a href="/blog">B</a>\';</script>') == []
    assert _rendered_hrefs('<style>/* <a href="/blog">B</a> */</style>') == []
    # An `href=` inside ANOTHER attribute's value is not this tag's href. A regex
    # with an optional quote reported one here (review finding, #3962); the shared
    # parser must not.
    assert _rendered_hrefs('<a title="see href=/blog">x</a>') == []
    assert _rendered_hrefs('<a onclick="location.href=/blog">x</a>') == []
    assert _rendered_hrefs('<a data-href="/blog">x</a>') == []
    # Not a link: a non-anchor element, and a valueless or empty href.
    assert _rendered_hrefs('<link rel="prefetch" href="/blog">') == []
    assert _rendered_hrefs("<a href>x</a>") == []
    assert _rendered_hrefs('<a href="">x</a>') == []
    assert _href_path("/blog") == "/blog"
    assert _href_path("https://tortoise.premiselabs.co/blog") == "/blog"
    assert _href_path("https://tortoise.premiselabs.co/blog/") == "/blog"


def test_product_hero_offers_the_blog() -> None:
    """#3950: the served homepage must offer the blog ABOVE THE FOLD.

    The owner's complaint is "cannot find the blog", so a link at the bottom of a
    630vh scroll narrative is a weak answer on the one page that matters. The
    hero already carries a secondary-link row (`Browse the docs -> . Design FAQ ->`,
    `website/product.html:332`) that is clickable from first paint (`.beat.pe-on`
    on the `i === 0` beat), so the blog goes there — no new CSS, and no change to
    the #1288 login-only top menu.

    Pinned on the container so a redesign cannot quietly demote the affordance
    back to footer-only. `#beat-hero` is a load-bearing id (CSS + the GSAP beat
    list), so it is stable to pin.

    Scope of the claim: the hero is clickable at first paint only while the GSAP
    script loads — `.beat` is `pointer-events: none` and `.beat.pe-on` (added by
    the inline GSAP init for the `i === 0` beat) re-enables it. That dependency is
    pre-existing (the hero CTA has it too); the FOOTER link is the no-JS
    fallback, which is part of why the footer link is kept as well.
    """
    hero = re.search(r'<section id="beat-hero".*?</section>', _read(PRODUCT), re.S)
    assert hero, "website/product.html lost its #beat-hero section"
    # Guard the fixture: the non-greedy match stops at the FIRST `</section>`, so
    # a nested <section> would silently truncate the checked region — and a
    # truncated region could fail (or vacuously pass) for the wrong reason.
    assert "<section" not in hero.group(0)[len("<section"):], (
        "the #beat-hero region now contains a nested <section>, so the "
        "first-`</section>` extraction may stop before the link. Re-scope this "
        "assertion (e.g. to the secondary-link row) rather than trusting a "
        "possibly-truncated region."
    )
    assert "/blog" in {_href_path(h) for h in _rendered_hrefs(hero.group(0))}, (
        "the served homepage no longer offers the blog above the fold. #3950: the "
        "owner's report was 'cannot find the blog or how to reach it' — a footer "
        "link at the end of the scroll narrative is not a sufficient answer on the "
        "landing page. Keep the hero's secondary-link row entry."
    )
