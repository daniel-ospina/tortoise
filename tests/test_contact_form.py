"""Static regression tests for the premiselabs.co contact form (#2409).

Guards the contract the form exists to honour — at the repo level, no network:

  1. DELIVERY TARGET IS SETTLED. The owner ruling (issue #2409, 2026-09-18) puts
     the outside-product channel at `hello@premiselabs.co`. It is a CODE
     CONSTANT in the Pages Function, never a request field — that is what keeps
     the form from being an open relay. A future edit that made the recipient
     configurable, or drifted it to another address, fails here.
  2. FAIL LOUDLY WHEN UNCONFIGURED. With the intake endpoint — or the intake's
     own inbound secret — absent, the endpoint must answer 503 `not_configured`
     with a human-actionable message, never a 200. This is the #3616 lesson
     applied to a second surface: a requirement that can only be read cannot
     fail. (The live proof is the curl probe in website/README.md; this pins the
     source that produces it.)
  3. THE FORM IS AN INTAKE PRODUCER, WITH NO PROVIDER CALL IN ITS PATH. The
     owner's architecture is WE RECEIVE: customers email hello@/support@ and
     intake processes the message; no reply is required for the product to
     work, and outbound email has exactly two legitimate homes — replying to a
     user who emailed first, and auth flows. So the form sends NO email: no
     `RESEND_*` read, no provider endpoint, no `Authorization`/`Bearer` header,
     and no send-capable credential. The ONE secret it reads is the intake's own
     inbound shared secret (`x-inbound-secret`), provisioned on the intake for
     this producer's source name: it submits an item and nothing else.
     `premise-labs#393` is a BUDGET TO MANAGE (objective: zero quota-rejected
     sends), NOT a reason to refuse to build — sending is not forbidden, it is
     simply not this leg's job.
  4. THE FORM IS SURFACED, AND `hello@` IS VISIBLE. `/contact` is a real page,
     linked from the company landing page, the product footer, the FAQ footer
     and the docs next-steps, and the trailing-slash / .html variants redirect
     to it. The decided address is shown plainly on the outside surface (lede
     and direct-mailto fallback), not hidden behind an error path.
  5. THE TRANSPORT IS A SINGLE SEAM, WIRED AT THE EXISTING INTAKE. One
     submission becomes one item in the intake's own envelope
     `{source, source_item_id, payload}` — posted, with `x-inbound-secret`, to
     the endpoint configured in `CONTACT_INTAKE_URL` — from
     `functions/_shared/contact-transport.ts`, behind `enqueue()`. The endpoint
     and its contract are the ones that already exist (one intake, many
     producers; premise-labs #426, relay ruling on #2409), and the outbound
     email leg remains the owner's open question: the owner's architecture, with
     #393 recorded as a budget to manage rather than a reason to refuse to
     build, must stay recorded in the code and in website/README.md for the next
     reader. The visitor-facing confirmation promises NO reply — intake is the
     receiving mechanism, and a promise nothing can keep is not a message to
     send.

Run:  python -m pytest tests/test_contact_form.py -v
"""
from __future__ import annotations

import re
from html import unescape
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

#: Verbs that promise the visitor a follow-up. The confirmation and the client
#: that displays it may use NONE of them, in any wording: intake is the receiving
#: mechanism and a reply is the exception. A verb family, not a sentence list,
#: because "pin the exact wording" was itself defeated by a promise phrased
#: differently (cycles 2-3), and every literal is scanned — not just the ones
#: anchored to `message:` — because a CONCATENATION hid its second half.
REPLY_PROMISE_VERBS = (
    # A first-person COMMITMENT: "we'll reply", "we will respond", "we can follow
    # up", "we aim to respond".
    r"\b(?:we|i)(?:'ll| will| shall| can| aim to| try to)\s+"
    r"(?:reply|respond|reaching out|reach out|write back|write to you|get back to you|"
    r"be in touch|be in contact|get in touch|follow up|answer|contact you)\b"
    # …or the present-tense form of the same promise: "we reply to every message".
    r"|\bwe\s+(?:reply|respond|answer|write back|follow up|get back to you)\b"
    # …or a promise made TO the visitor: "you'll hear from us".
    r"|\byou(?:'ll| will)\s+(?:hear|get a reply|receive a reply|be contacted)\b"
    r"|\bexpect a reply\b|\ba reply will\b"
)

#: The confirmation the visitor sees on success, pinned as a VALUE. It states
#: receipt and nothing more: a reply is the exception (outbound email belongs to
#: answering a user who wrote first), and while the reader gap is open
#: (swarm#18407) a promised reply is a commitment nothing can keep.
CONFIRMATION_EXACT = 'Thanks — we\'ve received your message.'

#: The email field's hint, pinned verbatim. It states a restriction on USE and
#: never a commitment to write back.
HINT_EMAIL_EXACT = "Only used to follow up on this message if we need to; never a mailing list."


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
    # The endpoint requires the intake's inbound secret too, so BOTH absences are
    # the same visible failure — a URL alone would be answered 401/403 and the
    # submission dropped, which is the silent loss this contract forbids.
    assert 'const INTAKE_SECRET_ENV = "CONTACT_INTAKE_SECRET"' in tsrc
    assert re.search(
        r'if\s*\(\s*intakeUrl\s*===\s*""\s*\|\|\s*intakeSecret\s*===\s*""\s*\)', tsrc
    ), "missing-endpoint/secret gate not found"
    assert '"x-inbound-secret": intakeSecret' in tsrc, (
        "the seam must send the header the intake requires"
    )
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


# ── 3. No email leg: no provider, no send-capable credential, no call ─────


def test_no_email_provider_or_send_capable_credential_in_the_form_path() -> None:
    """The email leg is ABSENT from the form's path. `premise-labs#393` is a
    BUDGET TO MANAGE (objective: zero quota-rejected sends), never a reason to
    refuse to build — the form routes into intake because receiving is the
    mechanism and sending is the exception.

    One credential DOES travel — the intake's own inbound secret — so the
    invariant is stated precisely: no SEND-CAPABLE credential, and no provider
    key of any kind.
    """
    for path in (FUNCTION_TS, TRANSPORT_TS):
        src = _src(path)
        low = src.lower()
        assert "resend" not in low, f"{path.name} names an email provider"
        assert "api.resend.com" not in low
        assert "RESEND_" not in src, f"{path.name} reads a RESEND_* variable"
        # No SEND-CAPABLE credential travels from the form. The one secret it
        # does send is the intake's inbound one (`x-inbound-secret`), which
        # submits an item and nothing else — send-capable provider headers stay
        # forbidden.
        assert "Bearer" not in src, f"{path.name} constructs a bearer header"
        assert "Authorization" not in src, f"{path.name} sets an authorization header"

    # Stronger: no source file under website/functions may name the sender or
    # its endpoint — the form's path is not the only place a stray key could
    # reappear.
    for path in (WEBSITE_DIR / "functions").rglob("*.ts"):
        text = path.read_text(encoding="utf-8")
        assert "api.resend.com" not in text, f"{path.relative_to(REPO_ROOT)} names the provider endpoint"
        assert "RESEND_" not in text, f"{path.relative_to(REPO_ROOT)} reads a RESEND_* variable"


def test_the_only_configuration_is_the_intake_endpoint_and_its_inbound_secret() -> None:
    """The seam reads exactly two variables — the endpoint and the intake's own
    inbound secret — and the function reads none of its own.

    The secret is required by the EXISTING intake, not by us: it derives the
    variable name from the source and answers 401/403 without a match. It is an
    inbound credential (it can submit an item and nothing else); no
    send-capable provider key is read anywhere.
    """
    tsrc = _src(TRANSPORT_TS)
    assert 'const INTAKE_URL_ENV = "CONTACT_INTAKE_URL"' in tsrc
    # Exactly two env reads, and the function performs none of its own.
    assert tsrc.count("envString(env, ") == 3, (
        "one read per variable in the gate, plus one in the gate's log line"
    )
    assert _src(FUNCTION_TS).count("envString(") == 0
    env_names = re.findall(r'^\s*const\s+\w*ENV\w*\s*=\s*"([^"]+)"', tsrc, flags=re.M)
    assert env_names == ["CONTACT_INTAKE_URL", "CONTACT_INTAKE_SECRET"], (
        f"unexpected env variables: {env_names}"
    )
    # The endpoint is not secret-shaped…
    for forbidden in ("API_KEY", "SECRET", "TOKEN", "PASSWORD"):
        assert forbidden not in env_names[0], f"the intake URL must not be secret-shaped: {env_names[0]}"
    # …and the one secret is the INTAKE's, not a provider's: nothing in the file
    # names a mail provider, and no send-capable name is read.
    for forbidden in ("RESEND", "SENDGRID", "MAILGUN", "POSTMARK", "API_KEY"):
        assert forbidden not in tsrc, f"a send-capable provider appears in the seam: {forbidden}"


def test_the_source_name_is_env_name_safe() -> None:
    """The source must survive being uppercased into an env-var NAME.

    The intake resolves its per-source secret as
    `INBOUND_SECRET_${source.toUpperCase()}` (swarm/supabase/functions/
    inbound-ingest/index.ts), so a slash or a space would ask for a variable no
    platform can set — and the endpoint would answer `403 unknown_source`. The
    earlier `website/contact` form of this constant could never have worked.
    """
    tsrc = _src(TRANSPORT_TS)
    assert 'INTAKE_SOURCE = "website_contact"' in tsrc
    source = re.search(r'INTAKE_SOURCE = "([^"]+)"', tsrc).group(1)
    assert re.fullmatch(r"[A-Za-z0-9_]+", source), (
        f"the source must be env-name-safe (uppercased into INBOUND_SECRET_<SRC>): {source!r}"
    )


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
    """One item, in the intake's OWN envelope: `source`, `source_item_id`,
    `payload` — the submission inside the payload, plus the text fields the
    intake reads."""
    tsrc = _src(TRANSPORT_TS)
    assert "source: INTAKE_SOURCE" in tsrc, "the item must carry its producing surface"
    assert "source_item_id: crypto.randomUUID()" in tsrc, (
        "the intake dedupes on (source, source_item_id): it must be minted per submission"
    )
    assert re.search(r"payload:\s*\{", tsrc), "the submission travels in the payload"
    assert "name: msg.name" in tsrc
    assert "replyTo: msg.replyTo" in tsrc
    assert "message: msg.message" in tsrc
    assert "receivedAt = new Date().toISOString()" in tsrc, "the payload must carry a receipt time"
    # `payload.subject`/`payload.body` are what the intake extracts as the item's
    # text (they feed its pre-LLM risk scan), so the message must be readable
    # there — not only as JSON — and the reply-to address must travel with it.
    assert "subject: `Contact form" in tsrc, "the item needs a readable subject"
    assert "body: `${msg.message}" in tsrc, "the item needs a readable body"
    assert "<${msg.replyTo}>" in tsrc, "the intake's text must carry the reply-to address"
    # The item is POSTed as JSON to the configured endpoint, with the header the
    # intake requires.
    assert '"Content-Type": "application/json"' in tsrc
    assert '"x-inbound-secret": intakeSecret' in tsrc
    assert "body: JSON.stringify(item)" in tsrc


def test_the_confirmation_promises_no_reply() -> None:
    """The visitor-facing confirmation must be factual, and must not promise a
    reply.

    Intake is the receiving mechanism and a reply is the exception (outbound
    email belongs to replying to a user who wrote first). While the reader gap
    is open (swarm#18407; the #426 interim copy is unbound), "we'll reply" is a
    commitment this transport cannot keep — so the confirmation says only what
    is true at that moment: the message was received. This is the relay's
    condition on shipping the form, and it is pinned here because copy is the
    easiest thing to soften without noticing.
    """
    src = _src(FUNCTION_TS)
    html_src = _src(CONTACT_HTML)
    # Pin the success literal by VALUE, with whitespace tolerance: a substring
    # check accepted `"…received your message." + " We'll reach out shortly."`
    # (cycle-2 review), and a single-line-only regex rejected a purely cosmetic
    # reflow (cycle-3 review).
    assert re.search(r'message:\s*"' + re.escape(CONFIRMATION_EXACT) + r'"', src), (
        "the success return must carry the receipt-only confirmation verbatim"
    )
    # …and then scan EVERY string literal in the file, not just the ones anchored
    # to `message:`. Three escapes made the anchored versions insufficient: a
    # reworded promise in the success branch (cycle-2), a decoy `return json(…)`
    # appended after the handler (cycle-3), and a CONCATENATED promise
    # (`"…received your message." + " We'll reach out shortly."`), whose second
    # half an anchored scan never reads (cycle-3). Comments are stripped first, so
    # the file's extensive prose about email is not scanned — only what the code
    # could show a visitor.
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    literals = re.findall(
        r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`', code, re.S
    )
    assert literals, "no string literals found — did the file shape change?"
    for text in literals:
        assert not re.search(REPLY_PROMISE_VERBS, text, re.I), (
            f"contact.ts carries a reply promise the visitor could be shown: {text}"
        )
    # The visitor-facing confirmation has TWO surfaces: the server literal above,
    # and the client that displays it. Cycle-3 review shipped a promise by editing
    # ONLY the client — `show(msg, "Thanks — we'll reply within two business
    # days.")` kept all 86 tests green — so the success branch must consume the
    # server's message, and the client's own fallback is pinned by value.
    script = html_src[html_src.index("<script>") :]
    assert re.search(
        r'show\(\s*msg\s*,\s*data\.message\s*\|\|\s*"Thanks — your message is on its way\."\s*\)',
        script,
    ), (
        "the success branch must display the server's message (`data.message`) with its "
        "own fallback pinned — the client cannot substitute its own confirmation"
    )
    # The whole PAGE is visitor-facing, not just its script: the static copy, the
    # lede and the noscript fallback are what a JS-off reader sees, and appending
    # a promise to the noscript paragraph kept the suite green when this scan
    # started at `<script>` (cycle-4 review). Rendered as a browser reads it —
    # scripts and styles dropped, tags to spaces, entities unescaped, whitespace
    # collapsed.
    page_markup = re.sub(r"<(script|style)\b.*?</\1>", " ", html_src, flags=re.S | re.I)
    page_text = " ".join(unescape(re.sub(r"<[^>]+>", " ", page_markup)).split())
    assert not re.search(REPLY_PROMISE_VERBS, page_text, re.I), (
        "the contact page promises a reply: "
        f"{re.search(REPLY_PROMISE_VERBS, page_text, re.I).group(0)!r}"
    )
    # No client-side string may promise a reply either (comments excluded: the
    # script explains the 503 path in prose).
    client_code = "\n".join(
        line for line in script.splitlines() if not line.strip().startswith(("//", "*", "/*"))
    )
    client_strings = re.findall(
        r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`)', client_code
    )
    for text in client_strings:
        assert not re.search(REPLY_PROMISE_VERBS, text, re.I), (
            f"the contact page's script promises a reply: {text}"
        )
    # The email field's hint states a restriction on USE. Pinned as the WHOLE
    # sentence — a verb list cannot hold "no promise" (cycle-2) — compared as
    # RENDERED text, so a cosmetic reflow or an entity form is not a failure
    # (cycle-3).
    hint = html_src[html_src.index('id="contact-email"') : html_src.index('id="contact-message"')]
    hint_text = re.search(r"<p[^>]*>(.*?)</p>", hint, re.S)
    assert hint_text is not None, f"cannot read the email hint: {hint!r}"
    rendered = " ".join(unescape(re.sub(r"<[^>]+>", " ", hint_text.group(1))).split())
    assert rendered == HINT_EMAIL_EXACT, (
        "the email hint must state a use-restriction and never a reply promise, got: "
        f"{rendered!r}"
    )


def test_the_intake_contract_is_recorded_in_the_seam() -> None:
    """The three non-obvious facts about the existing intake stay written down,
    so a future edit cannot "simplify" the envelope back into a shape the
    endpoint answers 403 to."""
    tsrc = _src(TRANSPORT_TS)
    assert "inbound-ingest" in tsrc, "the endpoint's function must be named"
    assert "INBOUND_SECRET_" in tsrc, "the per-source secret derivation must be stated"
    assert "unknown_source" in tsrc, "the 403 the wrong source name earns must be recorded"
    assert "one intake, many producers" in tsrc.lower(), "the architecture must be named"
    assert "x-inbound-secret" in tsrc, "the required header must be named"


def test_transport_decision_is_marked_open_with_the_reason() -> None:
    """The transport is deliberately unresolved, and the reason the form is an
    intake producer is stated … a reader must not mistake today's shape for the
    decision, nor read #393 as a quota-based refusal to build."""
    tsrc = _src(TRANSPORT_TS)
    assert "OPEN DECISION" in tsrc
    assert "NOT SETTLED" in tsrc
    assert "393" in tsrc, "the #393 budget must be named"
    assert "quota" in tsrc.lower(), "#393 is a managed quota budget"
    assert "send-capable credential" in tsrc.lower(), (
        "the seam must state that no SEND-capable credential is provisioned"
    )
    assert "x-inbound-secret" in tsrc, (
        "the inbound credential it DOES carry must be named, not implied away"
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
    # Scope the prose pins to the sentences that carry the contract. A presence
    # check over the whole section is satisfiable by a token that appears only in
    # the ASCII diagram or the human-steps list (cycle-1 review), which would let
    # the documenting sentence be deleted while the pin stayed green.
    config = section[section.index("**Configuration:**") : section.index("**Spam:**")]
    assert "CONTACT_INTAKE_URL" in config and "CONTACT_INTAKE_SECRET" in config, (
        "the Configuration paragraph must name BOTH seam variables"
    )
    fail_loud = config[config.index("**Fail-loud contract:**") :]
    assert "503" in fail_loud and "not_configured" in fail_loud, (
        "the fail-loud contract must state the 503/not_configured outcome"
    )
    assert "**No promise of a reply.**" in section, (
        "the no-reply confirmation must be stated for the next reader"
    )


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
    an O(n) rebuild per request.

    The slice starts at the CAP's own guard, not at the `hits.size >
    MAX_RATE_KEYS` string anywhere in the file: that string was satisfiable from
    inside the expired-keys sweep above it (cycle-1 review), from a `/* … */`
    block comment (cycle-4), and from a dead `if (false)` branch (cycle-4).

    ⚠️  WHAT THIS PINS, AND WHAT IT CANNOT. It gates the SHAPE of the cap — the
    guard, a cap-derived `excess`, and an eviction loop that breaks at the cap,
    deletes and counts down, on the function's only un-limited exit, with no
    disabled branch in the path. It does not PROVE the cap is reachable: a
    sufficiently contrived refactor (a dead helper holding the loop, an
    equivalent-but-unrecognised shape) is outside a static gate's reach. The
    behavioural answer is a TS harness that fills the map and asserts its bound;
    the Pages Functions have none (#4108).
    """
    src = _src(FUNCTION_TS)
    assert "MAX_RATE_KEYS" in src
    # The cap VALUE matters too: a 1000x bump bounds nothing in practice, and the
    # token assertions below would all still hold (cycle-4 review, honourable
    # mention).
    cap_value = re.search(r"MAX_RATE_KEYS\s*=\s*(\d+)", src)
    assert cap_value is not None, "MAX_RATE_KEYS must have a numeric value"
    assert 100 <= int(cap_value.group(1)) <= 50_000, (
        f"MAX_RATE_KEYS must be a real bound, got {cap_value.group(1)}"
    )
    # Strip BOTH comment forms FIRST: a commented-out eviction loop supplied every
    # pinned token as TEXT and kept an earlier version of this pin green (cycle-2
    # review), and stripping only `//` left `/* … */` able to do the same
    # (cycle-4 review).
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    # A dead branch keeps every token readable while eviction never runs —
    # `if (false) if (hits.size > MAX_RATE_KEYS) { … }` passed this pin
    # (cycle-4 review).
    assert not re.search(r"\b(?:if|while)\s*\(\s*(?:false|0)\s*\)", code), (
        "a disabled branch cannot guard the rate-limit cap"
    )
    # …and an early `return false` would make the cap unreachable with the loop
    # below still intact, so the cap must sit on the function's ONLY un-limited
    # exit.
    fn_start = code.index("function rateLimited")
    fn_exit = code.index("return false;", fn_start) + len("return false;")
    assert code[fn_start:fn_exit].count("return false") == 1, (
        "the cap must be on the function's only un-limited exit — an earlier `return false` "
        "would make this pin vacuous"
    )
    # The GUARD is part of the cap: inverting it (`<` instead of `>`) disables
    # eviction entirely while the loop below still reads correctly, and the guard
    # sat outside the old slice (cycle-3 review).
    guard = re.search(r"if \(hits\.size > MAX_RATE_KEYS\)", code)
    assert guard is not None, "the cap guard must compare the map size to MAX_RATE_KEYS"
    cap = code[guard.end() : code.index("return false;")]
    # Bind the initializer to the cap AND make it the only assignment: `excess = 0;`
    # after a correct `let excess = …` breaks out immediately, leaving the map
    # unbounded under live keys, while every token still appears (cycle-3 review).
    assert len(re.findall(r"\bexcess\s*=(?!=)", cap)) == 1, (
        "excess must be assigned exactly once — from the cap"
    )
    assert re.search(r"let excess\s*=\s*hits\.size\s*-\s*MAX_RATE_KEYS", cap), (
        "excess must be derived from the cap, not a constant"
    )
    loop = cap[cap.index("for (const k of hits.keys())") :]
    assert re.search(r"if \(excess <= 0\) break;", loop), "the loop must stop at the cap"
    assert re.search(r"hits\.delete\(k\);", loop), "no key eviction under the cap"
    assert re.search(r"excess--;|excess -= 1;", loop), "the loop must count down"


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
