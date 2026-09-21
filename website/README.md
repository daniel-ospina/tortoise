# Premise Labs — Landing Page

Single-scroll landing page for **Premise Labs**, the AI lab behind
[Tortoise](https://github.com/daniel-ospina/tortoise).

**Live:** [premiselabs.co](https://premiselabs.co)

## Deploy

**CI owns the deploy.** A push to `main` touching `website/**` runs
`.github/workflows/deploy-pages.yml`, which stages an explicit upload set, `cd`s
into it, and uploads it. Abridged below to the load-bearing commands — CI also
runs `rm -rf "$STAGE"`, five survival guards that abort the step *before* the
upload if a load-bearing entry did not survive, and takes `RUNNER_TEMP` from the
runner (`${RUNNER_TEMP:?}` there; `mktemp -d` here, so the snippet is runnable).

Run this **from the repository root** — `rsync`'s `website/` source is resolved
relative to the current directory, and this README itself lives inside
`website/`, so copy-pasting from there fails with
`link_stat "website/" failed`:

```bash
cd "$(git rev-parse --show-toplevel)"
STAGE="$(mktemp -d)/pages-upload" && mkdir -p "$STAGE"
rsync -a --exclude='/apps/' --exclude='/migrations/' \
  --exclude='node_modules/' --exclude='/.wranglerignore' --exclude='*.md' \
  website/ "$STAGE/"
# wrangler resolves `functions/` from the CWD, so run FROM the upload root.
cd "$STAGE" && npx --yes wrangler@4 pages deploy "$STAGE" \
  --project-name=premise-labs --branch=main
```

### Why the upload root is staged

`wrangler pages deploy <dir>` uploads **every** file under the directory it is
given, and `.wranglerignore` is **not read by wrangler at all** — the Pages
upload path uses a hardcoded `IGNORE_LIST` and no ignore-file read exists. So a
`.wranglerignore` in the upload root is inert: the file was publicly served (`200`
at `/.wranglerignore`) while it claimed to exclude `apps/`. It was deleted in
#3620 and replaced by the staged upload below.

What the stage excludes, and why:

| Excluded | Why |
|---|---|
| `apps/` | Dashboard + blog-admin sources, including a committed `node_modules` tree. The dashboard deploys separately to `tortoise-dashboard` (`app.premiselabs.co`). |
| `migrations/` | The auth session DDL — internal. |
| `*.md` | Internal docs (`README.md` — this file, `website_architecture.md`, the re-auth plan). |
| `node_modules/` | Dependency tree (belt-and-braces; `/apps/` already prunes it). |
| `.wranglerignore` | Deleted; excluded too, so a re-added file cannot be served. |

What **must** stay in the upload root — the deploy step fails loudly if one is
missing, because each fails *silently* otherwise: `functions/`, plus the step
running with the upload root as its **cwd** (`wrangler pages deploy` resolves
`functions/` against `process.cwd()` — never against the directory it uploads —
so a wrong cwd deploys a site with **no Functions**: dead auth on a green
deploy), `_redirects` and `_headers` (consumed from the root), and `admin/`
(generated into the root by the blog-admin build step).

The post-deploy step `Post-deploy — internal paths are not publicly served
(#3620)` then asserts each internal path returns **404** (exactly 404 — a 5xx
fails the step, because "could not read it" is not "it is not served").

**Residual risk:** a *new* top-level entry under `website/` **is** staged unless
it is excluded in the deploy step — but it can no longer ship on a green deploy.
The deploy job runs `tools/check_pages_upload_root.py` **before** the upload,
which fails if any tracked top-level entry is missing from
`config/pages-upload-classification.txt`, and
`tests/test_pages_bindings.py::test_every_top_level_entry_under_website_is_classified`
pins the same table on the full test selection. A new **nested** file under an
already-public directory (e.g. `website/blog/internal.txt`) is **not** caught by
that classification (it is top-level only) — add it to the exclude list **and**
to the post-deploy 404 assertion step by hand (a nested `*.md` is at least
covered by the blanket `*.md` exclusion, but that is a coincidence, not a pin).

For local troubleshooting only, `npx wrangler pages deploy <dir>` still works —
but never point it at `website/` itself.

## Waitlist form (#373)

The CTA beat (`#beat-cta`) contains the waitlist form. Submissions are
collected end-to-end with the same Resend + Supabase edge function pattern
El Dato uses:

```text
form (index.html) ──fetch JSON──▶ waitlist-subscribe edge function
  ├─ validates email + honeypot + rate limit + Turnstile (optional)
  ├─ INSERT waitlist_subscribers (?on_conflict=email → dedup)
  └─ best-effort confirmation email via Resend (fresh inserts only)
```

**Endpoint:** the `WAITLIST_ENDPOINT` constant in `index.html` points at the
`waitlist-subscribe` edge function on the premise-labs Supabase project.

**Turnstile:** the `TURNSTILE_SITE_KEY` constant in `index.html` starts empty
— the widget + script are only injected when a real site key is set (see
[Human steps](#human-steps-launch-blocking)). Never use a placeholder literal.

**Storage:** `waitlist_subscribers` table (migration `0005`) — email (unique),
source, consented_at. Confirmation email includes an unsubscribe link.
Writes go through the function's service role only (RLS enabled, no anon
policy).

**Tests:** `tests/test_waitlist_form.py` (static) +
`tests/test_waitlist_subscribe.mjs` (Node behavioral harness,
`node --experimental-strip-types tests/test_waitlist_subscribe.mjs`).

## Human steps (launch-blocking)

1. **Supabase secrets** (set via
   `supabase secrets set --project-ref ybetwichurajbfswfeqa`):
   `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `TURNSTILE_SECRET_KEY`.
2. **Turnstile site key** → paste into the `TURNSTILE_SITE_KEY` constant in
   `index.html`.
3. **Deploy** — see `supabase/README.md` (CI workflow does it on merge once
   `SUPABASE_ACCESS_TOKEN` + `SUPABASE_DB_URL` repo secrets are set).
4. **Smoke test:** submit a test email → confirm the row appears in Supabase
   Studio and the confirmation email arrives within 30s.

## Tech

- GSAP + ScrollTrigger for canvas graph animation
- Dark slate/cyan palette with green/gold accents
- No framework, no build step — single HTML file
- No analytics/consent scripts on this page (legal constraint, see
  `tests/e2e/test_legal_pages.py`)
