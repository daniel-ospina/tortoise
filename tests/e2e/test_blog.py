"""Blog E2E suite (#1800) — Tortoise blog + CMS verification gate.

Covers the epic's high-level E2E scenarios that are verifiable against a
deployed surface:

  E2E-1  /blog + /blog/<slug> server-rendered (200, content present, SEO head)
  E2E-7  host isolation — premiselabs.co /blog* → 301 tortoise host;
         /blogpost /blog-extra NOT redirected
  E2E-11 /blog/feed.xml + /blog/sitemap.xml valid XML (published-only)
  E2E-12 /admin/* → 302 /auth (no session; no content leaked)
  E2E-8  agent API rejects bad actors (401 no/invalid key; no anonymous write)
  E2E-14 sanitized SSR (no <script> in rendered post bodies)
  robots.txt lists the blog sitemap

Harness contract (follows test_legal_pages.py):
  - RUN_BLOG_E2E=1 REQUIRED — first statement is a runtime module skip; bare
    collection never errors.
  - BASE_URL / TORTISE_HOST env (defaults point at production; local runs pass
    http://127.0.0.1:8788 and TORTISE_HOST=http://127.0.0.1:8788).

Run locally against a wrangler pages dev preview:
  RUN_BLOG_E2E=1 BASE_URL=http://127.0.0.1:8788 \
    TORTISE_HOST=http://127.0.0.1:8788 pytest tests/e2e/test_blog.py -v

Post-deploy (CI / manual):
  RUN_BLOG_E2E=1 BASE_URL=https://premiselabs.co \
    TORTISE_HOST=https://tortoise.premiselabs.co pytest tests/e2e/test_blog.py -v
"""

from __future__ import annotations

import contextlib
import os
import uuid
import xml.etree.ElementTree as ET

import pytest
import requests

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BLOG_E2E") != "1",
    reason="RUN_BLOG_E2E=1 required (blog e2e suite)",
)

# Harness contract (test_legal_pages.py convention): no production assertions
# pre-merge — ALLOW_PROD=1 is required to point at https:// URLs.
COMPANY = os.environ.get("BASE_URL", "http://127.0.0.1:8788").rstrip("/")
TORTISE = os.environ.get("TORTISE_HOST", "http://127.0.0.1:8788").rstrip("/")
if os.environ.get("ALLOW_PROD") != "1" and (COMPANY.startswith("https://") or TORTISE.startswith("https://")):
    pytest.skip("ALLOW_PROD=1 required for https targets (no production assertions pre-merge)")
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


def test_admin_gate_redirects_unauthenticated() -> None:
    """E2E-12: /admin/* without a session → 302 /auth; no content returned."""
    r = SESSION.get(f"{TORTISE}/admin/blog", timeout=20, allow_redirects=False)
    assert r.status_code == 302
    assert "/auth" in r.headers.get("location", "")
    # No admin content in the redirect target body
    a = SESSION.get(r.headers["location"], timeout=20)
    assert "Review queue" not in a.text


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


@NO_AGENT_KEY
def test_agent_api_meta_length_contract() -> None:
    """#1866: agent API rejects meta fields beyond the editor/SSR contract
    (60/155) — a 61/156-char value must 400, boundary 60/155 must 200."""
    url = f"{TORTISE}/blog/api/posts"
    long_title = "meta contract e2e " + "x" * 30
    slug = f"meta-contract-{abs(hash(long_title)) % 100000}"

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
    # Cleanup — the row is draft; drafts are invisible to crawlers either way,
    # but unpublish (already draft) and let the row sit in the review queue.


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

    run_seed = os.environ.get('RUN_ID', uuid.uuid4().hex)
    slug = f"lifecycle-e2e-{run_seed[:8]}"
    url = f"{TORTISE}/blog/api/posts"
    title = f"Lifecycle E2E {slug}"

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
        # Best-effort cleanup: leave the row as a draft (never republish).
        with contextlib.suppress(Exception):
            unpublish_agent()
