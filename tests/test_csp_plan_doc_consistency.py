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
4. A DOCS-ONLY diff selects NO surface at all (#4454 — `docs/` is in `NON_PYTHON_PREFIXES`),
   so a PR that edits only this plan doc does not run this guard. It runs on the next diff
   that touches a policy module, the test file itself, or a full-matrix run — and the
   code-side half of the same invariant (`securityHeaders.test.js` §4b) is what a re-added
   origin actually reddens at merge time.
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
_DROPPED_RUM_MUTANT = re.compile(r"dropped[^.\n]{0,60}RUM origin", re.I)


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
    match = _DROPPED_RUM_MUTANT.search(doc)
    assert not match, (
        "the mutation-evidence list still names a DROPPED RUM origin as an executed failing "
        f"mutant: {match.group(0)!r}. Dropping it is the correct state (#4706); the mutant "
        "that must redden is a RE-ADDED one — which is exactly what this change's own "
        "mutation evidence reports.")


# ---------------------------------------------------------------------------
# 4. Non-vacuity: the three helpers red on the pre-fix text, recorded verbatim.
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
    assert _DROPPED_RUM_MUTANT.search(_PREFIX_MUTANT), (
        "the recorded pre-fix mutant sentence must match — the pattern in test 3 has drifted")
