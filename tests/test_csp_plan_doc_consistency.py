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
