"""Blog E2E suite (#1800) — Tortoise blog + CMS verification gate.

Covers the epic's high-level E2E scenarios that are verifiable against a
deployed surface:

  E2E-1  /blog + /blog/<slug> server-rendered (200, content present, SEO head)
  E2E-7  host isolation — premiselabs.co /blog* → 301 tortoise host;
         /blogpost /blog-extra NOT redirected
  E2E-11 /blog/feed.xml + /blog/sitemap.xml valid XML (published-only)
  E2E-12 /admin/* → 302 /auth?next=<path> (no session; no content leaked;
         #3080 return-to so login comes BACK to the console). Asserted on the
         APP origin — the console's home since #4171 (#4409); the marketing
         host's own `302 → app.*/admin` hand-off is asserted separately.
  E2E-8  agent API rejects bad actors (401 no/invalid key; no anonymous write)
  E2E-14 sanitized SSR (no <script> in rendered post bodies)
  robots.txt lists the blog sitemap

Harness contract (follows test_legal_pages.py):
  - RUN_BLOG_E2E=1 REQUIRED — first statement is a runtime module skip; bare
    collection never errors.
  - BASE_URL / TORTISE_HOST / APP_HOST env (BASE_URL and TORTISE_HOST default
    to http://127.0.0.1:8788; APP_HOST defaults to https://app.premiselabs.co,
    so a LOCAL run MUST pass it — otherwise the ALLOW_PROD guard skips the module
    rather than letting it call production).

Run locally against a `wrangler pages dev` preview of the MARKETING project:
  RUN_BLOG_E2E=1 BASE_URL=http://127.0.0.1:8788 \
    TORTISE_HOST=http://127.0.0.1:8788 APP_HOST=http://127.0.0.1:8788 \
    pytest tests/e2e/test_blog.py -v

  That covers the blog legs only. The two ADMIN-GATE probes assert on the origin
  that SERVES the gate; the marketing preview does not (its `/admin` branch
  redirects to the hardcoded `APP_ORIGIN`), so they self-skip when APP_HOST and
  TORTISE_HOST are the same server. To exercise the gate, run the dashboard
  harness instead: `tests/e2e/auth/test_admin_app_origin.py`.

Post-deploy (CI / manual):
  RUN_BLOG_E2E=1 BASE_URL=https://premiselabs.co \
    TORTISE_HOST=https://tortoise.premiselabs.co \
    APP_HOST=https://app.premiselabs.co pytest tests/e2e/test_blog.py -v

Write-path tests (#4220): the two tests that CREATE rows are marked
``blog_write`` and are NOT run by the deploy job — a deploy must not mutate
production content. They run on demand against a chosen target:
  RUN_BLOG_E2E=1 ALLOW_PROD=1 BASE_URL=https://premiselabs.co \
    TORTISE_HOST=https://tortoise.premiselabs.co BLOG_E2E_AGENT_KEY=... \
    pytest tests/e2e/test_blog.py -v -m blog_write
Also available as the `Blog write E2E (manual)` workflow (workflow_dispatch).
Both write tests DELETE the row they created (agent API DELETE, in a
``finally:``) and assert it is gone — see #4220.
"""

from __future__ import annotations

import contextlib
import os
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urljoin, urlparse

import pytest
import requests

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BLOG_E2E") != "1",
    reason="RUN_BLOG_E2E=1 required (blog e2e suite)",
)

# Harness contract (test_legal_pages.py convention): no production assertions
# pre-merge — ALLOW_PROD=1 is required to point at https:// URLs. APP is in this
# guard because it is the only origin whose DEFAULT is an https production URL:
# a local run that passes only BASE_URL/TORTISE_HOST would otherwise keep its
# default and make live calls to app.premiselabs.co.
COMPANY = os.environ.get("BASE_URL", "http://127.0.0.1:8788").rstrip("/")
TORTISE = os.environ.get("TORTISE_HOST", "http://127.0.0.1:8788").rstrip("/")
# The BFF/session origin (#4054/#4171). The admin console is SERVED here — the
# marketing origin only redirects to it — so any assertion about the gate's
# behaviour belongs on this origin. Local runs point every host at one wrangler
# server (APP_HOST=http://127.0.0.1:8788), in which case the host split does not
# exist and the cross-host leg is skipped (see `_SEPARATE_HOSTS`).
APP = os.environ.get("APP_HOST", "https://app.premiselabs.co").rstrip("/")
_SEPARATE_HOSTS = APP != TORTISE
if os.environ.get("ALLOW_PROD") != "1" and any(
    h.startswith("https://") for h in (COMPANY, TORTISE, APP)
):
    # `allow_module_level` is REQUIRED: without it pytest raises
    # "Using pytest.skip outside of a test" and INTERRUPTS collection, so a local
    # run that omitted APP_HOST errored out instead of running (or skipping).
    pytest.skip(
        "ALLOW_PROD=1 required for https targets (no production assertions pre-merge)",
        allow_module_level=True,
    )
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "tortoise-blog-e2e"


def test_blog_index_ssr() -> None:
    """E2E-1: /blog is server-rendered HTML with the blog chrome."""
    r = SESSION.get(f"{TORTISE}/blog", timeout=20)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    body = r.text
    assert "<title>Blog" in body  # SEO head rendered server-side
    assert 'rel="canonical"' in body
    assert "/consent.js" in body  # analytics head


def test_blog_article_ssr() -> None:
    """E2E-1: an article page (when a published post exists) renders SSR +
    sanitized (E2E-14) + JSON-LD."""
    r = SESSION.get(f"{TORTISE}/blog", timeout=20)
    if r.status_code != 200:
        pytest.skip("blog index unavailable")
    import re

    slugs = re.findall(r'href="/blog/([a-z0-9]+(?:-[a-z0-9]+)*)"', r.text)
    slugs = [s for s in slugs if s not in ("feed.xml", "sitemap.xml")]
    if not slugs:
        pytest.skip("no published posts seeded")
    slug = slugs[0]
    a = SESSION.get(f"{TORTISE}/blog/{slug}", timeout=20)
    assert a.status_code == 200
    body = a.text
    assert "application/ld+json" in body  # BlogPosting schema
    assert "og:title" in body
    # E2E-14: only the known-baseline scripts are allowed (ld+json schemas,
    # consent.js, blog-config, blog.js) — any injected/unknown script fails.
    allowed = {
        "application/ld+json",
        "src=\"/consent.js\"",
        "id=\"blog-config\"",
        "src=\"/blog/blog.js\"",
    }
    for m in re.finditer(r"<script([^>]*)>", body):
        attrs = m.group(1)
        assert any(a in attrs for a in allowed), f"unexpected script tag: {attrs}"


def test_host_isolation_blog() -> None:
    """E2E-7: company host 301s /blog* to the tortoise host; prefix
    false-positives NOT redirected."""
    for path in ("/blog", "/blog/", "/blog/any-slug"):
        r = SESSION.get(f"{COMPANY}{path}", timeout=20, allow_redirects=False)
        assert r.status_code == 301, f"{path} → {r.status_code}"
        assert r.headers.get("location", "").startswith(f"{TORTISE}/blog"), (
            f"{path} location: {r.headers.get('location')}"
        )
    for path in ("/blogpost", "/blog-extra", "/blogpost/x"):
        r = SESSION.get(f"{COMPANY}{path}", timeout=20, allow_redirects=False)
        assert not r.is_redirect and "location" not in r.headers, (
            f"{path} should NOT redirect (got {r.status_code})"
        )


def test_blog_feed_and_sitemap() -> None:
    """E2E-11: feed + sitemap are valid XML (published-only URLs)."""
    feed = SESSION.get(f"{TORTISE}/blog/feed.xml", timeout=20)
    assert feed.status_code == 200
    assert "application/rss+xml" in feed.headers.get("content-type", "")
    root = ET.fromstring(feed.text)  # raises on malformed XML
    items = root.findall(".//item")
    for item in items:
        link = item.findtext("link") or ""
        assert link.startswith(f"{TORTISE}/blog/"), f"feed link not absolute: {link}"

    sitemap = SESSION.get(f"{TORTISE}/blog/sitemap.xml", timeout=20)
    assert sitemap.status_code == 200
    sm = ET.fromstring(sitemap.text)
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    urls = [u.findtext(f"{ns}loc") or "" for u in sm.findall(f"{ns}url")]
    assert any(u.startswith(f"{TORTISE}/blog") for u in urls), "sitemap has no blog URLs"


def test_robots_txt_lists_blog_sitemap() -> None:
    """robots.txt cross-submission includes the blog sitemap."""
    r = SESSION.get(f"{TORTISE}/robots.txt", timeout=20)
    assert r.status_code == 200
    assert f"{TORTISE}/blog/sitemap.xml" in r.text


@pytest.mark.skipif(
    not _SEPARATE_HOSTS,
    reason="APP_HOST == TORTISE_HOST: that one server is the marketing project, "
    "which does not serve the admin gate (it redirects /admin to APP_ORIGIN)",
)
def test_admin_gate_redirects_unauthenticated() -> None:
    """E2E-12 + #3080: the ADMIN GATE, /admin/* without a session → 302 /auth?next=<path>.

    Asserted on the APP origin: that is where the console is served since #4171
    (`website/apps/dashboard/functions/admin/[[path]].ts`). This test used to hit
    the MARKETING host and expect the bounce — that stopped being true when the
    gate moved (#4171), and it spent the interval failing on `status != 302`.
    Repointed at the origin that owns the behaviour so the assertion tests the
    gate rather than the redirect in front of it (#4409).

    The return-to is load-bearing: without it the post-login redirect always
    landed on the app root, so /admin was unreachable by navigation.
    """
    r = SESSION.get(f"{APP}/admin/blog", timeout=20, allow_redirects=False)
    assert r.status_code == 302
    loc = r.headers.get("location", "")
    assert "/auth" in loc
    # #3080: the bounce must carry an allowlisted return-to.
    assert "next=" in loc, f"return-to missing from bounce: {loc}"
    nxt = parse_qs(urlparse(loc).query).get("next", [""])[0]
    assert nxt == "/admin/blog", f"unexpected return-to: {nxt!r}"
    # Open-redirect guard: a path, never an absolute or protocol-relative URL.
    assert nxt.startswith("/") and not nxt.startswith("//"), f"unsafe return-to: {nxt!r}"
    # No admin content in the redirect target body (loc is root-relative, so
    # resolve it against APP — requests needs an absolute URL).
    a = SESSION.get(urljoin(f"{APP}/", loc), timeout=20)
    assert "Review queue" not in a.text


@pytest.mark.skipif(
    not _SEPARATE_HOSTS,
    reason="APP_HOST == TORTISE_HOST (local single-server run): no host split to assert",
)
def test_marketing_admin_redirects_to_the_app_origin() -> None:
    """#4171/#4409: the marketing host hands /admin to the origin that serves it.

    Single hop (W2 forbids a chain) and a 302, not a 301 (§12: a new branch for a
    moved surface must stay reclaimable). The destination is the console ROOT, so
    a deep path is normalised rather than carried — the console's own routes are
    hash-based (`#/edit/:id`), and a fragment is preserved by the user agent
    across the redirect.
    """
    r = SESSION.get(f"{TORTISE}/admin/blog", timeout=20, allow_redirects=False)
    assert r.status_code == 302, (
        f"marketing /admin/blog -> {r.status_code}; want a single-hop 302 "
        "(§12: a NEW branch for the moved surface is 302, never 301)"
    )
    loc = r.headers.get("location", "")
    assert loc == f"{APP}/admin", (
        f"marketing /admin/blog -> {loc!r}; want exactly {APP + '/admin'!r} "
        "(a chained or off-origin target is the failure this guards)"
    )
    # The console is reachable at the destination (the gate answers, not 404).
    a = SESSION.get(f"{APP}/admin", timeout=20, allow_redirects=False)
    assert a.status_code in (200, 302), f"{APP}/admin -> {a.status_code}, console unreachable"


@pytest.mark.skipif(
    not _SEPARATE_HOSTS,
    reason="APP_HOST == TORTISE_HOST: that one server is the marketing project, "
    "which does not serve the admin gate (it redirects /admin to APP_ORIGIN)",
)
def test_admin_gate_return_to_is_scoped_to_admin() -> None:
    """#3080: the return-to allowlist only ever yields a same-origin /admin path.

    Asserted on the APP origin for the same reason as its sibling above: the
    `next=` return-to is minted by the admin GATE, which lives on the app origin.
    The marketing host answers `/admin*` with a bare `Location: <app>/admin` and
    no query, so probing it here could never satisfy `nxt.startswith("/admin")`
    (#4409).
    """
    for path in ("/admin", "/admin/", "/admin/blog", "/admin/assets/x.js"):
        r = SESSION.get(f"{APP}{path}", timeout=20, allow_redirects=False)
        assert r.status_code == 302, f"{path} → {r.status_code}"
        loc = r.headers.get("location", "")
        nxt = parse_qs(urlparse(loc).query).get("next", [""])[0]
        assert nxt.startswith("/admin"), f"{path} → unsafe return-to {nxt!r}"
        assert not nxt.startswith("//"), f"{path} → protocol-relative {nxt!r}"


def test_agent_api_rejects_bad_actors() -> None:
    """E2E-8: the agent publish API rejects unauthenticated writes."""
    url = f"{TORTISE}/blog/api/posts"
    # No key
    r = SESSION.post(url, json={"title": "x", "body": "y"}, timeout=20)
    assert r.status_code == 401, f"no-key → {r.status_code}"
    # Invalid key
    r = SESSION.post(
        url,
        json={"title": "x", "body": "y"},
        headers={"X-Agent-Key": "invalid-key"},
        timeout=20,
    )
    assert r.status_code == 401, f"bad-key → {r.status_code}"
    # Anonymous POST never creates a row (404/401 — no 201)
    assert r.status_code != 201


def test_purge_endpoint_rejects_unauthenticated() -> None:
    """#1865: /blog/api/purge is admin-gated — no session → 401 (no purge fires)."""
    r = SESSION.post(f"{TORTISE}/blog/api/purge", json={"slug": "any-slug"}, timeout=20)
    assert r.status_code == 401, f"purge no-session → {r.status_code}"


def test_agent_api_rejects_unauthenticated_delete() -> None:
    """#4220: the new DELETE surface is not an anonymous delete.

    Mutates nothing (no key → no write), so it runs on every deploy — the
    destructive surface gets a fail-closed check on each one. Falsifiable: a
    change that let DELETE fail open would return 200/404 here, not 401.
    """
    url = f"{TORTISE}/blog/api/posts/any-slug"
    no_key = SESSION.delete(url, timeout=20)
    assert no_key.status_code == 401, f"delete no-key → {no_key.status_code}"
    bad_key = SESSION.delete(url, headers={"X-Agent-Key": "invalid-key"}, timeout=20)
    assert bad_key.status_code == 401, f"delete bad-key → {bad_key.status_code}"


# ── #1864/#1865/#1866: crawler-visibility lifecycle + meta contract ─────────
# These need a VALID agent key (provisioned in blog_agent_keys with
# agent_name='blog-e2e'; pass the raw key as BLOG_E2E_AGENT_KEY). Without it
# the lifecycle tests SKIP — they cannot create/publish posts anonymously.
# The meta-contract negative test (400 on 61/156 chars) also needs the key
# because the agent API rejects invalid keys before validating the body.
AGENT_KEY = os.environ.get("BLOG_E2E_AGENT_KEY", "")
AGENT_HEADERS = {"X-Agent-Key": AGENT_KEY, "Content-Type": "application/json"}
NO_AGENT_KEY = pytest.mark.skipif(
    not AGENT_KEY,
    reason="BLOG_E2E_AGENT_KEY required (provision blog-e2e key in blog_agent_keys)",
)
# #4220: the deploy job runs this file with `-m "not blog_write"`. A test that
# creates/publishes prod content must not run on every deploy — it mutates the
# editorial queue and, if it fails mid-run, leaves residue. These two run on
# demand (workflow_dispatch / explicit local invocation).
BLOG_WRITE = pytest.mark.blog_write


def _delete_post(url: str, slug: str) -> requests.Response:
    """DELETE one of our own posts via the agent API (#4220)."""
    return SESSION.delete(f"{url}/{slug}", headers=AGENT_HEADERS, timeout=20)


def _unpublish_best_effort(url: str, slug: str) -> None:
    """PATCH status=draft, ignoring failure (#4316).

    DELETE is DRAFT-ONLY (the recorded lifecycle has no published→deleted
    transition), so a caller that may be facing a PUBLISHED row — the pre-clean
    of a run killed mid-lifecycle — must unpublish first, or the pre-clean's
    DELETE 409s and leaves the stale slug to collide with its own create.
    Best-effort: an absent row 404s, which is the normal case.
    """
    with contextlib.suppress(Exception):
        SESSION.patch(f"{url}/{slug}", json={"status": "draft"},
                      headers=AGENT_HEADERS, timeout=20)


def _delete_post_verified(url: str, slug: str) -> None:
    """Delete `slug` and assert the row is GONE, not merely unpublished (#4220).

    404 on the first call is tolerated — the row may never have been created
    (e.g. the test failed before its POST). A 200 is then re-probed: if the
    DELETE had only unpublished, or silently no-op'd, the second call would
    return 200 again rather than 404. The re-probe is the falsifiable half.
    """
    first = _delete_post(url, slug)
    assert first.status_code in (200, 404), f"cleanup DELETE → {first.status_code} {first.text[:200]}"
    if first.status_code == 200:
        again = _delete_post(url, slug)
        assert again.status_code == 404, (
            f"{slug} still present after DELETE ({again.status_code}) — residue would accumulate"
        )


@BLOG_WRITE
@NO_AGENT_KEY
def test_agent_api_meta_length_contract() -> None:
    """#1866: agent API rejects meta fields beyond the editor/SSR contract
    (60/155) — a 61/156-char value must 400, boundary 60/155 must 200."""
    url = f"{TORTISE}/blog/api/posts"
    long_title = "meta contract e2e " + "x" * 30
    # #4220: a DETERMINISTIC slug. The old `abs(hash(long_title)) % 100000`
    # re-randomised per process (PYTHONHASHSEED), so every run minted a NEW
    # slug — a fresh row per deploy with no name to clean up. One stable slug
    # keeps residue bounded to a single row even if a run is killed outright.
    slug = "meta-contract-e2e"

    # #4220: pre-clean — a crashed prior run can leave this exact slug behind,
    # and then the boundary POST below would 409 instead of 201. Absent is the
    # normal case, so the result is not asserted here.
    _unpublish_best_effort(url, slug)  # #4316: DELETE is draft-only
    _delete_post(url, slug)

    try:
        # Create with over-limit meta fields → 400 validation
        r = SESSION.post(
            url,
            json={
                "title": long_title,
                "body": "body",
                "slug": slug,
                "meta_title": "t" * 61,
                "meta_description": "d" * 156,
            },
            headers=AGENT_HEADERS,
            timeout=20,
        )
        assert r.status_code == 400, f"over-limit meta → {r.status_code}"
        body = r.json()
        assert "meta_title" in body, f"expected meta_title error, got {body}"
        assert "meta_description" in body, f"expected meta_description error, got {body}"

        # Boundary values (60/155) → accepted
        r = SESSION.post(
            url,
            json={
                "title": long_title,
                "body": "body",
                "slug": slug,
                "meta_title": "t" * 60,
                "meta_description": "d" * 155,
            },
            headers=AGENT_HEADERS,
            timeout=20,
        )
        assert r.status_code == 201, f"boundary meta → {r.status_code}"
    finally:
        # #4220: delete the row we created and verify it is gone. The old
        # note — "let the row sit in the review queue" — was the defect.
        _delete_post_verified(url, slug)


@BLOG_WRITE
@NO_AGENT_KEY
def test_publish_lifecycle_crawler_visibility() -> None:
    """#1864 + #1865: the crawler-visibility lifecycle —
    draft → 404+noindex → publish → 200 → unpublish (agent path) → 404+noindex;
    plus robots.txt Disallow and admin-shell X-Robots-Tag."""
    # robots.txt hardening (#1864)
    robots = SESSION.get(f"{TORTISE}/robots.txt", timeout=20)
    assert robots.status_code == 200
    assert "Disallow: /admin" in robots.text, "robots.txt missing Disallow: /admin"

    # Admin shell: unauthenticated → 302 (no shell served), so the noindex
    # header can't be asserted anonymously; the gate redirect is the contract
    # (already covered by test_admin_gate_redirects_unauthenticated).
    # The X-Robots-Tag on non-published blog responses is asserted below.

    # #4220: a stable slug by default (an explicit RUN_ID still overrides).
    # A per-run random slug minted a NEW row every run; one stable slug bounds
    # residue to a single row if a run is killed outright.
    run_seed = os.environ.get("RUN_ID") or "crawler"
    slug = f"lifecycle-e2e-{run_seed[:8]}"
    url = f"{TORTISE}/blog/api/posts"
    title = f"Lifecycle E2E {slug}"

    # #4220: pre-clean — a crashed prior run can leave this slug PUBLISHED, which
    # would fail the "draft → 404" assertion below. Unpublish (DELETE is
    # draft-only — #4316) then delete it first (absent is the normal case, so
    # the result is not asserted here).
    _unpublish_best_effort(url, slug)
    _delete_post(url, slug)

    def create() -> None:
        r = SESSION.post(
            url,
            json={"title": title, "body": "draft body", "slug": slug},
            headers=AGENT_HEADERS,
            timeout=20,
        )
        assert r.status_code == 201, f"create → {r.status_code} {r.text[:200]}"

    def unpublish_agent() -> None:
        r = SESSION.patch(
            f"{url}/{slug}",
            json={"status": "draft"},
            headers=AGENT_HEADERS,
            timeout=20,
        )
        assert r.status_code == 200, f"unpublish → {r.status_code} {r.text[:200]}"

    def published() -> None:
        r = SESSION.patch(
            f"{url}/{slug}",
            json={"status": "published"},
            headers=AGENT_HEADERS,
            timeout=20,
        )
        assert r.status_code == 200, f"publish → {r.status_code} {r.text[:200]}"

    def article_visible() -> tuple[int, str]:
        a = SESSION.get(f"{TORTISE}/blog/{slug}", timeout=20)
        return a.status_code, a.headers.get("x-robots-tag", "")

    def article_in_feed_sitemap() -> tuple[bool, bool]:
        feed = SESSION.get(f"{TORTISE}/blog/feed.xml", timeout=20).text
        sitemap = SESSION.get(f"{TORTISE}/blog/sitemap.xml", timeout=20).text
        return slug in feed, slug in sitemap

    try:
        create()
        # Draft: invisible to crawlers (404 + explicit noindex)
        code, tag = article_visible()
        assert code == 404, f"draft → {code}"
        assert "noindex" in tag, f"draft x-robots-tag missing noindex: {tag!r}"
        in_feed, in_sitemap = article_in_feed_sitemap()
        assert not in_feed and not in_sitemap, "draft leaked into feed/sitemap"

        # Publish: indexable (200, no noindex, present in feed/sitemap)
        published()
        code, tag = article_visible()
        assert code == 200, f"published → {code}"
        assert "noindex" not in tag, f"published x-robots-tag has noindex: {tag!r}"
        in_feed, in_sitemap = article_in_feed_sitemap()
        assert in_feed and in_sitemap, "published missing from feed/sitemap"

        # Unpublish via the agent API: back to 404 + noindex; #1865 expects
        # the edge cache to be purged (the origin 404 + no-store is the
        # contract; the purge itself is verified by the immediate 404 here).
        unpublish_agent()
        code, tag = article_visible()
        assert code == 404, f"unpublished → {code}"
        assert "noindex" in tag, f"unpublished x-robots-tag missing noindex: {tag!r}"
        in_feed, in_sitemap = article_in_feed_sitemap()
        assert not in_feed and not in_sitemap, "unpublished leaked into feed/sitemap"
    finally:
        # #4220: take the row off the public surface first (best-effort — it may
        # still be PUBLISHED if the run died mid-lifecycle), then DELETE it and
        # verify it is gone. The old cleanup stopped at the unpublish and left
        # the row in the production review queue forever.
        with contextlib.suppress(Exception):
            unpublish_agent()
        _delete_post_verified(url, slug)


@BLOG_WRITE
@NO_AGENT_KEY
def test_delete_refuses_a_published_post() -> None:
    """#4316 P1: DELETE is DRAFT-ONLY — a published post must survive it.

    The recorded lifecycle (plan §W4) is draft → published → archived
    (terminal): there is no published→deleted transition. `created_by` is the
    CREATOR while an operator publishes with `published_by`, so without the
    draft-only guard the agent key could irreversibly destroy an
    operator-approved, LIVE article.

    Falsifiable in both directions: pre-fix, the DELETE returns 200 and the
    article disappears; and the still-served assertion catches a 409 that
    deleted anyway (a refusal that is not real).
    """
    url = f"{TORTISE}/blog/api/posts"
    slug = "lifecycle-e2e-published-delete"

    def patch(payload: dict) -> requests.Response:
        return SESSION.patch(f"{url}/{slug}", json=payload, headers=AGENT_HEADERS, timeout=20)

    # Pre-clean: a crashed prior run can leave this slug published (DELETE is
    # draft-only) or draft. Unpublish, then delete; absent is the normal case.
    _unpublish_best_effort(url, slug)
    _delete_post(url, slug)

    try:
        r = SESSION.post(
            url,
            json={"title": f"Delete guard {slug}", "body": "live body", "slug": slug},
            headers=AGENT_HEADERS,
            timeout=20,
        )
        assert r.status_code == 201, f"create → {r.status_code} {r.text[:200]}"
        r = patch({"status": "published"})
        assert r.status_code == 200, f"publish → {r.status_code} {r.text[:200]}"

        refused = _delete_post(url, slug)
        assert refused.status_code == 409, (
            f"DELETE published → {refused.status_code} (want 409) {refused.text[:200]}"
        )
        assert refused.json().get("error") == "published", refused.text[:200]

        # The refusal must be REAL: the published article is still served.
        live = SESSION.get(f"{TORTISE}/blog/{slug}", timeout=20)
        assert live.status_code == 200, (
            f"published post gone after a refused DELETE ({live.status_code}) — the guard deleted it anyway"
        )

        # And the draft-only guard is a status gate, not a broken delete: once
        # unpublished, the same call removes the row.
        assert patch({"status": "draft"}).status_code == 200, "unpublish failed"
        assert _delete_post(url, slug).status_code == 200, "draft DELETE after unpublish failed"
    finally:
        with contextlib.suppress(Exception):
            patch({"status": "draft"})
        _delete_post_verified(url, slug)
