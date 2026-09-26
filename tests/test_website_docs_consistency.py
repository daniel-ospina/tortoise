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
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # tools/ is not an installed package
    sys.path.insert(0, str(REPO_ROOT))

from tools.ci_selection import load_manifest, select  # noqa: E402, I001
from tests._html_links import (  # noqa: E402
    _AnchorHrefParser,
    blog_entry_hrefs,
    extract_anchor_hrefs,
    is_blog_entry,
)

WEBSITE = REPO_ROOT / "website"
FUNCTIONS = WEBSITE / "functions"
# #4054: the repo now ships TWO Pages Functions trees. The BFF (auth, session,
# api/v1) moved to the app project, so `/welcome` and `/auth/*` are served from
# `website/apps/dashboard/functions/`. #4171 moved the blog admin gate there too
# (`/admin`), so only `/blog` stays in `website/functions/`. A resolver that only
# knew one tree would read a correct link as dangling.
FUNCTION_ROOTS = (
    FUNCTIONS,
    WEBSITE / "apps" / "dashboard" / "functions",
)
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
    if any(p.startswith("_") for p in parts):
        # Cloudflare reserves `_`-prefixed files (`_middleware`, `_routes.json`, and
        # by convention `_lib`/`_shared` helpers): they exist on disk but are never
        # served. Without this, a link to `/_middleware` resolved as a real page and
        # satisfied the dangling-link guard it should have failed (review finding,
        # #3962).
        return False
    targets = []
    for root in FUNCTION_ROOTS:
        targets.append(root.joinpath(*parts))
        for depth in range(len(parts), 0, -1):
            targets.append(root.joinpath(*parts[:depth], "index"))
            targets.append(root.joinpath(*parts[:depth], "[[path]]"))
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
    for tag in re.finditer(r"<meta\s[^>]*>", html, re.I):
        raw = tag.group(0)
        # Any attribute order or quoting: `<meta content="noindex" name="robots">` is
        # as valid as the name-first form, and missing it silently left a noindexed
        # page inside the guard's scope (review finding, #3962).
        if "noindex" in raw.lower() and re.search(
            r"""name\s*=\s*["']?robots["']?""", raw, re.I
        ):
            return True
    return False


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
    """Path-only normalisation, for the hero REGION check below.

    Not the blog predicate — that is `is_blog_entry` in the shared module, so the
    two layers cannot answer "is this a way in" differently.
    """
    return (urlparse(href).path or "/").rstrip("/") or "/"


def _offers_blog_entry(page: Path) -> bool:
    """True when the page has a real anchor that gets a visitor INTO the blog.

    `/blog` (the index) and `/blog/<slug>` (an article — its own nav links back
    to the index) both count as a way in, so a page is not faulted for linking a
    post instead of the index. What does NOT count: a URL sitting in a comment,
    a `<script>` or a `<style>` (`_rendered_hrefs` strips those first) — and an
    absolute href naming a host the site does not own, which would render a
    DNS error rather than the blog. Both rules live in `is_blog_entry`, shared
    with the E2E layer.

    Presence, not visibility — an anchor hidden with `display:none` would still
    satisfy this. The placement that actually matters is pinned separately by
    `test_product_hero_offers_the_blog`.
    """
    return bool(blog_entry_hrefs(_read(page)))


# The served+indexable page set as of #3950 (2026-09-17). This pins the
# DERIVATION'S OUTPUT (not an allowlist the guard consults): if a page later
# leaves the public surface, that is a decision someone must take deliberately,
# not a page that quietly disappears from coverage.
_IN_SCOPE_AT_3950 = frozenset({
    "aviso-privacidad.html", "contact.html", "docs.html", "dpa.html", "faq.html", "index.html",
    "license.html", "privacy.html", "product.html", "security.html",
    "self-hosted.html", "tos.html",
    # ⚠️ `signup.html` LEFT this set in #4054: the page (the `/auth` screen)
    # moved to the app project at `website/apps/dashboard/public/signup.html`,
    # so it is no longer a page `website/` serves and no longer this guard's
    # subject. Its route now lives at https://app.premiselabs.co/auth.
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


# Constructs that would make the extractor's DECLARED limits live (the "Not
# modelled, deliberately" note in `tests/_html_links.py`, tracked in #3970): tag
# names, checked against the tags the parser actually saw INSIDE foreign content.
#
# Asking the parser rather than re-scanning the raw text is deliberate. A regex
# over the source has to re-implement the parsing it is checking, and it was wrong
# in both directions: it matched on a trailing space, so a bare `<mi>` — item 4 of
# #3970's table — slipped through, and its `.*?</svg>` region ended at a `</svg>`
# written inside a COMMENT, hiding a live integration point after it (review
# finding, #3962).
_DECLARED_FOREIGN_RISK = frozenset({
    "foreignobject", "desc", "title", "template", "annotation-xml",
    "mi", "mo", "mn", "ms", "mtext",
})


def test_no_in_scope_page_makes_the_extractors_declared_limits_live() -> None:
    """Keep the extractor's declared limits honest (#3962 review, #3970).

    `_AnchorHrefParser` deliberately does not model the HTML integration points
    (`<svg><foreignObject>`, `<svg><desc>`, `<svg><title>`, MathML's text
    integration points) or cross-namespace end-tag resolution: closing them means
    reimplementing the HTML tree-construction algorithm, and #3970 carries the
    reproductions. That declaration is only safe while no COVERED page reaches
    those constructs.

    Nothing else would notice a page growing one. The guard would go on reporting
    the link — in the false-pass direction, since an unmodelled integration point
    makes a `<template>` render here that a browser leaves inert — and the reason
    the limit was acceptable would be quietly false. Three of these pages already
    carry `<svg>` (index, product, signup) as `<path>`-only icons, so the
    distance is one edit, not a hypothetical.

    The same scan catches the constructs the extractor DETECTS and refuses to read
    (`<frameset>`, the script-data escaped state): a wedge on a covered page is
    not an acceptable state either — it means the guard would report the page as
    offering no way in — so it fails here, where the fix is to look at the page.

    This is a floor, not a proof: it fails closed on the constructs that make the
    limit live, so widening the extractor or the page chrome is a decision taken
    with #3970 in hand rather than a silent change of meaning.
    """
    offenders: list[str] = []
    for page in sorted(_in_scope_pages()):
        parser = _AnchorHrefParser()
        parser.feed(_read(page))
        parser.close()
        risky = sorted({tag for tag in parser.foreign_tags if tag in _DECLARED_FOREIGN_RISK})
        if risky:
            offenders.append(f"website/{page.name} reaches {risky} in foreign content")
        if parser.foreign_root_mismatch:
            # The precise cross-namespace trigger (item 5 of #3970): a foreign-ROOT end
            # tag that matches no open root, e.g. `</math>` under `<svg>`. A browser
            # ignores it and stays foreign; a bare counter left foreign content, so
            # this is where the model is known to be wrong in a way that matters.
            offenders.append(
                f"website/{page.name} has a foreign-root end tag matching no open root "
                f"{sorted(set(parser.foreign_root_mismatch))}"
            )
        if parser.foreign_unmatched_endtags:
            # Wider RATCHET, kept deliberately: any other end tag inside foreign
            # content. Those are handled correctly today, but the covered pages use
            # only self-closing SVG children, so a page that starts closing SVG
            # elements by name is one edit from the case above (review finding,
            # #3962). The message says which of the two fired.
            offenders.append(
                f"website/{page.name} closes element(s) inside foreign content "
                f"{sorted(set(parser.foreign_unmatched_endtags))}"
            )
        if parser.wedged_reason:
            offenders.append(
                f"website/{page.name} stops being readable ({parser.wedged_reason})"
            )
    assert not offenders, (
        f"in-scope page(s) now reach a construct the extractor does not model: "
        f"{offenders}. `tests/_html_links.py` declares those limits out of scope "
        f"because no covered page used them; that is no longer true. Either model "
        f"the construct (see #3970) or change the page, and update the declaration "
        f"and this test in the same PR."
    )


def _guard_reachable_for(changed: str) -> bool:
    """True when a PR touching `changed` alone would execute THIS module.

    The predicate is the guard's own reachability, not "some surface ran": a path
    that selects a different surface keeps `surfaces` non-empty while this file
    never runs. `select(...)["test_files"]` is the STRING `"ALL"` on the
    full-selection path, so a bare membership test against it is a substring test —
    and `not in "ALL"` is always True, which would report a false failure exactly
    when the full matrix (which does run this file) was selected. Branch on the
    sentinel (review finding, #3962).
    """
    sel = select([changed], "pull_request", load_manifest())
    return bool(sel["full"]) or "test_website_docs_consistency.py" in sel["test_files"]


def test_every_in_scope_page_is_selectable_by_ci() -> None:
    """Close the REVERSE direction of the CI-selection ratchet (#1349/#3332).

    A page held to this guard with no matching `SOURCE_PATTERNS` entry selects no
    surface at all, so a PR touching ONLY that page never runs this file — the
    guard silently stops covering the page it was written for. That is the
    #1349/#3332 silent-drop class, and it bit this guard on arrival: 5 of the 12
    in-scope pages (`security`, `tos`, `license`, `dpa`, `aviso-privacidad`) were
    absent from the `onboarding` entry, so `--changed-files website/tos.html`
    selected `surfaces=[]`.

    The predicate is the GUARD'S OWN reachability, not "some surface ran": a page
    that selects a different surface keeps `surfaces` non-empty while this file
    never runs, so asserting on `surfaces` alone would report the silent drop as
    covered (review finding, #3962). The assertion is therefore on `test_files`,
    which is the set that actually gets executed.

    `tests/test_ci_selection.py::test_every_source_pattern_is_selectable` checks
    the OPPOSITE direction (entry -> runs) and states in its own docstring that it
    does not check this one, so nothing covered it. This drives the real
    `select()` rather than re-deriving the matcher, so it tracks the selector's
    actual behaviour instead of a copy of it.
    """
    unselectable = [
        f"website/{page.name}"
        for page in sorted(_in_scope_pages())
        if not _guard_reachable_for(f"website/{page.name}")
    ]
    assert not unselectable, (
        f"page(s) held to the blog guard whose PR does not run this file: "
        f"{unselectable}. A PR touching only such a page never executes "
        f"test_website_docs_consistency.py, so the guard cannot fail on the page "
        f"it was written for — the #1349/#3332 silent-drop class. Add them to "
        f"SOURCE_PATTERNS['onboarding'] in tools/ci_selection.py."
    )


def test_every_guard_input_is_selectable_by_ci() -> None:
    """Close the same REVERSE ratchet one level up: the guard's OWN inputs.

    `test_every_in_scope_page_is_selectable_by_ci` covers the pages in the derived
    scope, but three inputs change that scope or the rule WITHOUT being a page: the
    shared extractor `tests/_html_links.py` (the rule itself), `website/_redirects`
    (which pages `_canonical_redirect_targets` drops from scope), and
    `website/functions/blog/[[path]].ts` (what `_function_serves` resolves the blog
    link against). A PR editing only one of those selects no surface, so this file
    never runs and the guard silently stops covering what it was written for —
    verified before listing: `select(["website/_redirects"], ...)` returned
    `surfaces=[]` with this file absent from `test_files`.

    `tests/test_ci_selection.py::test_every_source_pattern_is_selectable` cannot
    catch this direction: it is derived FROM `SOURCE_PATTERNS`, so deleting an entry
    deletes its own only case (review finding, #3962). These paths are therefore
    pinned explicitly here.
    """
    unselectable = [
        path
        for path in (
            "tests/_html_links.py",
            "website/_redirects",
            "website/functions/blog/[[path]].ts",
        )
        if not _guard_reachable_for(path)
    ]
    assert not unselectable, (
        f"guard input(s) whose PR does not run this file: {unselectable}. A PR "
        f"touching only such an input never executes test_website_docs_consistency.py, "
        f"so the guard silently stops covering the pages it was written for — the "
        f"#1349/#3332 silent-drop class. Add them to SOURCE_PATTERNS['onboarding'] "
        f"in tools/ci_selection.py."
    )


def test_guard_reachable_helper_handles_the_full_selection_sentinel() -> None:
    """Pin BOTH branches of `_guard_reachable_for`, including the unhit one.

    Every path the two ratchets above pass resolves to `full=False`, so the
    `full`-sentinel branch was UNPINNED: the pre-fix expression
    (`"test_website_docs_consistency.py" not in "ALL"` -> True) still passed every
    test in this file and in `test_ci_selection.py`, so the bug could have been
    reintroduced silently (review finding, #3962). Both directions are asserted
    here directly, which is the only place the sentinel is exercised.
    """
    # Full selection: `test_files` is the STRING "ALL", so a bare membership test
    # reads as `not in "ALL"` -> True and would call a covered path unselectable.
    assert _guard_reachable_for(".github/workflows/python-ci.yml") is True
    # A path that selects no surface at all: genuinely not covered by this module.
    assert _guard_reachable_for("website/blog/index.html") is False


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
    # An `href=` that is part of ANOTHER attribute is not this tag's href —
    # neither in another attribute's VALUE (`title="see href=/blog"`,
    # `onclick="location.href=/blog"`) nor the tail of another attribute's NAME
    # (`data-href`). A regex with an optional quote reported all three (review
    # finding, #3962); the shared parser must not.
    assert _rendered_hrefs('<a title="see href=/blog">x</a>') == []
    assert _rendered_hrefs('<a onclick="location.href=/blog">x</a>') == []
    assert _rendered_hrefs('<a data-href="/blog">x</a>') == []
    # Duplicate attributes: the tokenizer keeps the FIRST, so the second must not
    # be reported as a link the browser does not have (review finding, #3962).
    assert _rendered_hrefs('<a href="#" href="/blog">x</a>') == ["#"]
    assert _rendered_hrefs('<a href="" href="/blog">x</a>') == []
    # Inert and raw-text containers are parsed but contribute no link.
    assert _rendered_hrefs('<template><a href="/blog">x</a></template>') == []
    assert _rendered_hrefs('<noscript><a href="/blog">x</a></noscript>') == []
    assert _rendered_hrefs('<textarea><a href="/blog">x</a></textarea>') == []
    # A self-closing NON-void tag OPENS in a browser (the `/` is ignored), so the
    # anchor after it is swallowed, not rendered (review finding, #3962).
    assert _rendered_hrefs('<template/><a href="/blog">x</a>') == []
    assert _rendered_hrefs('<script/><a href="/blog">x</a>') == []
    # ...including the rest of HTML's raw-text family, whose self-closing spelling
    # reaches `handle_startendtag`, where the stdlib does NOT switch to CDATA mode
    # — so these reached the extractor as markup and reported a link the browser
    # reads as text (review finding, #3962, Chromium-verified).
    assert _rendered_hrefs('<xmp/><a href="/blog">x</a>') == []
    assert _rendered_hrefs('<iframe/><a href="/blog">x</a>') == []
    assert _rendered_hrefs('<noembed/><a href="/blog">x</a>') == []
    assert _rendered_hrefs('<noframes/><a href="/blog">x</a>') == []
    assert _rendered_hrefs('<plaintext/><a href="/blog">x</a>') == []
    # ...and such an element must ENTER raw-text tokenising on the self-closing
    # path, because the stdlib does it only from `parse_starttag`. Without that, a
    # nested raw-text start tag pushes a SECOND `_suppress` entry the single end
    # tag cannot unwind: suppression sticks and every later anchor on the page is
    # dropped — a false NEGATIVE in the direction that hides a real link
    # (review finding, #3962, Chromium-verified).
    assert _rendered_hrefs('<xmp/><xmp></xmp><a href="/blog">x</a>') == ["/blog"]
    assert _rendered_hrefs('<title/><title></title><a href="/blog">x</a>') == ["/blog"]
    assert _rendered_hrefs('<textarea/><textarea></textarea><a href="/blog">x</a>') == ["/blog"]
    assert _rendered_hrefs('<iframe/><iframe></iframe><a href="/blog">x</a>') == ["/blog"]
    # `<noscript>` is raw text only with scripting ENABLED — the browser default,
    # and the only mode this guard's question is asked in — so a nested raw-text
    # tag inside it is text, not a second suppression frame.
    assert _rendered_hrefs('<noscript><noembed></noscript><a href="/blog">x</a>') == ["/blog"]
    # Constructs that would otherwise fail OPEN are treated as UNREADABLE instead,
    # because `HTMLParser`'s CDATA mode cannot express them: the script-data escaped
    # states (spec 13.2.5), where `</script` no longer closes the element, and
    # `<frameset>`, whose insertion mode ignores every other start tag. In both the
    # browser puts nothing in the tree, so counting their anchors is a false pass —
    # the direction this guard exists to prevent (review finding, #3962, both
    # Chromium-verified). Stops collecting rather than guessing: a page carrying one
    # fails the guard loudly instead of passing silently.
    assert _rendered_hrefs('<script><!--<script></script><a href="/blog">x</a></script>') == []
    assert _rendered_hrefs('<frameset><a href="/blog">x</a></frameset>') == []
    assert _rendered_hrefs('<frameset><frame><a href="/blog">x</a>') == []
    # ...but the token is IGNORED where the browser never enters frameset mode, so
    # the net must not fire there or a page that DOES offer the blog is reported as
    # offering nothing (a false FAIL; review finding, Chromium-verified). A `<body>`
    # sets the spec's frameset-ok flag, and a `<body>` inside an inert `<template>` is
    # template content rather than the document body (review finding,
    # Chromium-verified).
    assert _rendered_hrefs('<body><frameset><a href="/blog">x</a></frameset>') == ["/blog"]
    assert _rendered_hrefs('<template><frameset></template><a href="/blog">x</a>') == ["/blog"]
    assert _rendered_hrefs('<template><body></template><frameset><a href="/blog">x</a></frameset>') == []
    # An honored `<frameset>` REPLACES the body, so anchors collected BEFORE it are
    # gone too — keeping them reports a link no visitor can click.
    assert _rendered_hrefs('<a href="/blog"/><frameset>') == []
    # The spec's frameset-ok flag is NOT emulated: it decides whether the token above
    # is honored, and the tokens that clear it (a non-whitespace character,
    # `<br>`/`<img>`/`<button>`/`<li>`/…, a non-`hidden` `<input>`, `<template>`, …)
    # are not tracked. This extractor wedges whenever the token appears with no body
    # and no open suppression frame, so after any of those it reports no link where a
    # browser keeps one — a false FAIL, the loud direction, on an obsolete element
    # that no covered page uses. #3970 records it; emulating the flag cost a
    # regression per review round for corners that cannot reach this guard.
    assert _rendered_hrefs('<a href="/blog">x</a><table><frameset>') == []
    assert _rendered_hrefs('<a href="/blog"/><input type="hidden"><frameset>') == []
    # A foreign-ROOT end tag that matches no open root is ignored by a browser, which
    # therefore STAYS in foreign content — `</math>` under `<svg>`. The bare-counter
    # model left foreign content there and then treated the `<template>` as the inert
    # HTML element, reporting no way in for a page that has one (review finding,
    # #3962, Chromium-verified).
    assert _rendered_hrefs('<svg></math><template><a href="/blog">x</a>') == ["/blog"]
    assert _rendered_hrefs('<math></svg><template><a href="/blog">x</a>') == ["/blog"]
    # `<![CDATA[` is a CDATA section only in FOREIGN content; in HTML content a
    # browser makes it a BOGUS COMMENT ending at the first `>`. `HTMLParser` reads
    # it as CDATA everywhere, so with no `]]>` the rest of the document was
    # swallowed and real links were missed (review finding, #3962,
    # Chromium-verified).
    assert _rendered_hrefs('<div><![CDATA[</div><a href="/blog">x</a>') == ["/blog"]
    assert _rendered_hrefs('<svg><![CDATA[<a href="/blog">x</a>]]></svg>') == []
    # The abrupt close of an EMPTY comment (13.2.5.43): `<!-->` and `<!--->` end the
    # comment at once, but the stdlib looks for a later `-->` first and only falls
    # back to the abrupt close when there is none — so an element opened after the
    # abrupt close went unregistered here and its suppression was lost, counting a
    # link no browser renders (review finding, #3962, Chromium-verified).
    assert _rendered_hrefs('<!--><a href="/blog">x</a>-->') == ["/blog"]
    assert _rendered_hrefs('<!---><a href="/blog">x</a>-->') == ["/blog"]
    assert _rendered_hrefs('<!--><template>--><a href="/blog">x</a>') == []
    assert _rendered_hrefs('<!---><template>--><a href="/blog">x</a>') == []
    assert _rendered_hrefs('<!--><script>--><a href="/blog">x</a>') == []
    assert _rendered_hrefs('<!-- ordinary --><a href="/blog">x</a>') == ["/blog"]
    # ...and the ordinary `document.write('<script …>')` idiom has no `<!--`, so it
    # is NOT caught by that net — the trigger needs both markers.
    assert _rendered_hrefs(
        '<script>document.write(\'<script src=x></script>\');</script><a href="/blog">x</a>'
    ) == ["/blog"]
    # ...but only in the HTML namespace: inside foreign content `<template>`
    # renders normally, so its anchor IS a way in (regression guard, #3962).
    assert _rendered_hrefs('<svg><template><a href="/blog">x</a></template></svg>') == ["/blog"]
    # A self-closing FOREIGN root is honored (empty element, closes immediately),
    # so it must not leave foreign depth behind and disable `<template>`
    # suppression for the rest of the document — a false pass (review finding).
    assert _rendered_hrefs('<svg/><template><a href="/blog">x</a></template>') == []
    assert _rendered_hrefs('<math/><template><a href="/blog">x</a></template>') == []
    # Suppression must unwind correctly for nested opens.
    assert _rendered_hrefs('<template><script></script><a href="/blog">x</a></template><a href="/docs">d</a>') == ["/docs"]
    # Not a link: a non-anchor element, and a valueless or empty href.
    assert _rendered_hrefs('<link rel="prefetch" href="/blog">') == []
    assert _rendered_hrefs("<a href>x</a>") == []
    assert _rendered_hrefs('<a href="">x</a>') == []
    assert _href_path("/blog") == "/blog"
    assert _href_path("https://tortoise.premiselabs.co/blog") == "/blog"
    assert _href_path("https://tortoise.premiselabs.co/blog/") == "/blog"
    # `<title>` is RCDATA, like `<textarea>` above, so an anchor inside it is text
    # and not a link. On the supported interpreter the stdlib already covers both,
    # so these two outcome pins cannot detect the `_RAW` entries being removed —
    # the RULE is pinned directly at the end of this test (review finding).
    assert _rendered_hrefs('<title><a href="/blog">x</a></title>') == []
    # Foreign-content BREAKOUT (spec 13.2.6.5): a breakout start tag ends the
    # foreign context, where `<template>` is inert again. Without the rule the
    # parser stays "foreign", `<template>` renders, and a link no browser renders
    # is counted — the false pass this whole guard exists to prevent (review
    # finding, #3962, Chromium-verified).
    assert _rendered_hrefs('<svg><p><template><a href="/blog">x</a></template>') == []
    assert _rendered_hrefs('<svg><br><template><a href="/blog">x</a></template>') == []
    assert _rendered_hrefs('<math><div><template><a href="/blog">x</a></template>') == []
    assert _rendered_hrefs('<svg><font color=red><template><a href="/blog">x</a></template>') == []
    # ...but `<font>` WITHOUT one of its presentational attributes is not a
    # breakout tag, so the foreign context survives it and the template renders.
    assert _rendered_hrefs('<svg><font><template><a href="/blog">x</a></template>') == ["/blog"]
    # `</br>`/`</p>` pop foreign content too (13.2.6.5); any OTHER unmatched end
    # tag leaves it alone. Popping on every unmatched end tag inverted this.
    assert _rendered_hrefs('<svg></p><template><a href="/blog">x</a></template>') == []
    assert _rendered_hrefs('<svg></br><template><a href="/blog">x</a></template>') == []
    assert _rendered_hrefs('<svg></div><template><a href="/blog">x</a></template>') == ["/blog"]
    assert _rendered_hrefs('<svg></span><template><a href="/blog">x</a></template>') == ["/blog"]
    # A self-closing FOREIGN root is honored (empty element, closes immediately),
    # so it must not leave foreign depth behind and disable `<template>`
    # suppression for the rest of the document — a false pass (review finding).
    assert _rendered_hrefs('<svg/><template><a href="/blog">x</a></template>') == []
    assert _rendered_hrefs('<math/><template><a href="/blog">x</a></template>') == []
    # A NON-breakout element keeps foreign content foreign, so `<template>` there
    # still renders — the rule is the breakout list, not "any tag ends foreign".
    assert _rendered_hrefs('<svg><g><template><a href="/blog">x</a></template></svg>') == ["/blog"]
    assert _rendered_hrefs('<svg><template></template><template><a href="/blog">x</a></template>') == ["/blog"]
    # A raw-text ELEMENT in HTML is not a raw-text element in FOREIGN content:
    # `<script>` inside `<svg>` is a foreign element whose content is markup again,
    # so an anchor inside it IS a real link. HTMLParser's CDATA switch is an
    # HTML-rules behaviour, so it must not run there (review finding, #3962,
    # Chromium-verified).
    assert _rendered_hrefs('<svg><script><a href="/blog">x</a></script></svg>') == ["/blog"]
    assert _rendered_hrefs('<svg><style><a href="/blog">x</a></style></svg>') == ["/blog"]
    assert _rendered_hrefs('<svg><xmp><a href="/blog">x</a></xmp></svg>') == ["/blog"]
    assert _rendered_hrefs('<svg><title><a href="/blog">x</a></title></svg>') == ["/blog"]
    assert _rendered_hrefs('<svg><iframe><a href="/blog">x</a></iframe></svg>') == ["/blog"]
    assert _rendered_hrefs('<script><a href="/blog">x</a></script>') == []
    # Suppression must unwind correctly for nested opens.
    assert _rendered_hrefs('<template><script></script><a href="/blog">x</a></template><a href="/docs">d</a>') == ["/docs"]
    # A suppressed container stays CLOSABLE once ordinary or void elements sit
    # above it: the end tag that NAMES it unwinds it (Chromium-verified).
    assert _rendered_hrefs('<template><div></template><a href="/blog">x</a>') == ["/blog"]
    assert _rendered_hrefs('<template><br></template><a href="/blog">x</a>') == ["/blog"]
    assert _rendered_hrefs('<template><p></template><a href="/blog">x</a>') == ["/blog"]
    assert _rendered_hrefs('<noscript><img></noscript><a href="/blog">x</a>') == ["/blog"]
    # ...and an end tag that does NOT name the open container closes nothing, so
    # the anchor after it is still inside the template and is not a link — the
    # mirror of the three cases above (Chromium-verified).
    assert _rendered_hrefs('<div><template></div><a href="/blog">x</a>') == []
    # DELIBERATELY NOT pinned as browser-equal, and tracked in #3970: the HTML
    # INTEGRATION POINTS (`<svg><foreignObject>`, `<svg><desc>`, MathML's text
    # integration points) and cross-namespace end-tag resolution. A `<template>`
    # inside an integration point is inert in a browser but renders here, and
    # `<template><svg><template></template>` closes the outer template rather than
    # the inner one. Both require the full HTML tree-construction algorithm, and no
    # page this guard covers contains foreign content at all — the guard's
    # adversary is a link LOST from a hand-edited marketing page, not an author
    # hiding one inside SVG. See the class docstring's "Not modelled" note.
    #
    # What counts as a way INTO the blog, asked through the ONE rule both layers
    # share. A root-relative href is accepted as written; an absolute one must
    # name a host the site owns, and a path that resolves out of `/blog` is not a
    # way in (review finding, #3962).
    assert is_blog_entry("/blog")
    assert is_blog_entry("/blog/")
    assert is_blog_entry("/blog/some-post")
    assert is_blog_entry("https://premiselabs.co/blog")
    assert is_blog_entry("https://tortoise.premiselabs.co/blog")
    assert is_blog_entry("https://tortoise.premiselabs.co/blog/some-post")
    assert is_blog_entry("/blog//../docs")  # resolves to /blog/docs, still a way in
    assert not is_blog_entry("https://tortoise.premiselab.co/blog")
    assert not is_blog_entry("https://example.com/blog")
    assert not is_blog_entry("//evil.example/blog")
    assert not is_blog_entry("/docs")
    assert not is_blog_entry("blog/x")  # document-relative, not root-relative
    # Resolved, not raw-prefix: these only LOOK like the blog.
    assert not is_blog_entry("/blog/../docs")
    assert not is_blog_entry("/blog/%2e%2e/docs")
    assert not is_blog_entry("/blog/%2E%2E/docs")
    # A different origin is not the site's blog.
    assert not is_blog_entry("https://tortoise.premiselabs.co:8443/blog")
    assert not is_blog_entry("https://tortoise.premiselabs.co:80/blog")
    assert not is_blog_entry("http://tortoise.premiselabs.co:443/blog")
    # A scheme a browser cannot follow to the blog is not a way in.
    assert not is_blog_entry("mailto:/blog")
    assert not is_blog_entry("javascript:/blog")
    assert not is_blog_entry("data:/blog")
    assert not is_blog_entry("file:///blog")
    assert not is_blog_entry("ftp://premiselabs.co/blog")
    # No authority at all: a browser resolves the host to `blog`.
    assert not is_blog_entry("///blog")
    assert not is_blog_entry("https:///blog")
    # `urlsplit` does not split on a backslash, a browser does (review finding).
    assert not is_blog_entry("https://evil.example\\@tortoise.premiselabs.co/blog")
    # Malformed hrefs are not a way in, and must not raise while the guard runs.
    assert not is_blog_entry("http://[::1/blog")
    # The `_RAW` RULE itself, because the pinned OUTCOMES above cannot all see it:
    # on the pinned interpreter the stdlib already handles `<textarea>`/`<title>`,
    # and it handles the rest except through `handle_startendtag` (review finding,
    # #3962).
    assert {
        "textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext",
    } <= _AnchorHrefParser._RAW


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
    assert blog_entry_hrefs(hero.group(0)), (
        "the served homepage no longer offers the blog above the fold. #3950: the "
        "owner's report was 'cannot find the blog or how to reach it' — a footer "
        "link at the end of the scroll narrative is not a sufficient answer on the "
        "landing page. Keep the hero's secondary-link row entry."
    )


# ── #3436: duplicate element ids across the public site ─────────────────────
#
# A duplicate `id` is invalid HTML and makes every lookup ambiguous:
# `getElementById` returns only the FIRST match (so a script silently binds the
# wrong element), an in-page anchor lands on an arbitrary one, and
# `aria-labelledby`/`aria-controls` lose their target. #3436 reported
# `id="beta-gate"` twice in `website/signup.html`.
#
# The real markup never had two such elements. A raw-text scan counted a
# REMOVAL NOTE that quoted `<section id="beta-gate">` inside an HTML comment
# (verified across all 55 revisions of that file: 0 revisions ever carried two
# real `id="beta-gate"` elements). One comment occurrence pinned the whole
# class and `website/*.html` was the class's only real home, so the guard below
# is parser-based rather than regex-based, and the comment case is pinned in
# `test_id_uniqueness_guard_fails_on_a_deliberately_duplicated_id` — a regex
# guard reds on any page that documents its own ids.


class _IdCollector(HTMLParser):
    """The `id` of every element the stdlib tokenizer reports as a start tag.

    `handle_starttag` is the right hook: the tokenizer emits no start tag inside
    a comment — which is exactly the #3436 false positive, a raw-text scan that
    counted the removal note quoting `<section id="beta-gate">` as a second
    element.

    Only the FIRST `id` attribute on a tag is read — the stdlib hands over every
    one of a duplicate pair, while a browser keeps the first — and an empty value
    is treated as no id at all, because `getElementById("")` matches nothing and
    two elements with `id=""` are not a collision a script can observe.

    TWO KNOWN DIVERGENCES from a browser, in opposite directions. Both are
    inherited from the stdlib tokenizer rather than modelled here, and neither
    is an exhaustive list — this is a tokenizer's view of a document, not a
    browser's:

      * OVER-count: an `id` a browser does not expose as a document element.
        `<template>` content is the pinned case — a browser keeps it in an inert
        fragment `getElementById` never reaches, while the stdlib parses it as
        ordinary markup. This direction is fail-loud, the one a duplicate-id
        guard must err in: a false positive is a red test a human reads, while a
        false negative ships the defect.
      * UNDER-count, and SILENT: a construct where the stdlib stops emitting
        start tags and a browser does not. `<svg><style>…</style></svg>` is the
        case that has bitten: the stdlib switches to raw text by tag name, with
        no foreign-content awareness. `tests/_html_links.py` documents the same
        class of tokenizer divergence for the link extractor and #3970 tracks
        its limits; #4118 lists the ones found for this collector so far and
        would remove them by extracting ids through that render-fidelity seam
        instead of maintaining a second parser.

    `test_no_covered_page_makes_the_id_collectors_declared_limit_live` is the
    mitigation for the silent direction — the module's idiom for a limit that
    cannot yet be modelled — and it covers the `<svg>`-raw-text case only.
    """

    @property
    def _support_cdata(self) -> bool:
        """False: a CDATA section is CDATA only INSIDE foreign content.

        `HTMLParser` ships this as unconditional ``True``, and honouring
        `<![CDATA[ … ]]>` in HTML content makes the tokenizer swallow everything
        up to `]]>` — where a browser ends a BOGUS COMMENT at the first `>`
        instead, so the rest of the document is real markup. That is a SILENT
        miss, the direction that must not happen.

        This collector does not model foreign content, so it has to pick ONE
        value: `False` errs LOUD there (it counts markup a browser inside
        `<svg>`/`<math>` does not expose) and matches a browser everywhere else.
        `tests/_html_links.py` derives the value from its foreign-context depth,
        which this class does not track.
        """
        return False

    @_support_cdata.setter
    def _support_cdata(self, flag: bool) -> None:
        # The stdlib assigns this during `reset()`; the derived value above is the
        # authority, so the write is accepted and ignored.
        del flag


    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id":
                # FIRST id wins, empty or not — the stdlib hands over both
                # attributes of a duplicate pair and a browser keeps the first,
                # so `id="" id="x"` leaves the element with no id.
                if value:
                    self.ids.append((value, self.getpos()[0]))
                return


def _duplicate_element_ids(html: str) -> dict[str, list[int]]:
    """{id: [line, …]} for every id declared on more than one element."""
    collector = _IdCollector()
    collector.feed(html)
    lines: dict[str, list[int]] = {}
    for value, line in collector.ids:
        lines.setdefault(value, []).append(line)
    return {value: at for value, at in lines.items() if len(at) > 1}


def _all_website_pages() -> list[Path]:
    """Every checked-in top-level page — DERIVED from disk, never listed.

    Wider than `_in_scope_pages()` on purpose. That scope (public + indexable +
    served) is the blog guard's question — where a link can be followed — but an
    id collision is a property of the DOCUMENT: `404.html` is served on every
    miss and `invite-accept.html` on every invite link, and a `noindex` page is
    still a page whose own script runs. Derivation is what makes this a guard
    rather than a list that rots; `test_id_guard_covers_every_website_page` pins
    the result so it cannot silently SHRINK either.

    Scope is the top level plus the app project's AUTHORED page root; a page
    added with another extension (`.htm`) or nested (`website/legal/x.html`) is
    NOT covered — an open gap tracked in #4111. `website/apps/blog-admin/dist/`
    stays out: that is committed build output, regenerated rather than authored.
    `website/apps/dashboard/public/` is IN — #4054 moved the auth pages there
    (`welcome.html`, `signup.html`, `invite-accept.html`), and they are authored
    and served exactly as before, so the id collision they could carry is the
    same property as any other page's. Leaving them out would have shrunk this
    guard's coverage as a silent side effect of the move.
    """
    return sorted([
        *WEBSITE.glob("*.html"),
        *(WEBSITE / "apps" / "dashboard" / "public").glob("*.html"),
    ])


def _assert_no_duplicate_ids(page_name: str, html: str) -> None:
    """The guard's assertion, shared with the test that exercises IT.

    `test_id_uniqueness_guard_fails_on_a_deliberately_duplicated_id` calls this
    rather than a copy of its body, because no page in the corpus carries a
    duplicate — so the parametrized guard only ever executes the passing path,
    and this is the only test that executes the failing one.
    """
    duplicates = _duplicate_element_ids(html)
    assert not duplicates, (
        f"website/{page_name} declares the same id on more than one element: "
        f"{duplicates}. Duplicate ids are invalid HTML and make "
        f"getElementById, in-page anchors and aria references resolve to an "
        f"arbitrary element. Give one of the elements a distinct id and update "
        f"whatever references it (#3436)."
    )


@pytest.mark.parametrize("page", _all_website_pages(), ids=lambda p: p.name)
def test_website_pages_have_no_duplicate_element_ids(page: Path) -> None:
    """#3436 acceptance: no page declares the same id on two elements."""
    _assert_no_duplicate_ids(page.name, _read(page))


def test_id_uniqueness_guard_fails_on_a_deliberately_duplicated_id() -> None:
    """#3436 acceptance: the guard must red on the defect it names.

    Both directions are pinned, because a guard that cannot fail on a duplicated
    id is the same as no guard, and a guard that reds on the non-element
    occurrences would be the false positive that produced #3436 in the first
    place.
    """
    with pytest.raises(AssertionError, match="more than one element"):
        _assert_no_duplicate_ids(
            "synthetic.html", '<div id="dup"></div><span id="dup"></span>'
        )
    # The guard is SILENT on the #3436 shape: a comment quoting the element is
    # not a second element, so it must not raise here either.
    _assert_no_duplicate_ids(
        "synthetic.html",
        '<!-- <section id="dup"></section> --><section id="dup"></section>',
    )
    _assert_no_duplicate_ids(
        "synthetic.html", '<div id="a"></div><span id="b"></span>'
    )
    # The helper-level cases the guard's silence depends on.
    assert _duplicate_element_ids(
        '<script>const h = \'<div id="dup">\';</script><div id="dup"></div>'
    ) == {}
    assert _duplicate_element_ids(
        '<style>/* <div id="dup"> */</style><div id="dup"></div>'
    ) == {}
    # A repeated ATTRIBUTE is dropped by the tokenizer before a browser sees it.
    assert _duplicate_element_ids('<div id="dup" id="dup"></div>') == {}
    # A CDATA section in HTML content (the `_support_cdata` override, #4118):
    # honouring it would swallow to `]]>`, where a browser ends the comment at
    # the first `>` and parses the rest as markup. Pinned because no real page
    # reaches the construct, so nothing else here would notice a regression.
    assert _duplicate_element_ids(
        '<div id="dup"></div><![CDATA[ > <div id="dup"></div> ]]>'
    ) == {"dup": [1, 1]}
    # An `id` in `<template>` content IS counted: a browser keeps that content
    # inert in a fragment `getElementById` never reaches, while the stdlib parses
    # it as markup. That is the fail-loud direction, pinned so changing it is
    # deliberate; `<template>` is pinned because it is unaffected by the stdlib's
    # raw-text set, which varies by interpreter.
    assert _duplicate_element_ids(
        '<template><div id="dup"></div></template><div id="dup"></div>'
    ) == {"dup": [1, 1]}


# Foreign-content roots: a browser does not switch to raw text inside these, so
# the stdlib's tag-name-only switch diverges there (see `_IdCollector`).
_FOREIGN_ROOTS = frozenset({"svg", "math"})


class _ForeignRawtextProbe(_IdCollector):
    """Raw-text switches the tokenizer makes while foreign content is open.

    `set_cdata_mode` IS the suppression event: it is the tokenizer's own call,
    made for every tag whose content it then refuses to parse as markup. Hooking
    it, rather than listing tag names, is what makes this probe complete — an
    earlier revision checked `HTMLParser.CDATA_CONTENT_ELEMENTS` and was blind to
    `title`, `textarea` and `plaintext`, which the stdlib suppresses through
    `RCDATA_CONTENT_ELEMENTS` and its own plaintext rule (review finding). A tag
    the collector suppresses cannot be missed here, because both read the same
    call.

    A nesting-insensitive counter is enough for a PRE-CONDITION check: it can
    over-report (a 13.2.6.5 breakout leaves the browser in HTML content while
    this counter still counts the root as open), and over-reporting only asks
    for a hand check.

    It covers the `<svg>`-raw-text case and no more — the tokenizer has other
    browser divergences (`_IdCollector` says so, and #4118 lists them), so a
    green run here is evidence about this one path, not a clean bill of health.
    """

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.hits: list[tuple[str, int]] = []

    def set_cdata_mode(self, elem: str, *args: object, **kwargs: object) -> None:
        if self.depth:
            self.hits.append((elem, self.getpos()[0]))
        super().set_cdata_mode(elem, *args, **kwargs)  # type: ignore[arg-type]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _FOREIGN_ROOTS:
            self.depth += 1
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _FOREIGN_ROOTS and self.depth:
            self.depth -= 1


@pytest.mark.parametrize("page", _all_website_pages(), ids=lambda p: p.name)
def test_no_covered_page_makes_the_id_collectors_declared_limit_live(
    page: Path,
) -> None:
    """Guard the guard: the collector's declared SILENT miss must stay unreached.

    `HTMLParser` switches to raw text by tag name with no foreign-content
    awareness, while a browser inside `<svg>`/`<math>` does not — so markup
    inside `<svg><style>…</style></svg>` is a real element to the browser and no
    element to the collector. Unlike the over-count `_IdCollector` documents,
    this one is a MISS, and it is only safe while no covered page reaches it:
    the parametrized assertion fails the moment one does. This is ONE divergence,
    not all of them (#4118).
    """
    probe = _ForeignRawtextProbe()
    probe.feed(_read(page))
    assert not probe.hits, (
        f"website/{page.name} opens {probe.hits} inside `<svg>`/`<math>`. The id "
        f"collector cannot see markup inside such an element while a browser "
        f"does, so a duplicate id there would be MISSED (silently). Check this "
        f"page by hand, and fix the collector (#4118) rather than relaxing this "
        f"assertion."
    )


def test_the_declared_limit_probe_fires_on_the_construct_it_names() -> None:
    """Guard the guard: pin the probe's UNHIT branch.

    The parametrized test only ever asserts `not probe.hits` over real pages, so
    a probe that silently stopped detecting anything would leave it green on
    every page — the vacuous-guard shape this module already pins for
    `_guard_reachable_for`'s sentinel branch.
    """
    live = _ForeignRawtextProbe()
    live.feed('<svg><textarea><div id="dup"></div></textarea></svg>')
    assert live.hits == [("textarea", 1)], (
        f"the probe did not detect a suppressed tag inside `<svg>` (got "
        f"{live.hits!r}), so `test_no_covered_page_makes_the_id_collectors_"
        f"declared_limit_live` is vacuously green and the limit could be live on "
        f"a covered page. `textarea` exercises the RCDATA path, which a "
        f"CDATA-only probe missed."
    )
    for outside in (
        '<style>.x{}</style><div id="a"></div>',
        '<svg><div id="a"></div></svg><textarea>t</textarea>',
    ):
        quiet = _ForeignRawtextProbe()
        quiet.feed(outside)
        assert not quiet.hits, (
            f"the probe reports {quiet.hits!r} OUTSIDE foreign content in "
            f"{outside!r}, where the collector and a browser agree — it would "
            f"fail every page rather than guard one."
        )


# The top-level page set as of #3436 (2026-09-18). This pins the DERIVATION'S
# OUTPUT (not an allowlist the guard consults): a guard whose page set silently
# shrinks is a guard that silently stops covering a page, and the scope and its
# CI ratchet share one derivation, so they would shrink in lockstep unnoticed.
# `signin.html` was DELETED by #4054 (its route 301'd to /auth and was dead) — a
# page that is gone, not moved. `welcome.html`, `signup.html` and
# `invite-accept.html` MOVED to `website/apps/dashboard/public/` and are still
# covered, because `_all_website_pages()` now derives that root too.
_ALL_WEBSITE_PAGES_AT_3436 = frozenset({
    "404.html", "aviso-privacidad.html", "contact.html", "docs.html", "dpa.html", "faq.html",
    "index.html", "invite-accept.html", "license.html", "privacy.html",
    "product.html", "security.html", "self-hosted.html",
    "signup.html", "tos.html", "welcome.html",
})


def test_id_guard_covers_every_website_page() -> None:
    """Guard the guard: `_all_website_pages()` must not silently shrink.

    A narrow derivation would make the guard pass by covering less, and the
    ratchet below cannot notice because it reads the SAME derivation. The pin is
    an EQUALITY, so a page entering or leaving the TOP LEVEL requires this edit
    in the same PR — the change becomes a decision rather than one that quietly
    alters what "covered" means.

    The pin sees only what the glob sees; the shapes it cannot (another
    extension, a nested path) are tracked in #4111, and `_all_website_pages()`
    says so rather than implying coverage.
    """
    got = {page.name for page in _all_website_pages()}
    assert got == set(_ALL_WEBSITE_PAGES_AT_3436), (
        f"the id guard's page set changed: "
        f"missing={sorted(_ALL_WEBSITE_PAGES_AT_3436 - got)} "
        f"added={sorted(got - _ALL_WEBSITE_PAGES_AT_3436)}. If a page genuinely "
        f"left the site, update `_ALL_WEBSITE_PAGES_AT_3436` in the same PR and "
        f"say why; if one was ADDED, add it to the pin. A page that MOVED is not "
        f"gone — a page moved under `website/apps/` is no longer top-level, so "
        f"widen `_all_website_pages()` in this PR (see #4111), or the guard "
        f"stops covering the page this change was filed about."
    )


def test_every_website_page_is_selectable_by_ci() -> None:
    """Reverse ratchet for the id guard: every page it covers must run this file.

    The guard's scope is DERIVED, so a page added later is covered without
    editing a list — but if no `SOURCE_PATTERNS['onboarding']` entry matches that
    page, a PR touching only it selects no surface (`surfaces=[]`, `full=False`)
    and this file never runs. The guard then silently stops covering the page it
    was written for — the silent-drop class the other ratchets in this module
    document. Verified before listing: `select(["website/invite-accept.html"],
    …)` returned `surfaces=[]` with this file absent from `test_files`.

    The assertion is on `test_files` rather than `surfaces`, for the reason
    `_guard_reachable_for` documents: a page that selects a DIFFERENT surface
    keeps `surfaces` non-empty while this file still never executes.

    This subsumes the older `test_every_in_scope_page_is_selectable_by_ci`, whose
    page set is a strict subset of this one. That test is deliberately kept: it
    documents the BLOG guard's scope, and deleting a `#3950` ratchet inside an
    id-uniqueness change would be scope creep, not a simplification.
    """
    # NOTE: the path must be the page's REAL location, not `website/{name}`.
    # That assumption held only while every covered page was top-level; #4054
    # moved pages to `website/apps/dashboard/public/`, and a name-based path then
    # checks a file that does not exist (a permanently "unselectable" report that
    # no entry can satisfy).
    unselectable = [
        rel
        for rel in (p.relative_to(REPO_ROOT).as_posix() for p in _all_website_pages())
        if not _guard_reachable_for(rel)
    ]
    assert not unselectable, (
        f"page(s) covered by the id guard whose PR does not run this file: "
        f"{unselectable}. A duplicate id can then land on that page without the "
        f"guard ever executing. Add them to SOURCE_PATTERNS['onboarding'] in "
        f"tools/ci_selection.py."
    )
