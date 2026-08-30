---
title: "#1893 — Persist GitHub Source-Scope Repo Selection in onboarding_state — Implementation Plan"
type: engineering
domain: capability
doc_status: draft
created: 2026-08-28
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

<!-- research-path: issue #1893 body (Research: none; scoping O/I/T + Verification Checklist embedded) — standalone, no epic brief -->

# #1893 — Persist GitHub Source-Scope Repo Selection in onboarding_state Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Persist the user's GitHub source-scope selections (issues repo list + docs repo/branch list) as allowlisted `onboarding_state` keys so they survive logout/reload and rehydrate the selectors exactly once per team session.

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Approach 1 — write-on-change PATCH + one-shot hydration. The dashboard wraps the two scope setters: every user change `setScope(next)` AND fire-and-forget PATCHes `{github_issues_scope: next.repos}` / `{github_docs_scope: [{repo, branch}…]}` through a single FIFO promise queue (serializing the server's whole-state non-atomic RMW). A one-shot hydration effect — gated on `reposLoaded && onboarding && !reposLoadFailed`, keyed on the `currentTeamId` **state** (not the ref — the ref is populated in the mount-gate path that races the onboarding chain; keying on state guarantees a re-fire when the team resolves, closing the null-teamId dead-path) — reconciles the persisted scope against the live org repo list and seeds the selectors exactly once per team session (never inside `refreshOnboarding()`, which re-fires on mount, connect, toggles, and `finishWelcomeLoads`). Server registers the two keys in both default-state dicts + the PATCH model (allowlist-derived), and validates scope payloads at the PATCH boundary reusing `_validate_repo_scope` + `_is_safe_branch` (400 semantics, consistent with the index endpoints). Pure reconcile/serialize/job-body/gating helpers move to `src/sourceScope.js` for `node --test` coverage (captureStatus.js / sessionKey.js precedent — no vitest/jsdom).

### Solution Diamond (approaches evaluated)

The problem diamond produced 10 verified design requirements (R1 explicit-`[]` clearing write; R2 hydration guard; R3 single-hydration seeding outside `refreshOnboarding`; R4 seed reconciliation vs `reposList`; R5 PATCH-boundary validation; R6 four-surface registration + type-aware registration test; R7 pure-module dashboard tests; R8 no atomicity rework; R9 #1894 region-local edits; R10 team-anchor note). Three distinct solutions were generated:

| Approach | Mechanism | Rejected because |
|---|---|---|
| **A1 — write-on-change PATCH + one-shot hydration (CHOSEN)** | persist at the change boundary (immediate), one-shot seed gated on repos loaded + team resolved | — |
| A2 — `useReducer` + 400 ms debounced persist + pydantic `field_validators` | coalesces PATCHes, pure reducer | 400 ms debounce can lose the last toggle before logout in the exact observed REAUTH incident (violates T1 0-data-loss); flat-canonical refactor rewrites the exact docs-switch rows #1894 edits (worst merge story); 422-vs-400 contract inconsistency vs the index endpoints |
| A3 — index-time write (`last_*` keys) | PATCH scope only inside `reindexGithub`/`indexDocs` (memoryBusy-locked, race-free) | fails O/I/T indicator (a): a selection made but never indexed is lost on reload — the selectors' whole point is that *selection* survives, not last-run scope |
| B (client localStorage mirror — problem-diamond alternative) | team-keyed localStorage, zero server change | contradicts the issue's explicit Targets (server-anchored PATCH→GET round-trip + registration test); per-device; no server contract; diverges from #1894's onboarding_state direction. *Would have been better if* the requirement were device-local reload survival only |

A1's costs — PATCH-per-toggle and a small client-side write-serialization queue — are accepted as the price of instant persistence (closes the REAUTH window) and the smallest MemorySources diff (region-safe vs #1894).

### Pattern Research

> **Findings date:** 2026-08-28
> Gate skipped: plan touches zero third-party deps — React 19 / node:test (Node stdlib) / FastAPI / Pydantic are all already in-repo and used identically throughout `main.jsx` (sessionKey.js, captureStatus.js, identity.js pure-module precedent) and `hosted_api.py` (`_validate_repo_scope` / `_is_safe_branch` / `_STATE_KEY_TABLE`). No new libraries, no version pinning, no novel API usage.

### Integration Surface Map

Standard-tier condensed map (test-design skill — surfaces from the touched code; mirrors the issue's own Verification Checklist).

| # | Surface | Test Layer | Expected Verification |
|---|---------|-----------|----------------------|
| 1 | `onboarding_state` allowlist registration (both default dicts + PATCH model) | unit (`tests/test_onboarding_endpoints.py::test_state_keys_registered_parametrized`) | new keys present in `_ONBOARDING_DEFAULT_STATE`, `DEFAULT_ONBOARDING_STATE`, `_ALLOWED_STATE_KEYS`, `OnboardingStatePatchRequest` |
| 2 | PATCH → GET round-trip, incl. explicit `[]` clear | unit (new `test_scope_keys_explicit_empty_round_trip`) | non-empty persists; `[]` PATCHed when cleared survives the merge as `[]` (never dropped to absent) |
| 3 | PATCH-boundary scope validation (repo/branch allowlist) | unit (new `test_scope_keys_invalid_400`) | invalid repo name / unsafe branch → 400, nothing stored |
| 4 | Dashboard reconcile/serialize/job-body helpers | node --test (pure, `src/sourceScope.test.js`) | reconcile prunes stale, pruned-to-empty = all; serialize emits explicit `[]`; job bodies omit-empty |
| 5 | Dashboard hydration effect + persist wiring | build + code-review (+ manual clickthrough) | `npm run build` passes; selectors restore after reload (no jsdom harness — sessionKey/captureStatus precedent substitutes pure-node tests for the derivations) |
| 6 | dist bundle | build | `npm run build` regenerates committed `dist/` |

**Bug pattern flags:** non-atomic whole-state RMW (`_update_onboarding_state`) → single shared client-side FIFO queue; omit-empty (job builders) vs explicit-`[]` (persist path) contract split; pydantic `None`-filter must not drop `[]` (`[] is not None` — safe); one-shot hydration must not re-seed after `refreshOnboarding()` re-fires; hydration must not early-return on a null `teamId` that never changes again (key on `currentTeamId` state); a failed `repos` fetch must never be indistinguishable from a genuinely empty org (skip hydration, don't prune, don't latch); pre-hydration user interaction must not be clobbered by seeding.

### Journey Test Map

**Journey: "I selected specific repos; they must be there after I reload/log back in"**
1. Select specific issues repos (uncheck "All repos") → **Acceptance:** `PATCH /v1/onboarding/state` fires with `{"github_issues_scope": ["a","b"]}` → **Test:** server round-trip (surface 2) + `serializeIssuesScope` node test (surface 4)
2. Pick a docs repo + branch (or "all") → **Acceptance:** PATCH fires with `{"github_docs_scope": [{"repo":"a","branch":"dev"}]}` → **Test:** server round-trip + `serializeDocsScope` node test
3. Reload / log out + in → **Acceptance:** selectors show the persisted repos (reconciled against the org repo list) → **Test:** `reconcileIssuesScope`/`reconcileDocsScope` node tests + manual clickthrough
4. Clear back to "All repos" → **Acceptance:** PATCH fires with explicit `[]` (never omit-empty) → **Test:** `test_scope_keys_explicit_empty_round_trip`
5. Re-index / index docs → **Acceptance:** job bodies carry the restored scope (not org-wide) → **Test:** `buildIssuesJobBody`/`buildDocsJobBody` node tests (wired at main.jsx:1167/1204-1210)

### Failure Modes
- **Logout during REAUTH_REQUIRED <400ms after a toggle** → **Expected:** persist already fired instantly (no debounce — A1) → **Test:** persist path is synchronous-with-render in `handleIssuesScopeChange`/`handleDocsScopeChange`
- **Repos removed from the org between sessions** → **Expected:** reconcile prunes them; pruned-to-empty = all repos (safe default) → **Test:** reconcile prune cases in sourceScope.test.js
- **`GET /v1/onboarding/github/repos` fails at load (rate-limit/network)** → **Expected:** `reposLoadFailed` is set; hydration is SKIPPED (no seed, no latch, `scopeReadyRef` stays false) so the displayed default empty selection can never be persisted over the real stored one; a reload re-attempts the fetch. This is deliberately asymmetric with "repos removed from the org" — a failed fetch is not evidence of an empty org, so nothing is pruned. → **Test:** `shouldHydrate` predicate case (reposLoadFailed=true → false) in sourceScope.test.js
- **PATCH fails (network)** → **Expected:** `.catch(() => {})` — UI keeps the selection, next change re-persists the full list → **Test:** n/a (fire-and-forget by design; failure is silent)
- **First-timer pre-provision (no onboarding yet)** → **Expected:** hydration gated on `reposLoaded && onboarding` → no premature persist of default empty state → **Test:** `shouldHydrate` predicate case (onboarding=null → false)
- **Team switch** → **Expected:** under first-membership pinning (see Decision 7) onboarding state + reposList are the SAME first-membership team's data on every switch, so the re-armed hydration re-seeds the same state (harmless) and a user's recent choice survives (already persisted; per-key touch keeps it). No cross-team write is possible because there is no cross-team onboarding state. → **Test:** flagged edge, not asserted (pre-existing surface anchor)
- **User interacts with a scope selector before hydration completes** → **Expected:** per-key `scopeTouchedRef` marks ONLY the touched key; hydration seeds the untouched key(s) from the persisted server value and persists the touched key's user-chosen value (never the untouched key's un-seeded default — round-2 scope-verify P1); touch flags reset after hydration so a team switch re-enables seeding → **Test:** documented (the window is real — the "All repos" checkbox is live during the repos-fetch window, though the per-repo checkboxes only render once `reposList` loads; a click during the window is not silently reverted)

**Tech Stack:** React 19 (dashboard, in-repo), FastAPI + Pydantic (hosted_api.py, in-repo), node:test (Node 22 stdlib), pytest (docker lane).

---

## Task 1: Server state-key registration + registration-table extension

**Intent:** Register `github_issues_scope` / `github_docs_scope` on ALL four surfaces (both default dicts, derived allowlist, PATCH model) so the allowlist filter never silently drops them — the exact capture surface the parametrized registration test pins.
**Acceptance:** `test_state_keys_registered_parametrized` passes with the two new keys; `test_capture_surface_keys_shared_across_defaults` still passes; no dashboard change yet.

**Files:**
- Modify: `tortoise/hosted_api.py:1925` (DEFAULT_ONBOARDING_STATE), `tortoise/hosted_api.py:9584` (_ONBOARDING_DEFAULT_STATE), `tortoise/hosted_api.py:9739` (OnboardingStatePatchRequest)
- Test: `tests/test_onboarding_endpoints.py:476` (_STATE_KEY_TABLE), `tests/test_onboarding_endpoints.py:497` (parametrized test)

**Step 1: Extend `_STATE_KEY_TABLE` to a type-aware tuple form (test-first — RED).**
Change `_STATE_KEY_TABLE: dict[str, str]` → `dict[str, tuple[str, object]]` mapping `state_key → (patch_field, sample_value)`. Every existing row becomes a tuple (bool keys keep `True`; timestamp keys keep the ISO string); add the two scope rows:

```python
_STATE_KEY_TABLE: dict[str, tuple[str, object]] = {
    "capture_revised": ("capture_revised", True),
    "capture_ask_shown": ("capture_ask_shown", True),
    "session_capture_receipt": ("session_capture_receipt", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_claude": ("session_capture_receipt_claude", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_claude-desktop": ("session_capture_receipt_claude_desktop", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_claude-web": ("session_capture_receipt_claude_web", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_codex": ("session_capture_receipt_codex", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_cursor": ("session_capture_receipt_cursor", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_pi": ("session_capture_receipt_pi", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_claude": ("session_capture_last_error_claude", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_claude-desktop": ("session_capture_last_error_claude_desktop", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_claude-web": ("session_capture_last_error_claude_web", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_codex": ("session_capture_last_error_codex", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_cursor": ("session_capture_last_error_cursor", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_pi": ("session_capture_last_error_pi", "2026-08-25T00:00:00Z"),
    "install_probe_claude": ("install_probe_claude", "2026-08-25T00:00:00Z"),
    "install_probe_pi": ("install_probe_pi", "2026-08-25T00:00:00Z"),
    # #1893: persisted source-scope keys (short repo names / {repo, branch}).
    "github_issues_scope": ("github_issues_scope", ["repo-a", "repo-b"]),
    "github_docs_scope": ("github_docs_scope", [{"repo": "repo-a", "branch": "main"}]),
}
```

**Step 2: Update the parametrized test to unpack the tuple** (drop the bool/ISO derivation formula):

```python
for state_key, (patch_field, patch_value) in _STATE_KEY_TABLE.items():
    assert state_key in _ONBOARDING_DEFAULT_STATE, ...
    assert state_key in DEFAULT_ONBOARDING_STATE, ...
    assert state_key in _ALLOWED_STATE_KEYS, ...
    assert patch_field in OnboardingStatePatchRequest.model_fields, ...
    r = client.patch("/v1/onboarding/state", json={patch_field: patch_value})
    assert r.status_code == 200, r.text
    assert r.json()["onboarding"][state_key] == patch_value, ...
```

**Step 3: Run to verify RED.**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_onboarding_endpoints.py::test_state_keys_registered_parametrized -v`
Expected: FAIL — `github_issues_scope missing from _ONBOARDING_DEFAULT_STATE`.

**Step 4: Register the keys (GREEN).**
- `_ONBOARDING_DEFAULT_STATE` (hosted_api.py:9584, inside the dict — append-only):
  ```python
  "github_issues_scope": [],  # #1893: persisted issues source-scope (short names; [] = all)
  "github_docs_scope": [],    # #1893: persisted docs source-scope ([{repo, branch}]; [] = all)
  ```
- `DEFAULT_ONBOARDING_STATE` (hosted_api.py:1925): same two lines.
- `OnboardingStatePatchRequest` (hosted_api.py:9739):
  ```python
  github_issues_scope: list[str] | None = None
  github_docs_scope: list[dict] | None = None
  ```
  `_ALLOWED_STATE_KEYS` is derived (`set(_ONBOARDING_DEFAULT_STATE.keys())`, hosted_api.py:9624) — auto-covered. Underscore names → no `_PATCH_FIELD_TO_STATE_KEY` translation needed.

**Step 5: Run to verify GREEN.**
Run: same pytest command as Step 3 plus `...::test_capture_surface_keys_shared_across_defaults`
Expected: PASS (both tests).

## Task 2: PATCH-boundary scope validation + explicit-`[]` round-trip

**Intent:** The PATCH handler must 400-reject invalid repo/branch scope values (same conservative surface as the index endpoints — `_validate_repo_scope` + `_is_safe_branch`) while storing valid values — crucially `[]` (explicit clear = all repos) must survive as `[]`, never be omitted or None-ified.
**Acceptance:** invalid scopes → 400 with nothing stored; `[]` PATCH round-trips as `[]` through GET; valid non-empty scope round-trips (issues: strip/dedupe-normalized; docs: `""`/None branch → `null` at persist — the normalized form is what GET returns, pinned by test).

**Files:**
- Modify: `tortoise/hosted_api.py` — new `_validate_scope_payload` near `_validate_repo_scope` (hosted_api.py:10642) + call in `patch_onboarding_state` (hosted_api.py:9802)
- Test: `tests/test_onboarding_endpoints.py` — new tests in `TestOnboardingState`

**Step 1: Write the failing tests.**

```python
def test_scope_keys_explicit_empty_round_trip(client):
    """#1893: [] is a VALID scope value (all repos) and must round-trip as
    [] — the persist path never omits empty (unlike the job builders)."""
    # seed non-empty, then clear to [] — the clear must land as [] not absent
    r = client.patch("/v1/onboarding/state", json={"github_issues_scope": ["repo-a"]})
    assert r.status_code == 200
    r = client.patch("/v1/onboarding/state", json={"github_issues_scope": [], "github_docs_scope": []})
    assert r.status_code == 200, r.text
    got = r.json()["onboarding"]
    assert got["github_issues_scope"] == []
    assert got["github_docs_scope"] == []
    r = client.get("/v1/onboarding/state")
    assert r.json()["onboarding"]["github_issues_scope"] == []
    assert r.json()["onboarding"]["github_docs_scope"] == []

def test_scope_keys_invalid_400(client):
    """#1893: PATCH-boundary validation — invalid repo/branch scope entries
    are rejected (400), never stored (mirrors the index endpoints)."""
    r = client.patch("/v1/onboarding/state", json={"github_issues_scope": ["bad name!"]})
    assert r.status_code == 400
    r = client.patch("/v1/onboarding/state", json={"github_docs_scope": [{"repo": "ok", "branch": "../../x"}]})
    assert r.status_code == 400
    r = client.patch("/v1/onboarding/state", json={"github_docs_scope": [{"repo": 123}]})
    assert r.status_code == 400
    r = client.patch("/v1/onboarding/state", json={"github_docs_scope": [{"repo": "ok", "branch": 123}]})
    assert r.status_code == 400  # non-str branch → 400 (type-guard before strip, never 500)
    r = client.get("/v1/onboarding/state")
    assert r.json()["onboarding"]["github_issues_scope"] == []  # nothing stored
    assert r.json()["onboarding"]["github_docs_scope"] == []


def test_scope_branch_normalized_to_null(client):
    """#1893: a docs entry with branch "" (default contract) is persisted as
    null (normalized at the PATCH boundary) — GET returns null, and a repeat
    PATCH of the GET value is stable (no drift). Note: pydantic v2 lax mode
    coerces int→str, so `{"github_issues_scope": [123]}` would store
    ["123"] (a syntactically legal short name) — the 400 contract targets
    genuinely invalid inputs, not lax-coercible ones."""
    r = client.patch("/v1/onboarding/state", json={
        "github_docs_scope": [{"repo": "repo-a", "branch": ""}]})
    assert r.status_code == 200, r.text
    assert r.json()["onboarding"]["github_docs_scope"] == [{"repo": "repo-a", "branch": None}]
    # re-PATCH the GET value — stable (null stays null)
    r2 = client.patch("/v1/onboarding/state", json={
        "github_docs_scope": [{"repo": "repo-a", "branch": None}]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["onboarding"]["github_docs_scope"] == [{"repo": "repo-a", "branch": None}]
```

**Step 2: Run to verify RED.**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_onboarding_endpoints.py::TestOnboardingState -k 'scope' -v`
Expected: `test_scope_keys_invalid_400` FAILS (no validation exists yet — invalid values PATCH 200 and get stored) and `test_scope_branch_normalized_to_null` FAILS too (nothing normalizes `""`→null yet — `_update_onboarding_state` stores values raw; the `""`→null normalization lands only with `_validate_scope_payload` in Step 3). NOTE: `test_scope_keys_explicit_empty_round_trip` is GREEN at this point — Task 1 registered the keys, so `[]` survives the None-filter + allowlist merge. Run all three; the invalid-400 + branch-normalization failures are the REDs that drive Step 3's single `_validate_scope_payload`.

**Step 3: Implement `_validate_scope_payload`** (place near `_validate_repo_scope`, hosted_api.py:10642):

```python
def _validate_scope_payload(updates: dict) -> dict:
    """#1893: PATCH-boundary validation for the persisted source-scope keys
    (github_issues_scope / github_docs_scope). Same conservative surface as
    the index endpoints — _validate_repo_scope for issues (short names,
    deduped), _is_safe_branch for docs branches. [] is a VALID value
    (explicit clear = all repos) and is stored as-is — the persist path
    NEVER omits empty (unlike the job builders, where absent = all)."""
    if "github_issues_scope" in updates and updates["github_issues_scope"] is not None:
        repos = _validate_repo_scope(updates["github_issues_scope"])
        updates["github_issues_scope"] = repos if repos is not None else []
    if "github_docs_scope" in updates and updates["github_docs_scope"] is not None:
        scopes = []
        for s in updates["github_docs_scope"]:
            if not isinstance(s, dict) or not isinstance(s.get("repo"), str):
                raise HTTPException(status_code=400, detail="Invalid repo scope")
            repo = s["repo"].strip()
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", repo):
                raise HTTPException(status_code=400, detail="Invalid repo name")
            branch = s.get("branch")
            if branch == "" or branch is None:
                branch = None
            else:
                if not isinstance(branch, str):  # non-str branch → 400, never 500
                    raise HTTPException(status_code=400, detail="Invalid branch")
                branch = branch.strip()  # normalize like repo (padded branches never persist)
                if not _is_safe_branch(branch):
                    raise HTTPException(status_code=400, detail="Invalid branch")
            scopes.append({"repo": repo, "branch": branch})
        updates["github_docs_scope"] = scopes
    return updates
```

**Step 4: Wire into `patch_onboarding_state`** (hosted_api.py:9802) — after the harness/email pops, immediately before `state = _update_onboarding_state(...)`:

```python
    # #1893: validate the persisted source-scope keys at the PATCH boundary
    # (400 on invalid; valid values stored in NORMALIZED form — issues
    # strip/dedupe, docs ""/None branch → null; [] = explicit clear).
    updates = _validate_scope_payload(updates)
    state = _update_onboarding_state(team["team_id"], **updates)
```

Note: `_validate_repo_scope` is defined below the handler (module-level, resolved at call time — fine). Docs branches are NORMALIZED at persist (`""`/None → `None`), matching the `index_docs` consumer exactly (hosted_api.py:11262-11299); issues repos are strip/dedupe-normalized by `_validate_repo_scope`; `[]` (explicit clear) is stored as `[]` — the persist path NEVER omits empty (unlike the job builders, where absent = all).

**Step 5: Run to verify GREEN.**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_onboarding_endpoints.py::TestOnboardingState -v`
Expected: PASS (all scope tests + existing TestOnboardingState tests).

## Task 3: Pure `sourceScope.js` helpers + node --test suite

**Intent:** Move the reconcile/serialize/job-body derivations to a pure, zero-dependency module (captureStatus.js/sessionKey.js/identity.js precedent) so the dashboard's init-from-state + job-body behavior is unit-testable with `node --test` — no vitest/jsdom.
**Acceptance:** `node --test src/sourceScope.test.js` passes; the module has NO imports (pure).

**Files:**
- Create: `website/apps/dashboard/src/sourceScope.js`, `website/apps/dashboard/src/sourceScope.test.js`

**Step 1: Write the failing test file.**

```js
// sourceScope.test.js — node --test (Node 20+, zero deps) (#1893).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  reconcileIssuesScope, reconcileDocsScope,
  serializeIssuesScope, serializeDocsScope,
  buildIssuesJobBody, buildDocsJobBody,
  shouldHydrate, shouldPersist, shouldResetBranch,
} from './sourceScope.js'

test('reconcileIssuesScope: absent/null → all repos ([])', () => {
  assert.deepEqual(reconcileIssuesScope(null, ['a', 'b']), { repos: [] })
  assert.deepEqual(reconcileIssuesScope(undefined, ['a', 'b']), { repos: [] })
})

test('reconcileIssuesScope: keeps known repos, prunes stale', () => {
  assert.deepEqual(reconcileIssuesScope(['a', 'b'], ['a', 'b', 'c']), { repos: ['a', 'b'] })
  assert.deepEqual(reconcileIssuesScope(['a', 'gone'], ['a', 'b']), { repos: ['a'] })
})

test('reconcileIssuesScope: pruned-to-empty = all repos ([])', () => {
  assert.deepEqual(reconcileIssuesScope(['gone'], ['a', 'b']), { repos: [] })
})

test('reconcileDocsScope: absent → all repos with no branches', () => {
  assert.deepEqual(reconcileDocsScope(null, ['a']), { repos: [], branches: {} })
})

test('reconcileDocsScope: keeps known {repo, branch}, prunes stale', () => {
  assert.deepEqual(reconcileDocsScope(
    [{ repo: 'a', branch: 'dev' }, { repo: 'gone', branch: 'x' }], ['a', 'b']),
    { repos: ['a'], branches: { a: 'dev' } })
})

test('reconcileDocsScope: ""/None branch → default (omitted from branches)', () => {
  assert.deepEqual(reconcileDocsScope([{ repo: 'a', branch: '' }, { repo: 'b', branch: 'all' }], ['a', 'b']),
    { repos: ['a', 'b'], branches: { b: 'all' } })
})

test('serializeIssuesScope: explicit [] on clear (never omit-empty)', () => {
  assert.deepEqual(serializeIssuesScope({ repos: [] }), [])
  assert.deepEqual(serializeIssuesScope({ repos: ['a', 'b'] }), ['a', 'b'])
})

test('serializeDocsScope: full list incl. branch; [] on clear', () => {
  assert.deepEqual(serializeDocsScope({ repos: ['a'], branches: { a: 'dev' } }),
    [{ repo: 'a', branch: 'dev' }])
  assert.deepEqual(serializeDocsScope({ repos: ['a'], branches: {} }),
    [{ repo: 'a', branch: '' }])
  assert.deepEqual(serializeDocsScope({ repos: [], branches: {} }), [])
})

test('buildIssuesJobBody: omit-empty (absent = all) — job contract', () => {
  assert.deepEqual(buildIssuesJobBody({ repos: [] }), {})
  assert.deepEqual(buildIssuesJobBody({ repos: ['a'] }), { repos: ['a'] })
})

test('buildDocsJobBody: omit-empty; org preserved', () => {
  assert.deepEqual(buildDocsJobBody({ repos: [], branches: {} }, 'myorg'), { org: 'myorg' })
  assert.deepEqual(buildDocsJobBody({ repos: ['a'], branches: { a: 'dev' } }, 'myorg'),
    { org: 'myorg', repos: [{ repo: 'a', branch: 'dev' }] })
})

// ── gating predicates (#1893, scope-verify P1/P2): the one-shot hydration
// and persist gating decisions are PURE and node-tested — the null-teamId
// dead-path, the repos-fetch-failure prune hazard, and the persist gate.

test('shouldHydrate: false before repos load or onboarding resolves', () => {
  assert.equal(shouldHydrate({ reposLoaded: false, onboarding: null, reposLoadFailed: false, currentTeamId: 't1', hydratedTeamId: null }), false)
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: null, reposLoadFailed: false, currentTeamId: 't1', hydratedTeamId: null }), false)
})

test('shouldHydrate: false while repos fetch failed (never prune on a failed fetch)', () => {
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: true, currentTeamId: 't1', hydratedTeamId: null }), false)
})

test('shouldHydrate: false until the team resolves (no null-teamId dead-path)', () => {
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: false, currentTeamId: null, hydratedTeamId: null }), false)
})

test('shouldHydrate: true exactly once per team; false once hydrated', () => {
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: false, currentTeamId: 't1', hydratedTeamId: null }), true)
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: false, currentTeamId: 't1', hydratedTeamId: 't1' }), false)
  // a team switch re-hydrates (new team id)
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: false, currentTeamId: 't2', hydratedTeamId: 't1' }), true)
})

test('shouldPersist: gated on hydration having completed', () => {
  assert.equal(shouldPersist(false), false)
  assert.equal(shouldPersist(true), true)
})

test('shouldResetBranch: only when the picker knows the branch no longer exists', () => {
  // no branch info loaded yet — trust the persisted value
  assert.equal(shouldResetBranch('dev', null), false)
  // '' and 'all' are always valid (default / every-branch markers)
  assert.equal(shouldResetBranch('', { branches: ['main', 'dev'] }), false)
  assert.equal(shouldResetBranch('all', { branches: ['main', 'dev'] }), false)
  // persisted branch not among the loaded options → stale → reset
  assert.equal(shouldResetBranch('dev', { branches: ['main', 'hotfix'] }), true)
  // branch IS among the loaded options → keep
  assert.equal(shouldResetBranch('dev', { branches: ['main', 'dev'] }), false)
})
```

**Step 2: Run to verify RED.**
Run: `cd website/apps/dashboard && node --test src/sourceScope.test.js`
Expected: FAIL — module not found.

**Step 3: Implement `sourceScope.js`.**

```js
// #1893: pure source-scope persistence helpers — node --test colocated
// (captureStatus.js / identity.js precedent). No React, no fetch.

// Reconcile the PERSISTED issues scope (list of short repo names) against
// the live org repo list from GET /v1/onboarding/github/repos. Stale repos
// are pruned; pruned-to-empty (or absent) = ALL repos ([]).
export function reconcileIssuesScope(persisted, reposList) {
  if (!Array.isArray(persisted) || !Array.isArray(reposList)) return { repos: [] }
  const known = new Set(reposList)
  const repos = persisted.filter((r) => typeof r === 'string' && known.has(r))
  return { repos }
}

// Reconcile the PERSISTED docs scope (list of {repo, branch}) against the
// live org repo list. Stale repos pruned (with their branch); ''/None branch
// = default (omitted from the branches map — the picker's default option).
export function reconcileDocsScope(persisted, reposList) {
  if (!Array.isArray(persisted) || !Array.isArray(reposList)) return { repos: [], branches: {} }
  const known = new Set(reposList)
  const repos = []
  const branches = {}
  for (const entry of persisted) {
    if (!entry || typeof entry.repo !== 'string' || !known.has(entry.repo)) continue
    if (!repos.includes(entry.repo)) repos.push(entry.repo)
    if (typeof entry.branch === 'string' && entry.branch !== '') branches[entry.repo] = entry.branch
  }
  return { repos, branches }
}

// PERSIST serializers — ALWAYS emit the full list ([] on clear). The persist
// path never omits empty (unlike the job builders, where absent = all).
export function serializeIssuesScope(scope) {
  return (scope && Array.isArray(scope.repos)) ? scope.repos : []
}

export function serializeDocsScope(scope) {
  const repos = (scope && Array.isArray(scope.repos)) ? scope.repos : []
  const branches = (scope && scope.branches) || {}
  return repos.map((r) => ({ repo: r, branch: branches[r] || '' }))
}

// JOB-body builders — omit-empty (server treats absent = all). Mirrors the
// existing inline construction at main.jsx:1167 / 1204-1210.
export function buildIssuesJobBody(scope) {
  const repos = (scope && Array.isArray(scope.repos)) ? scope.repos : []
  return repos.length ? { repos } : {}
}

export function buildDocsJobBody(scope, org) {
  const payload = {}
  if (org) payload.org = org
  const repos = (scope && Array.isArray(scope.repos)) ? scope.repos : []
  if (repos.length) payload.repos = serializeDocsScope(scope)
  return payload
}

// #1893 (scope-verify P1/P2): gating predicates for the one-shot hydration
// and the persist path — pure so the null-teamId dead-path, the
// repos-fetch-failure prune hazard, and the persist gate are node-tested.
//
// shouldHydrate: true only when repos loaded, onboarding resolved, the
// repos fetch did NOT fail (a failed fetch is never evidence of an empty
// org — pruning on it would clobber the stored selection), the team is
// resolved (state, so the effect re-fires when the mount gate populates it
// — the ref would leave the effect inert on a null-team dead-path), and
// this team has not been hydrated yet (one-shot per team session).
export function shouldHydrate({ reposLoaded, onboarding, reposLoadFailed, currentTeamId, hydratedTeamId }) {
  if (!reposLoaded || !onboarding) return false
  if (reposLoadFailed) return false
  if (!currentTeamId) return false
  if (hydratedTeamId === currentTeamId) return false
  return true
}

// shouldPersist: the persist path stays gated until hydration has seeded
// (scopeReady) — the default empty state is never written before the
// initial GET resolves.
export function shouldPersist(scopeReady) {
  return !!scopeReady
}

// shouldResetBranch: reset a persisted docs branch to the default ('') when
// the branch picker HAS loaded that repo's options and the persisted branch
// is no longer among them ('' = default and 'all' = every-branch are always
// valid). Prevents a stale persisted branch from sticking a blank picker
// and later failing the docs job. No branch info yet → trust the value.
export function shouldResetBranch(branch, branchInfo) {
  if (!branch || branch === 'all') return false
  if (!branchInfo || !Array.isArray(branchInfo.branches) || !branchInfo.branches.length) return false
  return !branchInfo.branches.includes(branch)
}
```

**Step 4: Run to verify GREEN.**
Run: `cd website/apps/dashboard && node --test src/sourceScope.test.js`
Expected: PASS (19 tests — 13 derivation + 6 gating/reset predicates).

## Task 4: Dashboard wiring — wrapped setters, persist queue, one-shot hydration

**Intent:** Wire the persist path + hydration into `main.jsx` with the SMALLEST diff: new refs/effect/handlers near the scope state (line ~321), the two MemorySources wiring lines replaced, and the two job-body constructions switched to the pure builders.
**Acceptance:** `npm run build` passes; scope changes PATCH immediately (no debounce); selectors rehydrate once per team session after `reposLoaded`; nothing persists before the initial GET resolves.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` — import (~line 14), scope-state block (after line 321), wiring lines 3598-3599 + 4121-4122, `reindexGithub` body (1167), `indexDocs` payload (1204-1210)

**Step 1: Import the pure helpers** (after the sessionKey.js import, ~line 14):

```js
// #1893: pure source-scope reconcile/serialize/job-body helpers (node --test
// unit-tested — sourceScope.test.js).
import {
  reconcileIssuesScope, reconcileDocsScope,
  serializeIssuesScope, serializeDocsScope,
  buildIssuesJobBody, buildDocsJobBody,
  shouldHydrate, shouldPersist, shouldResetBranch,
} from './sourceScope.js'
```

**Step 2: Add refs + wrapped setters + persist queue** immediately after the `issuesScope` useState (main.jsx:321):

> ⛔ Placement rule (scope-verify P1): the refs/handlers block references `currentTeamId` in the hydration EFFECT's deps array — the deps array is evaluated at RENDER time, so the effect must be placed AFTER the `const [currentTeamId, setCurrentTeamId] = React.useState(null)` declaration at **main.jsx:466** (referencing it before that line is a temporal-dead-zone `ReferenceError` on every render — build passes but the app crashes on load). Recommended: refs + handlers + queue at ~321 (they reference only `setIssuesScope`/`setDocsScope`/`api`/serializers), `reposLoadFailed` state at ~321, and the hydration effect immediately after main.jsx:466 (all referenced bindings — `reposLoaded` 318, `onboarding` 297, `reposList` 317, `currentTeamId` 466 — are then in scope).

```js
  // #1893: persist source-scope selections as allowlisted onboarding_state
  // keys (github_issues_scope / github_docs_scope). scopeReadyRef gates the
  // persist path — nothing persists until the initial GET resolves + the
  // one-shot hydration below has seeded. hydratedTeamIdRef keys hydration
  // to ONE pass per team session: refreshOnboarding() re-fires after every
  // reindex/docs run + finishWelcomeLoads, so seeding inside it would
  // clobber newer selections with the stale server value.
  const hydratedTeamIdRef = React.useRef(null)
  const scopeReadyRef = React.useRef(false)
  // #1893 (scope-verify P1): PER-KEY pre-hydration touch tracking — a touch
  // on ONE key must never suppress seeding of the OTHER, and must never
  // persist the other key's un-seeded default over its stored server value
  // (the round-1 single-flag design wiped the untouched key — fixed).
  const scopeTouchedRef = React.useRef({ issues: false, docs: false })
  // #1893: a failed repos fetch must never be treated as an empty org —
  // hydration (and therefore pruning) is skipped and NOT latched, so a
  // reload re-attempts; nothing gets clobbered server-side.
  const [reposLoadFailed, setReposLoadFailed] = React.useState(false)
  // #1893: _update_onboarding_state is a WHOLE-STATE read-modify-write
  // (non-atomic) — issues + docs PATCHes must serialize against each other,
  // so a SINGLE shared FIFO queue (per-key queues would still race cross-key).
  const scopePersistQueueRef = React.useRef(Promise.resolve())

  function persistScope(payload) {
    // fire-and-forget: a failed persist never blocks the UI; the next
    // change re-persists the full list.
    scopePersistQueueRef.current = scopePersistQueueRef.current
      .then(() => api('/v1/onboarding/state', { method: 'PATCH', useSession: true,
        body: JSON.stringify(payload) }))
      .catch(() => {})
  }

  // scope-verify P2: mirror the latest selection in refs so the hydration
  // effect's touched-branch never serializes a STALE closure value (the
  // effect deps deliberately omit issuesScope/docsScope; the refs make the
  // persist ordering-independent of React passive-effect flush timing).
  const issuesScopeRef = React.useRef({ repos: [] })
  const docsScopeRef = React.useRef({ repos: [], branches: {} })

  function handleIssuesScopeChange(next) {
    scopeTouchedRef.current.issues = true
    issuesScopeRef.current = next
    setIssuesScope(next)
    if (shouldPersist(scopeReadyRef.current)) persistScope({ github_issues_scope: serializeIssuesScope(next) })
  }

  function handleDocsScopeChange(next) {
    scopeTouchedRef.current.docs = true
    docsScopeRef.current = next
    setDocsScope(next)
    if (shouldPersist(scopeReadyRef.current)) persistScope({ github_docs_scope: serializeDocsScope(next) })
  }
```

**Step 2b: One-shot hydration effect** — placed immediately AFTER the `currentTeamId` state declaration (main.jsx:466) so the deps array never evaluates a temporal-dead-zone binding:

```js
  // #1893 one-shot hydration: reconcile the persisted scope against the
  // live org repo list, exactly once per team session. Gated on the pure
  // shouldHydrate predicate (reposLoaded && onboarding && !reposLoadFailed
  // && currentTeamId && not-yet-hydrated) — never seeds the default empty
  // before the GET resolves, never prunes on a failed repos fetch, and
  // NEVER dead-paths on a null team: currentTeamId is STATE (populated by
  // the mount gate / team switcher), so the effect re-fires the moment the
  // team resolves. PER-KEY seeding: a key the user touched pre-hydration is
  // NOT seeded (their choice wins) and is persisted now; a key they did NOT
  // touch is seeded from the persisted server value — never overwritten
  // with the un-seeded default. Touch flags reset after hydration so a team
  // switch re-enables seeding for the new team.
  React.useEffect(() => {
    if (!shouldHydrate({ reposLoaded, onboarding, reposLoadFailed,
        currentTeamId, hydratedTeamId: hydratedTeamIdRef.current })) return
    hydratedTeamIdRef.current = currentTeamId
    if (!scopeTouchedRef.current.issues) {
      setIssuesScope(reconcileIssuesScope(onboarding.github_issues_scope, reposList))
    } else {
      // serialize from the ref mirror — never the effect closure (P2)
      persistScope({ github_issues_scope: serializeIssuesScope(issuesScopeRef.current) })
    }
    if (!scopeTouchedRef.current.docs) {
      setDocsScope(reconcileDocsScope(onboarding.github_docs_scope, reposList))
    } else {
      persistScope({ github_docs_scope: serializeDocsScope(docsScopeRef.current) })
    }
    scopeTouchedRef.current = { issues: false, docs: false }
    scopeReadyRef.current = true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reposLoaded, onboarding, reposList, currentTeamId, reposLoadFailed])
```

**Step 3: Swap the two MemorySources wiring sites** (main.jsx:3598-3599 and main.jsx:4121-4122):

```js
onDocsScopeChange={handleDocsScopeChange}
onIssuesScopeChange={handleIssuesScopeChange}
```
(both sites — wizard + overview)

**Step 4: Switch the job-body constructions to the pure builders** (behavior-identical):
- main.jsx:1167: `const body = buildIssuesJobBody(issuesScope)`
- main.jsx:1204-1210: `const payload = buildDocsJobBody(docsScope, org)`

**Step 5: Track repos-fetch failure + reset stale branches** (region-local additions):
- `loadRepos()` (main.jsx:1044-1053): add `setReposLoadFailed(false)` on success / `setReposLoadFailed(true)` on catch (before `setReposLoaded(true)`). A failed fetch → `reposLoadFailed` → `shouldHydrate` returns false → nothing seeds/prunes/latches; the selectors stay at defaults and the server data stays intact (reload re-attempts).
- The branchLists auto-seed effect (main.jsx:1076-1092): extend the per-repo branch fill with the stale-branch reset — when `branchLists[r]` is loaded and `shouldResetBranch(branches[r], branchLists[r])` is true, reset `branches[r]` to `''` (default). Prevents a persisted branch that no longer exists on GitHub from sticking a blank picker and failing the docs job. Note: the reset and the existing `defaultBranch` fill may race within the same effect run — either outcome (`''` or the API `defaultBranch`) is safe and consistent with design decision 6.

**Step 6: Build to verify.**
Run: `cd website/apps/dashboard && npm run build`
Expected: PASS (vite build, no import/lint errors). The committed `dist/` is regenerated.

**Step 7: Manual clickthrough (documented in the PR body):** connect GitHub → select 2 issues repos + 1 docs repo with a branch → confirm Network tab shows PATCHes with the scope keys → reload → selectors restore → clear to "All repos" → reload → all repos.

## Task 5: Full verification + commit

**Intent:** Prove the whole surface green (server docker lane + node --test + build) and ship through the mandatory commit gate.
**Acceptance:** all commands below pass; `git diff --stat` shows only the 5 touched files (+ dist assets); no unrelated changes.

**Files:**
- Verify: all of the above

**Step 1: Server tests (docker lane).**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_onboarding_endpoints.py -v`
Expected: PASS (all onboarding tests, incl. the 3 new scope tests + extended registration test).

**Step 2: Node tests.**
Run: `cd website/apps/dashboard && node --test src/sourceScope.test.js`
Expected: PASS (19 tests — 13 derivation + 6 gating/reset predicate tests).

**Step 3: Dashboard build + dist.**
Run: `cd website/apps/dashboard && npm run build`
Expected: PASS; `git status --short` shows updated `dist/assets/index-*.js`.

**Step 4: Regression smoke — index endpoints still 400-consistent.**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_index_docs_api.py -k 'scope or invalid' -v`
Expected: PASS (shared validation surface unchanged).

**Step 5: Commit via @commit-workflow** (mandatory gate — pre-flight typecheck/tests + PR + review).
```bash
git add tortoise/hosted_api.py tests/test_onboarding_endpoints.py \
  website/apps/dashboard/src/sourceScope.js website/apps/dashboard/src/sourceScope.test.js \
  website/apps/dashboard/src/main.jsx website/apps/dashboard/dist
# invoke commit-workflow skill per AGENTS.md hard rule
```

## Verification Plan

test-routing (standard tier, domain=code, UX=low): unit server tests (docker lane — `test_onboarding_endpoints.py` is NOT in the 17-file carve-out, per `tests/_embedded.py::TEST_NO_REDIRECT_STEMS`), node --test pure helpers (zero deps), vite build. E2E (`RUN_DASHBOARD_E2E=1`) deferred — no scope-selector e2e exists and the issue's checklist maps the dashboard surface to a component test, which the pure-node suite substitutes (sessionKey.js/captureStatus.js precedent). Manual clickthrough (Task 4 Step 7) covers the effect wiring; failure modes documented in the PR body.

**Runtime prerequisites:** `uv sync` (uv ≥ 0.6.0); FalkorDB up — `docker compose -f ../eldato/operations/memory/docker-compose.yml up -d`; `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`; Node 22 (`node --test`); `website/apps/dashboard/node_modules` present (npm install if missing).

## Acceptance Criteria → O/I/T

| O/I/T | Evidence |
|-------|----------|
| O: selections survive logout/reload | keys registered on all 4 surfaces (Task 1) + persist path (Task 4) + hydration (Task 4) |
| I1: selectors restore previous repos after reload | `reconcileIssuesScope`/`reconcileDocsScope` node tests + one-shot hydration effect (Task 3/4) + manual clickthrough |
| I2: reindex/docs jobs receive restored scope (not org-wide) | `buildIssuesJobBody`/`buildDocsJobBody` wired at main.jsx:1167/1204 + node tests (Task 3/4) |
| T1: 0 data loss — PATCH → GET round-trip | `test_scope_keys_explicit_empty_round_trip` + `test_scope_branch_normalized_to_null` (normalized-then-stored, stable) + instant persist (no debounce) (Task 2/4) |
| T2a: new keys pass state-key registration test | tuple-form `_STATE_KEY_TABLE` + `test_state_keys_registered_parametrized` (Task 1) |
| T2b: dashboard test covers init-from-state | `sourceScope.test.js` via `node --test` (Task 3) |

## #1894 Coordination (concurrent, same files)

| File | #1893 region | #1894 region | Coordination rule |
|------|-------------|--------------|-------------------|
| `tortoise/hosted_api.py` | append 2 keys to both default dicts (1925/9584) + 2 PATCH-model fields (9739) + `_validate_scope_payload` + one call in `patch_onboarding_state` (9802) | same dicts/models (last-indexed-at key) + `_STATE_KEY_TABLE` row | APPEND-ONLY to the dicts/models (new lines, never reorder/rewrite existing entries). **`_STATE_KEY_TABLE` VALUE-TYPE CHANGE (str → tuple) is the one structural edit — land #1893 first, #1894 adds its row in the tuple form.** If #1894 lands first, #1893 converts all rows (mechanical) and #1894's row follows suit. |
| `website/apps/dashboard/src/main.jsx` | scope-state block (~321), wiring lines 3598-3599/4121-4122, job-body lines 1167/1204-1210 | docs switch render (~4530-4780), job cards + polling (~1170-1210 area) | #1893's edits are region-local to the App scope block + 4 single lines. The 1167-1210 area is ADJACENT to #1894's polling edits — #1893 changes ONLY the body-construction line (1167) and payload block (1204-1210), never the poll/status callbacks. Keep both PRs' edits to their own lines; expect a trivial conflict on the shared function bodies only if both touch the same `const` lines. |
| `website/apps/dashboard/dist/` | rebuilt bundle | rebuilt bundle | Both regenerate `dist/` — resolve by rebuilding after the second merge (repo precedent: #1845 merge). |

**Merge order recommendation:** #1893 first (it owns the structural `_STATE_KEY_TABLE` format), #1894 rebases.

## Remaining Design Decisions (for plan-review scrutiny)

1. **Key naming:** `github_issues_scope` / `github_docs_scope` — mirrors the request-contract payload shapes (`repos` list / `{repo, branch}` list) with an explicit scope suffix; underscore names avoid the hyphen-translation table entirely. Rejected: `last_*` prefix (A3 semantics), `github_repo_scope` (ambiguous).
2. **Validation semantics:** PATCH-boundary validation reuses `_validate_repo_scope` + `_is_safe_branch` and raises 400 (consistent with index endpoints) — NOT pydantic `field_validators` (422) and NOT silent pruning. Issues repos are stored strip/dedupe-normalized (via `_validate_repo_scope`); docs branches are NORMALIZED `""`/None → `null` at persist (matching the `index_docs` consumer); `[]` (explicit clear) is stored as `[]`. The normalized form is what GET returns — pinned by `test_scope_branch_normalized_to_null`.
3. **Queue strategy:** ONE shared FIFO promise queue for BOTH keys (deviation from the controller's "per-key queue" wording) — `_update_onboarding_state` RMWs the WHOLE state, so per-key queues would still race cross-key (an issues PATCH's read could miss a concurrent docs PATCH's write). Single queue serializes all scope writes; requirement 9 (no server atomicity rework) honored.
4. **Reconcile behavior at seed:** intersect persisted names with `reposList`; prune stale; pruned-to-empty = all (`[]`). Branch values are reconciled against the live org repo list but NOT against `branchLists` at seed (lazy-loaded, may be absent) — instead, the branch auto-seed effect resets a stale persisted branch to default when the picker HAS loaded options and the branch is gone (`shouldResetBranch`), preventing a blank picker + failing docs job. `""`/`None` branch → default picker option (omitted from the branches map).
5. **Persist timing:** fire-and-forget with `.catch(() => {})`, gated on `shouldPersist(scopeReadyRef.current)` (set only after the first successful hydration; never before the initial GET resolves). No retry/backoff — a failed persist is silent; the next change re-persists the full list. Accepted: a transient PATCH failure means the selection survives only in memory until the next change (network-failure edge, not the REAUTH-incident class the issue targets).
6. **`branchLists` auto-seed nuance (accepted):** if branches load before a persist fires, the serialized docs scope may include the auto-seeded `defaultBranch` value (main.jsx:1077-1092) instead of `""` — harmless: it equals the server's default fallback and reconciles identically.
7. **First-membership pinning (KNOWN LIMITATION, deliberately out of scope):** the onboarding surface — GET/PATCH `/v1/onboarding/state`, `/v1/onboarding/github/repos`, `/v1/onboarding/github/branches` — never threads `?team_id=`; the server resolves `?team_id=, else the FIRST membership` (`_session_user_team`, hosted_api.py:1422). This is PRE-EXISTING for the entire onboarding surface (`github_connected`, `session_recording`, etc. share the anchor) and is deliberately NOT fixed here (threading `team_id` is a cross-cutting auth-resolution change beyond this standard task and collides with #1894). Consequence for #1893: for a multi-team user whose selected team != first membership, the scope selectors read/write the FIRST membership's onboarding_state (the same anchor the rest of the MemorySources panel already uses). The hydration latch keyed on `currentTeamId` re-arms on a team switch, which re-runs hydration against the SAME (first-membership) state — harmless re-seed/re-persist of identical data, and a user's post-hydration choice is preserved (it was already persisted). NOT asserted as per-team; documented so a future `?team_id=` threading change must revisit the touch-flag + latch logic.
8. **Registration-table format ripple:** the tuple-form `_STATE_KEY_TABLE` is a breaking shape change for any concurrent PR adding rows — coordinated with #1894 (see table above).

---

## Plan-Review Fixes (applied 2026-08-29, post-rebase verification)

Two parallel plan verifiers reviewed this plan against the rebased branch (origin/main @ 5c761815). Both converged on one P1 + minor P2s. Fixes folded into the implementation below:

- **[P1] Team-node provisioning in the three new pytest tests.** The `client` fixture overrides `get_current_team` only — it does NOT provision a Team node, and `_write_onboarding_state` is a `MATCH (t:Team {id:$id}) SET ...` that is a SILENT NO-OP without the node (`_get_onboarding_state` returns defaults). As written, all three scope tests passed without any persistence — the PATCH→GET round-trip evidence (O/I/T T1) was hollow. **Fix applied:** each test provisions `test-team-1` via the established `_make_sdk(namespace="registry")._get_registry().query("CREATE (t:Team {id:$id, onboarding_state:$st})", ...)` pattern (mirrors `test_install_probe_round_trip`), and:
  - `test_scope_keys_explicit_empty_round_trip` adds an intermediate GET asserting `["repo-a"]` between the seed PATCH and the clear PATCH (pins the seed phase, not just the clear).
  - `test_scope_branch_normalized_to_null` adds a real GET between the two PATCHes asserting `branch is None`, then re-PATCHes that GET value (pins persist + stability, not just the in-memory response).
  - `test_scope_keys_invalid_400` seeds a valid scope first, then asserts the 400s leave the valid seed intact via GET (pins "nothing stored", non-vacuous).
- **[P2] Docs-scope dedupe at the PATCH boundary.** `_validate_scope_payload`'s docs loop dedupes `repo` entries via a `seen` set (mirrors `_validate_repo_scope`) so `[{a},{a}]` can never persist duplicated — theoretical (client serializes unique repos + `reconcileDocsScope` dedupes), cheap to make robust.
- **[P2, cosmetic] New pytest tests live at module level** (like `test_install_probe_round_trip`), not inside `TestOnboardingState` — pytest collects both fine; the `-k 'scope'` selector covers them.
- **[P2] Node-suite growth (test-review cycle-1):** the pure-node suite grew from 16 → 19 tests (13 derivation + 6 gating) with mixed/corrupt persisted-shape reconcile cases, null-branch docs entries, serialize/build guard cases, and `shouldResetBranch` empty-options coverage. Acceptance counts updated accordingly (Task 3 Step 4 / Task 5 Step 2).
