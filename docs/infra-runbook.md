---
title: Tortoise Hosted Platform Infrastructure Runbook
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-infra
aboutObjects: fly-io, falkordb, cloudflare
created: 2026-08-03
---

# Tortoise Hosted Platform — Infrastructure Runbook

**Epic:** #7711
**Last updated:** 2026-08-03

## 1. Initial Provisioning

### Prerequisites
- Fly.io account with `flyctl` authenticated
- Cloudflare account with `wrangler` authenticated (or dashboard access)
- GitHub repo access with Actions secrets permission
- `premiselabs.co` domain on Cloudflare DNS

### FalkorDB Cloud (managed database)
FalkorDB runs on FalkorDB Cloud (managed) — provides AOF durability, automated
backups, and multi-tenancy. Create the instance in the FalkorDB Cloud console,
then set the connection string:

**tortoise-api (FastAPI) on Fly.io:**
```bash
fly apps create tortoise-y4mjjq   # or use the existing app
fly secrets set FASTAPI_INTERNAL_KEY=$(openssl rand -hex 32)
fly secrets set TORTOISE_SECRET_PEPPER=$(openssl rand -hex 32)
# Set FALKORDB_CLOUD_URI in GitHub Actions secrets (deploy workflow sets it on Fly):
#   docker://tortoise:<password>@<instance-endpoint>:53171/tortoise
fly deploy
fly certs create api.premiselabs.co
```

### Cloudflare Pages (Dashboard)
```bash
# Create project in Cloudflare dashboard: "tortoise-dashboard"
# Deploy placeholder:
wrangler pages deploy apps/dashboard --project-name=tortoise-dashboard
# Custom domain: app.premiselabs.co → tortoise-dashboard.pages.dev
```

### R2 Bucket
```bash
wrangler r2 bucket create tortoise-backups
# Lifecycle: delete objects older than 28 days (set in dashboard)
```

### DNS (Cloudflare)
| Type | Name | Target |
|------|------|--------|
| CNAME | api | tortoise-api.fly.dev |
| CNAME | app | tortoise-dashboard.pages.dev |

### GitHub Actions
Set these secrets in repo Settings → Secrets and variables → Actions:
- `FLY_API_TOKEN` — from `flyctl auth token`
- `CLOUDFLARE_API_TOKEN` — from Cloudflare dashboard (Pages + R2 permissions)

## 2. Secrets Rotation

```bash
# Rotate FASTAPI_INTERNAL_KEY (no downtime — old key works during deploy)
fly secrets set FASTAPI_INTERNAL_KEY=$(openssl rand -hex 32) -a tortoise-api
fly deploy -a tortoise-api

# Rotate TORTOISE_SECRET_PEPPER (⚠️ DOWNTIME — invalidates all API key hashes)
# Must re-provision all tenant API keys after rotation
fly secrets set TORTOISE_SECRET_PEPPER=$(openssl rand -hex 32) -a tortoise-api
fly deploy -a tortoise-api
```

## 3. Rollback

```bash
# List releases
fly releases -a tortoise-api

# Rollback to previous release
fly deploy --image $(fly releases -a tortoise-api --json | jq -r '.[1].ImageRef') -a tortoise-api
```

## 4. Health Check

```bash
# API health
curl https://api.premiselabs.co/health
# → {"status": "ok"}

# Verify FalkorDB connectivity
fly ssh console -a tortoise-api -C "python -c 'from tortoise.sdk import TortoiseSDK; sdk = TortoiseSDK(namespace=\"registry\"); print(sdk.db.ping())'"
```

## 4.5 Local Development — Use the Hosted Instance

Local tooling (MCP server, SDK scripts, graph-scripts) resolves its DB target
from `TORTOISE_DB_URI`:

1. Copy `FALKORDB_CLOUD_URI` (GitHub secret / FalkorDB Cloud console) into the
   repo-root `.env` (gitignored): `TORTOISE_DB_URI=redis://default:<pw>@<host>:<port>/tortoise`
2. The MCP server loads `.env` at startup and **fails loud** if the URI is
   unset — it never silently connects to an empty embedded graph.
3. `FalkorProjection.from_uri` accepts `docker://`, `redis://`, and `rediss://`
   schemes, so both local containers and the cloud instance work from the same
   env var.
4. Restart the MCP server after changing `.env` (the connection is resolved once
   at startup).

Do **not** put credentials in `.mcp.json` or commit `.env` — `.env` is gitignored
and `.mcp.json` reads `\${TORTOISE_DB_URI}` from the environment.

## 5. Dashboard Deploy

```bash
# Build and deploy dashboard
cd apps/dashboard
npm run build
wrangler pages deploy dist --project-name=tortoise-dashboard
```

## Secrets Matrix

| Secret | tortoise-api (Fly.io) | FalkorDB Cloud | GitHub Actions |
|--------|----------------------|---------------|----------------|
| FASTAPI_INTERNAL_KEY | ✅ | — | — |
| TORTOISE_SECRET_PEPPER | ✅ | — | — |
| FALKORDB_CLOUD_URI | ✅ (set via GitHub secret → Fly) | ✅ (instance creds) | ✅ |
| FLY_API_TOKEN | — | — | ✅ |
| CLOUDFLARE_API_TOKEN | — | — | ✅ |

## Reproducibility Test
Can a fresh Fly.io account + Cloudflare account follow §1 from zero and arrive at the same infra?
- [ ] FalkorDB Cloud instance provisioned, FALKORDB_CLOUD_URI secret set
- [ ] `fly apps create tortoise-y4mjjq` → deploys, health check passes, connects to FalkorDB Cloud
- [ ] `api.premiselabs.co` → resolves, TLS valid, /health returns ok (db: connected)
- [ ] `app.premiselabs.co` → resolves, serves dashboard placeholder
- [ ] GitHub push to main → auto-deploys tortoise-api
