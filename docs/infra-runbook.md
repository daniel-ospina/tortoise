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

**Epic:** #7711 (legacy provisioning epic — provenance) · availability watchdog: #2850
**Last updated:** 2026-09-11 (#2850 availability watchdog)

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

## 6. Out-of-band availability watchdog (#2850)

The 2026-09-10 outage (~19:10–19:55 UTC, ~45 min) took `https://api.premiselabs.co`
fully down for its whole duration and **nobody was paged**. Sentry could not see it:
Sentry runs *inside* the process, so a machine that is alive-but-not-serving
raises no exception and reports nothing — the Fly proxy simply stops routing
(`[PR01] no known healthy instances found for route tcp/443`) and every public
request hangs until it times out. There is exactly ONE machine, so its
de-registration from routing *is* a total outage. This watchdog is the missing
observer: it runs on GitHub Actions — a different failure domain than Fly —
every 5 minutes.

- **Workflow:** `.github/workflows/availability-watchdog.yml` (schedule `*/5 * * * *` + `workflow_dispatch`)
- **Logic + limits:** `.github/scripts/availability-watchdog.sh`
- **Harness (runs in CI job `availability-watchdog`):** `bash .github/scripts/availability-watchdog.test.sh`

### 6.1 What the probe checks

`GET https://api.premiselabs.co/v1/teams` with **no auth** — the real user
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

### 6.2 How to read a failure

1. **The workflow run goes RED** — that is the alert (enable GitHub Actions
   notifications for this repo, or the failure is only visible in the UI).
2. **One GitHub issue** appears (or an existing one gets a comment):
   `[monitor] PROD DOWN — api.premiselabs.co is not answering the availability
   probe` (or `PROD DEGRADED` for the UNEXPECTED class), labelled `auto-filed`.
3. The issue **body** is machine-managed and carries the verdict, the first
   observation time, the failing-run count, the raw probe evidence, and the
   self-healing state. Read it first; add human notes as **comments**.
4. The **first** line of the body is a state block:
   `<!-- watchdog-state kind=down first_failure_ts=… down_runs=… last_down_ts=… last_comment_ts=… cap_notified_ts=… restarts=… -->`.
   It drives the cooldown/velocity limits — do not hand-edit it. `restarts=`
   records restart **attempts** (a failed attempt still counts). The sustained
   window is additionally clamped to the issue's GitHub-assigned `created_at`,
   so editing `first_failure_ts` can delay a restart but never make one happen
   earlier than `SUSTAINED_DOWN_MINUTES` after the issue was created. The
   watchdog only ever adopts/mutates an issue authored by the GitHub Actions
   bot (`author:app/github-actions`) whose title is an **exact** match and whose
   body carries the watchdog marker line; a look-alike from any other account —
   or another workflow's bot issue whose title merely contains the marker terms
   — is ignored and a fresh machine issue is filed (see §6.8).

**One incident = one issue.** Repeats comment with an incremented count; the
issue is closed automatically with a `Recovered` comment when a probe answers
again. (This is the fix for the #2706 duplicate-issue failure mode.)

### 6.3 Manual restart (when you do not want to wait for the watchdog)

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

### 6.4 Self-healing and its limits

When DOWN is confirmed for a sustained period the watchdog restarts the Fly
machine itself, strictly rate limited so a database outage cannot become an
infinite restart loop (AWS automated-remediation guidance: cap the remediation
velocity and involve a human when the cap is hit).

| Limit | Default | Behaviour |
|---|---|---|
| `SUSTAINED_DOWN_MINUTES` | 10 | No restart until the service has been continuously down this long (≈3 failing runs / 2 probe intervals at the 5-min cadence) |
| `SUSTAINED_MIN_RUNS` | `max(2, ceil(SUSTAINED_DOWN_MINUTES / 5))` (2 at the wired 10-minute value) | At least this many failing runs must have been OBSERVED. Guards a stale/reopened incident whose stored clock is old from authorising a restart. Raising `SUSTAINED_DOWN_MINUTES` raises this too |
| `RESTART_COOLDOWN_MINUTES` | 20 | Minimum gap between automated restarts |
| `MAX_RESTARTS_PER_HOUR` | 2 | Rolling-hour cap. On the next failure the watchdog **stops restarting** and comments/pages asking for a human (paged at most every `CAP_RENOTIFY_MINUTES`, default 60 — the same text can still reappear in routine comments every `COMMENT_THROTTLE_MINUTES`). Set it to **`0` to disable automated restarts entirely** (an operator kill switch: alerting continues, nothing restarts) |
| `COMMENT_THROTTLE_MINUTES` | 15 | Routine “still down” comments are throttled; the body count still increments every run |
| `STALE_RESET_MINUTES` | 45 | If no failing run has been seen for this long, the incident is not continuous: the sustained WINDOW restarts (the restart ledger is **preserved**) |
| `CONTROL_URL` | `https://www.google.com/generate_204` | Runner-side egress control (see below) |

Override them in the `env:` block of `availability-watchdog.yml`. The watchdog
restarts **only**: (a) on a DOWN verdict — never on UNEXPECTED, where a restart
cannot help; (b) when `PROBE_URL` is the production endpoint — a drill
automatically disarms the restart leg; (c) when the failure is one a restart
cannot fix — `classify_failure()` maps curl's exit code to a class, and **DNS**
(6) and **TLS/certificate** (35, 51, 58–60, 66, 77, 80, 82–83, 90–91) failures
disarm the restart leg (`disarmed:unfixable`). The incident is still filed and
its body names the class and why nothing was restarted: restarting a machine
never repairs a resolver or an expired certificate, it only burns the budget.
Timeouts (28), connection-refused (7), and app-level 5xx stay restartable;
(d) when the previous incident's restart ledger cannot be read
(`disarmed:no_ledger` — fail closed, per above). Every run logs its verdict as
`restart decision: disarmed:<reason>` (or `restart decision: DOWN` on the armed
path), so *why* a restart did not happen is in the run log rather than
inferred. Three further safeguards worth knowing:

- **Write-then-act:** the attempt is recorded in the incident body *before*
  `flyctl` runs. If that write fails the restart does **not** happen — the body
  is the only cooldown/cap memory, so restarting without it could loop.
- **Attempts, not successes, are capped:** a failed `flyctl` call still counts
  against the hourly cap (the watchdog will not retry it every 5 minutes) and
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

### 6.5 Secrets

| Secret | Needed for | If missing |
|---|---|---|
| `FLY_API_TOKEN` | the automated restart | **Already exists** (used by `deploy-hosted.yml`). If absent, the restart leg is skipped, the log says so, and the incident **body** (plus any comment that is not throttled away) names the secret — **alerting still works** |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | optional paging on transitions | Page skipped with a log line (reuses the DR driver's secrets) |

`GITHUB_TOKEN` is supplied by Actions and needs `issues: write` (granted in the
workflow). A missing `GH_TOKEN` fails the run before probing — a monitor that
cannot file is a deaf monitor.

### 6.6 When restarts do not help

The watchdog stops after `MAX_RESTARTS_PER_HOUR` and asks for a human — treat
that as “this is not a wedged process”. Three disarm reasons also land here
without the cap being reached, and all are named in the incident body and the
run log: **`disarmed:unfixable`** (a DNS or TLS/certificate failure — repair the
resolver or the certificate; a restart is not the fix), **`disarmed:no_ledger`**
(the prior incident's restart ledger could not be read), and
**`disarmed:corrupt_ledger`** (the ledger was read but not fully parseable —
fix `restarts=` in the issue the message names). The two ledger disarms fail
closed: the restart leg cannot prove the hourly budget, so nothing restarts:

1. `flyctl logs -a tortoise-y4mjjq` — look for `Timeout reading from socket`,
   `Failed to create index`, or a crash loop.
2. Check **FalkorDB Cloud** reachability itself (a dead/slow DB is the #2850
   root cause; the app cannot serve while the DB stalls).
3. Check what was **deployed** — a bad release can 5xx without the process
   being wedged; roll back with `fly deploy --image $(fly releases -a tortoise-y4mjjq --json | jq -r '.[1].ImageRef') -a tortoise-y4mjjq`.
4. If restarts are actively harmful (e.g. they lengthen the outage), use a real
   lever — a `probe_url` drill only disarms **that one run**, and the next
   5-minute scheduled run probes production again:
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
   app runs on a **single machine** (see §6.8), so there is no failover to
   absorb a restart.

### 6.7 Drilling the watchdog

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

### 6.8 Known limits

- **Single-route, unauthenticated blindness.** The probe checks ONE route
  (`/v1/teams`) and only its no-auth branch. An outage that leaves that route
  answering `401` while other routes fail reads as UP (green) — and so does an
  auth-leg break that rejects every *real* token. The probe proves liveness and
  route presence, not end-to-end authenticated traffic.
- **A *total* runner-side network failure is INCONCLUSIVE, not DOWN** (the
  `CONTROL_URL` check). Alerting still fires; no restart is issued. The
  escalation page is throttled (at most once per `CAP_RENOTIFY_MINUTES`) and the
  per-run record is the incident **body** plus the RED workflow run — so "no
  page this run" does not mean "no alert". Note the control can only detect a
  TOTAL egress failure: a failure affecting only the probe's own host (its DNS
  zone, a Cloudflare/ASN block on the runner IP) leaves the control green and
  still reads as DOWN.
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
  restarts the sustained window **without** clearing the restart ledger.
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
  restarting without a provable budget; alerting is unaffected.
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
- The dedupe search API is eventually consistent; the 5-minute cadence makes
  that immaterial.
- Only one machine exists, so any restart is a (multi-minute) outage by itself
  — there is no failover. A restart is therefore always the *last* automated
  resort, gated on a trustworthy verdict (see the egress control).

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
