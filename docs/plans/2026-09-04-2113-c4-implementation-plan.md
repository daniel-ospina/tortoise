# C4 (#2113): FalkorDB per-graph ACL layer — implementation plan

> Branch: feat/2113-c4-acl-layer · Base: origin/main (19a50ace, post-C2/C3) · Epic #2083 §5.1/§5.5
> Status: IN PROGRESS (implementation)

## Scope (IN)

Ship the real `tortoise/acl_graph_users` module behind C2's pre-wired seams
(`_acl_user_create_hook`/`_acl_user_drop_hook` import target — never re-wire
the hook calls), with the hardened empirical recipe, lifecycle parity, and
rollback semantics. OUT: app-layer scope enforcement (C5 #2114), hosted-cloud
management-API client (no SDK in-repo — the direct-ACL path IS the seam both
modes share; the supabase control-plane *credential row storage* defers to
C5, documented).

## Decisions

- **D1 (recipe, empirical 4.20.4):** `ACL SETUSER tenant_<gid> on ><pw>
  ~team_{tid}_{gid} +GRAPH.QUERY +GRAPH.RO_QUERY +PING` — the key pattern is
  the EXACT graph namespace (`team_{tid}_{gid}`, both modes — registry
  `_graph_create` + supabase `_provision_graph` derive the same shape), NOT
  the research doc's `~tenant_a` shorthand (that matched repro graphs named
  `tenant_a`). Username `tenant_<gid>` (gid unique → username unique). Never
  `+@all`, GRAPH.LIST/KEYS/SCAN/CONFIG/DEBUG/UDF/AUTH, `%R~`/`%W~` (broken).
  SETUSER on an existing user = update (upsert-safe).
- **D2 (strictness split):** the C2-pre-wired hook stays fail-soft
  (log-and-proceed) for STANDALONE graph-bound key mints (create_api_key to
  an EXISTING graph — its ACL user was created at graph-mint; a re-create
  failure must not orphan a key). The PROVISIONING mint (`_mint_graph_key`
  → `_mint_key`, `acl_strict=True`) is STRICT: ACL create failure raises →
  `_mint_key` revokes its own just-minted key → `_provision_graph`'s generic
  except rolls back the graph (E2E indicator 5 — no orphan graph/key/ACL
  user). Ordering: the hook fires AFTER the key-cap pre-check and BEFORE the
  key write in strict mode (clean rollback — no key to revoke), keeping C2's
  after-write position for soft mode is unnecessary; one position (pre-write)
  for both.
- **D3 (admin client + guard):** `_admin_client()` parses TORTOISE_DB_URI
  (docker://|redis://|rediss:// with host) → redis-py client with the URI
  creds. No URI / embedded redislite → None → every ACL fn no-ops (carve-out
  lane + selfhost-embedded never touch ACL). Bare redis without the falkordb
  module → ACL fns no-op (MODULE LIST probe). Server unreachable at mint →
  treat as ACL-layer-down: fail-soft log for soft mints; STRICT raise only
  when the server IS reachable but SETUSER errors (a real recipe/perm
  failure) — a down ACL layer is never a SPOF (epic §5.5: app spine
  authoritative).
- **D4 (credential storage):** generated 32-hex password stored server-side
  with the graph: registry mode → Graph node props `acl_user`/`acl_pass`
  (SET on the minted node). Supabase mode → NO row columns in C4 (the hosted
  platform manages DB users out-of-band; C5 hosted seam reads the cloud API)
  — documented deferral, R16 parity note. Idempotent create: if the user
  already exists AND the node has stored creds → reuse the stored password
  (crash between SETUSER and storage must not invalidate); else generate +
  store after SETUSER succeeds.
- **D5 (drop + persistence):** drop = `ACL DELUSER tenant_<gid>` (username
  derivable — no storage read needed) + `ACL SAVE`. SAVE on every mutation
  (create/drop) when the server supports aclfile (selfhost persistence,
  R15); docker dev server runs requirepass-only (no aclfile) — SAVE is
  issued best-effort (ACL SAVE on a no-aclfile server returns an error →
  log, non-fatal; persistence across restart is a deployment config, the
  restart-recovery TEST asserts user-presence after reconnect + SAVE issue,
  and documents the aclfile requirement for true restart durability).
- **D6 (default user):** `ensure_default_user_secured()` — server mode:
  assert the default user requires a password (ACL GETUSER default: nopass
  absent / CONFIG requirepass set); if OPEN → refuse to create ACL users and
  raise AclLayerError with a clear message (the layer is theater without it —
  epic §5.1). Test: assert-docker default is password-secured.
- **D7 (test hygiene on the shared matrix server):** ACL users are GLOBAL
  server state. C4 tests use UNIQUE gids (per-mint uuid) and drop users in
  teardown; existing docker-lane suites that mint graphs gain ACL users only
  transiently (dropped on graph delete when the flow deletes; mint-only tests
  accumulate users — accepted, unique per gid, R13 notes a periodic audit;
  no suite asserts global user count == 0).

## Existing-surface map (verified on main 19a50ace)

- `hosted_api._mint_key` — fires `_acl_user_create_hook(graph_id, team_id)`
  for graph-bound mints (~4658), AFTER key write. C4: add `acl_strict` param
  + move the hook before the key write (revoke-self on strict failure).
- `hosted_api._mint_graph_key` — provisioning wrapper; passes acl_strict=True.
- `hosted_api._acl_user_create_hook` / `_acl_user_drop_hook` (~4731/4747) —
  import `tortoise.acl_graph_users.create_acl_user(graph_id, team_id)` /
  `drop_acl_user(graph_id)`. C4 module makes them real; hook body stays
  (adds strict arg threading).
- `hosted_api` delete-graph endpoint (~8185) — `_acl_user_drop_hook(graph_id)`
  after key cascade. Registry node/row is tombstoned BEFORE the hook — drop
  by derived username works (no storage read).
- `hosted_api._provision_graph` (~7752) — the ONE mint flow; generic except
  rolls back graph + revokes keys. Strict ACL failure raises → lands here.
- `sdk._graph_create` — registry Graph node {id, team_id, name, kind,
  namespace, status, created_at}; C4 SETs acl_user/acl_pass.
- Graph namespace convention `team_{tid}_{gid}` — registry `_graph_create`
  ns + supabase `_provision_graph` ns (D1 pattern source).
- Docker test lane URI `docker://:falkordb@localhost:6379/tortoise_test_matrix`
  — falkordb module 4.20.4, default user password-secured (requirepass).

## Tasks

1. **`tortoise/acl_graph_users.py`** — `AclLayerError`, `_admin_client()`
   (URI parse + redis-py; None guards), `create_acl_user(graph_id, team_id)`
   (upsert + credential storage on the registry node + SAVE), `drop_acl_user
   (graph_id)` (+SAVE), `acl_user_config(graph_id)` (ACL GETUSER parse for
   tests), `acl_user_exists`, `credential_for_graph(graph_id)` (C5 seam),
   `ensure_default_user_secured()`. Registry-node storage via
   `_make_sdk(namespace="registry")` guarded to registry control-plane mode.
2. **hosted_api wiring** — `_mint_key(..., acl_strict=False)`; hook fires
   pre-write; strict failure → revoke own key + re-raise; `_mint_graph_key`
   passes acl_strict=True; hooks thread strict. (Soft standalone mints keep
   log-only.)
3. **Rollback seam check** — `_provision_graph` generic except already
   revokes keys + rolls back graph; strict raise path lands there. Verify no
   ACL user orphan (the strict hook fires BEFORE the key write; graph
   rollback deletes the row/node; the ACL user is dropped by the delete-cascade
   drop hook only on DELETE — a ROLLBACK path must also drop the ACL user:
   add `_acl_user_drop_hook(graph["id"])` into `_rollback_graph`).
4. **Tests (docker lane — TORTOISE_DB_URI required; skip when absent or
   bare-redis):** `tests/test_acl_graph_users.py`
   - E2E-1 config-inspection: provision a graph via the mint flow →
     `acl_user_config` == exact perms (`~team_{tid}_{gid}`,
     +GRAPH.QUERY/RO_QUERY/PING, ON, no GRAPH.LIST/KEYS/SCAN/CONFIG, no +@all).
   - E2E-2 cross-graph NOPERM: connect as `tenant_<gidA>` (stored creds) →
     GRAPH.QUERY on team_{tid}_{gidB} → NOPERM error; own graph → OK.
   - E2E-8 drop-on-delete: delete the graph → user absent.
   - E2E-11 no-orphan: strict provisioning failure (patch create_acl_user to
     raise AclLayerError with server up) → graph+key rolled back + no ACL
     user left.
   - Persistence: ACL SAVE issued + fresh admin connection lists the user.
   - Default-user secured assert.
   - Credential mapping: registry Graph node carries acl_user/acl_pass;
     `credential_for_graph` returns them; the key itself has no ACL password.
5. **Sweep** — ruff, py_compile, carve-out untouched (module no-ops without
   URI — run the affected hosted_api carve-out files), docker-lane touched
   files + the new ACL test file; uri-guard declarations if any env mutation.

## Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Shared docker server ACL accumulation from existing mint suites | Unique tenant_<gid> per mint; no suite asserts zero users; R13 audit note |
| R2 | Strict ACL failure breaks provisioning 500s on transient ACL hiccup | Strict = server-reachable-but-command-error only; down/absent = fail-soft (D2/D3) |
| R3 | SETUSER recipe drift across FalkorDB releases | Empirical 4.20.4 pinned; config-inspection test asserts exact perms; track #2652 |
| R4 | Registry-node storage writes into the shared registry namespace | Guarded to registry mode + minted node id; failure → logged, credential mapping degrades (C5 reads absence fail-closed) |
| R5 | Carve-out/embedded regression | _admin_client None → every fn no-op; carve-out run of hosted_api files |

## Execution log (2026-09-04)

- **T1 `tortoise/acl_graph_users.py`** — `AclLayerError`, `_admin_client()` (URI → redis-py, None when embedded/bare-redis-absent — every fn no-ops), `create_acl_user` (SETUSER upsert: `tenant_<gid>` on `><pw>` `~team_{tid}_{gid}` +GRAPH.QUERY/RO_QUERY/PING; deny-all `-@all` base redis-8 composition asserted; default-user secured check first; credential stored on the registry Graph node; SAVE best-effort), `drop_acl_user` (+SAVE), `acl_user_config`/`acl_user_exists` (config inspection, RESP2-flat + RESP3-labeled + dict GETUSER shapes handled; rule strings token-expanded + lowercased for version-stable asserts), `credential_for_graph` (C5 seam), `_parse_getuser` shared.
- **T2 hosted_api wiring** — `_mint_key(acl_strict=False)`: hook fires BEFORE the key write for graph-bound mints (a strict failure raises with nothing committed → clean rollback); `_mint_graph_key` passes acl_strict=True; `_acl_user_create_hook` re-raises AclLayerError when strict, logs otherwise (soft standalone mints to existing graphs never block). `_rollback_graph` now drops the ACL user (a strict create may have landed pre-rollback).
- **T3 rollback seam** — verified: `_provision_graph` generic except catches the strict AclLayerError → `_revoke_graph_keys` (none) + `_rollback_graph` (node delete + ACL drop). Pinned by test_strict_mint_failure_no_orphan (no key, no user) + test_rollback_drops_user_and_node.
- **T4 tests** `tests/test_acl_graph_users.py` (9, docker-lane skipif URI-or-module absent): exact-permission config inspection (key = exact ns only, 3 commands over -@all, deny set absent, no nopass); credential stored server-side + idempotent re-create reuses the password; cross-graph NOPERM + KEYS denied as the tenant user; drop + idempotent; rollback drops node+user; strict mint failure leaves no key/user; SOFT mint survives ACL failure (fail-soft contract); ACL SAVE/reconnect presence; default-user secured assert.
- **T5 sweep** — ruff clean; py_compile clean; carve-out 486 pass (ACL tests skip — no URI → module no-ops); docker lane: test_acl_graph_users 9 pass + test_hosted_api 242 + supabase_control/dashboard/cli/writer_inventory/hosted_auth/auth_flip 358 pass. Existing provisioning/key/delete suites exercise the REAL hooks on the shared matrix server without breakage (users unique per gid; deleted with graphs).
- **Code-review round-1 fixes (PR #2220)**: (a) port default 16379 (match sibling URI parsers — a port-less docker:// URI silently pointed at 6379 and fail-soft no-oped the whole layer); (b) open-default strict provisioning now rolls back + surfaces an ACTIONABLE 503 with the D6 remedy (was an opaque 500 — selfhosts without requirepass); (c) ONE live secret per graph — supabase/hosted mode is CREATE-ONCE (further mints no-op when the user exists — per-mint `>pw` appends accumulated live never-revoked secrets); registry store-miss window (user exists, no stored password) now SETUSERs `resetpass` first so the orphaned secret dies; stored-password reuse never churns; (d) fail-closed id charset guard ([0-9A-Za-z_-]) on graph_id/team_id in create/drop/namespace — SETUSER rule args are space-split server-side (injection hardening for future callers). New pins: test_store_miss_rotate_invalidates_old_secret (old pw fails auth after rotate, new pw works), test_unsafe_id_rejected_fail_closed, test_open_default_strict_provision_503 (rollback + actionable 503). 12 ACL tests pass on docker lane; hosted_api 242 + carve-out 410 pass; ruff clean. **Second-model gate fixes (S1-S5)**: S1 team-delete ACL orphan — new `_drop_team_acl_users(team_id)` enumerates the team's custom graph ids (registry nodes / supabase rows) + drops each tenant user, wired into BOTH purge paths (`_purge_registry_team` + the supabase purge branch); R13's periodic audit stays a plan note (an actual reconcile job is ops/capstone scope). S2 exact-state upsert — `_setuser` now issues a FULL `reset` before rebuilding (`reset on >pw ~ns +cmds`), healing any drifted broader grant (+@all/allkeys from a manual fix or old recipe) instead of accumulating rules; `_GRAPH_DENY` removed (the deny-all base + reset make it dead). S3 one-live-secret unconditional — the full reset clears passwords every upsert, so stored≠live divergence can never yield two live secrets (re-asserting the same stored pw is churn-free). S4 connection-class failures in `_setuser` log + return False (fail-soft — a transient drop between probe and SETUSER no longer 503s a healthy selfhost); only a server-side ResponseError raises AclLayerError (strict rollback reserved for real recipe/perm failures). S5 test coverage — runtime NOPERM assertions for GRAPH.CONFIG/DEBUG/UDF/LIST as the tenant user + test_upsert_heals_drift_exact_state (+@all drift → exact state restored) + test_team_purge_drops_custom_graph_users. 14 ACL tests pass on docker lane; hosted_api 242 + carve-out 410 pass; ruff clean.
