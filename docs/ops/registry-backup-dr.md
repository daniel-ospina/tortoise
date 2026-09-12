---
title: "Registry/Knowledge-Graph Backup DR — Runbook (#596)"
type: operations
domain: platform
doc_status: live
created: 2026-08-08
issue: 596
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-hosted-platform
---

# Registry/Knowledge-Graph Backup DR — Runbook (#596)

> "registry" naming is retained from the registry-era design — the content is
> **per-team knowledge graphs** (control-plane metadata migrates to Supabase
> under #669). Since #2313 the sweep covers EVERY active graph of a team
> (the default + custom graphs), each with its own archives, state, retention
> and staleness incidents.

## #2304 trash lifecycle & purge (delete = quarantine → grace → erasure)

Since #2304, deleting a CUSTOM graph (DELETE /v1/graphs/{id}) is a QUARANTINE,
not a removal: the row is tombstoned (status='deleted' + deleted_at stamped on
BOTH lanes) and the graph enters the team's Trash for a **7-day recovery
window** (owner-restorable via POST /v1/graphs/trash/{id}/restore — owner/admin
session only; keys stay revoked). The default graph can never be deleted.

**Purge** (physical erasure) runs on demand via the internal endpoint:

    curl -X POST $API/v1/internal/backups/purge \
      -H "Authorization: Bearer $INTERNAL_KEY"
    # optional {"grace_days": N} (1..365) for drills; default 7

Per expired tombstone (deleted_at <= now - 7d; legacy tombstones with no
deleted_at count as past-grace) the purge: (1) re-verifies the row is STILL an
unpurged tombstone (a restored graph is never erased — race guard); (2) drops
the data-plane namespace (GRAPH.DELETE via select_graph(ns).delete(); an absent
graph is success — idempotent); (3) deletes the graph's backup artifacts —
nested pool `backups/{team}/{gid}/`, per-graph ops state
`ops/teams/{team}/graphs/{gid}/`, and legacy FLAT archives resolved through the
#2370 classification index; (4) stamps the row `purged_at` (row KEPT — audit
tombstone; the trash list stops listing it; restore returns 410).

The namespace ownership guard: only `team_{team_id}_{graph_id}` namespaces are
dropped — a namespace that no longer maps to the tombstoned id (re-occupied by
a live graph) is RETAINED and the row is stamped `purged_residual:true` for
operator review. Artifact-deletion errors never fail the namespace drop; they
are logged for operator follow-up (the row is stamped regardless).

**Restore/purge serialization:** the restore endpoint takes the per-team sweep
lock (`_sweep_team_lock`, timed 20s via to_thread — never blocks the event
loop) around its probe+flip; the purge/sweep hold the same lock per team, so a
restore racing a purge cannot interleave. A lock timeout returns 503.

**Cadence:** the purge rides the hourly driver (registry-cron.sh step 5,
wired by #2317) so past-window trash is erased within a day of expiry — the
runbook's erase claim is honored by the scheduler, not by operator memory. A
purge body of status ``errors`` (per-tombstone failures — row kept as the
retry anchor) or a non-2xx response fails the driver run loudly (red job),
never a silent skip.

## RPO / RTO contract (#2317)

**Cadence is HOURLY, not daily.** The product/pricing flag is named
``hourly_backups`` (renamed from the registry-era ``daily_backups`` in
#2317 — it understated the delivered cadence; it is pre-launch, "planned"/
false on every tier today). The driver cron (`registry-backup-cron.yml`,
`17 * * * *`) + per-graph sweep → **RPO ≤ 1 h typical / ≤ 2 h worst-case**
(#596 §3.4/§3.8 machinery). Retention buckets named ``daily``/``weekly``
(``keep_daily``/``keep_weekly``) are RETENTION horizons, not cadence.

**Achieved freshness is MEASURED, not assumed** (best-practice gap 6d):
- per-team/per-graph tri-state archive-age vs `BACKUP_STALE_THRESHOLD_MIN` —
  watcher poll → `GET /v1/internal/backups/status` → `per_team` (+ heartbeat);
- per-run sweep roll-up (totals/failures/streaks) — `/status` → `last_sweep`;
- driver direct-R2 DEFAULT-graph age leg (app-down case) — files STALE with
  the measured age in minutes;
- restore-drill records (measured restore time vs RTO) — `/status` →
  `last_drill`.

**RTO (per-team restore, per tier):**

| Tier | Restore-op RTO | App-bootable |
|---|---|---|
| free / solo / anon | n/a (no backup entitlement — `hourly_backups` false) | n/a |
| pro / team (`hourly_backups` when flipped live) | **≤ 15 min** measured drill-accept → verified scratch restore (`drill_ok`) | **≤ 1 h** from app-bootable state after a full-platform restore |

The ≤15-min/≤1h target CARRIES the retired registry-era commitment (#669) to
per-team restores. Every drill (manual or scheduled) records `duration_s` /
`rto_s` / `within_rto` to `ops/drills/last.json`; the scheduled drill opens a
RESTORE_DRILL_FAILED incident on failure OR when the measured time breaches
≤15 min (restore speed is a target, not an accident).

## #2319 Backup immutability (R2 bucket-lock) + regional-durability decision

Owner-recorded threat-model decision (issue #2319 — follow-up to the
2026-09-06 DR best-practices audit gaps 5 + 7). Decisions (a)/(b) below are
the recorded stance; the code/config surface implementing them ships with the
issue.

### (a) Regional-durability stance — single-region ACCEPTED (phase 1), mirror mechanism SHIPPED

**Recorded:** single-region R2 + single-region FalkorDB Cloud is **accepted for
the current phase** (pre-beta, chronic 0-team state), **with the second-store
mirror mechanism implemented and env-guarded now** — adopting dual-region is
provisioning a store + one env flag, no code change.

Rationale: the audit verdict is that backups are already offsite (separate
blast radius from the DB). R2 has **no native cross-region replication** and
its S3 layer reports region `auto`, so regional separation requires a separate
store (a second Cloudflare account / region-locked bucket, or another
S3-compatible region) plus our own copy job. At 0 eligible teams the expected
loss from a correlated region failure is nil, so paying recurring dual-store
storage + an always-on job now is premature — but the enabling mechanism is
cheap, so it ships behind `BACKUP_MIRROR_ENABLED` (below).

**Flip criteria** (re-check at each paid-team milestone / customer P0): ≥1
paying team with real data, any customer storing regulated or high-value
content, or any published SLA/RPO that assumes regional redundancy. Flip = §(c)
provisioning + §Verify step 4.

### (b) Bucket-lock immutability — ADOPTED (prefix `backups/`, Age 3 days, 1..6 bound)

**Adopted:** one R2 bucket-lock rule, prefix `backups/` exactly (NEVER the
whole bucket — the sweep rewrites `ops/*` state/heartbeat objects in place),
Age retention **3 days** (configurable 1..6, default 3).

Why the window:
- **Protection:** the lock blocks delete/overwrite within the window. The
  recovery-critical archives (hourly, RPO ≤2h) are ≤24h old — 3 days keeps the
  newest ~3 days of archives intact even against a key-holder who deletes
  everything older. Dumps never overwrite (unique keys), so only deletion is
  the live threat.
- **Purge-erasure honesty (#2304):** a deleted custom graph's backup artifacts
  must be physically erasable when the purge runs (≥7-day trash grace after
  the graph DELETE; the newest artifact is ≤~1h old at delete). At purge time
  artifacts are ≥7 days old, so any lock ≤6 days has expired — the **1..6-day
  bound is enforced in config** (a ≥7-day lock would stamp purged rows while
  locked artifacts persist — dishonest erasure). Default 3 keeps a 4-day margin.
- **Prune compatibility:** the prune (keep_hourly=24) deletes day-bucket losers
  from ~25h old — younger than any useful lock window — so the prune is
  **lock-tolerant**: a locked object is skipped + logged and retried after the
  window; the pool never wedges. Transient over-retention is bounded (~2 extra
  days of hourly dumps ≈ ≤48 objects/pool while locked objects age out); pools
  converge back to the ~35-object budget. Full-history immutability (lock ≥ the
  4-week horizon) is **rejected** — it would break the erasure obligation.

Mechanics that matter (Cloudflare docs, 2026):
- R2 bucket locks are **NOT S3 Object Lock**. They are prefix-scoped rules
  (`{id, enabled, prefix, condition}` with Age `maxAgeSeconds` / `Indefinite` /
  a date), managed via the dashboard, Wrangler (`r2 bucket lock add|list`), or
  the REST API — **never via the S3 seam**. R2's S3 compatibility layer does
  not implement `GetBucketVersioning` / `GetObjectLockConfiguration` / object-
  lock `CreateBucket`, and `x-amz-bypass-governance-retention` is unsupported
  (no bypass exists). Rules apply to new AND existing objects; strictest rule
  wins; removing a rule does NOT unlock objects still inside their window.
- Deleting a locked object over the S3 seam fails 403 `AccessDenied` (R2 code
  10069 `ObjectLockedByBucketPolicy`) — the prune/purge paths must tolerate it
  (they do; see below).

### Repo config contract + drift bounds

| Knob | Documented value | Enforced |
|---|---|---|
| `BACKUP_LOCK_ENABLED` | `true` ONLY after the rule is provisioned | `backup_config.py` (parsed when sweep enabled) |
| `BACKUP_LOCK_DAYS` | `3` (allowed **1..6** — must stay < the 7-day #2304 grace) | `backup_config.py` ConfigError out of range; drift test `test_backup_config.py` |
| rule prefix | `backups/` (module constant `_LOCK_PREFIX`) | code + docs |
| `CF_API_TOKEN` | optional; R2-scoped (Account → Cloudflare R2 → Edit) | live verification only when present; else `unverifiable` |
| `BACKUP_MIRROR_ENABLED` | `true` only with the four creds below | ConfigError when true + missing creds |
| `R2_MIRROR_ACCOUNT_ID/_ACCESS_KEY_ID/_SECRET_ACCESS_KEY/_BUCKET` | second store's own account/region + bucket | required-when-enabled |
| `R2_MIRROR_ENDPOINT` | optional override; default `https://{account_id}.r2.cloudflarestorage.com` | parsed |

### (c) Code-side additions

1. **Prune lock tolerance** (`hosted_backup.prune_backups` →
   `_delete_backup_objects`): per-object delete failures (incl. locked) are
   skipped + logged, never a pool abort; only actually-deleted ids are
   returned. Tests: `test_prune_tolerates_locked_objects_and_keeps_pruning`.
2. **Lock health check** — `POST /v1/internal/backups/verify-lock` (internal
   key; optional body `{account_id, bucket}` to check a different store, e.g.
   the mirror) reads the Cloudflare REST lock rules and compares the strictest
   retention covering `backups/` against `BACKUP_LOCK_DAYS`. `GET
   /v1/internal/backups/status` carries a `lock` block with the same result
   when `BACKUP_LOCK_ENABLED` is set. Statuses: `verified` | `drift` |
   `absent` | `unverifiable`. Unreachable / no token → `unverifiable` with a
   runbook pointer — a lock READ failure never fails backups (and there is NO
   boot-time check: protecting the #545 boot blast radius).
3. **Second-region mirror** (env-guarded): when the sweep (the hourly cron's
   core action) accepts an archive — every guard passed — it is copied to the
   mirror store and **read-back sha256-verified** against its manifest
   (`hosted_backup.mirror_backup`, wired in `backup_sweep._backup_graph`).
   A mirror failure is **loud** (per-graph error + /status `last_sweep`
   graph_failures streak) — never a silent durability gap; the primary backup
   is already durable and is never failed by a mirror hiccup (the next run
   re-mirrors a fresh archive). Mirror covers newly-created per-graph archives;
   a one-time backfill seeds history into a fresh mirror (§Verify step 4).

### Provisioning (console / Wrangler / REST — there is NO S3 path)

Console (simplest): R2 → `tortoise-backups` → **Settings** → **Bucket lock
rules** → Add rule: prefix `backups/`, retention **3 days** (or your chosen
`BACKUP_LOCK_DAYS`), save. Apply the same rule to the mirror bucket.

Wrangler (CLI):

    npx wrangler r2 bucket lock add tortoise-backups --prefix backups/ --retention-days 3
    # flags per `npx wrangler r2 bucket lock add --help`; verify by listing:
    npx wrangler r2 bucket lock list tortoise-backups

REST (exact — the PUT body shape from the Cloudflare docs):

    curl -X PUT "https://api.cloudflare.com/client/v4/accounts/<R2_ACCOUNT_ID>/r2/buckets/tortoise-backups/lock" \
      -H "Authorization: Bearer <CF_API_TOKEN>" -H "Content-Type: application/json" \
      -d '{"rules":[{"id":"backups-lock-3d","enabled":true,"prefix":"backups/","condition":{"type":"Age","maxAgeSeconds":259200}}]}'

(3 days = 259200 s. The API token needs Account → Cloudflare R2 → Edit;
jurisdiction-restricted buckets require the `cf-r2-jurisdiction` header.)

### Verify (operator runbook)

1. Config contract present: `curl $API/v1/internal/backups/status` (internal
   key) → `lock` block shows `enabled:true`; without `CF_API_TOKEN` it shows
   `status:unverifiable` — verify with (2)/(3) instead.
2. Live drift check: `curl -sS -X POST -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY" \
   $API/v1/internal/backups/verify-lock` → expect `"status":"verified"`
   (`drift`/`absent` = the rule is missing or shorter than `BACKUP_LOCK_DAYS`).
3. Rule-list check (no app/token needed): `npx wrangler r2 bucket lock list
   tortoise-backups` — confirm the `backups/` rule with the expected days.
4. Mirror adoption: provision the second store (own account/region), set
   `R2_MIRROR_*` + `BACKUP_MIRROR_ENABLED=true` on Fly (`fly secrets set`),
   then one-time backfill of existing history:
   `aws s3 sync s3://<primary>/backups s3://<mirror>/backups \
   --endpoint-url <mirror-endpoint> --region auto` (s3 sync is append-only by
   default — it never propagates primary deletions; the sweep keeps the mirror
   fresh afterwards). Verify a mirrored archive read-back hash matches its
   manifest.
5. Block-in-window drill: with a test object under `backups/`, `aws s3api
   delete-object --bucket $R2_BUCKET --key <test-key>` fails while locked;
   confirm the sweep prune logs "bucket-locked … skipping" for in-window
   objects and still prunes the rest, and that an in-window object is pruned
   normally once the window passes.
6. Mirror check: `curl … /v1/internal/backups/verify-lock -d '{"account_id":"<R2_MIRROR_ACCOUNT_ID>","bucket":"<R2_MIRROR_BUCKET>"}'`
   (same rule must protect the mirror).

### Residuals (recorded with this decision)
- A guard-rejected archive (P0 / empty / data-loss) whose immediate delete is
  blocked by the lock lingers ≤ the lock window (restore refuses it — the
  guards are fail-closed — and an incident is filed; bounded and visible).
- Purge drills with `grace_days` below the lock window leave artifacts until
  the window expires (logged residual; production purge at the 7-day grace is
  unaffected — artifacts are ≥7d old).
- Mirror covers new per-graph archives; `ops/*` and legacy flat objects are
  not mirrored (reconstructible / drained); the one-time sync seeds them.

## Architecture
- **Driver:** `.github/workflows/registry-backup-cron.yml` (hourly, GH Actions) → internal-key endpoints. Independent failure domain — an OOM crash-loop (#545) must not blind the pipeline.
- **Watcher (driver-disabled leg):** in-process read-only staleness daemon (spawned in `_lifespan`) that files GitHub issues + pushes Telegram ITSELF — covered by construction when the workflow is disabled.
- **Direct R2 leg (app-down leg):** the driver computes the DEFAULT graph's freshness from R2 prefixes (aws CLI) — nested `backups/{team}/default/` + legacy flat (`backups/{team}/2…`, the pre-#2313 default dumps; a legacy-flat classification index #2370 excludes C5-era custom flats when present) — independent of `/status`. A fresh CUSTOM graph can never mask a stale default (#2375).
- **Alert sink (dual-channel):** GitHub issue (agent) + Telegram push (human), R2 create-once per-incident dedup (`ops/alerts/{KIND}/{subject}.json` — `_` when the incident is subject-less, delete-to-resolve), GH-search fallback, pending-push retries.

## R2 layout
- `backups/{team}/{graph}/{ts}_{rnd}/dump.enc` + `manifest.json` — per-GRAPH archives (#2313; the default graph uses the literal `default` segment; custom graphs their control-plane id). Retention per graph: 24 hourly + 7 daily + 4 weekly (`keep_hourly`) ≈ **35 objects/pool** — keep ALL dumps younger than 24 h, then the NEWEST per UTC day within the 7-day horizon, then the newest per ISO week (4). #2373: day-bucket anchors — the pre-#2373 implementation kept one anchor per UTC HOUR-bucket (~172 objects/pool over the horizon); #2319's lock-window math uses the ~35 figure. Pre-#2313 team-level flat objects (`backups/{team}/{ts}_{rnd}/…`) are the DEFAULT graph's legacy archives — read-bucketed as default, drained by the sweep's per-team legacy prune.
- `ops/teams/{team}/state.json` — legacy transition-guard counts (mirror of the default graph's per-graph state; pre-#2313 consumers).
- `ops/teams/{team}/graphs/{graph_id}/state.json` — per-graph transition-guard counts (#2313).
- `ops/state.json` — team count (enumeration-delta guard) + sweep timestamps.
- `ops/drills/last.json` — #2317 drill record (pass/fail + measured restore time vs RTO; overwritten per drill; `/status` → `last_drill`).
- Alerts are keyed per (kind, subject): team incidents use the team id; CUSTOM-graph incidents use `"{team}:{graph}"` (#2313) — the same subject re-baseline resolves and the watcher opens.
- `ops/watcher-heartbeat.json`, `ops/driver-heartbeat.json` — mutual supervision.
- `ops/alerts/`, `ops/pending-push/`, `ops/simulate/`, `ops/suppression.json`.

## Driver state taxonomy — disabled ≠ healthy (#2796)

The driver (`.github/scripts/registry-cron.sh`) classifies `/status` plus its
app-independent direct-R2 leg into **five** states. Only a *deliberately* off
sweep is silent; the other four file a deduped `dr:backup` incident **and**
make the hourly job RED, so a broken pipeline cannot stay green for weeks (the
31-day #2790 outage hid behind 40 consecutive `success` runs).

| State | Detection | Driver behavior |
|---|---|---|
| deliberate-off | `enabled:false`, no `config_error`/`storage_error`, pool **measured** fresh | silent `exit 0` — a real operator pause must not page |
| off-because-broken | `enabled:false` + non-null `config_error` | files **SWEEP_CONFIG_ERROR**, job RED |
| off-because-storage-down | `enabled:false` + non-null `storage_error` | files **R2_DOWN**, job RED |
| off-while-pool-stale | `enabled:false`, no config/storage error, and the pool is not **measured fresh**: a `backups/{team}/default/` archive older than `BACKUP_DRIVER_DOWN_THRESHOLD_MIN` (240), a team prefix with **no default archive at all**, or a listing that failed | files **SWEEP_OFF_STALE**, job RED (the per-team STALE incident still files) |
| enabled-but-backing-up-nothing | `enabled:true` and the sweep backed up 0 teams (`no_teams` / `no_eligible_teams` / `no_work` / `enum_failed` / `error`) while the R2 pool holds ≥1 team prefix, **or while the pool cannot be measured**, **or** the sweep reported a lock that cannot be verified as recent | files **SWEEP_NO_COVERAGE**, job RED |

**Filing ⇒ the job is RED.** The taxonomy above is the classifier; the job
status is simpler and deliberately blunter — `file_alert` sets a run-level
`LOUD` flag and every terminal exit goes through it, so *any* incident filed
this run (APP_DOWN, WATCHER_DOWN, a per-team STALE from the direct-R2 leg, an
R2_DOWN — preflight or partial-listing — a stuck-lock SWEEP_NO_COVERAGE) exits
1. (The converse does not hold: a hard driver/config failure — missing
`GITHUB_TOKEN`, an unreadable R2 preflight, or a failed purge/reconcile
ride-along — also exits 1 without filing. RED therefore means "broken or
unverifiable", which is the point.) This is the actual fix for #2796: the
31-day #2790 outage hid behind 40 consecutive `success` runs *after* the driver
had already filed `STALE`.

**Unknown ≠ empty (the dominant rule).** A failed `list-objects-v2` — the
top-level listing, any per-team listing — or a failed `get-object` of the
legacy-flat classification index leaves the pool *unmeasured*: the driver then
never reads it as fresh, never files a false `STALE` from a failed read, and
never lets it silence the 0-team envelope. A listing that fails while
`head-bucket` passes also files **R2_DOWN** (storage partially reachable,
pool unverifiable) so a "successful" sweep cannot keep the run green. A failed
read while the sweep is OFF is **not** a confirmed deliberate pause, so it
files SWEEP_OFF_STALE. An unclassifiable `/status` (no boolean `.enabled`, or a
non-JSON 200) fails **closed** as SWEEP_NO_COVERAGE. A held sweep lock is not
healthy on its own: a usable `last_sweep_at` older than the driver-down window,
or an *unverifiable* lock (no usable `last_sweep_at`) when the pool is not
**measured empty**, is SWEEP_NO_COVERAGE. The
`error`/`enum_failed`/unrecognized sweep statuses file SWEEP_NO_COVERAGE
**regardless of pool state** (only the enumerated-empty statuses
`no_teams`/`no_eligible_teams`/`no_work` use the 0-team envelope).

**Named residual — an unassessable watcher.** When `enabled:true` and `/status`
carries no `.watcher` block at all (usually an older app build), the driver
neither files nor clears `WATCHER_DOWN`; a healthy sweep then exits 0. This is
an accepted residual, not an oversight: the block's *absence* is not evidence
the daemon is dead, and the per-graph `STALE`/`NEVER_BACKED_UP` coverage it
provides is still partly covered by the direct-R2 leg for default graphs.
Making it loud is tracked separately if a schema-drift incident ever occurs.

**0-team false-positive envelope:** when the sweep finds 0 teams **and the R2
pool is measured empty**, the chronic pre-beta state is assumed and nothing is
filed. Both surfaces must be empty before silence is allowed; a mismatch (or an
unmeasured pool) is loud. Coverage is read from `graph_totals.backed_up`, not
`teams_backed_up` — the latter counts only DEFAULT-graph backups, so a team
whose default graph is legitimately empty while a custom graph archived is not
a coverage gap.

**Storage errors are not the kill-switch.** A non-null `storage_error` files
**R2_DOWN** whether or not the sweep is enabled (the enabled path used to drop
it), and it blocks the `R2_DOWN` self-heal for that run — a sweep that
"completes" while `/status` reports broken app storage has not recovered it.

**Self-heal is evidence-tiered (#2411 + #2796):** `APP_DOWN` and the resolved
`SWEEP_CONFIG_ERROR`/`SWEEP_OFF_STALE` clear when `/status` + the sweep complete
(the app answered). `R2_DOWN` clears **only** when the driver's own
`head-bucket` preflight succeeded, `storage_error` is clear **and** the pool was
measured this run — a sweep that "completes" with R2 down must not close the
`R2_DOWN` it just filed (that would re-file and re-close forever). It can
therefore also clear from the disabled path once storage is proven back.
`WATCHER_DOWN` clears on **watcher evidence** (a `.watcher` block with a real
boolean `running` read this run) — a missing/malformed block neither files nor
clears it, because unknown is not evidence the daemon is alive.
`SWEEP_NO_COVERAGE` clears **only** on a run that actually backed up
(`backed_up`/`degraded`), and that judgment is made from
`graph_totals.backed_up`, not `teams_backed_up`.

**Dedup lifecycle:** incidents are create-once in R2
(`ops/alerts/{kind}/{subject}.json`, `_` when the incident is subject-less) with
a GitHub-search fallback. **One incident = one create-once point (#2844):** the
driver and the server-side `AlertStore` both write the canonical `_.json` for a
subject-less incident, so the conditional write actually linearizes — two
spellings meant two winners and two issues for one condition. The driver's
pre-#2844 spelling `global.json` is retained as a **legacy alias**: every read
path consults it and every resolve deletes every spelling, so objects already in
R2 are adopted and cleaned up rather than stranded holding a closed issue's
number. Adoption is qualified by issue state on **both** sides (#3127): the
`driver` checks `gh_issue_open` and re-files when the recorded issue is closed
or 404, and the `AlertStore` reads the issue's own state via
`github_issue.issue_is_open` — a sentinel naming a CLOSED issue is dropped and
the incident re-filed. Positive evidence of closure is required: a failed state
read, or no state reader wired, counts as OPEN. Refusing to adopt on a blip is
the duplicate-issue defect #2844 exists to fix; adopting a stale sentinel is the
silent-outage defect #3127 exists to fix — prefer the failure that pages. A state
read is used rather than a search result because GH *search* is rate limited
(~30/min) and matches titles heuristically, so "absent from the results" is not
proof of closure. Subject-scoped incidents have exactly one key and are never
crossed with another subject (#2375). Resolution is delete-to-resolve — the
object is dropped so a recurring condition pages again instead of being
swallowed. The 412 create-race branch reads the recorded R2 object and its issue
state: an open issue is a no-op, a closed **or deleted (404)** issue re-files,
and a transient/rate-limited response is treated as open so a blip never
duplicates.

**Resolution authority is evidence-gated (#3127):** only the writer whose probes
cover a kind's recovery condition may declare it recovered — `KIND_OWNERS` in
`tortoise/alert_store.py`, mirrored by `kind_owner()` in `registry-cron.sh` and
pinned across the language boundary by `test_kind_owner_contract_with_driver`.
A caller may also clear a sentinel **it** opened, since its own probe observed
the condition being cleared. Anything else is refused and logged with the
sentinel left intact. This exists because one shared object let the weaker probe
win: the watcher's reachability check cannot distinguish "R2 unreachable" from
"the bucket cannot be listed" (the driver's `R2_LIST_OK=0` class), so its
all-clear would close the driver's `R2_DOWN` — a false recovery, and the driver
would then re-file on its next run: one duplicate issue + Telegram pair per
hour. Each sentinel records the `writer` that filed it for exactly this check; a
legacy object with no `writer` field is closable only by the kind's owner. See
`docs/adr/ADR-011-resolution-authority-for-dr-alerts.md`.

**Publication redaction:** `config_error`/`storage_error`, the raw sweep body,
the purge failure body and `last_sweep` (whose `graph_failures[].error` carries
raw per-graph exception text) are published into a **public** GitHub issue +
Telegram **and** the public Actions log. `redact()` normalises to one line and
scrubs credential *shapes*: URI/DSN userinfo (with or without a scheme, matched
to the last `@` of the token, so an empty username `docker://:pw@host` — this
repo's own DSN — and a password containing `/` or `@` are both covered),
`Basic`/`Bearer`/`token`/`ApiKey` headers, known credential **prefixes**
(`ghp_`, `github_pat_`, `glpat-`, `xox…`, `AKIA`, `sk-`, at ANY length so a
short or line-split PAT cannot survive), `*_KEY=`/`"token":"…"` assignments
(suffix-anchored, so `patch:`/`compatible:`/`author:` are not false positives),
quoted token values, ≥20-char token-like runs and filesystem paths — while
**preserving lowercase JSON keys, timestamps and graph ids**, so the published
`last_sweep` roll-up stays readable. Over-redaction is the deliberate
fail-safe direction for a **public** body: a value that merely *looks*
token-like is redacted too — a 26-char hex `team_id` (matched by the generic
≥20-char token rule), a ≥6-char single-quoted identifier (the historical
`(got 'AbCdEfGh')` rule), or a word that merely ends in a credential suffix
(`hockey:`) — so triage keys on the preserved `graph_id` rather
than the redacted `team_id`. `redact_truncate()` redacts *before*
truncating so a secret is never cut into a sub-threshold fragment. Known
residuals (regex-inherent, both bounded): a secret with no recognisable prefix
split by raw whitespace into fragments each under 20 characters, and a value
containing an embedded quote inside a JSON payload (the escaped `\"` defeats
quote pairing). The primary control for both is the source — the three
key-parsing sites emit a sha256 fingerprint, never the raw value.

**Credential requirement:** the driver **fails closed** if `GITHUB_TOKEN`
is unset — Actions does not export it into step envs, so
`.github/workflows/registry-backup-cron.yml` passes `${{ secrets.GITHUB_TOKEN }}`
explicitly (`permissions: issues: write`). Without it every incident POST/close
would 401 while the job could still look green.

**Regression harness:** `bash .github/scripts/registry-cron.test.sh` (CI: the
`backup-driver-scripts` job in `.github/workflows/ci.yml`, gated on the
`registry-cron*` + cron/ci workflow surface). It stubs `aws`/`curl`/`date` and
asserts all five states, the unmeasurable-pool rule, the 0-team envelope, the
stuck/unverifiable lock, unclassifiable-status and missing-token failsafes,
redaction (DSN/header/prefix/quoted/newline-split shapes and the
`compatible:`/`patch:`/`author:` false-positive guards, `last_sweep`, the purge
body), self-heal tiers (incl. `SWEEP_NO_COVERAGE`),
the dual-key delete, the multi-team tab-separated pool, the enabled+stale and
enabled+unmeasurable cases, and
dedup open/closed/404/blip/backfill. (The driver carries the exec bit so the
harness invokes it directly — a `$(bash script)` command substitution trips the
agent worktree guard, #1484.)

## Control plane / dialect (#2823)
Every backup + DR operator (sweep, purge, re-baseline, drill, scheduled drill, acl-reconcile, the watcher) resolves its TEAM LIST through **one dialect-aware seam** (`hosted_api._control_plane_source()`): the `SupabaseControlPlane` when `TORTOISE_CONTROL_PLANE=supabase`/Supabase creds are set, else the FalkorDB `registry_control_plane` graph. The dialect is recorded as `source` on every sweep result and in `ops/state.json`; the operator-facing read is `/status` → `last_sweep.source` (`last_run_source` carries the most recent run's dialect when a no-op run preserved an earlier real sweep's outcome fields; the raw run JSON rides the driver's `SWEEP_NO_COVERAGE` alert body).

**A 0-team sweep on the wrong dialect used to be indistinguishable from an empty deployment** — it enumerated the graph the #669 flip deleted and reported a benign `no_teams` for 31 days (#2823). The seam now REFUSES a registry-dialect source in the Supabase lane (`enum_failed`, loud — `tortoise/backup_sweep.py:212`), and the driver files `SWEEP_NO_COVERAGE` for an enabled-but-0-backup sweep whose 0 is not corroborated by a **measured-empty** R2 pool — the pool holds ≥1 team prefix, or could not be listed at all. Lane vars: `TORTOISE_CONTROL_PLANE` / `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` (`.env.example` §Control plane).

## Alert taxonomy + triage
| Kind | Meaning | Triage |
|---|---|---|
| STALE | a graph's newest archive is older than `BACKUP_STALE_THRESHOLD_MIN` (90) — subject `team` (default) or `team:graph` (custom) | Check sweep logs; run the sweep; R2 connectivity |
| NEVER_BACKED_UP | an active graph exists with no archive yet (custom-graph incidents carry `team:graph`) | Confirm graph is new/empty; if old, investigate |
| METADATA_LOST | archives exist but the graph's per-graph state object missing | Re-run sweep (state re-created) |
| BACKUP_SET_MISSING | state exists but no archives (bulk delete/erroneous prune) | Investigate R2; restore from a retained archive if possible |
| DRIVER_DOWN | driver heartbeat stale (> 4h) — workflow disabled/dead | Re-enable the workflow; GH 60-day auto-disable |
| R2_DOWN | R2 unreachable or not listable (driver-side signal: `head-bucket` failed, or it passed but `list-objects-v2` failed so the pool is unverifiable) | Check R2 creds/billing/bucket policy and the access key's `ListObjects` permission |
| ALERTER_DOWN | daemon's GitHub PAT dead (`gh_ok: false`) | Rotate `DR_ISSUES_PAT` |
| APP_DOWN | app unreachable from the driver | Fly health; cold-start OOM (#545) |
| WATCHER_DOWN | watcher heartbeat stale (daemon dead) | Check app logs; restart |
| SWEEP_CONFIG_ERROR | `enabled:false` **with** a non-null `config_error` — the sweep flag says "run" but `load_config()` raised (e.g. missing `REGISTRY_STREAM_KEY`). The pre-#2796 driver exited 0 here. Error text is redacted before filing | Fix the Fly secret/config (`§REGISTRY_STREAM_KEY`); the next healthy run self-heals |
| SWEEP_OFF_STALE | `enabled:false`, no config/storage error, and the pool is not **measured fresh**: a `backups/{team}/default/` archive older than `BACKUP_DRIVER_DOWN_THRESHOLD_MIN` (240m), a team prefix with no default archive at all, or a failed listing | Re-enable backups or declare a bounded pause; investigate why the flag is off. If a listing failed, check the R2 access key's `ListObjects` permission |
| SWEEP_NO_COVERAGE | `enabled:true` but the sweep backed up 0 teams (the #2823 empty-enumeration class: `no_teams`/`no_work`/`no_eligible_teams`/`enum_failed`/`error`), **or** the R2 pool could not be measured, **or** `/status` was unclassifiable, **or** a held sweep lock outlived `BACKUP_DRIVER_DOWN_THRESHOLD_MIN` (or cannot be verified) | Inspect `last_sweep` on `/status`; the sweep enumerates 0 teams → #2823 / #2340 control-plane resolution |
| LIVENESS_NO_WORK | driver ran but did nothing (sweep skipped + reconcile empty) — reserved kind, **not yet emitted by the driver**; the enabled-but-0-teams case is now SWEEP_NO_COVERAGE (#2796) | Verify teams exist; otherwise expected pre-beta |
| SIZE_GUARD_ABORT | team graph > 100k nodes — dump aborted | Investigate graph growth; raise limit deliberately |
| DATA_LOSS_CANDIDATE | a team's node count dropped >50% (or >0→0) | **Manual close only** — verify + re-baseline or restore |
| P0_GUARD_FAIL | a dump named the wrong graph or was empty — objects deleted | Investigate the sweep; alert auto-consolidates |
| RESTORE_DRILL_FAILED | the #2317 scheduled monthly drill failed or breached the ≤15-min RTO (subject `global`; detail carries team/archive/duration) | Investigate the drill record (`ops/drills/last.json`); re-drill after fixing the restore path; auto-resolves on the next successful/no-candidates scheduled run |

## Restore / drill
- **Drill endpoint:** `POST /v1/internal/backups/drill` `{team_id, backup_key}` — internal-key only; restores into `_drill_*` scratch (live-phase binds scratch; registry end-stamp skipped; ≥1h cooldown). Zero production writes — asserted server-side. The archive's key shape names its graph; the target resolves through the ACTIVE-graph seam — **drilling a deleted/quarantined graph's archive is refused (409)** (#2313 tombstone guard, #2304). #2317: every drill records pass/fail + measured restore time (`duration_s` / `rto_s` / `within_rto`) to `ops/drills/last.json` (surfaced on `/status` → `last_drill`) — restore time is measured against the committed ≤15-min RTO, not assumed.
- **Scheduled drill (#2317):** `POST /v1/internal/backups/drill-scheduled` (no body) — the monthly, unattended leg driven by `.github/workflows/registry-drill-cron.yml` (`23 4 1 * *`). The app auto-selects the OLDEST eligible NESTED archive across teams (`backup_sweep.list_drill_candidates` — 5-segment per-graph pools only; legacy flat 4-segment artifacts are operator-drill territory), skips candidates whose graph is no longer ACTIVE (tombstone guard), drills the first eligible one through the same core as the manual endpoint (cooldown + boot-GC backstop shared), and records the outcome. Failure or an RTO breach opens a deduplicated **RESTORE_DRILL_FAILED** incident (GH issue + Telegram via the app's own secrets — the workflow carries only the internal key, no R2/PAT creds); success and the `no_candidates` state resolve it. The wrapper `.github/scripts/registry-drill-scheduled.sh` makes the job green/red (429 cooldown and `no_candidates` are benign exits — the chronic 0-archive state is the existing LIVENESS_NO_WORK/NEVER_BACKED_UP alarm's job). Manual drills never file incidents (an operator is present).
- **ACL rebuild after full-platform restore:** a DR into a fresh FalkorDB server restores graph DATA from R2 — per-graph ACL server users do NOT live in the graph namespace. Run `POST /v1/internal/backups/acl-reconcile` (internal key) to replay the idempotent `create_acl_user` upsert for every active custom graph of every eligible team (default graphs ride the team-scoped ACL; tombstoned graphs never touched).
- **Production restore (`drill:false`) is NOT in scope (501)** — restore-and-rotate machinery retired with the registry (#669).
- **Rollout drill:** operator-invoked (documented commands in the drill workflow) or via the scheduled endpoint (`workflow_dispatch` against a seeded archive — the CI/schedule acceptance). Requires ≥1 team archive; in the chronic 0-teams state run against a seeded scratch graph or defer with a recorded reason (the drill record shows `no_candidates`). **Re-drill after any restore-path code change, R2 layout change, or key rotation.**
- **Mid-drill crash:** boot GC sweeps `_drill_*`/`registry_drill_*`/`*_restore_*`/`*_pre_restore_*` older than 6h.

## Operator actions
- **Dead knob removed (#2317):** `BACKUP_SKIP_FRESH_MIN` / `BackupConfig.skip_fresh_min` (the registry-era "skip window") was parsed but never consumed and is DELETED — its original double-dispatch protection is now the sweep in-flight 202 guard + per-team locks + retention prune, and an all-skipped run would have surfaced a misleading "no_work" headline (#2372 truthfulness). Do not re-introduce it.
- **Suppression:** write `ops/suppression.json` `{"KIND": {"until": "ISO"}}` to pause a kind.
- **Re-baseline:** `POST /v1/internal/backups/re-baseline` `{team_id}` (+ optional `graph_id`, default `"default"`) after verifying a DATA_LOSS_CANDIDATE is a false positive. Custom-graph incidents resolve under `"{team}:{graph}"`; the default under the bare team.
- **Simulate (staging):** `POST /v1/internal/backups/simulate-stale|recover` (gated on `BACKUP_SIMULATE_ENABLED`) — proves detection→filing→dedup ≤ 2× poll cadence.
- **Secrets:** `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/`DR_ISSUES_PAT`/`BACKUP_ALERT_ASSIGNEE` are Fly + GH secrets; the Telegram pair exists in both (daemon-side and driver-side legs). `BACKUP_SWEEP_ENABLED=true` is set by deploy-hosted.yml only when all required secrets are present (fail-closed).

### REGISTRY_STREAM_KEY — out-of-band Fly secret (#661)

**Purpose:** encrypts sweep backup archives (dump.enc) with a key that is
NEVER present in GitHub. This breaks the GH-trust-boundary dependency:
even a GH-capable collaborator cannot decrypt registry backup archives
because the key lives only on Fly, set by the operator out-of-band.

**Setup (operator, once):**
```bash
# Rotate/seed via the automation tool (recommended — generates + emits the
# exact commands; NO plaintext in this runbook):
uv run python tools/rotate-backup-keys.py --role registry_stream --emit-commands --fly-app tortoise-y4mjjq

# Manual equivalent (do NOT commit or share the value):
python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
fly secrets set REGISTRY_STREAM_KEY=<generated-key> --app tortoise-y4mjjq
```

**Deploy safety:** `deploy-hosted.yml` deliberately EXCLUDES
`REGISTRY_STREAM_KEY` (and the retained `REGISTRY_STREAM_KEY_PREVIOUS`,
#2318) from the secret-sync loop — there is no active negative check (the
workflow can't check Fly-side state), but the key is never read from
`secrets.*` in the YAML. A missing key causes the sweep endpoint
(`POST /v1/internal/backups/sweep`) to 503 fail-closed — the app boots and
serves normally, but no sweep backups are created until the key is set.

### Secret store + automated rotation (#2318)

Backup keys (`REGISTRY_STREAM_KEY` and `TORTOISE_BACKUP_KEY`) are managed
through a secret-store seam (`tortoise/secret_store.py`):

- **Providers:** `env` (default — Fly/GH env secrets, back-compat) and
  `file` (versioned 0600 store at `BACKUP_KEY_STORE_PATH` for
  selfhost/tests). Selection: `BACKUP_KEY_STORE=env|file`. The file layout
  (per-role version list, newest = active) maps 1:1 onto a cloud KMS.
- **Cloud KMS (AWS/GCP/Vault/Cloudflare) is the documented extension
  point** — no provider is implemented because this runtime has no KMS
  credentials. To add one: implement the `KeyStore` protocol against the
  cloud SDK and register it in `open_key_store()`;
  `BACKUP_KEY_STORE=kms` fails closed until then. The hosted deployment
  remains on Fly secrets (Fly's managed secret store) today.
- **Dual-key rotation:** a rotation mints a NEW active key while the old
  active key is RETAINED as the decrypt candidate (`*_PREVIOUS` env var or
  an older file version). During the overlap window BOTH old and new
  archives decrypt in-app — the app's restore path walks an
  active-first candidate chain per role (`_decrypt_candidate_keys` in
  `hosted_backup.py`), keeping the #661 cross-role seam (a sweep archive
  restores through the user-backup path and vice versa). Encrypt always
  uses the ACTIVE key.
- **No plaintext key material in code, git, or logs:** surfaces expose
  8-hex sha256 fingerprints only (same convention as the export header);
  test fixtures use synthetic keys; this runbook never contains a real
  value.

**Rotating `REGISTRY_STREAM_KEY` (operator, automated — dual-key window):**
```bash
# 1. Generate + stage (prints fingerprints; add --emit-commands for the
#    secret values; --verify-dump proves an OLD archive still decrypts
#    with the retained key):
uv run python tools/rotate-backup-keys.py --role registry_stream \
  --emit-commands --fly-app tortoise-y4mjjq
#    A single `fly secrets set` sets BOTH vars (new active + old retained):
#      fly secrets set REGISTRY_STREAM_KEY=<new> REGISTRY_STREAM_KEY_PREVIOUS=<old> --app tortoise-y4mjjq
# 2. Deploy to pick up the new secret value:
fly deploy --app tortoise-y4mjjq
# 3. VERIFY the rotation run: drill the OLDEST archive (must restore with
#    the RETAINED key — in-app, no manual decryption):
#      curl -sS -X POST -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY" \
#        -H "Content-Type: application/json" \
#        -d '{"team_id":"<team>","backup_key":"backups/<team>/.../dump.enc"}' \
#        https://api.premiselabs.co/v1/internal/backups/drill
# 4. AFTER the overlap window (old archives pruned/verified), purge the
#    retained key (second rotation does this automatically):
uv run python tools/rotate-backup-keys.py --role registry_stream \
  --purge --fly-app tortoise-y4mjjq
#    → fly secrets unset REGISTRY_STREAM_KEY_PREVIOUS --app tortoise-y4mjjq + deploy
```

> Pre-#2318 runbook note (corrected): old stream-key archives are NOT
> decryptable with `TORTOISE_BACKUP_KEY` after a rotation — each key is
> independent. Recovery requires the OLD key; with #2318 the old key is
> retained in-app (`REGISTRY_STREAM_KEY_PREVIOUS`) for the overlap window,
> so the drill path (above) replaces the old manual-decryption recovery.
> A second rotation drops the previous-previous key — verify old archives
> before rotating again.

**Rotating `TORTOISE_BACKUP_KEY`** (user-facing backups; GH-syncable): same
pattern with `--role backup`. `TORTOISE_BACKUP_KEY_PREVIOUS` is synced by
`deploy-hosted.yml` only when the GH secret is set (optional — a rotation
keeps the old value there until the overlap ends, then the operator clears
it from GitHub + Fly).

**Selfhost / file store:** `tools/rotate-backup-keys.py --role <role>
--store file --path <store>` rotates in place (atomic 0600 write, bounded
retention — active + one previous). `--purge` drops the retained version
post-overlap. Point the app at the store with `BACKUP_KEY_STORE=file` +
`BACKUP_KEY_STORE_PATH`.

**Verification (operator, post-setup):**
```bash
# Trigger a drill against the oldest archive to confirm the key works:
curl -sS -X POST -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY" \
  -H "Content-Type: application/json" \
  -d '{"team_id":"<team>","backup_key":"backups/<team>/.../dump.enc"}' \
  https://api.premiselabs.co/v1/internal/backups/drill
```

## Known residuals (accepted)
- **App down AND driver disabled simultaneously** — no alert (documented residual; reopen condition: first unattended app-down).
- **NEVER detection while the app is down** — daemon-only; a never-backed-up team during an outage is silent until recovery.
- **GH single-provider scheduling axis** — the driver terminates in GitHub; a GH incident delays backup triggers (the daemon still alerts).
- **R2 outage** — neither fabricates (UNKNOWN on fresh boot) nor silences (GH-search fallback + driver R2_DOWN) once a known-good baseline exists.

### Post-#2313 residuals (recorded 2026-09-06 audit — #2378)
- **Watcher heartbeat is per-team only** — `ops/watcher-heartbeat.json` carries the per-team tri-state; the watcher's per-graph states live in the daemon's in-process last-status (not persisted). Per-graph SWEEP outcomes (totals, failures, consecutive-error streaks) surface on `GET /v1/internal/backups/status` → `last_sweep` (#2372). A daemon restart loses the in-process per-graph watch until the next poll.
- **Legacy-flat mislabel under control-plane failure** — with the control plane down (or before a team's first legacy-flat classification index exists, ≤1 sweep after #2370 deploys), legacy flat archives on `GET /backups` fall back to the DEFAULT graph bucket even when they were C5-era custom dumps (#2370 index makes this the exception). Restore of a legacy flat custom archive is refused regardless (cross-graph guard).
- **Tombstoned-graph archive pools are never pruned** — per-graph prune runs only for enumerated ACTIVE graphs and the team-wide drain skips nested keys, so a deleted graph's `backups/{team}/{gid}/` pool accumulates until #2304's purge decision lands (its research item 4 covers backup-artifact disposition; the mechanism is recorded here).
- **Failing-default drain drops C5-era custom history (#2415)** — while the DEFAULT is failing, the sweep's legacy-flat cleanup prunes pre-#2313 custom-era FLAT dumps of ACTIVE customs that backed up THIS pass (their current data is protected by the same-run nested pool, but the flat was the custom's only PRE-cutover historical snapshot; tombstoned/errored/unresolvable flats are left in place — their disposition is #2304's purge decision).
