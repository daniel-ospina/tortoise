# #4179 — One source of truth for the retention/deletion promise

**Level:** task · **Complexity:** standard · **Domain:** architecture standard / content standard / config low
**Branch:** `fix/4179-retention-deletion-source-of-truth`
**Canonical artifact (produced by this plan):** `docs/retention-and-deletion.md`

---

## Scope — problem diamond (condensed)

**Original framing (hypothesis, from the issue body):** three deletion paths state three
different windows; make all three 7 days and point every scattered statement at one doc.

**Alternative framings considered**

1. *Code bug framing* — "the team default is wrong (24h), fix it." Rejected as incomplete:
   the user-account path has **no** window, and the public pages name no horizon.
2. *Docs-only framing* — "write the doc, leave the code." Rejected: the owner ruled a
   behaviour (7-day restore for all three), and a doc that does not match code is the exact
   loop this issue exists to stop.
3. *Unify the mechanism framing* — "build a shared soft-delete/restore engine." Rejected:
   self-service deletion/restore is a larger, permission-adjacent change; a shared
   *constant* + a canonical *doc* gets the single authority without inventing a surface.

**Confirmed problem definition:** the retention/deletion **promise** is restated in the
privacy/dpa pages, the backup config, the sweep constants, the team-delete default, the
dashboard UI, and a dozen docs — and each restatement is a fresh place to drift. The fix is
one canonical document that *names the implementation constants as authoritative*, plus code
that derives every window from one constant and a scan that binds the restatements.

**Premise corrected:** `website/privacy.html` is **not** a flat self-contradiction — its
graph bullet already says a deleted graph's backup copies are erased with the graph. The
defect is that the **horizon is unbounded/unqualified**: §6 says backups "may retain data for
a limited additional period" and names no horizon, and the same phrase appears in
`website/dpa.html`. The fix writes the horizon down once.

**Falsification:** a **recursive scan** over the declared promise-bearing roots must find no
retention/deletion numeric claim that neither links the canonical doc nor is a named
implementation constant. The scan is the anti-scatter falsifier; it must catch the frontend
duplicate (`website/apps/dashboard/src/graphs.js` `TRASH_GRACE_DAYS = 7`) and nested docs.

### The reconciliation (owner's words, written down once)

> **Delete = gone from your view immediately · restorable for 7 days · no copy remains
> after about four weeks.**

The horizon is **path-dependent** — re-derived from the code, not the issue body:

| Path / copy | Deletion horizon | Code evidence |
|-------------|------------------|---------------|
| **Graph** deletion — undo window | **7 days** | `backup_sweep._GRAPH_PURGE_GRACE_DAYS` (→ `retention.RESTORE_WINDOW_DAYS`) |
| **Graph** deletion — the graph's own backup pool | erased **wholesale at the 7-day purge** (the purge lists+deletes the whole `backups/{org}/{gid}/` prefix) | `backup_sweep._purge_graph_storage` |
| **Live**-data snapshots (a backup horizon, not an undo) | 24 hourly + 7 daily + 4 weekly ≈ **28 days** | `backup_config.retention_hourly/daily/weekly` |
| **Team-account** deletion — backup artifacts | **NOT erased** — the org-wide prune never touches nested per-graph pools, so **all** the org's pools (**default included**) are orphaned indefinitely | `backup_sweep.py` "nested per-graph keys are never touched by an org-wide prune"; `_drop_org_graph_strict` only issues `GRAPH.DELETE` |
| **Second-region mirror** (shipped, `BACKUP_MIRROR_ENABLED` default **off**) | deletion **not propagated** (append-only `s3 sync`; the sweep never deletes mirror objects) | `docs/ops/registry-backup-dr.md`; `backup_config.mirror_enabled=False` |

**Corrections to the issue body's diagnosis (the body is a hypothesis, verified against code):**
1. The issue says `retention_weekly = 4` makes "permanently erased — including any stored
   backup copies" **false**. For the **primary** store on the **graph** path this is **not**
   the case. The genuine residuals are the **team-account** path (all org pools survive),
   the **mirror**, and the public pages' unbounded "limited additional period" sentence.
2. **7 ≠ 30 is a different axis.** `TORTOISE_EVENT_RETENTION_DAYS` (default 30, `sdk.py`;
   `docs/event-catalog.md`) is the **operational event-store** retention — not user content
   and not a deletion promise. The canonical doc states it and exempts it.

**Restore window ≠ retention window:** R1 forbids a retention window on user *content* of any
length; the 7-day window is a *restore* offer, never a retention claim.

**Explicitly out of scope** (owner decisions, from the issue): **no permissions model**
(owner **D31**, deferred); **no retention window on user content** (owner **R1**).

---

## The six open design decisions — RESOLVED

| # | Decision | Resolution | Rationale / surfacing |
|---|----------|------------|-----------------------|
| D1 | Where the canonical doc lives | **NEW `docs/retention-and-deletion.md`**, registered in `docs/00_index.md`, cross-linked from `docs/data-safety.md` | A dedicated, promise-named file findable by an engineer *and* a copywriter. |
| D2 | One constant or three | **ONE window authority:** `tortoise/retention.py` → `RESTORE_WINDOW_DAYS = 7`, `RESTORE_WINDOW_HOURS = 168`. `_GRAPH_PURGE_GRACE_DAYS`/`_TRASH_GRACE_DAYS` become **derived aliases**; team/user defaults derive from it. The backup-hold counts (`backup_config` triple) keep their own authority — a different concept. The **frontend** `TRASH_GRACE_DAYS` is **bound by test** to the authority (JS cannot import Python). | Naming a derived alias "authoritative" re-creates the ambiguity; the doc names the **sole** window authority and the aliases as derived. |
| D3 | User-account restore mechanism | **Support-process promise, not a new surface.** No self-service account deletion exists (privacy §16). `USER_ACCOUNT_DELETE_GRACE_HOURS` records the window for the existing email/support request. The real blocker for a surface is the **missing Supabase auth-admin deletion wiring** (`delete_org` docstring), *not* the deferred permissions model. | **SURFACED as a user-facing decision:** self-service surface vs support path. The window is ruled (7 days). The constant is a **support-SLA record**; its test assertion is labelled a *documentation pin*, not a behaviour check. |
| D4 | What "restore" means for a team account | **Support/operational restore, documented.** No self-service team-restore endpoint exists. | **SURFACED as a user-facing decision:** add a self-service team-restore surface or not. |
| D5 | Does deleting a team account delete everything inside? | **NO today — a verified second behaviour change, larger than the issue assumed.** The cascade revokes keys/memberships and drops the default graph's *namespace*; it erases **no** org backup artifact (default + custom nested pools survive indefinitely) and no custom namespace. **To be filed** (step 7). The doc states the promise AND the current status with the follow-up link. | Enumerated below. |
| D6 | Does a delete request reach backups (and the mirror), or do they age out? | **NOT DECIDED — surfaced as open.** (a) support/account delete request vs the 24/7/4 aging cycle; (b) propagation to the **second-region mirror**. The doc + wording state the current default (mirror off) and the path-qualified horizon, never claiming choice-independence. | **SURFACED as a decision needed.** |

### D5 evidence — what team-account deletion actually does today

Read from the code (worktree HEAD `57038760f`):

| Phase | What runs | What it deletes |
|-------|-----------|-----------------|
| `delete_org` (hosted_api.py) | revoke api keys, mark memberships removed, revoke invitations, stamp `deleted_at` + stored `grace_hours` | access only (soft) |
| `_purge_deleted_orgs` → `_drop_org_acl_users` | enumerates **all** `graphs` rows, drops each graph's FalkorDB **ACL user** | per-graph credentials |
| `_purge_deleted_orgs` → `_drop_org_graph_strict(org_id, graph_name)` | issues `GRAPH.DELETE` for **only the default graph** (no backup call) | default namespace |
| `purge_org_control_plane` (supabase_control.py) | DELETE api_keys, org_memberships, invitations, organizations | control-plane rows; `graphs` rows die via FK cascade |
| **NOT done** | custom-graph namespaces never `GRAPH.DELETE`d; **no** org backup artifact erased — `prune_backups(graph_id=None)` explicitly skips nested keys, so nested pools of **every** org graph (default + custom) and `ops/teams/{org}/...` state survive indefinitely | **orphaned namespaces + backups (default included)** |

The owner's "should delete everything inside that team account" is therefore **not**
implemented. The follow-up must cover **org-wide** artifact purge, default graph included.

---

## Adversarial Threat Surface

**(not adversarial).** No gate, no enforcement point, no attacker-facing surface. The team
cascade gap is *documented and filed*, not implemented, so no destructive path is widened.

---

## Phase 1.5 — Research

**Trigger assessment:** axes all **low** (no new UI; no model change; leaf module + doc); no
third-party deps. In-repo precedent for a single-authority constant
(`test_trash_grace.py::test_grace_window_constant_is_single_sourced`) and for a Python test
parsing a committed JS constant (`tests/test_cross_subdomain_cookie_sync.py` reads
`website/assets/supabase-session.js`). External research not demonstrated — **skipped per the
activation rule**.

---

## Implementation plan

### 1. New `tortoise/retention.py` — the window authority
Leaf module, no imports. `RESTORE_WINDOW_DAYS = 7`, `RESTORE_WINDOW_HOURS = 168`. Docstring
points at `docs/retention-and-deletion.md`.

### 1b. New `docs/retention-and-deletion.md` — the canonical document
Author the document with: the reconciliation table (the path-dependent horizon above); the
**rule** ("any file that states a window links here or is a named implementation constant");
the **named constants** (`retention.RESTORE_WINDOW_DAYS` = sole window authority;
`backup_config` triple = backup-hold counts; the derived aliases listed as derived); the
R1 distinction (restore ≠ retention); the event-store (30-day) exemption; the D5 team-cascade
status + follow-up link; the D6 open decision; and `§"Proposed public wording"` (the drafted
privacy/dpa text, pending owner approval). Register in `docs/00_index.md`; cross-link from
`docs/data-safety.md`.

### 2. Derive every path from it
- `tortoise/backup_sweep.py`: `_GRAPH_PURGE_GRACE_DAYS = RESTORE_WINDOW_DAYS` (was `= 7`).
- `tortoise/hosted_api.py`: module constants `TEAM_DELETE_GRACE_HOURS` and
  `USER_ACCOUNT_DELETE_GRACE_HOURS` from `retention`; both env reads
  (`delete_org`, `_purge_deleted_orgs`) default to it; docstrings "24h" → "7-day".
- `tortoise/supabase_control.py`: `soft_delete_org` default derives from `retention`; fix the
  stale `TORTOISE_ORG_DELETE_GRACE_HOURS` docstring.
- `tortoise/backup_config.py`: keep the retention triple as the backup-hold authority; derive
  `LOCK_DAYS_MAX = RESTORE_WINDOW_DAYS - 1` (was `6`) and keep the comment pointing at the doc.
- `tortoise/sdk.py`: add the doc pointer beside the 30-day event-retention default.

### 3. Tests — `tests/test_retention_promise.py` (NEW)
Registered in `config/ci-surfaces.yml` under **`api` + `core` + `onboarding`**. No
`select_graph` literals → no `ROUTED_SELECT_GRAPH_SITES` entry.
- `test_restore_window_constants_agree` — asserts the unit-normalized equality
  `backup_sweep._GRAPH_PURGE_GRACE_DAYS * 24 == retention.RESTORE_WINDOW_HOURS ==
  hosted_api.TEAM_DELETE_GRACE_HOURS`; the `USER_ACCOUNT_DELETE_GRACE_HOURS` equality is
  labelled a **documentation pin** (no consumer path exists). Imports live in the test body
  so collection cannot die before the assertion.
- `test_lock_bound_stays_below_restore_window` — `backup_config.LOCK_DAYS_MAX < retention.RESTORE_WINDOW_DAYS`.
- `test_frontend_trash_grace_matches_authority` — parse `website/apps/dashboard/src/graphs.js`
  (`TRASH_GRACE_DAYS = (\d+)`) and assert == `retention.RESTORE_WINDOW_DAYS`; also make
  `graphs.test.js` derive its expectation from `TRASH_GRACE_DAYS` (the existing pattern in
  `test_cross_subdomain_cookie_sync.py`).
- `test_privacy_page_states_backup_horizon_without_contradiction` — ships **in the
  owner-gated wording commit** (asserts the page states the path-qualified horizon and links
  the doc via the public GitHub blob URL). If that commit is dropped, this test is dropped
  with it; it is never left to red on an unapproved page.
- `test_no_unlinked_retention_claims` — the **anti-scatter scan**: recursive over
  `docs/**/*.md`, `website/**/*.html`, `website/apps/dashboard/src/**/*.{js,jsx}`,
  `tortoise/**/*.py`, `supabase/migrations/**/*.sql`, plus `website/apps/dashboard/*/`, failing
  on any retention/deletion numeric claim (7 days / 168 h / 24 h / 28 days / 30 days / four
  weeks in deletion/retention/restore/backup context) not on the allowlist. Allowlist entries
  carry **a reason and, for promise-bearing files, the doc link**. Excluded-with-reason:
  `tests/**` (assertions, not promises). Sentinels asserted in the scanned set (set-containment,
  not a count): `docs/ops/registry-backup-dr.md`,
  `docs/research/2026-09-06-backup-dr-best-practices.md`,
  `website/apps/dashboard/src/graphs.js`, `tortoise/hosted_backup.py`,
  `docs/event-catalog.md`. The issue's own plan doc is allowlisted with reason.

### 4. Update the existing tests the new default breaks
`tests/test_export_delete.py` — **six** sites:
- `test_delete_cascade` (`grace_hours == 24` ×2) → derived window;
- `test_delete_cascade_registry` (`rows[0][1] == 24`) → derived window;
- module docstring line 6 ("24h grace") → 7-day, and the prose comment at ~L857 ("a 24h
  stamp would skip the just-deleted team") → 7-day;
- the three post-grace purge fixtures that seed `deleted_at = now − 48h` with no env override
  (`test_purge_hard_deletes_past_grace_registry` ~L1011, `test_purge_deletes_rows_past_grace_supabase`
  ~L1056, `test_purge_keeps_row_anchor_on_graph_drop_failure_supabase` ~L1096) → move past the
  new window (`timedelta(days=8)`), since 48h is now *inside* the 168h window.
The `grace_hours == 0` env-override case is unchanged.

### 5. Point the scattered places at the doc
Add a one-line reference to `docs/retention-and-deletion.md` in the surveyed places, and let
the scan (step 3) be the completeness authority. Enumerated floor:
`tortoise/{backup_sweep,backup_config,hosted_api,sdk,supabase_control,hosted_backup}.py`;
`website/privacy.html`; `website/dpa.html`;
`website/apps/dashboard/src/{main.jsx,graphs.js,graphs.test.js}`;
`docs/{infra-runbook,data-safety,event-catalog,00_index}.md`;
`docs/ops/{registry-backup-dr,multi-graph-migration-runbook}.md`;
`docs/research/2026-09-06-backup-dr-best-practices.md`; `docs/scoping-2304-delete-semantics.md`;
`docs/plans/2026-09-06-2304-delete-trash-can.md`; `docs/prototypes/2304-trash-ui.md`;
`docs/registry-graph-schema.md`; `docs/scoping-432-subscriptions-claim-lifecycle.md`.
Unrelated numbers (invite/session-cookie expiry, price-notice 30 days, API-key 30-day expiry,
login-session 24h key) are allowlisted **with a reason**, never silently.

### 6. Privacy + DPA wording — DRAFTED, OWNER-GATED
`website/privacy.html` §6 **and `website/dpa.html`** (the same unbounded phrase) are fixed in
one **owner-gated wording commit** (drafted in `docs/retention-and-deletion.md` §"Proposed
public wording", applied to the pages, with the step-3 privacy test). **⛔ Do not merge/publish
until the owner approves the wording** — the PR is opened as a **draft**. The code/doc/test
commits are independent; the owner may approve-and-merge, or drop the wording commit (and the
privacy test with it) and merge the rest.

### 7. Follow-up
File the team-cascade widening (D5): purge **all** org backup artifacts (default + custom
nested pools + ops state) and custom-graph namespaces on team purge; link from the doc.

---

## Verification plan

| Surface | Layer | Check |
|---------|-------|-------|
| three restore windows | unit | `test_restore_window_constants_agree` (RED→GREEN) |
| frontend window binding | unit | `test_frontend_trash_grace_matches_authority` |
| lock bound coupling | unit | `test_lock_bound_stays_below_restore_window` |
| privacy page | contract | `test_privacy_page_states_backup_horizon_without_contradiction` (ships with the gated commit) |
| doc references (recursive) | scan/test | `test_no_unlinked_retention_claims` |
| team grace 7 days | unit | `tests/test_export_delete.py` (6 sites) updated + green |
| graph restore unchanged | integration | existing `tests/test_trash_grace.py` stays green |
| user-account window | doc pin | recorded + documented (support-operated; no code path today) |

### RED evidence protocol
Create `tortoise/retention.py` (step 1), write the test file (step 3), and run **before**
step 2:
```
uv run pytest tests/test_retention_promise.py tests/test_export_delete.py \
  -k "retention or delete_cascade or purge" --import-mode=importlib
```
Three-stage sequence (so RED is recorded for the right reason):
1. **After steps 1+3, before step 2** — RED is the missing-symbol failure
   (`ImportError`/`AttributeError`) in `tests/test_retention_promise.py`. The
   `test_export_delete.py` sites still **pass** (env default is still `24`).
2. **After step 2** (default now `168`) — the six `tests/test_export_delete.py` sites go
   RED: `test_delete_cascade`/`_registry` (`24 != 168`) and the three 48h fixtures (48h now
   **inside** the 168h window, so the team is not purged).
3. **After step 4** — GREEN.

## Acceptance criteria
- [ ] 0 disagreeing windows across the three paths (constants-agree test green; user path = doc pin)
- [ ] 1 canonical document (`docs/retention-and-deletion.md`), registered in the index
- [ ] 0 unlinked numeric retention claims outside it (recursive scan green)
- [ ] frontend `TRASH_GRACE_DAYS` bound to the authority by test
- [ ] privacy + DPA horizon drafted; **owner-gated** (not published without approval)
- [ ] user-account window **recorded and documented** (support-operated; no code path today)
- [ ] D5 gap enumerated (org-wide, default included), filed, and linked
- [ ] D6 surfaced as an open decision (live backups **and** mirror), not assumed

## Wiring check

| Touch point | Type | Covered by | Status |
|-------------|------|------------|--------|
| `tortoise/retention.py` | code | new authority module | ✅ |
| `docs/retention-and-deletion.md` | doc | new canonical doc (step 1b) | ✅ |
| `tortoise/{backup_sweep,hosted_api,supabase_control,backup_config,sdk}.py` | code/config | retention.py | ✅ |
| frontend `graphs.js` + `graphs.test.js` | UI/test | graph-authority binding test | ✅ |
| `tests/test_export_delete.py` (6 sites) | test | new 7-day default | ✅ |
| `config/ci-surfaces.yml` | CI manifest | new test file (api+core+onboarding) | ✅ |
| `website/privacy.html` + `dpa.html` + privacy test | user-facing copy | owner-gated wording commit | ⏳ approval |
| `docs/00_index.md` + `docs/data-safety.md` | docs index/cross-link | new doc | ✅ |
| team cascade (D5) | behaviour | follow-up issue | ⏳ filed |
| backups + mirror on delete request (D6) | decision | surfaced | ⏳ owner |
