<!-- research-path: issue-scoping comment #5455256203 (### Axis Research + ### Integration Docs embedded) -->

# #1894 Implementation Plan — docs memory-source switch state, last-indexed timestamp, index-job ETA

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the "GitHub docs" memory-source switch visibly read ON when indexed, persist a "last indexed at" timestamp in onboarding_state that survives reload, and surface live progress/ETA on both index-job cards.

**Team:** epistemic-team
**Complexity:** standard (Level: task → task-workflow-standard, gated)

**Architecture:** Three additive layers on the existing registered-state-key pattern:
1. **Backend state** (`tortoise/hosted_api.py`): register `github_indexed_at` + `github_docs_indexed_at` (ISO timestamp, `str|None`) in all four registration surfaces (both default dicts, derived allowlist, PATCH model), stamp them in the same completion branch that flips `*_indexed=True`, and write live per-repo `_job(...)` progress during both walks.
2. **Frontend derivation** (new pure module `memorySourcesStatus.js`, repo's zero-dep node --test convention): `formatRelativeTime`, `docsIndexedLabel`, `jobStatusLine` (elapsed always; progress/ETA only when real fields exist; ETA suppressed at progress 0).
3. **Frontend render** (`main.jsx` + `index.css`): unconditional "Indexed · <rel time>" docs label, github-row "Last indexed" suffix (null-guarded), CSS `.switch[disabled][data-on='true']` full-opacity, live status lines, `maxTries` 100 for index polls, `refreshOnboarding()` in both polls' `onDone`.

**No new third-party deps** (backend: stdlib `datetime`; frontend: existing React + node --test).

### Pattern Research

> **Findings date:** 2026-08-28
> Gate skipped: plan touches zero third-party dependencies (backend stdlib only; frontend uses existing React 19 + vite + node --test — no new library, no version change). In-repo patterns used exclusively (registered-state-key pattern, `install_probe_*` str-key precedent, pure-module node --test convention). Scoping Phase 1.5 `### Axis Research` (UX axis) already triangulated the relevant external guidance; embedded below as PRIOR_RESEARCH.

**PRIOR_RESEARCH (from scope, source-tagged):**
- [atomica11y] disabled switch stays in a11y tree; `aria-checked` must stay truthful or the switch is announced "off" — accessibility failure. Our fix keeps `aria-checked={docsOn}` truthful (already is) and fixes the visual via CSS.
- [accessibility.build] disabled switch state must remain discoverable; dimming alone is not a state signal.
- [nngroup] percentage/time-remaining only for waits >10s; step-based progress when accurate percentage impossible; fake precise progress misleads more than a spinner.
- [cloudfour] count completed steps when total unknowable; ETA not always feasible — never fake it.
- [codemia] show % immediately; wait a few updates before ETA; suppress ETA when rate noisy.
- [Apple HIG] as accurate as possible; 90%-fast-then-slow reads as deceptive.
- **Design implication:** ETA must derive from REAL per-repo progress (backend live writes — the frontend never fabricates); ETA suppressed at progress 0 (github first-run ONE-repo bound); elapsed time always.

### Integration Surface Map

Per-issue verification checklist (Standalone — no epic test-design); surfaces verified against code:

| Surface | Boundary | Test Layer | Test / Verification | Bug-pattern flags |
|---------|----------|-----------|---------------------|-------------------|
| onboarding_state allowlist registration (4 surfaces) | data/API — `_ONBOARDING_DEFAULT_STATE` (~9584), `DEFAULT_ONBOARDING_STATE` (~1924), derived `_ALLOWED_STATE_KEYS` (~9624), `OnboardingStatePatchRequest` (~9759) | unit | `test_onboarding_endpoints.py::test_state_keys_registered_parametrized` (`_STATE_KEY_TABLE` += 2 keys) + `test_github_index_lifecycle.py::test_state_keys_registered` + `test_index_docs_api.py::test_github_docs_indexed_state_key_registered` | allowlist silent drop (unregistered key) |
| completion stamps | API — `_run_indexing` finally (~10852), `_run_docs_indexing` finally (~11220) | unit | extend `test_cursor_and_backfill_marker_persisted` (assert `github_indexed_at`); extend `test_docs_job_poll_completed` (assert `github_docs_indexed_at`); extend `test_docs_job_midwalk_quota_hit` (assert stamp parity on quota-partial) | stamp only on the `repos_processed > 0` branch (parity with bool) |
| live per-repo progress | API — walk loops (`_run_indexing` ~10801, `_run_docs_indexing` ~11147) | unit | new mid-walk test: patch `GitHubIndexer.index_repo` (loop-friendly sleep, pump-aware), assert `_INDEX_JOBS[job_id]["repos_processed"]/["progress"]` mid-flight; docs variant via `walk_repo` (Task 5b) | live write placed AFTER `repos_processed += 1`; NOTE (plan-review P3): github quota-hit repo breaks BEFORE the increment (uncounted), docs DOCUMENTS-gate check runs AFTER the increment (quota repo IS counted — `repos_processed == 2` on the quota-partial terminal body, pinned by Task 5b's extension); docs `documents_indexed` trails by one repo (read before the current repo's ingest) — "indexed so far" semantics, documented |
| job poll response | API — `index_job_status` (~10976), `docs_job_status` (~11313) | — | no change (already returns `{job_id, **job}`) | — |
| docs row render + a11y | UI — `MemorySources` (~4514), `index.css` (.switch ~194-204, disabled ~328) | pure-module + CSS-rule assertion + build + clickthrough | `memorySourcesStatus.test.js` (docsIndexedLabel with/without timestamp, disconnected-but-indexed) + CSS-rule assertion (read index.css, assert `.switch[disabled][data-on='true']` opacity not 0.6) + `vite build` + manual clickthrough (wizard + Overview, 3 states) | label gate must NOT depend on `githubConnected` |
| job status lines | UI — `GithubIndexStatus` (~4804), `DocsIndexStatus` (~4841) | pure-module | `memorySourcesStatus.test.js` (jobStatusLine: elapsed always; ETA suppressed at progress 0; missing started_at/repos fields → omitted) | field-absence tolerance (`{status:'starting'}` client-minted job has no timestamps) |
| reload-persistence surfacing | UI — `indexDocs`/`reindexGithub` onDone (~1174/1216) | build + clickthrough | `vite build` (wiring) + clickthrough gate (reload shows timestamp without manual refresh) | onDone must call `refreshOnboarding()` |
| poll ceiling | UI — `startBoundedPoll` (~1017) | — | `maxTries: 100` (300s) for index-job polls only | docs ≈90s tight at 120s; org-wide walks exceed |

### Verification Plan

- **Domain:** code (backend Python + frontend JS). UX rated standard (accessibility-sensitive) — full UX verification: pure-module derivations + CSS-rule assertion + `vite build` + manual clickthrough gate (both MemorySources call sites: wizard step-1 + Overview, states: connected+indexed / un-indexed / running job).
- **Backend:** embedded carve-out lane (`TORTOISE_TEST_CARVE_OUT=1`) for the touched test files (`test_onboarding_endpoints.py`, `test_github_index_lifecycle.py`, `test_index_docs_api.py`) + full docker lane (`TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`) for the complete suite in VERIFY.
- **Frontend:** `node --test` on `memorySourcesStatus.test.js` + `vite build`.
- **Deferred (non-code):** none — content/config/research domains untouched.
- **Checklist adaptation (explicit, from scope-verify):** the issue's "component test" row is infeasible as written — the dashboard has NO vitest/jsdom and main.jsx is not importable in node. Renegotiated to: pure-module derivation tests + CSS-rule assertion + `vite build` + manual clickthrough.

---

## Task 1: Register `*_indexed_at` state keys (backend)

**Intent:** Make the new timestamp keys first-class registered state — the allowlist filter silently drops unregistered keys, so all four surfaces must register them (the parametrized test enforces this).

**Acceptance:** `github_indexed_at` + `github_docs_indexed_at` (default `None`, ISO-string values) present in `_ONBOARDING_DEFAULT_STATE`, `DEFAULT_ONBOARDING_STATE`, `_ALLOWED_STATE_KEYS`, and `OnboardingStatePatchRequest.model_fields`; PATCH round-trips an ISO value.

**Files:**
- Modify: `tortoise/hosted_api.py` (`_ONBOARDING_DEFAULT_STATE` ~9584-9622, `DEFAULT_ONBOARDING_STATE` ~1924-1960, `OnboardingStatePatchRequest` ~9759-9820)
- Test: `tests/test_onboarding_endpoints.py` (`_STATE_KEY_TABLE` ~476-507)

**Step 1:** Add to `_ONBOARDING_DEFAULT_STATE` (next to `github_docs_indexed`):
```python
"github_indexed_at": None,            # #1894: last github index completion (ISO, parity with github_indexed)
"github_docs_indexed_at": None,       # #1894: last docs index completion (ISO, parity with github_docs_indexed)
```

**Step 2:** Add the same two keys to `DEFAULT_ONBOARDING_STATE` (next to `github_docs_indexed`).

**Step 3:** Add to `OnboardingStatePatchRequest` (next to `github_docs_indexed`):
```python
github_indexed_at: str | None = None
github_docs_indexed_at: str | None = None
```

**Step 4:** Add both keys to `_STATE_KEY_TABLE` in `tests/test_onboarding_endpoints.py` (values are ISO strings — the parametrized test's existing `patch_value` branch already sends ISO strings for non-bool keys, so zero test-logic change):
```python
"github_indexed_at": "github_indexed_at",
"github_docs_indexed_at": "github_docs_indexed_at",
```

**Step 5:** Run: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_onboarding_endpoints.py::test_state_keys_registered_parametrized -v`
Expected: PASS (all 4 surfaces + PATCH round-trip for both keys).

**Step 6:** Commit.

## Task 2: Stamp `*_indexed_at` on completion (backend)

**Intent:** Persist the "last indexed" timestamp so it survives reload — stamped in the SAME branch that flips the bool (parity semantics: "last time indexing ran and made progress", incl. quota-partial runs — scope-verify cycle-1 P2 resolution).

**Acceptance:** After a github index job completes with ≥1 repo processed, `github_indexed_at` is an ISO timestamp in onboarding_state; same for `github_docs_indexed_at` after a docs job; neither is stamped on a 0-repo failure.

**Files:**
- Modify: `tortoise/hosted_api.py` (`_run_indexing` finally ~10852-10860, `_run_docs_indexing` finally ~11220-11227)
- Test: `tests/test_github_index_lifecycle.py` (`test_cursor_and_backfill_marker_persisted` ~457-464), `tests/test_index_docs_api.py` (`test_docs_job_poll_completed` ~241-256, `test_docs_job_midwalk_quota_hit` ~327-351)

**Step 1:** In `_run_indexing` finally, extend the `repos_processed > 0` branch (plan-review cycle-1 P1/P2 fix: use the MODULE-SCOPE name `datetime` — hosted_api.py line 23 imports `from datetime import UTC, datetime, timedelta`; `_dt` is only a function-local alias at line 1229 and would NameError inside the finally, silently dropping `github_index_cursor` persistence):
```python
if totals["repos_processed"] > 0:
    updates["github_indexed"] = True
    updates["github_indexed_at"] = datetime.now(UTC).isoformat()
```
(same pattern as line ~916 `datetime.now(UTC).isoformat()`.)

**Step 2:** In `_run_docs_indexing` finally, same for docs (`datetime`, module-scope):
```python
if totals["repos_processed"] > 0:
    updates["github_docs_indexed"] = True
    updates["github_docs_indexed_at"] = datetime.now(UTC).isoformat()
```

**Step 3:** Extend `test_cursor_and_backfill_marker_persisted` to assert `state["github_indexed_at"]` is a non-empty ISO string after completion.

**Step 4:** Extend `test_docs_job_poll_completed` to assert `state["github_docs_indexed_at"]` present.

**Step 5:** Extend `test_docs_job_midwalk_quota_hit` to assert `github_docs_indexed_at` IS stamped on the quota-partial run (parity policy — pins the semantics so the partial-run case is not ambiguous).

**Step 6:** Run:
```
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_github_index_lifecycle.py::test_cursor_and_backfill_marker_persisted tests/test_index_docs_api.py::test_docs_job_poll_completed tests/test_index_docs_api.py::test_docs_job_midwalk_quota_hit -v
```
Expected: PASS.

**Step 7:** Commit.

## Task 3: Live per-repo progress during walks (backend)

**Intent:** The job poll must surface real progress mid-walk (not just 0→100 at terminal) so the frontend ETA is honest — per-repo `_job(...)` writes after each processed repo.

**Acceptance:** During a multi-repo walk, polling the job shows `progress` = round(repos_processed/repos_total*100), `repos_processed`, `repos_total`, and (github) `points_created` updated per repo before completion.

**Files:**
- Modify: `tortoise/hosted_api.py` (`_run_indexing` walk loop ~10801-10815, `_run_docs_indexing` walk loop ~11147-11195)
- Test: `tests/test_github_index_lifecycle.py` (new test near `test_in_flight_single_flight_reuses`)

**Step 1:** In `_run_indexing`, immediately AFTER `totals["repos_processed"] += 1` (and after the quota-break guard, which is before the increment):
```python
_job(progress=round(totals["repos_processed"] * 100 / max(len(walk_repos), 1)),
     points_created=totals["points_created"],
     repos_processed=totals["repos_processed"],
     repos_total=len(walk_repos))
```

**Step 2:** In `_run_docs_indexing`, immediately AFTER `totals["repos_processed"] += 1` in BOTH branches (`scope_branch == "all"` and the else branch — note `repos_total` is already set at ~11115; `documents_indexed` reads the running total BEFORE the current repo's ingest, so the doc count trails by one repo — "indexed so far" semantics, documented; also note the docs DOCUMENTS-gate check runs AFTER the increment, so a quota-hit repo IS counted):
```python
_job(progress=round(totals["repos_processed"] * 100 / max(totals["repos_total"], 1)),
     documents_indexed=totals["documents_indexed"],
     repos_processed=totals["repos_processed"],
     repos_total=totals["repos_total"])
```

**Step 3:** New test `test_live_progress_written_during_walk(provisioned, mock_github, monkeypatch)` — **pump-aware mechanics** (plan-review cycle-1 P2 fix: a `threading` barrier inside patched `index_repo` would block the portal loop and deadlock — background tasks only advance WHILE a request is being serviced, per `_drain_jobs` comment test_github_index_lifecycle.py:163-166):
1. PATCH `github_indexed=True` on the team's onboarding_state (registered key) to defeat the first-run ONE-repo bound so the org-wide walk resolves both repos (`mock_github` resolves `["acme/repo1", "acme/repo2"]`).
2. Capture the original `GitHubIndexer.index_repo`; wrap it: delegate repo 1 to the REAL method (mock transport = deterministic), and for repo 2 `await asyncio.sleep(1.0)` (loop-friendly — never a threading barrier) then call the real method. The wrapper must return the full contract the loop destructures (`points_created`, `statements_superseded`, `events_minted`, `issues_beyond_window`, `errors`, `cursor`, `quota_hit`) — delegate to the real method to inherit it.
3. POST `/v1/index/github/re-poll`, then INTERLEAVE request pumps with `_wait_for` polls of `ha._INDEX_JOBS[job_id]` (each `provisioned.tc.get("/v1/onboarding/state")` pumps the portal — mirrors `_poll_until`), asserting `repos_processed >= 1` and `0 < progress < 100` mid-flight, then settle to terminal.
   Monkeypatch on `GitHubIndexer.index_repo` works despite the import-inside-function style (same module object; the class is importable at test top).

**Step 4:** Run: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_github_index_lifecycle.py -v` (whole file — single-flight and eviction tests must still pass with the new `_job` writes).
Expected: PASS (incl. `test_in_flight_single_flight_reuses`, `test_stuck_started_evicted`, `test_terminal_jobs_evicted_on_enqueue`).

**Step 5:** Commit.

## Task 4: New pure module `memorySourcesStatus.js` + tests (frontend)

**Intent:** Extract the status derivations into the repo's zero-dep node --test convention (sessionKey.js/captureStatus.js pattern) — testable without jsdom, and consumed by main.jsx.

**Acceptance:** `formatRelativeTime`, `docsIndexedLabel`, `jobStatusLine`, `jobElapsedSecs` exported; all edge cases (absent timestamps, absent repo fields, progress 0) covered by node --test.

**Files:**
- Create: `website/apps/dashboard/src/memorySourcesStatus.js`
- Create: `website/apps/dashboard/src/memorySourcesStatus.test.js`
- Test: `website/apps/dashboard/src/memorySourcesStatus.test.js`

**Step 1:** Write `memorySourcesStatus.js` (pure, no React):
```js
// memorySourcesStatus.js — #1894: indexed-state + job-progress derivations
// for the memory-source panel. Pure (no React), node --test unit-tested
// (mirrors sessionKey.js / captureStatus.js).

// ISO/epoch → relative "N min ago" (or a short date for stale times).
// Missing/unknown → null (the caller omits the suffix — never fabricates).
export function formatRelativeTime(isoAt, nowMs) {
  if (!isoAt) return null
  const t = Date.parse(isoAt)
  if (Number.isNaN(t) || !nowMs) return null
  const secs = Math.max(0, Math.floor((nowMs - t) / 1000))
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)} hr ago`
  return new Date(t).toLocaleDateString()
}

// Docs indexed label: "Indexed" always (truthful ON-state), with the
// relative-time suffix ONLY when a persisted timestamp exists (legacy
// indexed teams have no timestamp — honest omission, no fabricated time).
export function docsIndexedLabel(state, nowMs) {
  if (!state || !state.github_docs_indexed) return null
  const rel = formatRelativeTime(state.github_docs_indexed_at, nowMs)
  return rel ? `Indexed · ${rel}` : 'Indexed'
}

// Elapsed seconds from the job dict (epoch started_at/created_at).
// Missing timestamps (client-minted {status:'starting'}) → null.
export function jobElapsedSecs(job, nowMs) {
  if (!job || !nowMs) return null
  const t = job.started_at != null ? job.started_at
         : job.created_at != null ? job.created_at : null
  if (t == null) return null
  return Math.max(0, Math.floor(nowMs / 1000 - t))
}

export function fmtElapsed(secs) {
  if (secs == null) return null
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

// Live job-status line: elapsed ALWAYS; progress + ETA only when real
// fields exist (never fabricated — ETA suppressed at progress 0, matching
// the github first-run ONE-repo bound and the nngroup/codemia guidance).
export function jobStatusLine(job, nowMs) {
  if (!job) return null
  const elapsed = fmtElapsed(jobElapsedSecs(job, nowMs))
  const parts = []
  if (elapsed) parts.push(elapsed)
  const processed = job.repos_processed
  const total = job.repos_total
  if (processed != null && total != null) {
    parts.push(`${processed}/${total} repos`)
    if (total > 0 && job.progress != null && job.progress > 0 && job.progress < 100) {
      const remain = 100 - job.progress
      const secs = jobElapsedSecs(job, nowMs)
      if (secs != null && secs > 5) {
        const eta = Math.round((secs / job.progress) * remain)
        parts.push(`~${fmtElapsed(eta)} left`)
      }
    }
  } else if (job.progress != null && job.progress > 0 && job.progress < 100) {
    parts.push(`${job.progress}%`)
  }
  return parts.length ? parts.join(' · ') : null
}
```

**Step 2:** Write `memorySourcesStatus.test.js` covering:
- `formatRelativeTime`: fresh (<60s → "just now"), minutes, hours, absent → null, invalid → null. Stale (>24h) → assert `typeof result === 'string' && result.length > 0` — NEVER a locale-specific date string (`toLocaleDateString()` output varies across locales/CI; plan-review cycle-1 P3 + cycle-2 P4).
- `docsIndexedLabel`: indexed+timestamp → "Indexed · 2 min ago"; indexed, no timestamp → "Indexed" (legacy team, honest); not indexed → null; disconnected-but-indexed → still returns the label (no githubConnected dependency).
- `jobElapsedSecs`: epoch started_at; created_at fallback; missing → null.
- `jobStatusLine`: **progress 0 with NO repos fields** (real backend shape until the first live write) → elapsed only, NO ETA; progress 0 WITH repos fields → elapsed + "0/N repos", still NO ETA (the repos count renders unconditionally when fields exist; only the ETA/% is progress-gated); progress 50 with started_at 60s ago → "1m 0s · 1/2 repos · ~1m 0s left"; missing repos fields → elapsed only; missing timestamps → null; **progress >= 100 → NO ETA suffix** (plan-review cycle-1 P4: a slow single-repo walk's live write fires at progress 100 → remain=0 → "~0s left" flash would read as fake precision; suppress when `job.progress >= 100`).
- Derivations only in Task 4 — the CSS-rule assertion lands in Task 5 Step 8 after the rule exists (plan-review cycle-1 P1 fix: asserting a rule Task 5 hasn't added yet would make Task 4's "Expected: PASS" unreachable).

**Step 3:** Run: `cd website/apps/dashboard && node --test src/memorySourcesStatus.test.js`
Expected: all PASS.

**Step 4:** Commit.

## Task 5: Render the indexed state + live status in main.jsx + CSS (frontend)

**Intent:** Wire the derivations into the panel: docs row shows the truthful indexed label (unconditional on connectivity), github row shows "Last indexed", the status components render the live line, the disabled-but-on switch is not dimmed, and the polls refresh onboarding state on completion.

**Acceptance:** After implementation: docs switch full-opacity ON when indexed with "Indexed · <rel time>" label visible (even when disconnected); github row suffix only when timestamp present; in-progress job cards show elapsed + progress + ETA; job completion refreshes the timestamp without manual reload; `vite build` passes.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (imports ~top, MemorySources ~4514-4770, toggleDocs ~1148-1157, indexDocs ~1188-1236, reindexGithub ~1159-1191, GithubIndexStatus ~4804, DocsIndexStatus ~4841, startBoundedPoll call sites)
- Modify: `website/apps/dashboard/src/index.css` (.switch block ~194-204, disabled ~328)

**Step 1:** Import the module at the top of main.jsx (alongside the existing captureStatus/sessionKey imports):
```js
import { docsIndexedLabel, formatRelativeTime, jobStatusLine } from './memorySourcesStatus.js'
```

**Step 2:** In `MemorySources`, derive the docs label from the shared ticker (plan-review cycle-1 P4: `docsLabel` and the status lines must consume the SAME `now` value so "2 min ago" stays fresh — see Step 4 for the ticker, declared BEFORE the component's early returns):
```js
const docsLabel = docsIndexedLabel(state, now)
```
Replace the gated copy at ~4656-4658 (`{docsIndexed && githubConnected && (...)}`) with the unconditional label (when `docsIndexed`, regardless of `githubConnected`):
```jsx
{docsIndexed && docsLabel && (
  <p className="memory-source-state" aria-live="polite">{docsLabel}</p>
)}
```
**Contradictory-copy fix (plan-review cycle-1 P2):** also gate the "Connect GitHub first to index docs." branches (~4649-4653 and the `!githubConnected && docsWantOn` branch) on `!docsIndexed` — an indexed-but-disconnected team must NOT render "Connect GitHub first" under the "Indexed · <time>" label (contradictory instructions on the very row this issue fixes):
```jsx
{!githubConnected && !docsIndexed && docsWantOn ? ( ... ) : !githubConnected && !docsIndexed ? (
  <p className="dim small">Connect GitHub first to index docs.</p>
) : null}
```
Keep the Re-index docs affordance (rendered when `githubConnected`); the existing "Docs are indexed and active as a source. Use 'Re-index docs' to refresh." copy may be merged into the label line or kept as secondary — keep the button semantics intact.

**Step 3:** Github row (~4569): append the last-indexed suffix with a FULL null-guard (plan-review cycle-1 P3: an outer guard alone would render "· Last indexed null" for a present-but-unparseable value; mirror `docsIndexedLabel`'s inner-null contract):
```jsx
const lastIndexed = formatRelativeTime(state.github_indexed_at, now)
...
{state.github_indexed_at && lastIndexed && (
  <span className="dim small"> · Last indexed {lastIndexed}</span>
)}
```

**Step 4:** `GithubIndexStatus` started branch (~4806-4809): replace the bare "Indexing in progress…" with the live line:
```jsx
const line = jobStatusLine(job, now)
return <p className="dim small" aria-live="polite">{line ? `Indexing… · ${line}` : 'Indexing in progress…'}</p>
```
Same for `DocsIndexStatus` (~4846-4849): `Docs indexing… · ${line}`. Both status components consume the `now` prop passed from `MemorySources` (they are already rendered by it — `{indexJob && <GithubIndexStatus job={indexJob} />}` at ~4626, `{docsJob && <DocsIndexStatus job={docsJob} />}` at ~4749). **Ticker placement (plan-review cycle-1 P3):** `MemorySources` has EARLY RETURNS (`if (loading) return`, `if (!state) return`) — the hook MUST be declared at the TOP of the component body, BEFORE those early returns, or React throws "Rendered more hooks than during the previous render" when loading flips:
```js
const [now, setNow] = React.useState(Date.now())
React.useEffect(() => {
  const t = setInterval(() => setNow(Date.now()), 30_000)
  return () => clearInterval(t)
}, [])
```
Pass `now` to the status components and use it for `docsLabel` + the github-row suffix (single source, no per-component intervals).

**Step 5:** `maxTries: 100` for the two index-job polls only (`reindexGithub` ~1174, `indexDocs` ~1216) — `startBoundedPoll` default 40 stays for the wizard connect poll.

**Step 6:** `onDone` refresh: in `reindexGithub` and `indexDocs`, after `onDone: setIndexJob/setDocsJob`, ALSO call `refreshOnboarding()`:
```js
onDone: (job) => { setIndexJob(job); refreshOnboarding().catch(() => {}) }
```
(the wizard connect poll's `onDone` already calls `reindexGithub()`, which funnels through this same choke point — transitive coverage.)

**Step 7:** CSS (`index.css` ~328, after `.switch[disabled]`):
```css
/* #1894: an indexed docs switch is disabled but ON — never dim it to look off */
.switch[disabled][data-on='true'] { opacity: 1; cursor: not-allowed; }
```
Add a `.memory-source-state` style (emphasized, non-dim indexed label):
```css
.memory-source-state { font-size: 13px; margin: 4px 0 0; color: var(--accent, #06b6d4); }
```

**Step 8:** **CSS-rule assertion lands here** (plan-review cycle-1 P1 fix — the rule now exists): append to `memorySourcesStatus.test.js` a test that reads `index.css` and asserts the `.switch[disabled][data-on='true']` rule is present and does NOT set `opacity: 0.6` (text-level, zero deps). **Path resolution (plan-review cycle-2 P2 fix — `node --test` keeps `process.cwd()` = the invocation dir, so a CWD-relative `./index.css` from `website/apps/dashboard` would ENOENT):** read via `readFileSync(new URL('./index.css', import.meta.url), 'utf8')` — verified working on Node 22 (`package.json` has `"type": "module"`).

**Step 9:** Run: `cd website/apps/dashboard && npm run build` (vite build) — expected: PASS, no import errors.
Run: `cd website/apps/dashboard && node --test src/memorySourcesStatus.test.js` — expected: all PASS (incl. the CSS-rule assertion).

**Step 10:** Manual clickthrough gate (app-test / local-app-testing): wizard step-1 + Overview panel, three states — (a) connected+indexed team (label + full-opacity switch + timestamp), (b) un-indexed team (dimmed/off switch, no label), (c) running job (live progress line). If no live env available, document the gate as pending-human-clickthrough and rely on derivations + build + backend tests; report explicitly.

**Step 11:** Commit.

---

## Task 5b: Docs mid-walk live-write coverage (backend test)

**Intent:** Pin the docs walk's live `_job` writes (both increment sites: `scope_branch == "all"` and single-branch) so a placement regression in either branch fails the suite (plan-review cycle-1 P3: terminal-only coverage would miss a wrong-branch live write).

**Acceptance:** A docs job polled mid-walk shows `progress` between 0 and 100 with `repos_processed`/`repos_total` populated.

**Files:**
- Test: `tests/test_index_docs_api.py` (extend `test_docs_job_midwalk_quota_hit` ~327-351 OR new test patching `GitHubDocsIndexer.walk_repo` with a loop-friendly `asyncio.sleep`)

**Step 1 (PRIMARY — mid-walk, mandatory):** new test patching `GitHubDocsIndexer.walk_repo`: capture the original method, **delegate repo 1 to the REAL method** (the walk loop destructures `walk["blobs_fetched"]`/`["skipped_binary"]`/`["skipped_oversized"]` — a wrapper returning a partial dict raises KeyError; the real method inherits the full contract), make repo 2 `await asyncio.sleep(0.5)` then delegate, POST `/v1/index/docs`, and INTERLEAVE `provisioned.tc.get("/v1/onboarding/state")` pumps with `_wait_for` polls of `_INDEX_JOBS[job_id]` asserting `0 < progress < 100` and `repos_processed`/`repos_total` populated mid-flight, then settle to terminal (same pump-aware pattern as Task 3 Step 3).
**Step 1b (SUPPLEMENT):** extend `test_docs_job_midwalk_quota_hit` to additionally assert the terminal body carries `repos_total == 2` AND `github_docs_indexed_at` is stamped (quota-partial stamp parity from Task 2 Step 5). NOTE (plan-review cycle-2 P2): a TERMINAL-only assertion is vacuous for live-write placement — the completion `_job(status="completed", ..., repos_total=...)` (~11202) always carries the totals regardless of per-repo writes, and `test_docs_job_poll_completed` already asserts them; only the mid-walk assertion pins placement.

**Step 2:** Run: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_index_docs_api.py -v` — expected: PASS.

**Step 3:** Commit.

---

## Task 6: Full verification + PR

**Intent:** Run the complete verification plan (backend both lanes + frontend) and ship via commit-workflow.

**Acceptance:** All verification green; PR opened with code-review gate; `implementing` → `implemented` labels.

**Files:** none (verification)

**Step 1:** Embedded carve-out lane:
```
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_onboarding_endpoints.py tests/test_github_index_lifecycle.py tests/test_index_docs_api.py -v
```
Expected: PASS.

**Step 2:** Docker lane (full suite):
```
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/ -v
```
Expected: PASS (or pre-existing failures documented — tech-debt pre-flight).

**Step 3:** Frontend: `cd website/apps/dashboard && node --test src/ && npm run build` — expected: PASS.

**Step 4:** Run commit-workflow skill (pre-flight, PR, code-review gate, auto-merge). Rebase on origin/main if #1893 merged concurrently (conflict region: main.jsx + hosted_api.py registration table — resolve region-locally).

**Step 5:** Label lifecycle: `gh issue edit 1894 --remove-label implementing --add-label implemented`.

## Known Limitations (documented, not in scope)
- Runs >300s degrade to the existing honest timeout copy ("Still running — check back in a moment") + one late `refreshOnboarding()` — no auto-resume beyond the bounded window (scope-verify P2 resolution; documented).
- Legacy indexed teams (pre-deploy) show "Indexed" without a time — no truthful backfill exists (in-memory jobs evicted).
- `docsWantOn` toggle intent still unpersisted (deferred — separate state-model change).
