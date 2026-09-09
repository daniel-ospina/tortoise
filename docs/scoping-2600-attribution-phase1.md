---
title: "Issue Scoping — Attribution Phase 1 (resolver actor; session + write-event stamp)"
type: engineering
subjects.team: epistemic-team
domain: platform
doc_status: live
epic: "#2554"
issue: "#2600"
created: 2026-09-09
prereq: docs/scoping/tortoise-agent-daemon.md (approved epic scope, human gate 2026-09-08) + docs/research/2026-09-08-tortoise-agent-daemon.md + docs/research/memory-attribution-industry-findings.md
---

<!-- issue-scoping: v5.1 double diamond + verify -->

# Issue Scoping — Attribution Phase 1 (#2600)

> **Scope posture:** the epic scope doc (`docs/scoping/tortoise-agent-daemon.md`,
> APPROVED at human gate 2026-09-08) IS the approved problem+solution
> definition for Phase 1 (issue #2600 is the v1 gate). The double diamond at
> issue level therefore converges on *implementation* questions the epic left
> open (its §1.1 item-7 "open decisions" + code-reality anchors), rather than
> re-litigating SD-1/SD-2/E2E contracts. Locked decisions below are quoted
> from the epic, not re-derived.

## Confirmed Problem

Team memory cannot answer "which human filed/decided this". The auth layer
DROPS the human between credential resolution and the graph: OAuth
`resolve_oauth_access_token` (oauth.py:722) selects `user_id`/`client_id` from
`oauth_access_tokens` but returns only `_quota_fields(team)` — the actor is
discarded. Key lanes carry `created_by` (supabase_control.py:706; registry
hosted_api.py:1832) but nothing normalizes or stamps it. Captured Session
nodes store no actor (hosted_api.py:6765-6783 MERGE sets
created_at/turn_count/is_episodic + optional harness). Journaled write events
carry no human. A client can smuggle author-like props through the
create_point props passthrough (persisted verbatim, sdk.py:2461-2465).

## Verification Gates

### problem-verify: 1 cycle, clean | 0 issues remain
### solution-verify: 2 cycles, clean | 0 issues remain
- Round-1 verifier P1s fixed (controller): created_by shape gate; strip-vs-
  reject split; E2E-5 lane pinning; ContextVar-at-emit; registry MCP lane
  actor; journaled-set bound. P2/P3/P4 incorporated (EXPLAIN query shape,
  index-existence embedded assert, harness in RETURN, additive alias).
- Round-2 re-verify (fixes confirmed code-sound): P1-1 closed (apikey_verify
  composition — single normalization seam in mcp_auth middleware + REST DI
  chain, §2.1); P2s incorporated (E2E-4(a) distinct-content constraint §7;
  residual family completed §4: +SubjectAdded/ObjectRegistered + #2296
  Document; E2E-5 real-auth pin §7); P3s incorporated (§1 shape-matrix unit;
  gate session alias; _reject_server_managed_props strip site §5;
  _emit_event payload copy §2; embedded-EXPLAIN rationale corrected §6);
  line-drift P3 noted.

## Locked decisions (from epic scope — NOT re-litigated)

- **SD-2:** session→human + event-level actor backstop. NO per-node scalar
  owner (content-hash dedup merges same-content filings → owner must live on
  edges/events).
- Actor = the HUMAN. Server-side resolution only (OAuth `user_id` / key
  `created_by`). API/SDK exposes NO client-settable actor field in v1.
- Stamp BOTH the Session node AND the write-event actor field (§7.2).
- Legacy sessions/events null actor → no backfill; read paths render
  unattributed (blank/"—"), never a crash or fabricated actor.
- E2E-8 cost bound asserted via EXPLAIN (index seek + one hop), NOT wall-clock.
- Dashboard display reuses the existing Memory-sources/session row;
  display-name resolved at READ time via control-plane users lookup,
  fail-soft to id.

## Implementation Plan (converged — feeds docs/plans/attribution-phase1.md)

### 1. Resolver — return the human from every auth lane

| Lane | Code | Today | Change |
|---|---|---|---|
| OAuth `oat_` (MCP only) | `resolve_oauth_access_token` oauth.py:722 | selects user_id/client_id, returns `_quota_fields` only | return dict gains `actor_user_id` (= row `user_id` — only when UUID-shaped) + `client_id` |
| Supabase key | `resolve_api_key` supabase_control.py:503 | returns `created_by` | ADDITIVE `actor_user_id = created_by` when UUID-shaped; keep `created_by` untouched |
| Registry key (REST) | `get_current_team` hosted_api.py:1656 | returns `created_by` | ADDITIVE `actor_user_id = created_by` when UUID-shaped |
| Registry key (MCP) | `sdk.apikey_verify` sdk.py:14766 | returns team_id/key_id/delegation_depth/scopes/legacy_full_access — **NO created_by** | extend to select + return `created_by` from the APIKey node (`_verify_hashed_lookup` already returns full props — `m.get("created_by")`). Raw return; the UUID gate + `actor_user_id` alias happen at the §2.1 normalization seam, NOT in sdk.py (sdk cannot import supabase_control) |
| Session JWT | `get_current_team_session` hosted_api.py:2372 | sets `team[session_user_id]` | also set `team[actor_user_id] = user["user_id"]` (already UUID) |

**Human-shape gate (P1 fix):** `created_by` is NOT always a human identifier —
production mints produce UUID (UI/session-minted), literal `"api"` (API-minted,
hosted_api.py:5382), `'st_'||hash` (recovery, supabase_control.py:2027), EMAIL
(registry self-signup, hosted_api.py:4579). Alias `actor_user_id` ONLY when
UUID-shaped (mirror `mint_target_user_for_key`'s `_is_uuid` gate,
supabase_control.py:801); otherwise leave absent → unattributed (never a
fabricated actor). Registry selfhost EMAIL-shaped created_by also gates to
absent in v1 (selfhost teams are single-operator, no GoTrue/control-plane
users table, no session JWT; accepting raw email would put PII in
Session/GraphEvent/JSONL + a second actor shape — the loss is bounded and is a
documented residual + shape-matrix unit). Session-lane alias is ALSO gated
(`user["user_id"]` is a GoTrue UUID today — belt-and-braces uniformity).
**Additive only**: zero existing consumers of `created_by`/`session_user_id`
change.

### 2.1 Normalization seam — where `actor_user_id` lands on the dict (P1-1 fix)

Resolvers return RAW actor data (oauth `user_id`; key lanes `created_by` —
raw everywhere; `apikey_verify` gains raw `created_by`). A SINGLE UUID-gated
normalization produces the canonical `actor_user_id` key at two seams, so the
stamp-time read `team.get("actor_user_id")` is uniform on both transport
planes:

1. **mcp_auth `TeamResolutionMiddleware`** (after resolution, all 3 MCP
   lanes — oat_ / supabase key / registry `apikey_verify`):
   `team["actor_user_id"] = team.get("user_id") or (created_by if
   _is_uuid(created_by) else None)`. mcp_auth already function-imports
   supabase_control (get_control_plane/is_supabase_enabled/resolve_api_key) —
   `_is_uuid` joins the same lazy import; no sdk↔supabase_control cycle.
2. **hosted REST DI chain** (`get_current_team` registry branch,
   `_get_current_team_supabase`, `_session_user_team`/session branch): same
   normalization before the dict returns — one helper, four terminal returns
   (SKIP_AUTH and /internal/* emit no GraphEvents; /v1/session/login is
   SKIP_AUTH — no live surface missed).

Round-2 verifier composition check: oauth lane returns `user_id` raw; key
lanes `created_by` raw; `apikey_verify` returns `created_by` raw; the seams
normalize → every lane's resolved dict carries `actor_user_id` before the
ContextVar set-site reads it. A real registry selfhost key (email/st_) still
gates to absent → unattributed (FIX-1 disposition, asserted in the shape
matrix).

### 2. Actor transport — ContextVar set at the auth seams, read at emit time

**P2 fix (no SDK-instance bake):** hosted's embedded-fallback `_make_sdk`
reuses a shared keepalive anchor SDK across requests and MCP `_sdk` is
process-global — binding actor to an SDK instance would race/cross-contaminate.
Instead:

1. New module-level `_current_actor_user_id: ContextVar[str|None]` in
   `tortoise/sdk.py` (neutral home — no mcp_auth↔sdk cycle; `_emit_event` and
   `EventAPI` consumers read it).
2. Set sites (each in the request/DI context where the actor is known):
   - `TeamResolutionMiddleware` (mcp_auth.py, after resolution ~:355) — from
     the resolved team dict `actor_user_id` (all lanes incl oat_).
   - hosted `get_current_team*` dependency chain (registry REST lane, supabase
     lane, session lane) — set from the resolved dict before returning.
   - `_capture_session_impl` caller context (REST + MCP tool paths) — capture
     may run where the DI dependency chain is overridden (tests).
3. Read sites: `TortoiseSDK._emit_event` (sdk.py:2147) merges
   `actor_user_id` into (a) the `:GraphEvent` payload dict passed to
   `event_store.append_event` and (b) the JSONL event dict envelope when set.
   **Copy before mutate**: `_emit_event` assigns `graph_payload = payload`
   without copying (sdk.py:2176) — the actor merge must copy first so a
   caller-reused payload dict never gains an unexpected key.
   `EventAPI._emit` (api.py:42) carries an optional actor (None default)
   threaded at construction for extraction lanes (parity + honors the issue
   anchor — EventAPI is the extraction/mining funnel, not the hosted REST/MCP
   journal; see §4).
4. Thread caveat: SDK writes executed in a request task inherit the ContextVar.
   `asyncio.to_thread` hops do NOT (documented; hosted data-plane writes run in
   the request task).

### 3. Session stamp + index

- hosted `_capture_session_impl` Session MERGE (hosted_api.py:6765-6783):
  append `"s.actor_user_id=coalesce(s.actor_user_id, $uid)"` to `_merge_sets`
  **conditionally** (only when the team dict carries a gated actor) + bind
  `$uid` — coalesce = first-writer-wins on idempotent replay + backfills
  legacy-None on true-retry, conditional binding preserves the docker/embedded
  no-unused-param contract (mirrors the harness clause pattern).
- SDK mirror `capture_session` (sdk.py:2732, MERGE ~2924): same coalesce +
  conditional clause reading the ContextVar (embedded/local has no auth → the
  var is unset → actor stays None, byte-identical legacy shape). Keep the
  "keep the two in sync" comment.
- Index: add a `Session.actor_user_id` CREATE INDEX block to
  `projection/_ensure_indexes` (projection/__init__.py:1963, runs on every
  projection init both lanes) with the existing try/except
  "already indexed" swallow. Plain string single-prop RANGE index — #522
  embedded hazard is is_operator-BOOL-composite-specific and does NOT apply
  (mirrors embedded-safe Subject/Object/Event/Source string indexes). Extend
  tests/test_indexes.py EXPECTED_RANGE_DOCKER/EMBEDDED sets.
- NOT the capture write path (per-capture index race).

### 4. Event actor backstop — bounded to the actual journaled set

**Code reality (issue anchor corrected):** the epic says "EventAPI._emit
(api.py) is the generic funnel for ALL graph-write events". VERIFIED REALITY:
hosted REST/MCP writes journal through `TortoiseSDK._emit_event`
(sdk.py:2147) → `:GraphEvent` node (event_store.append_event, payload JSON)
for the 10 `_GRAPH_EVENT_TYPES` (sdk.py:720) + JSONL only when `event_log_path`
is set (NEVER in hosted `_make_sdk`). `EventAPI._emit` is the
extraction/mining funnel (in-memory log in capture sdk.py:3377; temp file in
MCP mine mcp_server.py:2614), NOT the hosted REST/MCP journal.

- Actor stamp at `TortoiseSDK._emit_event` (payload + JSONL envelope when the
  ContextVar is set) → the 10 store types carry the human on every lane:
  PointAdded, OperatorAdded, PointRetracted, PointSuperseded,
  OperatorAnnotated, PointPromoted, OperatorPromoted, DedupeRecorded,
  DedupeRejected, ObjectSuperseded. Operator/annotate/supersede/retract paths
  all call `_emit_event` → covered.
- `EventAPI._emit` optional actor param (None default) → extraction lanes can
  carry the human at construction; capture extraction is session-anchored
  anyway (points wired to the Session).
- **Bound the property:** "no sessionless write dangles unattributed" =
  every `:GraphEvent`-journaled write type. JSONL-only types have NO durable
  journal in hosted (event_log_path None) — a PRE-EXISTING #432/#548 design
  property, enumerated as a documented residual in the plan (not widened:
  poll/retention semantics + the type set are locked). **Complete residual
  family (P2):** PointRevised (update_point / tortoise_update_point
  mcp_server.py:1493), EventRecorded (Event-mint), ObjectRegistered
  (create_object → REST /v1/objects hosted_api.py:3358, MCP
  tortoise_create_object mcp_server.py:2176), SubjectAdded (create_subject →
  REST /v1/subjects hosted_api.py:3403, MCP tortoise_create_subject
  mcp_server.py:2168) — both entity families journal through `_create_entity`'s
  JSONL branches gated on `self._event_log_path` (sdk.py:15397-15444), absent
  from `_GRAPH_EVENT_TYPES`, so in hosted they land on the graph actor-less —
  plus the Document surface already tracked by #2296. These names must appear
  in the PR body so indicator 3 does not overclaim. Selfhost (CLI/mining,
  event_log_path set) gets the JSONL-envelope actor for all of them.

### 5. Forged-claim strip (strip-and-ignore — P1 mechanism fix)

The locked contract is STRIP-AND-IGNORE ("never a new 4xx surface"; E2E-3
tests "today's strip-and-ignore reality"). The existing `_sanitize_props`
(sdk.py:785) and `_SERVER_MANAGED_PROPS` (mcp_server.py:712) RAISE/reject —
extending them literally would make forged claims 4xx → E2E-3 RED.

- **New strip list** (pop + warn, never raise, never stored): reserved actor
  keys `actor_user_id`, `owner`, `initiated_by` (and `agent_id` on tenant
  surfaces that expose it) — dropped from props in `_sanitize_props` BEFORE the
  reject check AND inside the single shared MCP boundary helper
  `_reject_server_managed_props` (mcp_server.py:717, the choke point every
  props-accepting tool already calls — strip first, then the existing
  `_SERVER_MANAGED_PROPS` reject). Naming the choke point removes the
  per-tool-strip miss risk. The reject lists stay for the FUTURE
documented-client-field contract (per epic §1.1 item 3 note).
  Decided: `actor_user_id` INTENTIONALLY rides `_emit_event(**extra)` JSONL
  replay (rebuild restores the original payload actor); it is NOT added to the
  rebuild replay skip-set. (Reserved keys stripped from tenant props never
  reach an emit; the emit-time actor comes from the ContextVar, not props.)
- **authoredBy:** PRE-EXISTING client author-label (diary path sdk.py:10613,
  MCP tool param mcp_server.py:759, entity AUTHORED_BY edges sdk.py:15558).
  NOT stripped in v1 — it is a self-label (agent/participant name), never
  consulted by the "memory filed by X" read path, and the server cannot derive
  agent self-names. **Documented residual** (P1): a member can still set
  authoredBy on a point/entity today (it persists as a node prop + wires
  AUTHORED_BY edges on entity surfaces); v1 attribution (E2E-3 negative)
  targets the canonical actor keys, and the residual is recorded so the
  value-map "cannot forge authorship" claim is bounded to the attribution
  field. REST surfaces have NO props passthrough (CreatePointRequest is
  content/kind/tags/about_object/dedup only — forged keys die at the Pydantic
  boundary) — the strip list is for MCP props + SDK-direct callers.

### 6. Read path — list_sessions/get_session_detail

- list_sessions RETURN gains `s.actor_user_id` + `s.harness` (the §1.1 item-2
  "which human + which tool" pair); row dict gains `actor_user_id`, `harness`,
  and `actor_display` (resolved READ-time via control-plane users lookup —
  `_gotrue_admin_get_user` hosted_api.py:4704 or membership-seam email —
  fail-soft to raw id on lookup miss; null actor → `actor_display: null`
  → client renders "—"). get_session_detail same (+ actor in detail).
- Optional actor filter: `GET /v1/sessions?actor_user_id=<uuid>` (GET has no
  body → query-string param). Filter excludes sessions whose actor is
  null/missing (explicit sub-assertion — no leak of legacy rows as
  "included").
- E2E-8 EXPLAIN assert: `g.explain("MATCH (s:Session {actor_user_id:$u})"
  " RETURN s.id")` → "Node By Index Scan" present AND "All Node Scan" absent.
  Query shape is FILTER-FIRST (no ORDER BY in the probe — tiny seeded graph
  could otherwise legitimately label-scan+sort; assert on the filtered
  subquery). **Rationale correction (P3):** test_indexes.py RUNS EXPLAIN on
  the embedded `proj` fixture and asserts scan-shape tokens
  (test_labeled_lookups_are_index_scans, test_indexes.py:343-393) — embedded
  redislite EXPLAIN DOES emit meaningful plans; the docker-only pin is a CI-
  lane-standard choice, not an embedded limitation. Run the EXPLAIN assert on
  the docker lane (standard) and mirror the index-existence assertion on both
  lanes via `CALL db.indexes()` (extend EXPECTED_RANGE_DOCKER + EMBEDDED sets
  in test_indexes.py; subset-assert direction → the extension is a coverage
  requirement, not RED-avoidance).
- Dashboard: server response fields are the pytest-testable core
  (tests/test_hosted_api.py). The minimal row-label consumption
  (main.jsx:463-491 + capturedSessions.js sessionRowMeta) + node --test case
  is the §1.1-item-2 "reuse" — E2E-10 asserted at the API/integration level
  across its three cases (attributed / legacy-null / unknown-id) + the
  row-label unit.

### 7. E2E-5 dedup — lane pinned (P1 fix) + E2E-4(a) content constraint

E2E-5 ("two humans, same claim, BOTH actors resolvable") is pinned to the
**capture/session lane on the REAL hosted auth faces** (NOT the DI-override
seam — overrides bypass the DI body so no ContextVar is set; both members must
authenticate via real session JWTs or distinct keys with distinct UUID
creators, else both captures emit actor=None and the assertion is red for the
wrong reason). Mechanism (verified at the v2 seam sdk.py:3611-3721 +
3683-3690): two members capture distinct session_ids → two Session nodes each
stamped with their actor (coalesce = first-writer-wins per id); the second
member's same-content claim resolves the canonical by content_hash+kind BEFORE
create (dedup=content_hash_hit — member 1's provenance stays), then
`MERGE (session2)-[:CONTAINS]->(canonical)` fires for dedup-hit points too →
one canonical pt wired CONTAINS from both sessions, no single-owner scalar.
Test pins the **v2** extractor seam (M2 is non-default and deliberately
cross-session-excluding). Assertion surface (exact): per-session
`actor_user_id` (via list_sessions/get_session_detail) + each session's own
turn-point PointAdded events + sessionCaptured Event — NOT a PointAdded
GraphEvent carrying member 2's actor on the shared canonical claim (the fold
emits nothing; only member 1's PointAdded exists for that claim).

**E2E-4(a) test-design constraint (P2):** the sessionless lane-matrix test
(a oat_ / b1 supabase / b2 registry) must use DISTINCT content per lane (or
assert the first write's PointAdded event) — MCP `tortoise_create_point`
defaults `dedup=True` (mcp_server.py:759-762), so a same-content second lane
write is a silent no-op and the oat_ event-backstop indicator is never
genuinely exercised.

**Documented residual:** a SESSIONLESS second identical filing with dedup=True
resolves to the existing canonical; no PointAdded/:GraphEvent, no actor trace
(create_point dedup-hit path sdk.py:2353-2411 calls update_point when extra
props were passed — emitting JSONL-only PointRevised, never a store event —
then returns the canonical). No-trace is the create_point no-op contract for
the durable journal; stated explicitly so E2E-5 is never asserted against the
sessionless dedup-hit.

## Rejected Alternatives

| Alternative | Why rejected |
|---|---|
| Per-node `owner` prop | SD-2 locked (industry audit + content-hash dedup structural fact) |
| Actor baked onto SDK instance | Shared keepalive anchor + process-global MCP `_sdk` → cross-request contamination (verifier P2) |
| Extend `_sanitize_props`/`_SERVER_MANAGED_PROPS` reject lists for actor keys | Reject = new 4xx surface; E2E-3 locks strip-and-ignore (verifier P1) |
| Capture-write-path index ensure | Per-capture race; EXPLAIN runs on a read-only projection that may never have captured |
| Widen `:GraphEvent` store membership (PointRevised etc.) | #432/#548 type set + poll/retention semantics locked; residual documented instead |
| Strip authoredBy in v1 | Breaking pre-existing diary/author-label semantics; not consulted by the read path |

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| oauth.py resolver | auth | Task 1 (unit) | ✅ |
| supabase_control.resolve_api_key | auth | Task 1 (unit) | ✅ |
| hosted_api get_current_team* (reg/supabase/session) | auth | Task 1 (unit) | ✅ |
| sdk.apikey_verify (registry MCP lane) | auth | Task 1 (unit) | ✅ |
| mcp_auth middleware ContextVar | auth/MCP | Task 2 (unit) | ✅ |
| Session MERGE (hosted + SDK mirror) | graph write | Task 3 (integration) | ✅ |
| projection _ensure_indexes + test_indexes.py | index | Task 3 (integration) | ✅ |
| TortoiseSDK._emit_event + event_store payload | journal | Task 4 (integration) | ✅ |
| EventAPI._emit optional actor | journal (extraction) | Task 4 (unit) | ✅ |
| _sanitize_props strip list + MCP boundary | security | Task 5 (unit) | ✅ |
| list_sessions / get_session_detail | read path | Task 6 (integration + EXPLAIN) | ✅ |
| Dashboard row label + sessionRowMeta | UX (reuse) | Task 6 (node --test) | ✅ |
| Regression tt_/tk_/oat_ auth | regression | Task 8 (full pytest) | ✅ |

## Complexity

| Domain | Rating | Rationale |
|--------|--------|-----------|
| Architecture | standard | Resolver return-shape + ContextVar threading through shared auth middleware + capture + event journal + read filter — careful, test-heavy, bounded, fully specified by epic |
| Ontology | low | Two additive Session/event fields + one index; no node/edge kinds |
| UX | low | Read-only row-label reuse (server fields are the core) |
| Research | low | Design research complete (epic briefs); bounded code-reality verification done |

## Review Cycle Log

### solution-verify — Cycle 1
- Verifier A: P0=0, P1=2 (created_by shape gate; strip-vs-reject), P2=2, P3=4
- Verifier B: P0=0, P1=3 (E2E-5 dedup no-trace; authoredBy residual + strip-vs-
  reject; journaled-set bound), P2=2, P3=2, P4=1
- Controller: Fixed P1-A1 (shape gate §1), P1-A2 (strip-and-ignore §5),
  P1-B1 (E2E-5 lane pin §7), P1-B2 (authoredBy residual documented §5),
  P1-B3 (journaled-set bound §4); Ignored none. P2 incorporated (§2 ContextVar,
  §1 apikey_verify, §5 mechanism note); P3/P4 incorporated (§6 EXPLAIN shape,
  index-existence embedded assert, harness RETURN, additive-alias pin, 60s-cache
  no-work note).

### solution-verify — Cycle 2 (fixes re-verified)
- Verifier A: P0=0, P1=1 (apikey_verify composition — §2.1 seam), P2=2, P3=5
- Verifier B: P0=0, P1=0, P2=2, P3=1, P4=1 (all four fixes materially sound)
- Controller: Fixed P1-A1 (§2.1 normalization seam — raw created_by from
  apikey_verify + UUID-gated alias at middleware + REST DI seams; verified
  no sdk↔supabase_control import cycle). P2s incorporated (§7 E2E-4(a)
  distinct-content constraint; §7 E2E-5 real-auth pin; §4 residual family
  completed +SubjectAdded/ObjectRegistered/Document-#2296). P3/P4 incorporated
  (§1 shape-matrix unit incl registry-email disposition; gate session alias;
  §5 _reject_server_managed_props choke-point; §2 payload copy;
  §6 embedded-EXPLAIN rationale corrected). P3 line-drift noted for the plan
  (Session MERGE ~6795; SDK mirror ~2957; _SERVER_MANAGED_PROPS at 713).
- Re-dispatch: Cycle-2 P1-1 fixed with code evidence (import graph verified:
  mcp_auth already function-imports supabase_control; sdk.apikey_verify
  returns raw created_by; gate lives in the seams).
