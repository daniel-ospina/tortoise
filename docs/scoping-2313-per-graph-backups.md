---
title: "Scoping #2313 — Per-Graph Backup Coverage (fix the default-only sweep)"
type: decisions
domain: platform
doc_status: live
created: 2026-09-06
issue: 2313
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutObjects:
- tortoise-hosted-platform
---

<!-- issue-scoping: v5.1 double diamond front-half deliverable (problem + solution diamonds, evidence-cited).
     Full verify gates (problem-verify / solution-verify) + parallel review run in the parent session on this artifact. -->
# Scoping #2313 — Per-Graph Backup Coverage (fix the default-only sweep)

> **Verdict: root-cause claim CONFIRMED on current main.** The backup sweep (hourly driver) enumerates **teams** and resolves **exactly one graph per team** (the default). Custom graphs (`kind='custom'`, namespaces `team_{tid}_{gid}`) are never enumerated by the sweep, so they have **no automated backup, no drift guard, no retention**. The C5 (#2114) graph-bound on-demand path is the *only* graph-aware backup path, and it still stores into the shared team-keyed pool. Evidence below.

## Confirmed Problem

> A Pro/team account whose developer data lives in custom graphs gets backup artifacts for **only the default graph** (`teams.graph_name` / `team_{team_id}`). The custom graphs — the isolation-critical per-customer data the multi-graph epic exists for — are never dumped by the scheduled sweep, never get per-graph state/drift-guard/retention, and can be destroyed by any write bug or operator error with **no recovery path**, while the operator believes backups exist because the default graph is covered.

### Why It Is Broken — code evidence (current main)

**1. The sweep enumerates TEAMS, then resolves ONE graph per team.**
- `tortoise/backup_sweep.py:141` `enumerate_teams` — Supabase mode reads only `teams` (`select=["id","graph_name"]`, :156); registry mode `MATCH (t:Team) RETURN t.id` (:158). `enumerate_eligible_teams` (:164) adds tier/backup_enabled filters on `teams` (:178-179, :183). **Zero references to the `graphs` seam** (`graph_list`/`graph_metadata`) anywhere in backup_sweep.py (grep-verified).
- `team_graph_name` (backup_sweep.py:190) resolves **one** graph name per team: Supabase `teams.graph_name` (:208-221), registry deterministic `team_{team_id}` (:221). Docstring (:18-19) states the model: one graph per team.
- `run_backup_sweep` (:423) loops team ids → resolves one `graph_name` (:511-517) → `_backup_team` (:254) → one `create_backup` (:303). Per-team serialization via the caller's lock factory; per-team state at `ops/teams/{team_id}/state.json` (`_TEAM_STATE_PREFIX`, :50; write at :391).
- Consequence: the drift guards (empty-content transition, >50% drop, per-label #661) run **only inside `_backup_team` on the single resolved graph**. A wiped custom graph fires nothing — no dump is attempted, no state exists to compare, no incident.

**2. Every backup primitive is team-keyed; nothing downstream is graph-aware.**
- `tortoise/hosted_backup.py:540` `create_backup` — `backup_id = f"{team_id}/{ts}_{rnd}"` (:563); objects at `backups/{backup_id}/dump.enc|manifest.json` (:576-587). The **only** graph reference is manifest content (`graph_name` in the manifest dict, :568).
- `list_backups(storage, team_id)` (:599) lists the team prefix `backups/{team_id}/` (:603).
- `prune_backups(storage, team_id, keep_daily/weekly/hourly)` (:932) walks the team prefix (:966) and parses the flat 4-part key `backups/{team}/{ts}_{rnd}/manifest.json` (:976-986). **Retention math is per-POOL, not per-graph.**
- `restore_backup` (:819): cross-team guard `backup_key.startswith(f"backups/{team_id}/")` (:860); manifest `team_id` check (:856); **cross-graph guard exists and is sound** — manifest `graph_name` must equal the requested `graph_name` (:867-872) and the authenticated payload repeats it (:915-921). But the only caller-supplied graph name today is the team default (see #3).
- Registry stamps are team-row stamps: `_stamp_backup_latest` PATCHes the `teams` row / SETs the Team node (:500-538). There is no per-graph stamp surface.

**3. Hosted endpoints: list/restore are default-graph-only; only the C5 on-demand dump is graph-aware.**
- `tortoise/hosted_api.py:16377` `GET /backups` (backups_list) → `list_backups(..., team_id)` — team-wide, default-keyed.
- `POST /backups` (backups_create, :16442) — team-wide keys/session back up the **default** via `team_graph_name` (:16493); a **graph-bound key (C5 #2114)** backs up ITS OWN graph (`graph_namespace`, fail-closed `GRAPH_NOT_FOUND` 403 when vanished, :16489-16500) — **the only graph-aware backup path that exists**. Its artifacts still land in the shared team-keyed pool and share the default graph's prune pool (prune at :16517 with no graph dimension).
- `POST /backups/restore` (backups_restore, :16553) — resolves `team_graph_name` (default only, :16594-16599) and **rejects graph-bound keys** via `_reject_graph_bound_team_surface` (:16563). Restoring a custom graph's backup into itself is impossible through this endpoint today.
- Internal sweep driver: `POST /v1/internal/backups/sweep` (:16746) → `run_backup_sweep` (:16769); driven hourly by GitHub cron (`registry-backup-cron.yml` → `.github/scripts/registry-cron.sh`). Re-baseline endpoint (:16901) is team-keyed (`ops/teams/{team_id}/state.json`, :16920-16924). Drill endpoint (:16932) hardcodes `graph_name=f"team_{team_id}"` (:16957).

**4. The graphs enumeration seam exists — the sweep just never uses it.**
- `tortoise/supabase_control.py:2240` `graph_metadata(cp, team_id)` returns the derived default (`graph_id:"default"`, `kind:"default"`, namespace = `teams.graph_name`; :2255-2280) **plus** `graphs` rows with `kind='custom' AND status='active'` (:2282-2300); a missing graphs table degrades to default-only (logged, :2305-2311).
- `tortoise/sdk.py:12839` `graph_list(team_id)` — Supabase mode delegates to `graph_metadata` (:12853); registry mode `MATCH (g:Graph {team_id}) RETURN properties(g) ORDER BY kind…` (:12861-12872), where `status` coalesces `"active"` for pre-C1 nodes (:12875-12877) and **deleted nodes are returned but not filtered** — callers filter (e.g. GET /v1/graphs skips `status=="deleted"`, hosted_api.py:8910-8912).
- Schema: `supabase/migrations/20260901000001_graphs_and_key_scopes.sql` — `graphs(id text PK, team_id FK, name, kind 'default'|'custom' NOT NULL DEFAULT 'custom', namespace NOT NULL, status 'active'|'deleted' NOT NULL DEFAULT 'active', recording bool, created_at)`; partial unique index on (team_id, name) WHERE status <> 'deleted'; RLS + service-role grants. `teams.graph_name` exists alongside (`0006_teams.sql:41`).
- Graph lifecycle today: `DELETE /v1/graphs/{graph_id}` (hosted_api.py:8766) soft-tombstones (`status='deleted'`, :8832-8833) + revokes keys + drops ACL user + frees the quota slot — the stored namespace is **never dropped** and **no sweep covers custom graphs** (the #2304 hook).

**5. The exact "broken" claim.**
- A Pro team with N active custom graphs has backup artifacts for **1 graph (the default)** — verifiable in R2: `backups/{team_id}/` contains only default-graph manifests (plus any ad-hoc C5 dumps). Custom graphs receive **zero automated artifacts**, zero state.json, zero drift guards, zero retention. If the team's customer data lives in custom graphs and the default is unused (steady-0), the sweep "succeeds" every run on an empty default while the real data is unprotected — the #101 empty-team signal is a chronic no-op by design (`empty_skipped`, backup_sweep.py:375-379), and the operator-facing watcher reports the team `ok`.
- The C5 per-graph on-demand POST is the ONLY graph-aware backup path (create with a graph-bound key resolves `graph_namespace`). Confirmed.

### Adjacent finding (same root cause family, do NOT absorb)
- **Pool-level retention competition is latent today:** `prune_backups` retention is computed on the shared team pool. In hourly-bounded mode (production defaults `retention_hourly=24`/`daily=7`/`weekly=4`, backup_config.py:62-64) the hour-bucket/weekly anchors are **per pool**, so when two graphs' dumps share an hour bucket or ISO week, only the newest survives (prune_backups:966-1026). A C5 custom-graph on-demand dump landing in the same hour bucket as a default-graph dump is silently deleted after the hourly window. Per-graph pruning (Approach A below) fixes this structurally; the C5-only path remains a blind spot until then.

### Falsification Check
This definition is wrong if any of:
1. The sweep enumerates graph rows anywhere — **false**: grep of `graph_list|graph_metadata|graphs` in backup_sweep.py has zero hits (verified).
2. Some other scheduler backs up custom namespaces — **false**: the only production sweep callers are the hourly internal endpoint (hosted_api.py:16746) driving `run_backup_sweep`, and the event-retention sweep (hosted_api.py:511, event logs only, not graph dumps).
3. Custom graphs cannot exist on backup-eligible teams — **false**: free=1 graph/solo=2/pro+team=unlimited (`max_graphs_per_team` null in product/pricing.json:68,92); solo can hold 1 custom graph; pro/team unlimited. Solo teams are not backup-eligible (`daily_backups=false`, pricing.json:58) — the fix inherits team eligibility, so the affected population is pro/team custom graphs, which is exactly the developer-customer case.
4. The C5 path already covers custom graphs on the sweep schedule — **false**: it is on-demand only (POST /backups), requires a caller, and is not scheduled.

### Confidence: 88
Every link in the chain is code-verified with file:line citations. Residual uncertainty: exact production graph counts (no DB read in this session) and the degree to which graph-bound C5 keys exist in the wild (`per_graph_keys` pricing strings still read "planned" — pricing.json _comment 2026-09-01); neither affects the root-cause verdict.

---

## Decisions Already Inherited (do not re-litigate)

| Source | Decision | Implication for #2313 |
|---|---|---|
| #2304 (owner, 2026-09-06) | Delete = **trash-can semantics**: quarantine + 7-day grace window (default), two restore modes (full-restore destructive-warned / read-only reconcile), purge after window | Sweep must **exclude `status='deleted'` (quarantined) graphs** (Indicator 4 in #2313). Restore path needs a **tombstone guard** (refuse backup-restore of a deleted graph) consistent with #2304 research item 4. Sequence: **this fix first, before shrinking the trash window** (both issues' research notes). |
| #2304 research item 4 | Backup-artifact interplay is **an open decision**: prune artifacts at purge vs keep-with-tombstone-restore-guard | **Coordination point — flagged to the owner (Open Question Q1), not silently picked.** This doc recommends prune-at-purge + tombstone guard (rationale in Solution). |
| Epic #2083 C5 residual (runbook §5:100-102; capstone-report.md:63) | "backup/event-retention sweeps still enumerate the DEFAULT graph only — per-graph sweep enumeration + state keying is a follow-up (R13 audit owns retention amplification)" | This issue IS that follow-up. R13 (plan :552) owns the retention-amplification audit — the per-graph cost model must be documented for it. |
| C5 #2114 | Graph-bound key dumps ITS OWN graph on demand (`graph_namespace`), fail-closed `GRAPH_NOT_FOUND` 403; graph-bound keys are **rejected** from the team-default restore surface | The per-graph dump binding precedent (dump projection bound to the resolved graph, hosted_api.py:16501-16522) is reused by the sweep. Per-graph restore = owner/team-key surface. |
| #669 / #2023 / #770 | `teams.graph_name` is the SOR for the default graph name (Supabase lane mints `team_{team_id}` since #1903; registry-lane SDK teams keep `team_{name}`) | The sweep must keep reading `team_graph_name` for the default and derive custom namespaces from the graphs seam — never assume a naming convention. |
| Quota/lifecycle | Graphs row/node soft-delete tombstones, key revocation cascade, quota freed, name reusable (hosted_api.py:8766+) | The graphs seam carries `status`; enumeration filters on it (active only) in both lanes. |

---

## Solution Diamond

### Options

**Option A — Per-graph sweep with graph-keyed objects (RECOMMENDED).**
Add a graph dimension end-to-end, with the graph **in the object key** and per-graph state.

- **Enumeration (new seam, both lanes):** keep team enumeration/eligibility as-is; per eligible team, iterate graphs via the existing `graph_list`/`graph_metadata` seam (default-first, R3 parity), filter `status != 'deleted'` (registry lane filters the returned prop; Supabase lane already active-only). Deterministic per-team graph list `[(graph_id, kind, namespace)]` with `graph_id` normalized to `"default"` for the default graph in **both** lanes (registry default Graph node has a random gid + kind='default' → map kind default → `"default"`; Supabase seam already emits `"default"`).
- **Inner loop per graph (mirrors today's `_backup_team`, backup_sweep.py:254):** size guard → per-graph prior state → per-label counts → dump via `create_backup(graph_name=namespace)` → P0 guard (manifest graph_name == namespace) → empty-transition/>50%/per-label drift guards with incidents keyed `(team_id, graph_id)` → per-graph state write → per-graph prune. Per-graph failure isolation inside the team (one bad graph never aborts its team's other graphs; a resolution failure for one graph is isolated like per-team today :511-537).
- **Storage/state keying:** `backups/{team_id}/{graph_id}/{ts}_{rnd}/dump.enc|manifest.json` and `ops/teams/{team_id}/graphs/{graph_id}/state.json`. `manifest` gains `graph_id`. New `_validate_graph_id` (mirror `_validate_team_id`, hosted_backup.py:466) — graph ids flow into keys.
- **Prune:** per-graph prefix — retention buckets (daily window, hour-bucket anchors, weekly anchors) computed **independently per graph**; fixes the latent pool-competition bug (Adjacent finding).
- **List/restore:** `list_backups(storage, team_id, graph_id=None)` — None returns all (current callers unchanged), graph_id filters. `restore_backup` gains the graph identity in the key; the endpoint derives the requested graph from the chosen backup (its key's graph segment; legacy keys via manifest `graph_name` → graphs-seam reverse lookup) and resolves its namespace via the graphs seam instead of `team_graph_name` (default-only). **Tombstone guard:** refuse restore when the graph's registry row/node `status='deleted'` (graph not resurrectable via backup restore; the trash restore modes in #2304 own quarantined-graph recovery) — one seam read, fail-closed, same shape as the existing cross-team/cross-graph guards (hosted_backup.py:849-921).
- **Watcher/status:** fix the key-shape parsers (`_newest_backup_ts` flat parse, backup_watcher.py:62-81; state-team derivation :238-243); extend `compute_status` per-graph freshness (a team is `ok` only when every active graph has a fresh archive — otherwise the issue's "operator believes backups exist" failure recurs at graph granularity). Re-baseline endpoint gains an optional `graph_id` (team-wide default for back-compat). Drill accepts an optional graph selection (default remains the default graph).
- **Legacy compat (no data loss, no breakage):** reads (list/prune/restore/watcher/direct-R2) tolerate legacy flat objects under `backups/{team_id}/{ts}_{rnd}/…`; legacy objects are bucketed by their manifest `graph_name` (→ graph) at read time. Writes after the cut use graph keys. The cron script's direct-R2 freshness leg (`--prefix backups/${team}/`, registry-cron.sh:119-126) is key-shape-agnostic (LastModified on `dump.enc` under the team prefix) and survives unchanged — graph segment nests under the team prefix.
- **Dashboard (UX low):** `GET /backups` response carries per-graph metadata (graph_id/kind per manifest) without breaking the current card (`list.length`, main.jsx:3971-3974). Per-graph UI rows = follow-up issue (do not absorb).
- **Cost model:** retention identical per graph (same keep_daily/weekly/hourly defaults, backup_config.py:62-64); storage ≤ N_graphs × (bounded per-graph set). Documented for R13's retention-amplification audit; pro/team graphs are unlimited (pricing.json:68,92) so a per-team storage budget knob is a config option, default off (Open Question Q5).

*Files:* backup_sweep.py, hosted_backup.py, backup_watcher.py, backup_config.py (manifest graph_id field default; optional knobs), hosted_api.py (endpoints: list/restore/re-baseline/drill + internal sweep unchanged), sdk/supabase_control (enumeration reuse only), docs/ops/registry-backup-dr.md, docs/registry-graph-schema.md (retention note), website/dashboard (only if per-graph rows are approved).

*Trade-offs:* cleanest long-term model — object identity IS graph identity (prefix scans are O(1)/graph), structural cross-graph isolation, per-graph retention precise. Costs: one deliberate key-layout migration with backward-compat (six parsers + test seeds updated; legacy objects readable), more test churn up front.

**Option B — Manifest-keyed graph discrimination, flat keys unchanged.**
Keep today's `backups/{team_id}/{ts}_{rnd}/…` layout. The sweep adds the per-graph loop, but prune/list group **by reading each manifest's `graph_name`**; per-graph state.json keys; retention math per graph done in memory over the team prefix.

*Trade-offs:* zero key churn, legacy objects need no migration, watcher/direct-R2/cron parsers untouched. Costs: per-graph listing/prune and watcher per-graph staleness require downloading **every** manifest of the pool on each pass/poll (extra R2 GETs and grouping complexity); cross-graph retention safety rests on in-memory grouping correctness rather than storage structure; the watcher's per-graph freshness (the fix's core visibility promise) forces manifest reads on every poll cycle; and the "which objects belong to which graph" ambiguity stays latent for any future consumer. *When it would win:* if object-store list costs dominated and the graph fan-out were tiny — not the case here (pro/team unlimited graphs).

**Option C — Graph-top-level layout `backups/graphs/{team_id}/{graph_id}/…`.**
Cleanest names, worst blast radius: breaks the team-prefix continuity that the cron direct-R2 leg, watcher `_team_prefixes`, the cross-team prefix guard (`restore_backup` :860), and the tenant-isolation model all rely on; needs parallel support for legacy team-prefix objects. Rejected — no benefit over A.

**Option D — Flag-gated incremental (`BACKUP_CUSTOM_GRAPHS_ENABLED`, default off).**
Treats custom-graph coverage as opt-in per deployment. Rejected: the issue is that high-value data is unprotected **by default**; a default-off flag creates two incident-response regimes and delays protection without reducing implementation risk (the flag gates enumeration only). *When it would win:* if the sweep's runtime budget or storage cost were the binding constraint — they are not at current scale, and the cost model (A) bounds it.

### Convergence rationale (quality over convenience)
Option A is chosen because it fixes the **root cause** (graph identity absent from every backup artifact and every downstream consumer) rather than papering over it, and because the issue's Indicator 2 (list/prune/restore per graph) and the #2304 long-tail-recovery assumption are only *durably* satisfiable when artifacts structurally know their graph. B's manifest-grouping keeps the ambiguity that produced this bug class in the first place (team-keyed pool with graph as manifest metadata was exactly what made C5 dumps invisible to the sweep) and pushes per-graph visibility cost into every poll. A's one-time migration cost is repaid by per-graph prefix scans that B would re-pay as R2 GETs forever.

### Implementation step sketch (feeds writing-plans; NOT the plan)
1. Enumeration: `enumerate_team_graphs(source, team_id)` seam (both lanes) + tests (fake + registry), reusing `graph_metadata`/`graph_list`. Registry lane: filter `status == 'deleted'`; map kind default → `"default"`.
2. `hosted_backup`: `create_backup(..., graph_id=)` (key segment + manifest field); `_validate_graph_id`; `list_backups(..., graph_id=None)`; per-graph `prune_backups(..., graph_id=...)` prefix math; restore: graph segment parse + legacy-flat fallback + tombstone guard (graphs seam status read) + namespace resolution.
3. `backup_sweep`: per-team graphs loop; per-graph state keys `ops/teams/{tid}/graphs/{gid}/state.json`; drift incidents carry graph_id; per-graph prune; ops-state unchanged (team-level). Legacy state key `ops/teams/{tid}/state.json` read as the default graph's state (one-time baseline carry-over).
4. Watcher: key-parse fixes + per-graph freshness in `compute_status` + per-graph state-team derivation.
5. Endpoints: restore graph resolution + tombstone guard; re-baseline `graph_id` param; drill optional graph; internal sweep driver unchanged (pass-through).
6. Config/docs: retention/cost model doc (registry-backup-dr.md, registry-graph-schema.md, runbook §5 note flip); optional per-team budget knob; pricing doc note.
7. E2E (live FalkorDB docker lane): N active graphs → N artifacts per sweep (hourly driver); delete one graph → excluded next run; per-graph restore swap; tombstone restore refused.

### Verification checklist — test-surface gaps (mapped this session)

| Surface | Test file | Covers today | Per-graph gap |
|---|---|---|---|
| Sweep enumeration (registry lane) | tests/test_backup_sweep.py (33 tests) | teams only, 1 graph/team; dialect split, fail-closed, guards | No graph enum; no kind/status filter; no deleted exclusion; no multi-graph team; per-graph isolation untested |
| Sweep enumeration (supabase lane) | tests/test_supabase_control.py | `graph_metadata` kind/status filtering (default+custom active, deleted excluded — :1686-1721); drift default-only; fake supports graphs rows | Sweep never calls the seam (zero refs); graphs-read *exception* → default fallback untested; fake `order`/`limit` kwargs untested; no `kind='default'`-row exclusion assert |
| Enumeration fake | tests/fake_control_plane.py | generic tables, filters eq/neq/is, order/limit exist (:560-646) | No graphs-preseeded sweep fixture; `created_at` implicit "" ordering caveat |
| Per-graph storage/state/prune | tests/test_backup.py + test_backup_config.py | file-level round-trip; config parse trio (:118-143 template) | N/A (no graph concept); config has no graph fields |
| Pipeline keys/list/restore/prune | tests/test_hosted_backup.py (large) | flat key shape pinned (:303,:855), prune retention incl. hourly (:1737), cross-team + cross-graph guards (:458-561,:487,:922), sha256, empty-over-live, pre-restore copy | Key-layout seams (create_backup id, list prefix, prune 4-part parse, restore prefix guard) all pin the shape that changes; no custom-graph restore success path; **tombstone guard unimplemented + untested**; stamps are team-row only |
| Watcher | tests/test_backup_watcher.py | compute_status per-team states (never/stale/ok/stamp_missing, :69-118); production key-parse regression (:262) pins the flat shape | No graph dimension anywhere; key-parse test must be extended not deleted; per-graph freshness states net-new |
| Restore surface HTTP | (tests/test_dr_endpoints.py — drill; test_backup_e2e.py = file-level backup.py round-trip) | drill endpoint HTTP-level w/ own seeds | E2E multi-graph on live FalkorDB net-new (issue Target: backup_e2e pattern) |
| Dashboard | website/apps/dashboard/src/main.jsx (BackupsCard :245, loadBackups :3955) | summary count only | Per-graph surface decision (Q3) — minimal = server response metadata, no UI change |

Net: **all per-graph concerns (enumeration, keys, state, prune, restore-target, tombstone, watcher freshness) are NOT COVERED today** — mapped exhaustively by three parallel review passes; every existing test pins single-graph semantics that must survive additively.

### Axis Research (Phase 1.5)
> Trigger: Architecture axis high (complex), Research standard — fired. Axes rated: retention-per-key/prune (architecture), deleted-entity backup-artifact posture (research). Findings feed Option A's prune/tombstone design.

- **Per-tenant logical backups with per-key retention are the canonical pattern** for pooled multi-tenant SaaS: nightly logical exports per tenant, restore into staging before swap, frequency matched to RPO, per-tenant retention policies (multi-tenant-saas.com hybrid-isolation per-tenant backup/restore guide; Grasp multi-tenant DR module: "per-tenant backups, per-tenant retention"); AWS managed-backup SaaS post: segregate tenant data during backup and store independently. *Canonical → validates per-graph objects + per-graph retention buckets (Option A over B).*
- **Tenant/resource-aware key naming is the isolation norm**: `tenant:{id}:{resource-type}:{resource-id}` / `s3://bucket/{tenant-id}/objects/` with per-scope access control (Redis multi-tenant data-isolation post). *Canonical → validates `{team}/{graph}/{ts}` nesting; defense-in-depth cross-tenant restore checks must be tested (Frontegg: "a backup is not sufficient until restoration preserves tenant ownership").*
- **Deleted-entity backup posture (pitfalls/GDPR/erasure)**: erasure obligations extend to backup systems; regulators (ICO; CNIL; Danish DPA) accept pragmatic "beyond use" or delete-at-expiry if restore paths re-erase; delete-at-purge of artifacts + documented expiry is the cleanest honest-delete posture; retained artifacts need restore-time guards (deletion index + post-restore checks) (ICO right-to-erasure guidance; probackup.io deletion-index article; cloudswitched/arcserve summaries). *Pitfalls → #2304 coordination (Q1): prune-at-purge + tombstone guard; never rely on artifact absence alone.*
- No new third-party deps introduced (existing boto3/R2, fake seam). `### Integration Docs`: none — no new deps; reuses in-repo storage adapters and the tested graphs seam.

---

## Integration Surface Map

| Surface | Lane | Change | Covered by |
|---|---|---|---|
| Team→graphs enumeration | supabase | reuse `graph_metadata` per eligible team (default + custom active; deleted excluded by query) | tests/test_supabase_control.py + new sweep tests |
| Team→graphs enumeration | registry | reuse `graph_list` per team; sweep filters `status != 'deleted'` (prop coalesces active for pre-C1) | tests/test_backup_sweep.py registry dialect |
| Storage keys/state/prune/list | R2 + Memory | graph segment + per-graph state.json + per-graph prune; legacy flat objects readable | tests/test_hosted_backup.py (extend, don't delete), test_backup_sweep.py |
| Restore | API + pipeline | derive graph from backup key; namespace via graphs seam; tombstone guard; legacy-flat fallback | tests/test_hosted_backup.py + endpoint tests |
| Sweep loop + drift guards | driver (cron → internal endpoint) | per-graph inner loop; incidents carry graph_id; re-baseline graph param; drill optional graph | tests/test_backup_sweep.py |
| Watcher staleness | lifespan thread | per-graph freshness states; key-parse fixes | tests/test_backup_watcher.py |
| Cron direct-R2 freshness | .github/scripts/registry-cron.sh | unchanged (team prefix preserved) | manual/drill verification |
| Dashboard Backups card | website | server response metadata only unless Q3 approves rows | e2e clickthrough |
| Docs | docs/ops/registry-backup-dr.md, registry-graph-schema.md, runbook §5 | R2 layout §, per-graph retention note, residual flip | doc review |
| Data model | migration 20260901000001 | no schema change (graph_id = existing row/node id; manifest field only) | — |

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| graphs seam (graph_metadata/graph_list) | shared seam | reused, no change | ✅ |
| hosted_backup primitives | library | #2313 in scope | ✅ |
| internal sweep endpoint + driver cron | infra | in scope (loop change) | ✅ |
| list/restore endpoints + tombstone guard | API | in scope | ✅ |
| watcher + re-baseline + drill | API/thread | in scope | ✅ |
| #2304 trash window ordering + purge-artifact decision | cross-issue | **Q1 owner decision; sequence pin** | ⚠️ |
| dashboard per-graph rows | UI | Q3 owner decision (else follow-up issue) | ⚠️ |
| R13 retention-amplification audit | docs/ops | cost model documented in this issue | ✅ |
| C5 on-demand pool-competition (adjacent) | hosted_api | fixed structurally by per-graph prune; C5-only path noted | ⚠️ file follow-up |

## Open Questions (owner)

1. **[#2304 coordination] Artifact policy at purge:** prune-at-purge vs keep-with-tombstone-restore-guard. Recommendation: **prune artifacts at purge time + keep the restore-time tombstone guard regardless** — (a) in-window trash recovery never needs artifacts (data is still live in the DB during the grace window), so purge-time deletion cannot break #2304's two restore modes; (b) artifacts legitimately outlive a purge today (pre-fix C5 dumps, legacy objects, purge retry failures, graphs deleted before this feature ships) so a guard is required either way; (c) GDPR/erasure posture favors honest deletion (see Axis Research). Sequence pin: land #2313 before #2304 shrinks the 7-day default.
2. **Restore-of-quarantined-graph surface:** during the grace window, is a deleted graph's PRE-delete backup reachable only through #2304's trash full-restore mode (our tombstone guard refuses it everywhere else)? Recommend yes — one restore surface for deleted graphs.
3. **Dashboard minimal viable:** keep the summary card + enrich `GET /backups` response with per-graph metadata (no UI change), vs add per-graph rows now. Recommend the former; rows as a follow-up issue (UX low).
4. **Default-graph key segment normalization:** registry lane default Graph node has a random gid + `kind='default'`; normalize to literal `"default"` in both lanes for key stability? Recommend yes (Supabase seam already emits `"default"`).
5. **Retention knobs:** same keep_daily/weekly/hourly defaults per graph (recommend, matches today's contract), optional per-team storage budget knob for unlimited-graph teams (R13) — default off?
6. **Legacy flat-object bucketing:** accept read-time bucketing of legacy `backups/{team_id}/{ts}/…` objects by manifest `graph_name` (→ graphs-seam reverse lookup), with the default graph as the fallback when the manifest names a graph that no longer exists?

## Complexity (domain-aware — issue-rated complexity:complex, level: project)

| Domain | Rating | Rationale |
|--------|--------|-----------|
| Architecture | complex | Cross-system data-safety change: both control-plane lanes, storage keying migration + legacy compat, restore guards, watcher freshness, retention/cost model |
| Research | standard | Enumeration/state/keying decisions + #2304 coordination (this doc + Q1-Q6) |
| UX | low | Backups surface per graph (minimal; Q3) |

## Discovery: Adjacent Issues to File (do NOT absorb)
- **Pool-level retention competition on the C5 on-demand path** (hourly/weekly anchors shared across graphs delete custom-graph dumps) — structurally fixed for sweep artifacts by per-graph prune; the C5 on-demand path should file its own guard once per-graph keys land (or absorb into this issue's prune work as a one-line key-scope change — owner call).
- **Test debt:** test_backup_sweep.py:429 title claims prune coverage but asserts no deletion; `graph_metadata` exception→default fallback path and fake `order`/`limit` kwargs untested (test_supabase_control.py) — fold into this issue's test work where touched.

## Review Cycle Log (front-half deliverable)
- problem diamond: verified via 3 parallel codebase passes + external research; converge confidence 88.
- solution diamond: 4 options diverged; A recommended on outcome quality (structural graph identity + per-graph retention correctness), B/C/D rejected with when-each-would-win.
- Remaining pipeline (parent session): problem-verify (2 verifiers) → solution-verify (2 verifiers) → second-model coherence → wiring gate → Phase 8 post to #2313. This file is the review artifact.


## Owner Decisions (2026-09-06 — Q1–Q6 approved as recommended; deltas folded)
- **Q1 (#2304 coordination):** prune backup artifacts at purge time AND keep the restore-time tombstone guard (both). Never rely on artifact absence alone.
- **Q2:** during the trash grace window, a deleted graph's pre-delete backups are reachable ONLY via #2304's trash full-restore mode; refused everywhere else.
- **Q3:** dashboard = keep the summary Backups card + enrich GET /backups response with per-graph metadata; per-graph UI rows are a follow-up issue (not absorbed).
- **Q4:** normalize the default graph's key segment to the literal `default` in both lanes.
- **Q5:** same retention defaults (24h/7d/4w) per graph; per-team storage budget knob optional, default off.
- **Q6:** legacy flat backup objects are read-time bucketed by manifest graph_name (graphs-seam reverse lookup), default graph as fallback.
- **Cadence framing (research verifier):** hosted sweep is HOURLY (RPO ≤1h typical / ≤2h worst, #596 §3.4/§3.8 post-#669), NOT nightly — this doc's cadence wording was corrected to match. Custom graphs today = effectively infinite RPO (never swept).
- **Folded delta (research benchmark):** per-graph ACL-user rebuild/verification on full-platform restore is IN SCOPE for this issue's restore/DR work (tombstone guard + restore completeness; drill remains scratch-only — live-ACL verification added to the DR runbook task).
- **Out of scope (separate follow-ups filed):** scheduled restore drills + explicit RTO; KMS/rotation for backup keys; R2 bucket-lock immutability + geo-region decision; cadence-label truthfulness + dead-knob cleanup (BACKUP_SKIP_FRESH_MIN).

