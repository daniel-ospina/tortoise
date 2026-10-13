"""The #3525 CSP plan doc must not re-assert the RUM origin #4706 removed (#8334 review, P2).

`docs/plans/2026-09-22-3525-csp-no-store.md` is the artifact a later lane reads before
touching the Content-Security-Policy. It carries the four policy strings under
`## Policy definitions (the actual strings this change ships)`, plus the operational
directives that go with them. #4706 removed `https://cloudflareinsights.com` from every
policy (the edge-injected beacon posts to same-origin `/cdn-cgi/rum`), but the doc only
declared the `## Production finding` section and the first `## Residuals` bullet
superseded — three other passages kept asserting the removed origin:

  * the `## Policy definitions` fenced blocks themselves (a lane copying them re-adds the
    origin and reddens `website/apps/dashboard/src/securityHeaders.test.js`),
  * the `## Adversarial Threat Surface` mutation-evidence sentence, which listed "a dropped
    beacon or RUM origin" as an EXECUTED failing mutant (backwards: dropping it is the
    correct state),
  * operational consequence #2, an imperative — "Every policy must name the beacon's script
    origin and its RUM endpoint (`https://cloudflareinsights.com`)" — that now contradicts
    `policyProblems`, which fails any policy that names that host.

That is the stale-record class this repo names explicitly. These guards are the
doc-side tripwire; the code-side assertion (that a policy NAMING the host reddens) lives in
`securityHeaders.test.js` §4b and is not duplicated here.

Static, stdlib-only, no network / browser / DB — the `tests/test_website_docs_consistency.py`
harness contract. Run:

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_csp_plan_doc_consistency.py -q

What these guards do NOT promise, named rather than hidden:

1. The disclaimer rule is a PARAGRAPH heuristic: a paragraph that mentions the removed host
   is cleared if it also carries a removal marker. A wrongly-asserted claim appended to an
   already-disclaimed paragraph (e.g. the origin table) passes. The precise, derived check
   is the `connect-src` host-set comparison in test 1; rule 2 is the prose net around it.
2. The marker list is a list. A legitimate future re-add of the origin phased differently
   ("restored for zone X") may or may not red these — the point is that a re-add cannot
   arrive silently in the shipped-strings block, not that the prose is fully pinned.
3. Only this ONE plan doc is read. A stale copy in another doc, an issue body, or a PR
   description is invisible here (#8334 itself is the evidence that prose outside a guard
   drifts).
4. The plan doc is registered by its CONCRETE path in `SOURCE_PATTERNS["onboarding"]`
   (#8334 review, Finding 1), so a docs-only diff that edits THIS doc selects the
   `onboarding` surface and runs this guard. `select()` matches with `startswith` and has
   no glob support, so the entry names the file, never `docs/` (which is in
   `NON_PYTHON_PREFIXES` and is not itself a pattern); a stale copy in another doc, an
   issue body, or a PR description is still invisible here.
5. The indirection guard reads the SPELLING the doc names in its `an indirect callee <x>`
   slot. A double-quoted computed key written anywhere else, or a correct spelling named
   beside a wrong one, is not caught; the reader-side truth stays the JS reader's own
   `nameOf`/`headerCallMethod` (verified against `@babel/parser` when this guard was
   written), not re-derived here.
6. The shape-count guard is a bullet-level MARKER check (`attribution-only` /
   `superseded` / `2026-10-13` / `fourth`), the same shape as rule 2's RUM-origin net: a
   bullet that asserts the array-of-pairs shape is invisible cannot red if it also carries
   a marker word. The precise closure evidence is the 2026-10-13 addendum's mutation table.

Two further guards, added for the #8367 review, cover that PR's own two doc-claim defects:
the `## Residuals` bullet that claimed the array-of-pairs `HeadersInit` shape is invisible
to the stamp scan (`countStamps`/`stampNames` model it since gap 5), and the indirection
clause that named a *double-quoted* computed callee (`h["set"](…)`) as the spelling that
evades the readers (it is READ — `nameOf` has a `StringLiteral` branch; the *template*
spelling is the one that evades).

A later #8367 review added two more, on the same addendum: its "Baseline after the fix"
claim pinned a whole-suite total that re-stales at every merge of `origin/main`, and
`### Bounds that remain` did not name the gap-3 umbrella's ATTRIBUTION-ONLY bound (a stamp
whose header NAME is an expression, in a file outside `HTML_SITES`). Both are guarded below.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_DOC = REPO_ROOT / "docs" / "plans" / "2026-09-22-3525-csp-no-store.md"
# Both functions trees ship a `security-headers.ts`; the dashboard one is the source of
# truth the doc's section claims to reproduce, the marketing one is its second RELAXED copy.
SHIPPED_MODULES = (
    REPO_ROOT / "website" / "apps" / "dashboard" / "functions" / "_shared" / "security-headers.ts",
    REPO_ROOT / "website" / "functions" / "_shared" / "security-headers.ts",
)

RUM_ORIGIN = "https://cloudflareinsights.com"

# `connect-src` at the start of a line (the doc's fenced blocks) or opening a quoted
# directive string (the TS modules). Anchoring this way keeps a backticked mention in prose
# ("`connect-src 'self'`") from being parsed as a policy value.
_CONNECT_SRC = re.compile(r'(?:^|\n|")connect-src\s+([^;\n"]+)')
_HOST = re.compile(r"(?:https?|wss)://[^\s,;\"'`]+")
_POLICY_DEFINITIONS = re.compile(r"^## Policy definitions.*?(?=^## )", re.M | re.S)

# A paragraph mentioning the removed origin must DECLARE it removed/superseded. Deliberately
# precise tokens: a bare "manual" would clear the imperative at operational consequence #2
# (the paragraph says the audit "is run manually"), which is exactly one of the three spots
# this guard exists to catch.
_REMOVAL_MARKERS = (
    "corrected",
    "superseded",
    "supersedes",
    "removed",
    "not name",
    "manual embed",
    "manual-embed",
    "never needed",
    "measured wrong",
    "same-origin",
)

# The reversed mutation claim: a *dropped* RUM origin described as the mutant that fails.
# POLARITY, not proximity (#8334 review, Finding 2): the dropped thing must BE the RUM
# origin. The predecessor `dropped[^.\n]{0,60}RUM origin` measured proximity across any
# 60 non-period characters and excluded newlines, so (a) joining the doc's CORRECT sentence
# onto one line matched it — a semantics-neutral reflow made the guard red on right text —
# and (b) a genuinely wrong claim wrapped between `dropped` and `RUM origin` did not match.
# The optional filler words are the ones the claim itself uses; `re-added` is deliberately
# absent, so the corrected sentence cannot match through it.
_DROPPED_RUM_MUTANT = re.compile(
    r"dropped\s+(?:(?:beacon|a|the|or)\s+)*(?:third-party\s+)?RUM\s+origin", re.I)


def _calls_a_dropped_rum_origin_the_mutant(text: str) -> bool:
    """True when `text` names a DROPPED RUM origin as an executed failing mutant.

    All whitespace — including the line wrap a markdown reflow introduces — is collapsed
    before matching, so the same sentence wrapped differently gets the same verdict.
    """
    return bool(_DROPPED_RUM_MUTANT.search(re.sub(r"\s+", " ", text)))


def _connect_src_values(text: str) -> list[str]:
    """Every `connect-src` directive value in `text` (the directive's sources, not parsed)."""
    return _CONNECT_SRC.findall(text)


def _connect_src_hosts(text: str) -> set[str]:
    """The origin sources named by every `connect-src` directive in `text`."""
    return {h.rstrip(",;") for value in _connect_src_values(text) for h in _HOST.findall(value)}


def _policy_definitions_section(text: str) -> str:
    match = _POLICY_DEFINITIONS.search(text)
    assert match, (
        "the plan doc has no `## Policy definitions` section — this guard's subject was "
        "renamed or deleted, so it would pass vacuously (#8334 review)")
    return match.group(0)


def _shipped_connect_src_hosts() -> set[str]:
    hosts: set[str] = set()
    for module in SHIPPED_MODULES:
        hosts |= _connect_src_hosts(module.read_text(encoding="utf-8"))
    return hosts


# A fenced-block directive in the doc: `name value;` (or a last line with no `;`).
_DOC_DIRECTIVE = re.compile(r"^([a-z-]+)\s+([^;\n]+)", re.M)
_DOC_FENCE = re.compile(r"```\n(.*?)```", re.S)
# The `**`POLICY_NAME`**` bold label that introduces a fenced block.
_BOLD_POLICY = re.compile(r"\*\*`(\w+)`\*\*")
# A `export const NAME = csp(...)` binding and the string arguments it joins.
_MODULE_CSP_BINDING = re.compile(r"export const (\w+) = csp\((.*?)\n\);", re.S)
_MODULE_DIRECTIVE = re.compile(r'"([a-z-]+)\s+([^"]*)"')


def _doc_policy_blocks(text: str) -> dict[str, dict[str, frozenset[str]]]:
    """{policy name: {directive: token set}} for every fenced block in `text`.

    A block is attributed to the nearest `**`POLICY_NAME`**` label before it. Directives
    absent from a block are not invented — `ADMIN_CSP` is documented as a diff from
    `STRICT_CSP`, so its block carries only the two directives that differ.
    """
    out: dict[str, dict[str, frozenset[str]]] = {}
    for fence in _DOC_FENCE.finditer(text):
        labels = _BOLD_POLICY.findall(text[: fence.start()])
        if not labels:
            continue
        directives = {
            name: frozenset(value.split())
            for name, value in _DOC_DIRECTIVE.findall(fence.group(1))
        }
        if directives:
            out[labels[-1]] = directives
    return out


def _module_policy_blocks(module: Path) -> dict[str, dict[str, frozenset[str]]]:
    """{exported policy name: {directive: token set}} for every `csp(...)` const."""
    out: dict[str, dict[str, frozenset[str]]] = {}
    text = module.read_text(encoding="utf-8")
    for name, body in _MODULE_CSP_BINDING.findall(text):
        directives = {
            directive: frozenset(value.split())
            for directive, value in _MODULE_DIRECTIVE.findall(body)
        }
        if directives:
            out[name] = directives
    return out


def _paragraphs_asserting_the_removed_origin(text: str) -> list[str]:
    """Paragraphs naming `RUM_ORIGIN` without declaring it removed/superseded."""
    out = []
    for para in text.split("\n\n"):
        if RUM_ORIGIN not in para:
            continue
        if any(marker in para.lower() for marker in _REMOVAL_MARKERS):
            continue
        out.append(para.strip().splitlines()[0])
    return out


# ---------------------------------------------------------------------------
# 1. Derived: the doc's shipped-strings block may only name connect-src hosts
#    the shipped modules actually allow.
# ---------------------------------------------------------------------------

def test_shipped_strings_block_names_no_host_the_modules_dropped():
    doc = PLAN_DOC.read_text(encoding="utf-8")
    section = _policy_definitions_section(doc)
    doc_hosts = _connect_src_hosts(section)
    module_hosts = _shipped_connect_src_hosts()

    # Non-vacuity: both sides must actually carry the directives and the Supabase host
    # every policy pairs with, or an empty parse would satisfy a subset assertion.
    assert len(_connect_src_values(section)) >= 3, (
        f"only {len(_connect_src_values(section))} `connect-src` directives parsed out of the "
        "`## Policy definitions` fenced blocks — the blocks were reflowed and this guard is "
        "now reading nothing")
    assert "https://*.supabase.co" in doc_hosts, f"parsed doc hosts look wrong: {sorted(doc_hosts)}"
    assert "https://*.supabase.co" in module_hosts, (
        f"parsed module hosts look wrong: {sorted(module_hosts)}")

    extra = sorted(doc_hosts - module_hosts)
    assert not extra, (
        "the plan doc's `## Policy definitions` block — the strings the doc labels \"the actual "
        "strings this change ships\" — names connect-src host(s) no shipped policy allows: "
        f"{extra}. A lane copying these blocks ships the drift. If the origin is deliberately "
        "re-added, re-add it to the modules and their _headers copies in the same commit "
        "(securityHeaders.test.js §4b reddens otherwise), and correct this block with it.")


# ---------------------------------------------------------------------------
# 1b. Derived, whole-directive: a doc block must MATCH the policy it names, so a
#     dropped (or added) source in ANY directive reddens — not only `connect-src`.
# ---------------------------------------------------------------------------

def test_every_doc_policy_block_agrees_with_the_shipped_policy_it_names():
    """The doc's copyable blocks must match the module policy they name, directive for
    directive (#8334 review, Finding 4).

    Test 1 compares only `connect-src` HOST SETS, so a `script-src` that dropped a shipped
    origin could not redden it. Measured at the PR head: the doc's `RELAXED_CSP` block was
    missing `https://static.ads-twitter.com` — the #7633 consent-gated X tag both shipped
    constants carry — so the banner's "a lane that copies these blocks copies the CURRENT
    strings" was false, and a copying lane would have shipped the #7633 defect with every
    gate green. Only the directives PRESENT in a doc block are compared (the `ADMIN_CSP`
    block documents a diff; the nonce policy is prose, not a fence), so a deliberately
    partial block stays legal while a silent omission inside it does not.
    """
    doc = PLAN_DOC.read_text(encoding="utf-8")
    section = _policy_definitions_section(doc)
    doc_blocks = _doc_policy_blocks(section)
    assert set(doc_blocks) >= {"RELAXED_CSP", "STRICT_CSP", "ADMIN_CSP"}, (
        f"parsed doc policy blocks: {sorted(doc_blocks)} — the `**`NAME`**` labels or the "
        "fences were reflowed and this guard is reading nothing")
    shipped_policies = _module_policy_blocks(SHIPPED_MODULES[0])
    for name, directives in doc_blocks.items():
        assert name in shipped_policies, (
            f"the doc documents a policy `{name}` that `{SHIPPED_MODULES[0].name}` does not "
            f"export (exports: {sorted(shipped_policies)})")
        shipped = shipped_policies[name]
        for directive, tokens in directives.items():
            assert directive in shipped, (
                f"`{name}` in the doc carries a `{directive}` directive the shipped policy "
                "does not")
            assert tokens == shipped[directive], (
                f"`{name}.{directive}` in the plan doc differs from the shipped policy: "
                f"doc-only={sorted(tokens - shipped[directive])} "
                f"shipped-only={sorted(shipped[directive] - tokens)}. The doc labels this "
                "section the actual strings the change ships and a lane copies from it.")


# ---------------------------------------------------------------------------
# 2. Prose: every remaining mention of the removed origin must declare it removed.
# ---------------------------------------------------------------------------

def test_no_plan_doc_passage_asserts_the_removed_origin_as_current():
    doc = PLAN_DOC.read_text(encoding="utf-8")
    offenders = _paragraphs_asserting_the_removed_origin(doc)
    assert not offenders, (
        f"the #3525 plan doc still asserts `{RUM_ORIGIN}` as current in paragraph(s) starting: "
        f"{offenders}. #4706 measured the RUM POST as same-origin `/cdn-cgi/rum` and removed "
        "this origin from every policy; a passage that keeps asserting it (an imperative, a "
        "policy block, a table row) is the stale record the next lane copies. Correct it in "
        "place, or declare the passage superseded by the 2026-10-12 addendum.")


# ---------------------------------------------------------------------------
# 3. Polarity: a *dropped* RUM origin is not the failing mutant.
# ---------------------------------------------------------------------------

def test_plan_doc_does_not_call_a_dropped_rum_origin_a_failing_mutant():
    doc = PLAN_DOC.read_text(encoding="utf-8")
    match = _DROPPED_RUM_MUTANT.search(re.sub(r"\s+", " ", doc))
    assert not match, (
        "the mutation-evidence list still names a DROPPED RUM origin as an executed failing "
        f"mutant: {match.group(0)!r}. Dropping it is the correct state (#4706); the mutant "
        "that must redden is a RE-ADDED one — which is exactly what this change's own "
        "mutation evidence reports.")


# ---------------------------------------------------------------------------
# 4. Non-vacuity: the helpers red on the pre-fix text, recorded verbatim.
# ---------------------------------------------------------------------------

# The pre-fix text at d413f2f91, copied from the doc as it stood (the commit this PR
# reviewed). Kept as literals so non-vacuity stays provable after the doc is corrected —
# `__file__`-relative reading of the live doc cannot show the guard ever failed.
_PREFIX_DOC_FRAGMENT = """## Policy definitions (the actual strings this change ships)

**`STRICT_CSP`** — the dashboard SPA document (`/`, `/team`, `/team/`, `/index.html`
overrides in `website/apps/dashboard/public/_headers`):

```
default-src 'self';
script-src 'self' https://static.cloudflareinsights.com;
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:;
font-src 'self' data:;
connect-src 'self' https://cloudflareinsights.com;
object-src 'none';
base-uri 'none';
frame-ancestors 'none';
form-action 'self'
```

## Production finding (post-merge) — the edge-injected Cloudflare beacon

2. **Every policy must name the beacon's script origin and its RUM endpoint**
   (`https://cloudflareinsights.com`, per the beacon body itself).
   The beacon test (§4b) in `securityHeaders.test.js` now enforces exactly that;"""

_PREFIX_MUTANT = """check class below has an EXECUTED failing mutant — a dropped beacon or RUM origin in any of
the five policies, a look-alike hostname"""

# The CORRECTED mutation sentence from the live doc, joined onto one line — the semantics-
# neutral reflow that made the predecessor pattern match right text (false-positive control,
# Finding 2).
_CORRECTED_MUTANT_REFLOWED = (
    "check class below has an EXECUTED failing mutant — a dropped beacon origin or a "
    "**re-added** third-party RUM origin in any of the five policies, a look-alike hostname")

# Genuinely wrong claims that WRAP between `dropped` and `RUM origin` — the false negatives
# the predecessor pattern's `[^.\n]` character class admitted (Finding 2).
_WRAPPED_BAD_MUTANT_CLAIMS = (
    "check class below has an EXECUTED failing mutant — a dropped\nRUM origin in any of "
    "the five policies, a look-alike hostname",
    "check class below has an EXECUTED failing mutant — a dropped beacon or\nRUM origin in "
    "any of the five policies, a look-alike hostname",
)

# The doc's `RELAXED_CSP` `script-src` VALUE at head 573659693 — verbatim, before Finding 4's
# fix. Kept as a literal so the omission's non-vacuity stays provable after the doc is
# corrected.
_PREFIX_RELAXED_SCRIPT_SRC = (
    "'self' 'unsafe-inline' https://static.cloudflareinsights.com "
    "https://cdnjs.cloudflare.com https://us-assets.i.posthog.com "
    "https://www.googletagmanager.com https://connect.facebook.net "
    "https://challenges.cloudflare.com")


def test_the_derived_host_set_check_reds_on_the_prefix_policy_block():
    section = _policy_definitions_section(_PREFIX_DOC_FRAGMENT)
    doc_hosts = _connect_src_hosts(section)
    assert RUM_ORIGIN in doc_hosts, "the pre-fix block must name the removed origin"
    assert doc_hosts - _shipped_connect_src_hosts(), (
        "the pre-fix policy block must fall outside the shipped host set — if this passes, "
        "the derived check in test 1 is vacuous")


def test_the_disclaimer_check_reds_on_the_prefix_passages():
    offenders = _paragraphs_asserting_the_removed_origin(_PREFIX_DOC_FRAGMENT)
    assert len(offenders) == 2, (
        f"both recorded pre-fix passages (the fenced STRICT_CSP block and the imperative) "
        f"must be flagged, got {offenders!r} — the marker list has drifted and the guard in "
        "test 2 no longer bites")


def test_the_polarity_check_reds_on_the_prefix_mutant_sentence():
    assert _calls_a_dropped_rum_origin_the_mutant(_PREFIX_MUTANT), (
        "the recorded pre-fix mutant sentence must match — the pattern in test 3 has drifted")


def test_the_polarity_check_ignores_the_corrected_sentence_reflowed():
    assert not _calls_a_dropped_rum_origin_the_mutant(_CORRECTED_MUTANT_REFLOWED), (
        "the guard matches the CORRECT sentence (\"...a dropped beacon origin or a "
        "**re-added** third-party RUM origin...\") once it is joined onto one line — the "
        "verdict is decided by a line wrap, not by the sentence's polarity")


def test_the_polarity_check_reds_on_a_wrapped_wrong_claim():
    for claim in _WRAPPED_BAD_MUTANT_CLAIMS:
        assert _calls_a_dropped_rum_origin_the_mutant(claim), (
            "a genuinely wrong claim that wraps between `dropped` and `RUM origin` evaded "
            f"the guard: {claim!r}")


def test_the_directive_comparison_reds_on_the_prefix_relaxed_script_src():
    """Recorded pre-fix control: the head's `RELAXED_CSP` block omitted the shipped
    `https://static.ads-twitter.com`, which is exactly what the 1b comparison reds on."""
    shipped = _module_policy_blocks(SHIPPED_MODULES[0])["RELAXED_CSP"]["script-src"]
    doc_tokens = frozenset(_PREFIX_RELAXED_SCRIPT_SRC.split())
    assert doc_tokens != shipped, (
        "the recorded pre-fix block must differ from the shipped policy")
    assert "https://static.ads-twitter.com" in shipped - doc_tokens, (
        "the recorded pre-fix block must be missing the shipped ads-twitter origin — if "
        "not, the 1b comparison is not the check that catches this omission")


# ---------------------------------------------------------------------------
# 5. The doc's named indirection-evasion spelling must be one that EVADES.
#
# The `.set`/`.append`-through-an-indirection residual is real, but the doc named a
# DOUBLE-quoted computed member call (`h["set"](…)`) as the spelling that evades the
# DIRECT member-call readers. It does not: `headerCallMethod` returns
# `nameOf(callee.property)`, and `nameOf` carries a `StringLiteral` branch, so
# `h["set"](…)` is the same reader hit as `h.set(…)`. Measured with the reader file's own
# helpers (`@babel/parser` + `nameOf`/`headerCallMethod` verbatim):
#
#     h["set"]("Content-Security-Policy", P)   -> [["set", "content-security-policy"]]  // READ
#     h[`set`]("Content-Security-Policy", P)   -> []                                    // evades
#
# The spelling that DOES evade is the TEMPLATE one — `nameOf` has no `TemplateLiteral`
# branch — which is what the reader's own docstring names (`securityHeaders.test.js`,
# `headerCallMethod`) and what the clause said before this PR reworded it.
# ---------------------------------------------------------------------------

# The residual clause itself — substantive enough that losing it must red this guard (the
# gap is real and must stay recorded), unlike losing the callee example solely.
_INDIRECTION_RESIDUAL = re.compile(r"reached through an indirection")
_INDIRECTION_CLAUSE = re.compile(r"an indirect callee\s+(?P<example>\S+)", re.I)
# A computed member key written as a STRING LITERAL — read by the guard's `nameOf`.
_DOUBLE_QUOTED_COMPUTED_KEY = re.compile(r'\[\s*"[A-Za-z_$][\w$]*"\s*\]')


def _indirection_evasion_example(text: str) -> str | None:
    """The callee spelling the doc names in its `an indirect callee <x>` slot, or None
    when that slot (or the residual clause) is absent."""
    match = _INDIRECTION_CLAUSE.search(re.sub(r"\s+", " ", text))
    return match.group("example") if match else None


def test_plan_doc_indirection_example_names_only_a_spelling_that_evades():
    flat = re.sub(r"\s+", " ", PLAN_DOC.read_text(encoding="utf-8"))
    assert _INDIRECTION_RESIDUAL.search(flat), (
        "the doc no longer records the `.set`/`.append`-through-an-indirection residual "
        "(`reached through an indirection … still evades the DIRECT member-call readers`) — "
        "the true completeness gap is now silent and this guard is reading nothing")
    example = _indirection_evasion_example(flat)
    if example is None:
        return  # no callee example named — nothing here can be wrong about one
    assert not _DOUBLE_QUOTED_COMPUTED_KEY.search(example), (
        f"the doc names {example!r} as an indirect callee that EVADES the readers, but a "
        "double-quoted computed member key is READ (`headerCallMethod` → `nameOf`, whose "
        "`StringLiteral` branch handles it) — `h[\"set\"](…)` is the same hit as "
        "`h.set(…)`. Name the TEMPLATE spelling (the branch `nameOf` lacks), or drop the "
        "example.")


# ---------------------------------------------------------------------------
# 6. The stamp-shape residual must not claim a shape the scan now models.
#
# Gap 5 (the 2026-10-13 addendum) added the array-of-pairs `HeadersInit` pair to
# `countStamps` and `stampNames`, so the `## Residuals` bullet that called the scan "three
# shapes" and said such a pair "is not seen by it" asserts a hole the guard closes — the
# stale-record class the doc's own addenda resolve with an explicit supersede marker.
# A bullet naming `array-of-pairs` that still carries an unseen-claim must therefore
# declare the closure; the residual that legitimately remains is the narrower
# `[[<unreadable name>, <policy>]]` pair the addendum's "Bounds that remain" names.
# ---------------------------------------------------------------------------

# One markdown bullet (a `- **…` item up to the next bullet or blank line), so a claim is
# judged against its own bullet and not against a neighbouring residual.
_BULLET = re.compile(r"^- \*\*.*?(?=\n- |\n\n|\Z)", re.M | re.S)
_ARRAY_OF_PAIRS_SHAPE = re.compile(r"array[- ]of[- ]pairs", re.I)
_UNSEEN_SHAPE_CLAIM = re.compile(
    r"(?:is|are)\s+not\s+(?:seen|modelled|modeled|counted|attributed)"
    r"|not\s+seen\s+by\s+it", re.I)
# Deliberately precise: a bare "modelled"/"seen" would clear a claim that a shape is NOT
# modelled. These tokens say the array-of-pairs shape itself is closed/superseded.
_SHAPE_CLOSURE_MARKERS = (
    "attribution-only",
    "attribution only",
    "superseded",
    "supersedes",
    "2026-10-13",
    "fourth",
    "now counted",
    "now attributed",
)


def _bullets_claiming_the_array_of_pairs_shape_is_invisible(text: str) -> list[str]:
    """Bullets naming the array-of-pairs `HeadersInit` shape as unseen, without declaring
    it modelled/superseded."""
    out = []
    for bullet in _BULLET.findall(text):
        if not _ARRAY_OF_PAIRS_SHAPE.search(bullet):
            continue
        if not _UNSEEN_SHAPE_CLAIM.search(bullet):
            continue
        if any(marker in bullet.lower() for marker in _SHAPE_CLOSURE_MARKERS):
            continue
        out.append(re.sub(r"\s+", " ", bullet).strip())
    return out


def test_no_plan_doc_bullet_claims_the_array_of_pairs_shape_is_invisible():
    doc = PLAN_DOC.read_text(encoding="utf-8")
    offenders = _bullets_claiming_the_array_of_pairs_shape_is_invisible(doc)
    assert not offenders, (
        "the #3525 plan doc still claims the array-of-pairs `HeadersInit` shape is not seen "
        f"by the stamp scan: {offenders}. Gap 5 (the 2026-10-13 addendum) added that shape "
        "to `countStamps`/`stampNames` and the reviewer mutation reds, so the passage "
        "asserts a hole the guard closes. Strike the claim, or mark it superseded by the "
        "addendum.")


# ---------------------------------------------------------------------------
# 4b. Non-vacuity for 5 and 6: the recorded pre-fix text reds, and a corrected
#     "still not seen" residual does not (marker, not absence of the claim).
# ---------------------------------------------------------------------------

# The pre-fix `## Residuals` bullet at a34745afa5 — verbatim, before this fix.
_PREFIX_THREE_SHAPES_BULLET = """- **The stamp scan models three shapes** — a `Content-Security-Policy` object property
  (literal or computed key), a `headers.set/append` argument, and an
  `h["Content-Security-Policy"] = …` assignment — so a second policy added as an
  array-of-pairs `HeadersInit` (`new Headers([...headers, ["Content-Security-Policy", P]])`)
  is not seen by it. A browser intersects the two policies, so that shape would narrow the
  served policy with the guard green. Accepted as a textual-scan bound (verified by a
  reviewer mutation), not a closure: the scan is a tripwire for the `_headers`/module drift
  this change is about."""

# The pre-fix indirection clause at a34745afa5 — verbatim. Its callee example was a
# double-quoted computed member call, which the readers DO read.
_PREFIX_INDIRECTION_CLAUSE = """a `.set`/`.append` reached through an indirection
  (an indirect callee `h["set"](…)`, a destructured `const { set } = h`, or `.call`/`.apply`/`.bind`) —
  including a `.bind`-ed
  beacon-less CSP stamp merged into a served response, which was verified green — still evades
  the DIRECT member-call readers"""


def test_the_shape_count_guard_reds_on_the_prefix_three_shapes_bullet():
    offenders = _bullets_claiming_the_array_of_pairs_shape_is_invisible(
        _PREFIX_THREE_SHAPES_BULLET)
    assert offenders, (
        "the recorded pre-fix `three shapes` bullet must be flagged — the unseen-claim "
        "pattern or the marker list has drifted and the guard in test 6 no longer bites")


def test_the_shape_count_guard_clears_a_bullet_that_declares_the_closure():
    """False-positive control: a corrected bullet still says the narrower residual "is not
    seen", and must be cleared by its marker, not by the absence of the claim."""
    corrected = (
        "- **The stamp scan models four shapes** — the array-of-pairs `HeadersInit` pair is "
        "the fourth (added by the 2026-10-13 addendum); what is not seen is an "
        "`[[<unreadable name>, <policy>]]` pair.")
    assert not _bullets_claiming_the_array_of_pairs_shape_is_invisible(corrected), (
        "a bullet that declares the array-of-pairs closure must not be flagged — the guard "
        "is red on right text")


def test_the_indirection_example_guard_reds_on_the_prefix_clause():
    example = _indirection_evasion_example(_PREFIX_INDIRECTION_CLAUSE)
    assert example is not None, (
        "the recorded pre-fix clause must still parse — the extraction pattern has drifted")
    assert _DOUBLE_QUOTED_COMPUTED_KEY.search(example), (
        "the recorded pre-fix clause must name a double-quoted computed callee — if not, "
        "the guard in test 5 is not the check that catches this defect")


# ---------------------------------------------------------------------------
# 7. The addendum's baseline claim keeps the reproduce command, not a suite total.
#
# "Baseline after the fix: 18/18 … the whole dashboard suite 877/877" was measured at the
# pre-merge head a34745afa5. The branch then merged origin/main (2fd5083bf) at an unchanged
# tree, which added 3 tests to src/*.test.js: 877 → 880, while the guard's own 18 did not
# move. A whole-suite total is the evidence class that re-stales; the COMMAND is the part the
# next lane reproduces. This guard is the doc-side tripwire (#8367 review, Finding 1).
# ---------------------------------------------------------------------------

_BASELINE_CLAIM = re.compile(r"^Baseline after the fix:.*?(?=\n\n|\Z)", re.M | re.S)
_DASHBOARD_SUITE_COMMAND = "npm run build && node --test src/*.test.js"
# A whole-suite pass TOTAL ("dashboard suite 877/877") — digits joined by a slash, optionally
# bolded. The guard's own `18/18` is not this shape (no suite/dashboard prefix) and must
# survive: the mutation table's "1 fail, 17/18" columns are keyed to it.
_DASHBOARD_SUITE_TOTAL = re.compile(
    r"(?:dashboard|suite)[^.\n]{0,60}?\*{0,2}\d{2,4}\s*/\s*\d{2,4}\*{0,2}", re.I)


def _baseline_claim(text: str) -> str:
    match = _BASELINE_CLAIM.search(text)
    assert match, (
        "the addendum has no `Baseline after the fix:` claim — this guard's subject was "
        "renamed or deleted, so it would pass vacuously (#8367 review)")
    return match.group(0)


def _pins_a_dashboard_suite_total(text: str) -> re.Match:
    """The whole-suite total in `text`, or None. Whitespace is collapsed first so a
    markdown reflow cannot hide the count."""
    return _DASHBOARD_SUITE_TOTAL.search(re.sub(r"\s+", " ", text))


def test_baseline_claim_keeps_the_command_and_pins_no_suite_total():
    paragraph = _baseline_claim(PLAN_DOC.read_text(encoding="utf-8"))
    assert _DASHBOARD_SUITE_COMMAND in paragraph, (
        "the baseline claim no longer names the CI job's own command "
        f"(`{_DASHBOARD_SUITE_COMMAND}`), so the next lane cannot reproduce it")
    offender = _pins_a_dashboard_suite_total(paragraph)
    assert not offender, (
        "the baseline claim pins a whole-suite pass total "
        f"({offender.group(0)!r}). That number re-stales at every merge of `origin/main` at "
        "an unchanged tree (877 before the `2fd5083bf` merge, 880 after), which is exactly "
        "what the #8367 review flagged. Keep the command; do not pin the count.")


# The pre-fix baseline claim at 203a16812 — verbatim, before this fix.
_PREFIX_BASELINE_CLAIM = (
    "Baseline after the fix: **18/18** in `securityHeaders.test.js`; the whole dashboard suite\n"
    "**877/877** (`npm run build && node --test src/*.test.js`).")


def test_baseline_suite_total_guard_reds_on_the_prefix_claim():
    assert _pins_a_dashboard_suite_total(_PREFIX_BASELINE_CLAIM), (
        "the recorded pre-fix claim (`the whole dashboard suite **877/877**`) must match — "
        "the pattern has drifted and the guard in section 7 is vacuous")


def test_baseline_suite_total_guard_clears_the_corrected_claim():
    assert not _pins_a_dashboard_suite_total(
        _baseline_claim(PLAN_DOC.read_text(encoding="utf-8"))), (
        "the corrected baseline claim still carries a suite total — the guard is red on "
        "right text")


# ---------------------------------------------------------------------------
# 8. `### Bounds that remain` must name the gap-3 umbrella's attribution-only bound.
#
# The umbrella keys on `attributableStamps()` = `stampNames` minus the `(unreadable header
# name)` sentinel, and `countStamps` runs only over `HTML_SITES`. So a stamp whose header
# NAME is an expression (`h.set(HDR, …)`) in a file outside `HTML_SITES` is neither counted
# nor classified — the same deliberate bound the array-of-pairs arm draws, and NOT named in
# the bounds list, so gap 3 reads as though the umbrella covers every stamp shape. The code
# arm is deliberately absent (a fail-closed arm would red the non-`HTML_SITES` files that
# stamp no header), which is exactly why the BOUND has to be the recorded artifact.
# ---------------------------------------------------------------------------

_BOUNDS_SECTION = re.compile(r"^### Bounds that remain.*?(?=\n### |\n## |\Z)", re.M | re.S)
_GAP3_UMBRELLA = re.compile(r"umbrella", re.I)
_UNREADABLE_HEADER_NAME = re.compile(
    r"unreadable[ -](?:header[ -])?name"
    r"|header\s+name\s+is\s+an\s+expression"
    r"|h\.set\(HDR", re.I)


def _bounds_section(text: str) -> str:
    match = _BOUNDS_SECTION.search(text)
    assert match, (
        "the plan doc has no `### Bounds that remain` section — this guard's subject was "
        "renamed or deleted, so it would pass vacuously (#8367 review)")
    return match.group(0)


def _bounds_listing_the_gap3_umbrella(section: str) -> list[str]:
    """Bullets that name the gap-3 umbrella bound AND the unreadable-header-name trigger."""
    return [
        bullet for bullet in _BULLET.findall(section)
        if _GAP3_UMBRELLA.search(bullet) and _UNREADABLE_HEADER_NAME.search(bullet)
    ]


def test_bounds_section_names_the_gap3_umbrella_attribution_bound():
    section = _bounds_section(PLAN_DOC.read_text(encoding="utf-8"))
    bullets = _BULLET.findall(section)
    assert len(bullets) >= 3, (
        f"only {len(bullets)} bullets parsed out of `### Bounds that remain` — the section "
        "was reflowed and this guard is reading nothing")
    assert _bounds_listing_the_gap3_umbrella(section), (
        "the `### Bounds that remain` list does not name the gap-3 umbrella's "
        "ATTRIBUTION-ONLY bound: `countStamps` runs only over `HTML_SITES` and the umbrella "
        "keys on `attributableStamps()`, so a stamp whose header NAME is an expression "
        "(`h.set(HDR, …)`) in a file outside `HTML_SITES` is neither counted nor "
        "classified. The bound is deliberate (a fail-closed arm would red the "
        "non-`HTML_SITES` files that stamp no header), but unnamed it reads as though the "
        "umbrella covers every stamp shape. Record it here the way the array-of-pairs bound "
        "is recorded.")


# The pre-fix `### Bounds that remain` bullets at 203a16812 — abbreviated, first lines
# verbatim. None ties the umbrella to an unreadable header name; the array-of-pairs bullet
# says "unreadable FIRST ELEMENT" (no `name`) and the section never says "umbrella".
_PREFIX_BOUNDS_BULLETS = (
    "- **The array-of-pairs arm is ATTRIBUTION-ONLY — there is no fail-closed arm for an "
    "unreadable\n  first element.** Measured on this tree, the fail-closed form adds +3 to "
    "`blog/_lib.ts`.\n"
    "- **`metaCspValues` reads SOURCE `.html` files textually, `<meta>` tag by tag.** A "
    "policy\n  assembled by concatenating fragments across tags is outside it.\n"
    "- **The gated-surface bound is not yet equal to the source-scan bound.** A marketing\n"
    "  `.html`-only diff does NOT currently select the `dashboard` gate.\n")

# The bound as it reads once named (the corrected-bullet control).
_NAMED_GAP3_UMBRELLA_BOUND = (
    "- **The gap-3 umbrella is ATTRIBUTION-ONLY — a stamp whose header NAME is an "
    "expression is neither counted nor classified outside `HTML_SITES`.** `countStamps` "
    "still runs only over the six `HTML_SITES` files.\n")


def test_the_gap3_umbrella_bound_guard_reds_on_the_prefix_bounds_list():
    assert not _bounds_listing_the_gap3_umbrella(_PREFIX_BOUNDS_BULLETS), (
        "the recorded pre-fix `Bounds that remain` list must NOT name the gap-3 umbrella "
        "bound — if it does, the guard in section 8 is not the check that catches this "
        "defect")


def test_the_gap3_umbrella_bound_guard_clears_the_named_bound():
    assert _bounds_listing_the_gap3_umbrella(_NAMED_GAP3_UMBRELLA_BOUND), (
        "a bullet that names the gap-3 umbrella bound and its unreadable-header-name "
        "trigger must clear — the guard is red on right text")
