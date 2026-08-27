<!-- research-path: issue #1719 problem-converge comments (scoping correction + problem-verify cycles 1-3); no epic research doc — standalone P0 -->

# #1719 — /v1/session/login 500 → auth redirect broken: mint-path shape gate + honest degradation

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** `/v1/session/login` returns 200 session JSON for valid keys and honest 401/403/429/502/503 (never a raw 500) for every other input class, on every key shape — including legacy pre-#1511 `created_by` values — and the two stacked incidents (mint-path PostgREST failure + FalkorDB Cloud down) are each fixed or surfaced honestly.

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** The RC1 500 is the mint-path `team_memberships` PostgREST read failing on a non-UUID `created_by` literal (`api_keys.created_by` is TEXT 0007; `team_memberships.user_id` is uuid 0003; PostgREST 22P02 → `RuntimeError` → unwrapped → #1591 global handler 500). **Hypothesis confidence: ~92-95% (upgraded from ~80% by the parallel-review Devil's Advocate — provisioning-path proof):** every Supabase-mode `agent_signup` key mints `created_by = "anon-<12hex>"` via the 0010 RPC (`v_created_by := COALESCE(p_user_id::text, p_identity)`); every `register`/email key mints `created_by = "reg-*"`; the registry-flip path mints `"api"`. The reporter's key IS an agent key (team `agent-877b2b`, 26-hex team_id = the `uuid4().hex[:26]` agent format). The observed global-handler 500 body proves a non-NULL `created_by` (else the existing `if not key_created_by` guard would 403 without querying) → 22P02 is the only hypothesis predicting it; the junk-key-401 asymmetry (lookup_hash read carries no uuid cast) excludes table-level causes. **The non-UUID class is CURRENT, not legacy — every anon/reg key minted today.** The endpoint's shape-decision tree (hosted_api.py:2745-2760) already classifies these shapes correctly but is unreachable because `mint_target_user_for_key` queries before gating. Fix = shape-gate **inside** `mint_target_user_for_key` (non-UUID → None without querying) so the existing tree runs; mirror the same PostgREST type rejection in `FakeControlPlane` (default-on UUID filter fidelity) so CI catches the whole class; wrap the residual mint-path control-plane failure class in a 503 boundary map; reorder the rate-limit charge so server faults don't consume the 5/hr bucket; give the client honest 5xx copy; and gate deploys on DB health (RC3 owner track per #1381).

### Pattern Research

Skipped — plan touches zero third-party dependencies. All changes are in-repo (supabase_control.py, hosted_api.py, tests/fake_control_plane.py, website/signup.html, .github/workflows/deploy-hosted.yml). Prior research is the issue's own problem-converge record (scoping correction 2026-08-25 + problem-verify cycles 1-3), which already refuted the naive "FalkorDB down → login 500" causal link and pinned the escape path to the mint path via the global-handler body discriminator (`{"detail":"Internal server error"}` vs the `_get_current_team_supabase` catch-all `"Auth error"`).

### Integration Surface Map

| Surface | Seam | Failure mode | Test layer |
|---|---|---|---|
| `mint_target_user_for_key` (supabase_control.py:662) | helper — single unsanitized `user_id eq` literal | non-UUID `created_by` → PostgREST 22P02 → RuntimeError → 500 | unit (fake) |
| `membership_for_user_team` / `user_memberships` (supabase_control.py:649/634) | query helper — user_id from JWT (real UUID) elsewhere | same class via future call sites | unit (fake fidelity) |
| `/v1/session/login` (hosted_api.py:2667+) | endpoint — mint path + backstop | residual control-plane outage on mint reads → 500 | endpoint (TestClient + fake) |
| `/v1/claim`, `/v1/claim/email`, `/v1/claim/status` (hosted_api.py:~7138/7211/7286) | claim funnel — `is_anon_team` / `membership_for_user_team` unwrapped | control-plane outage → 500 (RC1-b blast radius) | endpoint (test_claim_endpoints.py) |
| Rate limiter (hosted_api.py:1938/2716) | per-IP 5/hr bucket, charge pre-resolution | server fault burns bucket → 429 masks incident ~1h (RC2) | endpoint |
| `signup.html` API-key modal (website/signup.html:1242) | client | unknown non-ok (500) → misleading "Invalid API key." (RC4) | manual/e2e (welcome-e2e-monitor) |
| `deploy-hosted.yml` (no health gate; publish-selfhost.yml:70-84 has one) | CI/CD | ships app over dead DB; no post-deploy signal (RC3) | workflow (manual dispatch) |
| `FakeControlPlane` (tests/fake_control_plane.py) | test seam | string-compare has no uuid type fidelity → CI green while prod 500s | unit (fidelity tests) |

### Journey Test Map

```markdown
### Journey: Owner signs in with a valid dashboard-minted key (the O/I)
1. **Step:** Paste tt_ key → /v1/session/login → **Acceptance:** 200 session JSON (access_token + expires_at) → **Test:** test_dashboard_minted_key_succeeds / test_member_minted_key_mints_member_session_no_escalation
2. **Step:** /auth page stores session, redirects → **Acceptance:** lands on app.premiselabs.co → **Test:** welcome-e2e-monitor (live)

### Journey: Legacy anon-team key (pre-#1511 identity creator)
1. **Step:** Paste tt_ key with created_by="anon-<12hex>" on an unclaimed team → **Acceptance:** 403 ANON_TEAM_NO_OWNER (NOT 500), claim funnel reachable → **Test:** test_identity_key_on_anon_team_403_anon_team_no_owner
2. **Step:** Claim card → /v1/claim → **Acceptance:** claim succeeds (funnel works, no 500) → **Test:** test_claim_endpoints.py suite

### Journey: Legacy registry-flip key (created_by="api") or reg- identity on claimed team
1. **Step:** Paste tt_ key → **Acceptance:** 403 KEY_NOT_USER_MINTED with the mint-a-new-key copy (NOT 500, NOT "Invalid API key.") → **Test:** test_api_created_by_key_403_key_not_user_minted / test_identity_key_on_claimed_team_403_key_not_user_minted

### Journey: Control-plane outage mid-login
1. **Step:** Paste valid tt_ key → **Acceptance:** 503 control_plane_unavailable (NOT 500); client shows "temporarily unavailable"; the attempt does NOT consume the 5/hr bucket → **Test:** new endpoint outage tests + limiter no-charge tests
```

### Failure Modes

- **Non-UUID created_by ("api"/"anon-*"/"reg-*")** → **Expected:** guard returns None pre-query → existing tree → 403 ANON_TEAM_NO_OWNER / KEY_NOT_USER_MINTED → **Test:** existing 403 tests (now exercising the guard), helpers unit tests
- **UUID creator, not an active member** → **Expected:** query returns [] → None → 403 KEY_NOT_USER_MINTED (unchanged, guard must NOT block UUID-shaped values) → **Test:** test_removed_creator_403_key_not_user_minted
- **Mint-path control-plane RuntimeError (any cause)** → **Expected:** 503 control_plane_unavailable, honest copy → **Test:** new ErrorControlPlane-on-mint-path endpoint test
- **Junk key during outage** → **Expected:** 401 (resolution works — api_keys read unaffected by mint path); charges the bucket (brute-force protection preserved) → **Test:** test_invalid_key_401 + new charge-point test
- **Rate bucket exhausted by server faults** → **Expected:** impossible (5xx no longer charges) → **Test:** new limiter test
- **Future unsanitized user_id-eq call site** → **Expected:** FakeControlPlane raises (mirrors 22P02) → CI fails → **Test:** fidelity unit tests + full suite
- **DB down + deploy of a fix** → **Expected:** deploy-hosted health gate blocks UNLESS `skip-db-health-gate` bypass is set (incident-fix deploys) → **Test:** workflow manual dispatch

**Tech Stack:** Python 3.12 (FastAPI, pytest, httpx), JS (vanilla signup.html), GitHub Actions, Fly.io, Supabase PostgREST, FalkorDB Cloud.

---

## Decision (converged approach): **A-core + B's 503 boundary map + RC2 + RC4 + RC3 track**

The principled merge is **Approach A (shape-gate at the mint boundary)** as the core fix, **plus** four bounded companions — **B's residual 503 boundary map** (without B's typed-query registry and fail-open membership carve-out), **RC2** (rate-limit charge reorder, 401-preserving), **RC4** (honest client copy), and **RC3** (owner track + deploy health gate). Rationale and rejections below.

### Why this is the best outcome

1. **Delivers the O/I directly — qualified per second-model P1.** A **UUID-created_by** key → guard passes → membership query succeeds → mint → 200. **Legacy non-UUID keys** ("api"/"anon-*"/"reg-*") → 403 with mint-a-new-key copy **by design** — their resolution path is OAuth or a freshly-minted dashboard key, stated explicitly so a post-fix 403 on an old key is not misread as a regression. **Hypothesis caveat (second-model P2):** this is the 22P02-specific fix (named as such in Task 2); it is fail-safe to ship regardless (a non-UUID can never be a valid mint target), but if Task 1's prod log refutes 22P02, the operative response is Task 1b (mint-path remediation) + the Task 4 503 map — the shape-gate + fidelity machinery is then CI-lock-only, not the fix.
2. **Fixes the whole 22P02 class, not one line.** The guard sits at the mint boundary, and the `FakeControlPlane` default-on UUID fidelity is the regression lock: a future unsanitized call site now fails CI with the same PostgREST 400 shape prod would throw, closing the "CI green while prod 500s" gap that let this ship. **Correction (codebase-review P1-1):** the plan's earlier "grep-verified only unsanitized literal" claim was FALSE — `membership_role`/`set_membership` (supabase_control.py:1549/1563) iterate `for col in ("user_id", "identity")` with the uuid-typed `user_id` filter FIRST, so an identity anchor ("anon-abc") 22P02s before the identity fallback → live 500 at remove_member/change_member_role (hosted_api.py:5786/5791/5830/5835). This same-class bug is folded into Task 2's scope (identity-aware membership helpers).
3. **Honest errors on every class.** Junk key → 401; legacy shape → 403 with the *right* error_code; mint-path outage → 503 `control_plane_unavailable`; limiter no longer masks incidents; client copy distinguishes 4xx (key problem) from 5xx (server problem).
4. **Each companion is small, independent, and failure-mode-covering.** No new schema-coupled metadata (the B registry would itself be a drift hazard — the same bug class as #1001/#1003), no fail-open on core auth reads (B's membership carve-out would 403 legit users during an outage with a permanent-sounding error — worse than 500).

### Rejected alternatives — when they WOULD have been better

- **B — Typed Query Seam + Fail-Soft Membership (full).** Rejected: a hand-maintained column-type registry is schema-coupled metadata that drifts (the #1001 class); fail-open on `user_memberships`/`membership_for_user_team` during an outage converts a 500 (retryable) into a 403 ("No team membership" / "this key can't be used to sign in" — permanent-sounding, client-cached decisions); contract carve-out needs review sign-off for a one-literal class. **This WOULD have been better** if there were multiple unsanitized call sites today, if the 22P02 hypothesis were refuted in favor of an unknown shape-specific failure needing systematic diagnosis, or if a schema-versioned type registry already existed. The 503 boundary map retains B's degradation value without its machinery.
- **C — Layered Reliability as the primary.** Rejected as the *primary*: its core fix ("the same one-line non-UUID guard as A") *is* A — C alone would leave the guard conditional on prod-log confirmation and wouldn't make CI catch the class (no fake fidelity). **This WOULD have been better** if the guard were confirmed wrong by prod logs (then C's operational-only surface would be the correct minimal response), or if the 22P02 hypothesis could not be verified and an operational-first posture were required. Its RC2/RC3/RC4 pieces are adopted wholesale.
- **Guard in `membership_for_user_team` instead of `mint_target_user_for_key`.** Rejected: silently returning None for a type-invalid filter would mask future caller bugs (fail-open on a core auth read) and duplicates the shape decision the endpoint tree already owns. **This WOULD have been better** if multiple callers were expected to pass arbitrary user_ids; today exactly one does, and the fake fidelity makes the helper's fail-closed contract (raise on type error) enforceable in CI.
- **No-op / owner-track-only (fix only FalkorDB).** Rejected — this was the issue's original causal link, already refuted in the scoping correction: the login path shares zero code with the graph plane, and the observed body discriminator proves the mint path. **This WOULD have been better** if the login 500 had actually been FalkorDB-caused (it is not — RC3 is a co-scoped, separate incident).

---

## Task 1: Diagnostic — name the failing query in prod

**Intent:** Confirm or refute the 22P02 hypothesis with production evidence before/while shipping the fix (the fix is safe regardless — a non-UUID `created_by` can never be a valid mint target — but the log names the exact PostgREST error and any co-failing shape).
**Acceptance:** Either the prod log shows `22P02` (or an equivalent PostgREST 400) on the mint-path `team_memberships` query, or the hypothesis is refuted with a named alternative; findings recorded on issue #1719.
**Files:**
- Modify: none (ops/diagnostic only)
- Test: n/a

**Step 1: Pull the unhandled-exception traceback.**
Run: `fly logs -a tortoise-y4mjjq | grep -B2 -A40 "unhandled exception: POST /v1/session/login"` (the #1591 handler logs the full traceback via `tortoise.api` logger; the PostgREST error body is inside the `RuntimeError` string — e.g. `"Supabase control-plane query failed (team_memberships): HTTP 400"` with the PostgREST detail, `22P02 invalid input syntax for type uuid`).

**Step 2: Fresh-IP real-key probe (only if the repro egress is not 429-locked).**
Run: `curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://api.premiselabs.co/v1/session/login -H 'Content-Type: application/json' -d '{"api_key":"<real tt_ key>"}'` — from a fresh egress (or after clearing the session bucket) → expect 500 pre-fix / 200 post-fix.

**Step 3: Falsification matrix.**
- If 500 persists after FalkorDB restore → RC1 confirmed (predicted).
- If junk tt_ key → 401 while real key → 500 → api_keys healthy, mint-path confirmed (already observed).
- If the traceback names a non-22P02 failure (e.g. PGRST002 schema-cache, column grant gap) → record it; the Task 4 503 map keeps the endpoint honest while that root cause is repaired per the issue's remediation track.

**Step 4: Record findings** as a comment on issue #1719 (evidence + named error). **BLOCKING for closing #1719 (Devil's-Advocate P1-3):** Tasks 2-6 may ship without this step, but #1719 may NOT be closed until one of: (a) the log names 22P02 (or equivalent PostgREST 400 on the mint-path query) — the shape-gate + fidelity are then the confirmed fix; (b) the log names an alternative and Task 1b's repair lands and a real-key login returns 200; or (c) the log is unavailable (traceback rotated past retention) but a fresh-IP real-key repro regenerates the 500 traceback pre-deploy (Step 2's mechanism, run before Task 8's deploy removes the repro surface). Without a named traceback, the 5-8% non-22P02 residue could leave the mint path at honest-503 forever and the O/I silently unmet.

**Task 1b (contingency — restores the problem-verify cycle-3 P1-2 remediation track): mint-path repair for non-22P02 causes.**
**Intent:** The 22P02 non-UUID hypothesis is ~80% attribution (problem diamond). If Task 1's prod-log traceback names a DIFFERENT mint-path cause (PGRST002 schema-cache staleness, column-grant/RLS gap, column drift), the endpoint must not degrade to a permanent 503 — the real control-plane failure is repaired. This task is the explicit contingency the solution-converge collapsed into the non-blocking Task 1 diagnostic + 503 map.
**Acceptance:** If Task 1 names 22P02 → this task is a no-op (shape-gate + fidelity are the fix). If Task 1 names PGRST002 / grant / RLS / drift → the named cause is repaired and a real-key mint-path login returns 200 (NOT 503) before #1719 is closed.
**Files:** `tortoise/supabase_control.py` / Supabase project (per cause); `tests/test_session_login.py`
**Step 1 (decision gate):** after Task 1's log pull, classify the traceback: 22P02 (→ skip, shape-gate covers) vs PGRST002 (→ `NOTIFY pgrst, 'reload schema'` or schema re-exposure per Supabase docs) vs permission-denied-on-column (→ `aclexplode` team_memberships; repair the 0009 column-grant gap for service_role) vs column drift (→ migration).
**Step 2:** execute the named repair; add a regression test pinning the named failure class → 200/503 (never global-handler 500).
**Step 3:** real-key mint-path login from a fresh IP → 200; close #1719 only after this or the 22P02 skip path.

## Task 2: Shape-gate in `mint_target_user_for_key` (the 22P02-specific fix — named per second-model P2)

**Intent:** Prevent the mint-path query from ever receiving a non-UUID `user_id` literal — the 22P02 class — so the endpoint's existing shape-decision tree becomes reachable and legacy keys get their correct honest 403.
**Acceptance:** `mint_target_user_for_key(cp, creator, team)` returns None *without querying* for any non-UUID creator ("api", "anon-*", "reg-*", NULL, junk); queries normally for UUID-shaped creators (active member → the UUID; non-member → None). `test_session_login_helpers.py` mint tests pass with realistic UUIDs; existing endpoint 403 tests (`test_api_created_by_key_403_*`, `test_identity_key_on_anon_team_403_*`, `test_identity_key_on_claimed_team_403_*`, `test_null_created_by_key_403_*`) still return 403 with the correct error_code (no 500).
**Files:**
- Modify: `tortoise/supabase_control.py:662-678` (mint_target_user_for_key), module-level `_is_uuid` helper
- Modify: `tortoise/hosted_api.py:2745-2748` (reuse `_is_uuid` — single source of truth, no regex drift)
- Test: `tests/test_session_login_helpers.py:19-52`, `tests/test_session_login.py`

**Step 1: Write the failing unit tests** (test_session_login_helpers.py — migrate `_cp_with_members` fixtures to real UUID constants):
- `test_mint_target_returns_active_member_uuid` → seeds `user_id=<uuid>` membership, asserts the UUID is returned.
- `test_mint_target_returns_none_without_query_for_non_uuid` → `mint_target_user_for_key(cp, "api"/"anon-abc"/"reg-xyz", "t1")` is None **and** `cp.query_count == 0` (guard short-circuits before any query).
- Keep/adapt: NULL → None; UUID non-member → None (query ran); UUID that left the team → None.

**Step 2: Run to verify they fail.**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_session_login_helpers.py -v` (note: the "no query" assertion fails pre-guard because the current helper queries).

**Step 3: Implement the guard.**
In `tortoise/supabase_control.py`: add a module-level `_is_uuid(value)` helper using Python's parser — `try: uuid.UUID(value); return True except (ValueError, TypeError, AttributeError): return False` — NOT a strict hyphenated regex. **Regex-vs-Postgres mismatch fix (solution-verify P2-B):** PostgreSQL's uuid parser accepts 32-hex without hyphens and braced `{…}` forms that a strict `[0-9a-f]{8}-…` regex rejects; pre-fix such a `created_by` cast fine, queried, and minted 200 — a stricter guard would regress them to ANON_TEAM_NO_OWNER/KEY_NOT_USER_MINTED. Python's `uuid.UUID` matches PG's acceptance (no-hyphen + braces + case). In `mint_target_user_for_key`, before `membership_for_user_team`: `if not key_created_by or not _is_uuid(key_created_by): return None`. Update the docstring: non-UUID creators never mint (user_id is uuid; no real user exists for "api"/identity strings) — the endpoint tree classifies them (ANON_TEAM_NO_OWNER / KEY_NOT_USER_MINTED).

**Step 4: Reuse the helper in the endpoint tree** (hosted_api.py:2745): replace the inline `re.fullmatch(...)` with the imported `_is_uuid(created_by or "")` — one definition, no drift. Note: hosted_api.py imports supabase_control function-locally (61/61 imports are function-local) — the import goes inside session_login's existing `from tortoise.supabase_control import (...)` block (2686-2689) and the inline regex assignment at 2746 is DELETED, not shadowed (codebase-review P3-3).

**Step 4b (NEW — codebase-review P1-1): same-class fix in the identity-aware membership helpers.** `membership_role` (supabase_control.py:1549-1557) and `set_membership` (1562-1568) iterate `for col in ("user_id", "identity")` — the uuid-typed `user_id` filter runs FIRST, so an identity anchor ("anon-abc", the 0010 `identity` shape) 22P02s before the `identity` fallback ever matches → live 500 at remove_member/change_member_role (hosted_api.py:5786/5791/5830/5835) today. Fix: branch on value shape — **keep the two-column loop form for UUID values (["user_id", "identity"] — lossless: a duplicate identity-anchored row holding a UUID string still matches via identity; do NOT narrow to user_id-only), and use the identity-only loop (["identity"]) for non-UUID values** (a non-UUID can never match a uuid column, and querying it would 22P02). Add `test_membership_role_identity_anchor_ok` (supabase_control.py test:1275 class) asserting `membership_role(cp, team, "anon-abc")` returns the role (identity match) and `remove_member/change_member_role` on an anon member return 200 (no 500).

**Step 5: Run the helpers + endpoint suites.**
Run: `... uv run pytest tests/test_session_login_helpers.py tests/test_session_login.py tests/test_supabase_control.py -v` — all mint-path 403 tests green, no 500s; membership_role/set_membership identity anchors pass.

## Task 3: `FakeControlPlane` UUID filter fidelity (the regression lock)

**Intent:** Make CI catch the 22P02 class — the fake currently string-compares, so the exact prod failure is invisible ("CI green while prod 500s"). Mirror PostgREST: a non-UUID filter value on a known uuid column raises the same `RuntimeError("... HTTP 400")` the real seam produces.
**Acceptance:** `FakeControlPlane.query("team_memberships", filters=[("user_id","eq","api")])` raises RuntimeError by default; valid UUID filters behave normally; stored-row inspection is NOT type-checked (only filter values); full docker-lane suite green after migrating non-UUID test constants.
**STATUS: DONE (2026-08-27)** — `uuid_fidelity` default-on check at top of `query()` (GET+PATCH+DELETE), `UUID_FILTER_COLUMNS` registry, `_assert_uuid_fidelity` mirroring PostgREST 22P02. Migrated non-UUID user_id fixtures across 13 test files (auth_flip, claim_endpoints, dashboard_login [truncated f"user-{hex8}" pattern], export_delete, import_endpoint, agent_signup_idempotency, agent_signup, email_signup, hosted_api, invites_email_http, invites_http, oauth_mcp, pack_state, session_key_http, supabase_control, writer_inventory, abuse_integration). 919 tests green across all 24 fake-importing files; residual full-suite hangs are the pre-existing embedded-store flake class (isolated passes confirmed on main).
**Files:**
- Modify: `tests/fake_control_plane.py` (query() filter validation + `_UUID_FILTER_COLUMNS` registry)
- Modify: `tests/test_supabase_control.py` (`TestSessionHelpers` non-UUID constants → real UUIDs)
- Modify: `tests/test_claim_endpoints.py` (`_jwt` default + affected assertions → real UUIDs)
- Test: new fidelity tests in `tests/test_supabase_control.py` or `tests/test_fake_control_plane.py`

**Step 1: Write the failing fidelity tests:**
- `test_non_uuid_user_id_eq_filter_raises` → `fake.query("team_memberships", filters=[("user_id", "eq", "api")])` raises RuntimeError matching `HTTP 400` (PostgREST 22P02 surface).
- `test_uuid_user_id_eq_filter_ok` → UUID filter returns rows.
- `test_user_id_is_null_filter_ok` → `("user_id","is",None)` unaffected (is.null has no cast).

**Step 2: Implement fidelity (default ON).**
Add module-level `_UUID_FILTER_COLUMNS = {("team_memberships", "user_id")}` (extendable registry — mirrors the existing `missing_columns` seam but is a default-on mirroring property, not an opt-in drift simulation). **Placement (solution-verify P2):** check at the TOP of `query()`, BEFORE method dispatch — GET filters are built in the filter loop, but PATCH/DELETE filters flow through the separate `_matches()` helper (fake_control_plane.py:298-311, 377-395) and 22P02 in prod identically; a check placed only after the GET loop is silently incomplete. For each `("col","eq"/"neq",value)` filter on a registered uuid column, raise `RuntimeError(f"Supabase control-plane query failed ({table}): HTTP 400")` when the value is a non-None non-UUID string (mirror `_is_uuid`). Keep a constructor escape hatch `uuid_fidelity: bool = True` for suites deliberately testing pre-#1511 non-UUID data (document it).

**Step 3: Run the full docker lane and triage breakages.**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/ -v` — expected migrations (broader than the two named files — solution-verify P2 + codebase-review P1-1): `test_supabase_control.py` TestSessionHelpers (`user_id="user-1"` → UUID const, since real JWT subjects are UUIDs) AND the invitation/member-lookup suites at supabase_control.py:922 (invitation_accept), 1549/1563 (member lookup), plus lines 560-780 and the claim/RPC rows at 1656-1815 (non-UUID ids like "user-2"/"owner-1" flow into `user_id eq` filters — all test artifacts, JWT subjects are UUIDs in prod); **`test_supabase_control.py:1275` `test_membership_role_and_set_membership` — nuanced triage (codebase-review cycle-2 P1):** the test seeds BOTH a `user_id: "u1"` row (asserted via `membership_role(fake, "team-free-001", "u1") == "owner"`) AND identity-anchor rows (`"anon-abc"`/`"ghost"`). Under Task 2 Step 4b: (a) the **`user_id: "u1"` fixture and its "u1" argument MUST migrate to a real UUID constant** — a "u1" literal in the user_id column is prod-impossible (would 22P02 on INSERT; user_id is uuid) and `_is_uuid("u1")` is False → identity-only query → None → assertion fails; migrating it is exactly the "real JWT subjects are UUIDs" rationale. (b) The **identity-anchor rows and their "anon-abc"/"ghost" assertions stay NON-UUID** so the helper's identity path stays exercised (that is the P1-1 fix's whole point). Blanket "don't migrate" would forbid the only correct fix and invite a masking workaround; blanket "migrate everything" would delete the identity-path coverage. Spell out the split in the test edit. `test_claim_endpoints.py` `_jwt("user-a")` + the `f"user-{uuid.uuid4().hex[:8]}"` literals at 568/602 → real UUIDs. **Any breakage that is a real unsanitized call site is a bug the fidelity caught — fix it as such (guard or correct value), never by disabling fidelity.**

**Step 4: Re-run to green.** All suites pass with fidelity ON.

## Task 4: 503 boundary map for the mint path + claim funnel

**Intent:** Degrade honestly when the mint-path control-plane reads fail for reasons other than shape (outage, schema-cache, column grants) — 503 `control_plane_unavailable`, never a raw 500 → client "Invalid API key." (RC1-b blast radius: the claim funnel, the anon-key escape hatch, shares the unwrapped reads).
**Acceptance:** `/v1/session/login` mint-path RuntimeError → 503 `{"detail": {"error_code": "control_plane_unavailable", "message": "Sign-in is temporarily unavailable — try again in a moment."}}`; `/v1/claim`, `/v1/claim/email`, `/v1/claim/status` outage → 503 with the SAME copy string; resolution failures inside `_get_current_team_supabase` are UNCHANGED (documented fail-closed "Auth error", out of scope — noted as follow-up); all existing 200/401/403/409/429 tests stay green.
**Files:**
- Modify: `tortoise/hosted_api.py` session_login mint section (~2739-2805) + claim_team (~7138), claim_email (~7211), claim_status (~7286-7290)
- Test: `tests/test_session_login.py`, `tests/test_claim_endpoints.py`

**Step 1: Write the failing endpoint tests:**
- `test_mint_path_control_plane_outage_503` → monkeypatch `sc.membership_for_user_team` (and/or seed a raising control plane for the mint leg only) → 503 with `error_code == "control_plane_unavailable"` (never the global-handler 500 body).
- `test_claim_status_outage_503_or_fail_closed` → claim_status's `is_anon_team` raises → 503 (never 500; never `{"claimable": true}`).
- Keep pinned: `test_invalid_key_401` (resolve path — unaffected), all 403 shape tests.

**Step 2: Implement the boundary map.**
Wrap the mint-path sequence in session_login (`mint_target_user_for_key`, `is_anon_team`, post-verify `membership_for_user_team` backstop) in `except RuntimeError: raise HTTPException(503, detail={"error_code": "control_plane_unavailable", "message": "Sign-in is temporarily unavailable — try again in a moment."})` (mirror the GoTrue 502/503 mapping style already at 2810-2820). Apply the same 3-line wrap to claim_team/claim_email `is_anon_team` and claim_status's `is_anon_team` + `membership_for_user_team` (claim_status keeps its resolve_api_key fail-closed `{"claimable": false}` behavior — unchanged). **Scope reconciliation (solution-verify P1):** the no-500 acceptance criterion is scoped to the mint-path + claim-funnel reads this Task wraps; the resolve-leg catch-all (`_get_current_team_supabase` → 500 "Auth error", hosted_api.py:1387) is a documented follow-up and is OUTSIDE this criterion's promise. During this incident (api_keys readable, team_memberships failing) the resolve leg is healthy, so the criterion holds in practice — but the acceptance wording (Criterion 4) is narrowed to "no *unmapped global-handler* 500" on the mint/claim surfaces, and the client `>=500` copy (Task 6) covers the residual resolve-leg case honestly.

**Step 3: Run the endpoint suites** (test_session_login.py, test_claim_endpoints.py, test_agent_signup.py, test_email_signup.py, test_writer_inventory.py) — green.

## Task 5: RC2 — rate-limit charge reorder (server faults must not mask)

**Intent:** A legitimate user must not be locked out of login for ~1h by the server's own fault, and 429s must not mask an ongoing incident (the repro egress was 429-locked during diagnosis). Preserve brute-force protection: 401 invalid-key attempts still charge.
**Acceptance:** Session-login bucket charges on 200/401/403 (server decisions — 403s still cost control-plane reads per attempt and ANON_TEAM_NO_OWNER is a claimability oracle; charging caps leaked-key enumeration at 5/hr); 5xx outcomes (503 mint-path, 502 GoTrue, etc.) do not consume budget; 6th valid attempt in an hour still 429 (`test_rate_limited_429` stays green); junk-key 401s and 403s still consume (5×-401→429 and 5×-403→429 tests); non-`tt_` junk strings (prefix-gate 401, pre-bucket) charge under the wrapped design too — single code path, no double-charge. **Client-path note (second-model P2):** an anon-key owner who retries 5× hits 429 before seeing the ANON_TEAM_NO_OWNER claim-navigation signal — the claim funnel is on `/v1/claim` (separate bucket), so claiming is NOT blocked; the client surfaces the first 403's claim copy (signup.html ANON_TEAM_NO_OWNER branch redirects to `?claim=1` immediately on the FIRST attempt), so the 429 only appears if the user ignores the claim redirect. Accepted + documented; do not exempt ANON_TEAM_NO_OWNER from charging (it still costs control-plane reads).
**Files:**
- Modify: `tortoise/hosted_api.py` `_check_ip_bucket_rate_limit` (~1938, add `defer_charge` param) + session_login (~2716-2722, charge at resolution / on 401)
- Test: `tests/test_session_login.py`

**Step 1: Write failing tests:**
- `test_503_does_not_consume_bucket` → force mint-path 503, then a valid-key login succeeds despite a prior 503 (bucket not charged).
- `test_invalid_key_401_consumes_bucket` → 5 junk 401s → 6th attempt 429.
- Existing `test_rate_limited_429` must still pass (5 valid 200s → 429 on 6th).

**Step 2: Implement.**
In `_check_ip_bucket_rate_limit`, add `defer_charge: bool = False` — when True, prune + check (429 when full) but skip `bucket.append(now)`; add a small `_charge_ip_bucket(buckets, lock, key)` that re-prunes and appends (re-applying `_normalize_mapped_ipv6` on the key — the check normalizes inside; charging with the raw key would split dual-stack buckets). **Canonical control flow (PINNED — codebase-review P1-2: NOT "pick ONE"; single mechanism, no enumerated-raise-site drift):** (1) body parse → (2) prefix-gate `tt_` 401 (charged — see below) → (3) `_check_ip_bucket_rate_limit(defer_charge=True)` (prune+429-check only, no charge) → (4) resolution → (5) mint section inside the body-wide try/except (Task 4's RuntimeError→503 map) → (6) `return session` → charge once. **Charge mechanism: a single body-wide `try/except HTTPException` wraps the endpoint — on `status in (401, 403)` charge once and re-raise; 429/5xx pass through uncharged. The prefix-gate 401 is covered by the same wrap** (it raises inside the try), so no separate pre-bucket charge call and no double-charge; the limiter stays below the prefix gate (its 429 still fires for tt_-shaped attempts — the non-tt_ junk class 401s before the limiter but IS charged by the wrap, tightening the currently-unbounded non-tt_ 401 noise). `_charge_ip_bucket` MUST replicate `_check_ip_bucket_rate_limit`'s `RATE_LIMIT_DISABLED=1` and missing-`request.client` early-returns (test env runs with the limiter disabled; asymmetric check-vs-charge would corrupt the bucket store under tests). In session_login: pass `defer_charge=True`; charge **immediately before `return session`** (final 200) AND when the terminal outcome is 401/403 (via the wrap); do NOT charge on 5xx (502/503/500) or 429 paths. **Charge-point wording fix (solution-verify P1):** the charge must NOT happen "after `_get_current_team_supabase` returns a team" — that point precedes the dashboard gate, the mint tree, GoTrue mint, and the TOCTOU backstop, so a mint-path 503 would fire after the charge and re-break the no-masking invariant. Pin to the terminal outcome only: final 200 (after the mint backstop, before/after audit) and 401/403 HTTPException exits. 403 charging is deliberate (solution-verify P2-A): 403s are server *decisions* (ANON_TEAM_NO_OWNER / KEY_NOT_USER_MINTED / dashboard_login_disabled / suspended) that still cost control-plane reads per attempt — charging them caps a leaked legacy-key probe at 5/hr without reintroducing incident masking (403 is not a server fault). Document the accepted tradeoff: a 401/403-only attacker is limited to 5/hr; server-fault retries are unlimited but fail fast server-side. Add `test_legacy_key_403_does_not_bypass_bucket`: 5× ANON_TEAM_NO_OWNER probes → 6th attempt 429 (403s charge); and `test_503_does_not_consume_bucket` per Step 1.

**Step 3: Run test_session_login.py + test_claim_endpoints.py (shared helper untouched for other callers — regression check) — green.**

## Task 6: RC4 — honest client copy in signup.html

**Intent:** The modal must never tell a user "Invalid API key." for a server-side failure (that actively misdirected the reporter). 5xx is "temporarily unavailable" — matching the existing 502/503 copy. **UX-review P2-1/P2-2 scope (dashboard claim card + bootstrap):** the dashboard (app.premiselabs.co) is the destination of the whole journey; its claim-card error handler renders dict-detail bodies as raw JSON (`JSON.stringify(b.detail)`) and its bootstrap/mint catch shows raw server strings. Same 5xx honesty applies there — see Step 3.
**Acceptance:** Any 500 response from /v1/session/login renders the unavailable copy; 401 (and other 4xx without a recognized error_code) still render "Invalid API key."; the dashboard claim card renders `b.detail.message` (not raw JSON) for 503; dashboard bootstrap/mint 5xx renders the unavailable copy. **Copy-string unification (UX-review P3-1):** ONE string everywhere — "Sign-in is temporarily unavailable — try again in a moment." (Task 4 acceptance + Task 6 + signup.html:1211 must match).
**Files:**
- Modify: `website/signup.html:1210-1218` (the 502/503 branch) + `:1242` (default)

**Step 1: Edit the copy branch.** Change the existing `if (resp.status === 502 || resp.status === 503)` to `if (resp.status >= 500)` so the 500 case (pre-fix clients / residual unmapped server faults) reuses "Sign-in is temporarily unavailable — try again in a moment." The default branch (4xx, unknown error_code) keeps "Invalid API key.".

**Step 2: Verify** the deployed asset md5 matches local after deploy (the issue's own check: `signup.html` md5 comparison) — **after `deploy-pages.yml` completes** (the website deploys via Cloudflare Pages, NOT deploy-hosted.yml — codebase-review P3-2; deploy-hosted only ships `tortoise/**`). Confirm the modal copy manually against a stubbed 500 (welcome-e2e harness or live devtools).

**Step 3 (UX-review P2-1/P2-2): dashboard claim-card + bootstrap 5xx copy (website/apps/dashboard/src/main.jsx).** (a) In both claim handlers (`performClaim` ~886-898, `claimEmailPassword` ~1282-1291): prefer the dict `message` field — `b.detail && typeof b.detail === 'object' && b.detail.message ? b.detail.message : (typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail))` — so a 503 `control_plane_unavailable` renders "Sign-in is temporarily unavailable — try again in a moment.", never raw JSON; add `status >= 500 → unavailable copy` handling. (b) In `completeLogin` (~1111-1137) and the mint catch (~707/718): add `e.status >= 500 → "temporarily unavailable"` copy (mirroring signup.html). **Rebuild + deploy the dashboard dist** (`cd website/apps/dashboard && npm run build` then deploy-pages; the dist is committed — main.jsx changes require the rebuild).

**Step 4:** Deployed md5 checks for signup.html AND the dashboard bundle (deploy-pages.yml output) match local; modal + dashboard claim card copy verified against a stubbed 500.

## Task 7: RC3-a — deploy-hosted health gate (workflow)

**Intent:** Deploys must not silently ship over a dead graph plane (deploy-hosted succeeded 3× today while the instance was NXDOMAIN). Post-deploy gate asserts the app's DB health, mirroring publish-selfhost.yml:70-84, with an explicit bypass for incident-fix deploys.
**Acceptance:** After `flyctl deploy` succeeds, the workflow polls `https://api.premiselabs.co/health` for `db.ok == true` (and `/health/ready` 200 last — it ANDs both planes): fail fast ONLY on app-unreachable (curl error); keep polling `db.ok=false` for ≥3 min (cold-start DB connection exceeds 60s per #338; DNS propagation after a restart runs ~2 min per #1381) before failing the workflow; the `skip-db-health-gate: true` (workflow_dispatch input) bypass (with a `::warning::` log) remains available for incident-fix deploys. **Self-block hazard (Devil's-Advocate P1-1):** `db.ok=false` is the CURRENT prod state (RC3), so the default gate path would fail this fix's own deploy unless the bypass is set — during an incident, "one forgotten checkbox → login still 500s, unbounded" is the most probable failure mode. Mitigation: for THIS deploy (and incident-fix deploys generally), default the bypass input to `true` (with the `::warning::`) OR make the gate warn-only while `skip-db-health-gate` semantics are proven — do not rely on operator memory in incident mode. A fix deploy during a DB incident remains possible via the documented bypass.
**Files:**
- Modify: `.github/workflows/deploy-hosted.yml` (post-deploy step)

**Step 1: Add the gate step** after the deploy retry loop: curl `https://api.premiselabs.co/health` on a poll loop (per Step 2's window shape — fail fast only on app-unreachable; poll `db.ok=false` for ≥3 min; then assert `/health/ready` 200 last), parse `db.ok` (python/jq), assert true; on persistent failure `::error::` + exit 1 (copy the publish-selfhost pattern's loop shape).

**Step 2: Add the bypass input.** `workflow_dispatch.inputs.skip-db-health-gate` (**default `true` during the incident window — Devil's-Advocate P1-1 self-block mitigation; flip to `false` once RC3 restores the DB**) → `if: ${{ ! inputs.skip-db-health-gate }}` on the gate step; emit `::warning::` when the bypass is set (auditability — P4-E); document in the header comment: incident-fix deploys (e.g., deploying this very fix while the DB is down) set the bypass, then the RC3-b owner track restores the DB. **Window tuning (solution-verify P2-C):** do NOT assert `db.ok=true` within a hard 60s window — `/health`'s DB probe depends on a FalkorDB Cloud connection that cold exceeds 60s (#338 decoupled /health from the DB precisely for this) and DNS propagation after a restart runs ~2 min (#1381). Shape the gate: fail fast only on app-unreachable (curl error); keep polling `db.ok=false` for ≥3 min before failing; assert `/health/ready` 200 last (it ANDs both planes).

**Step 3: Manual validation** via a workflow_dispatch on a branch (gate passes when DB healthy; with `skip-db-health-gate` the deploy proceeds).

## Task 8: Full suite, review, commit

**Intent:** Everything above ships through the mandatory gates.
**Acceptance:** Docker-lane full suite green; `commit-workflow` skill invoked (pre-flight, PR, code-review gate, auto-merge).
**Files:**
- Test: `tests/` (docker lane: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/ -v`)

**Step 1:** Run the full docker lane; fix any residual breakages (see Task 3 triage note).
**Step 2:** `ruff`/`mypy` per repo config (`uv run ruff check .`, `uv run mypy tortoise/` — match existing baseline).
**Step 3:** Invoke `skills/commit-workflow/SKILL.md` (branch `fix/1719-session-login-mint-guard`, pre-flight, PR with the plan reference, code-review gate, auto-merge).

## Task 9: RC3-b — owner track + live verification (per #1381)

**Intent:** Restore the graph plane (the separate stacked incident) and prove the O/I end-to-end on prod.
**Acceptance:** `/health` → `status: ok`, `db.ok: true` (5+ consecutive polls); `/health/ready` → 200; real-key `POST /v1/session/login` from a fresh IP → 200 session JSON; `/auth` Playwright: API-key login lands on app.premiselabs.co; welcome-e2e-monitor green 5+ runs.
**Files:**
- Test: live smoke (curl + browser)

**Step 1: Owner console action (blocking, per #1381 resolution).** Log into FalkorDB Cloud console → does instance `r-6jissuruar…` still exist? Restart if stopped (the #1381 resolution: restart → DNS propagated ~2 min) / re-provision if deleted/expired.

**Step 2: If the URI changed,** update the `FALKORDB_CLOUD_URI` GitHub secret → redeploy via deploy-hosted (with `skip-db-health-gate` if deploying before the restart lands — then re-run the gate).

**Step 3: Live verification.**
- `curl https://api.premiselabs.co/health` → `db.ok: true` (poll 5×).
- `curl -X POST https://api.premiselabs.co/v1/session/login -d '{"api_key":"<dashboard-minted tt_ key>"}'` from a fresh egress → 200 with `access_token` + `expires_at` (UUID-created_by class).
- **FULL-JOURNEY bullet (Devil's-Advocate cycle-2 P1 — the reporter's key class):** fresh anon key (`anon-*` created_by) → `POST /v1/session/login` → 403 ANON_TEAM_NO_OWNER with claim copy → `/v1/claim` 200 (live `claim_membership` RPC smoke) → session-authenticated key mint → `POST /v1/session/login` 200 → dashboard reachable. The issue closes only when THIS chain completes for a fresh anon key, not just the 403.
- Browser: `tortoise.premiselabs.co/auth` → API-key login → redirect to app.premiselabs.co (no "Invalid API key.", no stay-on-/auth).
- Legacy-shape key probe → 403 with the correct error_code (spot-check the claim funnel still serves anon keys).

**Step 3b (claimed-team branch — Devil's-Advocate cycle-2 P2):** if Task 1 / Task 9 reveals team `agent-877b2b` (or the probe team) is ALREADY claimed, the reporter's key yields KEY_NOT_USER_MINTED and a key-holder with no dashboard session has no defined recovery — confirm the probe team is unclaimed in Task 1 (is_anon_team check) and, if claimed, define the fallback (email/OAuth sign-in path) in the verification record before the close gate.

**Step 4:** Confirm welcome-e2e-monitor green 5+ consecutive runs; close #1719.

---

## Testing Strategy

- **Unit (fake):** Task 2 helper guard (incl. no-query assertion), Task 3 fidelity (raise on non-UUID filter, OK on UUID / is.null), no drift from regex reuse.
- **Endpoint (TestClient + FakeControlPlane, monkeypatched httpx):** Task 4 503 map (mint-path outage), Task 5 limiter charge-point (503 no-charge, 401 charge, 6th-attempt 429 preserved), all existing 200/401/403/409/429 pins. **NEW chained test (Devil's-Advocate cycle-2 P1):** `test_anon_key_claim_mint_login_chain` — seed anon team + identity key → login 403 ANON_TEAM_NO_OWNER → `/v1/claim` 200 → session-authed mint (UUID created_by) → login 200. Closes the "legs tested in isolation, chain never tested" gap.
- **Suite regression:** full docker lane; FakeControlPlane fidelity default-ON makes the whole suite a canary for the 22P02 class.
- **Client:** manual/e2e copy check (deployed md5 comparison + devtools stubbed 500).
- **Workflow:** manual dispatch validation of the health gate + bypass.
- **Live (post-deploy):** Task 9 smoke matrix.

## Verification Plan (post-deploy, ordered)

1. `GET /health` → `status: ok` + `db.ok: true` (5+ consecutive polls) — RC3 closed.
2. Real-key `POST /v1/session/login` (fresh IP) → 200 session JSON — O/I Indicator 2.
3. `/auth` Playwright: API-key login → redirects to app.premiselabs.co — O/I Indicator 3.
4. No-500 invariant: junk key → 401; "api"/"anon-*"/"reg-*" keys → 403 with correct error_code; mint-path outage (kill switch test in staging) → 503.
5. welcome-e2e-monitor green 5+ consecutive runs (unblocked by DB).

## Acceptance Criteria

1. `/health` ok + `db.ok: true`, 5+ consecutive polls (O/I Indicator 1).
2. Real-key `POST /v1/session/login` → 200 `access_token`/`expires_at` (O/I Indicator 2) — **qualified per second-model P1: "UUID-created_by keys → 200; legacy non-UUID keys ("api"/"anon-*"/"reg-*") → 403 with mint-a-new-key copy by design."** **Full-journey acceptance (Devil's-Advocate P1-2):** the reporter's key is an agent key (`created_by="anon-…"`) → post-fix 403 ANON_TEAM_NO_OWNER → claim funnel → OAuth identity attach → dashboard key mint → 200. Task 9 must verify the FULL journey for the reporter's key class, not just the 403 — "key → 403 with claim copy → OAuth claim → dashboard key mint → 200" — so the issue closes only when the user can actually reach the dashboard. A dashboard-minted (UUID-created_by) key → 200 directly.
3. API-key login on tortoise.premiselabs.co/auth redirects to app.premiselabs.co (O/I Indicator 3).
4. **No-500 invariant (mint/claim surfaces — scoped per solution-verify P1):** no input class reaches the *unmapped global-handler* 500 body on the `/v1/session/login` mint path, `/v1/claim`, `/v1/claim/email`, `/v1/claim/status` — every mint/claim failure is an explicit 401/403/409/429/502/503. The resolve-leg catch-all (500 "Auth error" on a full Supabase outage) is a documented follow-up; the client `>=500` copy renders it honestly in the interim.
5. Legacy non-UUID keys → 403 with the *correct* error_code; claim funnel reachable (never "Invalid API key." for them).
6. Mint-path control-plane failure → 503 `control_plane_unavailable`; client renders "temporarily unavailable".
7. Rate limiter: 5/hr preserved for 200/401/403 (server decisions); 5xx does not consume budget; no 429 masking of incidents.
8. `FakeControlPlane` raises on non-UUID `user_id` filters by default — CI catches the class (a future unsanitized call site fails the suite).
9. deploy-hosted has a post-deploy DB health gate (with documented incident-fix bypass).
10. welcome-e2e-monitor green 5+ consecutive runs.

## Runtime Prerequisites

- **Owner (blocking for RC3):** FalkorDB Cloud console access — instance `r-6jissuruar…` (restart per #1381; re-provision if deleted/expired; update `FALKORDB_CLOUD_URI` GitHub secret + redeploy if the URI changed).
- **Diagnostics:** `fly logs -a tortoise-y4mjjq` access (Task 1); a fresh egress IP for the live smoke (the repro egress may hold a 429 bucket).
- **Tests:** docker lane per AGENTS.md (`docker compose -f ../eldato/operations/memory/docker-compose.yml up -d` + `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`).
- **Deploy:** GitHub `FLY_API_TOKEN_DEPLOY` / `FLY_API_TOKEN` (already configured); `SUPABASE_ACCESS_TOKEN` (migration-drift gate, unchanged).

## Follow-ups (explicitly out of scope, noted)

- `_get_current_team_supabase`'s resolve-path catch-all maps any control-plane failure to 500 "Auth error" (hosted_api.py:1387) — the client renders it via Task 6's `status >= 500` branch as "temporarily unavailable" (honest, not "Invalid API key."). A resolve-path 503 map would be honest across all key-auth endpoints but changes a shared fail-closed contract; defer.
- Session-JWT management endpoints (`user_memberships` at hosted_api.py:1406/3847) share the outage class → 500; same deferred pattern.
- B's typed-query registry: revisit only if a second unsanitized literal appears (the fake fidelity will force it into the open).
