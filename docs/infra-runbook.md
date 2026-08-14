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

## 4.6 Pre-Beta Graph Integrity Check (#1200)

**When:** before each beta cohort starts. The gate proves the hosted prod
graphs are structurally sound (chain integrity + EP-risk audit) before the
first cohort touches the product. It is REPEATABLE — the same commands run
against every team graph.

**What the gate answers:**
1. Chain integrity — `tortoise_check_structure` / `tortoise_summarize_structure`
   (Gate 0→4 orphans, dangling refs, never-wired draft points).
2. EP-risk surfaces — orphan NANDs, batch-connected mitigations, NAND edges to
   dead points, unmitigated low-confidence operators.
3. Evidence hygiene — missing `sourceKind` (#1158), superseded points without
   `:SUPERSEDES` edges, keyword-suspicious IMPL edges.

**Credentials needed (owner session):**
- One **tenant `tt_` API key per hosted team graph** (dashboard welcome page or
  `tortoise key create` against the hosted API). The hosted MCP endpoint
  (`https://api.premiselabs.co/mcp/`) is **auth-gated** — `Authorization:
  Bearer tt_<key>` per request, resolved server-side to that team's namespace
  (`team_<id>`). Without a key the endpoint returns 401/empty (fail-closed).
- Host-side fallback: `fly ssh console -a tortoise-api` — the app resolves
  `FALKORDB_CLOUD_URI` → `TORTOISE_DB_URI` at runtime; run the SDK script from
  inside the console to target the managed FalkorDB directly.
- `FASTAPI_INTERNAL_KEY` only for `/internal/*` endpoints (not needed for the
  integrity check — it is not exposed on the public REST surface).

**Procedure A — hosted MCP (preferred, per team):**
1. Point an MCP client at `https://api.premiselabs.co/mcp/` with
   `Authorization: Bearer tt_<key>`.
2. `tortoise_summarize_structure` → per-gate counts (gate0_jtbds … gate4_requirements).
3. `tortoise_check_structure` → chain violations (orphan_use_case,
   dangling_use_case_ref, dangling_jtbd_ref, dangling_workflow_ref,
   orphaned_draft).
4. `tortoise_list_pointkinds` → confirm the expected kinds exist in the team graph.

**Procedure B — SDK script (repeatable, any target):**
> **Prerequisite:** `graph-scripts/audit_beta_gate.py` imports the `tortoise` SDK
> (`from tortoise.sdk import TortoiseSDK`). Install it once from the repo root
> (`pip install -e .`) — or, without installing, point `PYTHONPATH` at the repo
> root (the parent of `graph-scripts/`): `PYTHONPATH=/path/to/tortoise python3
> graph-scripts/audit_beta_gate.py`. Run from the repo root either way.

```bash
# Hosted/selfhost FalkorDB (from fly ssh console, or with a keyless URI)
TORTOISE_DB_URI='rediss://...' python3 graph-scripts/audit_beta_gate.py --namespace team_<id>

# Local embedded snapshot (never touch a live single-writer DB, #942)
cp ~/.tortoise/tortoise.db /tmp/gate-snapshot.db
PYTHONPATH=/path/to/tortoise TORTOISE_DB_PATH=/tmp/gate-snapshot.db python3 graph-scripts/audit_beta_gate.py

# Machine-readable (CI gate): exit 0 = PASS, 1 = P1 findings, 2 = error
PYTHONPATH=/path/to/tortoise TORTOISE_DB_PATH=/tmp/gate-snapshot.db python3 graph-scripts/audit_beta_gate.py --json
```
`graph-scripts/audit_beta_gate.py` runs the full surface: baseline counts,
`summarize_structure`, `check_structure`, `tortoise/audit.py` `audit_graph`,
and the five beta-gate risk queries below.

**What to look for (P1 = block the cohort, fix or file with owner):**
1. **Orphan NANDs** — NAND operator points with ZERO edges (created, never
   wired; EP can never apply them). Query: `MATCH (n:Point
   {is_operator:true, op_type:'NAND'}) WHERE NOT (n)--() RETURN n.id`.
2. **Batch-connected mitigations nuking EP weights** — ONE mitigation point
   targeting >1 operator. Skill rule (how-to-use-tortoise): connect
   mitigations ONE at a time, verify each — a batch mitigation cascade-nukes
   downstream confidence. NOTE the edge shape: legacy writes used
   `:mitigates` (m→op); the current SDK (`mitigate_operator`) writes
   `(m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)` — the gate script matches
   BOTH shapes; a raw-Cypher batch connect on either label fails the gate.
3. **NAND edges to dead points** — targets whose `status` is retracted or
   superseded (dangling contradiction; `deleted` is not a valid point
   status — delete_point tombstones to `retracted`).
4. **Chain violations** — orphaned drafts (draft points never wired,
   `check_structure` `orphaned_draft`), useCase without JTBD parent,
   dangling use_case/jtbd/workflow refs.
5. **Caveat — `impl_instead_of_nand` is keyword-based** (content contains
   "not"/"no "/"fail"…). Verify semantics before acting; ingested README/doc
   text produces false positives.

**P2 debt — file, don't block:**
- **missing_sourceKind (#1158)** — evidence points without a source tier
  (`audit_graph` reports as **medium**; tier the source, do not hand-set
  point-level sourceKind — #398 source-level inheritance).
- missing sourceDate on graded evidence; superseded points without a
  `:SUPERSEDES` edge (audit `superseded_no_edge` is high — fix before
  cohort if present; `superseded_active_edges` is medium).

**Threshold:** zero unresolved P1 at cohort start (risk surfaces 1–3 must be
empty; chain/audit P1s fixed or filed with owners). `impl_instead_of_nand` is
keyword-based and excluded from the gate script's P1 hard-fail set — the script
reports it as `audit_advisory` (debt, not a cohort blocker). P2s are debt — file,
don't block. `audit_beta_gate.py` exits 0 = PASS, 1 = P1 findings, 2 =
infrastructure error (DB unreachable, busy embedded store) — CI consumers
should treat 1 and 2 distinctly.

**Related:** #1149 tenant-provision incidents (multi-tenant hygiene), #1158
`audit.py` sourceKind gap, #942 embedded single-writer rule.

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

### Runtime Config (non-secret)

| Var | Default | Effect |
|-----|---------|--------|
| `TORTOISE_SESSION_EXTRACTION` | `auto` | `/v1/sessions` extraction mode (`auto\|required\|regex`). `required` fails closed: **all** session captures return 503 when no LLM provider key (`OPENROUTER/DEEPSEEK/OPENAI/GEMINI_API_KEY`) is set — do not enable it until a provider key is deployed. Unknown values fall back to `auto`. |

## Reproducibility Test
Can a fresh Fly.io account + Cloudflare account follow §1 from zero and arrive at the same infra?
- [ ] FalkorDB Cloud instance provisioned, FALKORDB_CLOUD_URI secret set
- [ ] `fly apps create tortoise-y4mjjq` → deploys, health check passes, connects to FalkorDB Cloud
- [ ] `api.premiselabs.co` → resolves, TLS valid, /health returns ok (db: connected)
- [ ] `app.premiselabs.co` → resolves, serves dashboard placeholder
- [ ] GitHub push to main → auto-deploys tortoise-api
