<!-- research-path: scoping artifact on issue #1095 (2026-08-13, 3 verify cycles) -->

# Migration Drift Deploy Gate Implementation Plan (#1095)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** App deploys must never ship code that reads Supabase objects not yet applied to prod — a fail-closed deploy-time drift gate + secret-free PR CI invariants.

**Team:** epistemic-team

**Architecture:** Two-layer prevention. (1) Deploy-time gate: before `flyctl deploy` in `deploy-hosted.yml`, compare repo migration version prefixes against the remote `supabase_migrations.schema_migrations` table. **Data source: Supabase Management API** `POST /v1/projects/ybetwichurajbfswfeqa/database/query` with the existing `SUPABASE_ACCESS_TOKEN` repo secret (curl + jq — verified working this session; no new secret needed, resolves the P0 from review; token-based auth is the same proven path supabase-deploy used since #883). Block on repo-ahead table/column/function/unique-index migrations; warn on index-only and remote-ahead. Fail-closed (exit 0/1/2, verify-cutover contract). (2) PR CI: secret-free unique-prefix check (the 0012×2/0015×2 class) + append-only content-immutability check (reject edits/renames/deletions of migration files in the base; allow same-PR-added edits). Migration apply stays dispatch-only (#771); the gate's remediation message is the coupling that forces apply-before-deploy.

### Pattern Research
Skipped — plan touches zero third-party deps (curl + jq are preinstalled on ubuntu-latest runners; `jq` verified present). Supabase CLI source-verified during scoping: `migrateFilePattern = ^([0-9]+)_(.*)\.sql$` → version = prefix before first `_`; `schema_migrations.version` stores that same prefix (byte-identical keys on both sides); `--include-all` covers repo-ahead only. Management API query endpoint: `POST /v1/projects/{ref}/database/query` body `{"query": "..."}`, Bearer token auth, JSON response array (verified live this session — returns `[{"version": ...}]`).

### Integration Surface Map
| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| `.github/scripts/check-migration-drift` (new) | unit (pytest) | exit 0 clean / 1 drift / 2 error; real-corpus fixtures via `DRIFT_API_URL`/`DRIFT_TOKEN` env seam |
| `deploy-hosted.yml` gate step | CI (push to main) | blocks before flyctl deploy on pending table/column/function migration |
| `supabase-deploy.yml` push gate job | CI (push) | warn-only + remediation banner; own concurrency group; apply job dispatch-gated |
| `ci.yml` PR jobs | CI (pull_request) | unique-prefix reject; append-only reject/allow matrix (M/R/D vs A) |
| 9 stale 03→04 comments | static sweep | grep clean |

### Tech Stack
Bash (script, `set -euo pipefail`), curl, jq, GitHub Actions, pytest (script tests).

---

### Task 0: Preflight — confirm secrets + endpoint (tracked)

**Intent:** Remove the P0 from review (SUPABASE_DB_URL does not exist) — the gate uses the EXISTING SUPABASE_ACCESS_TOKEN via the Management API, so no new secret is provisioned. This task verifies the prerequisites are real.
**Acceptance:** `SUPABASE_ACCESS_TOKEN` present in repo secrets; Management API query endpoint returns `schema_migrations` rows for ybetwichurajbfswfeqa.
**Files:**
- None (verification only)

**Step 1:** Confirm secret: `gh secret list | grep SUPABASE_ACCESS_TOKEN` → present (verified 2026-08-13).

**Step 2:** Confirm endpoint: `curl -s -X POST https://api.supabase.com/v1/projects/ybetwichurajbfswfeqa/database/query -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" -H "Content-Type: application/json" -d '{"query":"SELECT version FROM supabase_migrations.schema_migrations ORDER BY version"}'` → returns version rows (verified this session: 0001–0015 + 20260813000001–04).

**Step 3:** Document in supabase/README.md that the drift gate uses the Management API + ACCESS_TOKEN (not DB_URL) — correct the stale DB_URL mentions (#883 removed it).

**Step 4:** Commit (if README changed).

---

### Task 1: `check-migration-drift` script + tests

**Intent:** The load-bearing drift detector — one fail-closed script both workflows call.
**Acceptance:** Script exits 0 (clean/remote-ahead/index-only), 1 (repo-ahead blocking migration), 2 (missing token/API error/parse failure); tested against fixture corpora via `DRIFT_API_URL`/`DRIFT_TOKEN`/`DRIFT_CURL`/`DRIFT_MIGRATIONS_DIR` env seams (curl command + migrations dir overridable for hermetic tests).
**Files:**
- Create: `.github/scripts/check-migration-drift`
- Create: `tests/test_migration_drift_gate.py`

**Step 1: Write the failing tests** (following `tests/test_flip_gate.py`'s pattern — subprocess smoke of the bash script):

```python
# tests/test_migration_drift_gate.py
# Seam: script reads DRIFT_API_URL (default https://api.supabase.com), DRIFT_TOKEN (default $SUPABASE_ACCESS_TOKEN),
# DRIFT_CURL (default "curl") for hermetic fakes. Tests monkeypatch DRIFT_CURL to a stub returning JSON version lists.
def test_clean_exit_zero():            # fixture repo set == fixture remote set → exit 0
def test_repo_ahead_table_blocks():    # remote lacks 20260813000004 (claim_membership) → exit 1, names 04
def test_index_only_warns():           # remote lacks 20260813000003 (idx_audit_ip_time) → exit 0 + WARN
def test_unique_index_blocks():        # remote lacks a CREATE UNIQUE INDEX migration → exit 1
def test_remote_ahead_warns():         # remote has version repo lacks (0000 repair case) → exit 0 + WARN
def test_missing_token_exit_2():       # DRIFT_TOKEN unset → exit 2
def test_api_error_exit_2():           # stub curl returns HTTP 4xx/5xx → exit 2 (ON_ERROR analog)
def test_query_error_exit_2():         # stub curl returns malformed JSON → exit 2
def test_mixed_content_blocks():      # fixture: CREATE TABLE + plain CREATE INDEX → exit 1 (block wins)
def test_unparseable_migration_blocks():  # fixture: unclassifiable statement → exit 1
```

**Step 2: Run to verify they fail** — `python3 -m pytest tests/test_migration_drift_gate.py -v` → FAIL (script absent).

**Step 3: Implement the script** (key logic):
- Version-key extraction: `ls "$DRIFT_MIGRATIONS_DIR"/*.sql | sed -nE 's#.*/([0-9]+)_.*#\1#p'` (matches CLI `^([0-9]+)_` filter; the `-n` + `p` drops non-conforming files — fixes reviewer P2-1). Non-conforming `.sql` filenames are also reported (applied by NOBODY — reviewer P2-5).
- Remote set: `curl -s -X POST "$DRIFT_API_URL/v1/projects/ybetwichurajbfswfeqa/database/query" -H "Authorization: Bearer $DRIFT_TOKEN" -H "Content-Type: application/json" -d '{"query":"SELECT version FROM supabase_migrations.schema_migrations ORDER BY version"}'` then **type-assert** `jq -e 'type == "array"'` before `jq -r '.[].version'` (error responses are objects — assert first for a clear diagnostic; reviewer P2-3).
- **Fail-closed error handling** (reviewer P1-1): check curl exit code, HTTP status (jq `-e` / grep for error), and JSON validity; any failure → `echo "cannot determine migration state" >&2; exit 2`. Missing `DRIFT_TOKEN`/`SUPABASE_ACCESS_TOKEN` → exit 2. `set -euo pipefail`.
- DDL classification (case-insensitive, comment-stripped — reviewer P2-3): strip `--` comments and `/* */`; **BLOCK-FIRST precedence (reviewer P2-2): warn only if NO block pattern matches** — `grep -iE "create unique index|primary key|add constraint .*unique|exclude using|create table|alter table|create or replace function|create trigger|do \$\$"` → block-class; else plain non-unique `grep -iE "create index"` → warn-class; else unparseable → block-class (fail-closed). Mixed content (table + plain index) → block wins.
- Exit contract: 0 clean/warn-only; 1 blocking drift (print pending list + remediation: `gh workflow run supabase-deploy.yml --ref main` / `supabase db push --linked --include-all`; `supabase migration repair --linked --status applied <version>` for rename case); 2 error.
- Print BOTH repo-ahead and remote-ahead sets on drift (reviewer recommendation) for operator clarity.

**Step 4: Run to verify pass** — `python3 -m pytest tests/test_migration_drift_gate.py -v` → PASS. Also live check: `SUPABASE_ACCESS_TOKEN=... bash .github/scripts/check-migration-drift` (expect exit 0 — 03/04 applied 14:58 UTC today).

**Step 5: Commit.**

---

### Task 2: Wire the gate into `deploy-hosted.yml`

**Intent:** Every app deploy to prod is gated on schema parity before code ships.
**Acceptance:** Gate step runs after checkout, before the "Verify secrets exist" step (strictly before `flyctl deploy`), is FAIL-CLOSED (missing token → deploy fails), and blocks on repo-ahead. No `if:` guard on the gate step.
**Files:**
- Modify: `.github/workflows/deploy-hosted.yml`

**Step 1:** Add `SUPABASE_ACCESS_TOKEN` to the deploy job's env + the existing "Verify secrets exist" step (the gate needs it; fail-closed if absent).

**Step 2:** Insert gate step after `actions/checkout`, before the secrets step:

```yaml
- name: Check migration drift (fail-closed)
  run: bash .github/scripts/check-migration-drift
  env:
    SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}
```

**Step 3:** Verify the workflow has no early-exit path before deploy (confirmed in review: single job, no matrix, no `if:` conditions). Update the workflow header comment: deploy now requires apply-green first.

**Step 4:** Commit.

---

### Task 3: Add push-triggered gate job to `supabase-deploy.yml`

**Intent:** Pure-migration PRs (no app code) still get drift visibility; apply stays dispatch-only.
**Acceptance:** Push to `supabase/**` runs a WARN-only gate job in its OWN concurrency group with a remediation banner; the apply job is dispatch-gated at JOB level; the stale "Note when push runs no-op" step is removed (its function moves to the gate job banner).
**Files:**
- Modify: `.github/workflows/supabase-deploy.yml`

**Step 1:** Add job-level `if: github.event_name == 'workflow_dispatch'` to the existing `deploy` job (reviewer P1-6/P2-1: job-level, since workflow-level concurrency is claimed per-run — a push run would still hold the shared group). Move the concurrency group to job level on `deploy`; give the new gate job its own group or none.

**Step 2:** Add a new `check-drift` job — **push-only** (`if: github.event_name == 'push'`; on dispatch the apply job IS the operator's action, a parallel gate would warn while apply fixes — reviewer P2-5):
- `bash .github/scripts/check-migration-drift` with `SUPABASE_ACCESS_TOKEN`.
- On exit 1 → `::warning::Migrations pending — dispatch Deploy Supabase to apply` and exit 0 (WARN-only; the BLOCK lives at deploy-hosted where it means something; repo-ahead on a supabase/** push is the expected state). This replaces the removed "Note when push runs no-op" step.
- On exit 2 → **fail red with the error** (missing token/config is a real problem, not a warning — reviewer P1-7).
- **Do NOT** use the `env.SUPABASE_ACCESS_TOKEN != ''` guard on this step.
- Reviewer P2-5: run gate on **push only** (dispatch runs the apply job itself; a parallel gate on dispatch would warn while apply fixes — noisy).

**Step 3:** Remove the now-dead `- name: Note when push runs no-op (flip gating)` step (inside the deploy job, unreachable once the job is dispatch-gated).

**Step 4:** Update the flip-sequence header comment: operator now waits for apply green, then dispatches deploy-hosted (the gate enforces apply-before-deploy).

**Step 5:** Commit.

---

### Task 4: PR CI invariant jobs (shared script) in `ci.yml`

**Intent:** Catch the duplicate-prefix and content-edit classes at review time, secret-free. The check logic lives in a SHARED script so the hermetic test exercises the shipped logic, not a Python copy (reviewer P1-2).
**Acceptance:** PR CI rejects duplicate prefixes, rejects edits/renames/deletions of base-branch migration files, allows same-PR-added edits. Hermetically tested via the shared script against a fixture git repo (observable in THIS PR, not only on a future one).
**Files:**
- Create: `.github/scripts/check-migration-append-only` (prefix mode + diff mode; seams `DRIFT_REPO`/`DRIFT_BASE_SHA`)
- Modify: `.github/workflows/ci.yml`
- Create: `tests/test_migration_append_only.py`

**Step 1:** Implement `.github/scripts/check-migration-append-only` (single script, two modes via arg):
- `prefix`: `ls "$DRIFT_REPO/supabase/migrations"/*.sql | sed -nE 's#.*/([0-9]+)_.*#\1#p' | sort | uniq -d` → non-empty = exit 1. Also fail on any `.sql` filename not matching `^[0-9]+_.*\.sql$` (CLI-non-conforming = applied by nobody — reviewer P2-5).
- `diff`: `git -C "$DRIFT_REPO" diff --find-renames=20% "$DRIFT_BASE_SHA"...HEAD --name-status -- supabase/migrations/` — status `M`/`R`/`D` on a path present in the base tree → exit 1 ("migrations are append-only — add a new timestamp file"); status `A` (genuinely new path) → allowed. Deletions (`D`) explicitly rejected (reviewer P1-3); rename threshold 20% so content-drift renames aren't emitted as D+A.
- Historical violations would be rejected (e9711813 edited applied 0015, 98e466b3 ported a DROP into applied 20260813000002) — correct.

**Step 2:** ci.yml jobs (named **`migration-unique-prefix`** and **`migration-append-only`** — reviewer P2-4) call the shared script:
- Both jobs: checkout with **`fetch-depth: 0`** (reviewer P1-4/integration P1-1: default fetch-depth:1 means the base SHA isn't available; the existing docs job's `|| true` swallow is the proof).
- `migration-unique-prefix`: `bash .github/scripts/check-migration-append-only prefix`
- `migration-append-only`: `bash .github/scripts/check-migration-append-only diff` with `DRIFT_BASE_SHA: ${{ github.event.pull_request.base.sha }}`

**Step 3:** Hermetic test `tests/test_migration_append_only.py`: build a fixture git repo (or crafted trees + `--name-status`), drive the SHARED script via subprocess, exercise M/R/D/A matrix.

**Step 4:** Add the jobs (`migration-unique-prefix`, `migration-append-only`) to branch-protection required checks on `main` — **SEQUENCE: push ci.yml first → PR run reports the check names → configure required checks → merge** (reviewer P2-2; `enforce_admins=false` documented as residual).

**Step 5:** Commit.

---

### Task 5: Comment sweep (03→04) + live-doc updates

**Intent:** Remove stale migration-ID references that would miswire the gate's remediation guidance.
**Acceptance:** Grep for "20260813000003" in live code/docs returns only historical plan docs (correction-note only).
**Files:**
- Modify: `tortoise/supabase_control.py:1099,1125,1139`; `tortoise/hosted_api.py:602,5198`; `tortoise/audit_events.py:101`; `tests/test_supabase_control.py:1341`; `tests/fake_control_plane.py:96`; `tests/test_claim_endpoints.py:251`
- Modify: `supabase/README.md` (deploy section — gate uses Management API + ACCESS_TOKEN; correct stale DB_URL claims; flip-sequence now apply-before-deploy)

**Step 1:** Replace "20260813000003" → "20260813000004" in the 9 code/test refs (claim_membership + audit_events.detail live in 04 post-#1082 renumber; verify each context). Do NOT touch `supabase/migrations/20260813000003_*.sql` or `supabase/tests/pglite/validate.mjs:67` (legitimately reference the 03 filename).

**Step 2:** Update supabase/README.md: Deploy section (Management API gate, not DB_URL), flip-sequence comment (apply-before-deploy), remove stale DB_URL-as-gating-secret claim (#883 made it token-only).

**Step 3:** Add a correction note to `docs/plans/2026-08-13-1082-claim-path.md` (historical plan doc — do not rewrite history).

**Step 4:** Verify `grep -rn "20260813000003" tortoise/ tests/ .github/ supabase/README.md` → **zero matches in live code** (historical docs under `docs/` legitimately retain 03 — the audit-index migration IS 03; only claim_membership moved 03→04).

**Step 5:** Commit.

---

### Task 6: Verify + reconcile

**Intent:** Prove the gate would have blocked today's incident; confirm prod is current.
**Acceptance:** Incident fixture test passes; remote schema_migrations shows 03/04 applied; full test suite green.
**Files:**
- Test: `tests/test_migration_drift_gate.py` (incident fixture)

**Step 1:** Incident fixture: repo has 20260813000004, remote set lacks it → script exits 1 naming 04 (replays the 14:41→14:58 window).

**Step 2:** Live check: `SUPABASE_ACCESS_TOKEN=... bash .github/scripts/check-migration-drift` → exit 0 (03/04 applied).

**Step 3:** Run the hermetic suite: `python3 -m pytest tests/ -q` (expect green; 46/46 baseline).

**Step 4:** Commit.
