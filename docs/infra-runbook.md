---
title: Tortoise Hosted Platform Infrastructure Runbook
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-infra
aboutObjects: fly-io, falkordb, cloudflare
created: 2026-08-03
updated: 2026-09-23
---

# Tortoise Hosted Platform — Infrastructure Runbook

> **Retention/deletion windows:** the single source of truth is `docs/retention-and-deletion.md`. Do not restate a window here — link that document.

**Epic:** #7711 (legacy provisioning epic — provenance) · availability watchdog: #2850
**Last updated:** 2026-09-22

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

### Canonical API host — and why `tortoise.dev` is not ours (recorded 2026-09-19, #3474)

**The canonical hosted API base URL is `https://api.premiselabs.co`** (MCP at
`https://api.premiselabs.co/mcp/`). The client-facing surfaces that declare the API base
agree on that spelling — `.mcp.json`, `README.md`, `client/README.md`,
`docs/INGEST_CONTRACT.md`, `docs/data-safety.md`, and the availability watchdog's
`DEFAULT_PROBE_URL` (`.github/scripts/availability-watchdog.sh`). Other hostnames are
different surfaces, **not** competing client-facing API bases —
`tortoise.premiselabs.co` (landing / legal, Cloudflare Pages — its `/auth/start`
answers **404 by design**: the sign-in BFF moved off this origin in #4054),
`app.premiselabs.co` (dashboard — and the BFF/auth surface since #4054), and
`tortoise-y4mjjq.fly.dev` (the Fly app host, also
used as a server-side/internal base such as `INTERNAL_API_URL`).

⛔ **`tortoise.dev` is a third party's zone. Never point a client, a doc, or a DNS
record at `api.tortoise.dev`, and do not add a record for it.** Recorded so this is not
investigated a third time. The probes below are reproducible and were taken
2026-09-19 03:23 UTC (timings are single samples).

```bash
dig +short api.tortoise.dev          # 172.67.211.252, 104.21.53.100 + 2 IPv6 — Cloudflare anycast
echo | openssl s_client -connect api.tortoise.dev:443 -servername api.tortoise.dev
                                     # handshake COMPLETES; "Verify return code: 0 (ok)";
                                     # cert CN=tortoise.dev, SAN tortoise.dev + *.tortoise.dev
curl -sS -i https://api.tortoise.dev/health      # HTTP 522 "error code: 522" — a LOUD status
                                                 # code, not a hang; 16-byte body in ~20s
curl -sS -i https://tortoise.dev/                # HTTP 200, 0 bytes, x-turbo-charged-by: LiteSpeed
                                                 # → a third-party shared host, not Fly, not Pages
curl -sS https://api.premiselabs.co/health       # HTTP 200 (0.24s) — the canonical host, healthy
curl -sS https://tortoise-y4mjjq.fly.dev/health  # HTTP 200 (0.32s) — the Fly app itself
```

Ownership — reproducible evidence first, credential-gated evidence labelled as such:

- *(reproducible with Fly credentials)* `fly certs list -a tortoise-y4mjjq` →
  `api.premiselabs.co` only, and `fly certs check api.tortoise.dev` → *certificate not
  found*. Certificate Transparency (`crt.sh`) holds **no `api.tortoise.dev` SAN** — only
  `tortoise.dev`, `*.tortoise.dev`, `www…` names. Fly has never served this name.
- *(reproducible)* Registrar RDAP (`rdap.dynadot.com`, 2026-09-19): **Dynadot LLC**; the
  registrant is a privacy service (`Super Privacy Service LTD c/o Dynadot`), registered
  2024-05-19, expires 2027-05-19. Nothing in it identifies an owner we know.
- *(reproducible)* With this record excluded, nothing in the repo or its history has ever
  offered the name: `git grep -I 'api\.tortoise\.dev' -- ':!docs/infra-runbook.md'` →
  **0 hits**, and
  `git log -S'tortoise.dev' --all -- . ':(exclude)docs/infra-runbook.md'` → **0 commits**.
- *(operator-verified, credential-gated — not reproducible without Cloudflare access)*
  `GET /zones` on our Cloudflare account returned exactly `dmeer.app`, `eldato.com.mx`,
  `premiselabs.co`: **`tortoise.dev` is absent from our account.** Nameserver pairs are
  *not* used as evidence here — this account issues more than one pair (`everton`/`sonia`
  for `premiselabs.co`, `elisa`/`woz` for `eldato.com.mx`), so a different pair proves
  nothing on its own.

**Consequence: there is no in-repo fix and no DNS change for us to make.** #3831
reached the same conclusion — its earlier "remove the record" decision is VOID, because
the owner never owned the name. The issue's original "TLS black-hole" framing is also
**stale**: the host now fails *loudly* with a 522 in ~20 s instead of hanging.

*Adjacent work owned elsewhere — do not re-fix it in this section:* the
`tortoise-api.fly.dev` target in the table above does not resolve (`dig +short
tortoise-api.fly.dev` → empty; the Fly app is `tortoise-y4mjjq` per `fly.toml`),
tracked by **#3046**. A host that answers slowly instead of failing fast is the
client-contract defect tracked by **#3805** (one canonical base URL + bounded
fail-fast).

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

### 4.1 What a client must observe on `/health` — the effect and its budget (#3811)

`/health` is the liveness surface. A client that starts against the hosted
service must observe, **within the client's own startup budget**:

| observed | value |
|---|---|
| HTTP status | **200** on the healthy path, with the body below. A dead *downstream* is `status: degraded` in the body — never a handler-generated non-200 (health truth lives in the body, not in a 5xx). **Limit, stated:** `/health` is *not* exempt from the outermost `WaitBoundMiddleware` (`_TRANSPORT_WAIT_BOUND_EXEMPT` covers only `POST /v1/context`; `/v1/internal/` is exempt separately, by the `_TRANSPORT_WAIT_BOUND_EXEMPT_PREFIX` added in #4939), so a request that does not complete inside its **10 s** wait bound (`tortoise/mcp_auth.py::_TRANSPORT_WAIT_BOUND_S`) is answered **504 + `Retry-After`** instead of hanging (#4412). That refusal is legible, but a no-retry client cannot act on it — 200-inside-the-budget is the requirement; the refusal is the legible-failure floor, not a substitute. |
| body | a JSON object carrying `status` (`"ok"` \| `"degraded"`) and `db` (`{"ok": bool, "latency_ms": …, "error": …}`). The deploy gate reads `db.ok` **by value**, never by spelling (#4470). |
| latency | **< 15 s** — the client's own eager-startup deadline. In practice **< 10 s**, because the app's own wait bound refuses first. |

**The budget's source is the client, not this document.** Pi's `mcp-client`
connects **eagerly at session start**: one attempt per eager server, a hard
15 000 ms per-server budget and **no retry** (`DEFAULT_CONNECTION_TIMEOUT_MS =
15000`, `~/.pi/agent/extensions/mcp-client/index.ts`; §6.11). 15 s is the
wall-clock envelope in which the service must be answerable for a client to
start at all — there is no second attempt. `/health` is the surface whose stall
is the **same held event loop** that fails that connect (#2924: “/health hangs
>8 s” means the loop was held, and the client's first request times out inside
the same window).

**Stated plainly:** the client's eager request targets `/mcp`, not `/health`.
`/health` reports whether the process is answerable at all, so a `/health`
response past the client's single attempt is by construction a client-visible
startup failure.

**Executed, not asserted against source text.**
`tests/test_health_client_effect.py` starts the real app on a real port, issues
the real request, and asserts the resolved status/body/latency — and re-runs it
with the data-plane probe wedged past the budget, so a handler that inherits the
stall (the #2924 shape) reds. It complements
`tests/test_health_ready_nonblocking.py`, which pins nonblocking *structure*
in-process and names no client-visible budget.

## 4.5 Local Development — Local Stays Local

**Best practice: a self-hosted/local instance is intentionally local.** Do not
point local tooling at the hosted (cloud) DB — a remote connection from a local
install defeats the purpose of hosting locally.

> **Exception — the committed repo-root `.mcp.json` defaults to the HOSTED
> endpoint.** That file ships pointed at `https://api.premiselabs.co/mcp/` with
> an env-indirect `Bearer ${TORTOISE_API_KEY}` (`tortoise/onboarding/SKILL.md`
> §3b), so an agent launched inside this checkout talks to the hosted API, not
> to a local daemon, unless you point the `tortoise` entry's `url` back at your
> own daemon (`http://localhost:8000/mcp` — the self-host path in
> `docs/quickstart-selfhosted.md`). The local rules below describe that
> self-hosted/stdio configuration.

- Local tooling (MCP server, SDK scripts, graph-scripts) resolves its DB target
  from `TORTOISE_DB_URI` — canonical local form
  `docker://:falkordb@localhost:6379/tortoise` (compose publishes 127.0.0.1:6379;
  `.env.example`). A **stdio** MCP entry sets its own `env` block; the compose
  daemon resolves `TORTOISE_DB_URI` from its own environment, and the committed
  repo-root `.mcp.json`'s `tortoise` entry carries no `env` at all (see the
  exception above). The legacy `FALKORDB_*` trio defaults to the
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
- HTTP (tenant) mode uses a fresh `org_{id}` namespace — existing stdio data
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

`POST /v1/sessions` — the beta testers' most-critical feature — runs the LLM
extractor over the conversation. **A capture is stored unconditionally**:
with no LLM provider key configured the Session + its turn Points are STORED
and stay searchable, and only the LLM extraction into memory points is
skipped — the receipt carries `extraction_mode: "no-provider"` plus a warning
(#3892 owner ruling, 2026-09-18), or `extraction_mode: "extraction-disabled"`
when the team turned extraction OFF in the dashboard (#4258, default ON). The regex extraction loop was removed as a
product path (#822) and there is no fallback, so with no key no memory points
are produced. This section is the ops contract for making sure extraction is
enabled.

### Env keys (set on `tortoise-api`/Fly; GitHub Actions secrets are the source)

| Key | Provider | Default model | Notes |
|-----|----------|---------------|-------|
| `OPENROUTER_API_KEY` | OpenRouter (aggregator) | `deepseek/deepseek-chat` | First in priority; one key → many model families |
| `DEEPSEEK_API_KEY` | DeepSeek | `deepseek-chat` | Cheapest-tier default; matches the analyzer's historical default |
| `OPENAI_API_KEY` | OpenAI | `gpt-4o-mini` | |
| `GEMINI_API_KEY` | Google Gemini | `gemini-2.0-flash` | Also used by MCP tooling — its presence here does NOT alone prove session capture is enabled |
| `TORTOISE_SESSION_LLM_MODEL` | — | per-provider default | Override, format `<provider>:<model>`; the provider must match the key that is set. **On the hosted deployment `deploy-hosted.yml` now sets this unconditionally** — from the GitHub secret if present, else the versioned default `openrouter:google/gemini-2.5-flash` — so hosted extraction requires `OPENROUTER_API_KEY` (or a GitHub secret overriding the model). It is deliberately NOT left optional: an absent GitHub secret used to leave the hand-set Fly value in place forever (#4126). Unset for self-hosters, where the per-provider default applies. |
| `TORTOISE_SESSION_LLM_MOCK` | — | unset | **TEST-ONLY** seam (`1` = offline MockModel). **NEVER set on Fly** — it counts as *configured* for the extraction gate, so a deploy with it set passes the gate while captures silently write offline MockModel points (see Verification procedure step 1) |

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
  (`fly secrets list -a tortoise-api`). A GH-secret miss ships a deploy whose
  captures STORE turns but never extract into memory; the deploy workflow
  warns (warn-only by design, #1346 — a fail-closed gate here held ALL deploys
  for 2+ days) so the miss is visible without blocking the rest of the API.

### Cost bounds per capture

Bounds enforced by `capture_session` (tortoise/hosted_api.py). **A missing
provider key is not a gate** (#3892): a keyless capture is STORED (turns only)
with extraction skipped, so it never refuses and never reaches the 402
estimate below (the keyless path mints zero non-episodic points). The
extraction-bearing path is bounded IN ORDER:

1. **Turn cap** — `MAX_SESSION_TURNS = 500` → `400` above it.
2. **Points quota (pre-write estimate)** — `402` when the extraction-aware
   estimate exceeds the team's points quota. Estimate:
   `est = 3 × Σ_turns min(sentences, MAX_EXTRACTIONS_PER_TURN=200)`
   (`sdk._session_extraction_estimate` — the default v2 lane; sentence count
   is capped per turn — the #329 flood gate). Skipped entirely on the keyless
   path, which extracts nothing.

No sessions quota: the flat `max_sessions = 1000` was removed in **#4010** —
sessions are unlimited for every tier, and a stored `Team.max_sessions` is
deliberately not honoured as a cap.

Free-tier interplay (product/pricing.json): `max_graph_nodes: 10000` is the
points-quota numerator for NON-episodic Points only (turn Points / Session /
Event are episodic and don't count), and `included_write_ops_per_month: 10000`
is the write-ops budget. Worst-case node amplification per turn: 200
sentences × 3 = 600 nodes (v2 default), so a full 500-turn session is ~300K
estimated nodes — for an extraction-bearing capture, always stopped by the 402
gate BEFORE any write. In practice the
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
#    MOCK=1 counts as 'configured' for the extraction gate — a deploy with it set
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
# A 200 with "extraction_mode":"no-provider" = no key: turns stored,
# extraction skipped. "extraction-disabled" = the team turned extraction OFF.

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

- [ ] ≥1 LLM provider key in GitHub secrets (the deploy warns if absent — warn-only per #1346, so without one every capture STORES its turns but extracts nothing into memory)
- [ ] `TORTOISE_SESSION_LLM_MOCK` is NOT set on Fly (`fly secrets list -a tortoise-y4mjjq | grep TORTOISE_SESSION_LLM_MOCK` → empty). MOCK=1 is a TEST-ONLY seam that counts as configured for the extraction gate — a deploy with it set passes the gate while captures write offline MockModel points. NEVER set it on Fly.

## 5. Dashboard Deploy

```bash
# Build and deploy dashboard
cd apps/dashboard
npm run build
wrangler pages deploy dist --project-name=tortoise-dashboard
```

## 6. Availability & Machine Topology — routing checks, autostop policy, and the single-volume ceiling (#2850)

**App:** `tortoise-y4mjjq` (region `iad`). **Status:** 2026-09-12 — the routing
check was changed from HTTP to TCP, and the machine lifecycle policy was made
explicit (§6.2, §6.4). A separate non-routing `[checks.loop_liveness]` entry is
now **live** (§6.0, §6.4): it is deploy-gating and targets the 9090 listener
added by #3062, whose precondition was verified on the live machine 2026-09-17
(§6.0). Genuine
2-machine redundancy
is still **not** achievable as a config-only change — §6.3 says exactly what
blocks it and what would have to change. The redundancy work remains an operator
decision; it was not attempted here.

### 6.0 Merge order — the #2850 fix landed independently; the follow-up is now live

**The top-level `[checks.loop_liveness]` entry is now LIVE (#3447, 2026-09-17).**
It landed only after its #3062 precondition was verified on the live machine
(evidence below). The #2850 core fix — the TCP routing check and the explicit
lifecycle policy — shipped first and independently, carrying **zero deploy-gate
risk**; that check was the only deploy-gate hazard, and it is now safe to gate on.

**Why it was split out (historical).** `flyctl`'s deploy health wait
(`WaitForHealthchecksToPass`, `internal/machine/leasable_machine.go`, called from
`machines_deploymachinesapp.go`) counts **`len(cfg.Checks)`** — the top-level
`[checks]` table — *plus* service checks, and then requires **every reported
check** to be passing. There is no informational/readiness filter in the deploy
path and `kind` is not consulted. Because
`.github/workflows/deploy-hosted.yml` auto-deploys on any push touching
`fly.toml`, shipping the check before its 9090 listener exists (that listener is
added by #3062) would fail the deploy after `--wait-timeout 420`, retry **5×**
with a 45 s sleep — **~35 minutes of failing deploys, each retry
rolling/replacing the sole production machine** (~85 s cold-boot outage each
time). Routing was never at risk; the deploy gate was. Deferring the check
removed the only source of that hazard from that PR; #3447 landed it once the
precondition below was verified.

- **This PR is safe to merge and deploy independently.** The TCP routing check
  rides `internal_port = 8000`, which the deployed image already listens on, so
  the deploy gate passes with **no dependency on #3062**.
  `deploy-hosted.yml` will auto-deploy on merge — that is expected and safe.
- **The ordering constraint is now SATISFIED (#3447).** The check landed only
  after #3062 was deployed, so the image and the check are in sync. `#3062`
  ships `monitoring.start_health_listener`
  (`_HealthzHandler`, bound to `0.0.0.0` via `HEALTHZ_BIND` /
  `TORTOISE_HEALTHZ_BIND`, serving unauthenticated `/healthz`). Verification
  evidence, captured 2026-09-17 against `tortoise-y4mjjq`:

  ```bash
  # VERIFIED: LOCAL address 00000000:2382 (= 0.0.0.0:9090), not the
  # loopback-only 0100007F:2382. The check asserts BOTH facts that matter: the
  # port is listening, AND it is bound on the wildcard address rather than
  # loopback. `/proc/net/tcp` and `grep` are both
  # present in the image; `ss`/iproute2 is NOT (Dockerfile.hosted installs only
  # curl + build-essential), so an `ss` probe prints "ss: not found":
  fly ssh console -a tortoise-y4mjjq -C "grep ':2382' /proc/net/tcp"
  # VERIFIED: http=200 from a token-less request — {"status":"ok",
  # "loop_age_ms":67.7,"loop_stale":false}:
  fly ssh console -a tortoise-y4mjjq -C "python3 -c \"import urllib.request as u;print(u.urlopen('http://127.0.0.1:9090/healthz',timeout=5).status)\""
  ```

  Both outputs were as expected, so the follow-up (#3447) landed. #3062 merged
  2026-09-13T02:45:10Z and the machine was redeployed after it (last update
  2026-09-16T18:49:30Z), so the listener is **deployed**, not merely present in
  the image.

### 6.1 What happened (2026-09-10, ~19:10–20:35 UTC)

- Public API unreachable ~35 min. Fly's proxy logged `[PR01] no known healthy
  instances found for route tcp/443` continuously for `/health`, `/v1/organizations`,
  `/v1/sessions`, `/mcp/`.
- `flyctl machines list` showed exactly **one** machine, state `started`,
  `CHECKS 0/1`.
- From **inside** that machine: port 8000 listening, `/health` answered 200
  `{"status":"ok","db":{"ok":true,"latency_ms":322.8}}`, loadavg 0.05, 3 GB
  of 4 GB free. **The app was healthy the entire time; only the
  health-check → routing path failed.** The original "the app wedges / the event
  loop is blocked" diagnosis is wrong and superseded by this evidence.
- Check flapped: `✗ 19:13:26 → ✓ 19:20:41 → ✗ 19:21:11 → ✓ 19:24:26 → ✗ 19:25:43`;
  it recovered on its own ~20:35 with no intervention. Throughout, the machine
  was never stopped or restarted by the failing check — de-registration takes it
  out of the router only, and when the check passed again it was re-added. (The
  manual `fly machine restart` at 19:32 was an operator action, not a platform
  response to the check.)
- Mechanism: `/health` performed a FalkorDB round trip. Steady state was
  250–330 ms, but real socket timeouts occurred; when a stall outlasted the
  check's window Fly marked the **only** machine unhealthy and removed the sole
  route. The browser saw a CORS error because a failed response carries no
  `access-control-allow-origin` header.

The flap only became an outage because of three independent defects:

| # | Defect | Status |
|---|---|---|
| 1 | `path = "/health"` was coupled to a downstream — process liveness inherited every DB/probe stall | **mitigated in the app** — `hosted_api.health` (`@app.get("/health")`) returns 200 unconditionally with `status` = `ok`/`degraded`; DB truth lives in `/health/ready` (`hosted_api.health_ready`). `status` is `ok` only when `db.ok` **and** `backup_watcher.ok` are true; read `backup_watcher.state` for which: `running` and `disabled` are `ok` (`disabled` = no sweep config, or `BACKUP_WATCHER_DISABLED=1`), while `stopped` (started, thread gone), `failed` (wanted, the start raised — `backup_watcher.error` carries it) and `unknown` (metadata unreadable) are not. Before this, a watcher that never started was invisible on `/health` while the process served normally (#2851/#2922: ~31 days) |
| 2 | Routing was decided by an **HTTP** service check, so any application-level latency (probe latency, event-loop queueing) could de-register the only machine | **fixed in config** — `[[services.tcp_checks]]` is kernel-served, so it is not starved by event-loop/thread-pool scheduling and a slow/starved app no longer de-registers the machine (§6.4); the application-level liveness signal is the now-**live** non-routing `[checks.loop_liveness]` check (§6.0/§6.4), which cannot affect routing |
| 3 | One machine + an implicit, undeclared lifecycle policy | policy now explicit (§6.2); machine redundancy **blocked** (§6.3) |

### 6.2 Machine lifecycle policy — now declared in `fly.toml`

`fly.toml` previously declared **none** of `auto_stop_machines`,
`auto_start_machines`, `min_machines_running`, so production ran on platform
defaults that appeared nowhere in the repo. Fly documents the defaults and the
semantics in the `[[services]]` section of the [config reference](https://fly.io/docs/reference/configuration/#the-services-sections):

| Key | Default if absent | Set now | Deliberate choice |
|---|---|---|---|
| `auto_stop_machines` | `"off"` | `"off"` | **Never stop.** A stopped sole machine means zero healthy instances (the #2850 signature) and the next request waits out an ~85 s cold boot. This restates the effective default — no behavior change, no cost change. |
| `auto_start_machines` | `true` | `true` | Fly's explicit "run continuously" recipe is `off` + `false`. We deliberately keep `true`: it turns autostart back into a self-heal path, so a machine that is genuinely **stopped** (operator stop, host failure, crash-loop give-up) is started again by the next request. It does **not** apply to a de-registered machine: de-registration removes the machine from the router only — the machine keeps running, is never stopped or restarted, and is re-added to routing when its check passes again. Fly recommends both-on or both-off (to avoid machines that never start *or* never stop). We deliberately take the never-stop side, which cannot strand a stopped machine. |
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
  removes it from routing. The process keeps running (in #2850 it stayed alive
  and answered on loopback throughout the outage), and the machine is **re-added
  to routing when the check passes again** — re-registration is a router
  decision, not a machine lifecycle action, so no stop or restart is involved.
  Restarting is a separate, manual step reserved for a genuinely *wedged*
  process (`fly machine restart`, as used at 19:32 in #2850).
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

**(c) `/data` state would fork, and the durable DB must be shared — but the
embedded fallback is not a state-split risk.** A per-machine volume means a
per-machine `/data/ingest` corpus: `/v1/index/docs` re-runs on the other machine
re-fetch the corpus (the hash-dedup cache is volume-local), and any state under
`/data` stops being a single source of truth. Graph data itself is fine — it
lives in FalkorDB Cloud, selected by `TORTOISE_DB_URI`. The embedded fallback
(`TORTOISE_DB_PATH`, default `/data/tortoise.db`; resolved by
`hosted_api._resolve_embedded_db_path`) is per-machine, **but it is unreachable
in production, so it cannot silently split state**: `entrypoint.sh` resolves the
DB target in order — an explicit `TORTOISE_DB_URI`, else `FALKORDB_CLOUD_URI`,
else if `FLY_APP_NAME` is set it prints `FATAL — FALKORDB_CLOUD_URI not set in
production. Refusing to start with no durable DB.` and **exits non-zero**
(fail-closed by design). Embedded redislite is selected only in the final `else`
branch, reachable only when `FLY_APP_NAME` is unset (local/dev). A production
deploy missing `FALKORDB_CLOUD_URI` therefore does **not** boot into two SQLite
files — it refuses to start. The real constraint is the inverse: because
embedded is not a production option, **both** machines must reach the same
durable DB — and any machine that cannot must fail closed, as `entrypoint.sh`
already enforces.

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
4. Only then does `min_machines_running` have any effect. It can never create
   or add redundancy — with the current one-machine, single-attach-volume,
   in-process-state topology `min_machines_running = 2` is a **silent no-op**,
   and it stays a no-op until both machines above actually exist. Once they do,
   `2` with `auto_stop_machines = "stop"` keeps both warm; `1` keeps one warm
   and lets the proxy stop the other when idle. With `auto_stop_machines = "off"`
   every machine always runs and `min_machines_running` stays declarative.
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

### 6.4 Health checks — the TCP routing check and the live loop-liveness check

`fly.toml` carries **two checks**: the kernel-served TCP routing check, which is
not an HTTP probe of the application on the routing path, and the now-**live**
non-routing top-level `[checks.loop_liveness]` check (#3447), whose contract and
rationale are recorded at the end of this section.

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
  deliberately lives elsewhere (`/health/ready`, and the 9090 liveness check in
  §6.4).
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
- `grace_period = "180s"` is a deliberately generous carry-over, not a
  requirement of the TCP check. The app binds `0.0.0.0:8000` **immediately** on
  start: `entrypoint.sh` states it, and `hosted_api._lifespan` spawns the ~85 s
  torch/model pre-warm as a `daemon=True` thread that never blocks bind — so the
  listening socket exists within seconds and the model load does not gate a TCP
  connect. Whether Fly honors the full 180 s is unresolved; see §6.4.1.
  `[deploy] wait_timeout = "5m"` (CI passes 420 s) still exceeds boot +
  `grace_period` under either reading of §6.4.1.

**Live — non-routing check, top-level `[checks.loop_liveness]` (#3447).** Shape:
`type = "http"`, `port = 9090`, `path = "/healthz"`, `interval = "15s"`,
`timeout = "5s"`, `grace_period = "180s"`. It shipped in `fly.toml` only after
the §6.0 precondition was verified (2026-09-17: `00000000:2382` and `http=200`).

- **Top-level checks do not affect request routing — but they DO gate
  `fly deploy`.** Fly's config reference scopes them to "independent health
  checks that don't affect request routing"; the proxy ignores them when
  choosing where to send traffic, so the 9090/`/healthz` signal can never take
  the app offline. That is *not* the whole story: `flyctl`'s deploy health wait
  counts top-level checks too (see the "Determined" block in §6.9 and §6.0), so
  a failing or mis-bound `loop_liveness` check **blocks a deploy** even though it
  cannot de-register the machine. The check is therefore **routing-inert but
  deploy-gating** — which is exactly why it was deferred until its 9090 listener
  (from #3062) was deployed and verified on the live machine (§6.0).
- Top-level checks **require** `port`, and Fly requires that port to be bound on
  **`0.0.0.0`**. The application-side contract is
  `monitoring.start_health_listener` (added by **#3062**): it binds `0.0.0.0`
  (`HEALTHZ_BIND`, overridable via `TORTOISE_HEALTHZ_BIND`) and serves `/healthz`
  **unauthenticated by construction**; that deployed-image verification is now
  complete (§6.9 #2).
- The interface contract for the listener is: `GET /healthz` returns **503**
  only when the loop is STALE **AND** IDLE, else **200**
  (`503 if (stale and idle) else 200` — `tortoise/monitoring.py`). A stale loop
  with a request in flight still reports 200, so this check does **not**
  reliably surface a true wedge; it errs toward 200, the safe direction for the
  deploy gate it feeds.

#### 6.4.1 The `grace_period` clamp — an open, testable question (NOT a fact)

**Status: unconfirmed — treat the clamp as a hypothesis, not a fact.** The
`hosted_api.health` docstring (`tortoise/hosted_api.py`, `@app.get("/health")`)
claims that "Fly caps the http_check grace period at 60s", attributed to the
#338 fix.
Independent research could **not** confirm this: no such cap appears in Fly's
config reference, in `flyctl`, or in `fly-go`, and `flyd` is closed-source, so an
undocumented server-side clamp cannot be ruled out. The honest position is
**unconfirmed — possibly an undocumented server-side behaviour**. The section
above must not be read as assuming either outcome:

- **If no clamp exists** (what the documented field list implies): the effective
  `grace_period` is the configured `"180s"` — generous headroom the TCP check
  does not need. The listener exists within seconds of start (the ~85 s
  torch/model load does not gate a connect), and the ~2 min FalkorDB DNS tail
  (#1381) is surfaced by the deploy workflow's DB health verification, not by this check.
- **If the clamp exists**: the effective grace period is **60 s**, still far
  longer than the seconds it takes the listener to bind, so the clamp is no
  longer a cold-start hazard for the TCP check. The historical worry — that a
  60 s clamp would mark every cold start unhealthy — applied to the old HTTP
  check, whose probe depended on the boot-time model load; it does not carry
  over to a kernel-connect check.

**How to test it** (staging app only, never production). The check must be made
to **fail deterministically** — with the normal image the socket binds within
seconds, so the check passes and the clamp question is never exercised. Point
the staging service's check at a deliberately **closed port** (or hold the app
down), set `grace_period` above 60 s, start the machine, and record the delay
from machine start to the first failed check in `fly checks list` / `fly logs`.
A first failure at ≈60 s ⇒ the clamp is real; ≈`grace_period` s (or no failure
before grace expires) ⇒ no clamp. `fly config show` only echoes the local
config and cannot answer this. Nobody has run this test yet — it is listed in
§6.9.

### 6.5 Cost reference (what "one machine warm" actually costs)

> ⚠️ **LIST-PRICE ESTIMATES — not a quote — and DATED (snapshot 2026-09-10).**
> These are approximate list-price estimates derived from Fly's published
> per-second and per-GB rates; Fly bills actual usage, so this is not a quote and
> not a guaranteed monthly figure. They assume the machine shape declared in
> `fly.toml` `[[vm]]` — **`shared-cpu-2x` with `memory_mb = 4096`**, region
> `iad`, running continuously — and one attached volume.
> These figures are kept so the *shape* of the cost argument (one machine ≈ half
> a two-machine fleet; RAM above the 512 MB preset dominates) stays reviewable.
> Fly's live prices differ by ~4 % on these line items (re-checked 2026-09-12:
> the 512 MB preset is ≈`$0.00000156/s · $0.0056/hr · $4.04/mo`, and the 4096 MB
> machine ≈`$0.00000857/s · $0.0309/hr · $22.22/mo`), so every monthly total here
> — including the two-machine figure and the ~$21.40 references in §6.2/§6.3 —
> is **low**. The live
> [pricing page](https://fly.io/docs/about/pricing/) **is the source of truth;
> re-fetch it before making a spend decision.**
>
> *Controller follow-up: re-fetch and re-derive this table (and the `fly.toml`
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
"Post-deploy DB health verification"), so during #2850 nothing alerted for ~35 min while
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
since been **determined** (deploy gating; the bind/auth design from #3062)
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
   the deployed image?** (This gated the **follow-up** PR that restored
   `[checks.loop_liveness]` — §6.0. It was **verified 2026-09-17**:
   `grep ':2382' /proc/net/tcp` → `00000000:2382`, and `http=200` from
   `/healthz`.) The bind-address and auth
   *design* concerns are resolved
   by **#3062**, not open. The listener serving 9090 in production is
   **`monitoring.start_health_listener`** (`_HealthzHandler`), added by #3062: it
   binds `0.0.0.0` by default (`HEALTHZ_BIND`, overridable via
   `TORTOISE_HEALTHZ_BIND`) and serves `/healthz` **unauthenticated by
   construction** — a separate handler from the Bearer-gated
   `monitoring._Handler`, exposing only the loop heartbeat. The older
   `monitoring.serve_health(port=9090, bind="127.0.0.1")` is **not** on this
   path: it is started only by the standalone CLI (`tortoise health-server`,
   `tortoise/__main__.py`), which the hosted app never invokes. The deployed-image
   verification was completed 2026-09-17: the 9090 port accepted a
   connection from inside the machine —
   `fly ssh console -a tortoise-y4mjjq -C "grep ':2382' /proc/net/tcp"`
   (observed a line whose local address is `00000000:2382` — i.e. `0.0.0.0:9090`,
   not the loopback-only `0100007F:2382`; `ss` is not in the image, `grep` and
   `/proc/net/tcp` are) — then a
   token-less request from inside the machine (observed 200).
3. **The `grace_period` clamp** — §6.4.1. The genuinely `flyd`-internal residual
   is **what status a check reports *during* grace_period**: if an undocumented
   server-side clamp exists, the effective window is shorter than configured.
   (A mid-boot failure only matters for a check that depends on the boot-time
   model load — a kernel TCP connect does not.) Run the §6.4.1 staging test —
   first failure at ≈60 s ⇒ a clamp exists; at ≈`grace_period` ⇒ none.

**Determined — no longer open questions:**

- **Does a failing top-level check block `fly deploy`? YES.**
  `WaitForHealthchecksToPass` (`internal/machine/leasable_machine.go`, called
  from `machines_deploymachinesapp.go`) counts `len(cfg.Checks)` *plus* service
  checks and then requires every reported check to pass; it does not consult
  `kind` and has no informational/readiness filter. So a top-level check is
  **routing-inert but deploy-gating**: it can never de-register the machine, and
  it *can* fail a deploy. That is why the check was **deferred to a follow-up**
  that merged only after #3062 was verified (§6.0) — #3447 landed it 2026-09-17.
- **Is the loopback/401-auth risk real for the hosted app?** No — **#3062
  resolves it** via `start_health_listener` / `TORTOISE_HEALTHZ_BIND` /
  `_HealthzHandler` (see #2 above). The earlier framing of
  `monitoring.serve_health(port=9090, bind="127.0.0.1")` and the Bearer-gated
  `monitoring._Handler` as production risks was wrong: neither is on the hosted
  9090 path.

## 6.10 Cold-start first contact — the session-auth JWKS fetch (#3284)

**Symptom.** The **first** session-authenticated request on a fresh process could
hang until a client gave up (a `000` / no answer), while `/health` stayed 200.
That pairing is the signature: the loop was fine, one request was waiting.

**Cause.** The Supabase JWKS fetch (`tortoise/session_auth.py`) was paid by the
first request instead of by the process, and its budget was not what it claimed.
`httpx.AsyncClient(timeout=5)` is **per phase** (connect/read/write/pool — a
20 s sum), not a 5 s total, and one request can pay **two** fetches (TTL refresh
+ `kid`-miss refetch). The same lesson is already recorded on the control-plane
probe (`hosted_api.CONTROL_PLANE_PROBE_PHASES`).

**What changed (2026-09-16).**

- `TORTOISE_JWKS_TIMEOUT` is now a **hard total per fetch** (default 4 s),
  enforced by `asyncio.timeout` **outside** the `_fetch_jwks` seam, so it also
  covers DNS (which httpx's connect phase cannot cancel). Per-phase timeouts
  still narrow first, so a phase normally unwinds the client by itself. One
  request is bounded by `2 ×` that value.
- `_first_contact_prewarm` (lifespan startup half, **behind** the listener —
  never awaited before `yield`, per #2953) pre-pays the JWKS fetch and the
  control-plane probe in Supabase mode. Registry/self-host does neither. A
  failed warm-up deliberately does **not** arm the request-path cooldown (it
  would otherwise refuse every request for `TORTOISE_JWKS_COOLDOWN` after a
  single boot-time blip) — the first request makes its own bounded attempt,
  UNLESS the request-path cooldown is already armed (`_last_failure_at` is a
  module global, so it survives lifespans), in which case that request is
  answered from the cooldown with **no fetch**.
  An empty key set (`200 {"keys": []}`) is reported as its own outcome, not as
  a transport failure: it answers 401, not 503.
- Every session-auth **503 now carries `Retry-After`** (the remaining cooldown
  window) and a JSON body. `Retry-After` is listed in
  `Access-Control-Expose-Headers`, so the dashboard JS can read it (the header
  is not CORS-safelisted). A failure is actionable instead of looking like an
  outage.

**What is still not app-fixable.** A **zero-byte** 503 with `server: Fly/…` and
no body is generated by Fly's proxy *before the app sees the request*
(`error.message="… [PR01] no known healthy instances found …"`) — that was
#3144, and it is a **de-registration** symptom, not a fetch symptom. The app
cannot attach a body or a `Retry-After` to it. The available lever is "the
machine is never de-registered for an app-level reason": the in-memory `/health`
(#3062) and the kernel-served TCP check (#3063). If you see the zero-byte shape,
check `flyctl machine status` (`Checks [0/1]`) and correlate with `PR01` in
`flyctl logs` — do not look for it in the app's own 503s.

## 6.11 MCP auth-plane 503 — the `Retry-After` contract (#3144 / #3812)

**Symptom.** An MCP client's startup connect to `/mcp` fails and the whole
session runs with **zero** Tortoise tools. Pi's `mcp-client` connects eagerly at
startup with a 15 s budget and **no retry**, so a single 503 during org
resolution is a silent loss of the entire tool surface — the client-visible
impact #3144 records.

**Cause.** `OrgResolutionMiddleware` (`tortoise/mcp_auth.py`) resolves the
bearer token against the control plane (Supabase) or the registry, and re-runs
that resolution whenever its per-token cache entry is older than **60 s** —
which is exactly the first request after an idle period. When the lookup raises
(cold / unreachable dependency), the middleware answers a JSON-RPC `503`
`ERR_REGISTRY` … which carried **no `Retry-After`**. A well-behaved client had
no instruction to back off and could not distinguish a recoverable dependency
outage from a hard outage.

**What changed.** The auth-plane 503 now carries `Retry-After` (integer seconds,
per RFC 7231 §7.1.3) from `TORTOISE_MCP_AUTH_RETRY_AFTER` (default `5`, clamped
to `1..3600`). The contract is **executed**, not asserted against source text:
`tests/test_mcp_http.py::TestAuthRetryAfterContract` drives the real mounted MCP
app through warm resolution → cache aged past the 60 s TTL (the idle state) → a
cold re-resolve that fails → asserts the 503's parseable `Retry-After` in a sane
range → heals the dependency and asserts the retry, after exactly the advertised
delay, **resolves** (not a mocked acknowledgement). Removing the header turns
that test red (#3812).

**Still not app-fixable.** The zero-byte shape in §6.10 is generated by Fly's
proxy before the app sees the request — the app cannot attach a header to it.
This section is the **app-side** half of the same objective for the hosted
tenant surface: the auth-plane 503 a connecting MCP client can actually
receive is retryable. (`StaticKeyMiddleware` — self-host `auth_mode="static"`
— also answers a 503 with no `Retry-After`; that is a deliberate fail-closed
*configuration* error, not a retryable dependency outage, and it is outside the
hosted `/mcp` connect path this section covers.)

## 7. Out-of-band availability watchdog (#2850)

The 2026-09-10 outage (~19:10–19:55 UTC, ~45 min) took `https://api.premiselabs.co`
fully down for its whole duration and **nobody was paged**. Sentry could not see it:
Sentry runs *inside* the process, so a machine that is alive-but-not-serving
raises no exception and reports nothing — the Fly proxy simply stops routing
(`[PR01] no known healthy instances found for route tcp/443`) and every public
request hangs until it times out. There is exactly ONE machine, so its
de-registration from routing *is* a total outage. This watchdog is the missing
observer: it runs on GitHub Actions — a different failure domain than Fly —
on a `*/5 * * * *` cron **intent** (measured delivery is ~15 min; see §7.5a).

- **Workflow:** `.github/workflows/availability-watchdog.yml` (schedule `*/5 * * * *` + `workflow_dispatch`)
- **Logic + limits:** `.github/scripts/availability-watchdog.sh`
- **Harness (runs in CI job `availability-watchdog`):** `bash .github/scripts/availability-watchdog.test.sh`

**Two production targets (#3628).** The watchdog now drives TWO surfaces, as two
steps of the same job: the Fly API probe (§7.1a) and the Cloudflare Pages auth
surface probe (§7.1b). They alert **independently** (the auth step runs even if
the API step failed) and each files its **own** incident, keyed by its own host
label — the two never share an issue. Only the Fly API target is restartable;
the auth target is **hard-disarmed** from the restart leg (§7.4).

> **The `PROD DOWN` path has never fired; `PROD DEGRADED` fired once.**
> `gh issue list --state all --search '"[monitor] PROD DOWN" in:title'` returns
> `[]`, but `"[monitor] PROD DEGRADED"` matches **#3637** (2026-09-16T07:34:59Z,
> now closed) — the only production exercise of the alerting machinery. There is
> **no `[monitor] DRILL` incident at all**, so the drill path (§7.7) has not been
> exercised and is *not* the proven route the production incident is. That makes
> it all the more important that `is_prod` is a **set membership** test rather
> than a boolean flag: an unrecognised URL must stay a DRILL, so a misconfigured
> or newly-added target can never arm self-heal against an unexpected host (fail
> closed).

### 7.1 What the probe checks

#### 7.1a The Fly API target (default)

`GET https://api.premiselabs.co/v1/organizations` with **no auth** — the real user
path (an authenticated API route served by the app), not just an open socket.
Unauthenticated, that route must answer **`401`** (`Missing session token`) —
verified in source: `tortoise/session_auth.py::verify_session_jwt` raises 401
with zero network I/O when the `Authorization` header is absent.

**What that does and does not prove:** liveness, and that this route still
exists and is served. It does **not** exercise authenticated traffic — an
auth-leg break that rejects every real token (JWKS/JWT misconfiguration, mass
suspension) answers `401` and therefore reads UP.

| Verdict | HTTP seen | Meaning |
|---|---|---|
| **UP** | `2xx`, `401`, `403`, `429` | The app answered. `401` is the expected no-auth answer; `429` means the app answered and is throttling us |
| **DOWN** | `000` (timeout / connection error) or `5xx` | No answer — the outage class |
| **UNEXPECTED** | anything else (`3xx`, `404`, …) | It answered, but not as expected — usually a bad deploy or a moved route, **not** a wedge |

A generous per-request timeout (25 s) plus 3 attempts ~10 s apart must all fail
before the run declares DOWN, so a single transient blip cannot fire an alarm.

#### 7.1b The Pages auth target (#3628)

The second step probes `GET https://app.premiselabs.co/auth/start`
(the `tortoise-dashboard` Pages project — the SESSION-BEARING origin; #4054 moved
the BFF there, so the marketing origin answers **404 by design**).
It exists because of the **#3616 sign-in outage** (~35 min):
only `/auth/start` revealed it. The other candidate routes stayed GREEN the
whole time — this is the trap to remember when tempted to probe something
cheaper:

| Route | Status during #3616 | Reads as |
|---|---|---|
| `/auth/start` | **503** `session_store_unavailable` | **DOWN/DEGRADED — the only revealing route** |
| `/welcome` | 302 | UP (a bare liveness probe is blind) |
| `/api/session` (anon) | 401 `not_signed_in` | UP — its missing-cookie branch precedes the binding check (`functions/api/session.ts`) |
| `/auth` | 200 | UP |

The auth target's UP contract is **narrower and stronger** than the API's:

| | Value | Why |
|---|---|---|
| `PROBE_EXPECT_STATUS` | `302` | A healthy `/auth/start` is a redirect, not a 200. The watchdog's built-in arms classify 3xx as UNEXPECTED, so **without this allow-list a healthy site would page** — the #1 way to get this wrong |
| `PROBE_REQUIRE_HEADER` | `code_challenge_method=s256` | Proof the PKCE flow row was actually written to D1. A 302 **without** it is an *answered-but-wrong* (UNEXPECTED → `PROD DEGRADED`) verdict, not an outage — “the site is up but nobody can sign in”, the entire lesson of #3616 |
| `PROBE_HOST_LABEL` | `app.premiselabs.co` | The **session-bearing origin** the probe actually hits (the BFF moved here in #4054/#4104). The incident **title is built from this label** and the dedupe is an **exact-title match**, so it must agree with the host actually probed; renaming it **orphans incidents filed under the old title** (closed by hand, not auto-resolved). Two production targets must not share one label, or they fight over a single issue |

The allow-list replaces **only** the UP arms: `000`/`5xx` are checked **first**
and stay **DOWN** even if listed, and any other status stays **UNEXPECTED**. A
malformed allow-list therefore fails closed (a genuine outage still alerts,
never a silent disarm).

That 302 + `code_challenge_method=s256` contract is exactly what the deploy
gate already asserts (`tests/e2e/auth/test_bff_flow.py` — “expected redirect from
/auth/start” and “S256 only” in the `Location` header); the watchdog is the
**scheduled twin of the deploy gate**, pointed at the same tuple so the same
fault is caught after a deploy as well as during one (#3618 blocks deploying it,
this blocks living with it).

**Never restartable.** The auth surface has **no Fly machine** behind it; a `503`
there means a missing/renamed binding (D1/KV) or a Pages routing change, which
`flyctl machine restart` on the API app cannot repair and which would restart an
**unrelated service**. The auth URL is in the production set but **not** in the
restartable set; on a **DOWN** verdict the run log / incident body say
`disarmed:no_machine` explicitly (§7.4), while an answered-but-wrong
(**DEGRADED**) verdict logs `disarmed:unexpected` — the restart leg is off the
table either way.

### 7.2 How to read a failure

1. **The workflow run goes RED** — that is the alert (enable GitHub Actions
   notifications for this repo, or the failure is only visible in the UI).
2. **One GitHub issue** appears (or an existing one gets a comment):
   `[monitor] PROD DOWN — api.premiselabs.co is not answering the availability
   probe` (or `PROD DEGRADED` for the UNEXPECTED class), labelled `auto-filed`.
   The auth target files a **separate** incident with its own host in the title
   (`[monitor] PROD DOWN — app.premiselabs.co is not answering the
   availability probe`). An external page (Telegram) also fires on the
   transition when the paging secrets are set.
3. The issue **body** is machine-managed and carries the verdict, the first
   observation time, the failing-run count, the raw probe evidence, and the
   self-healing state. Read it first; add human notes as **comments**.
4. The **first** line of the body is a state block:
   `<!-- watchdog-state kind=down first_failure_ts=… down_runs=… last_down_ts=… last_comment_ts=… cap_notified_ts=… ledger_state=… ledger_src=… escalate_state=… escalate_ts=… page_ok_ts=… restarts=… -->`.
   It drives the cooldown/velocity limits — do not hand-edit it. `restarts=`
   records restart **attempts** (a failed attempt still counts). The sustained
   window is additionally clamped to the issue's GitHub-assigned `created_at`,
   so editing `first_failure_ts` can delay a restart but never make one happen
   earlier than `SUSTAINED_DOWN_MINUTES` after the issue was created. The
   watchdog only ever adopts/mutates an issue authored by the GitHub Actions
   bot (`author:app/github-actions`) whose title is an **exact** match and whose
   body carries the watchdog marker line; a look-alike from any other account —
   or another workflow's bot issue whose title merely contains the marker terms
   — is ignored and a fresh machine issue is filed (see §7.8).

**One incident = one issue.** Repeats comment with an incremented count; the
issue is closed automatically with a `Recovered` comment when a probe answers
again. (This is the fix for the #2706 duplicate-issue failure mode.)

### 7.3 Manual restart (when you do not want to wait for the watchdog)

```bash
export FLY_API_TOKEN=...            # same token as GitHub secret FLY_API_TOKEN
flyctl machine list -a tortoise-y4mjjq          # state + checks (0/1 = unhealthy)
flyctl machine restart <machine-id> -a tortoise-y4mjjq
flyctl logs -a tortoise-y4mjjq                  # what it was doing
```

A restart is a **symptom fix**: the app takes ~60–90 s to boot, and the wedge
recurs while the root cause is live (#2850: a FalkorDB socket timeout blocks
the event loop; #2953: uvicorn binds the socket only after lifespan startup).
The watchdog does **not** know you restarted anything: it keys off its own
ledger, so a manual restart during the 20-minute cooldown window can still be
followed by an automated one if the next probe lands while the app is booting.

### 7.4 Self-healing and its limits

When DOWN is confirmed for a sustained period the watchdog restarts the Fly
machine itself, strictly rate limited so a database outage cannot become an
infinite restart loop (AWS automated-remediation guidance: cap the remediation
velocity and involve a human when the cap is hit).

| Limit | Default | Behaviour |
|---|---|---|
| `SUSTAINED_DOWN_MINUTES` | 10 | No restart until the service has been continuously down this long (≈3 failing runs / 2 probe intervals at the 5-min cron *intent*) |
| `SUSTAINED_MIN_RUNS` | `max(2, ceil(SUSTAINED_DOWN_MINUTES / 5))` (2 at the wired 10-minute value) | At least this many failing runs must have been OBSERVED. Guards a stale/reopened incident whose stored clock is old from authorising a restart. Raising `SUSTAINED_DOWN_MINUTES` raises this too |
| `RESTART_COOLDOWN_MINUTES` | 20 | Minimum gap between automated restarts |
| `MAX_RESTARTS_PER_HOUR` | 2 | Rolling-hour cap. On the next failure the watchdog **stops restarting** and comments/pages asking for a human (paged at most every `CAP_RENOTIFY_MINUTES`, default 60 — the same text can still reappear in routine comments every `COMMENT_THROTTLE_MINUTES`). Set it to **`0` to disable automated restarts entirely** (an operator kill switch: alerting continues, nothing restarts) |
| `COMMENT_THROTTLE_MINUTES` | 15 | Routine “still down” comments are throttled; the body count still increments every run |
| `STALE_RESET_MINUTES` | 45 | If no failing run has been seen for this long, the incident is not continuous: the sustained WINDOW restarts (the restart ledger is **preserved**) |
| `CONTROL_URL` | `https://www.google.com/generate_204` | Runner-side egress control (see below) |

Override them in the `env:` block of `availability-watchdog.yml`. The watchdog
restarts **only**: (a) on a DOWN verdict — never on UNEXPECTED, where a restart
cannot help; (b) when `PROBE_URL` is a **restartable member of the production
set** — an unrecognised URL is a drill and a drill automatically disarms the
restart leg, and a production URL that is **not** restartable (the Pages auth
surface — no Fly machine behind it, so a restart of the API app cannot repair a
missing binding and would restart an unrelated service) is hard disarmed
regardless of the failure class (`disarmed:no_machine` on a DOWN verdict,
`disarmed:unexpected` on a DEGRADED one); (c) when the failure is
one a restart cannot fix — `classify_failure()` maps curl's exit code to a
class, and **DNS**
(6) and **TLS/certificate** (35, 51, 58–60, 66, 77, 80, 82–83, 90–91) failures
disarm the restart leg (`disarmed:unfixable`). The incident is still filed and
its body names the class and why nothing was restarted: restarting a machine
never repairs a resolver or an expired certificate, it only burns the budget.
Timeouts (28), connection-refused (7), and app-level 5xx stay restartable;
(d) when the previous incident's restart ledger cannot be read
(`disarmed:no_ledger` — fail closed, per above). Every run logs its verdict as
`restart decision: disarmed:<reason>` (or `restart decision: DOWN` on the armed
path); on the armed path the run also logs `restart outcome:
<go|wait_sustained|wait_runs|wait_cooldown|cap>`, which is the reason a restart
did **not** happen yet — so *why* a restart did not happen is in the run log
rather than inferred. (Both lines are needed: an armed-but-waiting run prints
`restart decision: DOWN`, so the mode line alone does not carry the reason.)
Three further safeguards worth knowing:

- **Write-then-act:** the attempt is recorded in the incident body *before*
  `flyctl` runs. If that write fails the restart does **not** happen — the body
  is the only cooldown/cap memory, so restarting without it could loop.
- **Attempts, not successes, are capped:** a failed `flyctl` call still counts
  against the hourly cap (the watchdog will not retry it on every run) and
  escalates to a human instead.
- **Runner-side egress control:** before ANY restart the watchdog probes
  `CONTROL_URL` (a known-good endpoint outside this app's failure domain). If
  that also fails, the verdict is **INCONCLUSIVE** — the incident is still
  recorded and alerted, but nothing is restarted, because a DNS/egress/proxy
  failure on the GitHub runner looks exactly like an app outage. No control
  probe, no disruptive action.
- **Write-then-act comments:** the body (including the comment-throttle stamp)
  is written BEFORE the comment, and the restart/escalation stamps before the
  restart/page. A failed state write therefore means **no restart, no page and
  no comment** (the run fails loudly) — that is what keeps a broken GitHub
  connection from turning into a notification loop.

### 7.5 Secrets

| Secret | Needed for | If missing |
|---|---|---|
| `FLY_API_TOKEN` | the automated restart of the **Fly API** target — **not** the Pages auth step, which is hard-disarmed regardless | **Already exists** (used by `deploy-hosted.yml`). If absent, the restart leg is skipped, the log says so, and the incident **body** (plus any comment that is not throttled away) names the secret — **alerting still works** |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | transition paging (**optional**) **and** the sustained-incident escalation leg (**required once an incident is sustained**) | A transition page is skipped with a log line. A **sustained** incident's escalation is **fail-closed**: the run FAILS with `escalation REQUIRED and NOT DELIVERED`, nothing is stamped as delivered, and the incident body records `escalate_state=failed` — a broken pager is never rendered as “all clear”. Both probe steps get them at STEP level |
| `ESCALATION_CHAT_ID` | **optional** recipient override for the sustained-escalation leg only (restart / failed-restart / cap / INCONCLUSIVE pages keep going to `TELEGRAM_CHAT_ID`) | Defaults to `TELEGRAM_CHAT_ID`, so point it at an on-call group without moving any existing page |

`GITHUB_TOKEN` is supplied by Actions and needs `issues: write` (granted in the
workflow). A missing `GH_TOKEN` fails the run before probing — a monitor that
cannot file is a deaf monitor.

### 7.5a The sustained-incident escalation leg (#3887)

**Why it exists.** Before #3887 every page was **transition-based**: one page
when an incident was filed, and the only post-run-1 pages that **asked a human
to act** lived INSIDE the restart leg (`cap` / `heal_failed` / `no_egress`; the
`✅ RECOVERED` page is post-run-1 but ends the incident rather than asking for
anything). A sustained *answered-wrongly*
(`UNEXPECTED`) incident — the class a restart correctly declines — therefore
reached a human **once, then never again**. On 2026-09-16 `GET /v1/organizations`
answered 404 for **11 h 19 m**; one issue was filed, one page went out, and the
loss ended only when an unrelated deploy landed.

**What it does now.** When an incident (either verdict) has been failing for
`ESCALATE_SUSTAINED_MINUTES` **AND** for `ESCALATE_MIN_RUNS` observed failing
runs — **both** required — the watchdog pages a human, then reminds at most once
per `CAP_RENOTIFY_MINUTES` while it stays failing.

| Knob | Derived default | Meaning |
|---|---|---|
| `ESCALATE_ENABLED` | `1` | `0` is an operator **kill switch**: no sustained page, no stamp, logged loudly and stated in the incident body (a kill, never an “all clear”). **Wired in CI** — both probe steps read the repository variable `ESCALATE_ENABLED` (step-level, `vars.ESCALATE_ENABLED`), so the switch is usable *without editing the workflow*; an unset variable expands to empty, which is the enabled default. Only a **byte-exact** `0` disables escalation: a padded value (`" 0"`, `"0\n"`, a YAML block/folded scalar) warns and **keeps paging** |
| `ESCALATE_SUSTAINED_MINUTES` | `3 × SUSTAINED_DOWN_MINUTES` = **30** | Wall-clock floor for the SUSTAINED page. Derived from the (normalized) sustained pair, and **clamped UP to `SUSTAINED_DOWN_MINUTES`** if set below it: the human page must never fire before the automated action it escalates. (The clamp floor is the restart gate, not the derived 30 — an explicit value in `[SUSTAINED_DOWN_MINUTES, 30)` is honoured). **Not wired in CI** — a script default; to override it (or `ESCALATE_MIN_RUNS`) in CI you must add it to the probe step's `env:` (same caveat as `.env.example`) |
| `ESCALATE_MIN_RUNS` | `SUSTAINED_MIN_RUNS + 1` = **3** | Observed failing runs. One bad probe satisfies neither leg. **Not wired in CI** — a script default, overridable only by adding it to the probe step's `env:` |
| `CAP_RENOTIFY_MINUTES` (shared) | `60` | For this leg: the minimum gap between a **confirmed** human page and the next sustained page, **and** the reminder interval while an incident stays failing — so this leg cannot re-page inside a window a confirmed page already covered. **One-directional, and not “of any kind”:** the pre-existing `cap` / `INCONCLUSIVE` re-notify gates still throttle on their own `cap_notified_ts` **attempt** stamp, which a failed send also consumes — that divergence is #4575 |

**Recipient.** `ESCALATION_CHAT_ID` (default `TELEGRAM_CHAT_ID`) is the
SUSTAINED leg's recipient **only**. The pre-existing restart / failed-restart /
velocity-cap / INCONCLUSIVE pages keep going to `TELEGRAM_CHAT_ID`, so an
operator can point sustained pages at an on-call group **without moving any
existing page**. The public body and the run log carry the recipient **kind**
(`telegram-default` / `telegram-override`), never the id.

**Cadence, deliberately.** The probe's cron *intent* is 5 minutes, but its
**measured** delivery is ~96 runs/day — one run per ~**15 min** (see
`.github/scripts/availability-record.sh`). Three observed failing runs is
therefore ~2 probe intervals ≈ **30 min** at the measured cadence, so the run
leg and the 30-minute floor **bind together at ~30 min**; the range only extends
past that when runs are spaced slower than ~15 min (up to ~45 min at one run per
~22 min). Either way the first page lands **~30 min** into an 11-hour incident,
and over 11 h 19 m a recipient gets roughly **11–12** pages, not 135 — the
reminder is a **state**, not a per-run event.

**Fail-closed delivery.** A page is recorded as delivered only when the
HTTP request succeeded **and** Telegram's own `ok` field is `true` — the same
contract `tortoise/telegram_push.py` enforces. A 2xx with `ok:false` (a bad chat
id, a bot removed from the chat) is **not** delivery. On an undelivered
escalation the watchdog stamps nothing as delivered, records
`escalate_state=failed` in the body, fails the run naming the channel, and
**retries on the next run** — a failed page delivers nothing, so retrying is not
a page storm, and silence is the one outcome a pager must never produce.

**Wall-clock anchor (never muted, never zeroed).** The escalation window's
start is the incident's **server-side `created_at`**, *not* the body's
`first_failure_ts` — those two differ exactly when it matters. `first_failure_ts`
is resettable and forgeable in ways `created_at` is not:

- the **stale-clock reset** (no failing run within `STALE_RESET_MINUTES`,
default 45) sets it to *now*, which would make a sustained incident observed on
a **>45-minute cadence** unreachable by the wall-clock leg **forever** — the
exact silent-forever incident #3887 exists to remove; and
- a hand-edited body can set it in the **future**, which would mute the leg for
as long as the clock is believed.

Both are therefore ignored by the escalation leg: `created_at` is authoritative,
and a **future** anchor (of either kind) is treated as untrustworthy and
**defers to the `ESCALATE_MIN_RUNS` run leg** rather than silencing the pager.
The same holds when `created_at` itself is **unusable** (no unforgeable start):
the wall-clock leg is likewise deferred to the run leg, and the page carries
**no age** rather than a 1970-derived one.
This changes the escalation leg only — the restart leg still uses the
resettable `first_failure_ts` and still stale-resets it, so an old or reopened
incident cannot authorise a restart before `SUSTAINED_DOWN_MINUTES` of observed
failure. The **run leg** is what stops the new anchor from paging a long-lived
incident on its *first* observed failing tick: both legs stay required.

**Two deliberate non-adoptions**, both stated so a later reader does not
tidy them away:

- **OVERRIDES: PagerDuty's “acknowledgment pauses further notifications.”**
  Not adopted: the incident body is on a **public** repo and this watchdog's own
  threat model treats it as human-editable, so an ack field would be a
  fail-**open** mute on the pager. A bounded reminder interval is used instead.
- **No acknowledgment field, and no independent heartbeat yet.** A heartbeat /
  dead-man's-switch for the pager's own liveness (the canonical fail-closed
  construction) is a different failure surface and is tracked separately in
  tortoise **#4573**.

### 7.6 When restarts do not help

The watchdog stops after `MAX_RESTARTS_PER_HOUR` and asks for a human — treat
that as “this is not a wedged process”. Four disarm reasons also land here
without the cap being reached, and all are named in the incident body and the
run log: **`disarmed:no_machine`** (a production surface with no Fly machine —
the Pages auth target on a **DOWN** verdict: repair the binding/route, there is
nothing to restart; the same target logs `disarmed:unexpected` on a DEGRADED
verdict, because a restart is off the table before the no-machine guard is even
reached),
**`disarmed:unfixable`** (a DNS or TLS/certificate failure — repair the
resolver or the certificate; a restart is not the fix), **`disarmed:no_ledger`**
(the prior incident's restart ledger could not be read), and
**`disarmed:corrupt_ledger`** (the ledger was read but not fully parseable —
fix `restarts=` in the issue the message names). The two ledger disarms fail
closed: the restart leg cannot prove the hourly budget, so nothing restarts,
and the fail-closed verdict is **durable**: it is persisted in the incident's
machine-readable state block, so the next run cannot silently arm with an
empty budget. The watchdog re-reads the named source issue on every subsequent
run and resumes automatically once its `restarts=` field parses again
(`disarmed:no_ledger` retries the previous-incident lookup instead — a source
that is found and valid is adopted, and a successful lookup that finds no other
incident means the budget really is empty):

1. `flyctl logs -a tortoise-y4mjjq` — look for `Timeout reading from socket`,
   `Failed to create index`, or a crash loop.
2. Check **FalkorDB Cloud** reachability itself (a dead/slow DB is the #2850
   root cause; the app cannot serve while the DB stalls).
3. Check what was **deployed** — a bad release can 5xx without the process
   being wedged; roll back with `fly deploy --image $(fly releases -a tortoise-y4mjjq --json | jq -r '.[1].ImageRef') -a tortoise-y4mjjq`.
4. If restarts are actively harmful (e.g. they lengthen the outage), use a real
   lever — a `probe_url` drill only disarms **that one run**, and the next
   scheduled run probes production again:
   - **Primary lever — stop restarts, keep alerting:** put
     `MAX_RESTARTS_PER_HOUR: '0'` in the workflow's `env:` via a PR/merge (the
     kill switch). It survives runs and leaves alerting intact.
   - **Stop the whole monitor:** `gh workflow disable availability-watchdog`
     (also stops alerting — you are now the monitor), then re-enable it.
   - **Do NOT delete the `FLY_API_TOKEN` secret** as a lever: `deploy-hosted.yml`
     requires it fail-closed (#1896), so the next deploy or rollback is blocked
     until it is re-set. (Removing it *does* disable the restart leg, but at the
     cost of the deploy pipeline.)
5. **This watchdog does not fix availability.** It is an observer plus a
   bounded restart. The durable fixes are #2850 (do not wedge on a DB stall)
   and #2953 (bind the socket before the startup DB sweep). Note also that the
   app runs on a **single machine** (see §7.8), so there is no failover to
   absorb a restart.

### 7.7 Drilling the watchdog

```bash
gh workflow run availability-watchdog -f probe_url=https://api.premiselabs.co/no-such-route
```

A non-production `probe_url` is a **drill**, and a drill gets its **own incident
identity** — it files/comments `[monitor] DRILL DOWN — <host> [DRILL]` (or
`DRILL DEGRADED`) with the `auto-filed` label, and it can never restart
production, mutate a production incident's state, or close one. Close the drill
issue afterwards.

Expect: a RED run and a `DRILL DEGRADED` issue — *filed* if none is open,
otherwise a **comment** (the first repeat comments immediately; further repeats
inside `COMMENT_THROTTLE_MINUTES` only bump the body count). A subsequent drill
run that reads **UP** comments `Recovered` on and closes the **drill** incident
(never a production one) — so drills do not accumulate, but do close the one
you opened.

To check the *paging* path, set the repo secrets (`gh secret set
TELEGRAM_BOT_TOKEN`) — a dispatched workflow uses the repository secrets, not
your shell environment. `gh workflow run` cannot pass them inline.

**The `PROD DOWN` path has never fired; `PROD DEGRADED` fired once (#3637,
2026-09-16T07:34:59Z)** — `gh issue list --state all --search '"[monitor] PROD
DOWN" in:title'` returns `[]`, and **no `[monitor] DRILL` incident exists**, so
the production DEGRADED incident is the only *proven* exercise of the alerting
machinery (§7, §7.7). Two consequences:

1. **Drill the drill before trusting a change.** A reverted/mis-wired
   `is_prod` set would make every production run a silent drill (no PROD page),
   and nothing in the live path would tell you — the drill is where you notice.
2. **Widening the production set must not let a drill arm self-heal.** The set
   (`PROD_PROBE_URLS`) and the restartable set (`RESTARTABLE_PROBE_URLS`) are
   separate; only exact members of the production set are PROD, and only exact
   members of the restartable set may restart. An unrecognised URL stays a
   DRILL with self-heal disarmed — never add a flag, never loosen this to a
   prefix/wildcard match, and never let a drill URL appear in either set.

**Both targets are probed on every run**, drills included: the auth step always
uses its production URL and is hard-disarmed from the restart leg. Note the auth
probe is **not** side-effect-free — asserting the PKCE header means the GET must
reach `/auth/start`, which **writes an `auth_flows` row** (one per run; expired
rows are currently not garbage-collected — tracked in #3647). A drill therefore
exercises the API drill path while still alerting on a genuinely-down auth
surface.

### 7.8 Known limits

- **Two probes, still narrow.** The API probe checks ONE route
  (`/v1/organizations`) and only its no-auth branch; the auth probe checks ONE
  route (`/auth/start`) and asserts 302 + the PKCE header. An outage that leaves
  either route answering as expected while other routes fail reads as UP
  (green) — and so does an auth-leg break that rejects every *real* token (the
  probe sends none). Together they cover the two paths where a silent outage is
  worst, not the whole surface.
- **A *total* runner-side network failure is INCONCLUSIVE, not DOWN** (the
  `CONTROL_URL` check). Alerting still fires; no restart is issued. The
  INCONCLUSIVE page is throttled to at most once per `CAP_RENOTIFY_MINUTES`,
  measured from its own **attempt** stamp — so an *undelivered* inconclusive
  page also consumes the window (pre-existing; #4575) — and the per-run record is
  the incident **body** plus the RED workflow run, so "no page this run" does not
  mean "no alert". Note the control can only detect a TOTAL egress failure: a
  failure affecting only the probe's own host (its DNS zone, a Cloudflare/ASN
  block on the runner IP) leaves the control green and still reads as DOWN.
- **A sustained incident whose STATE CANNOT BE WRITTEN gets no escalation page.**
  The escalation leg's idempotency stamp lives in the incident body, so on a
  path that deliberately refuses to rewrite that body — the corrupt-`restarts=`
  refusal, which refuses because rewriting would erase the ledger it cannot
  trust, plus the `search`/body-read/create/body-write failure exits — the leg
  cannot run: without a durable stamp a send would repeat on every run. Those
  paths already end in a loud `fail` (and, on a first occurrence, a body
  explaining the refusal), and the corrupt-ledger message now says explicitly
  that **no escalation page was sent and why**, so the gap is named rather than
  silent. Independent liveness for the pager itself is tortoise **#4573**.
- **A sustained incident observed through a FLAP gets no escalation page.**
  When a probe answers UP but the recovery-confirmation probe fails while an
  incident is already open, the run leaves the incident open and exits GREEN
  *before* the escalation leg, so no state advances and nothing is paged. That
  early exit is deliberate (the open incident is the standing alert, and
  changing when a flap counts as still-failing is a behavioural change with its
  own design), so the run **names the gap** instead: it logs a warning saying
  the escalation leg is not reached and **no escalation page is sent this
  run**, so “no page” is never read as “nothing to page about”.
- **This leg covers the PAGER, not the MONITOR.** If the workflow is disabled,
  the schedule is dropped, or the job never reaches the failing path, no
  escalation can fire and there is no run log to read — “the pager is dead” then
  looks exactly like “all clear”. That is the dead-man's-switch surface, not
  this one: tortoise **#4573**.
- **The dedupe search is a loose `in:title` term match**, not an exact phrase,
  **and an adopted item must clear three checks**: the search is constrained to
  `author:app/github-actions`; the returned item's `user.login` must be the
  RESERVED `github-actions[bot]` (not merely `user.type == "Bot"`, which also
  admits `renovate[bot]`/`dependabot[bot]`); its title must be an EXACT match;
  and its body must carry the watchdog's own marker line
  (`<!-- availability-watchdog-state -->`), which no other producer emits. On a
  public repo an unrelated account — or another workflow's bot issue whose title
  merely contains the marker's terms — is **never** adopted, patched, commented
  on or closed: the watchdog treats it as "no incident" and files its own fresh
  issue. Because the author is GitHub-assigned and cannot be chosen or forged by
  the issue creator, a look-alike filed by any other account is never a match.
- **Truth is derived from the incident body**, which is human-editable, **plus
  a server-side anchor the body cannot forge.** The sustained window is clamped
  to the incident issue's GitHub-assigned `created_at`, so a hand-edited
  `first_failure_ts` (or a missing/`0` `last_down_ts`, which now trips the
  stale-clock reset instead of being read as "just now") can **delay** a
  restart, but cannot authorise one before the issue has actually existed for
  `SUSTAINED_DOWN_MINUTES`. Scalars and the restart ledger are sanitised, and a
  stale clock (no failing run within `STALE_RESET_MINUTES`, default 45)
  restarts the sustained window **without** clearing the restart ledger. The
  **escalation** leg does not rest on that resettable clock: its wall-clock
  anchor is the same server-side `created_at`, and a **future** `first_failure_ts`
  is untrustworthy and defers to the `ESCALATE_MIN_RUNS` run leg — so neither a
  stale reset (a >45-min-cadence incident) nor a forged future stamp can zero or
  mute the pager (§7.5a).
- **The cooldown and the hourly cap are still read from the body's
  `restarts=` ledger**, so their integrity rests on the bot-only write access
  to the incident issue — a human edit to `restarts=` can weaken them. That is
  why the ledger is **fail-closed**: a `restarts=` value that is present but
  not fully parseable (`abc`, a >12-digit stamp) makes the run refuse to
  restart outright rather than silently dropping the unreadable entry. Prefer
  the `MAX_RESTARTS_PER_HOUR=0` kill switch over editing the ledger.
- **A failed state write is fatal** (the run fails) because the body is the
  only cooldown/cap memory — expect a RED run whose log says the state write
  failed, with no restart. The same applies to a failed body READ (never
  treated as “no state”, which would erase the ledger) and to the cap's
  escalation stamp (no page is sent until the stamp is durable).
- **A DOWN↔DEGRADED flip can leave two open incidents** (one of each kind) for
  a single outage; recovery closes both. Rare, but do not assume the second
  issue is a new outage.
- **Flapping** (UP→DOWN→UP within minutes) keeps an open incident open
  (recovery needs a confirmation probe) but does churn comments. That
  unconfirmed-recovery run exits **GREEN** — the open incident, not the run
  colour, is the standing alert, so do not read a green run as all-clear while
  an incident is open. The sustained
  clock is **preserved** while the incident stays open (it is only reset when
  the gap since the last failing run exceeds `STALE_RESET_MINUTES`), so a flap
  does not delay self-healing; a flap that recovers long enough to close the
  incident starts a fresh sustained clock, but the restart budget is **shared
  across incidents** and does not reset (next bullet). If a flap is seen with
  NO incident open, the watchdog files one for the observed failure rather than
  reporting a green run.
- **The restart ledger is cross-incident.** Recovery closes the incident, so a
  naive per-incident budget would let a flapping service (recovers ≥1 probe,
  then fails ≥10 min again) draw a fresh `MAX_RESTARTS_PER_HOUR` allowance every
  cycle, exceeding the cap *globally* while never exceeding it within one
  incident. Instead, when a new incident is filed the watchdog **seeds its
  ledger from the still-in-window stamps** of the most recent machine-authored
  incident — open *or* closed — via `recent_restart_ledger()`. The rolling-hour
  cap therefore holds across incident boundaries. If that prior ledger cannot
  be read, the restart leg is **disarmed** (`disarmed:no_ledger`) rather than
  restarting without a provable budget; alerting is unaffected. The disarm is
  **durable**: a sentinel (`ledger_state=`/`ledger_src=`) is written into the
  incident's state block, so the next run keeps failing closed instead of
  adopting the freshly-opened incident with an empty ledger (which would erase
  the cap stamps the prior incident carried). Each run retries the lookup and
  clears the sentinel only when the source ledger parses again.
- **GitHub scheduled workflows can be delayed** under platform load, and GitHub
  **disables** schedules after ~60 days of repo inactivity — a missing run looks
  like silence. After any long quiet period, dispatch the workflow once to
  confirm the schedule is still enabled — but note that a **blank or production
  `probe_url` is a full PRODUCTION run with self-healing ARMED**: it re-probes
  production and can restart it. Only a non-production value is a drill. To
  check the schedule without touching production, first set `MAX_RESTARTS_PER_HOUR: '0'`
  (or accept that a down service may be restarted).
- If a human closes the incident issue mid-outage, the next run files a fresh
  issue and the sustained/velocity clock restarts (documented, accepted).
- The dedupe search API is eventually consistent; a ~15-min cadence makes
  that immaterial.
- Only one machine exists, so any restart is a (multi-minute) outage by itself
  — there is no failover. A restart is therefore always the *last* automated
  resort, gated on a trustworthy verdict (see the egress control).

## 8. Deploy Gates — the `SKIP_*` bypass convention and the Fly secret-provenance gate (#4126)

`deploy-hosted.yml` runs a set of **fail-closed deploy gates**: migration drift
(#1095), Fly machine orphan/crash-loop (#1896), Fly secret provenance (#4126),
pack-catalog smoke (#1929), and post-release DB health (#1719 — since #4538 it
runs in its own `post-deploy-verify` job and does not colour the deploy job; its
phase contract — the weaker predicate informs, the strongest decides — is §8.5).
Some can be bypassed for an incident-fix deploy — and **a bypass is an
incident-window state, not a setting.** **Not every gate is bypassable:** the
migration-drift gate has no `if:` guard and no `SKIP_` lane by design (the #1001
P0 recurred while a migration was missing from prod, so a missing token or an
error must fail the deploy).

### 8.1 The Fly secret-provenance gate (#4126)

**What it checks.** Every name returned by `flyctl secrets list -a tortoise-y4mjjq`
must have a declared *managing source* in version control. A name that exists
only on Fly is drift by construction: it survives every deploy unversioned,
nobody can rotate it from GitHub, and CI cannot see it. This gate exists because
that exact state shipped an outage: `TORTOISE_SESSION_LLM_MODEL` and
`OPENROUTER_API_KEY` were hand-set on Fly, the deployed key 403'd on every
extraction call, and production answered `200` with `extracted: 0` for 50/50
sessions — invisible to every other gate, because a name no file mentions cannot
be compared against anything.

- **Declared inventory (the contract):** `.github/scripts/fly-managed-secrets.txt`
  — every Fly secret, with its managing source, the entry format, and each
  token's constraints. **That file is authoritative for the grammar; the token
  table below is a one-line orientation only, not the contract.**
- **Checker:** `.github/scripts/check-fly-secret-drift.py` — in production it
  reads the live list via `flyctl secrets list --app <app> --json` (the
  `FLY_SECRETS_FILE` seam is what makes the test suite hermetic; tests:
  `tests/test_fly_secret_drift.py`).
- **Workflow step:** `Check Fly secret provenance (fail-closed)`. It runs before
  the migration-drift gate so its output is visible on every deploy attempt, not
  only on one that gets as far as Fly.

The source tokens are:

| Token | Meaning |
|---|---|
| `gh-secret:<GH_NAME>` | propagated by the workflow from the GitHub Actions secret `<GH_NAME>` (not always the same name — e.g. `GITHUB_CLIENT_ID` ← `GH_CLIENT_ID`) |
| `workflow` | set by the workflow from non-secret context (`${GITHUB_SHA}`, a composed feature flag) |
| `fly-toml-env` | applied from `fly.toml` `[env]` — versioned, and the deploy applies it |
| `fly-only:<issue-ref>` | a **deliberately** out-of-band secret; the named issue carries the recorded decision (§8.3) |

A bare `unmanaged` entry — present on Fly with no declared source — **FAILS the
gate**. It names the #4126 defect precisely, not debt to be recorded.

**Fail-closed, and the two exit classes.** Exit 1 = undeclared or stale
declarations (the actionable incident-time class). Exit 2 = the gate *could not
determine state* (missing/empty secret list, unparsable manifest, PyYAML
provisioning failure, an unreadable `fly-only:` ref) — **exit 2 can NEVER be
bypassed** and always blocks the deploy. A `gh-secret:X` declaration also needs
its matching probe line in the workflow's `GH_SECRETS_PRESENT` block: no CI
token can list repository secrets, so the run states which ones it carries, and
a forgotten line FAILS the deploy for that name rather than passing it.

### 8.2 The `SKIP_*` bypass convention

Every bypassable gate has **two lanes**: a `workflow_dispatch` input and a repo
variable with a `SKIP_` prefix. On a **push-triggered** run the `inputs` context
is null, so the **repo variable is the only lane** — which is why the incident
procedure sets the variable.

| Repo variable | Dispatch input | Gate |
|---|---|---|
| `SKIP_DB_HEALTH_GATE` | `skip-db-health-gate` | post-release DB health verification (#1719) |
| `SKIP_PACK_SMOKE` | `skip-pack-smoke` | pack-catalog smoke (#1929) |
| `SKIP_FLY_MACHINES_GUARD` | `skip-fly-machines-guard` | Fly machine orphan/crash-loop guard (#1896) |
| `SKIP_FLY_SECRET_PROVENANCE` | `skip-fly-secret-provenance` | Fly secret provenance (§8.1, #4126) |

Rules that hold for every one of them:

- **A bypass is never silent — and since #4759 it is visible in the run
  SUMMARY, not only in a step log.** Every bypass renders through ONE reporter
  (`.github/scripts/deploy-bypass.sh report`): it keeps the `::warning::` and
  appends a uniform block to `$GITHUB_STEP_SUMMARY` naming the gate, the lane
  that fired, and `BYPASSED`. Each deploy job also ENDS with a `Deploy gate
  audit` step (`if: always()`), so a green run states every gate in that job as
  bypassed or not — the absence of a bypass block is never the reader's only
  evidence. Before #4759 the four warnings were also inconsistent (two dedicated
  steps, two inline `echo … >&2`) and the only trace was inside a step log: a
  bypassed gate and a passed gate looked the same in the summary.
- **Set the variable for the incident window and CLEAR IT AFTER.** A bypass left
  set means that gate guards no deploy — check it first when a gate seems never
  to fire.
- **A persistent bypass is DATED, and a machine checks the date.** Setting a
  `SKIP_*` variable also sets a companion `SKIP_<VAR>_SET_AT` = `YYYY-MM-DD`
  (UTC) — the window start. `.github/workflows/skip-bypass-expiry.yml` runs
  daily, ages every set lane against it, and goes **RED** past the window
  (`WINDOW_DAYS_DEFAULT = 7` in `deploy-bypass.sh` — a week never nags during a
  real incident, while a bypass that outlives one working week is stale by any
  reading). A set lane with **no usable start date is a violation too**, so
  forgetting the date fails LOUD rather than falling back to the prose-only
  window #4605 died of. The same age is called out `OVERDUE` / `NOT RECORDED` in
  the summary of every deploy.

  **This is a reminder, never a deploy block.** `skip-bypass-expiry` is a
  separate scheduled workflow with no path to `deploy-hosted.yml`; blocking
  would strand the very incident-fix deploy the bypass exists for. Filing no
  issue is deliberate: the red run is the reminder (GitHub notifies the
  schedule's author) and the noisy alternative — a second bot-issue producer in
  a public repo, with its own dedupe and forgery guards — buys nothing the red
  run and the deploy-time `OVERDUE` callout do not already say.
- **How much a bypass skips depends on the gate's shape.** The two guards whose
  wrapper translates the checker's exit code — provenance (#4126) and machines
  (#1896) — are bypassed for **exit 1 only** (undeclared/stale declarations,
  fleet violations); their **exit 2** (could not determine state) can **never**
  be bypassed and always blocks the deploy. The other two are a plain
  step/job-level `if:` — `SKIP_PACK_SMOKE` skips the whole packaging-smoke job,
  and `SKIP_DB_HEALTH_GATE` skips the whole health step — so nothing in it
  runs, and the bypass is not exit-class-limited.

**A bypass is never the committed default.** Every `skip-*` dispatch input
defaults to the non-bypass value, so clearing the repo variable re-arms the gate
on a dispatch run for every gate in the table — the concrete defaults live in
`.github/workflows/deploy-hosted.yml` (`workflow_dispatch.inputs`) and are not
restated here. That was *not* true between #1719 and the #4605 re-arm:
`skip-db-health-gate.default` was `'true'` during the RC3 restore window (when
`db.ok=false` was the live prod state), so clearing the variable alone left the
health verification skipped and the input also had to be passed as `false`.
That default was an incident-window mitigation with its own exit condition —
re-arm once the data plane is healthy — and #4605 re-armed it on 2026-09-22
after sampling `/health` showed `db.ok=true` on every completed response.
(`#4538` moved the check into its own job; it did not change this default.) If
the data plane is unhealthy again, bypass **per run** via the input or **per
window** via the variable — the committed default stays `false`.

```bash
gh variable list                                             # what is currently bypassed
gh variable set    SKIP_FLY_SECRET_PROVENANCE      --body true          # during the incident
gh variable set    SKIP_FLY_SECRET_PROVENANCE_SET_AT --body 2026-09-22  # the window start (#4759)
gh variable delete SKIP_FLY_SECRET_PROVENANCE                        # after — REQUIRED
gh variable delete SKIP_FLY_SECRET_PROVENANCE_SET_AT                 # after — REQUIRED
```

The `_SET_AT` date is the window start, never a switch: no gate's lane condition
reads it (`SKIP_<VAR>_SET_AT` cannot bypass anything), and `deploy-hosted.yml`
only binds it into the report step's `env:` to be stated in the summary. Per-run
bypasses via the dispatch input need no date — nothing is left set.

**Why the expiry check reads `vars`, not `gh variable list --json updatedAt`.**
The repository-variables endpoint requires the fine-grained "Variables"
permission, which the workflow `GITHUB_TOKEN` does not carry, and the `vars`
context exposes a variable's VALUE but not its `updatedAt` — so the `updatedAt`
route cannot be a machine check on a scheduled run without a separate
credential (an infra blocker, recorded here rather than silently dropped). The
dated companion variable is read through the ordinary `vars` context, needs no
new scope, and works on the push lane where `inputs` is null. The trade — one
more variable to set — is bounded by the fail-closed rule above: forgetting it
is a violation, not silence.

### 8.3 Why a name can be deliberately Fly-only (#661) — do NOT "tidy" it into a GitHub secret

`REGISTRY_STREAM_KEY` is declared `fly-only:#661` in the manifest, and #661 is a
**closed recorded decision**: the key must be *never present in GitHub*
(operator out-of-band) so registry content confidentiality does not inherit the
GitHub trust boundary — with its own E2E, "a GH workflow cannot decrypt a
registry archive with the Fly-only key". Its `OVERRIDES:` marker is on #661.

Moving it into a GitHub Actions secret **reverses that security decision**;
retiring it breaks registry streaming. If you believe the decision is wrong, the
route is to **reopen #661** and argue the evidence there — not to drop the
`fly-only:` line. A new `fly-only:` entry needs an owner decision, not a
convenience spelling; a bare `unmanaged` entry is not the same thing. Rotation
stays out-of-band: `tools/rotate-backup-keys.py --role registry_stream`.

### 8.4 When the gate fails, where to look

1. The failing run's `::error::` names the exact Fly variable and, for a
   `gh-secret:` declaration, the GitHub secret it expected.
2. Read the entry (or the missing entry) in
   `.github/scripts/fly-managed-secrets.txt` — its header contract states what
   each source token requires.
3. Inspect the live state:
   ```bash
   fly secrets list -a tortoise-y4mjjq
   ```
4. Resolve it by **declaring the real source**: add the propagation line to the
   workflow plus the matching probe line (§8.1), or record the value in
   `fly.toml [env]` and unset the Fly secret — **deploy the `[env]` entry first**,
   because a Fly secret SHADOWS `[env]`.
5. Only if that is impossible **during an incident** (e.g. an operator must
   hand-set a secret mid-incident) may you set
   `SKIP_FLY_SECRET_PROVENANCE=true` for the window — clear it afterwards, and
   declare the secret anyway.

**Rotation:** for a `gh-secret:` name, rotating the GitHub secret is the only
step — the next deploy propagates it.

### 8.5 The post-release DB health gate's phases — the weaker predicate informs, the strongest decides (#4771)

The `post-deploy-verify` job runs `.github/scripts/deploy-health-gate.sh` after
the release. It has **three phases**, and their roles differ deliberately:

| phase | predicate | on failure |
|---|---|---|
| 1 | app reachable (5 quick probes) | **exit 1** — a dead app is a deploy failure, not a DB wait |
| 2 | `db.ok` — the FalkorDB data plane **alone** | `::warning::` only — the run **PROCEEDS** |
| 3 | `/health/ready` — `AND(Supabase control plane, FalkorDB data plane)` | **exit 1** |

**Phase 3 is the only phase that decides the run.** Phase 2 stays because it is
the faster, more specific observation — it *names* FalkorDB — but it is the
**weaker** predicate and no longer gets to decide. The two phases poll
**independent** probes with different budgets: `db.ok` is served from the
background liveness refresher (`_HEALTH_PROBE`), while readiness runs its own
coordinator, `_READY_PROBE`, at request time (`tortoise/hosted_api.py::health_ready`).
So they can legitimately disagree — observed in production on 2026-09-22:
`/health` reported `db.ok=false` on a 1.5 s probe timeout while `/health/ready`
answered `200` on **both** planes.

**This is not a weakening.** Readiness ANDs the *same* FalkorDB data plane (its
probe calls the same `_probe_db()`), so a genuinely unreachable FalkorDB fails
phase 3 too and the run still exits 1. What changed is only that the weaker
observation can no longer decide on its own. **Do not "restore" phase 2's
`exit 1`** — that is the #4771 defect (the #4545 invariant violated at the
decision level, after #4545 had fixed it at the assertion level). The harness
`.github/scripts/deploy-health-gate.test.sh` pins both halves: `db.ok` never true
+ readiness 200 → pass, and `db.ok` never true + readiness never 200 → fail.

**OVERRIDES:** the general expectation that a deploy gate should fail on **any**
unhealthy subsystem — here the weaker `db.ok` observation *informs* and the
strongest observed predicate (`/health/ready`, an AND of both planes) *decides*,
because two independent probes on different budgets can disagree and only the
stronger one is evidence that the release is actually unready.

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
- [ ] Live `POST /v1/sessions` smoke returns 200 + `extraction_mode: "llm"` (a keyless `"no-provider"`, or `"extraction-disabled"` for a team with extraction off, means turns were stored but extraction was skipped)
- [ ] `fly.toml` declares `auto_stop_machines` / `auto_start_machines` / `min_machines_running` explicitly (no implicit platform defaults) and `fly config show` matches (§6.2)
- [ ] Every machine has its own volume (`fly volumes list` count == `fly machines list` count) — a machine sharing `tortoise_api_data` is impossible and must never be attempted (§6.3)
- [ ] Routing check is `[[services.tcp_checks]]` (kernel-served: **not starved by event-loop/thread-pool scheduling** — it can still fail if the accept backlog saturates) and no `[[services.http_checks]]` entry remains (§6.4)
- [ ] **Loop-liveness precondition satisfied:** the top-level `[checks.loop_liveness]` check (#3447) shipped only after #3062 was deployed and the 9090 port was verified listening **on `0.0.0.0`** — `fly ssh console -a tortoise-y4mjjq -C "grep ':2382' /proc/net/tcp"` showed local address `00000000:2382` (not the loopback-only `0100007F:2382`), and a token-less `/healthz` returned 200 (verified 2026-09-17) (§6.0, §6.4)
- [ ] Top-level `[checks.loop_liveness]` targets port 9090 / path `/healthz`; the listener (`monitoring.start_health_listener`, #3062) binds `0.0.0.0` and `/healthz` returns 503 only for STALE **AND** IDLE, else 200 (§6.4, §6.9)
- [ ] A deliberately failing second `[[services]]` check does **not** de-register the primary service (§6.9 — open until observed)
