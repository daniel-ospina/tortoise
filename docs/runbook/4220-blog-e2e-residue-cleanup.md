---
title: "4220 Blog E2E Residue Cleanup — Runbook"
type: operations
domain: operations
doc_status: live
created: 2026-09-20
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# 4220 — Blog E2E residue cleanup: runbook

> Procedure for removing the synthetic `blog_posts` rows that the blog E2E
> suite deposited in PRODUCTION before #4220 was fixed. The fix stops NEW
> residue; this runbook removes what already accumulated.
>
> **Status: NOT YET RUN.** The cleanup needs a credential this repo's agents do
> not hold and must not ask for (`SUPABASE_ACCESS_TOKEN`, or a locally linked
> `supabase` CLI). It is a human step — see §4.

---

## 1. What happened (mint path, verified in repo)

`.github/workflows/deploy-pages.yml` job `verify-blog` (`needs: deploy`) ran
`tests/e2e/test_blog.py` against production on **every deploy**, with a
write-capable agent key. Two tests created rows and deliberately left them:

| Test | Slug | Old cleanup |
|---|---|---|
| `test_agent_api_meta_length_contract` | `meta-contract-<n>` | *"let the row sit in the review queue"* — none |
| `test_publish_lifecycle_crawler_visibility` | `lifecycle-e2e-<seed>` | *"leave the row as a draft (never republish)"* — none |

The meta-contract slug came from `abs(hash(title)) % 100000`. Python string
hashing is **randomised per process** (`PYTHONHASHSEED`), so `n` differed on
every run: no two runs even collided, and the pile grew without bound. That is
the mechanism behind the owner's observation — *"a lot of blog drafts that are
clearly tests"*.

Both rows stayed `draft`, so this was never an SEO or crawler defect (the suite
asserted `noindex` + absence from feed/sitemap). It polluted the editorial
review queue a human has to read.

## 2. What the fix changed (no new residue)

* **Both creating tests now DELETE the row they created** via the agent API
  (`DELETE /blog/api/posts/:slug`, added by #4220), in a `finally:` so it runs
  on failure too, and then assert the slug is **gone** — a second DELETE must
  return 404. Leaving it as a draft — the old behaviour — is no longer
  possible without failing the test.
* **Slugs are deterministic** (`meta-contract-e2e`, `lifecycle-e2e-crawler`),
  so even a run killed outright can leave at most ONE row per test, with a
  name that re-runs reuse (each test pre-cleans its slug first).
* **The write tests are off the deploy path** (marker `blog_write`; the deploy
  job runs `-m "not blog_write"`) and the deploy job no longer receives
  `BLOG_E2E_AGENT_KEY` at all. They run on demand — see §3.
* The DELETE endpoint is agent-authenticated and scoped to
  `created_by = <calling agent>` exactly like PATCH; `archived` remains
  terminal (409).

## 3. Running the write tests deliberately

On demand, against a chosen target:

**GitHub UI / CLI** — `Blog write E2E (manual, #4220)`, mode `lifecycle`:

```bash
gh workflow run "Blog write E2E (manual, #4220)" \
  --repo daniel-ospina/tortoise \
  -f mode=lifecycle
# against a preview deployment instead of production:
gh workflow run "Blog write E2E (manual, #4220)" \
  --repo daniel-ospina/tortoise \
  -f mode=lifecycle \
  -f base_url=https://<preview>.pages.dev \
  -f tortoise_host=https://<preview>.pages.dev
```

**Locally** (same flags the CI job uses):

```bash
RUN_BLOG_E2E=1 ALLOW_PROD=1 TORTOISE_TEST_CARVE_OUT=1 \
  BASE_URL=https://premiselabs.co \
  TORTISE_HOST=https://tortoise.premiselabs.co \
  BLOG_E2E_AGENT_KEY=<provisioned blog-e2e key> \
  uv run pytest tests/e2e/test_blog.py -v -m blog_write
```

The read-only tests still run on every deploy:
`pytest tests/e2e/test_blog.py -v -m "not blog_write"`.

## 4. Removing the existing pile (HUMAN WITH CREDENTIAL)

The cleanup is `graph-scripts/4220_blog_residue_cleanup.py`. It is **dry-run by
default**; deletion requires `--execute`. Two independent guards:

* **Guard A — prefix.** `--prefix` must be `meta-contract-`,
  `lifecycle-e2e-`, or `both`. An arbitrary prefix is refused, so a typo cannot
  reach editorial content.
* **Guard B — `created_by = 'blog-e2e'`.** Every statement — enumerate, delete,
  and the post-delete verification — carries this predicate. The owner's
  `hello-tortoise-first-post` draft (a human-authored row in the same table) is
  never in scope.

Credential: `SUPABASE_ACCESS_TOKEN` (Management API SQL — the same access path
as the #2146 cleanup), or a locally linked `supabase` CLI
(`--via cli`, i.e. `supabase db query --linked`).

**Step 1 — dry-run (lists slugs and counts, writes nothing):**

```bash
gh workflow run "Blog write E2E (manual, #4220)" \
  --repo daniel-ospina/tortoise -f mode=purge -f prefix=both
# or locally:
SUPABASE_ACCESS_TOKEN=... python3 graph-scripts/4220_blog_residue_cleanup.py --prefix both
```

**Step 2 — review the listed slugs** (they should all read
`[draft] blog-e2e`, plus any `lifecycle-e2e-*` a crashed run left `published`).

**Step 3 — execute:**

```bash
gh workflow run "Blog write E2E (manual, #4220)" \
  --repo daniel-ospina/tortoise -f mode=purge -f prefix=both -f execute=true
# or locally:
SUPABASE_ACCESS_TOKEN=... python3 graph-scripts/4220_blog_residue_cleanup.py \
  --prefix both --execute
```

The script re-counts after the DELETE and exits non-zero if anything remains —
"deleted" is never reported without the verification query agreeing.

**Edge cache:** if a listed row is `published`, its page may be cached at the
Cloudflare edge. Purge it (`POST /blog/api/purge {slug}`, admin session) or
confirm the route is uncached — as of the #1865 handoff note blog pages serve
`cf-cache-status: DYNAMIC` (purge inert), so this is normally a no-op. Then
confirm `https://tortoise.premiselabs.co/blog/<slug>` is 404.

Exit codes: `0` goal met; `1` deletes ran but rows remain; `2`
refused/could-not-determine (missing credential, unknown prefix, SQL error) —
fail-closed, never a silent "nothing to do".

## 5. Verification checklist

- [ ] Dry-run lists only `blog-e2e`-created `meta-contract-*` /
      `lifecycle-e2e-*` rows; `hello-tortoise-first-post` is NOT listed.
- [ ] `--execute` run reports `verified: 0 rows ... remain`.
- [ ] A fresh dry-run reports `nothing to clean — 0 rows match`.
- [ ] Any previously-published slug returns 404 on the blog host.
