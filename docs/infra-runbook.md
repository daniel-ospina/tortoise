# Tortoise Hosted Platform — Infrastructure Runbook

**Epic:** #7711
**Last updated:** 2026-08-03

## 1. Initial Provisioning

### Prerequisites
- Fly.io account with `flyctl` authenticated
- Cloudflare account with `wrangler` authenticated (or dashboard access)
- GitHub repo access with Actions secrets permission
- `premiselabs.co` domain on Cloudflare DNS

### Fly.io Apps

**tortoise-api (FastAPI):**
```bash
fly apps create tortoise-api
fly secrets set FASTAPI_INTERNAL_KEY=$(openssl rand -hex 32)
fly secrets set TORTOISE_SECRET_PEPPER=$(openssl rand -hex 32)
fly secrets set FALKORDB_PASSWORD=$(openssl rand -hex 16)
fly deploy
fly certs create api.premiselabs.co
```

**falkordb-tortoise (FalkorDB):**
```bash
fly apps create falkordb-tortoise
fly volumes create falkordb_data --size 1 --region iad
fly secrets set FALKORDB_PASSWORD=<same value as tortoise-api>
fly deploy -c falkordb.fly.toml
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

## 5. Dashboard Deploy

```bash
# Build and deploy dashboard
cd apps/dashboard
npm run build
wrangler pages deploy dist --project-name=tortoise-dashboard
```

## Secrets Matrix

| Secret | tortoise-api | falkordb-tortoise | GitHub Actions |
|--------|-------------|-------------------|----------------|
| FASTAPI_INTERNAL_KEY | ✅ | — | — |
| TORTOISE_SECRET_PEPPER | ✅ | — | — |
| FALKORDB_PASSWORD | ✅ | ✅ | — |
| FLY_API_TOKEN | — | — | ✅ |
| CLOUDFLARE_API_TOKEN | — | — | ✅ |

## Reproducibility Test
Can a fresh Fly.io account + Cloudflare account follow §1 from zero and arrive at the same infra?
- [ ] `fly apps create tortoise-api` → deploys, health check passes
- [ ] `fly apps create falkordb-tortoise` → deploys, volume mounted, reachable from tortoise-api
- [ ] `api.premiselabs.co` → resolves, TLS valid, /health returns ok
- [ ] `app.premiselabs.co` → resolves, serves dashboard placeholder
- [ ] GitHub push to main → auto-deploys tortoise-api
