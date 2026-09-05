<!-- research-path: docs/epics/2026-08-29-agent-driven-onboarding-1976/02-research-brief.md -->

# W6 Session-Capture Disclosure Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
> **Issue:** #2002 (W6 of epic #1976, agent-driven onboarding) · **Branch:** feat/2002-W6-onboarding

**Goal:** Ship session-capture disclosure on the self-use path: (1) a first-capture trigger on the capture path that writes the `capture-disclosed` NODE CHECKPOINT (epic §2 WF-5 / §4 DM-1) and returns a first-capture marker so the in-conversation agent fires W2's ONE-line announcement (copy contract owned by `tortoise/onboarding/SKILL.md` §6 — W6 never drifts the wording); (2) Settings view/delete of captured transcripts in the W4 Captured-sessions home; (3) `DELETE /v1/sessions/{session_id}` that removes the Session + its owned graph subgraph (turn/extracted Points, sessionCaptured Event, agentSession Source) AND cleans the capture receipts (jsonb) with no orphans; all while preserving #1927 default-ON (no re-gate, off-switch quiet 409) and enforcing team-member authz (until W10 RBAC).

**Team:** epistemic-team
**Role:** (unavailable)

**Architecture:** The capture path is `_capture_session_impl` (hosted_api.py) — shared by POST /v1/sessions AND the `tortoise_session_capture` MCP tool (mcp_server.py imports the same impl), so the trigger lives in ONE place and both surfaces stay in lockstep (never touch mcp_server.py / sdk.py — shared modules, W8 parallel ownership). The trigger runs after all durable capture writes on the 2xx path: `_os.write_completed_step(proj, team_id, "capture-disclosed", status_from_mirror=...)` (idempotent keyed-MERGE; FWW; created-signal honest under the state.py per-org lock), then `_maybe_apply_completion` (capture-disclosed is NOT a card step — never a counted row, so it can never false-complete the guide; the gate eval is a monotonic no-op unless the real gate was already satisfied). The response gains `"first_capture": created` — the in-conversation agent reads it and fires the ONE line from SKILL.md §6. A session-existence re-verify immediately before the receipt write (the receipt↔Session invariant, T1-P12) skips the receipt when the Session was deleted mid-capture (delete-during-capture race: no orphaned receipt). DELETE is a new hosted endpoint (no partial exists — verified: only GET /v1/sessions + GET /v1/sessions/{id} + POST exist) with dual-auth team-member authz (`get_current_team_session_ungated` — session JWT membership-validated via `_session_user_team`, key auth team-scoped), graph deletion of exactly the session's owned subgraph, then receipt cleanup by recompute: a receipt key (bare or per-harness) is cleared iff ZERO remaining :Session nodes correspond to it (per-harness by `s.harness`, bare by `harness IS NULL`). The dashboard Settings home (W4 seam, Home 4 "Captured sessions") gains per-row View (transcript panel from GET /v1/sessions/{id}, reusing the #714 .session-detail/.turn-* CSS) + Delete (confirm → DELETE → list + onboarding refresh), with the row/list derivation logic in a pure `capturedSessions.js` module (node --test, no jsdom).

### Pattern Research

**Axis Architecture (high)**
- **FalkorDB graph deletion** (in-repo precedent: `_journal_append_product`/delete paths, test_export_delete.py): no cascade delete — delete each owned node type explicitly; DETACH DELETE removes a node + its edges in one statement. Session-owned subgraph (from _capture_session_impl + sdk.py capture_session + _materialize_session_source): `(:Session {id})`, `(:Point {id: '{sid}_t{i}'})` turn Points + extracted Points wired `(s)-[:CONTAINS]->(p)`, `(:Event {eventKind:'sessionCaptured', sessionId, eventId})` (the provenance event stamped onto extracted Points as `p.eventId`), `(:Source {url: 'session:{sid}'})` (agentSession materialization). Entity links `(s)-[:aboutObject]->(Object)` die with the Session DETACH DELETE; the Object survives (deleting a transcript never deletes the issue/entity it referenced).
- **Capture receipts** (in-repo: _capture_receipt_key / _record_capture_last_error, hosted_api.py:5364+): per-harness jsonb timestamps (`session_capture_receipt`, `session_capture_receipt_{harness}`), set only on 2xx (T1-P12 receipt↔Session invariant: a receipt proves a durable Session). Receipts carry NO session id — cleanup must recompute from the remaining graph (a receipt is an orphan iff zero Sessions remain for its harness bucket).
- **Jsonb key clear** (in-repo precedent: `_record_capture_last_error(..., None)`): write the key = None through the shared writer (`_update_onboarding_state`) — registered keys pass the allowlist; the projection/dashboard read falsy → 'install-pending'/'waiting' honest states (never a fabricated receipt).
- **FalkorDB concurrency** (canonical: https://docs.falkordb.com/design/concurrency — per-graph write serialization, atomic single queries): delete-vs-capture races narrow to (a) the capture-side session-existence re-verify before the receipt write + (b) the delete-side recompute AFTER graph removal (interleavings converge: whichever op's receipt-write lands last recomputes against the freshest graph in the delete path; the capture path skips the receipt when the Session is gone).

**Axis UX (standard)**
- Settings Captured-sessions home (W4, merged #2139) renders honest list states (loading / state-missing / recording-off / empty) + rows (date · turns · extracted · truncated id). W6 adds View + Delete per row + an inline transcript panel. Reuses existing CSS (#714 .session-detail/.turn-list/.turn-item/.kind-* — already shipped in index.css) and existing patterns (confirm() like revokeKey/remove-member; per-row busy + inline error like the Memory-sources row errors). No new design language.

> **Findings date:** 2026-09-02

### Integration Surface Map

> **Implementation status:** Tasks 1–5 COMPLETE (2026-09-03). Delivered: first-capture trigger with pre-check + POST-write compensation (both race windows), DELETE endpoint (dual-auth), Settings View/Delete UI + pure module, 12 docker-lane tests + 7 lane-agnostic authz tests + 6 JS tests.
>
> **Delta from plan (documented):** (1) the capture-side receipt guard is pre-check AND post-write re-verify + compensation (the delete's graph removal + recompute can land between the pre-check and the receipt PATCH — the post-check closes that window; every interleaving converges to receipt ⟺ surviving Session); (2) member-authz assertions live in `tests/test_onboarding_w6_member_authz.py` (Supabase-mode FakeControlPlane harness — the docker lane registers via tt_ keys only, so the session-JWT lane needs the dual-auth harness, mirroring test_action_endpoints_dual_auth); (3) `DELETE /v1/sessions/{id}` returns `cleaned_receipts` for observability; (4) GET /v1/sessions/{id} widened to dual-auth (`get_current_team_session_ungated`, #1828 precedent) so the session-authed Settings transcript View renders without a fresh bootstrap mint.

| # | Surface | Type | Data Flow | Test Layer | Contract |
|---|---------|------|-----------|-----------|----------|
| 11a | First-capture trigger (capture path → capture-disclosed node checkpoint) | DB (graph) + API | Both | Integration (docker lane) | First 2xx capture writes the capture-disclosed COMPLETED_STEP edge (FWW keyed MERGE) + response `first_capture: true`; 2nd capture → noop edge + `first_capture: false`; replay of the first session → false (never re-fires); capture-disclosed never a card-counted step (state.py CARD_STEPS exclusion is pre-pinned + re-asserted); capture-disclosed never renders "N of M" complete; #1927 default-ON preserved (no new gate; off-switch stays quiet 409 — regression) |
| 11b | DELETE /v1/sessions/{session_id} graph cleanup | DB (graph) | Out | Integration (docker lane) | 200 {deleted:true} removes Session + CONTAINS Points (turns + extracted) + sessionCaptured Event + agentSession Source; aboutObject targets survive; 404 unknown id; idempotent (2nd delete 404); cross-team isolation by tenant namespace |
| 11c | DELETE capture-receipt cleanup | DB (jsonb/registry) | Out | Integration (docker lane) | Deleted session's receipt keys cleared iff zero remaining Sessions in that harness bucket (bare receipt ↔ harness-less Sessions; per-harness ↔ s.harness); a second session under the same harness keeps the receipt; probes untouched |
| 11d | Delete-during-capture race | Concurrent | Both | Integration (docker lane) | Capture → delete → replay-capture (same session_id) writes NO receipt (session-existence re-verify at receipt time); receipt never orphans; delete recompute clears a receipt whose session vanished |
| 11e | Authz (team-member until W10) | Auth | Guard | Integration (docker lane) | DELETE + detail GET dual-auth (session JWT OR tt_ key); session user without membership in ?team_id= team → 403 (`_session_user_team`); key auth team-scoped by resolution |
| 11f | Settings view/delete UI (W4 home consumer) | UI | Both | JS unit (node --test) + ux-verification | Per-row View (transcript panel: turns + extracted) + Delete (confirm → DELETE → list mutates via pure filter fn); busy/error states honest; capture-status derivation (captureStatus.js) untouched |

**Bug Pattern Flags**
- Race conditions (MEDIUM → HIGH per epic risk "Capture delete orphans graph data"): delete-during-capture → capture-side Session re-verify before receipt write + delete-side recompute after removal; deterministic negative test (replay-after-delete).
- Silent function skips: announcement trigger must be reachable from BOTH REST + MCP capture surfaces (shared impl) — docker-lane test drives POST /v1/sessions; MCP surface inherits by construction (mcp_server imports the same impl — not re-tested here, W8 lane).
- Conditional guards: off-switch 409 path must stay byte-identical (#1927); receipt write skip must not fire on normal replay convergence (T1-P12 regression: replay-with-session-present still writes the receipt).

**Checklist Notes**
- Atomicity: single-query graph removals per node type (each DETACH DELETE is atomic in FalkorDB).
- Idempotency: checkpoint write FWW; delete 404-on-absent; receipt clear idempotent (None write).
- Boundary values: zero sessions (empty state already handled), one session, two sessions same harness (receipt survives), two harnesses (only the empty bucket's receipt clears), unknown session id.
- Empty vs null: Session with no harness property (bare bucket) vs explicit harness; eventId absent (Event write failed path — Source/points un-stamped) — delete must still remove the Session/Points without a dangling Event match.

### Verification Plan

- **Docker lane (default):** `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_onboarding_w6_capture_disclosure.py tests/test_onboarding_state.py tests/test_onboarding_w4_settings.py tests/test_onboarding_state_split.py tests/test_capture_session.py -q` — trigger + checkpoint + delete hygiene + receipt cleanup + race negatives + #1927 regression.
- **Embedded carve-out:** `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embedded_lifecycle.py tests/test_guard.py -q` (must stay green — no embedded-only file touched).
- **Dashboard JS:** `cd website/apps/dashboard && node --test src/capturedSessions.test.js src/setupGuide.test.js src/captureStatus.test.js`.
- **Hosted E2E (opt-in):** `RUN_HOSTED_E2E=1 uv run pytest tests/e2e/hosted/ -q -rs` — DE2E-11 module (new) mirrors the W5 journey-module shape.
- **ruff:** `uv run ruff check tortoise/hosted_api.py tests/test_onboarding_w6_capture_disclosure.py` (0.16.4, RUF059 active).
- **vite build + dist commit (#1148):** `cd website/apps/dashboard && npm run build` when main.jsx changes; commit dist assets.

### Journey Test Map

### Journey: First capture → Settings view/delete (DE2E-11)
1. **Step:** first capture POST /v1/sessions → **Acceptance:** 200; response first_capture=true; capture-disclosed edge on the OnboardingState node; receipts set → **Test:** docker-lane trigger tests + graph assertion.
2. **Step:** agent fires the ONE in-conversation line (copy in SKILL.md §6 — code returns the marker only) → **Acceptance:** marker true on first capture only → **Test:** replay + second-session negatives.
3. **Step:** Settings → Captured sessions → View → **Acceptance:** transcript panel shows turns + extracted (GET /v1/sessions/{id} dual-auth) → **Test:** JS transcript-row derivation + docker-lane detail GET.
4. **Step:** Delete → **Acceptance:** session + receipt gone; list refreshes; no orphan nodes; deleting mid-capture never orphans a receipt → **Test:** delete hygiene + race negatives.
5. **Step:** recording off → **Acceptance:** capture 409 (quiet, unchanged #1927); Settings shows recording-off copy (W4) → **Test:** #1927 regression.

## Task 1: First-capture trigger (hosted_api.py `_capture_session_impl`)

- After the durable 2xx receipt write block, add the trigger:
  - `legacy_mirror = bool(_get_onboarding_state(team["team_id"]).get("onboarding_complete"))`
  - `res = _os.write_completed_step(proj, team["team_id"], "capture-disclosed", status_from_mirror=legacy_mirror)` — wrapped non-fatal (additive warning like the receipt block; a checkpoint hiccup never 500s a committed capture).
  - `_maybe_apply_completion(team["team_id"])` — monotonic gate eval (no-op unless the real gate was already met; capture-disclosed never counts toward the card).
  - `first_capture = bool(res["created"])`.
- Before the receipt write, add the delete-race re-verify: `MATCH (s:Session {id:$sid}) RETURN count(s)` → if 0 (deleted mid-capture), skip the receipt write + append an additive warning (data was removed by DELETE — the receipt would be an orphan).
- Response: `resp["first_capture"] = first_capture` (+ comment: the in-conversation agent fires W2 SKILL.md §6's ONE line when true; copy lives there, never duplicated).

## Task 2: DELETE /v1/sessions/{session_id} (hosted_api.py, next to GET detail)

- `@app.delete("/v1/sessions/{session_id}")` with `Depends(get_current_team_session_ungated)` (dual-auth team-member; session membership validated in `_session_user_team`).
- Query sequence on the team graph (`_make_sdk(namespace=team["team_id"])._get_proj()`):
  1. Existence: `MATCH (s:Session {id:$sid}) RETURN s.id, s.harness` → 404 `{"detail": "Session not found"}` (matches GET detail contract) when absent.
  2. `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) DETACH DELETE p` (turn + extracted points; aboutObject edges to entities die here, entities survive).
  3. Collect provenance event ids: `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE p.eventId IS NOT NULL RETURN DISTINCT p.eventId` + `MATCH (src:Source {url:$url}) RETURN src.eventId` → delete `MATCH (e:Event) WHERE e.eventId IN $ids DETACH DELETE e`.
  4. `MATCH (src:Source {url:$url}) DETACH DELETE src`.
  5. `MATCH (s:Session {id:$sid}) DETACH DELETE s`.
- Receipt cleanup by recompute (AFTER graph removal — self-healing under the race): for each truthy receipt key in state (bare + per-harness registered keys): bucket = harness (None for bare) → count remaining Sessions (`s.harness = $h` / `s.harness IS NULL`) → zero → `_update_onboarding_state(team_id, **{key: None})` (precedent: last-error clear). Probes/last-errors untouched.
- Return `{"deleted": True}`. Graph failure handling: fail-loud 500 with retry-safe semantics (delete is idempotent).

## Task 3: Settings view/delete (W4 home consumer) + pure module

- `website/apps/dashboard/src/capturedSessions.js` (pure, node --test): `removeSession(sessions, id)` (list minus id), `sessionMeta(s)` (turns/extracted counts with defaults), `transcriptGroups(detail)` (defaults for missing turns/extracted arrays), `sessionBucketLabel`/small formatting helpers the rows need.
- main.jsx App: `deleteCapturedSession(id)` handler (confirm → DELETE via `api` with `useSession: true` + `?team_id=` → on success remove from local `sessions` via pure fn + `refreshOnboarding()` (receipts/probes may change) → error surfaced via a sessions-row error state) + wire `fetchSessionDetail` (already present) through Settings props.
- SettingsTab Home 4: per-row View + Delete buttons (confirm copy states deletion is permanent), busy/error states per row (single-flight), transcript panel (expanded inline, reusing #714 CSS classes) with turns + extracted sections; keep W4's honest empty/off states.

## Task 4: Tests

- `tests/test_onboarding_w6_capture_disclosure.py` (docker-lane, module-level skip mirroring test_onboarding_state_split.py; registry-mode registered client mirroring test_onboarding_w4_settings.py):
  - Trigger: fresh team first capture → 200 + `first_capture: true` + capture-disclosed edge visible via `onboarding_state.completed_steps`; second NEW session → false + noop; replay same session → false; off-switch (session_recording False) → 409, no edge, #1927.
  - Delete hygiene: capture (with a fixed session_id) → DELETE → 200; Session/Points/Event/Source counts zero; GET detail → 404; receipt cleared; unknown id → 404; receipt survives when a second same-harness session remains; cross-harness bucket independence.
  - Race negative: capture → DELETE → replay same session_id POST → 200 with additive warning, NO receipt re-landed, zero Session nodes (delete-during-capture no-orphan).
  - Authz: non-member session user (second registered user not in team) DELETE with `?team_id=` → 403.
- JS: `src/capturedSessions.test.js` (pure-module style like captureStatus.test.js).
- Register `test_onboarding_w6_capture_disclosure.py` in `config/ci-surfaces.yml` under `onboarding:` (comment #2002 W6).
- `tests/test_markers.py` ROUTED_NAMESPACES: only if the test file uses the literal `registry` namespace (docker-lane register fixture may — check; if used, add entry).

## Task 5: Verification + shipping

- Full local verify (docker lane subset + carve-out + node --test + ruff), vite build + commit dist if main.jsx changed, commit-workflow (registry `<worktree>::<file>`), push, PR (draft=False), record-review at final sha, enable auto-merge (MERGE).

## Review-Round Deltas (post-verification, committed before merge)

Two reviewer passes ran on the worktree diff/PR (#2180). Round-1 findings fixed:
- **P1 event-gather ordering:** `delete_session` now gathers the point-derived `sessionCaptured` `eventId`s BEFORE the CONTAINS point DETACH DELETE (the gather previously ran after the points were gone — `ev_rows` was always empty, orphaning the Event whenever the Source carried no eventId). Delete census asserts `events` 1→0.
- **P2 guarded reconcile:** receipt cleanup moved into `_reconcile_capture_receipts` (best-effort; state-read/count/clear failures skip the pass) and runs on the success path AND the 404 re-delete path — a mid-delete outage self-heals on retry (404 can no longer strand an orphan).
- **P2 bucket-aware compensation:** the capture-side post-write compensation clears the receipt ONLY when the whole harness bucket is empty (`_session_count_by_harness(body.harness) == 0`), mirroring delete-side semantics (a same-harness sibling keeps the receipt); truthful additive warnings for cleared / retained / clear-failed.
- **P2 ci-surfaces duplicate:** `test_onboarding_w6_capture_disclosure.py` was registered twice — deduped.
- **Sweep (round-2 P2-low residual):** dead-session branches also sweep THIS capture's exact-id writes (turn ids `{sid}_t{i}`, minted extracted ULIDs, the minted Event, the agentSession Source) so a delete landing mid-capture can no longer orphan post-delete point/Event/Source writes. Exact-ids only (never prefix/label-wide); replay-safe via the `minted_event` flag; guarded best-effort. The destructive queries fold the session-absence check into the same command (`OPTIONAL MATCH (s:Session {id:$sid}) … WHERE s IS NULL`) so a same-session-id re-capture racing the sweep makes it a no-op — atomic per-command across processes (round-3 review). White-box guard test intercepts + asserts all three sweep queries.

## Task Template Fields (executing-plans handoff)

- **registry:** `<worktree>::<file>` — worktree = /Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2002-W6-onboarding
- **VGATE:** standard (tier-2 — hosted_api.py NOT in the shared set; no touch of sdk.py/ep.py/exceptions.py/tool_registry.py/mcp_server.py/projection//conftest.py)
- **Branch:** feat/2002-W6-onboarding — REBASED onto origin/main (3eff9c5b) at commit time: main drifted post-base with the #2111 C2 provisioning + #2145 multi-graph epics, which moved the session endpoints onto the C2 auth model (key branches run the deleg=0 dormancy gate; capture POST stays gated key-only; list_sessions + GET detail stay on the session-ungated dependency for the dashboard surface — W6's Settings view/delete matches that split).
