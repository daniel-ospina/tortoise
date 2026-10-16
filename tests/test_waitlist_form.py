"""Static regression tests for the premiselabs.co landing page waitlist form (#373).

Guards the three issue indicators end-to-end at the repo level (no network):
  1. Email submissions stored in Supabase  → form posts JSON to the
     waitlist-subscribe edge function; migration 0005 defines the table with
     a UNIQUE email constraint + service_role-only RLS; config.toml sets
     verify_jwt=false so anonymous browser POSTs reach the function.
  2. Confirmation email via Resend         → handle.ts contains the Resend
     send (api.resend.com/emails), the unsubscribe footer, and only fires on
     fresh inserts (duplicate discriminator via on_conflict + return=representation).
  3. Form shows success state              → index.html has the form with
     success / already-subscribed / error / network-failure states.

Also mirrors the two legal-suite constraints the E2E suite pins
(tests/e2e/test_legal_pages.py) so a regression can't silently reintroduce
"consent.js"/analytics instrumentation on index.html or a "we (use|collect|…)"
phrase on privacy.html. The legal suite's present-tense guard is a
whole-document SET-EQUALITY assertion — this file mirrors it with the same
regex + normalizer. If the legal suite's pinned set changes, sync
EXPECTED_GUARD_MATCHES here too.

Run:  python -m pytest tests/test_waitlist_form.py -v

Also covers the /investors surface: its list form feeds the same table under its
own source, and its email gate writes a DIFFERENT table (deck_views) through a
different function, so a reader who traded an address for access is not on a
mailing list they never joined.
"""
from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBSITE_DIR = REPO_ROOT / "website"
SUPABASE_DIR = REPO_ROOT / "supabase"
MIGRATIONS_DIR = SUPABASE_DIR / "migrations"
FUNCTIONS_DIR = SUPABASE_DIR / "functions"

INDEX_HTML = WEBSITE_DIR / "index.html"
PRIVACY_HTML = WEBSITE_DIR / "privacy.html"
CONFIG_TOML = SUPABASE_DIR / "config.toml"

FUNCTION_URL = "https://ybetwichurajbfswfeqa.supabase.co/functions/v1/waitlist-subscribe"

# The endpoint is derived from the Supabase project ref (config.toml project_id
# + the documented supabase function host convention). This pins the page to
# the SAME source of truth as config.toml so a future project migration is
# caught at CI time instead of silently breaking the form (regression guard
# for the prior hardcoded-URL incident, PR #55).


# ── Normalizer mirror (tests/e2e/test_legal_pages.py `_clean`) ─────────────
def _clean(text: str) -> str:
    """Byte-identical mirror of the legal suite's `_clean()` — the present-
    tense guard and instrumentation scans must agree with the live E2E run."""
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,;:!?)\]])", r"\1", text)
    return text.strip().lower()


# ── Indicator 3: the form exists on index.html with full UX states ─────────

def test_index_html_has_waitlist_form() -> None:
    raw = INDEX_HTML.read_text(encoding="utf-8")
    # Form + fields (ids/names are the cross-file contract with handle.ts)
    assert 'id="waitlist-form"' in raw, "form id=waitlist-form missing"
    assert 'action' not in re.search(r'<form[^>]*>', raw).group(0), \
        "form must NOT use native action submit — JS fetch() with JSON is required"
    assert 'type="email"' in raw and "autocomplete=" in raw
    assert 'name="hp"' in raw, "honeypot name=hp missing (contract with handle.ts)"
    assert 'type="submit"' in raw


def test_index_html_form_wires_to_waitlist_subscribe_function() -> None:
    raw = INDEX_HTML.read_text(encoding="utf-8")
    # JS must fetch the edge function with a JSON content type
    assert "waitlist-subscribe" in raw, "fetch target missing waitlist-subscribe"
    assert "fetch(" in raw
    assert '"Content-Type": "application/json"' in raw or \
        "'Content-Type': 'application/json'" in raw, "fetch must send JSON content type"


def test_index_html_has_message_states() -> None:
    raw = INDEX_HTML.read_text(encoding="utf-8")
    # Exactly two message elements carrying four states (per plan Task 6)
    assert 'id="waitlist-msg"' in raw and 'role="status"' in raw
    assert 'id="waitlist-error"' in raw and 'role="alert"' in raw
    # "Already subscribed" arrives from the server as data.message and must be rendered
    assert "data.message" in raw, "server message (already-subscribed) must be rendered"
    assert "Something went wrong" in raw, "network-failure copy missing"


def test_index_html_turnstile_site_key_constant() -> None:
    raw = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"TURNSTILE_SITE_KEY\s*=\s*['\"]([^'\"]*)['\"]", raw)
    assert m, "TURNSTILE_SITE_KEY constant missing"
    key = m.group(1)
    assert key == "" or re.fullmatch(r"0x[a-zA-Z0-9_-]{20,40}", key), \
        f"TURNSTILE_SITE_KEY must be empty or a real sitekey (got {key!r})"
    assert "PLACEHOLDER" not in key, "placeholder site key would render a broken widget"


def test_investors_turnstile_site_key_constant_and_scope() -> None:
    """The site key exists on the second page too, and covers the right form.

    `waitlist-subscribe` verifies the token server-side whenever its secret is
    set, so `/investors`' mailing-list form must send one — and the operator's
    key step has to name this page, or the form is refused with no token and
    nothing in the copy says why. The gate posts to `deck-view`, which verifies
    nothing, so it must NOT render a widget: a challenge the server discards is
    friction with nothing behind it.
    """
    raw = INVESTORS_HTML.read_text(encoding="utf-8")
    m = re.search(r"TURNSTILE_SITE_KEY\s*=\s*['\"]([^'\"]*)['\"]", raw)
    assert m, "TURNSTILE_SITE_KEY constant missing"
    key = m.group(1)
    assert key == "" or re.fullmatch(r"0x[a-zA-Z0-9_-]{20,40}", key), \
        f"TURNSTILE_SITE_KEY must be empty or a real sitekey (got {key!r})"
    assert "PLACEHOLDER" not in key, "placeholder site key would render a broken widget"
    # The gate's wire() call passes captcha=false; the list's passes true.
    assert re.search(r"wire\('gate-form'[^;]*,\s*false\s*\)", raw), \
        "the gate must not render a captcha the deck-view function never verifies"
    assert re.search(r"wire\('waitlist-form'[^;]*,\s*true\s*\)", raw), \
        "the mailing-list form must send the token waitlist-subscribe verifies"
    # The flag must be DERIVED from the per-form argument, not merely passed:
    # `wire(..., false)` with a flag read from the global would re-render the
    # gate's widget while both call sites still looked correct.
    assert "var useCaptcha = !!captcha && !!TURNSTILE_SITE_KEY;" in raw, \
        "the per-form flag must be derived from the per-form argument"
    assert re.search(r"if \(TURNSTILE_SITE_KEY", raw) is None, \
        "a decision site reading the global would render the gate's widget regardless"
    for site in (
        "if (useCaptcha) {",
        "if (useCaptcha && !turnstileFailed && !token) {",
        "if (useCaptcha && !turnstileFailed) { payload['cf-turnstile-response']",
    ):
        assert site in raw, f"the render/guard/payload site must read the derived flag: {site}"
    assert len(re.findall(r"if \(useCaptcha", raw)) == 4, \
        "render, pre-submit guard, payload and reset must all read the derived flag"


# ── Indicator 1a: config allows anonymous browser POSTs ────────────────────

def test_config_toml_verify_jwt_false() -> None:
    raw = CONFIG_TOML.read_text(encoding="utf-8")
    assert "[functions.waitlist-subscribe]" in raw
    section = raw.split("[functions.waitlist-subscribe]", 1)[1]
    section = section.split("[functions.", 1)[0]
    assert re.search(r"verify_jwt\s*=\s*false", section), \
        "waitlist-subscribe must have verify_jwt=false (browser POSTs carry no JWT)"
    # Endpoint single-source-of-truth: index.html URL must match config.toml's
    # project_id (regression guard for hardcoded-URL drift, PR #55 class).
    project_ref = re.search(r"project_id\s*=\s*[\"']([^\"']+)[\"']", CONFIG_TOML.read_text(encoding="utf-8"))
    assert project_ref, "config.toml must declare project_id"
    expected_url = f"https://{project_ref.group(1)}.supabase.co/functions/v1/waitlist-subscribe"
    index = INDEX_HTML.read_text(encoding="utf-8")
    assert expected_url in index, f"index.html endpoint {expected_url} != config.toml project_id"
    assert expected_url == FUNCTION_URL


# ── Indicator 1b: migration 0005 defines the table ──────────────────────────

def test_migration_0005_exists_and_defines_table() -> None:
    mig = MIGRATIONS_DIR / "0005_waitlist_subscribers.sql"
    assert mig.exists(), "migration 0005 missing"
    raw = mig.read_text(encoding="utf-8")
    assert "waitlist_subscribers" in raw
    assert "email" in raw
    assert re.search(r"email\s+text\s+NOT\s+NULL\s+UNIQUE", raw, re.I), \
        "inline UNIQUE on email required (dedup constraint)"
    assert "consented_at" in raw, "consent stamping column required (single opt-in)"
    assert "source" in raw
    assert "ENABLE ROW LEVEL SECURITY" in raw.upper()
    assert re.search(r"TO\s+service_role", raw, re.I), \
        "service_role policy required (matches 0003/0004 convention)"
    assert not re.search(r"TO\s+anon\b", raw, re.I), \
        "no anon policy — writes only via service role in the edge function"


# ── Indicator 2: edge function markers ─────────────────────────────────────

def test_edge_function_handle_exists_and_is_pure() -> None:
    handle = FUNCTIONS_DIR / "waitlist-subscribe" / "handle.ts"
    index = FUNCTIONS_DIR / "waitlist-subscribe" / "index.ts"
    assert handle.exists(), "handle.ts missing (pure module for Node testing)"
    assert index.exists(), "index.ts missing (serve wrapper)"
    h = handle.read_text(encoding="utf-8")
    # Zero imports — must be importable under `node --experimental-strip-types`
    assert "import " not in h, "handle.ts must have zero imports (Node harness)"


def test_edge_function_markers() -> None:
    handle = (FUNCTIONS_DIR / "waitlist-subscribe" / "handle.ts").read_text(encoding="utf-8")
    index = (FUNCTIONS_DIR / "waitlist-subscribe" / "index.ts").read_text(encoding="utf-8")
    assert "api.resend.com/emails" in handle, "Resend send missing"
    assert "unsubscribe" in handle.lower(), "unsubscribe footer required (privacy §2 promise)"
    assert "challenges.cloudflare.com/turnstile/v0/siteverify" in handle, "Turnstile verify missing"
    assert "on_conflict=email" in handle, "on_conflict must be a URL query param"
    assert "resolution=ignore-duplicates" in handle, "ignore-duplicates Prefer missing"
    assert "return=representation" in handle, "return=representation required (duplicate discriminator)"
    assert "AbortSignal.timeout" in handle, "Resend 2s timeout required"
    assert "TURNSTILE_SECRET_KEY" in handle and "RESEND_API_KEY" in handle, "env reads missing"
    assert "SUPABASE_SERVICE_ROLE_KEY" in handle, "service role key read missing"
    assert "__resetRateLimit" in handle, "rate-limit reset export required (harness isolation)"
    assert "serve" in index and "Deno.env.toObject()" in index, \
        "index.ts must wire env: serve((req) => handle(req, Deno.env.toObject()))"


# ── Second surface: /investors feeds the same list under its own source ────

INVESTORS_HTML = WEBSITE_DIR / "investors.html"
# The source the /investors list form declares; the edge function must allow it,
# because the page and the function ship together and a source the server does
# not recognise degrades to `landing_page` with no error anywhere.
INVESTORS_SOURCES = ("investors",)
DECK_VIEW_URL = FUNCTION_URL.replace("waitlist-subscribe", "deck-view")
# The page's own constants, and the two wire() calls that bind a form to one of
# them. Parsed rather than substring-matched: the endpoint NAMES occur twice each
# (constant + call) and the quoted 'investors' twice (both wire calls), so a
# substring check cannot tell which form writes where (see the test's docstring).
VIEW_ENDPOINT_DECL = f"var VIEW_ENDPOINT = '{DECK_VIEW_URL}';"
LIST_ENDPOINT_DECL = f"var LIST_ENDPOINT = '{FUNCTION_URL}';"
WIRE_CALL = re.compile(
    r"wire\('([a-z-]+)',\s*'[a-z-]+',\s*'[a-z-]+',\s*([A-Z_]+),\s*\{([^}]*)\}\s*[,)]",
)


class _FormRegions(HTMLParser):
    """Map each <form id> to the region of the page it sits in.

    `wire()` binds a handler to an element ID, so the id is only half the
    contract: swapping the two id attributes keeps every other assertion green
    while attaching the gate's handler to the mailing-list form and vice versa
    — the same shipped failure as swapping the endpoints, reached the other way.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.regions: dict[str, str | None] = {}
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form":
            self.regions[a.get("id") or ""] = next((m for m in reversed(self._stack) if m), None)
            return
        if tag not in ("div", "section"):
            return
        if tag == "div" and "gate" in (a.get("class") or "").split():
            self._stack.append("gate")
        elif tag == "section" and a.get("id") == "stay-in-touch":
            self._stack.append("stay-in-touch")
        else:
            self._stack.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("div", "section") and self._stack:
            self._stack.pop()


def test_investors_page_posts_to_the_same_endpoint_and_declares_its_source() -> None:
    """One table, two surfaces — and a third path for the gate.

    The list form must point at the endpoint derived from config.toml (the same
    single source of truth as index.html above) and declare the source the
    function is asked to record, or attribution silently collapses to the
    landing page's value and "who joined via the investor page" is unanswerable
    from the data. The gate must point at deck-view instead: a view is not a
    subscription, and the two must never share a write path.
    """
    raw = INVESTORS_HTML.read_text(encoding="utf-8")
    assert "name=\"email\"" in raw, "investors.html must carry the email field"
    # The honeypot protects both forms on the page: the submit handler reads the
    # field by name, so a form without it has nothing for the functions' bot
    # gate to receive.
    assert raw.count('name="hp"') >= 2, "both investors.html forms need the honeypot field"

    # Bind each form to ITS endpoint and ITS body — presence alone is not enough.
    # The endpoint NAMES occur twice each (constant + call) and the quoted
    # 'investors' twice (both wire calls), so a substring check passes even with
    # the write paths swapped — the one regression that must not ship: the swap
    # would put every deck reader on the mailing list they never joined, and would
    # collapse attribution to the landing page's value with no error anywhere.
    calls = WIRE_CALL.findall(raw)
    assert len(calls) == 2, f"expected the two wire() calls, found {len(calls)}"
    bound = {form: (endpoint, extra) for form, endpoint, extra in calls}
    assert set(bound) == {"gate-form", "waitlist-form"}, f"unexpected form ids: {sorted(bound)}"

    # ...and each id must still be ON the form it names.
    regions = _FormRegions()
    regions.feed(raw)
    assert regions.regions.get("gate-form") == "gate", (
        "the gate form must sit inside .gate — an id swap would attach its handler to the list form"
    )
    assert regions.regions.get("waitlist-form") == "stay-in-touch", (
        "the list form must sit inside #stay-in-touch"
    )

    gate_endpoint, gate_extra = bound["gate-form"]
    assert gate_endpoint == "VIEW_ENDPOINT", "the gate must post to the deck-view endpoint constant"
    assert "deck:" in gate_extra and '\'investors\'' in gate_extra, "the gate must name its deck"
    assert "source" not in gate_extra, "a view is not a subscription: the gate must send no source"

    list_endpoint, list_extra = bound["waitlist-form"]
    assert list_endpoint == "LIST_ENDPOINT", "the list must post to the waitlist endpoint constant"
    for source in INVESTORS_SOURCES:
        assert f"source: '{source}'" in list_extra, f"the list must declare source {source!r}"
    assert "deck" not in list_extra, "the list is not a deck view: it must send no deck"

    # ...and the constants must be the URLs derived from config.toml's project id.
    assert VIEW_ENDPOINT_DECL in raw, f"VIEW_ENDPOINT must be {DECK_VIEW_URL!r}"
    assert LIST_ENDPOINT_DECL in raw, f"LIST_ENDPOINT must be {FUNCTION_URL!r}"
    assert DECK_VIEW_URL.endswith("/deck-view") and FUNCTION_URL.endswith("/waitlist-subscribe")

    handle = (FUNCTIONS_DIR / "waitlist-subscribe" / "handle.ts").read_text(encoding="utf-8")
    assert "ALLOWED_SOURCES" in handle, "handle.ts must allow-list attribution sources"
    for source in INVESTORS_SOURCES:
        assert f'"{source}"' in handle, f"handle.ts must allow-list {source!r}"
    assert '"landing_page"' in handle, "landing_page must stay the default source"


def test_investors_turnstile_resets_its_own_widget() -> None:
    """The reset names the widget this form's own render() call returned.

    `turnstile.reset()` with no argument re-renders the most recently created
    widget on the page — whichever form that happens to be. Binding the reset to
    the captured id keeps it pinned to the widget this form rendered.
    """
    raw = INVESTORS_HTML.read_text(encoding="utf-8")
    captured = re.search(r"(\w+)\s*=\s*window\.turnstile\.render\(", raw)
    assert captured, "the form that renders a widget must retain the id its render() call returns"
    assert f"window.turnstile.reset({captured.group(1)})" in raw, \
        "the reset must name the same widget id the render captured"
    assert "window.turnstile.reset()" not in raw, \
        "a no-arg reset re-renders the last-created widget (the other form's)"


def test_investors_forms_bound_the_request_and_expose_their_errors() -> None:
    """Three properties of the two forms that no other assertion covers.

    A request that never settles must not strand the reader on a disabled
    button: the gate's recorded policy is to open when our side is unhealthy,
    and a hang is that. An error must be attached to the field it belongs to,
    not only announced once. And the unlock must move focus into the document it
    reveals, because the button is disabled and re-enabled behind the reader.
    """
    raw = INVESTORS_HTML.read_text(encoding="utf-8")

    assert "new AbortController()" in raw and "controller.abort()" in raw, \
        "the submissions need a time bound"
    assert "signal: controller ? controller.signal : undefined" in raw, \
        "the abort controller must actually be attached to the fetch"
    assert re.search(r"controller\.abort\(\); \}, 8000\)", raw), \
        "the 8 s deadline is what bounds a hung request"

    # The abort must land in the fail-open path, not in an error state.
    chain = raw.split("fetch(endpoint", 1)[1].split("function unlock", 1)[0]
    assert re.search(r"\}\)\.catch\(function \(\) \{.*?if \(onOk\) \{ onOk\(\); \}", chain, re.S), \
        "an aborted or unreachable request must take the fail-open path"

    for described in ('aria-describedby="gate-error"', 'aria-describedby="waitlist-error"'):
        assert described in raw, f"each field must name its own error region: {described}"
    assert "emailInput.setAttribute('aria-invalid', 'true')" in raw, \
        "a shown error must mark the field invalid"
    assert "emailInput.removeAttribute('aria-invalid')" in raw, \
        "a re-submit must clear the invalid flag"
    # Only errors about the address may mark the field invalid: the two client
    # validation sites and a 400 naming the address. A server error (403, 405),
    # a captcha prompt or a network failure must leave a VALID address alone.
    assert raw.count("markInvalid();") == 3, \
        "only address-related sites may mark the field invalid"
    assert "emailInput.addEventListener('input'" in raw, \
        "editing the value must clear the invalid claim without a submit"
    assert re.search(
        r"addEventListener\('input', function \(\) \{\s*if \(!emailInput\.hasAttribute\('aria-invalid'\)\) \{ return; \}",
        raw,
    ), "a keystroke must not erase a message that is not about the field"
    # A 400 is not automatically about the address — `waitlist-subscribe` answers
    # 400 for a captcha refusal and a body-cap breach as well, and marking the
    # field for those asserts a VALID address is malformed and lets one keystroke
    # delete an error the reader cannot fix by editing. Only the address fault
    # marks it, and only when the server names it.
    assert re.search(
        r"if \(res\.status === 400 && data && data\.error === ADDRESS_ERROR\) \{ markInvalid\(\); \}",
        raw,
    ), "only a 400 that names the address may mark the field"
    assert "var ADDRESS_ERROR = 'Invalid email address';" in raw, \
        "the address fault the page keys on must be the string the handlers send"
    # ...and both handlers must in fact send it for a 400 address fault, or the
    # page's key goes dead and a real address error shows unmarked (unclearable).
    for fn in ("waitlist-subscribe", "deck-view"):
        handler = (REPO_ROOT / "supabase" / "functions" / fn / "handle.ts").read_text()
        assert 'json({ error: "Invalid email address" }, 400, corsOrigin)' in handler, \
            f"{fn} must answer a bad address with the 400 the page keys on"
    # A 2xx whose body cannot be read resolves the fetch and rejects the body
    # read; treating that as `{}` announces a subscription that was never
    # recorded. Only a non-2xx may fall through to an empty object.
    assert re.search(
        r"res\.json\(\)\.catch\(function \(e\) \{.*?if \(res\.ok\) \{ throw e; \}.*?return \{\};\s*\}\)",
        raw, re.S,
    ), "an unreadable body on a 2xx must not take the success path"

    assert "heading.setAttribute('tabindex', '-1')" in raw and "heading.focus()" in raw, \
        "the unlock must move focus into the revealed document"


def test_deck_view_function_and_table_exist() -> None:
    """The gate's write path: its own function, its own table, no email.

    A view record is what "who opened the deck" needs; making the gate write
    `waitlist_subscribers` would merge viewing with subscribing and put readers
    on a list they did not join (the behaviour harness pins the write target —
    this pins that the pieces exist and that the function is browser-reachable).
    """
    handle = (FUNCTIONS_DIR / "deck-view" / "handle.ts").read_text(encoding="utf-8")
    index = (FUNCTIONS_DIR / "deck-view" / "index.ts").read_text(encoding="utf-8")
    assert "import " not in handle, "handle.ts must have zero imports (Node harness)"
    assert "deck_views" in handle, "the function must write deck_views"
    assert "api.resend.com" not in handle, "the gate emails nobody"
    assert "__resetRateLimit" in handle, "rate-limit reset export required (harness isolation)"
    assert "serve" in index and "Deno.env.toObject()" in index, \
        "index.ts must wire env: serve((req) => handle(req, Deno.env.toObject()))"

    # Globbed, not named: the prefix is a timestamp that must be raised whenever a
    # migration lands on main ahead of this branch (duplicate prefixes abort
    # `supabase db push`), so pinning a literal filename makes a renumber red.
    migs = sorted(MIGRATIONS_DIR.glob("*_deck_views.sql"))
    assert len(migs) == 1, f"expected exactly one deck_views migration, found {migs}"
    raw = migs[0].read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS public.deck_views" in raw
    assert re.search(r"email\s+text\s+NOT\s+NULL", raw, re.I), "email column required"
    assert "ENABLE ROW LEVEL SECURITY" in raw.upper()
    assert re.search(r"TO\s+service_role", raw, re.I), "service_role policy required"
    assert not re.search(r"TO\s+anon\b", raw, re.I), "no anon policy — writes via service role only"

    config = CONFIG_TOML.read_text(encoding="utf-8")
    assert "[functions.deck-view]" in config, "deck-view must be declared in config.toml"
    section = config.split("[functions.deck-view]", 1)[1].split("[functions.", 1)[0]
    assert re.search(r"verify_jwt\s*=\s*false", section), \
        "deck-view must have verify_jwt=false (anonymous browser POSTs carry no JWT)"


# ── Legal mirror: privacy.html present-tense guard (set equality) ──────────

PRESENT_TENSE_GUARD = re.compile(r"we (use|collect|share|sell|process|retain|store|transfer)\b")
# Mirror of tests/e2e/test_legal_pages.py EXPECTED_GUARD_MATCHES (only the
# "we share limited data…" canonical sentence matches the guard today). The
# E2E suite asserts SET EQUALITY over the whole served page — new waitlist
# disclosure MUST use passive phrasing and never add a match.
EXPECTED_GUARD_MATCHES = {"we share"}


def test_privacy_present_tense_guard_matches_pinned_set() -> None:
    body = _clean(PRIVACY_HTML.read_text(encoding="utf-8"))
    matches = {m.group(0) for m in PRESENT_TENSE_GUARD.finditer(body)}
    assert matches == EXPECTED_GUARD_MATCHES, \
        f"privacy present-tense guard {sorted(matches)} != pinned {sorted(EXPECTED_GUARD_MATCHES)}"


def test_privacy_pinned_sharing_sentence_present() -> None:
    body = _clean(PRIVACY_HTML.read_text(encoding="utf-8"))
    assert "we share limited data with advertising/analytics providers" in body, \
        "pinned sharing sentence removed"


def test_privacy_has_waitlist_disclosure() -> None:
    raw = PRIVACY_HTML.read_text(encoding="utf-8")
    assert "Waitlist data" in raw, "§2 waitlist data category missing"
    assert "unsubscribe" in raw.lower(), "privacy must promise an unsubscribe option"


# ── Legal mirror: index.html stays analytics-free (instrumentation IFF) ────

def test_index_html_analytics_free() -> None:
    """Mirror of the legal suite's instrumentation scan (#10(c1)) restricted
    to index.html — the landing page must stay analytics-free."""
    raw = INDEX_HTML.read_text(encoding="utf-8")
    assert 'src="/consent.js"' not in raw and "consent.js" not in raw, \
        "index.html must not load consent.js"
    for marker in ("posthog.init(", "fbq(", "gtag("):
        assert marker not in raw, f"analytics init call {marker} found on index.html"
    for marker in ("js.posthog.com", "connect.facebook.net", "www.googletagmanager.com"):
        assert marker not in raw, f"analytics script src {marker} found on index.html"
    for marker in ("consent-banner", "klaro", "consent_manager", "data-consent"):
        assert marker not in raw, f"consent-banner marker {marker} found on index.html"
