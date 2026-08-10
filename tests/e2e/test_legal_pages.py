"""Playwright E2E suite for the legal pages (#657) — negation-safe verification gate.

Covers plan checks #1-#10 (T7 of docs/plans/2026-08-08-657-legal-pages-plan.md):

  #1  /privacy 200 + negation-safe canonical block
  #2  /tos 200 + negation-safe block + eligibility + carve-outs + dollar guard
      (== 1, the "$5M" AUG) + fees keywords + Pricing Page hyperlink
  #3  /license + /dpa 200 (both REQUIRED — G-gate ③/⑨ LOCKED)
  #4  footer legal links on product/welcome/signup/signin/self-hosted
      (BASE_URL half unconditional; TORTISE_HOST half gated on TORTISE_HOST_CHECK)
  #5  cross-host /privacy + /tos 200 (gated)
  #6  middleware root rewrites — tortoise.* → product marker,
      premiselabs.co → index marker (gated)
  #7  same-viewport acceptance (.legal-accept vs .providers + #btn-submit at
      1280x720 AND 375x667) + DOM parentage + mocked email signup with
      mandatory inbox-state + mocked GitHub OAuth click
  #8  link crawl — enumerated page set final-200 + tortoise root (gated) +
      third-party external links
  #9  mobile render @375px — no horizontal scroll + minimum content + headings
  #10 served-content assertions — revision history, consent-banner sentence,
      repo-state IFF local tree markers, per-tool conditional framing,
      consent.js banner presence, placeholder-leak regexes (broad + D4-shape),
      present-tense guard == pinned expected subset, full-document tag balance
      + section titles, draft↔render fidelity, effective-date format once per
      legal page

Harness contract (cycle-4 P1-1, pinned):
  - RUN_LEGAL_E2E=1 REQUIRED — the FIRST executable statement is a runtime
    module skip; bare collection of this module NEVER errors (skip is the
    guaranteed outcome on every surface that collects tests/e2e/ without it).
    NEVER pytest.exit() — it aborts the whole pytest session.
  - ALLOW_PROD=1 required to point BASE_URL/TORTISE_HOST at https:// URLs
    (no production assertions pre-merge; local runs pass http://127.0.0.1).
  - TORTISE_HOST_CHECK == "1" gates the tortoise.* tests (#4 tortoise-host
    half, #5, #6, #8) — the post-deploy CI job (follow-up #677) sets it only
    when its DNS preflight is green; a skipped tortoise.* test is
    GREEN-WITH-ANNOTATION by design.

Run locally against a wrangler pages dev preview:
  cd website && npx wrangler@4 pages dev . --port 8788 --ip 127.0.0.1
  RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 \
    TORTISE_HOST=http://127.0.0.1:8788 TORTISE_HOST_CHECK=1 \
    python -m pytest tests/e2e/test_legal_pages.py -v

Run against production (manual post-deploy verification — the repo's
pages deploy workflow deploy-pages.yml has NO post-deploy job; the CI
post-deploy job is tracked as follow-up #677):
  RUN_LEGAL_E2E=1 ALLOW_PROD=1 BASE_URL=https://premiselabs.co \
    TORTISE_HOST=https://tortoise.premiselabs.co \
    python -m pytest tests/e2e/test_legal_pages.py -v
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

# ── Opt-in short-circuit (cycle-4 P1-1) — FIRST executable statement. ──────
# A plain module-level runtime call: pytest.skip() in module code skips the
# whole module at collection WITHOUT erroring. NEVER pytest.exit().
if not os.environ.get("RUN_LEGAL_E2E"):
    pytest.skip("legal suite: opt-in via RUN_LEGAL_E2E=1", allow_module_level=True)

# ── Harness guard (cycle-4 P1-1) — refuse production URLs pre-merge. ───────
BASE_URL = os.environ.get("BASE_URL", "https://premiselabs.co")
TORTISE_HOST = os.environ.get("TORTISE_HOST", "https://tortoise.premiselabs.co")
if (BASE_URL.startswith("https://") or TORTISE_HOST.startswith("https://")) and os.environ.get("ALLOW_PROD") != "1":
    pytest.skip(
        "no production assertions pre-merge — set ALLOW_PROD=1 to test production",
        allow_module_level=True,
    )

# ── Lazy playwright import — bare collection must never error. ─────────────
from playwright.sync_api import Page, expect  # noqa: E402

# ── Tortoise-host check contract (cycle-4 P2-1). ───────────────────────────
TORTISE_HOST_CHECK = os.environ.get("TORTISE_HOST_CHECK") == "1"

# ── Third-party external-link crawl gate (cycle-8 P1, #747 CI) ────────────
# The crawl fetches unauthenticated third-party hrefs (github.com/LICENSE,
# etc.). From shared CI runner IPs those hosts rate-limit aggressively (429
# persists past the test's single retry) → nondeterministic pre-merge gate.
# CI sets LEGAL_E2E_SKIP_EXTERNAL_CRAWL=1 (skip = green-with-annotation,
# same pattern as the tortoise-host deferral); local runs keep the full crawl.
EXTERNAL_CRAWL_SKIP = pytest.mark.skipif(
    os.environ.get("LEGAL_E2E_SKIP_EXTERNAL_CRAWL") == "1",
    reason="third-party external-link crawl disabled (CI: unauthenticated "
    "third-party fetches from shared runner IPs are rate-limit flaky) — "
    "run locally for the full crawl",
)


def _tortoise_emulated_locally() -> bool:
    """Local middleware-emulation canary (cycle-4 P1-2 discrimination).

    `wrangler pages dev` normalizes the Host header for Pages Functions
    (workers-sdk#165), so Host-spoofed requests may NOT trigger the
    tortoise.* -> product.html rewrite locally. If the middleware file is
    UNCHANGED from HEAD and emulation is broken, the tortoise-host tests
    DEFER to T8 production (skip = green-with-annotation, documented in the
    T7 task output). If the middleware file CHANGED, do NOT skip — that is a
    real regression risk that must fail."""
    hostname = urlsplit(TORTISE_HOST).hostname or ""
    if hostname and not hostname.startswith(("127.", "localhost")):
        return True  # real host (ALLOW_PROD) — run the checks as-is
    try:
        import subprocess

        changed = (
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "diff", "--quiet", "HEAD", "--", "website/functions/_middleware.ts"],
                capture_output=True,
            ).returncode
            == 1
        )
        if changed:
            return True  # real regression risk — never skip a changed middleware
        import urllib.request

        req = urllib.request.Request(
            TORTISE_HOST + "/", headers={"Host": "tortoise.premiselabs.co"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "ignore")
        return PRODUCT_ROOT_MARKER in body
    except Exception:
        return False


# ── Paths (repo-relative; the suite reads the committed drafts/titles). ────
REPO_ROOT = Path(__file__).resolve().parents[2]
TORTISE_EMULATED = _tortoise_emulated_locally()
TORTOISE_HOST_SKIP = pytest.mark.skipif(
    not (TORTISE_HOST_CHECK and TORTISE_EMULATED),
    reason="DNS stale or local middleware emulation unavailable (workers-sdk#165) — "
    "tortoise-host checks deferred to T8 production (#657)",
)
WEBSITE_DIR = REPO_ROOT / "website"
DRAFTS_DIR = REPO_ROOT / "docs" / "drafts"
TOS_SECTION_TITLES = DRAFTS_DIR / "2026-08-08-657-tos-section-titles.txt"
PRIVACY_SECTION_TITLES = DRAFTS_DIR / "2026-08-08-657-privacy-section-titles.txt"
TOS_DRAFT = DRAFTS_DIR / "2026-08-08-657-tos-draft.md"
PRIVACY_DRAFT = DRAFTS_DIR / "2026-08-08-657-privacy-draft.md"

LEGAL_PAGES = ("/privacy", "/tos", "/license", "/dpa")
FOOTER_LINK_HREFS = ("/privacy", "/tos", "/license", "/dpa")
PRODUCT_ROOT_MARKER = "Memory that makes your agents"
PRODUCT_ROOT_MARKER_LOWER = PRODUCT_ROOT_MARKER.lower()
FOOTER_SELECTOR = "footer, .legal-footer, .footer"
# The ABSOLUTE canonical Pricing Page URL (reviewer P2): the relative
# '/#pricing-section' form breaks on premiselabs.co, where '/' serves index.html
# with NO pricing content — the id="pricing-section" anchor exists only in
# product.html (served at '/' on the tortoise host via the middleware rewrite).
PRICING_PAGE_URL = "https://tortoise.premiselabs.co/#pricing-section"
FOOTER_PAGES = ("/welcome", "/signup", "/signin", "/self-hosted.html")
CRAWL_PAGES = (
    "/welcome", "/signup", "/signin", "/self-hosted.html", "/docs.html",
    "/privacy", "/tos", "/license", "/dpa",
)

# ── Pinned canonical sentences (T1/T2 Step 2 — the authoritative set; ──────
#    changes only when the approved draft legitimately changes). All
#    assertions compare NORMALIZED (lowercase, tag/entity-stripped,
#    whitespace-collapsed) text, so canonical strings are lowercase here.
PINNED_CANONICAL = {
    "no-training": "we do not use your content or usage data to train ai models",
    "no-sale": "we do not sell your personal information",
    "sharing": "we share limited data with advertising/analytics providers as disclosed",
    "minimal-pii": "we do not intentionally collect sensitive personal information",
    "eligibility": "you must be at least 18 years old",
    "carve-outs": "statutory/gdpr liability, fraud, and willful misconduct are not capped by this limitation of liability section",
    "fees": "we may charge fees for the service based on a combination of subscription, usage-based (metered), and/or per-seat pricing, as defined by the pricing tiers published on the pricing page",
    "art27": "we are not currently established in the eu/eea. if we become subject to gdpr art. 27, we will designate and disclose an eu representative here.",
    "consent-banner": "you can give or withdraw consent at any time via the consent banner",
    "repo-state": "no analytics tools are currently deployed",
    "posthog-processor": "posthog is a data processor; data is stored in the united states",
    "planned-tools-conditional": "not deployed yet and may be activated with your consent; consent is managed via the consent banner",
    "ga4-gtm-deployed": "google analytics (ga4) — audience and product analytics (google llc), delivered through google tag manager",
    "gtm-consent-gated": "google tag manager itself is loaded only after consent",
    "purposes": "your email address and account information are used to deliver the service, respond to requests, and manage billing; usage data is used to improve the product",
    "art126": "we may request identity verification before acting on any request",
}

# ── Negation guards (cycle-4 P1-5 / P2-4, cycle-5 P1-3). ───────────────────
NO_TRAINING_GUARD = re.compile(
    r"may train|we train|to train our (ai )?models|train(ing)? (on|using) (your|user|customer)"
)
PRESENT_TENSE_GUARD = re.compile(r"we (use|collect|share|sell|process|retain|store|transfer)\b")

# Check #10(e): the expected-match subset = the SUBSET of pinned canonical
# sentences that actually match the guard regex (most canonical sentences
# contain "we do" and do NOT match — e.g. the sharing sentence does).
EXPECTED_GUARD_MATCHES = {
    m.group(0) for s in PINNED_CANONICAL.values() for m in PRESENT_TENSE_GUARD.finditer(s)
}

# Broad template-placeholder check (#10(d1)) and D4-shape check (#10(d2),
# entity RESOLVED at G-gate ② — the former [ENTITY TYPE, ...] placeholder
# must not appear; the free-tier placeholder is lowercase prose and never
# matches this uppercase-only regex).
PLACEHOLDER_RE = re.compile(r"\{\{|\$\{|<INSERT|TODO")
D4_SHAPE_RE = re.compile(r"\[[A-Z][A-Z ,]{8,}\]")
EFFECTIVE_DATE_RE = re.compile(r"effective date:\s*(?:yyyy-mm-dd|\d{4}-\d{2}-\d{2})")

# ── Normalizers ────────────────────────────────────────────────────────────


def _clean(text: str) -> str:
    """Lowercase, strip HTML tags/entities, collapse whitespace.

    Used for ALL content assertions — both sides of every comparison pass
    through this (the guard regexes are lowercase, so both sides normalize).
    Spaces introduced by tag-to-space stripping are collapsed before
    sentence punctuation so '...page</a>.' normalizes to '...page.'.
    """
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,;:!?)\]])", r"\1", text)
    return text.strip().lower()


def _clean_md_line(text: str) -> str:
    """Strip markdown formatting from a single draft line (pre-normalization)."""
    text = re.sub(r"<!--.*?-->", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text)
    text = re.sub(r"\(outline\s*[^)]*\)", " ", text)
    text = re.sub(r"[<>]", " ", text)
    return text


# ── HTML tag-balance gate (#10(f) — same mechanism as the T4 build gate) ──


class _BalanceParser(HTMLParser):
    """Stack-tracking parser: fails on mismatched closes, non-empty stack at
    EOF, or premature </html>/</body>. Void elements never push."""

    VOID = {"meta", "link", "br", "img", "input", "hr", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.VOID:
            return  # never push void tags (product.html head uses slash-less <meta>/<link>)
        self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        pass  # self-closed <tag/> — never push

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"closing </{tag}> with empty stack")
            return
        if tag in ("html", "body") and tag not in self.stack:
            self.errors.append(f"premature </{tag}> (document closed before remaining content)")
            return
        if self.stack[-1] != tag:
            self.errors.append(f"mismatched close </{tag}> vs stack top <{self.stack[-1]}>")
            return
        self.stack.pop()


def _assert_tag_balance(html_text: str) -> None:
    parser = _BalanceParser()
    parser.feed(html_text)
    parser.close()
    problems = list(parser.errors)
    if parser.stack:
        problems.append(f"unclosed tags at EOF: {parser.stack}")
    assert not problems, "HTML tag-balance failures: " + "; ".join(problems)


# ── Draft↔render fidelity helpers (#10(g)) ─────────────────────────────────

_SENTENCE_SPLIT = re.compile(r"(?<!\d)(?<=[.!?])\s+(?=[A-Z0-9\"'\u201c])")

# Document bodies start at these anchors (the meta/drafting-note sections
# before them are NOT rendered content).
DRAFT_BODY_ANCHORS = {
    "tos": (TOS_DRAFT, r"^## 1\. Definitions"),
    "privacy": (PRIVACY_DRAFT, r"^## Scope of this policy"),
}


def _draft_units(line: str) -> list[str]:
    """Normalized sentence units from one draft line. Bold lead-ins are kept
    as their own unit so heading-style lead-ins ('15.4 Eligibility (18+).')
    do not merge with the following sentence in the rendered text."""
    parts = re.split(r"\*\*(.+?)\*\*", line)
    units: list[str] = []
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        cleaned = _clean(_clean_md_line(part))
        if not cleaned:
            continue
        if i % 2 == 1:  # bold segment — single unit
            units.append(cleaned)
        else:
            units.extend(_SENTENCE_SPLIT.split(cleaned))
    return units


def _is_draft_placeholder(unit: str) -> bool:
    """Placeholder forms resolved at build (G-gate resolutions + the pinned
    effective-date forms) — exempt from verbatim containment."""
    return (
        "[effective date" in unit
        or "[jurisdiction of residence to be confirmed" in unit
        or "[free-tier usage limits to be published]" in unit
    )


def _is_antiphrase(unit: str) -> bool:
    """The one draft sentence the renderer intentionally reworded to satisfy
    the anti-phrase guard (P2-4) — the CCPA opt-out lead sentence. The rest
    of that sentence ('because no sale occurs...') IS contained."""
    return "do not sell or share" in unit


def _draft_body_lines(draft_path: Path, anchor: str) -> list[str]:
    raw = draft_path.read_text(encoding="utf-8")
    m = re.search(anchor, raw, flags=re.M)
    assert m, f"draft anchor not found in {draft_path}"
    return [line for line in raw[m.start():].splitlines() if line.strip()]


# ── Local instrumentation scan (#10(c1) repo-state IFF) ────────────────────


def _scan_instrumentation_markers() -> list[str]:
    """Scan the local website/ tree (excl. apps/) for analytics/consent
    instrumentation markers — the shared consent.js module (#658) and any
    page that loads it. The privacy page's repo-state sentence must match
    the ACTUAL local state (IFF by construction)."""
    markers: list[str] = []
    files = sorted(WEBSITE_DIR.glob("*.html")) + sorted(WEBSITE_DIR.glob("*.js"))
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        if re.search(
            r"<script[^>]*src=[\"']https?://(js\.posthog\.com|connect\.facebook\.net|www\.googletagmanager\.com)",
            text, re.I,
        ):
            markers.append(f"{f.name}: analytics script tag")
        if re.search(r"<script[^>]*src=[\"']/consent\.js", text, re.I):
            markers.append(f"{f.name}: consent.js script tag")
        if re.search(r"posthog\.init\(|fbq\(|gtag\(", text, re.I):
            markers.append(f"{f.name}: analytics init call")
        if re.search(r"googletagmanager\.com/gtm\.js", text, re.I):
            markers.append(f"{f.name}: GTM container loader (gtm.js)")
        if re.search(r"id=[\"']consent-banner|klaro|consent_manager|data-consent", text, re.I):
            markers.append(f"{f.name}: consent-banner markup")
    return markers


# ── Page helpers ───────────────────────────────────────────────────────────


def _goto(page: Page, url: str, status: int = 200) -> str:
    resp = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    assert resp is not None, f"{url} produced no response"
    assert resp.status == status, f"{url} returned {resp.status} (expected {status})"
    return page.content()


def _body_text_clean(page: Page) -> str:
    return _clean(page.evaluate("document.body.innerText"))


def _footer_links_present(content: str) -> None:
    """Raw-HTML half (tortoise-host / crawl tests, no live page): the four
    legal hrefs appear in the served document."""
    for href in FOOTER_LINK_HREFS:
        assert href in content, f"footer link {href} missing"


# ═══════════════════════════════════════════════════════════════════════════
# #1 privacy 200 + negation-safe block
# ═══════════════════════════════════════════════════════════════════════════


def test_privacy_200_and_negation_safe_block(page: Page) -> None:
    """GET /privacy → 200; canonical no-training / no-sale / minimal-PII
    commitments with negation guards; anti-phrase ABSENT."""
    content = _goto(page, BASE_URL + "/privacy")
    body = _clean(content)

    # No-training: canonical sentence present AND no positive-training pattern.
    assert PINNED_CANONICAL["no-training"] in body
    assert not NO_TRAINING_GUARD.search(body), "training-positive phrasing found on /privacy"

    # No-sale co-occurrence: both sentences must be present.
    assert PINNED_CANONICAL["no-sale"] in body
    assert "we share limited data with advertising/analytics providers" in body

    # Minimal-PII pair.
    assert "do not intentionally collect" in body
    assert "sensitive personal information" in body

    # Keywords (plan #1): meta, processor, one month, sharing.
    for kw in ("meta", "processor", "one month", "sharing"):
        assert kw in body, f"missing keyword {kw!r} on /privacy"

    # Anti-phrase (P2-4): the CCPA link label does not ship this release.
    assert "do not sell or share" not in body


# ═══════════════════════════════════════════════════════════════════════════
# #2 tos 200 + negation-safe block + eligibility + carve-outs + fees
# ═══════════════════════════════════════════════════════════════════════════


def test_tos_200_and_negation_safe_block(page: Page) -> None:
    """GET /tos → 200; same negation-safe commitments + Business Source
    License boundary + Delaware + liability + DPA + 18+ eligibility + CSA §8
    carve-outs + dollar guard == 1 ($5M AUG) + fee-clause keywords."""
    content = _goto(page, BASE_URL + "/tos")
    body = _clean(content)

    for kw in ("business source license", "delaware", "liability", "dpa"):
        assert kw in body, f"missing keyword {kw!r} on /tos"

    # Same negation-safe block as /privacy.
    assert PINNED_CANONICAL["no-training"] in body
    assert not NO_TRAINING_GUARD.search(body), "training-positive phrasing found on /tos"
    assert PINNED_CANONICAL["no-sale"] in body
    assert "we share limited data with advertising/analytics providers" in body
    assert "do not intentionally collect" in body
    assert "sensitive personal information" in body

    # Eligibility (P2-1): pinned canonical sentence verbatim + visible "18+"
    # marker — never bare "18"/"age" (vacuous: "age" is a substring of "usage").
    assert PINNED_CANONICAL["eligibility"] in body
    assert "18+" in body

    # Anti-phrase absent.
    assert "do not sell or share" not in body

    # CSA §8 carve-outs co-occurrence (cycle-5 P2-5).
    assert "willful misconduct" in body
    assert "not capped" in body
    assert PINNED_CANONICAL["carve-outs"] in body

    # Dollar guard (cycle-4 P1-4, pinned == 1 at cycle-5 P2-2): the sole
    # allowed occurrence is the "$5M" AUG threshold in the license-boundary
    # section — never 0, never more.
    assert len(re.findall(r"\$[0-9]", body)) == 1, "tos body must contain exactly one $N figure ($5M AUG)"

    # Fee clause (G-gate ⑥ LOCKED): canonical sentence + mechanics keywords.
    assert PINNED_CANONICAL["fees"] in body
    assert "30 days" in body and "renewal" in body, "price-change notice mechanics missing"
    assert "non-refundable" in body


def test_tos_pricing_page_hyperlink_resolves(page: Page) -> None:
    """The 'Pricing Page' phrase must be HYPERLINKED (never bare text) with the
    ABSOLUTE canonical URL https://tortoise.premiselabs.co/#pricing-section
    (reviewer P2: the relative '/#pricing-section' form breaks on premiselabs.co,
    where '/' serves index.html with NO pricing content — the anchor exists
    only in product.html). The fragment target must EXIST in the served
    product page content, not just return HTTP 200."""
    _goto(page, BASE_URL + "/tos")
    anchors = page.evaluate(
        """() => [...document.querySelectorAll('a')]
              .filter(a => a.textContent.includes('Pricing Page'))
              .map(a => ({ text: a.textContent.trim(), href: a.getAttribute('href'), abs: a.href }))"""
    )
    assert anchors, "no anchor with 'Pricing Page' link text found on /tos"
    for a in anchors:
        assert a["href"] and a["href"] != "#", f"Pricing Page href is not resolvable: {a}"
        assert a["href"] == PRICING_PAGE_URL, (
            f"Pricing Page href must be the absolute canonical URL {PRICING_PAGE_URL!r} "
            f"(the relative '/#pricing-section' form breaks on premiselabs.co), got {a['href']!r}"
        )
    # The fragment target must actually exist in the served product page (the
    # document the tortoise host serves at '/') — a content assertion, not
    # just an HTTP-200 status. /product.html is 404 on the premiselabs.co
    # host (middleware blocks it — product lives on the tortoise host only).
    # Raw request with Host spoof: browser navigations can't set Host, and in
    # local emulation TORTISE_HOST is 127.0.0.1 (workers-sdk#165).
    t_host = urlsplit(TORTISE_HOST).hostname or "tortoise.premiselabs.co"
    t_spoof = t_host if t_host.startswith("tortoise.") else "tortoise.premiselabs.co"
    product = page.request.get(TORTISE_HOST + "/", headers={"Host": t_spoof}, timeout=15_000).text()
    assert 'id="pricing-section"' in product, \
        "served product page is missing the id=\"pricing-section\" anchor — the Pricing Page href would break"
    assert "write ops" in product, \
        "served product page is missing the 'write ops' pricing tier data"


# ═══════════════════════════════════════════════════════════════════════════
# #3 /license + /dpa 200 (both REQUIRED — G-gate ③/⑨ LOCKED)
# ═══════════════════════════════════════════════════════════════════════════


def test_license_and_dpa_serve_200(page: Page) -> None:
    """/license and /dpa both return 200 with substantive content. Both ship
    unconditionally (G-gate ③/⑨ LOCKED 2026-08-08) — no skip-if-not-shipped
    marker. The dollar guard does NOT apply to /license (unguarded by design)."""
    content = _goto(page, BASE_URL + "/license")
    body = _clean(content)
    assert len(body) > 500, "/license renders an empty shell"
    assert "business source license" in body
    assert "not legal advice" in body, "license page must carry the not-legal-advice line"

    content = _goto(page, BASE_URL + "/dpa")
    body = _clean(content)
    assert len(body) > 500, "/dpa renders an empty shell"
    assert "data processing agreement" in body or "processor" in body


# ═══════════════════════════════════════════════════════════════════════════
# #4 footer link presence (cycle-5 P1-1: split — BASE_URL half unconditional)
# ═══════════════════════════════════════════════════════════════════════════


def test_footer_legal_links_on_all_site_pages(page: Page) -> None:
    """UNCONDITIONAL half: the four legal links are present in the FOOTER
    element on product.html, welcome.html, signup.html, signin.html,
    self-hosted.html (G-gate ⑨ link set: Privacy · Terms · License · DPA —
    all four ship, no conditional). Scoped to the footer element via a real
    locator (footer, .legal-footer, .footer) — not page-wide substring checks
    (reviewer P3-5a)."""
    for path in FOOTER_PAGES:
        _goto(page, BASE_URL + path)
        footer = page.locator(FOOTER_SELECTOR).first
        expect(footer).to_be_visible()
        hrefs = footer.evaluate(
            "el => [...el.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))"
        )
        for href in FOOTER_LINK_HREFS:
            assert href in hrefs, f"footer link {href} missing on {path} (footer hrefs: {hrefs})"


@TORTOISE_HOST_SKIP
def test_tortoise_host_footer_half(page: Page) -> None:
    """TORTISE-HOST half: the middleware rewrites / → product.html on the
    tortoise host, so the product footer must be served there too. Runs only
    when TORTISE_HOST_CHECK == 1 (stale-DNS runs skip, never red)."""
    host = urlsplit(TORTISE_HOST).hostname or "tortoise.premiselabs.co"
    spoof = host if host.startswith("tortoise.") else "tortoise.premiselabs.co"
    resp = page.request.get(TORTISE_HOST + "/", headers={"Host": spoof}, timeout=15_000)
    assert resp.status == 200, f"tortoise host root returned {resp.status}"
    body = resp.text()
    assert PRODUCT_ROOT_MARKER in body, "tortoise root does not serve product.html"
    _footer_links_present(body)


# ═══════════════════════════════════════════════════════════════════════════
# #5 cross-host
# ═══════════════════════════════════════════════════════════════════════════


@TORTOISE_HOST_SKIP
def test_cross_host_privacy_and_tos_200(page: Page) -> None:
    """The other host serves the same legal pages (middleware only rewrites
    / — non-root paths pass through on both hosts)."""
    for path in ("/privacy", "/tos"):
        resp = page.request.get(TORTISE_HOST + path, timeout=15_000)
        assert resp.status == 200, f"{path} on {TORTISE_HOST} returned {resp.status}"
        assert PINNED_CANONICAL["no-training"] in _clean(resp.text())


# ═══════════════════════════════════════════════════════════════════════════
# #6 middleware regression (pinned root markers, P2-2)
# ═══════════════════════════════════════════════════════════════════════════


@TORTOISE_HOST_SKIP
def test_middleware_root_rewrites(page: Page) -> None:
    """tortoise.* root → product.html marker; premiselabs.co root →
    index.html marker. Host spoofing drives the middleware branch (browsers
    forbid setting Host on navigations, so the raw request API is used)."""
    t_host = urlsplit(TORTISE_HOST).hostname or "tortoise.premiselabs.co"
    t_spoof = t_host if t_host.startswith("tortoise.") else "tortoise.premiselabs.co"
    r = page.request.get(TORTISE_HOST + "/", headers={"Host": t_spoof}, timeout=15_000)
    assert r.status == 200
    assert PRODUCT_ROOT_MARKER_LOWER in _clean(r.text()), \
        "tortoise root marker missing (middleware rewrite broken)"

    b_host = urlsplit(BASE_URL).hostname or "premiselabs.co"
    b_spoof = b_host if b_host.endswith("premiselabs.co") else "premiselabs.co"
    r2 = page.request.get(BASE_URL + "/", headers={"Host": b_spoof}, timeout=15_000)
    assert r2.status == 200
    assert "the primary function of memory is not recall, but learning" in _clean(r2.text()), \
        "premiselabs root marker missing"


@TORTOISE_HOST_SKIP
def test_product_route_blocked_on_company_host(page: Page) -> None:
    """The product page lives ONLY on the tortoise host. /product and
    /product.html are static assets on the shared Pages project and would
    otherwise leak onto premiselabs.co — the middleware must 404 them on
    the company host while the tortoise host keeps serving the product at
    its root."""
    b_host = urlsplit(BASE_URL).hostname or "premiselabs.co"
    b_spoof = b_host if b_host.endswith("premiselabs.co") else "premiselabs.co"
    for path in ("/product", "/product.html"):
        r = page.request.get(BASE_URL + path, headers={"Host": b_spoof}, timeout=15_000)
        assert r.status == 404, f"{path} on premiselabs.co → {r.status} (expected 404)"

    t_host = urlsplit(TORTISE_HOST).hostname or "tortoise.premiselabs.co"
    t_spoof = t_host if t_host.startswith("tortoise.") else "tortoise.premiselabs.co"
    r_t = page.request.get(TORTISE_HOST + "/", headers={"Host": t_spoof}, timeout=15_000)
    assert r_t.status == 200
    assert PRODUCT_ROOT_MARKER_LOWER in _clean(r_t.text()), \
        "tortoise root must still serve the product page"


# ═══════════════════════════════════════════════════════════════════════════
# #7 same-viewport acceptance + DOM parentage + mocked clicks
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("viewport", [(1280, 720), (375, 667)])
def test_signup_acceptance_same_viewport(page: Page, viewport: tuple[int, int]) -> None:
    """The acceptance statement is visible in the SAME viewport as both the
    .providers OAuth buttons and #btn-submit — bounding-box assertion, not
    mere DOM presence (S1 / scope §5.7 #7)."""
    width, height = viewport
    page.set_viewport_size({"width": width, "height": height})
    _goto(page, BASE_URL + "/signup")

    accept = page.locator(".legal-accept")
    expect(accept).to_be_visible()

    # Pinned statement text (T5 Step 4, normalized).
    text = _clean(accept.inner_text())
    assert "by creating an account, you agree to the" in text
    assert "terms of service" in text and "privacy policy" in text

    boxes = page.evaluate(
        """() => {
            const r = (sel) => {
                const b = document.querySelector(sel).getBoundingClientRect();
                return {x: b.x, y: b.y, w: b.width, h: b.height};
            };
            return {acc: r('.legal-accept'), prov: r('.providers'), btn: r('#btn-submit')};
        }"""
    )

    def in_viewport(b: dict) -> bool:
        return b["x"] >= 0 and b["y"] >= 0 and b["x"] + b["w"] <= width and b["y"] + b["h"] <= height

    def x_overlap(a: dict, b: dict) -> bool:
        return min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) > 0

    # All three fully visible in the viewport at the same time.
    assert all(in_viewport(boxes[k]) for k in ("acc", "prov", "btn")), \
        f"acceptance not co-visible with OAuth buttons + submit at {width}x{height}: {boxes}"
    # Same visible column (shared x-range) with both neighbours.
    assert x_overlap(boxes["acc"], boxes["prov"]) and x_overlap(boxes["acc"], boxes["btn"])


def test_signup_acceptance_dom_structure(page: Page) -> None:
    """DOM parentage (P1-2): .legal-accept is a SIBLING of .providers (and
    .divider), PRECEDES <form id="email-form"> in document order, and is NOT
    a descendant of the form. Links resolve to /tos and /privacy."""
    _goto(page, BASE_URL + "/signup")

    struct = page.evaluate(
        """() => {
            const acc = document.querySelector('.legal-accept');
            const prov = document.querySelector('.providers');
            const div = document.querySelector('.divider');
            const form = document.querySelector('#email-form');
            const order = new Map([...document.querySelectorAll('*')].map((el, i) => [el, i]));
            return {
                siblingOfProviders: acc && prov && acc.parentElement === prov.parentElement,
                siblingOfDivider: acc && div && acc.parentElement === div.parentElement,
                precedesForm: acc && form && order.get(acc) < order.get(form),
                notInForm: acc && form && !form.contains(acc),
            };
        }"""
    )
    assert struct["siblingOfProviders"], ".legal-accept is not a sibling of .providers"
    assert struct["siblingOfDivider"], ".legal-accept is not a sibling of .divider"
    assert struct["precedesForm"], ".legal-accept does not precede #email-form"
    assert struct["notInForm"], ".legal-accept must not be a descendant of the form"

    hrefs = page.evaluate(
        """() => [...document.querySelectorAll('.legal-accept a')].map(a => a.getAttribute('href'))"""
    )
    assert "/tos" in hrefs and "/privacy" in hrefs, f"legal-accept links wrong: {hrefs}"


def test_mock_email_signup_shows_confirmation(page: Page) -> None:
    """Mocked email-path signup (the ONLY un-mocked modified surface): the
    auth/v1/signup request fires with the typed email, the acceptance block
    stays visible, zero console errors, and the MANDATORY inbox-state
    (#confirmation-required visible + #confirm-email == typed email) renders
    (cycle-4 P2-7b — not optional)."""
    email = f"e2e-{uuid.uuid4().hex[:8]}@premise-labs.dev"
    console_errors: list[str] = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    fired: dict = {}

    def handle(route):
        url = route.request.url
        if "auth/v1/signup" in url:
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST,OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                })
                return
            fired["signup"] = route.request
            # session-less response → the page shows the check-your-inbox state.
            # email mirrors real Supabase signUp responses (user.email present).
            route.fulfill(
                status=200,
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
                body=json.dumps({"user": {"id": "mock-user", "email": email, "identities": [{"id": "mock-id"}]}}),
            )
            return
        route.continue_()

    page.route("**/auth/v1/signup*", handle)
    _goto(page, BASE_URL + "/signup")
    expect(page.locator(".legal-accept")).to_be_visible()

    page.locator("#email").fill(email)
    page.locator("#password").fill("E2ePass-12345!")
    page.locator("#btn-submit").click()

    # MANDATORY inbox-state assertion (cycle-4 P2-7b).
    expect(page.locator("#confirmation-required")).to_be_visible(timeout=15_000)
    expect(page.locator("#confirm-email")).to_have_text(email)

    # X conversion event (#736 Path A): the dataLayer push fired on success
    # with event=x_signup, the typed email, and a non-empty conversion_id
    # (Supabase user id — the dedup key for the X Lead event).
    data_layer = page.evaluate("() => window.dataLayer || []")
    assert any(
        entry.get("event") == "x_signup" and entry.get("conversion_id") and entry.get("email") == email
        for entry in data_layer
    ), f"x_signup entry missing from dataLayer: {data_layer}"

    # The signup request fired with the typed payload.
    assert "signup" in fired, "auth/v1/signup request never fired"
    payload = json.loads(fired["signup"].post_data or "{}")
    assert payload.get("email") == email, f"signup payload email mismatch: {payload.get('email')!r}"
    assert payload.get("password") == "E2ePass-12345!"

    # Acceptance block still present + zero console errors.
    expect(page.locator(".legal-accept")).to_be_visible()
    assert console_errors == [], f"console errors during email signup: {console_errors}"


def test_mock_github_oauth_fires(page: Page) -> None:
    """Mocked OAuth click: 'Continue with GitHub' fires the auth/v1/authorize
    request with provider=github (supabase-js navigates the browser to the
    authorize URL directly — the mock fulfills that navigation with a stub),
    with no acceptance-block interference."""
    fired: dict = {}

    def handle(route):
        url = route.request.url
        if "auth/v1/authorize" in url:
            fired["authorize"] = route.request
            route.fulfill(
                status=200,
                content_type="text/html",
                body="<html><head><title>OAuth mock</title></head><body>OAuth mock</body></html>",
            )
            return
        route.continue_()

    page.route("**/auth/v1/authorize*", handle)
    _goto(page, BASE_URL + "/signup")
    expect(page.locator(".legal-accept")).to_be_visible()

    page.locator("#btn-github").click()

    deadline = time.time() + 15
    while "authorize" not in fired and time.time() < deadline:
        page.wait_for_timeout(100)
    assert "authorize" in fired, "auth/v1/authorize request never fired"
    assert "provider=github" in fired["authorize"].url, \
        f"OAuth request missing provider=github: {fired['authorize'].url}"


# ═══════════════════════════════════════════════════════════════════════════
# #8 0 broken links — enumerated set + tortoise root (gated) + external links
# ═══════════════════════════════════════════════════════════════════════════


def test_crawl_all_pages_final_200(page: Page) -> None:
    """Every enumerated page on BASE_URL resolves with a final 200 status
    (redirects followed — .html variants 308 to extensionless on Pages)."""
    for path in CRAWL_PAGES:
        resp = page.request.get(BASE_URL + path, timeout=15_000, max_redirects=10)
        assert resp.status == 200, f"{path} → {resp.status} (final {resp.url})"


@TORTOISE_HOST_SKIP
def test_crawl_tortoise_root_serves_product(page: Page) -> None:
    """The tortoise-host crawl item: / on the tortoise host (middleware
    rewrite) returns 200, serves the product page, and carries the footer."""
    host = urlsplit(TORTISE_HOST).hostname or "tortoise.premiselabs.co"
    spoof = host if host.startswith("tortoise.") else "tortoise.premiselabs.co"
    resp = page.request.get(TORTISE_HOST + "/", headers={"Host": spoof}, timeout=15_000)
    assert resp.status == 200, f"tortoise root → {resp.status}"
    body = resp.text()
    assert PRODUCT_ROOT_MARKER in body
    _footer_links_present(body)


# Project-owned domains are excluded from the third-party crawl: they are the
# crawl's own enumerated set (already asserted against BASE_URL/TORTISE_HOST),
# a separately-deployed project (app.premiselabs.co — the dashboard, S4), or
# covered by the welcome suite (premiselabs.co/onboarding-prompt.md). Fetching
# them in a local pre-merge run would hit PRODUCTION URLs (P1-3 violation).
_PROJECT_OWNED_HOSTS = ("premiselabs.co", "tortoise.premiselabs.co", "app.premiselabs.co")


@EXTERNAL_CRAWL_SKIP
def test_crawl_external_links_resolve(page: Page) -> None:
    """Third-party hrefs on the site must resolve with a final 200 (15s
    timeout; redirects followed — e.g. github.com LICENSE 301→blob is OK)."""
    external: set[str] = set()
    for path in CRAWL_PAGES:
        resp = page.request.get(BASE_URL + path, timeout=15_000)
        html = resp.text()
        for href in re.findall(r'href="([^"]+)"', html):
            if not href.startswith(("http://", "https://")):
                continue
            host = (urlsplit(href).hostname or "").lower()
            if host.endswith(_PROJECT_OWNED_HOSTS) or host.endswith(".pages.dev"):
                continue
            if host in ("localhost", "127.0.0.1"):
                continue
            external.add(href)

    assert external, "no external links found to crawl"
    failures = []
    for url in sorted(external):
        last: str | None = None
        for _attempt in range(2):  # one retry — GitHub 5xx/rate-limit is transient
            try:
                r = page.request.get(url, timeout=15_000, max_redirects=10)
                if r.status == 200:
                    last = None
                    break
                last = f"{url} → {r.status}"
            except Exception as exc:  # noqa: BLE001 — crawl must report any failure
                last = f"{url} → {type(exc).__name__}: {exc}"
            time.sleep(1)
        if last:
            failures.append(last)
    assert not failures, "broken external links:\n  " + "\n  ".join(failures)


def test_docs_html_contact_is_mailto(page: Page) -> None:
    """Scope P3-5 'docs.html contains no mailto' SUPERSEDED (owner decision
    2026-08-08): the hello@premiselabs.co mailbox now exists — docs.html
    contact is the mailto (the earlier mailto→GitHub-issues swap is
    REVERSED). The served /docs.html MUST contain mailto:hello@premiselabs.co
    in the raw document (docs.html is also in the crawl + mobile-render sets)."""
    raw = page.request.get(BASE_URL + "/docs.html", timeout=15_000).text()
    # Cloudflare Email Address Obfuscation rewrites mailto: on proxied custom
    # domains (data-cfemail + email-decode script). Accept the plain form
    # (pages.dev) or the obfuscated form (custom domains).
    assert ("mailto:hello@premiselabs.co" in raw) or (
        "data-cfemail" in raw and "email-decode.min.js" in raw
    ), "/docs.html must carry the hello@premiselabs.co contact " \
        "(plain mailto or Cloudflare-obfuscated form)"


# ═══════════════════════════════════════════════════════════════════════════
# #9 mobile render @375px — no horizontal scroll + minimum content + headings
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "path", ["/privacy", "/tos", "/welcome", "/signup", "/docs.html"]
)
def test_mobile_render_no_horizontal_scroll(page: Page, path: str) -> None:
    """At 375px the page must render without horizontal scroll (S8)."""
    page.set_viewport_size({"width": 375, "height": 667})
    _goto(page, BASE_URL + path)
    dims = page.evaluate(
        "({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})"
    )
    assert dims["sw"] <= 375, f"{path} horizontal scroll: scrollWidth {dims['sw']} > 375"


def test_legal_pages_minimum_content(page: Page) -> None:
    """The 15/16 sections must ACTUALLY render — a shell with zero horizontal
    scroll must FAIL (P1-4). Inner-text floor + heading counts."""
    floors = {"/privacy": (8000, 17), "/tos": (12000, 16)}  # (chars, h1+h2 count)
    for path, (floor, min_heads) in floors.items():
        _goto(page, BASE_URL + path)
        inner_len = page.evaluate("document.body.innerText.length")
        assert inner_len >= floor, f"{path}: only {inner_len} chars of body text (floor {floor})"
        heads = page.evaluate("document.querySelectorAll('h1,h2').length")
        assert heads >= min_heads, f"{path}: only {heads} h1/h2 headings (need ≥ {min_heads})"


# ═══════════════════════════════════════════════════════════════════════════
# #10 served-content assertions
# ═══════════════════════════════════════════════════════════════════════════


def test_revision_history_present(page: Page) -> None:
    """#10(a): revision-history block on every legal page — heading matches
    /revision|document history/i AND ≥1 changelog entry."""
    for path in LEGAL_PAGES:
        _goto(page, BASE_URL + path)
        body = _body_text_clean(page)
        assert re.search(r"revision|document history", body), f"{path}: no revision-history heading"
        assert "initial publication" in body, f"{path}: no changelog entry"


def test_privacy_consent_banner_sentence(page: Page) -> None:
    """#10(b): EXACT consent-banner sentence on /privacy (normalized)."""
    _goto(page, BASE_URL + "/privacy")
    assert PINNED_CANONICAL["consent-banner"] in _body_text_clean(page)


def test_privacy_repo_state_matches_local_tree(page: Page) -> None:
    """#10(c1): the repo-state sentence ('no analytics tools are currently
    deployed') is present IFF the local website/ tree has NO instrumentation
    markers (BOTH-AND — no vacuous pass). With markers present (consent.js +
    PostHog loader + GTM container live), /privacy must instead assert the
    deployed-processor framing for PostHog and GA4 (via GTM) and the
    conditional framing for the planned tools (Meta / X / LinkedIn)."""
    markers = _scan_instrumentation_markers()
    _goto(page, BASE_URL + "/privacy")
    body = _body_text_clean(page)
    repo_state_present = PINNED_CANONICAL["repo-state"] in body
    if markers:
        assert not repo_state_present, \
            "page claims no tools deployed but local tree has instrumentation markers"
        assert PINNED_CANONICAL["posthog-processor"] in body, \
            "instrumented tree but /privacy lacks PostHog deployed-processor framing"
        assert PINNED_CANONICAL["planned-tools-conditional"] in body, \
            "instrumented tree but /privacy lacks planned-tools conditional framing"
        if any("GTM container" in m for m in markers):
            assert PINNED_CANONICAL["ga4-gtm-deployed"] in body, \
                "GTM present in local tree but /privacy lacks GA4-via-GTM deployed framing"
            assert PINNED_CANONICAL["gtm-consent-gated"] in body, \
                "GTM present in local tree but /privacy lacks GTM consent-gated framing"
    else:
        assert repo_state_present, \
            "local tree has no instrumentation but /privacy does not state deployment status"


def test_privacy_per_tool_conditional_framing(page: Page) -> None:
    """#10(c2): EACH tool name matches via word-boundary regex and has an
    occurrence within 120 chars of a consent/conditional word — NOT bare
    'meta' (false-matches 'metadata')."""
    _goto(page, BASE_URL + "/privacy")
    text = _body_text_clean(page)
    for tool in (r"\bmeta pixel\b", r"\bposthog\b", r"google analytics", r"x \(twitter\)", r"\blinkedin\b"):
        framed = False
        for m in re.finditer(tool, text):
            ctx = text[max(0, m.start() - 120): m.end() + 120]
            if re.search(r"consent|activated|will|may|when|if", ctx):
                framed = True
                break
        assert framed, f"tool {tool!r} has no conditional/consent-framed occurrence on /privacy"


def test_consent_js_served_and_banner_present(page: Page) -> None:
    """#10(h): the shared consent module serves 200 with the wired PostHog
    project API key (project 548850, US Cloud) replacing the fail-safe
    placeholder, the wired Google Tag Manager container (GTM-WQR34GSC — the
    single consent-gated tag container carrying GA4, #736) with NO direct
    gtag loader, the dormant Meta Pixel stub, plus the banner markup; every
    tortoise funnel page loads it (defer); the company landing page
    (index.html) deliberately does NOT (no analytics per design)."""
    resp = page.request.get(BASE_URL + "/consent.js", timeout=15_000)
    assert resp.status == 200, f"/consent.js status {resp.status}"
    js = resp.text()
    assert "POSTHOG_KEY" in js, "consent.js missing the POSTHOG_KEY constant"
    assert "__POSTHOG_PROJECT_API_KEY__" not in js, \
        "consent.js still contains the fail-safe placeholder (must be replaced)"
    assert "phc_zvBi25UoCxrq79qS7cudZhfAS3XQwEfrzEoZfR2EHkjS" in js, \
        "consent.js missing the wired PostHog project API key"
    assert "tortoise_consent" in js, "consent.js missing the consent-state storage key"
    assert '"consent-banner"' in js, "consent.js missing the banner markup"
    assert "us.i.posthog.com" in js and "-assets.i.posthog.com" in js, \
        "consent.js missing the US loader (us-assets.i.posthog.com/static/array.js)"

    # #736 GTM unification: GTM container wired and consent-gated; GA4 loads
    # via GTM (no direct gtag loader remains in consent.js).
    assert "GTM-WQR34GSC" in js, "consent.js missing the wired GTM container ID"
    assert "www.googletagmanager.com/gtm.js?id=" in js, \
        "consent.js missing the GTM loader (gtm.js?id=<container>)"
    assert 'event: "gtm.js"' in js, \
        "consent.js missing the dataLayer gtm.start init before the GTM snippet"
    assert "gtag(" not in js, \
        "consent.js still contains a direct gtag loader (GA4 must come via GTM)"
    assert "__META_PIXEL_ID__" in js, \
        "consent.js missing the dormant Meta Pixel stub (placeholder ID, fail-safe)"

    # Product content lives on the tortoise host (middleware rewrites /
    # → product.html there; premiselabs.co/product is 404 by design). Raw
    # request with Host spoof (browser can't set Host; local emulation uses
    # 127.0.0.1, workers-sdk#165).
    t_host = urlsplit(TORTISE_HOST).hostname or "tortoise.premiselabs.co"
    t_spoof = t_host if t_host.startswith("tortoise.") else "tortoise.premiselabs.co"
    product_raw = page.request.get(TORTISE_HOST + "/", headers={"Host": t_spoof}, timeout=15_000).text()
    assert re.search(r'<script[^>]+src="/consent\.js"[^>]*>', product_raw, re.I), \
        "tortoise host product page: missing consent.js script tag"

    for path in ("/signup", "/signin", "/welcome"):
        raw = page.request.get(BASE_URL + path, timeout=15_000).text()
        assert re.search(r'<script[^>]+src="/consent\.js"[^>]*>', raw, re.I), \
            f"{path}: missing consent.js script tag"

    raw_index = page.request.get(BASE_URL + "/index.html", timeout=15_000).text()
    assert "consent.js" not in raw_index, \
        "index.html must stay analytics-free (company landing — no instrumentation per design)"


def test_no_template_placeholders(page: Page) -> None:
    """#10(d1): broad template-placeholder regex matches NOTHING on both
    legal pages ({{, ${, <INSERT, TODO)."""
    for path in ("/privacy", "/tos"):
        content = _goto(page, BASE_URL + path)
        assert not PLACEHOLDER_RE.search(content), f"{path}: template placeholder found"


def test_no_d4_shape_placeholders(page: Page) -> None:
    """#10(d2): the D4-shape placeholder regex [A-Z][A-Z ,]{8,} matches
    NOTHING on both pages (count 0, unconditional — the former
    '[ENTITY TYPE, JURISDICTION OF FORMATION TO BE CONFIRMED]' placeholder is
    REPLACED; the free-tier placeholder is lowercase prose and never matches)."""
    for path in ("/privacy", "/tos"):
        content = _goto(page, BASE_URL + path)
        assert len(D4_SHAPE_RE.findall(content)) == 0, f"{path}: D4-shape placeholder found"


def test_present_tense_guard_matches_only_pinned_set(page: Page) -> None:
    """#10(e): the SET of `we (use|collect|share|sell|process|retain|store|
    transfer)\b` matches over the normalized served text equals EXACTLY the
    pinned expected-match subset (the canonical sentences that match the
    guard — typically only the sharing sentence). Any other match fails."""
    for path in ("/privacy", "/tos"):
        _goto(page, BASE_URL + path)
        body = _body_text_clean(page)
        matches = {m.group(0) for m in PRESENT_TENSE_GUARD.finditer(body)}
        assert matches == EXPECTED_GUARD_MATCHES, \
            f"{path}: present-tense guard matches {sorted(matches)} != pinned {sorted(EXPECTED_GUARD_MATCHES)}"


def test_html_structure_and_section_titles(page: Page) -> None:
    """#10(f): FULL-document tag balance over the raw served bytes (same
    stack-tracking gate as T4 Step 5 — VOID set, close-vs-top, EOF-empty,
    no premature </html>/</body>), </body></html> present, all section titles
    from the committed title lists render as body text, heading counts."""
    cases = (("/tos", TOS_SECTION_TITLES), ("/privacy", PRIVACY_SECTION_TITLES))
    for path, titles_file in cases:
        # Balance over the RAW served bytes (page.content() would let the
        # browser parser silently repair a broken template).
        raw = page.request.get(BASE_URL + path, timeout=15_000).text()
        _assert_tag_balance(raw)
        assert "</body>" in raw and raw.rstrip().endswith("</html>"), \
            f"{path}: document not closed with </body></html>"

        titles = [ln.strip() for ln in titles_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(titles) >= 15, f"{path}: title list too short ({len(titles)})"

        _goto(page, BASE_URL + path)
        text = _body_text_clean(page)
        missing = [t for t in titles if _clean(t) not in text]
        assert not missing, f"{path}: section titles missing from body text: {missing}"

        heads = page.evaluate("document.querySelectorAll('h1,h2').length")
        assert heads >= len(titles) + 1, f"{path}: {heads} headings < {len(titles)} sections + title"


def test_effective_date_format_present_once(page: Page) -> None:
    """`Effective date: YYYY-MM-DD` (placeholder form now; real date at T8
    deploy) must appear EXACTLY once per legal page — the canonical
    extractable string (P2-9)."""
    for path in LEGAL_PAGES:
        raw = page.request.get(BASE_URL + path, timeout=15_000).text()
        matches = EFFECTIVE_DATE_RE.findall(_clean(raw))
        assert len(matches) == 1, f"{path}: expected exactly 1 effective-date line, got {len(matches)}: {matches}"


def test_draft_to_render_fidelity(page: Page) -> None:
    """#10(g): every draft sentence ≥ 40 chars (normalized) from the approved
    draft bodies appears in the rendered page text (per page) — closes the
    approve-render gap (paraphrase/drop/reorder detection).

    Documented exemptions:
      - markdown table rows (changelog structure — build-resolved)
      - placeholder forms resolved at build (effective-date / jurisdiction /
        free-tier-limits — G-gate resolutions, plan-pinned sub-40 fragments)
      - the single anti-phrase draft sentence the renderer intentionally
        reworded to satisfy the anti-phrase guard (P2-4)
    """
    cases = (
        (TOS_DRAFT, "/tos", DRAFT_BODY_ANCHORS["tos"][1]),
        (PRIVACY_DRAFT, "/privacy", DRAFT_BODY_ANCHORS["privacy"][1]),
    )
    for draft, page_path, anchor in cases:
        lines = _draft_body_lines(draft, anchor)
        _goto(page, BASE_URL + page_path)
        rendered = _body_text_clean(page)
        checked = 0
        missing: list[str] = []
        for line in lines:
            if _clean(_clean_md_line(line)).startswith("|"):  # table row
                continue
            for unit in _draft_units(line):
                if len(unit) < 40:
                    continue
                if _is_draft_placeholder(unit) or _is_antiphrase(unit):
                    continue
                checked += 1
                if unit not in rendered:
                    missing.append(unit)
        assert not missing, (
            f"{page_path}: {len(missing)} draft sentence(s) ≥40 chars missing from render "
            f"(of {checked} checked):\n  " + "\n  ".join(m[:160] for m in missing[:10])
        )
