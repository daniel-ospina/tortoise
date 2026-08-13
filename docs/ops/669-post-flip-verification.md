---
title: "Ops: Post-Flip Verification Runbook (#669 Task 11, #766)"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-08-10
aboutSubjects: tortoise
aboutObjects: tortoise, supabase, falkordb, hosted-api
---

# Post-Flip Verification — Runbook (#669 Task 11 / #766)

> **What this is:** ... the operator checklist for **step 4** of the #669
> control-plane flip — verify the flip landed correctly on production,
> before the registry is deleted. Plain language, runnable top to bottom.

## The flip sequence (where this runbook sits)

| # | Step | Who runs it | Status |
| --- | --- | --- | --- |
| 1 | Pre-deploy gate: `bash .github/scripts/verify-cutover` (+ operator's `--live` run) | CI + operator | ✅ done before step 2 |
| 2 | Dispatch `supabase-deploy` (migrations + `tenant-provision` + `waitlist-subscribe` Edge Functions) | operator | ✅ done |
| 3 | Dispatch `deploy-hosted` (the app flip — app reads Supabase) | operator | ✅ done |
| 4 | **THIS RUNBOOK — post-flip verification incl. E2E-7** | operator | ⬅️ you are here |
| 5 | Registry delete (point of no return): `.github/scripts/delete-registry --confirm` | operator + **owner informed** | 🔒 only after step 4 passes |

Steps 2+3 together are "the single deploy" — never run step 3 without step 2.
The registry delete (step 5) is deliberately NOT part of this runbook: it runs
**only after** every check below passes, and the owner must be informed first.

## 0. Preconditions (before starting)

- [ ] Step 1 passed: `verify-cutover` reported PASS (CI job `flip-gate` **and** your live run).
- [ ] Step 2 finished: `supabase-deploy` completed (migrations 0006–0013 + Edge Functions deployed; project ref `ybetwichurajbfswfeqa`).
- [ ] Step 3 finished: `deploy-hosted` completed and the app is serving.
- [ ] **App version confirmed on production** — the deployed release matches the flip commit (see the command box below).
- [ ] `TORTOISE_SUPPRESS_ENUM_DELTA=1` is set on the app for the flip window (confirmed with the operator who set it). **It will be unset at the end of this runbook (§13).**
- [ ] Credentials ready in your shell (see the command box below — from the owner / GitHub Actions secrets, never commit them).
- [ ] Quick sanity: `curl -s https://api.premiselabs.co/health` → `{"status":"ok"}`.

Commands for the checklist items above:

```bash
# confirm the deployed release matches the flip commit
flyctl releases --app tortoise-y4mjjq | head -5

# credentials (never commit these)
export TORTOISE_DB_URI="rediss://…"                  # FalkorDB (same value as FALKORDB_CLOUD_URI)
export SUPABASE_URL="https://ybetwichurajbfswfeqa.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="…"                 # service-role key
export FASTAPI_INTERNAL_KEY="…"                      # for /v1/internal/* checks
```

> **Run order note:** §1 (E2E-7) must run **before** §2 (E2E-1) creates real
> signup rows — the flip gate asserts Supabase is still placeholder-only.

---

## 1. E2E-7 — the flip itself (run FIRST, before any signup)

**Goal:** prove the flipped app is live, both planes are green, and the
registry is still empty (it is deleted only in step 5).

### 1.1 `/health/ready` — green on BOTH planes

```bash
curl -s -i https://api.premiselabs.co/health/ready | head -1
curl -s https://api.premiselabs.co/health/ready
```

✅ **Expect:** HTTP `200` and
`{"status":"ok","db":"connected","control_plane":"connected"}`.
Anything else (503, missing `control_plane`) = the flip is not healthy — **stop** and roll back (§12).

### 1.2 `/health/security` — reports the Supabase lookup scheme

```bash
curl -s https://api.premiselabs.co/health/security
```

✅ **Expect:** `"scheme": "lookup_hash_sha256"` and
`"lookup": "sha256(pepper + key) exact-match over teams/api_keys (Supabase)"`.
(Registry mode would say `salted_pbkdf2_hmac_sha256` — if you see that, the
flip build is not the one serving; **stop**.)

### 1.3 Flip gate re-run — registry empty + Supabase placeholder-only

```bash
bash .github/scripts/verify-cutover --live
```

✅ **Expect:** exit 0 and `verify-cutover: PASS — preconditions hold`.
This re-asserts (read-only, touches nothing):

- `registry_control_plane` has **0 nodes** (still present-but-empty at this step — deletion is step 5), and
- Supabase holds only reconcilable placeholders (no real `teams`/`api_keys` rows yet).

Run this **before** §2 — once a verification signup creates a real row, the
Supabase leg of the gate will (correctly) report it.

### 1.4 Knowledge graphs intact + registry state (read-only dry-run)

```bash
TORTOISE_DB_URI="$TORTOISE_DB_URI" .github/scripts/delete-registry   # NO --confirm — dry-run only
```

✅ **Expect:** exit 0, `would delete: ['registry_control_plane']` (present,
to be deleted in step 5 — **this is expected at step 4**), and every
knowledge graph listed as INTACT with its node count, e.g.
`knowledge graphs INTACT before delete (1): team_xxx=12 nodes`.

- Knowledge graphs are `team_*` (tenant data) — they must all be listed.
- If it prints `already absent (nothing to delete): registry_control_plane`
  that is also fine at this step (nothing has resurrected it).
- **Never** run a bare `GRAPH.QUERY count` on a possibly-missing registry
  graph — GRAPH.QUERY auto-creates a missing graph (the exact artifact the
  flip removes). The scripts above handle this; don't hand-roll it.

### 1.5 E2E-7 negative — the registry no longer authenticates anything

A key that exists **only** in the registry (absent from Supabase) must get
`401` on both REST and MCP. A random `tt_` string is a fine stand-in:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  https://api.premiselabs.co/v1/team \
  -H "Authorization: Bearer tt_ffffffffffffffffffffffffffffffff"
```

✅ **Expect:** `401` on REST (and the same key → `401` on the MCP endpoint, §3.2 shape).

---

## 2. E2E-1 — signup provisions the master list in Supabase ONLY

**Goal:** a fresh signup creates `teams` + `team_memberships` + `api_keys`
rows in Supabase and writes **nothing** to the registry.

### 2.1 Create a verification signup

Option A — API register (fastest; rate-limited to 3/hour/IP):

```bash
curl -s -X POST https://api.premiselabs.co/v1/register \
  -H "Content-Type: application/json" \
  -d '{"email":"verify-'"$(date +%s)"'@example.com","password":"verify-pass-123"}'
```

✅ **Expect:** `200` with `{"api_key":"tt_…","team_id":"…","graph_name":"team_…"}`.
Save the `api_key` and `team_id` — you'll use them in E2E-2/3/5/9.

Option B — real user flow (also covers the manual round-trip, §11):
open `https://tortoise.premiselabs.co/signup`, sign up with email + password.

### 2.2 Supabase rows exist (dashboard SQL)

Open the Supabase dashboard SQL editor:
`https://supabase.com/dashboard/project/ybetwichurajbfswfeqa/sql/new` and run:

```sql
SELECT id, name, tier, graph_name, email FROM teams ORDER BY created_at DESC LIMIT 5;
SELECT team_id, user_id, role, status FROM team_memberships ORDER BY created_at DESC LIMIT 5;
SELECT team_id, key_prefix, created_via, revoked_at FROM api_keys ORDER BY created_at DESC LIMIT 5;
```

✅ **Expect:** your new team in `teams`, a matching `team_memberships` row
(role `owner`, status `active`), and an `api_keys` row
(`created_via='provisioned'`, `revoked_at` NULL).

### 2.3 Registry still 0 nodes

Re-run the read-only dry-run from §1.4 — the registry graph must still be
empty (or absent):

```bash
TORTOISE_DB_URI="$TORTOISE_DB_URI" .github/scripts/delete-registry
```

✅ **Expect:** knowledge graphs INTACT; `registry_control_plane` either
"would delete" (0 nodes — delete is step 5) or "already absent". The signup
must not have added any registry nodes.

---

## 3. E2E-2 — API-key auth resolves via instant lookup (REST + MCP)

### 3.1 Provisioned key authenticates on REST

```bash
curl -s https://api.premiselabs.co/v1/team -H "Authorization: Bearer tt_…"
```

✅ **Expect:** `200` with your `team_id`, `tier` (`free`), `max_users`, `max_graphs`, and `write_ops_limit` — quota/tier now comes from Supabase `teams` (note: `max_api_keys` is a quota concept, not a `/v1/team` response field — review P2, PR #887).

### 3.2 The same key authenticates on MCP

```bash
curl -s -X POST https://api.premiselabs.co/mcp/ \
  -H "Authorization: Bearer tt_…" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

✅ **Expect:** `200` with a JSON-RPC `result` (tool list) — NOT `401`.
(The endpoint is Streamable HTTP; `tools/list` is the standard smoke call.)

### 3.3 Revoke a key → rejected on both paths

Mint a second key (this also feeds E2E-9's audit check):

```bash
curl -s -X POST https://api.premiselabs.co/v1/team/keys \
  -H "Authorization: Bearer tt_…" -H "Content-Type: application/json" -d '{}'
# save {"id": "<key_id>", "key": "tt_…"} from the response
```

Use it once (✅ `200` on `/v1/team`), then revoke it:

```bash
curl -s -X DELETE https://api.premiselabs.co/v1/team/keys/<key_id> \
  -H "Authorization: Bearer tt_…"
```

✅ **Expect:** `{"revoked": true, "key_id": "<key_id>", "revoked_at": "…"}`.
Retry with the revoked key:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://api.premiselabs.co/v1/team \
  -H "Authorization: Bearer tt_<revoked-key>"
```

✅ **Expect:** `401` on REST — and `401` on the MCP endpoint too
(`api_keys.revoked_at` is the single revocation source of truth).

---

## 4. E2E-3 — invitation flow end-to-end

> Note: invite **mint requires the Team tier** (a verification signup is
> `free`, so minting returns `402`). Either run this against a Team-tier
> team, or temporarily bump the verification team's tier in the dashboard
> SQL editor (`UPDATE teams SET tier='team' WHERE id='<team_id>';`) and
> restore it afterwards.

### 4.1 Mint (owner/admin invites by email)

```bash
curl -s -X POST https://api.premiselabs.co/v1/invites \
  -H "Authorization: Bearer tt_…" -H "Content-Type: application/json" \
  -d '{"team_id":"<team_id>","email":"invitee-'"$(date +%s)"'@example.com","role":"admin"}'
```

✅ **Expect:** `200` with `{"invite_id":"…","status":"invited","token":"…","expires_at":"…","role":"admin"}`.
Save the `token` (returned once — it is stored only as a `lookup_hash`).

### 4.2 Accept (as the invitee; JWT email must match)

```bash
curl -s -X POST https://api.premiselabs.co/v1/invites/accept \
  -H "Authorization: Bearer tt_<invitee's-key>" -H "Content-Type: application/json" \
  -d '{"token":"<token>"}'
```

✅ **Expect:** `200` with `{"team_id":"…","role":"admin"}` — the membership
is created with the **invited role**.

### 4.3 Consumed invite cannot be re-accepted

Repeat 4.2 with the same token:

✅ **Expect:** `400` (`accepted` in the detail) — the pending invite was consumed.

SQL cross-check (dashboard):

```sql
SELECT id, team_id, email, role, status, accepted_at FROM invitations ORDER BY created_at DESC LIMIT 3;
```

✅ **Expect:** status `accepted` + a non-NULL `accepted_at`.

---

## 5. E2E-4 — backup sweep reads Supabase and stamps `teams`

### 5.1 Run the sweep (or wait for the hourly cron)

```bash
curl -s -X POST https://api.premiselabs.co/v1/internal/backups/sweep \
  -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY"
```

✅ **Expect:** one of:

- `{"status":"no_teams","teams_backed_up":0,…}` — **expected at zero data** (the chronic no-teams state is quiet, §10), or
- per-team `results` with `status:"ok"` for teams that have knowledge-graph data.

### 5.2 `teams.backup_latest_at` stamped

```sql
SELECT id, graph_name, backup_latest_at, backup_restored_at FROM teams ORDER BY created_at DESC LIMIT 5;
```

✅ **Expect:** for a team with data, `backup_latest_at` is a recent timestamp
(from the sweep above / the last hourly run). Teams are enumerated from
Supabase `teams` now — no registry reads.

---

## 6. E2E-5 — onboarding + GitHub connect round-trip

### 6.1 Onboarding state read + patch

```bash
curl -s https://api.premiselabs.co/v1/onboarding/state -H "Authorization: Bearer tt_…"
```

✅ **Expect:** `200` with the default onboarding object + your team email.

```bash
curl -s -X PATCH https://api.premiselabs.co/v1/onboarding/state \
  -H "Authorization: Bearer tt_…" -H "Content-Type: application/json" \
  -d '{"session_recording":true}'
```

✅ **Expect:** `200` with `session_recording: true` merged in.

### 6.2 GitHub connect (initiate + status)

```bash
curl -s -X POST https://api.premiselabs.co/v1/onboarding/github/connect \
  -H "Authorization: Bearer tt_…" -H "Content-Type: application/json" -d '{}'
```

✅ **Expect:** `200` with `{"auth_url":"https://github.com/login/oauth/authorize?…","state":"…"}`
(if GitHub OAuth is configured; `503` otherwise — a known env dependency, not a flip regression).

```bash
curl -s https://api.premiselabs.co/v1/onboarding/github/status -H "Authorization: Bearer tt_…"
```

✅ **Expect:** `200` with `{"connected":false,…}` (or `true` after completing the OAuth dance).

---

## 7. E2E-6 — welcome-page reveal shows the key ONCE

**Manual (browser):**

1. Sign up / sign in at `https://tortoise.premiselabs.co/welcome` (welcome page).
2. First visit: the reveal block shows the plaintext `tt_…` key **once**.
3. Reload / visit again: the page shows the **returning state** (dashboard CTA, no re-revealed key).

**SQL cross-check (dashboard):** the one-time reveal nulls the plaintext but
keeps the lookup anchor:

```sql
SELECT user_id, team_id, role, status, api_key IS NULL AS key_nulled, lookup_hash IS NOT NULL AS hash_kept
FROM team_memberships ORDER BY updated_at DESC LIMIT 3;
```

✅ **Expect:** for the revealed membership: `key_nulled = true` AND `hash_kept = true`.

**Automated smoke (repo):** the Playwright suite covers the same contract:

```bash
uv run python -m pytest tests/e2e/test_welcome_page.py -q --timeout=120
```

---

## 8. E2E-9 — audit events land in Supabase (actor_user_id TEXT)

A gated action already happened in §3.3 (the API-key mint). Cross-check:

```sql
SELECT id, team_id, actor_user_id, operation, resource_type, resource_id, created_at
FROM audit_events WHERE operation = 'api_key_create'
ORDER BY created_at DESC LIMIT 5;
```

✅ **Expect:** a row for the mint with `actor_user_id` as **TEXT**
(e.g. `anon-…`, `team:…` — non-UUID actors must insert fine after migration
0006). No row + no error = `TORTOISE_AUDIT_DSN` is not pointed at Supabase on
Fly — check the app env (`.env.example` documents the pooler DSN format).

---

## 9. E2E-8 — health reflects the new reality

Already covered in §1.1/§1.2 — both endpoints are part of E2E-7's gate.
Re-confirm at the end of the run:

```bash
curl -s https://api.premiselabs.co/health/ready && echo && curl -s https://api.premiselabs.co/health/security
```

✅ **Expect:** ready = `{"status":"ok","db":"connected","control_plane":"connected"}`;
security = `scheme: lookup_hash_sha256` (Supabase).

---

## 10. #596 monitor confirmation — no_teams state, watcher quiet

```bash
curl -s https://api.premiselabs.co/v1/internal/backups/status \
  -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY"
```

✅ **Expect:**

- `"no_teams": true` (chronic zero-team state — the watcher's honest signal),
- `"watcher": {"running": true, …}` with a fresh `last_poll_at`, and
- **no new GitHub issue / Telegram alert** from the #596 watcher during the
  flip window (the enumeration-delta guard is suppressed by
  `TORTOISE_SUPPRESS_ENUM_DELTA=1`, so a spurious "wiped enumeration source"
  incident must NOT have been filed — see §13 for restoring the guard).

If a `STALE`/`NEVER_BACKED_UP`/`ENUM_DELTA` incident WAS filed during the
window, investigate before proceeding.

---

## 11. Manual signup round-trip (the new-user experience)

1. Open `https://tortoise.premiselabs.co/signup` → create account (email + password ≥ 6 chars).
2. Land on the welcome page → the key reveal shows `tt_…` **once**.
3. First API call with the revealed key:

   ```bash
   curl -s https://api.premiselabs.co/v1/team -H "Authorization: Bearer tt_…"
   ```

   ✅ **Expect:** `200` with your team info.

4. (Optional) add the MCP endpoint to your client:
   `https://api.premiselabs.co/mcp` with the same Bearer key.

---

## 12. Rollback — lossless UNTIL step 5 runs

If ANY check above fails (**before** the registry delete):

1. Redeploy the pre-flip build (the release before the flip):

   ```bash
   flyctl releases --app tortoise-y4mjjq        # find the pre-flip release
   flyctl deploy --app tortoise-y4mjjq --image <pre-flip-image>   # or re-dispatch deploy-hosted at the pre-flip commit
   ```

2. The registry auto-recreates **empty** on demand — provisioning was the
   source of truth, and at zero data there is nothing to lose.
3. Re-run §0 preconditions; re-run this runbook before attempting the flip again.

> **Point of no return:** after step 5 (`delete-registry --confirm`) runs,
> rollback is no longer lossless: recovery = redeploy the prior build AND
> re-seed from Supabase (trivially empty at zero data). That is why the
> delete happens only after this runbook fully passes, with the owner informed.

---

## 13. Restore the #596 guard — unset TORTOISE_SUPPRESS_ENUM_DELTA

The suppression flag was set on the app for the flip window ONLY. Now that
verification passed, restore the enumeration-delta guard:

```bash
flyctl secrets unset TORTOISE_SUPPRESS_ENUM_DELTA --app tortoise-y4mjjq
flyctl secrets list --app tortoise-y4mjjq   # confirm it is gone
```

✅ **Expect:** the secret no longer appears. (It must NEVER be left set —
while set, a real enumeration wipe would be silently suppressed.)

---


---

## 15. EXECUTION LOG — what the 2026-08-11 flip actually found (live)

The flip was executed 2026-08-11. Beyond the checks above, live verification
surfaced **six code paths that still read the deleted registry in Supabase
mode** — each silently recreated the empty `registry_control_plane` graph
(FalkorDB auto-creates graphs on query). All were fixed and redeployed
(PR #911); **no runtime or boot path reads the registry anymore** (verified:
after one `GRAPH.DELETE`, a full health+signup+REST+MCP exercise over 60s
left the graph absent).

| # | Path | Why it recreated the registry | Fix |
|---|---|---|---|
| 1 | backup watcher (boot) | passed the raw registry handle to the #768 seam | Supabase control plane in Supabase mode |
| 2 | `POST /backups` + `/backups/restore` | stamp seam got the raw registry handle | Supabase control plane |
| 3 | `_iter_registered_teams` + `_purge_deleted_teams` | boot event-retention + deleted-team purge read the registry | Supabase teams (deleted_at IS NULL); purge skips the registry cascade post-flip |
| 4 | `/health/ready` | the data-plane probe opened the registry namespace | probe the default (`tortoise`) graph |
| 5 | **metering** (`/v1/team` → `get_current_usage`, `record_write_ops`) | MeteringRecord nodes lived in the registry — every authenticated request recreated it | migration `0014_metering_records` + seam; atomic `metering_increment` RPC |
| 6 | `quota.resolve_team_limits` (MCP tool enforcement) | read the registry Team node | teams row via the seam (NULL = unlimited parity) |

Also applied during the flip: migration 0014, Edge Function secrets verified,
`TORTOISE_SUPPRESS_ENUM_DELTA=1` active for the window. **Remaining follow-up:
`TORTOISE_AUDIT_DSN` needs the Supabase pooler DB password (owner) — audit
falls back to JSONL safely until then.** Migrations 0006–0014 are live;
`webhook_events` + `metering_records` tables verified.

## 14. NEXT STEP — step 5: registry delete (only after all of the above passes)

Hand off to the owner, then run the flip's final step:

```bash
TORTOISE_DB_URI="$TORTOISE_DB_URI" .github/scripts/delete-registry --confirm
```

✅ **Expect:** `VERIFY OK — registry graphs gone; all knowledge graphs intact
(node counts unchanged)` — the script itself asserts the post-delete state
(registry absent, every `team_*` graph still present with unchanged counts).

Post-delete confirmation (idempotent re-run):

```bash
TORTOISE_DB_URI="$TORTOISE_DB_URI" .github/scripts/delete-registry
```

✅ **Expect:** `already absent (nothing to delete): registry_control_plane`.

**After step 5:** the control plane lives in Supabase only; the knowledge
graphs + #596 backups are untouched.
