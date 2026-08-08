# Problem Diverge — #596 Hosted backup: scheduled cron + failure alerting + control-plane backup

**Date:** 2026-08-08 | **Phase:** issue-scoping double diamond, Phase 1 (problem-diverge) | **Depth:** light research (3 adversarial web queries) + repo-internal sweep

**Guiding question:** *Is "nothing invokes the backup machinery" the actual problem — or is it one symptom of a longer, broken deliverable chain?*

---

## Repo-internal evidence base (checked this session)

| # | Finding | Where |
|---|---------|-------|
| E1 | **No cron exists anywhere.** Zero `schedule:` triggers across all 10 workflows in `.github/workflows/`; no Fly machines in `fly.toml` (only `[[services]]`); no pg_cron in `supabase/functions/` (only `tenant-provision`, `waitlist-subscribe`). `/v1/internal/reconcile` is commented *"Called by an external cron"* but **nothing calls it** — its sweeps (expired bootstrap keys, orphaned provision keys) are also silently not running. | hosted_api.py:1934–1936; `.github/workflows/`; fly.toml |
| E2 | **No team can be Pro today.** Team nodes are created `tier:'free', backup_enabled:false` on every path; nothing in production flips either field. No Stripe checkout/webhook exists anywhere (only a string field list in sdk.py). `sdk.team_update()` *can* set tier/backup_enabled (sdk.py:3263) but is only exercised by tests — comment admits "no REST surface exists yet". Tests fake Pro via dependency override. | hosted_api.py:392–393, 1044–1045, 2531; sdk.py:3263–3289; tests/test_hosted_api.py:1084 |
| E3 | **Registry backup doc drift.** docs/registry-graph-schema.md claims the registry "receives hourly BGSAVE backups to R2" — no such code exists. Conversely, entrypoint.sh states FalkorDB Cloud provides "AOF durability, automated backups" — so the registry **is** covered by vendor-level DB snapshots today; what's missing is the operator-controlled, portable logical layer. | docs/registry-graph-schema.md:2; entrypoint.sh:10–14 |
| E4 | **No alerting infra.** `.env.example` has no SLACK/WEBHOOK/NOTIFY vars; no webhook module. Established automation channel = GitHub issues (post-merge-validation.yml #559 flags issues on failure; test-debt-gate auto-files). The self-hosted "<100 points" check is `exit 2` + stderr — a caller-watched exit code, not an alert. | `.env.example`; scripts/daily-backup.sh:~160–163; .github/workflows/post-merge-validation.yml |
| E5 | **Restore verification is count-only.** node/edge counts verified against the *decrypted payload* (good), but nothing checks content — props, relationships, embeddings. A corrupt archive that preserves totals restores "verified". | hosted_backup.py:456–486 |
| E6 | **Stamp semantics.** `backup_latest_at` is written **after** upload, best-effort — a failed stamp leaves an archive with no stamp (false "stale" signal); stamp-missing ≠ backup-missing. Nothing currently *reads* the stamp for staleness (no heartbeat exists). | hosted_backup.py:411–419, 419; tests: `test_registry_stamp_lands_in_canonical_registry`, `test_create_backup_registry_stamp_failure_is_best_effort` |
| E7 | **Dumps execute inside the protected process.** `create_backup` runs via `asyncio.to_thread` in the app, dumping the **full graph to a JSON dict in memory** (MATCH (n) over everything incl. embeddings). Same 4GB VM documented as OOM-crash-looping (#545). A large team's dump can OOM the API mid-sweep — killing *other* teams' backups in the same run. Single-process blast radius. | hosted_api.py:2589; hosted_backup.py:126–158; fly.toml VM block |
| E8 | **No restore drill has ever run against production.** restore_backup is unit-tested (67 tests) but there is no evidence of a prod restore; the only restore scripts in the repo are self-hosted-era pre-migration snapshots. Registry restore has no endpoint at all — restore is team-keyed (`/backups/restore`) and registry restore would be ad-hoc SDK/operator work. | tests/test_hosted_backup.py; hosted_api.py:2613; graph-scripts/pre_migration_snapshot.py |
| E9 | **#101 root cause is misattributed in the issue.** The issue says the 2026-08-05 root cause was "backup.py exists but nothing runs it". The pipeline's own docstrings describe the fuller chain: "wipe followed by any write re-saves the empty state" — scheduling was one factor; the empty-state re-save was the other. Cadence alone doesn't prevent the wipe class; the empty-backup guard + alerting do. | restore_backup docstring, hosted_backup.py:344–346 |
| E10 | **pricing.json promises**: `daily_backups: "planned"` on pro (L81) and team (L104). The checkbox is real; the entitlement path to *become* Pro does not exist. | product/pricing.json |

**External research (3 adversarial queries, sonar):**
- Silent-failure class is documented and common: "script succeeds but backs up nothing", exit 0 + empty files for 23 days; fix = heartbeat + anomaly alerting + restore tests, not just job success. (actsupport.com, poppaping.com, oneuptime.com, LinkedIn cronzy case)
- Count-only restore verification is a known weakness: RESTORE VERIFYONLY "does not verify the structure of the data"; count checks miss wrong values / missing relationships that preserve totals. (Microsoft docs, red-gate, mssqltips)
- "A backup you've never restored isn't a backup": restore drills in isolated environments are the industry-standard proof of recovery; cadence ≠ recoverability. (fluentorbit, monpg, acronis, momentslog)

---

## Alternative Problem Framings

### Framing 1 — "Nothing invokes it" is a symptom; the deliverable chain is broken at the *entitlement* link (there are no Pro teams to back up)
The scheduler as specced enumerates `tier != 'free' AND backup_enabled` — which today is **the empty set** (E2). The first scheduled run will therefore *trivially succeed while backing up nothing*, which is precisely the silent no-op class the issue wants to alert on. The problem is the delivery chain *promise → entitlement → scheduler → verification → tenant visibility*, and scheduling is one of five missing links. Pricing integrity (the stated revenue goal) cannot be delivered by a driver alone — a tenant must be able to become Pro, or the daily-backups checkbox stays fictional.
- **Strength:** Explains why the feature as specced would be a silent no-op even after shipping; forces the enumeration + "backed up ≥1 team" assertion into scope; surfaces the sequencing dependency on the billing work #296.
- **Weakness:** Billing may be deliberately deferred by the operator; risks scope-creep into product work. The scheduler can still ship *if* it includes the no-op detector (see Framing 2).

### Framing 2 — The real failure class is silent operation; the gap is observability + verification, not a driver
Two silent modes exist: (a) the #101 class — nothing runs; now *detectable* but unwatched: stamps + R2 listings exist, no heartbeat reads them (E6); (b) the newer class — job ran, exited 0, backed up nothing or backed up a degraded graph (external evidence; GH Actions cron is documented jittery/skippable). The problem is not "add a driver" — it is "make backup health a first-class observable": heartbeat (stamp age vs cadence), per-run assertions (teams enumerated > 0, node-count deltas vs baseline), and an alert channel with a human at the end. Note the chosen GitHub-issue channel only fires when someone *looks* at the repo — it is the repo's own convention, but as an alert medium it lacks push, dedup, and severity.
- **Strength:** Names the failure class the E2E "failure raises an alert" actually guards; subsumes the driver question as a mechanism; survives the Framing-1 objection (a no-op run becomes loud).
- **Weakness:** Could balloon into a monitoring project; still needs *something* to trigger the checks (driver choice returns in scope).

### Framing 3 — The gap is restore confidence, not backup cadence ("backups that are never restored are not backups")
Verification is count-only (E5); nothing has exercised restore against production data (E8); the registry — the actual platform DR surface — has no defined restore path at all. A daily cadence producing R2 objects proves *writes*, not *recovery*. The E2E "scheduled backup produces R2 objects" tests the wrong end of the pipeline. The 2026-08-05 lesson (E9) was about empty-state re-save, which count-verification only partially addresses (an empty backup over an empty graph verifies clean).
- **Strength:** Matches the strongest external literature; is the only framing that de-risks the actual failure the customer experiences (restore moment); exposes that the registry "restorable" E2E is currently **not executable via any shipped surface**.
- **Weakness:** Drills cost real engineering and read as "nice-to-have" against the shipping promise; risks demoting the scheduler the pricing checkbox needs.

### Framing 4 — The registry/control-plane graph is the actual DR gap; team backups are a billing checkbox with zero beneficiaries today
Every team backup depends on the registry to even be meaningful: without `Team` nodes there is no enumeration, no `graph_name` mapping, no keys — intact R2 archives become orphaned bytes. The registry half of this issue is tier-independent, buildable today, and has the most extreme failure impact; it is also the only half that does not depend on the broken entitlement path. The issue lists it as scope item 3, but the problem definition should lead with it.
- **Strength:** Prioritizes what is buildable now and protects the platform; no dependency on billing; works even in Framing 1's empty-Pro-world.
- **Weakness:** Alone it does not close the stated pricing gap — the customer-facing promise needs the team-backup half too.

---

## Assumptions

| # | Assumption (from issue) | Status | Evidence / Falsification |
|---|---|---|---|
| A1 | Hosted backup machinery (create/restore/list/prune) is live and working | **[validated]** | hosted_backup.py (732 lines), 67 tests in tests/test_hosted_backup.py, endpoints wired hosted_api.py:2540–2665, merged in #582 (commit c99eb9b). |
| A2 | "Nothing invokes it periodically" | **[validated]** | No `schedule:` in any of 10 workflows; no Fly machine; no pg_cron; no in-process timer (E1). |
| A3 | "An external-cron pattern already exists" (reconcile) | **[partially validated]** | Endpoint shape + internal-key auth exist (hosted_api.py:341–346, 1936). **Falsified as a *running* pattern:** no cron calls it — the comment is aspirational; reconcile's own sweeps are uninvoked (E1). |
| A4 | Pro tier promises daily backups | **[validated as promise]** | pricing.json L81 (pro), L104 (team): `daily_backups: "planned"`. **[unverified as deliverable]** — see A5. |
| A5 | Pro teams exist and are enumerable (`tier != 'free' AND backup_enabled`) | **[FALSIFIED today]** | All Team nodes created `tier:'free', backup_enabled:false` (hosted_api.py:392–393, 1044–1045); no production code flips either; `sdk.team_update` (sdk.py:3263) only called by tests; no Stripe/webhook; `create_team` hardcodes `"tier":"free"`. Enumeration target = ∅. |
| A6 | Scheduler can call `create_backup` per Pro team daily | **[unverified]** | Depends on A5; additionally, dumps execute inside the OOM-documented app process (hosted_api.py:2589; fly.toml #545) with full-graph JSON in memory (hosted_backup.py:126–158) — a large dump can kill the API (and the same run's other teams) mid-sweep (E7). |
| A7 | Registry graph "has no backup at all" | **[validated at app layer; partially false at platform layer]** | No app-level backup code; docs/registry-graph-schema.md's "hourly BGSAVE backups to R2" is unimplemented (E3). But entrypoint.sh states FalkorDB Cloud provides "AOF durability, automated backups" — vendor snapshots cover the registry DB today; they are not operator-controlled or portable. |
| A8 | Low-node-count is a useful alert signal; mirror `<100` | **[partially falsified]** | daily-backup.sh's `<100` is single-graph self-hosted semantics (scripts/daily-backup.sh:~160) and is `exit 2` + stderr — no delivery. Multi-tenant: legitimately-small team graphs false-positive; registry baseline grows with tenant count (absolute threshold wrong); the class it cannot catch is "0 teams enumerated" — today's reality (E2). |
| A9 | "Fail loudly" via GitHub-issue automation | **[validated channel]** | post-merge-validation.yml (#559) + test-debt-gate file issues. **[unverified as alert medium]** | No push/dedup/severity; only seen when the operator looks; no Slack/webhook anywhere (E4). |
| A10 | Restore-freshness cross-check is possible (stamps land in canonical registry, #582) | **[validated]** | hosted_backup.py:419; `test_registry_stamp_lands_in_canonical_registry`. **[nuance]** | Stamp is best-effort *post*-upload (hosted_backup.py:411–419) — archive can exist with no stamp (false stale); stamp-missing ≠ backup-missing (E6). |
| A11 | E2E "registry restorable" is achievable with shipped machinery | **[unverified]** | `restore_backup` accepts arbitrary `graph_name` in unit tests, but no registry-restore endpoint/runbook exists; `/backups/restore` is team-keyed (hosted_api.py:2613). Registry restore = ad-hoc SDK/operator work (E8). |
| A12 | Node/edge-count verification proves recoverability | **[partially falsified]** | Count-only checks (hosted_backup.py:456–486) miss content corruption that preserves totals; never-restored backups are hypotheses (E5, external evidence). |
| A13 | 2026-08-05 root cause was "nothing runs it" | **[partially validated]** | Scheduling was a factor; the fuller chain was empty-state re-save ("wipe followed by any write re-saves the empty state" — restore_backup docstring). Cadence alone would not have prevented the wipe class (E9). |
| A14 | This issue flips pricing.json's `daily_backups` checkbox | **[validated]** | It is the checkbox deliverable — but real delivery requires the entitlement path (A5) or the promise stays fictional. |

---

## Boundary & Stakeholders

**Out of scope (explicitly or by omission):**
- **Billing / tier-upgrade path** — Stripe checkout/webhook, `PATCH /v1/teams/{id}`, `backup_enabled` flip. The issue silently presumes this exists (it does not); without it, scope item 1 has a population of zero (A5).
- **Reconcile scheduling** — the sibling uninvoked job (expired bootstrap keys, orphaned keys). Same "nothing runs it" class, different domain (A3).
- **Tenant-facing backup health** — no public signal of `backup_latest_at`; `/backups` list is Pro-only and behind auth. Tenants cannot see their backup is healthy/stale.
- **Automated restore drills** — periodic restore-verification against production (or staging) data; the strongest external recommendation, absent from the E2E.
- **Content-level archive verification** beyond node/edge counts.
- **`TORTOISE_BACKUP_KEY` rotation policy** — a single key decrypts all archives; rotation semantics undefined.
- **Retention-policy changes** — 7-daily + 4-weekly is shipped; the scheduler only invokes `prune_backups`.
- **Self-hosted #101 automation** — `daily-backup.sh` remains manual/cron-less.
- **FalkorDB Cloud vendor relationship** — managed snapshots, restore SLA, cross-region placement are outside this repo.

**Affected but unmentioned:**
- **Tenants (Pro/Team)** — hold the daily-backup promise; today: zero health signal, cannot trigger anything (402), a stale/never-verified archive only surfaces at *their* restore moment.
- **Support** — Pro = "standard" support (pricing.json); they field "restore failed"/"data gone" tickets; no registry-restore runbook exists for them.
- **Supabase Edge Function owners (`tenant-provision`)** — provision writes `backup_enabled:false`; any entitlement flip touches their contract; `waitlist-subscribe` hints at the future upgrade path.
- **The operator's GitHub inbox** — the issue-filing alert channel needs dedup/triage or it is noise (A9).
- **The shared FalkorDB Cloud instance** — daily full-graph scans per team are new read load on the tenant DB, unmeasured.
- **SDK consumers / internal tooling** — `team_update(tier/backup_enabled)` is SDK-exposed but only tests call it; the entitlement gate must live server-side, not in the public SDK (a registry-namespaced caller could otherwise self-upgrade).
- **Embedding-heavy tenants specifically** — their archives are the memory-hungriest dumps (embeddings serialized in full), i.e. the highest OOM risk inside the protected process (E7).

---

## Net problem statement (diverge output)

The issue defines the problem as *a missing driver*. The evidence says the problem is a **broken delivery chain with a silent no-op guarantee at both ends**: (a) there are no Pro teams to back up (entitlement missing — first scheduled run trivially succeeds backing up nothing); (b) even when backups run, nothing verifies they ran *or* that they can restore (count-only verification, no heartbeat, no drill, GitHub-issue "alert" with no push); and (c) the highest-impact surface — the registry — is the one with no restore path at all, despite being the only piece buildable today. The scheduler is necessary but not sufficient; the definitions to converge on are *detection* (Framing 2) and *recovery proof* (Framing 3), with the registry (Framing 4) as the tier-independent anchor.
