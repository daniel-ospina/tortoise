"""Regression tests for the premiselabs.co contact form (#2409).

Most of this file is STATIC source analysis, at the repo level and without
network access. Four fixes cannot be proven by reading source — the byte cap,
the redirect policy, the honeypot's confirmation and the log contents are
BEHAVIOURAL — so section 7 executes the two modules under Node (Deno as a
fallback) with a stub `fetch` and asserts the outcomes (see `_CONTACT_HARNESS`).

Guards the contract the form exists to honour:

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
import shutil
import subprocess
import tempfile
from html import unescape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBSITE_DIR = REPO_ROOT / "website"

FUNCTION_TS = WEBSITE_DIR / "functions" / "contact" / "submit.ts"
TRANSPORT_TS = WEBSITE_DIR / "functions" / "_shared" / "contact-transport.ts"
CONTACT_HTML = WEBSITE_DIR / "contact.html"
REDIRECTS = WEBSITE_DIR / "_redirects"
README = WEBSITE_DIR / "README.md"

#: The owner-decided outside-product destination. Changing this is a product
#: decision, not a code change — it must be argued in the issue, not here.
CONTACT_TO = "hello@premiselabs.co"

#: The reply words those commitments land on.
REPLY_WORD = (
    r"(?:repl(?:y|ies|ying)|respond|response|answer|reaching out|reach out|"
    r"write back|write to you|get back to you|be in touch|be in contact|"
    r"get in touch|follow(?:ing)? up|hear from|be contacted|contact you)\b"
)

#: Reply promises, as a VOCABULARY of the shapes copy actually uses: a
#: first-person/team/visitor subject within a short span of a reply word. A reply
#: promise is a natural-language property and no regex decides it, so this is not
#: a proof — it recognises the commitment shapes below and fails closed on them
#: (a sentence that merely NEGATES a promise trips it too; the fix there is to
#: phrase it without the commitment verb). A phrasing outside the shape is not
#: caught; the durable answer is a product-level check or the behavioural harness
#: filed as #4108. Coverage is asserted by TEST CASES at the bottom of this file,
#: not by this prose.
REPLY_PROMISE_VERBS = (
    # A subject that can make the promise, then a commitment marker, then a reply
    # word: "our team will reply…", "we will send you a response", "we'll be sure
    # to respond" (cycle-7 review: the earlier arms keyed on `we|i` only and
    # missed `our team`, and on immediate adjacency so "send you a response"
    # passed).
    r"\b(?:we|i|our team|the team|support)\b[^.!?]{0,30}?"
    r"(?:'ll| will| shall| going to| aim to| try to| be sure to| always| usually)"
    r"[^.!?]{0,25}?" + REPLY_WORD
    # …or the present-tense first person: "we reply to every message".
    + r"|\bwe\b[^.!?]{0,20}?" + REPLY_WORD
    # …or a promise made TO the visitor: "you will receive a response". `can` is
    # deliberately NOT a commitment marker here, so an invitation to write to us
    # ("you can also get in touch at that address") is not a promise.
    + r"|\byou(?:'ll| will)\b[^.!?]{0,25}?" + REPLY_WORD
)

def _brace_end(text: str, start: int) -> int:
    """Index just past the `}` that closes the block whose `{` follows `start`.

    Used to bound a function to its REAL body: a slice that ends at the first
    `return false;` inside it makes a "exactly one `return false`" assertion
    vacuous by construction (cycle-14 review).
    """
    depth, opened = 0, False
    for i in range(text.index("{", start), len(text)):
        if text[i] == "{":
            depth, opened = depth + 1, True
        elif text[i] == "}":
            depth -= 1
            if opened and depth == 0:
                return i + 1
    raise AssertionError(f"unbalanced braces after offset {start}: cannot bound the function")


#: A quoted string in CSS/JS source, one group per quote style, escapes included.
_QUOTED = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'')

#: `const NAME = "literal"` — an identifier bound to a string literal.
_CONST_LITERAL = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)\s*[;,)]"
)


def _inline_constants(text: str) -> str:
    """Substitute identifiers bound to a string literal with that literal's text.

    A promise split across DISTANT literals — one half hoisted into a named
    constant and concatenated at the call site — defeats any proximity search over
    raw source: the assembled scan sees the IDENTIFIER, not the text the visitor
    reads, so `"Thanks…" + SUBJECT + " reply soon."` stayed green while rendering a
    promise (cycle-17 review). Substituting the literal back in makes the halves
    adjacent again, so the assembled scan sees what renders.
    """
    for name, literal in {m.group(1): m.group(2) for m in _CONST_LITERAL.finditer(text)}.items():
        # Not a property NAME (`NAME:`) — only a reference to the binding.
        text = re.sub(rf"\b{re.escape(name)}\b(?!\s*:)", literal, text)
    return text


#: Source-level escape spellings that RENDER as other characters: CSS hex escapes
#: (`"\57 e'll…"` is `"We'll…"`) and JS unicode/hex escapes (`"\u0057e'll…"`). A
#: scan over raw source sees the escape, not the text the visitor reads, so the
#: escapes are decoded before matching (cycle-13 review).
_ESCAPE = re.compile(
    r"\\(?:([0-9a-fA-F]{1,6})[ \t]?|u\{([0-9a-fA-F]{1,6})\}|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2}))"
)


def _decode_escapes(text: str) -> str:
    """Decode the escape spellings a browser renders as other characters."""

    def repl(m: re.Match[str]) -> str:
        digits = next(g for g in m.groups() if g is not None)
        codepoint = int(digits, 16)
        return chr(codepoint) if 0 < codepoint <= 0x10FFFF else ""

    return _ESCAPE.sub(repl, text)
#: The limiter's body, VERBATIM (comments stripped, whitespace collapsed) — see the
#: pin in `test_rate_limit_map_is_bounded` for why this exists. Note the tripwire's
#: intended noise: a predicate rename, a type annotation, or any added statement is
#: a CHANGE here — re-read the limiter, then update this constant.
RATE_LIMITED_BODY = (
    "function rateLimited(ip: string, now: number): boolean { const cutoff = now - "
    "RATE_WINDOW_MS; const recent = (hits.get(ip) || []).filter((t) => t > cutoff); if "
    "(recent.length >= RATE_LIMIT) { hits.set(ip, recent); return true; } "
    "recent.push(now); hits.delete(ip); hits.set(ip, recent); if (hits.size > "
    "MAX_RATE_KEYS) { for (const [k, v] of hits) { if (v.every((t) => t <= cutoff)) "
    "hits.delete(k); } let excess = hits.size - MAX_RATE_KEYS; for (const k of "
    "hits.keys()) { if (excess <= 0) break; hits.delete(k); excess--; } } "
    "return false; }"
)

#: The limiter's CALL SITE, VERBATIM (comments stripped, whitespace collapsed) — the
#: key derivation must stay this, and nothing may touch `hits` before the call.
#: `const ip = crypto.randomUUID()` gives every request its own key and
#: `hits.clear();` before the call wipes the history, and either one makes the
#: limiter unable to throttle anyone while the body pin above is untouched
#: (cycle-25 review).
RATE_LIMIT_CALL_SITE = (
    'const ip = request.headers.get("CF-Connecting-IP") || "unknown"; if '
    "(rateLimited(ip, Date.now())) { return fail( 429, \"rate_limited\", \"Too many "
    "messages from this connection. Please wait a few minutes, or email "
    "hello@premiselabs.co directly.\", { \"Retry-After\": String(Math.ceil(RATE_WINDOW_MS "
    "/ 1000)) }, ); }"
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


def _strip_comments(source: str) -> str:
    """Remove TS/JS comments WITHOUT eating string content.

    A `//` inside a literal is not a comment start — a URL (`https://…`) is the
    everyday case — and stripping comments regex-first truncated the literal and
    hid everything after it, including a reply promise appended to a
    visitor-facing message (cycle-9 review, fail-open). So quoted spans are
    copied verbatim and only real comments are dropped: line comments, block
    comments, and backtick templates.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch in "\"'`":
            j = i + 1
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == ch:
                    j += 1
                    break
                j += 1
            out.append(source[i:j])
            i = j
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end == -1 else end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


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
        # forbidden. Checked on COMMENT-STRIPPED code: a comment cannot travel,
        # so a note documenting the rule must not red the suite (cycle-6 review).
        code_only = _strip_comments(src)
        assert "Bearer" not in code_only, f"{path.name} constructs a bearer header"
        assert "Authorization" not in code_only, f"{path.name} sets an authorization header"

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

    WHAT THIS PIN CANNOT DO: it is a static scan, so a promise assembled by
    EVALUATED indirection — a function returning half of it, a computed lookup
    (`MAP.k`, `["a", "b"][1]`), a getter, `String.raw`, or a string imported from
    another module — is outside its reach (cycle-18 review). The client's own
    fallback is the exception: being a single fixed literal, it is pinned as an
    EXACT expression rather than scanned, so no concatenation or call can hide
    there. The behavioural answer for the rest is the TS harness filed as #4108.
    """
    src = _src(FUNCTION_TS)
    html_src = _src(CONTACT_HTML)
    # Strip comments first, so the file's extensive prose about email is not
    # scanned — only what the code could show a visitor — and then take EVERY
    # string literal in the file, not just the ones anchored to `message:`. Three
    # escapes made the anchored versions insufficient: a reworded promise in the
    # success branch (cycle-2), a decoy `return json(…)` appended after the
    # handler (cycle-3), and a CONCATENATED promise (`"…received your message." +
    # " We'll reach out shortly."`), whose second half an anchored scan never reads
    # (cycle-3).
    code = _strip_comments(src)
    literals = re.findall(
        r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`', code, re.S
    )
    assert literals, "no string literals found — did the file shape change?"
    # Pin the VALUE: the receipt-only confirmation must be present as a literal.
    # Extracting it into a named constant is a legitimate, value-preserving edit,
    # so the pin is membership, not adjacency (cycle-7 review).
    assert f'"{CONFIRMATION_EXACT}"' in literals, (
        "the receipt-only confirmation must appear as a string literal"
    )
    for text in literals:
        assert not re.search(REPLY_PROMISE_VERBS, _decode_escapes(text), re.I), (
            f"contact.ts carries a reply promise the visitor could be shown: {text}"
        )
    # …and the WHOLE comment-stripped source too, so a promise SPLIT at a literal
    # boundary is still seen: `"Thanks…" + " " + "We'll " + "reply soon."` passes a
    # per-literal scan (cycle-8 review) while the assembled message the server
    # returns is a promise. Escapes are decoded first, since `\u0057e'll` is the
    # same promise written so a raw scan cannot see it (cycle-13 review).
    assembled = re.search(REPLY_PROMISE_VERBS, _decode_escapes(_inline_constants(code)), re.I)
    assert assembled is None, (
        f"contact.ts assembles a reply promise: {assembled and assembled.group(0)!r}"
    )
    # The visitor-facing confirmation has TWO surfaces: the server literal above,
    # and the client that displays it. Cycle-3 review shipped a promise by editing
    # ONLY the client — `show(msg, "Thanks — we'll reply within two business
    # days.")` kept all 86 tests green — so the success branch must consume the
    # server's message, and its fallback is pinned BY VALUE among the scanned
    # literals (a hoisted constant is a legitimate edit; cycle-7 review).
    script = html_src[html_src.index("<script>") :]
    # Comments are stripped from the SCRIPT the same way as the TS file, and the
    # structural pins read the STRIPPED text: a commented-out
    # `// show(msg, data.message || "Thanks — your message is on its way.")`
    # satisfied both of them while the live branch showed something else
    # (cycle-8 review).
    script_code = _strip_comments(script)
    assert re.search(r"show\(\s*msg\s*,\s*data\.message\s*\|\|", script_code), (
        "the success branch must display the server's message (`data.message`)"
    )
    # …and the fallback must BE the reviewed literal, not merely contain it.
    # Membership let `"Thanks — your message is on its way." + half() + " reply
    # soon."` through: `half()` returns `" We'll"`, so no single literal carries
    # both a subject and a reply word, and the rendered defensive path is a promise
    # (cycle-18 review). Pinned as an exact expression over the constant-INLINED
    # script, so hoisting the string into a constant stays legitimate while any
    # concatenation or evaluated call there does not.
    inlined_script = _inline_constants(script_code)
    assert re.search(
        r'data\.message\s*\|\|\s*"Thanks — your message is on its way\."\s*[;,)]',
        inlined_script,
    ), (
        "the success fallback must be exactly the reviewed literal — a concatenation "
        "or a computed string there is visitor-facing text no promise scan can read"
    )
    # The whole PAGE is visitor-facing, not just its script: the static copy, the
    # lede and the noscript fallback are what a JS-off reader sees, and appending
    # a promise to the noscript paragraph kept the suite green when this scan
    # started at `<script>` (cycle-4 review). Rendered as a browser reads it —
    # scripts and styles dropped, tags to spaces, entities unescaped, whitespace
    # collapsed.
    page_markup = re.sub(r"<(script|style)\b.*?</\1>", " ", html_src, flags=re.S | re.I)
    # CSS-generated copy is rendered text the visitor reads — and a screen reader
    # announces it — so `content:` values are scanned rather than discarded with
    # the block: `.lede::after { content: "We'll reply within two business days." }`
    # showed the promise to every visitor while every pin stayed green, because
    # `<style>` was dropped wholesale (cycle-10 review).
    # EVERY quoted string inside a `<style>` block is scanned, not just the ones
    # in a `content:` declaration — the property-name shape is refused by
    # construction instead of spelled out, which is what the last three cycles
    # kept finding one spelling at a time:
    #   * CSS property names are ASCII case-insensitive (`CONTENT:` renders).
    #   * A `content` VALUE is a LIST of strings that CONCATENATE: `content:
    #     "Thanks! " "We'll reply soon."` is one promise, and a first-string-only
    #     scan stayed green (cycle-12 review).
    #   * Copy is reachable through a custom property — `--msg: "We'll reply
    #     soon."; content: var(--msg)` — so scoping to `content:` misses it.
    # Each block contributes its strings AND their concatenation (so a promise
    # split at a string boundary is still assembled), and comments are stripped
    # first: they cannot render, and a rule documented in one must not fail the
    # suite (cycle-10 review).
    css_text = " ".join(
        _decode_escapes(text)
        for block in re.findall(r"<style\b[^>]*>(.*?)</style>", html_src, flags=re.S | re.I)
        for stripped in [re.sub(r"/\*.*?\*/", "", block, flags=re.S)]
        for strings in [
            [unescape(q.group(1) or q.group(2) or "") for q in _QUOTED.finditer(stripped)]
        ]
        for text in [*strings, " ".join(strings)]
    )
    # ATTRIBUTE-carried copy is visitor-facing too (cycle-5 review): the meta
    # description is what search engines and link previews show, and a
    # placeholder / aria-label / title / alt is text the visitor reads in the
    # page. Replacing whole tags dropped all of it.
    #
    # EVERY attribute value is scanned, in either quote style — an enumerated
    # name list is the same mistake one level down: `aria-description` (announced
    # by a screen reader) and a single-quoted `content` both escaped it
    # (cycle-6 review).
    attr_text = " ".join(
        unescape(m.group(1) if m.group(1) is not None else m.group(2))
        for m in re.finditer(r'''=\s*(?:"([^"]*)"|'([^']*)')''', page_markup)
    )
    page_text = " ".join(
        (unescape(re.sub(r"<[^>]+>", " ", page_markup)) + " " + attr_text + " " + css_text).split()
    )
    found = re.search(REPLY_PROMISE_VERBS, page_text, re.I)
    assert found is None, (
        "the contact page promises a reply (in its text, an attribute or CSS "
        f"content): {found and found.group(0)!r}"
    )
    # No client-side string may promise a reply either — and the scan runs over
    # the whole stripped script as well as each literal, because a promise SPLIT at
    # a literal boundary (`"We'll " + "reply soon."`) passes a per-literal scan
    # (cycle-8 review) while the assembled text the visitor reads is a promise.
    client_strings = re.findall(
        r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`)', script_code
    )
    for text in client_strings:
        assert not re.search(REPLY_PROMISE_VERBS, _decode_escapes(text), re.I), (
            f"the contact page's script promises a reply: {text}"
        )
    # …and the assembled script, which the comment above has claimed since cycle 8
    # while only the per-literal loop existed: `show(msg, data.message ||
    # ("Thanks…" + " We'll " + "reply soon."))` kept the whole suite green, because
    # no single literal carries both a subject and a reply word (cycle-13 review).
    assembled_client = re.search(
        REPLY_PROMISE_VERBS, _decode_escapes(_inline_constants(script_code)), re.I
    )
    assert assembled_client is None, (
        f"the contact page's script assembles a reply promise: "
        f"{assembled_client and assembled_client.group(0)!r}"
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
    block comment (cycle-4), and it could be nested inside a disabled branch —
    `if (false)` (cycle-4) or `if (!true)` (cycle-6) — so its brace depth in the
    function body is asserted now rather than its spelling.

    ⚠️  WHAT THIS PINS, AND WHAT IT CANNOT. It gates the SHAPE of the cap — the
    guard, a cap-derived `excess`, and an eviction loop that breaks at the cap,
    deletes and counts down, on the function's only un-limited exit, with no
    disabled branch in the path. It does not PROVE the cap is reachable: a
    sufficiently contrived refactor (a dead helper holding the loop, an
    equivalent-but-unrecognised shape) is outside a static gate's reach. The
    behavioural answer is a TS harness that fills the map and asserts its bound;
    the Pages Functions have none (#4108).

    The limiter as a whole is now pinned the same way, and the limit is
    DEMONSTRATED rather than assumed: twenty-seven review cycles each found a real
    escape, and the last four independently reached the same conclusion. The pins
    below certify the reviewed shape and its values — the body verbatim
    (`RATE_LIMITED_BODY`), the call site verbatim (`RATE_LIMIT_CALL_SITE`), every
    `hits` reference confined to those regions or the declaration, and the store's
    own type. They cannot certify BEHAVIOUR, and no finite set of them can:
    `recent.length = 0;` in three positions, `&& false`, `hits.set(ip, [])`,
    `hits.clear()` (four locations), `crypto.randomUUID()` as the key,
    `Date.now = () => NaN` and a honeypot condition that always fires were each
    green against a static pin set that had just been extended to catch the
    previous one (cycles 19-27). A static pin's defeat surface over a mutable
    implementation is unbounded; the honest guard is behavioural — execute the
    handler and assert the sixth request is 429'd — which is #4108, and that is
    where the next fix for this file belongs.
    """
    src = _src(FUNCTION_TS)
    assert "MAX_RATE_KEYS" in src
    # Strip comments FIRST, with the scanner that leaves string content intact:
    # every assertion below reads `code`, because a comment can otherwise satisfy
    # any token test — it did for the eviction loop (cycles 2/4) and for the cap
    # VALUE itself (cycle-8 review).
    code = _strip_comments(src)
    # The cap VALUE matters too: a 1000x bump bounds nothing in practice, and the
    # token assertions below would all still hold (cycle-4 review, honourable
    # mention).
    cap_value = re.search(r"MAX_RATE_KEYS\s*=\s*(\d+)", code)
    assert cap_value is not None, "MAX_RATE_KEYS must have a numeric value"
    assert 100 <= int(cap_value.group(1)) <= 50_000, (
        f"MAX_RATE_KEYS must be a real bound, got {cap_value.group(1)}"
    )
    # The limiter's OWN threshold matters at least as much, and is pinned the same
    # way: `RATE_LIMIT = 1_000_000_000` makes `recent.length >= RATE_LIMIT`
    # untrippable — throttling silently becomes a no-op while every token assertion
    # above still holds (cycle-10 review). The window is written as arithmetic
    # (`10 * 60 * 1000`), so only a product of integer literals is accepted and the
    # value is computed from the factors — never `eval`.
    for name, lo, hi in (("RATE_LIMIT", 1, 100), ("RATE_WINDOW_MS", 60_000, 86_400_000)):
        defined = re.search(rf"{name}\s*=\s*([^;]+);", code)
        assert defined is not None, f"{name} must be defined"
        literal = defined.group(1).strip()
        assert re.fullmatch(r"[\d_]+(?:\s*\*\s*[\d_]+)*", literal), (
            f"{name} must be a product of integer literals, got {literal!r}"
        )
        value = 1
        for factor in re.split(r"\s*\*\s*", literal):
            value *= int(factor.replace("_", ""))
        assert lo <= value <= hi, (
            f"{name} must be a real bound, got {literal} = {value}: outside [{lo}, {hi}] "
            "throttling is effectively disabled"
        )
    # Strip BOTH comment forms FIRST: a commented-out eviction loop supplied every
    # pinned token as TEXT and kept an earlier version of this pin green (cycle-2
    # review), and stripping only `//` left `/* … */` able to do the same
    # (cycle-4 review).
    # A disabled branch keeps every token readable while eviction never runs —
    # `if (false) if (hits.size > MAX_RATE_KEYS) { … }` passed an earlier version
    # of this pin (cycle-4 review), and so did `if (!true)` (cycle-6), which no
    # spelling list can enumerate. So the guard's NESTING is checked instead: it
    # must sit directly in the function body, at brace depth 1. Any enclosing
    # block — whatever its condition — makes it unreachable and fails here.
    fn_start = code.index("function rateLimited")
    guard_at = code.index("if (hits.size > MAX_RATE_KEYS)", fn_start)
    depth = code[fn_start:guard_at].count("{") - code[fn_start:guard_at].count("}")
    assert depth == 1, (
        f"the cap guard is nested at brace depth {depth}: an enclosing branch can disable "
        "the cap while every token stays readable"
    )
    # …and a BRACELESS enclosing branch adds no brace at all, so the depth check
    # misses it: `if (!true) if (hits.size > MAX_RATE_KEYS) { … }` disabled the cap
    # with the suite green (cycle-6 review). The guard must therefore begin its own
    # statement — nothing between the previous statement boundary and the guard but
    # whitespace.
    stmt = re.split(r"[;{}]", code[fn_start:guard_at])[-1]
    assert not re.search(r"\b(?:if|while|for)\b", stmt), (
        "the cap guard must not be the braceless body of another branch: "
        f"{stmt.strip()[:80]!r}"
    )
    # …and the limiter's own TRIP must still be a trip, and the function must have
    # exactly ONE un-limited exit — measured against its real body. The earlier
    # version sliced to the FIRST `return false;` in it, which makes
    # `count("return false") == 1` true by construction: rewriting the trip
    # (`if (recent.length >= RATE_LIMIT) { … return true; }`) to `return false;`
    # then slipped the slice's end up to that line, so every other assertion here
    # still held while no submission could ever be 429'd (cycle-14 review).
    fn_end = _brace_end(code, fn_start)
    body = code[fn_start:fn_end]
    assert body.count("return false;") == 1, (
        "the limiter must have exactly one un-limited exit — a second `return false;` "
        "(for instance on its own trip) makes throttling a no-op"
    )
    trip = re.search(r"if\s*\(\s*recent\.length\s*>=\s*RATE_LIMIT\s*\)", body)
    assert trip is not None, "the limiter's trip comparison must be present"
    # The trip is bounded to its REAL block and its exit must be a DIRECT statement
    # of that block: the earlier `[^}]*` span stopped at the first nested `}`, so
    # `if (recent.length >= RATE_LIMIT) { if (false) { return true; } ... }` kept
    # the token inside the matched span while the real branch fell through to
    # `return false;` and the limiter stayed a no-op (cycle-16 review). Same
    # technique as the cap guard's nesting test.
    trip_at = trip.start()
    trip_block = body[trip_at : _brace_end(body, trip_at)]
    assert re.search(r"\breturn true;", trip_block), (
        "the limiter's trip must RETURN TRUE: `return false;` there never throttles "
        "anyone while every other pin stays green"
    )
    trip_inner = trip_block[trip_block.index("{") + 1 : -1]
    trip_head = trip_inner[: trip_inner.index("return true;")]
    assert trip_head.count("{") - trip_head.count("}") == 0, (
        "the trip's `return true;` must be a direct statement of the trip body — "
        "nested in a disabled branch (`if (false) { return true; }`) the limiter "
        "stays a no-op while every pin is satisfied"
    )
    # …and a BRACELESS nested branch adds no brace at all, so the depth check above
    # misses it — `if (!true) return true;` inside the trip left the limiter a no-op
    # with the suite green (cycle-16 review). Nothing may stand between the trip's
    # statement boundary and the exit but whitespace.
    trip_stmt = re.split(r"[;{}]", trip_head)[-1]
    assert not re.search(r"\b(?:if|while|for)\b", trip_stmt), (
        "the trip's `return true;` must not be the braceless body of another branch: "
        f"{trip_stmt.strip()[:60]!r}"
    )
    # `recent` is the trip's INPUT, and an untrippable input makes the trip a dead
    # branch: `filter(() => false)` leaves it permanently empty, so
    # `recent.length >= RATE_LIMIT` can never be true while every assertion here
    # still holds — `cutoff` stays "used" by the sweep, so nothing else notices
    # (cycle-15 review). The window's own shape is pinned, and the entry it gains
    # must carry the CURRENT timestamp.
    # …and the window declaration is pinned as a WHOLE statement (fullmatch, not a
    # sub-shape search): `re.search` over a prefix of the predicate accepted
    # `(t) => t > cutoff && false`, which empties the window and makes the trip a
    # dead branch with the suite green — the exact fail-open this pin's message
    # forbids, reachable with a literal suffix rather than indirection
    # (cycle-21 review).
    window_stmt = re.search(r"const recent\s*=\s*([^;]+);", body)
    assert window_stmt is not None, "the rate window must be a single statement"
    assert re.fullmatch(
        r"\s*\(\s*hits\.get\(\s*ip\s*\)\s*\|\|\s*\[\s*\]\s*\)\s*\.filter\(\s*"
        r"\(?\s*([A-Za-z_$][\w$]*)\s*(?::\s*[^)=]+)?\s*\)?\s*=>\s*\1\s*>=?\s*cutoff\s*\)\s*",
        window_stmt.group(1),
    ), (
        "the window must be exactly `(hits.get(ip) || []).filter((t) => t > cutoff)` — "
        f"got {window_stmt.group(1).strip()!r}: any suffix (`&& false`, a chained "
        "`.filter`) empties it and makes the trip a dead branch"
    )
    assert re.search(r"recent\.push\(\s*now\s*\)", body), (
        "the window must be extended with the CURRENT timestamp, or the limiter never trips"
    )
    # …and from the push onwards the ONLY thing that may touch the window is the
    # write-back itself: `hits.get(ip)` is the limiter's only state source and the
    # map holds the array BY REFERENCE, so a `recent.length = 0;` on either side of
    # the write-back empties the stored history and the trip can never fire, while
    # the write-back's own value pin stays satisfied. Both gaps are checked
    # (cycle-22 and cycle-23 reviews).
    push_at = body.index("recent.push(now)")
    push_end = body.index(";", push_at) + 1
    written = re.search(r"\bhits\.set\(\s*ip\s*,\s*recent\s*\)\s*;", body[push_end:])
    assert written is not None, (
        "the pushed window must be written back into the map as `recent`, or the "
        "limiter can never trip"
    )
    written_end = push_end + written.end()
    for gap, span in (
        ("the push and the write-back", body[push_end : push_end + written.start()]),
        ("the write-back and the end of the limiter", body[written_end:]),
    ):
        assert not re.search(r"\brecent\b", span), (
            f"nothing may touch the window between {gap}: the map holds the array by "
            f"reference, so mutating it there empties the stored history and the "
            f"limiter never throttles — {span.strip()[:60]!r}"
        )
    # ── The class, closed by construction ─────────────────────────────────
    # Blocklisting statement shapes does NOT converge here. `recent.length = 0;`
    # (after the filter, after the push, after the write-back), `&& false` on the
    # predicate, `hits.set(ip, [])` and `hits.clear()` each defeated a different
    # pin while every other assertion still held — cycles 19-24 found one more
    # statement shape every time, because a static pin set has an UNBOUNDED defeat
    # surface over a mutable implementation. So the reviewed body itself is pinned
    # VERBATIM (comments stripped, whitespace collapsed — reformatting and comments
    # are not changes): any statement change fails HERE until a human re-reads the
    # limiter. That is the honest static form of this guard — a tripwire, not a
    # proof — and the behavioural answer (fill the map, assert the bound and the
    # trip) remains #4108. The semantic pins above are kept for the error messages
    # and for the values.
    reviewed = re.sub(r"\s+", " ", code[fn_start:fn_end]).strip()
    assert reviewed == RATE_LIMITED_BODY, (
        "the rate limiter's body changed — re-read it, then update RATE_LIMITED_BODY: "
        f"{reviewed!r}"
    )
    # …and the CALL SITE is pinned the same way, because the body pin is scoped to
    # the function: `const ip = crypto.randomUUID()` (a fresh key per request) or
    # `hits.clear();` before the call disables throttling entirely without touching
    # a line of the pinned body (cycle-25 review).
    called = code.index("rateLimited(", fn_end)
    site_start = code.rindex("const ip", 0, called)
    site_end = _brace_end(code, code.index("if (rateLimited(", site_start))
    decl_start = code.index("const hits")
    decl_end = code.index(";", decl_start) + 1
    # …and the map must BE a `Map`: a `WeakMap` (or any type without `size`) makes
    # `hits.size > MAX_RATE_KEYS` always false, so the cap silently never runs and
    # the key count is unbounded (cycle-26 review, follow-on).
    assert re.fullmatch(
        r"\s*const hits\s*=\s*new Map<string, number\[\]>\(\);\s*", code[decl_start:decl_end]
    ), (
        "the limiter's state must be a `Map<string, number[]>` — a store without `size` "
        f"silently disables the cap: {code[decl_start:decl_end].strip()!r}"
    )
    site = re.sub(r"\s+", " ", code[site_start:site_end]).strip()
    assert site == RATE_LIMIT_CALL_SITE, (
        "the rate limiter's call site changed — re-read it, then update "
        f"RATE_LIMIT_CALL_SITE: {site!r}"
    )
    # …and `hits` may be referenced NOWHERE else in the file. Anchoring the call
    # site at `const ip` left everything earlier in the handler unguarded:
    # `hits.clear();` as the handler's first statement wipes the map before every
    # request and the limiter never throttles, with neither verbatim pin touched
    # (cycle-26 review). Every `hits` reference must live inside the limiter's body
    # or the pinned call site — a total-coverage assertion, so a NEW location cannot
    # slip through, and an alias/rename would break a verbatim pin.
    for hit in re.finditer(r"\bhits\b", code):
        inside_body = fn_start <= hit.start() < fn_end
        inside_call = site_start <= hit.start() < site_end
        # The declaration is the one legitimate reference outside those spans.
        declared = decl_start <= hit.start() < decl_end
        assert inside_body or inside_call or declared, (
            "`hits` is the limiter's state and may only be touched inside the limiter "
            f"or its call site; found at offset {hit.start()}: "
            f"{code[max(0, hit.start() - 60) : hit.start() + 60]!r}"
        )
    # `cutoff` is what the window is filtered BY: `const cutoff = now` (or
    # `Infinity`) makes every stored entry stale, so the window is always empty and
    # the trip can never fire — while `RATE_WINDOW_MS` stays "used" by the
    # `Retry-After` header, so the value pin above still passes (cycle-20 review).
    cutoff_expr = re.search(r"const cutoff\s*=\s*([^;]+);", body)
    assert cutoff_expr is not None, "the window cutoff must be derived from RATE_WINDOW_MS"
    assert re.fullmatch(r"\s*now\s*-\s*RATE_WINDOW_MS\s*", cutoff_expr.group(1)), (
        f"the cutoff must be `now - RATE_WINDOW_MS`, got {cutoff_expr.group(1).strip()!r}: a "
        "cutoff that always stales every entry makes the window empty and the trip dead"
    )
    # …and the filtered window must REACH the trip intact, as well as the push. The
    # cycle-19 guard began at the trip block, so a plain statement inserted between
    # the filter and the trip — `recent.length = 0;` — left the window empty before
    # it was ever measured (cycle-20 review). Neither gap may touch `recent`.
    window_end = body.index(";", body.index("const recent")) + 1
    for label, span in (
        ("the filter and the trip", body[window_end:trip_at]),
        ("the trip and the push", body[_brace_end(body, trip_at) : body.index("recent.push(now)")]),
    ):
        assert not re.search(r"\brecent\b", span), (
            f"nothing may mutate the filtered window between {label}: {span.strip()[:60]!r}"
        )
    # The GUARD is part of the cap: inverting it (`<` instead of `>`) disables
    # eviction entirely while the loop below still reads correctly, and the guard
    # sat outside the old slice (cycle-3 review).
    loop_at = code.index("for (const k of hits.keys())", guard_at)
    # The LOOP is inside the cap too: wrapping it in an always-false branch left
    # all four loop assertions green while eviction never ran (cycle-7 review), so
    # the guard's own nesting test is applied to it: it must be a direct statement
    # of the guard's body.
    guard_body = code[code.index("{", guard_at) + 1 : loop_at]
    loop_depth = guard_body.count("{") - guard_body.count("}")
    assert loop_depth == 0, (
        f"the eviction loop is nested at depth {loop_depth} in the guard's body: an "
        "enclosing branch can disable the cap while every token stays readable"
    )
    loop_stmt = re.split(r"[;{}]", guard_body)[-1]
    assert not re.search(r"\b(?:if|while|for)\b", loop_stmt), (
        f"the eviction loop must not be the braceless body of another branch: {loop_stmt.strip()[:80]!r}"
    )
    cap = code[guard_at:fn_end]
    # Bind the initializer to the cap AND make it the only assignment: `excess = 0;`
    # after a correct `let excess = …` breaks out immediately, leaving the map
    # unbounded under live keys, while every token still appears (cycle-3 review).
    assert len(re.findall(r"\bexcess\s*=(?!=)", cap)) == 1, (
        "excess must be assigned exactly once — from the cap"
    )
    assert re.search(r"let excess\s*=\s*hits\.size\s*-\s*MAX_RATE_KEYS", cap), (
        "excess must be derived from the cap, not a constant"
    )
    loop = code[loop_at:]
    assert re.search(r"if \(excess <= 0\) break;", loop), "the loop must stop at the cap"
    assert re.search(r"hits\.delete\(k\);", loop), "no key eviction under the cap"
    assert re.search(r"excess--;|excess -= 1;", loop), "the loop must count down"


# ── 6. The reply-promise vocabulary's coverage, as CASES ─────────────────

#: Promises the family MUST catch. One per shape real copy uses — this table is
#: what the constant's comment means by "coverage is asserted by test cases".
REPLY_PROMISES = (
    "We will reply within two business days.",
    "We'll be sure to respond shortly.",
    "We always reply to every message.",
    "Our team will reply within two business days.",
    "The team will respond as soon as possible.",
    "You will receive a response within two business days.",
    "We will send you a response shortly.",
    "You'll hear from us within a few days.",
    "Thanks — we'll follow up next week.",
)

#: Copy that must NOT be flagged: an invitation to write to us is not a promise,
#: and the negation of a promise is not one either — the second is the family's
#: declared fail-closed edge, kept here so a widening that starts catching
#: invitations is visible.
REPLY_NON_PROMISES = (
    "Please email hello@premiselabs.co directly.",
    "You can also get in touch at that address.",
    "Thanks for reaching out.",
    "We are sorry for the detour.",
    "We could not accept your message, so it was not sent.",
)


def test_the_reply_promise_vocabulary_covers_the_shapes_copy_uses() -> None:
    """Coverage as cases, so widening or narrowing the family is visible here."""
    for promise in REPLY_PROMISES:
        assert re.search(REPLY_PROMISE_VERBS, promise, re.I), (
            f"a reply promise the scan must catch is not caught: {promise!r}"
        )
    for other in REPLY_NON_PROMISES:
        assert not re.search(REPLY_PROMISE_VERBS, other, re.I), (
            f"copy that only invites a message, or negates a promise, is flagged: {other!r}"
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
    assert 'action="/contact/submit"' in html, "form must post to the Pages Function"
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


# ── 7. Behavioural contract — the fixes a source scan cannot prove ────────
#
# The rest of this file READS source; these tests RUN it. The two modules are
# copied into a temp dir (the seam import rewritten to an explicit `.ts`, which
# both runtimes require for a direct import) and driven through a stub `fetch`,
# with types stripped rather than checked, so the Cloudflare ambient
# `PagesFunction` type need not exist. Each test fails if its fix is reverted.
# Node is tried first (`--experimental-strip-types`, already on the CI runner, so
# the guard needs no new runtime there); Deno is the fallback. When NEITHER is
# available the test FAILS — a skipped guard is indistinguishable from a passing
# one, which is the hole this replaced.
_CONTACT_HARNESS = r'''
// Behavioural harness for the contact form's Pages Function + transport seam.
// Run with no args for all checks, or name checks to run only those.
// Exit 0 = every selected check passed; a failed check throws (non-zero exit).
import { onRequest } from "./contact.ts";

const enc = new TextEncoder();
// Runtime-agnostic argv: Node (`--experimental-strip-types`) is preferred, Deno
// is the fallback. `declare` is erased by both, so nothing references `process`
// under Deno or `Deno` under Node.
declare const Deno: { args: string[] } | undefined;
declare const process: { argv: string[] };
const only: string[] = typeof Deno !== "undefined" ? Deno.args : process.argv.slice(2);

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg);
}

const ENV = {
  CONTACT_INTAKE_URL: "https://intake.test/inbound-ingest",
  CONTACT_INTAKE_SECRET: "s3cret",
};

async function post(body: BodyInit, contentType: string, env: Record<string, unknown>) {
  const req = new Request("https://premiselabs.co/contact/submit", {
    method: "POST",
    headers: { "content-type": contentType },
    body,
  });
  return await onRequest({ request: req, env } as never);
}

// ── FIX 1: a streamed 72 KB form body must be refused 413 ──────────────────
if (only.length === 0 || only.includes("fix1")) {
  const big = "a".repeat(72 * 1024);
  const stream = new ReadableStream({
    start(c) {
      c.enqueue(enc.encode(`name=Ada&email=ada@example.com&message=${big}`));
      c.close();
    },
  });
  const req = new Request("https://premiselabs.co/contact/submit", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: stream,
    // Node's undici requires `duplex` for a streaming body; Deno accepts it too.
    duplex: "half",
  });
  assert(!req.headers.has("content-length"), "harness precondition: body must be streamed (no content-length)");
  const res = await onRequest({ request: req, env: {} } as never);
  const text = await res.text();
  assert(res.status === 413, `streamed 72 KB form body: expected 413, got ${res.status} ${text}`);

  // …and the form-data branch still PARSES after the bytes are re-read: a small
  // multipart post (whose boundary lives in the content-type header) must reach
  // validation and succeed, so the byte cap cannot have broken the no-JS path.
  const boundary = "----ct-harness-boundary";
  const multipart = [
    `--${boundary}`,
    'Content-Disposition: form-data; name="name"',
    "",
    "Ada",
    `--${boundary}`,
    'Content-Disposition: form-data; name="email"',
    "",
    "ada@example.com",
    `--${boundary}`,
    'Content-Disposition: form-data; name="message"',
    "",
    "hello there",
    `--${boundary}--`,
    "",
  ].join("\r\n");
  const realFetch = globalThis.fetch;
  globalThis.fetch = (() => Promise.resolve(new Response("{}", { status: 200 }))) as typeof fetch;
  try {
    const ok = await post(multipart, `multipart/form-data; boundary=${boundary}`, ENV);
    const okText = await ok.text();
    assert(
      ok.status === 200,
      `multipart form post after the byte cap: expected 200, got ${ok.status} ${okText}`,
    );
  } finally {
    globalThis.fetch = realFetch;
  }
}

// ── FIX 2: a 3xx intake response is a failure, not a success ───────────────
if (only.length === 0 || only.includes("fix2")) {
  let sawRedirectMode: string | null | undefined;
  const realFetch = globalThis.fetch;
  globalThis.fetch = ((_u: string | URL | Request, init?: RequestInit) => {
    sawRedirectMode = init?.redirect;
    // Emulate a following fetch: only a manual fetch can SEE the 3xx; a
    // following one is handed the 200 landing page the redirect led to.
    return Promise.resolve(
      init?.redirect === "manual"
        ? new Response("moved", { status: 302, headers: { location: "https://intake.test/final" } })
        : new Response("landing page", { status: 200 }),
    );
  }) as typeof fetch;
  try {
    const res = await post(
      JSON.stringify({ name: "Ada", email: "ada@example.com", message: "hello" }),
      "application/json",
      ENV,
    );
    const text = await res.text();
    assert(sawRedirectMode === "manual", `fetch must pass redirect: "manual" (got ${sawRedirectMode})`);
    assert(res.status === 502, `3xx intake must be a failure (502), got ${res.status} ${text}`);
  } finally {
    globalThis.fetch = realFetch;
  }
}

// ── FIX 3: the honeypot answers exactly like a real success ────────────────
if (only.length === 0 || only.includes("fix3")) {
  const realFetch = globalThis.fetch;
  globalThis.fetch = (() => Promise.resolve(new Response("{}", { status: 200 }))) as typeof fetch;
  try {
    const hp = await post(
      JSON.stringify({ name: "Ada", email: "ada@example.com", message: "hi", hp: "bot" }),
      "application/json",
      ENV,
    );
    const ok = await post(
      JSON.stringify({ name: "Ada", email: "ada@example.com", message: "hi" }),
      "application/json",
      ENV,
    );
    const hpBody = (await hp.json()) as { message?: string };
    const okBody = (await ok.json()) as { message?: string };
    assert(hp.status === 200 && ok.status === 200, `honeypot/real status: ${hp.status}/${ok.status}`);
    assert(
      hpBody.message === okBody.message,
      `honeypot message ${JSON.stringify(hpBody.message)} differs from real success ${JSON.stringify(okBody.message)}`,
    );
  } finally {
    globalThis.fetch = realFetch;
  }
}

// ── FIX 4: the upstream body is never logged ───────────────────────────────
if (only.length === 0 || only.includes("fix4")) {
  const realFetch = globalThis.fetch;
  const realError = console.error;
  const logged: string[] = [];
  console.error = (...args: unknown[]) => {
    logged.push(args.map(String).join(" "));
  };
  globalThis.fetch = (() =>
    Promise.resolve(new Response("UPSTREAM-SECRET-BODY", { status: 500 }))) as typeof fetch;
  try {
    const res = await post(
      JSON.stringify({ name: "Ada", email: "ada@example.com", message: "hi" }),
      "application/json",
      ENV,
    );
    assert(res.status === 502, `500 intake must be a failure, got ${res.status}`);
    assert(logged.length > 0, "a rejected intake must be logged");
    assert(
      !logged.some((l) => l.includes("UPSTREAM-SECRET-BODY")),
      `the upstream body was logged: ${JSON.stringify(logged)}`,
    );
    assert(
      logged.some((l) => l.includes("500")),
      `the log must carry the upstream status: ${JSON.stringify(logged)}`,
    );
  } finally {
    globalThis.fetch = realFetch;
    console.error = realError;
  }
}

// A request stub whose body records whether the HANDLER read it.
//
// A real `Request` cannot answer that: undici pre-pulls a stream body as soon as
// the Request is constructed, so a pull counter fires even when the handler never
// touches the body — an assertion that fails for a reason unrelated to the fix.
// The stub observes the handler's own `getReader()` call instead.
function stubRequest(headers: Record<string, string>, body: { getReader(): unknown } | null) {
  const h = new Map(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  let reads = 0;
  return {
    stubReads: () => reads,
    request: {
      url: "https://premiselabs.co/contact/submit",
      method: "POST",
      headers: { get: (n: string) => h.get(n.toLowerCase()) ?? null },
      body: body
        ? {
            getReader: () => {
              reads += 1;
              return body.getReader();
            },
          }
        : null,
    },
  };
}

// ── FIX 5: the byte cap must bound what is SPENT, not only what is KEPT ────
// `arrayBuffer()` buffers the whole body before the caller can measure it, so a
// 100 MB post cost 100 MB of isolate memory before the 16 KiB cap ever ran: the
// cap bounded the parse, not the read. Both halves of that are asserted here.
if (only.length === 0 || only.includes("fix5")) {
  // 5a — a declared oversize is refused WITHOUT reading the body. The reader
  // rejects, so a read is not merely wasteful here, it is a failure, and the
  // stub counts the handler's own getReader() calls.
  const declared = stubRequest(
    { "content-type": "application/json", "content-length": String(64 * 1024 * 1024) },
    {
      getReader: () => ({
        read: () => Promise.reject(new Error("the body was read despite a declared oversize")),
        cancel: () => Promise.resolve(),
      }),
    },
  );
  const resDeclared = await onRequest({ request: declared.request, env: ENV } as never);
  assert(resDeclared.status === 413, `declared oversize: expected 413, got ${resDeclared.status}`);
  assert(
    declared.stubReads() === 0,
    "a declared oversize must be refused without reading the body",
  );

  // 5b — with NO declared length the read STOPS at the ceiling rather than
  // draining the stream: 100 KiB is offered and far fewer chunks may be consumed.
  // (fix1 pins the 413 verdict against a REAL stream; this is the spend, exact.)
  let chunksRead = 0;
  const streamed = stubRequest(
    { "content-type": "application/json" },
    {
      getReader: () => ({
        read: () => {
          if (chunksRead >= 100) return Promise.resolve({ done: true, value: undefined });
          chunksRead += 1;
          return Promise.resolve({ done: false, value: new Uint8Array(1024).fill(97) });
        },
        cancel: () => Promise.resolve(),
      }),
    },
  );
  const resStreamed = await onRequest({ request: streamed.request, env: ENV } as never);
  assert(resStreamed.status === 413, `streamed oversize: expected 413, got ${resStreamed.status}`);
  assert(
    chunksRead < 64,
    `the reader drained the body instead of stopping at the ceiling: ${chunksRead} KiB consumed`,
  );
}

// ── FIX 6: a rejected submission is CHARGED — the flood is what gets throttled ─
// The limiter used to sit after validation, so the 400/413/415 paths returned
// before it and never incremented the counter: the traffic that costs the most
// was the traffic that was counted the least. Now it is charged, and the refusal
// happens BEFORE the body is read.
if (only.length === 0 || only.includes("fix6")) {
  const flood = "203.0.113.77"; // TEST-NET-3: this check owns the key
  const postFrom = async (body: BodyInit) => {
    const req = new Request("https://premiselabs.co/contact/submit", {
      method: "POST",
      headers: { "content-type": "application/json", "cf-connecting-ip": flood },
      body,
    });
    return await onRequest({ request: req, env: ENV } as never);
  };
  for (let i = 0; i < 5; i += 1) {
    const bad = await postFrom(
      JSON.stringify({ name: "Ada", email: "not-an-email", message: "hi" }),
    );
    assert(bad.status === 400, `rejected submission #${i + 1}: expected 400, got ${bad.status}`);
  }
  const sixth = stubRequest(
    {
      "content-type": "application/json",
      "content-length": String(64 * 1024 * 1024),
      "cf-connecting-ip": flood,
    },
    {
      getReader: () => ({
        read: () => Promise.reject(new Error("a rate-limited request must not read its body")),
        cancel: () => Promise.resolve(),
      }),
    },
  );
  const resSixth = await onRequest({ request: sixth.request, env: ENV } as never);
  assert(
    resSixth.status === 429,
    `the sixth submission from one connection: expected 429, got ${resSixth.status}`,
  );
  assert(
    sixth.stubReads() === 0,
    "a rate-limited request must be refused before its body is read",
  );
}

console.log("contact harness: all selected checks passed");
'''


def _contact_harness_runtime() -> list[str] | None:
    """The command prefix that can run a `.ts` harness, or None when there is none.

    Node is tried first: `--experimental-strip-types` (Node >= 22.6) runs the two
    modules as-is and Node is already on the CI runner, so the guard needs no new
    runtime there. The probe RUNS a trivial `.ts` file rather than reading a
    version string, so an unfamiliar-but-capable future Node keeps working and an
    old one falls through to Deno instead of failing the suite.
    """
    node = shutil.which("node")
    if node is not None:
        with tempfile.TemporaryDirectory() as probe_dir:
            (Path(probe_dir) / "probe.ts").write_text("const x: number = 1;\n", encoding="utf-8")
            probe = subprocess.run(
                [node, "--experimental-strip-types", "probe.ts"],
                cwd=probe_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
        if probe.returncode == 0:
            return [node, "--experimental-strip-types"]
    deno = shutil.which("deno") or str(Path.home() / ".deno" / "bin" / "deno")
    if Path(deno).is_file():
        return [deno, "run", "--no-check", "--quiet"]
    return None


def _run_contact_harness(*checks: str) -> None:
    """Run the behavioural harness for the named checks (all of them when none).

    FAILS (never skips) when no TypeScript runtime is available: a skipped guard
    reports the same green as a passing one, and CI is where the merge decision
    is made — the exact place a silent skip must not be accepted.
    """
    import pytest

    runtime = _contact_harness_runtime()
    if runtime is None:
        pytest.fail(
            "no TypeScript runtime for the contact-form behavioural harness — "
            "need node >= 22.6 (--experimental-strip-types) or deno; refusing "
            "to skip, because a skipped guard looks like a passing one"
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        entry = _src(FUNCTION_TS).replace(
            '"../_shared/contact-transport"', '"./contact-transport.ts"'
        )
        assert '"./contact-transport.ts"' in entry, (
            "the seam import did not resolve — contact.ts's import path changed"
        )
        (tmpdir / "contact.ts").write_text(entry, encoding="utf-8")
        (tmpdir / "contact-transport.ts").write_text(_src(TRANSPORT_TS), encoding="utf-8")
        (tmpdir / "driver.ts").write_text(_CONTACT_HARNESS, encoding="utf-8")
        result = subprocess.run(
            [*runtime, "driver.ts", *checks],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=300,
        )
    assert result.returncode == 0, (
        f"contact harness {checks or 'all'} failed (exit {result.returncode}):\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "all selected checks passed" in result.stdout


def test_streamed_form_body_over_the_byte_cap_is_refused() -> None:
    """FIX 1: a 72 KB form body with NO Content-Length must be a 413.

    The old code trusted the client's `Content-Length`, counted `text.length`
    (UTF-16 units, not bytes) on the JSON path, and left the form-data path
    with no cap at all — so this streamed body reached validation and was
    answered 400 as an over-long message, and a larger one would simply have
    been buffered. The fix reads the body once as bytes and caps that count.
    """
    _run_contact_harness("fix1")


def test_intake_redirect_is_a_failure_not_a_success() -> None:
    """FIX 2: a 3xx from the intake must not be reported as delivered.

    A FOLLOWED redirect replays the POST as an empty GET, so the message is
    lost while the landing page answers 200 and the visitor is told it
    arrived. The harness's stub `fetch` only surfaces the 3xx when
    `redirect: "manual"` is set, so the check fails if that option is dropped.
    """
    _run_contact_harness("fix2")


def test_honeypot_confirmation_is_identical_to_a_real_success() -> None:
    """FIX 3: a different confirmation string let a bot detect the trap."""
    _run_contact_harness("fix3")


def test_rejected_intake_body_is_never_logged() -> None:
    """FIX 4: the upstream body is not ours to print — only the status is."""
    _run_contact_harness("fix4")


def test_byte_cap_bounds_what_is_spent_not_only_what_is_kept() -> None:
    """FIX 5: the ceiling must stop the READ, not just the parse.

    The cap used to run on a fully buffered body, so an unauthenticated 100 MB
    post spent 100 MB of isolate memory before the 16 KiB check was consulted.
    5a fails if a declared oversize is read at all; 5b fails if a streamed body
    is drained rather than abandoned at the ceiling.
    """
    _run_contact_harness("fix5")


def test_a_rejected_submission_is_charged_against_the_limit() -> None:
    """FIX 6: rejected traffic must be throttled, not waved through.

    The limiter ran after validation, so 400/413/415 returned before it and never
    incremented the counter — the requests that cost the most were counted the
    least. Five rejected submissions must now charge the connection, and the
    sixth must be refused BEFORE its body is read.
    """
    _run_contact_harness("fix6")

