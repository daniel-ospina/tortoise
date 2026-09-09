<!-- research-path: docs/research/2026-09-08-tortoise-agent-daemon.md (Second-Pass §§4-5: machine/model = future, schema-compatible) + docs/scoping/tortoise-agent-daemon.md (SD-2 identity model: harnesses are transport, human is actor; machine + model are future additive fields) -->

# #2599 — Attribution Phase 2: additive session `machine_id` + `model` fields

> **Complexity:** standard. Architecture=standard, Ontology=standard, UX=low.

**Goal:** Extend the Session node write path so each captured session records which machine it was filed from and which agent model produced it — complementing the human-actor attribution from Phase 1 (#2600, PR #2664). Additive properties on the same Session node, no new node/edge kinds, no EP-visible change. Fields surface read-only on the dashboard Memory-sources row alongside the human actor. Missing fields render as unattributed (blank/"—").

**Open Decision Resolutions (researched against issue brief + scoping docs):**

1. **Machine identifier form:** CLIENT-SUPPLIED `machine_id` — informational-only, forgeable, never security-trusted. The issue recommends hashed (SHA-256 of hostname) to avoid raw hostname info-leak in shared team graphs. On the server side, we accept a reasonable opaque string (length-capped 1-256, printable-chars sanitized), stored verbatim. The client is expected to pre-hash if privacy-sensitive — this is a CLIENT-claimed field (unlike `actor_user_id` which is server-resolved). Decision: **accept client-supplied opaque string, length+charset sanitized server-side, store verbatim. Client contract: pre-hash hostname if privacy-sensitive. Dashboard renders as-is with "—" when absent.** Rationale: follows the same informational display tier as actor (forgeable, never security-trusted), and the server cannot independently verify machine identity behind the proxy.

2. **Model capture source:** CLIENT-SUPPLIED `model` string on `SessionRequest` — the harness knows which model it's running (e.g. Claude Opus, Sonnet, deepseek-v4-flash, GPT-4o). The server does not resolve model identity. Fallback when unknown: leave unset → renders unattributed. Decision: **accept client-supplied model string (length-capped ≤128), stored verbatim, rendered read-only. No server-side model resolution or validation of model identity.** Rationale: same informational trust tier as machine_id; harnesses are the authoritative source of their model name.

3. **API/filter exposure:** Dashboard row display only (+ read path detail). No API query filter for machine_id or model (trivial to add later if needed; not needed for v1 display). Decision: **read-only display on `GET /v1/sessions` list and `GET /v1/sessions/{id}` detail. No `?machine_id=` or `?model=` filter params.** Rationale: mirroring Phase 1's actor_user_id filter was considered but rejected — actor filtering supports a real use case (find my sessions), while machine/model filtering has no v1 use case and would add complexity without demand.

**Implementation Pattern (follows #2600 additive property convention):**
- `SessionRequest` gains `machine_id: str | None` and `model: str | None` — optional, client-supplied, length-capped, charset-sanitized
- `_capture_session_impl` writes them conditionally (like `harness` clause — set only when present, coalesce for first-writer-wins on idempotent re-POST)
- `tortoise_session_capture` MCP tool passes them through to `SessionRequest`
- `list_sessions` + `get_session_detail` RETURN columns gain `s.machine_id, s.model` (appended at END same as Phase 1 appended actor_user_id and harness)
- Dashboard `sessionRowMeta` + `transcriptModel` normalize with `machine_id` / `model`; `main.jsx` renders them read-only
- No change to `_sanitize_props` or `_reject_server_managed_props` — these are client-claimed informational fields, NOT server-managed (they're explicitly allowed to be client-supplied)
- No index needed (no query filter for these fields)
- No backfill of legacy sessions — read path renders "—" when field absent

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | `SessionRequest` (hosted_api.py ~6409) | Model | In | unit | gains `machine_id: str\|None (max_length=256)` + `model: str\|None (max_length=128)`; charset validator rejects non-printable characters | malformed/overlong → field_validator 422 (same as invalid harness) |
| 2 | `_capture_session_impl` Session MERGE (hosted_api.py ~6820) | DB | Write | integration (docker) | conditional `s.machine_id=$machine_id` + `s.model=$model` WHEN body provides them (if-gated, same pattern as harness); coalesce applied for first-writer-wins on idempotent re-POST (same as actor_user_id) | docker/embedded no-unused-param preserved (conditional clause) |
| 3 | `tortoise_session_capture` (mcp_server.py ~2941) | Tool | In | integration | gains `machine_id: str\|None` + `model: str\|None` params → forwarded to `SessionRequest(...)` | pydantic 422-equivalent on invalid input |
| 4 | `list_sessions` (hosted_api.py ~8350) | DB | Read | integration | RETURN gains `s.machine_id, s.model` (appended after harness); response dict gains keys | fail-soft on missing graph → legacy null renders blank |
| 5 | `get_session_detail` (hosted_api.py ~8499) | DB | Read | integration | same RETURN append + response dict | legacy null renders blank |
| 6 | Dashboard `sessionRowMeta` (capturedSessions.js) | UX | Read | node --test | row meta gains `machine_id` / `model` with safe defaults | null/missing → '' (blank) |
| 7 | Dashboard `transcriptModel` (capturedSessions.js) | UX | Read | node --test | detail model gains `machine_id` / `model` with safe defaults | null/missing → '' (blank) |
| 8 | Dashboard `main.jsx` row + panel render | UX | Read | visual | machine_id + model displayed alongside actor span; absent → blank/"—" | overflow, truncation |
| 9 | Phase 1 regression | Integration | Both | docker E2E | E2E-3 (forged strip) + E2E-10 (fail-soft rendering) still pass — new fields never interfere | additive clause never breaks null-coalescing actor_user_id |

### Verification Plan

| # | Domain | Depth | Action |
|---|--------|-------|--------|
| 1 | code (unit) | standard | `tests/test_attribution_machine_model.py` — pure units: SessionRequest accepts valid machine_id/model, rejects overlong/non-printable; rowMeta/transcriptModel normalization |
| 2 | code (integration) | docker lane | `tests/test_attribution_machine_model.py` — E2E-style: capture session with machine_id+model → verify stored on Session node + returned on read path; absent → blank; regression on Phase 1 actor stamp |
| 3 | code (regression) | full Phase 1 suite | `tests/test_attribution_actor.py` — all 43 tests must pass unchanged |
| 4 | ux | node --test | `website/apps/dashboard/src/capturedSessions.test.js` — new tests for machine_id/model normalization |
| 5 | rebuild | dist | `cd website/apps/dashboard && npm run build` |

### Cross-repo Coordination

- **agent-infra hooks (claude-hooks, pi reflect-hook):** This issue adds the TORTOISE acceptance/persist/display path for `machine_id` + `model`. The capture hooks in agent-infra must be updated separately to SUPPLY these fields in their session POST bodies. This PR is the server-side half. The issue body notes this as a coordination item — the hooks are NOT sending these fields yet; this PR adds the pipe, and a follow-up agent-infra change will start filling it.