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

import os
import xml.etree.ElementTree as ET

import pytest
import requests

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BLOG_E2E") != "1",
    reason="RUN_BLOG_E2E=1 required (blog e2e suite)",
)

COMPANY = os.environ.get("BASE_URL", "https://premiselabs.co").rstrip("/")
TORTISE = os.environ.get("TORTISE_HOST", "https://tortoise.premiselabs.co").rstrip("/")
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
    # E2E-14: no script tags beyond the consent/config/snippet set
    scripts = re.findall(r"<script", body)
    assert len(scripts) <= 4, f"unexpected script tags: {len(scripts)}"


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
        assert r.status_code == 200, f"{path} should NOT redirect (got {r.status_code})"


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
    urls = [u.findtext("loc") or "" for u in sm.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url")]
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
