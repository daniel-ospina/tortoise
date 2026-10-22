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

import ast
import re
import sys
from html import unescape as html_unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # tools/ is not an installed package
    sys.path.insert(0, str(REPO_ROOT))

# isort order for the first-party block is `tests` < `tools` < `tortoise`; keep it
# sorted so no `I001` suppression is needed (it lands on the block's first line,
# so an import added above it is what breaks the suppression).
from tests._html_links import (  # noqa: E402
    _AnchorHrefParser,
    blog_entry_hrefs,
    extract_anchor_hrefs,
    extract_element_ids,
    is_blog_entry,
)
from tools.ci_selection import load_manifest, select  # noqa: E402
from tortoise.commit_schema import Point, point_content_id  # noqa: E402

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
QUADRATURE = REPO_ROOT / "tortoise" / "quadrature.py"

# Both public pages, for the parametrized guards.
PAGES = pytest.mark.parametrize("page", [DOCS, FAQ], ids=["docs.html", "faq.html"])

# The external study the FAQ cites in support of keeping a verbatim source layer.
_PAPER_TITLE = "Fidelity Before Structure"
# The title this page cited before it was corrected: not a real paper title.
_FABRICATED_TITLE = "Verbatim Chunks Beat Extracted Artifacts"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing public page: {path}"
    return path.read_text(encoding="utf-8")


def _normalise(text: str) -> str:
    """Collapse a rendered page or a draft line to comparable plain text.

    Tags → space, entities unescaped, whitespace collapsed, and the markdown the
    drafts use (bold, links, code, list bullets) stripped, so a table row written
    as markdown can be compared with the same row as the browser sees it. This is
    the same normalisation shape `tests/e2e/test_legal_pages.py::_clean` uses —
    kept local because importing that module runs its module-level skip.
    """
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_unescape(text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [label](url) → label
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _canonical_statuses() -> set[str]:
    src = _read(SDK)
    m = re.search(r"POINT_STATUS_VALUES\s*=\s*frozenset\(\{([^}]*)\}\)", src)
    assert m, "POINT_STATUS_VALUES not found in tortoise/sdk.py — did the definition move?"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def _sdk_constant(name: str) -> int:
    """A module-level integer constant read from `tortoise/sdk.py`.

    Read, never restated: a pin that hardcodes the number the copy states cannot
    catch the COPY going false when the code moves. Measured at the #9515 review —
    `_CAPTURE_TURN_CAP = 5000` → `2000` left the FAQ pin green while the page
    claimed 5,000 characters.
    """
    m = re.search(rf"^{re.escape(name)}\s*=\s*(\d+)", _read(SDK), re.MULTILINE)
    assert m, f"{name} not found at module level in tortoise/sdk.py — did it move?"
    return int(m.group(1))


def _exp_bodies(text: str) -> set[str]:
    """Every `exp(...)` argument in `text`, compacted for comparison.

    The paren scan is explicit rather than a regex because the IMPL factor nests
    one (`exp(-w*(ca-cb)**2)`), and a non-nesting pattern silently returns a
    TRUNCATED body that compares unequal to an identical formula.
    """
    bodies: set[str] = set()
    start = 0
    while (open_at := text.find("exp(", start)) != -1:
        depth, idx = 0, open_at + 4
        while idx < len(text):
            if text[idx] == "(":
                depth += 1
            elif text[idx] == ")":
                if depth == 0:
                    break
                depth -= 1
            idx += 1
        bodies.add(text[open_at + 4:idx])
        start = open_at + 4
    return bodies


def _quadrature_exponent(func: str) -> str:
    """The exponent `tortoise/quadrature.py::<func>` returns, in `_exp_bodies` form.

    Derived so the FORMULA is pinned, not the prose around it: `/docs` states
    `exp(-w·c_a·c_b)` for NAND and `exp(-w·(c_a−c_b)²)` for IMPL, and swapping
    either for the other (or for a renamed symbol) reds here. `.` is normalised to
    `*` because the source multiplies and the page renders a middot.
    """
    src = _read(QUADRATURE)
    m = re.search(
        rf"^def {re.escape(func)}\(.*?return np\.exp\((.+?)\)\s*$", src, re.M | re.S)
    assert m, f"{func} not found in tortoise/quadrature.py — did it move?"
    return re.sub(r"\s+", "", m.group(1))


def _page_formulas(page: Path) -> set[str]:
    """The formulas a rendered page states, in `_exp_bodies` form.

    `<sup>2</sup>` becomes `**2` BEFORE the tags are stripped: the page writes the
    IMPL square as an HTML superscript, so a tag-stripping-only pass yields
    `(ca-cb)2` — a truncated exponent that would read as a mismatch on honest copy.
    """
    raw = re.sub(r"<sup>2</sup>", "**2", _read(page))
    plain = _normalise(raw).replace("·", "*").replace("−", "-")
    return _exp_bodies(re.sub(r"\s+", "", plain))

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


def test_docs_states_the_formulas_quadrature_actually_computes() -> None:
    """/docs' mechanism card must state the FACTORS the engine computes.

    The card's prose is pinned above, but prose is rewordable and a formula is
    not: `tortoise/quadrature.py` returns `np.exp(-w * ca * cb)` for NAND and
    `np.exp(-w * (ca - cb) ** 2)` for IMPL, and /docs renders both. Comparing the
    two DERIVED (never restated) makes a swap falsifiable — replacing the NAND
    factor on the page with IMPL's left the prose pin green (measured at the
    #9515 review: `joint-truth penalty` survives a wrong formula), and editing
    `quadrature.py` itself now reds the page that describes it.
    """
    nand = _quadrature_exponent("phi_nand")
    impl = _quadrature_exponent("phi_impl")
    assert nand != impl, (
        "phi_nand and phi_impl return the same factor — the two edge types are no "
        "longer different constraints, and /docs says they are"
    )
    formulas = _page_formulas(DOCS)
    for func, exponent in (("phi_nand", nand), ("phi_impl", impl)):
        assert exponent in formulas, (
            f"website/docs.html does not state exp({exponent}), which is what "
            f"tortoise/quadrature.py::{func} computes. The card's prose alone cannot "
            f"catch a wrong formula: it is pinned as words, and words survive a swap."
        )


def _extraction_write_calls() -> list[ast.Call]:
    """Every `create_point(...)` call inside `SDK._extract_session_v2`.

    Parsed rather than grepped: the extraction path's sourcing claim is about the
    DOOR it writes through, and a regex over the file would also match the
    advisory `sdk.create_point` mentioned in a neighbouring docstring.
    """
    tree = ast.parse(_read(SDK))
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_extract_session_v2":
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "create_point"
                ):
                    out.append(sub)
    return out


def test_the_extraction_write_path_sources_every_extracted_point() -> None:
    """The evidence under the FAQ's "every extracted claim carries the source".

    `Point.source_ref` (`tortoise/commit_schema.py`, pinned below) governs the
    COMMIT payload. The extraction path does not go through it — it writes with
    `sdk.create_point`, whose contract is advisory (`no_source`, #8171) — so the
    commit-schema pin cannot carry the FAQ's sentence on its own. What makes the
    sentence true is the write itself: `_extract_session_v2` passes
    `extractedFrom=f"session:{session_id}"`, and `create_point` wires that edge.
    Dropping the keyword left every website pin green while the published claim
    went false (measured at the #9515 review).
    """
    calls = _extraction_write_calls()
    assert calls, (
        "no create_point(...) call found inside tortoise/sdk.py::_extract_session_v2 "
        "— the extraction write path moved or was renamed; this pin must be "
        "re-anchored, not deleted"
    )
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        assert "extractedFrom" in kw, (
            f"the extraction write at tortoise/sdk.py:{call.lineno} does not pass "
            f"extractedFrom — an extracted point would then carry no link to the "
            f"session it was drawn from, which is what website/faq.html promises"
        )
        assert "session:" in ast.unparse(kw["extractedFrom"]), (
            f"the extraction write at tortoise/sdk.py:{call.lineno} no longer links "
            f"the extracted point to a `session:` Source"
        )


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


# ── #9510: a published claim must be true of the path it names ────────────────


def test_commit_schema_requires_a_source_on_every_extracted_claim() -> None:
    """The evidence under the FAQ's narrowed traceability sentence.

    `website/faq.html` claims traceability for *extracted* claims only. That is
    true because the extraction contract REQUIRES a source: `Point.source_ref` is
    `Field(min_length=1)` with no default, so an extracted Point cannot validate
    without one, and `""` is refused too.

    It is deliberately NOT claimed for *every* claim. `sdk.create_point` accepts a
    fresh claim with no source and no provenance and returns
    `advisories: [{code: 'no_source'}]` — advisory only, nothing is refused, and
    `audit`'s `missing_sourceKind` check exists precisely because unsourced claims
    occur ("PROVENANCE ADVISORY (#8171)", tortoise/sdk.py). The pre-fix sentence
    spread the commit-path requirement over a door that does not enforce it — the
    same defect class #9510 exists to remove.
    """
    content = "the production port is 16379"
    base = {
        "id": point_content_id(content),
        "content": content,
        "pointKind": "statement",
        "reason": "NEW",
        "confidence": 0.5,
        "c_cal": 0.5,
    }
    assert base["id"].startswith("pt_")
    with pytest.raises(ValidationError):
        Point.model_validate(base)
    with pytest.raises(ValidationError):
        Point.model_validate({**base, "source_ref": ""})
    sourced = Point.model_validate({**base, "source_ref": "doc:runbook"})
    assert sourced.source_ref == "doc:runbook"


def test_faq_ties_only_extracted_claims_to_their_source() -> None:
    """The FAQ may not universalise the commit path's source requirement.

    Pre-fix wording — "every claim is tied to the source it was drawn from" — was
    false of the point-creation door (see the test above). The scoped sentence is
    pinned so it cannot drift back, and the universal is pinned ABSENT so
    restoring it is a red test rather than a silent overclaim.
    """
    text = _normalise(_read(FAQ))
    assert "every extracted claim carries the source it was drawn from" in text
    # A SMELL DETECTOR, NOT A PROOF. It catches the phrasings this page has
    # actually shipped the defect in; it cannot decide the general case, and the
    # general case is a REVIEW judgement, not a lexical one.
    #
    # Deliberately NOT widened toward "proof". Doing that was tried: scanning the
    # whole sentence for a source noun reds the page's own true thesis ("Every
    # claim's score is recomputed whenever its source is superseded"), and a
    # negation window wide enough to accept "It is not required that every claim
    # carry a source" also swallows the affirmative "Not only do all claims carry
    # a source". A guard that reds TRUE copy is worse than a narrow one: the first
    # person it blocks weakens or deletes it, and we lose the pin entirely.
    #
    # What IS load-bearing, all found by mutation:
    #  * the qualifier is what makes the shipped sentence true, so a quantifier
    #    whose intervening words include "extracted" is the LEGITIMATE form;
    #  * the lookahead starts at the claim noun. A single `[^.]{0,120}` tail would
    #    span a comma-joined coordinate ("every extracted claim …, and all such
    #    claims must carry a source") and absorb the offender into the legitimate
    #    match, filtering both away;
    #  * the quantifier-to-noun span is 12 words — at 4, "every newly created and
    #    separately stored claim must carry a source" slipped through;
    #  * a requirement predicate must follow the noun. Requiring a predicate (and
    #    excluding a bare "is") is what keeps true sentences such as "Every
    #    claim's score is recomputed whenever its source is superseded" green;
    #  * any negation anywhere in the sentence SKIPS it, so the honest "No claim
    #    lacks a source" and "Every claim need not carry a source" stay green.
    #    The cost is that emphatic affirmatives starting "Not only …" are missed;
    #    that is an accepted, stated limitation, not an oversight;
    #  * BOTH the match and the lookahead are case-insensitive — "All claims carry
    #    a Source" is the same overclaim in title case, which is exactly how an
    #    FAQ question heading is written.
    quantifier = re.compile(
        r"\b(every|all|each|any)\b((?:\s+\w+){0,12}\s+)(?:claims?|points?|beliefs?)\b", re.I
    )
    predicate = re.compile(
        r"\b(?:carry|carries|need|needs|require|requires|must|have|has|tie|tied|"
        r"trace|traced|traceable|record|records|keep|keeps)\b",
        re.I,
    )
    negation = re.compile(r"\b(?:not|no|never|without|lacks?)|n't\b", re.I)
    offenders = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if negation.search(sentence):
            continue  # honest negations and emphatic affirmatives: out of scope
        for m in quantifier.finditer(sentence):
            if "extracted" in m.group(2).lower():
                continue  # the scoped form: this is the shipped, true statement
            tail = sentence[m.end() : m.end() + 120]
            if not predicate.search(tail):
                continue  # no requirement predicate: not this defect
            if re.search(
                r"\b(?:sourc|traceabl|traced|origin|provenance|came from|where it came)",
                tail,
                re.I,
            ):
                offenders.append(sentence[m.start() : m.end() + 120].strip())
    assert not offenders, (
        "faq.html universalised the source requirement again: "
        f"{offenders}. Scope it to extracted claims (the commit contract requires "
        "source_ref, but sdk.create_point does not — it returns a no_source "
        "advisory, #8171)."
    )


#: Formula-level mechanism vocabulary. `faq.html`'s own note above the TOC rules it
#: out ("Keep mechanism prose here to the minimum needed to answer an objection");
#: the account belongs on `/docs`, which the FAQ defers to.
_MECHANISM_MARKERS = (
    "joint-truth penalty",
    "difference coupling",
    "own weight and direction",
    "exp(-w",
)


def _mechanism_hits(text: str) -> list[str]:
    """The formula-level mechanism phrases present in ``text``."""
    return [marker for marker in _MECHANISM_MARKERS if marker in text]


def test_the_belief_mechanism_is_documented_on_docs_first() -> None:
    """`faq.html`'s own maintainer note forbids mechanism prose on that page.

    The note (in the source above the TOC) reads: "The full technical account of
    the belief model lives on /docs ... Update /docs first and let this page
    follow. Keep mechanism prose here to the minimum needed to answer an
    objection."

    Measured history, so the premise of the guard is not a guess: `origin/main`'s
    `faq.html` named neither relation (`grep -c "NAND\\|IMPL"` → 0) while
    `docs.html` already carried a NAND/IMPL account on its Confidence (EP) card.
    What this change adds is DEPTH, not location — the formula-level account
    (`tortoise/quadrature.py`: NAND is a joint-truth penalty, IMPL a difference
    coupling) is now on /docs, and the FAQ answer that used to stand alone
    defers to it. Both halves are asserted, and both are false on the pre-fix
    tree, so this test fails on the defect it guards: /docs carries the
    mechanism, and the FAQ points at /docs for it.

    The FAQ keeps ONE mechanism sentence — the one that answers the objection
    (mutual vs directed contradiction). It is pinned so the exception the note
    allows cannot widen into an account, and `_mechanism_hits` is exercised on a
    sample that DOES contain the formula-level vocabulary, so the refusal below
    is a live detector rather than a `not in` on strings the file never held.
    """
    note = re.sub(r"\s+", " ", _read(FAQ))
    assert "Keep mechanism prose here to the minimum" in note, (
        "faq.html's maintainer note is gone — this test enforces a rule the file "
        "states. Re-anchor it here or retire both deliberately."
    )
    docs, faq = _normalise(_read(DOCS)), _normalise(_read(FAQ))
    for mechanism in (
        "joint-truth penalty",
        "toward the source's level",
        "own weight and direction",
    ):
        assert mechanism in docs, (
            f"website/docs.html no longer states {mechanism!r}. The mechanism account "
            f"belongs on /docs: the FAQ defers to it ('Update /docs first and let "
            f"this page follow') and must not be the only page carrying it."
        )
    assert "what each relation does to a score" in faq, (
        "website/faq.html no longer points at /docs for the mechanism. The deferral "
        "is the half that keeps mechanism prose off this page — its own note says "
        "'Update /docs first and let this page follow'."
    )
    # The objection-level contradiction sentence is the one mechanism paragraph the
    # note permits (it answers "does a contradiction delete the losing belief?");
    # pinned so the permitted exception cannot quietly grow.
    assert (
        "on both sides for a mutual contradiction, on the target only for a directed one"
        in faq
    ), (
        "website/faq.html lost (or reworded) the objection-level contradiction "
        "sentence. If it moved to /docs, update this pin deliberately: it is the only "
        "mechanism sentence the page's own note allows."
    )
    assert _mechanism_hits(faq) == [], (
        f"website/faq.html restates the mechanism ({_mechanism_hits(faq)}). Update "
        f"/docs first and keep this page to the objection-level claim — that is the "
        f"file's own maintainer note."
    )
    sample = _normalise(
        "<p>A <strong>NAND</strong> is a joint-truth penalty and carries its own "
        "weight and direction; an <strong>IMPL</strong> is a difference coupling: "
        "<code>exp(-w&middot;c<sub>a</sub>&middot;c<sub>b</sub>)</code>.</p>"
    )
    assert _mechanism_hits(sample) == [
        "joint-truth penalty",
        "difference coupling",
        "own weight and direction",
        "exp(-w",
    ], (
        "the formula-level detector no longer fires on mechanism vocabulary, so the "
        "assertion above proves nothing — this sample must hit it."
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
    "contact.html", "docs.html", "dpa.html", "faq.html", "index.html",
    "license.html", "privacy.html", "product.html", "security.html",
    "self-hosted.html", "tos.html",
    # ⚠️ `aviso-privacidad.html` LEFT this set in #9486: the Spanish LFPDPPP
    # notice was RETIRED because the controller's jurisdiction of residence is
    # the United Kingdom — the document had no basis. Its route 301s to
    # `/privacy`, which is the privacy notice. Removing it from this pin is the
    # deliberate decision the comment above requires, not a page quietly
    # disappearing from coverage.
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


def _seam_declared_limit_reasons(html: str) -> list[str]:
    """Why `html` reaches a construct `tests/_html_links.py` does not model.

    One implementation for BOTH ratchets below, which ask this of two different
    scopes: the link rule covers the served+indexable pages, the id rule
    (#4118) covers every page the id guard reads. Two hand-synced copies of these
    four checks would be the drift class this module exists to close — the same
    argument that put the two link rules in one module (#3962).

    The reasons (the caller prefixes the page):

      * an HTML INTEGRATION POINT or MathML text integration point reached in
        foreign content, where the seam's tree model is known incomplete;
      * a foreign-ROOT end tag matching no open root (the cross-namespace case);
      * any other end tag closed inside foreign content, which is handled today
        but is one edit from the case above;
      * a WEDGE — a construct the seam detects and refuses to read (`<frameset>`,
        the script-data escaped state).
    """
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    reasons: list[str] = []
    risky = sorted({tag for tag in parser.foreign_tags if tag in _DECLARED_FOREIGN_RISK})
    if risky:
        reasons.append(f"reaches {risky} in foreign content")
    if parser.foreign_root_mismatch:
        # The precise cross-namespace trigger (item 5 of #3970): a foreign-ROOT end
        # tag that matches no open root, e.g. `</math>` under `<svg>`. A browser
        # ignores it and stays foreign; a bare counter left foreign content, so
        # this is where the model is known to be wrong in a way that matters.
        reasons.append(
            f"has a foreign-root end tag matching no open root "
            f"{sorted(set(parser.foreign_root_mismatch))}"
        )
    if parser.foreign_unmatched_endtags:
        # Wider RATCHET, kept deliberately: any other end tag inside foreign
        # content. Those are handled correctly today, but the covered pages use
        # only self-closing SVG children, so a page that starts closing SVG
        # elements by name is one edit from the case above (review finding,
        # #3962). The message says which of the two fired.
        reasons.append(
            f"closes element(s) inside foreign content "
            f"{sorted(set(parser.foreign_unmatched_endtags))}"
        )
    if parser.wedged_reason:
        reasons.append(f"stops being readable ({parser.wedged_reason})")
    return reasons


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
    offenders = [
        f"website/{page.name} {reason}"
        for page in sorted(_in_scope_pages())
        for reason in _seam_declared_limit_reasons(_read(page))
    ]
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
    in-scope pages (`security`, `tos`, `license`, `dpa`, `aviso-privacidad` — the
    last has since been retired, #9486) were
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
    hero already carries a secondary-link row (`Browse the docs -> . GitHub repo ->`)
    in `#beat-hero` that is clickable from first paint (`.beat.pe-on`
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


def _duplicate_element_ids(html: str) -> dict[str, list[int]]:
    """{id: [line, …]} for every id declared on more than one element.

    Thin wrapper over `tests/_html_links.py::extract_element_ids`, the same seam
    the blog guard's link rule reads: one parser for this module's "what does a
    browser put in the document tree?" question (#4118). The guard used to carry
    its own `HTMLParser` subclass here, and each divergence found in it (one per
    review cycle — foreign raw text, CDATA in HTML content, an abrupt-close
    comment) had to be re-modelled there or declared and gated in its own right.
    Reading the seam removes the class: the same three constructs are the seam's
    problem, handled once and pinned once.
    """
    lines: dict[str, list[int]] = {}
    for value, line in extract_element_ids(html):
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
    # An EMPTY id is no id: `getElementById("")` matches nothing, so two of them
    # are not a collision a script can observe. This extractor's policy, stated
    # because Chromium's `querySelectorAll('[id]')` does list both.
    assert _duplicate_element_ids('<div id=""></div><div id=""></div>') == {}
    # A CDATA section in HTML content: honouring it would swallow to `]]>`, where
    # a browser ends the comment at the first `>` and parses the rest as markup.
    # Chromium-verified: two elements with id=dup.
    assert _duplicate_element_ids(
        '<div id="dup"></div><![CDATA[ > <div id="dup"></div> ]]>'
    ) == {"dup": [1, 1]}
    # An element's OWN id counts even where its CONTENT is not markup — the
    # suppression is about content, and both of these are real elements to
    # `getElementById` (Chromium-verified: two elements with id=dup each).
    assert _duplicate_element_ids(
        '<style id="dup">/* x */</style><div id="dup"></div>'
    ) == {"dup": [1, 1]}
    assert _duplicate_element_ids(
        '<template id="dup"></template><div id="dup"></div>'
    ) == {"dup": [1, 1]}


# ── #4118: the collector IS the render-fidelity seam ────────────────────────
#
# The guard's ids now come from `tests/_html_links.py::extract_element_ids`, so
# the divergences #4118 listed are the seam's problem instead of a second
# parser's. Every outcome below was verified against headless Chromium
# (2026-10-11) — the ground truth the issue states, and the same check the seam's
# own #3962 pins were built on.


def test_id_guard_sees_the_constructs_that_used_to_be_missed_silently() -> None:
    """#4118: the SILENT-miss divergences are closed, pinned against Chromium.

    Both constructs below were reported by the id guard as "no duplicate" while a
    browser has two elements with that id — the direction a duplicate-id guard
    must not have, because nothing fails: the defect ships.

    The raw-text case is the whole family, not `<style>`: the stdlib's switch is
    driven by `set_cdata_mode` (from `CDATA_CONTENT_ELEMENTS`,
    `RCDATA_CONTENT_ELEMENTS` and its own plaintext rule), which fires on a tag
    NAME with no foreign-content awareness. Enumerating the set is how the
    earlier revision was blind to `title`/`textarea`/`plaintext`, so the seam
    suppresses the tokenizer's switch itself instead (#4118 comment thread);
    these cases pin the outcome for every tag that switch covers.
    """
    # 1. A raw-text tag inside FOREIGN content holds markup, not text. Under BOTH
    #    foreign roots, at two depths, in the self-closing spelling.
    for raw in (
        "style", "script", "xmp", "iframe", "noembed", "noframes",
        "textarea", "title", "plaintext",
    ):
        inner = f'<{raw}><div id="dup"></div></{raw}>'
        assert _duplicate_element_ids(
            f'<svg>{inner}</svg><div id="dup"></div>'
        ) == {"dup": [1, 1]}, raw
        assert _duplicate_element_ids(
            f'<math>{inner}</math><div id="dup"></div>'
        ) == {"dup": [1, 1]}, raw
        assert _duplicate_element_ids(
            f'<svg><g>{inner}</g></svg><div id="dup"></div>'
        ) == {"dup": [1, 1]}, raw
        assert _duplicate_element_ids(
            f'<svg><{raw}/><div id="dup"></div></svg>'
            f'<div id="dup"></div><div id="dup"></div>'
        ) == {"dup": [1, 1, 1]}, raw
    # The same tag is STILL raw text in HTML content — the fix must not have
    # turned suppression off, which would count markup a browser keeps as text
    # (the fail-loud direction, but a wrong answer all the same).
    assert _duplicate_element_ids(
        '<xmp><div id="dup"></div></xmp><div id="dup"></div>'
    ) == {}
    assert _duplicate_element_ids(
        '<textarea><div id="dup"></div></textarea><div id="dup"></div>'
    ) == {}
    # A 13.2.6.5 BREAKOUT tag ends the foreign context, so the raw-text tag after
    # it is the HTML one again and its "markup" is text — Chromium-verified, and
    # the case a foreign-DEPTH-ONLY fix gets wrong.
    assert _duplicate_element_ids(
        '<svg><p><style><div id="dup"></div></style></svg><div id="dup"></div>'
    ) == {}
    assert _duplicate_element_ids(
        '<svg><br><style><div id="dup"></div></style></svg><div id="dup"></div>'
    ) == {}
    # 2. An ABRUPT-CLOSE comment ends where a browser ends it. `HTMLParser`
    #    searches for a later `--!?>` FIRST, so one of these spellings swallowed
    #    everything up to the next `-->` — including the element below.
    assert _duplicate_element_ids(
        '<!--> <div id="dup"></div> --><div id="dup"></div>'
    ) == {"dup": [1, 1]}
    assert _duplicate_element_ids(
        '<!---><div id="dup"></div>--><div id="dup"></div>'
    ) == {"dup": [1, 1]}
    # 3. The OVER-count direction the old collector documented is gone as a side
    #    effect of reading the seam: `<template>` content is a browser's inert
    #    fragment, which `getElementById` never reaches, so it is not a duplicate
    #    (Chromium-verified: ONE element with id=dup). Pinned because it is a
    #    behaviour CHANGE, not a bug fix — the old guard red on this.
    assert _duplicate_element_ids(
        '<template><div id="dup"></div></template><div id="dup"></div>'
    ) == {}
    # `<noscript>` likewise, and for the mirror reason: with scripting ENABLED —
    # the browser default, and the only mode this guard's question is asked in —
    # its content is raw text, not elements (Chromium-verified: one element).
    assert _duplicate_element_ids(
        '<noscript><div id="dup"></div></noscript><div id="dup"></div>'
    ) == {}
    # A self-closing element is ONE element, and the two forms reach
    # `_record_id` from different handlers (`<svg>`/`<math>` through the foreign
    # branch, everything else through `handle_starttag`). Recording on both paths
    # would report a duplicate where a browser has a single element, so each form
    # is pinned alone — a lone element must never be a duplicate
    # (Chromium-verified: one element either way).
    assert _duplicate_element_ids('<svg id="dup"/>') == {}
    assert _duplicate_element_ids('<g id="dup"/>') == {}


def test_no_page_the_id_guard_reads_makes_the_seams_declared_limits_live() -> None:
    """Guard the guard: the seam's declared limits must stay unreached (#4118).

    The id guard now reads its ids from `tests/_html_links.py`, so every page it
    covers is subject to that seam's declared limits (#3970) as well — and a page
    that reaches one can make the guard see too FEW ids, a SILENT miss. That is
    the risk `_ForeignRawtextProbe` gated for one construct before this change,
    which is why replacing the collector had to replace its gate too rather than
    delete it.

    The scope is `_all_website_pages()`, deliberately wider than the blog guard's
    own ratchet: that one covers the served+indexable pages, because a link can
    only be followed from a page a visitor reaches, while an id collision is a
    property of the DOCUMENT. `404.html` is served on every miss and the app's
    auth pages run their own scripts, so a missed duplicate there is just as
    real.
    """
    offenders = [
        f"{page.relative_to(REPO_ROOT).as_posix()} {reason}"
        for page in _all_website_pages()
        for reason in _seam_declared_limit_reasons(_read(page))
    ]
    assert not offenders, (
        f"page(s) the id guard reads now reach a construct the shared extractor "
        f"does not model: {offenders}. Those limits are declared out of scope in "
        f"`tests/_html_links.py` because no covered page used them (#3970); while "
        f"one is live here the id guard can MISS a duplicate silently — the "
        f"direction #4118 exists to close. Either model the construct (see "
        f"#3970) or change the page, and update the declaration and this test in "
        f"the same PR."
    )


def test_the_declared_limit_helper_fires_on_the_constructs_it_names() -> None:
    """Guard the guard: pin the UNHIT branch of BOTH ratchets above.

    Both only ever assert an empty offender list over real pages, so a helper
    that silently stopped detecting anything would leave them green on every page
    — the vacuous-guard shape this module already pins for
    `_guard_reachable_for`'s sentinel branch. One case per check, plus the clean
    direction so a helper that fired on everything would be caught too.
    """
    assert _seam_declared_limit_reasons(
        '<svg><foreignObject><div></div></foreignObject></svg>'
    ), "the HTML integration point check detected nothing"
    assert _seam_declared_limit_reasons(
        '<svg></math>'
    ), "the foreign-root-mismatch check detected nothing"
    assert _seam_declared_limit_reasons(
        '<svg></g>'
    ), "the unmatched-end-tag check detected nothing"
    assert _seam_declared_limit_reasons(
        '<frameset><div></div></frameset>'
    ), "the wedge check detected nothing"
    for clean in ('<div></div>', '<svg><path/></svg>'):
        assert not _seam_declared_limit_reasons(clean), (
            f"the limit scan reports a construct in {clean!r}, where the seam "
            f"models the document exactly — it would fail every page rather "
            f"than guard one."
        )


# The top-level page set as of #3436 (2026-09-18). This pins the DERIVATION'S
# OUTPUT (not an allowlist the guard consults): a guard whose page set silently
# shrinks is a guard that silently stops covering a page, and the scope and its
# CI ratchet share one derivation, so they would shrink in lockstep unnoticed.
# `signin.html` was DELETED by #4054 (its route 301'd to /auth and was dead) — a
# page that is gone, not moved. `welcome.html`, `signup.html` and
# `invite-accept.html` MOVED to `website/apps/dashboard/public/` and are still
# covered, because `_all_website_pages()` now derives that root too.
# `design-partners-intro.html` was ADDED (2026-10-01): the design-partner
# one-pager. A top-level served document, so the id guard covers it — its
# `noindex` keeps it out of the BLOG guard's scope, not out of this one.
# `investors.html` was ADDED (#8568, 2026-10-13): the investor one-pager, forked
# from it. Same class of document, same reasoning — `noindex`, covered here.
# `aviso-privacidad.html` was REMOVED (#9486): the file is deleted, so it is
# no longer a page the id guard has a subject in — `/aviso-privacidad` now
# 301s to `/privacy`.
_ALL_WEBSITE_PAGES_AT_3436 = frozenset({
    "404.html", "contact.html", "design-partners-intro.html",
    "docs.html", "dpa.html", "faq.html",
    "index.html", "investors.html", "invite-accept.html", "license.html", "privacy.html",
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


# ---------------------------------------------------------------------------
# #2535 — session-recording retention in the Privacy Policy
# ---------------------------------------------------------------------------
# The Terms of Service (§4.3) promise that previously captured sessions "remain
# subject to this Agreement and the Privacy Policy", so the Privacy Policy must
# actually state how long they are kept. Before #2535 it did not state it at all,
# and the Terms pointed a reader — or a customer's security reviewer, or a
# regulator — at a document that did not answer the question.
#
# These pins exist because the #2535 change was originally covered by NOBODY:
# `test_retention_promise.py` asserts only the backup horizon, and nothing here
# inspected the session-recording text. Deleting the entire new §6 entry and
# reverting the version still produced a green run.

PRIVACY = WEBSITE / "privacy.html"


def test_privacy_states_session_recording_retention() -> None:
    """#2535: the retention statement must exist, and must not claim an expiry."""
    text = _read(PRIVACY)
    assert "<strong>Session recordings.</strong>" in text, (
        "privacy.html must disclose session recordings as a processed category; "
        "a retention carve-out for an undisclosed category is not a disclosure"
    )
    assert "retained <strong>until they are deleted</strong>" in text
    assert "no retention window and no automatic expiry" in text


def test_privacy_does_not_promise_a_deletion_control_that_does_not_exist() -> None:
    """#2535 review finding: state the SHIPPED authorization model, not a planned one.

    Session list/detail/delete are organisation-member authorised with no owner
    check and no role gate: ``delete_session`` depends on
    ``get_current_org_session_ungated`` and ``_require_scope``, which returns
    early for session auth (``org.get("key_id") is None``). Any active member can
    therefore delete ANY session in the organisation, not only their own.

    Wording that describes a member-scoped / admin-scoped split would describe an
    access-control boundary a security reviewer reads as a control — and it would
    be false. The planned refinement may be described as planned, and this pin
    holds the page to the shipped behaviour.
    """
    text = _read(PRIVACY)
    assert (
        "any member of the organization can view the organization's recorded "
        "sessions and delete them"
    ) in text
    assert "an organization administrator can delete across the organization" not in text, (
        "that split is NOT shipped: there is no role gate on session deletion"
    )


def test_privacy_header_version_matches_the_version_section() -> None:
    """#2535 review finding: the header badge contradicted §15 on the same page.

    The policy's own rule ("when this policy changes, the version number and
    effective date are updated") is only honoured if every place the version
    appears is updated. The 1.2 change updated §15 and left the header at 1.1,
    so the first thing a reader saw contradicted the section body.
    """
    text = _read(PRIVACY)
    header = re.search(
        r'<span class="label">Version</span>\s*<span class="value">([0-9.]+)</span>',
        text,
    )
    assert header is not None, "privacy.html must carry a header version badge"
    section = re.search(r"<strong>Current version:</strong>\s*([0-9.]+)", text)
    assert section is not None, "privacy.html must carry a §15 current version"
    assert header.group(1) == section.group(1), (
        f"header version {header.group(1)!r} contradicts §15's {section.group(1)!r}"
    )


def test_privacy_does_not_leave_session_deletion_backups_undisclosed() -> None:
    """#2535 review finding: deletion is not complete at the moment it is requested.

    A snapshot taken before a session is deleted still contains it and ages out on
    the four-week cycle. Stating an absolute "retained until deleted" while naming
    the backup residual for only the graph and account paths would over-promise.
    """
    text = _read(PRIVACY)
    assert "A deleted session recording is likewise not actively purged" in text
    assert "not governed by the 7-day restore window" in text, (
        "§16 must scope the carve-out to the 7-day restore window, not to all windows"
    )


def _draft_history_rows(draft_path: Path, heading: str) -> dict[str, tuple[str, str]]:
    """`{version: (date, normalised summary)}` from a draft's markdown table."""
    draft = draft_path.read_text(encoding="utf-8")
    start = draft.lower().rfind(heading)
    assert start != -1, f"{draft_path.name} lost its '{heading}' table"
    rows: dict[str, tuple[str, str]] = {}
    for line in draft[start:].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped) <= set("|-: "):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3 or cells[0].lower() == "version":
            continue  # separator or header
        rows[cells[0]] = (cells[1], _normalise(cells[2]))
    return rows


def _rendered_history_rows(render_path: Path) -> dict[str, tuple[str, str]]:
    """`{version: (date, normalised summary)}` from the page's history table.

    Parsed from the document rather than substring-matched: a containment check
    passes when a draft row merely STARTS the same way, which is exactly how a row
    can lose the sentence that makes it complete and stay green.
    """
    html = render_path.read_text(encoding="utf-8")
    body = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    assert body, f"{render_path.name} has no history table body"
    rows: dict[str, tuple[str, str]] = {}
    for tr in re.findall(r"<tr>(.*?)</tr>", body.group(1), re.S):
        cells = [_normalise(td) for td in re.findall(r"<td>(.*?)</td>", tr, re.S)]
        if len(cells) >= 3:
            rows[cells[0]] = (cells[1], cells[2])
    return rows


def test_each_draft_history_row_mirrors_its_rendered_page() -> None:
    """A draft's revision-history table and the page's must be the SAME table.

    `test_draft_to_render_fidelity` (e2e) deliberately skips table rows ("changelog
    structure — build-resolved"), so nothing else compares them. This pins the two
    sides as parsed tables — the version SET in both directions and the summary per
    version — because a one-directional containment check cannot see a row that
    lost a sentence, a row the draft is missing, or a row the render is missing.

    The 1.0 row's date may still be the deploy placeholder (`[Effective date: …]`):
    that exemption covers the DATE CELL only, never the row, so its summary is
    still compared.
    """
    cases = (
        (WEBSITE.parent / "docs" / "drafts" / "2026-08-08-657-privacy-draft.md",
         "document history / revisions", PRIVACY),
        (WEBSITE.parent / "docs" / "drafts" / "2026-08-08-657-tos-draft.md",
         "revision history", WEBSITE / "tos.html"),
    )
    for draft_path, heading, render_path in cases:
        draft_rows = _draft_history_rows(draft_path, heading)
        render_rows = _rendered_history_rows(render_path)
        assert draft_rows, f"{draft_path.name} has no history rows to mirror"
        assert set(draft_rows) == set(render_rows), (
            f"{draft_path.name} and {render_path.name} list different versions: "
            f"draft-only {sorted(set(draft_rows) - set(render_rows))}, "
            f"page-only {sorted(set(render_rows) - set(draft_rows))}"
        )
        for version, (date, summary) in draft_rows.items():
            r_date, r_summary = render_rows[version]
            # A bracketed date is an unresolved deploy placeholder. Only v1.0 may
            # still carry one; any OTHER version with one is a row that stopped
            # being real, and a resolved date is compared for every version.
            if date.startswith("["):
                assert version == "1.0", (
                    f"{draft_path.name}: v{version} still has the deploy placeholder "
                    f"{date!r} — only the initial 1.0 row may"
                )
            else:
                assert date == r_date, (
                    f"{draft_path.name}: v{version} dated {date!r} but "
                    f"{render_path.name} says {r_date!r}"
                )
            assert summary == r_summary, (
                f"{draft_path.name}: v{version} the two texts differ.\n"
                f"  draft: {summary}\n  page : {r_summary}"
            )


def test_privacy_draft_mirrors_the_session_recording_retention() -> None:
    """The canonical doc requires the draft and the published page to move together.

    `docs/retention-and-deletion.md` states the draft's policy body is coupled to
    the published page and must be edited in BOTH files together. The e2e fidelity
    gate only checks draft ⊆ render, so a render-only ADDITION passes it silently —
    which is how the mirror would have gone stale here.
    """
    draft = _read(REPO_ROOT / "docs" / "drafts" / "2026-08-08-657-privacy-draft.md")
    assert "**Session recordings.**" in draft
    # The version is READ from the render, not re-typed here: a hand-maintained
    # second copy of the version number goes stale on every bump and forces a
    # test edit that asserts nothing about the document.
    m = re.search(r"<p><strong>Current version:</strong>\s*([0-9.]+)</p>", _read(PRIVACY))
    assert m, "privacy.html lost its 'Current version' line"
    # The draft carries the number TWICE (the §15 line and a `**Version:**` line in
    # its header) and neither is compared by the history-table test, so both are
    # pinned here against the render's single source of truth.
    meta = re.search(r'<span class="label">Version</span>\s*<span class="value">([0-9.]+)</span>', _read(PRIVACY))
    assert meta, "privacy.html lost its meta Version badge"
    assert meta.group(1) == m.group(1), (
        f"privacy.html's meta badge says {meta.group(1)} but §15 says {m.group(1)}"
    )
    assert f"**Version:** {m.group(1)}" in draft, (
        "the privacy draft's header version no longer mirrors the rendered policy"
    )
    assert f"**Current version:** {m.group(1)}" in draft, (
        "the privacy draft's version no longer mirrors the rendered policy — the "
        "draft and the page move together (docs/retention-and-deletion.md)")


def _privacy_section(heading: str) -> str:
    """Return the body of the privacy.html <h2> section starting at ``heading``.

    Scoping matters: an unscoped `in text` assertion is satisfied by whichever
    section happens to carry the phrase, so a mutation to a DIFFERENT section
    passes. Cycle 2 proved exactly that — deleting only the §2 bullet, or only
    the §4 bullet, left every pin green.
    """
    html = _read(PRIVACY)
    start = html.find(heading)
    assert start != -1, f"privacy.html must carry the heading {heading!r}"
    nxt = html.find("<h2>", start + len(heading))
    return html[start : nxt if nxt != -1 else len(html)]


def test_privacy_lists_session_recordings_in_the_data_category_section() -> None:
    """#2535 review cycle 2: §2 must disclose the category, not just §6 carve it out.

    A retention carve-out for a category the policy never discloses is not a
    disclosure. Scoped to §2 so that deleting the §2 bullet alone goes red.
    """
    section = _privacy_section("<h2>2. Data processed by the service</h2>")
    assert "<strong>Session recordings.</strong>" in section, (
        "§2 must list session recordings as a processed category"
    )


def _faq_answer(anchor: str) -> str:
    """The FAQ answer under ``id="<anchor>"``, up to the next question.

    Scoped for the same reason `_privacy_section` is: an unscoped `in text` pin is
    satisfied by whichever answer happens to carry the phrase. Measured at the
    #9515 review — the `truncat` stem matched an unrelated sentence about
    *truncated neighbourhoods* while the source-text answer itself could drop the
    marker claim entirely and stay green.
    """
    html = _read(FAQ)
    marker = f'id="{anchor}"'
    start = html.find(marker)
    assert start != -1, f"faq.html must carry the answer {anchor!r}"
    nxt = html.find("<h3", start + len(marker))
    return html[start : nxt if nxt != -1 else len(html)]


def test_privacy_establishes_the_basis_that_6_cites() -> None:
    """#2535 review cycle 2: §6 cites §4 for justification, so §4 must establish it.

    Scoped to §4 so that deleting the §4 legal-basis bullet alone goes red.
    """
    section = _privacy_section("<h2>4. Purposes of processing and legal bases</h2>")
    assert "session content its members contribute is processed" in section, (
        "§4 must state the purpose for session recordings, which §6 cites"
    )
    assert "legitimate interests (Art. 6(1)(f)):" in section


def test_faq_source_answer_states_the_capture_shaping() -> None:
    """The FAQ may not call the stored source "verbatim" or "never rewritten".

    The stored turn is shaped once, on the way in: `_CAPTURE_TURN_CAP` (read from
    `tortoise/sdk.py`, not restated here) routes every writer through
    `_clip_capture_turn_content`, which cuts to the cap AND appends a marker
    carrying the true original length ("no cut is silent"), and
    `_redact_turn_contents` replaces credential-shaped spans in place (#4911) —
    both before the write. So the absolute "the source is never rewritten" is
    false of the shipped capture path, and this page is the one that has to say
    what actually happens instead (#9510 review, #9517 carries the same fix).

    Extraction's own honesty claim is separate and stays pinned: extraction is
    additive, and every EXTRACTED claim carries its source. The scoping to
    "extracted" is deliberate — `sdk.create_point` takes an unsourced claim and
    only advises (`PROVENANCE ADVISORY`, #8171).
    """
    # `"verbatim evidence"` never shipped on `origin/main`, but it DID exist in this
    # branch at 5a6c8fa86 ("verbatim evidence underneath") and was the defect this
    # test's own cycle removed, so it is pinned against reintroduction, not against
    # history.
    text = _normalise(_read(FAQ))
    answer = _normalise(_faq_answer("source-text"))
    for refuted in (
        "verbatim and authoritative",
        "the source is never rewritten",
        "verbatim evidence",
        "kept verbatim",
    ):
        assert refuted not in text, (
            f"website/faq.html restates {refuted!r}, which is false of the shipped "
            f"capture path (5000-character clip with a truncation marker, plus "
            f"credential redaction, both applied before the write)."
        )
    # The NUMBER is DERIVED from `_CAPTURE_TURN_CAP`, never restated: hardcoding
    # 5,000 here left this test green when the constant moved to 2,000 and the
    # page went on claiming 5,000 characters (measured at the #9515 review). The
    # other two are pinned as STEMS, not sentences — a future editor rewording
    # "credential-shaped spans are redacted" to "credentials are redacted" has
    # not broken anything, and a pin that reds true copy gets deleted by the
    # first person it blocks.
    cap = _sdk_constant("_CAPTURE_TURN_CAP")
    assert f"{cap:,} characters" in answer, (
        f"faq.html's source-text answer no longer states the capture window "
        f"({cap:,} characters, `_CAPTURE_TURN_CAP` in tortoise/sdk.py)."
    )
    assert "original length" in answer.lower(), (
        "faq.html's source-text answer no longer says a cut turn is marked with its "
        "original length — that is what `_capture_truncation_marker` writes, and it is "
        "what makes the cut non-silent. Scoped to this answer on purpose: the old "
        "`truncat` stem was satisfied by an unrelated sentence about truncated "
        "neighbourhoods, so it never pinned this claim at all (measured at the #9515 "
        "review)."
    )
    assert "redact" in answer.lower(), (
        "faq.html's source-text answer no longer says credential-shaped spans are "
        "redacted (_redact_turn_contents, #4911)"
    )
    assert "every extracted claim carries the source it was drawn from" in answer, (
        "the scoped traceability claim was dropped along with the false universal"
    )
