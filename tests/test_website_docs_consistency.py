"""Cross-page consistency guards for the public documentation surfaces (#3332).

Pins the facts the two public pages share — the Point status vocabulary, the
ontology citation, and how `confidence` is computed — so they cannot silently
drift apart again. The status vocabulary is asserted in full on `docs.html`
(the page that documents the lifecycle); `faq.html` is held to ALL-OR-NONE — every
word in the write set or none at all — since it does not document statuses at all.
(#13490: this line said "all six or none" while the write set has been FOUR words
since #11824 S0; it now names the rule instead of a count that rots at the next
door change.)
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
from urllib.parse import unquote, urljoin, urlparse, urlsplit
from xml.etree import ElementTree

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
    _script_data_state,
    blog_entry_hrefs,
    extract_anchor_hrefs,
    extract_anchors_within,
    extract_element_attributes,
    extract_element_ids,
    extract_first_base_href,
    extract_heading_boundary_facts,
    extract_heading_positions,
    extract_mode_dependent_regions,
    extract_raw_text_elements,
    extract_visible_text,
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
    """The Point status WRITE set, read from the LIVE constant (#13490).

    It used to be parsed out of `sdk.py`'s source with a regex requiring the
    literal `frozenset({...})` form. #11824 S0 (`2f1d5a5ca0`, #13143) narrowed
    the set to the four lifecycle words and made the declaration a REFERENCE —
    `POINT_STATUS_VALUES = frozenset(_LIFECYCLE_STATUS_VALUES)` — so the regex
    matched nothing and the guard read as "did the definition move?" while the
    change it exists to announce had already happened. Importing the constant
    cannot rot that way, and it is the same discipline `_sdk_constant` states:
    read the value the SDK ships, never restate it.

    ⛔ FOUR words is the WRITE set (#11824 §3, `tortoise/lifecycle_vocabulary.py`).
    The READ set is deliberately wider and is not this constant — stored rows
    keep `retracted`/`archived`/`outdated` (no backfill), which is why the page
    below may NAME those words while listing only these four.
    """
    from tortoise.sdk import POINT_STATUS_VALUES
    return set(POINT_STATUS_VALUES)


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


def _page_plain(raw: str) -> str:
    """`raw` page HTML as comparable plain text, formula renderings normalised.

    `<sup>2</sup>` becomes `**2` BEFORE the tags are stripped: the page writes the
    IMPL square as an HTML superscript, so a tag-stripping-only pass yields
    `(ca-cb)2` — a truncated exponent that would read as a mismatch on honest copy.
    `·` and `−` are the page's multiplication and minus; the source writes `*`/`-`.
    """
    return (
        _normalise(re.sub(r"<sup>2</sup>", "**2", raw))
        .replace("·", "*")
        .replace("−", "-")
    )


def _compact_formulas(text: str) -> set[str]:
    """The `exp(...)` factors stated in `text`, compacted the way `_exp_bodies` reads."""
    return _exp_bodies(re.sub(r"\s+", "", text))


def _relation_factors(
    raw: str, relations: tuple[str, ...] = ("NAND", "IMPL")
) -> dict[str, set[str]]:
    """The factor stated in the CLAUSE that names each relation, per relation.

    A page-wide set of `exp(...)` bodies is BLIND TO A SWAP: both bodies being
    present is invariant under exchanging them, so a page reading
    "NAND is a joint-truth penalty — exp(-w·(c_a−c_b)²)" stayed green while the
    formula pin claimed to make a swap falsifiable (measured at the #9515 review).
    Each relation's factor is therefore read only from the text after its NAME, up
    to the next relation name, so the number is bound to the edge type it belongs
    to and an exchange of the two reds. It also stops a formula restated in an
    unrelated card from keeping the pin green after the card drops it.
    """
    names = [r.upper() for r in relations]
    plain = _page_plain(raw)
    boundary = re.compile(r"\b(?:" + "|".join(names) + r")\b", re.I)
    hits = [(m.start(), m.end(), m.group(0).upper()) for m in boundary.finditer(plain)]
    out: dict[str, set[str]] = {name: set() for name in names}
    for idx, (_, end, name) in enumerate(hits):
        if name not in out:
            continue
        stop = hits[idx + 1][0] if idx + 1 < len(hits) else len(plain)
        out[name] |= _compact_formulas(plain[end:stop])
    return out


def _stated_factors(raw: str) -> dict[str, str]:
    """Each `<code>exp(...)</code>` on the page, keyed by its DERIVED factor form.

    Maps `-w*ca*cb` back to the page's own rendering
    (`exp(-w&middot;c<sub>a</sub>&middot;c<sub>b</sub>)`), so a test can mutate the
    page exactly the way a wrong edit would and show the pin reds.
    """
    out: dict[str, str] = {}
    for m in re.finditer(r"<code>(exp\(.*?)</code>", raw, re.S):
        forms = _compact_formulas(_page_plain(m.group(1)))
        assert len(forms) == 1, f"could not derive one factor from {m.group(1)!r}"
        out.setdefault(forms.pop(), m.group(1))
    return out

# ── Point status vocabulary ──────────────────────────────────────────────────


def test_sdk_status_vocabulary_is_what_the_pins_below_assume() -> None:
    """Guard the guard: if sdk.py's statuses change, the pins must be revisited.

    ⛔ THIS PIN FIRED, AND THIS IS THE REVISIT (#13490). It named the pre-#11824
    set — `{draft, live, retracted, superseded, outdated, archived}` — which was
    the #432 set plus the two words that were never lifecycle states: `outdated`
    and `archived` (design §3.2). #11824 S0 (#13143) closed the WRITE vocabulary
    at the four below and left the READ set wide, so the pins that follow were
    re-derived together with this one (`website/docs.html` lists exactly these
    four and explains the legacy words; `test_docs_lists_every_canonical_point_
    status` compares against this same set).

    Keeping the literal here is the point — a set derived from the same import
    the pin checks would assert that the constant equals itself.
    """
    assert _canonical_statuses() == {
        "draft",
        "live",
        "deprecated",
        "superseded",
    }


def test_docs_states_the_formulas_quadrature_actually_computes() -> None:
    """/docs' mechanism card must state the FACTORS the engine computes, per edge type.

    The card's prose is pinned above, but prose is rewordable and a formula is
    not: `tortoise/quadrature.py` returns `np.exp(-w * ca * cb)` for NAND and
    `np.exp(-w * (ca - cb) ** 2)` for IMPL, and /docs renders both. Comparing the
    two DERIVED (never restated) makes a wrong factor falsifiable — replacing the
    NAND factor on the page with IMPL's left the prose pin green (measured at the
    #9515 review: `joint-truth penalty` survives a wrong formula) — and editing
    `quadrature.py` itself reds the page that describes it.

    The factors are bound to the relation that NAMES them, because a page-wide SET
    of bodies is invariant under a swap: `NAND — exp(-w·(c_a−c_b)²)` is materially
    wrong and stayed green under the set-only version of this pin (measured at the
    same review). The positive control at the end executes that swap.
    """
    nand = _quadrature_exponent("phi_nand")
    impl = _quadrature_exponent("phi_impl")
    assert nand != impl, (
        "phi_nand and phi_impl return the same factor — the two edge types are no "
        "longer different constraints, and /docs says they are"
    )
    raw = _read(DOCS)
    factors = _relation_factors(raw)
    for func, relation, exponent in (
        ("phi_nand", "NAND", nand),
        ("phi_impl", "IMPL", impl),
    ):
        other = impl if relation == "NAND" else nand
        assert exponent in factors[relation], (
            f"website/docs.html does not state exp({exponent}) in the clause that "
            f"names {relation}, which is what tortoise/quadrature.py::{func} "
            f"computes. The card's prose alone cannot catch a wrong formula: it is "
            f"pinned as words, and words survive a swap."
        )
        assert other not in factors[relation], (
            f"website/docs.html states exp({other}) in the clause that names "
            f"{relation} — the two factors are attached to the wrong edge types. "
            f"{relation} must state exp({exponent})."
        )

    # POSITIVE CONTROL — the mutation this pin exists to catch actually reds it.
    # Without this the guard could regress to a set membership check and stay
    # green on a swapped page, which is the defect the #9515 review found.
    rendered = _stated_factors(raw)
    missing = {nand, impl} - set(rendered)
    assert not missing, (
        f"website/docs.html does not render exp(...) for {sorted(missing)} in a "
        f"<code> element, so the swap control below cannot run"
    )
    assert raw.count(rendered[nand]) == 1 and raw.count(rendered[impl]) == 1, (
        "the two factors are not each stated exactly once on /docs, so the swap "
        "control is ambiguous"
    )
    swapped = (
        raw.replace(rendered[nand], "\x00NAND-FACTOR\x00")
        .replace(rendered[impl], rendered[nand])
        .replace("\x00NAND-FACTOR\x00", rendered[impl])
    )
    red = _relation_factors(swapped)
    assert nand not in red["NAND"] and impl in red["NAND"], (
        "the factor pin is blind to a swap: exchanging the NAND and IMPL factors "
        "on /docs did not red the clause-to-relation binding, so the pin cannot "
        "catch the defect its docstring names"
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
                    # #12901 FINAL batch: `create_point` was PRIVATISED to
                    # `_create_point` (body unchanged); the extraction write is the
                    # same call, so the pin follows the spelling the class holds.
                    and sub.func.attr in ("create_point", "_create_point")
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

    `docs.html` documents the lifecycle, so it must carry every word in the WRITE
    set (`POINT_STATUS_VALUES`, four since #11824 S0 — see
    `test_sdk_status_vocabulary_is_what_the_pins_below_assume`). `faq.html` does
    not document statuses at all — it is only required to stay internally
    consistent if it ever starts to.

    Accepts either rendering the page uses: the lifecycle list uses
    ``status: <name>`` and the legacy-flag footnote uses ``<code><name></code>``.
    ⛔ That two-form acceptance is NOT enough on the page that documents the
    lifecycle, and #13490 is the proof: the legacy-spelling paragraph legitimately
    writes ``<code>deprecated</code>`` and ``<code>superseded</code>``, so
    deleting those two LIFECYCLE BULLETS from `docs.html` still left
    ``present == canonical`` and this test GREEN — the guard reported OK while the
    page shipped 2 of the 4 writable statuses, which is precisely the partial
    vocabulary #3332 found. A bullet is therefore load-bearing only in the
    ``status: <name>`` form, so `docs.html` must carry THAT form for every word.
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
            f"tortoise/sdk.py. Either list every word in it or none — a partial "
            f"list is the #3332 defect."
        )
        if page == DOCS:
            assert present == canonical, (
                f"website/docs.html omits canonical Point status(es): "
                f"{sorted(canonical - present)}."
            )
            # The BULLET form, so prose that merely NAMES a word cannot satisfy
            # the pin (see the docstring: measured on the two words the
            # legacy-spelling paragraph mentions).
            for s in sorted(canonical):
                assert f"status: {s}" in html, (
                    f"website/docs.html's lifecycle list has no "
                    f"`status: {s}` bullet — the word is named somewhere else on "
                    f"the page, which is what let a deleted bullet pass before "
                    f"#13490. Every writable status needs its own lifecycle "
                    f"entry, not a mention in the prose."
                )


def test_docs_scopes_the_outdated_flag_to_surfaces_that_drop_it() -> None:
    """`docs.html` may not claim EVERY read surface HIDES an `outdated` row.

    The `outdated` boolean is a dead marker for retrieval — `_terminal_excluded`
    ANDs its `coalesce(n.outdated, false) = false` conjunct by default
    (`tortoise/live.py:159`) — but it is NOT hidden on every read.
    `belief_timeline` (`tortoise/sdk.py:27720`) takes no opt-in flag: its
    primary query (`:27821`) matches `{pointKind:'decision'}` and applies NO
    status/outdated conjunct to the POINT (the terminal predicate sits on the
    topic OBJECT), so an invalidated decision Point — which keeps its
    `aboutObject` edge, `invalidate_point` transfers no edges — comes back
    as-is, and a superseded one is re-attached down the CORRECTS chain
    (`:27834-27844`) — but only when the SUCCESSOR is itself a decision about
    the topic: `supersede_point` hands the prior's `aboutObject` to the
    successor, and the re-attach's `WHERE cur.id IN $ids` (`:27835`) can only
    match when that successor is one of the topic's decisions, so a decision
    superseded by a point of another kind drops out of the history.
    `get_point` (`:29952`) returns the row by address. That
    the prior SURVIVES is asserted by
    `tests/test_de2e6_temporal.py::TestDe2e6ReviewFixes::test_timeline_keeps_superseded_prior`
    (`:405`, its assertions at `:420-422`: `len(timeline) >= 2` plus a `CORRECTS`
    link). The `outdated` key the
    page names is pinned as well — the same test asserts the re-attached prior is
    marked, so `sdk.py`'s `if outdated: entry["outdated"] = True` cannot be
    deleted with the suite still green (measured: removing it fails that case).

    Two absolutes are pinned here, both measured on a real page:
    `no longer writable at all` — falsified by the server's three `outdated`
    writers, and the error this branch's first commit fixed — and
    `every read surface hides` — a replacement absolute that is wider than the
    code, the same defect one commit later. A replacement that is wider than
    the code is the same error, not a smaller one, on the page whose subject is
    precision about this word. `docs/ONTOLOGY.md:1176` states the bounded
    version ("readers … must continue to honour") and `:1184` scopes the
    exclusion to the DEFAULT query surfaces, so this pin holds the page to the
    ontology: every read treats the row as ENDED, the default query surfaces
    drop it, and the addressed/history reads still show it.

    ⚠️ `no longer writable at all` is banned only UNQUALIFIED. Scoped to the
    STATUS word the sentence is TRUE ("as a status, `outdated` is no longer
    writable at all"), and a pin that banned the phrase outright would red on a
    page more accurate than the one it protects.

    The scope is checked per SENTENCE, not per character window: a backward
    window both false-passes (a `status` token from the NEIGHBOURING sentence
    qualifies an unqualified claim) and false-fails (English often puts the
    qualifier AFTER the phrase, which a backward window never sees). Measured
    on both bodies by the cycle-3 review before this was a sentence split.
    """
    html = _read(DOCS)
    for _sentence in re.split(r"(?<=[.!?])\s+", " ".join(html.split())):
        if "no longer writable at all" not in _sentence:
            continue
        assert "status" in _sentence, (
            "website/docs.html calls `outdated` no longer writable AT ALL in a "
            "sentence that never names the STATUS word. The *status* is "
            "refused (`read_caller_status` raises with a repair naming "
            "`deprecated`/`superseded`), but the *property* of the same name "
            "is live and server-stamped on supersede, on invalidate, and when "
            "a newer assessment of the same source is written by the same "
            "assessor (`tortoise/record_projection.py` "
            "PointSuperseded/PointInvalidated, `tortoise/sdk.py` "
            "`_assess_source`). Scope the claim to the status word."
        )
    assert "every read surface hides" not in html, (
        "website/docs.html claims every read surface HIDES an `outdated` row. "
        "That is not true: `get_point` returns ANY flagged row by address, and "
        "`belief_timeline` returns a SUPERSEDED prior down the CORRECTS chain "
        "by default — see "
        "tests/test_de2e6_temporal.py::TestDe2e6ReviewFixes::test_timeline_keeps_superseded_prior, "
        "which passes on main. State the bounded fact instead: every read treats "
        "it as ENDED, the default QUERY surfaces drop it, and the addressed / "
        "history reads still show it (the history read for a superseded or "
        "invalidated DECISION point — an `assessment` is not one)."
    )
    assert "belief_timeline" in html, (
        "website/docs.html no longer names the read surface that KEEPS an "
        "`outdated` row (`belief_timeline`). Without it the paragraph's scoping "
        "generalises to the reader's whole API, which the SDK contradicts. If "
        "the carve-out is now described another way, update this pin with the "
        "reasoning rather than deleting it."
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

    The scoped sentence is pinned so it cannot drift back, and the UNIVERSAL is
    pinned ABSENT — as a CLASS, not as one string, because the false universal has
    never had a single fixed phrasing. What shipped on `origin/main` before this
    ruling was "Your source stays, verbatim and authoritative … the source is never
    rewritten" (removed by #9517; the capture-shaping test below pins that wording
    against reintroduction). "every claim is tied to the source it was drawn from"
    was this PR's own interim draft (`a5eff2b2d`), never copy that shipped on main —
    an earlier version of this docstring called it the pre-fix wording, which was
    wrong and is exactly why the detector matches the quantifier + requirement
    predicate + source noun instead of one sentence.
    """
    text = _normalise(_read(FAQ))
    assert "every extracted claim carries the source it was drawn from" in text
    # A SMELL DETECTOR, NOT A PROOF. It catches the SHAPE of the defect wherever it
    # is written — quantifier + requirement predicate + source noun — which is what
    # survives a rephrasing; it cannot decide the general case, and the general
    # case is a REVIEW judgement, not a lexical one.
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


#: The per-turn capture window, derived from the clipper's own constant so the
#: FAQ cannot outrun what the store keeps (#9517).
_CAPTURE_TURN_CAP = re.compile(r"^_CAPTURE_TURN_CAP\s*=\s*(\d+)", re.MULTILINE)

#: The qualifier the clipper's own blank-retention path makes necessary (#9608).
#: `_clip_capture_turn_content` returns an over-cap **whitespace-only** turn
#: UNMARKED — it holds no readable text, so there is nothing to mark and nothing
#: was lost (#4897 review rounds 12-14; load-bearing, because marking a
#: marker-only turn let the v1 extractor mint it into a synthetic Point).
#: "A turn over the window is kept WITH a truncation marker" is therefore a
#: universal the shipped clipper refutes, so neither retention answer may state
#: it unconditionally — both carry `#source-text`'s predicate instead.
#:
#: ⛔ THE PIN IS THE INVARIANT, NOT THIS WORDING. A phrase blacklist pins only the
#: phrasings it happens to list: a reworded absolute ("… is stored with a
#: truncation marker") passed the first version of this guard at the #9608 review.
#: So every CLAUSE in either answer that mentions the marker must carry a
#: condition on the cut — the regexes below — and a future editor rewording the
#: condition ("every turn that loses readable text is marked", "a marker is added
#: only when text is discarded") stays green. `_MARKER_CONDITIONAL` is the
#: illustrative phrasing used in the failure messages only, never the gate.
#:
#: ⛔ TWO MEASURED BYPASSES (#9608 review cycle 2, P2), both now closed:
#:  1. ``truncat|marker`` did not match the VERB ("any such cut is marked"), so a
#:     false absolute phrased that way was never selected as a marker clause
#:     while a true decoy in the same answer satisfied the gate. The verb is
#:     therefore a mention — but ONLY inside the cut domain (`_MARKER_DOMAIN_RE`),
#:     because a bare ``\bmark(?:ed|s|ing)\b`` made the answer's provenance clause
#:     ("every extracted claim is marked with the source it was drawn from") a
#:     marker clause and red a true rewording (#9608 review cycle 3, P2).
#:  2. ``…[^.]{0,80}readable text`` matched **unreadable** text ("the extractor
#:     drops unreadable text so that …") — the lookbehind closes it.
#: The bare ``truncat`` stem is deliberately NOT a mention on its own: this file
#: already records (#9515) that it matched an unrelated sentence about
#: *truncated neighbourhoods*, and a pin that reds true copy gets deleted.
#:
#: ⛔ AND A CONDITION MUST CONDITION THE MARKER (#9608 review cycle 3, P3). Two
#: branches that only *described* loss passed a false absolute: "…kept with a
#: truncation marker naming its original length, so no readable text is lost"
#: (a loss CLAIM — readable text IS discarded, only its length is recorded) and
#: "…, and readable text is discarded". So the negated branch must reach a
#: marker-absence object (``without`` / ``unmarked`` / ``silently``) and the
#: passive branch must sit behind a subordinator (``if`` / ``when`` / ...).
#:
#: ⛔ CLAUSES ARE SPLIT ON ``[.!?;:]`` BUT NOT ON ``,`` — a semicolon decoy ("…kept
#: with a truncation marker naming its original length; a cut that discards
#: readable text is a rare case") must not borrow the condition from the next
#: clause, while a comma is ordinary inside a single clause ("…carries a
#: truncation marker, but only when text is discarded") and splitting there would
#: red true copy.
#:
#: ⛔ AN ABSENCE DISCHARGE IS A CONDITION TOO (#9608 cycle 4, P3 — rot): the copy
#: must be able to state the EXCEPTION this whole test is about ("a whitespace-only
#: turn is returned unmarked, with no truncation marker"), and "holds
#: non-whitespace" is the clipper's own predicate spelled out, so both discharge
#: the rule. Without them the guard red the only sentence a maintainer would write
#: to document the blank path.
#:
#: ⛔ KNOWN, DOCUMENTED TRADEOFFS, not oversights: a same-clause appositive that
#: re-conditions the marker ("…naming its original length — a marker records a cut
#: that discards readable text") and an anaphoric follow-on clause ("…; the same
#: applies to a blank turn") are accepted, because the em dash and comma are the
#: same punctuation that keeps true copy ("…naming the original length — whenever
#: readable text is discarded") green. This is a smell-detector against an
#: absolute, not a natural-language prover.
_MARKER_MENTION_RE = re.compile(r"(?:truncat\w*\s+marker|marker\b)", re.I)
_MARKER_VERB_RE = re.compile(r"\bmark(?:ed|s|ing)\b", re.I)
#: The domain that makes a bare marker VERB a marker mention rather than ordinary
#: prose (a claim "marked with the source" is provenance, not the capture marker).
_MARKER_DOMAIN_RE = re.compile(
    r"\b(?:truncat\w*|clip\w*|cut\w*|windows?|original length)\b", re.I
)
_MARKER_CONDITION_RE = re.compile(
    r"(?:"
    r"(?:discards?|loses?|drops?|removes?|sacrifices?)"
    r"[^.;:\u2014]{0,80}(?<!un)(?:readable|legible) text"
    r"|(?:no|nothing)\s+(?:readable|legible)\b[^.;:\u2014]{0,60}"
    r"\b(?:without|unmarked|silently)\b"
    r"|\b(?:if|when|whenever|where|unless)\b[^.;:\u2014]{0,40}"
    r"(?:readable|legible) text\s+(?:is|would be|gets)\s+"
    r"(?:lost|discarded|dropped|removed|cut)"
    r"|only (?:when|if)\b|\bunless\b"
    r"|\b(?:no|without)\s+(?:an?\s+)?(?:truncation\s+)?marker\b"
    r"|\bunmarked\b"
    r"|\bnon-whitespace\b"
    r")",
    re.I,
)
_MARKER_CONDITIONAL = "cut that discards readable text"
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+")

#: Floor under `_CAPTURE_TURN_CAP` for the copy's "naming the turn's original
#: length" (#9608 review, P3). `_capture_truncation_marker` is
#: `37 + len(str(total))` characters; inside a window smaller than that, the
#: clipper falls back to the BARE sentinel (``…[truncated:``), which names no
#: length, so the FAQ's claim would be false for EVERY readable cut. 64 is a
#: CONSERVATIVE CUSHION, not the marker's width: it guarantees `keep >= 0` for any
#: turn shorter than 10^27 characters (37 + 27 digits = 64). At the production
#: 5,000 that fallback needs a >=4,964-digit total, i.e. unreachable — pinned so a
#: lowered window reds the suite for the reason the copy depends on rather than
#: only on the string match.
_MARKER_WIDTH_FLOOR = 64


def _capture_turn_cap() -> int:
    """How many code points of a captured turn are actually stored (`sdk.py`).

    Derived, not restated: `_clip_capture_turn_content` cuts every over-cap turn
    to at most this many code points and marks the cut with the turn's original
    length — EXCEPT a turn that holds no non-whitespace at all, which is returned
    unmarked because nothing readable was discarded (#4897 review rounds 12-14;
    #9608 for why that exception is what makes the copy conditional). So a docs
    claim about what "the source" retains is bounded by this number, and a change
    to the constant must red the FAQ rather than silently falsify it.
    """
    src = _read(SDK)
    m = _CAPTURE_TURN_CAP.search(src)
    assert m, (
        "_CAPTURE_TURN_CAP not found in tortoise/sdk.py — did the capture "
        "window move? The FAQ's source-retention answers are bound to it."
    )
    return int(m.group(1))


def _marker_clauses(answer: str) -> list[str]:
    """The CLAUSES in a FAQ answer that talk about the truncation marker.

    Plain text via `_normalise` so the split is on rendered prose rather than on
    markup (an `<a>` inside a clause would otherwise break it), split on
    `_CLAUSE_SPLIT_RE` — see the note above on why a semicolon separates but a
    comma does not.
    """
    return [
        c
        for c in _CLAUSE_SPLIT_RE.split(_normalise(answer))
        if _MARKER_MENTION_RE.search(c)
        or (
            _MARKER_VERB_RE.search(c)
            and _MARKER_DOMAIN_RE.search(c)
        )
    ]


def _faq_answer(anchor: str) -> str:
    """The FAQ answer under ``id="<anchor>"``, up to the next question.

    Scoped for the same reason `_privacy_section` is: an unscoped `in text` pin is
    satisfied by whichever answer happens to carry the phrase. Measured twice —
    #9517 found a retention promise that outlived the capture window, and the
    #9515 review found the `truncat` stem matching an unrelated sentence about
    *truncated neighbourhoods* while the source-text answer itself could drop the
    marker claim entirely and stay green.

    One definition on purpose: #9517 and #9515 each grew their own copy, and a
    module-scope redefinition silently shadows the earlier one (#9515 review).
    """
    html = _read(FAQ)
    marker = f'id="{anchor}"'
    start = html.find(marker)
    assert start != -1, f"website/faq.html lost the #{anchor} heading"
    nxt = html.find("<h3", start + len(marker))
    return html[start : nxt if nxt != -1 else len(html)]


def test_the_faq_source_retention_answers_disclose_the_capture_window() -> None:
    """#9517 ISSUE 2: a retention promise may not outrun what the store keeps.

    The two answers below promised the whole captured source is kept and
    "still there to search directly", while the capture path stores at most
    `_CAPTURE_TURN_CAP` (5,000) code points per turn, appends a truncation marker
    carrying the original length, and redacts credential-shaped spans before the
    write (#4911). For a long captured turn the tail is simply not in the store,
    so an unbounded "the raw material is still there" is false — and no page in
    `website/` disclosed the window at all.

    The window is read out of `tortoise/sdk.py`, so raising the cap without
    updating the copy, or wording the promise past the cap, is a red test.

    #9608: disclosing the window and the marker is not enough — the marker is
    CONDITIONAL. `_clip_capture_turn_content` returns an over-cap
    whitespace-only turn UNMARKED (nothing readable was discarded), so
    `#missed`'s "is kept with a truncation marker" was an absolute the clipper
    refutes — the same self-defeating-claim failure #9515 / #9510 exist to stop.

    ⛔ SO THE PIN IS THE INVARIANT, NOT A PHRASE (#9608 review): every CLAUSE in
    either answer that mentions the marker must state the condition on the cut.
    A banned-phrase list only catches the wordings it lists — the first version of
    this guard passed a reworded absolute ("is stored with a truncation marker")
    and red a true rewording, which is the rot `_RULING_REWRITES` exists to avoid.
    """
    cap = _capture_turn_cap()
    assert cap >= _MARKER_WIDTH_FLOOR, (
        f"_CAPTURE_TURN_CAP is {cap} — below the conservative "
        f"{_MARKER_WIDTH_FLOOR}-character floor at which the window can still "
        "reserve a marker that carries the original length for any turn the store "
        "can physically hold (tortoise/sdk.py::_capture_truncation_marker is "
        "37 + digits, and falls back to a length-free sentinel when the window "
        "cannot hold it), so the FAQ's 'a truncation marker naming the turn's "
        "original length' would be false for every readable cut (#9608)."
    )
    window = f"{cap:,}"
    for anchor in ("source-text", "missed"):
        answer = _faq_answer(anchor)
        assert window in answer, (
            f"website/faq.html#{anchor} promises the source is kept without "
            f"disclosing that a captured turn is stored clipped at {window} "
            "characters (tortoise/sdk.py::_CAPTURE_TURN_CAP). Scope the "
            "retention claim to what the store guarantees (#9517)."
        )
        assert "marker" in answer.lower(), (
            f"website/faq.html#{anchor} discloses the {window}-character window "
            "but not that a clipped turn is marked with its original length — "
            "the marker is what makes the stored turn honest to a reader."
        )
        marker_clauses = _marker_clauses(answer)
        assert marker_clauses, (
            f"website/faq.html#{anchor} lost every clause that discloses the "
            "truncation marker the capture path writes (#9608)."
        )
        for clause in marker_clauses:
            assert _MARKER_CONDITION_RE.search(clause), (
                f"website/faq.html#{anchor} states the truncation marker WITHOUT "
                f"the condition that makes it true (#9608): {clause!r}. "
                "tortoise/sdk.py::_clip_capture_turn_content returns an over-cap "
                "WHITESPACE-ONLY turn UNMARKED — no readable text was discarded, "
                "so there is nothing to mark and nothing was lost — which refutes "
                "an unqualified 'a turn over the window is kept with a truncation "
                "marker'. Reuse #source-text's predicate: 'a "
                f"{_MARKER_CONDITIONAL} carries a truncation marker naming the "
                "turn's original length'."
            )


#: #9510's ruling names each of these FAQ passages by anchor, quotes the
#: self-undercutting clause it forbids, and states the positive statement that
#: replaces it. The pair is the ruling's, not a taste judgement — which is why it
#: is a bounded per-anchor table rather than a phrasing blacklist that would rot.
_RULING_REWRITES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # A2 — "a different retrieval signal" framed Tortoise as a better index;
    # the ruling's rewrite is typed, weighted constraints **computed over**,
    # and the cognitive-layer-for-agents positioning.
    "not-just-rag": (
        ("a different retrieval signal",),
        ("computed over", "reasoning, memory and learning"),
    ),
    # A4 — "not as evidence that this graph works" undercuts the very graph the
    # answer is describing; the rewrite names what the study measured and what
    # Tortoise computes over.
    "source-text": (
        ("not as evidence that this graph works",),
        ("computes over typed relations",),
    ),
    # A10 — "the graph itself is not the differentiator" is correct but empty;
    # the rewrite is "a graph *stores* relationships; Tortoise *computes* over
    # them". A11's problem span was three clauses, and the first ("an alpha,
    # single-maintainer reimplementation that is not a competitor product")
    # survived three review rounds because nothing bound it: it is the premise
    # the removed "bolt-on-able rather than out of reach" conclusion was drawn
    # from, so keeping it keeps the self-undercut (#9517 review 5).
    "different": (
        ("not the differentiator", "single-maintainer reimplementation"),
        ("<em>computes</em> over them",),
    ),
}


def test_the_rulings_named_faq_rewrites_are_the_ones_that_shipped() -> None:
    """#9517 review 3: the ruling's A2/A4/A10 passages must not survive verbatim.

    #9510 forbids negative self-review on customer-facing copy, and its
    "Inventory and disposition" table names each of these three passages by
    anchor with the clause to remove and the positive statement to put in its
    place. The PR that implements the ruling rewrote seven of the twelve FAQ
    items, left these three standing, and its body then asserted the FAQ was
    the whole exposure — so the surviving passages are what a reader sees while
    the PR says otherwise.

    A4 is the sharp one: "do not read this study as evidence that this graph
    works" sits in the same paragraph the PR edits, and it hands the reader the
    argument against the product on the page that argues for it.
    """
    for anchor, (forbidden, required) in _RULING_REWRITES.items():
        answer = _faq_answer(anchor)
        for phrase in forbidden:
            assert phrase not in answer, (
                f"website/faq.html#{anchor} still carries {phrase!r}. #9510's "
                "inventory disposes of that clause — a surface that volunteers "
                "its own weak points hands the reader the argument against us "
                "— and #9517's own body claimed the FAQ was clean against the "
                "ruling, so the passage cannot stay here unrewritten."
            )
        for phrase in required:
            assert phrase in answer, (
                f"website/faq.html#{anchor} does not carry the positive "
                f"statement {phrase!r} that replaces the ruled-out clause. "
                "Deleting the self-undercut without stating what Tortoise "
                "actually does leaves the answer shorter, not stronger."
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


def test_docs_toc_anchors_all_resolve() -> None:
    """Every TOC entry on the docs page must land on a real id.

    The FAQ half of this pin is below; `docs.html` never had one. This is a
    TOC⇄id CONSISTENCY pin, not the deep-link pin: it would stay green if the
    `storage` section and its TOC entry were both removed or both renamed.
    The deep link `/tortoise/docs#storage` is pinned by
    `test_the_storage_section_is_the_notices_destination_and_is_advertised`
    below, which names the anchor the notices ship (#14066 review, P2).
    """
    html = _read(DOCS)
    hrefs = set(re.findall(r'href="#([^"]+)"', html))
    ids = set(re.findall(r'id="([^"]+)"', html))
    assert hrefs, "website/docs.html has no in-page anchors — did the TOC get removed?"
    assert hrefs <= ids, f"website/docs.html anchors with no target: {sorted(hrefs - ids)}"


# ── #14013: the page the working-set capacity notices link to ────────────────
#
# `tortoise/capacity_notice.py` (#13998) ships `STORAGE_DOCS_URL` =
# `https://premiselabs.co/tortoise/docs#storage` and names this section as its
# destination. Three things make that link honest rather than merely live:
#
#   1. the anchor exists, and the TOC advertises it (else it is an orphan);
#   2. the section keeps the notices' NON-TECHNICAL voice — the whole point of
#      the link is the reader who does not know the architecture, and internal
#      vocabulary (`allowance`, `eviction`, `tier`, `FalkorDB`, `working set`)
#      is a vocabulary that email is explicitly guarded against;
#   3. it states what is NOT lost, because the notices' promise is that the
#      record holds everything. A page that only describes degradation is a
#      page that undoes the email it was written for.
#
# The claims themselves are the durable ones `#13989` records — true before and
# after the tracker's strands land, because the sections outside the fast layer
# are fetched when searched. Nothing here quotes a rate or a limit, so nothing
# here can rot when `product/pricing.json` changes.


#: Attributes that remove a heading's right to BOUND, and for which reader.
#: `style` (`display:none`) hides it from every reader, and `aria-hidden` removes it
#: from the ACCESSIBILITY TREE while leaving it visible on screen (MDN: "does not
#: visually hide them; CSS is required for visual hiding") — so a reader who never
#: meets the heading meets the copy under it as part of the section, and the boundary
#: does not exist for them either. Both are decided by the MARKUP's own attribute, which
#: is what a static guard can read.
#: ⛔ AND THEY ARE READ ON THE WHOLE HEADING SUBTREE, not the heading's start tag
#: (#14229 review, measured): `<h2 id="storage-limits"><span style="display:none">
#: Limits</span></h2>` is advertised, invisible, and cut the slice while every pin
#: stayed green — the attribute that removes the boundary was ONE LEVEL DOWN. A
#: descendant `style` can also PAINT (a `::after` claim inside the heading), which is
#: the same escape from the other side: refusing the bound brings it into the slice,
#: where `_attributes_outside_allowlist` reds it loudly.
#: ⛔ `hidden` IS NOT HERE, and its absence is measured rather than a relaxation:
#: `_unmet_by_reader` already returns True for it, so `_record_heading` never records
#: such a heading and it can never be a bound candidate at all —
#: `extract_heading_positions('<h2 id="x" hidden>T</h2>') == []`. A member no document
#: can reach decides nothing, and it told the next reader `hidden` needed this pin when
#: the parser had already refused it a layer up (#14229 review). On a DESCENDANT it
#: needs no member either: its text is not admitted by the painted-text rule, so the
#: heading reports no visible text and is refused by the other half of the same test.
#: `class` is excluded for the measured reason stated at `_heading_may_bound_the_slice`.
_STATIC_HIDING_ATTRIBUTES = frozenset({"style", "aria-hidden"})


def _heading_may_bound_the_slice(
    offset: int, boundary_facts: dict[int, tuple[bool, bool]],
) -> bool:
    """Whether the `<h2>` at `offset` is one a reader MEETS as a slice boundary.

    ⛔ THE TEST IS THE HEADING'S OWN SUBTREE, AND IT IS KEYED BY THE HEADING — not
    by a source LINE, which is what this carried until #14229's second review and
    what made two headings on one line each other's evidence. Measured: with
    `<h2 id="storage-limits">Limits</h2><h2 id="storage-draft" aria-hidden="true">
    Draft</h2>` on ONE line, the line-keyed version refused the VISIBLE, advertised
    heading (whose copy is byte-identical to the fixture), the slice ran long and
    `_assert_storage_section_reviewed` reddened on a page whose boundary was correct —
    the false-red direction this file refuses `class` for. `extract_heading_boundary_
    facts` reports `(offset, has_visible_text, carries_a_hiding_attribute)` per
    heading, so the question is about the heading the caller asked about.

    ⛔ AND BOTH HALVES ARE MEASURED, EACH ON ITS OWN ROW, because each closes an escape
    the other cannot see (#14229 review):

    * `has_visible_text` — `<h2 id="storage-limits"></h2>`, a comment-only spelling,
      and `<span hidden>Limits</span>` are all advertised, all render no line a reader
      reads, and each cut the slice while the claim under it stayed outside every pin;
    * `carries_a_hiding_attribute` — `style`/`aria-hidden` on the `<h2>` or ANY
      DESCENDANT. `style` also does not remove the text, so the first half cannot
      see it; `aria-hidden` leaves the text on screen, so it cannot either.

    The direction is deliberately the LOUD one in both: a boundary a reader cannot see
    must not decide what was reviewed, so the slice runs LONG and the offending
    attribute or copy enters the pinned region.

    ⛔ THE ADJACENCY AND WRAPPED-SPELLING HISTORY, kept because both were real and
    neither is a reason for a window. The ±1 neighbourhood this carried was justified
    by "a legal start tag may wrap: `<h2\n  id="x"\n  style="display:none">` puts the
    offending attribute on a different line from the `id`" — ⛔ WHICH THE PARSER
    FALSIFIES: `_record_attributes` stamps every attribute of a tag with the TAG's
    position, so `extract_element_attributes('<h2\n  id="x"\n  style="display:none">')`
    returns BOTH attributes on the tag's line, for any wrapping depth (measured). The
    neighbourhood could therefore only ever match a DIFFERENT heading — measured, with
    `<h2 id="storage-draft" hidden>` on the line after a visible
    `<h2 id="storage-limits">`, the visible heading stopped bounding, the slice ran
    long and the wording golden reddened on a page whose boundary was correct. Neither
    a window nor a line is needed now: the facts belong to the heading itself.

    `test_a_visible_heading_beside_a_hiding_one_still_bounds_the_storage_slice` pins
    the adjacency, the wrapped spelling AND the same-line sibling; and
    `test_an_advertised_heading_a_reader_never_meets_does_not_bound_the_storage_slice`
    pins both halves of the visibility test, one row each.
    """
    has_visible_text, carries_a_hiding_attribute = boundary_facts[offset]
    return has_visible_text and not carries_a_hiding_attribute


def _docs_section(anchor: str = "storage", *, page: str | None = None) -> str:
    """The markup of `docs.html`'s `#<anchor>` section, up to the next ADVERTISED `<h2`.

    A slice rather than the whole page: the vocabulary rule applies to what a
    reader arriving at the anchor meets, and holding the page's deliberately
    technical sections (`How it works`, the API reference) to it would be wrong.

    ⛔ The slice ends at the next heading the TOC ADVERTISES, not at the next
    `<h2 id=` of any kind. Bounded at any `<h2 id=`, a sibling heading nobody
    linked — plus everything under it — sits outside the slice and escapes the
    golden and vocabulary pins; measured, a whole
    `<h2 id="storage-limits">` carrying `FalkorDB`, `allowance`, `tier` and
    "permanently deleted" passed all five (#14066 review round 3). A heading a
    reader can only reach through the TOC is a heading that had to be added
    deliberately; one that is not advertised stays inside the pinned text.

    ⛔ THE BOUND IS READ OUT OF THE PARSED TREE, not matched in the raw source.
    A raw `<h2` pattern is wrong in both directions, and the fail-open half is
    the one that ships a claim nobody reviewed: a heading parked in a comment or
    in an inert `<template>` is not a heading a reader meets, so cutting the
    section there left everything after it — `<h2 id="quickstart">` inside a
    `<template>` plus a paragraph naming `FalkorDB`, the `allowance` and a `tier` —
    outside the golden, the vocabulary pin and the attribute allowlist at once,
    all three green (measured, #14073 review). The other direction is the
    false red this PR's first commit fixed: a heading carrying extra attributes,
    in any order, with or without spaces around `=`.
    `extract_heading_positions` is the parser's answer (the same render-fidelity
    seam `tests/_html_links.py` gives the id and attribute guards), so both
    halves go away with the raw pattern rather than being patched one spelling at
    a time.

    ⛔ The anchor's absence is asserted BEFORE any offset is used. A bare `index`
    raises `ValueError`, which surfaces as an ERROR in the tests that call this
    helper and names no cause — the opposite of what a guard owes a reader whose
    edit moved the section (#14066 review round 1).

    ⛔ AND SO IS THE INDEX — `extract_anchors_within` reads the TOC block and its
    entries out of the same parsed tree, because the RAW-SOURCE index was the last
    fail-open in this bound: a `<div class="toc">` in a comment or an inert
    `<template>` was matched as the TOC, so a decoy block REPLACED the advertised
    set and an un-advertised heading could bound the slice again — measured with
    `<!-- <div class="toc"><a href="#storage-limits">…` above the real TOC plus a
    `FalkorDB`/`allowance`/`tier`/"permanently deleted" paragraph at the boundary:
    the golden, the vocabulary pin and the allowlist all green while the claim
    rendered (#14073 review). The false red went with it — `href='#quickstart'`,
    `href=#quickstart` and `class="toc wide"` are legal, rendered spellings that
    ran the slice past the section or fired this bound's own "no TOC block"
    assertion. Exactly one rendered TOC block is required (asserted below): two is
    an index ambiguity, and the second could advertise a heading the reviewed one
    does not.

    ⛔ AND WHICH ENTRY THE ONE BLOCK ADVERTISES IS A URL QUESTION, not a string
    prefix — the same false-red family, two members further on. `href="#x"` was
    matched literally, so an entry the URL parser strips to a fragment
    (`href=" #quickstart"`, or a value wrapped in newlines — both legal, both
    rendered) and an ABSOLUTE link to this same page (`/tortoise/docs#quickstart`,
    `/docs#quickstart`, the full-URL form) were dropped from the advertised set.
    The slice then ran PAST the section and the golden reddened on copy nobody
    touched (#14073 review, measured). `_fragment_this_page_advertises` decides it
    with the module's own route map, so the spellings are one question rather than
    a list; a host outside `SITE_HOSTS` is never same-page, because accepting a
    foreign host would GROW the advertised set — the fail-open direction. The
    remaining declared residual: a TOC entry pointing at ANOTHER page's fragment is
    not advertised here either, so if a reviewer ever moves an entry's target the
    slice runs long and the golden reds loudly instead of silently shrinking.

    ⚠️ THE DECLARED RESIDUAL, stated rather than left to be derived: a heading that
    IS advertised — `<h2 id="storage-limits">` plus a TOC entry for it — cuts the
    slice there, and everything under it leaves all three pins. That is the design (see
    the bound rule above: a heading a reader can reach through the TOC had to be added
    deliberately), and it is pinned as a live behaviour by
    `test_the_storage_slice_bound_catches_the_heading_it_exists_for`, so it cannot
    be mistaken for an accident.

    ⛔ BUT "ADVERTISED" IS NOT "VISIBLE", and until round 7 this paragraph read
    reachability as if it were: a TOC entry plus
    `<h2 id="storage-limits" style="display:none">` is advertised AND invisible, so it
    cut the slice while a reader landing at `#storage` met the paragraph under it AS
    PART OF THE SECTION — measured, a `FalkorDB`/`allowance`/`tier` "permanently
    deletes" claim painted inside the `#storage` band with all pins green. A heading
    may therefore bound only if it does not remove its own boundary:
    `_heading_may_bound_the_slice` refuses `style` and `aria-hidden` (`hidden` is
    refused one layer up by `_unmet_by_reader`, so it never reaches this bound), and on
    that violation the slice runs LONG (loud) instead of stopping at an invisible line.
    The `class`-via-stylesheet case is NOT refused — see
    `_heading_may_bound_the_slice` for why that is a measured limit rather than an
    oversight.

    ⛔ AND "ITS OWN BOUNDARY" IS THE WHOLE SUBTREE, not the start tag — the third
    escape in this family (#14229 review, measured):
    `<h2 id="storage-limits"></h2>`, a comment-only spelling, and
    `<span style="display:none">Limits</span>` as the heading's ONLY content are all
    advertised, all render no line a reader reads, and each cut the slice with the
    claim under it outside every pin. So the test is the subtree's TEXT and its
    descendants' attributes — a heading a reader never meets must not decide what
    was reviewed, and the direction stays loud in both halves.

    What the residual still does NOT guarantee: that an extra VISIBLE heading was
    reviewed against the plain-language rule. Nothing here requires `#storage` to be a
    single level-2 heading, so a reviewer of a PR that adds one is the only thing
    between an advertised sub-heading and an unreviewed claim.

    `page` exists for the mutation controls below, which drive a MUTANT page
    through this real bound rather than through a reimplementation of it.
    """
    html = _read(DOCS) if page is None else page
    headings = extract_heading_positions(html)
    start = _h2_offset(html, anchor)
    assert start >= 0, _missing_heading_diagnosis(html, anchor)
    # ⛔ THE INDEX IS THE TOC BLOCK, not every `href="#…"` in the document. Read
    # off the whole page, one inline cross-reference anywhere (a "see also", a
    # footer jump) promotes a heading to a slice boundary without a deliberate
    # index decision — measured: an un-advertised `<h2 id="storage-limits">`
    # carrying `FalkorDB`/`allowance`/`tier`/"permanently deleted" escaped every
    # pin once one `<a href="#storage-limits">` existed elsewhere (#14066 review
    # round 4).
    #
    # ⛔ AND THE BLOCK IS READ OUT OF THE PARSED TREE TOO — the last raw-source read
    # in this bound, and it failed open the same way the heading bound did. A
    # `<div class="toc">` parked in a comment or an inert `<template>` was matched
    # as the TOC, so a decoy block REPLACED the advertised set: with
    # `<!-- <div class="toc"><a href="#storage-limits">…` above the real TOC and an
    # un-advertised `<h2 id="storage-limits">` plus a
    # `FalkorDB`/`allowance`/`tier`/"permanently deleted" paragraph at the section
    # boundary, all three pins stayed green while the claim rendered (#14073
    # review, measured). The other direction reddened the golden on a correct
    # page: `href='#quickstart'`, `href=#quickstart` and `class="toc wide"` are all
    # legal, all rendered, and the first two ran the slice past the section while
    # the third fired the "no TOC block" assertion below.
    #
    # EXACTLY ONE rendered TOC block, asserted rather than assumed: with two, which
    # one is the index is an ambiguity a reader cannot resolve from the copy, and a
    # second block could advertise a heading the reviewed one does not — a
    # boundary the reviewed page never made a decision about.
    tocs = extract_anchors_within(html, tag="div", class_token="toc")
    assert len(tocs) == 1, (
        f"website/docs.html has {len(tocs)} `<div class=\"toc\">` block(s) in the "
        f"document tree; the slice is bounded by the headings the TOC "
        f"advertises, so it needs exactly one. With none, nothing bounds the "
        f"slice and it becomes the rest of the page; with two, which block is the "
        f"index is undecidable, and the second could advertise a heading the "
        f"reviewed one does not."
    )
    advertised = {
        fragment
        for href in tocs[0]
        if (fragment := _fragment_this_page_advertises(
            href, DOCS, base=_document_base_url(html, DOCS),
        )) is not None
    }
    # ⛔ THE BOUND PREMISE, MEASURED (round 7): a heading that cannot serve as a boundary
    # — `style` hides it from every reader, `aria-hidden` removes it from the
    # accessibility tree, and a subtree that paints no text is a line no reader reads —
    # cannot decide what a reader at the anchor meets. `class` is deliberately NOT in
    # that set — it hides only through a stylesheet rule this parser cannot compute, and
    # refusing it would red the round-5 control that moves `class` in front of `id` on a
    # correct page — and `hidden` is not either, because `_unmet_by_reader` already
    # refuses such a heading one layer up (and a `hidden` DESCENDANT leaves the heading
    # with no visible text, which the same test refuses). See
    # `_heading_may_bound_the_slice`.
    #
    # ⛔ ONE PARSE FOR EVERY CANDIDATE, and KEYED BY THE HEADING rather than by a source
    # line (#14229 review): `extract_heading_boundary_facts` walks the tree once and
    # reports the subtree facts WITH each heading's offset, so two headings on one line
    # cannot be each other's evidence.
    boundary_facts = {
        heading_offset: (has_visible_text, carries_a_hiding_attribute)
        for heading_offset, has_visible_text, carries_a_hiding_attribute in
        extract_heading_boundary_facts(
            html, hiding_attributes=_STATIC_HIDING_ATTRIBUTES,
        )
    }
    for heading_id, offset in headings:
        if offset <= start:
            continue  # the section's OWN opening tag
        if heading_id in advertised and _heading_may_bound_the_slice(
            offset, boundary_facts,
        ):
            return html[start:offset]
    return html[start:]


def _browser_origin(url: str) -> tuple[str, int | None] | None:
    """`(host, non-default port)` for a WEB url, browser-normalized, or None.

    ⛔ THE SAME-PAGE QUESTION IS "DOES THIS ENTRY LAND ON THIS DOCUMENT", not a
    string comparison of `netloc`: `HTTPS://PREMISELABS.CO/tortoise/docs`,
    `https://premiselabs.co:443/…` and `https://someone@premiselabs.co/…` all reach
    this page in a browser, and an equality test on the raw netloc refused every one
    of them — a NEW false red this function introduced, the same family as the
    relative-href one it was fixing (#14073 review, measured). The default port is
    dropped, the host is lowercased, and userinfo is ignored (it changes the origin
    a script sees, not the document a navigation lands on). A host that differs in
    MORE than case (a trailing dot, say) is still a different host.

    ⛔ AND THE SCHEME IS NORMALIZED, deliberately — `http://` and `https://` to the
    canonical host are ONE answer here, because a TOC entry spelled `http://` is
    upgraded/redirected onto this very page, so refusing it shrinks the advertised
    set and reddens the golden on copy nobody touched (#14073 review: the previous
    revision silently reversed this, and no row said which way it should be). The
    scheme still gates WHICH urls can name a document at all: `ftp:`/`mailto:`/
    `javascript:` return None, because an anchor to them does not navigate this page.
    """
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:  # an unparsable port or IPv6 literal names no document here
        return None
    scheme = (parsed.scheme or "https").lower()
    if scheme not in ("http", "https"):
        return None
    default = 443 if scheme == "https" else 80
    return ((parsed.hostname or "").lower(), None if port == default else port)


#: An OPTIONAL scheme followed by TWO OR MORE slashes and the authority text: the shape
#: WHERE WHATWG AND RFC 3986 DISAGREE.
_AUTHORITY = re.compile(r"\A(?:[A-Za-z][A-Za-z0-9+.\-]*:)?/{2,}([^/?#]*)")


def _whatwg_authority(target: str) -> str | None:
    """The authority a browser parses out of `target`, or None when it has none.

    ⛔ `urljoin` IS RFC 3986, NOT WHATWG. With THREE or more leading slashes — or
    `scheme:///path` — the extra slashes are PATH characters to `urljoin`, which then
    keeps the base's host, while a browser SKIPS them and parses the next segment as
    the AUTHORITY: `///docs` is `https://docs/`, `https:///tortoise/docs` is
    `https://tortoise/docs` (verified against Node's WHATWG resolver). Treating the
    two as agreeing advertised a boundary for an entry that leaves the site — the
    fail-open direction, and the exact shape this module already had to fix once in
    `is_blog_entry` (#3962). This parses the authority the way the browser does, so
    the comparison below cannot be fooled by a slash count.
    """
    found = _AUTHORITY.match(target)
    return found.group(1) if found else None


def _document_base_url(markup: str, document: Path) -> str:
    """The BASE URL that relative and fragment-only links resolve against.

    The canonical page URL unless the document declares a `<base href>`, in which
    case the browser resolves that against the canonical URL and every later href
    resolves against the result (#14073 review, P2). The two are the same string
    on a page with no `<base>`, which is every page today — so this changes no
    current answer, and it removes the case where the predicate contract ("the
    heading id EXACTLY when a browser navigating that href lands on this page")
    was unsatisfiable rather than merely wrong.

    ⛔ AN UNPARSABLE `<base href>` FALLS BACK TO THIS PAGE, AND THIS `except` IS
    THE ONLY PLACE THAT FALLBACK IS APPLIED — it is not dead code, and deleting
    it as unreachable turns one page edit into an ERROR naming no cause. The
    HTML Standard's "set the frozen base URL" step (`4.2.3 The base element`)
    sends a `base` whose href FAILS TO PARSE to the document's FALLBACK base
    URL — this page — so the browser behaves as if no `<base>` were declared:
    `#storage` still lands here and the heading is still advertised. `urljoin`
    raises `ValueError` for a bracketed host the stdlib cannot read
    (`http://[::1/`, measured), which is the parse failure this catches, and a
    `ValueError` escaping from here would be indistinguishable from a broken
    helper. The predicate's own `ValueError` arm is for the LINK TARGET, never
    for the base.

    ⚠️ NOT MODELLED, and the reason not to read this paragraph as "the whole
    frozen-base-URL step": the same step also sends a `base` whose URL parsed
    but whose scheme is `data:` or `javascript:`, or which the document's CSP
    forbids, to that fallback. Those three stay OUT of scope deliberately — no
    page here declares a `<base>` at all, and each would only ever make this
    guard refuse a page a browser renders correctly (the fail-closed direction),
    so they are a note rather than a work item.
    """
    canonical = _canonical_page_url(document)
    declared = extract_first_base_href(markup)
    if declared is None:
        return canonical
    try:
        return urljoin(canonical, declared.strip(" \t\n\r\f\v"))
    except ValueError:  # a bracketed host the stdlib cannot parse
        return canonical


def _fragment_this_page_advertises(
    href: str, document: Path, *, base: str | None = None,
) -> str | None:
    """The heading id `href` advertises ON `document`, or `None`.

    `base` is the BASE URL the document links resolve against (`_document_base_url`)
    — the caller passes it because only the caller has the markup. Omitted, it is
    the canonical page URL, which is what a page with no `<base href>` does.

    See `_docs_section` for why a literal `href.startswith("#")` is the wrong test.
    Three questions, in this order:

    1. WHICH FRAGMENT — the URL parser strips leading/trailing C0-or-space from an
       href value, so `" #quickstart"` and a value wrapped in newlines are the
       same URL as `"#quickstart"`. `urlparse` gives the fragment after that
       stripping, which is the tokenizer's work, not a pattern's.
    2. WHICH URL — resolved with `urljoin` against the page's BASE URL (`base`,
       from `_document_base_url`), so a RELATIVE href is rooted where a browser
       roots it. Rooting it at `/` instead accepted `tortoise/docs#quickstart`, which a
       browser resolves to `/tortoise/tortoise/docs` — a 404 — and REJECTED
       `../docs#quickstart`, which is `/docs` and serves this page (the loud
       direction, but wrong; #14073 review, measured both).

       ⛔ THE BASE IS NOT THE PAGE URL WHENEVER A `<base href>` IS PRESENT, and
       that is the one place the two answers differ: with `<base href="/">` the
       fragment-only `#quickstart` leaves this page entirely, and with a foreign
       base it leaves the site — so resolving against the page URL advertised a
       landing no browser makes (#14073 review, P2). It is RESOLVED here rather
       than refused, because `urljoin` then produces the URL the browser really
       navigates to, and the origin/document checks below judge it on its merits.
    3. WHICH DOCUMENT — must be the same-origin canonical URL (`_canonical_page_url`)
       AND resolve to this file through the module's route map
       (`_document_for_loc`), so `/docs` (the retired alias), `/tortoise/docs`,
       `docs.html` and `?query#frag` are ONE question instead of a second list of
       spellings that rots at the next route change.

    ⛔ THE ORIGIN IS THE CANONICAL ONE OR NOTHING — and an ORIGIN, not a netloc
    string: `///docs#storage-limits` is `https://docs/` to a browser (extra slashes
    are skipped and the next segment becomes the HOST) even though `urljoin` reads
    the third slash as a path character and keeps this page's host; the dashboard
    host `app.premiselabs.co` is in `SITE_HOSTS` but is a SEPARATE Pages project
    that serves no docs at all, and `_document_for_loc` is host-blind below the
    root, so a path-only rule accepts it. Either would GROW the advertised set: a
    larger set, a shorter slice, the fail-open direction (#14073 review, all
    measured). Default ports, host case and userinfo are normalized away, because a
    browser sends those to this page — they are the false-red side. A retired host
    (`tortoise.premiselabs.co`) or a foreign scheme is therefore not advertised: the
    slice runs long and the golden says so, which is the direction a guard wants to
    be wrong in.

    ⛔ AND THE FRAGMENT IS PERCENT-DECODED, because that is what a browser matches
    against an element's `id`: `#quick%73tart` navigates to `#quickstart`, and
    comparing the raw fragment dropped an entry that DOES reach the heading — the
    same false red. `unquote` leaves `+` alone, so this is not query-string decoding.
    """
    target = href.strip(" \t\n\r\f\v")
    if not target:
        return None
    try:
        parsed = urlparse(target)
    except ValueError:
        # A bracketed host the stdlib cannot parse (`//[::1`, `//[]`) must REFUSE, not
        # raise: this function's contract is "the heading id, or None", and a
        # `ValueError` out of `_docs_section` is an ERROR naming no cause. The guard
        # covers every parse below too — the sibling `try`s are the same rule, applied
        # where a different spelling reaches a different parser (#14073 review, the
        # `//[::1` spelling still raised after the first fix).
        return None
    if not parsed.fragment:
        return None
    # ⛔ A BACKSLASH IN THE AUTHORITY OR THE PATH IS REFUSED, NOT INTERPRETED. In a
    # special URL a browser treats `\` as `/` there — it TERMINATES the authority and
    # delimits the path — so `https://evil.example\@premiselabs.co/tortoise/docs#x`
    # has HOST `evil.example` to a browser while every Python parser reads
    # `premiselabs.co` (as userinfo). The two disagree about what the URL IS, so there
    # is no honest translation: refusing keeps the site out of the advertised set, and
    # accepting let a foreign host advertise a heading on this page (#14073 review,
    # measured end-to-end with the pinned `storage-limits` shape).
    #
    # ⛔ AND IT STOPS AT THE `?`, for the same reason the `;` rule below tests the PATH:
    # WHATWG converts `\` only in the authority and path states, so a backslash in the
    # QUERY is left verbatim and both parsers agree the document is this one —
    # `/tortoise/docs?q=a\b#quickstart` DOES land on `#quickstart`. Refusing the whole
    # pre-fragment text refused that spelling: a false red, the family this function
    # exists to close (#14073 review, measured).
    if "\\" in target.split("#", 1)[0].split("?", 1)[0]:
        return None
    # ⛔ AND A `;` IN THE PATH IS REFUSED BEFORE RESOLUTION, because `urljoin` itself
    # loses a trailing `;` (it round-trips through `urlparse`, whose `.params` is not
    # re-emitted when empty): `/tortoise/docs;` was joined to `/tortoise/docs` and read
    # as this page, though the middleware 404s `/docs;` exactly as it 404s `/docs;x`
    # (`PRODUCT_PAGES` holds bare names). A `;` in the QUERY is untouched — it does not
    # change which document is requested — so the test is on the PATH (#14073 review,
    # measured end-to-end with the pinned `storage-limits` shape).
    if ";" in urlsplit(target).path:
        return None
    canonical = _canonical_page_url(document)
    resolution_base = canonical if base is None else base
    page_origin = _browser_origin(canonical)
    # ⛔ THE AUTHORITY THE BROWSER SEES, before `urljoin` has its say: for a
    # two-or-more-slash spelling the two resolvers disagree (see `_whatwg_authority`).
    authority = _whatwg_authority(target)
    if authority is not None:
        # …with the href's OWN scheme, so `http://host:80/…` is its own default port
        # rather than compared against https's (an `https://` probe would read :80 as
        # non-default and refuse a spelling the browser accepts — #14073 review).
        probe_scheme = (parsed.scheme or "https").lower()
        if _browser_origin(f"{probe_scheme}://{authority}") != page_origin:
            return None
    try:
        resolved_url = urljoin(resolution_base, target)
    except ValueError:
        return None
    # ⛔ THE FRAGMENT IS CUT TEXTUALLY. Rebuilding the resolved URL through
    # `urlparse(...)._replace(fragment="").geturl()` DROPS a trailing `;` — it lands
    # in `.params` and is not re-emitted — so `/tortoise/docs;` reached
    # `_document_for_loc` as `/tortoise/docs` and a URL the middleware 404s read as
    # this page: the same `;params` defect as `;x`, one spelling over (#14073 review,
    # measured; `_document_for_loc` refuses `docs;` when asked directly).
    document_url = resolved_url.split("#", 1)[0]
    if _browser_origin(document_url) != page_origin:
        return None
    if _document_for_loc(document_url) != document:
        return None
    return unquote(parsed.fragment)


#: An `<h2 …>` opening tag, its attribute text captured whole.
#:
#: ⛔ CONTROL-ONLY. `_docs_section` reads headings out of the parsed tree
#: (`extract_heading_positions`), so nothing on the live path matches a tag in the
#: raw source. This pattern survives because the mutation controls below must be
#: able to compute what a RAW-SOURCE bound would have done — which is the whole
#: point of a control, and the reason this is not the implementation. The id it
#: feeds to `_h2_id` is parsed too, so a control measures the raw-source BOUND and
#: not a second, independent pattern defect (#14073 reviews rounds 5 and 6).
_H2 = re.compile(r"<h2\b([^>]*)>")

#: The words the storage section may not use, and what the notices say instead.
#: Shared with the mutation control so the two cannot disagree about the rule.
_BANNED_VOCABULARY = {
    "allowance": "the notices say 'fast-search capacity'",
    "evict": "the notices say items 'step out of' the fast layer",
    "tier": "capacity is offered first; a plan change is never the answer",
    "falkor": "the notices say 'a graph database'",
    "working set": "plain phrasing is 'the fast layer'",
    "hot cache": "plain phrasing is 'the fast layer'",
}


def _h2_id(attribute_text: str) -> str | None:
    """The `id` a browser would see on `<h2 {attribute_text}>`, or `None`.

    ⛔ PARSED, NEVER PATTERN-MATCHED — the same lesson as the attribute allowlist,
    and this was the last place it was not applied. A pattern over the whole
    attribute text also finds an `id=` written INSIDE another attribute's quoted
    VALUE: `<h2 class='see id="quickstart"'>Limits</h2>` has NO id to a browser,
    yet a pattern reads one and the slice would end at a heading that never
    bounded anything — reproduced with a contradicting claim underneath it, all
    six pins green (#14073 review round 6). Feeding the isolated tag to the repo's
    one render-fidelity parser gives the id a browser would see, or none.

    ⛔ CONTROL-ONLY, like `_H2`: `_docs_section` no longer bounds on attribute text
    at all, it bounds on parsed `<h2>` positions (`extract_heading_positions`), so
    a heading in an inert region cannot end the slice either (#14073 review round
    5). This function now exists so `_slice_bound_by_raw_headings` can reproduce
    the RAW-SOURCE bound faithfully — with the id parsed, so the control isolates
    the raw-bound defect instead of re-testing the pattern one.
    """
    ids = [element_id for element_id, _line in
           extract_element_ids(f"<h2 {attribute_text}></h2>")]
    return ids[0] if ids else None


def _h2_offset(html: str, anchor: str) -> int:
    """Offset of the `<h2>` carrying `id="<anchor>"`, or -1 when there is none.

    Read off the parsed tree, so a `<h2 id="storage">` inside a comment or an
    inert `<template>` is not this section (#14073 review).
    """
    for heading_id, offset in extract_heading_positions(html):
        if heading_id == anchor:
            return offset
    return -1


def _missing_heading_diagnosis(html: str, anchor: str) -> str:
    """Why `_docs_section` has no bound — said truthfully, not as "the heading is gone".

    `_h2_offset` answers `-1` in TWO different worlds, and they are not the same
    defect (#14073 review, P2):

    * the heading is really ABSENT from the document — it was renamed or moved, so
      the notices' link text is broken and re-adding it is the fix;
    * the heading is PRESENT in the source and the reader model REFUSED it — a wedged
      document, an ancestor a reader does not meet, or a `<table>` whose foster
      parenting reorders the tree against the source (`_unmet_by_reader`).

    Reporting the second as "has no `<h2 id=…>`" sends the next lane hunting for a
    deleted heading that is right there in the file — and it is exactly the state
    this guard's own loud rules are designed to produce (an over-refusal keeps the
    slice LONG). The two states are therefore named separately.
    """
    in_source = re.search(
        rf"<h2\b[^>]*\bid\s*=\s*([\"']?){re.escape(anchor)}\1(?![\w-])", html
    )
    if in_source is None:
        return (
            f"website/docs.html has no `<h2 id=\"{anchor}\">`. "
            f"`capacity_notice.STORAGE_DOCS_URL` (#13998) points at "
            f"`/tortoise/docs#{anchor}`, so moving or renaming this section silently "
            f"breaks the notices' link text."
        )
    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    if parser.wedged_reason:
        why = (
            f"the document WEDGED ({parser.wedged_reason}), so no rendered position "
            f"is knowable from here"
        )
    else:
        why = (
            "an open ancestor makes it unmeetable (`_unmet_by_reader`: an unrendered "
            "ancestor, an open `<select>`/`<option>`, or a `<table>` whose foster "
            "parenting reorders the tree against the source)"
        )
    return (
        f"website/docs.html HAS `<h2 id=\"{anchor}\">` in its source "
        f"(at offset {in_source.start()}), but this model records no rendered "
        f"position for it: {why}. The heading is not missing — the bound REFUSED it, "
        f"which keeps the slice long on purpose. Fix the markup (or the refusal); "
        f"do not re-add a heading that is already there."
    )


def _banned_vocabulary(markup: str) -> list[str]:
    """The banned words `markup` carries, normalised the way the pin normalises.

    ⛔ VISIBLE TEXT ONLY, and that is a deliberate narrowing of what the inline
    version of this pin scanned (#14073 review). Bare markup text matches words
    inside tag names and attribute values too, and none of those reach the
    reader: `class="hot-cache"` and `href="#tier"` used to redden the pin for
    strings no browser renders. What the narrowing must NOT be read as is "the
    attributes are covered elsewhere": `_attributes_outside_allowlist` refuses
    every attribute beyond `id`, `class` and `href` BY NAME, which leaves `href`
    VALUES — a destination a reader acts on — judged by nothing here, so the
    section's destinations are pinned separately by
    `_REVIEWED_SECTION_HREFS`. Widening the allowlist later means re-widening
    this scan in the same change.
    """
    folded = re.sub(r"[^a-z0-9]+", " ", _visible_text(markup).lower())
    return sorted(word for word in _BANNED_VOCABULARY if word in folded)


def _attributes_outside_allowlist(markup: str) -> list[str]:
    """Attribute names `markup` carries that the reviewed section does not need.

    ⛔ PARSED, NEVER PATTERN-MATCHED. The source pattern this replaced
    (`\\s([a-zA-Z][a-zA-Z-]*)=`) required the name to be immediately followed by
    `=` and to hold only letters and `-`. Three spellings a browser renders in
    full walked past it — `title = "…"` (whitespace around `=` is legal),
    `data-30day="…"` and `data_del="…"` (names may hold digits and `_`) — each
    carrying a reader-facing claim the golden cannot see, because the golden
    compares VISIBLE text. `tests/test_contact_form.py:1202-1209` records the same
    lesson for the sibling surface: quoting, entity decoding, comments and
    script/style blocks are the parser's job, not a pattern's. This reads the
    repo's one render-fidelity parser (`tests/_html_links.py`).

    ⛔ AND A `<noscript>` REGION IS REFUSED, NOT CLEARED. It is the one suppressed
    region whose answer flips with the SCRIPTING MODE — text with JS on, HTML
    content with JS off (MDN, quoted on `_AnchorHrefParser._MODE_DEPENDENT`) — so
    the parser cannot say whether a reader meets a claim inside it, and "no
    attribute found" there is the fail-open half of a real answer. Reporting the
    region as an offending entry is the strong form: the page reds until a human
    decides, instead of a `title` reaching a no-JS reader while this guard reports
    clean (#14073 review).
    """
    allowed = {"id", "class", "href"}
    return sorted({
        f"<{tag} {name}>"
        for tag, name, _line in extract_element_attributes(markup)
        if name not in allowed
    } | {
        f"<{tag}>" for tag, _line in extract_mode_dependent_regions(markup)
    } | {
        # ⛔ A RENDERING DIRECTIVE IS REFUSED TOO, not cleared: `<style>`/`<script>`
        # contributes no text node and no attribute, so a `content:` property can paint
        # a claim that every pin here stays green over (#14073 review round 7,
        # measured in Chromium). Refusing the section is the strong form — a directive
        # added later is a manual review instead of a silent pass.
        f"<{tag}>" for tag, _line in extract_raw_text_elements(markup)
    })


def _slice_bound_at_any_h2(page: str) -> str:
    """The slice an UNCONDITIONAL `<h2 id=` bound computes — the reverted defect.

    Used ONLY by the mutation control below, so that control can show what the
    current bound buys: with this bound the un-advertised heading and its text sit
    outside the slice and every pin stays green (#14066 review round 3).
    ⛔ Not the implementation — `_docs_section` above is.
    """
    rest = page[_h2_offset(page, "storage"):]
    bound = rest.find('<h2 id="', 1)
    return rest if bound < 0 else rest[:bound]


def _slice_bound_by_page_wide_hrefs(page: str) -> str:
    """The slice a WHOLE-PAGE `href="#…"` index computes — the round-4 defect.

    Also control-only: it exists to show that an inline cross-reference elsewhere
    on the page promotes an un-advertised heading to a boundary and escapes the
    pins, which is why the index is the TOC block (#14066 review round 4).
    """
    advertised = set(re.findall(r'href="#([^"]+)"', page))
    rest = page[_h2_offset(page, "storage"):]
    for match in _H2.finditer(rest):
        if match.start() == 0:
            continue
        if _h2_id(match.group(1)) in advertised:
            return rest[:match.start()]
    return rest


def _slice_bound_by_literal_tag(page: str) -> str:
    """The slice the PRE-#14073 implementation computed — the literal-tag defect.

    Control-only. Before this PR the section's own opening tag was the literal
    string `<h2 id="storage">`, and the end was the next `rest.find('<h2 id=', 1)`
    — a PREFIX match. Both assume `id` is the tag's FIRST attribute, so the moment
    a heading grew `class` in front of its `id` the tag stopped matching: at the
    section's own tag the pre-fix code found nothing (and reddened the anchor pin
    with a message that blamed the storage copy), and at a LATER heading it ran
    the slice on past the real boundary.

    ⛔ Not the implementation — `_docs_section` above is. This exists so the
    control below can show the defect the parsed bound removes, instead of
    asserting a property that holds for both bounds (#14073 review).
    """
    start = page.find('<h2 id="storage">')
    if start < 0:
        # The pre-fix code asserted the literal was present, so this is its red —
        # an empty slice is the faithful answer to "where does the section start".
        return ""
    rest = page[start:]
    end = rest.find("<h2 id=", 1)
    return rest if end < 0 else rest[:end]


def _slice_bound_by_raw_headings(page: str) -> str:
    """The slice a RAW `<h2` bound computed — the fail-open this PR removes.

    Control-only: `_docs_section` as it was at `2a64b42` — a source pattern over
    the raw markup, with no notion of whether a browser parses the heading it
    matched. Exists so the negative control below can show what the parsed bound
    buys instead of asserting a property both bounds already have (#14073
    review).

    ⛔ The TOC regex below is part of what this reproduces: the pre-fix bound read
    its INDEX off the raw document too, so a decoy `<div class="toc">` in a
    comment replaced the advertised set and a rendered heading could then bound the
    slice. Keeping the regex here is what lets a control measure THAT defect; it is
    not an implementation (#14073 review).
    """
    start = _h2_offset(page, "storage")
    if start < 0:
        return ""
    toc = re.search(r'<div class="toc">(.*?)</div>', page, re.S)
    assert toc is not None, "the control needs the page's `<div class=\"toc\">` block"
    advertised = set(re.findall(r'href="#([^"]+)"', toc.group(1)))
    rest = page[start:]
    for match in _H2.finditer(rest):
        if match.start() == 0:
            continue
        if _h2_id(match.group(1)) in advertised:
            return rest[:match.start()]
    return rest


def test_the_storage_section_is_the_notices_destination_and_is_advertised() -> None:
    """The anchor the email links to exists on the page AND appears in its TOC.

    ⚠️ This pins the LITERAL both sides must agree on, plus the notices' own
    constant once it is importable. `tortoise/capacity_notice.py` lands with the
    unmerged PR `#14038`, so at this head nothing in the tree knows the URL the
    email ships — the `importorskip` arms below become the real pin on the day it
    merges, so a fragment change there cannot quietly stop landing here.
    """
    html = _read(DOCS)
    assert 'id="storage"' in html, (
        "website/docs.html has no `id=\"storage\"`. `capacity_notice.STORAGE_DOCS_URL` "
        "points at `/tortoise/docs#storage` (#13998), so the notice's link text "
        "nowhere lands."
    )
    assert 'href="#storage"' in html, (
        "the storage section is an orphan: it has an id but no TOC entry, so the "
        "only way to reach it is the email itself."
    )

    capacity_notice = pytest.importorskip(
        "tortoise.capacity_notice",
        reason="lands with #13998's PR #14038; until then the literal above is the pin",
    )
    url = urlparse(capacity_notice.STORAGE_DOCS_URL)
    assert url.path == "/tortoise/docs", (
        f"the notices now link to {url.path}, which is not the docs page this "
        f"section lives on."
    )
    assert url.fragment == "storage", (
        f"the notices link to #{url.fragment}, but the section's id is 'storage' — "
        f"the link text would land on the page's top."
    )


def test_the_storage_section_uses_no_internal_vocabulary() -> None:
    """Plain language, per #14013's contradiction test.

    These are the words the notices themselves are guarded against. A reader who
    clicks *\"How Tortoise stores and searches your data\"* arrives without the
    architecture; naming `FalkorDB`, a `tier`, an `allowance` or `eviction` hands
    them the internal model instead of the explanation.

    ⛔ THE SECTION IS NORMALISED FIRST, and the vendor key is `falkor`, not
    `falkordb`. A raw substring test on the lowercased markup is defeated by
    every spelling a writer actually uses — `Falkor DB`, `working-set`,
    `hot-cache` all passed a first version of this pin (measured, #14066 review
    round 2). Punctuation collapses to a space and `falkor` subsumes
    `falkordb`, `falkor db` and the bare product name the owner himself uses.

    ⛔ THE SCAN IS OVER VISIBLE TEXT, which is NARROWER than an earlier version of
    this pin that folded the raw markup — declared here so a reader does not have
    to infer it from the body (#14073 review, P2). Tag and attribute text left
    the scan: `class="hot-cache"` used to red this pin for a string no browser
    renders. What that does NOT cost is coverage of an attribute NAME a reader
    meets, because `_attributes_outside_allowlist` refuses every attribute on the
    section beyond `id`, `class` and `href` — the only attributes that carry
    reader-facing TEXT (`title`, `aria-*`, `alt`) are rejected outright.

    ⛔ IT DOES COST `href` VALUES, WHICH IS WHY A THIRD PIN EXISTS. `href` is
    admitted by name and its VALUE is what a reader acts on — hover shows it,
    clicking follows it — so an anchor retargeted to another address is invisible
    to this scan (text unchanged) and to the allowlist (name unchanged). That gap
    is closed by `_REVIEWED_SECTION_HREFS`, asserted in
    `test_the_storage_section_says_exactly_what_was_reviewed`; an earlier version
    of this docstring claimed the allowlist closed it, which was measured false
    (#14073 review). Widening the allowlist later means re-widening this scan in
    the same change.
    """
    # ⛔ THE HELPER, NOT A COPY OF IT. This pin used to re-implement the fold
    # inline while `_banned_vocabulary`'s docstring promised the caller's
    # controls were "normalised the way the pin normalises" — so the helper could
    # be reverted to the raw-markup scan the docstring says was fixed and the
    # whole suite stayed green while every slice-bound control measured the
    # reverted behaviour (measured, #14073 review round 4). One implementation.
    found = _banned_vocabulary(_docs_section())
    assert not found, (
        f"the #storage section contains {found!r}: "
        + "; ".join(_BANNED_VOCABULARY[word] for word in found)
        + ". Keep it in the same voice as the capacity notices (#13998/#14013)."
    )
    # ⛔ AND THE NARROWING ITSELF IS PINNED, in the FALSE-RED direction. Tag and
    # attribute text left the scan because a reader meets no claim there — so a
    # banned word sitting in a `class` or an `href` VALUE must NOT red. Without
    # this row, “the fold reads visible text only” is asserted by nothing, and the
    # helper is free to revert to folding raw markup (measured, same review).
    assert _banned_vocabulary('<span class="hot-cache">ok</span>') == [], (
        "a banned word in a `class` value is not text a reader meets, so folding "
        "the raw markup is the false-red half this pin was narrowed to remove"
    )
    assert _banned_vocabulary('<a href="#tier">ok</a>') == [], (
        "an `href` VALUE is a destination, not reader-facing text: a banned word "
        "there must not red the vocabulary pin (it is judged by "
        "`_REVIEWED_SECTION_HREFS` instead)"
    )


#: The reviewed wording of `#storage`, as a reader sees it.
#:
#: ⛔ Regenerate ONLY as part of a deliberate copy change, and treat the
#: resulting diff as the thing that needs reviewing. The recipe CALLS THE PINS'
#: OWN HELPERS, so the fixture and the section they are compared against cannot
#: disagree about where the section ends — a hand-copied slice stopped at any
#: `<h2 id=`, which silently dropped an un-advertised heading and its text from
#: the "reviewed" copy (measured, #14066 review round 4):
#:
#:     uv run python - <<'PY'
#:     import textwrap
#:     from tests.test_website_docs_consistency import (
#:         STORAGE_SECTION_GOLDEN, _docs_section, _visible_text)
#:     text = _visible_text(_docs_section())
#:     STORAGE_SECTION_GOLDEN.write_text(
#:         "\n".join(textwrap.wrap(text, 98)) + "\n", encoding="utf-8")
#:     PY
STORAGE_SECTION_GOLDEN = REPO_ROOT / "tests" / "fixtures" / "14013_storage_section.txt"

#: Every `href` the reviewed #storage section carries, sorted. Pinned as a literal
#: set because the attribute allowlist admits `href` BY NAME and the wording golden
#: compares TEXT only — so without this, the section's one outbound claim can be
#: retargeted with every other storage pin green (#14073 review, P2, measured).
#: A deliberate change here is a change to where a reader is sent: review it as a
#: claim change, the same way the wording fixture is reviewed.
_REVIEWED_SECTION_HREFS = ("mailto:hello@premiselabs.co",)


def _visible_text(markup: str) -> str:
    """What a reader sees: the rendered tree's text, entities decoded, whitespace folded.

    ⛔ NOT A REGEX OVER THE SOURCE, and the difference was a fail-open on this very
    golden (#14073 review, P0, measured). The previous body was
    `re.sub(r"<[^>]+", " ", markup)`, which deletes everything from ANY `<` to the
    next `>` — but a `<` starts a tag only when the next character is an ASCII
    letter, `/`, `!` or `?`; otherwise the tokenizer emits it as TEXT (WHATWG
    13.2.5.6, tag-open state). So a line added inside `#storage` —
    `< FalkorDB holds a slice of the record; above the allowance for your tier the
    least-used items are permanently deleted after thirty days >` — was rendered by
    every browser and DELETED by this function: the wording golden, this module's
    own vocabulary scan and the attribute allowlist all reported the section clean
    while a reader met a contradicting claim. A `<script>` body counted the other
    way, reddening the golden on markup no reader sees.

    The answer therefore comes from `extract_visible_text`, the render-fidelity
    seam the rest of this PR already uses: suppression (raw text, inert
    `<template>`, `<noscript>` under scripting) and the inherited render flag
    (`hidden`, unrendered element types) decide what exists to be read. A closed
    `<details>`/`<dialog>` deliberately does NOT suppress its subtree here — the loud
    rule at `_text_is_painted` counts it, because dropping rendered summary text was a
    fail-open in three consecutive review rounds. The two punctuation fixes below stay here because they are about
    PRESENTATION of the compared string, not about the tree: stripping an inline
    `<strong>`/`<a>` leaves a space before the following `.` or `,`, which is not
    what is on the page.
    """
    text = " ".join(extract_visible_text(markup).split())
    for spaced in (" .", " ,", " ;", " :"):
        text = text.replace(spaced, spaced[1])
    return text


def test_the_storage_section_says_exactly_what_was_reviewed() -> None:
    """Golden block — the WORDING is the deliverable, so any edit is a red.

    Presence pins cannot catch a CONTRADICTING sentence: a first version of these
    tests passed *\"Items outside the fast layer are removed permanently after 30
    days of inactivity\"* inserted above the required *\"nothing is lost\"* line,
    and passed a cost written as words rather than `$`/% (#14066 review round 2,
    measured against a mutated copy). A page whose whole reason to exist is that
    it cannot drift from what the product does has to fail on new text, not only
    on missing text — so the section is compared to the copy that was reviewed.

    This is the same mechanism `tests/test_13998_capacity_notice.py` uses for the
    notices themselves (`tests/fixtures/13998_notice_*.txt`): the copy is
    reviewed once, then held. When `#14012` lands its two controls, the wording
    changes — regenerate the fixture in that PR, where the change IS the review.

    ⛔ ATTRIBUTES ARE ASSERTED ABSENT FIRST — an ALLOWLIST, not a banned list.
    `_visible_text` strips tags, so a claim parked in an attribute is invisible to
    this comparison, and a reader meets several of them (hover for `title`,
    screen reader for `aria-*`). Measured: a `title` saying items are "permanently
    deleted … after thirty days" passed every storage pin, and so did an
    `aria-description` after `title`/`alt`/`aria-label` were banned one at a time
    (#14066 review rounds 3 and 4). The section needs `id`, `class` and `href` and
    nothing else, so the strong form is what is enforced; the day a fourth is
    genuinely wanted, compare RAW markup against a raw fixture instead.

    ⛔ THE ALLOWLIST IS NOT ENOUGH ON ITS OWN, AND `href` IS WHY. An allowlist
    judges an attribute BY NAME, so the one attribute it admits whose VALUE a
    reader acts on is the one it cannot judge. An anchor's destination is
    reader-facing — hover shows it, clicking follows it — and `_visible_text`
    compares text only, so `<a href="mailto:legal@evil.example">hello@premiselabs.co</a>`
    satisfied the allowlist, the wording golden AND the vocabulary scan at once
    while retargeting the section's only outbound claim. The destinations are
    therefore pinned as a literal set below, which is the narrow form of the
    raw-markup comparison this docstring already names as the general fix
    (#14073 review, P2; measured on all three pins simultaneously).

    ⛔ THE TAG SET IS PARSED, not matched in the raw source. A source pattern is
    wrong in both directions: it reads an attribute name written in a comment as a
    reader-facing one, and it MISSES the spellings HTML permits but a tidy writer
    does not emit — `title = "…"`, `data-30day="…"`, `data_del="…"` all carried a
    claim past the previous pattern (#14073 review; the fail-open is pinned by
    `test_the_storage_attribute_allowlist_catches_every_legal_spelling`).

    ⛔ WAS A DECLARED RESIDUAL, NOW REFUSED — A RENDERING DIRECTIVE IS NOT COPY.
    Every pin above judges the section's MARKUP and the text a browser paints FROM it;
    a `<style>` block inside the section whose `content:` property paints a claim (or a
    `<script>` that writes one) is neither text nor an attribute, so a `<style>`-only
    attack had no pin. It was declared here as a cost decision — "refusing every
    `<style>`/`<script>` in the section WOULD close the static case, at the price of a
    new pin, its own mutant, and a red on any directive the section ever carries".
    Round 7 measured the cost of NOT closing it and it is the larger one
    (#14073 review): a single attribute-free `<style>` whose `::after` painted
    `FalkorDB permanently deletes items above the allowance for your tier after thirty
    days` inside the section left the allowlist, the destination pin, the vocabulary
    scan and this golden ALL green, and Chromium painted it (`770x82`, inside the
    `#storage` band). The price quoted above has been paid instead: the refusal is the
    directive arm of `_attributes_outside_allowlist`, its mutant is arm 4 of
    `test_each_pin_in_the_storage_aggregate_reds_on_its_own_violation`, and a
    directive added to the section later reds rather than passing silently.

    Modelling the CSS CASCADE remains out of a static guard's reach, and a `<style>`
    placed OUTSIDE the section that targets a node inside it is the same class — that
    is the residual this now leaves, and it is the one the section cannot see.
    """
    markup = _docs_section()
    _assert_storage_section_reviewed(markup)


def _assert_storage_section_reviewed(markup: str) -> None:
    """The three #storage pins, as ONE callable so a control can drive them.

    ⛔ A CONTROL THAT RE-IMPLEMENTS A PIN DOES NOT TEST IT. The destination
    control below used to negate this comparison inline, so deleting the pin from
    the test left the control green and the retargeted page passing every storage
    pin again — a discriminating control that vouched for nothing (measured,
    #14073 review round 2). Extracting the body makes the control drive the
    implementation: remove the pin and the control fails.

    The pins, in the order the docstring above gives them: attribute NAMES
    (allowlist), attribute VALUES a reader acts on (`href` destinations), then the
    reviewed WORDING.

    ⛔ THREE PINS, THREE MUTANTS: `test_each_pin_in_the_storage_aggregate_reds_on
    _its_own_violation` drives each one through THIS callable, so a FOURTH pin
    added below needs a fourth mutant there. Added without one it is decorative —
    no existing control sees it, and the suite stays green (that is the vacuity
    #14073 review round 3 measured for two of these three).
    """
    present = _attributes_outside_allowlist(markup)
    assert not present, (
        f"the #storage section carries attribute(s) {present} beyond "
        f"<id>, <class>, <href>. The golden compares VISIBLE text, so a "
        f"reader-facing claim in an attribute would never be reviewed against it."
    )
    # ⛔ THE DESTINATIONS TOO, because the allowlist above judges a name and this
    # is the admitted attribute whose VALUE a reader meets. See the docstring's
    # "THE ALLOWLIST IS NOT ENOUGH ON ITS OWN" paragraph.
    destinations = sorted(set(extract_anchor_hrefs(markup)))
    assert destinations == list(_REVIEWED_SECTION_HREFS), (
        f"the #storage section links to {destinations}, not the reviewed "
        f"{list(_REVIEWED_SECTION_HREFS)}. The wording golden compares TEXT "
        f"only, so a retargeted href leaves every other storage pin green. This "
        f"is the section's only outbound claim: change it deliberately, with the "
        f"wording change reviewed, rather than pasting the new value in to make "
        f"this green."
    )
    got = _visible_text(markup)
    want = " ".join(STORAGE_SECTION_GOLDEN.read_text(encoding="utf-8").split())
    assert got == want, (
        "the #storage section no longer matches the reviewed wording "
        "(tests/fixtures/14013_storage_section.txt). This section is the "
        "destination of #13998's notices, so a wording change is a claim change: "
        "regenerate the fixture deliberately if it is right, do not paste the "
        "actual text into it to make this green.\n\n"
        f"--- reviewed ---\n{want}\n\n--- in the page ---\n{got}"
    )


def test_the_storage_section_states_what_is_not_lost_and_quotes_no_figure() -> None:
    """The claims the email makes, and no figure that could contradict it.

    #14013's done-when is that *\"a reader who has never seen the architecture
    can say what happens when the fast layer fills\"* — so the section must state
    the loss mode (a moment longer) and the non-loss mode (nothing) outright,
    in the notices' own words.

    The figure half is a real constraint, not tidiness: `product/pricing.json`
    is the one source for any rate the product publishes.

    ⚠️ This pin is NAMED-CLAIMS + SYMBOL-ABSENCE only, and it is deliberately not
    the drift guard: a cost written as words, or a contradicting sentence, passes
    it. `test_the_storage_section_says_exactly_what_was_reviewed` above is the
    pin that fails on new text.
    """
    section = _docs_section()
    folded = section.lower()
    for claim in (
        "nothing is lost",
        "stays searchable",
        "search keeps working across everything",
        "can take a moment longer",
        "postgres",
        "graph database",
    ):
        assert claim in folded, (
            f"the #storage section no longer says {claim!r}. Those are the "
            f"notices' own claims (#13998) — the page is where a reader who went "
            f"looking for the durable explanation has to find them."
        )
    for figure in ("$", "%"):
        assert figure not in section, (
            f"the #storage section carries {figure!r}. No rate or limit belongs "
            f"on this page: `product/pricing.json` is the one source for a "
            f"published figure, and #14013 forbids quoting anything it does not "
            f"carry."
        )


def test_a_table_scope_blocked_end_tag_cannot_end_the_storage_slice() -> None:
    """POSITIVE CONTROL — the nested-table slice escape (#14073 round 17, P0).

    Table scope (spec 13.2.4.2) is only `html`, `table`, `template`. So for
    `<table><tbody><tr><td><table></td></table>` the walk down from the top meets
    the INNER `table` before the `td`, and `</td>` is a parse error the browser
    IGNORES. A carve-out that returns True unconditionally for table-structure end
    tags pops the `td` AND the inner table; the following `</table>` then pops the
    OUTER table, so the model holds no table at all while a browser still does —
    and the next `<h2>` is cut across, ending the `#storage` slice before a claim
    that is foster-parented back INTO the section a reader lands in.

    Measured at round 17: that exact mutation left
    `tests/test_website_docs_consistency.py tests/test_website_static.py` at
    **287 passed, 1 skipped** with `_banned_vocabulary() == []`, while headless
    Chrome painted the claim at 812x27 px at y=4648 with `#quickstart`'s ancestor
    chain `H2#quickstart>TD>TR>TBODY>TABLE>DIV`. After the table-scope fix the same
    mutant reds 9 tests.
    """
    page = _read(DOCS)
    section = _docs_section()
    boundary = _h2_offset(page, "storage") + len(section)
    reviewed = " ".join(STORAGE_SECTION_GOLDEN.read_text(encoding="utf-8").split())

    blocked = (
        "<table><tbody><tr><td><table></td></table>\n"
        '<h2 id="quickstart">Quickstart (5 minutes)</h2></td>\n'
        "<p>FalkorDB permanently deletes items above the allowance for your tier "
        "after thirty days.</p>\n"
        "</table>\n"
    )
    mutant = page[:boundary] + blocked + page[boundary:]

    caught = _docs_section(page=mutant)
    sliced = " ".join(_visible_text(caught).split())
    assert "permanently deletes items above the allowance" in sliced, (
        "the `</td>` here is a table-scope parse error the browser IGNORES (the "
        "inner `table` is a table-scope boundary), so the outer table is still open "
        "and the slice must run PAST the next heading. Ending the slice on that "
        "ignored token puts a contradicting claim outside every pin while it renders "
        "inside #storage."
    )
    assert {"allowance", "falkor", "tier"} <= set(_banned_vocabulary(caught)), (
        "the vocabulary pin does not see the claim, so the plain-language rule can "
        "be walked around by a table-scope-blocked end tag"
    )
    assert sliced != reviewed


def test_the_scope_walk_uses_the_plain_scope_list_not_button_scope() -> None:
    """`button` does not end scope; `select` does. Both were wrong (#14073 round 17, P1).

    The `<p>` rule needs BUTTON scope, so `_P_SCOPE_BOUNDARY` carries `button` — and
    reusing that set for every ordinary end tag stops the downward walk at a
    `<button>`, so an end tag a browser HONOURS is ignored here and a container a
    browser closed stays open. An over-advertised set is the fail-open direction for
    the TOC: `_docs_section`'s only source of "advertised" is the index, so a heading
    whose fragment the browser never puts in the TOC could bound the slice.

    Measured: `<div class="toc"><a href="#a">A</a><button></div><a href="#b">B</a>
    </button></div>` — Chrome's DOM is `<div class="toc"><a>…<button></button></div>
    <a href="#b">`, i.e. the `</div>` pops the button and the div, so `#b` is
    OUTSIDE. Round 17 measured this model reporting `['#a', '#b']`.

    `select` is the mirror omission: it ends scope in the spec and was absent from
    the button-scope set entirely.
    """
    html = (
        '<div class="toc"><a href="#a">A</a><button></div>'
        '<a href="#b">B</a></button></div>'
    )
    assert extract_anchors_within(html, tag="div", class_token="toc") == [["#a"]], (
        "a `<button>` stopped the scope walk, so the `</div>` was ignored and `#b` "
        "was reported as advertised even though the browser puts it outside the TOC"
    )
    # `select` DOES end scope, so the mirror shape must keep `#b` inside.
    select_html = (
        '<div class="toc"><a href="#a">A</a><select></div>'
        '<a href="#b">B</a></select></div>'
    )
    assert extract_anchors_within(select_html, tag="div", class_token="toc") == [["#a"]], (
        "`select` ends scope in the spec, so a `</div>` inside a `<select>` is "
        "ignored and `#b` stays inside the div — reporting it as outside is the "
        "same divergence in the opposite direction"
    )


def test_an_end_tag_a_browser_ignores_does_not_end_the_storage_slice() -> None:
    """POSITIVE CONTROL — a scope-blocked end tag cannot walk the slice bound around.

    Spec 13.2.6.4.7: an end tag whose element is not IN SCOPE is a parse error and
    the token is IGNORED — `td`, `th`, `table`, `caption` and the rest of the
    boundary list stop the downward walk. Browsers therefore leave the stack alone
    for `<div><table><tr><td></div>`; a model that pops instead tears the table
    down, ends the `#storage` slice at the NEXT heading, and lets everything after
    it be foster-parented BEFORE that heading — inside the section a reader lands
    in — with every pin green.

    Measured at #14073 review round 16 with this exact mutation: the claim's `<p>`
    rendered 812x27 px at y=4648 in headless Chromium with
    `closest('#storage')` non-null, while `-k "storage or toc"` was byte-identical
    to the clean-tree baseline (12 passed, 1 skipped). Deleting only the `</div>`
    turned it red — so the browser-ignored token, not the injected claim, was what
    converted a caught page into a defeating one.
    """
    page = _read(DOCS)
    section = _docs_section()
    boundary = _h2_offset(page, "storage") + len(section)
    reviewed = " ".join(STORAGE_SECTION_GOLDEN.read_text(encoding="utf-8").split())

    assert " ".join(_visible_text(_docs_section()).split()) == reviewed, (
        "the clean page no longer matches the golden, so this control is asserting "
        "a premise that does not hold"
    )

    # The end tag a browser IGNORES, then the next advertised heading, then a claim
    # that contradicts the reviewed copy.
    blocked = (
        "<div><table><tr><td></div>\n"
        '<h2 id="quickstart">Quickstart (5 minutes)</h2></td></tr>\n'
        "<p>FalkorDB permanently deletes items above the allowance for your tier "
        "after thirty days.</p>\n"
        "</table>\n"
    )
    mutant = page[:boundary] + blocked + page[boundary:]

    caught = _docs_section(page=mutant)
    sliced = " ".join(_visible_text(caught).split())
    assert "permanently deletes items above the allowance" in sliced, (
        "a `</div>` written inside a `<td>` is IGNORED by every browser (the `td` is "
        "a scope boundary, so the token is a parse error) — the table stays OPEN, so "
        "the next heading is still inside it and the slice must run PAST it. Ending "
        "the slice on that ignored token is what put a contradicting claim outside "
        "every pin while it rendered inside #storage."
    )
    assert {"allowance", "falkor", "tier"} <= set(_banned_vocabulary(caught)), (
        "the vocabulary pin does not see the claim, so the section's plain-language "
        "rule can be walked around by an end tag a browser ignores."
    )
    assert sliced != reviewed, (
        "the golden pin is blind to the injected claim, so this control proves nothing"
    )


def test_a_css_invisible_advertised_heading_does_not_bound_the_storage_slice() -> None:
    """A TOC entry is not evidence a reader MEETS the heading it points at.

    ⛔ The bound's justification was "a heading a reader can REACH through the TOC
    had to be added deliberately". Reachable is not VISIBLE, and the gap was a false
    PASS of exactly the claim this section exists to refuse (#14073 review round 7):
    with `<h2 id="storage-limits" style="display:none">` advertised in the real TOC
    and a `FalkorDB`/`allowance`/`tier` "permanently deletes" paragraph under it,
    the heading cut the slice while a reader landing at `#storage` met that paragraph
    as part of the section — measured in Chromium at 770x27 px inside the `#storage`
    band, with the golden, the vocabulary scan, the destination pin and the
    allowlist all green.

    The heading now refuses to bound (`_heading_may_bound_the_slice`: a bounding
    heading must not carry a STATICALLY BOUNDARY-REMOVING attribute — `style` or
    `aria-hidden`; `class` may bound), so the copy falls INSIDE the slice and the pins
    see it. The direction is deliberately the LOUD one: a boundary a reader cannot see
    must not decide what was reviewed.

    ⛔ TWO SPELLINGS, ONE PER SET MEMBER, because a member no arm drives is a claim
    nobody observes (#14229 review: with only the `style` arm, deleting `aria-hidden`
    from `_STATIC_HIDING_ATTRIBUTES` changed no outcome in any document). `aria-hidden`
    does NOT hide the heading visually (MDN: it removes it from the accessibility tree,
    "CSS is required for visual hiding") — it earns its place because a reader who
    never meets the heading meets the copy under it as part of the section, so the
    boundary does not exist for that reader either.
    """
    page = _read(DOCS)
    boundary = _h2_offset(page, "storage") + len(_docs_section())
    claim = (
        "FalkorDB permanently deletes items above the allowance for your tier "
        "after thirty days."
    )
    toc_entry = '      <a href="#storage">How your data is stored and searched</a>\n'
    assert toc_entry in page, "the TOC entry this control advertises was renamed"
    for label, hiding, refused_as in (
        ("style", ' style="display:none"', "<h2 style>"),
        ("aria-hidden", ' aria-hidden="true"', "<h2 aria-hidden>"),
    ):
        invisible = (
            f'<h2 id="storage-limits"{hiding}>Limits</h2>\n'
            f"<p>{claim}</p>\n"
        )
        mutant = (page[:boundary] + invisible + page[boundary:]).replace(
            toc_entry, toc_entry + '      <a href="#storage-limits">Limits</a>\n', 1,
        )
        # THE PREMISE, asserted: the heading really is ADVERTISED, so the only thing
        # that can stop it bounding is the attribute — not a dropped TOC entry.
        assert '<a href="#storage-limits">' in mutant
        escaped = _slice_bound_at_any_h2(mutant)
        assert claim not in " ".join(_visible_text(escaped).split()), (
            f"{label}: premise failed — a bound that cuts at this heading puts the "
            f"claim OUTSIDE the slice, which is the behaviour this control refuses"
        )
        caught = _docs_section(page=mutant)
        assert claim in " ".join(_visible_text(caught).split()), (
            f"a heading carrying `{label}` still bounds the #storage slice, so copy a "
            f"reader meets inside the section escapes every pin (#14073 review "
            f"round 7; alternate spelling pinned by #14229 review)"
        )
        with pytest.raises(AssertionError, match=refused_as):
            _assert_storage_section_reviewed(caught)


def test_an_advertised_heading_a_reader_never_meets_does_not_bound_the_storage_slice() -> None:
    """A heading with NO visible text is not a boundary, whatever its start tag says.

    ⛔ THE THIRD ESCAPE IN THIS FAMILY, and the one the start-tag test could not see
    (#14229 second review, reproduced here and measured in Chromium there): the bound
    inspected the advertised heading's OWN attributes, so a heading that carries none
    still cut the slice while painting nothing — a 0-height line (`rect 812x0`,
    `innerText == ''`) with the claim under it painting 812x27 px inside the `#storage`
    band. Every row below shipped with the golden, the vocabulary scan, the destination
    pin and the allowlist ALL green.

    Four escaping spellings, because they are two different mechanisms and each has a
    member the other cannot see:

    * NO VISIBLE TEXT — the empty heading and the comment-only heading. No attribute is
      involved, so an attribute test cannot answer it; the copy under them is copy a
      reader meets as part of the section.
    * A HIDDEN DESCENDANT — `style="display:none"` on a `<span>` (which still
      contributes TEXT, so the no-text test cannot see it) and `aria-hidden="true"`
      (which leaves the text on screen, so that test cannot see it either).

    The fix audits the heading's own SUBTREE (`extract_heading_boundary_facts`), and
    the direction is the loud one: the refused heading no longer cuts the slice, so the
    claim falls INSIDE the pinned region and `_assert_storage_section_reviewed`
    reddens. The two controls that follow prove the audit refuses the ESCAPES rather
    than every heading: a heading with a visible `<span>` and a heading whose span
    carries a `class` both still bound, which is the round-5 rule this bound must
    never break.
    """
    page = _read(DOCS)
    boundary = _h2_offset(page, "storage") + len(_docs_section())
    claim = (
        "FalkorDB permanently deletes items above the allowance for your tier "
        "after thirty days."
    )
    toc_entry = '      <a href="#storage">How your data is stored and searched</a>\n'
    assert toc_entry in page, "the TOC entry this control advertises was renamed"
    advertised_toc = toc_entry + '      <a href="#storage-limits">Limits</a>\n'

    def _mutant(heading: str) -> str:
        return (
            page[:boundary]
            + heading
            + "\n"
            + f"<p>{claim}</p>\n"
            + page[boundary:]
        ).replace(toc_entry, advertised_toc, 1)

    for label, heading in (
        ("empty heading", '<h2 id="storage-limits"></h2>'),
        ("comment-only heading", '<h2 id="storage-limits"><!-- Limits --></h2>'),
        (
            "style-hidden descendant",
            '<h2 id="storage-limits"><span style="display:none">Limits</span></h2>',
        ),
        (
            "aria-hidden descendant",
            '<h2 id="storage-limits"><span aria-hidden="true">Limits</span></h2>',
        ),
    ):
        mutant = _mutant(heading)
        # THE PREMISE, asserted: the heading really is ADVERTISED, so the only thing
        # that could stop it bounding is its own subtree — not a dropped TOC entry.
        assert '<a href="#storage-limits">' in mutant
        escaped = _slice_bound_at_any_h2(mutant)
        assert claim not in " ".join(_visible_text(escaped).split()), (
            f"{label}: premise failed — a bound that cuts at this heading puts the "
            f"claim OUTSIDE the slice, which is the escape this control refuses"
        )
        caught = _docs_section(page=mutant)
        assert claim in " ".join(_visible_text(caught).split()), (
            f"`{label}` still bounds the #storage slice: the heading paints no line a "
            f"reader reads, so the copy under it is copy a reader landing at #storage "
            f"meets inside the section — and it is outside every pin (#14229 review)"
        )
        with pytest.raises(AssertionError):
            _assert_storage_section_reviewed(caught)

    # ── the controls: a VISIBLE heading still bounds, `class` and all ─────
    for label, heading in (
        ("visible span", '<h2 id="storage-limits"><span>Limits</span></h2>'),
        (
            "class-bearing span",
            '<h2 id="storage-limits"><span class="badge">Limits</span></h2>',
        ),
        ("plain heading", '<h2 id="storage-limits">Limits</h2>'),
    ):
        caught = _docs_section(page=_mutant(heading))
        assert claim not in " ".join(_visible_text(caught).split()), (
            f"a heading a reader DOES meet ({label}) stopped bounding: the subtree "
            f"audit is refusing correct pages, which is the false-red direction this "
            f"file refuses `class` for (#14229 review)"
        )


def test_a_visible_heading_beside_a_hiding_one_still_bounds_the_storage_slice() -> None:
    """A heading's boundary is decided by ITS OWN SUBTREE, never by a neighbour's.

    ⛔ THE FALSE RED THE ±1 NEIGHBOURHOOD CAUSED (#14229 review, measured). The
    neighbourhood was justified by "a legal start tag may wrap: `<h2\n  id="x"\n
    style="display:none">` puts the offending attribute on a different line from the
    `id`" — which the parser falsifies: `_record_attributes` stamps every attribute of
    a tag with the TAG's own position, so both attributes land on ONE line at any
    wrapping depth. `line - 1` / `line + 1` could therefore only match a DIFFERENT
    heading, and did: a hiding heading on the line after a visible
    `<h2 id="storage-limits">` stopped the VISIBLE one from bounding, the slice ran
    long, and the wording golden reddened on a page whose boundary was correct — the
    false-red direction this file refuses `class` for.

    ⛔ AND A LINE WAS STILL NOT THE HEADING (#14229 second review, measured): two
    headings can share ONE source line, so line-keyed membership let an un-advertised
    `aria-hidden` sibling refuse the visible advertised heading beside it — same false
    red, one scope smaller. The facts are keyed by the heading's own OFFSET, which is
    arm 4 below.

    Four arms, because the two keys that failed have two halves and the member set
    moved:

    * the ADJACENCY must bound — driven with `aria-hidden`, a CURRENT member, since
      that is the shape a neighbourhood can still mis-fire on (this arm FAILS on the
      window);
    * the WRAPPED spelling a line-keyed test would need is keyed to the TAG's position,
      so the fact lands on the heading at any wrapping depth (plus the fragment
      assertion that measures the stamping);
    * `hidden` decides nothing at ANY distance, which is why it left the member set:
      `_unmet_by_reader` refuses such a heading a layer up, so it is not a candidate
      and its only possible effect was on a NEIGHBOUR;
    * TWO HEADINGS ON ONE LINE are two facts (this arm FAILS on line-keyed membership).
    """
    page = _read(DOCS)
    boundary = _h2_offset(page, "storage") + len(_docs_section())
    claim = (
        "FalkorDB permanently deletes items above the allowance for your tier "
        "after thirty days."
    )
    toc_entry = '      <a href="#storage">How your data is stored and searched</a>\n'
    assert toc_entry in page, "the TOC entry this control advertises was renamed"
    advertised_toc = toc_entry + '      <a href="#storage-limits">Limits</a>\n'
    visible = '<h2 id="storage-limits">Limits</h2>\n'

    def _mutant_with_neighbour(neighbour: str) -> str:
        return (
            page[:boundary]
            + visible
            + neighbour
            + f"<p>{claim}</p>\n"
            + page[boundary:]
        ).replace(toc_entry, advertised_toc, 1)

    # ── arm 1: a heading that cannot bound on the NEXT line must not unbound its
    #          neighbour (driven with `aria-hidden`, a current member)
    neighbour = '<h2 id="storage-draft" aria-hidden="true">Draft</h2>\n'
    mutant = _mutant_with_neighbour(neighbour)
    assert '<a href="#storage-limits">' in mutant, (
        "premise: the visible heading is ADVERTISED, so only the neighbourhood can "
        "stop it bounding"
    )
    assert 'href="#storage-draft"' not in mutant, (
        "premise: the neighbour is NOT advertised, so it cannot be the bound the "
        "slice ends at — the only advertised heading after #storage is the visible one"
    )
    visible_offset = _h2_offset(mutant, "storage-limits")
    neighbour_offset = _h2_offset(mutant, "storage-draft")
    facts = {
        offset: (has_text, hides)
        for offset, has_text, hides in extract_heading_boundary_facts(
            mutant, hiding_attributes=_STATIC_HIDING_ATTRIBUTES,
        )
    }
    assert facts[visible_offset] == (True, False), (
        "premise: the visible heading's OWN subtree carries only `id` and the text "
        f"`Limits`, so nothing about IT can refuse its bound. Got {facts[visible_offset]}"
    )
    assert facts[neighbour_offset] == (True, True), (
        "premise: the neighbour's `aria-hidden` IS what the bound reads, and it is "
        f"a DIFFERENT heading's fact. Got {facts[neighbour_offset]}"
    )
    caught = _docs_section(page=mutant)
    assert claim not in " ".join(_visible_text(caught).split()), (
        "a visible advertised heading carrying only `id` stopped bounding because a "
        "hiding heading sits on the next source line: the slice ran long and the "
        "wording golden reds on a page whose boundary is CORRECT (#14229 review)"
    )
    assert "Draft" not in caught and "Limits" not in caught, (
        "the slice did not stop at the visible heading: it is the FIRST advertised "
        "heading after #storage, so the bound is not being measured"
    )

    # ── arm 2: the wrapped spelling the window existed for is on the TAG's line ──
    fragment = '<h2\n  id="x"\n  style="display:none">T</h2>\n'
    assert extract_element_attributes(fragment) == [("h2", "id", 1), ("h2", "style", 1)], (
        "a wrapped start tag no longer stamps every attribute on the TAG's line, so "
        "the honest test would be a LINE and the fact would be mis-keyed"
    )
    wrapped_facts = {
        offset: (has_text, hides)
        for offset, has_text, hides in extract_heading_boundary_facts(
            fragment, hiding_attributes=_STATIC_HIDING_ATTRIBUTES,
        )
    }
    assert wrapped_facts == {0: (True, True)}, (
        "premise: a WRAPPED statically-hiding heading reports its hiding attribute "
        f"against ITS OWN offset at any wrapping depth. Got {wrapped_facts}"
    )
    wrapped = (
        '<h2\n  id="storage-limits"\n  style="display:none">Limits</h2>\n'
        f"<p>{claim}</p>\n"
    )
    wrapped_mutant = (page[:boundary] + wrapped + page[boundary:]).replace(
        toc_entry, advertised_toc, 1,
    )
    assert '<a href="#storage-limits">' in wrapped_mutant, (
        "premise: the wrapped heading is advertised too"
    )
    wrapped_caught = _docs_section(page=wrapped_mutant)
    assert claim in " ".join(_visible_text(wrapped_caught).split()), (
        "a WRAPPED statically-hiding heading now bounds the slice: the exact-line "
        "test does not cover the spelling the deleted neighbourhood was written for, "
        "so this fix would have lost that coverage (#14229 review)"
    )

    # ── arm 3: `hidden` decides nothing, at any distance ──────────────────
    # The member set's evidence, asserted rather than narrated: a `hidden` heading is
    # refused one layer up, so it is never a bound CANDIDATE — and removing it from
    # the set therefore changes no page. Re-adding an unreachable member would red here.
    assert extract_heading_positions('<h2 id="x" hidden>T</h2>') == [], (
        "a `hidden` heading is now RECORDED, so it is a bound candidate and `hidden` "
        "belongs in `_STATIC_HIDING_ATTRIBUTES` after all"
    )
    hidden_mutant = _mutant_with_neighbour('<h2 id="storage-draft" hidden>Draft</h2>\n')
    hidden_facts = {
        offset: (has_text, hides)
        for offset, has_text, hides in extract_heading_boundary_facts(
            hidden_mutant, hiding_attributes=_STATIC_HIDING_ATTRIBUTES,
        )
    }
    assert _h2_offset(hidden_mutant, "storage-draft") == -1, (
        "a `hidden` heading is now RECORDED as a bound candidate, so `hidden` "
        "belongs in `_STATIC_HIDING_ATTRIBUTES` after all"
    )
    assert hidden_facts[_h2_offset(hidden_mutant, "storage-limits")] == (True, False), (
        "the visible heading's OWN facts moved because a `hidden` heading sits on the "
        "next line: a member no document can reach is being consulted again, which "
        "is the false red this test refuses (#14229 review). Got "
        f"{hidden_facts[_h2_offset(hidden_mutant, 'storage-limits')]}"
    )
    hidden_caught = _docs_section(page=hidden_mutant)
    assert claim not in " ".join(_visible_text(hidden_caught).split()), (
        "a `hidden` heading on the next line stopped its visible neighbour bounding"
    )

    # ── arm 4: TWO HEADINGS ON ONE LINE are two facts, not one line ──────
    # ⛔ THE LINE-KEYED VERSION'S FALSE RED (#14229 second review, measured): a hiding
    # sibling on the SAME source line unbound a correct visible heading, the slice ran
    # long and the golden reddened on a page whose `#storage` copy is byte-identical to
    # the fixture. The fact belongs to the heading, so the sibling's line is not this
    # heading's evidence.
    same_line = (
        '<h2 id="storage-limits">Limits</h2>'
        '<h2 id="storage-draft" aria-hidden="true">Draft</h2>\n'
        f"<p>{claim}</p>\n"
    )
    same_line_mutant = (page[:boundary] + same_line + page[boundary:]).replace(
        toc_entry, advertised_toc, 1,
    )
    visible_offset = _h2_offset(same_line_mutant, "storage-limits")
    draft_offset = _h2_offset(same_line_mutant, "storage-draft")
    assert same_line_mutant.count("\n", 0, visible_offset) == same_line_mutant.count(
        "\n", 0, draft_offset
    ), "premise: both headings really are on ONE source line"
    assert '<a href="#storage-limits">' in same_line_mutant, (
        "premise: the visible heading is advertised"
    )
    same_line_facts = {
        offset: (has_text, hides)
        for offset, has_text, hides in extract_heading_boundary_facts(
            same_line_mutant, hiding_attributes=_STATIC_HIDING_ATTRIBUTES,
        )
    }
    assert same_line_facts[visible_offset] == (True, False), (
        "premise: the visible heading's own subtree is clean, and its neighbour is "
        f"on the same line. Got {same_line_facts[visible_offset]}"
    )
    assert same_line_facts[draft_offset] == (True, True), (
        "premise: the un-advertised sibling's aria-hidden is ITS OWN fact, reported "
        f"against its own offset. Got {same_line_facts[draft_offset]}"
    )
    same_line_caught = _docs_section(page=same_line_mutant)
    assert claim not in " ".join(_visible_text(same_line_caught).split()), (
        "a hiding sibling on the SAME source line unbound a correct visible heading: "
        "the slice ran long and the wording golden reds on a page whose boundary is "
        "correct — the line-keyed membership this replaced (#14229 second review)"
    )


def test_raw_text_directives_are_recorded_exactly_where_a_reader_meets_them() -> None:
    """`extract_raw_text_elements`' contract, one row per rendering fact.

    ⛔ THE DOCSTRING OVER-PROMISED "every `<style>`/`<script>`" AND NOTHING TESTED IT
    (#14229 review): the foreign branch returns BEFORE the HTML raw-text branch, so
    `<svg><style>` was silently unrecorded — even though SVG's stylesheet applies
    DOCUMENT-WIDE, which is precisely the question the caller asks. Driven directly
    rather than through `_attributes_outside_allowlist`, because that consumer's single
    answer ("this section carries <style>") cannot tell these cases apart.
    """
    for label, markup, expected in (
        ("plain style", "<style>p{color:red}</style>", [("style", 1)]),
        ("plain script", "<script>x()</script>", [("script", 1)]),
        # A FOREIGN directive is a real stylesheet and applies document-wide, so a
        # caller asking "is this statically reviewable?" must be told no.
        ("svg style", "<svg><style>p{}</style></svg>", [("style", 1)]),
        ("math style", "<math><style>p{}</style></math>", [("style", 1)]),
        # Inert content renders in no mode, and a comment is not markup at all — the
        # caller is already cleared to read these, so reporting them would be a false
        # red on a correct page.
        ("inert template", "<template><style>p{}</style></template>", []),
        # ⛔ AND THE SAME INERTNESS THROUGH A FOREIGN ROOT (#14229 review): `_open` is
        # called unconditionally for `<svg>`/`<math>`, so the FOREIGN branch was
        # reached while the template was open and appended without consulting the
        # stack — measured, this returned `[('style', 1)]` while the HTML row above
        # returned `[]`. Same answer, one content mode later.
        (
            "inert template (foreign style)",
            "<template><svg><style>p{}</style></svg></template>",
            [],
        ),
        (
            "inert template (foreign script)",
            "<template><math><script>x()</script></math></template>",
            [],
        ),
        ("comment", "<!-- <style>p{}</style> -->", []),
        ("plain markup", '<h2 id="x">T</h2><p>body</p>', []),
    ):
        got = extract_raw_text_elements(markup)
        assert got == expected, f"{label}: got {got}, want {expected}"


def test_the_storage_slice_bound_catches_the_heading_it_exists_for() -> None:
    """POSITIVE CONTROL — the slice bound reds on the mutations it was written for.

    On the CURRENT page the advertised-heading bound and the unconditional
    `<h2 id=` bound compute the SAME slice, so reverting `_docs_section` to
    `rest.find("<h2 id=", 1)` leaves every row in this file green (measured at
    #14073's review). A pin that cannot red is indistinguishable from an absent
    one — the same argument as the swap control in `_quadrature_exponent` — so the
    mutants here are built in memory from the committed page and driven through
    the REAL bound. No fixture moves, and a fresh reader sees what each bound buys.
    """
    page = _read(DOCS)
    section = _docs_section()
    boundary = _h2_offset(page, "storage") + len(section)
    reviewed = " ".join(STORAGE_SECTION_GOLDEN.read_text(encoding="utf-8").split())

    # ── the slice ends at the next ADVERTISED heading ────────────────────────
    # An un-advertised sibling heading: reachable by URL, absent from the TOC, so
    # nobody reviews it as part of the section a reader lands in.
    limits = (
        '<h2 id="storage-limits">Limits</h2>\n'
        "<p>FalkorDB holds a slice of the record; above the allowance for your "
        "tier, the least-used items are permanently deleted after thirty days.</p>\n"
    )
    mutant = page[:boundary] + limits + page[boundary:]

    escaped = _slice_bound_at_any_h2(mutant)
    assert " ".join(_visible_text(escaped).split()) == reviewed, (
        "the unconditional `<h2 id=` bound now sees the un-advertised heading, so "
        "this control no longer demonstrates the defect it pins (#14066 round 3); "
        "re-derive that measurement before trusting the bound."
    )
    assert not _banned_vocabulary(escaped), (
        "the vocabulary pin is not blind under the reverted bound, so this control "
        "is asserting a premise that no longer holds"
    )

    caught = _docs_section(page=mutant)
    assert " ".join(_visible_text(caught).split()) != reviewed, (
        'the golden pin is blind to an un-advertised `<h2 id="storage-limits">` '
        "inserted inside the #storage region: the slice was cut before it, so a "
        "claim nobody reviewed ships green."
    )
    assert {"allowance", "falkor", "tier"} <= set(_banned_vocabulary(caught)), (
        "the vocabulary pin does not see the un-advertised heading's text, so the "
        "section's plain-language rule can be walked around by adding a heading "
        "the TOC does not advertise."
    )

    # ── the index is the TOC block, not every `href="#…"` on the page ────────
    inlined = mutant.replace(
        '<h2 id="api">', '<a href="#storage-limits">limits</a>\n    <h2 id="api">', 1
    )
    assert inlined != mutant, (
        'website/docs.html has no `<h2 id="api">` to hang the inline '
        "cross-reference on, so this control cannot run"
    )
    assert _banned_vocabulary(_docs_section(page=inlined)), (
        'one inline `<a href="#storage-limits">` elsewhere on the page now excludes '
        "the un-advertised heading, so the index is reading the whole page — "
        "#14066 round 4's escape is back."
    )
    assert " ".join(_visible_text(_slice_bound_by_page_wide_hrefs(inlined)).split()) == reviewed, (
        "the whole-page index no longer escapes the inline cross-reference, so this "
        "control no longer demonstrates why the TOC block is the index"
    )

    # ── a later heading that grew an attribute in FRONT of its id ───────────
    # The pre-#14073 bound searched for the literal `<h2 id=`, which is only
    # present when `id` is the tag's FIRST attribute. Reorder a later heading and
    # that bound loses it: the slice runs on to the next bare tag, so the golden
    # reds on an edit that does not touch the storage copy. The parsed bound does
    # not care where `id` sits among the attributes (#14073 review).
    following = _H2.search(page, boundary)
    assert following is not None, (
        "the #storage section is the last `<h2>` on the page, so this control "
        "cannot run"
    )
    tag = following.group(0)
    reordered = (
        page[:following.start()]
        + '<h2 class="qs"' + tag[len("<h2"):]
        + page[following.end():]
    )
    assert reordered != page, (
        f"could not move the id out of first position in {tag!r}, so this "
        "control cannot run"
    )
    assert " ".join(_visible_text(_docs_section(page=reordered)).split()) == reviewed, (
        "moving `class` in front of `id` on the heading AFTER #storage moved the "
        "slice's end, so the golden now fails on an edit that does not touch the "
        "storage copy — the literal-tag defect is back"
    )
    assert " ".join(_visible_text(_slice_bound_by_literal_tag(reordered)).split()) != reviewed, (
        "the literal `<h2 id=` bound still finds the reordered heading, so this "
        "control no longer demonstrates the false red the parsed bound removes "
        "(#14073 review: a control that passes under both bounds pins nothing)"
    )

    # ── and the section's OWN tag, the same way ─────────────────────────────
    # BOTH orders are pinned: `class` AFTER `id` is the spelling a writer adds by
    # hand, and `class` BEFORE `id` is the one that also moves `id` out of first
    # position. The reviewer named the first; the second is the reordering case.
    for label, replacement in (
        ("class AFTER id", '<h2 id="storage" class="s">'),
        ("class BEFORE id", '<h2 class="storage" id="storage">'),
    ):
        own = page.replace('<h2 id="storage">', replacement, 1)
        assert own != page, f"could not add `class` to the #storage heading ({label})"
        assert _h2_offset(own, "storage") >= 0, (
            f"a `class` added to the #storage heading ({label}) made the parsed "
            "bound stop finding the section, so the anchor pin now reads as 'the "
            "section was renamed'"
        )
        assert " ".join(
            _visible_text(_docs_section(page=own)).split()
        ) == reviewed, (
            f"a `class` on the #storage heading ({label}) moved the slice, so the "
            "golden reds on an edit that does not touch the copy"
        )
        assert _slice_bound_by_literal_tag(own) == "", (
            f"the literal-tag bound still finds the {label} spelling, so this "
            "control no longer demonstrates the false red it pins"
        )

    # ── the INDEX is read out of the PARSED tree, not off the raw source ─────
    # The last raw-source read in the bound. A decoy TOC in a place a reader never
    # meets replaces the advertised set, so an un-advertised heading bounds the
    # slice again and its text leaves all three pins (#14073 review). The control
    # below reproduces the decoy AND the escape, so neither half can rot silently.
    decoy_toc = '<!-- <div class="toc"><a href="#storage-limits">Limits</a></div> -->\n'
    for label, decoy in (
        ("comment", decoy_toc),
        ("inert template",
         '<template><div class="toc"><a href="#storage-limits">Limits</a></div></template>\n'),
    ):
        decoyed = page[:boundary] + limits + page[boundary:]
        decoyed = decoyed.replace('<div class="toc">', decoy + '<div class="toc">', 1)
        assert decoyed != page, f"could not insert the decoy TOC ({label})"

        # THE PREMISE: the raw-source index reads the decoy, so `storage-limits`
        # becomes advertised and the rendered heading after it bounds the slice.
        escaped = _slice_bound_by_raw_headings(decoyed)
        assert " ".join(_visible_text(escaped).split()) == reviewed, (
            f"the raw-source index no longer reads the decoy TOC in a {label}, so "
            "this control no longer demonstrates the fail-open it pins"
        )
        assert not _banned_vocabulary(escaped), (
            f"the vocabulary pin sees the claim the {label} decoy hides, so it is "
            "not the raw-source index this control measures"
        )

        caught = _docs_section(page=decoyed)
        assert {"allowance", "falkor", "tier"} <= set(_banned_vocabulary(caught)), (
            f"a `<div class=\"toc\">` inside a {label} REPLACED the advertised "
            "set: an un-advertised heading now bounds the #storage slice and the "
            "claim under it leaves the golden, the vocabulary pin and the "
            "attribute allowlist at once"
        )
        assert " ".join(_visible_text(caught).split()) != reviewed, (
            f"the golden is blind to the claim hidden by the {label} decoy TOC"
        )

    # The same read in the FALSE-RED direction: all three spellings below are
    # legal HTML and all three render, so the index must read the same set from
    # them. Measured against the pre-fix raw index, which ran the slice past the
    # section (`href='…'`, `href=…`) or fired the "no TOC block" assertion
    # (`class="toc wide"`) — a red blamed on the storage copy.
    for label, old, new in (
        ("single-quoted href", 'href="#quickstart"', "href='#quickstart'"),
        ("unquoted href", 'href="#quickstart"', "href=#quickstart"),
        ("extra class token", '<div class="toc">', '<div class="toc wide">'),
    ):
        respelled = page.replace(old, new, 1)
        assert respelled != page, f"website/docs.html has no {old!r} to respell ({label})"
        assert " ".join(
            _visible_text(_docs_section(page=respelled)).split()
        ) == reviewed, (
            f"respelling the TOC as {label} moved the slice, so the golden reds on "
            "an edit that does not touch the storage copy: the index is still "
            "being read off the raw source"
        )
    # The raw index provably misses the two href spellings, so the asserts above
    # are not passing for a reason that holds under both implementations.
    for old, new in (
        ('href="#quickstart"', "href='#quickstart'"),
        ('href="#quickstart"', "href=#quickstart"),
    ):
        assert " ".join(
            _visible_text(_slice_bound_by_raw_headings(page.replace(old, new, 1))).split()
        ) != reviewed, (
            f"the raw-source index now reads {new!r}, so this control no longer "
            "demonstrates the false red the parsed index removes"
        )
    assert re.search(r'<div class="toc">', page.replace('<div class="toc">', '<div class="toc wide">', 1)) is None, (
        "a token added to the TOC's class is still matched by the literal "
        "`<div class=\"toc\">` pattern, so this control pins nothing"
    )

    # ── the DECLARED residual: an ADVERTISED heading still cuts the slice ────
    # Pinned as a live behaviour rather than left to be derived, so a reader meets
    # it here instead of finding it in production. A TOC entry is a deliberate
    # authorship decision, and the cost of one is a heading whose text nobody had
    # to hold to the plain-language rule — see `_docs_section`'s docstring.
    advertised = page[:boundary] + limits + page[boundary:]
    advertised = advertised.replace(
        '<a href="#quickstart">',
        '<a href="#storage-limits">Limits</a> <a href="#quickstart">',
        1,
    )
    assert 'href="#storage-limits"' in advertised, (
        "could not add the TOC entry that makes the residual reachable"
    )
    assert " ".join(
        _visible_text(_docs_section(page=advertised)).split()
    ) == reviewed, (
        "an ADVERTISED sub-heading no longer cuts the #storage slice, which means "
        "the declared residual in `_docs_section`'s docstring is stale — re-derive "
        "it before trusting this bound's scope"
    )
    assert _banned_vocabulary(_docs_section(page=advertised)) == [], (
        "the pins now see past an advertised sub-heading, so the residual the "
        "docstring declares has been closed and that paragraph must be rewritten"
    )


def test_the_storage_section_bound_cuts_at_a_RENDERED_heading_only() -> None:
    """NEGATIVE CONTROL — a heading no browser renders must not end the slice.

    The bound used to be a raw `<h2` pattern, so a heading parked where a reader
    never meets it — inside a comment, inside a `<script>` body, inside an inert
    `<template>` — still ended the slice. Everything after it then sat outside
    the golden, the vocabulary pin AND the attribute allowlist, which is the
    fail-open this test pins: an advertised id in an inert region plus one
    paragraph carrying `FalkorDB`, the `allowance` and a `tier` passed all three
    pins (measured at `2a64b42`, #14073 review).

    Both directions are asserted: the parsed bound now keeps the paragraph inside
    the slice, AND the raw-source bound provably escaped it — otherwise this
    control would pass against the defect it exists to pin.

    ⛔ ONE INSERTION MODE IS MODELLED AND THE REST ARE NOT, so this test's name
    claims no more than that. A `<h2>` with an open `<select>` ancestor is not
    recorded: the classic rules' "in select" mode dropped every other start tag,
    while the current standard no longer lists that mode (its sequence goes from
    "in cell" to "in template") and a `<h2>` inside a `<select>` may now be
    created — so the browser answer is not settled. Only one of the two answers is
    safe, and it is the one modelled: BOUNDING at a heading the pins cannot see is
    fail-open (the claim under it leaves every pin, measured — it bounded the slice
    and all three pins stayed green; #14073 review), while refusing to bound leaves
    the slice long and the extra text pinned. The declared cost: a genuinely
    rendered heading inside a select does not end the slice.

    ⛔ AND THE TOKENS THAT DO CLOSE THE SCOPE ARE MODELLED — `<input>` and a nested
    `<select>`, the living standard's "in body" rules (`_SELECT_POP`). This
    paragraph used to say
    the classic mode's `input`/`keygen`/`textarea` were all unmodelled; that was
    wrong twice over: `<input>` and a nested `<select>` are modelled now, and
    `keygen`/`textarea` have no select-scope step in the living standard, so
    modelling them would be the FAIL-OPEN direction — a `<select><textarea>` decoy
    pops the select, advertises its anchor and bounds the slice early, which is the
    escape #14073 exists to close. Both readings are pinned in
    `test_the_select_scope_closes_on_input_and_a_nested_select_and_nothing_else`.

    ⛔ AND THE IMPLICIT-CLOSE RULES ARE NOT MODELLED, in the SAFE direction only
    because the container helper refuses the tags they affect: for an ordinary
    container a leftover `<p>` is harmless (`_close_extent` searches the whole stack
    for the container's tag), and for a tag a sibling closes (`<li>`, `<p>`, `<td>`…)
    the extent would run PAST the sibling — a larger advertised set, a shorter
    slice — so `extract_anchors_within` raises on those instead. What is left is a
    container that mis-nests across a raw-text region, which closes EARLY (fewer
    advertised entries, longer slice — the loud direction, and no covered page
    carries one today; the seam's page-level ratchet does NOT cover this one).
    """
    page = _read(DOCS)
    section = _docs_section()
    boundary = _h2_offset(page, "storage") + len(section)
    reviewed = " ".join(STORAGE_SECTION_GOLDEN.read_text(encoding="utf-8").split())
    claim = (
        "<p>FalkorDB holds a slice of the record; above the allowance for your "
        "tier, the least-used items are permanently deleted after thirty days.</p>\n"
    )
    for label, inert in (
        ("comment", '<!-- <h2 id="quickstart">Quickstart</h2> -->'),
        ("inert template", '<template><h2 id="quickstart">Quickstart</h2></template>'),
        ("script body", '<script>var s = \'<h2 id="quickstart">Quickstart</h2>\';</script>'),
        # `in select` — parsed as markup by every tokenizer, and NOT bounded: the
        # classic mode dropped the start tag, the current standard may create it,
        # and the refusal below is the conservative side of that (it keeps the
        # claim INSIDE the slice instead of betting the other way). Reachable with
        # ordinary authoring, and it is why modelling this case was part of the fix.
        ("select", '<select><h2 id="quickstart">Quickstart</h2></select>'),
        # ⛔ ELEMENT TYPES A SUPPORTING BROWSER DOES NOT RENDER — the same fail-open
        # one level out from insertion modes: the heading was IN the tree, so it was
        # recorded and bounded the slice while the claim under it left every pin
        # (#14073 review, measured). `<datalist>` is `display: none` in the UA
        # stylesheet; the rest put their children in the fallback slot.
        ("datalist", '<datalist id="opts"><h2 id="quickstart">Quickstart</h2></datalist>'),
        ("canvas fallback", '<canvas><h2 id="quickstart">Quickstart</h2></canvas>'),
        ("video fallback", '<video><h2 id="quickstart">Quickstart</h2></video>'),
        # …and the ATTRIBUTE spelling of the same thing: `hidden` on an ancestor
        # (or on the heading) removes the subtree from the rendering, with no CSS
        # involved. `hidden` is not in the section allowlist, and it need not be:
        # this wrapper lives OUTSIDE the slice, so nothing else would see it.
        ("hidden subtree", '<div hidden><h2 id="quickstart">Quickstart</h2></div>'),
        ("hidden heading", '<h2 hidden id="quickstart">Quickstart</h2>'),
        ("closed details", '<details><h2 id="quickstart">Quickstart</h2></details>'),
        # ⛔ A BARE `<option>` — no `<select>` above it, so the `in select` rule does
        # not apply and the element-type rule did not name it: the heading has a 0x0
        # rect and paints 0 px in this box's Chrome (measured), so it was a decoy that
        # BOUND the slice while Chromium painted the claim under it. The group row
        # below is here because the refusal is ancestry, not the immediate parent — a
        # fix that special-cased the direct parent would pass the first row and fail
        # it (#14073 review round 8).
        ("bare option", '<option><h2 id="quickstart">Quickstart</h2></option>'),
        (
            "optgroup over a bare option",
            '<optgroup><option><h2 id="quickstart">Quickstart</h2></option></optgroup>',
        ),
        # ⛔ A RUBY PARENTHESIS IS THE OTHER `display: none` MEMBER (UA stylesheet), and
        # BOTH questions agree about it: nothing inside a `<rp>` paints, so a heading
        # there bounded the slice while no reader met it (#14073 review round 9).
        ("ruby parenthesis", '<rp><h2 id="quickstart">Quickstart</h2></rp>'),
    ):
        mutant = page[:boundary] + inert + "\n" + claim + page[boundary:]
        assert mutant != page, f"could not insert the {label} case"

        # THE PREMISE: `quickstart` is advertised, so a raw bound would cut here.
        escaped = _slice_bound_by_raw_headings(mutant)
        assert "permanently deleted" not in _visible_text(escaped).lower(), (
            f"the claim after a `<h2 id=\"quickstart\">` inside a {label} is now "
            f"INSIDE the raw-source slice, so this control no longer demonstrates "
            f"the fail-open it pins"
        )
        assert not _banned_vocabulary(escaped), (
            f"the vocabulary pin sees the claim hidden by the {label} case, so the "
            f"raw-source bound is not what this control measures"
        )

        caught = _docs_section(page=mutant)
        assert {"allowance", "falkor", "tier"} <= set(_banned_vocabulary(caught)), (
            f"a `<h2 id=\"quickstart\">` inside a {label} ended the #storage "
            f"slice, so the paragraph after it ships outside the golden, the "
            f"vocabulary pin and the attribute allowlist at once"
        )
        assert " ".join(_visible_text(caught).split()) != reviewed, (
            f"the golden is blind to the paragraph after a heading inside a "
            f"{label}: the slice was cut at a heading no reader meets"
        )

    # ── a heading that only QUOTES an advertised id is not a boundary ────────
    # `<h2 class='see id="quickstart"'>` has NO id to a browser. A pattern over
    # the attribute text still reads one, ends the slice there, and a claim
    # written under such a heading ships green — measured against every pin
    # (#14073 review round 6). The id now comes from the parser, so this heading
    # is an ordinary heading: it stays INSIDE the pinned slice.
    quoting = page[:boundary] + (
        "<h2 class='see id=\"quickstart\"'>Limits</h2>\n"
        "<p>FalkorDB holds a slice of the record; above the allowance for your "
        "tier, the least-used items are permanently deleted after thirty days.</p>\n"
    ) + page[boundary:]
    assert _h2_id(' class=\'see id="quickstart"\'') is None, (
        "`_h2_id` reads an id out of another attribute's QUOTED VALUE, so a "
        "heading that carries no id can still bound the slice"
    )
    quoting_slice = _docs_section(page=quoting)
    assert " ".join(_visible_text(quoting_slice).split()) != reviewed, (
        "a heading quoting an advertised id in a class value is still treated as "
        "the slice boundary: the claim under it is never reviewed"
    )
    assert {"allowance", "falkor", "tier"} <= set(_banned_vocabulary(quoting_slice)), (
        "the vocabulary pin does not see the text under a heading that merely "
        "quotes an advertised id"
    )

    # ── the `in select` rule, measured at the parser (the shape above is the
    # consequence) ──────────────────────────────────────────────────────────
    # Asserted directly so the model itself cannot be removed silently: inside a
    # `<select>` — including under an `<option>`/`<optgroup>` — a `<h2>` is NOT
    # BOUNDED, and outside one the same tag is. ⛔ "Not bounded" and not "not
    # created": the classic mode dropped the start tag, the current standard no
    # longer lists that mode and may well create the element, so the browser answer
    # is not settled — and the refusal is the conservative side of it (bounding at
    # a heading the pins cannot see is the fail-open one). Only the reasoning is
    # pinned here; a comment claiming fidelity instead would invite someone to
    # delete the guard as redundant.
    assert extract_heading_positions('<select><h2 id="qs">Q</h2></select>') == [], (
        "a `<h2>` inside a `<select>` is BOUNDED as a heading: the classic `in "
        "select` mode dropped it and the current standard may create it, so "
        "neither answer is settled — but bounding at it is the fail-open one, so "
        "this refuses to"
    )
    assert extract_heading_positions(
        '<select><optgroup><option><h2 id="qs">Q</h2></option></optgroup></select>'
    ) == [], (
        "a `<h2>` nested under `<optgroup><option>` inside a `<select>` is "
        "bounded as a heading: ancestry, not the immediate parent, is the rule"
    )
    assert extract_heading_positions(
        '<select><h2 id="qs">Q</h2></select><h2 id="after">A</h2>'
    ) == [
        ("after", len('<select><h2 id="qs">Q</h2></select>')),
    ], (
        "the `in select` rule leaked past the closing `</select>`: a heading "
        "after the select is a real heading and must be recorded"
    )
    # ── and the refusal cannot be POPPED by an end tag in a template ─────────
    # `</select>` written inside `<template>` is processed against the template's
    # own fragment stack, which never holds the outer select — so a browser leaves
    # that select open and the heading is still one no reader meets. Popping it here
    # recorded the heading and bounded the slice: the safety rule above, off by one
    # stray end tag (#14073 review, measured).
    assert extract_heading_positions(
        '<select><template></select></template><h2 id="qs">Q</h2></select>'
    ) == [], (
        "a `</select>` written inside `<template>` popped the outer select, so a "
        "heading the `in select` rule refuses was recorded and bounded the slice"
    )
    # The same seam, one recording over, and BOTH of its directions: a `</a>`-style
    # end tag written in a template must not close the outer TOC block (a browser
    # processes it against the template's fragment stack), so the entry after the
    # template is still an entry — and no entry is invented from one before it.
    assert extract_anchors_within(
        '<div class="toc"><select><template></select></template>'
        '<a href="#x">x</a></select></div>',
        tag="div",
        class_token="toc",
    ) == [[]], (
        "an anchor inside a `<select>` whose `</select>` was written in a template "
        "is advertised as a TOC entry: the refusal was popped by the stray end tag"
    )
    assert extract_anchors_within(
        '<div class="toc"><template></div></template><a href="#x">x</a></div>',
        tag="div",
        class_token="toc",
    ) == [["#x"]], (
        "a `</div>` written inside `<template>` closed the outer TOC block: a "
        "browser ignores it there, so the entry after the template is still inside "
        "the block and must stay advertised"
    )


def test_the_select_scope_closes_on_input_and_a_nested_select_and_nothing_else() -> None:
    """`<input>` and a nested `<select>` pop an open `<select>` — nothing else does.

    The living standard has no "in select" insertion mode (`grep -c 'in select'
    parsing.html` is 0; the sequence runs "in cell" → "in template"), which this
    module's neighbouring control already notes. What survives is a per-token rule
    in "in body":

      `<input>` — *"If the stack of open elements has a select element in scope:
      Parse error. Repeatedly pop the current node off the stack of open elements
      until a select element has been popped."* → pops, and is inserted.
      `<select>` — *"Parse error. Ignore the token. Repeatedly pop …"* → pops AND
      creates no element of its own.
      `<textarea>` — *"Insert an HTML element for the token … Switch the tokenizer
      to the RCDATA state"* — no select-scope step, so it does NOT pop.
      `keygen` — has its own start-tag rule (it is in *"area, br, embed, img,
      keygen, wbr"*) and that rule has no select-scope step, so it does NOT pop
      either. ⛔ It is in the standard; an earlier revision of this docstring said
      it was not.

    ⛔ THE TWO EXTRA TOKENS ARE NOT A HARMLESS OVER-REACH — THEY ARE THE FAIL-OPEN
    DIRECTION, which is why this test pins their ABSENCE as hard as `input`'s
    presence. They were copied from the removed classic mode in the first revision
    of this fix (#14073 review); with them in the set, a `<select><textarea>` decoy
    inside the TOC block pops the select, so an anchor inside it is advertised and
    the slice BOUNDS there — a contradicting sentence after an un-advertised
    `<h2 id="storage-limits">` then sits outside the golden and the vocabulary pin
    with both green. That is the exact escape this PR exists to close.

    The positive case is the measured one: on `<select><input><a href="/blog">`,
    `origin/main` reported `['/blog']` and the pre-fix branch reported `[]` — a link
    every browser renders, reported absent to `_offers_blog_entry` and to the E2E
    legal-pages consumer.

    ⛔ THE SET IS AN ALLOWLIST, AND THE TWO ROWS ABOVE ARE A BANNED LIST. Rows that
    name the tokens which MUST NOT pop only constrain the tokens they name: a THIRD
    token added to `_SELECT_POP` was measured leaving this whole module green
    (`frozenset({'input', 'div', 'span'})` → `103 passed, 1 skipped`), so the
    mechanism this test exists to protect was unguarded against exactly the edit that
    widens it. End-to-end, with `div`/`span` added and a decoy
    `<select><div></div><a href="#storage-limits">` inside the TOC block, the slice
    bounded at that anchor, came out EQUAL to the reviewed wording, and the
    allowlist, the destinations, the vocabulary scan and the golden were all green
    while a contradicting `#storage-limits` heading sat outside it — the same
    fail-open, one token over. The allowlist row below closes that: it pins the SET,
    so adding any member reds here by name rather than silently widening the escape
    (#14073 review round 8, P2).
    """
    # ── THE SET ITSELF: an allowlist of the one token the spec pops on ──
    declared_pop = set(_AnchorHrefParser._SELECT_POP)
    assert declared_pop == {"input", "select"}, (
        "`_SELECT_POP` changed: every member of this set CLOSES an open `<select>`, so "
        "an extra one advertises anchors inside a select and bounds the slice at a "
        "heading no reader meets. Allowlist the two tokens the living standard names "
        "(`input` pops the scope, a nested `select` pops the outer one and is then "
        "IGNORED — see `_SELECT_POP_IGNORED`); do not ban the ones reverted here, or "
        "the next added token is unpinned — measured: with only 'input' in the set, "
        "`{'input', 'div', 'span'}` was still 103 passed, 1 skipped, and a decoy "
        "`<select><div><a href=\"#storage-limits\">` in the TOC block left every "
        "storage pin green")
    # ── AND THE SAME FOR THE IMPLIED-`</p>` SET, for the same reason ──
    #
    # `_P_CLOSERS` decides which start tags close an open `<p>` (and with it
    # everything opened inside that `<p>`). It was an unpinned set too, and it was
    # measured wrong the first time: `li`, `dd` and `dt` carry their own "close a `p`
    # element" step and were missing, so `<p>one<li>two` left a `<p>` open here that
    # every browser closes (Chrome: 0 element children in the `<p>`,
    # `nextElementSibling` `LI`; `DD`/`DT` likewise — and `<td>`, the control, inserts
    # nothing outside a table and leaves the `<p>` open). The omission was the safe
    # direction, but a set this stack's ancestry depends on must red when it MOVES.
    assert set(_AnchorHrefParser._P_CLOSERS) == {
        "address", "article", "aside", "blockquote", "center", "dd", "details",
        "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure",
        "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hgroup", "hr", "li", "listing", "main", "menu", "nav", "ol", "p",
        "plaintext", "pre", "search", "section", "summary", "table", "ul", "xmp",
    }, (
        "`_P_CLOSERS` changed: each member's start tag CLOSES an open `<p>` in a "
        "browser, and closing it decides which ancestors the summary and any painted "
        "text inherit from. `li`/`dd`/`dt` belong (their own spec step closes a `<p>`); "
        "`search` is the ROUND-12 addition (its own spec step closes a `<p>`; Chrome: "
        "`<p><audio><search>CLAIM` dumps as `<p><audio></audio></p><search>CLAIM`, so "
        "the claim is painted while this stack dropped it). `plaintext` belongs too, "
        "and it is the ROUND-10 addition: its own spec step "
        "closes a `<p>`, and Chrome agrees — `<p>one<plaintext>two<p>three` dumps as "
        "`<p>one</p><plaintext>two<p>three</plaintext>`, i.e. the `<p>` closed and "
        "everything after is plaintext's text. `td` does not (outside a table it "
        "inserts nothing). Do not widen or narrow this by guess — drive the shape in "
        "Chrome first")
    # ── PRESENT: `<input>` pops the scope, so what follows is outside the select ──
    assert extract_anchor_hrefs(
        '<select><input><a href="/blog">Blog</a></select>'
    ) == ["/blog"], (
        "`<input>` did not pop the open `<select>`: an anchor after it is a link "
        "every browser renders, and dropping it blinds the blog guard to a link a "
        "visitor can click"
    )
    assert extract_heading_positions(
        '<select><input><h2 id="qs">Q</h2></select>'
    ) == [("qs", len("<select><input>"))], (
        "a `<h2>` after `<input>` in a select was treated as a heading no reader "
        "meets: the select is already popped in the browser, so this is a rendered "
        "heading and must bound the slice"
    )
    # ── PRESENT: a NESTED `<select>` pops the outer one and is IGNORED whole ──
    # Spec: *"Parse error. Ignore the token. Repeatedly pop the current node off
    # the stack of open elements until a select element has been popped."* So the
    # outer select closes, the inner one never exists, and an anchor after it is a
    # rendered sibling link. Measured in Chromium:
    # `<select id=o><select id=i><a href="/blog">` -> one select (`o`), anchor a
    # sibling `<a>` in the body. Without the IGNORE half the anchor would still be
    # suppressed by an element the browser discarded.
    assert extract_anchor_hrefs(
        '<select><select><a href="/blog">Blog</a></select></select>'
    ) == ["/blog"], (
        "a nested `<select>` did not pop the outer one: an anchor after it is a "
        "link every browser renders"
    )
    assert extract_element_ids(
        '<select id="o"><select id="i"><a href="/b">B</a></select></select>'
    ) == [("o", 1)], (
        "a nested `<select>` created an element of its own: the spec says IGNORE "
        "the token after popping, and a browser has only the outer select, so "
        "recording the inner `id` invents an element that does not exist"
    )
    assert extract_heading_positions(
        '<select><select><h2 id="qs">Q</h2></select></select>'
    ) == [("qs", len("<select><select>"))], (
        "a `<h2>` after a nested `<select>` was treated as a heading no reader "
        "meets: the outer select is popped and the inner one ignored, so this is a "
        "rendered heading and must bound the slice"
    )
    # ── ABSENT: `textarea`/`keygen` do NOT pop — the scope stays open ──
    for token in ("textarea", "keygen"):
        closing = f"</{token}>"
        assert extract_anchor_hrefs(
            f'<select><{token}>{closing}<a href="#x">x</a></select>'
        ) == [], (
            f"`<{token}>` popped the open `<select>`: the living standard has no "
            "select-scope step for it, so the anchor after it is still inside the "
            "select and still no link — popping here advertises a claim the TOC "
            "bound must not see (the fail-open direction)"
        )
        assert extract_heading_positions(
            f'<select><{token}>{closing}<h2 id="qs">Q</h2></select>'
        ) == [], (
            f"a `<h2>` after `<{token}>` in a select was BOUNDED: `{token}` does "
            "not pop the select in the living standard, so this is a heading no "
            "reader meets and bounding at it is the fail-open escape"
        )
    # The decoy shape itself, end to end: an anchor inside a select whose only
    # "pop" is a `<textarea>` must NOT be advertised as a TOC entry.
    assert extract_anchors_within(
        '<div class="toc"><select><textarea></textarea>'
        '<a href="#storage-limits">x</a></select></div>',
        tag="div",
        class_token="toc",
    ) == [[]], (
        "a `<select><textarea>` decoy advertised a TOC entry: `textarea` does not "
        "pop the select, so that anchor is one a reader cannot follow and bounding "
        "the slice on it leaves a claim outside every pin"
    )
    # ── UNCHANGED: an anchor with the select genuinely open is still no link ──
    assert extract_anchor_hrefs(
        '<select><a href="#x">x</a></select><a href="#y">y</a>'
    ) == ["#y"], (
        "the select-pop rule widened the link set for a genuinely open select"
    )
    # ── IN SCOPE IS THE PRECONDITION: a boundary between select and token ──
    # ⛔ "IS ANY `<select>` OPEN?" IS NOT THE SPEC'S QUESTION (#14073 review round 14,
    # P2, measured). The rule reads *"If the stack of open elements has a select
    # element in scope"*, and §13.2.4.2 puts `table`/`td`/`th`/`caption`/`object`/
    # `template` in the scope-boundary list — so a cell between the current node and
    # the select makes the answer NO and NO POP happens, exactly as for the implied
    # `</p>`. Measured in Chrome: `aInSelect` true and `innerText` empty (the anchor
    # is INSIDE the still-open select, i.e. no link), while the control below has
    # `aInSelect` false and `innerText` 'x'.
    assert extract_anchor_hrefs(
        '<select><table><td><input><a href="/blog">x'
    ) == [], (
        "the `<input>` popped a `<select>` it is not IN SCOPE of: the table cell is a "
        "scope boundary, so a browser keeps the select open and the anchor is no "
        "link — reporting it advertises a 'way in' no browser exposes"
    )
    assert extract_anchor_hrefs('<select><input><a href="/blog">x') == ["/blog"], (
        "premise: with nothing between them the `<input>` IS in the select's scope "
        "and the pop must still happen (Chrome: `aInSelect` false, `innerText` 'x')"
    )
    # ⛔ THE IGNORED-TOKEN ANSWER IS GATED BY THE SAME SCOPE QUESTION, and this row holds
    # the two apart. A `<select>` start tag that a browser IGNORES after popping is only
    # ignored when a pop actually HAPPENED; but the pop is itself scope-gated, and the
    # scope gate lives inside `_close_extent`. So a nested `<select>` behind a cell
    # boundary — where the outer select is NOT in scope and no pop happens — still opens
    # a real second select, and the browser keeps the anchor inside it. Measured in
    # Chrome: two selects, `a` chained to the INNER select, `innerText ''`, width 0 — so
    # reporting the href would advertise a second "way in" that no browser exposes.
    # (Without this row the outer scope check can be deleted and nothing reds, because
    # `_close_extent` refuses the pop on its own while `_close_select_scope` still
    # answers "ignored" — a false red the shape below is the only witness to.)
    assert extract_anchor_hrefs(
        '<select><table><td><select><a href="/blog">x'
    ) == [], (
        "a nested `<select>` behind a cell boundary did NOT pop anything (the outer "
        "select is out of scope), so it is NOT an ignored token: the browser opens a "
        "real second select and the anchor is inside it (Chrome: 2 selects, "
        "`innerText ''`, width 0) — reporting it advertises a link no reader meets"
    )
    # ⛔ TWO MORE SHAPES WHERE A VIRTUAL "CLOSE" WAS A LINK A BROWSER NEVER EXPOSES
    # (#14073 review round 19, P1 + P2, measured). Both are the SAME class as the rows
    # above — an element the model removed while the browser kept it — reached through
    # the sibling close and the table start-tag path rather than through select scope.
    #
    # (a) THE ADOPTION AGENCY KEEPS ITS `furthestBlock`. AAA removes only the nodes
    # BETWEEN the formatting element and that block, and `<select>` is SPECIAL, so:
    #   `<a><select><a href="/blog">L</a>`  -> Chrome `a > select > a`, anchor 0×0, unclickable
    # (a `<li>`/`<nobr>` in place of the outer `<a>` behaves identically.)
    #
    # (b) `<table><select><table><a href="/blog">` -> Chrome CLOSES the select at the
    # inner `<table>` (`<select></select><table></table><a href…`), anchor 10×17 and NOT
    # inside a select — so this model used to DROP a link a reader can click (the loud
    # direction: the golden would cut the slice short on a correct page). Its control is
    # the row above: a `<table>` with no select already open does NOT close it.
    for label, markup, expected in (
        ("the AAA furthest block", '<a><select><a href="/blog">L</a>', []),
        ("the li sibling close", '<ul><li><select><li><a href="/blog">L</a></ul>', []),
        ("the nobr sibling close", '<nobr><select><nobr><a href="/blog">L</a>', []),
        ("a table in a foster-parented select",
         '<table><select><table><a href="/blog">C</a>', ["/blog"]),
        # …and BOTH rules hold at DEPTH (#14073 review round 20, P1 + P2, measured).
        # The furthest block is the NEAREST special above the match, so a wrapper
        # inside the select does not lose the select frame: Chrome has this anchor
        # `closest('select')` at 0×0. Scanning top-down for the deepest special kept
        # only the wrapper and advertised the href.
        ("a wrapper inside the AAA's select", '<a><select><div><a href="/blog">L</a>', []),
        ("an option inside the AAA's select",
         '<a><select><option><div><a href="/blog">L</a>', []),
        # …and the table rule fires from any depth, so a rendered link is not dropped:
        # Chrome closes the select at that `<table>` and the anchor is 9.8×17 outside it.
        ("a table two levels inside the select",
         '<table><select><div><table><a href="/blog">L', ["/blog"]),
        ("an optgroup between the table and the select",
         '<table><select><optgroup><table><a href="/blog">L', ["/blog"]),
        # …and only a FOSTER-PARENTED select closes (#14073 review round 21, P1,
        # measured). A select inside a `<caption>`/cell is a legitimate table descendant
        # and the browser keeps it open across a later `<table>` — the anchor is 0×0
        # and `closest('select')`. A select in a ROW is foster-parented like one in the
        # table itself, so that one still closes (anchor 11×17, outside).
        ("a select in a caption", '<table><caption><select><table><a href="/blog">B</a>', []),
        ("a select in a cell",
         '<table><tr><td><select><table><a href="/blog">B</a>', []),
        ("a select in a row", '<table><tr><select><table><a href="/blog">B</a>', ["/blog"]),
    ):
        assert extract_anchor_hrefs(markup) == expected, (
            f"{label}: an element a browser KEEPS was removed, or one it CLOSES was "
            f"kept — measured in Chrome, the anchor is 0×0 inside the select in the "
            f"three AAA/sibling shapes and is a rendered sibling link after the table "
            f"closes the select in the fourth"
        )
    # ⛔ THE SIBLING CLOSE ASKS "IN SCOPE" TOO (#14073 review round 21, P1, measured).
    # A `<button>`'s rule is scope-gated, and a `<select>` is an ordinary scope boundary,
    # so no pop happens across one — Chrome keeps the anchor inside the select at 0×0
    # while popping through it advertised the href. The control with no boundary pops
    # and Chrome exposes that link (9×15).
    assert extract_anchor_hrefs(
        '<button><select><div><button><a href="/blog">B</a></div></select></button>'
    ) == [], (
        "a `<button>` sibling close popped THROUGH a `<select>`: `select` is an "
        "ordinary scope boundary, so the browser never pops the outer button and the "
        "anchor stays inside the select (Chrome: `closest('select')`, 0×0)"
    )
    assert extract_anchor_hrefs('<button><div><button><a href="/blog">B</a>') == ["/blog"], (
        "premise: with no scope boundary between them the pop DOES happen and the "
        "anchor is a rendered link (Chrome: 9×15, not in a select)"
    )
    assert extract_anchor_hrefs('<select><table><td><input><h2 id="qs">Q</h2>'
    ) == [], (
        "a `<h2>` after an `<input>` that never popped the select was BOUNDED: the "
        "cell is a scope boundary so the select survives, and a heading inside it is "
        "one no reader meets — bounding at it is the fail-open escape"
    )
    # ── `select` IS ITSELF A SCOPE BOUNDARY (#14073 review round 15, P1, measured) ──
    # §13.2.4.2's scope-element list contains `select`, so a `<p>` beyond an open
    # `<select>` is NOT in button scope and the implied close must not run. Without the
    # member the walk went past the select, closed the `<p>` and popped the select with
    # it, so a heading and an anchor a browser never renders were recorded/collected.
    # Chrome: the `<h2>` inside a `<select>` has a 0×0 rect, and `<select><div><a href>`
    # has `innerText ''` with the anchor inside the control.
    assert {"select"} <= set(_AnchorHrefParser._P_SCOPE_BOUNDARY) == set(
        _AnchorHrefParser._SCOPE_BOUNDARY
    ) | {"button"}, (
        "`select` left the scope-boundary list (or the two lists stopped differing by "
        "`button` alone): a `<p>` or `<ruby>` beyond an open `<select>` is then treated "
        "as in scope, the select is popped by the implied close, and a heading/anchor "
        "inside it is recorded although a browser renders none of it"
    )
    assert extract_heading_positions('<p><select><h2 id="q">Q</h2>') == [], (
        "a `<h2>` inside a `<select>` was BOUNDED: Chrome gives it a 0×0 rect, so it "
        "is no heading a reader meets and bounding the slice at it leaves a claim "
        "outside every pin"
    )
    assert extract_anchor_hrefs(
        '<p><select><div><a href="/blog">x</a></div>'
    ) == [], (
        "an anchor inside a `<select>` was reported as a link: Chrome keeps the div "
        "and the anchor INSIDE the control (rect 0×0, `innerText` empty), so this is "
        "a 'way in' no browser exposes"
    )
    # ── NEGATIVE CONTROL: the pop must not fire from inside an inert `<template>` ──
    # A browser processes that token against the template's own fragment stack,
    # which never holds the outer select — so the select survives it.
    assert extract_anchor_hrefs(
        '<select><template><input></template><a href="#x">x</a></select>'
    ) == [], (
        "an `<input>` written inside an inert `<template>` popped the outer "
        "`<select>`: a browser processes it against the template's fragment stack, "
        "which never holds that select, so the anchor after it is still one no "
        "reader meets"
    )


#: Spellings that name THIS page and carry a fragment, and are therefore
#: advertisements of a heading on it. All are legal HTML, all rendered by a browser.
_SAME_PAGE_TOC_SPELLINGS = (
    ("leading space", ' href=" #quickstart"'),
    ("wrapped in newlines", ' href="\n        #quickstart\n      "'),
    ("absolute path", ' href="/tortoise/docs#quickstart"'),
    ("retired alias path", ' href="/docs#quickstart"'),
    ("relative file", ' href="docs.html#quickstart"'),
    # A RELATIVE href is rooted at the PAGE's URL (`/tortoise/docs`), not at the site
    # root: `../docs` is `/docs`, which serves this page. Rooting at `/` instead both
    # rejected this spelling and ACCEPTED `tortoise/docs` (see the loud rows below).
    ("parent-relative path", ' href="../docs#quickstart"'),
    ("full URL", ' href="https://premiselabs.co/tortoise/docs#quickstart"'),
    # ⛔ ORIGIN, NOT NETLOC STRING. A browser sends all of these to this page, and a
    # raw `netloc` equality test refused them — the false-red family this commit is
    # closing, one spelling over (#14073 review, measured).
    ("explicit default port", ' href="https://premiselabs.co:443/tortoise/docs#quickstart"'),
    ("uppercase scheme and host", ' href="HTTPS://PREMISELABS.CO/tortoise/docs#quickstart"'),
    ("userinfo", ' href="https://someone@premiselabs.co/tortoise/docs#quickstart"'),
    # ⛔ A PERCENT-ENCODED FRAGMENT is what a browser DECODES before matching an
    # element's id, so this entry does reach `#quickstart`; comparing the raw
    # fragment dropped it (the same false-red family).
    ("a percent-encoded fragment", ' href="#quick%73tart"'),
    # ⛔ PINNED IN THE OTHER DIRECTION TOO: a scheme downgrade is a navigation onto
    # this page (a browser upgrades or redirects it), so it advertises. Normalizing
    # the scheme here was a silent reversal until this row existed.
    ("scheme downgrade", ' href="http://premiselabs.co/tortoise/docs#quickstart"'),
    ("scheme downgrade, explicit port", ' href="http://premiselabs.co:80/tortoise/docs#quickstart"'),
    # ⛔ A BACKSLASH IN THE QUERY IS NOT A SEPARATOR TO ANY PARSER — WHATWG converts `\`
    # only in the authority and path states — so this href does reach `#quickstart`.
    # The backslash refusal is scoped to authority+path for exactly this row.
    ("backslash in the query", ' href="/tortoise/docs?q=a\\b#quickstart"'),
)

#: Entries that do NOT name a heading on THIS page — a browser leaves this document, or
#: 404s. Accepting any of them GROWS the advertised set, which is the fail-open
#: direction (a larger set cuts the slice earlier). Each is a spelling a human can
#: actually author, which is why they are pinned rather than argued away.
_FOREIGN_TOC_SPELLINGS = (
    ("another page's fragment", ' href="/tortoise/faq#quickstart"'),
    ("a foreign host", ' href="//evil.example/docs#quickstart"'),
    ("a foreign host with a scheme", ' href="https://evil.example/docs#quickstart"'),
    ("a non-http scheme", ' href="mailto:team@premiselabs.co#quickstart"'),
    ("a non-web scheme", ' href="ftp://premiselabs.co/tortoise/docs#quickstart"'),
    # ⛔ A PATH PARAMETER IS PART OF THE PATH A BROWSER REQUESTS (`urlsplit` keeps it,
    # `urlparse` moves it to `.params`), so `/tortoise/docs;x` is a 404 the middleware
    # answers, not this page: resolving it to `docs.html` made a dead URL read as live
    # and advertised a boundary from it (#14073 review, measured).
    ("path parameters", ' href="/tortoise/docs;x#quickstart"'),
    # ⛔ AND A BROKEN AUTHORITY MUST REFUSE, NOT RAISE: `///[::1` puts an invalid IPv6
    # literal where `urlparse` cannot parse it, and an uncaught `ValueError` out of
    # `_docs_section` is an ERROR naming no cause. Measured: `None` before the
    # authority probe existed, then `ValueError("Invalid IPv6 URL")` (#14073 review).
    # ⛔ BOTH SPELLINGS, because the first fix only guarded the probe: the `//[::1`
    # form is parsed BEFORE the probe and still raised — the class the review named is
    # only closed when every parser the function touches is guarded (#14073 review).
    ("an invalid IPv6 authority", ' href="///[::1#quickstart"'),
    ("an invalid IPv6 authority, no slash run", ' href="//[::1#quickstart"'),
    ("an empty IPv6 authority", ' href="//[]#quickstart"'),
    # ⛔ THE BACKSLASH SMUGGLE. In a special URL a browser treats `\\` as `/`, so it
    # TERMINATES the authority: `https://evil.example\@premiselabs.co/...` has host
    # `evil.example` to a browser and host `premiselabs.co` to every Python parser.
    # Accepting it let a foreign host advertise a heading on this page — measured
    # end-to-end with the pinned `storage-limits` shape, all three pins green.
    ("a backslash authority smuggle", ' href="https://evil.example\\@premiselabs.co/tortoise/docs#quickstart"'),
    # ⛔ EMPTY PATH PARAMETERS, which `urljoin` erases on its own (`urlparse`'s
    # `.params` is dropped when empty), so the `;x` row above did not cover it: the
    # middleware 404s `/docs;` exactly as it 404s `/docs;x`.
    ("empty path parameters", ' href="/tortoise/docs;#quickstart"'),
    # ⛔ THE SEPARATE PAGES PROJECT. `app.premiselabs.co` serves no docs at all, and
    # `_document_for_loc` is host-blind below the site root, so a path-only rule
    # accepted it (`https://app.premiselabs.co/docs#quickstart` → this document).
    ("the dashboard host", ' href="https://app.premiselabs.co/tortoise/docs#quickstart"'),
    ("a retired host", ' href="https://tortoise.premiselabs.co/tortoise/docs#quickstart"'),
    # ⛔ MIS-ROOTED RELATIVE — a browser resolves `tortoise/docs` from `/tortoise/docs`
    # to `/tortoise/tortoise/docs`, which the middleware 404s (`productRest` + the
    # `PRODUCT_PAGES` gate). Rooting it at `/` made it look like this page.
    ("a mis-rooted relative path", ' href="tortoise/docs#quickstart"'),
    # ⛔ THE WHATWG/RFC-3986 SPLIT. `urljoin` reads the third slash as a PATH character
    # and keeps this page's host; a browser SKIPS the extra slashes and parses the next
    # segment as the AUTHORITY, so these five leave the site (`///docs#q` is
    # `https://docs/#q`, verified against Node's WHATWG resolver). The two `https:///…`
    # spellings were ACCEPTED by the previous revision of this function (#14073 review,
    # measured) — the authority is parsed the way the browser does now, not the way
    # `urljoin` reports it.
    ("three leading slashes", ' href="///docs#quickstart"'),
    ("three leading slashes, path", ' href="///tortoise/docs#quickstart"'),
    ("four leading slashes", ' href="////docs#quickstart"'),
    ("empty-authority scheme", ' href="https:///docs#quickstart"'),
    ("empty-authority scheme, path", ' href="https:///tortoise/docs#quickstart"'),
    # ⛔ A NON-DEFAULT PORT IS NOT THIS DOCUMENT'S ORIGIN, and neither is a port the
    # URL parser cannot even read. Both stay unadvertised (the loud direction), and the
    # unreadable one is ANSWERED rather than raised: an exception out of this predicate
    # would surface as an ERROR in the bound and name no cause (#14073 review).
    ("a non-default port", ' href="https://premiselabs.co:8443/tortoise/docs#quickstart"'),
    ("an unreadable port", ' href="https://premiselabs.co:99999999/tortoise/docs#quickstart"'),
)


def test_the_advertised_set_is_a_url_question_not_a_string_prefix() -> None:
    """An in-page TOC entry is a URL, not a literal `'#'`-prefixed string (#14073).

    `advertised = {href[1:] for href in tocs[0] if href.startswith("#")}` dropped
    two legal spellings of an entry that DOES navigate to a heading on this page: a
    value the URL parser strips to a fragment (`href=" #quickstart"`, or a value
    wrapped in newlines) and an absolute link to the same document
    (`/tortoise/docs#quickstart`, `/docs#quickstart`, `docs.html#quickstart`, the
    full-URL form). Each loss SHRANK the advertised set, so the slice ran past
    `#quickstart` and the golden reddened on copy nobody touched — the same
    false-red family as `href='#x'`/`href=#x`, whose prose claimed the class was
    closed (measured, #14073 review).

    Both directions are asserted here: every legal same-page spelling must BOUND at
    `quickstart` exactly as the unmutated page does, and a link that does NOT name
    this document must not bound there — a larger advertised set is a shorter
    slice, the fail-open direction. ⛔ Both halves of "does not name this document"
    are pinned: navigation AWAY (`/tortoise/faq`, a foreign host, `mailto:`) and the
    two spellings a PATH-ONLY rule wrongly accepted — the dashboard host (a separate
    Pages project with no docs) and a mis-rooted relative path (`tortoise/docs`,
    which a browser resolves to `/tortoise/tortoise/docs`, a 404). See
    `_fragment_this_page_advertises`.
    """
    page = _read(DOCS)
    entry = '<a href="#quickstart">Quickstart (5 minutes)</a>'
    assert entry in page, (
        "website/docs.html no longer carries the TOC entry this control mutates, so "
        "it cannot measure the advertised-set rule"
    )
    reviewed_slice = _docs_section()
    for label, replacement in _SAME_PAGE_TOC_SPELLINGS:
        mutant = page.replace(entry, f"<a{replacement}>Quickstart (5 minutes)</a>", 1)
        assert mutant != page, f"could not rewrite the TOC entry for the {label} case"
        assert _docs_section(page=mutant) == reviewed_slice, (
            f"a TOC entry spelled as an {label} stopped advertising `quickstart`, "
            f"so the #storage slice ran past the section and the golden reddened "
            f"on copy nobody touched"
        )
    for label, replacement in _FOREIGN_TOC_SPELLINGS:
        mutant = page.replace(entry, f"<a{replacement}>Quickstart (5 minutes)</a>", 1)
        assert mutant != page, f"could not rewrite the TOC entry for the {label} case"
        wider = _docs_section(page=mutant)
        assert len(wider) > len(reviewed_slice), (
            f"an entry pointing at {label} was accepted as an advertisement of a "
            f"heading on THIS page, so the advertised set grew and the slice "
            f"shrank — the fail-open direction"
        )
        assert " ".join(_visible_text(wider).split()) != " ".join(
            _visible_text(reviewed_slice).split()
        ), (
            f"the {label} case did not move the slice at all, so it is not "
            f"measuring the advertised-set rule"
        )


def test_the_reader_met_rule_refuses_every_declared_unrendered_class() -> None:
    """A heading no supported browser renders must not bound or advertise (#14073).

    The rule is `_unmet_by_reader`, and it must cover EVERY class it declares —
    element types, the `hidden` attribute (own and inherited), and
    `<details>`/`<dialog>` before an `open` arrives. One class left unmodelled is
    enough to re-open the fail-open the negative control pins: a heading in the
    tree that a reader never meets, bounding the slice so the claim under it leaves
    the golden, the vocabulary pin and the allowlist at once (measured).

    Each positive control is the same shape WITHOUT the invisibility, so a rule
    that simply refused everything would red here instead of passing.
    """
    # ⛔ THE SET UNDER TEST IS NAMED HERE, not read back out of `_UNRENDERED` — a loop
    # over the declaration alone passes when a member is DELETED from it, which is the
    # defect this test exists to pin (the same self-referential shape the
    # `_IMPLICITLY_CLOSED` ratchet had; measured: dropping `object`, `audio`, `meter`,
    # `progress` left the loop version green). The named set is a SUPERSET check on the
    # declaration plus a behavioural check per tag.
    unrendered = {
        "audio", "canvas", "datalist", "meter", "object", "progress", "video",
        "rp",
    }
    assert not unrendered - set(_AnchorHrefParser._UNRENDERED), (
        f"`_UNRENDERED` no longer declares "
        f"{sorted(unrendered - set(_AnchorHrefParser._UNRENDERED))}: a `<h2>` there is "
        f"recorded again, bounds the slice, and the claim under it leaves all three "
        f"pins"
    )
    for element in sorted(unrendered):
        assert [
            heading_id
            for heading_id, _ in extract_heading_positions(
                f'<{element}><h2 id="qs">Q</h2></{element}>'
            )
        ] == [], (
            f"a `<h2>` inside `<{element}>` is recorded as a heading: a supporting "
            f"browser renders no child of it, so bounding there is the fail-open "
            f"direction"
        )
    for label, markup in (
        ("heading itself hidden", '<h2 hidden id="qs">Q</h2>'),
        ("inherited from a div", '<div hidden><section><h2 id="qs">Q</h2></section></div>'),
        ("inherited through a wrapper", '<div hidden><div class="x"><h2 id="qs">Q</h2></div></div>'),
        ("details without open", '<details><h2 id="qs">Q</h2></details>'),
        ("dialog without open", '<dialog><h2 id="qs">Q</h2></dialog>'),
        # ⛔ A CLOSED `<details>`'s `<summary>` IS RENDERED, and this refusal is a
        # DELIBERATE over-refusal in the loud direction, pinned so it reads as a
        # decision: modelling "the first summary renders, a second one does not" is
        # not attempted, because recording a heading in that second summary would
        # bound the slice at something no reader meets — the fail-open direction.
        # The cost is a heading in a real summary does not bound: the slice runs
        # long and the extra text stays pinned.
        ("summary of a closed details", '<details><summary><h2 id="qs">Q</h2></summary></details>'),
        # ⛔ `<option>` IS NOT AN ELEMENT-TYPE MEMBER OF `_UNRENDERED`, and this row is
        # why the rule it needs is `_UNBOUNDABLE_ANCESTORS` instead: its CHILD
        # HEADING is unrendered (0x0 rect, 0 painted px — measured), but its own
        # DIRECT TEXT paints, so the refusal must stay on the BOUNDARY question. A
        # heading inside a bare `<option>` bounded the #storage slice with the claim
        # under it outside every pin (#14073 review round 8, measured).
        ("bare option", '<option><h2 id="qs">Q</h2></option>'),
        # ⛔ `<rp>` IS THE OPPOSITE CASE and belongs in `_UNRENDERED`: a ruby
        # parenthesis is `display: none` (HTML Standard rendering; CSS Ruby's sample UA
        # stylesheet) and NOTHING inside it paints — the heading's rect is 0x0 and its
        # own text paints 0 px. Recorded before this row existed: a `<rp>` decoy BOUND
        # the slice (#14073 review round 9).
        ("ruby parenthesis", '<rp><h2 id="qs">Q</h2></rp>'),
        # the option refusal is ANCESTRY, so the group around it is covered too
        (
            "optgroup over a bare option",
            '<optgroup><option><h2 id="qs">Q</h2></option></optgroup>',
        ),
    ):
        assert extract_heading_positions(markup) == [], (
            f"a `<h2>` is recorded although no reader meets it ({label}): bounding "
            f"at it leaves the text under it outside every pin"
        )
    # ── and the unboundable ancestors stay BOUNDARY-ONLY ──────────────────
    # ⛔ THE REFUSAL MUST NOT MOVE INTO `_UNRENDERED`. That set feeds the TEXT
    # question too, and `<option>`'s own DIRECT TEXT is rendered: measured on this
    # box's Chrome, `<option>TAIL</option>` paints 244 px and
    # `<option><h2>HELLO</h2>TAIL</option>` paints 613 px (TAIL — the heading is
    # 0x0). A future edit that "simplifies" the rule into `_UNRENDERED` would DROP
    # painted copy from the visible-text golden, which is the fail-open direction
    # this file has already paid for three times (`<summary>`, `<object>`).
    assert not {"select", "option"} & set(_AnchorHrefParser._UNRENDERED), (
        "an unboundable ancestor was added to `_UNRENDERED`: that set also drives "
        "`extract_visible_text`, so the element's own rendered DIRECT text would be "
        "dropped and a claim written there would leave every pin green"
    )
    # …and the mirror: `<rp>` IS in it, because nothing inside it paints at all, so
    # its own text must drop too (0 painted px measured).
    assert "rp" in _AnchorHrefParser._UNRENDERED, (
        "`rp` left `_UNRENDERED`: a `<rp>` is `display: none` in the UA stylesheet, "
        "so a heading inside it is recorded again and BOUNDS the slice with the "
        "claim under it outside every pin"
    )
    assert extract_visible_text("<p>a</p><rp>PAREN</rp>").split() == ["a"], (
        "a `<rp>`'s own text is counted: nothing in a ruby parenthesis is painted, "
        "so counting it is only the false-red direction — but the reason `rp` is in "
        "`_UNRENDERED` rather than in the boundary set is that BOTH questions agree"
    )
    for label, markup, expected in (
        ("option direct text", "<option>TAIL</option>", "TAIL"),
        ("option direct text beside a heading", "<option><h2>HELLO</h2>TAIL</option>", "HELLO TAIL"),
        ("optgroup over an option", "<optgroup><option>TAIL</option></optgroup>", "TAIL"),
    ):
        assert " ".join(extract_visible_text(markup).split()) == expected, (
            f"the text recording disagrees with the measurement ({label}): an "
            f"`<option>`'s own text IS painted, so dropping it is the fail-open "
            f"direction and counting a claim beside it is only the false-red one"
        )
    # ── positive controls: the same headings where a reader DOES meet them ────
    for label, markup, expected_id in (
        ("bare heading", '<h2 id="qs">Q</h2>', "qs"),
        ("inside a plain wrapper", '<div class="wrap"><h2 id="qs">Q</h2></div>', "qs"),
        ("after the hidden subtree closes", '<div hidden></div><h2 id="qs">Q</h2>', "qs"),
        ("open details", '<details open><h2 id="qs">Q</h2></details>', "qs"),
        ("open dialog", '<dialog open><h2 id="qs">Q</h2></dialog>', "qs"),
        # the refusal ends with the ancestor: a heading AFTER the option closes still
        # bounds, so `_UNBOUNDABLE_ANCESTORS` is ancestry and not a page-wide switch
        ("after a closed option", '<option>x</option><h2 id="qs">Q</h2>', "qs"),
        # ⛔ AND A BARE `<optgroup>` IS A POSITIVE CONTROL, not a refusal: it DOES
        # render its child heading (measured — 798 ink px on a white UA page against 0
        # for an empty `<optgroup>` control, 812x38 against 812x0 on a `docs.html`
        # copy), which is why it is not in `_UNBOUNDABLE_ANCESTORS`. An earlier
        # revision refused it on a sentence the measurement falsifies, and cited pixel
        # counts that did not reproduce (#14073 review rounds 9-10).
        ("bare optgroup", '<optgroup><h2 id="qs">Q</h2></optgroup>', "qs"),
    ):
        assert [
            heading_id
            for heading_id, _ in extract_heading_positions(markup)
        ] == [expected_id], (
            f"a rendered heading is NOT recorded ({label}): the rule refuses too "
            f"much, and a heading outside the slice is copy no pin sees"
        )
    # ── and the same rule governs the advertised ENTRIES ─────────────────────
    assert extract_anchors_within(
        '<div class="toc"><datalist><a href="#x">x</a></datalist></div>',
        tag="div",
        class_token="toc",
    ) == [[]], (
        "an anchor inside `<datalist>` is advertised as a TOC entry: nothing there "
        "is rendered, so the entry adds a boundary the pins cannot verify"
    )
    assert extract_anchors_within(
        '<div class="toc"><a href="#x" hidden>x</a><a href="#y">y</a></div>',
        tag="div",
        class_token="toc",
    ) == [["#y"]], (
        "an anchor carrying `hidden` is advertised as a TOC entry, so its"
        " boundary is one no reader can reach"
    )


def test_the_link_set_and_the_boundary_set_answer_different_questions() -> None:
    """`hidden` narrows the ADVERTISED boundary, never the rendered-LINK set (#14073).

    The two questions were briefly answered by one rule, which made the blog guard
    blind to a link the page's own script reveals: `website/product.html` renders its
    OAuth error banner by clearing `el.hidden` (`#oauth-error-retry` → `/auth`), and a
    static `hidden` refusal dropped that anchor from the rendered-link set — the
    question "can a visitor reach the blog from this page?" then answers from a
    document where a link a visitor CAN click does not exist (measured: 27 links
    before the rule, 26 after; #14073 review).

    So `_collect` keeps the LINK answer as it was (`<select>` only — an `<a>` in a
    select is not clickable in any browser, under any cascade) and applies the wider
    boundary rule to the container index only. Both directions are pinned here, at the
    parser and on the real page that regressed.
    """
    hidden_link = '<a href="#x" hidden>x</a><a href="#y">y</a>'
    assert extract_anchor_hrefs(hidden_link) == ["#x", "#y"], (
        "an anchor carrying `hidden` is dropped from the rendered-link set: a page's "
        "own script can reveal it, so the link guard would report a link a visitor "
        "can click as absent"
    )
    assert extract_anchor_hrefs(
        '<select><a href="#x">x</a></select><a href="#y">y</a>'
    ) == ["#y"], (
        "an anchor inside a `<select>` is counted as a link: no browser makes it "
        "clickable, under any cascade or script"
    )
    # …and the boundary answer is still the wider one (asserted above, in
    # `test_the_reader_met_rule_refuses_every_declared_unrendered_class`).
    # The REAL page: the JS-revealed banner's link must survive.
    product = _read(WEBSITE / "product.html")
    banner_opening = '<div id="oauth-error" class="oauth-error-banner" role="alert" hidden>'
    retry_anchor = '<a id="oauth-error-retry" href="/auth">Sign up again →</a>'
    assert banner_opening in product, (
        "website/product.html's OAuth error banner is no longer a `hidden` div at "
        "this spelling, so this control no longer measures the JS-revealed case"
    )
    assert retry_anchor in product, (
        "website/product.html no longer carries the JS-revealed OAuth retry link, "
        "so this control cannot measure the link/boundary split"
    )
    assert product.index(banner_opening) < product.index(retry_anchor), (
        "the OAuth retry link is no longer INSIDE the hidden banner, so this "
        "control no longer measures a link the page's script reveals"
    )
    # ⛔ NEITHER A MEMBERSHIP TEST ON `/auth` NOR A SOURCE COUNT IS A CONTROL HERE:
    # `product.html` carries TEN `href="/auth"` occurrences and only EIGHT are real
    # anchors (two are inside a `<script>` template string), so membership is inert
    # and a raw count comparison is simply wrong (both measured, #14073 review).
    # The discriminating question is the SPLIT ITSELF: the rendered-link set must not
    # change when the `hidden` attribute on that banner is removed, because that is
    # what the page's own script does. Under the wide rule the two arrays differed by
    # exactly this link (7 vs 8).
    revealed = product.replace(banner_opening, banner_opening.replace(" hidden>", ">"), 1)
    assert revealed != product, (
        "could not remove the banner's `hidden` attribute, so this control cannot "
        "measure the split"
    )
    assert extract_anchor_hrefs(product) == extract_anchor_hrefs(revealed), (
        "`product.html`'s rendered-link set changes when the JS-revealed banner's "
        "`hidden` attribute is dropped: a static `hidden` rule is modelling a "
        "page's script"
    )


def test_a_nested_or_foreign_suffix_page_is_not_resolved_as_a_product_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `.html` spelling is accepted at the TOP level only, exactly as served (#14073).

    The middleware's `PRODUCT_PAGES` holds `/docs` and `/docs.html` and nothing else —
    no nested entry, no other suffix. A first attempt at the mirror used
    `Path(cleaned).stem`, which matches a page name at ANY DEPTH and for ANY extension:
    with a `docs.html` under a nested directory (or a `docs.tar`), a `<loc>` the
    middleware 404s resolved to a document, so a dead URL read as live in every sitemap
    walk (`test_no_sitemap_advertises_a_page_that_is_deleted_or_noindex` reports the
    dead `<loc>` only when this resolver returns None). Measured on a temp tree.
    """
    monkeypatch.setattr(
        sys.modules[__name__], "WEBSITE", tmp_path, raising=True
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "docs.html").write_text("<p>not a product page route</p>", "utf-8")
    (tmp_path / "docs.tar").write_text("not a page at all", "utf-8")
    assert _document_for_loc("https://premiselabs.co/tortoise/nested/docs") is None, (
        "a NESTED `/tortoise/nested/docs` resolves to `nested/docs.html`: the "
        "middleware's `PRODUCT_PAGES` is a set of TOP-LEVEL names, so that URL is a "
        "404 and a `<loc>` naming it would read as live"
    )
    assert _document_for_loc("https://premiselabs.co/tortoise/docs.tar") is None, (
        "`/tortoise/docs.tar` resolves as a product page: the only suffix the "
        "middleware serves under the prefix is `.html`"
    )
    assert _document_for_loc("https://premiselabs.co/tortoise/docs.html") is None, (
        "the temp tree has no top-level `docs.html`, so this URL must not resolve — "
        "otherwise the control above is measuring file existence rather than the "
        "served-name gate"
    )

def test_extract_anchors_within_refuses_every_tag_a_sibling_can_close() -> None:
    """The extent helper refuses EVERY container a sibling start tag closes (#14073).

    The refusal listed only the implied-end-tag tags (`li`, `p`, `td`, …), while
    its own message promised that every accepted tag is closed by its end tag
    alone. `button`, `h1`–`h6`, `a`, `nobr`, `select` and `table` are also
    closed by a FOLLOWING START TAG, and for them the extent ran past the sibling:
    `'<button class="toc"><a href="#a">A</a><button><a href="#b">B</a>'` gave
    `[['#a', '#b']]` where a browser has `[['#a']]` — a larger advertised set and a
    SHORTER slice, the fail-open direction (measured, #14073 review). Only `div` is
    a safe answer today, and that is the positive control below.

    ⛔ `form` IS REFUSED, BUT NOT FOR A SIBLING REASON — and it is worth saying
    which, because this test asserted the sibling reason and a later revision
    deleted the member on the strength of that error. The in-body `<form>` rule is
    *"If the form element pointer is not null … then this is a parse error; ignore
    the token"*: a sibling `<form>` is not an end tag, so OUTSIDE a table the
    end-tag extent is exactly the browser's tree. The "in table" rule (13.2.6.4.9)
    is what settles it: *"Insert an HTML element for the token … Pop the current
    node (which will be that form element) off the stack of open elements"* — so an
    in-table form is an EMPTY element whose content is foster-parented out, and an
    extent would advertise anchors it does not hold (measured:
    `<table><tr><td>x</td></tr><form class=toc id=f><a href="#a">A</a></form>`
    gives a browser a form holding NO anchors; #14073 review). Unmodellable in
    general, so refused — the safe side, not a wrong one.
    """
    declared = set(_AnchorHrefParser._IMPLICITLY_CLOSED)
    # ⛔ THE SET UNDER TEST IS NAMED HERE, not read back out of the refusal list — a
    # loop over `_IMPLICITLY_CLOSED` alone passes when a tag is DELETED from it,
    # which is exactly the defect this test exists to pin (measured: narrowing the
    # list back to the implied-end-tag family left the self-referential version of
    # this test green). The named set is a SUPERSET check on the declaration plus a
    # behavioural check per tag, so neither half can be removed silently.
    sibling_closed = {
        "a", "button", "h1", "h2", "h3", "h4", "h5", "h6", "li",
        "nobr", "option", "p", "select", "table", "td",
    }
    assert not sibling_closed - declared, (
        f"`_IMPLICITLY_CLOSED` no longer declares "
        f"{sorted(sibling_closed - declared)}: a container tag a sibling start tag "
        f"closes is accepted again, and its extent runs past the sibling — a larger "
        f"advertised set, a shorter slice, the fail-open direction"
    )
    for tag in sorted(sibling_closed):
        with pytest.raises(ValueError, match="WITHOUT an end tag"):
            extract_anchors_within(
                f'<{tag} class="toc"><a href="#a">A</a>',
                tag=tag,
                class_token="toc",
            )
    # `form` is refused for a reason OUTSIDE both sibling families — the "in table"
    # rule pops it the instant it is inserted, so its content is foster-parented out
    # of it and an end-tag extent would collect anchors the form does not hold. It is
    # therefore NOT a sibling-close tag, but it IS unmodellable, and the refusal is
    # the conservative answer rather than a mistaken one.
    assert "form" in declared, (
        "`_IMPLICITLY_CLOSED` accepts `form` again: a `<form>` written inside a table "
        "is popped as soon as it is inserted (spec 13.2.6.4.9), so its content is "
        "foster-parented out and the edge would advertise anchors the form does not "
        "hold — a larger set and a shorter slice, the fail-open direction"
    )
    with pytest.raises(ValueError, match="WITHOUT an end tag"):
        extract_anchors_within(
            '<table><tr><td>x</td></tr><form class="toc" id="f">'
            '<a href="#a">A</a></form></table>',
            tag="form",
            class_token="toc",
        )
    # POSITIVE CONTROL: the tag the callers actually ask for is accepted, and it
    # measures what the refusal protects — the sibling's anchor is NOT included.
    assert extract_anchors_within(
        '<div class="toc"><a href="#a">A</a></div><div class="toc"><a href="#b">B</a></div>',
        tag="div",
        class_token="toc",
    ) == [["#a"], ["#b"]], (
        "the accepted container tag does not bound its extent at its own end tag, "
        "so `div` is not the safe answer the refusal message advertises"
    )


def test_the_storage_attribute_allowlist_catches_every_legal_spelling() -> None:
    """The allowlist is a PARSED tag set, not a source pattern (#14073 review).

    `_visible_text` strips tags, so a claim parked in an attribute never reaches
    the golden — the allowlist is the only thing between it and the reader. The
    pattern this replaced required the attribute name to be immediately followed
    by `=` and to hold only letters and `-`, and WHATWG's HTML syntax permits all
    three of: whitespace around `=`, digits, and `_` in the name.

    ⛔ Both directions are asserted: every spelling below must be CAUGHT now, and
    the three the old pattern missed must provably have been missed — otherwise
    this test would pass against the fail-open it exists to pin.
    """
    marker = "Items are permanently deleted after thirty days"
    section = _docs_section()
    opening = '<h3 id="storage-record">'
    assert opening in section, (
        "website/docs.html has no `<h3 id=\"storage-record\">`, so the attribute "
        "control has nowhere to hang an attribute"
    )
    # (label, attribute to add, whether the replaced source pattern saw it)
    cases = (
        ("double-quoted", f' title="{marker}"', True),
        ("spaces around =", f' title = "{marker}"', False),
        ("digit in the name", f' data-30day="{marker}"', False),
        ("underscore in the name", f' data_del="{marker}"', False),
        ("aria attribute", f' aria-description="{marker}"', True),
    )
    for label, attribute, pattern_saw_it in cases:
        marked = section.replace(opening, opening[:-1] + attribute + ">", 1)
        assert marked != section, f"could not add {attribute!r} to {opening}"
        name = attribute.strip().split("=")[0].strip()

        # The PREMISE: the value is invisible to the golden, so only the
        # allowlist can stop it.
        assert marker not in _visible_text(marked), (
            f"{label}: the claim became VISIBLE text, so this control is measuring "
            f"the golden rather than the allowlist"
        )
        assert _attributes_outside_allowlist(marked) == [f"<h3 {name}>"], (
            f"the attribute allowlist does not name `{name}` ({label}): a "
            f"reader-facing claim parked in it ships unreviewed, because the "
            f"golden compares visible text only"
        )
        seen = set(re.findall(r"\s([a-zA-Z][a-zA-Z-]*)=", marked)) - {
            "id", "class", "href"
        }
        assert (name in seen) is pattern_saw_it, (
            f"the replaced source pattern's answer for {label} is now {name in seen}, "
            f"not {pattern_saw_it}: this control no longer demonstrates the "
            f"fail-open it pins, so its premise must be re-derived"
        )

    # ── the other direction: a name a browser never makes an attribute ───────
    # Reading the parser is only worth what it buys in BOTH directions. A source
    # pattern counted an attribute NAME written inside a comment, a raw-text
    # element or an inert `<template>` as a reader-facing attribute — so a page
    # could be reddened by a string the browser never renders, while the guard's
    # own name says "attributes the section carries". Pinned here because the
    # parser route is the fix, and this is the half of it that a reader cannot
    # check by looking at the allowlist's positive cases.
    for label, wrapper in (
        ("comment", '<!-- <h3 title="{claim}"> -->'),
        ("inert template", '<template><h3 title="{claim}"></h3></template>'),
    ):
        inert = wrapper.format(claim=marker)
        with_inert = section.replace(opening, inert + opening, 1)
        assert with_inert != section, f"could not insert the {label} case"
        assert _attributes_outside_allowlist(with_inert) == [], (
            f"an attribute name written inside a {label} is reported as a "
            f"reader-facing attribute: the guard reds a correct page for a "
            f"string no browser renders, and the allowlist's "
            f"'the section carries' claim stops being true of its own answer"
        )

    # ⛔ THE SAME INERTNESS ASKED OF THE DIRECTIVE LEG, where the plain
    # `<template><style>` row above cannot see it: the HTML raw-text branch gates on the
    # suppression stack, but `<svg>`/`<math>` are opened even inside an inert template,
    # so the FOREIGN branch reached the directive append with the template open and
    # reddened a CORRECT page (#14229 review, measured:
    # `_attributes_outside_allowlist(...)` returned `['<style>']` for template content
    # no mode renders).
    inert_foreign_directive = "<template><svg><style>p{}</style></svg></template>"
    with_inert_directive = section.replace(
        opening, inert_foreign_directive + opening, 1,
    )
    assert with_inert_directive != section, (
        "could not insert the inert-foreign-directive case"
    )
    assert _attributes_outside_allowlist(with_inert_directive) == [], (
        "a `<style>` inside an inert `<template>` is refused when it arrived through "
        "FOREIGN content: template content renders in no mode, so this reds a correct "
        "page (#14229 review). Got "
        f"{_attributes_outside_allowlist(with_inert_directive)}"
    )

    # ⛔ A SCRIPT BODY IS TWO QUESTIONS, so it gets two assertions instead of one
    # row in the loop above. Its fake ATTRIBUTE must not be counted (a browser makes
    # no attribute there), and the `<script>` ELEMENT must be refused in its own
    # right — a directive can paint a claim that no text pin reads (#14073 review
    # round 7). Collapsing them into `== []` is what let the second hide: the fix
    # for the first was the parser route, and the element refusal arrived later.
    script_body = '<script>var s = "<h3 title=\'{claim}\'>";</script>'
    with_script = section.replace(opening, script_body.format(claim=marker) + opening, 1)
    assert with_script != section, "could not insert the script body case"
    assert _attributes_outside_allowlist(with_script) == ["<script>"], (
        "the script BODY's fake attribute must not be reported and the <script> "
        "element itself must be: got "
        f"{_attributes_outside_allowlist(with_script)}"
    )


    # ── the one suppressed region whose answer depends on the MODE ───────────
    # MDN, `<noscript>`: children are HTML content with scripting OFF and TEXT
    # with it on. The parser suppresses it for the scripting-enabled tree (right
    # for the anchor question), so a `title` inside it is invisible to
    # `extract_element_attributes` — and yet a no-JS reader, and a screen reader in
    # that mode, meets it. Measured before the gate: the wrapper turned
    # `['<h3 title>']` into `[]` while every other pin stayed green, so the allowlist
    # — the only thing between the claim and the reader — reported clean (#14073
    # review). The region must therefore be REFUSED, not cleared.
    wrapped = section.replace(
        opening,
        f'<noscript><h3 id="storage-record" title="{marker}">x</h3></noscript>'
        + opening,
        1,
    )
    assert wrapped != section, "could not wrap the storage heading in <noscript>"
    assert marker not in _visible_text(wrapped), (
        "the <noscript>-wrapped claim became VISIBLE text, so this control is "
        "measuring the golden rather than the allowlist's treatment of the region"
    )
    assert _attributes_outside_allowlist(wrapped) == ["<noscript>"], (
        "a `<noscript>` region in the storage section is cleared rather than "
        "refused: with scripting disabled its children are ordinary markup, so a "
        "`title` there reaches a reader while the allowlist reports clean"
    )
    # …and the wrapper is what changes the answer: the same region inside an inert
    # `<template>` renders in NO mode, so it stays cleared (the row above). Without
    # this control the refusal could have been implemented as "any `<noscript` in
    # the text", which would red a page for a string no browser parses.
    assert extract_mode_dependent_regions(
        f'<template><noscript><h3 title="{marker}"></h3></noscript></template>'
    ) == [], (
        "a `<noscript>` inside an inert `<template>` is template content and "
        "renders in neither mode, so refusing it reds a correct page for markup "
        "no reader meets"
    )


def test_the_storage_section_destinations_are_pinned_not_just_their_names() -> None:
    """A retargeted `href` reds, though the allowlist, golden and scan all pass.

    `_REVIEWED_SECTION_HREFS` exists because the attribute allowlist admits `href`
    BY NAME and every other storage pin reads visible TEXT — so the section's one
    outbound claim had a value no pin looked at (#14073 review, P2). Without the
    literal pin, all three of the following stay green on a page that sends the
    reader somewhere else entirely.

    ⛔ THE PREMISE IS ASSERTED, not assumed: each mutant below is checked to be
    INVISIBLE to the other three pins, so this test cannot quietly start measuring
    the wording golden instead of the destination pin.
    """
    section = _docs_section()
    reviewed = section
    assert sorted(set(extract_anchor_hrefs(reviewed))) == list(
        _REVIEWED_SECTION_HREFS
    ), "the reviewed section's own destinations drift from the pinned set"
    original = _REVIEWED_SECTION_HREFS[0]
    assert original in section, f"{original!r} is not in the #storage section"

    for label, replacement in (
        ("a foreign mailbox", "mailto:legal@evil.example"),
        ("a foreign host", "https://evil.example/storage"),
        ("a dead fragment", "#storage-limits-not-a-heading"),
    ):
        mutant = section.replace(f'href="{original}"', f'href="{replacement}"', 1)
        assert mutant != section, f"could not retarget the href for {label}"

        # The premise: the other three pins cannot see this.
        assert _attributes_outside_allowlist(mutant) == [], (
            f"{label}: the allowlist caught it, so this control is not measuring "
            f"the destination pin"
        )
        assert _visible_text(mutant) == _visible_text(section), (
            f"{label}: the destination became visible text, so the wording "
            f"golden sees it and this control is not measuring the pin"
        )
        assert _banned_vocabulary(mutant) == [], (
            f"{label}: the vocabulary scan caught it, so this control is not "
            f"measuring the destination pin"
        )

        # …and the pin that does look at the value names it — DRIVEN, not
        # re-implemented: this calls the same `_assert_storage_section_reviewed`
        # the real test calls, so deleting the pin from the implementation reds
        # this control instead of leaving it green (#14073 review round 2).
        with pytest.raises(AssertionError, match="links to"):
            _assert_storage_section_reviewed(mutant)


def test_each_pin_in_the_storage_aggregate_reds_on_its_own_violation() -> None:
    """Each pin of the storage AGGREGATE fires on ITS OWN rule, driven through it.

    ⛔ THE NAME IS THE SCOPE. This drives the three asserts inside
    `_assert_storage_section_reviewed` and nothing else; the file's other storage
    tests (`_docs_section`'s bound, the named-claims pin, the fixture's own
    regeneration) are NOT mutants of this aggregate. A name claiming "every
    storage pin" would tell the next lane that a fourth assert added to the
    aggregate is already driven — the precise vacuity this test exists to close,
    reintroduced by its own title (#14073 review round 7).

    ⛔ ONE DRIVEN PIN IS NOT A DRIVEN AGGREGATE. `_assert_storage_section_reviewed`
    holds three pins and its docstring promises "remove the pin and the control
    fails" — but only the destination pin had a control that CALLS it, so the
    allowlist and the wording-golden asserts could each be deleted with the suite
    fully green (measured, #14073 review round 3). The allowlist has a test that
    calls `_attributes_outside_allowlist` DIRECTLY, so it cannot see the
    aggregate's assert disappear; the two tests that read
    `STORAGE_SECTION_GOLDEN` elsewhere use it only as a comparator on mutant
    pages, so nothing enforced `page == fixture`.

    Each mutant below violates exactly ONE rule — asserted, so a mutation that
    trips a different pin cannot be read as covering this one.
    """
    section = _docs_section()

    # 1. The attribute allowlist: a legal spelling of a forbidden NAME. The value
    #    is inert text, so neither the wording nor the destinations move.
    attributed = section.replace(
        '<h3 id="storage-record">', '<h3 id="storage-record" title="x">', 1
    )
    assert attributed != section, "the allowlist control could not add an attribute"
    assert _visible_text(attributed) == _visible_text(section)
    assert sorted(set(extract_anchor_hrefs(attributed))) == list(
        _REVIEWED_SECTION_HREFS
    )
    with pytest.raises(AssertionError, match="beyond"):
        _assert_storage_section_reviewed(attributed)

    # 2. The destinations: a retargeted href. Names and visible text unchanged.
    retargeted = section.replace(
        f'href="{_REVIEWED_SECTION_HREFS[0]}"',
        'href="mailto:legal@evil.example"',
        1,
    )
    assert retargeted != section, "the destination control could not retarget"
    assert _attributes_outside_allowlist(retargeted) == []
    assert _visible_text(retargeted) == _visible_text(section)
    with pytest.raises(AssertionError, match="links to"):
        _assert_storage_section_reviewed(retargeted)

    # 4. A RENDERING DIRECTIVE: an attribute-free `<style>` whose `::after` paints a
    #    claim. It contributes no text node and no attribute, so no other rule here
    #    can see it — measured in Chromium painting inside the `#storage` band while
    #    the golden, the vocabulary scan and the destinations all stayed green
    #    (#14073 review round 7).
    directed = section.replace(
        "</p>", '</p><style>#storage-record::after{content:"x"}</style>', 1
    )
    assert directed != section, "the directive control could not add a <style>"
    assert _visible_text(directed) == _visible_text(section)
    assert sorted(set(extract_anchor_hrefs(directed))) == list(
        _REVIEWED_SECTION_HREFS
    )
    with pytest.raises(AssertionError, match="<style>"):
        _assert_storage_section_reviewed(directed)

    # 3. The reviewed wording: one sentence added, nothing else touched.
    reworded = section.replace(
        "</p>", " Search may lose older items.</p>", 1
    )
    assert reworded != section, "the wording control could not add a sentence"
    assert _attributes_outside_allowlist(reworded) == []
    assert sorted(set(extract_anchor_hrefs(reworded))) == list(
        _REVIEWED_SECTION_HREFS
    )
    with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
        _assert_storage_section_reviewed(reworded)


def test_a_second_toc_block_refuses_rather_than_picking_one() -> None:
    """The "exactly one rendered TOC block" refusal is DRIVEN, not just asserted.

    `_docs_section` says the single-TOC rule is "asserted rather than assumed"
    because with two blocks which one is the index is undecidable, and the second
    could advertise a heading the reviewed one does not. Neutralising that assert
    left the suite green (measured, #14073 review round 4): the control suite
    covered a TOC inside a comment and inside an inert `<template>`, but not a
    second RENDERED block — the one spelling the refusal exists for.

    ⛔ WHAT THIS DRIVES IS THE REFUSAL, and only the refusal — the module-level
    docstring on `_docs_section` explains the damage a second block does (an
    un-advertised heading bounds the slice, carrying copy past every storage pin).
    That explanation is prose, not a pin, and this test does not assert it: a
    claim asserted by nothing is the defect this file keeps finding.
    """
    page = DOCS.read_text(encoding="utf-8")
    marker = '<div class="toc">'
    assert marker in page, "website/docs.html has no `<div class=\"toc\">`"
    # A second RENDERED block, ahead of the real one, advertising a heading the
    # reviewed TOC does not.
    two_tocs = page.replace(
        marker,
        '<div class="toc"><a href="#storage-limits">Also</a></div>' + marker,
        1,
    )
    assert two_tocs != page
    with pytest.raises(AssertionError, match="toc"):
        _docs_section(page=two_tocs)


def test_a_refused_section_heading_is_not_reported_as_a_missing_one() -> None:
    """A PRESENT but refused `<h2 id="storage">` must not be diagnosed as absent.

    `_h2_offset` answers `-1` in two different worlds (#14073 review, P2): the
    heading was renamed/moved (the notices' link is broken — re-adding it is the
    fix), or it is right there in the source and the reader model REFUSED it. The
    old message named only the first, so an over-refusal — exactly what this
    guard's loud rules are built to produce — sent the next lane hunting for a
    deleted heading that was never deleted. Both worlds are driven below.
    """
    page = DOCS.read_text(encoding="utf-8")
    assert '<h2 id="storage">' in page, "premise: the reviewed page carries the heading"

    # (a) PRESENT but REFUSED: an open `<select>` above it is an `_UNBOUNDABLE_ANCESTORS`
    # ancestor, so `_unmet_by_reader` refuses the heading and nothing bounds the slice.
    refused = page.replace('<h2 id="storage">', '<select><h2 id="storage">', 1)
    assert refused != page
    with pytest.raises(AssertionError) as refused_error:
        _docs_section(page=refused)
    message = str(refused_error.value)
    assert 'HAS `<h2 id="storage">` in its source' in message, (
        "the diagnosis blamed a MISSING heading for one the model refused, which is "
        f"the misleading report #14073 review named: {message}"
    )
    assert 'has no `<h2 id="storage">`' not in message, (
        f"the absent-heading wording must not survive on a refused heading: {message}"
    )

    # (b) GENUINELY ABSENT: rename the id, and the message must still say so — the
    # fix for a refused heading must not turn a real deletion into a refusal.
    renamed = page.replace('<h2 id="storage">', '<h2 id="storage-renamed">', 1)
    assert renamed != page
    with pytest.raises(AssertionError) as absent_error:
        _docs_section(page=renamed)
    absent = str(absent_error.value)
    assert 'has no `<h2 id="storage">`' in absent, (
        f"a heading that really is gone must be reported as missing: {absent}"
    )
    assert "HAS `" not in absent


def test_a_base_element_moves_what_the_section_advertises() -> None:
    """A `<base href>` changes which document a fragment-only link lands on.

    A browser resolves an anchor against the document's BASE URL, which the first
    `<base href>` overrides — including for fragment-only links. `_docs_section`
    used to answer the same-page question against the page's own URL only, so with
    any `<base>` present every TOC entry silently navigated elsewhere while the
    guard reported the heading as advertised (#14073 review, P2, measured with an
    injected `<base href="/">`: nothing reddened).

    ⛔ BOTH DIRECTIONS. A base that leaves this page must REMOVE the advertised
    heading, and a base that IS this page must keep it — otherwise "refuse
    everything" would pass, and a guard that refuses correct pages is the false
    red this module's docstrings keep warning about.
    """
    canonical = _canonical_page_url(DOCS)
    fragment_only = "#storage"
    assert _fragment_this_page_advertises(fragment_only, DOCS) == "storage", (
        "the control itself is broken: `#storage` must advertise on this page "
        "when no `<base>` is present"
    )

    # A base that leaves the page: `#storage` now navigates to the site root.
    assert _fragment_this_page_advertises(
        fragment_only, DOCS, base=urljoin(canonical, "/")
    ) is None, (
        "a `<base href=\"/\">` sends `#storage` to the site root, so the section "
        "must NOT be read as advertising that heading — the guard saw only the "
        "fragment and called a page-crossing link same-page"
    )
    # A base on a foreign host leaves the SITE, which is the fail-open direction.
    assert _fragment_this_page_advertises(
        fragment_only, DOCS, base="https://evil.example/"
    ) is None, (
        "a foreign `<base>` moves every fragment-only link off the site, so "
        "advertising a heading for it is the fail-open direction this predicate "
        "exists to close"
    )
    # A base that IS this page changes nothing — the loud direction must not fire.
    assert _fragment_this_page_advertises(
        fragment_only, DOCS, base=canonical
    ) == "storage", (
        "a `<base href>` naming this page's own URL is what an honest page would "
        "carry, so refusing it would be a false red"
    )

    # …and the document-level seam reads the first rendered `<base href>`.
    page = DOCS.read_text(encoding="utf-8")
    assert extract_first_base_href(page) is None, (
        "website/docs.html declares a `<base href>` — the base seam is now load "
        "bearing and this test's premise needs re-deriving"
    )
    assert _document_base_url(page, DOCS) == canonical
    marked = page.replace("<head>", '<head><base href="/">', 1)
    assert marked != page, "website/docs.html has no `<head>` to inject a base into"
    assert _document_base_url(marked, DOCS) == urljoin(canonical, "/")

    # ⛔ AND THROUGH THE REAL BOUND, not only through the predicate. Asserting the
    # seam and the derivation leaves the WIRING — `_docs_section` passing
    # `base=_document_base_url(html, DOCS)` — untested, so deleting that argument
    # kept every storage pin green while a `<base href="/">` sent every
    # fragment-only TOC link off the page (measured, #14073 review round 2).
    assert _docs_section(page=marked) != _docs_section(), (
        "a `<base href=\"/\">` no longer moves the section bound: `_docs_section` "
        "is resolving TOC entries against the page URL instead of the document "
        "base URL, so every fragment-only link is read as same-page"
    )

    # A `<base>` a browser never parses must NOT move the base.
    assert extract_first_base_href(
        page.replace("<head>", '<head><template><base href="https://evil.example/">'
                     "</template>", 1)
    ) is None, (
        "a `<base>` inside an inert `<template>` is not one a browser applies, so "
        "honouring it would move the base to a page no reader navigates from"
    )
    # ⛔ AND A FOREIGN `<base>` IS NOT THE `base` ELEMENT (#14073 review round 14, P2,
    # measured). Inside `<svg>`/`<math>` a start tag is inserted *"as a foreign element,
    # in the SVG/MathML namespace"* (§13.2.6.5), and the document base URL is defined by
    # the HTML `base` element only — so a `<svg><base href>` moves nothing. Measured on
    # this box's Chrome against a `file://` page: `<base href>` moves `baseURI` to
    # `https://evil.example/`, while `<svg><base href>` and `<math><base href>` leave it
    # at the document's own URL. `_record_heading` carries the same guard; this record
    # was simply missed, and the direction is the fail-open one on this question.
    for foreign in ("svg", "math"):
        assert extract_first_base_href(
            f'<head><{foreign}><base href="https://evil.example/"></{foreign}>'
        ) is None, (
            f"a `<base>` inside `<{foreign}>` is a foreign element, not the HTML "
            f"`base`, so a browser does not apply it — honouring it moves every "
            f"fragment-only link off the page"
        )
    assert extract_first_base_href(
        '<head><base href="https://evil.example/" target="_blank">'
    ) == "https://evil.example/", (
        "premise: an HTML `<base href>` IS applied, so the rows above measure the "
        "foreign half and not a broken seam"
    )
    # ⛔ DECLARED DIVERGENCE — AN HTML INTEGRATION POINT (#14073 review round 15, P2,
    # measured). `desc`/`title`/`foreignObject` are integration points, so their children
    # are HTML again and a `<base>` written there IS applied: Chrome moves baseURI to
    # `https://evil.example/` for both rows below (the base's `namespaceURI` is the XHTML
    # one). This seam refuses them, because foreign content is tracked as a DEPTH and the
    # parser cannot tell an integration point from the SVG itself. Pinned as a DECLARED
    # divergence rather than left implicit: the day the foreign element stack lands, this
    # row reds and the declared residual is due for deletion.
    for integration_point in ("desc", "title", "foreignObject"):
        assert extract_first_base_href(
            f'<head><svg><{integration_point}><base href="https://evil.example/">'
            f"</{integration_point}></svg>"
        ) is None, (
            f"a `<base>` inside `<svg><{integration_point}>` is now applied: that is the "
            f"right answer (it is an HTML integration point) — delete this declared "
            f"residual from `_record_base` and from here, and re-run the base rows"
        )

    # ⛔ AND THE FIRST ONE WINS. A later `<base href>` is ignored, so honouring it
    # would compute a base no browser uses — a fail-open on this very question.
    # Unpinned until round 4: deleting `or self.base_href is not None` left the
    # whole suite green (measured, #14073 review).
    assert extract_first_base_href(
        '<head><base href="/first"><base href="https://evil.example/">'
    ) == "/first", (
        "only the FIRST `<base href>` a browser parses can move the base; "
        "returning a later one computes a URL no browser navigates from"
    )
    # …and the bound follows the first base, not the last — WITH A PREMISE THAT
    # CAN FAIL. The obvious mutant (`<base href="/">` then a foreign host) is not
    # a control for this rule: BOTH bases leave the page, so the slice is the rest
    # of the document whichever one wins (measured: 21389 chars for either order),
    # and a `!=` against the unbased slice passes both ways. The two bases below
    # differ in what they do to the slice — the FIRST names this page, so the TOC
    # entries stay same-page and the slice is the reviewed section; the SECOND
    # leaves the page, so honouring it runs the slice to the end of the document
    # (measured on this page: 2990 vs 21389).
    two_bases = page.replace(
        "<head>", '<head><base href="/tortoise/docs"><base href="/">', 1
    )
    assert two_bases != page, "website/docs.html has no `<head>` to inject bases into"
    assert _docs_section(page=two_bases) == _docs_section(), (
        "a second `<base href>` overrode the first: the bound must take the base "
        "the browser takes, which is the FIRST one. Here the first base names "
        "this page and the second leaves it, so a last-base-wins read runs the "
        "slice to the end of the document and the reviewable section grows."
    )


def test_a_table_open_across_the_bound_refuses_rather_than_cutting_the_source() -> None:
    """A heading a browser may move content ACROSS cannot bound the slice.

    The bound is a RAW SOURCE range (`html[start:offset]`) and each heading is
    recorded at its raw `getpos()` (`tests/_html_links.py`), which is a valid
    boundary only while source order IS tree order. Inside a `<table>` it is not:
    in the "in table" insertion mode a claim is FOSTER PARENTED — inserted
    immediately BEFORE the `<table>` — so copy authored AFTER the bounding heading
    renders BEFORE it, i.e. inside the section, while the slice excludes it and
    every storage pin stays green.

    ⛔ WHICH ROUTES ACTUALLY INVERT THE TREE — measured, because the earlier
    version of this docstring asserted the inversion for a mutant that does not
    produce it. Chrome on this box, `--headless=new --dump-dom`, on the mutants
    built below (rect tops; `#storage` at 3601 throughout):

      heading in a `<td>`, claim in "in table" mode
        (`<li><table><tr><td>{h}</td></tr>{c}</table>`)   claim 4924 < `#quickstart` 5011  → IN the section
      the same under an `<svg><table>` breakout            claim 5082 < `#quickstart` 5169  → IN the section
      `<li><table><li>{h}<tr><td>{c}`                     claim 5002 > `#quickstart` 4944  → NOT in the section

    So only the CELL routes move the claim across the bound. Where the heading
    itself is foster-parented out (the `<li>`/`</div>` rows) the browser puts the
    heading before the table and the claim inside it, i.e. on the FAR side of the
    bound — no inversion. Those rows are still pinned, because the rule this guard
    enforces is the one the model can actually settle (`an open table above the
    bounding heading cannot bound`), and refusing there is the loud direction; the
    closed-table control at the end of this test is what bounds that over-refusal.

    ⛔ BOTH DIRECTIONS, as everywhere in this module. A table OPEN across the bound
    must refuse (the slice runs long, the loud direction), and a table that opens
    AND CLOSES inside the section must still bound exactly — otherwise "refuse any
    page with a table" would pass, and that is the false red this module's
    docstrings keep warning about.
    """
    page = DOCS.read_text(encoding="utf-8")
    heading = re.search(r'<h2 id="quickstart"[^>]*>.*?</h2>', page, re.S)
    assert heading is not None, (
        "website/docs.html has no `<h2 id=\"quickstart\">` — this test's whole "
        "premise (that heading bounds the section) needs re-deriving"
    )
    claim = (
        "<p>FalkorDB holds only a slice of what you save, and once you are above "
        "the allowance for your tier the least-used items are permanently "
        "deleted after thirty days.</p>"
    )
    # ⛔ EVERY ROUTE, because the parser's STACK is not faithful about `table` and
    # one route is green over the others (#14073 review, P0, measured): these four
    # legal shapes each DELETE the `<table>` from `_open_elements` while a browser
    # keeps it open, and an open table is exactly the precondition for foster
    # parenting back across the bound. Checking the stack alone left all four
    # bypassable, so `_unmet_by_reader` also consults `_table_open_unmatched`.
    # ⛔ EACH ROUTE PLACES THE CLAIM ITSELF (`{c}`), because WHERE the claim sits
    # relative to the heading is the difference between a route that reproduces the
    # inversion a browser really performs and one that only exercises the rule.
    routes = {
        # ⛔ THE MEASURED INVERSION (Chrome, on this box — see the docstring): the
        # heading stays INSIDE a cell, so it is not foster-parented, while the claim
        # after the cell is in "in table" mode and IS foster-parented immediately
        # BEFORE the table — above the heading, inside `#storage`, opposite to
        # source order. Without these three rows this test would assert an inversion
        # no row performs.
        # The shape is the one `_html_links._unboundable`'s own docstring cites as
        # the measured hazard (`<table><tr><td><h2 id="quickstart">…</td></tr>CLAIM
        # </table>`), so the rule and the mutant now describe the same document.
        "cell holds the heading, claim foster-parented out": (
            "<li><table><tr><td>{h}</td></tr>{c}</table>"
        ),
        "cell holds the heading under an `<svg><table>` breakout": (
            "<li><svg><table><tr><td>{h}</td></tr>{c}</table>"
        ),
        "cell holds the heading under a `<math><table>` breakout": (
            "<div><math><table><tr><td>{h}</td></tr>{c}</table>"
        ),
        # the `<li>` start-tag loop STOPS at the first SPECIAL element, `table`
        "<li> stops at a special element": "<li><table><li>{h}<tr><td>{c}</td></tr></table>",
        # the button rule is gated on "in scope", and `table` is a scope element
        "<button> gated on table being in scope": (
            "<button><table><button>{h}<tr><td>{c}</td></tr></table>"
        ),
        # AAA removes only the formatting node, never the table above it
        "<a> adoption agency keeps the table": (
            "<a><table><a>{h}<tr><td>{c}</td></tr></table>"
        ),
        # a browser IGNORES an end tag whose element is not in scope; `div` is not
        "</div> is ignored while a table is open": (
            "<div><table></div>{h}<tr><td>{c}</td></tr></table>"
        ),
        # ⛔ AND THE SHAPES THAT BEAT A PREVIOUS REVISION OF THIS GUARD. `table` is
        # a BREAKOUT tag, so `<svg><table>` opens a real HTML table — a flag read
        # before `_open` (which clears `_foreign`) missed it.
        "<svg><table> breaks out to a real table": (
            "<li><svg><table><li>{h}<tr><td>{c}</td></tr></table>"
        ),
        "<svg><table> under a scope-blocked end tag": (
            "<div><svg><table></div>{h}<tr><td>{c}</td></tr></table>"
        ),
        "<math><table> breaks out to a real table": (
            "<div><math><table></div>{h}<tr><td>{c}</td></tr></table>"
        ),
        # …and a boolean flag was cleared by a NESTED table's own `</table>` while
        # the outer table stayed open, which is why the flag is now a depth.
        "a nested `</table>` must not close the outer": (
            "<li><table><tr><td><table></table><li>{h}<tr><td>{c}</td></tr></table>"
        ),
        "a nested `</table>` under a scope-blocked end tag": (
            "<div><table><tr><td><table></table></div>{h}<tr><td>{c}</td></tr></table>"
        ),
        "a nested `</table>` with AAA keeping the outer": (
            "<a><table><tr><td><table></table><a>{h}<tr><td>{c}</td></tr></table>"
        ),
        # ⛔ AND THE ONE ROUTE THAT PINS THE DEPTH ITSELF (#14073, measured on this
        # box). The three rows above cannot red when the counter is reverted to a
        # BOOLEAN, because on those shapes the stack STILL holds the table, so the
        # stack test refuses first: reverting the depth to a boolean left this whole
        # module green — 111 passed, 1 skipped — and a 370-shape sweep found no
        # bypass either, which is how the gap was found. A `<button>` sibling close
        # pops THROUGH the stack's table, so the counter is the ONLY guard left
        # there, and a nested `</table>` clears a boolean at exactly that point.
        # Chrome on this mutant: claim y=4926, `#quickstart` y=5085, ink 12630 px —
        # the claim renders ABOVE the bounding heading, inside `#storage`, while the
        # slice excludes it and every pin reads a section the reader does not see.
        # The same shape with a bare `<td>` measures the same way (y=4926 vs 5016).
        "<button> pops through the table, so the count must survive a nested close": (
            "<button><table><tr><td><table></table><button>"
            "<tr><td>{h}</td></tr>{c}</table>"
        ),
        "<button> pops through the table, bare <td> cell": (
            "<button><table><tr><td><table></table><button><td>{h}</td>{c}</table>"
        ),
    }
    for why, template in routes.items():
        mutant = (
            page[: heading.start()]
            + template.format(h=heading.group(0), c=claim)
            + page[heading.end() :]
        )
        # Without the inversion this mutant proves nothing, so assert it first.
        assert mutant.index(claim) > mutant.index('id="quickstart"'), (
            "the mutant no longer places the claim AFTER the bounding heading in "
            f"the source ({why}), so it does not reproduce the inversion"
        )
        section = _docs_section(page=mutant)
        assert "permanently deleted" in section, (
            "the section bound cut the source at a heading a browser may "
            "foster-parent content across: the claim sits AFTER that heading in "
            "the source, so the slice excludes it, while the open table means a "
            "browser may render it on the other side of the bound — inside "
            "`#storage` — where the wording, vocabulary and destination pins would "
            "all read a section the reader does not see. "
            f"Route: {why}, which removes the `<table>` from the parser's stack "
            "while the browser keeps it open. A heading with a table open above it "
            "must not bound (`_unmet_by_reader`), which keeps the slice long and "
            "reds the golden on the claim instead of hiding it."
        )
        # ⛔ WHICH assert, not just ANY assert: without `match=` this row passes on a
        # broken setup assert (a manifest reload, a fixture count) and proves nothing
        # about the pin it names (#14073 review, P2b — every sibling row pins its own).
        # MEASURED, not guessed: on this route the DESTINATIONS pin fires first (the
        # foster-parented claim drags the section's link set), so pinning the wording
        # golden here would have been a false claim about which pin the row drives.
        with pytest.raises(AssertionError, match="the #storage section links to"):
            _assert_storage_section_reviewed(section)

    # ⛔ AND THE FALSE-RED DIRECTION: a table that opens AND closes strictly inside
    # the section moves no content across the bound, so the bound must not refuse.
    table = "<table><tr><td>Reviewed</td></tr></table>"
    closed = page.replace('<h3 id="storage-record">', table + '<h3 id="storage-record">', 1)
    assert closed != page, "the control could not inject a table into the section"
    assert len(_docs_section(page=closed)) == len(_docs_section()) + len(table), (
        "a `<table>` that opens and closes inside the section moved the bound: the "
        "refusal must fire only for a table OPEN at the bounding heading, not for "
        "any page that contains a table"
    )


def test_the_wording_pin_reads_the_rendered_tree_not_the_source() -> None:
    """The golden compares what a browser RENDERS, not a regex over the source.

    ⛔ THIS WAS A P0 FAIL-OPEN (#14073 review round 5, measured). `_visible_text`
    was `re.sub(r"<[^>]+", " ", markup)`, which deletes everything from ANY `<` to
    the next `>` — but a `<` starts a tag only when the next character is an ASCII
    letter, `/`, `!` or `?`; otherwise the tokenizer emits it as TEXT (WHATWG
    13.2.5.6, tag-open state, "anything else"). So this ONE LINE added inside the
    section was rendered by every browser and deleted by the pin:

        < FalkorDB holds a slice of the record; above the allowance for your tier
        the least-used items are permanently deleted after thirty days >

    and the wording golden, the vocabulary scan (same text) and the allowlist all
    reported the section clean. The same mistake fails in the loud direction too: a
    `<script>` body counted as visible text and reddened the golden on markup no
    reader meets.

    Both directions are driven below, each asserting its own premise so a mutant
    that trips a different pin cannot be read as covering this one.
    """
    section = _docs_section()
    reviewed = " ".join(STORAGE_SECTION_GOLDEN.read_text(encoding="utf-8").split())

    # 1. Rendered-but-not-markup: a `<` that begins no tag. The other two pins are
    #    asserted UNCHANGED, so only the wording/vocabulary half can catch it.
    claim = (
        "< FalkorDB holds a slice of the record; above the allowance for your tier "
        "the least-used items are permanently deleted after thirty days >"
    )
    escaped = section.replace("</h2>", f"</h2>\n{claim}", 1)
    assert escaped != section, "the control could not insert the claim"
    assert _attributes_outside_allowlist(escaped) == [], (
        "premise: the claim adds no attribute, so the allowlist pin cannot be what "
        "catches it"
    )
    assert sorted(set(extract_anchor_hrefs(escaped))) == list(_REVIEWED_SECTION_HREFS), (
        "premise: the claim adds no href, so the destination pin cannot catch it"
    )
    assert _banned_vocabulary(escaped) == [
        "allowance", "falkor", "tier",
    ], (
        "the vocabulary scan reads visible text too, so it must see the claim — the "
        "regex body did not and reported []"
    )
    with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
        _assert_storage_section_reviewed(escaped)

    # ⛔ THE MECHANISM, pinned: the text the pin now reads really is the text the old
    #    regex deleted. Without this the two assertions above would also pass on a
    #    page whose claim happened to be inside a tag, and nothing would record WHY
    #    the regex was wrong.
    assert claim in _visible_text(escaped), (
        "a `<` followed by a non-tag character is TEXT to a browser; the wording "
        "pin must read it"
    )
    assert re.sub(r"<[^>]+>", " ", escaped).find(claim) == -1, (
        "premise no longer holds: the raw-source stripper this replaced DOES keep "
        "the claim, so this control is no longer demonstrating the defect"
    )

    # 2. The loud direction: text a reader never meets must not move the golden.
    for name, injected in (
        ("script", "<script>var q='nothing is lost';</script>"),
        ("style", "<style>.x::after{content:'tier'}</style>"),
        ("hidden", "<p hidden>ghost claim about allowance and tier</p>"),
        ("noscript", "<noscript>ghost claim about allowance</noscript>"),
        ("iframe fallback", "<iframe>ghost claim about allowance</iframe>"),
    ):
        mutant = section.replace("</h2>", f"</h2>{injected}", 1)
        assert mutant != section, f"the {name} control could not inject"
        assert " ".join(_visible_text(mutant).split()) == reviewed, (
            f"a {name} region holds text no reader meets; counting it reddens the "
            f"golden on legal markup, which is what the regex body did"
        )

    # 3. …and the ONE over-drop that runs the OTHER way, which none of the rows
    #    above can see: a closed `<details>` DOES paint its `<summary>`, so a claim
    #    written there must RED the golden (#14073 review round 6, P1, measured —
    #    before this the allowlist, the destinations, the vocabulary scan and the
    #    wording golden were ALL green while Chromium painted the summary).
    summary_claim = section.replace(
        "</h2>",
        "</h2><details><summary>FalkorDB permanently deletes your least-used items "
        "after thirty days</summary>Tap to read more.</details>",
        1,
    )
    assert summary_claim != section, "the summary control could not inject"
    assert _attributes_outside_allowlist(summary_claim) == [], (
        "premise: the summary adds no attribute, so the allowlist cannot be what "
        "catches it"
    )
    assert "falkor" in _banned_vocabulary(summary_claim), (
        "a `<summary>` is painted by a closed `<details>`, so the visible-text scan "
        "must see its words"
    )
    with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
        _assert_storage_section_reviewed(summary_claim)
    # …and the closed BODY is COUNTED TOO, deliberately (#14073 review round 9). This
    # seam takes the LOUD side for the whole `<details>`/`<dialog>` family: three
    # review rounds tried to model exactly which part of a closed disclosure a browser
    # paints, and each round found the next shape that model got wrong — every one of
    # them a claim that passed all three pins while a browser painted it (direct-child
    # summary, then one wrapper deeper, then `<details><p>…<summary>`, then a `<p>`'s
    # descendants and six renamed/voided tokens). Counting the body instead costs a
    # reviewed copy change when a disclosure is added inside the pinned section, and
    # that is the direction a wording pin must err in.
    body_only = section.replace(
        "</h2>",
        "</h2><details><summary></summary>FalkorDB permanently deletes everything"
        "</details>",
        1,
    )
    assert body_only != section
    assert "falkor" in _banned_vocabulary(body_only), (
        "a closed `<details>` no longer hides its body from this seam — the loud side "
        "of a disclosure's copy; dropping it is the fail-open three rounds shipped"
    )
    with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
        _assert_storage_section_reviewed(body_only)

    # 3b. …and the SAME claim one INLINE ELEMENT DEEPER inside that summary, which the
    #    direct-child version of the carve-out could not see (#14073 review round 7,
    #    P1, measured): the painted slot is the whole `<summary>` SUBTREE, so a claim
    #    inside a `<span>`/`<b>`/`<em>` there is painted too, and the first version of
    #    `_text_is_painted` asked whether the CURRENT element was the summary — true
    #    only while the text was the summary's direct child. Measured before the fix:
    #    the allowlist, the destination pin, the vocabulary scan and the wording golden
    #    were ALL green while Chromium painted the span. Driven for three wrappers, so
    #    the row tests the depth-independence rather than one tag.
    for wrapper in ("span", "b", "em"):
        nested_claim = section.replace(
            "</h2>",
            "</h2><details><summary><" + wrapper + ">FalkorDB permanently deletes your "
            "least-used items after thirty days</" + wrapper + "></summary>Tap to "
            "read more.</details>",
            1,
        )
        assert nested_claim != section, f"the `<{wrapper}>`-in-summary control could not inject"
        assert _attributes_outside_allowlist(nested_claim) == [], (
            f"premise: a bare `<{wrapper}>` adds no attribute, so the allowlist cannot "
            f"be what catches this"
        )
        assert sorted(set(extract_anchor_hrefs(nested_claim))) == list(
            _REVIEWED_SECTION_HREFS
        ), f"premise: the `<{wrapper}>` adds no href, so the destination pin cannot catch this"
        assert "falkor" in _banned_vocabulary(nested_claim), (
            f"the `<summary>` slot is painted as a SUBTREE, so a claim inside a "
            f"`<{wrapper}>` there reaches the reader; the visible-text scan must see "
            f"its words, and the direct-child carve-out did not"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(nested_claim)
    # …and the closed BODY is COUNTED one element deeper too: the loud rule is about the
    # disclosure, not about depth, so no wrapper inside it is a hiding place.
    nested_body = section.replace(
        "</h2>",
        "</h2><details><summary>Tap to read more.</summary><div><span>FalkorDB "
        "permanently deletes everything</span></div></details>",
        1,
    )
    assert nested_body != section
    assert "falkor" in _banned_vocabulary(nested_body), (
        "the closed BODY is counted at any depth under the loud rule — a disclosure "
        "inside the pinned section is a reviewed copy change, not a hiding place"
    )

    # …and only ONE of the two `hidden` positions still hides text: a `hidden` ANCESTOR
    # above the `<details>`. A `hidden` INSIDE it is counted (asserted below), because
    # the first unrendered ancestor is the disclosure.
    # ⛔ AND THE HONEST COST OF THE ONE RULE (#14073 review round 9): a `hidden`
    # subtree INSIDE a disclosure is COUNTED, because the first unrendered ancestor is
    # the `<details>` and this seam stops modelling which of its descendants a browser
    # paints. That is a false red, not a fail-open, and it cannot hide a claim here:
    # `_attributes_outside_allowlist` refuses any attribute beyond `id`/`class`/`href`
    # in the pinned section, so a `hidden` cannot exist inside it. Stated rather than
    # left for a reader to derive from the implementation.
    assert " ".join(_visible_text(
        "<details><summary><span hidden>GHOST claim</span>shown</summary></details>"
    ).split()) == "GHOST claim shown", (
        "the loud rule counts a disclosure's whole subtree; a `hidden` INSIDE one is "
        "counted too. If this changes, the rule acquired a `hidden` carve-out and the "
        "declared cost above needs rewriting with it"
    )
    assert "GHOST" not in _visible_text(
        "<div hidden><details><summary>GHOST claim</summary></details></div>"
    ), (
        "a `hidden` ANCESTOR above the `<details>` hides the summary too, so the rule "
        "must read the elements above the summary/`<details>` pair as well"
    )
    # 3c. …and the `<object>` FALLBACK, which `_UNRENDERED` used to drop from this
    #    recording (#14073 review round 7, P2, measured). `<object>` without a usable
    #    `data` is page-controlled rather than a support condition — HTML Standard 4.8.7
    #    enters the resource branch only "If the `data` attribute is present and its
    #    value is not the empty string", otherwise "The object element represents the
    #    element's children" — so its children ARE the rendering in every browser, and
    #    the `#storage` allowlist can never refuse it (a bare `<object>` adds no
    #    attribute). Measured before the fix: the allowlist, the destination pin, the
    #    vocabulary scan and the golden were ALL green.
    object_claim = section.replace(
        "</h2>",
        "</h2><object><p>FalkorDB permanently deletes your record once the allowance "
        "for your tier is reached</p></object>",
        1,
    )
    assert object_claim != section, "the `<object>` control could not inject"
    assert _attributes_outside_allowlist(object_claim) == [], (
        "premise: a bare `<object>` adds no attribute, so the allowlist cannot be what "
        "catches this"
    )
    assert sorted(set(extract_anchor_hrefs(object_claim))) == list(
        _REVIEWED_SECTION_HREFS
    ), "premise: `<object>` adds no href, so the destination pin cannot catch this"
    assert "falkor" in _banned_vocabulary(object_claim), (
        "an `<object>` with no usable resource paints its children, so its copy is one "
        "a reader meets; `_UNRENDERED` answers the BOUNDARY question and dropping the "
        "text here hides the claim from the pin that exists to catch it"
    )
    with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
        _assert_storage_section_reviewed(object_claim)

    # …and the SIX the measurement does NOT admit stay dropped, so admitting "a
    # fallback slot" wholesale cannot pass the row above: a supporting browser paints
    # none of their fallback text (measured, see `_FALLBACK_RENDERS`), so counting it
    # would red the golden on copy no reader meets. The set is pinned from BOTH sides —
    # add a member and this loop reds; remove one and the `<object>` row above reds.
    for undrawn in ("video", "audio", "canvas", "meter", "progress", "datalist"):
        undrawn_claim = section.replace(
            "</h2>",
            f"</h2><{undrawn}><p>FalkorDB permanently deletes your record</p></{undrawn}>",
            1,
        )
        assert undrawn_claim != section, f"the `<{undrawn}>` control could not inject"
        assert "falkor" not in _banned_vocabulary(undrawn_claim), (
            f"a supporting browser paints no fallback text of `<{undrawn}>`, so counting "
            f"it reddens the golden on legal markup; it is `_FALLBACK_RENDERS` that "
            f"separates it from `<object>`"
        )

    # …and the ATTRIBUTE-hidden cases a reader never meets STAY dropped at depth, or
    # "count everything" would pass the rows above. A closed `<details>`/`<dialog>` is
    # deliberately NOT in this list any more — the disclosure family is counted, which
    # is the loud side and the reason the six shapes above cannot fail open.
    for name, unreadable in (
        ("hidden subtree", "<div hidden><p>FalkorDB deletes</p></div>"),
        ("hidden summary", "<p hidden>FalkorDB deletes</p>"),
    ):
        mutant = section.replace("</h2>", f"</h2>{unreadable}", 1)
        assert mutant != section, f"the {name} control could not inject"
        assert "falkor" not in _banned_vocabulary(mutant), (
            f"{name}: no reader meets this copy in a supporting browser, so counting it "
            f"reddens the golden on legal markup"
        )

    # 3c. …and the SAME claim where a browser's tree has the `<summary>` as the
    #    `<details>`' child but THIS STACK does not (#14073 review round 8, P1,
    #    measured). The stack keeps elements the tokenizer has already closed, ignored
    #    or foster-parented, so a `<p>`/`<td>`/`<tr>`/`<caption>`/`<colgroup>`/`<table>`
    #    written between them sat "between" the details and its summary and inherited
    #    `False` down into it. Every browser closes or ignores those at the `<summary>`
    #    start tag and paints the summary (Chromium: +205 dark px per case,
    #    `details.open === false`, summary rect non-zero); before the fix the allowlist,
    #    the destinations, the vocabulary scan and the wording golden were ALL green.
    #    DRIVEN FOR EVERY SHAPE, because the shape family is the test — a single member
    #    is what left this open after round 7.
    for shape in ("p", "td", "tr", "caption", "colgroup", "table"):
        shelled = section.replace(
            "</h2>",
            "</h2><details><" + shape + ">Tap to read more<summary>FalkorDB "
            "permanently deletes your least-used items after thirty days"
            "</summary></details>",
            1,
        )
        assert shelled != section, f"the `<{shape}>` control could not inject"
        assert _attributes_outside_allowlist(shelled) == [], (
            f"premise: the `<{shape}>` shell adds no attribute, so the allowlist cannot "
            f"be what catches this"
        )
        assert sorted(set(extract_anchor_hrefs(shelled))) == list(
            _REVIEWED_SECTION_HREFS
        ), f"premise: the `<{shape}>` shell adds no href, so the destination pin cannot catch this"
        assert "falkor" in _banned_vocabulary(shelled), (
            f"a browser closes/ignores `<{shape}>` at the `<summary>` start tag, so the "
            f"summary IS the details' child and its words reach the reader"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(shelled)
    # 3d. …and the same claim where the element this stack keeps sits INSIDE the `<p>`
    #    rather than beside it (#14073 review round 9, P1, measured). The browser's
    #    implied `</p>` — spec "in body", a block-start tag such as `<summary>` with a
    #    `<p>` in button scope — pops the `<p>` AND everything opened inside it, so in
    #    `<details><p>Tap to read more<span><summary>CLAIM` the `<summary>` is the
    #    `<details>`' DIRECT child and is painted. The round-8 walk looked past
    #    `_SUMMARY_TRANSPARENT` members only, and `<span>` is not one, so it stopped
    #    there and dropped the claim. Measured in Chrome: `summary.parentElement` is
    #    `DETAILS`, rect 1264×18, `innerText` contains the claim — while
    #    `extract_visible_text` returned `''` and the allowlist, the destinations, the
    #    vocabulary scan and the wording golden were all green.
    #
    #    DRIVEN FOR EVERY PHRASING ELEMENT, because the family is the test: the
    #    round-8 row above drives a single wrapper and was what left this open.
    for phrasing in ("span", "b", "em", "code", "label"):
        nested = section.replace(
            "</h2>",
            "</h2><details><p>Tap to read more<" + phrasing + "><summary>FalkorDB "
            "permanently deletes your least-used items after thirty days"
            "</summary></" + phrasing + "></p></details>",
            1,
        )
        assert nested != section, f"the `<{phrasing}>`-inside-`<p>` control could not inject"
        assert _attributes_outside_allowlist(nested) == [], (
            f"premise: the `<{phrasing}>`-inside-`<p>` shell adds no attribute, so the "
            f"allowlist cannot be what catches this"
        )
        assert sorted(set(extract_anchor_hrefs(nested))) == list(
            _REVIEWED_SECTION_HREFS
        ), f"premise: the `<{phrasing}>`-inside-`<p>` shell adds no href, so the destination pin cannot catch this"
        assert "falkor" in _banned_vocabulary(nested), (
            f"the implied `</p>` pops `<{phrasing}>` together with the `<p>`, so the "
            f"summary IS the details' child and its words reach the reader; dropping "
            f"them is a claim painted and invisible to every storage pin"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(nested)
    # …and a `<span>`/`<b>` with NO `<p>` between it and the `<details>` is COUNTED
    # too. A browser keeps that wrapper, so this summary is not the details' child and
    # a closed details paints nothing here: counting it is the declared COST of the
    # loud rule (a false red), taken because the mirror error — deciding per wrapper
    # which one the browser kept — is what produced three fail-open rounds. The red is
    # manufactured the reviewer's way: a claim that never reached the golden.
    for kept_phrasing in ("span", "b"):
        no_p = section.replace(
            "</h2>",
            "</h2><details><" + kept_phrasing + "><summary>FalkorDB permanently "
            "deletes everything</summary></" + kept_phrasing + "></details>",
            1,
        )
        assert no_p != section
        assert "falkor" in _banned_vocabulary(no_p), (
            f"the disclosure family is counted whatever the browser keeps inside it, "
            f"so `<{kept_phrasing}>` does not hide the wording — that is the declared "
            f"cost of the loud rule, and the reason no wrapper choice can be a "
            f"hiding place"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(no_p)
    # 3e. …and the FOREIGN-BREAKOUT family (#14073 review round 10, P1, measured). A
    #    breakout start tag inside `<svg>`/`<math>` is NOT foreign content: the spec pops
    #    back to the HTML namespace and reprocesses the token under the "in body" rules,
    #    which is where the close-a-`<p>` step lives. `_close_p_scope` is called FIRST in
    #    `handle_starttag`, before `_open` clears `_foreign`, so gating it on `_foreign`
    #    skipped exactly the tags that DO close the `<p>`. The `<p>` then stayed on the
    #    stack carrying an `_UNRENDERED` child, `renders=False` inherited, and every
    #    following text node was dropped — measured, the parser returned `''` while
    #    Chrome built `<p><datalist><svg></svg></datalist></p><h2>…</h2>` and painted
    #    the claim at 1264×27. Driven over every `_UNRENDERED` child and several
    #    breakout tags, and through the real aggregate, because the fail-open was in the
    #    ANCESTRY and not in any one tag.
    for unrendered in ("datalist", "rp", "meter", "canvas", "video", "audio", "progress"):
        for breakout in ("h2", "div", "table"):
            escaped = section.replace(
                "</h2>",
                "</h2><p><" + unrendered + "><svg><" + breakout + ">FalkorDB permanently "
                "deletes your least-used items after thirty days",
                1,
            )
            assert escaped != section, f"the `<{unrendered}><svg><{breakout}>` control could not inject"
            assert "falkor" in _banned_vocabulary(escaped), (
                f"`<{breakout}>` breaks out of `<svg>`, so it is processed \"in body\" "
                f"where the implied `</p>` runs: the stale `<p>` and its `<{unrendered}>` "
                f"child must not survive to drop the following text. Dropping it is a "
                f"claim a browser paints and no pin reads"
            )
            with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
                _assert_storage_section_reviewed(escaped)
    # …and the parser row directly, because the end-to-end rows above can only fail
    # through the golden text and this names the seam that broke.
    for unrendered in ("datalist", "rp", "meter", "canvas", "video", "audio", "progress"):
        shape = f"<p><{unrendered}><svg><h2>CLAIMXYZ"
        assert "CLAIMXYZ" in extract_visible_text(shape), (
            f"`extract_visible_text({shape!r})` dropped a claim Chrome paints: the "
            f"breakout `<h2>` is HTML content, so it closes the `<p>` and the "
            f"`<{unrendered}>` with it, and the text after it is rendered"
        )
    # 3f. …and the RUBY family, where the ordinary-scope and button-scope questions
    #    differ (#14073 review round 12, P1, measured — a REGRESSION this branch
    #    introduced when `_ruby_in_scope` first reused the button-scope set). A
    #    `<button>` is an ordinary-scope NON-boundary but a BUTTON-scope boundary, so
    #    sharing one set made a `<button>` between the `<ruby>` and the `rp`/`rt`/`rb`/
    #    `rtc` suppress the spec's implied close: the `_UNRENDERED` `<rp>` stayed on the
    #    stack and every following text node was dropped — `extract_visible_text`
    #    returned `''` while Chrome built
    #    `<p><ruby><button><rp></rp><rt>CLAIM</rt></button></ruby>` (chain
    #    `RT -> BUTTON -> RUBY -> P`) and painted it. The previous commit's ruby rows all
    #    carry a `<ruby>` with nothing in between, so they could not red for this.
    for closer in ("rt", "rb", "rtc"):
        shape = f"<p><ruby><button><rp><{closer}>CLAIMXYZ"
        assert "CLAIMXYZ" in extract_visible_text(shape), (
            f"`extract_visible_text({shape!r})` dropped a claim Chrome paints: `<button>` "
            f"is an ORDINARY-scope non-boundary, so the `<ruby>` is still in scope, "
            f"`<{closer}>` generates the implied end tags, pops the `<rp>`, and the text "
            f"after it is rendered — reusing the button-scope boundaries here is the "
            f"regression this row pins"
        )
    # …and the protected case the precondition exists for is unchanged.
    assert "CLAIMXYZ" in extract_visible_text("<ruby><rp><rt>CLAIMXYZ</rt></rp></ruby>"), (
        "`<rt>` after `<rp>` must still generate implied end tags when a `<ruby>` is in "
        "scope: the precondition is not 'never close', it is 'only when ruby is in scope'"
    )
    # …and the NEGATIVE that stops the carve-out becoming "skip anything": an element
    # the browser DOES keep between them (`<div>`, `<section>`) means the summary is not
    # the details' child, and a closed details paints no body.
    # The shapes that broke the MODELLING approach are now counted by the ONE rule, so
    # none of them can fail open. Driven per shape because the family is the test: a
    # single member is what left each earlier round open.
    for shape in ("div", "section", "span", "td", "tr", "caption", "colgroup", "table",
                  "image", "frame", "head", "basefont", "bgsound", "keygen"):
        shelled = section.replace(
            "</h2>",
            "</h2><details><" + shape + "><summary>FalkorDB permanently deletes "
            "everything</summary></" + shape + "></details>",
            1,
        )
        assert shelled != section, f"the `<{shape}>` carrier could not inject"
        assert "falkor" in _banned_vocabulary(shelled), (
            f"text inside a `<details>` is COUNTED by this seam whatever the tag above "
            f"it is (`<{shape}>` here): modelling which part of a closed disclosure a "
            f"browser paints is what produced three rounds of fail-open"
        )
    # ⛔ P0, ROUND 10 — an INCOMPLETE `_P_CLOSERS` IS A FAIL-OPEN, NOT A NICETY
    # (#14073 review round 10, measured). `_close_p_scope` pops the `<p>` and
    # everything inside it, so a p-closer missing from the set leaves an
    # `_UNRENDERED` element on the stack and the loud rule then drops painted copy.
    # Measured before the fix: `<p><audio><li>CLAIM</li></audio></p>` — Chrome's tree is
    # `<p><audio></audio></p><li>CLAIM</li>` at body level, 194 painted px, and this
    # scan returned `''`. Driven as the full 6×4 grid so a member cannot be dropped
    # from the set unnoticed.
    for unrendered in ("audio", "video", "canvas", "datalist", "meter", "progress"):
        for closer in ("li", "dd", "dt", "plaintext", "search"):
            closed_p = section.replace(
                "</h2>",
                f"</h2><p><{unrendered}><{closer}>FalkorDB permanently deletes it"
                f"</{closer}></{unrendered}></p>",
                1,
            )
            assert closed_p != section
            assert "falkor" in _banned_vocabulary(closed_p), (
                f"`<{closer}>` closes the open `<p>` in button scope, which pops the "
                f"`<{unrendered}>` above it, so a browser paints the claim at body "
                f"level; the scan must read it"
            )
            # `<plaintext>` also swallows the REST of the document as raw text, so the
            # golden refuses through the destination pin (the section's link is gone)
            # rather than the wording pin — still a refusal, and the vocabulary
            # assertion above is the one that measures the fail-open.
            with pytest.raises(
                AssertionError,
                match=None if closer == "plaintext" else "no longer matches the reviewed wording",
            ):
                _assert_storage_section_reviewed(closed_p)

    # ⛔ P1, ROUND 12 — THE OTHER THREE `_IMPLICITLY_CLOSED` SIBLINGS. The set named
    # `button`, `a` and `nobr` as tags a sibling start tag closes, but only the block
    # family was modelled in `_close_implied`, so their close never ran on the text
    # path and an `_UNRENDERED` child survived it. Measured in Chrome:
    # `<button><audio><button>CLAIM` renders `<button><audio></audio></button>
    # <button>CLAIM</button>` (rect 120x15, `closest('audio')` null), and `<a>`/`<nobr>`
    # land the same way through the adoption agency algorithm (`143x17`, no audio
    # ancestor) — while this scan returned `''` for all three. Driven per token so a
    # member cannot be dropped from `_close_implied` unnoticed.
    for unrendered in ("audio", "video", "canvas", "datalist", "meter", "progress"):
        for sibling in ("a", "button", "nobr"):
            closed_sibling = section.replace(
                "</h2>",
                f"</h2><{sibling}><{unrendered}><{sibling}>FalkorDB permanently "
                f"deletes it</{sibling}></{unrendered}></{sibling}>",
                1,
            )
            assert closed_sibling != section
            assert "falkor" in _banned_vocabulary(closed_sibling), (
                f"`<{sibling}>` closes the open `<{sibling}>` and everything inside it "
                f"(pops the `<{unrendered}>` above), so a browser paints the claim at "
                f"body level; the scan must read it"
            )

    # ⛔ P1, ROUND 13 — THE ADOPTION AGENCY ALGORITHM RELOCATES A BLOCK OUT of an
    # unrendered ancestor, and the paint seam has already judged the text by then.
    # Measured in Chrome: `<strong><canvas><div>CLAIM</canvas></strong>` paints the
    # claim at 224x17 px with `closest('canvas')` null, while the SAME misnesting
    # without a formatting element (`<canvas><div>CLAIM</canvas>`) stays 0x0 inside
    # the canvas. So the seam cannot decide, and it must not guess: it refuses.
    # Both controls ride along — the shape WITHOUT the formatting element must still
    # be quietly dropped (a refusal rule that fires on legal/mended markup gets
    # deleted), and a well-formed unrendered subtree must not fire at all.
    for unrendered in ("audio", "video", "canvas", "datalist", "meter", "progress"):
        # ⛔ BOTH SPELLINGS, because the relocation is the browser IGNORING the
        # misnested close and the two differ in WHICH end tag arrives first:
        #   (i)  `</canvas>` present → this parser still holds the block, so the
        #        unrendered end tag is where the pattern is visible;
        #   (ii) `</canvas>` ABSENT  → the formatting end tag is the only arrival.
        # Round 14 measured (i) and (ii) as SEPARATE escapes: a check keyed to one
        # end tag alone leaves the other green over a painted claim.
        for _shape, body in (
            ("closed", f"<strong><{unrendered}><div>{{claim}}</{unrendered}></strong>"),
            ("unclosed", f"<strong><{unrendered}><div>{{claim}}</strong>"),
            # AAA's furthestBlock must be a SPECIAL element; `button` is special and
            # is NOT a `</p>`-closer, which is why `_SPECIAL` and not `_P_CLOSERS`
            # governs this test.
            ("button", f"<strong><{unrendered}><button>{{claim}}</{unrendered}></strong>"),
        ):
            escaped = section.replace(
                "</h2>",
                "</h2>" + body.replace("{claim}", "FalkorDB permanently deletes it"),
                1,
            )
            assert escaped != section
            with pytest.raises(ValueError, match="adoption-agency-relocation"):
                _visible_text(escaped)
        # control 1: no formatting element below → AAA cannot lift it, the text really
        # is inside the unrendered element, and the seam keeps dropping it.
        control = section.replace(
            "</h2>",
            f"</h2><{unrendered}><div>FalkorDB permanently deletes it"
            f"</{unrendered}>",
            1,
        )
        assert "falkor" not in _banned_vocabulary(control), (
            f"a block misnested inside a `<{unrendered}>` with NO formatting element "
            f"is reported as painted: AAA has no furthestBlock to relocate, so a "
            f"browser keeps the text inside the unrendered element"
        )
    # ⛔ AND A `hidden` ANCESTOR IS NOT A TAG EITHER (#14073 review round 24, P2, measured
    # in Chrome). Every row above keys the unrendered ancestor on its TAG; `hidden` instead
    # rides the stack's INHERITED flag, so the pattern saw nothing to lift a block out of and
    # the text was silently DROPPED while a browser painted it:
    #
    #   '<a hidden><ul><a>CLAIM</ul></a>'                     chrome: painted   module: ''
    #   '<a><span hidden><ul><a>CLAIM</ul></a></span></a>'    chrome: painted   module: ''
    #   '<nobr hidden><ul><nobr>CLAIM</ul></nobr>'            chrome: painted   module: ''
    #   '<a><ul><a>CLAIM</ul></a>'  (control, no hidden)      chrome: painted   module: 'CLAIM'
    #   '<a hidden><div>CLAIM</a>' (furthest block →  body)  chrome: painted   module: ''
    #
    # The hidden element may be the formatting element ITSELF (rows 1, 3, 5) or a wrapper
    # below it (row 2), and both are refused now. AAA's own precondition still gates it —
    # no SPECIAL element below means no furthest block, nothing relocates, and the text
    # really is hidden (`<a hidden><ul>CLAIM</ul></a>` stays dropped) — so a refusal cannot
    # fire on a shape where the claim never becomes visible.
    for hidden_shape in (
        "<a hidden><ul><a>FalkorDB permanently deletes it</ul></a>",
        "<a><span hidden><ul><a>FalkorDB permanently deletes it</ul></a></span></a>",
        "<nobr hidden><ul><nobr>FalkorDB permanently deletes it</ul></nobr>",
        "<a hidden><div>FalkorDB permanently deletes it</a>",
    ):
        with pytest.raises(ValueError, match="adoption-agency-relocation"):
            extract_visible_text(hidden_shape)
    # controls: the SAME shapes without the `hidden` are painted (so the rows above are
    # about the flag, not about refusing anything with a formatting element)…
    assert "falkor" in _banned_vocabulary(
        extract_visible_text("<a><ul><a>FalkorDB permanently deletes it</ul></a>")
    )
    # …and the two ancestors whose flag is EXPECTED to be False stay quiet, because their
    # text is counted elsewhere: an unopened `<details>` (the disclosure family) and
    # `<object>` (a browser paints its fallback). Refusing there would be a FALSE BLOCK,
    # which is how a refusal rule gets deleted.
    assert "falkor" in _banned_vocabulary(
        extract_visible_text("<b><details><ul><b>FalkorDB permanently deletes it")
    )
    assert "falkor" in _banned_vocabulary(
        extract_visible_text(
            '<a><object><div>FalkorDB permanently deletes it</div></object></a>'
        )
    )
    # ⛔ AND AAA IS NOT AN END-TAG RULE (#14073 review round 15, P0, measured).
    # Spec 13.2.6.4.7 "in body" runs the adoption agency algorithm on two START
    # tags: an `<a>` while an `a` is in the active formatting list, and a `<nobr>`
    # while a `nobr` is in scope. Both relocate a block out of an unrendered
    # ancestor, and both arrive BEFORE `_close_implied` pops the stack the pattern
    # reads — so a check that lives only in `handle_endtag` is defeated by never
    # emitting the end tag at all. Measured in Chrome: `<a><canvas><div>CLAIM<a>`
    # paints the claim at 52x17 px (`chain a>div>body`), `<nobr>...<nobr>` likewise,
    # while the closed control `<a><canvas><div>CLAIM</div><a>` stays 0x0 inside the
    # canvas. `extract_visible_text` returned '' for all of them.
    for start_tag in ("a", "nobr"):
        for unrendered in ("canvas", "video"):
            escaped = section.replace(
                "</h2>",
                f"</h2><{start_tag}><{unrendered}><div>FalkorDB permanently "
                f"deletes it<{start_tag}>",
                1,
            )
            assert escaped != section
            with pytest.raises(ValueError, match="adoption-agency-relocation"):
                _visible_text(escaped)
            # control: the block CLOSED before the second formatting start tag — no
            # furthestBlock to lift, so the text stays unrendered and is dropped.
            closed = section.replace(
                "</h2>",
                f"</h2><{start_tag}><{unrendered}><div>FalkorDB permanently "
                f"deletes it</div><{start_tag}>",
                1,
            )
            assert "falkor" not in _banned_vocabulary(closed)
    # ⛔ …AND ONLY THOSE TWO START TAGS. Every other formatting start tag just
    # reconstructs the active formatting list and pushes; it runs no AAA, so a
    # `_FORMATTING`-wide check here OVER-fires. Measured: `<b><canvas><div>CLAIM<b>`
    # is 0x0 inside the canvas — the text really is unrendered, and dropping it is
    # the correct answer, not a reason to refuse the page.
    for no_aaa in ("b", "i", "em", "strong", "u", "code", "font", "s"):
        # A SELF-CONTAINED document, not `section` with this injected: leaving a
        # `<canvas><div>` open would feed the whole remainder of the page into the
        # unrendered subtree, which is a different (genuinely ambiguous) document.
        # This isolates exactly the claim — does the START tag alone trigger AAA?
        quiet = (
            f"<p>x</p><{no_aaa}><canvas><div>FalkorDB permanently deletes it"
            f"<{no_aaa}>"
        )
        assert "falkor" not in _banned_vocabulary(quiet), (
            f"`<{no_aaa}>` runs no adoption agency algorithm on its START tag, so "
            f"a block misnested inside a `<canvas>` stays unrendered and must be "
            f"dropped quietly — refusing the page here is a false block"
        )

        # control 2: well-formed unrendered content must not refuse the page.
        wellformed = section.replace(
            "</h2>",
            "</h2><canvas><div>fallback</div></canvas>",
            1,
        )
        _visible_text(wellformed)

    # ⛔ P1 OVER-FIRE — THE START-TAG CALL SITE NEEDS THE SPEC'S PRECONDITION
    # (#14073 review, measured). The pattern's bottom slot accepts ANY `_FORMATTING`
    # member, but the spec runs AAA on an `<a>` start tag only while an `a` is
    # ALREADY in the formatting list, and on a `<nobr>` only while a `nobr` is in
    # scope — see `_formatting_in_scope`. Without that gate the check fired on an
    # already-open `b` in front of an arriving `a`. Chrome, measured on this box:
    # text rect 0x0, link 0x0, `innerText` '' — the page paints NOTHING, so refusing
    # it is a false block.
    for over_fire in (
        '<b><canvas><div>FalkorDB permanently deletes it<a href="/blog">x',
        '<strong><video><div>FalkorDB permanently deletes it<nobr>',
        # …and `<template>` is a SCOPE BOUNDARY, so the OUTER `a` is not in scope for
        # the inner one and no AAA runs. Chrome: 0 ink px.
        '<a><canvas><div>MARK<template><a href="/x">INNER</a></template>'
        "</div></canvas></a>",
    ):
        assert "falkor" not in _banned_vocabulary(_visible_text("<p>x</p>" + over_fire)), (
            "no formatting element of the ARRIVING tag's own name is open in scope, so "
            "the spec runs no adoption agency algorithm here and a browser paints "
            "nothing — refusing the page is a false block"
        )
    # …while the shapes the rule DOES name still refuse, so the gate above cannot be
    # "fixed" by deleting the call site (which would reopen the round-15 P0).
    for still_refuses in (
        '<a><canvas><div>FalkorDB permanently deletes it<a href="/blog">x',
        "<nobr><canvas><div>FalkorDB permanently deletes it<nobr>",
    ):
        with pytest.raises(ValueError, match="adoption-agency-relocation"):
            _visible_text("<p>x</p>" + still_refuses)

    # ⛔ P2d — `object` IS a `_FALLBACK_RENDERS` member, not an unrendered middle
    # link (#14073 review, measured). The paint seam COUNTS an `<object>`'s text
    # (`renders=True`), so nothing was discarded on arrival and AAA lifting a block
    # out of it recovers no hidden claim — yet the pattern refused a page whose own
    # seam had already read that text. Driven through the END-tag arrival, which is
    # the path the scope gate does not already cover.
    for object_aaa in (
        "<a><object><div>FalkorDB permanently deletes it</a>",
        '<a><object><div>FalkorDB permanently deletes it'
        '<a href="/blog">y</a></div></object></a>',
    ):
        assert "falkor" in _banned_vocabulary(_visible_text("<p>x</p>" + object_aaa)), (
            "an `<object>` without a usable resource renders its children, so the seam "
            "counts that text; refusing the page contradicts the seam it is built on"
        )

    # ⛔ P1, ROUND 12 — FOSTER PARENTING IS PER TABLE. A cell belonging to a NESTED
    # table must not end the OUTER table's truncation. Measured:
    # `<table><audio><table><tr><td>CLAIM</td></tr></table></audio></table>` — Chrome
    # foster-parents the `<audio>` out (`closest('audio')` null, 143x17 painted) while
    # this scan kept `[table, audio, …]` and returned `''`.
    for unrendered in ("audio", "video", "canvas", "datalist", "meter", "progress"):
        nested = section.replace(
            "</h2>",
            f"</h2><table><{unrendered}><table><tr><td>FalkorDB permanently deletes it"
            f"</td></tr></table></{unrendered}></table>",
            1,
        )
        assert nested != section
        assert "falkor" in _banned_vocabulary(nested), (
            f"a cell of an INNER table ended the outer table's truncation: the "
            f"foster-parented `<{unrendered}>` is not an ancestor of that text in a "
            f"browser, so the claim is painted and must be read"
        )

    # ⛔ P0, ROUND 10 — FOSTER PARENTING: a browser moves text OUT of a table, so the
    # table is not its ancestry at all. Measured: `<table><audio><table>CLAIM</table>
    # </audio></table>` paints at body level (Chrome's tree is `<audio></audio><table>
    # </table>CLAIM<table></table>`, 194 painted px) while this stack saw it under
    # `audio` and dropped it. The "in cell"/"in caption" modes are where text stays in
    # the table, so those keep dropping it — asserted as the negatives right after.
    for unrendered in ("audio", "video", "canvas", "datalist", "meter", "progress", "object"):
        fostered = section.replace(
            "</h2>",
            f"</h2><table><{unrendered}><table>FalkorDB permanently deletes it"
            f"</table></{unrendered}></table>",
            1,
        )
        assert fostered != section
        assert "falkor" in _banned_vocabulary(fostered), (
            f"a non-whitespace text node in the `in table` mode is foster parented in "
            f"front of the table, so a `<{unrendered}>` between them is discarded and "
            f"the claim is painted at the table's parent level"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(fostered)

    # …and the truncation can only REMOVE ancestors, so it never manufactures a drop:
    # a `hidden` ancestor of the TABLE still hides the foster-parented text, and text
    # in the "in cell" mode is never moved out of the table at all.
    for label, markup in (
        ("hidden table ancestor",
         "<div hidden><table><audio><table>FalkorDB permanently deletes it</table>"
         "</audio></table></div>"),
        ("in cell", "<table><tr><td><audio>FalkorDB permanently deletes it"
                    "</audio></td></tr></table>"),
    ):
        kept_dark = section.replace("</h2>", "</h2>" + markup, 1)
        assert kept_dark != section
        assert "falkor" not in _banned_vocabulary(kept_dark), (
            f"{label}: foster-parented text lands before the TABLE, so an unrendered "
            f"ancestor of the table still hides it — and a cell never foster parents "
            f"at all"
        )

    # ⛔ P0, ROUND 11 — MODELLING ONLY `<p>` WAS NOT ENOUGH. The spec closes a whole
    # family of SIBLINGS on their own start tag, and an `rt`/`rp`/`rb`/`rtc` start tag
    # runs "generate implied end tags"; an omitted close leaves an `_UNRENDERED`
    # element above the text and the loud rule then drops painted copy. Measured before
    # the fix, each of these scanned as `''` while Chrome painted the claim:
    #   `<ul><li><audio><li>CLAIM` -> `<li><audio></audio></li><li>CLAIM</li>` (23 817 px)
    #   `<ruby><rp><rt>CLAIM`      -> `<ruby><rp></rp><rt>CLAIM</rt></ruby>` (5 997 px)
    # Driven per member, because the family is the test: one shape proves one rule.
    for label, markup in (
        ("li closes li", "<ul><li><audio><li>FalkorDB permanently deletes it</li></audio></li></ul>"),
        ("dd closes dd", "<dl><dd><audio><dd>FalkorDB permanently deletes it</dd></audio></dd></dl>"),
        ("dt closes dt", "<dl><dt><progress><dt>FalkorDB permanently deletes it</dt></progress></dt></dl>"),
        ("rt implies rp closed", "<ruby><rp><rt>FalkorDB permanently deletes it</rt></rp></ruby>"),
        ("rb implies rp closed", "<ruby><rp><rb>FalkorDB permanently deletes it</rb></rp></ruby>"),
        ("rtc implies rp closed", "<ruby><rp><rtc>FalkorDB permanently deletes it</rtc></rp></ruby>"),
        ("option closes option", "<select><option><audio>FalkorDB permanently deletes it"
                                 "</option></audio></select>"),
        ("optgroup closes optgroup", "<select><optgroup><video>FalkorDB permanently deletes it"
                                     "</optgroup></video></select>"),
        # ⛔ ROUND 14 — the implied close asks the ORDINARY scope question, and a
        # token a browser IGNORES is not a boundary. Both were measured fail-opens
        # through all four `#storage` pins before the fix.
        ("rt implies rp closed through a button",
         "<ruby><button><rp><rt>FalkorDB permanently deletes it</rt></rp></button></ruby>"),
        ("a stray `<td>` does not stop the implied close",
         "<p><td><rp><div>FalkorDB permanently deletes it</div></rp></td></p>"),
    ):
        sibling = section.replace("</h2>", "</h2>" + markup, 1)
        assert sibling != section, f"the {label} control could not inject"
        assert "falkor" in _banned_vocabulary(sibling), (
            f"{label}: the spec's own start-tag step closes the earlier sibling AND "
            f"whatever was opened inside it, so a browser paints the claim — leaving "
            f"the unrendered wrapper on this stack drops text the reader meets"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(sibling)

    # ⛔ P0, ROUND 11 — A FOSTER-PARENTED ELEMENT IS NOT AN ANCESTOR OF THE CELL. The
    # "in table" mode moves an element opened there OUT, in front of the table, so a
    # `<td>` that follows inherits the TABLE's render flag, not the discarded
    # element's. Measured: `<table><audio><td>CLAIM</td></audio></table>` renders
    # `<audio></audio><table><tbody><tr><td>CLAIM</td>…` (`td.closest('audio')` is
    # null, 34 735 dark px) while this stack had `audio` between the table and the
    # `td` and dropped the claim.
    for unrendered in ("audio", "video", "canvas", "datalist", "meter", "progress", "rp"):
        for structure in ("td", "caption", "tr><td"):
            table_cell = section.replace(
                "</h2>",
                f"</h2><table><{unrendered}><{structure}>FalkorDB permanently deletes it"
                f"</{structure.split('>')[-1]}></{unrendered}></table>",
                1,
            )
            assert table_cell != section
            assert "falkor" in _banned_vocabulary(table_cell), (
                f"`<{unrendered}>` was opened in the 'in table' mode, so a browser "
                f"foster parents it OUT and the `<{structure}>` is not inside it — "
                f"the claim is painted inside the cell"
            )
            with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
                _assert_storage_section_reviewed(table_cell)

    # …and the cell does NOT launder a wrapper that is genuinely inside it: text under
    # an `_UNRENDERED` element opened INSIDE the cell stays dropped, and so does text
    # under one inside a nested table in the cell (measured: `audio` still an ancestor
    # of the text in both, `closest('audio')` is the wrapper).
    for label, markup in (
        ("wrapper inside the cell",
         "<table><tr><td><audio>FalkorDB permanently deletes it</audio></td></tr></table>"),
        ("wrapper inside a nested table cell",
         "<table><tr><td><audio><table>FalkorDB permanently deletes it</table>"
         "</audio></td></tr></table>"),
    ):
        keep_dark = section.replace("</h2>", "</h2>" + markup, 1)
        assert keep_dark != section
        assert "falkor" not in _banned_vocabulary(keep_dark), (
            f"{label}: this wrapper IS an ancestor of the text in a browser, so "
            f"counting it would be a false red that the loud rule must not introduce"
        )

    # ⛔ P0, ROUND 12 — THE SIBLING FAMILY IS `a`/`button`/`nobr` TOO, and `search` is a
    # block-start `<p>`-closer. Each omission left an `_UNRENDERED` wrapper on the stack
    # and the loud rule dropped a claim a browser paints (measured, with the end-to-end
    # injection leaving all four `#storage` pins green):
    #   `<p><audio><search>CLAIM`   -> `<p><audio></audio></p><search>CLAIM`    122 px
    #   `<a><audio><a>CLAIM`        -> `<a><audio></audio></a><a>CLAIM</a>`     122 px
    #   `<button><audio><button>…`  -> `<button><audio></audio></button><button>…` 75 px
    #   `<nobr><audio><nobr>CLAIM`  -> `<nobr><audio></audio></nobr><nobr>…`    122 px
    # …and the CONTROL that must NOT close: a heading does not close a heading, so a
    # browser keeps `<h2>` inside the `<audio>` (0 px) and this stack must agree.
    for label, markup in (
        ("search closes p", "<p><audio><search>FalkorDB permanently deletes it</search></audio></p>"),
        ("a closes a", "<a><audio><a>FalkorDB permanently deletes it</a></audio></a>"),
        ("button closes button",
         "<button><audio><button>FalkorDB permanently deletes it</button></audio></button>"),
        ("nobr closes nobr", "<nobr><audio><nobr>FalkorDB permanently deletes it</nobr></audio></nobr>"),
    ):
        closes = section.replace("</h2>", "</h2>" + markup, 1)
        assert closes != section, f"the {label} control could not inject"
        assert "falkor" in _banned_vocabulary(closes), (
            f"{label}: a browser closes the earlier sibling AND discards the wrapper "
            f"inside it, so the claim is painted at the parent's level"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(closes)

    heading_in_audio = section.replace(
        "</h2>",
        "</h2><h1><audio><h2>FalkorDB permanently deletes it</h2></audio></h1>",
        1,
    )
    assert heading_in_audio != section
    assert "falkor" not in _banned_vocabulary(heading_in_audio), (
        "a heading does NOT close a heading, so the browser keeps the claim inside the "
        "`<audio>` and paints none of it — counting it would be a false red"
    )

    # ⛔ P0, ROUND 12 — THE LABEL GATE IS THE `<select>`'s FLAG, NOT THE OPTION'S. A
    # wrapper BETWEEN them does not stop the label painting (measured: 67 px with and
    # without), so gating on the option's inherited flag dropped a painted label.
    for wrapper in ("audio", "video", "canvas", "meter", "progress", "rp"):
        wrapped_label = section.replace(
            "</h2>",
            f"</h2><select><{wrapper}><option>FalkorDB permanently deletes it</option>"
            f"</{wrapper}></select>",
            1,
        )
        assert wrapped_label != section
        assert "falkor" in _banned_vocabulary(wrapped_label), (
            f"the option's LABEL is its text content, so `<{wrapper}>` between the "
            f"select and the option does not hide it — the enclosing select's own "
            f"render flag is what decides"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(wrapped_label)

    # …and the select's flag still governs: a `hidden` select hides the label, and a
    # `<datalist>` option is not painted at all (both measured 0 px).
    for label, markup in (
        ("hidden select", "<div hidden><select><option>FalkorDB permanently deletes it"
                          "</option></select></div>"),
        ("datalist option", "<datalist><option><audio>FalkorDB permanently deletes it"
                            "</audio></option></datalist>"),
    ):
        dark_label = section.replace("</h2>", "</h2>" + markup, 1)
        assert dark_label != section
        assert "falkor" not in _banned_vocabulary(dark_label), (
            f"{label}: a browser paints none of this, so the loud rule must not count it"
        )

    # ⛔ P1, ROUND 17 — A PAINTED LABEL REPAINTS *ANY* RAW-TEXT MEMBER'S TEXT. The
    # admission in `handle_data` was `all(tag in _RAW_RENDERS …)`, which is FALSE for a
    # `<title>`/`<iframe>`/`<style>`/`<noembed>`/`<noframes>`/`<noscript>` — so the drop
    # happened BEFORE the label carve-out could see it, and a claim written inside one
    # of those INSIDE an option was painted by every browser and invisible to all four
    # `#storage` pins. Measured (screenshot ink, claim vs the same fragment without it):
    # `<select><option><title>CLAIM</title></option></select>` +1360 px, the same
    # `<title>` in a `<div>` 0, a `<template>` inside the option 0, and a `hidden`
    # select 0. Each row below is one of those measurements.
    for raw in ("title", "iframe", "noembed", "noframes"):
        raw_label = section.replace(
            "</h2>",
            f"</h2><select><option><{raw}>FalkorDB permanently deletes it</{raw}></option>"
            f"</select>",
            1,
        )
        assert raw_label != section, f"the `<{raw}>`-in-a-label control could not inject"
        assert "falkor" in _banned_vocabulary(raw_label), (
            f"a `<{raw}>` inside a painted `<option>` was dropped: the label renders its "
            f"whole text content, so the claim IS painted (measured: 1360 ink px over the "
            f"same fragment without it) while all four #storage pins stayed green"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(raw_label)
    # `<style>` is the SAME painted label, and it is now refused by the DIRECTIVE
    # backstop first (`extract_raw_text_elements`): a rendering directive paints what
    # no text pin can read, so it is refused rather than cleared — the same shape as
    # `<noscript>` below, and loud either way (#14073 review round 7). Its own arm, so
    # the paint premise is still asserted while the firing pin is the allowlist.
    style_label = section.replace(
        "</h2>",
        "</h2><select><option><style>FalkorDB permanently deletes it</style>"
        "</option></select>",
        1,
    )
    assert style_label != section, "the `<style>`-in-a-label control could not inject"
    assert "falkor" in _banned_vocabulary(style_label), (
        "a `<style>` inside a painted `<option>` was dropped from the wording pin, so "
        "the premise this arm pins (the claim IS painted) no longer holds"
    )
    assert "<style>" in _attributes_outside_allowlist(style_label), (
        "a `<style>` in the section is not refused by the directive backstop, so a "
        "`content:` property can paint a claim every pin here stays green over"
    )
    with pytest.raises(AssertionError, match="<style>"):
        _assert_storage_section_reviewed(style_label)
    # `<noscript>` is the SAME painted label (measured +1360) but it is refused by the
    # ATTRIBUTE backstop first (`_mode_dependent`: a `<noscript>` region's rendering
    # depends on the scripting mode), so the pin that fires is the allowlist, not the
    # golden — loud either way, and asserted with its own premise below.
    noscript_label = section.replace(
        "</h2>",
        "</h2><select><option><noscript>FalkorDB permanently deletes it</noscript>"
        "</option></select>",
        1,
    )
    assert noscript_label != section, "the `<noscript>`-in-a-label control could not inject"
    assert "<noscript>" in _attributes_outside_allowlist(noscript_label), (
        "premise: a `<noscript>` region is refused by the mode-dependent allowlist rule, "
        "so the next assertion measures THAT pin and not the wording golden"
    )
    with pytest.raises(AssertionError, match="carries attribute"):
        _assert_storage_section_reviewed(noscript_label)
    # ── the three controls that keep the rule from over-admitting ──
    for label, markup in (
        # the same `<title>` outside a label paints nothing (0 ink px).
        ("a `<title>` outside a label", "<title>FalkorDB permanently deletes it</title>"),
        # an INERT `<template>` inside the label is not repainted (0 ink px).
        ("a `<template>` inside the label",
         "<select><option><template>FalkorDB permanently deletes it</template></option></select>"),
        # and a hidden select hides the label whatever raw text it holds (0 ink px).
        ("a `<title>` in a hidden select's label",
         "<div hidden><select><option><title>FalkorDB permanently deletes it</title>"
         "</option></select></div>"),
    ):
        not_painted = section.replace("</h2>", "</h2>" + markup, 1)
        assert not_painted != section, f"the {label} control could not inject"
        assert "falkor" not in _banned_vocabulary(not_painted), (
            f"{label}: a browser paints none of this (measured 0 ink px), so the label "
            f"admission must not reach it — a `_RAW` member's text is admitted ONLY "
            f"where a painted label repaints it"
        )

    # ⛔ P1, ROUND 18 — THE LABEL QUESTION NEEDS THE UNTRUNCATED ANCESTRY, and this is
    # the shape that proved it. A `<select>` written inside a `<table>` is
    # FOSTER-PARENTED OUT of the table by the browser, so it is real and painted — and
    # because the label admission asked `_foster_truncated_ancestry()`, the `<select>`
    # and `<option>` frames were cut away and the claim was dropped. Measured on this
    # box: the foster-parented select is 91 px wide with the claim and 22 px without
    # (+591 ink px), and it sits between `#storage` and `#quickstart` — so an
    # end-to-end insertion left all four `#storage` pins green over a painted sentence.
    foster_label = (
        "<table><select><option><title>FalkorDB permanently deletes it</title></option>"
        "</select></table>"
    )
    assert "falkor" in _banned_vocabulary(section.replace("</h2>", "</h2>" + foster_label, 1)), (
        "a `<select>` inside a `<table>` is foster-parented OUT of it and painted (Chrome: "
        "91 px with the claim, 22 px without), so the label admission must read the "
        "UNTTRUNCATED stack — truncating at the table removed exactly the frames that "
        "are the evidence, turning a count into a drop"
    )
    with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
        _assert_storage_section_reviewed(section.replace("</h2>", "</h2>" + foster_label, 1))

    # ⛔ P2, ROUND 18 — BUT A LABEL NEEDS A `<select>` ANCESTOR. Only a *select's* option
    # is flattened to a label; a BARE `<option>`/`<optgroup>` lays its children out
    # normally, so a `<title>` inside it is `display:none` and paints nothing. Measured:
    # `<option><title>CLAIM</title></option>` -> the `<title>` rect is 0×0 and the ink
    # delta is 0 (while the option's own text paints 626 px). Counting it was a false red
    # INSIDE the golden — the wrong direction to be wrong in, but still wrong.
    for label, markup in (
        ("a bare `<option>`", "<option><title>FalkorDB permanently deletes it</title></option>"),
        ("a bare `<optgroup>`",
         "<optgroup><title>FalkorDB permanently deletes it</title></optgroup>"),
    ):
        bare_label = section.replace("</h2>", "</h2>" + markup, 1)
        assert bare_label != section, f"the {label} control could not inject"
        assert "falkor" not in _banned_vocabulary(bare_label), (
            f"{label} with no `<select>` above it is NOT flattened to a label: a browser "
            f"lays its children out normally, so the `<title>` is `display:none` and "
            f"paints nothing (measured: `<title>` rect 0×0, ink delta 0)"
        )

    # ⛔ DECLARED RESIDUAL: DECLARATIVE SHADOW DOM (#14073 review round 18, P2, measured).
    # `<template shadowrootmode>` IS rendered by a browser (Chrome: `div.shadowRoot` is
    # the span, range 84×17 px, ink 590 vs 0), and this model drops it like any other
    # template. Not fixed here: it needs a shadow-tree boundary that the rest of the
    # ancestry model does not have, and the class docstring names it as a limit next to
    # the foreign/`<frameset>` ones. What this row holds is the LOUD half — the
    # attribute backstop refuses the mode attribute, so a page using it cannot pass
    # silently on the pins even though the text scan is blind.
    shadow = "<div><template shadowrootmode=\"open\"><span>FalkorDB permanently deletes it</span></template></div>"
    assert "shadowrootmode" in " ".join(_attributes_outside_allowlist(
        section.replace("</h2>", "</h2>" + shadow, 1)
    )), (
        "the text scan is blind to declarative shadow DOM, so the ATTRIBUTE allowlist is "
        "the loud half: a page that uses `<template shadowrootmode>` must be refused "
        "there, or this residual becomes a silent fail-open"
    )

    # ⛔ P2, ROUND 12 — `-->` ENDS script-data-escaped, so the wedge must NOT fire. The
    # guard used to raise for `<script><!--<script>--></script>`, which a browser reads
    # normally (Chrome closes the script and paints what follows, 122 px), reddening the
    # destinations pin on legal markup. It still fires for the shape it exists for: an
    # escaped `<script` with no `-->` after it, where `</script` stops closing.
    assert " ".join(_visible_text(
        "<script><!--<script>--></script><p>SEE THIS</p>"
    ).split()) == "SEE THIS", (
        "the `-->` returns the tokenizer to script data, so `</script>` closes the "
        "element and the following text is painted — this must not wedge"
    )
    # …and the wedge must NOT fire on the shapes a chunk-local regex got wrong
    # (#14073 review round 13, P2, measured). Each of these was REFUSED while Chrome
    # closes the script and paints what follows (372 px), reddening the destinations
    # golden on legal markup: the `-->` returns the tokenizer to plain script data (so
    # the later `<script` is just text), and a `</script` reached from the double-escaped
    # state only drops back to ESCAPED, where the NEXT one closes the element.
    for label, markup, expected in (
        ("no escaped <script> at all", "<script><!--x--><script></script><p>SEE THIS</p>", "SEE THIS"),
        ("--> then text then <script>", "<script><!--a-->b<script></script><p>SEE THIS</p>", "SEE THIS"),
        ("--> then a second <script>", "<script><!--<script>--><script></script><p>SEE THIS</p>", "SEE THIS"),
    ):
        assert " ".join(_visible_text(markup).split()).endswith(expected), (
            f"{label}: a browser closes the script and paints the following text, so "
            f"this must be READ, not refused — the wedge is 'THIS CHUNK ends "
            f"double-escaped', not 'a `<script` with no `-->` in it'"
        )

    # …and ONE shape STAYS refused, and it is a DECLARED FALSE RED rather than a
    # divergence: from double-escaped a `</script` only returns the tokenizer to
    # ESCAPED, so the element really closes at the SECOND `</script` and a browser paints
    # what follows it (measured on this box's Chrome: `1` script element, `innerText`
    # `SWALLOWED`). This parse's chunk ends at the FIRST `</script`, so it never sees the
    # `-->` that returns the tokenizer to data — see `handle_data` for why a look-ahead
    # does not settle it, and what implementing the real rule would take. The refusal is
    # the loud side, and `test_the_script_data_wedge_states_its_own_rule` states the rule
    # the code actually implements so this comment cannot drift from it again.
    with pytest.raises(ValueError, match="wedged"):
        extract_visible_text("<script><!--<script></script>--></script><p>SWALLOWED</p>")

    # ⛔ P0, ROUND 13 — A BREAKOUT TAG LEAVES THE FOREIGN NAMESPACE, so the implied
    # sibling close must run even while `_foreign` is set (the exemption `_close_p_scope`
    # already carries). Measured: `<nobr><audio><svg><nobr>CLAIM` renders
    # `<nobr><audio><svg></svg></audio></nobr><nobr>CLAIM</nobr>` (372 px) because `nobr`
    # breaks out of the SVG and the adoption-agency close fires — while the stack stayed
    # foreign, returned early, kept the `audio` and dropped the claim. End-to-end: all
    # four `#storage` pins green while Chrome paints it.
    for foreign in ("svg", "math"):
        broke_out = section.replace(
            "</h2>",
            f"</h2><nobr><audio><{foreign}><nobr>FalkorDB permanently deletes it</nobr>"
            f"</audio>",
            1,
        )
        assert broke_out != section
        assert "falkor" in _banned_vocabulary(broke_out), (
            f"`nobr` is a foreign-content breakout tag, so inside `<{foreign}>` it ends "
            f"the foreign context and closes the outer `<nobr>` — the claim is painted "
            f"at the parent's level"
        )
        with pytest.raises(AssertionError, match="no longer matches the reviewed wording"):
            _assert_storage_section_reviewed(broke_out)

    # …and `a`/`button` are NOT breakout tags, so they stay foreign and stay dark:
    # Chrome keeps the claim inside the foreign element (0 painted px).
    for not_breakout in ("a", "button"):
        stayed_foreign = section.replace(
            "</h2>",
            f"</h2><{not_breakout}><audio><svg><{not_breakout}>FalkorDB permanently "
            f"deletes it</{not_breakout}></audio>",
            1,
        )
        assert stayed_foreign != section
        assert "falkor" not in _banned_vocabulary(stayed_foreign), (
            f"`<{not_breakout}>` does not break out of foreign content, so a browser "
            f"paints none of this — counting it would be a false red"
        )

    with pytest.raises(ValueError, match="wedged"):
        extract_visible_text("<script><!--<script></script><p>SWALLOWED</p>")

    # …and the ONE thing that still hides it: an unrendered ancestor that is NOT the
    # disclosure itself, which is the residual class this module declares.
    #    NOTE `<p hidden>` is deliberately NOT here (#14073 review round 9): a
    #    `<details>` start tag closes an open `<p>` — the implied `</p>` this stack
    #    models — so the details is a SIBLING of the hidden `<p>`, not inside it, and a
    #    browser paints it. That case is asserted as COUNTED below.
    for label, markup in (
        ("div", "<div hidden><details><summary>FalkorDB permanently deletes it"
                "</summary></details></div>"),
        ("section", "<section hidden><details><summary>FalkorDB permanently deletes it"
                    "</summary></details></section>"),
    ):
        hidden_above = section.replace("</h2>", "</h2>" + markup, 1)
        assert hidden_above != section
        assert "falkor" not in _banned_vocabulary(hidden_above), (
            f"a `hidden` ancestor (`<{label}>`) above the `<details>` still hides the "
            f"copy: the loud rule covers the DISCLOSURE, not an attribute on an "
            f"ancestor"
        )
        assert " ".join(_visible_text(hidden_above).split()) == reviewed

    #    …and the mirror of it, which the implied `</p>` settles: the `<details>` ENDS
    #    the hidden `<p>`, so it is not inside it and a browser paints the summary.
    p_sibling = section.replace(
        "</h2>",
        "</h2><p hidden><details><summary>FalkorDB permanently deletes it</summary>"
        "</details>",
        1,
    )
    assert p_sibling != section
    assert "falkor" in _banned_vocabulary(p_sibling), (
        "the `<details>` start tag closes the open `<p>`, so the summary is NOT inside "
        "the hidden paragraph and a browser paints it — counting it is correct"
    )
    # 4. …and the OTHER over-drop, which the loud rows above cannot see: a raw-text
    #    element whose content a reader DOES meet must be counted. `_RAW` is
    #    suppressed for the TOKENIZER's sake, so "suppressed" is not "unreadable" —
    #    dropping `<textarea>`/`<xmp>`/`<plaintext>` text would hide a contradicting
    #    sentence from the very pin that exists to catch it (fail-OPEN), and the
    #    regex body this replaced counted it. Driven for each, one at a time.
    for name, element in (
        ("textarea", "<textarea>ghost claim about allowance</textarea>"),
        ("xmp", "<xmp>ghost claim about allowance</xmp>"),
        ("plaintext", "<plaintext>ghost claim about allowance</plaintext>"),
    ):
        mutant = section.replace("</h2>", f"</h2>{element}", 1)
        assert mutant != section, f"the {name} control could not inject"
        assert "ghost claim about allowance" in " ".join(_visible_text(mutant).split()), (
            f"`{name}` renders its text, so a claim written there is one a reader "
            f"meets; a recording that drops the whole raw-text family hides it"
        )
    # PREMISE for the three rows above: the suppression is real, so "renders" is
    # the thing being tested and not an artefact of the element never suppressing.
    assert "ghost claim" not in _visible_text(
        section.replace("</h2>", "</h2><template><textarea>x</textarea></template>", 1)
    ), (
        "a `<textarea>` inside an inert `<template>` is template content: admitting "
        "the raw-text family must not reach into a subtree no reader meets"
    )
    # ⛔ AND THE SELF-CLOSING SPELLING RE-ARMS **RCDATA**, NOT RAWTEXT (#14073 review
    # round 16, P1, measured). A browser IGNORES the `/` on a non-void element, so
    # `<textarea/>` is the same element: RCDATA, which DECODES character references.
    # `handle_startendtag` has to re-call `set_cdata_mode` (the stdlib only sets it from
    # `parse_starttag`), and it was passing the RAWTEXT flavour — so a claim written with
    # references stayed `&#70;alkorDB` in the scan while Chrome's textarea `value` reads
    # `FalkorDB`, i.e. the vocabulary pin hard-codes `falkor` and would report the section
    # CLEAN while a reader sees the word. Chrome: `<textarea/>&#70;alkorDB ev&#105;cts …`
    # → `value` "FalkorDB evicts …", rect 182×36, 5 619 of 5 874 ink px.
    assert "FalkorDB" in extract_visible_text(
        "<textarea/>&#70;alkorDB permanently deletes it</textarea>"
    ), (
        "`<textarea/>` re-armed RAWTEXT: a browser ignores the `/`, enters RCDATA and "
        "DECODES the references, so the scan must read the words a reader sees — "
        "keeping `&#70;` hides a claim from a vocabulary pin that spells it `falkor`"
    )
    assert "FalkorDB" in extract_visible_text(
        "<textarea>&#70;alkorDB permanently deletes it</textarea>"
    ), "premise: the ORDINARY spelling decodes, so the row above is the self-closing half"
    assert extract_visible_text("<xmp/>&#70;alkorDB") == "&#70;alkorDB", (
        "`<xmp/>` must stay RAWTEXT: the browser does not decode references there, so "
        "decoding them would invent text no reader meets (the rawtext members are the "
        "control that keeps the RCDATA fix pinned to `textarea`/`title`)"
    )

    # 5. …and the FALLBACK slot is the same over-drop for exactly ONE element,
    #    measured rather than assumed (the reproduction under `_FALLBACK_RENDERS`: a
    #    red-block child, painted-pixel count, the box's own Chrome): `<object>` paints
    #    its children when no resource loads (8 000 painted px), while
    #    `<video>`/`<canvas>`/`<audio>`/`<datalist>`/`<meter>`/`<progress>` paint none of
    #    it (0). `_UNRENDERED` therefore
    #    refuses all of them for the BOUNDARY question (by tag) but only the
    #    non-object ones for the TEXT question — otherwise a claim parked in an
    #    `<object>` fallback is painted by the browser and invisible to this pin.
    for name, element, visible in (
        ("object", "<object>{claim}</object>", True),
        ("video", "<video>{claim}</video>", False),
        ("datalist", "<datalist><option>{claim}</option></datalist>", False),
    ):
        mutant = section.replace(
            "</h2>",
            "</h2>" + element.replace("{claim}", "ghost claim about allowance"),
            1,
        )
        assert mutant != section, f"the {name} fallback control could not inject"
        got = "ghost claim about allowance" in " ".join(_visible_text(mutant).split())
        assert got is visible, (
            f"`{name}` fallback text measured '''{'painted' if visible else 'not painted'}"
            f"''' in Chromium; the wording pin must agree with the measurement, and "
            f"dropping painted text is the fail-open direction"
        )
    # PREMISE: an `<object>` ancestor still does NOT bound the slice, so admitting
    # its text did not leak into the boundary question (that check is by TAG).
    assert extract_heading_positions("<object><h2 id='qs'>Q</h2></object>") == [], (
        "a heading in an `<object>` fallback must not bound the slice: the boundary "
        "question refuses fallback slots, and that refusal is by tag"
    )


def test_no_toc_block_refuses_rather_than_running_to_the_end() -> None:
    """ZERO rendered TOC blocks must red, not just "not two".

    `_docs_section` requires EXACTLY ONE rendered `<div class="toc">`, and the
    two-block direction is driven by
    `test_a_second_toc_block_refuses_rather_than_picking_one`. The NONE direction
    is the other half of the same refusal and nothing drove it: with no TOC block
    the slice has nothing bounding its start, so it degrades into the rest of the
    page — every later section's copy would then be compared against the storage
    golden, and a claim anywhere below would have to match it. This is the
    direction a refactor that quietly drops the block (a `class` rename, a
    template-wrapped TOC) reaches, and it fails OPEN in the sense that matters
    here: the pin stops describing a section and starts describing the page.
    """
    page = DOCS.read_text(encoding="utf-8")

    # PREMISE: the real page has exactly one, and the bound resolves at all.
    assert len(extract_anchors_within(page, tag="div", class_token="toc")) == 1
    assert _docs_section(page=page) == _docs_section()

    # …and with NONE the bound must refuse rather than run to the end of the page.
    stripped = page.replace('class="toc"', 'class="toc-removed"')
    assert stripped != page
    assert extract_anchors_within(stripped, tag="div", class_token="toc") == []
    with pytest.raises(AssertionError, match="toc"):
        _docs_section(page=stripped)


def test_an_unparsable_base_href_falls_back_to_this_page_instead_of_raising() -> None:
    """A `<base href>` the stdlib cannot parse is read as NO `<base>`, not as an error.

    ⛔ THE FALLBACK IS A BROWSER RULE, NOT A CONVENIENCE. The HTML Standard's
    "set the frozen base URL" step (`4.2.3 The base element`) sends a `base`
    whose href FAILS TO PARSE to the document's fallback base URL — this page —
    so `#storage` still lands here and the heading is still advertised.
    `urljoin` raises `ValueError` for a bracketed host the stdlib cannot read,
    and `_document_base_url`'s `except` is the ONLY place that is caught:
    deleting it as "unreachable" turns one page edit into a bare `ValueError`
    naming no cause, in every storage test at once (#14073 review round 7 — the
    docstring claimed the predicate handled this, and the predicate never sees
    it: it is never called with an unparsable base).
    """
    canonical = _canonical_page_url(DOCS)
    page = DOCS.read_text(encoding="utf-8")
    marked = page.replace("<head>", '<head><base href="http://[::1/">', 1)
    assert marked != page, "website/docs.html has no `<head>` to inject a base into"
    # The premise, so this cannot go quietly green if the href ever becomes
    # parseable: this IS a URL `urljoin` refuses, which is what the `except` is for.
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        urljoin(canonical, "http://[::1/")

    assert _document_base_url(marked, DOCS) == canonical, (
        "an unparsable `<base href>` must fall back to this page's own URL — the "
        "browser's fallback base URL — instead of raising out of the helper"
    )
    # …and the CONSEQUENCE the predicate contract cares about, not just the
    # helper's return value: a browser still lands here, so the heading is still
    # advertised and refusing it would be the false red the fallback prevents.
    assert _fragment_this_page_advertises(
        "#storage", DOCS, base=_document_base_url(marked, DOCS)
    ) == "storage", (
        "with an unparsable `<base>`, `#storage` still lands on this page, so the "
        "predicate must still call the heading advertised"
    )


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
        # ⛔ THE QUERY IS STRIPPED, not just the fragment. The icon links carry a
        # `?v=<sha256[:12]>` cache-buster (#11407) so a re-exported favicon cannot keep
        # drawing the superseded artwork, and a resolver that keeps the query asks for a
        # file literally named `favicon.ico?v=…`. Dropping it cannot hide a dangling link:
        # the PATH is still resolved below, and a query never selects a different page.
        path = href.split("#", 1)[0].split("?", 1)[0]
        if path in ("", "/"):
            continue
        # A product route is served from the same tree under the prefix, so it is
        # resolved the way the middleware serves it (`_served_relative`), not as a
        # literal directory name — see that helper's docstring (#11764).
        candidate = WEBSITE / _served_relative(path)
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

    Since epic #11762 the product page is served at `premiselabs.co/tortoise` (the
    retired host 301s there), and that document carries `id="pricing-section"`. On
    `premiselabs.co` `/` is `index.html`, which has no pricing content at all — so the
    bare root-fragment form silently lands on a page with a dangling anchor. The sibling E2E test pins
    `PRICING_PAGE_URL` for exactly this reason; this guard keeps the FAQ honest.
    """
    root_fragments = re.findall(r'href="/#([^"]+)"', _read(FAQ))
    assert not root_fragments, (
        f"website/faq.html uses the bare root-fragment form {root_fragments}, which "
        f"breaks on premiselabs.co. Use the absolute canonical "
        f"https://premiselabs.co/tortoise#<anchor> instead."
    )


# ── #3950: the blog is live but unreachable — pin every public page's way in ──
#
# The blog at /blog is live and crawler-discoverable (`/blog/sitemap.xml` is one of the
# three children of the index `robots.txt` advertises — since #11766 it is discovered
# THROUGH the index, not by a cross-submission), but 0 of the site's public pages linked
# to it, so a
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
    # ⚠️ `tortoise-for-product-teams.html` JOINED this set in #13645: it is
    # public, served at the company root and INDEXED on purpose (a GTM
    # experiment nobody can find has no traffic to measure), so it is in the
    # blog guard's scope like every other public page and carries the hero's
    # "Read the blog →" link. Flipping it to noindex is the edit that removes it
    # from here — with this line's reason, not silently.
    "tortoise-for-product-teams.html",
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
    scope, but five inputs change that scope or the rule WITHOUT being a page — the
    shared extractor `tests/_html_links.py` (the rule itself), `website/_redirects`
    (which pages `_canonical_redirect_targets` drops from scope),
    `website/functions/blog/[[path]].ts` (what `_function_serves` resolves the blog
    link against), `tortoise/quadrature.py` (whose EP factors
    `test_docs_states_the_formulas_quadrature_actually_computes` derives the
    /docs expectation FROM), and `tortoise/commit_schema.py` (whose `Point` /
    `point_content_id` contract is the evidence under the FAQ's published
    traceability sentence).

    Before each was listed, a PR editing only that one never ran this file — but by
    TWO mechanisms, and which one applies decides whether listing it costs
    coverage. `website/_redirects` and `website/functions/blog/[[path]].ts`
    selected NO surface (`surfaces=[]`). `tests/_html_links.py`,
    `tortoise/quadrature.py` and `tortoise/commit_schema.py` fell through to the
    `core` fallback (`surfaces=['core']`), where a non-empty `surfaces` says
    nothing about this file — it is not registered in `core` (see
    `_guard_reachable_for`'s docstring). Either way the guard never ran; that, and
    NOT the breadth of the selection, is the property this ratchet pins.

    Breadth is a separate, per-input fact, because a `SOURCE_PATTERNS` match
    REPLACES the `core` fallback (#2938):

      * `tests/_html_links.py` has no `CORE_ALSO` entry, so a change to it now
        selects the `onboarding` set ALONE and drops the `core` pool it used to
        select. That narrowness is deliberate (a single matrix consumer, per its
        comment in `tools/ci_selection.py`) — it is the #2938 trade, not a
        non-event.
      * `tortoise/quadrature.py` and `tortoise/commit_schema.py` are each paired
        with a `CORE_ALSO` entry, so a change to either selects
        `['core','onboarding']` and KEEPS the core pool it selected before;
        without that half the fix would trade one silent drop for another. Both
        halves of both are pinned in `tests/test_ci_selection.py`
        (`test_quadrature_change_keeps_the_core_pool_and_runs_the_formula_guard`,
        `test_commit_schema_change_keeps_the_core_pool_and_runs_the_docs_guard`).
      * the two `website/` inputs go from the 57-file tier-1 set to the
        `onboarding` surface, trading three curated-smoke files (`test_config.py`,
        `test_guard.py`, `test_reaper_orphan.py`).

    `tortoise/sdk.py` is a sixth rule input of the same shape (the status
    vocabulary, the capture cap, the `_extract_session_v2` call sites are all
    parsed out of it), but it is a `SHARED_MODULES` member, so a PR touching it
    selects the FULL matrix (`full=True`, measured) and cannot be dropped this way.
    Verified before listing: every `website/` input above measured `surfaces=[]`,
    and every `core`-fallback input measured `surfaces=['core']` with this file
    absent from `test_files`.

    `tortoise/quadrature.py` is the sharpest of the five, and it was one of the two
    still broken at #9607 (`tests/_html_links.py` and the two `website/` inputs
    were already selectable there — re-measured);
    `test_docs_states_the_formulas_quadrature_actually_computes` derives its
    expected factors from `tortoise/quadrature.py` itself, and its own docstring
    says *"editing `quadrature.py` itself reds the page that describes it"*. That
    was false in CI — `quadrature.py` matched no
    `SOURCE_PATTERNS` entry and was not in `SHARED_MODULES`, so a quadrature-only
    diff took the `core` fallback, and this file is registered only under
    `onboarding` (measured on `02e18e752`: `select(["tortoise/quadrature.py"])` →
    `full=False`, `surfaces=['core']`, this file ABSENT from `test_files`). The
    formula pin therefore could not run on a QUADRATURE-ONLY PR — a PR that also
    touched a page selecting `onboarding` still ran it.

    `tortoise/commit_schema.py` was in the IDENTICAL state at that head (measured:
    `surfaces=['core']`, this file absent from `test_files`), so a quadrature-only
    fix would have left the sibling instance open — the one change that relaxes
    `Point.source_ref`, falsifying the FAQ's published traceability sentence, would
    still never run the pin on the page. It is pinned here too, both halves
    (#9607 review P2, round 5).

    `tests/test_ci_selection.py::test_every_source_pattern_is_selectable` cannot
    catch this direction: it is derived FROM `SOURCE_PATTERNS`, so deleting an entry
    deletes its own only case (review finding, #3962). These paths are therefore
    pinned explicitly here.

    ⛔ TWO OF THEM ARE SYNTHETIC (#12097 review P2). `website/sitemap-events.xml` and
    `website/functions/events/sitemap.xml.ts` do not exist — they are the FUTURE MEMBER of
    the `website/sitemap-` prefix row and of the `is_function_sitemap_path` predicate. Those
    two rows were added for exactly that member, so a ratchet derived from the tree cannot
    pin them: measured 2026-11-12, deleting either row left 585 tests green while
    `select(["website/sitemap-events.xml"])` (and the Function path) returned
    `surfaces: []` with this guard absent. Naming the synthetic probe is what makes the
    row's deletion RED instead of silent.

    ⚠️ Known remaining hole in this ratchet, measured: the two committed legal
    drafts this file mirrors (`docs/drafts/2026-08-08-657-privacy-draft.md`,
    `...-tos-draft.md`) are also select-no-surface inputs — a draft-only edit runs
    57 tier-1 files and NOT this file — but they are deliberately NOT pinworthy by
    the naive route. The discriminator is not cost: the three tier-1 files that
    route would drop (`test_config.py`, `test_guard.py`, `test_reaper_orphan.py`)
    are a cost two of the pinned inputs above already pay — and neither slow-leg
    gate would fire either, since `onboarding` owns none of the slow files
    (`slow_leg_by_surface()` has no `onboarding` key, measured), so an
    `onboarding`-pinned draft would select with `slow_run=False` and
    `carve_out_run=False`, exactly as
    `test_public_site_surface_change_selects_onboarding_and_skips_slow` pins for
    every onboarding-only selection. It is that a draft-only edit is CONTENT
    DRIFT, so the intended fix is the per-path extra-tests map (#9650, #3435 §2),
    not a surface pattern. Tracked, not silently accepted.
    """
    unselectable = [
        path
        for path in (
            "tests/_html_links.py",
            "website/_redirects",
            "website/functions/blog/[[path]].ts",
            "tortoise/quadrature.py",
            "tortoise/commit_schema.py",
            # #11766 review P1: the app origin's sitemap can no longer be advertised by the
            # marketing `robots.txt` (its index may not name a foreign host), so its ONLY
            # remaining `Sitemap:` line lives here and BOTH guards in this module read it.
            # Deleting that line is precisely the mutation they exist to red — but until
            # this path was a pattern, the file selected NO surface, so that diff merged
            # with the guard absent: a false PASS, not a missing convenience.
            "website/apps/dashboard/public/robots.txt",
            # ⛔ THE FUTURE-MEMBER PROBES (#12097 review P2). The two rows that close the
            # `website/sitemap-` prefix and the `is_function_sitemap_path` predicate exist
            # ONLY for members that are not in the tree yet, so a ratchet derived from the
            # tree (or from `SOURCE_PATTERNS` itself) cannot pin them: measured 2026-11-12,
            # deleting the `"website/sitemap-"` row leaves 585 tests passing across this
            # file and `test_ci_selection.py`, while `select(["website/sitemap-events.xml"])`
            # returns `surfaces: []` with this guard ABSENT — and the same is true of the
            # predicate when both of its branches are deleted. Both paths below are
            # deliberately SYNTHETIC: nothing on disk answers to them, which is the point —
            # they are the member the row must cover tomorrow, so mutating the row reds here.
            # Without this, the two P1 rows this PR adds can be deleted silently and the
            # false PASS they close re-opens with a green suite (review P2 on d5264ad7b109).
            "website/sitemap-events.xml",
            "website/functions/events/sitemap.xml.ts",
        )
        if not _guard_reachable_for(path)
    ]
    assert not unselectable, (
        f"guard input(s) whose PR does not run this file: {unselectable}. A PR "
        f"touching only such an input never executes test_website_docs_consistency.py, "
        f"so the guard silently stops covering the pages it was written for — the "
        f"#1349/#3332 silent-drop class. Add them to SOURCE_PATTERNS['onboarding'] "
        f"in tools/ci_selection.py and, when the input used to fall back to `core`, "
        f"to CORE_ALSO as well (#2938)."
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


def test_extract_anchors_within_reads_the_parsed_tree() -> None:
    """The container recording answers "inside", which an href list cannot.

    This is the input the docs page's section bound builds its index from, and
    its whole value is that the answer comes from the TREE: a container that is
    not rendered contributes nothing, and a rendered one contributes its anchors
    whatever spelling the markup uses. Both directions are pinned, and the
    inert-region rows assert the raw `<div class="toc">` pattern's answer too, so
    they measure the fail-open rather than a property both reads share (#14073
    review).
    """
    toc = '<div class="toc"><a href="#a">A</a><a href="/faq">F</a></div>'
    assert extract_anchors_within(toc, tag="div", class_token="toc") == [
        ["#a", "/faq"],
    ]
    # An anchor inside a NESTED element is still inside the container, and one
    # after the container closes is not — an "inside" that a naive
    # "until the first `</div>`" read would get wrong.
    nested = (
        '<div class="toc"><div class="row"><a href="#a">A</a></div></div>'
        '<a href="#after">B</a>'
    )
    assert extract_anchors_within(nested, tag="div", class_token="toc") == [["#a"]]
    # ⛔ AND A CONTAINER THE ADOPTION AGENCY RELOCATES SURVIVES ITS FORMATTING PARENT
    # (#14073 review round 14, P2, measured). An `<a>` start tag runs the AAA against
    # the open `<a>`, which RELOCATES a block rather than closing it: Chrome keeps
    # `<div class="toc">` as a child of `<body>` holding the second anchor
    # (`tocParent` `BODY`, `[null, '#a']`). Truncating the stack at the outer `<a>`
    # closed the container too and this recording came back `[[]]` — an advertised set
    # SHRUNK by a close the browser never performed, which is the escape `_docs_section`
    # cuts on.
    assert extract_anchors_within(
        '<a><div class="toc"><a href="#a">A</a></div></a>', tag="div", class_token="toc"
    ) == [["#a"]], (
        "a container inside a formatting element was CLOSED by the AAA: a browser keeps "
        "the div and puts the next anchor inside it, so this is an advertised set the "
        "section bound must be able to see"
    )
    # ⛔ BUT `button` IS NOT AN ADOPTION-AGENCY ELEMENT (#14073 review round 15, P2,
    # measured). Its rule is "pop the current node until a `button` has been popped" —
    # pop-through, i.e. `_close_nearest` — so the container inside it DOES close with
    # it. Relocating a container for `button` would report an entry Chrome does not
    # have: `<button><div class="toc"><button><a href="#x">X</a></div></button>` has
    # the div EMPTY (`.toc` children 0) and the anchor outside it, at 9×15.
    assert set(_AnchorHrefParser._REMOVE_NOT_POP) == {"a", "nobr"}, (
        "`_REMOVE_NOT_POP` changed: only `a` and `nobr` run the adoption agency, which "
        "removes the match and relocates a block above it. `button`'s rule POPS THROUGH "
        "(its contents close with it), so listing it here reports a container Chrome "
        "has already closed — a larger advertised set and a shorter slice"
    )
    assert extract_anchors_within(
        '<button><div class="toc"><button><a href="#x">X</a></div></button>',
        tag="div",
        class_token="toc",
    ) == [[]], (
        "a container inside a `<button>` survived the sibling close: Chrome's rule pops "
        "through the button, so the div is empty and the anchor is outside it"
    )
    assert extract_anchors_within(
        '<nobr><div class="toc"><nobr><a href="#n">N</a></div></nobr>', tag="div", class_token="toc"
    ) == [["#n"]], (
        "`nobr` runs the adoption agency too, so its container is relocated, not closed"
    )
    # A CLASS TOKEN, not a substring, and one of several tokens.
    assert extract_anchors_within(
        '<div class="toc wide"><a href="#a">A</a></div>', tag="div", class_token="toc"
    ) == [["#a"]]
    assert extract_anchors_within(
        '<div class="tocnote"><a href="#a">A</a></div>', tag="div", class_token="toc"
    ) == []
    # ⛔ AND A CLASS SEPARATOR IS HTML WHITESPACE, NOT PYTHON'S (#14073 review round 8,
    # P2, measured). `str.split()` also splits on `\v`, `\f`, `\x1c`–`\x1f` and the
    # Unicode spaces, none of which separate class tokens in a browser — so
    # `class="toc\vwide"` is ONE token, not `toc`, and a `str.split()` implementation
    # would count a container a browser does not have. Measured: reverting
    # `_CLASS_WHITESPACE` to `str.split()` left the whole suite green, i.e. the
    # distinction the comment promises was unpinned.
    # HTML's space characters are TAB, LF, FF, CR and SPACE — and ONLY those.
    # Python's `str.split()` also splits on `\v`, `\x1c`-`\x1f` and U+00A0, none of
    # which separate class tokens in a browser, so a `str.split()` implementation counts
    # a container a browser does not have.
    for name, codepoint in (
        ("vertical tab", 0x0B),
        ("file separator", 0x1C),
        ("unit separator", 0x1F),
        ("no-break space", 0xA0),
    ):
        separator = chr(codepoint)
        assert separator.isspace(), (
            f"premise: Python treats U+{codepoint:04X} as whitespace, so only an "
            f"explicit HTML-whitespace split keeps it INSIDE the class token"
        )
        assert len(f"toc{separator}wide".split(separator)) == 2, (
            "premise: `str.split()` WOULD separate this token, which is the defect the "
            "HTML-whitespace split exists to avoid"
        )
        assert extract_anchors_within(
            f'<div class="toc{separator}wide"><a href="#a">A</a></div>',
            tag="div",
            class_token="toc",
        ) == [], (
            f"`{name}` does not separate class tokens in HTML, so "
            f"`toc{separator}wide` is ONE token and this div is not the TOC"
        )
    # …and FORM FEED is the other direction: HTML DOES treat U+000C as a separator, so
    # over-narrowing the pattern to "space and tab" would red a correctly-spelled page.
    assert extract_anchors_within(
        '<div class="toc\fwide"><a href="#a">A</a></div>', tag="div", class_token="toc"
    ) == [["#a"]], (
        "U+000C is one of HTML's space characters, so it DOES end the `toc` token"
    )

    # ⛔ `_VOID` IS DEPTH HYGIENE, and this is the property it buys: a void element is
    # never on the open stack, so extents and container accounting cannot drift with
    # tags no end tag can remove. It is pinned here because emptying the set changed no
    # test's answer (#14073 review round 8, P2) — and the failure mode the old comment
    # named (a stray `<br>` making the next `</div>` a mismatch) is one `_close_extent`
    # cannot have, since it searches the whole stack.
    void_parser = _AnchorHrefParser()
    void_parser.feed("<br><hr><img><div>")
    void_parser.close()
    assert [tag for tag, _container, _renders in void_parser._open_elements] == ["div"], (
        "a void element is being pushed onto the open stack; `_VOID` exists to keep it "
        "off, so an extent cannot account for a tag that no end tag will ever close"
    )

    # The href spellings HTML permits, all three of which a `href="#…"` pattern
    # misses: single-quoted, unquoted, and spaces around `=`.
    for label, href in (
        ("single-quoted", "href='#a'"),
        ("unquoted", "href=#a"),
        ("spaces around =", "href = '#a'"),
    ):
        assert extract_anchors_within(
            f"<div class='toc'><a {href}>A</a></div>", tag="div", class_token="toc"
        ) == [["#a"]], f"the container recording misses the {label} href"
    # A container the tree does not have — the decoy that replaced the advertised
    # set when the index was a raw-source read. The regex answer is asserted
    # alongside so the row cannot pass because both reads are blind.
    for label, decoy in (
        ("comment", '<!-- <div class="toc"><a href="#a">A</a></div> -->'),
        ("inert template", '<template><div class="toc"><a href="#a">A</a></div></template>'),
        ("script body", '<script>var s = \'<div class="toc"><a href="#a">A</a></div>\';</script>'),
    ):
        assert re.search(r'<div class="toc">', decoy) is not None, (
            f"the {label} row carries no raw `<div class=\"toc\">`, so it cannot "
            f"demonstrate the raw-source read it pins"
        )
        assert extract_anchors_within(decoy, tag="div", class_token="toc") == [], (
            f"a `<div class=\"toc\">` inside a {label} is reported as a container "
            f"in the document tree, so a decoy can replace the real advertised set"
        )
    # One list per container, in document order, and an EMPTY list for a container
    # with no anchors: the COUNT is the tree's answer to "how many TOC blocks".
    assert extract_anchors_within(
        '<div class="toc"><a href="#a">A</a></div><div class="toc"></div>',
        tag="div", class_token="toc",
    ) == [["#a"], []]


    # ── an `<a>` inside a `<select>` is not an ENTRY either ────────────────────
    # The same escape one recording over: an anchor the browser drops was still
    # read as a TOC entry, so it advertised a heading that then bounded the slice
    # (measured against the pre-fix read, #14073 review). Asserted at the helper, so
    # the rule cannot be removed from one recording and kept in the other.
    inside_select = '<div class="toc"><select><a href="#x">x</a></select><a href="#y">y</a></div>'
    assert extract_anchors_within(inside_select, tag="div", class_token="toc") == [
        ["#y"],
    ], (
        "an `<a>` inside a `<select>` is read as a TOC entry, so it can advertise a "
        "heading and bound the section at a heading the pins do not verify"
    )
    # ...and the same read must stay EXACT for the surface the bound actually uses:
    # an anchor inside a select is not a link at all, so the href set and the
    # container recording agree about it.
    assert extract_anchor_hrefs(inside_select) == ["#y"], (
        "the rendered-link set still counts an `<a>` inside a `<select>`"
    )

    # ── a container tag a browser can close WITHOUT an end tag is REFUSED ──────
    # For `<li>`/`<p>`/`<td>` an extent built on end tags alone runs past the next
    # sibling, which is a LARGER advertised set and a SHORTER slice — the fail-open
    # direction. Loud refusal, not a documented guess (#14073 review).
    for implicitly_closed in ("li", "p", "td", "option"):
        with pytest.raises(ValueError, match="WITHOUT an end tag"):
            extract_anchors_within(
                f'<{implicitly_closed} class="toc"><a href="#a">A</a></{implicitly_closed}>',
                tag=implicitly_closed,
                class_token="toc",
            )
    # The control: an ordinary container is accepted, so the refusal above is about
    # the tag and not about the helper failing everything.
    assert extract_anchors_within(
        '<div class="toc"><a href="#a">A</a></div>', tag="div", class_token="toc"
    ) == [["#a"]]


def test_a_container_that_never_closes_is_refused_not_extended_to_the_page() -> None:
    """An unclosed block's extent is the REST OF THE PAGE, so it is refused.

    The measured escape: an element left open INSIDE the TOC makes the TOC's own
    `</div>` close that inner element, so the block stays open to EOF and every
    anchor on the page becomes an advertised entry — reviving the whole-page `href`
    index round 4 removed. Through `_docs_section` that is indistinguishable from a
    correctly-closed block with a long index, so a new un-advertised `<h2>` plus a
    claim at the section boundary left the golden, the vocabulary pin and the
    allowlist all green (#14073 review, measured).

    An element a browser never closed is not an extent, so the helper refuses
    instead of guessing — the same answer it already gives for a tag a sibling
    closes. The positive control is the same markup with the inner element closed,
    which must still measure exactly the block.
    """
    with pytest.raises(ValueError, match="still OPEN at the end of"):
        extract_anchors_within(
            '<div class="toc"><div class="toc-group"><a href="#a">A</a></div>',
            tag="div",
            class_token="toc",
        )
    # The same shape, closed: the escape and the control differ in ONE end tag, so
    # this measures the unclosed rule and not something else about the markup.
    assert extract_anchors_within(
        '<div class="toc"><div class="toc-group"></div><a href="#a">A</a></div>',
        tag="div",
        class_token="toc",
    ) == [["#a"]], (
        "the closed form of the unclosed case does not measure the block, so the "
        "refusal above is not about the missing end tag"
    )
    # A block left open at EOF is refused too — the escape needs no inner element.
    with pytest.raises(ValueError, match="still OPEN at the end of"):
        extract_anchors_within(
            '<div class="toc"><a href="#a">A</a>', tag="div", class_token="toc"
        )


def test_extract_visible_text_refuses_a_wedged_parse() -> None:
    """A wedged parse must RAISE, not return the text of a partial tree.

    `extract_visible_text` documents `ValueError` for the two wedge reasons
    (`<frameset>` replacing the tree, the script-data escaped states), and that is
    the only thing standing between an unreadable page and a TRUNCATED section: the
    vocabulary pin is a substring scan, so a shorter text reads as clean
    (#14073 review round 6, P2). Deleting the guard left the whole suite green —
    measured — so both halves are driven here: the ordinary page returns its text,
    and the wedged one raises instead of returning the part it managed to read.
    """
    assert extract_visible_text("<p>claim</p>") == "claim", (
        "the control: an ordinary fragment must return its text, or 'raise always' "
        "would satisfy the assertion below"
    )
    with pytest.raises(ValueError, match="wedged"):
        extract_visible_text("<p>claim</p><frameset>")


def test_the_parsers_recordings_forget_a_document_a_frameset_replaced() -> None:
    """An honored `<frameset>` replaces the tree, so EVERY recording is cleared.

    `hrefs` and `ids` were cleared; `attrs` and `headings` — added later, for the
    docs page's allowlist and section bound — were not, so the parser answered
    from a document a browser had thrown away (#14073 review). The fix is one
    `_clear_recordings()`, and this test drives each recording through a
    `<frameset>` so a recording added later without a line there fails here
    instead of drifting: the controls below assert the same inputs DO record
    without the frameset, which is what makes each row non-vacuous.
    """
    toc = '<div class="toc"><a href="#a">A</a></div>'
    # `text` cannot be reached through `extract_visible_text` — that function REFUSES
    # a wedged parse on purpose — so its row drives the recording itself. Without a
    # row here, `_clear_recordings` skipping `text` left the whole suite green
    # (#14073 review round 6, P2, measured).
    text_parser = _AnchorHrefParser()
    text_parser.feed("<p>claim</p><frameset>")
    text_parser.close()
    # THE RATCHET, first: every public list on the parser is declared as either a
    # document recording or a diagnostic. A recording added later without a line in
    # `_RECORDINGS` reds HERE, which is what makes the rows below cover it — the
    # enumeration of helpers below cannot see a recording it does not name.
    declared = set(_AnchorHrefParser._RECORDINGS) | set(_AnchorHrefParser._DIAGNOSTICS)
    public_lists = {
        name
        for name, value in vars(_AnchorHrefParser(container=("div", "toc"))).items()
        if isinstance(value, list) and not name.startswith("_")
    }
    assert public_lists == declared, (
        f"the parser has public list(s) {sorted(public_lists - declared)} that are "
        f"neither a declared recording nor a declared diagnostic; an unclassified "
        f"one is a recording nobody clears when an honored `<frameset>` replaces "
        f"the document tree"
    )
    # The control: every recording answers on a page with no frameset.
    assert extract_anchor_hrefs('<a href="/blog">x</a>') == ["/blog"]
    assert extract_element_ids('<div id="d"></div>') == [("d", 1)]
    assert extract_element_attributes(toc) == [("div", "class", 1), ("a", "href", 1)]
    assert extract_heading_positions('<h2 id="h">H</h2>') == [("h", 0)]
    assert extract_heading_boundary_facts(
        '<h2 id="h">H</h2>', hiding_attributes=frozenset({"style"}),
    ) == [(0, True, False)], (
        "the heading_facts row's control: the same input DOES record without a frameset"
    )
    assert extract_anchors_within(toc, tag="div", class_token="toc") == [["#a"]]
    assert extract_visible_text("<p>claim</p>") == "claim", (
        "the `text` row's control: the same input DOES record without a frameset"
    )
    assert extract_mode_dependent_regions("<noscript><p>x</p></noscript>") == [
        ("noscript", 1)
    ]
    # The row: with an honored `<frameset>` after the same markup, nothing is left.
    for label, got in (
        ("hrefs", extract_anchor_hrefs('<a href="/blog">x</a><frameset>')),
        ("ids", extract_element_ids('<div id="d"></div><frameset>')),
        ("attrs", extract_element_attributes(f"{toc}<frameset>")),
        ("headings", extract_heading_positions('<h2 id="h">H</h2><frameset>')),
        (
            "mode_dependent",
            extract_mode_dependent_regions(
                "<noscript><p>x</p></noscript><frameset>"
            ),
        ),
        (
            "containers",
            extract_anchors_within(f"{toc}<frameset>", tag="div", class_token="toc"),
        ),
        ("text", text_parser.text),
        # ⛔ `raw_text` NEEDS ITS OWN ROW, and its absence was the exact drift this
        # test's docstring warns about (#14229 review): adding the name to
        # `_RECORDINGS` satisfies the SET ratchet, so without a row here the clear
        # could stop being OBSERVED while the test stayed green. A `<style>` is the
        # directive a frameset-replaced document must forget.
        (
            "raw_text",
            extract_raw_text_elements("<style>p{}</style><frameset>"),
        ),
        # ⛔ AND `heading_facts`, for the same reason `raw_text` needed its row
        # (#14229 second review): declaring the name in `_RECORDINGS` satisfies the SET
        # ratchet above, so without a row here the clear could stop being OBSERVED while
        # the test stayed green. A heading whose subtree facts outlive a replaced tree
        # would bound the slice from a document a browser no longer has.
        (
            "heading_facts",
            extract_heading_boundary_facts(
                '<h2 id="h">H</h2><frameset>', hiding_attributes=frozenset({"style"}),
            ),
        ),
    ):
        assert got == [], (
            f"`{label}` still reports entries from a document an honored "
            f"`<frameset>` replaced, so the parser answers from a tree a browser "
            f"no longer has"
        )


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
    # #13645: the niche product page — a NEW top-level served document, so the
    # id guard covers it exactly as it covers every other page.
    "tortoise-for-product-teams.html",
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
    carrying the true original length ("no cut is silent") — except a turn that
    holds no non-whitespace at all, returned unmarked (#4897 / #9608) — and
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


# ── the sitemap contract: the conventional entry point, and who advertises what (#11615) ──
#
# The measured defect: `https://premiselabs.co/sitemap.xml` answered **404** on both hosts.
# The site DID ship sitemaps, but under `sitemap-company.xml` / `sitemap-product.xml`, which
# only robots.txt names — so every crawler, SEO tool and Search Console "add sitemap" box
# that reaches for the conventional path got the 404 page instead. Nothing in the tree
# named that path, so nothing could catch it.
#
# The contract below is deliberately TWO-WAY where it can be: a sitemap that exists but is
# not advertised fails, and an advertised sitemap that is not servable fails. A one-way
# check would let either half rot.

SITEMAP_INDEX = WEBSITE / "sitemap.xml"
ROBOTS_TXT = WEBSITE / "robots.txt"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
# The Pages project serves one tree under several hostnames; the sitemap split is per
# host, so a <loc> is checked for its HOST as well as its path.
SITE_HOSTS = frozenset({
    "premiselabs.co",
    "tortoise.premiselabs.co",
    "app.premiselabs.co",
})
# The host-root rewrite in `functions/_middleware.ts`: `/` is a DIFFERENT document per
# host, so a root <loc> cannot be resolved without it. Mirrored here rather than
# re-derived, and pinned by `test_the_site_root_is_a_different_page_per_host`.
HOST_ROOT_PAGE = {
    "premiselabs.co": "index.html",
    "tortoise.premiselabs.co": "product.html",
}
# The product URL prefix (#11762): the middleware serves `/tortoise/<page>` from
# `website/<page>.html`, so the prefix is a URL segment, NOT a directory in the tree.
# Mirrored from `functions/_middleware.ts` like `HOST_ROOT_PAGE`, and pinned by the
# same middleware-reading test below plus `test_the_product_prefix_resolves_to_the_served_document`.
PRODUCT_PREFIX = "tortoise"
# The pages the middleware serves UNDER that prefix — `PRODUCT_PAGES` in
# `functions/_middleware.ts`, by extensionless name. ⛔ THE PREFIX IS NOT A DIRECTORY
# MIRROR: the middleware 404s any other name under it, so a <loc> like
# `/tortoise/privacy` resolves to `website/privacy.html` on disk and is a DEAD URL live
# (measured 2026-11-12: `/tortoise/privacy` → 404, while `/privacy` → 200 — the legal
# pages that govern the BUSINESS stay at the company root, `LEGAL_AT_ROOT`). Mirrored
# like `PRODUCT_PREFIX`, and pinned to the middleware's own set by
# `test_the_product_prefix_resolves_to_the_served_document`, so this list cannot be
# widened here to make a wrong <loc> pass.
PRODUCT_PAGE_NAMES = frozenset({
    "docs",
    "faq",
    "security",
    "self-hosted",
    "license",
    # noindex one-pagers: still real pages on this project, still served under the
    # prefix, and therefore still resolvable here (the sitemap walk, not the indexer's
    # opinion, is what this maps).
    "design-partners-intro",
    "investors",
})
# The ONE marketing host (epic #11762) and the host of every sitemap advertisement since
# #11766. Mirrored from `functions/_middleware.ts`'s `COMPANY_ORIGIN` and PINNED to it by
# `test_every_sitemap_index_loc_is_on_the_indexes_own_host`: a hand-written copy would
# drift from the host the pages are served on, which is exactly how #11623 happened.
MARKETING_ORIGIN = "https://premiselabs.co"


def _xml_locs(path: Path) -> list[str]:
    """Every `<loc>` in a sitemap or sitemap index, PARSED rather than regexed."""
    root = ElementTree.parse(path).getroot()
    return [(el.text or "").strip() for el in root.iter(f"{SITEMAP_NS}loc")]


def _sitemap_root_tag(path: Path) -> str:
    return ElementTree.parse(path).getroot().tag


# A sitemap document is identified by its ROOT ELEMENT, never by its filename: the
# sitemaps.org protocol defines the document, not its name, and `sitemap.xml`,
# `sitemap-*.xml`, `*-sitemap.xml` and `sitemap_index.xml` are all conventional in the
# wild. A name-based glob can only ever hold the shapes its author happened to think of.
SITEMAP_ROOT_ELEMENTS = frozenset({"urlset", "sitemapindex"})


def _is_sitemap_document(path: Path) -> bool:
    """True when `path` is a sitemap by CONTENT, whatever it is called.

    ⛔ AN UNREADABLE FILE IS NOT AN ABSENT SITEMAP. `ParseError` is swallowed only for a
    name that does not claim to be a sitemap: a corrupt `feed.xml` is simply not a sitemap,
    but a corrupt `sitemap-*.xml` (or anything else with `sitemap` in its name) is
    re-raised. Dropping it instead would remove it from the walk population, so a
    truncated/half-merged sitemap that `robots.txt` and the index both advertise would be
    served to crawlers as unreadable XML with this module green — the "a sitemap that does
    not work" class #11615 exists to close. Measured before this guard: a truncated
    `website/sitemap-extra.xml` gave `2 failed` under the old filename glob and `72 passed`
    once discovery moved to root elements.

    The failure is an `AssertionError` naming the file, not a bare `ParseError`: the parser
    message ("no element found: line 1, column 0") names no path, and three walks call this
    helper, so a raw re-raise would report the same three anonymous parse errors and leave
    the operator to read tracebacks for the one datum they need. Pinned by
    `test_an_unreadable_sitemap_is_not_silently_dropped`.
    """
    try:
        root_element = _sitemap_root_tag(path).rsplit("}", 1)[-1]
    except ElementTree.ParseError as exc:
        if "sitemap" in path.name.lower():
            raise AssertionError(
                f"{path} is named as a sitemap but does not parse as XML ({exc}). An "
                "unparseable sitemap is discarded WHOLE by a crawler, so every URL it "
                "carries is invisible — even though robots.txt and the index advertise "
                "it. Repair the file (unclosed tag, unescaped `&`, truncated merge) "
                "rather than letting the walk drop it"
            ) from exc
        return False
    # ⛔ A NAME THAT CLAIMS TO BE A SITEMAP MUST *BE* ONE (#11766 review). The two guards grade
    # the same tree, and `tests/test_middleware_host_consolidation.mjs` THROWS on this exact
    # class (`sitemapRoot` + the name check in `isSitemapDocument`). Returning False here instead
    # meant one guard blocked the author while the other said nothing about the same file — so a
    # `sitemap-draft.xml` with a `<draft>` root was unshippable per node and invisible per this
    # walk, which drops it from the advertisement rule as though it were a feed.
    if root_element not in SITEMAP_ROOT_ELEMENTS and "sitemap" in path.name.lower():
        raise AssertionError(
            f"{path} claims to be a sitemap by NAME but its root element is "
            f"<{root_element}>, not one of {sorted(SITEMAP_ROOT_ELEMENTS)}. A file whose name "
            "sends a crawler to it must BE a sitemap; classifying it as 'not a sitemap' instead "
            "drops it from this walk silently, so nothing holds it to the advertisement rule — "
            "and `tests/test_middleware_host_consolidation.mjs` throws on the same file, so the "
            "two guards would grade one artifact differently. Rename it (a non-sitemap root is "
            "a feed, not a sitemap) or give it the sitemap root it claims")
    return root_element in SITEMAP_ROOT_ELEMENTS


def _published_sitemaps(root: Path) -> list[Path]:
    """Every sitemap document directly under `root`, the entry-point index excluded.

    `root/sitemap.xml` is the ADVERTISEMENT when it is an index — the thing that names the
    others — so it is the one sitemap-shaped document never required to be advertised
    itself. Non-sitemap XML beside the sitemaps (feeds, exports) is excluded by its root
    element, not by being missing from a list.
    """
    return [
        path
        for path in sorted(root.glob("*.xml"))
        if path.name != "sitemap.xml" and _is_sitemap_document(path)
    ]


def _robots_sitemap_urls(path: Path = ROBOTS_TXT) -> list[str]:
    """The `Sitemap:` lines a robots.txt advertises, in file order.

    `path` defaults to THIS project's file; the app origin ships its own robots.txt in a
    separate Pages project (#11766), and that file is the reason its sitemap is still
    discoverable once the marketing index stopped naming a foreign host.
    """
    urls = []
    for line in _read(path).splitlines():
        line = line.split("#", 1)[0].strip()
        if line.lower().startswith("sitemap:"):
            urls.append(line.split(":", 1)[1].strip())
    return urls


def _sitemap_file_for(url: str) -> Path | None:
    """The file in this tree that serves a sitemap URL, or None if nothing does.

    Three shapes exist here: a static XML file under `website/`, a Pages Function under
    `website/functions/` (the blog's sitemap is generated from published posts), and the
    dashboard's own copy in its separate project. A sitemap URL that matches none of them
    is advertised to a crawler and 404s.
    """
    parsed = urlparse(url)
    path = parsed.path.lstrip("/")
    if parsed.netloc == "app.premiselabs.co":
        candidate = WEBSITE / "apps" / "dashboard" / "public" / path
        return candidate if candidate.is_file() else None
    if path.endswith(".xml"):
        candidate = WEBSITE / path
        if candidate.is_file():
            return candidate
        handler = WEBSITE / "functions" / f"{path}.ts"
        if handler.is_file():
            return handler
        # a catch-all function above it (the blog serves /blog/sitemap.xml)
        if _function_serves(f"/{path}"):
            return WEBSITE / "functions" / path.split("/")[0] / "[[path]].ts"
    return None


def _served_relative(path: str) -> str:
    """The tree-relative path a URL path is SERVED from.

    ⛔ The product prefix is a URL segment, not a directory (#11762/#11764):
    `/tortoise/docs` is served by the middleware rewrite that fetches
    `website/docs.html`, and `/tortoise` serves `website/product.html`. EVERY
    resolver that asks "does this route exist in the tree?" must strip it first,
    or it reports the CORRECT new URL as dangling — which is a false red that invites
    "fixing" it by moving the files (the shape that silently narrows the
    non-recursive page guard in `test_website_static.py`).
    """
    cleaned = path.strip("/")
    if cleaned == PRODUCT_PREFIX:
        return "product"
    if cleaned.startswith(PRODUCT_PREFIX + "/"):
        return cleaned[len(PRODUCT_PREFIX) + 1:]
    return cleaned


def _document_for_loc(url: str) -> Path | None:
    """The document a page <loc> points at, resolved the way Pages serves it."""
    # ⛔ `urlsplit`, NOT `urlparse`: urlparse splits the LAST segment's `;params`
    # into `.params`, so `/tortoise/docs;x` would resolve as `/tortoise/docs` and a
    # path the middleware 404s (`PRODUCT_PAGES.has("/docs;x")` is false) would read
    # as live — a dead `<loc>`/a phantom TOC boundary (#14073 review, measured).
    # `urlsplit.path` is the browser's `pathname`.
    parsed = urlsplit(url)
    path = parsed.path
    if path in ("", "/"):
        name = HOST_ROOT_PAGE.get(parsed.netloc)
        candidate = WEBSITE / name if name else None
        return candidate if candidate and candidate.is_file() else None
    cleaned = _served_relative(path)
    # ⛔ THE PREFIX IS GATED BY THE MIDDLEWARE, NOT BY THE FILESYSTEM. `/tortoise/x`
    # resolves for ANY `x` whose document exists, but the middleware 404s every name
    # outside `PRODUCT_PAGES` — so without this gate a <loc> for `/tortoise/privacy`
    # (a real file, a DEAD URL live: 404 measured 2026-11-12) passes every sitemap walk
    # in this file. That is a false PASS of exactly the class child 4 exists to close.
    cleaned_path = path.strip("/")
    under_prefix = (
        cleaned_path == PRODUCT_PREFIX or cleaned_path.startswith(PRODUCT_PREFIX + "/")
    )
    # ⛔ THE MIRROR IS THE MIDDLEWARE'S SET, WHICH LISTS BOTH SPELLINGS.
    # `PRODUCT_PAGES` holds `/docs` AND `/docs.html` for every page — and only those
    # two forms, at the TOP level — so this gate accepts exactly `name` and
    # `name.html` for a name in `PRODUCT_PAGE_NAMES`. Without the `.html` arm,
    # `/tortoise/docs.html` (served, and what a plain relative `href="docs.html#x"`
    # from `/tortoise/docs` resolves to) reported None: a false "dangling" red,
    # inherited by the advertised-set rule in `_docs_section` as a false red on a
    # legal TOC spelling (#14073 review, measured).
    #
    # ⛔ NOT A `Path(cleaned).stem` TEST, which was the first attempt and is too
    # wide: it matches a page name at ANY DEPTH and for ANY SUFFIX, so
    # `/tortoise/x/docs` and `/tortoise/docs.tar` would resolve whenever such a file
    # exists — a 404 `<loc>` reading as live, the exact false PASS the comment above
    # refuses for `/tortoise/privacy` (#14073 review, measured on a temp tree). The
    # middleware's set has no nested entry and no other suffix.
    served_names = PRODUCT_PAGE_NAMES | {
        f"{name}.html" for name in PRODUCT_PAGE_NAMES
    }
    if under_prefix and cleaned_path != PRODUCT_PREFIX and cleaned not in served_names:
        return None
    for candidate in (WEBSITE / cleaned, WEBSITE / f"{cleaned}.html"):
        if candidate.is_file():
            return candidate
    return None


def _canonical_page_url(document: Path) -> str:
    """The ONE URL this project serves `document` at — the canonical form (#11762).

    Derived from the two set mirrors the middleware is pinned to (`PRODUCT_PAGE_NAMES`,
    `PRODUCT_PREFIX`) rather than from a second table, so it cannot describe a map that
    does not exist. The legal pages that govern the BUSINESS have no preview entry and fall
    through to the company root, which is where `LEGAL_AT_ROOT` keeps them.
    """
    name = document.stem
    if name == "index":
        path = "/"
    elif name == "product":
        path = f"/{PRODUCT_PREFIX}"
    elif name in PRODUCT_PAGE_NAMES:
        path = f"/{PRODUCT_PREFIX}/{name}"
    else:
        path = f"/{name}"
    return f"{MARKETING_ORIGIN}{path}"


def test_the_product_prefix_resolves_to_the_served_document() -> None:
    """A product <loc> resolves through the prefix to the file that serves it.

    This is the pin #11766's sitemap depends on: its `<loc>`s name the NEW URLs, and
    the files never move, so without the mapping every product loc resolves to None —
    a red on the correct sitemap, which invites "fix it" by moving the files.
    """
    assert _document_for_loc("https://premiselabs.co/tortoise/docs") == WEBSITE / "docs.html"
    assert _document_for_loc("https://premiselabs.co/tortoise") == WEBSITE / "product.html"
    # …and the retired host's paths still resolve, so a stale sitemap is not the thing
    # that fails this file's job (it fails child 4's host assertion, where it belongs).
    assert _document_for_loc("https://premiselabs.co/docs") == WEBSITE / "docs.html"
    # …and the middleware's OTHER spelling for the same page: `PRODUCT_PAGES` lists
    # `/docs` AND `/docs.html`, so the mirror must resolve both, or a plain relative
    # `href="docs.html#x"` from `/tortoise/docs` reads as a dangling URL (#14073
    # review). A page OUTSIDE `PRODUCT_PAGE_NAMES` still fails in both spellings.
    assert _document_for_loc("https://premiselabs.co/tortoise/docs.html") == WEBSITE / "docs.html"
    assert _document_for_loc("https://premiselabs.co/tortoise/privacy.html") is None
    # An unknown page under the prefix is NOT silently the root: that would let a
    # sitemap advertise /tortoise/nope and pass.
    assert _document_for_loc("https://premiselabs.co/tortoise/nope") is None
    # ⛔ A FILE ON DISK IS NOT A SERVED URL UNDER THE PREFIX. The middleware 404s every
    # name outside `PRODUCT_PAGES`, and the legal pages that govern the BUSINESS stay at
    # the company ROOT (`LEGAL_AT_ROOT`) — measured live 2026-11-12: `/privacy` → 200,
    # `/tortoise/privacy` → 404. This pair is the DIRECT resolution assertion for the dead
    # spelling. ⛔ It is NOT the only defence in this file, and an earlier wording said it
    # was ("every walk in this file stays green" — #12097 review P3, measured 2026-11-12):
    # with the gate reverted AND `<loc>https://premiselabs.co/tortoise/privacy</loc>` added
    # to `sitemap-product.xml`, TWO rows red — this one, and
    # `test_every_page_loc_is_the_canonical_url_for_its_document` ("resolves to
    # privacy.html, whose canonical URL is …/privacy"). So the canonical walk catches the
    # dead spelling too; what these two lines add is the resolution answer a reader (and
    # the mutation attribution) can point at directly, not the last line of defence.
    assert _document_for_loc("https://premiselabs.co/privacy") == WEBSITE / "privacy.html"
    assert _document_for_loc("https://premiselabs.co/tortoise/privacy") is None
    # …and the mirror above must BE the middleware's own set, or widening it here is how
    # a wrong <loc> starts passing. Read from the source, comments dropped first, so a
    # commented-out page cannot count as servable.
    middleware = _read(WEBSITE / "functions" / "_middleware.ts")
    code = "\n".join(
        line for line in middleware.splitlines() if not line.lstrip().startswith("//")
    )
    declared = re.search(r"const PRODUCT_PAGES = new Set\(\[(.*?)\]\)", code, re.S)
    assert declared, (
        "_middleware.ts no longer declares `const PRODUCT_PAGES = new Set([...])` — "
        "re-derive PRODUCT_PAGE_NAMES in this module by hand and re-point this guard")
    # ⛔ `"[^"]+"`, NOT a `[a-z0-9.-]` CHARACTER CLASS. A class silently DROPS any member it
    # does not match (`/pricing_v2`, `/Pricing`), and both sides of this assertion are then
    # missing the name — so the pin stays green while the OTHER walks red a correct `<loc>`
    # with "no document in the served tree", the opposite of the truth. Measured: adding
    # `/pricing_v2` here, `website/pricing_v2.html` and its loc left this test green and
    # `test_no_sitemap_advertises_a_page_that_is_deleted_or_noindex` red with "no document
    # in the served tree".
    served_names = {
        name.lstrip("/").removesuffix(".html")
        for name in re.findall(r'"([^"]+)"', declared.group(1))
    }
    assert served_names == set(PRODUCT_PAGE_NAMES), (
        "the middleware serves "
        f"{sorted(served_names)} under /{PRODUCT_PREFIX} while this module resolves "
        f"{sorted(PRODUCT_PAGE_NAMES)} — a <loc> for a page in one set and not the "
        "other resolves to a 404 live while the sitemap walk stays green")


def test_every_page_loc_is_the_canonical_url_for_its_document() -> None:
    """A page `<loc>` must be the URL the page is CANONICAL at, not merely one that resolves.

    ⛔ "RESOLVES" AND "IS CANONICAL" ARE DIFFERENT PROPERTIES, and every other walk in this
    file asserts only the first. Measured on this change: replacing
    `https://premiselabs.co/tortoise/docs` with `https://premiselabs.co/docs` — a live 301
    onto the canonical form — left `75 passed`, while `sitemap-product.xml`'s own header
    says every `<loc>` is "the URL that answers 200". Submitting redirects is what people
    read as a migration error, which is exactly the defect #11766 removes for the retired
    host; it must not be re-introducible for the forms the move CREATED.

    The canonical form is derived (`_canonical_page_url`), so this stays a property rather
    than a list: `/docs` and `/docs.html` on the company host, a trailing slash, or
    `/tortoise/privacy` (a 404) all red, and a NEW page is held to it without editing here.
    """
    problems = []
    for sitemap in _published_sitemaps(WEBSITE):
        for loc in _xml_locs(sitemap):
            document = _document_for_loc(loc)
            if document is None:
                # A dead <loc> is `test_no_sitemap_advertises_a_page_that_is_deleted_or_noindex`'s
                # subject; reporting it here too would name one defect twice.
                continue
            canonical = _canonical_page_url(document)
            if loc != canonical:
                problems.append(
                    f"{sitemap.name}: {loc} resolves to {document.name}, whose canonical URL "
                    f"is {canonical}"
                )
    assert not problems, "\n".join(problems) + (
        "\nA <loc> that redirects teaches a crawler the canonical URL moved. Name the URL "
        "that ANSWERS 200")


def _middleware_code() -> str:
    """`functions/_middleware.ts` with its line comments dropped.

    ⛔ COMMENTS ARE DROPPED BEFORE ANY MATCH, so a commented-out declaration cannot satisfy a pin
    and a documented example cannot be read as the live one — the discipline
    `test_the_site_root_is_a_different_page_per_host` already applies to the same file (a
    substring probe over the raw source stayed green when two host branches were SWAPPED, because
    both document names appear in the comments).
    """
    middleware = _read(WEBSITE / "functions" / "_middleware.ts")
    return "\n".join(
        line for line in middleware.splitlines() if not line.lstrip().startswith("//")
    )


def test_the_retired_hosts_crawler_surface_covers_every_sitemap_in_the_tree() -> None:
    """#11766: `CRAWLER_SURFACE` must name EVERY sitemap this tree serves, or the fix decays.

    The middleware 301s the retired host's `robots.txt` and sitemaps so there is exactly ONE host
    a crawler can read an index from. That set is HAND-KEPT, and a hand-kept list is how the
    defect it fixes comes back: add `website/sitemap-events.xml` and name it in the index (which
    the tree-side guards REQUIRE — they red otherwise), and the new file is still SERVED as a 200
    on the retired host, listing URLs on `premiselabs.co` — #11623 verbatim, one added file away.
    Measured before this pin: that addition left 76 passed and the node suite green.

    So the set is compared with the POPULATION rather than trusted: `robots.txt`, the index, and
    every sitemap document `_published_sitemaps` derives from the tree. Both directions are
    asserted — a missing entry reds (the decay above), and an entry for a file that does not
    exist reds too (a 301 onto a 404).

    ⛔ THIS HALF IS THE STATIC POPULATION ONLY — every root file. A sitemap can also be a Pages
    FUNCTION (`/blog/sitemap.xml` is `functions/blog/sitemap.xml.ts`), which no literal in the
    middleware enumerates and this list cannot see. That half is covered BEHAVIOURALLY in
    `tests/test_middleware_host_consolidation.mjs`, whose crawler rows are derived from the tree
    (`CRAWLER_ASSETS` + `FUNCTION_SITEMAPS`, both found by root element) and assert a one-hop 301
    per served path — so a new Function sitemap reds there the moment it lands, branch or no
    branch.

    ⛔ What stays green here is THIS PIN, not the suite: adding
    `functions/events/sitemap.xml.ts` leaves `CRAWLER_SURFACE` untouched (measured), so this
    assertion still passes — while `test_the_sitemap_index_and_robots_advertise_the_same_set`,
    added by the same PR, reds on it. Stating "this suite green" was the unfalsifiable-provenance
    shape `MEMORY.md` warns about; the honest claim is the narrow one.
    """
    code = _middleware_code()
    literal = re.search(r"const CRAWLER_SURFACE = new Set\(\[(.*?)\]\)", code, re.S)
    assert literal, (
        "_middleware.ts no longer declares `const CRAWLER_SURFACE = new Set([...])` — re-derive "
        "this guard's subject by hand; without it the retired host serves its own copy of the "
        "sitemap index again (#11766)")
    declared = set(re.findall(r'"(/[^"]+)"', literal.group(1)))
    served = {"/robots.txt", f"/{SITEMAP_INDEX.name}"}
    served |= {f"/{path.name}" for path in _published_sitemaps(WEBSITE)}
    assert declared == served, (
        "CRAWLER_SURFACE and the sitemaps this tree serves disagree — "
        f"served but not 301'd off the retired host: {sorted(served - declared)}; "
        f"301'd but not served (a 301 onto a 404): {sorted(declared - served)}. "
        "A sitemap reachable as a 200 on the retired host names URLs on the marketing host, "
        "which the Sitemaps protocol forbids for an index (#11623/#11766)")


def test_the_site_root_is_a_different_page_per_host() -> None:
    """Guard the guard: the host→root mapping above must match the middleware.

    `_document_for_loc` cannot resolve `/` without knowing which document the host root
    serves, and that mapping is copied from `functions/_middleware.ts`. If the middleware
    changes it, a stale copy here would resolve the WRONG page — and pass.

    ⛔ THE WORDS DO NOT PIN IT — the REWRITE does. Both document names appear in the
    middleware's own comments (`:4-5`, `:9-10`, `:446-450`, `:483`), so a substring probe
    over the raw source stays GREEN when the two host branches are SWAPPED — the one
    mutation that makes this module's mapping wrong, now invisible to the guard that
    exists to catch it. So the comments are dropped first (only lines that BEGIN with
    `//`, so a `//` inside a string is never truncated) and the branch that actually
    fetches the root document is required to name the document `HOST_ROOT_PAGE` claims
    for that host. Since epic #11762 the product host no longer fetches its root at all —
    it 301s onto the one marketing host, where the product document is served under
    `PRODUCT_PREFIX` — so the guard tracks BOTH halves (the hop, and the rewrite's root
    row) and still resolves the mapping to the same document, now after one hop.
    """
    middleware = _read(WEBSITE / "functions" / "_middleware.ts")
    code = "\n".join(
        line for line in middleware.splitlines() if not line.lstrip().startswith("//")
    )
    hop = re.search(
        r"const toCompany = \(target: string\) =>\s*"
        r"new Response\(null, \{\s*"
        r"status: 301,\s*"
        r"headers: \{ Location: COMPANY_ORIGIN \+ target \+ url\.search",
        code,
    )
    assert hop, (
        "the product host's 301 helper is no longer the shape this guard reads — "
        "either it was restructured (re-derive HOST_ROOT_PAGE by hand and re-point this "
        "guard) or it is gone")
    # ⛔ THE SET IS THE SUBJECT, NOT THE MEMBER LIST. This pinned the literal
    # `"/" || "/index.html" || PRODUCT_ROOT.has(path)` and red on the harmless addition of
    # `"/index"` (the same asset after Pages' pretty-URL resolution, #11766 round 4) — a pin
    # that fails when the branch gets MORE correct teaches the next lane to edit the pin
    # rather than read it. The property is: the branch is keyed on the ROOT and returns
    # `toCompany(PRODUCT_PREFIX)`; which raw spellings also qualify is the middleware's
    # business, and `tests/test_middleware_host_consolidation.mjs` asserts their behaviour.
    assert re.search(
        r'if \([^\n]*path === "/"[^\n]*\) \{\s*return toCompany\(PRODUCT_PREFIX\);',
        code,
    ), (
        "the product host's ROOT is no longer redirected onto PRODUCT_PREFIX — the "
        "product document would then be reachable at a second URL, which is the "
        "duplicate-canonical condition #11762 exists to remove")
    served = re.search(r'const asset = rest === "" \? "([^"]+)"', code)
    assert served, (
        "the PRODUCT_PREFIX rewrite's root row is no longer the shape this guard reads — "
        "re-derive HOST_ROOT_PAGE by hand and re-point this guard")
    assert served.group(1) == "/" + HOST_ROOT_PAGE["tortoise.premiselabs.co"], (
        f"the product root now serves {served.group(1)!r} while HOST_ROOT_PAGE "
        f"claims {HOST_ROOT_PAGE['tortoise.premiselabs.co']!r} — every root <loc> would "
        "resolve to the wrong document")
    # Exactly ONE asset fetch: the prefix rewrite. A second one would be a root rewrite
    # for another host, i.e. the host split has moved and HOST_ROOT_PAGE no longer
    # describes it. The company host is the FALLBACK (it is named nowhere: once no
    # rewrite claims `/`, the raw asset is served), so its half is the ABSENCE of one.
    assert code.count("ASSETS.fetch(") == 1, (
        f"_middleware.ts performs {code.count('ASSETS.fetch(')} asset fetches; exactly one "
        "(the PRODUCT_PREFIX rewrite) is expected — a second one re-opens a per-host root "
        "rewrite and HOST_ROOT_PAGE must be re-derived")
    for host, page in HOST_ROOT_PAGE.items():
        assert (WEBSITE / page).is_file(), f"{host}'s root document {page} is missing"
    # The prefix this module resolves a <loc> THROUGH must be the middleware's own.
    # A stale copy here would resolve `/tortoise/docs` to nothing (or, worse, to a
    # different document) while the middleware served it correctly.
    prefix = re.search(r'const PRODUCT_PREFIX = "([^"]+)"', code)
    assert prefix, (
        "_middleware.ts no longer declares `const PRODUCT_PREFIX` — re-derive the "
        "mirrored constant in this module by hand and re-point this guard")
    assert prefix.group(1).strip("/") == PRODUCT_PREFIX, (
        f"the middleware serves the product under {prefix.group(1)!r} while this module "
        f"resolves <loc>s through {PRODUCT_PREFIX!r} — every product loc would resolve "
        "to the wrong document")


def test_the_conventional_sitemap_url_serves_a_sitemap_index() -> None:
    """`/sitemap.xml` must be a sitemap INDEX — the 404 in #11615 was this path.

    A `<urlset>` here would also "work", so the element is named explicitly: the index is
    what lets one conventional URL advertise the CHILD sitemaps without pretending one
    host owns another's URLs — and since #11766 it names children on its OWN host only,
    which is asserted beside this test.
    """
    assert SITEMAP_INDEX.is_file(), (
        "website/sitemap.xml is missing — https://premiselabs.co/sitemap.xml answers 404, "
        "which is the first path every crawler, SEO tool and Search Console box probes "
        "(#11615)")
    assert _sitemap_root_tag(SITEMAP_INDEX) == f"{SITEMAP_NS}sitemapindex", (
        f"website/sitemap.xml is a {_sitemap_root_tag(SITEMAP_INDEX)} — the conventional "
        "entry point must be a <sitemapindex>, so it can advertise every child sitemap ON "
        "ITS OWN HOST (since #11766 the children are all on this host; a cross-host index "
        "is what #11623 removed, not the fix)")
    locs = _xml_locs(SITEMAP_INDEX)
    assert locs, "the sitemap index advertises nothing"
    for loc in locs:
        parsed = urlparse(loc)
        assert parsed.scheme == "https", f"{loc} is not https"
        assert parsed.netloc in SITE_HOSTS, (
            f"{loc} is on {parsed.netloc}, which is not a host this project serves "
            f"({sorted(SITE_HOSTS)})")


def test_every_sitemap_index_loc_is_on_the_indexes_own_host() -> None:
    """#11766: the index may name sitemaps on ITS OWN host, and nothing else.

    This is the assertion that would have caught #11623: the index named children on
    `tortoise.premiselabs.co` and `app.premiselabs.co` while being served from three
    hosts, which the Sitemaps protocol forbids for index files (sitemaps.org, *Using
    Sitemap index files*: an index "can only specify Sitemaps that are found on the same
    site as the Sitemap index file … not on … http://yourhost.yoursite.com" — a SUBDOMAIN
    is the spec's own counterexample).

    "The index's own host" is the ONE marketing host (#11762): the retired product host
    is a PERMANENT 301 to it and serves nothing, so a child named there is not a second
    site — it is the same site spelled at a redirect. That host is READ from the
    middleware (`COMPANY_ORIGIN`) rather than restated from memory, because the copy that
    drifts from the host the pages are actually served on is the defect itself.

    ⛔ THE PROPERTY IS ON THE CHILD <loc>s, which is why `SITE_HOSTS` (the membership set
    used above) cannot express it: three served hosts all pass that check, and that is
    precisely the index #11623 shipped.
    """
    middleware = _read(WEBSITE / "functions" / "_middleware.ts")
    code = "\n".join(
        line for line in middleware.splitlines() if not line.lstrip().startswith("//")
    )
    origin = re.search(r'const COMPANY_ORIGIN = "(https://[^"]+)"', code)
    assert origin, (
        "_middleware.ts no longer declares `const COMPANY_ORIGIN = \"https://…\"` — "
        "re-derive MARKETING_ORIGIN in this module by hand and re-point this guard")
    assert origin.group(1).rstrip("/") == MARKETING_ORIGIN, (
        f"the middleware serves the one marketing host at {origin.group(1)!r} while the "
        f"sitemap advertisements here assume {MARKETING_ORIGIN!r} — every child <loc> "
        "would be submitted against a host nothing is served from, or a host that 301s")
    own_host = urlparse(MARKETING_ORIGIN).netloc
    # ⛔ THE ADVERTISEMENT SIDE IS HELD TO THE SAME HOST, not only the index's children. A
    # `Sitemap:` line naming `app.premiselabs.co` or the retired host is the cross-submission
    # this child removes, and without this assertion it could be re-added with every guard
    # green — measured: appending the app line, or the retired host's, left 77 passed.
    advertised_hosts = {urlparse(url).netloc for url in _robots_sitemap_urls()}
    assert advertised_hosts == {own_host}, (
        f"website/robots.txt advertises sitemaps on {sorted(advertised_hosts)} — this file names "
        f"the sitemaps of the host it is served from ({own_host}), and nothing else (#11766). "
        "A foreign host here is the cross-submission the same-host index replaced; the app "
        "project's own robots.txt advertises its own sitemap")
    off_host = sorted({urlparse(loc).netloc for loc in _xml_locs(SITEMAP_INDEX)} - {own_host})
    assert not off_host, (
        f"website/sitemap.xml names {off_host} — an index may only specify sitemaps on "
        f"its own site, and this one is served from {own_host} (#11623/#11766). Repoint "
        "the child at its URL on the marketing host, or drop it: either it is this "
        "project's page (so it lives here now, epic #11762) or it belongs to another "
        "project with its own robots.txt (the app origin) and this index must not "
        "claim it")


def _advertised_sitemaps() -> set[str]:
    """Every sitemap URL a crawler reaches from robots.txt, transitively.

    robots.txt names the INDEX (`/sitemap.xml`) rather than the three children, which is
    what Google documents for a sitemap index ("you don't need to include each individual
    sitemap") — so the index↔robots relation CANNOT be textual set equality (#11766). The
    drift the equality existed to catch is not "robots names the index" but "a child
    becomes undiscoverable", and that is what this closure measures: start from the
    `Sitemap:` lines, and for every advertised document that is a SITEMAP INDEX add its
    children, until nothing new appears.

    A line that resolves to no file contributes nothing — that is
    `test_every_advertised_sitemap_is_servable_from_this_tree`'s subject, asserted
    separately so the two failures are not reported as one.
    """
    reached = set(_robots_sitemap_urls())
    queue = list(reached)
    while queue:
        document = _sitemap_file_for(queue.pop())
        # ⛔ A FUNCTION-SERVED SITEMAP CANNOT BE A STATIC INDEX here: the blog's child is a
        # `.ts` handler, and handing it to an XML parser raises rather than answering. It
        # is a `<urlset>` by construction, so it contributes no children and is skipped.
        if document is None or document.suffix != ".xml":
            continue
        if _sitemap_root_tag(document) != f"{SITEMAP_NS}sitemapindex":
            continue
        for child in _xml_locs(document):
            if child not in reached:
                reached.add(child)
                queue.append(child)
    return reached


def _function_sitemap_paths() -> set[str]:
    """URL paths served by a Pages Function whose filename IS `sitemap.xml.ts`.

    DERIVED from the tree, because the walk over `website/*.xml` cannot see these: the blog's
    sitemap is generated at request time from published posts. Without this population the
    "is every sitemap discoverable?" question has no subject for the one child that is a
    Function, which is how a dropped `<loc>` for `/blog/sitemap.xml` could pass every guard
    in this file (measured — a fresh-context verifier deleted it and the module stayed 75
    green).
    """
    return {
        "/" + str(path.relative_to(WEBSITE / "functions"))[: -len(".ts")]
        for path in (WEBSITE / "functions").rglob("sitemap.xml.ts")
    }


def test_the_sitemap_index_and_robots_advertise_the_same_set() -> None:
    """Every sitemap this tree SERVES must be reachable from robots.txt.

    One-way checks rot: robots could advertise an index whose child has been removed (a
    crawler follows a dead `<loc>`), or a child could stop being advertised by anything at
    all. Set EQUALITY used to hold the two files to each other, and it no longer can, for two
    independent reasons (#11766): robots.txt now names the INDEX (the shape Google documents
    for an index, "you don't need to include each individual sitemap"), and the same-site
    children mean the two lists differ by construction.

    ⛔ SO THE POPULATION COMES FROM THE TREE, NOT FROM THE ADVERTISEMENT, and that is the
    whole point of this test rather than a detail. The reachability closure alone is
    TAUTOLOGICAL in the direction that matters: with robots.txt naming the index, the closure
    is seeded FROM the index's own children, so "every child of the index is reachable" is
    true by construction and deleting a child from the index leaves every guard green — the
    drift equality existed to catch. Asking instead "is every sitemap the tree SERVES
    reachable?" is non-vacuous in both shapes, and it is the question a crawler's discovery
    actually depends on. Measured after the fix: removing the blog child's `<loc>` reds here.
    """
    reachable = _advertised_sitemaps()
    assert reachable, "robots.txt advertises no sitemap at all"
    own_host = urlparse(MARKETING_ORIGIN).netloc
    reachable_pairs = {(urlparse(url).netloc, urlparse(url).path or "/") for url in reachable}
    served_pairs = {(own_host, f"/{path.name}") for path in _published_sitemaps(WEBSITE)}
    served_pairs |= {(own_host, path) for path in _function_sitemap_paths()}
    # The index itself is advertisement, not a child (`_published_sitemaps` excludes it) —
    # but it must be reachable too, or a crawler that reads robots.txt cannot start.
    served_pairs.add((own_host, f"/{SITEMAP_INDEX.name}"))
    missing = sorted(served_pairs - reachable_pairs)
    assert not missing, (
        f"sitemap(s) this tree serves but no crawler is led to: {missing}. A sitemap that "
        "exists but is advertised by nothing is invisible — the other half of #11615. "
        "Advertise it from robots.txt, or name it as a child of the index robots.txt "
        "advertises (which is one line for all of them), or delete it")


def test_every_advertised_sitemap_is_servable_from_this_tree() -> None:
    """An advertised sitemap that 404s is worse than an unadvertised one.

    This is the half the tree could not see before: robots.txt named four sitemaps and
    nothing checked that the file (or the Function) behind each one exists.

    Since #11766 the walk is the CLOSURE (`_advertised_sitemaps`), not the `Sitemap:`
    lines: robots.txt names the index, and a walk of the raw lines would grade exactly one
    document while the three children it points at stayed unmeasured.

    ⛔ THE APP PROJECT'S OWN ADVERTISEMENT IS IN THE WALK, and dropping it here would be a
    coverage REGRESSION: `website/apps/dashboard/public/robots.txt` is committed in this
    tree and advertises `https://app.premiselabs.co/sitemap.xml`, a URL `_sitemap_file_for`
    resolves inside the dashboard's `public/`. Without it, deleting that sitemap while its
    robots.txt still names it is green everywhere — a robots.txt advertising a 404, which is
    this test's own subject.

    Its CONVERSE — a sitemap that exists and is advertised by nothing — is
    `test_every_sitemap_in_the_tree_is_advertised`. Both halves are needed: this one walks
    the advertisement, that one walks the tree.
    """
    advertised = set(_advertised_sitemaps())
    app_robots = WEBSITE / "apps" / "dashboard" / "public" / "robots.txt"
    if app_robots.is_file():
        advertised |= set(_robots_sitemap_urls(app_robots))
    missing = [url for url in sorted(advertised) if _sitemap_file_for(url) is None]
    assert not missing, (
        f"an advertised sitemap has no file or Function behind it: "
        f"{missing}. Either add the sitemap or drop the reference — a crawler that reads "
        "robots.txt follows the index and its children, and a dead `<loc>` there is "
        "discarded with everything else in that file")


def test_every_sitemap_in_the_tree_is_advertised() -> None:
    """The OTHER half: a sitemap no crawler is told about is invisible, not covered.

    Neither model above can see this one. `test_every_advertised_sitemap_is_servable…`
    walks the ADVERTISEMENT (every advertised URL must have a file behind it), and
    `test_every_in_scope_page_appears_in_a_sitemap` walks the FILES (every page must be in
    some `sitemap-*.xml`). So delete a `<sitemap>` block from the index AND its matching
    `Sitemap:` line from robots.txt and everything still agrees: the two advertisements
    stay equal, and every page the dropped file carried is still counted as covered — by
    a file no crawler is ever told exists. That is the #11615 defect with its halves
    swapped (coverage without an entry point), which is why it is held here.

    Derived from the tree, not from a list, so a NEW sitemap is held to it the moment it
    lands — the same derivation discipline as `test_every_in_scope_page_appears_in_a_sitemap`.
    The population is found by ROOT ELEMENT, not by filename (`_published_sitemaps`), so a
    new file is held to this under ANY conventional name (`blog-sitemap.xml` included), not
    only the `sitemap-*.xml` shape that happened to exist when the guard was written.
    """
    advertised = {
        (urlparse(url).netloc, urlparse(url).path or "/")
        for url in set(_xml_locs(SITEMAP_INDEX)) | set(_robots_sitemap_urls())
    }
    advertised_paths = {path for _host, path in advertised}
    problems: list[str] = []
    for sitemap in _published_sitemaps(WEBSITE):
        path = f"/{sitemap.name}"
        if path not in advertised_paths:
            problems.append(
                f"website/{sitemap.name} — served at {path}, but no `<loc>` in "
                "website/sitemap.xml and no `Sitemap:` line in website/robots.txt names "
                "that path, so no crawler is told it exists")
    # The app origin's sitemap is a committed copy for a SEPARATE Pages project, so the
    # glob above cannot reach it; it is still a sitemap this tree publishes.
    #
    # ⛔ SINCE #11766 THE MARKETING INDEX MUST NOT ADVERTISE IT — it is not a child on
    # the index's host, and that cross-host entry was the whole defect in #11623. So the
    # advertisement that has to exist is the APP PROJECT'S OWN `robots.txt`: dropping the
    # child here must not become "the app's sitemap is advertised by nothing", and it must
    # not be "fixed" by deleting the app's sitemap, which still serves its own property.
    app_public = WEBSITE / "apps" / "dashboard" / "public"
    app_sitemap = app_public / "sitemap.xml"
    app_robots = app_public / "robots.txt"
    if app_sitemap.is_file() and ("app.premiselabs.co", "/sitemap.xml") not in advertised:
        app_urls = _robots_sitemap_urls(app_robots) if app_robots.is_file() else []
        if "https://app.premiselabs.co/sitemap.xml" not in app_urls:
            problems.append(
                "website/apps/dashboard/public/sitemap.xml — advertised by nothing. The "
                "marketing index may not name it (a foreign host is #11623/#11766), so "
                "the app project's OWN robots.txt has to advertise "
                "https://app.premiselabs.co/sitemap.xml, and it does not")
    assert not problems, "\n".join(problems) + (
        "\nAdvertise it from the file that OWNS it — this project's `robots.txt` or a "
        "`<loc>` in the index robots.txt advertises, or, for the app origin, that "
        "project's own `robots.txt`. ⛔ NOT from a foreign-host index: #11766's "
        "same-host assertion reds on exactly that advice, which is what this sentence "
        "used to give. Or delete it — a sitemap that exists but is advertised by "
        "nothing is #11615 with its halves swapped")


def test_sitemap_discovery_reads_the_root_element_not_the_filename(tmp_path: Path) -> None:
    """A sitemap is identified by its ROOT ELEMENT — a name the old glob never knew counts.

    `test_every_sitemap_in_the_tree_is_advertised` claimed to derive its population from the
    tree, but the derivation was `WEBSITE.glob("sitemap-*.xml")`, so it only ever held the
    one naming shape present when it was written. A `website/blog-sitemap.xml` — a plausible
    shape here, since the blog's own sitemap is served by
    `website/functions/blog/sitemap.xml.ts` — would have shipped advertised by nothing with
    every guard green: #11615 exactly, and the guard that exists to catch it silent.
    """
    ns = SITEMAP_NS[1:-1]  # the namespace URI, without the `{…}` wrapper
    urlset = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="{ns}"><url><loc>https://premiselabs.co/docs</loc></url></urlset>\n'
    )
    (tmp_path / "blog-sitemap.xml").write_text(urlset, encoding="utf-8")
    (tmp_path / "sitemap-company.xml").write_text(urlset, encoding="utf-8")
    (tmp_path / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sitemapindex xmlns="{ns}"><sitemap>'
        "<loc>https://premiselabs.co/sitemap-company.xml</loc>"
        "</sitemap></sitemapindex>\n",
        encoding="utf-8",
    )
    # Not a sitemap — the root element says so, so sitting beside them cannot smuggle it in.
    (tmp_path / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"></rss>\n',
        encoding="utf-8",
    )

    found = {p.name for p in _published_sitemaps(tmp_path)}

    # The pre-fix derivation, stated here so this test's non-vacuity is visible rather than
    # asserted: the glob it used cannot see the file at all.
    assert {p.name for p in tmp_path.glob("sitemap-*.xml")} == {"sitemap-company.xml"}
    assert found == {"blog-sitemap.xml", "sitemap-company.xml"}


def test_an_unreadable_sitemap_is_not_silently_dropped(tmp_path: Path) -> None:
    """An unreadable sitemap must FAIL the walk, not disappear from it.

    Discovery by root element means "is not a sitemap" and "cannot be read as one" are the
    SAME answer to a parser, so without the re-raise in `_is_sitemap_document` a truncated
    or half-merged sitemap that `robots.txt` and the index both advertise drops out of the
    population BEFORE anything parses it, and all three walks stay green. The crawler-facing
    result is an advertised sitemap of unreadable XML, which search engines discard WHOLE
    (sitemaps.org: a file that fails to parse yields no URLs), i.e. #11615 with the guards
    reporting coverage that does not exist. Measured before the re-raise: a truncated
    `website/sitemap-extra.xml` gave `2 failed` under the old filename glob and `72 passed`
    after discovery moved to root elements.

    ⛔ BOTH HALVES ARE PINNED, because the cheap repair is the wrong one: dropping the
    `except` entirely would redden a corrupt `feed.xml`, which really is just an XML file
    beside the sitemaps and not a sitemap at all. Only a name that CLAIMS to be a sitemap
    is re-raised.
    """
    ns = SITEMAP_NS[1:-1]  # the namespace URI, without the `{…}` wrapper
    (tmp_path / "sitemap-company.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="{ns}"><url><loc>https://premiselabs.co/docs</loc></url></urlset>\n',
        encoding="utf-8",
    )
    # Truncated mid-document — every parse of it raises. Pre-fix it was silently treated
    # as "not a sitemap", so `pytest.raises` below DID NOT RAISE.
    (tmp_path / "sitemap-blog.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="{ns}"><url><loc>https://premiselabs.co/docs</loc></url>\n',
        encoding="utf-8",
    )
    # Also truncated, but its name does not claim to be a sitemap.
    (tmp_path / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n',
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match=re.escape("sitemap-blog.xml")):
        _published_sitemaps(tmp_path)

    # The swallow is SCOPED, not removed: with the broken sitemap gone, the non-sitemap XML
    # beside it is still tolerated — so "delete the except" is not a passing repair.
    (tmp_path / "sitemap-blog.xml").unlink()
    assert {p.name for p in _published_sitemaps(tmp_path)} == {"sitemap-company.xml"}


def test_no_sitemap_advertises_a_page_that_is_deleted_or_noindex() -> None:
    """Every page URL in a TOP-LEVEL sitemap under `website/` must resolve — not be noindex.

    Two failures in one: a `<loc>` for a page that has been deleted sends a crawler to a
    404, and a `<loc>` for a `noindex` page tells a crawler the page exists while the page
    tells it to ignore the page. `investors.html` and `design-partners-intro.html` are
    noindex and their own comments assert they are absent from the sitemap — nothing
    checked that until now.

    SCOPE, stated so it cannot be mistaken for "every sitemap": the walk is
    `_published_sitemaps(WEBSITE)` — every top-level `*.xml` under `website/` whose ROOT
    ELEMENT makes it a sitemap, so a sitemap with a non-standard name (`blog-sitemap.xml`)
    is walked too, and an unreadable sitemap-shaped file is RE-RAISED rather than dropped
    (see `_is_sitemap_document`). What it cannot reach is
    `website/apps/dashboard/public/sitemap.xml`: that file belongs to the app origin, a
    SEPARATE Pages project, and its root document IS in
    this tree (`website/apps/dashboard/index.html`, served by that project's own build), so
    `_document_for_loc` could resolve its `<loc>` — it does not, because `HOST_ROOT_PAGE`
    deliberately carries no app entry: the mapping describes THIS project's middleware, and
    the app is not served by it. Covering the app sitemap's `<loc>`s is therefore a one-line
    mapping plus a walk entry (no impossibility), and it is owned by #11624 together with
    the per-host index. What IS asserted about that file today: it is advertised by its own
    project's `robots.txt` (`test_every_sitemap_in_the_tree_is_advertised` — the marketing
    index deliberately no longer names it, #11766), and a change to it selects this file
    (tests/test_ci_selection.py).

    HOST is checked here for every `<loc>` in the walk, because `_document_for_loc` resolves a
    non-root `<loc>` by PATH only: a `<urlset>` `<loc>` on a host this project does not serve
    resolves to a real document, and the checks above stay green. Measured before this assertion
    existed: `premiselabs.co`'s `/contact` rewritten to `https://not-our-host.example.net/contact`
    passed 71/71.

    WHAT THIS DOES NOT CLOSE, named rather than implied: the page `<loc>`s of THIS project are now
    held to their canonical URL — `test_every_page_loc_is_the_canonical_url_for_its_document` reds
    on `https://premiselabs.co/docs` in `sitemap-product.xml` and on
    `https://tortoise.premiselabs.co/docs`, host AND path together, with no per-file host table.
    What remains outside these walks is the population the walks cannot reach: the app project's
    `apps/dashboard/public/sitemap.xml` `<loc>`s (a separate Pages project; #11624) and the blog's
    Function-generated `<loc>`s (asserted in `tests/test_blog_seo_meta.mjs` against `SITE_URL`, not
    here). Do not re-derive a host table for the page case — the canonical test is that answer.
    """
    problems: list[str] = []
    for sitemap in _published_sitemaps(WEBSITE):
        for loc in _xml_locs(sitemap):
            netloc = urlparse(loc).netloc
            if netloc not in SITE_HOSTS:
                problems.append(
                    f"{sitemap.name}: {loc} — {netloc} is not a host this project serves "
                    f"({sorted(SITE_HOSTS)}), so the URL is unfetchable from that sitemap")
                continue
            document = _document_for_loc(loc)
            if document is None:
                problems.append(f"{sitemap.name}: {loc} — no document in the served tree")
                continue
            if document.suffix == ".html" and _is_noindex(_read(document)):
                problems.append(
                    f"{sitemap.name}: {loc} — {document.name} declares noindex, so the "
                    "sitemap asks a crawler to index a page that refuses indexing")
    assert not problems, "\n".join(problems)


def test_every_in_scope_page_appears_in_a_sitemap() -> None:
    """...and the other direction: an indexable page with no `<loc>` is invisible.

    The set is DERIVED (`_in_scope_pages`: public, served, not noindex, not redirected
    away), so a page added later is held to this without editing a list — the same
    derivation the blog-link guard uses.

    The advertised side is the TOP-LEVEL `_published_sitemaps(WEBSITE)` walk, for the reason
    `test_no_sitemap_advertises_a_page_that_is_deleted_or_noindex` states: that walk and
    `_in_scope_pages` describe the same population (this project's served pages). The app
    origin's `<loc>`s are resolved by NONE of the sitemap walks here — every one of them
    covers `website/` at its TOP LEVEL (`_published_sitemaps` globs `*.xml`, non-recursive),
    which cannot reach `apps/dashboard/public/` — so that gap is real and is owned by #11624,
    not covered elsewhere.
    """
    advertised = {
        _document_for_loc(loc).resolve()
        for sitemap in _published_sitemaps(WEBSITE)
        for loc in _xml_locs(sitemap)
        if _document_for_loc(loc) is not None
    }
    missing = [p.name for p in _in_scope_pages() if p.resolve() not in advertised]
    assert not missing, (
        f"indexable page(s) in no sitemap: {missing}. A crawler only finds a page that is "
        "linked or advertised; add them to the sitemap for their host")


def test_the_sitemap_index_alias_redirects() -> None:
    """/sitemap_index.xml is the other convention a tool probes (Yoast, most auditors).

    And `/sitemap.xml` must NOT itself be a redirect SOURCE. `website/_redirects` rows are
    evaluated BEFORE the static asset is looked up — Cloudflare Pages' own rule is
    "redirects are always followed, regardless of whether or not an asset matches the
    incoming request" — so a row like `/sitemap.xml  /index.html  301` would keep every
    other assertion here green while sending the conventional path away: #11615's 404,
    restored, with the guard that exists to catch it silent. That is the same class this
    PR closed for `config/pages-upload-classification.txt` (a table-derived expectation
    cannot see the table change), so the source side is asserted too.
    """
    redirects = _canonical_redirect_targets()
    assert redirects.get("/sitemap_index.xml") == "/sitemap.xml", (
        "website/_redirects must send /sitemap_index.xml to /sitemap.xml — otherwise the "
        "second-most-probed sitemap path is a 404 (#11615)")
    assert "/sitemap.xml" not in redirects, (
        "website/_redirects redirects /sitemap.xml away to "
        f"{redirects.get('/sitemap.xml')!r}. Pages follows a _redirects row BEFORE serving "
        "the matching asset, so the conventional sitemap path would 301 off the real file "
        "and #11615 is back — drop the row; the index is a static file at the site root")


def test_the_ruby_implied_close_asks_the_ordinary_scope_question() -> None:
    """`button` is NOT an ordinary scope boundary, so it cannot block a ruby close.

    ⛔ THE PRECONDITION ON "GENERATE IMPLIED END TAGS" IS *"If the stack of open
    elements has a `ruby` element in scope"*, and the scope-boundary list at
    §13.2.4.2 does not contain `button` — that member is added only for BUTTON scope.
    This module asked the button-scope question, and the off-by-one-set was a fail-open
    (#14073 review round 14, P1, measured): `<ruby><button><rp>P<rt>CLAIM` kept the
    `<rp>` (a `display: none` `_UNRENDERED` element) on the stack, so every following
    text node dropped the claim — while Chrome makes the `<rt>` a child of the
    `<button>` (`rt.parentElement` `BUTTON`) and paints the claim (`innerText`).

    The control is a `table`: it is a boundary in BOTH lists, so a `ruby` above one is
    out of scope and the implied close must NOT run. That is what keeps this fix pinned
    to `button` alone instead of to "any ancestor".
    """
    assert (
        _AnchorHrefParser._P_SCOPE_BOUNDARY - {"button"}
    ) == _AnchorHrefParser._SCOPE_BOUNDARY, (
        "`_SCOPE_BOUNDARY` is no longer the button-scope list MINUS `button`: "
        "the two lists differ in exactly that member (spec 13.2.4.2), and using the "
        "button-scope one for a rule that asks the ordinary question is the fail-open "
        "this test exists for"
    )

    # ── the mechanism, driven directly: `button` must not stop the walk, `table` must ──
    through_button = _AnchorHrefParser()
    through_button.feed("<ruby><button><rp>")
    assert through_button._ruby_in_scope() is True, (
        "a `<button>` between the current node and the `<ruby>` stopped the scope "
        "walk: `button` only belongs to BUTTON scope, so the `<ruby>` is still in "
        "scope and licenses the implied close"
    )
    through_table = _AnchorHrefParser()
    through_table.feed("<ruby><table><rp>")
    assert through_table._ruby_in_scope() is False, (
        "a `<table>` between the current node and the `<ruby>` did not stop the scope "
        "walk: `table` is a boundary in BOTH lists, so the implied close must not run "
        "and this is the control that keeps the fix pinned to `button`"
    )

    # ── and end to end, through the recording the wording pin reads ──
    assert "CLAIM" in extract_visible_text("<ruby><button><rp>P<rt>CLAIM"), (
        "the claim the browser paints inside a `<button>`-wrapped ruby was dropped: "
        "the `<rp>` is `display: none`, so leaving it above the text hides it from "
        "the wording golden through every `#storage` pin"
    )


def test_the_table_structure_tokens_a_browser_ignores_insert_nothing() -> None:
    """A table-structure start tag outside a table is IGNORED, not inserted.

    Spec 13.2.6.4.7: *"A start tag whose tag name is one of: 'caption', 'col',
    'colgroup', 'frame', 'head', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr' — Parse
    error. Ignore the token."* Pushing one invented an ancestor the tree does not have,
    and several members are `_P_SCOPE_BOUNDARY`, so a stray one stopped the implied
    `</p>` walk and left an `_UNRENDERED` sibling above the text. Measured:
    `extract_visible_text('<p><td><rp><div>CLAIM')` was `''` while Chrome ignores the
    `<td>`, lets the `<div>` close the `<p>` (popping the `<rp>` with it) and paints the
    claim. End-to-end through `_assert_storage_section_reviewed` that shape left the
    allowlist, the destinations, the vocabulary scan and the golden ALL green.

    The ignore is CONTEXTUAL, and both halves are pinned here: the same tokens inside a
    `<table>` are inserted by the table modes, which is what the foster-parenting rows
    depend on.
    """
    assert set(_AnchorHrefParser._IGNORED_OUTSIDE_TABLE) == {
        "caption", "col", "colgroup", "frame", "head", "tbody", "td", "tfoot",
        "th", "thead", "tr",
    }, (
        "`_IGNORED_OUTSIDE_TABLE` changed: it is the spec's ignore list verbatim, and "
        "every member is a token a browser drops outside a table — widening or "
        "narrowing it moves an element in or out of the ancestry the text question "
        "reads"
    )
    assert "CLAIM" in extract_visible_text("<p><td><rp><div>CLAIM"), (
        "a stray `<td>` outside a table was inserted and stopped the implied `</p>`: "
        "a browser ignores the token, the `<div>` closes the `<p>` and the claim is "
        "painted — this is the fail-open the row above exists for"
    )
    for stray in ("th", "caption", "tbody", "tr", "colgroup", "col", "frame", "head"):
        assert "CLAIM" in extract_visible_text(f"<p><{stray}><rp><div>CLAIM"), (
            f"a stray `<{stray}>` outside a table was inserted: a browser ignores the "
            f"token (Parse error), so it cannot be the boundary that hides the claim"
        )
    outside = _AnchorHrefParser()
    assert outside._ignored_token("td") is True, (
        "`<td>` was not ignored with no table open"
    )
    inside = _AnchorHrefParser()
    inside.feed("<table><tr>")
    assert inside._ignored_token("td") is False, (
        "`<td>` was ignored WITH a table open: the table modes insert it, and the "
        "foster-parenting rows depend on the cell being in the tree"
    )
    # ⛔ AND AN IGNORED TOKEN STILL ENDS FOREIGN CONTENT (#14073 review round 15, P2,
    # measured). `head` is on the foreign-content BREAKOUT list, and `_open` is where
    # that runs — returning before it left this parser "foreign" past `<svg><head>`, so
    # the following `<select>` was never pushed and an anchor inside it was reported as
    # a link. Chrome: the select is outside the svg and the anchor's rect is 0×0.
    assert extract_anchor_hrefs(
        '<svg><head><select><a href="/blog">x</a></select>'
    ) == [], (
        "`<svg><head>` did not break out of the foreign context: `head` is a breakout "
        "tag, so the `<select>` after it is HTML and the anchor inside it is no link"
    )
    assert extract_anchor_hrefs(
        '<math><head><select><a href="/blog">x</a></select>'
    ) == [], (
        "`<math><head>` did not break out of the foreign context (MathML half of the "
        "same rule)"
    )
    assert extract_anchor_hrefs(
        '<svg><div><select><a href="/blog">x</a></select>'
    ) == [], (
        "premise: a NON-ignored breakout tag already ended the foreign context, so the "
        "rows above measure the ignored-token path and not the select rule"
    )


def test_the_document_roots_a_browser_ignores_insert_nothing() -> None:
    """`<html>`/`<body>` insert no element, and pushing them was a P0 in BOTH directions.

    ⛔ THE `_open_extent` CONTRACT WAS FALSE FOR THE TWO TAGS EVERY PAGE CARRIES (#14073
    review, P0, measured). Its docstring says "only elements a browser puts in the tree
    are pushed"; in "in body" a start tag whose name is `html`/`body` is a **parse
    error that inserts nothing** (spec 13.2.6.4.7 — a browser merges the attributes
    into the pre-inserted root/body). This parser does not pre-insert those roots, so
    the token became a PHANTOM at the CURRENT depth instead of at the bottom — and
    `html` is a `_P_SCOPE_BOUNDARY` member (hence `_SCOPE_BOUNDARY`), so the phantom
    was a scope boundary ABOVE live content and stopped end tags a browser honours.

    The two directions are DIFFERENT FAILURES and both are pinned here:

    * the DROP direction (a claim the page must not make, invisible to the golden) —
      `</audio>` could not close the `audio`, an `_UNRENDERED` ancestor stayed open
      across the text and `_text_is_painted()` returned False;
    * the BOUND direction (an over-advertised TOC, i.e. a SHORTER slice) — a browser
      closes `div.toc` at its own `</div>` and leaves the anchor OUTSIDE it.

    Each row is asserted against the shape WITHOUT the root tag where one exists, so a
    fix that simply refused every tag would red here instead of passing.
    """
    # ── the DROP direction: the claim a browser paints must reach the golden ──
    assert extract_visible_text("<audio><html></audio>CLAIM</html></audio>") == "CLAIM", (
        "a phantom `<html>` became a scope boundary, so `</audio>` could not close the "
        "`audio`: the `_UNRENDERED` ancestor stayed open across the claim and the "
        "wording golden never saw text a browser paints. End-to-end through "
        "`_assert_storage_section_reviewed` this left the allowlist, the destinations, "
        "the vocabulary scan and the golden ALL green with a contradicting paragraph "
        "inside `#storage` — the exact class the pin exists to refuse"
    )
    assert extract_visible_text("<body><p>CLAIM") == "CLAIM", (
        "`<body>` is not pushed any more, and this is the row that keeps the frame "
        "intact on the ordinary path every real page takes: the text under the body "
        "root must still reach the golden"
    )
    # ── the BOUND direction: the advertised set must not be the larger one ──
    assert extract_anchors_within(
        '<div class="toc"><html></div><a href="#x">x</a></html></div>',
        tag="div",
        class_token="toc",
    ) == [[]], (
        "a phantom `<html>` kept `div.toc` open past its own `</div>`: a browser "
        "closes the container there and leaves the anchor OUTSIDE it, so reporting "
        "the anchor ADVERTISES a heading that is not in the TOC — a larger advertised "
        "set, a shorter slice, and every `#storage` pin green over the contradiction"
    )
    assert extract_anchors_within(
        '<div class="toc"><a href="#what">w</a></div>', tag="div", class_token="toc"
    ) == [["#what"]], (
        "control: the same container WITHOUT the root tag still reports its own "
        "anchors, so the refusal above is about the phantom and not about refusing "
        "the container"
    )
    # ── and the root tags stay OUT of `_ignored_token` on purpose ──
    # ⛔ ROUTING THEM THROUGH `_ignored_token` WOULD BE THE OBVIOUS FIX AND IT IS WRONG:
    # `_open()`'s `body` branch is what sets `_body_seen`, which the frameset rule
    # reads, and `handle_starttag` consults `_ignored_token` BEFORE `_open` — so the
    # suppression would silently disable the frameset arm. `_open` runs first, and only
    # the PUSH is skipped.
    probe = _AnchorHrefParser()
    probe.feed("<body>")
    assert probe._body_seen is True, (
        "`<body>` no longer sets `_body_seen`: the frameset rule ("
        "`tag == 'frameset' and not self._body_seen`) would then ignore frameset "
        "anchors on a page that HAS a body"
    )
    root_probe = _AnchorHrefParser()
    root_probe.feed("<html><body><a href='/blog'>x</a>")
    assert "html" not in [open_tag for open_tag, _, _ in root_probe._open_elements], (
        "`html` reached `_open_elements`: this is the phantom that stops end tags a "
        "browser honours, and every row above depends on it staying off the stack"
    )
    # ⛔ THE `body` HALF NEEDS ITS OWN ROW (#14073 review, P3, measured). `_open_extent`'s
    # comment says the pairing is what the rows pin, and that was FALSE: with the guard
    # narrowed to `("html",)` the module stayed `112 passed, 1 skipped`. `body` is not a
    # `_P_SCOPE_BOUNDARY` member, so no text or anchor row here can observe the phantom —
    # it would sit on the stack until some LATER walk read it, which is why only a stack
    # row pins it. The pairing exists because neither tag inserts an element; without this
    # row, "fixing" the `html` half alone looks complete and silently leaves the rest in.
    assert "body" not in [open_tag for open_tag, _, _ in root_probe._open_elements], (
        "`body` reached `_open_elements`: a browser inserts nothing for this tag in "
        "'in body' (spec 13.2.6.4.7 — the attributes merge into the pre-inserted body), "
        "and this parser pre-inserts no roots, so the pushed token is a phantom at the "
        "CURRENT depth. Unlike `html` it is not a scope boundary, so nothing else in "
        "this module would notice it going back in"
    )
    # ── SKIPPING THE PUSH MUST NOT SKIP THE `hidden` FLAG (#14073 round 22, P2) ──
    # ⛔ THE ROOT TAGS CARRY A `hidden` THAT HAS TO LAND SOMEWHERE. `html`/`body` are in
    # neither `_UNRENDERED` nor `_RENDERS_WITH_ATTR`, so the stack's inherited `renders`
    # flag was the ONLY thing that could carry it — and once the push was skipped, a
    # hidden root silently became a visible one: the MEASURED regression was
    # `extract_visible_text('<body hidden><p>CLAIM')` → `'CLAIM'` on the fixed tree where
    # the parent commit answered `''`, plus a hidden body's `<h2>` back in the heading
    # index. A browser paints NOTHING there (`hidden` is global and the UA stylesheet's
    # `[hidden] { display: none }` applies to the body: measured in Chrome, both
    # `<body hidden>` and `<html hidden>` give a 0x0 box, 0 painted px). The flag lives in
    # `_root_renders` and BOTH questions read it.
    for hidden_root in ("<body hidden><p>CLAIM", "<html hidden><p>CLAIM"):
        assert extract_visible_text(hidden_root) == "", (
            f"{hidden_root!r}: a `hidden` root is not painted at all, so its text must "
            "not reach the wording golden — skipping the PUSH must not skip the flag"
        )
    assert extract_visible_text("<body><p>CLAIM") == "CLAIM", (
        "control: the SAME shape without `hidden` still reaches the golden, so the two "
        "rows above are about the attribute and not about refusing the root"
    )
    assert extract_heading_positions('<body hidden><h2 id="h">H</h2>') == [], (
        "a `<h2>` under a hidden body was recorded: it would then BOUND the section at "
        "a heading no reader meets — the over-advertised boundary direction"
    )
    assert extract_heading_positions('<body><h2 id="h">H</h2>') == [("h", 6)], (
        "control: the same heading without the hidden root is still recorded"
    )
    assert extract_anchor_hrefs('<body hidden><a href="/blog">x</a>') == ["/blog"], (
        "the LINK question must stay NARROW: a banner the page's own script can reveal "
        "is still a link a visitor can click (see `_in_select`'s note), so the root's "
        "`hidden` must not remove it from the rendered-link set"
    )
    # ⛔ TWO MORE TEXT PATHS THE FLAG HAS TO REACH (#14073 review round 23, P2 each).
    # (a) `handle_data` admits a `_RAW` member whose text a painted LABEL repaints, and it
    #     asks `_in_painted_label` BEFORE `_text_is_painted` — so a hidden root was a
    #     SECOND, unflagged path into the golden:
    assert extract_visible_text("<body hidden><select><option>CLAIM") == "", (
        "a hidden root repaints nothing, so even a painted option LABEL cannot show its "
        "text; the module's own control `<div hidden><select><option>CLAIM` answers `''`, "
        "and this row is what makes the two agree"
    )
    assert extract_visible_text("<body><select><option>CLAIM") == "CLAIM", (
        "control: the same label WITHOUT the hidden root still reaches the golden, so the "
        "row above is about the root and not about refusing the label carve-out"
    )
    # (b) The root token MERGES into a root the browser already inserted, so it hides text
    #     the parser read BEFORE it — `'ONE'` was kept while a browser paints neither word:
    assert extract_visible_text("<p>ONE<body hidden><p>TWO") == "", (
        "text read before the root token was kept: a `hidden` merged into the body hides "
        "the whole document, including content authored above the token"
    )
    assert extract_visible_text("<p>ONE<body><p>TWO") == "ONE TWO", (
        "control: without `hidden` the pre-root text stays, so the row above is about the "
        "attribute and not about dropping earlier text on any root token"
    )
    # ⛔ A `body` TOKEN CARRYING `hidden` HIDES THE WHOLE DOCUMENT, INCLUDING INSIDE AN OPEN
    # `<select>` (#14073 review round 31, P2 — MEASURED; rounds 24/25 said the text must be
    # COUNTED there). Round 24 argued the in-select case was browser-dependent and took the
    # loud side. Chrome's answer is not dependent: the token's attributes merge into the
    # `<body>` element the browser already inserted, and `[hidden] { display: none }` on the
    # body paints nothing — `<p>ONE<select><body hidden></select><p>TWO` gives `[hidden]` on
    # `['BODY']`, 0 painted pixels, and both text nodes in a 0x0 box.
    assert extract_visible_text("<p>ONE<select><body hidden></select><p>TWO") == "", (
        "a `hidden` on a `body` token inside an open `<select>` was COUNTED as painted: the "
        "attribute lands on the `<body>` element whatever the stack holds, so nothing paints "
        "and the golden must carry neither word"
    )
    assert extract_visible_text("<p>ONE<select>X") == "ONE X", (
        "control: the same shape without the root token keeps both words"
    )
    # ⛔ AN `html` TOKEN'S ATTRIBUTES MERGE INTO THE DOCUMENT ROOT, NOT INTO THE TOP ELEMENT OF
    # THE STACK (#14073 review round 31, P2 — round 26's P1 is FALSIFIED; the measurements are
    # in `_open_extent`'s root-token arm). Round 26 quoted the living standard's "let
    # htmlElement be the top element of the stack of open elements" wording and pinned
    # `'ONE TWO'` for the four documents below, each justified as "both paragraphs still
    # paint". MEASURED in Chrome, NOT ONE of them paints a word: `[hidden]` lands on `['HTML']`
    # in every one and every text node keeps a 0x0 box. This module's contract is the BROWSER's
    # tree (class docstring), so the browser's answer is the pin and the spec quote is the
    # recorded divergence.
    for root_hider in (
        "<p>ONE<select><html hidden></select><p>TWO",
        "<p>ONE<div><html hidden></div><p>TWO",
        "<p>ONE<span><html hidden></span><p>TWO",
        "<div><span><html hidden>CLAIM</span>MORE</div>",
    ):
        assert extract_visible_text(root_hider) == "", (
            f"{root_hider!r}: an `html` token carrying `hidden` puts the attribute on the "
            "document ROOT (measured), so the WHOLE page is unpainted — no word of it may "
            "reach the wording golden. Answering a subtree here under-counts the hide and "
            "over-counts the text"
        )
    assert extract_visible_text("<div><span><html>CLAIM</span>MORE</div>") == "CLAIM MORE", (
        "control: an `html` token WITHOUT `hidden` changes nothing even with an open element "
        "— the attribute is the whole trigger, so the rows above are about it"
    )
    assert extract_visible_text("<p>ONE<select><html></select><p>TWO") == "ONE TWO", (
        "control: the same `html` token WITHOUT `hidden` changes nothing"
    )
    # A non-root `hidden` is a DIFFERENT question and keeps its subtree-only answer — this
    # row is what makes the four above about the ROOT token rather than about `hidden`:
    assert extract_visible_text("<div><span hidden>CLAIM</span>MORE</div>") == "MORE", (
        "control: `hidden` on an ordinary element hides only its own subtree; if this row "
        "answered `''` too, the root rows above would pass on ANY over-hide"
    )
    # ...and an `html` token with NOTHING open reaches the ROOT, and there it hides the page:
    assert extract_visible_text("<html hidden><p>CLAIM") == "", (
        "`<html hidden>` with an empty stack lands on the root element, so nothing is painted"
    )
    # The HEADING question follows the same rule: a `<h2>` is a boundary only if a reader MEETS
    # it, and an `html` token's `hidden` is not confined to a subtree a later `<h2>` could sit
    # outside of.
    assert extract_heading_positions('<h2 id="h">H</h2><select><html hidden><p>X') == [], (
        "a `<h2>` written BEFORE the select was kept as a boundary: the `html` token's "
        "`hidden` lands on the ROOT (measured), so that heading is in a 0x0 box like the "
        "rest — keeping it bounds the slice at a heading no reader meets"
    )
    assert extract_heading_positions('<div><html hidden><h2 id="h">H</h2></div>') == [], (
        "a `<h2>` inside the document an `html` token hid was still recorded, so it would "
        "bound the section at a heading no reader meets"
    )
    assert extract_heading_positions(
        '<div><h2 id="h">H</h2><span hidden>X</span></div>'
    ) == [("h", 5)], (
        "control: a non-root `hidden` leaves a `<h2>` outside its subtree meetable, so the "
        "rows above are about the ROOT token and not about refusing headings near `hidden`"
    )
    # ⛔ A PRE-ROOT HEADING CANNOT BOUND EITHER (#14073 review round 24, P2): the clear the
    # row above pins for `text` has to cover `headings` too, or the boundary question answers
    # a heading no reader meets.
    assert extract_heading_positions('<h2 id="h">H</h2><body hidden><p>X') == [], (
        "a `<h2>` read BEFORE the hidden root survived: it would then BOUND the section at "
        "a heading `[hidden] { display: none }` on the body paints nowhere"
    )
    assert extract_heading_positions('<body><h2 id="h">H</h2>') == [("h", 6)], (
        "control: the same heading on a visible body is still recorded"
    )
    for never_an_extent in ("body", "html"):
        try:
            extract_anchors_within(
                f'<{never_an_extent} class="b"><div class="toc"><a href="#x">x</a></div>',
                tag=never_an_extent,
                class_token="b",
            )
        except ValueError as exc:
            assert "inserts NO element" in str(exc), (
                f"`{never_an_extent}` was refused, but not for the reason that makes it "
                f"un-extentable: {exc}"
            )
        else:  # pragma: no cover - the assertion below is the failure message
            raise AssertionError(
                f"`{never_an_extent}` was accepted as a container tag and returned a "
                "silent ZERO containers: the tag inserts no element, so it can never be "
                "an extent, and the tags this helper REFUSES (`_IMPLICITLY_CLOSED`) "
                "refuse rather than answer zero. ⛔ NOT a universal over the "
                "ignored-token class: `head` and `frame` are members of "
                "`_IGNORED_OUTSIDE_TABLE` and still answer a silent `[]` (#14073 review, P3), "
                "and `image` is NOT refused either — it is ACCEPTED when the block is "
                "closed (`[['#x']]`) and raises only through the unrelated "
                "never-closed rule when it is not (measured; the parenthetical that "
                "claimed otherwise is gone, #14073 review round 24)"
            )


def test_the_script_data_wedge_states_its_own_rule() -> None:
    """The wedge fires when THIS chunk ends double-escaped — and the module says so.

    ⛔ THREE ARTIFACTS DESCRIBED A RULE THE CODE DID NOT HAVE (#14073 review round 14,
    P2). `_script_data_state`'s docstring, both comments in `handle_data` and the
    refusal row in the wording test each described a "no `</script` still ahead"
    look-ahead; the code consults the STATE OF THE CHUNK and nothing else, and the chunk
    ENDS at the first `</script` because `HTMLParser`'s CDATA mode has no escape states.
    The look-ahead is also not implementable as a refinement: from double-escaped a
    `</script` only returns the tokenizer to ESCAPED, so the shape has to be resolved by
    CONTINUING the state machine past the stdlib's close point and SKIPPING the bytes
    between the two tokens.

    So this pins the implemented rule from both sides, and names the cost: one LEGAL
    shape is refused. That is the loud direction, and it is declared rather than
    discovered by the next reader.
    """
    assert _script_data_state("<!--<script>") == "double", (
        "the state machine no longer reports double-escaped for the chunk shape the "
        "wedge reads — the rule this module documents would then be about something "
        "else"
    )
    assert _script_data_state("<!--<script>-->") == "data", (
        "a `-->` must return the tokenizer to plain data: that is the round-12 fix, "
        "and without it the wedge fires on a document a browser reads normally"
    )
    # ── A CHUNK THAT ENDS AT THE TOKEN IS DECIDED, NOT UNDECIDED ─────────────
    # ⛔ THE TWO BOUNDARY CASES DO NOT SHARE AN ANSWER (#14073 review round 16, P2,
    # measured). `handle_data` is fed a chunk `HTMLParser`'s CDATA mode cut at the
    # `</script` it matched, so the next character after a chunk ending in `<script` is
    # `<` — outside §13.2.5.26's returned-escaped arm — so the state is ESCAPED and the
    # element closes there. Guessing `double` wedged a document every browser paints (and the
    # parent commit read it right, so this was a regression):
    assert _script_data_state("<!--<script") == "escaped", (
        "a chunk ending exactly at `<script` was reported double-escaped: the next "
        "character is the `<` of the `</script` the chunker cut at, §13.2.5.26 "
        "reconsumes in the ESCAPED state, and calling it double wedges legal markup "
        "(#14073 review round 16, P2)"
    )
    assert _script_data_state("<!--<script</script") == "escaped", (
        "the mirror case inside one chunk: `<script` followed by `<` stays escaped, so "
        "that `</script` closes the element"
    )
    assert "CLAIM" in extract_visible_text(
        "<script><!--<script</script><p>CLAIM</p>"
    ), (
        "legal markup was refused: Chrome closes the script at that `</script` and "
        "paints CLAIM, so the wording golden must see it too — a false refusal here "
        "is the over-block direction, and it hid a painted claim from every pin"
    )
    assert extract_anchor_hrefs(
        '<script><!--<script</script><a href="/blog">x</a>'
    ) == ["/blog"], (
        "the same false refusal dropped a link a browser exposes, in the silent form: "
        "the anchor was reported absent rather than raising"
    )
    # ── what must NOT wedge: three shapes a browser paints (each measured) ──
    for markup in (
        "<script><!--<script>--></script><p>SEE THIS</p>",
        "<script><!--x--><script></script><p>SEE THIS</p>",
        "<script><!--<script>--><script></script><p>SEE THIS</p>",
    ):
        assert "SEE THIS" in extract_visible_text(markup), (
            f"legal markup was refused: {markup!r} — a browser closes the script and "
            f"paints the following text, so the wedge must not fire"
        )
    # ── and the DECLARED COST: legal markup whose element closes at a LATER ──
    # `</script`. Chrome paints the following text (`1` script element), and this
    # refuses because its chunk ends at the FIRST `</script` and therefore never sees
    # the `-->` that returns the tokenizer to data. Loud, not silently wrong.
    for declared in (
        "<script><!--<script></script>--></script><p>SWALLOWED</p>",
        "<script><!--<script></script></script><a href=\"/blog\">SWALLOWED</a>",
    ):
        with pytest.raises(ValueError, match="wedged"):
            extract_visible_text(declared)
    # ⛔ AND THE DELIMITER IS PART OF THE RULE (#14073 review round 15, P2, measured).
    # §13.2.5.31 only returns to ESCAPED when the character after `</script` is a
    # delimiter; without that test a chunk ending `</script` at the very end was read as
    # "escaped", the element "closed", and the rest of the document — script text in a
    # browser — was read as markup:
    #
    #     extract_anchor_hrefs('<script><!--<script></script</script><a href="/blog">x</a>')
    #       -> [] here; Chrome has `1` script element, `body ''`, no anchors
    #
    # i.e. a `/blog` link no browser exposes, one token past the state machine's own rule.
    assert _script_data_state("<!--<script></script") == "double", (
        "a chunk ending at `</script` with no delimiter was decided as ESCAPED: the "
        "delimiter is the next chunk's business, and guessing 'the element closed' "
        "re-reads script text as markup (the fail-open direction)"
    )
    assert _script_data_state("<!--<script></script>") == "escaped", (
        "a delimited `</script>` inside double-escaped must return the tokenizer to "
        "ESCAPED — that is the rule the undecided case above defers to"
    )
    assert _script_data_state("<!--<script></script<") == "double", (
        "the delimiter test was dropped: §13.2.5.31 returns to ESCAPED only on "
        "tab/LF/FF/space, `/` or `>`, so `</script<` must stay DOUBLE-escaped — "
        "anything else is text a browser keeps as script and this scan would re-read "
        "as markup"
    )
    # ⛔ AND A LITERAL CR IS A DELIMITER TOO (#14073 review round 16, latent). §13.2.3
    # preprocesses every CR to an LF before tokenization ("there are never any CR
    # characters in the input to the tokenization stage"), and every current caller reads
    # through universal-newline translation — so the `\r`-as-delimiter case is a contract
    # gap rather than a live bypass, and one character closes it.
    assert _script_data_state("<!--<script\r>") == "double", (
        "a literal CR after `<script` did not enter double-escaped: the tokenizer never "
        "sees a CR (§13.2.3 preprocesses it to an LF), so a parse that has one must "
        "treat it as the LF delimiter"
    )
    # …and the SAME test on the OTHER arm: leaving double-escaped is the mirror rule, and
    # dropping `\r` there was invisible to the whole suite (#14073 review round 19, P2 —
    # the mutation `data[nxt] in " \t\n\f/>"` left `110 passed`). A `</script\r>` reached
    # from double-escaped only drops back to ESCAPED; the element closes at the NEXT
    # `</script`, so the wedge must not fire.
    assert _script_data_state("<!--<script></script\r>") == "escaped", (
        "a CR after `</script` did not return the tokenizer to escaped: the two arms "
        "share one delimiter set, and the CR-to-LF preprocessing applies to both — with "
        "this arm missing the `\r` the state stays double and a legal chunk wedges"
    )
    with pytest.raises(ValueError, match="wedged"):
        extract_visible_text("<script><!--<script></script</script><p>SWALLOWED</p>")
