"""Static regression tests for the premiselabs.co contact form (#2409).

Guards the contract the form exists to honour — at the repo level, no network:

  1. DELIVERY TARGET IS SETTLED. The owner ruling (issue #2409, 2026-09-18) puts
     the outside-product channel at `hello@premiselabs.co`. It is a CODE
     CONSTANT in the Pages Function, never a request field — that is what keeps
     the form from being an open relay. A future edit that made the recipient
     configurable, or drifted it to another address, fails here.
  2. FAIL LOUDLY WHEN UNCONFIGURED. With the intake endpoint absent the endpoint
     must answer 503 `not_configured` with a human-actionable message — never a
     200. This is the #3616 lesson applied to a second surface: a requirement
     that can only be read cannot fail. (The live proof is the curl probe in
     website/README.md; this pins the source that produces it.)
  3. THE FORM IS AN INTAKE PRODUCER, WITH NO PROVIDER CALL IN ITS PATH. The
     owner's architecture is WE RECEIVE: customers email hello@/support@ and
     intake processes the message; no reply is required for the product to
     work, and outbound email has exactly two legitimate homes — replying to a
     user who emailed first, and auth flows. So the form sends NO email: no
     `RESEND_*` read, no provider endpoint, no `Authorization`/`Bearer` header,
     and no send-capable credential. `premise-labs#393` is a BUDGET TO MANAGE
     (objective: zero quota-rejected sends), NOT a reason to refuse to build —
     the absence of a sending key is the shape of an OPEN transport decision,
     not a quota verdict.
  4. THE FORM IS SURFACED, AND `hello@` IS VISIBLE. `/contact` is a real page,
     linked from the company landing page, the product footer, the FAQ footer
     and the docs next-steps, and the trailing-slash / .html variants redirect
     to it. The decided address is shown plainly on the outside surface (lede
     and direct-mailto fallback), not hidden behind an error path.
  5. THE TRANSPORT IS A SINGLE, PROVIDER-NEUTRAL SEAM — AND IT IS UNRESOLVED.
     One submission becomes one JSON item `{name, replyTo, message, receivedAt,
     source}` posted to a configurable intake endpoint from
     `functions/_shared/contact-transport.ts`, behind `enqueue()`. The owner has
     NOT settled the transport (a queue vs. email), so the open decision and the
     owner's architecture — with #393 recorded as a budget to manage, not a
     reason to refuse to build — must stay recorded in the code and in
     website/README.md for the next reader.

Run:  python -m pytest tests/test_contact_form.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBSITE_DIR = REPO_ROOT / "website"

FUNCTION_TS = WEBSITE_DIR / "functions" / "api" / "contact.ts"
TRANSPORT_TS = WEBSITE_DIR / "functions" / "_shared" / "contact-transport.ts"
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
    """Open-relay guard: the destination must never come from the payload.

    A `to`/`recipient` read off the body would let anyone send through this
    domain to anyone. The only request-supplied address is the Reply-To, and it
    is validated and travels as `replyTo` — never as a destination.
    """
    src = _src(FUNCTION_TS)
    # The function passes exactly the validated message fields to the seam; no
    # destination is among them.
    assert re.search(
        r"enqueue\(\s*\{\s*name,\s*replyTo:\s*email,\s*message\s*\}", src
    ), "the seam must receive name/replyTo/message only"
    # No request field may be used as a destination.
    assert not re.search(r"to:\s*\[\s*(body|data)\.", src)
    assert "to: CONTACT_TO" not in src, "the recipient belongs to routing, not to the queued item"

    tsrc = _src(TRANSPORT_TS)
    # The queued item has no destination field at all — the intake endpoint
    # owns routing, so the submitter cannot steer it.
    assert "replyTo: msg.replyTo" in tsrc, "the submitter's address belongs in replyTo"
    item = tsrc[tsrc.index("const item = {") : tsrc.index("body: JSON.stringify(item)")]
    assert not re.search(r"\b(to|recipient|destination)\s*:", item), (
        "the queued item must carry no destination"
    )


# ── 2. Fail-loud when unconfigured ────────────────────────────────────────


def test_missing_endpoint_returns_503_not_configured() -> None:
    """The seam's `not_configured` outcome maps to a visible 503.

    The configuration gate is transport-specific, so it lives in the seam; the
    HTTP status lives in the function. Both halves of the unconfigured path are
    pinned: a 503 only because the outcome is mapped, and the outcome only
    because the gate precedes the network call.
    """
    src = _src(FUNCTION_TS)
    assert re.search(r'if\s*\(\s*outcome\.status\s*===\s*"not_configured"\s*\)', src), (
        "the function does not map the seam's not_configured outcome"
    )
    # Pin the STATUS inside the mapping — a 200 there would be the silent
    # success this whole contract exists to forbid.
    branch = src[src.index('outcome.status === "not_configured"') : src.index('outcome.status === "failed"')]
    assert re.search(r"return\s+fail\(\s*503", branch), f"unconfigured path is not a 503: {branch[:200]!r}"
    assert '"not_configured"' in branch
    # …and the mapping precedes the final success return, so a silent success on
    # the unconfigured path is structurally impossible. (rindex: the honeypot
    # branch's generic success legitimately appears earlier.)
    assert src.index('outcome.status === "not_configured"') < src.rindex("return json(")

    tsrc = _src(TRANSPORT_TS)
    # The intake endpoint comes from env, and its gate precedes the network call.
    assert 'const INTAKE_URL_ENV = "CONTACT_INTAKE_URL"' in tsrc
    assert "envString(env, INTAKE_URL_ENV)" in tsrc
    assert re.search(r'if\s*\(\s*intakeUrl\s*===\s*""\s*\)', tsrc), "missing-endpoint gate not found"
    gate_pos = tsrc.index('intakeUrl === ""')
    fetch_pos = tsrc.index("await fetch(")
    assert gate_pos < fetch_pos, "the configuration gate must precede the network call"
    gate = tsrc[gate_pos:fetch_pos]
    assert 'status: "not_configured"' in gate


def test_unconfigured_message_gives_the_visitor_the_fallback() -> None:
    """A 503 the visitor cannot act on is a dead end, not a failure signal."""
    src = _src(FUNCTION_TS)
    branch = src[src.index('outcome.status === "not_configured"') : src.index('outcome.status === "failed"')]
    assert "CONTACT_TO" in branch, "the fallback must name the decided address"
    assert "not been sent" in branch or "not sent" in branch


# ── 3. No email leg: no provider, no credential, no provider call ─────────


def test_no_email_provider_or_credential_anywhere_in_the_form_path() -> None:
    """The email leg is ABSENT from the form's path. `premise-labs#393` is a
    BUDGET TO MANAGE (objective: zero quota-rejected sends), never a reason to
    refuse to build — the form routes into intake because receiving is the
    mechanism and sending is the exception."""
    for path in (FUNCTION_TS, TRANSPORT_TS):
        src = _src(path)
        low = src.lower()
        assert "resend" not in low, f"{path.name} names an email provider"
        assert "api.resend.com" not in low
        assert "RESEND_" not in src, f"{path.name} reads a RESEND_* variable"
        # No credential of any kind travels from the form.
        assert "Bearer" not in src, f"{path.name} constructs a bearer header"
        assert "Authorization" not in src, f"{path.name} sets an authorization header"

    # Stronger: no source file under website/functions may name the sender or
    # its endpoint — the form's path is not the only place a stray key could
    # reappear.
    for path in (WEBSITE_DIR / "functions").rglob("*.ts"):
        text = path.read_text(encoding="utf-8")
        assert "api.resend.com" not in text, f"{path.relative_to(REPO_ROOT)} names the provider endpoint"
        assert "RESEND_" not in text, f"{path.relative_to(REPO_ROOT)} reads a RESEND_* variable"


def test_the_only_configuration_is_the_intake_endpoint() -> None:
    """The seam reads exactly one variable — a plain URL, no secret — and the
    function reads none of its own."""
    tsrc = _src(TRANSPORT_TS)
    assert 'const INTAKE_URL_ENV = "CONTACT_INTAKE_URL"' in tsrc
    # Exactly one env read, and it is the intake endpoint.
    assert tsrc.count("envString(env, INTAKE_URL_ENV)") == 1
    assert _src(FUNCTION_TS).count("envString(") == 0
    # Exactly one env-var-name constant, and it is not secret-shaped.
    env_names = re.findall(r'^\s*const\s+\w*ENV\w*\s*=\s*"([^"]+)"', tsrc, flags=re.M)
    assert env_names == ["CONTACT_INTAKE_URL"], f"unexpected env variables: {env_names}"
    for forbidden in ("API_KEY", "SECRET", "TOKEN", "PASSWORD"):
        assert forbidden not in env_names[0]


# ── 5. The transport seam is singular, provider-neutral, and unresolved ───


def test_the_enqueue_is_isolated_behind_one_seam() -> None:
    """Swapping transport must be a change to ONE module. The function imports
    the seam, performs no network call of its own, and never names a provider or
    an email sender."""
    src = _src(FUNCTION_TS)
    assert re.search(
        r'import\s*\{\s*enqueue\s*\}\s*from\s*"\.\./_shared/contact-transport"', src
    ), "the function must import the transport seam"
    assert "resend" not in src.lower(), "an email sender's name leaks into the function"
    assert "https://api." not in src, "a provider endpoint leaks into the function"
    assert "fetch(" not in src, "the function must not perform the network call itself"
    assert "enqueue({" in src, "the function must route intake through the seam"

    tsrc = _src(TRANSPORT_TS)
    assert tsrc.count("fetch(") == 1, "the seam must be the single place a request is made"


def test_queued_item_has_exactly_the_agreed_shape() -> None:
    """One item: name, reply-to email, message, received-at, source."""
    tsrc = _src(TRANSPORT_TS)
    assert "name: msg.name" in tsrc
    assert "replyTo: msg.replyTo" in tsrc
    assert "message: msg.message" in tsrc
    assert "receivedAt: new Date().toISOString()" in tsrc, "the item must carry a receipt time"
    assert "source: INTAKE_SOURCE" in tsrc, "the item must carry its producing surface"
    assert 'INTAKE_SOURCE = "website/contact"' in tsrc
    # The item is POSTed as JSON to the configured endpoint.
    assert re.search(r'headers:\s*\{\s*"Content-Type":\s*"application/json"\s*\}', tsrc)
    assert "body: JSON.stringify(item)" in tsrc


def test_transport_decision_is_marked_open_with_the_reason() -> None:
    """The transport is deliberately unresolved, and the reason the form is an
    intake producer is stated … a reader must not mistake today's shape for the
    decision, nor read #393 as a quota-based refusal to build."""
    tsrc = _src(TRANSPORT_TS)
    assert "OPEN DECISION" in tsrc
    assert "NOT SETTLED" in tsrc
    assert "393" in tsrc, "the #393 budget must be named"
    assert "quota" in tsrc.lower(), "#393 is a managed quota budget"
    assert "no credential" in tsrc.lower() or "do not" in tsrc.lower(), (
        "the seam must say a send-capable credential is not to be provisioned"
    )


def test_open_decision_is_recorded_in_the_readme() -> None:
    """Recorded for the next reader, not only in the PR report."""
    readme = _src(README)
    section = readme[readme.index("## Contact form (#2409)") :]
    assert "hello@premiselabs.co" in section, "the decided recipient must be stated"
    assert "OPEN DECISION" in section, "the undecided transport must be flagged as open"
    assert "contact-transport.ts" in section, "the seam's location must be named"
    assert "393" in section and "quota" in section.lower(), (
        "the #393 managed budget behind the intake shape must be recorded"
    )
    assert "CONTACT_INTAKE_URL" in section, "the seam's one configuration must be named"
    assert "503" in section


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


# ── 6. The page, its surfacing, and hello@ visibility ─────────────────────


def test_contact_page_posts_to_the_function() -> None:
    html = _src(CONTACT_HTML)
    assert 'action="/api/contact"' in html, "form must post to the Pages Function"
    for field in ("name", "email", "message"):
        assert f'name="{field}"' in html, f"missing field {field}"
    assert 'name="hp"' in html, "honeypot field missing from the page"
    # Honeypot must be hidden from assistive tech as well as from sight.
    assert 'class="hp-field"' in html and 'aria-hidden="true"' in html


def test_hello_is_visible_plainly_on_the_outside_surface() -> None:
    """The owner ruling: where an outside surface shows an address it is
    `hello@`. It must be visible up front, not only in an error path."""
    html = _src(CONTACT_HTML)
    # Visible in the lede — before the form, outside any error/fallback block.
    lede = html[html.index('class="lede"') : html.index("<main>")]
    assert CONTACT_TO in lede, "the decided address must be visible up front"
    assert f'href="mailto:{CONTACT_TO}"' in lede, "the up-front address must be actionable"
    # And as the direct-mailto fallback below the form.
    fallback = html[html.index('class="fallback"') : html.index("<noscript>")]
    assert f'href="mailto:{CONTACT_TO}"' in fallback, "the fallback must name the decided address"
    # No other address may be shown on the outside surface.
    assert "support@" not in html


def test_hello_is_the_address_other_surfaces_show() -> None:
    """`docs.html` names a contact channel — it must show `hello@`, and no
    outside surface may show a competing address."""
    docs = _src(WEBSITE_DIR / "docs.html")
    assert f"mailto:{CONTACT_TO}" in docs
    for rel in ("index.html", "product.html", "faq.html", "docs.html", "contact.html"):
        assert "support@" not in _src(WEBSITE_DIR / rel), f"{rel} shows a non-hello@ address"


def test_contact_is_surfaced_from_the_pages_visitors_land_on() -> None:
    for rel in ("index.html", "product.html", "faq.html"):
        html = _src(WEBSITE_DIR / rel)
        assert re.search(r'href="/contact"', html), f"{rel} does not link to /contact"


def test_contact_variants_redirect_to_the_canonical() -> None:
    redirects = _src(REDIRECTS)
    assert re.search(r"^/contact/\s+/contact\s+301$", redirects, flags=re.M)
    assert re.search(r"^/contact\.html\s+/contact\s+301$", redirects, flags=re.M)


def test_ops_step_is_documented() -> None:
    """The one human action that makes intake live must be written down where
    an operator looks, not only in an issue."""
    readme = _src(README)
    assert "Contact form (#2409)" in readme
    assert "CONTACT_INTAKE_URL" in readme
    assert "503" in readme
