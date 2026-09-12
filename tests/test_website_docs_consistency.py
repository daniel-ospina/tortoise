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
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBSITE = REPO_ROOT / "website"
DOCS = WEBSITE / "docs.html"
FAQ = WEBSITE / "faq.html"
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


def test_faq_internal_links_point_at_real_pages() -> None:
    """A relative link on the FAQ must resolve to a file that exists.

    Catches a renamed or removed sibling page, and a typo'd route — the FAQ links
    out to /docs, /self-hosted, /security, /tos, /dpa, /privacy and /license.
    Fragment-bearing links are matched too (the fragment is split off and the path
    resolved), so a `/#anchor` form cannot slip through unchecked.
    """
    html = _read(FAQ)
    missing: list[str] = []
    for href in sorted(set(re.findall(r'href="(/[^"]*)"', html))):
        path = href.split("#", 1)[0]
        if path in ("", "/"):
            continue
        candidate = WEBSITE / path.lstrip("/")
        if candidate.is_file() or candidate.with_suffix(".html").is_file():
            continue
        missing.append(href)
    assert not missing, (
        f"website/faq.html links to route(s) with no page: {missing}. "
        f"Add the page, fix the link, or route it through website/_redirects."
    )


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
