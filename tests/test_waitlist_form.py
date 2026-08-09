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
"""
from __future__ import annotations

import html as html_lib
import re
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


# ── Indicator 1a: config allows anonymous browser POSTs ────────────────────

def test_config_toml_verify_jwt_false() -> None:
    raw = CONFIG_TOML.read_text(encoding="utf-8")
    assert "[functions.waitlist-subscribe]" in raw
    section = raw.split("[functions.waitlist-subscribe]", 1)[1]
    section = section.split("[functions.", 1)[0]
    assert re.search(r"verify_jwt\s*=\s*false", section), \
        "waitlist-subscribe must have verify_jwt=false (browser POSTs carry no JWT)"


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
