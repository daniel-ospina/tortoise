---
title: "Per-Graph Backup UX in the Hosted Dashboard — Research Brief"
type: synthesis
domain: ux
doc_status: live
created: 2026-09-10
issue: 2784
related:
- 2339
- 2790
- 2796
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise, dashboard, backup-pipeline, hosted-api
---

# Per-Graph Backup UX in the Hosted Dashboard — Research Brief

**Findings-date:** 2026-09-10
**Trigger:** The product owner asked to relocate the `Backups` stat card off the API Keys tab (`main.jsx:348-369`, rendered `:7285`) to Graphs, but challenged the premise: *"backups are a property of each graph, no? So research what UX makes sense."*
**Routing:** `--domain=ux` inferred (the research skill's registered wiki path for that domain is `docs/07_ux/wiki/`; this repo has no such tree yet). Output filed to `docs/research/` per the explicit task path.
**Depth:** Medium (research skill) — internal-first, 5 external Perplexity queries (`sonar`), adversarial queries included.
**Epistemic checkpoint (Step 1.7):** `tortoise_unavailable` (hosted `/v1/search` 404) → prior-claim query skipped; no prior epistemic claims retrieved. Fresh research.

---

## 0. Problem Reframing (Step 0)

**Reframed problem statement:**
> A Tortoise Pro team owner/admin trying to answer *"is each of my graphs protected, and can I get my data back?"* but the dashboard shows a **single pool-wide count rendered as the literal string `none` when zero**, on the **API Keys** tab, with **no graph identity, no freshness, no entitlement state, and no restore affordance** — which results in **false confidence** (a number must mean something) or **false alarm** (`none` even though a custom graph was swept).

**5 Whys:**
1. Why relocate it? It is a leftover sitting on API Keys.
2. Why is it on API Keys? #2000 (W4) decluttered the Overview to exactly 3 elements; the card was moved "so the count stays reachable" (`docs/plans/2026-09-02-2000-W4-onboarding-plan.md:24`).
3. Why was "reachable" the only requirement? The Overview simplification shipped without designing a replacement home.
4. Why is that a problem? A **pool-wide count is the wrong unit** — backups are graph-scoped, so a count cannot tell you *which* graph is protected.
5. Why does the unit matter? Because #2313 made the artifact model and `GET /backups` per-graph, but the UI never caught up; the only per-graph backup UI today is the **trash Inspect panel** for *deleted* graphs (`main.jsx:7566-7615`).

**How Might We:**
- HMW surface per-graph backup health **inside the Graphs tab** without adding a tab or a bulk count?
- HMW make "am I protected?" answerable **at a glance** from where graphs are managed?
- HMW keep the surface **honest** across never-backed-up / healthy / failing / stale / not-entitled states?
- HMW avoid a new load-bearing endpoint by reusing the `GET /backups` payload that already carries `graph_id`/`kind`?

**Assumption map:**

| Assumption | Tag |
|---|---|
| Backups are graph-scoped (object keys + manifests carry `graph_id`) | `[validated]` — `hosted_backup.py:794-910`, #2313 |
| `GET /backups` returns per-entry `graph_id` + `kind` to the client | `[validated]` — `hosted_api.py:19116-19135`, `:19241-19244` |
| The dashboard already receives that data but discards all but `count` + `latest` | `[validated]` — `main.jsx:4860-4862` |
| Only deleted (trash) graphs have a per-graph backup UI today | `[validated]` — `main.jsx:7566-7615` |
| Custom-graph self-service restore does not exist yet | `[validated]` — #2339 open; `hosted_api.py:19488-19492` |
| `GET /backups` is **not** tier-gated, so free users see the same empty `none` | `[validated]` — `hosted_api.py:19187` uses `get_current_team_session_ungated`, no `_require_backup_tier` |
| Production `hourly_backups` is `true` for Pro/Team | `[unverified]` — repo `pricing.json` says `"planned"` (`product/pricing.json:82,105`) |

**Reverse-the-problem:** What if the dashboard showed *no* aggregate backup number at all, and instead the **Graphs tab told you per-graph protection status**? That constraint dissolves the relocation question entirely — it is the framing this brief adopts.

---

## 1. What We Have Internally (codebase, docs, prior decisions)

### 1.1 Dashboard tab structure & reusable patterns

- **7 live tabs + 1 archived wizard:** `KNOWN_TABS = ['overview', 'keys', 'graphs', 'members', 'billing', 'settings', 'profile']` (`main.jsx:774`); tab nav at `:6454`.
- **API Keys tab** hosts the keys table and, at its tail, `<BackupsCard status={backupsStatus} count={(backupInfo && backupInfo.count) || null} />` (`main.jsx:7285`). `BackupsCard` (`:348-369`) has a 15 s local loading floor and renders `status === 'ok' ? (count || 'none') : …`.
- **Graphs tab** (`main.jsx:7289`): a table with columns **Name · Kind · Status · Keys · Actions** (`:7337`). Row action patterns already in place:
  - **Inline rename** pencil ✏️ (Enter/blur commits, Escape cancels) — `:7343-7368`.
  - **Keys** button expanding an **inline `graph-key-panel`** (`:7620`) — `aria-expanded={panelGraphId === g.graph_id}` — the established per-graph detail pattern.
  - **🗑 delete** with a **type-to-confirm modal** (`delete` typed by hand), default graph's trash disabled with a tooltip reason — `:7460-7505`.
  - **Trash section** (`<details className="trash-section">`, `:7508`) with per-row **Restore** (confirm-inline) + **Inspect** (`:7520-7560`).
- **The trash Inspect panel is already a per-graph backup surface** (`:7566-7615`): it renders `archive_count`, `latest_backup.created_at`, `node_count`/`edge_count`, and honest empty/dump-only states ("No backups yet for this graph", "Backups exist — no readable manifest for details"). It is **read-only**, **owner/admin-only**, and **deleted-graph-only**.
- **Settings tab** (`SettingsTab`, `:379`) has exactly four homes: Setup guide, GitHub connect, Memory sources, Captured sessions (`:402-470`). No backup home exists.
- **Established honesty conventions:** skeleton → terminal `'—'` on error, `role="alert"` per-row errors, `aria-live="polite"` status lines, `dim` empty states, disabled controls wrapped in a tooltip span to explain "why".

### 1.2 Backup API surface — exact field semantics

**`GET /backups`** — `hosted_api.py:19186-19248`
- Auth: `get_current_team_session_ungated` (`:19187`) — session JWT **or** `tt_` key; **no tier gate**. Team-scoped by `team["team_id"]` only.
- Body: `{"backups": [ <manifest>, … ]}` (`:19241-19244`), newest-first (`hosted_backup.py:958`).
- Each manifest is the raw `create_backup` payload (`hosted_backup.py:894-909`) **enriched** by `_manifest_graph` (`hosted_api.py:19116-19135`) with:
  - `backup_id` — `{team_id}/{graph_id}/{ts}_{rnd}` (`hosted_backup.py:794-804`)
  - `graph_id` — canonical key segment; `"default"` for legacy flat manifests
  - `kind` — `"default"` | `"custom"`
  - `graph_name` — resolved namespace (`team_{id}` / `team_{id}_{gid}`)
  - `created_at` — dump `dumped_at` (ISO)
  - `node_count`, `edge_count`, `sha256`, `format`, `team_id`
- **Not pool-wide in the sense of cross-team** — it is one team's pool, but **pool-wide across that team's graphs**. Legacy flat artifacts are read-bucketed to `default` with a #2370 classification-index reverse lookup (`:19205-19238`).
- **No freshness, no failure, no retention, no "entitled" field** in the response. The client must derive those.

**`POST /backups`** — `hosted_api.py:19294-19430` — on-demand dump; Pro-tier gate `_require_backup_tier` (`:19075-19090`, checks `hourly_backups`), `graphs:read` scope, graph-bound keys dump their own graph (`:19320-19375`), then per-graph prune.

**`POST /backups/restore`** — `hosted_api.py:19433-19569` — **default-surface only**; session auth; `confirm=true` required; `graphs:write` scope; rejects graph-bound keys (`_reject_graph_bound_team_surface`) and **explicitly refuses custom-graph keys**: *"restoring a custom graph from this endpoint is not supported — graph-bound restore required"* (`:19488-19492`). `BackupRestoreRequest {backup_key, confirm}` (`:2827-2830`).

**Trash Inspect (per-graph backup read)** — `GET /v1/graphs/trash/{graph_id}/points` (`hosted_api.py:10484-10575`): returns `archive_count`, `latest_backup {backup_id, created_at, node_count, edge_count}`, `deleted_at`; counts per-run dirs + flat archives, dump-only runs included (#2469).

**Watcher per-graph states (server-only)** — `backup_watcher.py:436-475` computes `per_graph[key] ∈ {ok, stale, never, stamp_missing, backup_set_missing}` and exposes them only on the **internal-auth** `GET /v1/internal/backups/status` (`hosted_api.py:19900` via `_check_internal`). The dashboard cannot read this endpoint.

**`GET /v1/graphs`** — `hosted_api.py:10576-10636`: returns `{graph_id, name, kind, status, recording, key_count}` — **no backup field**.

### 1.3 Entitlement

- Tier gate: `_require_backup_tier` → `tortoise.pricing.hourly_backups_enabled(tier)` (`pricing.py:104-121`). **Strict boolean**: `pricing.json` uses the string `"planned"` to mean *not live*; only JSON `true` unlocks. In-repo, **no tier is `true`** — free/solo/anon `false`, pro/team `"planned"` (`product/pricing.json:36-37,57-58,81-82,104-105,123-124`).
- Automated sweep eligibility is a **separate** signal: `tier != 'free' AND backup_enabled = true` on the Team node (`backup_sweep.py:206-222`).
- The card currently renders no entitlement state at all; a free user and a Pro user with no backups both see **`none`** (`main.jsx:360`).
- Dashboard `pricing.js` imports `product/pricing.json` but exposes only plan limits, **not** backup features (`website/apps/dashboard/src/pricing.js`).

### 1.4 Existing tests

- `tests/e2e/hosted/test_05_backup_restore.py` — **team-default graph only**: POST → mutate → restore round-trip, plus 402/400 negatives. No per-graph coverage.
- `website/apps/dashboard/src/overviewSettings.test.js:43` — asserts `"Backups"` is **gone from the Overview**; it does not assert where it should live or what it shows.
- Watcher tests cover per-team + per-graph tri-state (`tests/test_backup_watcher.py`); no dashboard test exists for `BackupsCard` content.

### 1.5 Prior decisions & adjacent issues (what we already concluded)

- **#2313 (closed, critical):** per-graph sweep landed — artifacts keyed `backups/{team}/{graph}/{ts}` (`backup_sweep.py:921-922`, `hosted_backup.py:794`; enumeration `backup_sweep.py:264`), per-graph state + retention, watcher per-graph freshness. Scope doc `docs/scoping-2313-per-graph-backups.md` **Q3 owner decision (2026-09-06):** *"dashboard = keep the summary Backups card + enrich GET /backups response with per-graph metadata; per-graph UI rows are a follow-up issue (not absorbed)."* **The enrichment shipped; the follow-up never did.**
- **#2339 (open):** graph-bound self-service restore for custom graphs. `POST /backups/restore` refuses custom keys; no graph-bound restore endpoint exists. A wiped custom graph currently has **archives but no self-service recovery**.
- **#2304 (closed):** trash-can semantics + 7-day window; the Inspect panel is the read-side rescue surface.
- **#2373 / #2372:** retention labels were false (hour-bucket vs documented) and the sweep headline hid per-graph failures — both fixed; **cadence is hourly** (#2317), not nightly.
- **#2000 (open, implemented):** the Overview calm that caused the relocation.

---

## 2. External Findings by Theme (Perplexity, `sonar`)

**Theme A — Backups belong in the resource's context, never account settings. [HIGH]**
Every comparable surfaces backup/restore in the resource scope, not a global settings area: **Supabase** `Database → Backups` per project (daily, 7/14/30-day retention; PITR under *Point in Time* with earliest/latest recovery points); **Neon** project/branch-level restore (PITR or snapshot); **PlanetScale** database/branch UI; **Vercel Postgres** database resource page; **GitHub** repository-scoped; **Fly.io** per-volume snapshots; **MongoDB Atlas** per-cluster backup + cluster-level PITR. IBM Cloud Databases likewise puts a dedicated *Backups and restore* tab on the **instance dashboard**. Sources: Supabase docs, Neon docs, PlanetScale docs, Vercel docs, GitHub docs, Fly.io docs, MongoDB Atlas docs, IBM Cloud docs. (7+ independent vendor docs agree.)
→ Strongly supports a **per-graph** affordance on the Graphs tab; argues against a global Settings → Backups home.

**Theme B — Show recovery points & retention, not a count. [MEDIUM] ⚠️ emerging** (3 external sources: Supabase, Atlas, IBM; internal corroboration is partial — the trash Inspect panel renders recovery detail, `main.jsx:7589-7598`)
Comparables present *available recovery points* (timestamps/earliest-latest), retention windows, and per-backup detail tables (IBM: table of backups + per-backup details; Supabase: retention + PITR window; Atlas: cluster retention). A bare integer never appears as the primary backup surface.

**Theme C — Health states, not backup counts. [HIGH]** (≥4 independent sources)
Backup tooling dashboards consistently surface **resources without snapshots / stale snapshots / up-to-date** (IBM Storage Defender), **Success / Running / Warning / Error** plus most-recent-backup (Salesforce Trailhead), **"Not Backed Up"** (Arcserve), and **connector health + time of latest snapshot** (Keepit). The recurring vocabulary is freshness + last-success + health, not volume.

**Theme D — Restore is a distinct, confirm-gated workflow. [HIGH]** (≥4 independent sources)
UX Patterns Guide ("Restore from trash") recommends a durable deleted-items surface supporting restore-to-original or to-a-chosen-location and destructive confirmation before purge. LogRocket's reversible-actions framework distinguishes low-friction undo from critical system-level recovery (confirmation/rollback by impact). SQL Server / Autobase PITR is a *separate* workflow with an explicit time selection. Restore-into-new is a common safety variant (also AWS multi-tenant sample: restore a tenant backup into a fresh tenant).

**Theme E — Adversarial: "backup count" is a misleading primary metric. [MEDIUM] ⚠️ emerging** (3 graded sources; 2 further UX anecdotes not counted toward the tier)
- Backups must be **tested/verified**; an untested backup may be corrupt/partial — a count says nothing about recoverability (CDW "10 common mistakes"; "7 deadly sins of backup and recovery").
- The real unit of recovery is the **application/service**, defined operationally, not the backup job (rack2cloud "Restore Design Failure").
- **Corroborating UX anecdotes (not graded sources):** backup tools mislead when *setup context ≠ restore-time reality* (Duplicati forum); users need *what is recoverable now and how safely* (uxdesign.cc forgiveness framing).
→ A per-row "3 backups" count would repeat the current card's sin at finer granularity.

**Theme F — Multi-tenant guidance: per-tenant restoreability + honest tenant boundaries. [MEDIUM] ⚠️ emerging** (2 sources)
Multi-tenant SaaS guidance (Frontegg; Codewheel) stresses per-tenant restoreability, rehearsed restores into staging, and that *"a backup is not sufficient until restoration preserves tenant ownership."* A cross-tenant explorer marks backups **active vs orphaned** — the same honesty requirement for legacy/ghost archives.

---

## 3. Recommendation

### 3.1 Primary: per-graph backup status **in the Graphs tab rows**, with an inline per-graph Backups panel

Concretely:

1. **Remove `BackupsCard` from the API Keys tab** (`main.jsx:7285`, `:348-369`). A pool-wide count on a key-management tab is the wrong unit and the wrong home; #2000's "stays reachable" rationale is satisfied on Graphs instead.
2. **Add a `Backups` cell to the Graphs table** (currently Name · Kind · Status · Keys · Actions, `:7337`). The cell shows **freshness/health, not a count** — one of the honest states in §3.3 — with a tooltip carrying cadence + retention (e.g. *"Hourly sweep · RPO ≤1h · keeps ≈24h/7d/4w per graph"*, matching `docs/ops/registry-backup-dr.md:265`).
3. **Add a per-row `Backups` action** that opens an **inline `graph-key-panel`** — reusing the exact Keys-panel pattern (`:7620`, `panelGraphId` state) so there is no new navigation primitive. The panel lists archives (timestamp · nodes · edges) and owns the **Restore** affordance.
4. **Demote the count to secondary text inside the panel** ("12 archives · oldest 4w"), never a headline.
5. **Derive per-graph status client-side from the existing `GET /backups` payload** for v1: group `b.backups` by `graph_id`/`kind`, take the newest `created_at` per graph, compare to a freshness threshold. `main.jsx:4860-4862` already receives the full list and throws it away. No new endpoint needed for v1. (Durable option if freshness must match the watcher exactly, including degraded-R2 honesty: a team-scoped `GET /backups/status`, or `last_backup_at`/`backup_state` on `GET /v1/graphs`.)
6. **No global Settings → Backups home at v1.** It would be a third surface contradicting the per-resource pattern (Theme A). Revisit only if account-level retention policy or per-team storage budgets ship (#2313 Q5 optional knob).

### 3.2 Restore placement

- **Lives in the per-graph Backups panel as a row action**, not a global flow (Theme A/D; mirrors the trash **Restore** button at `main.jsx:7520-7535`).
- **Default graph:** wire to the existing `POST /backups/restore` (`hosted_api.py:19433`) behind the established destructive-confirm pattern (type-to-confirm or an explicit *"replaces the live graph"* warning, as the trash restore already does at `:7526-7532`). `confirm=true` is already required server-side.
- **Custom graphs:** render the Restore action **present but disabled** with the honest reason ("Custom-graph restore is coming") until **#2339** ships — never a dead 0/absent affordance. For a **deleted** custom graph inside the 7-day window, the existing **trash Restore** remains the path (#2304 Q2).
- **#2339 interaction:** the per-graph Backups panel is precisely where #2339's graph-bound restore belongs. Build the panel + gated affordance now so #2339 is a server-side flip + UI enablement, not a new surface.

### 3.3 Honest per-graph states (map to the watcher's `per_graph` vocabulary)

| State | UI treatment | Server signal |
|---|---|---|
| **Protected** | live chip · "Updated {relative}" | newest archive fresh (`ok`) |
| **Stale** | warn chip · "Stale — last {relative}" | `stale` (age > threshold) |
| **Never backed up** | dim/needs-attention · "Never backed up" | `never` |
| **Backups missing** | error chip · "Archives missing — restore at risk" | `backup_set_missing` (#2374) — **watcher-only, NOT reachable in v1** (deferred with the status endpoint, §3.5) |
| **Status unknown** | dim · "Backed up — status unknown" | `stamp_missing` is **watcher-only in v1**; v1 renders this copy only as the honesty fallback for an archive set it received but cannot classify |
| **Status unavailable** | dim · "Status unavailable" | **top-level** watcher health flag `unknown` / `r2_ok=false` (`backup_watcher.py:207,215,359`) — **not** a `per_graph` value. Per-graph R2/control-plane degradation is indistinguishable from `stale` client-side (`:459`). **Never fabricate never/failing.** |
| **Not on this plan** | locked chip · **copy is conditional** on the §4 evidence: `hourly_backups` still `"planned"` in production → "Backups are coming to Pro" (no CTA); flag shipped `true` → "Backups aren't enabled on your plan" + Upgrade CTA | `hourly_backups` not `true` **or** team `backup_enabled=false` — **neither is currently client-readable**; v1 needs the minimal plumbing in §3.5 |

The `per_graph` vocabulary is `{ok, stale, never, stamp_missing, backup_set_missing}` (`backup_watcher.py:436-475`); the last two rows above are **client-derived / non-`per_graph`** states and are labelled as such. The "Not on this plan" row is the honesty fix for today's `none`: an empty list must **not** read as "protected but empty" to an unentitled user, nor as "unprotected" to a user whose sweep is merely disabled.

### 3.4 Alternatives evaluated and rejected

| Option | Verdict | Why |
|---|---|---|
| **(b) Graph detail view / modal as the primary home** | **Rejected (folded into a)** | The Graphs tab has no row-detail route; a modal duplicates the existing inline `graph-key-panel` pattern and adds navigation cost for no gain. The inline panel achieves "detail without leaving the list". |
| **(c) Global Settings → Backups home with per-graph breakdown** | **Rejected for v1** | Contradicts the per-resource pattern every comparable uses (Theme A); makes protection status invisible exactly where graphs are managed; adds a third surface. Keep only for account-level retention policy. |
| **(d) Keep a summary card, move it to Graphs** | **Rejected as primary** | A pool-wide count on Graphs is still the wrong unit — it cannot answer *which* graph. At most a roll-up heading ("3 of 4 graphs protected"); not the surface. |
| **Per-row bare "N backups" count** | **Rejected** | Repeats the current card's sin at finer granularity; count ≠ recoverability (Theme E) and `0` is ambiguous across never-ran / failing / not-entitled. |
| **Keep card on API Keys unchanged** | **Rejected** | Sustains the leftover: wrong unit, wrong home, dishonest `none`. |

### 3.5 Scope boundary

This brief designs the **UI**. It does **not** implement #2339's endpoint, change `GET /backups`, or touch retention. If the client-side freshness derivation is rejected, the follow-up server change (`GET /backups/status` team-scoped, or `GET /v1/graphs` enrichment) should be filed as its own issue.

**Entitlement plumbing (required for the "Not on this plan" state).** Neither signal is currently client-readable: `pricing.js` exposes only plan limits, and `TeamInfoResponse` (`hosted_api.py:2703-2740`) returns no `hourly_backups`/`backup_enabled`. Two options, in preference order:

- **(a) Minimal plumbing in scope for v1:** export a `backupFeatures(tier)` helper from `pricing.js` (it already bundles `product/pricing.json` — no server change) and add the additive `backup_enabled` field to `TeamInfoResponse`/`GET /v1/team` (the value is already written on Team nodes, e.g. `hosted_api.py:1212`, but never returned). This is the only option that makes the row implementable.
- **(b) Defer the state:** if the server field is rejected, drop the "Not on this plan" row from v1 and carry it into the same follow-up issue as `GET /backups/status`. The remaining v1-reachable states reduce to **fresh / stale / never**, plus two honesty fallbacks derived from the list (unclassifiable archive set → "status unknown"; degraded R2/CP → "status unavailable"). The `backup_set_missing` and `stamp_missing` distinctions arrive only with the status endpoint.

---

## 4. Confidence Tiers

**Tier rule applied uniformly:** **High** = corroborated by internal code, OR ≥4 independent external sources forming a consensus. **Medium** ⚠️ emerging = external-only with 2–3 sources in one category, OR a synthesis/inference with no direct supporting source. **Low** ⚠️ single-source = 1 source. **Speculative** = LLM memory only. "Independent sources" = separate products/authors; multiple vendor docs on one point are independent even though they share the "vendor documentation" category.

- **Backups are graph-scoped and `GET /backups` already carries `graph_id`/`kind`** — **High** (internal code `hosted_api.py:19116-19241`, `hosted_backup.py:794-909`; #2313; corroborated by the per-resource pattern in 8 comparables).
- **Backup/restore belongs in resource context, not global settings** — **High** (8 independent vendor docs: Supabase, Neon, PlanetScale, Vercel, GitHub, Fly.io, MongoDB Atlas, IBM).
- **Retention/recovery-points, not a count, is the right primary display** — **Medium** ⚠️ emerging (3 vendor docs: Supabase, Atlas, IBM; internal corroboration is partial — the trash Inspect panel already renders recovery detail, `main.jsx:7589-7598`, but it is a deleted-graph surface). Salesforce Trailhead is a *health-state* source (Theme C), not a recovery-point source, so it is not counted here.
- **Health/freshness beats raw count as the primary metric** — **High** (≥4 independent external sources: IBM Storage Defender, Salesforce Trailhead, Arcserve, Keepit).
- **Restore should be a distinct, confirm-gated per-resource action** — **High** (≥4 independent external sources: UX Patterns Guide, LogRocket, SQL Server/Autobase PITR, AWS multi-tenant sample; partly corroborated by the internal trash restore confirm pattern).
- **Recommended placement (a): per-graph Backups cell + inline panel** — **Medium** ⚠️ emerging (synthesis; no source states this exact placement — inferred from Theme A + internal reuse of `graph-key-panel`).
- **Derive per-graph status client-side from `GET /backups`** — **Medium** ⚠️ emerging (synthesis; the payload fields are validated internally, but grouping scale is unverified — ~35 archives/graph × unlimited graphs, `product/pricing.json:68,92`).
- **Entitlement must be an explicit state** — **High** (internal: ungated list + `"planned"` flags make the current `none` provably ambiguous; but note the client plumbing gap in §3.5).
- **Adversarial caveat: count is misleading** — **Medium** ⚠️ emerging (3 external sources, single category).
- **Multi-tenant guidance: per-tenant restoreability + honest boundaries** — **Medium** ⚠️ emerging (2 sources: Frontegg, Codewheel; contextual — informs the orphaned/legacy-archive honesty requirement, not the placement decision).
- **Production `hourly_backups` value** — **Low** ⚠️ single-source (repo says `"planned"`; a production override may exist — **verify when a production pricing source is available**).

### Required Evidence — production `hourly_backups` value

- Confirm the production `product/pricing.json` (or env `TORTOISE_PRICING_PATH`) value of `tiers.pro.features.hourly_backups` and `tiers.team.features.hourly_backups` before the "Not on this plan" copy ships. If it is `true` in prod, the state is reachable only for free/solo; if `"planned"`, **all** tiers currently show it and the copy must say "Backups are coming to Pro", not "Upgrade to Pro".

---

## 5. Contradictions

1. **Flag name drift.** The task brief says entitlement is `daily_backups`; the code renamed it to **`hourly_backups`** (#2317) and `pricing.py:104-121` reads only `hourly_backups`. Older test names/docstrings still say `daily_backups`. **Resolution: use `hourly_backups`; `daily_backups` is stale.**
2. **Prior owner decision vs this recommendation.** #2313 Q3 decided *"keep the summary Backups card"*; this brief recommends **removing** it in favor of per-graph rows. **Resolution: deliberate supersession** — Q3 predates the per-graph implementation landing and named the rows as a follow-up that was never filed. Flag for owner confirmation.
3. **Cadence wording.** #2313, #2372, #2373 titles say "nightly/daily"; the driver is **hourly** (`docs/ops/registry-backup-dr.md:71`). **Resolution: UI copy must say hourly**, never nightly/daily.
4. **Entitlement copy vs pricing state.** The server 402 says *"Backups are a Pro feature — upgrade"* (`hosted_api.py:19089`) but `pricing.json` marks pro/team `hourly_backups:"planned"`. **Resolution: verify production before promising Pro entitlement in the UI copy** (see Required Evidence).

---

## 6. Source Confidence Summary

| Claim | Tier | Independent sources |
|---|---|---|
| Backups are graph-scoped; payload carries `graph_id`/`kind` | High | 2 (internal code + #2313) + comparables pattern |
| Backup/restore UI belongs in resource context | High | 8 vendor docs (Supabase, Neon, PlanetScale, Vercel, GitHub, Fly.io, Atlas, IBM) |
| Retention/recovery-points, not count, is the primary display | Medium ⚠️ emerging | 3 (Supabase, Atlas, IBM) + partial internal Inspect |
| Health/freshness states beat backup counts | High | 4 (IBM, Salesforce, Arcserve, Keepit) + adversarial |
| Restore = distinct, confirm-gated, per-resource | High | 4 (UX Patterns Guide, LogRocket, SQL Server, AWS sample) |
| Count is a misleading metric | Medium ⚠️ emerging | 3 (CDW, "7 deadly sins", rack2cloud) |
| Multi-tenant restoreability + honest tenant boundaries | Medium ⚠️ emerging | 2 (Frontegg, Codewheel) |
| Recommended placement (Graphs rows + inline panel) | Medium ⚠️ emerging | synthesis (no direct source) |
| Client-side derivation from `GET /backups` | Medium ⚠️ emerging | internal validation (unverified at scale) |
| Entitlement must be explicit | High | 2 (ungated list, `"planned"` flags) |
| Production `hourly_backups` value | Low ⚠️ single-source | 1 (repo file) |

---

## 7. Open Questions (owner)

1. **Confirm the supersession of #2313 Q3** — remove the summary card rather than keep it. (Blocks implementation choice.)
2. **Production entitlement state + client plumbing** — is `hourly_backups` live for Pro/Team, and do we take scope option (a) (export the flag from `pricing.js` + add `backup_enabled` to `/v1/team`) or (b) (defer the "Not on this plan" row to the follow-up)? Determines whether the state ships in v1 and what the CTA says.
3. **Freshness threshold for the client-side chip** — reuse the watcher's `BACKUP_STALE_THRESHOLD_MIN=90`, or a UI-specific staleness? (Server constant lives in `backup_config.py`; needs re-export or duplication.)
4. **Custom-graph Restore gating** — show disabled-with-reason until #2339, or hide entirely? (Recommend disabled-with-reason per the repo's existing locked-🗑 tooltip pattern.)
5. **Scale** — team-scoped `GET /backups` may become heavy for unlimited-graph teams; does v1 client-side grouping need a `GET /backups/status` sooner?
