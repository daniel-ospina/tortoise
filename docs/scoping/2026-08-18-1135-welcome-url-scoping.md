---
title: Scoping — #1135 welcome_url consolidation with EMAIL_LINK_BASE_URL
type: engineering
domain: platform
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-18
---

# Scoping — #1135 Consolidate hardcoded welcome_url with EMAIL_LINK_BASE_URL

**Level:** task · **Complexity:** micro · **Date:** 2026-08-18

## Problem

`tortoise/hosted_api.py` hardcodes two hosts that are (or should be) env-driven:

| Site | Host | Env var (single source) | Hardcoded today |
|---|---|---|---|
| Static Pages site (welcome.html, invite-accept.html) | `tortoise.premiselabs.co` | `EMAIL_LINK_BASE_URL` (default `https://tortoise.premiselabs.co`, see `.env.example:228`, `email_notify._email_link_base()`) | `welcome_url = "https://tortoise.premiselabs.co/welcome.html"` (hosted_api.py:7279) |
| Dashboard app | `app.premiselabs.co` | `TORTOISE_DASHBOARD_URL` (default `https://app.premiselabs.co`, see `__main__.py:694`) | `_BILLING_DEFAULT_*_URL` (hosted_api.py:8026-8028) |

Note: the issue text cites `hosted_api.py:4801` / `:5511-5513` — those line numbers are stale (they match no version of the file; welcome_url was at 6390 and billing defaults at 7137 as of 2026-08-13). The referenced constants are the ones above.

`email_notify.py` already reads `EMAIL_LINK_BASE_URL` via a private `_email_link_base()` helper — the welcome page lives on that same host, so `welcome_url` should reuse it.

## Fix

1. **welcome_url** → `f"{email_link_base()}/welcome.html"` where `email_link_base()` is the (made-public) `email_notify` helper reading `EMAIL_LINK_BASE_URL` (default `https://tortoise.premiselabs.co`).
2. **Billing defaults** → derive the dashboard host from `TORTOISE_DASHBOARD_URL` (default `https://app.premiselabs.co`), matching the CLI claim-flow convention in `__main__.py` — host configured once.
3. Document `TORTOISE_DASHBOARD_URL` in `.env.example`.

Explicitly NOT changing: `_ALLOWED_ORIGINS` (CORS allowlist, different concern), `main.jsx` welcome CTA link (frontend static asset, no env plumbing), `__main__.py` claim flow (already env-driven).

## Tests

New `tests/test_welcome_url_consolidation.py`:
- github callback denied path → redirect host follows `EMAIL_LINK_BASE_URL` (both set + default).
- billing checkout/portal defaults follow `TORTOISE_DASHBOARD_URL` when no `BILLING_*_URL` overrides.

## Acceptance

- No hardcoded `tortoise.premiselabs.co` / `app.premiselabs.co` host literals remain in `hosted_api.py` for these two surfaces.
- Existing behavior unchanged when env vars unset (defaults identical).
