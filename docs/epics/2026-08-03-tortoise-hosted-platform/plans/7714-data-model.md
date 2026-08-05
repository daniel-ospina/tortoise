<!-- research-path: docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md -->

# Control-Plane Data Model — Registry Graph + Postgres Audit Log

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Implement Team, Membership, APIKey, Invitation entities in a dedicated FalkorDB `control_plane` graph, plus a Postgres `audit_events` table with three-tier persistence (Postgres → JSONL fallback → replay).

**Architecture:** Extend the existing `TortoiseSDK` with a `_get_registry()` method that accesses a separate FalkorDB graph (`control_plane`) via the existing `db.select_graph()` handle. No second FalkorDB connection. Postgres audit via psycopg2 sync INSERT with local JSONL fallback — no silent data loss. The SDK grows by ~300 lines in a clearly demarcated control-plane section. Postgres is an optional dependency (no-op when `TORTOISE_AUDIT_DSN` is unset).

### Pattern Research

**psycopg2 sync INSERT:** Canonical Python-Postgres driver. Single-connection model matches existing sync SDK pattern (no async bridging needed). `psycopg2-binary>=2.9` provides pre-built wheels — no C build step. Reconnection: exponential backoff (1s→2s→4s, max 30s) on `psycopg2.OperationalError`.

**FalkorDB multi-graph access:** `FalkorProjection.__init__` already calls `self.db.select_graph(graph_name)`. The `db` handle supports multiple `select_graph()` calls returning independent Graph objects. No second connection, no additional resource management. Pattern: cache the registry Graph handle in `self._registry_g` (mirrors `self._proj` caching pattern).

**FalkorDB relationship edges:** The epic plan §4 specifies `(:Membership)-[:BELONGS_TO]->(:Team)`, `(:APIKey)-[:BELONGS_TO]->(:Team)`, `(:Invitation)-[:FOR_TEAM]->(:Team)`. Created with Cypher `CREATE (m)-[:BELONGS_TO]->(t)` within the control_plane graph. `(:Team)-[:OWNS]->(:Graph)` is a logical relationship only — Graph entities live in tenant namespaces, so the mapping is via the `graph_name` property on Team.

**SHA-256 API key hashing:** Reuse existing `tortoise.auth.hash_api_key()` with pepper from `TORTOISE_SECRET_PEPPER`. Keys stored as hex digest. Plaintext returned exactly once at creation. Key prefix: first 10 characters of plaintext (`tt_a1b2c3d4`) stored for dashboard display.

### Integration Surface Map

| # | Surface | Type | Test Layers | Key Failure Modes |
|---|---------|------|-------------|-------------------|
| S1 | FalkorDB control_plane graph | Graph DB | unit (SDK CRUD), DB-integration (FalkorDBLite) | graph handle caching, cross-graph access, select_graph timeout |
| S2 | Postgres audit_events | RDBMS | integration (@pytest.mark.postgres) | connection failure → JSONL fallback, fallback replay, schema auto-create |
| S3 | tortoise/sdk.py | SDK | unit (existing + new) | refactored team_create backward compat, migration idempotency |
| S4 | tortoise/mcp_server.py | MCP | unit (tool registration) | destructiveHint correctness, readOnlyHint for list operations |
| S5 | tortoise/auth.py | Auth | unit (unchanged) | hash_api_key reuse verified |

**Bug Pattern Flags:**
- FalkorDB has no UNIQUE constraints — idempotency keys for team_create
- Postgres connection failure must not fail registry operations — JSONL fallback
- Migration is idempotent but best-effort — idempotency via name-based dedup
- Cross-graph referential integrity is application-level — validate team_id exists before creating memberships

### Verification Plan

**Test layers:**
- unit: all registry CRUD via FalkorDBLite (no Postgres needed for CI green)
- integration: Postgres audit via `@pytest.mark.postgres` (skipped when `TEST_AUDIT_DB_URI` unset)
- pgTAP: audit_events immutability trigger (deferred to #7738 — test design issue)

**E2E scenarios exercised:**
- E2E-4-D (tenant isolation — registry portion)
- E2E-7-D (security baseline — key storage)
- E2E-8-D (multi-team membership)

---

## Implementation Plan

### Task 1: Infrastructure Setup

**Intent:** Add dependencies, error types, and env vars before any logic changes.
**Acceptance:** `psycopg2-binary` in pyproject.toml, `ControlPlaneError` + `AuditLogError` importable, `.env.example` updated.
**Files:**
- Modify: `pyproject.toml`
- Modify: `tortoise/exceptions.py`
- Modify: `.env.example`

**Step 1:** Add `psycopg2-binary` as optional dependency:
```toml
[project.optional-dependencies]
postgres = ["psycopg2-binary>=2.9"]
```
Users install with `pip install tortoise[postgres]` for Postgres audit support.
In `audit_events.py`, lazy import: `try: import psycopg2; except ImportError: psycopg2 = None`.
When psycopg2 is unavailable, audit operates in JSONL-only mode (the fallback).
**Step 2:** Add `ControlPlaneError` and `AuditLogError` to `tortoise/exceptions.py`:
```python
class ControlPlaneError(ValueError):
    """Registry operation failed — duplicate, not found, invalid role, etc."""


class AuditLogError(RuntimeError):
    """Fatal audit log failure — Postgres unreachable and fallback exhausted."""
```
**Step 3:** Add `TORTOISE_AUDIT_DSN` to `.env.example`:
```
# Postgres connection for audit_events (optional — falls back to JSONL if unset)
# TORTOISE_AUDIT_DSN=postgresql://user:pass@localhost:5432/tortoise_audit
```
**Step 4:** Run `pip install -e .` to verify psycopg2-binary installs cleanly.

---

### Task 2: Audit Event Logger

**Intent:** Create `tortoise/audit_events.py` with three-tier persistence: Postgres → JSONL fallback → replay. Postgres is optional (lazy import, no hard dependency).
**Acceptance:** `AuditLogger.append()` works when Postgres is up; writes to JSONL when down; replays on reconnect.
**Files:**
- Create: `tortoise/audit_events.py`
- Test: `tests/test_audit_events.py`

**Step 1:** Create `tortoise/audit_events.py` with `AuditLogger` class:
```python
class AuditLogger:
    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or os.environ.get("TORTOISE_AUDIT_DSN")
        self._conn = None
        self._fallback_dir = Path.home() / ".tortoise"
        self._fallback_dir.mkdir(parents=True, exist_ok=True)
        self._fallback_path = self._fallback_dir / "audit_fallback.jsonl"
        self._replay_lock = threading.Lock()
        self._replay_backoff = 1.0

    def append(self, team_id, actor_user_id, operation,
               resource_type=None, resource_id=None,
               ip_address=None, user_agent=None) -> None:
        # Lazy connect on first write
        if self._dsn and self._conn is None:
            self._connect()
        # Try Postgres INSERT
        # On failure: append to fallback.jsonl
        # On any success: attempt replay of fallback

    def _replay_fallback(self) -> None:
        # Under lock: read fallback.jsonl, INSERT each line, truncate on success

    def close(self) -> None:
        # Close Postgres connection if open
```
**Step 2:** Implement `_ensure_schema()` — `CREATE TABLE IF NOT EXISTS audit_events (...)`
**Step 3:** Implement `_connect()` with exponential backoff reconnection
**Step 4:** Write `tests/test_audit_events.py`:
- `test_append_writes_to_postgres` (mark: postgres)
- `test_append_falls_back_to_jsonl_when_postgres_down` (unit, mock psycopg2)
- `test_replay_on_reconnect` (integration, mark: postgres)
- `test_schema_auto_created` (integration, mark: postgres)
**Step 5:** Verify tests: `python -m pytest tests/test_audit_events.py -v -m "not postgres"` passes (unit); `python -m pytest tests/test_audit_events.py -v -m "postgres"` with `TEST_AUDIT_DB_URI` set passes (integration)

---

### Task 3: Registry Graph Access

**Intent:** Add `_get_registry()` to `TortoiseSDK` for accessing the `control_plane` graph.
**Acceptance:** `_get_registry()` returns a cached FalkorDB Graph handle; uses existing db connection.
**Files:**
- Modify: `tortoise/sdk.py`

**Step 1:** Add `_registry_g` attribute to `TortoiseSDK.__init__` (default `None`)
**Step 2:** Implement `_get_registry()`:
```python
def _get_registry(self):
    if self._registry_g is None:
        proj = self._get_proj()
        self._registry_g = proj.db.select_graph("control_plane")
    return self._registry_g
```
**Step 3:** Add `_ensure_registry_indexes()` — per-label indexes on control_plane graph:
```python
def _ensure_registry_indexes(self):
    g = self._get_registry()
    indexes = [
        ("Team", "name"),
        ("Membership", "team_id"),
        ("Membership", "user_id"),
        ("APIKey", "team_id"),
        ("APIKey", "key_hash"),
        ("Invitation", "team_id"),
        ("Invitation", "token_hash"),
    ]
    for label, prop in indexes:
        try:
            g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception:
            # Log warning for unexpected errors; "index already exists" is safe
            _logger.debug("Index may already exist: %s.%s", label, prop)
```
**Step 4:** Extend `__init__` and `close()`:
```python
# In __init__ (alongside self._registry_g = None):
self._registry_g = None
self._audit_logger = None

# In close():
def close(self):
    # ... existing SVBP cleanup ...
    if self._audit_logger is not None:
        self._audit_logger.close()
    self._registry_g = None
```

---

### Task 4: Team CRUD + Migration

**Intent:** Refactor `team_create()` to write to the `control_plane` graph. Add team_get, team_list, team_update, team_delete, and a one-shot migration.
**Acceptance:** `team_create()` writes to control_plane graph; returns same shape; migration is idempotent.
**Files:**
- Modify: `tortoise/sdk.py` (refactor `team_create`, add 5 new methods)
- Test: `tests/test_control_plane.py`

**Step 1: Refactor `team_create()`**
- Remove direct Team node creation in tortoise graph (current lines 1263-1286)
- Instead: write `:Team` node to control_plane graph via `_get_registry()`
- Add `idempotency_key` param — if provided, check for existing Team with matching `idempotency_key` property before creating
- Still create the team's graph (`team_{name}`) in FalkorDB for tenant data — the graph creation is unchanged
- Return shape unchanged: `{name, graph_name, api_key, id}`
- Audit: call `self._audit_logger.append(team_id=tid, operation="team_create")`

**Step 2: Add `team_get(team_id)`** — `MATCH (t:Team {id:$id}) RETURN t` via registry graph; returns dict or None

**Step 3: Add `team_list()`** — `MATCH (t:Team) RETURN t ORDER BY t.createdAt` via registry graph

**Step 4: Add `team_update(team_id, **fields)`** — `MATCH (t:Team {id:$id}) SET t += $fields`; validates allowed fields (name, tier, stripe_customer_id, subscription_id, backup_enabled, max_users, max_teams, max_graphs)

**Step 5: Add `team_delete(team_id)`** — requires `confirmation` kwarg matching team name. Cascading cleanup:
1. MATCH/DELETE all Membership nodes with BELONGS_TO edge to Team
2. MATCH/DELETE all APIKey nodes with BELONGS_TO edge to Team
3. MATCH/DELETE all Invitation nodes with FOR_TEAM edge to Team
4. DELETE the Team node
5. Drop tenant graphs (`team_{name}`): use `proj.db.delete_graph(graph_name)` (Docker FalkorDB only). For FalkorDBLite (embedded mode), `delete_graph` is unavailable — log a warning and skip. The deletion is best-effort for the graph data; the control-plane metadata deletion is authoritative.
6. Audit the delete event (Postgres audit_events preserved — immutable)
Raises `ControlPlaneError` if confirmation doesn't match team name.

**Step 6: Add `migrate_teams_to_registry()`**
```python
def migrate_teams_to_registry(self) -> dict:
    """Idempotent one-shot: move Team nodes from tortoise graph to control_plane."""
    proj = self._get_proj()
    reg = self._get_registry()
    # Read existing Team nodes from tortoise graph
    teams = proj.g.query("MATCH (t:Team) RETURN t").result_set
    migrated, skipped = 0, 0
    for row in teams:
        team = row[0]  # Node object with properties
        # Check if already in registry
        existing = reg.query(
            "MATCH (t:Team {name:$name}) RETURN count(t) > 0",
            params={"name": team.get("name")},
        ).result_set[0][0]
        if existing:
            skipped += 1
            continue
        # Create in registry
        reg.query(
            "CREATE (t:Team {id:$id, name:$name, api_key:$key, "
            "graph_name:$gn, createdAt:$now})",
            params={"id": team.get("id"), "name": team.get("name"),
                    "key": team.get("api_key", ""),
                    "gn": team.get("graph_name", f"team_{team.get('name')}"),
                    "now": team.get("createdAt", now_iso())},
        )
        migrated += 1
    # Mark originals outdated
    if migrated > 0:
        proj.g.query("MATCH (t:Team) SET t.status = 'outdated'")
    return {"migrated": migrated, "skipped": skipped}
```

**Step 7: Write tests in `tests/test_control_plane.py`** (FalkorDBLite only — no Postgres needed):
- `test_team_create_writes_to_registry_graph`
- `test_team_create_is_idempotent_with_key`
- `test_team_create_rejects_duplicate_name`
- `test_team_get_returns_none_for_missing`
- `test_team_list_returns_all_teams`
- `test_team_update_changes_mutable_fields`
- `test_team_delete_cascades_to_children`
- `test_team_delete_requires_name_confirmation`
- `test_migrate_teams_is_idempotent`

---

### Task 5: Membership CRUD

**Intent:** Add Membership nodes with BELONGS_TO edges and role validation.
**Acceptance:** Create/list/update/delete memberships; role enum enforced; max_users checked.
**Files:**
- Modify: `tortoise/sdk.py`
- Test: `tests/test_control_plane.py`

**Step 1: Add `membership_create(team_id, user_id, role)`**
- Validate `role ∈ {owner, admin}` → raise `ControlPlaneError` if not
- Validate `team_id` exists in registry → raise `ControlPlaneError` if not
- Check `max_users` constraint on team (COUNT memberships for team vs team.max_users)
- `CREATE (m:Membership {id:$id, user_id:$uid, team_id:$tid, role:$role, joinedAt:$now})`
- `MATCH (m:Membership {id:$id}), (t:Team {id:$tid}) CREATE (m)-[:BELONGS_TO]->(t)`
- Audit

**Step 2: Add `membership_get(membership_id)`** → dict or None

**Step 3: Add `membership_list(team_id)`** → list of memberships for team

**Step 4: Add `membership_update_role(membership_id, new_role)`**
- Validate `new_role ∈ {owner, admin}`
- `MATCH (m:Membership {id:$id}) SET m.role = $role`
- Audit

**Step 5: Add `membership_delete(membership_id)`** — `DETACH DELETE` the node + its BELONGS_TO edge. Audit. Idempotent: returns False if not found.

**Step 6: Write tests:**
- `test_membership_create_with_valid_role`
- `test_membership_create_rejects_invalid_role`
- `test_membership_create_rejects_missing_team`
- `test_membership_create_rejects_at_max_users`
- `test_membership_update_role`
- `test_membership_delete_is_idempotent`

---

### Task 6: APIKey CRUD

**Intent:** Store hashed API keys in the control_plane graph. Plaintext returned once.
**Acceptance:** Keys stored as SHA-256 hash; plaintext shown once; revoke sets revoked_at; verify looks up hash.
**Files:**
- Modify: `tortoise/sdk.py`
- Test: `tests/test_control_plane.py`

**Step 1: Add `apikey_create(team_id, created_by)`**
- Generate: `api_key = f"tt_{uuid.uuid4().hex}"`
- Hash: `key_hash = hash_api_key(api_key)`
- Prefix: `key_prefix = api_key[:10]` (e.g., `tt_a1b2c3d4`)
- `CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, key_prefix:$kp, created_by:$cb, created_at:$now})`
- `MATCH (k:APIKey {id:$id}), (t:Team {id:$tid}) CREATE (k)-[:BELONGS_TO]->(t)`
- Return: `{id, key_prefix, api_key, created_at}` — plaintext in THIS response only
- Audit

**Step 2: Add `apikey_list(team_id)`** → list of `{id, key_prefix, created_by, created_at, last_used_at, revoked_at}` — no plaintext, no hash

**Step 3: Add `apikey_revoke(key_id)`**
- `MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now` — soft delete for audit trail
- Audit
- Idempotent: if already revoked, returns `{revoked: True, already: True}`

**Step 4: Add `apikey_verify(key_plaintext)`**
- Hash input: `key_hash = hash_api_key(key_plaintext)`
- `MATCH (k:APIKey {key_hash:$kh}) WHERE k.revoked_at IS NULL RETURN k.team_id, k.id`
- Returns `{team_id, key_id}` or None
- (Consumed by API auth middleware — separate issue)

**Step 5: Write tests:**
- `test_apikey_create_stores_hash_not_plaintext`
- `test_apikey_create_returns_plaintext_once`
- `test_apikey_list_excludes_plaintext`
- `test_apikey_revoke_sets_revoked_at`
- `test_apikey_verify_revoked_returns_none`
- `test_apikey_verify_valid_returns_team_context`

---

### Task 7: Invitation CRUD

**Intent:** Store hashed invitation tokens with 7-day expiry. Token lookup for email acceptance flow.
**Acceptance:** Tokens hashed; accept checks expiry; cleanup method for expired; single-use enforced.
**Files:**
- Modify: `tortoise/sdk.py`
- Test: `tests/test_control_plane.py`

**Step 1: Add `invitation_create(team_id, email, role, created_by)`**
- Generate: `token = str(uuid.uuid4())`
- Hash: `token_hash = hash_api_key(token)`
- `expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()`
- Reject if pending invitation for same email+team exists → `ControlPlaneError`
- `CREATE (i:Invitation {id:$id, team_id:$tid, email:$email, role:$role, token_hash:$th, created_by:$cb, created_at:$now, expires_at:$exp, accepted_at:null})`
- `MATCH (i:Invitation {id:$id}), (t:Team {id:$tid}) CREATE (i)-[:FOR_TEAM]->(t)`
- Return: `{id, email, role, expires_at, token}` — plaintext token in THIS response only
- Audit

**Step 2: Add `invitation_list(team_id)`** → list of `{id, email, role, created_at, expires_at, accepted_at}` — no token hashes

**Step 3: Add `invitation_get_by_token(token_plaintext)`**
- Hash: `token_hash = hash_api_key(token_plaintext)`
- `MATCH (i:Invitation {token_hash:$th}) RETURN i`
- Returns invite dict or None
- (For email link acceptance flow)

**Step 4: Add `invitation_accept(invitation_id, user_id)`**
- Check `expires_at > now()` → reject expired with `ControlPlaneError("Invitation expired")`
- Check `accepted_at IS NULL` → reject if already accepted
- `SET i.accepted_at = $now`
- Call `membership_create(team_id=invite.team_id, user_id=user_id, role=invite.role)`
- Audit
- Returns `{membership_id, team_id}`

**Step 5: Add `invitation_revoke(invitation_id)`** — soft-delete (SET status = 'revoked'). Audit.

**Step 6: Add `cleanup_expired_invitations()`**
- `MATCH (i:Invitation) WHERE i.expires_at < $now AND i.accepted_at IS NULL AND (i.status IS NULL OR i.status <> 'expired') SET i.status = 'expired'`
- Returns count of cleaned invitations

**Step 7: Write tests:**
- `test_invitation_create_rejects_duplicate_pending`
- `test_invitation_accept_rejects_expired`
- `test_invitation_accept_creates_membership`
- `test_invitation_accept_rejects_already_accepted`
- `test_invitation_get_by_token_finds_match`
- `test_cleanup_expired_invitations_marks_expired`

---

### Task 8: MCP Tool Exposure

**Intent:** Expose registry CRUD methods as MCP tools with correct safety hints.
**Acceptance:** Read-only tools have `readOnlyHint=true`; destructive tools have `destructiveHint=true` and require human confirmation.
**Files:**
- Modify: `tortoise/mcp_server.py`

**Step 1: Add read-only tools** (readOnlyHint=true):
- `tortoise_team_list` → `_safe(sdk.team_list)`
- `tortoise_team_get(team_id)` → `_safe(sdk.team_get, team_id)`
- `tortoise_membership_list(team_id)` → `_safe(sdk.membership_list, team_id)`
- `tortoise_apikey_list(team_id)` → `_safe(sdk.apikey_list, team_id)`
- `tortoise_invitation_list(team_id)` → `_safe(sdk.invitation_list, team_id)`

**Step 2: Modify existing tool** `tortoise_team_create` (already at line 506): update docstring to note `idempotency_key` param and control_plane storage. The wrapper `_safe(sdk.team_create, name)` transparently picks up the refactored SDK method.

**Step 3: Add new mutation tools** (destructiveHint=true via `annotations=ToolAnnotations(destructiveHint=True)`):
- `tortoise_apikey_create(team_id, created_by)` → `_safe(sdk.apikey_create, team_id, created_by)`
- `tortoise_apikey_revoke(key_id)` → `_safe(sdk.apikey_revoke, key_id)`
- `tortoise_invitation_create(team_id, email, role, created_by)` → `_safe(sdk.invitation_create, team_id, email, role, created_by)`
- `tortoise_invitation_revoke(invitation_id)` → `_safe(sdk.invitation_revoke, invitation_id)`

**Step 4:** `tortoise_team_delete` is NOT exposed via MCP — too dangerous for agent automation. Team deletion requires dashboard confirmation flow.

---

### Task 9: Final Integration & Existing Test Updates

**Intent:** Wire AuditLogger into SDK lifecycle. Update existing tests. Verify backward compatibility.
**Acceptance:** `team_create()` returns same shape. Existing tests pass. New tests pass. CI green.
**Files:**
- Modify: `tortoise/sdk.py` (lifecycle wiring)
- Modify: `tests/test_sdk.py` (if needed)
- Run: `python -m pytest tests/ -v -m "not postgres"`

**Step 1:** Wire AuditLogger into SDK:
```python
# In TortoiseSDK.__init__:
self._audit_logger = AuditLogger()

# In each CRUD method:
self._audit_logger.append(team_id=..., actor_user_id=..., operation=...)
```

**Step 2:** Run existing tests to verify backward compatibility:
```bash
python -m pytest tests/test_sdk.py tests/test_auth.py -v
```

**Step 3:** Run full CI-simulating test suite:
```bash
python -m pytest tests/ -v -m "not postgres"
```

**Step 4:** If `TEST_AUDIT_DB_URI` is available, run Postgres integration tests:
```bash
python -m pytest tests/ -v -m "postgres"
```

---

## Key Design Decisions

1. **Registry graph name: `control_plane`** — separate graph, accessed via `proj.db.select_graph()`, not namespace-prefixed. Clear separation from epistemic graphs.
2. **Single db handle:** No second FalkorDB connection. `_get_registry()` returns cached Graph from existing db. Matches existing `_get_proj()` pattern.
3. **Audit three-tier:** Postgres INSERT → local JSONL fallback → replay on reconnect. No silent data loss. Matches auth dev-bypass philosophy (best-effort, not gate).
4. **API key hashing:** Reuse `auth.hash_api_key()` with pepper. Plaintext returned once. Soft-revoke via `revoked_at` timestamp.
5. **Error conventions:** GET returns None for not-found. CREATE raises `ControlPlaneError` for validation failures. DELETE idempotent (returns False if not found).
6. **Relationship edges:** `BELONGS_TO` for Membership/APIKey, `FOR_TEAM` for Invitation. Enables graph traversal queries. `OWNS` (Team→Graph) is logical only — Graph entities live in tenant namespaces.
7. **Migration:** Idempotent, marks originals outdated, backward-compat transition period via registry-first-then-fallback.
8. **Concurrent access:** Idempotency key for `team_create`. Documented eventual consistency for other operations — acceptable for human-scale control-plane operations.
9. **Team delete cascading:** Deletes Membership/APIKey/Invitation nodes. Drops tenant graphs. Preserves Postgres audit_events (immutable). Requires name confirmation.
10. **user_id validation:** Stored as-is. Callers (API server) validate against Supabase auth before calling membership_create.

## Runtime Prerequisites

- `TORTOISE_AUDIT_DSN` env var (e.g., `postgresql://user:pass@localhost:5432/tortoise_audit`). If unset, audit events go to JSONL fallback only.
- `audit_events` table auto-created on first connect.
- No change to `TORTOISE_DB_URI` — FalkorDB connection unchanged.

## Acceptance Criteria

1. `team_create()` writes to `control_plane` graph, returns same shape as before
2. All CRUD operations handle edge cases: duplicates → error, not-found → None, invalid role → error
3. API keys never stored as plaintext; plaintext returned exactly once
4. Audit events: Postgres → JSONL fallback → replay (no silent loss)
5. Invitation accept rejects expired tokens; cleanup method marks expired
6. Team delete cascades to Membership/APIKey/Invitation; preserves audit_events
7. Migration idempotent (run twice = same state)
8. Tests pass with FalkorDBLite only (`-m "not postgres"`) for CI green
9. Existing tests pass (backward compat)
