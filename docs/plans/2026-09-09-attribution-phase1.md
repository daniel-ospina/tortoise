<!-- research-path: docs/scoping/tortoise-agent-daemon.md (epic #2554, approved 2026-09-08) + docs/research/2026-09-08-tortoise-agent-daemon.md + docs/research/memory-attribution-industry-findings.md + docs/scoping-2600-attribution-phase1.md (issue-scoping comment on #2600) -->

# #2600 — Attribution Phase 1: resolver human actor; Session + write-event stamp

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the hosted graph answer "which human filed/decided this" — thread the server-resolved human actor through every auth lane, stamp it onto captured Session nodes and journaled write-events, add the read-path actor filter + display, and strip forged client claims (epic #2554 v1 gate).

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Resolver threading + ContextVar backstop + additive stamps, all fail-soft:
- **Resolvers return RAW actor data** (oauth `user_id`; key lanes `created_by` — already returned by `resolve_api_key` AND already carried as `"created_by": created_by` on the registry branch dict at hosted_api.py:1830, missing only from `apikey_verify`); a **single UUID-gated normalization seam** produces the canonical `actor_user_id` dict key on both transport planes (§2.1 of the issue-scoping doc): `mcp_auth.TeamResolutionMiddleware` (after resolution, all 3 MCP lanes) and the hosted REST DI chain at its **three terminal dict-BUILD sites** (code-verified): (1) `get_current_team` registry branch (~1830 — dict built inline with `created_by`), (2) `_get_current_team_supabase` (wraps `resolve_api_key`), (3) the session branch of `get_current_team_session`/`_ungated` (~2432), where `user["user_id"]` is attached as `session_user_id` AFTER `_session_user_team` returns a dict carrying neither `user_id` nor `created_by` (the key branches of `get_current_team_session` DELEGATE to `get_current_team` → sites 1/2 already; the DI-override seam and the SKIP_AUTH return are NOT normalized — test seam + no-GraphEvent surface). Non-UUID `created_by` shapes (`"api"`, `'st_'||hash`, EMAIL) gate to ABSENT → unattributed, never a fabricated actor.
- **ContextVar `_current_actor_user_id`** (module-level in `sdk.py`, neutral home — no mcp_auth↔sdk cycle) is SET at two data-plane set sites — MCP: `TeamResolutionMiddleware` dispatch ContextVar block (AFTER the cache-hit/cache-miss if/else, so warm-cache hits also set — verified mcp_auth.py:355-365 is the converged block after the branch); REST: inside `_data_sdk(team)` (hosted_api.py:2254) **CONDITIONALLY — only when the dict carries `actor_user_id`, never erasing a value the auth seams set** (cycle-2 P0: the MCP capture tool hand-builds an actor-less team dict, so an unconditional set would wipe the middleware-set actor before the Session MERGE/emits; the tool also threads `team["actor_user_id"] = _current_actor_user_id.get()` per Task 1 (b)). READ at `TortoiseSDK._emit_event` (the journaled 10-type store funnel) + `capture_session` (SDK mirror). asyncio.to_thread caveat documented (hosted writes run in the request task; display-name lookups use to_thread safely because they read rows, not the request actor).
- **Session stamp:** both `_capture_session_impl` (hosted REST + MCP capture) and the SDK-mirror `capture_session` append `s.actor_user_id=coalesce(s.actor_user_id,$uid)` **conditionally** (coalesce = first-writer-wins on idempotent replay; conditional binding preserves the docker/embedded no-unused-param contract — mirrors the `harness` clause pattern). Plus `Session.actor_user_id` CREATE INDEX in `projection._ensure_indexes` (both lanes) + `test_indexes.py` EXPECTED_RANGE set extension.
- **Event backstop:** `_emit_event` merges the ContextVar actor into the `:GraphEvent` payload (copied first — never mutate a caller-reused dict) + the JSONL envelope when set; `EventAPI._emit` gains an optional `actor` (None default) for extraction lanes. Bounded to the 10 `_GRAPH_EVENT_TYPES`; JSONL-only types (PointRevised/EventRecorded/ObjectRegistered/SubjectAdded/BatchIdStamped) are a documented residual (no durable hosted journal — pre-existing #432/#548 design).
- **Strip-and-ignore (never reject):** reserved actor keys `actor_user_id`/`owner`/`initiated_by` (+`agent_id` on tenant surfaces) are POPPED + warned in `_sanitize_props` (before the reject check) and in `_reject_server_managed_props` (the single MCP boundary choke point, strip-first then existing reject).
- **Read path:** `list_sessions`/`get_session_detail` RETURN gains `s.actor_user_id`, `s.harness`, `actor_display` (READ-time, ONE `team_members` fetch per request → id→email map; fail-soft to raw id; null actor → null). Optional `GET /v1/sessions?actor_user_id=<uuid>` filter. Dashboard reuses the session row + transcript header (minimal label change, node --test).

**Pattern Research:** Skipped — zero third-party deps (writing-plans skip rule). All mechanisms verified against in-repo precedents at scoping: coalesce-conditional clause pattern = hosted `harness` clause (hosted_api.py:6766-6781); `_is_uuid` gate = `mint_target_user_for_key` (supabase_control.py:801); strip-vs-reject = #329 `_sanitize_props` + #1486 `_SERVER_MANAGED_PROPS`; ContextVar auth pattern = `TeamResolutionMiddleware` (`_current_team_id` etc., mcp_auth.py:38-56); index-ensure pattern = existing try/except CREATE INDEX blocks in `projection._ensure_indexes` (projection/__init__.py ~1963) + `test_indexes.py` EXPECTED_RANGE_DOCKER/EMBEDDED sets; EXPLAIN-pin convention = `test_indexes.py:343-393`. Ranges were code-verified at scoping (line drift noted: Session MERGE hosted ~6795, SDK mirror ~2957, `_SERVER_MANAGED_PROPS` ~713).

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | `resolve_oauth_access_token` (oauth.py:722) | Auth | Out | unit (tests/test_oauth*.py) | returns team dict + RAW `user_id`; token select already carries user_id/client_id — return must not drop it | oat_ key keeps authenticating; dict additive — no key removed |
| 2 | `resolve_api_key` (supabase_control.py:503) | Auth | Out | unit (test_supabase_control.py) | already returns `created_by` raw (verify + pin) | shape gate must not change existing return |
| 3 | `apikey_verify` (sdk.py:14766) | Auth | Out | unit | gains RAW `created_by` (from `m.get("created_by")` — `_verify_hashed_lookup` returns full props) | registry lane no longer actor-less; selfhost no regression |
| 4 | mcp_auth `TeamResolutionMiddleware` | Auth | Both | unit/integration | normalizes `actor_user_id` = user_id or UUID-gated created_by after ALL 3 lanes; sets `_current_actor_user_id` ContextVar | lane dicts w/o actor → None (unattributed not fabricated); 60s-LRU cache still returns normalized dicts |
| 5 | hosted REST DI (`get_current_team` registry branch ~1830, `_get_current_team_supabase`, session branch of `get_current_team_session`/`_ungated` at ~2432 where session_user_id attaches; key branches delegate to site 1) + `_data_sdk` ~2254 sets the ContextVar | Auth | Both | unit | `_alias_actor_user_id` on the 3 terminal builds + ContextVar set in `_data_sdk` (single data-plane set) | SKIP_AUTH + /internal/* surfaces emit no GraphEvents — no live surface missed |
| 6 | `_capture_session_impl` Session MERGE (hosted_api ~6795) | DB | Write | integration (docker) | conditional `s.actor_user_id=coalesce(s.actor_user_id,$uid)` appended when `team.get("actor_user_id") or _current_actor_user_id.get()` (MCP capture tool builds its dict manually — ContextVar fallback covers it) | idempotent re-POST keeps FIRST actor; cross-actor re-POST keeps first; docker/embedded no-unused-param |
| 7 | SDK mirror `capture_session` MERGE (sdk.py ~2957) | DB | Write | unit (both directions) | same coalesce clause reading the ContextVar; parity code exercised positive+negative | embedded/local (no auth, var unset) → byte-identical legacy shape |
| 8 | `projection._ensure_indexes` + `test_indexes.py` | DB/index | Both | integration | `Session.actor_user_id` RANGE index both lanes; EXPECTED_RANGE_DOCKER/EMBEDDED + docker composite-set parity | index idempotent; EXPLAIN shows index seek (E2E-8) |
| 9 | `TortoiseSDK._emit_event` (sdk.py:2147) | Event | Out | integration | actor merged into `:GraphEvent` payload (payload COPIED first) + JSONL envelope when ContextVar set | caller-reused payload dict never gains unexpected key; JSONL shape additive |
| 10 | `EventAPI._emit` (api.py:42) | Event | Out | unit | optional `actor=None` param threaded at construction | extraction lanes default None → no behavior change |
| 11 | `_sanitize_props` (sdk.py:785) + `_reject_server_managed_props` (mcp_server.py:717) | Security | In | unit | pop+warn reserved actor keys BEFORE reject check at both seams | forged claim stripped not 4xx (E2E-3); store never carries client actor |
| 12 | `list_sessions`/`get_session_detail` (hosted_api.py:8297/8333) | DB | Read | integration + EXPLAIN | RETURN s.actor_user_id, s.harness; row gains actor_user_id/harness/actor_display (one team_members fetch/request, to_thread); optional ?actor_user_id filter; filter EXCLUDES null-actor rows | fail-soft on missing graph; display resolution failure → raw id; never N+1 |
| 13 | Dashboard session row + transcript panel (main.jsx:463-491 + transcript header, capturedSessions.js) | UX | Read | node --test | sessionRowMeta + transcriptModel normalize actor_display/actor_user_id; row + panel render when non-empty | legacy null → blank/"—"; no crash |

**Single-set-site invariant (cycle-3):** the actor ContextVar is set ONLY by `_data_sdk` (REST) + the middleware converged block (MCP). Verified: every REST store-journaling write today opens via `_data_sdk` (create_point hosted_api.py:3437, capture 6649/6715, commit_session ~8188, entity creates 3376/3415, delete_session 8529). Direct-`_make_sdk` data writers (onboarding_seed 16781, public_demo 17013, import_team 12927) do NOT store-journal (no `_emit_event`/`_GRAPH_EVENT_TYPES` call in hosted_api.py) → out of scope — but the invariant is load-bearing: ANY future direct-`_make_sdk` handler that calls a store-journaling SDK method would emit actor=None silently. REST exposes no supersede/annotate/retract/operator endpoints (MCP/SDK-only — served by the middleware ContextVar). The conditional `_alias_actor_user_id`-gated set at the DI terminals is NOT needed for any current journaling surface — the single-`_data_sdk` design stands.

### Verification Plan

**Domain(s):** code (+ minimal ux read-only). **Complexity:** Architecture=standard, Ontology=low, UX=low, Accessibility=low.

| # | Skill | Depth | Reason |
|---|-------|-------|--------|
| 1 | test-writing | standard | unit layers: resolver returns, shape gate, strip seams, EventAPI actor, payload-copy |
| 2 | test-integration | full (docker lane) | Session stamp, index EXPLAIN, event backstop, read filter, E2E-3/4/5/6/8/10 cases |
| 3 | ux-verification | light | dashboard row label is server-field consumption; node --test unit covers derivations |
| 4 | code-review | standard | via commit-workflow at PR time |

**Skipped:** test-e2e (no browser journey changed — server fields are the pytest-testable core), full pytest regression (Task 7 runs the suite on the docker lane).

---

## Task 1: Resolver RAW actor returns + UUID-gate normalization seams + ContextVar

**Files:**
- Modify: `tortoise/oauth.py:722` (`resolve_oauth_access_token`)
- Modify: `tortoise/sdk.py:14766` (`apikey_verify`)
- Modify: `tortoise/mcp_server.py:2949-2960` (`tortoise_session_capture` — thread `team["actor_user_id"] = _current_actor_user_id.get()`; Task 1 Step 4(b) OWNS this edit, its test lands in Task 2)
- Modify: `tortoise/mcp_auth.py:127` (`TeamResolutionMiddleware` + ContextVars + set-site)
- Modify: `tortoise/hosted_api.py` (REST DI terminal returns)
- Modify: `tortoise/supabase_control.py` (verify `resolve_api_key` returns raw created_by — no change expected)
- Modify: `tortoise/sdk.py` (module-level `_current_actor_user_id: ContextVar[str|None]` + shape helper + `_alias_actor_user_id`)
- Test: unit tests in existing auth suites (`tests/test_supabase_control.py`, `tests/test_oauth_mcp.py`, `tests/test_mcp_server_auth_modes.py`, `tests/test_hosted_auth.py`)

**Step 1 — Shape-gate helper + ContextVar (sdk.py).**

Add to `tortoise/sdk.py` module level (near the top import block — `from contextvars import ContextVar` already needed or add):

```python
# #2600: server-resolved human actor for session + event stamps. Set ONLY at
# auth seams (never from client input). sdk.py is the neutral home — both
# mcp_auth and hosted_api already import sdk; sdk must not import them.
_current_actor_user_id: ContextVar[str | None] = ContextVar(
    "_current_actor_user_id", default=None)


def _is_uuid_shape(value: object) -> bool:
    """UUID-shaped string gate for actor aliasing (#2600). created_by is NOT
    always a human id: literal 'api', 'st_'||hash recovery mints, registry
    emails all exist. Alias to actor_user_id ONLY for UUID shapes.
    Byte-copies supabase_control._is_uuid semantics (the #1738 class) so the
    gate matches Postgres uuid acceptance on BOTH planes: brace-strip, reject
    urn:/uuid: prefixed forms, accept hyphenated / 32-hex-no-hyphen / braced.
    (sdk cannot import supabase_control — this is the neutral copy.)"""
    import uuid as _uuid
    if not isinstance(value, str) or not value:
        return False
    probe = value.strip()
    if probe.startswith("{") and probe.endswith("}"):
        probe = probe[1:-1].strip()
    if probe.startswith(("urn:", "uuid:")):
        return False
    try:
        _uuid.UUID(probe)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _alias_actor_user_id(team: dict) -> dict:
    """#2600: canonical actor_user_id key on a resolved team dict — UUID-gated,
    never fabricated. Reads RAW resolver fields (user_id / created_by /
    session_user_id — each lane carries whichever applies) and aliases to
    actor_user_id ONLY when UUID-shaped. Additive: never removes a key.
    Shared by mcp_auth.TeamResolutionMiddleware and hosted_api REST DI
    terminals (single implementation, both planes)."""
    raw = team.get("user_id") or team.get("created_by") or team.get(
        "session_user_id")
    if _is_uuid_shape(raw):
        team["actor_user_id"] = raw
    return team
```

Note: keep `_is_uuid` in supabase_control.py untouched (used by key-mint paths); `_is_uuid_shape` + `_alias_actor_user_id` in sdk.py are the neutral gate+alias consumers on both planes import (mcp_auth already imports sdk; hosted_api too — no cycle). Verify no name collision (grep).

**Step 2 — oauth resolver returns RAW `user_id`.**

`resolve_oauth_access_token` (oauth.py:722): the token row select already carries `user_id`/`client_id`. Change the terminal return to include raw actor fields:

```python
    team = _quota_fields(cp, team)
    team["user_id"] = row.get("user_id")
    team["client_id"] = row.get("client_id")
    return team
```

Docstring: "additive — the normalized `actor_user_id` alias is produced by the consuming seam (mcp_auth middleware / REST DI), NOT here (keeps resolvers transport-agnostic)."

**Step 3 — apikey_verify returns RAW `created_by`.**

`apikey_verify` (sdk.py:14766): the `matches[0]` dict carries full props from `_verify_hashed_lookup`. Extend the return dict with `"created_by": m.get("created_by")` (raw — UUID gate at the seam). Add `_is_uuid_shape` import-adjacent note that the alias happens in mcp_auth (sdk cannot import supabase_control).

**Step 4 — REST DI normalization + ContextVar set (hosted_api.py).**

The session lane's raw actor exists ONLY as `session_user_id`/`user["user_id"]`, attached at `get_current_team_session` (hosted_api.py:2432) AFTER `_session_user_team` returns — so `_session_user_team` itself is NOT a normalization site (its team dict carries neither `user_id` nor `created_by`). The three terminal dict-BUILD sites are: (1) `get_current_team` registry branch (dict built inline at ~1830 — ALREADY carries `"created_by": created_by`; `_alias_actor_user_id` reads it), (2) `_get_current_team_supabase` (wraps `resolve_api_key`), (3) **the session branch of `get_current_team_session`/`_ungated`** — right where `team[_SESSION_USER_ID_KEY] = user["user_id"]` runs (~2432), the helper's `session_user_id` read picks it up. `get_current_team_session`'s key branches DELEGATE to `get_current_team` (verified: returns `await get_current_team(request)` at ~2396) → sites 1/2 already cover them. The DI-override seam is intentionally NOT normalized (tests inject raw dicts; scoping §7 forbids it for E2E-5).

At each of the three sites call the shared helper right before the terminal `return team`:

```python
from tortoise.sdk import _alias_actor_user_id
...
    team = _alias_actor_user_id(team)   # additive actor_user_id (UUID-gated)
    return team
```

The ContextVar set is NOT done here — it lives in `_data_sdk` (below) so every REST data-plane write sets it exactly once, in the request task, on the SDK-open path.

**REST ContextVar set site (cycle-2 P0 — MUST be non-destructive):** the guaranteed single set site for every REST data-plane write is `_data_sdk(team)` (hosted_api.py:2254). **But an unconditional `.set()` CLOBBERS the actor on the MCP capture path**: `tortoise_session_capture` (mcp_server.py:2949-2960) hand-builds an actor-less team dict and calls `_capture_session_impl(body, None, team)` → `_data_sdk(team)` runs ~100 lines BEFORE the Session MERGE — an unconditional `set(None)` would erase the middleware-set ContextVar for the Session stamp AND every capture-emitted `_emit_event`. Two-part fix:

**(a) Conditional set in `_data_sdk` — never erase a request-scoped value:**

```python
def _data_sdk(team: dict) -> TortoiseSDK:
    # #2600: set ONLY when the dict carries an actor — never erase a value
    # the auth seams set (the MCP capture tool's hand-built dict has no
    # actor_user_id; its actor lives in the ContextVar).
    if team.get("actor_user_id") is not None:
        _current_actor_user_id.set(team["actor_user_id"])
    ...
```

**(b) Thread the actor into the MCP capture tool's team dict** (mcp_server.py:2949-2960) — the tool already reads sibling ContextVars (`_current_graph_id`/`_current_scopes`, etc.) at ~2928-2955; add `team["actor_user_id"] = _current_actor_user_id.get()` so the dict is self-describing and the Session MERGE/event stamps never depend on the asyncio.run context bridge:

```python
    team = {"team_id": team_id, "tier": limits.get("tier", "free"),
            "key_id": None}
    team["actor_user_id"] = _current_actor_user_id.get()  # #2600
```

Both together: REST-key writes are set by (a) (dict normalized at DI); MCP captures ride the ContextVar into the dict via (b), so (a) re-sets the same value (no-op). The MCP-capture END-TO-END test is owned in Task 2 Step 5 (Session-stamp assert, satisfiable there) and Task 3/6 (journaled PointAdded assert once Task 3's `_emit_event` merge lands) — NOT here; this step changes code only.

Keep `session_user_id`/`auth_lane` keys untouched (additive alias only).

**Step 5 — MCP middleware normalization + ContextVar set (cache-hit safe).**

`TeamResolutionMiddleware.dispatch` resolves in the cache-hit/miss if/else (mcp_auth.py:200-250) and then has a CONVERGED ContextVar block AFTER the branch (355-365, where `_current_team_id.set(...)` etc. run). Normalize + set the actor in THAT converged block (not inside the miss branch) so warm 60s-cache hits also set it — the cached dict carries the same RAW `user_id`/`created_by` fields:

```python
from tortoise.sdk import _alias_actor_user_id, _current_actor_user_id  # function-level import near the supabase_control lazy imports
...
# in the converged ContextVar block (after the if/else):
team = _alias_actor_user_id(team)
_current_actor_user_id.set(team.get("actor_user_id"))
```

Applies to ALL three MCP lanes (oat_ via resolve_oauth_access_token, supabase via resolve_api_key, registry via apikey_verify) AND the SELFHOST branch (~406-412, set None).

**Step 6 — Unit tests (write-first per surface).**

- oauth resolver returns `user_id`/`client_id` present in the resolved dict (and every pre-existing key still present — additive).
- apikey_verify returns `created_by` (seed a registry APIKey node with a UUID created_by; assert raw return; seed email/`st_` creator → raw returned, seam gates it).
- `_is_uuid_shape` matrix (matches supabase_control._is_uuid acceptance): hyphenated UUID / 32-hex-no-hyphen / braced `{...}` → True; `urn:uuid:...` / `uuid:...` prefixed → False; `"api"` / `"st_abc"` / email / None / non-str → False.
- `_alias_actor_user_id`: dict with `user_id` → `actor_user_id` set; `created_by` UUID → set; `session_user_id` UUID → set (session-lane shape); `created_by="api"`/email/urn → absent; no raw fields → absent, no KeyError; pre-existing keys never removed.
- REST DI (per seam, tests/test_hosted_auth.py): each of the three terminal dict-build sites returns a dict with `actor_user_id` when its raw field is UUID (absent when non-UUID/absent) — assert the DICT ONLY (the ContextVar is NOT set at the DI terminals by design). ContextVar assertions live in the `_data_sdk` unit: team dict WITH `actor_user_id` → ContextVar set to it; WITHOUT → ContextVar UNCHANGED (conditional set — never erased; cycle-2 P0).
- Middleware: resolve each lane (oat_ / supabase key / registry) against the fixture CP + registry SDK → `_current_actor_user_id` set to the UUID actor; non-UUID creator → None. **Cache-hit pin:** dispatch the SAME token twice within the 60s TTL → actor ContextVar set identically on the second (warm) dispatch; then dispatch a SECOND credential immediately after → var carries the second actor (no stale-actor leak across sequential tool calls in one transport session).
- Regression: existing auth-mode suites pass unchanged.

**Step 7 — Commit.** `feat(attribution): resolver raw actor + normalization seams + actor ContextVar`

## Task 2: Session stamp + Session.actor_user_id index

**Files:**
- Modify: `tortoise/hosted_api.py:6795` (`_capture_session_impl` Session MERGE)
- Modify: `tortoise/mcp_server.py:2949-2960` (**no code change in Task 2 — Task 1 Step 4(b) owns the threading edit; Task 2 tests the MCP capture lane only**)
- Modify: `tortoise/sdk.py:2957` (SDK mirror `capture_session` MERGE)
- Modify: `tortoise/projection/__init__.py:~1963` (`_ensure_indexes`)
- Modify: `tests/test_indexes.py` (EXPECTED_RANGE_EMBEDDED + DOCKER)
- Test: integration in `tests/test_capture_session.py` + `tests/test_hosted_api.py`

**Step 1 — Session MERGE actor clause (hosted).**

In `_capture_session_impl` (hosted_api.py ~6790), the `_merge_sets`/`_merge_params` build already handles the conditional `harness` clause (6766-6781). Extend identically, reading the actor from the team dict AND falling back to the ContextVar (the MCP capture tool builds its team dict manually and never runs the REST DI terminals — mcp_server.py:2952-2960; the ContextVar IS set by the middleware in that path):

```python
    actor_uid = team.get("actor_user_id") or _current_actor_user_id.get()
    if actor_uid:
        _merge_sets.append("s.actor_user_id=coalesce(s.actor_user_id, $uid)")
        _merge_params["uid"] = actor_uid
```

(import `_current_actor_user_id` from tortoise.sdk at function level next to the local `import uuid` block). The base sets gain NO unconditional actor (docker/embedded no-unused-param contract preserved). The coalesce form is REQUIRED (scoping §3): idempotent re-POST keeps the FIRST writer's actor when a re-POST resolves a different actor, and backfills legacy-None on true-retry (scoping §7 + failure-mode P1-1 — a legacy null-actor session re-POSTed by member B gets B backfilled onto the Session node; the session's legacy turns stay null at the EVENT level, so the `?actor_user_id` filter matches the session as B's — explicitly pinned in Step 5). #1727 replay still emits zero new nodes (MERGE is a no-op SET).

**Step 2 — SDK mirror (sdk.py capture_session ~2957).**

Same conditional clause, reading the ContextVar (parity for external SDK consumers that run their own auth; in-repo hosted paths all delegate to `_capture_session_impl`, so this is parity-future-proofing — see Step 5 for the positive test):

```python
    _actor = _current_actor_user_id.get()
    if _actor:
        _merge_sets.append("s.actor_user_id=coalesce(s.actor_user_id, $uid)")
        _merge_params["uid"] = _actor
```

Embedded/local (var unset) → sets unchanged → byte-identical legacy shape. Keep the "Keep the two in sync" comment updated.

**Step 3 — Index in `_ensure_indexes`.**

`_ensure_indexes` (projection/__init__.py ~1963) has **NO existing Session label block** (verified — only Point/Document/entity-label string blocks at 1996-2129). Add a new Session block mirroring the plain-string single-prop RANGE index pattern (Subject/Object/Event/Document precedents — NOT the #522 is_operator composite; the sibling try/except "already indexed" swallow shape is at ~1996):

```python
    # #2600: actor-filtered session reads (list_sessions?actor_user_id) —
    # plain string single-prop RANGE (embedded-safe, mirrors string indexes).
    try:
        proj.g.query(
            "CREATE INDEX FOR (s:Session) ON (s.actor_user_id)")
    except Exception:
        pass  # already indexed (idempotent — mirrors sibling blocks)
```

**Step 4 — test_indexes.py extension.**

`EXPECTED_RANGE_EMBEDDED` (~21) has NO Session entry today (only Point/Document/Subject/Object/Event/Source). Add a NEW label entry using the file's list-of-fields convention (values are flat field-name lists consumed by `for f in fields: assert "RANGE" in idx[label].get(f, [])` at test_indexes.py:131-135/533-537 — do NOT use a dict value):

```python
EXPECTED_RANGE_EMBEDDED = {
    ...,
    "Session": ["actor_user_id"],  # #2600
}
```

`EXPECTED_RANGE_DOCKER` derives via `list(v)` at ~42 — no docker edit needed. NOTE: this is a net-new label whose embedded assertion (`test_entity_key_indexes_exist`) now REQUIRES the Task 2 Step 3 `_ensure_indexes` Session block to land in the same change — both must commit together or the embedded lane goes red.

**Step 5 — Integration tests (docker lane).**

Post-capture auth reality (verified): POST /v1/sessions depends on `get_current_team_gated` (hosted_api.py:6487) — KEY-only (`eyJ` session JWT → 401). **The standard docker lane runs REGISTRY CP** (AGENTS.md sets only TORTOISE_DB_URI; `is_supabase_enabled()` False) — `_session_user_team` raises "Session auth is hosted-mode only" and hosted key mints store literal `"api"` when session_user_id is absent. So the docker-lane tests do NOT mint through the hosted session exchange; instead they **seed the registry graph directly with distinct-UUID creators via the SDK mint seam** (`sdk.apikey_create(team_id, created_by=<uuidA/B>)` — sdk.py:14616) and authenticate with the real returned `tt_` keys → the REAL registry auth face (`get_current_team` registry branch, which already carries `created_by` at hosted_api.py:1830) carries the human. Session-lane normalization is asserted at the Task 1 unit layer (supabase-fake fixture: SUPABASE_URL + FakeControlPlane — repo precedent test_hosted_api.py:1299/6312/5138); the supabase-fake mode exercises `_session_user_team` + `created_by = session_user_id` + `_alias_actor_user_id(session_user_id)`. (b1-supabase E2E cases run under the FakeControlPlane supabase-fake mode; b2-registry + E2E-5 run on the plain docker lane.)

- **Stamped capture (registry lane):** mint key_A with `created_by=<uuidA>` → POST /v1/sessions with a fresh session_id (authenticate key_A) → **assert via DIRECT graph read** (`MATCH (s:Session {id:$sid}) RETURN s.actor_user_id, s.harness` — list_sessions does not expose these fields until Task 5 lands, so the Task-2 test must not read them from the API) == uuidA + harness present. Second member-minted key (distinct uuidB) → its own session stamped with uuidB.
- **Idempotent re-POST (same actor):** re-POST the SAME session_id with the SAME key → still one Session; `actor_user_id` unchanged; zero new nodes (#1727 assertion preserved).
- **Coalesce cross-actor re-POST (failure-mode P1-1, the ONLY test that distinguishes coalesce from plain SET):** A captures session S (key A) → B re-POSTs S (key B) → Session.actor_user_id == A (first-writer-wins); node count unchanged; A's PointAdded events untouched. Variant: actor-less team (non-UUID `created_by`, e.g. "api") re-POSTs S → A still kept (missing clause ≠ wipe). Concurrency variant: `asyncio.gather` double-POST of a FRESH S from A and B → exactly one Session node + deterministic actor (TOCTOU arbitrated by the coalesce).
- **Legacy backfill pin:** seed a Session node with NO actor_user_id (legacy) → B re-POSTs it → Session.actor_user_id == B (coalesce backfill on true-retry); the legacy turns' events stay null (event-level unattributed) — assert both.
- **Failed-prior true-retry divergence (failure-mode P2) — seam pinned (cycle-3):** `capture_ok=False` is written ONLY when the v2 extractor RETURNS meta errors / marks points skipped (hosted_api.py:7466-7490 — the 200 + additive-errors path), NEVER when the extractor raises (a raise takes the 503/500 path before the SET → `capture_ok=None` → legacy replay). Three shapes pinned separately: (i) returning-errors shape (monkeypatch a per-point write failure / enrichment skip so `_extract_session_v2` returns errors → `capture_ok=False`) → B true-retries → assert the SESSION-side contract here (`Session.actor_user_id == A` survives the retry, coalesce-first-writer); the "retry-minted claim PointAdded events carry B" event leg is asserted in **Task 6 Step 2 (shape-(i) event-leg variant)** post-Task-3 `_emit_event` merge — see the Task 6 cross-ref; (ii) raise shape (extractor raises → 503, `capture_ok=None`) → B's re-POST REPLAYS → Session keeps A, ZERO B events; (iii) pre-MERGE-failure shape (provider gate 503 fires before the Session MERGE → no Session) → B's fresh capture stamps B. Do not conflate the three.
- **MCP capture lane (E2E-3 positive Given — cycle-2 P0):** `tortoise_session_capture` over /mcp with an oat_ AND a registry key token (real middleware resolution → ContextVar → Task 1 (b) threads it into the tool dict) → assert `Session.actor_user_id` via DIRECT graph read (the stamp lands in this task). The journaled PointAdded leg lands in Task 3's E2E-4(a)-adjacent integration once `_emit_event` merges the actor (capture claim-minting runs through `sdk.create_point` inside `_extract_session_v2` on the tool's `_data_sdk`-opened SDK, in the request task with the ContextVar set).
- **SDK-mirror positive + negative (unit, embedded):** set `_current_actor_user_id` in a context → call `sdk.capture_session` → Session node carries the actor; unset in a second context → no actor prop (byte-identical legacy shape). Covers the mirror clause in BOTH directions.

**Step 6 — Commit.** `feat(attribution): Session actor_user_id stamp (hosted + mirror) + index`

## Task 3: Event backstop at _emit_event + EventAPI._emit optional actor

**Files:**
- Modify: `tortoise/sdk.py:2147` (`_emit_event`)
- Modify: `tortoise/api.py:42` (`EventAPI._emit`)
- Modify: extraction/mine construction sites (`tortoise/sdk.py:3379`, `tortoise/mcp_server.py:2617` — only if threading actor)
- Test: unit + integration

**Step 1 — `_emit_event` payload copy + actor merge.**

In `_emit_event` (sdk.py:2147): the body assigns `graph_payload = payload` (~2176). Change to copy-then-merge so a caller-reused payload dict never gains an unexpected key:

```python
    actor = _current_actor_user_id.get()
    ...
    if type_ in _GRAPH_EVENT_TYPES:
        graph_payload = dict(payload or {})   # copy FIRST (#2600)
        if actor is not None:
            graph_payload["actor_user_id"] = actor
        ...
        append_event(proj, seq, type_, graph_payload, self.ulid())
```

JSONL envelope (only when `self._event_log`/`event_log_path` set): merge `actor_user_id` into the emitted event dict when actor is not None (additive — rebuild replay restores the original payload actor; the actor is NOT added to any replay skip-set).

Verify the exact current code path around 2175-2200 (params, exception handling) before editing — merge into the same `graph_payload` var used for both append + any JSONL write.

**Step 2 — EventAPI._emit optional actor.**

`api.py:42` `_emit`: add `actor: str | None = None` keyword, persisted onto the emitted event dict (`if actor is not None: event["actor_user_id"] = actor` or the constructor stores it). Default None → extraction lanes byte-identical. No caller change required (capture extraction is session-anchored anyway).

**Step 3 — Unit tests.**

- `_emit_event` with ContextVar set → `:GraphEvent` node payload carries `actor_user_id`; with var unset → NO actor key (legacy shape).
- Caller-reused payload dict: pass the same dict to two emissions → the caller's dict NEVER gains `actor_user_id` (copy-first pin).
- JSONL envelope: SDK with `event_log_path` set + var set → JSONL line carries actor; rebuild replay restores original payload.
- EventAPI._emit: default None → no actor key; actor=... → present.

**Step 4 — Integration: sessionless write resolvable to human — E2E-4(a) (oat_ MCP lane).**

E2E-4(a) = the OAuth lane, and `oat_` is accepted ONLY on /mcp (the REST /v1/* surface has no OAuth branch — plan-review P1). Sessionless create_point via the MCP tool over /mcp with an oat_ token (fixture CP seeded with an `oauth_access_tokens` row — same fake-CP pattern as Task 1 Step 6) → the `:GraphEvent` (PointAdded etc.) node carries the token's `user_id` actor. DISTINCT content per lane everywhere (MCP `tortoise_create_point` defaults dedup=True — same content on a second lane is a silent no-op, scoping P2).

**Step 5 — Commit.** `feat(attribution): journaled event actor backstop (_emit_event + EventAPI)`

## Task 4: Forged-claim strip-and-ignore (never reject)

**Files:**
- Modify: `tortoise/sdk.py:785` (`_sanitize_props`)
- Modify: `tortoise/mcp_server.py:717` (`_reject_server_managed_props` / strip helper)
- Test: unit

**Step 1 — `_sanitize_props` pop+warn.**

At the TOP of `_sanitize_props` (before the reject checks), pop reserved actor keys with a warning log (import logging module-level if not present):

```python
    # #2600: reserved actor keys are STRIP-AND-IGNORE (never a 4xx; the server
    # owns the actor). Pop BEFORE the reject checks below.
    for _reserved in ("actor_user_id", "owner", "initiated_by", "agent_id"):
        if _reserved in props:
            logging.getLogger("tortoise.api").warning(
                "ignoring client-supplied %r on tenant props", _reserved)
            props.pop(_reserved)
```

The `dict(props)` copy already at the top (785-786) makes pop safe. **agent_id:** verified NO MCP tenant tool declares an `agent_id` param (grep: `agent_id` appears only as the server-side EventAPI construction arg at api.py:29 / sdk.py:3379 / mcp_server.py:2617 — extraction/mine lanes, never client props). Since no tenant surface legitimately sets it, add `agent_id` to BOTH strip lists (SDK + MCP boundary) — the strip is a no-op on every current surface but closes the reserved-key contract for any future props surface.

**Step 2 — MCP boundary strip (locked: INSIDE the shared helper — option (a)).**

Scoping §5 pins strip-first inside the single choke point (`_reject_server_managed_props`, mcp_server.py:717) so no per-tool strip is missed (verified: 11 call sites across mcp_server.py:784-2337 — per-tool edits are exactly the miss-risk the choke point exists to eliminate). Modify `_reject_server_managed_props` to strip reserved actor keys from the props dict BEFORE computing the existing reject set. The "pure check contract" concern is moot: the function already receives every tool's props dict, and the SDK-side `_sanitize_props` strip (Step 1) is the fail-closed backstop for non-MCP surfaces:

```python
# #2600: server-resolved actor keys are STRIP-AND-IGNORE at the boundary
# (never a 4xx — the server owns the actor). Pop BEFORE the reject check.
_RESERVED_ACTOR_PROPS = frozenset(
    {"actor_user_id", "owner", "initiated_by", "agent_id"})


def _reject_server_managed_props(props: dict) -> str | None:
    """Strip client-forged actor claims, then reject remaining server-managed
    fields (#329/#1486). Returns an error message or None."""
    for k in _RESERVED_ACTOR_PROPS:
        props.pop(k, None)   # strip + ignore — never stored, never a 4xx
    bad = _SERVER_MANAGED_PROPS & set(props or {})
    if not bad:
        return None
    return ("server-managed field(s) cannot be set via props: "
            f"{sorted(bad)}")
```

Do NOT add per-tool strip calls (the (b) alternative is rejected — 11 miss-risk edits) and do not leave the call shape open.

**Step 3 — Unit tests (E2E-3 negative).**

- **Parametrized multi-tool sweep (failure-mode P2-5):** for EACH props-accepting MCP tool (the `_reject_server_managed_props` call sites at mcp_server.py:784-2337 — create_point, update_point/annotate/supersede/operator/entity surfaces), send each of `{"actor_user_id"}`, `{"owner"}`, `{"initiated_by"}`, `{"agent_id"}` in props → tool succeeds; stored node/event carries ONLY the server-resolved actor (or None) — never the forged value.
- **REST boundary pin:** forged fields in a raw JSON body to /v1/points are SILENTLY DROPPED (verified `extra="ignore"` default on CreatePointRequest) — assert the store never gains the forged key and the response is 2xx (not 422). No model_config change.
- `_sanitize_props` with forged key raises nothing (other rejects still raise).
- `authoredBy` is NOT stripped (documented residual — pre-existing client author-label).

**Step 4 — Commit.** `feat(attribution): strip-and-ignore forged actor claims (SDK + MCP boundary)`

## Task 5: Read path — actor fields + filter + display resolution

**Files:**
- Modify: `tortoise/hosted_api.py:8297` (`list_sessions`), `:8333` (`get_session_detail`)
- Modify: `website/apps/dashboard/src/capturedSessions.js`, `website/apps/dashboard/src/main.jsx:463-491`
- Test: integration in `tests/test_hosted_api.py`; node --test `website/apps/dashboard/src/capturedSessions.test.js`

**Step 1 — list_sessions RETURN extension.**

Extend the query RETURN — **append the new columns at the END** (final-verification P2: the handler maps rows positionally `{"id": r[0], "created_at": r[1], "turns": r[2], "extracted": r[3]}` at hosted_api.py:8306-8308 — inserting `s.actor_user_id`/`s.harness` before `count(p)` would shift `count(p)` from `r[3]` to `r[5]` and silently swap dashboard counts):

```cypher
MATCH (s:Session) OPTIONAL MATCH (s)-[:CONTAINS]->(p:Point)
WHERE p.pointKind IN ['decision', 'statement']
RETURN s.id, s.created_at, s.turn_count, count(p),
       s.actor_user_id, s.harness
ORDER BY s.created_at DESC LIMIT 50
```

Row dict gains `actor_user_id` (None when legacy) + `harness` as `r[4]`/`r[5]` (existing `r[0..3]` mapping UNCHANGED — value-level regression assert below). Then **`actor_display` resolution — GOOD path (plan-review good-easy P2 + cycle-2 P1): ONE membership fetch per request with a mode branch + full fail-soft**, never N per-actor GoTrue calls, never a 500. A SHARED helper resolves display for a list of actor ids, called by BOTH list_sessions and get_session_detail (never a divergent second implementation — cycle-3 P2):

```python
# #2600: actor display = membership email when present, else raw id. NEVER 500.
# Supabase lane: supabase_control.team_members (2302). Registry lane: real
# Membership nodes carry NO email (registry emails exist only on invite-time
# fake 'invite-{iid}' rows whose user_id can never equal a Session actor) →
# registry display is RAW-ID-ONLY in v1 (cycle-3 P2 — an "email shown" case is
# structurally impossible on the registry lane).
from tortoise.supabase_control import get_control_plane, is_supabase_enabled

def _actor_display_map(actor_ids: list[str], team_id: str) -> dict:
    """actor_user_id -> display, for the NON-NULL actor ids in a row set.
    Callers pass ONLY the non-null actors (the any-actor gate lives HERE, so
    get_session_detail and list_sessions share it and legacy-only responses
    never fire the fetch — cycle-3 P2). Registry lane: {} always."""
    if not actor_ids:
        return {}  # no attributed rows → never touch the CP
    try:
        if not is_supabase_enabled():
            return {}  # registry: raw-id display (no email seam for members)
        members = team_members(get_control_plane(), team_id)
        email_of = {m.get("user_id"): (m.get("email") or "")
                    for m in members
                    if m.get("user_id") and m.get("email")}
        return {uid: email_of.get(uid) for uid in actor_ids
                if uid in email_of}
    except Exception:
        return {}  # never a doomed call, never a 500 — raw-id fallback
```

Callers (both handlers pass `[r.actor_user_id for r in rows if r.actor_user_id]` — the any-actor gate): `members_by_id = await asyncio.to_thread(_actor_display_map, actor_ids, team["team_id"])` then per row `actor_display = None if not uid else (members_by_id.get(uid) or uid)`. The graph-fail-soft empty path (rows=[]) passes an empty list → no fetch.

**Honesty about the email seam (cycle-2/3 P2):** `team_members` (2302) reads only `team_memberships.invited_email` — ACTIVE members' emails live in GoTrue `auth.users`, NOT in the membership table. Email displays ONLY for rows the supabase invite-accept flow retained `invited_email` on (accepted-via-invite members, shape at supabase_control.py:1266-1290); every other member + the entire registry lane → `actor_display` = raw id. Accept raw-id display for most members in v1 (fail-soft per epic). E2E-10 tests must seed the REAL seam shape — the supabase-fake lane with an accepted-invite row that retained `invited_email` (email shown), an active row without it (raw id), an invite-pending row with `user_id=None` (filtered → raw id), an unknown id (raw id) — NEVER mock an email capability the seam structurally lacks.

**Step 2 — get_session_detail extension.**

Same fields in the detail RETURN + `actor_display` on the detail dict, reusing the SAME module-level `_actor_display_map` helper (same mode-branch + try/except → raw id + any-actor gate + null-actor → `actor_display: null` guarantees) — never a second divergent resolution. **Append the new RETURN columns at the END** (the detail handler maps `sess_rows[0][0..2]` positionally — same hazard as list_sessions). **Client consumer:** extend `transcriptModel` (capturedSessions.js) with the actor + render it in the transcript panel header (Task 5 Step 6) — a dead API field with no consumer is YAGNI (plan-review P2).

**Step 3 — Optional actor filter.**

`GET /v1/sessions?actor_user_id=<uuid>` (query-string — GET has no body). When present, add `WHERE s.actor_user_id = $uid` to the MATCH (before the OPTIONAL CONTAINS so count semantics hold). The filter EXCLUDES null-actor rows (explicit contract — no leak of legacy rows). Non-UUID param → 422 (validate with the `_is_uuid_shape` helper — a malformed filter is a client error, not a silent empty result). If the handler is async, the param comes from `request.query_params`.

**Step 4 — Integration tests (docker).**

- Session captured with known actor → list includes row with correct actor_user_id + actor_display. **Display reflects the REAL seam capability:** the registry lane (docker CP) is RAW-ID-ONLY — assert email never appears and raw id does; the supabase-fake lane (FakeControlPlane) asserts the email case ONLY with the seam-reachable shape (an accepted-invite ACTIVE row that retained `invited_email` → email shown; active row without it → raw id; invite-pending row with `user_id=None` → filtered, raw id). Never seed a hybrid shape no registry/supabase code path produces (cycle-3 P2).
- **Value-level regression on the no-filter response (final-verification P2):** seed a session with a KNOWN `turn_count` + one extracted claim → assert `turns`, `extracted`, `id`, `created_at` equal the seeded values AND `actor_user_id`/`actor_display` are correct on the same row (one assert pinning all six positional bindings `r[0..5]` against the graph — catches an off-by-N RETURN reshuffle that key-presence-only asserts would ship). Same for get_session_detail's row mapping.
- **Call-count bound (failure-mode P2-2 + cycle-3 P2):** seed ≥3 sessions with DISTINCT actors → assert the membership fetch is called ONCE per request (never per-row). **Legacy-all-null suppression:** a response whose rows all carry null `actor_user_id` (the universal legacy state at ship time) → the membership fetch / registry SDK open is NOT fired (the any-actor gate inside the shared helper), response stays 200 with `actor_display: null`. **Detail-path gate (cycle-3 P2):** monkeypatched `team_members`/`_actor_display_map` is NOT called on a null-actor session detail and called exactly ONCE on an attributed session detail (mirrors the list-side legacy suppression — the gate lives in the shared helper). Mixed-row case: attributed + legacy-null + unknown-id in ONE response → per-row independent fail-soft.
- **Fail-soft on the display seam (cycle-2 P1):** (i) `team_members`/membership fetch raising (monkeypatched) → list_sessions 200, rows present, `actor_display` == raw id; (ii) supabase mode with `get_control_plane()` raising → 200 fallback, never a 500 (today list_sessions is graph-only and must stay 2xx on CP absence); (iii) graph-down (rows=[]) → the members call is NOT fired, response `[]`. Also exercise the SUPABASE branch of `_actor_display_map` on the supabase-fake lane (cycle-3 P2 — the docker lane only exercises the registry branch): FakeControlPlane rows of the real `team_members` shape (user_id-NULL invited row filtered; empty-email active row → raw id; raising fetch → raw id).
- `?actor_user_id=<the actor>` → only that session; `?actor_user_id=<other UUID>` → empty; legacy null-actor session NEVER appears under any filter (but a legacy session BACKFILLED by a later re-POST — Task 2 coalesce — DOES match its backfilled actor; pinned in Task 2 Step 5).
- Malformed filter (email/"api"/garbage/urn:uuid:) → 422.
- get_session_detail carries actor + display (same helper, incl. null-actor → `actor_display: null` and `{"session": null}` fail-soft).
- Regression: no-filter list shape unchanged (existing dashboard consumers).

**Step 5 — EXPLAIN bound test (docker lane) — E2E-8.**

Add to `tests/test_hosted_api.py` (docker-gated as existing convention) or a focused integration module:

```python
rows = proj.g.explain(
    "MATCH (s:Session {actor_user_id:'" + actor_uuid + "'}) RETURN s.id"
).result_set
text = str(rows)
assert "Node By Index Scan" in text
assert "All Node Scan" not in text
```

FILTER-FIRST probe shape (no ORDER BY — a tiny seeded graph could otherwise legitimately label-scan+sort). **Inline the actor UUID as a quoted literal** — the in-repo EXPLAIN convention (test_indexes.py:343-393) inlines literal predicates; do NOT pass `params` to `g.explain` (whether GRAPH.EXPLAIN accepts the CYPHER parameter header server-side is unverified). **Real-query drift pin (failure-mode P2-3):** seed one attributed + one legacy session, EXPLAIN the real parameterized list query shape with the `?actor_user_id` filter substituted (OPTIONAL MATCH + ORDER BY s.created_at DESC LIMIT 50 intact) and assert the actor predicate's access path is a Node By Index Scan on `Session.actor_user_id` — or, if the sort legitimately forces a scan on the seeded shape, pin that plan explicitly so drift is visible (document the caveat: the full-shape plan is asserted for DRIFT VISIBILITY; the FILTER-FIRST probe is the load-bearing index-seek guarantee). Assertion lives in the docker-lane test only. Mirror the index-EXISTENCE assert on both lanes via `CALL db.indexes()` (Task 2 Step 4 covers via EXPECTED_RANGE).

**Step 6 — Dashboard row label + transcript panel actor (minimal).**

`website/apps/dashboard/src/capturedSessions.js`: extend `sessionRowMeta` (list row) AND `transcriptModel` (detail panel) to normalize the actor field (keep safe defaults):

```js
export function sessionRowMeta(session) {
  const s = session || {}
  return {
    turns: Number.isFinite(s.turns) ? s.turns : 0,
    extracted: Number.isFinite(s.extracted) ? s.extracted : 0,
    id: s.id || '',
    actor: (s.actor_display || '').trim() || (s.actor_user_id || ''),
  }
}

export function transcriptModel(detail) {
  const d = detail || {}
  ...
  return { ..., actor: (d.actor_display || '').trim() || (d.actor_user_id || '') }
}
```

`main.jsx:463-491` (list row caption) + the transcript panel header: render the actor when non-empty (`{meta.actor && <span className="dim small">… {meta.actor}</span>}`) — legacy null renders blank/"—", never a crash. Node --test cases in `capturedSessions.test.js`: null/empty → '', display present → display, display missing → raw id (for BOTH helpers).

**Step 7 — Commit.** `feat(attribution): list_sessions actor fields + filter + EXPLAIN bound + dashboard row`

## Task 6: E2E integration cases (docker lane) — E2E-3/4(b1,b2)/5/6/10

**Files:**
- Test: `tests/test_hosted_api.py` (or a dedicated module following repo naming, e.g. extending `tests/test_hosted_api.py` with docker-lane tests)
- Test: `tests/test_capture_session.py`

**Step 1 — E2E-4 lane matrix (b1 supabase / b2 registry) + E2E-3 attribution negative + E2E-6 revocation.**

(E2E-4(a) oat_ owns the MCP OAuth lane — Task 3 Step 4. No overlap.)

- **b1 supabase key:** fake CP seeded with team + api_key whose `created_by` is a UUID → REST create_point with the key → GraphEvent carries the key's actor; forged `actor_user_id` prop in the raw JSON body is **silently dropped-and-ignored** (verified: `CreatePointRequest` hosted_api.py:2632 has NO `model_config` → Pydantic v2 default `extra="ignore"` — the forged field never reaches the store; pin this behavior, not a 422).
- **b2 registry key:** registry SDK `apikey_verify` lane (registry mode, key minted with UUID creator) → same assertions.
- **E2E-3 negative (session attributed to human — no fabrication):** a client-supplied actor claim on a capture/point is stripped AND the stored actor == server-resolved actor (never the forged value). REST: forged field silently dropped (extra="ignore") → stored actor is the server's or None. MCP: reserved key popped + warned (Task 4).
- **E2E-6 revocation (REST lane — per-request DB resolution):** revoke the key/OAuth token → subsequent REST writes 401 → no orphaned attribution; pre-revocation events keep their actor. **MCP caveat (failure-mode P2-1):** the middleware 60s LRU cache can serve a revoked token for ≤60s — assert either the revocation path pops the cache or, on the MCP lane, use a never-warmed token for the post-revoke 401 and separately assert the revoke pops the cache entry (pin whichever the code does; do not assert a warm-token MCP 401 inside the TTL).

**Step 2 — E2E-5 dedup keeps BOTH actors (REAL hosted auth faces).**

POST /v1/sessions is key-only (`get_current_team_gated`). On the standard docker lane (registry CP — no supabase session exchange), the two "members" are **registry-seeded keys with distinct UUID creators** via the SDK mint seam (`sdk.apikey_create(team_id, created_by=<uuidA/B>)`) authenticated through the REAL registry auth face (b2 shape; see Task 2 Step 5 for the fixture rationale). NOT the DI-override seam (overrides bypass the DI body / `_data_sdk` path → no actor → both captures actor=None → red for the wrong reason):

1. Member A = key with `created_by=<uuidA>` on a FRESH PRIVATE team_id minted inside this test (its own empty graph namespace — final-verification P1: the mock seam mints a SUITE-WIDE CONSTANT claim "the new strategy is durable", sdk.py:18842, so sharing a team namespace with any earlier capture test makes A's own capture dedup-hit a pre-existing canonical whose PointAdded carries a different/no actor — red for the wrong reason); captures session_A (fresh id) with claim C → Session_A.actor_user_id = uuidA; PointAdded(claim) actor = uuidA.
2. **Freshness precondition asserted BEFORE A's capture:** zero Points + zero PointAdded GraphEvents with the mock-claim content exist in the test team; A's capture response carries the claim as `dedup=DEDUP_NEW`. Member B = key with `created_by=<uuidB>` on the same private team; captures session_B with the SAME claim C → Session_B.actor_user_id = uuidB; content_hash+kind resolves the canonical (no duplicate); `MERGE (session_B)-[:CONTAINS]->(canonical)` fires (dedup-hit points wire CONTAINS from the second session too).
3. Assert (scoping §7 surface, code-corrected — cycle-3):
   - **Per-session actor (deterministic, unconditional):** `Session_A.actor_user_id == uuidA` and `Session_B.actor_user_id == uuidB` — asserted via direct graph read in Task 2 and via list_sessions/get_session_detail in Task 6 (both surfaces carry the field after Task 5).
   - **Shared-claim negative (observable only when v2 extraction fires):** the v2 extractor's claim-minting goes through `sdk.create_point` (inside `sdk._extract_session_v2`, called on the hosted SDK at hosted_api.py:6912) → PointAdded GraphEvents in the request task with the actor ContextVar. Member B's same-content claim resolves the canonical (content_hash+kind) BEFORE create → NO new PointAdded GraphEvent carries B on the shared claim — only A's PointAdded exists for that claim. **This leg is conditional on the v2 extraction seam actually minting the claim** (mock determinism proven by existing hosted tests — test_hosted_api.py:1445). **Escape-hatch precision (final-verification P2):** if the hosted event legs are dropped, the two-actor dedup actor semantics MUST still be pinned by the deterministic SDK-lane test (below) — the contract can never vanish silently; if that SDK-lane test passes, the hosted legs are a bonus layer and the dedup-gap is named in the PR body (mirroring the epic's gap-naming convention).
   - **Turn-points emit NO GraphEvent** (direct MERGEs in the hosted loop — do NOT assert turn-point PointAdded events; corrected from scoping §7's overstatement).
   - **sessionCaptured Event node:** minted via `sdk.create_event` (hosted_api.py:6983) as an Event NODE — NOT a `:GraphEvent` store path, and NOT actor-stamped by any Task 1-4 step (EventRecorded is the JSONL-only residual). Assert its deterministic `eventId`/`sessionId` PROVENANCE linkage resolves to the stamped Session (Session.actor_user_id via the Event's sessionId) — do NOT assert a literal actor on the Event node unless a Task 2/3 amendment explicitly stamps it (out of scope for v1; note in the PR body).

**Deterministic SDK-lane two-actor dedup test (LLM-free — Task 6 owns this even if the mock-seam event legs are dropped):** ContextVar=A → `sdk.create_point(kind, content)` → PointAdded GraphEvent with actor A (direct `:GraphEvent` graph read); ContextVar=B → same content → canonical returned (no duplicate) and NO second PointAdded GraphEvent with actor B exists. This pins the E2E-5 dedup actor semantics (first writer's PointAdded keeps A; B's identical write returns the canonical and emits nothing) without depending on the extraction seam or graph freshness.

**Shape-(i) event-leg variant (convergence-confirmation P1 — the cross-ref from Task 2 Step 5 resolves HERE):** A captures session S on the private test team with a monkeypatched per-point failure so `_extract_session_v2` RETURNS errors (`capture_ok=False`; `Session.actor_user_id == A` per Task 2's assert); B true-retries S → assert via direct `:GraphEvent` graph read that the RE-MINTED claims' PointAdded events carry uuidB (B did the write) AND that A's already-minted claims carry no B event — post-Task-3 `_emit_event` merge. Contrast with shape (ii) replay (ZERO B events) to prove the true-retry branch fired.

Pin the v2 extractor seam (`TORTOISE_SESSION_EXTRACTOR` unset/v2 default) — M2 is non-default and cross-session-excluding. Distinct session_ids per member; the two session ids must NOT be equal (that would be first-writer-wins on ONE session).

**Step 3 — E2E-10 read-path attribution display (three cases, endpoint-pinned).**

At the API level, on BOTH list_sessions AND get_session_detail (each uses the same `_actor_display_map`): (i) attributed session with an accepted-invite member → actor_user_id + email display; (ii) legacy null-actor → actor_user_id null → `actor_display: null` (client renders "—"); (iii) unknown-id actor (lookup miss / registry lane) → `actor_display` == raw id. Assert the API responses; the row-label + transcriptModel unit covers the client render. Display shape on each lane is what the REAL seam produces (registry = raw id; supabase-fake = the retained-`invited_email` shapes).

**Step 4 — Commit.** `test(attribution): E2E-3/4/5/6/10 integration cases (docker lane)`

## Task 7: Full regression + full pytest

**Files:** none (verification)

**Step 1 — Full docker-lane suite.**

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_2600'
uv run pytest tests/ -v 2>&1 | tee /tmp/attribution-pytest.log
```

Expected: all green (new tests pass; no regression on tt_/tk_/oat_ auth paths — full suite). Track the tail count + any pre-existing failures against the baseline (documented if present, not introduced).

**Step 2 — Embedded carve-out spot check** (the affected embedded paths): `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embedded_lifecycle.py tests/test_guard.py tests/test_indexes.py -v` — index extension + mirror stamp are embedded-runnable.

**Step 3 — Dashboard node tests:**

```bash
cd website/apps/dashboard && node --test src/capturedSessions.test.js
```

**Step 4 — Commit (if any drift).** `chore(attribution): post-implementation test pins`

## Task 8: PR + commit-workflow (auto-merge)

**Step 1 — Commit-workflow skill.** Push branch `feat/2600-attribution-plumbing` → PR vs main. Preflight includes the full suite (Task 7). Code-review gate runs (standard complexity). PR body MUST enumerate the documented residuals (scoping §4 residual family: PointRevised/EventRecorded/ObjectRegistered/SubjectAdded/BatchIdStamped JSONL-only + authoredBy client-label + registry EMAIL-shaped created_by unattributed + sessionless dedup no-trace) so indicator 3 does not overclaim, and note the manual OAuth spike item if not executed.

**Step 2 — Manual verification note.** The live hosted OAuth spike (epic scope §1.1 item 7) requires a real hosted deployment + OAuth provider — if not feasible in this environment, note it explicitly as a manual-verification item for the epic gate (the docker-lane oat_ integration tests cover the resolver/middleware behavior).

## Wiring Check

| Touch Point | Task | Test |
|---|---|---|
| oauth.py resolver | 1 | unit (test_oauth) |
| supabase_control.resolve_api_key | 1 | unit |
| hosted_api get_current_team* (registry branch ~1830, supabase, session branch ~2432) | 1 | unit (tests/test_hosted_auth.py) |
| sdk.apikey_verify | 1 | unit |
| mcp_auth middleware ContextVar (converged block, cache-hit safe) | 1 | unit/integration |
| _data_sdk ContextVar set (~2254) | 1 | unit |
| mcp_server `tortoise_session_capture` tool-dict threading | 1 (code) + 2 (test) | unit + integration (Task 2 Step 5 MCP bullet) |
| Session MERGE (hosted + mirror) | 2 | integration (docker) + mirror unit both directions |
| projection _ensure_indexes + test_indexes | 2 | integration (both lanes) |
| TortoiseSDK._emit_event + EventAPI | 3 | unit + integration |
| _sanitize_props + MCP boundary strip | 4 | unit (E2E-3 neg, multi-tool sweep) |
| list_sessions / get_session_detail | 5 | integration + EXPLAIN (docker) |
| Dashboard row label + transcript header + sessionRowMeta/transcriptModel | 5 | node --test |
| E2E-3/4(a,b1,b2)/5/6/10 | 3 + 6 | integration (docker) |
| Deterministic SDK-lane two-actor dedup | 6 | unit/integration (Task 6) |
| Regression tt_/tk_/oat_ | 7 | full pytest |

## Complexity

Architecture standard · Ontology low · UX low · Accessibility low (server response fields are the pytest-testable core; dashboard change is a read-only row label).

## Review Cycle Log

### plan-review — Cycle 1 (3 fresh-context reviewers: Structural+Efficiency, Integration, Failure-Mode)

- **Structural reviewer:** P0=0, P1=4, P2=5. P1s: (1) session-lane normalization site `_session_user_team` carries no user_id/created_by — must be the session branch of `get_current_team_session` (~2432) reading session_user_id; (2) no REST ContextVar set-site — must live in `_data_sdk`; (3) session-JWT capture tests un-runnable — POST /v1/sessions is `get_current_team_gated` (key-only) — use member-minted keys with distinct UUID creators; (4) E2E-4(a) oat_ is MCP-only — anchor to the MCP tool. P2s: lane-matrix duplication split; DRY `_alias_actor_user_id` shared; team_members display fetch over per-actor GoTrue calls; strip inside `_reject_server_managed_props`; SDK-mirror clause tested both directions.
- **Integration reviewer:** P0=1 (session-lane normalization can't fire — same finding as Structural P1-1), P1=3 (REST ContextVar set missing; MCP capture tool team dict lacks actor — ContextVar fallback; E2E-5 real-auth premise), P2=4 (cache-hit normalization/set placement; strip call-shape; display lookup blocks event loop + Supabase-only; get_session_detail actor has no client consumer).
- **Failure-mode reviewer:** P0=0, P1=2 (coalesce cross-actor not tested — plain SET indistinguishable; REST ContextVar set unassigned), P2=6 (E2E-6 revocation lane unpinned vs 60s cache; display N+1 no bound; EXPLAIN probe ≠ real query shape; warm-cache actor ContextVar not asserted; forge-strip single-tool + REST extra pin conditional; SDK-mirror set direction untested).
- **Controller fixes (all P1s + actionable P2s, verified against code before applying):** §1 `_alias_actor_user_id` shared helper (reads user_id/created_by/session_user_id, UUID-gated with urn:/brace rejection mirroring supabase_control._is_uuid); normalization moved to the 3 real terminal dict-build sites + `_data_sdk` ContextVar set; MCP middleware set in the converged post-branch ContextVar block (cache-hit safe); Session MERGE reads `team.get("actor_user_id") or _current_actor_user_id.get()` (covers MCP tool's hand-built dict); capture tests + E2E-5 re-pinned to member-minted keys (POST /v1/sessions is key-only); E2E-4(a) → MCP oat_ lane (Task 3), Task 6 owns b1/b2 + E2E-3/6/10; strip locked INSIDE `_reject_server_managed_props` (option (a)); display resolution = one team_members fetch via to_thread (both planes, no GoTrue); get_session_detail actor consumes via transcriptModel + transcript header; EXPLAIN adds real-query-shape drift pin; multi-tool forge sweep + REST extra="ignore" pin; coalesce cross-actor/legacy-backfill/concurrency tests; E2E-6 REST-lane pin + MCP cache note; middleware cache-hit actor test; SDK-mirror positive+negative unit.

<!-- plan-review: cycle 1 issues fixed — re-dispatch cycle 2 -->

### plan-review — Cycle 2 (3 fresh-context reviewers, same roles)

- **Structural reviewer:** P0=0, P1=1, P2=5. P1: the `_data_sdk` unconditional ContextVar set (cycle-1 fix) CLOBBERS the actor on the MCP capture path (hand-built actor-less team dict → `_data_sdk` re-sets None before the Session MERGE/emits) — fix = conditional set + thread the actor into the tool dict. P2s: 3-vs-4 site-count inconsistency; agent_id dangles on a false premise (no MCP tool declares it — strip everywhere); display snippet unconditional team_members raises in registry mode; E2E-5 assertion surface trimmed (restore per-session turn-point PointAdded + sessionCaptured Event asserts); test_indexes shape must be list-of-fields not dict.
- **Integration reviewer:** P0=0, P1=2, P2=4. P1: display resolution unconditional `team_members(get_control_plane())` 500s registry/selfhost (get_control_plane raises) + no try/except (breaks the #1591 graph-only fail-soft contract) — must mode-branch + fail-soft + skip-on-empty; docker-lane E2E-5 "member-minted keys via session exchange" not executable (registry CP — created_by would be literal "api") — seed registry directly via sdk.apikey_create(team_id, created_by=<uuidA/B>). P2s: MCP capture lane never tested end-to-end (asyncio.run context bridge unverified); EXPLAIN params unverified (inline literal per repo convention); Session not in EXPECTED_RANGE sets (new label); _is_uuid_shape "mirrors" docstring wrong (braced divergence).
- **Failure-mode reviewer:** P0=1 (same MCP-capture clobber — the _data_sdk set nulls the actor on the flagship human-filing path), P1=1 (display fetch no fail-soft + no lane branch → 500s), P2=3 (email seam structurally can't return active-member emails — E2E-10(i) would mock a nonexistent capability; cross-actor true-retry capture_ok=False divergence unpinned; actor_display email disclosure boundary for graphs:read keys unpinned).
- **Controller fixes (verified against code):** `_data_sdk` set made CONDITIONAL (only when dict carries the actor — never erase) + `tortoise_session_capture` threads `team["actor_user_id"] = _current_actor_user_id.get()` (Task 1 Step 4 (b), Task 2 file list + Step 5); display map mode-branched (supabase team_members OR registry Membership query via _make_sdk(namespace="registry")), full try/except → raw id, skipped when graph returned no rows, and honest about the email seam (active members → raw id in v1; E2E-10(i) seeds the REAL seam shape); docker-lane fixtures pinned (registry `apikey_create` direct mint with distinct UUID creators for E2E-5 + Task 2 Step 5; supabase-fake FakeControlPlane mode for the b1/session-lane unit tests); E2E-5 assertion surface restored (per-session turn-point PointAdded + sessionCaptured Event); site count reconciled to three; `_is_uuid_shape` now byte-copies supabase_control._is_uuid (brace-strip, urn-reject); agent_id added to both strip lists; test_indexes entry = `"Session": ["actor_user_id"]` (new-label, same-change requirement stated); EXPLAIN uses inline literals + honest drift-pin caveat; failed-prior true-retry divergence (Session stays A, retry events carry B) pinned as intended; display fail-soft/registry-mode tests added; actor_display email boundary noted (v1 shows raw ids for active members — no new disclosure beyond the ids list_sessions already returns).

<!-- plan-review: cycle 2 issues fixed — re-dispatch cycle 3 (convergence gate) -->

### plan-review — Cycle 3 (3 fresh-context reviewers, same roles)

- **Structural reviewer:** P0=0, P1=3, P2=3. P1s: (1) E2E-5 PointAdded/sessionCaptured assertions rest on an overclaimed emission premise — hosted capture mints turn-points by direct MERGE (no GraphEvent); extracted claims DO emit via `sdk._extract_session_v2` → `sdk.create_point` → PointAdded (the grep missed the delegation at hosted_api.py:6912); sessionCaptured is an Event NODE (no literal actor stamp) — assertion surface code-corrected; (2) Task-2 tests read list_sessions fields that only land in Task 5 — assert via direct graph read in Task 2, defer API read-back to Task 5/6; (3) MCP-capture test double-owned across Task 1/2 with prerequisites in Task 2/3 — owned once (Task 2 Session-stamp leg; Task 3 event leg).
- **Integration reviewer:** P0=0, P1=2, P2=3. P1: sessionCaptured Event node never actor-stamped by any task — assert provenance linkage (Event.sessionId → Session.actor_user_id) instead of a literal actor; registry lane display email case structurally impossible (real Membership nodes carry no email; emails live only on invite-time 'invite-{iid}' rows) — registry lane is raw-id-only, email case relocated to supabase-fake with the accepted-invite retained-invited_email shape. P2s: DI unit wording contradicted the single-set-site design (fixed — assert dict only at DI, ContextVar at _data_sdk); surface-map lacked the implicit single-set-site precondition (added).
- **Failure-mode reviewer:** P0=0, P1=0, P2=4. Verified the `_data_sdk` dual-call and is_supabase_enabled() probes as NON-GAPS. P2s fixed: capture_ok=False reachable only via the 200+returned-errors path (never a raise) — three failed-prior shapes pinned separately; registry email-shown case unmockable (same finding as Integration — raw-id-only + supabase-fake accepted-invite shape); all-legacy-rows response still fired the membership fetch — fetch now gated on `any(row actor_user_id)`, legacy-suppression test added; supabase `_actor_display_map` branch untested — supabase-fake list_sessions test added.
- **Controller fixes:** E2E-5 assertion surface code-corrected (deterministic Session-stamp asserts + conditional-on-extraction PointAdded negative + turn-points emit nothing + sessionCaptured provenance linkage); Task 2 Step 5 asserts via direct graph read; failed-prior three-shape seam pinned; MCP capture test owned in Task 2 (Session leg) + Task 3 (event leg); display registry lane declared raw-id-only; `_actor_display_map` fetch gated on any-row-actor + skip-on-graph-down; get_session_detail reuses the same helper; supabase-branch display tests added; surface-map single-set-site invariant documented.

<!-- plan-review: cycles=3, status=converged-pending-final-verification, version=2.3.0 -->

### Final verification (Phase 5 — 2 fresh-context reviewers)

- **Final verifier A:** P0=0, P1=1, P2=3. P1: Task 2 Step 5 shape (i) asserted the retry-minted PointAdded event leg at Task 2's commit — but `_emit_event` doesn't stamp actors until Task 3 → split (Session-side assert in Task 2; event leg moved to Task 6). P2s: stale Verification-Plan "Task 8" cross-ref → Task 7; mcp_server.py edit split-owned (Task 1 owns the code edit per Step 4(b), Task 2 owns the test only — Files lists + Wiring row fixed); E2E-5 fallback clause vacuous (canonical-not-duplicated unassertable when the seam mints nothing).
- **Final verifier B (failure-mode):** P0=0, P1=1, P2=3. P1: E2E-5 event leg rests on graph freshness never pinned — the mock seam mints a SUITE-WIDE constant claim ("the new strategy is durable", sdk.py:18842), so a shared team namespace can make A's own capture dedup-hit a pre-existing canonical (red for the wrong reason) → E2E-5 pinned to a fresh private team_id + pre-capture zero-claims assert + `dedup=DEDUP_NEW` on A's capture. P2s: deterministic LLM-free two-actor dedup test added (ContextVar A→create_point→PointAdded A; B→canonical, no second PointAdded) so the dedup contract survives even if the hosted event legs drop; any-actor fetch gate moved INTO the shared `_actor_display_map` helper (get_session_detail path covered + call-count test on the detail path); positional RETURN reshuffle hazard (inserting new columns before `count(p)` shifts `r[3]`→`r[5]`) — new columns APPENDED at the END of both RETURNs + value-level six-binding regression assert added.
- **Controller fixes:** Task 2 shape (i) event leg moved to Task 6; Verification-Plan cross-ref corrected; mcp_server.py edit owned by Task 1 (code) + Task 2 (test), Wiring row added; E2E-5 private-team freshness precondition + `dedup=DEDUP_NEW` assert + deterministic SDK-lane two-actor dedup test; shared `_actor_display_map(actor_ids, team_id)` with the any-actor gate inside (list + detail both covered); RETURN columns appended at the end + value-level regression assert.

🔍 Final verification: found 2 P1s + 6 P2s — resolved in 1 fix pass (controller-verified against code).

### Convergence confirmation (final gate)
- Convergence-confirmation reviewer: P0=0, P1=1 (dangling cross-ref — Task 2 Step 5 shape (i) event leg referenced "Task 6" with no such test present). Fixed: Task 2 cross-ref now names "Task 6 Step 2 (shape-(i) event-leg variant)" and that bullet EXISTS (asserts re-minted claims' PointAdded carry uuidB via direct :GraphEvent read; contrasts with shape (ii) replay zero-events).
- Re-dispatch: **NO ISSUES FOUND** — all verification points confirmed (cross-ref resolves, bullet satisfiable post-Task-3, no contradiction, Session-side assert stays in Task 2).

<!-- plan-review: cycles=3+final+confirmation, status=clean, version=2.3.0 -->
