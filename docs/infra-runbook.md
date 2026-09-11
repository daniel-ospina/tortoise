---
title: Tortoise Hosted Platform Infrastructure Runbook
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-infra
aboutObjects: fly-io, falkordb, cloudflare
created: 2026-08-03
updated: 2026-09-11
---

# Tortoise Hosted Platform — Infrastructure Runbook

**Epic:** #7711
**Last updated:** 2026-09-11

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

## 6. Availability & Machine Topology — routing checks, autostop policy, and the single-volume ceiling (#2850)

**App:** `tortoise-y4mjjq` (region `iad`). **Status:** 2026-09-11 — the routing
check was changed from HTTP to TCP, a separate non-routing `[checks]` liveness
entry was added, and the machine lifecycle policy was made explicit (§6.2,
§6.4). Genuine 2-machine redundancy is still **not** achievable as a config-only
change — §6.3 says exactly what blocks it and what would have to change. The
redundancy work remains an operator decision; it was not attempted here.

### 6.1 What happened (2026-09-10, ~19:10–20:35 UTC)

- Public API unreachable ~35 min. Fly's proxy logged `[PR01] no known healthy
  instances found for route tcp/443` continuously for `/health`, `/v1/teams`,
  `/v1/sessions`, `/mcp/`.
- `flyctl machines list` showed exactly **one** machine, state `started`,
  `CHECKS 0/1`.
- From **inside** that machine: port 8000 listening, `/health` answered 200
  `{"status":"ok","db":{"ok":true,"latency_ms":322.8}}`, loadavg 0.05, 3 GB
  of 4 GB free. **The app was healthy the entire time; only the
  health-check → routing path failed.** The original "the app wedges / the event
  loop is blocked" diagnosis is wrong and superseded by this evidence.
- Check flapped: `✗ 19:13:26 → ✓ 19:20:41 → ✗ 19:21:11 → ✓ 19:24:26 → ✗ 19:25:43`;
  it recovered on its own ~20:35 with no intervention.
- Mechanism: `/health` performed a FalkorDB round trip. Steady state was
  250–330 ms, but real socket timeouts occurred; when a stall outlasted the
  check's window Fly marked the **only** machine unhealthy and removed the sole
  route. The browser saw a CORS error because a failed response carries no
  `access-control-allow-origin` header.

The flap only became an outage because of three independent defects:

| # | Defect | Status |
|---|---|---|
| 1 | `path = "/health"` was coupled to a downstream — process liveness inherited every DB/probe stall | **mitigated in the app** — `tortoise/hosted_api.py:1470` now returns 200 unconditionally with `status` = `ok`/`degraded`; DB truth lives in `/health/ready` (`tortoise/hosted_api.py:1496`) |
| 2 | Routing was decided by an **HTTP** service check, so any application-level latency (probe latency, event-loop queueing) could de-register the only machine | **fixed in config** — `[[services.tcp_checks]]` is served by the kernel and can no longer be failed by a slow/starved app (§6.4); the application-level probe is now a non-routing check |
| 3 | One machine + an implicit, undeclared lifecycle policy | policy now explicit (§6.2); machine redundancy **blocked** (§6.3) |

### 6.2 Machine lifecycle policy — now declared in `fly.toml`

`fly.toml` previously declared **none** of `auto_stop_machines`,
`auto_start_machines`, `min_machines_running`, so production ran on platform
defaults that appeared nowhere in the repo. Fly documents the defaults and the
semantics in the `[[services]]` section of the [config reference](https://fly.io/docs/reference/configuration/#the-services-sections):

| Key | Default if absent | Set now | Deliberate choice |
|---|---|---|---|
| `auto_stop_machines` | `"off"` | `"off"` | **Never stop.** A stopped sole machine means zero healthy instances (the #2850 signature) and the next request waits out an ~85 s cold boot. This restates the effective default — no behavior change, no cost change. |
| `auto_start_machines` | `true` | `true` | Fly's explicit "run continuously" recipe is `off` + `false`. We deliberately keep `true`: it turns autostart back into a self-heal path, so a machine stopped by any other means (operator stop, crash-loop give-up) is restarted by the next request. Fly's mismatch warning targets the reverse pairing (stop + never-start), which strands an app with stopped machines. |
| `min_machines_running` | `0` | `1` | Availability floor: ≥1 machine warm in the primary region. Fly documents this as **inert unless `auto_stop_machines` is `"stop"`/`"suspend"`**, so today it is declarative intent; it becomes load-bearing the moment autostop is enabled or a second machine exists. |

**Why `auto_stop_machines` matters here specifically:** a failing service check
only *de-registers* a machine from routing — it never stops it. But once the
check has taken the sole machine out of routing, public load is zero by
definition, and Fly Proxy's autostop loop is what would then decide whether to
stop (or suspend) it for idleness. Fly's default when the key is absent is
`"off"` (never stop). Setting it explicitly to `"off"` guarantees that a
de-registration cannot cascade into a cold stop — otherwise a routing glitch
would become an ~85 s cold start on the next request. Availability/cost
tradeoff: keeping ≥1 machine warm is ~$21.40/mo, which is already paid today
because the app serves continuously; the counterfactual is an unpredictable
cold-start outage for ~$21/mo of savings, which is not worth it for a P0 API.

Two Fly behaviors operators expect but do **not** get (both from the
[health-checks reference](https://fly.io/docs/reference/health-checks/)):

- A failing service check **never restarts or stops** the machine — it only
  removes it from routing. Recovery from a wedge is manual (`fly machine restart`,
  as used at 19:32 in #2850).
- Autostop/autostart **never creates or destroys** machines — it only
  starts/stops machines that already exist. So `min_machines_running` can never
  *manufacture* redundancy (see §6.3).

Do not raise `min_machines_running` to `2` without doing §6.3 first. With one
machine it is a silent no-op (no error, no second machine, no additional
resilience) — the dangerous kind of config change.

### 6.3 Can we run ≥2 machines? Not with this volume, and not with this app yet

**Plain answer: `min_machines_running = 2` is not achievable today.** It would
neither fail to boot nor help — it would be a no-op. Redundancy needs three
transitions, and only the first is Fly configuration:

**(a) The volume is single-attach.** Fly volumes are one-to-one with machines:
"You need to run as many volumes as there are Machines… A Machine can only mount
one volume at a time and a volume can be attached to only one Machine"
([Volumes overview](https://fly.io/docs/volumes/overview/)). `fly.toml` has
`[[mounts]] source = "tortoise_api_data" → /data`, so a second machine **cannot**
mount that volume. A hand-rolled second machine pointing at the same source
fails at the platform level, not silently. `fly scale count 2` is the supported
path: flyctl creates a **new** volume per new machine when no unattached volume
exists — i.e. *volume-per-machine*, not a shared volume. (Also note: with a
volume attached, only the `rolling` deploy strategy is available — `canary` and
`bluegreen` are refused for volume-mounted apps, which is why `fly.toml`
`[deploy] strategy = "rolling"` is not a free choice.)

**(b) The app is not horizontally scale-ready.** It keeps request- and
background-critical state **in process memory**, so a second machine would
serve *divergent* answers, not more capacity:

| State | Location | What breaks with 2 machines |
|---|---|---|
| Index-job registry + ownership (`_INDEX_JOBS`, `_INDEX_JOB_OWNERS`) | `tortoise/hosted_api.py:18101`, `:18167` | `GET /v1/index/{github,docs}/{job_id}` polled on the other instance → 404; `POST /v1/index/*` in-flight dedup (`is_new`) stops working |
| Dream queues/tasks (`_DREAM_QUEUES`, `_DREAM_TASKS`) | `tortoise/hosted_api.py:747`, `:750` | background work duplicated or stranded per instance |
| Provisioning / team-create / invite locks (`_PROVISION_LOCKS`, `_TEAM_CREATE_LOCKS`, `_INVITE_TEAM_LOCKS`) | `:9296`, `:9017`, `:10854` | `asyncio.Lock` does not exclude across machines — mutual exclusion is lost (double-provision risk) |
| Per-IP / per-key rate-limit buckets (`_SENSITIVE_BUCKETS`, `_SIGNUP_BUCKETS`, `_SESSION_BUCKETS`, `_CLAIM_BUCKETS`, `_RECOVER_*`, `_INVITE_ACCEPT_*`) | `:2914`, `:2920`, `:3259`, `:3256`, `:3195`, `:3305-3309` | effective limits multiply by the machine count; anti-abuse budgets become per-instance |

**(c) `/data` state would fork.** A per-machine volume means a per-machine
`/data/ingest` corpus: `/v1/index/docs` re-runs on the other machine re-fetch
the corpus (the hash-dedup cache is volume-local) and any state under `/data`
stops being a single source of truth. Graph data itself is fine — it lives in
FalkorDB Cloud, selected by `TORTOISE_DB_URI` — but the embedded fallback
(`TORTOISE_DB_PATH`, default `/data/tortoise.db`, `hosted_api.py:188`) is
per-machine, so a deploy missing `FALKORDB_CLOUD_URI` would silently split state
across two SQLite files instead of failing closed.

**What would have to change, in order, if we want ≥2 machines:**

1. Move the in-process state in (b) to a shared store (Supabase/Redis) or make
   it explicitly instance-local (e.g. make index-job polling instance-affine).
   This is app-layer work, tracked separately from this runbook.
2. Decide `/data`'s role: per-machine volume (accept the fork; ingest staging
   is reconstructible), shared object storage (R2, already provisioned for
   backups), or drop the mount. Do not share one volume — it is not possible.
3. Provision the second volume + machine: `fly volumes create tortoise_api_data_2
   --region iad --size <same-GB-as-existing>` then `fly scale count 2`, or let
   `fly scale count 2` create the volume. **Use `fly scale count` / `fly machine
   clone`** — a machine created with `fly machine run` has an empty process
   group and will be flagged ORPHAN by the #1896 pre-deploy guard
   (`.github/scripts/check-fly-machines-guard.py`), blocking deploys.
4. Only then give `min_machines_running` a real value: `2` with
   `auto_stop_machines = "stop"` keeps both warm; `1` keeps one warm and lets
   the proxy stop the other when idle. With `auto_stop_machines = "off"` every
   machine always runs and `min_machines_running` stays declarative.
5. Re-verify `fly checks list` shows every machine healthy **before** trusting
   the second route, and confirm the deploy gate still passes
   (`TORTOISE_DB_URI` is derived at boot by `entrypoint.sh`; a machine whose
   volume failed to attach must not be mistaken for a healthy one).

**Cost of the decision (§6.5 for the derivation):** a second
`shared-cpu-2x:4096MB` machine adds **+$21.40/mo** in `iad`, plus its volume at
$0.15/GB/mo — roughly **$42.80/mo total compute** for the two-machine fleet,
double today's compute run-rate. That is material: it is the operator's call,
not something to enable quietly. What was done instead costs **$0** and removes
the dominant failure mode (§6.2 + the app-side `/health` fix + the TCP routing
check in §6.4).

### 6.4 Health checks — TCP routing check + a separate non-routing liveness check

`fly.toml` now carries **two independent checks** with different jobs. Neither
is an HTTP probe of the application on the routing path.

**Routing check — `[[services.tcp_checks]]`** (rides the service's
`internal_port = 8000`; `interval = "15s"`, `timeout = "5s"`,
`grace_period = "180s"`). This replaced `[[services.http_checks]]` +
`path = "/health"`.

- A TCP check's connect is served by **the kernel** on the listening socket.
  The event loop and any thread pool cannot starve it, so a slow or blocked
  application **cannot** fail the check. Fly's documented semantics for a
  failing *service* check are that the proxy stops routing to the Machine and
  the Machine is not restarted or stopped — so with an HTTP check, application
  latency could **de-register the only machine** (the #2850 outage). With TCP it
  can only make a response slow, never remove the route.
- `[[services.*_checks]]` has **no `port` key** — a service check always rides
  the service's `internal_port`, so it cannot be pointed at 9090.
- `timeout = "5s"` against a 15 s interval (was 15 s/15 s — a 1:1 ratio with
  zero headroom, so a hung probe consumed its whole period). A kernel accept is
  effectively instant (Fly's `tcp_checks` default timeout is 2 s); 5 s is
  generous headroom, not a latency budget.
- `grace_period = "180s"` is retained (boot is ~85 s — torch + model load,
  #545 — and the listening socket must exist first). Whether Fly honors the full
  180 s is unresolved; see §6.4.1. `[deploy] wait_timeout = "5m"` (CI passes
  420 s) still exceeds boot + `grace_period` under either reading of §6.4.1.

**Non-routing check — top-level `[checks.loop_liveness]`** (`type = "http"`,
`port = 9090`, `path = "/healthz"`, `interval = "15s"`, `timeout = "5s"`,
`grace_period = "180s"`).

- **Top-level checks do not affect request routing.** Fly's config reference
  scopes them to "independent health checks that don't affect request routing";
  the proxy ignores them when choosing where to send traffic. The 9090/`/healthz`
  signal is therefore purely an alerting/visibility channel — it exists so a
  **stalled event loop** shows up to an operator, without that same signal being
  able to take the app offline.
- Top-level checks **require** `port`, and Fly requires that port to be bound on
  **`0.0.0.0`**. This depends on the application-side listener; the bind-address
  and auth questions are open and tracked in §6.9.
- The interface contract for the listener is: `GET /healthz` returns **200**
  when the event loop is progressing and **503** when it has stalled.

#### 6.4.1 The `grace_period` clamp — an open, testable question (not a fact)

The `hosted_api.health` docstring (around `tortoise/hosted_api.py:1474`) asserts
that "Fly caps the http_check grace period at 60s", attributed to the #338 fix.
Independent research could **not** confirm this: no such cap appears in Fly's
config reference, in `flyctl`, or in `fly-go`, and `flyd` is closed-source, so an
undocumented server-side clamp cannot be ruled out. The honest position is
**unconfirmed — possibly an undocumented server-side behaviour**. The section
above must not be read as assuming either outcome:

- **If no clamp exists** (what the documented field list implies): the effective
  `grace_period` is the configured `"180s"`, which clears the ~85 s boot plus
  the ~2 min FalkorDB DNS tail (#1381, surfaced by the deploy workflow's DB
  health gate).
- **If the clamp exists**: the effective grace period is **60 s**, which is
  *shorter than the ~85 s boot*. The machine would be marked unhealthy during
  every cold start, and the right response would be to write
  `grace_period = "60s"` (or re-tune the boot path) — not to raise it.

**How to test it** (staging app only, never production): set `grace_period`
above 60 s, boot a machine, and record the delay from machine start to the first
failed check in `fly checks list` / `fly logs`. A first failure at ≈60 s ⇒ the
clamp is real; ≈`grace_period` s ⇒ no clamp. `fly config show` only echoes the
local config and cannot answer this. Nobody has run this test yet — it is listed
in §6.9.

### 6.5 Cost reference (what "one machine warm" actually costs)

Fly list price, region `iad`, as published on the
[pricing page](https://fly.io/docs/about/pricing/) (retrieved 2026-09-10):

| Item | Rate | Monthly |
|---|---|---|
| `shared-cpu-2x` / 512 MB (preset base) | $0.00000150/s · $0.0054/hr | $3.89 |
| + 3.5 GB RAM above the 512 MB preset (4096 MB total) | $5/GB/30 days | +$17.50 |
| **`shared-cpu-2x` / 4096 MB (current `[[vm]]`), running** | $0.00000826/s · $0.0297/hr | **$21.40** |
| Stopped machine | rootfs only | $0.15 per GB/30 days |
| Volume storage (per volume, attached or not) | — | $0.15/GB/mo |
| **Two-machine fleet (both warm, 4 GB each)** | — | **$42.80** (+ volumes) |

The current volume's provisioned size is **not recorded in the repo** (verify
with `fly volumes list -a tortoise-y4mjjq`) — factor it in at $0.15/GB/mo per
volume, and again for the second volume if §6.3 is ever executed.

### 6.6 The cheapest available redundancy: alert on the route, not the app

The one machine cannot be made redundant yet, and a failing check does not
restart it, so the highest-value cheap mitigation is a **route-level alert**: an
external probe of `https://api.premiselabs.co/health` every 30–60 s (Fly
Machines API / Uptime Kuma / the existing Telegram alerting used by the backup
sweep) that fires when the public endpoint fails for >2 consecutive probes.

The deploy workflow only probes at deploy time (`.github/workflows/deploy-hosted.yml`
"Post-deploy DB health gate"), so during #2850 nothing alerted for ~35 min while
the machine was locally healthy and serving. `fly checks list` and the presence
of `[PR01] no known healthy instances` in the proxy logs are the two signals
that would have caught it immediately.

### 6.7 Operator commands (read-only unless noted)

```bash
# Is the route healthy? (CHECKS column, per machine)
fly checks list -a tortoise-y4mjjq
fly machines list -a tortoise-y4mjjq

# What is the app's effective config vs fly.toml?
fly config show -a tortoise-y4mjjq | jq '.services[0] | {auto_stop_machines, auto_start_machines, min_machines_running}'

# How many volumes exist (must equal the machine count)?
fly volumes list -a tortoise-y4mjjq

# Is the app itself healthy (bypasses the proxy)?
fly ssh console -a tortoise-y4mjjq -C "python3 -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=15).read())\""

# Last resort for a wedged machine (mutating — operator only):
fly machine restart <id> -a tortoise-y4mjjq
```

### 6.8 Residual risks / not verified in this change

- **Single machine remains a single point of failure** (host failure, deploy
  replacement, wedge). The explicit policy + TCP routing check remove the
  *avoidable* outage (a slow app de-registering the only machine), not the
  *structural* one.
- **Rolling deploys still replace the only machine** — there is a boot-length
  window with no healthy instance. `canary`/`bluegreen` cannot fix this while a
  volume is attached; only §6.3 can.
- **`/health` still spawns a DB probe per call** (`asyncio.to_thread`; the probe
  is bounded at 1.5 s and abandons its worker thread on timeout,
  `tortoise/monitoring.py:111`). Under a black-holed DB, threads can accumulate
  slowly. This no longer affects routing (the routing check is TCP), but it still
  affects the human/operator view and any external probe that hits `/health`.
  App-layer, tracked outside this runbook; a `wait_for` wrapper would bound the
  request even if the probe regresses.
- **Nobody has replayed #2850 in staging.** The fix rests on the in-machine
  evidence and the code path, not on a reproduced failure (the issue's
  indicator list requires this).
- The `backup watcher` `UnboundLocalError` seen at boot during the incident is
  real and tracked separately (#2851) — it is not a topology issue.

### 6.9 Open questions to test in staging (highest value first)

These are **unverified** — deliberately documented rather than assumed. They are
the tests worth running in a throwaway/staging app before trusting this topology
or re-introducing service-level checks.

1. **Does a failing check on a second `[[services]]` block de-register the
   machine for the *primary* service?** A machine can carry multiple
   `[[services]]` sections, each with its own checks. Fly documents that a
   failing service check removes the Machine from routing *for that service*,
   but it does **not** document whether de-registration is per-service or
   per-machine. If it is per-machine, adding a second service (or any future
   service-level check) could re-introduce the #2850 total outage through a side
   door. **This is the single highest-value staging test in this runbook.**
   Test: add a second `[[services]]` with a deliberately failing check, confirm
   traffic to the primary service continues, and confirm `fly checks list`
   reports the two checks independently. Until that is observed, treat
   "multiple service checks are independent" as an assumption.
2. **Is the 9090 listener actually bound on `0.0.0.0`?** `fly.toml` now requires
   9090 on `0.0.0.0` for the top-level check, but the existing standalone health
   server defaults to loopback: `serve_health(port=9090, bind="127.0.0.1")`
   (`tortoise/monitoring.py:253`), and the CLI default is also `127.0.0.1`
   (`tortoise/__main__.py:5895-5896`). A loopback-only listener makes the check
   fail permanently. Verify inside the machine:
   `fly ssh console -a tortoise-y4mjjq -C "ss -ltn | grep 9090"`.
3. **Is `/healthz` genuinely unauthenticated?** The existing 9090 handler
   (`monitoring._Handler`) is auth-gated in prod mode (#7395) and returns 401
   without a valid Bearer token. Top-level checks can only send a static header,
   not a rotating secret, so the contract requires `/healthz` to be
   unauthenticated (200/503). Verify with a token-less request from inside the
   machine.
4. **Does the top-level check block `fly deploy`?** Top-level checks are
   documented as not affecting routing, but it is not documented whether a
   failing top-level check counts against the rolling-deploy health wait (as
   service checks do). If it does, a mis-bound or auth-gated 9090 listener could
   deadlock deploys. Test alongside #2/#3 before relying on the check.
5. **The `grace_period` clamp** — §6.4.1.

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
| RESEND_API_KEY | ✅ (billing + transactional email, #310/#307) | — | ✅ |
| RESEND_FROM_EMAIL | ✅ (single managed sender identity — #1136; default `noreply@premiselabs.co`) | — | ✅ |
| BILLING_FROM_EMAIL | optional (distinct billing sender override — #1136) | — | — |
| BILLING_NOTIFY_TO | ✅ (ops inbox for billing/abuse emails) | — | ✅ |

### Runtime Config (non-secret)

| Var | Default | Effect |
|-----|---------|--------|
| `TORTOISE_SESSION_EXTRACTION` | `auto` | `/v1/sessions` extraction mode (`auto\|required\|regex`). `required` fails closed: **all** session captures return 503 when no LLM provider key (`OPENROUTER/DEEPSEEK/OPENAI/GEMINI_API_KEY`) is set — do not enable it until a provider key is deployed. Unknown values fall back to `auto`. |
| `RESEND_SEND_BUDGET_DAILY` | `100` | In-process hard cap on provider-accepted sends per UTC day (#1138 — Resend free tier 100/day). When reached, further invite sends are skipped with a loud warning instead of silently 429ing. Estimate only — resets on process restart. |
| `RESEND_SEND_BUDGET_MONTHLY` | `3000` | Same as above for the UTC month (free tier 3,000/month). |

## Reproducibility Test
Can a fresh Fly.io account + Cloudflare account follow §1 from zero and arrive at the same infra?
- [ ] FalkorDB Cloud instance provisioned, FALKORDB_CLOUD_URI secret set
- [ ] `fly apps create tortoise-y4mjjq` → deploys, health check passes, connects to FalkorDB Cloud
- [ ] `api.premiselabs.co` → resolves, TLS valid, /health returns ok (db: connected)
- [ ] `app.premiselabs.co` → resolves, serves dashboard placeholder
- [ ] GitHub push to main → auto-deploys tortoise-api
- [ ] ≥1 LLM provider key in GitHub secrets → deployed to Fly (`fly secrets list -a tortoise-y4mjjq`) → `tortoise doctor` reports `Session extraction ✅` on the app
- [ ] Live `POST /v1/sessions` smoke returns 200 + `extraction_mode: "llm"` (not a 503)
- [ ] `fly.toml` declares `auto_stop_machines` / `auto_start_machines` / `min_machines_running` explicitly (no implicit platform defaults) and `fly config show` matches (§6.2)
- [ ] Every machine has its own volume (`fly volumes list` count == `fly machines list` count) — a machine sharing `tortoise_api_data` is impossible and must never be attempted (§6.3)
- [ ] Routing check is `[[services.tcp_checks]]` (kernel-served, cannot be failed by a slow app) and no `[[services.http_checks]]` entry remains (§6.4)
- [ ] Top-level `[checks.loop_liveness]` targets port 9090 / path `/healthz`; the listener binds `0.0.0.0` and `/healthz` is unauthenticated 200/503 (§6.4, §6.9)
- [ ] A deliberately failing second `[[services]]` check does **not** de-register the primary service (§6.9 — open until observed)
