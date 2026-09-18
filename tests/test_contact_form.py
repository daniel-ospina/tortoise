"""Static regression tests for the premiselabs.co contact form (#2409).

Guards the contract the form exists to honour — at the repo level, no network:

  1. DELIVERY TARGET IS SETTLED. The owner ruling (issue #2409, 2026-09-18) puts
     the outside-product channel at `hello@premiselabs.co`. The recipient is a
     CODE CONSTANT in the Pages Function, never a request field — that is what
     keeps the form from being an open relay. A future edit that made the
     recipient configurable, or drifted it to another address, fails here.
  2. FAIL LOUDLY WHEN UNCONFIGURED. With `RESEND_API_KEY` absent the endpoint
     must answer 503 `not_configured` with a human-actionable message — never a
     200. This is the #3616 lesson applied to a second surface: a requirement
     that can only be read cannot fail. (The live proof is the curl probe in
     website/README.md; this pins the source that produces it.)
  3. THE SECRET IS READ FROM ENV AND NEVER LOGGED/ECHOED. `env.RESEND_API_KEY`
     is the only read site; the key goes out as an Authorization header, never
     in a URL, and never reaches a log line or a response body.
  4. THE FORM IS SURFACED. `/contact` is a real page, linked from the company
     landing page, the product footer, the FAQ footer and the docs next-steps,
     and the trailing-slash / .html variants redirect to it.

Run:  python -m pytest tests/test_contact_form.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBSITE_DIR = REPO_ROOT / "website"

FUNCTION_TS = WEBSITE_DIR / "functions" / "api" / "contact.ts"
CONTACT_HTML = WEBSITE_DIR / "contact.html"
REDIRECTS = WEBSITE_DIR / "_redirects"
README = WEBSITE_DIR / "README.md"

#: The owner-decided outside-product destination. Changing this is a product
#: decision, not a code change — it must be argued in the issue, not here.
CONTACT_TO = "hello@premiselabs.co"


def _src(path: Path) -> str:
    assert path.is_file(), f"{path.relative_to(REPO_ROOT)} is missing"
    return path.read_text(encoding="utf-8")


# ── 1. Recipient is a constant, and it is the decided address ──────────────


def test_recipient_is_the_decided_address() -> None:
    assert f'const CONTACT_TO = "{CONTACT_TO}"' in _src(FUNCTION_TS)


def test_recipient_is_not_taken_from_the_request() -> None:
    """Open-relay guard: the To: address must never come from the payload.

    A `to`/`recipient` read off the body would let anyone send mail through this
    domain to anyone. The only request-supplied address is the Reply-To, and it
    is validated.
    """
    src = _src(FUNCTION_TS)
    assert "to: [CONTACT_TO]" in src, "the To: field must be the CONTACT_TO constant"
    # No request field may be used as the delivery target.
    assert not re.search(r"to:\s*\[\s*(body|data)\.", src)
    assert "reply_to: email" in src, "the submitter's address belongs in Reply-To"


# ── 2. Fail-loud when unconfigured ────────────────────────────────────────


def test_missing_key_returns_503_not_configured() -> None:
    src = _src(FUNCTION_TS)
    # The credential gate exists and returns 503 with the named error code.
    assert "env.RESEND_API_KEY" in src
    assert re.search(r"if\s*\(\s*apiKey\s*===\s*\"\"\s*\)", src), "missing-key gate not found"
    # …and it returns BEFORE any send is attempted, so a silent success is
    # structurally impossible on the unconfigured path.
    gate_pos = src.index("apiKey === \"\"")
    send_pos = src.index("await fetch(RESEND_URL")
    assert gate_pos < send_pos, "the credential gate must precede the send"
    # Pin the STATUS inside the gate itself — a 200 there would be the silent
    # success this whole contract exists to forbid.
    gate = src[gate_pos:send_pos]
    assert re.search(r"return\s+fail\(\s*503", gate), f"unconfigured path is not a 503: {gate[:200]!r}"
    assert '"not_configured"' in gate


def test_unconfigured_message_gives_the_visitor_the_fallback() -> None:
    """A 503 the visitor cannot act on is a dead end, not a failure signal."""
    src = _src(FUNCTION_TS)
    gate = src[src.index("apiKey === \"\"") : src.index("const from =")]
    assert "hello@premiselabs.co" in gate
    assert "not been sent" in gate or "not sent" in gate


# ── 3. Secret handling ───────────────────────────────────────────────────


def test_secret_is_a_bearer_header_never_a_url_or_a_log() -> None:
    src = _src(FUNCTION_TS)
    assert "Authorization: `Bearer ${apiKey}`" in src
    assert "Authorization: `Bearer ${RESEND_API_KEY}`" not in src
    # `${apiKey}` must appear exactly once, in the Authorization header — never
    # in a URL (URLs are logged; a bearer token in one is a leaked token).
    assert src.count("${apiKey}") == 1, f"${apiKey} used {src.count('${apiKey}')} times"
    for line in src.splitlines():
        if "${apiKey}" in line:
            assert "Bearer" in line, f"key interpolated outside the auth header: {line.strip()}"
    for m in re.finditer(r"console\.(?:log|error|warn)\(([^)]*)\)", src, flags=re.S):
        assert "apiKey" not in m.group(1), f"secret referenced in a log call: {m.group(0)[:80]}"


def test_secret_name_is_the_established_one() -> None:
    """Same credential name the waitlist edge function and email_notify use, so
    one key serves both surfaces and ops has one thing to bind."""
    assert "RESEND_API_KEY" in _src(FUNCTION_TS)
    assert "RESEND_FROM_EMAIL" in _src(FUNCTION_TS)


# ── 4. Abuse protection and validation ───────────────────────────────────


def test_honeypot_and_rate_limit_present() -> None:
    src = _src(FUNCTION_TS)
    assert "body.hp" in src, "honeypot field not read"
    assert "RATE_LIMIT" in src, "rate limit not wired"
    # Pin the CALL SITE, not just the definition — `"rateLimited(" in src` is
    # satisfied by the function declaration, so deleting the only call would
    # have left this test green with no protection at all.
    assert re.search(r"if\s*\(\s*rateLimited\(ip,\s*Date\.now\(\)\)\s*\)", src), (
        "rate limiter is defined but never consulted"
    )


def test_cross_site_browser_submissions_are_refused() -> None:
    """The form-encoded fallback is a CORS-simple request — no preflight — so
    without this check any third-party page could drive submissions from its
    visitors' IPs, each a fresh rate-limit key."""
    src = _src(FUNCTION_TS)
    assert "isCrossSite(" in src, "no cross-site guard"
    assert re.search(r"if\s*\(\s*isCrossSite\(request\)\s*\)", src), (
        "cross-site guard is defined but never consulted"
    )
    assert '"Sec-Fetch-Site"' in src and '"Origin"' in src, (
        "cross-site guard must read Sec-Fetch-Site and/or Origin"
    )
    assert re.search(r"return\s+fail\(\s*403", src), "cross-site submissions must be refused with a 403"


def test_honeypot_answers_generic_success() -> None:
    """Telling a bot it tripped the honeypot teaches it to leave the field
    empty; the honeypot branch must look like any other success."""
    src = _src(FUNCTION_TS)
    hp = src[src.index("body.hp") : src.index("const name =")]
    assert "ok: true" in hp, f"honeypot branch is not a generic success: {hp!r}"


def test_rate_limit_map_is_bounded() -> None:
    """A cap that only removes EXPIRED entries bounds nothing under sustained
    traffic from many addresses — the map would grow without limit and then run
    an O(n) rebuild per request."""
    src = _src(FUNCTION_TS)
    assert "MAX_RATE_KEYS" in src
    evict = src[src.index("hits.size > MAX_RATE_KEYS") : src.index("return false;")]
    assert "hits.delete(k)" in evict, "no key eviction under the cap"
    assert "excess" in evict or "hits.size - MAX_RATE_KEYS" in evict, (
        "the cap must remove keys until the map is back under it"
    )


def test_reply_to_is_validated() -> None:
    """The reply-to is the one submitter-controlled address; an unvalidated one
    is a header-injection primitive (CR/LF) and an open-relay helper."""
    src = _src(FUNCTION_TS)
    assert "validEmail(" in src
    assert "problems.push(\"a valid reply-to email address\")" in src
    # A control-character check must exist in the validator (CR/LF defence).
    validator = src[src.index("function validEmail") : src.index("async function readPayload")]
    assert "\\u0020" in validator or "\\r" in validator, "no CR/LF guard in validEmail"


# ── 5. The page and its surfacing ────────────────────────────────────────


def test_contact_page_posts_to_the_function() -> None:
    html = _src(CONTACT_HTML)
    assert 'action="/api/contact"' in html, "form must post to the Pages Function"
    for field in ("name", "email", "message"):
        assert f'name="{field}"' in html, f"missing field {field}"
    assert 'name="hp"' in html, "honeypot field missing from the page"
    # Honeypot must be hidden from assistive tech as well as from sight.
    assert 'class="hp-field"' in html and 'aria-hidden="true"' in html


def test_contact_page_reaches_the_decided_address_as_fallback() -> None:
    """If delivery is unconfigured (503) the visitor still needs a way to reach
    us from the page itself."""
    assert CONTACT_TO in _src(CONTACT_HTML)


def test_contact_is_surfaced_from_the_pages_visitors_land_on() -> None:
    for rel in ("index.html", "product.html", "faq.html"):
        html = _src(WEBSITE_DIR / rel)
        assert re.search(r'href="/contact"', html), f"{rel} does not link to /contact"


def test_contact_variants_redirect_to_the_canonical() -> None:
    redirects = _src(REDIRECTS)
    assert re.search(r"^/contact/\s+/contact\s+301$", redirects, flags=re.M)
    assert re.search(r"^/contact\.html\s+/contact\s+301$", redirects, flags=re.M)


def test_ops_step_is_documented() -> None:
    """The one human action that makes delivery live must be written down where
    an operator looks, not only in an issue."""
    readme = _src(README)
    assert "Contact form (#2409)" in readme
    assert "RESEND_API_KEY" in readme
    assert "503" in readme
