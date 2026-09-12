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

### 6.0 ⛔ Merge order — `#3063` must NOT merge before `#3062`

**The top-level `[checks.loop_liveness]` entry is deploy-gating, and the listener
it points at ships in #3062 (`fix/2850-health-liveness-decouple`), which is
*not* yet merged.**

- **The gate.** `flyctl`'s deploy health wait (`WaitForHealthchecksToPass`,
  `internal/machine/leasable_machine.go`, called from
  `machines_deploymachinesapp.go`) counts **`len(cfg.Checks)`** — the top-level
  `[checks]` table — *plus* service checks, and then requires **every reported
  check** to be passing. There is no informational/readiness filter in the deploy
  path and `kind` is not consulted. "Top-level checks do not affect routing" is
  true of the **proxy** and false of the **deploy gate**; they are separate
  mechanisms.
- **The consequence of merging #3063 alone.** `.github/workflows/deploy-hosted.yml`
  triggers on any push touching `fly.toml`, so production auto-deploys an image
  whose app does **not** listen on `0.0.0.0:9090` (that listener is added by
  #3062). The deploy then fails after `--wait-timeout 420`, and the workflow
  retries **5×** with a 45 s sleep — **~35 minutes of failing deploys**, with
  **each retry rolling/replacing the sole production machine** (~85 s cold-boot
  outage each time). At minimum, the check stays permanently red in
  `fly checks list`, which also defeats the route-level alerting story (§6.6).
- **Required order (either is acceptable).** (1) **#3062 merges first** — it
  ships `monitoring.start_health_listener` (`_HealthzHandler`, bound to `0.0.0.0`
  via `HEALTHZ_BIND` / `TORTOISE_HEALTHZ_BIND`, serving unauthenticated
  `/healthz`) — *then* this PR lands the check that consumes it; **or** (2) both
  PRs land in the **same batch** (merged and deployed together), so the image and
  the check are never out of sync.
- **Pre-merge verification — do this before the first deploy that introduces the
  check.** Confirm the deployed image actually listens on `0.0.0.0:9090`:

  ```bash
  # Expect 0.0.0.0:9090 (NOT 127.0.0.1:9090):
  fly ssh console -a tortoise-y4mjjq -C "ss -ltn | grep ':9090'"
  # Expect 200 from a token-less request (contract: 200 progressing / 503 stalled):
  fly ssh console -a tortoise-y4mjjq -C "python3 -c \"import urllib.request as u;print(u.urlopen('http://127.0.0.1:9090/healthz',timeout=5).status)\""
  ```

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
| 1 | `path = "/health"` was coupled to a downstream — process liveness inherited every DB/probe stall | **mitigated in the app** — `hosted_api.health` (`@app.get("/health")`) returns 200 unconditionally with `status` = `ok`/`degraded`; DB truth lives in `/health/ready` (`hosted_api.health_ready`) |
| 2 | Routing was decided by an **HTTP** service check, so any application-level latency (probe latency, event-loop queueing) could de-register the only machine | **fixed in config** — `[[services.tcp_checks]]` is kernel-served, so it is not starved by event-loop/thread-pool scheduling and a slow/starved app no longer de-registers the machine (§6.4); the application-level probe is now a non-routing check |
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

| State | Symbol(s) (module scope, `tortoise/hosted_api.py`) | What breaks with 2 machines |
|---|---|---|
| Index-job registry + ownership | `_INDEX_JOBS`, `_INDEX_JOB_OWNERS` | `GET /v1/index/{github,docs}/{job_id}` polled on the other instance → 404; `POST /v1/index/*` in-flight dedup (`is_new`) stops working |
| Dream queues/tasks | `_DREAM_QUEUES`, `_DREAM_TASKS` | background work duplicated or stranded per instance |
| Provisioning / team-create / invite locks | `_PROVISION_LOCKS`, `_TEAM_CREATE_LOCKS`, `_INVITE_TEAM_LOCKS` | `asyncio.Lock` does not exclude across machines — mutual exclusion is lost (double-provision risk) |
| Per-IP / per-key rate-limit buckets | `_SENSITIVE_BUCKETS`, `_SIGNUP_BUCKETS`, `_SESSION_BUCKETS`, `_CLAIM_BUCKETS`, `_RECOVER_*`, `_INVITE_ACCEPT_*` | effective limits multiply by the machine count; anti-abuse budgets become per-instance |

**(c) `/data` state would fork.** A per-machine volume means a per-machine
`/data/ingest` corpus: `/v1/index/docs` re-runs on the other machine re-fetch
the corpus (the hash-dedup cache is volume-local) and any state under `/data`
stops being a single source of truth. Graph data itself is fine — it lives in
FalkorDB Cloud, selected by `TORTOISE_DB_URI` — but the embedded fallback
(`TORTOISE_DB_PATH`, default `/data/tortoise.db`; resolved by
`hosted_api._resolve_embedded_db_path`) is per-machine, so a deploy missing
`FALKORDB_CLOUD_URI` would silently split state across two SQLite files instead
of failing closed.

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

- A TCP check's connect is served by **the kernel** on the listening socket, so
  the check **is not starved by event-loop or thread-pool scheduling**: the
  kernel completes the handshake even while the application is slow. That is a
  much wider safety margin than an HTTP check's, but it is **not** "cannot
  fail". A connect still has to be *accepted*, and a kernel check passes only
  while the listening socket's **accept backlog** has room — an application that
  never returns to `accept()` for long enough will eventually have new
  connections dropped/refused and the check will fail. The change sheds a large
  class of failure modes (application-level latency, DB stalls, executor
  saturation), not all of them.
- **Deliberate tradeoff: TCP proves *reachable*, not *ready*.** A wedged-but-
  listening process — accepting connections, doing no useful work — passes this
  check and therefore stays routable. That is the accepted price of never
  letting application latency remove the only route; the readiness signal
  deliberately lives elsewhere (`/health/ready`, and the 9090 liveness check
  below).
- Fly's documented semantics for a failing *service* check are that the proxy
  stops routing to the Machine and the Machine is not restarted or stopped — so
  with an HTTP check, application latency could **de-register the only machine**
  (the #2850 outage). With TCP the same latency can only make a response slow,
  and cannot by itself remove the route (the accept-backlog case above is the
  remaining, far narrower, path to a failed check).
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

- **Top-level checks do not affect request routing — but they DO gate
  `fly deploy`.** Fly's config reference scopes them to "independent health
  checks that don't affect request routing"; the proxy ignores them when
  choosing where to send traffic, so the 9090/`/healthz` signal can never take
  the app offline. That is *not* the whole story: `flyctl`'s deploy health wait
  counts top-level checks too (see the "Determined" block in §6.9 and the
  merge-order box in §6.0), so a failing or mis-bound `loop_liveness` check
  **blocks a deploy** even though it cannot de-register the machine. The check is
  therefore **routing-inert but deploy-gating**, and that is a **known, accepted
  cost** — it is deliberately loud where it matters.
- Top-level checks **require** `port`, and Fly requires that port to be bound on
  **`0.0.0.0`**. The application-side contract is
  `monitoring.start_health_listener` (added by **#3062**): it binds `0.0.0.0`
  (`HEALTHZ_BIND`, overridable via `TORTOISE_HEALTHZ_BIND`) and serves `/healthz`
  **unauthenticated by construction**; the only residual is deployed-image
  verification (§6.9 #2).
- The interface contract for the listener is: `GET /healthz` returns **200**
  when the event loop is progressing and **503** when it has stalled.

#### 6.4.1 The `grace_period` clamp — an open, testable question (not a fact)

The `hosted_api.health` docstring (`tortoise/hosted_api.py`, `@app.get("/health")`)
asserts that "Fly caps the http_check grace period at 60s", attributed to the
#338 fix.
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

> ⚠️ **APPROXIMATE and DATED (snapshot 2026-09-10) — not current list price.**
> These figures are kept so the *shape* of the cost argument (one machine ≈ half
> a two-machine fleet; RAM above the 512 MB preset dominates) stays reviewable.
> Fly's live prices differ by ~8 % on these line items (re-checked 2026-09-12:
> the 512 MB preset is ≈`$0.00000156/s · $0.0056/hr · $4.04/mo`, and the 4096 MB
> machine ≈`$0.00000857/s · $0.0309/hr · $22.22/mo`), so every monthly total here
> — including the two-machine figure and the ~$21.40 references in §6.2/§6.3 —
> is **low**. The live
> [pricing page](https://fly.io/docs/about/pricing/) **is the source of truth;
> re-fetch it before making a spend decision.**> *Controller follow-up: re-fetch and re-derive this table (and the `fly.toml`
> COST comment, which carries the same snapshot) — flagged, not silently
> edited.*

Fly list price at the snapshot date, region `iad`, as published on the
[pricing page](https://fly.io/docs/about/pricing/) (retrieved 2026-09-10):

| Item | Rate (approx., 2026-09-10) | Monthly (approx.) |
|---|---|---|
| `shared-cpu-2x` / 512 MB (preset base) | ~$0.00000150/s · ~$0.0054/hr | ~$3.89 |
| + 3.5 GB RAM above the 512 MB preset (4096 MB total) | $5/GB/30 days (rate unchanged) | +$17.50 |
| **`shared-cpu-2x` / 4096 MB (current `[[vm]]`), running** | ~$0.00000826/s · ~$0.0297/hr | **~$21.40** |
| Stopped machine | rootfs only | $0.15 per GB/30 days |
| Volume storage (per volume, attached or not) | — | $0.15/GB/mo |
| **Two-machine fleet (both warm, 4 GB each)** | — | **~$42.80** (+ volumes) |

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
  is bounded at 1.5 s and abandons its worker thread on timeout —
  `monitoring.probe_db`, via the `executor.shutdown(wait=False)` path). Under a black-holed DB, threads can accumulate
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
or re-introducing service-level checks. Two items from the original list have
since been **determined** (deploy gating; the bind/auth design shipped by #3062)
and are recorded at the end of this section instead of being left open.

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
2. **Is the 9090 listener bound on `0.0.0.0` and `/healthz` unauthenticated in
   the deployed image?** The bind-address and auth *design* concerns are resolved
   by **#3062**, not open. The listener serving 9090 in production is
   **`monitoring.start_health_listener`** (`_HealthzHandler`), added by #3062: it
   binds `0.0.0.0` by default (`HEALTHZ_BIND`, overridable via
   `TORTOISE_HEALTHZ_BIND`) and serves `/healthz` **unauthenticated by
   construction** — a separate handler from the Bearer-gated
   `monitoring._Handler`, exposing only the loop heartbeat. The older
   `monitoring.serve_health(port=9090, bind="127.0.0.1")` is **not** on this
   path: it is started only by the standalone CLI (`tortoise health-server`,
   `tortoise/__main__.py`), which the hosted app never invokes. The remaining
   item is verification of the deployed image:
   `fly ssh console -a tortoise-y4mjjq -C "ss -ltn | grep ':9090'"` (expect
   `0.0.0.0:9090`), then a token-less request from inside the machine (expect
   200).
3. **The `grace_period` clamp** — §6.4.1. The genuinely `flyd`-internal residual
   is **what status a check reports *during* grace_period**: if an undocumented
   server-side clamp exists, a machine is marked unhealthy mid-boot. Run the
   §6.4.1 staging test — first failure at ≈60 s ⇒ a clamp exists; at
   ≈`grace_period` ⇒ none.

**Determined — no longer open questions:**

- **Does a failing top-level check block `fly deploy`? YES.**
  `WaitForHealthchecksToPass` (`internal/machine/leasable_machine.go`, called
  from `machines_deploymachinesapp.go`) counts `len(cfg.Checks)` *plus* service
  checks and then requires every reported check to pass; it does not consult
  `kind` and has no informational/readiness filter. So a top-level check is
  **routing-inert but deploy-gating**: it can never de-register the machine, and
  it *can* fail a deploy. That is a **known, accepted cost** of the check, and
  the reason for the merge-order requirement in §6.0 (**#3062 must land first,
  or in the same batch**).
- **Is the loopback/401-auth risk real for the hosted app?** No — **#3062
  resolves it** via `start_health_listener` / `TORTOISE_HEALTHZ_BIND` /
  `_HealthzHandler` (see #2 above). The earlier framing of
  `monitoring.serve_health(port=9090, bind="127.0.0.1")` and the Bearer-gated
  `monitoring._Handler` as production risks was wrong: neither is on the hosted
  9090 path.

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
- [ ] Routing check is `[[services.tcp_checks]]` (kernel-served: **not starved by event-loop/thread-pool scheduling** — it can still fail if the accept backlog saturates) and no `[[services.http_checks]]` entry remains (§6.4)
- [ ] Top-level `[checks.loop_liveness]` targets port 9090 / path `/healthz`; the listener (`monitoring.start_health_listener`, #3062) binds `0.0.0.0` and `/healthz` is unauthenticated 200/503 (§6.4, §6.9)
- [ ] **Merge order honored:** #3062 is deployed before/with `[checks.loop_liveness]`, and `ss -ltn | grep ':9090'` inside the machine shows `0.0.0.0:9090` (§6.0)
- [ ] A deliberately failing second `[[services]]` check does **not** de-register the primary service (§6.9 — open until observed)
