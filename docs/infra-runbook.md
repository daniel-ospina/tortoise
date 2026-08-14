---
title: Tortoise Hosted Platform Infrastructure Runbook
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-infra
aboutObjects: fly-io, falkordb, cloudflare
created: 2026-08-03
updated: 2026-08-14
---

# Tortoise Hosted Platform — Infrastructure Runbook

**Epic:** #7711
**Last updated:** 2026-08-14

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
# Deploy the React/Vite SPA (source of truth):
./website/apps/dashboard/deploy.sh   # npm run build + wrangler pages deploy dist
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

## 4.5 Local Development — Local Stays Local

**Best practice: a self-hosted/local instance is intentionally local.** Do not
point local tooling at the hosted (cloud) DB — a remote connection from a local
install defeats the purpose of hosting locally.

- Local tooling (MCP server, SDK scripts, graph-scripts) resolves its DB target
  from `TORTOISE_DB_URI` — canonical local form
  `docker://:falkordb@localhost:6379/tortoise` (compose publishes 127.0.0.1:6379;
  `.mcp.json`, `.env.example`). The legacy `FALKORDB_*` trio defaults to the
  same port (`FALKORDB_PORT=6379` in `.env.example`; code defaults stay
  on the legacy port — env-overridable — for backward compat with older local containers).
- The MCP server loads a repo-root `.env` if present and **fails loud** when the
  URI is unset — it never silently connects to an empty embedded graph.
- `FalkorProjection.from_uri` accepts `docker://`, `redis://`, and `rediss://`.
  The `redis://`/`rediss://` schemes exist for the **hosted API** (Fly), which
  resolves `FALKORDB_CLOUD_URI` → `TORTOISE_DB_URI` at runtime (entrypoint.sh).
- Restart the MCP server after changing the URI (resolved once at startup).

**Self-hosted authenticated MCP (`serve --http`, #702):** local stdio is
dev-mode only (no auth tokens on stdio — setting `TORTOISE_API_KEY` disables
it). For an authenticated local MCP endpoint, the DURABLE path is the compose
daemon (docker-compose.yml — set `TORTOISE_API_KEY` there, connect to
`http://localhost:8000/mcp`). For a single-agent eval setup without Docker:

```bash
tortoise key create                    # bootstrap a local registry team + tt_ key
TORTOISE_DB_PATH=~/.tortoise/tortoise.db tortoise serve --http   # tenant auth, binds 127.0.0.1:8000
```

> ⚠️ **Embedded FalkorDBLite (TORTOISE_DB_PATH) is SINGLE-WRITER, EVAL ONLY**
> (#942): fine for ONE agent; concurrent writers lose data. The embedded+tenant
> combo above is a single-agent eval setup — NOT a supported team deployment.
> Teams/multi-agent/production use the compose sidecar or managed Cloud.

- Client config: `url http://127.0.0.1:8000/mcp`, header `Authorization: Bearer tt_<key>`.
- HTTP (tenant) mode uses a fresh `team_{id}` namespace — existing stdio data
  stays in the `tortoise` graph (no automatic migration).
- `--auth static` (single `TORTOISE_API_KEY`/`--api-key`) and `--auth none`
  (localhost eval, NO auth) are available; default bind 127.0.0.1.
- Static-auth first run: `export TORTOISE_SECRET_PEPPER=$(openssl rand -hex 32)`
  before `serve --http --auth static` — required when the static key comes
  from the `TORTOISE_API_KEY` env var: the auth import fails on startup in
  that case. Passing `--api-key` directly does not need it.
- Changing `TORTOISE_SECRET_PEPPER` invalidates all local keys (re-run
  `tortoise key create`).

**`--auth none` safety (fail-closed):** `serve --http --auth none` on a
non-loopback/wildcard `--bind` is **refused (exit non-zero)** unless
`--allow-insecure-no-auth` is passed — and that override is **UNSAFE**
(no authentication; trusted networks only). Loopback binds (default
`127.0.0.1`) with `--auth none` remain allowed. For a LAN-accessible server,
pass `--allowed-hosts HOST[,HOST...]` (e.g. `--bind 0.0.0.0 --allowed-hosts
myhost.lan`) so the host guard accepts the hostnames clients use.

Do **not** commit `.env` (gitignored) and do **not** put DB credentials in
`.mcp.json`.

## 4.6 Session Capture — LLM Provider Configuration (#1197)

`POST /v1/sessions` — the beta testers' most-critical feature — runs the M2
LLM extractor over the conversation and **fails closed with 503 when no LLM
provider key is configured**: the regex extraction loop was removed as a
product path (#822) and there is no fallback. No key = capture disabled =
silent 503s for every tester. This section is the ops contract for making
sure that never happens.

### Env keys (set on `tortoise-api`/Fly; GitHub Actions secrets are the source)

| Key | Provider | Default model | Notes |
|-----|----------|---------------|-------|
| `OPENROUTER_API_KEY` | OpenRouter (aggregator) | `deepseek/deepseek-chat` | First in priority; one key → many model families |
| `DEEPSEEK_API_KEY` | DeepSeek | `deepseek-chat` | Cheapest-tier default; matches the analyzer's historical default |
| `OPENAI_API_KEY` | OpenAI | `gpt-4o-mini` | |
| `GEMINI_API_KEY` | Google Gemini | `gemini-2.0-flash` | Also used by MCP tooling — its presence here does NOT alone prove session capture is enabled |
| `TORTOISE_SESSION_LLM_MODEL` | — | per-provider default | Override, format `<provider>:<model>`; the provider must match the key that is set |
| `TORTOISE_SESSION_LLM_MOCK` | — | unset | **TEST-ONLY** seam (`1` = offline MockModel). **NEVER set on Fly** — it COUNTS as *configured* for the 503 gate, so a deploy with it set passes every gate while captures silently write offline MockModel points (see Verification procedure step 1) |

Provider priority when MULTIPLE keys are set (first configured wins):
`openrouter → deepseek → openai → gemini` (`sdk._SESSION_LLM_PROVIDER_PRIORITY`).
A key is only ever sent to the provider that issued it (#329).
`ANTHROPIC_API_KEY` is deliberately NOT consumed (#722) — its presence must
never be assumed to enable capture. `tortoise doctor` reports the resolved
provider/model and fails in hosted mode when the key is missing.

### Provider choice guidance

- **Recommended default:** `DEEPSEEK_API_KEY` + default `deepseek-chat` —
  cheapest viable tier, zero extra config.
- **Aggregation / future model swaps:** `OPENROUTER_API_KEY` — one key covers
  many model families (`openrouter:deepseek/deepseek-chat`, …) with per-route
  cost control.
- The key must exist on BOTH GitHub Actions secrets (deploy source —
  `deploy-hosted.yml` sets Fly secrets from GH secrets) and the running app
  (`fly secrets list -a tortoise-api`). A GH-secret miss silently ships a
  503-on-every-capture deploy; the deploy workflow now fails the job when no
  provider key is present.

### Cost bounds per capture

Bounds are enforced IN ORDER by `capture_session` (tortoise/hosted_api.py):

1. **Provider gate** — no key → `503` (fail-closed).
2. **Turn cap** — `MAX_SESSION_TURNS = 500` → `400` above it.
3. **Points quota (pre-write estimate)** — `402` when the extraction-aware
   estimate exceeds the team's points quota. Estimate:
   `est = 2 × Σ_turns min(sentences, MAX_EXTRACTIONS_PER_TURN=200)`
   (the ×2 covers the M2 relations stage's IMPL/NAND operator nodes; sentence
   count is capped per turn — the #329 flood gate).
4. **Sessions quota** — `DEFAULT_MAX_SESSIONS = 1000` (`_check_team_limit`).

Free-tier interplay (product/pricing.json): `max_graph_nodes: 10000` is the
points-quota numerator for NON-episodic Points only (turn Points / Session /
Event are episodic and don't count), and `included_write_ops_per_month: 10000`
is the write-ops budget. Worst-case node amplification per turn: 200
sentences × 2 = 400 nodes, so a full 500-turn session is ~200K estimated
nodes — always stopped by the 402 gate BEFORE any write. In practice the
cheap-tier models extract far fewer points than the cap; the estimate is the
fail-closed upper bound.

**Dollar cost:** depends on the provider's then-current pricing and the
transcript length (5,000-char truncation per turn in `_session_llm_transcript`).
All four default models are cheap-tier (`deepseek-chat`, `deepseek/deepseek-chat`,
`gpt-4o-mini`, `gemini-2.0-flash`). At free-tier volumes (10K write ops/month)
per-capture cost is fractions of a cent — the quota gates above are the hard
stop, not spend; monitor spend via the provider dashboard.

### Verification procedure

```bash
# 1. Provider key present on the running app AND the MOCK test seam ABSENT.
#    MOCK=1 counts as 'configured' for the 503 gate — a deploy with it set
#    passes the gate but every capture writes offline MockModel points. The
#    deploy workflow's verify-secrets step cannot check this (MOCK lives on
#    Fly's env, not GitHub secrets) — it is an operator checklist item:
fly secrets list -a tortoise-y4mjjq | grep -E "OPENROUTER|DEEPSEEK|OPENAI|GEMINI"
if fly secrets list -a tortoise-y4mjjq | grep -q TORTOISE_SESSION_LLM_MOCK; then
  echo "FAIL: TORTOISE_SESSION_LLM_MOCK is set on Fly — remove it (TEST-ONLY seam, never prod)"; exit 1
fi

# 2. Doctor reports the resolved provider/model (hosted mode FAILS on
#    provider-missing — rc 1):
fly ssh console -a tortoise-y4mjjq -C "python -m tortoise doctor"

# 3. Live capture smoke (needs FalkorDB up + a real team JWT):
curl -s https://api.premiselabs.co/health/ready    # {"status":"ok","db":"connected"}
# POST /v1/sessions with a team token → expect 200 + "extraction_mode":"llm".
# A 503 with detail containing "LLM provider key" = provider missing.

# 4. Local hermetic E2E (offline — MockModel seam, exercises the full path):
RUN_HOSTED_E2E=1 python -m pytest tests/e2e/hosted/ -q -rs
```

**Credentials needed for the LIVE smoke** (not available to repo automation):
Fly org access (`flyctl auth`) for `fly secrets list` / `fly ssh console`, and
a Supabase team JWT for the authenticated capture call. As of 2026-08-14 the
live `api.premiselabs.co` reports `{"status":"ok"}` on `/health` but
`/health/ready` returned `Database unreachable` — verify FalkorDB connectivity
before relying on a capture smoke (#1197).

**Deploy checklist (operator, before/after each deploy-hosted run):**

- [ ] ≥1 LLM provider key in GitHub secrets (deploy gate hard-fails otherwise)
- [ ] `TORTOISE_SESSION_LLM_MOCK` is NOT set on Fly (`fly secrets list -a tortoise-y4mjjq | grep TORTOISE_SESSION_LLM_MOCK` → empty). MOCK=1 is a TEST-ONLY seam that *counts as configured* for the 503 gate — a deploy with it set passes every gate while captures write offline MockModel points. NEVER set it on Fly.

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
| TORTOISE_BACKUP_KEY | ✅ (base64 32-byte AES-256-GCM key) | — | ✅ |
| R2_ACCOUNT_ID | ✅ | — | ✅ |
| R2_ACCESS_KEY_ID | ✅ | — | ✅ |
| R2_SECRET_ACCESS_KEY | ✅ | — | ✅ |
| R2_BUCKET | ✅ (`tortoise-backups`) | — | ✅ |
| LLM provider key — `OPENROUTER_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` (≥1 REQUIRED for `POST /v1/sessions`; #822/#1197) | ✅ | — | ✅ (deploy source; deploy workflow fails without ≥1) |

### Runtime Config (non-secret)

| Var | Default | Effect |
|-----|---------|--------|
| `TORTOISE_SESSION_LLM_MODEL` | per-provider default | `/v1/sessions` extraction model (LLM-default, #822 — the `TORTOISE_SESSION_EXTRACTION` mode knob and regex path were removed). Format `provider:model` (e.g. `deepseek:deepseek-chat`, `openrouter:deepseek/deepseek-chat`, `openai:gpt-4o-mini`); the provider must match the key that is set. Defaults: `deepseek-chat`, `deepseek/deepseek-chat`, `gpt-4o-mini`, `gemini-2.0-flash`. Capture **fails closed (503)** when no provider key (`OPENROUTER/DEEPSEEK/OPENAI/GEMINI_API_KEY`) is set — deploy a provider key before enabling captures. Provider priority when multiple are set: openrouter → deepseek → openai → gemini. See §4.6 for cost bounds + verification. |
| `TORTOISE_SESSION_LLM_MOCK` | unset | TEST-ONLY seam: `1` swaps in the offline MockModel extractor (no network). NEVER set on Fly — hosted captures would write MockModel points. `tortoise doctor` fails in hosted mode when this is set. |

## Reproducibility Test
Can a fresh Fly.io account + Cloudflare account follow §1 from zero and arrive at the same infra?
- [ ] FalkorDB Cloud instance provisioned, FALKORDB_CLOUD_URI secret set
- [ ] `fly apps create tortoise-y4mjjq` → deploys, health check passes, connects to FalkorDB Cloud
- [ ] `api.premiselabs.co` → resolves, TLS valid, /health returns ok (db: connected)
- [ ] `app.premiselabs.co` → resolves, serves dashboard placeholder
- [ ] GitHub push to main → auto-deploys tortoise-api
- [ ] ≥1 LLM provider key in GitHub secrets → deployed to Fly (`fly secrets list -a tortoise-y4mjjq`) → `tortoise doctor` reports `Session extraction ✅` on the app
- [ ] Live `POST /v1/sessions` smoke returns 200 + `extraction_mode: "llm"` (not a 503)
