---
title: "C2 Implementation Plan — unified provisioning service + graph lifecycle"
type: engineering
domain: platform
doc_status: live
created: 2026-09-01
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-hosted-platform
---

# C2 (#2111): Unified provisioning service + graph lifecycle — implementation plan

<!-- research-path: epic plan docs/epics/2026-09-01-2083-multi-graph/03-plan.md §5.2/W1/W3 + §6.2 + §7 E2E-1/3/7/8/11; verified live against codebase 2026-09-01 (main 8ccea6e5 with C1): create_graph hosted_api.py:6681, list_graphs :6712, create_api_key :4348, _check_team_limit :1702 (402), _team_limits_from_node :6327, _membership_team :6256, _team_node :6284, _ensure_not_suspended :6309, _short_id :1113, insert_api_key supabase_control.py:902, apikey_create sdk.py:12506 (hardcodes tt_ — P1 #4 finding), apikey_revoke :12570, _verify_hashed_lookup :12457 (tt_ prefix fast-path — P2), graph_count registry branch :12199 (no status filter — P1 #3), auth format gates hardcode tt_ (hosted_api.py:1299/3288/9917/10023/10114/686, mcp_auth.py:151). Zero third-party deps → Perplexity gate skipped. -->

**TIER:** Complex
**Epic contract:** 03-plan.md §5.2, §6.2, W1, W3, E2E-1/3/7/8/11
**Depends on:** #2110 (C1 — merged), #2094 (test-design reference)
**Exit gate:** E2E-1, E2E-3, E2E-7 (quota half), E2E-8, E2E-11 — both modes

## Scope boundaries

**IN:** (1) ONE provisioning service (key-driven `POST /v1/teams/{team_id}/graphs` + session alias `POST /v1/graphs`); (2) tier gate 402 + quota gate 409+X-Graph-Quota + key-cap 409 rollback; (3) mint (graphs row/node + per-graph key, scopes∩child-policy, deleg=0, tk_); (4) rollback (no orphan); (5) `DELETE /v1/graphs/{id}` soft-delete + cascade (keys revoked, ACL drop seam, slot free, name reuse), default 403; (6) `GET /v1/graphs` status+key_count; (7) auth gates accept `tk_`; (8) ACL-create seam at mint; (9) registry graph_count status filter.

**OUT:** key lifecycle endpoints (C3 #2112 — standalone mint/list/shrink/revoke CONSUMES the shared helper from Task 3, never re-implements), ACL internals (C4 #2113), tenancy spine (C5 #2114), delivery-shape/recording (C6 #2115), dashboard (C7 #2116).

## Decisions

- **D0 (owner-approved deviation, recorded 2026-09-01 plan-review P0 #2):** the issue body says "the per-graph key mint calls the ONE shared key-mint service OWNED BY C3". Execution order is C2-first (C3 is parallel, not yet shipped), so C2 SHIPS the shared mint helper as a standalone function (`_mint_graph_key`, Task 3) that C3's standalone endpoints will CONSUME. This preserves the "ONE implementation, never re-implemented" contract; C3's issue body is updated at C2 merge to reference the helper as its dependency. If the owner prefers C3-first sequencing, the plan can be reordered — but the shared helper must exist exactly once either way.
- **D1 ONE service function** `_provision_graph` — both endpoints delegate; tier→suspension→name→quota→mint→rollback in one place.
- **D2 Tier gate = 402, BLOCKS ONLY `free` (plan-review P0 #1, E2E-3 pin):** free (max_graphs=1, default fills slot 1) → 402 upgrade-CTA FIRST; solo (max_graphs=2) + pro + team PASS the tier gate — solo's 1st custom is allowed (E2E-3), a 3rd is a 409 quota (not 402). **The session alias must REMOVE its existing `_check_team_limit(..., "graphs")` 402 call** (stale 402 would break E2E-3's 409 pin — the shared service is the one gate).
- **D3 Quota gate = 409 + X-Graph-Quota header** (distinct from tier-402; distinct from the legacy `create_api_key`'s 402 for key-cap — documented asymmetry so C3 doesn't "normalize" it). Atomic count-then-insert under a per-team asyncio.Lock + post-insert re-count backstop that **rolls back the just-inserted graph when over cap** (E2E-11 no oversubscription; single-process caveat documented like the signup-token lane — multi-worker selfhost re-count degrades "exactly 1" to "0 succeed", never over-subscribes).
- **D4 Key-cap rollback:** quota gate → INSERT graph → mint key (max_api_keys pre-check; at cap → 409-mapped, GRAPH ROLLS BACK — no graph-without-key, no orphan).
- **D5 Child policy:** requested ∩ {graphs:read, graphs:write} — escalation scopes NEVER inherited; DB CHECK backstop; default = ["graphs:read"].
- **D6 Minted key = tk_ + deleg=0 + created_by_key_id.** `apikey_create` gains a `prefix` kwarg (default "tt_", back-compat) so the registry mint can produce tk_ (P1 #4). Auth gates widened to `("tt_","tk_")` (Task 1). `_verify_hashed_lookup` prefix fast-path also widened (P2 perf).
- **D7 Reveal-once:** plaintext exactly once (mint return); hash-only stored; no re-exposure; audit never logs bodies (verified existing behavior).
- **D8 Delete lifecycle:** soft-delete + cascade (keys revoked → 401, ACL drop seam, slot free, name reuse); default graph 403 (kind='default' OR graph_id='default' — mode-agnostic).
- **D9 GET /v1/graphs:** status + key_count per row, point_count dropped, default-first.
- **D10 Supabase mint writes graphs row via `insert_graph` seam (C2 owns the write C1 deferred); registry `_graph_create` persists (already).**
- **D11 Rollback:** any post-write failure → delete graph row/node + revoke minted key if landed. Audit: registry-mode key ops already audit via `apikey_create`/`apikey_revoke` (sdk.py `self._audit`); no graph-lifecycle audit event exists in the registry today (even `_graph_create` has none) and C2's issue body never lists audit as an indicator — NOT added here (scope creep). Handoff note for C3/C8 if a graph-level audit trail is wanted later.
- **D12 Session-alias create keeps ANY-member auth (plan-review note):** the contract text says "owner/admin session user" (§6.2) but that assumed the pre-existing E5 — which actually gates on membership only (any active member, verified 2026-09-02 against main 8ccea6e5). No scope/align decision tightens member rights (principal-level RBAC is #2082's boundary; the epic scopes credentials); the dashboard's Graphs tab renders the Create form for ALL session members today, so an owner/admin-only API would 403 an existing UI surface until C7 gates it (regression mid-epic). DELETE is a NEW surface → owner/admin enforced there (as contracted). Deviation recorded for the code-review gate; if reviewers rule otherwise, the alias gains a role check + C7 UI gate ships in the same change.

## Tasks (TDD — mint helper FIRST so the service has its dependency)

### Task 1: Auth format gates accept `tk_`
**Intent:** minted tk_ keys must authenticate (E2E-1's credential is useless at 401 invalid_format).
**Acceptance:** tk_ resolves via get_current_team (both modes) + MCP + body-validation sites; tt_ unchanged; other prefixes 401.
**Files:** Modify `tortoise/hosted_api.py` (:1299, :3288, :9917, :10023, :10114, :686) + `tortoise/mcp_auth.py` (:151) + `tortoise/sdk.py` (:12457 fast-path); Test `tests/test_hosted_auth.py`, `tests/test_mcp_server_auth_modes.py`.
**Steps:** module constant `_KEY_PREFIXES = ("tt_", "tk_")` (shared importable); replace `startswith("tt_")` → `startswith(_KEY_PREFIXES)` at all sites incl. the sdk fast-path; tests: tk_ resolve both modes + MCP + bad-prefix 401.

### Task 2: Seam helpers — insert/delete graph, count graph keys, registry status filter
**Intent:** Supabase graphs INSERT (C1 deferred it), rollback delete, key_count source, and the registry count fix E2E-8 needs.
**Acceptance:** `insert_graph`/`delete_graph_row`/`count_graph_keys` (both modes) work; registry `graph_count` excludes status='deleted' (P1 #3).
**Files:** Modify `tortoise/supabase_control.py`, `tortoise/sdk.py` (graph_count registry branch + graph_key_ids); Test `tests/test_supabase_control.py`, `tests/test_control_plane.py`.
**Steps:**
1. `insert_graph(cp, row)` POST to graphs; `delete_graph_row(cp, team_id, graph_id)` DELETE by id+team.
2. `count_graph_keys` (Supabase: api_keys count where graph_id; registry: MATCH (k:APIKey {graph_id})).
3. **Registry graph_count: `MATCH (g:Graph {team_id:$tid}) WHERE g.status IS NULL OR g.status <> 'deleted' RETURN count(g)`** — C1's docstring promised C3 would do this; C2's delete makes it C2's requirement (E2E-8 slot release + registry↔Supabase parity R3).
4. Tests: insert/delete/count; graph_count after soft-delete decrements (registry mode).

### Task 3: Shared per-graph key mint `_mint_graph_key` (C3's dependency)
**Intent:** the ONE mint (delegation stamping, max_api_keys gate, reveal-once, tk_) — C3's standalone endpoints consume this exact function (D0).
**Acceptance:** mint returns {id, key_plaintext, key_prefix, scopes, delegation_depth, graph_id, created_by_key_id, created_at}; hash-only stored; key-cap → 409-mapped; plaintext once; works both modes incl. registry tk_.
**Files:** Modify `tortoise/hosted_api.py` (helper near create_api_key), `tortoise/supabase_control.py` (insert_api_key C1-column passthrough verified), `tortoise/sdk.py` (apikey_create `prefix` kwarg, back-compat default "tt_"); Test `tests/test_hosted_api.py`, `tests/test_supabase_control.py`.
**Steps:**
1. `apikey_create` registry: add `prefix: str = "tt_"` kwarg (back-compat); registry mint passes "tk_".
2. `insert_api_key` Supabase: verify C1 columns (graph_id/scopes/delegation_depth/created_by_key_id) flow through the POST (it's a passthrough — add explicit doc).
3. `_mint_graph_key(team_id, graph_id, requested_scopes, caller_key_id)` → scopes ∩ child-policy (D5), tk_ token, lookup_hash, key_prefix, deleg=0, created_by_key_id; key-cap pre-check (count active keys ≥ max_api_keys → `_KeyCapExceeded` → caller maps 409); Supabase → insert_api_key; registry → apikey_create(prefix="tk_", ...).
4. ACL-create seam (P1 #5): `_create_acl_user(graph_id, namespace)` fail-soft hook invoked here — C4-owned assertion; no-op if C4 module absent (surface-6 mint half).
5. Tests: mint both modes (scopes filtered, deleg=0, tk_ prefix, reveal-once — plaintext NOT re-listed).

### Task 4: The ONE provisioning service `_provision_graph` + both endpoints
**Intent:** tier→suspension→name→quota→mint→rollback in one function; E2E-1/3/7/11 semantics.
**Acceptance:** both endpoints → identical 201 {graph, key, key_plaintext, revealed_once}; free→402 FIRST; solo 1st custom→201; solo 3rd→409+X-Graph-Quota; key-cap→409+rollback (no orphan); concurrent→never over cap; deleg=0 key→403 (E2E-4-negative); cross-team key→404 (P1 #6); unknown team→404.
**Files:** Modify `tortoise/hosted_api.py`; Test `tests/test_hosted_api.py` (TestProvisioningService).
**Steps:**
1. `_tier_gate(team)`: tier == "free" → 402 upgrade-CTA (BEFORE quota — W1 ordering). Solo+ passes.
2. `_graph_quota_gate(team)`: max_graphs finite + graph_count >= max → 409 + `X-Graph-Quota: <count>/<max>` + upgrade-CTA body. (pro/team max_graphs None = unlimited; no warn band.)
3. `_provision_graph(team, name, requested_scopes, caller_key_id)` — per-team asyncio.Lock (`_PROVISION_LOCKS` dict): name validation (empty→422; dup-active→409 — check INSIDE the lock, registry has no unique index) → quota gate → `insert_graph`/`_graph_create` → `_mint_graph_key` (Task 3) → post-insert re-count (over cap → rollback graph → 409) → 201 envelope. On ANY failure after graph write → rollback (D11) → mapped status.
4. `POST /v1/teams/{team_id}/graphs` (key auth): resolve key → **key.team_id must == path team_id else 404** (P1 #6) → 403 if no graphs:create scope OR delegation_depth == 0 (minted key can't provision — E2E-4) → suspension 403 → `_provision_graph` (caller_key_id = key.id).
5. `POST /v1/graphs` (session alias): existing membership/suspension checks + 422 → `_provision_graph` (caller_key_id = None). **Remove the stale `_check_team_limit(..., "graphs")` 402** (D2).
6. Audit + api_key_created analytics + abuse velocity per mint (mirror create_api_key); response Cache-Control no-store.
7. Tests: E2E-1 (201 envelope, reveal-once, 409 dup), E2E-3 (free 402 FIRST / solo 1st 201 / solo 3rd 409+header), E2E-4-negative (deleg=0 key → 403), 401 revoked caller, cross-team 404, unknown-team 404, key-cap 409 + no orphan (graph_list empty).

### Task 5: `DELETE /v1/graphs/{id}` + `GET /v1/graphs` extension
**Intent:** lifecycle + list (E2E-8).
**Acceptance:** DELETE 204 + tombstone + keys 401 + slot freed (next provision 201) + same-name recreate 201 + default 403 + missing 404; GET rows status+key_count, default-first, no point_count.
**Files:** Modify `tortoise/hosted_api.py`, `tortoise/supabase_control.py`, `tortoise/sdk.py`; Test `tests/test_hosted_api.py`, `tests/test_control_plane.py`.
**Steps:**
1. `soft_delete_graph` (Supabase): UPDATE status='deleted' WHERE id AND team_id AND kind<>'default' → 0 rows → distinguish default (403) vs missing (404) by a prior kind lookup.
2. Registry `graph_delete(team_id, graph_id)`: SET status='deleted' (kind='default' guard → 403).
3. Auth wiring (P2 from review): key path → resolve + `graphs:delete` scope (403 missing scope) + team match (404); session path → `_require_owner_admin` (403 non-owner).
4. Cascade: `graph_key_ids` → revoke each (resolve rejects revoked_at → 401 next use); `_drop_acl_user(graph_id)` fail-soft seam (C4); slot frees via graph_count status filter (Task 2).
5. `GET /v1/graphs`: graph_list (active-only filter) + key_count per row; default-first; point_count dropped.
6. Tests: full E2E-8 sequence (delete → 204 → key 401 → provision ok → same-name 201 → default 403) + 403 missing-scope + 404.

### Task 6: Concurrency + rollback drill + full verification
**Intent:** E2E-11 + no-orphan proof + suite runs.
**Acceptance:** 8 concurrent on 1-free-slot → exactly 1×201 + 7×409, count ≤ cap; key-cap drill → no orphan graph; full docker lane + carve-out + PGlite green.
**Files:** Test `tests/test_hosted_api.py`, `tests/test_supabase_control.py`.
**Steps:**
1. Concurrency: asyncio.gather 8 on a solo team with 1 free slot → 1×201/7×409 (both modes); graph_count ≤ cap after.
2. Rollback drills: key-cap → 409 + graph_list empty; forced graph-insert failure → mapped 500 + no key orphan.
3. No-charge assertion (E2E-7): quota reject path never touches billing (holds by construction — assert no billing seam call in the gate; gated on existing test-env support).
4. Full docker lane + carve-out + PGlite (no migration change — regression only).

## Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | tk_ gate missed (dead minted key) | Central constant + all 7 sites + MCP test + sdk fast-path (Task 1) |
| R2 | Quota oversubscription | Per-team lock + post-insert re-count with rollback (D3); single-process caveat documented |
| R3 | Orphan graph/key on failure | Rollback on every post-write failure (D11) + drill tests |
| R4 | key_plaintext leak | Reveal-once (mint return only); audit never logs bodies (verify) |
| R5 | Default-graph delete guard mode mismatch | Mode-agnostic guard (kind OR graph_id); both modes tested |
| R6 | C4 ACL seam absent at C2 time | Fail-soft create/drop hooks; surface-6 assertions deferred to capstone #2118 per issue body |
| R7 | 402-vs-409 confusion (legacy create_api_key 402s) | Dedicated _graph_quota_gate 409; documented asymmetry for C3 |
| R8 | C3 mint-ownership drift | D0 recorded deviation + C3 issue updated at C2 merge (consumes helper) |
| R9 | Registry↔Supabase quota drift on delete | Task 2 registry status filter (P1 #3) — one count definition |

## Execution log (2026-09-02 — live update during implementation)

All Tasks 1–6 implemented in `tests/test_hosted_api.py` (`TestProvisioningService` E2E-1/3/7, `TestGraphLifecycle` E2E-8, `TestProvisioningConcurrency` E2E-11), `tests/test_hosted_auth.py` (`TestTkPrefixAuth`), `tests/test_supabase_control.py` (`TestGraphLifecycleSeam`), plus rewritten `TestGraphSurface`/suspension-parity tests (free-402 pin replaces the pre-C2 #765 no-persistence 200s).

- **Review-caught fixes during implementation (self-review before the PR gate):**
  1. **Registry mint id/plaintext drift (real bug):** `_mint_graph_key` pre-computed `kid`/`api_key` then called `sdk.apikey_create` (registry), which generates its OWN node id AND plaintext — the envelope's `key.id`/`key_plaintext` would not match the stored node (C3 revoke/shrink-by-id would miss; a revealed key would fail verify). Fixed: capture `created["id"]`/`created["api_key"]`/`created["key_prefix"]`; regression asserts envelope-id==node-id AND plaintext-verifies-against-stored-hash.
  2. **Registry default-node delete → 404 not 403:** the `kind='default'` node carries a random gid; the old `graph_id == "default"` guard missed it and `graph_delete` returned False → 404. Fixed: kind pre-lookup maps default→403, unknown→404 (literal "default" guard kept for the Supabase-derived id). Test `test_registry_default_node_by_gid_403` added.
  3. **Lifecycle test sequencing:** name-reuse must happen in the freed slot BEFORE a new-name provision (solo allows 1 custom).
  4. **Session delete was dead code (real bug):** DELETE /v1/graphs/{id} used `Depends(get_current_team)` (key-only) but the plan (§6.3/W3) requires "key with graphs:delete OR owner/admin session" — a session JWT (eyJ…) 401s at get_current_team's format gate, and its return dict never carries `user_id`. Fixed: the endpoint now uses `Depends(get_current_team_session)` (the dual-auth dependency: key → get_current_team; session → `_session_user_team`), reading `session_user_id` for the session-face role check. The #1148 dashboard-key-login gate only rejects legacy `tt_` keys on flag-off teams — tk_ graphs:delete keys and sessions always pass (matches the provisioning face). New tests: `test_delete_graph_session_owner_204`, `test_delete_graph_session_member_403`, `test_delete_graph_403_suspended`.
  5. **Session-alias mint attribution (created_by="api" → session user UUID):** the session alias passed caller_key_id=None, so the minted key recorded created_by="api" — losing WHO minted. `_provision_graph`/`_mint_graph_key` gained `session_user_id` (threaded from the session alias's get_current_user); created_by = session_user_id or "api" (#1511 parity with create_api_key). Assertion added in `test_create_graph_writes_row_supabase_mode`.
- **Code-review-gate findings (sub-agent review, 2026-09-02) — ALL FIXED:**
  - **P1-1 (create_api_key no deleg gate):** a minted (deleg=0) key could mint an OWNER-level key via POST /v1/team/keys (the DB CHECK constrains deleg=0 scopes, not capability surfaces). Fixed: `create_api_key` 403s deleg=0 key callers ("Minted keys cannot mint new keys"). Test `test_minted_key_cannot_mint_team_key_403`.
  - **P1-2 (session-login exchange escalation):** a deleg=0 per-graph key carries created_by = the minting session user UUID — /v1/session/login would mint the OWNER's session to any holder of a least-privilege key. Fixed: the exchange rejects deleg=0 keys (403 KEY_NOT_USER_MINTED — child-policy consistent: minted keys never carry login identity). Test `test_minted_key_session_login_403`.
  - **P2-3 (key_count mode drift):** registry list reused graph_key_ids (cascade source, counts revoked); Supabase count_graph_keys filters active. Fixed: new `sdk.graph_active_key_count` (revoked_at IS NULL) used by GET /v1/graphs; graph_key_ids stays the cascade source.
  - **P2-4 (missing re-count + sequential concurrency test):** the D3 comment claimed a post-insert re-count backstop that didn't exist; the concurrency test was sequential (never contended the lock). Fixed: real post-insert re-count in `_provision_graph` (over cap → rollback the just-inserted graph + 409); concurrency tests rewritten genuinely concurrent via thread pool — new `test_8_concurrent_on_1_free_slot_gap` proves EXACTLY ONE 201 of 8 on a 1-free-slot race.
  - **P2-5 (missing mint telemetry):** plan Task 4 Step 6 promised api_key_created + R2 abuse evaluation per mint. Fixed: both endpoints fire api_key_created (source="provision", session user / key creator as distinct_id) + `_abuse_evaluate_keys` after successful provision.
  - **P2-6 (created_by = key id):** key-driven mints recorded created_by = caller_key_id (breaking the user-UUID/"api" convention consumers assume; lineage already rides created_by_key_id). Fixed: created_by = session_user_id or "api" in both lanes.
  - **P2-7 (non-additive response claim):** the old top-level {graph_id,name,kind,graph_name} was replaced by the nested envelope — the "additive" docstring was wrong. Fixed: docstring corrected (verified the only in-repo consumer, dashboard createGraph, reads status only).
- **D12 recorded above** (session-alias create keeps ANY-member auth — contract text assumed pre-C2 E5 required owner/admin; it gates on membership only).
- **C3 handoff notes (recorded for C3 #2112):** (a) `_mint_graph_key` is the shared mint — CONSUME, never re-implement; it strips escalation (∩ child policy, E2E-1). C3's STANDALONE mint endpoint must 403 on escalation-scope requests BEFORE delegating (plan §6.3 line ~384: 403 escalation on minted key) — pre-validate at the endpoint. (b) Registry mint returns the REAL node id/plaintext (fix 1) — C3 revoke/shrink by envelope id now works. (c) key-cap gate lives in the helper (`_KeyCapExceeded`); C3's standalone mint maps it 409 + rollback semantics per its own contract. (d) POST /v1/team/keys NOW gates deleg=0 callers (review P1-1) — C3's new mint surface must too. (e) C3's revoke must feed `graph_active_key_count` semantics (revoked keys drop the meter).
- **C5 handoff notes (tenancy spine #2114):** until C5 binds graph_namespace, a per-graph tk_ key on MCP behaves TEAM-WIDE (mcp_auth accepts tk_ but resolution never reads graph_id — observation 2 from the C2 review). tk_ keys must not be advertised on MCP surfaces before C5 lands. The REST resolve path DOES return graph_namespace (C1) but nothing consumes it yet.

## Handoff

Review gate: plan-review (this doc, 2 P0s fixed). Execution: single-session. Code-review gate at PR time (complexity:complex → mandatory). **C2→C3:** `_mint_graph_key` is C3's dependency (D0); C3's issue body updated at C2 merge. C4 surface-6 runs at capstone if C4 ships after C2. C5/C6/C7 consume the 201 envelope + lifecycle as designed.
