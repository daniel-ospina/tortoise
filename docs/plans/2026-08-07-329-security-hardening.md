<!-- research-path: docs/plans/scoping-329-problem.md -->

# #329 Security Hardening — Cypher Injection, Path Traversal, Key Exfiltration, Graph Flooding — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Close the verified tenant-reachable security defects (API-key misrouting, tenant→operator file disclosure, unbounded graph growth, cross-tenant enumeration, info disclosure) and the latent injection landmines (traverse rel_type, search_engine label interpolation, supersede edge-type interpolation) with regression tests for each.

**Team:** epistemic-team
**Architecture:** Hybrid of centralized shared primitives (`tortoise/security.py` — stdlib-only validation helpers) + isolated accounting (`tortoise/quota.py` — fail-closed team limits shared by REST and MCP) + surgical wiring at each choke point (sdk.py, ingest.py, analyze.py, search_engine.py, projection/, mcp_server.py, hosted_api.py, mcp_auth.py). The `context` field is already removed (#49 Phase 2) — no context work here beyond regression verification.

### Pattern Research

**Library docs (preflight)** — no new third-party deps in plan (stdlib `re`/`os`/`pathlib`/`contextvars` only). Skipped.

**Library version & API surface** — N/A (no external libs introduced).

**Idiomatic usage patterns** — N/A. Existing in-repo patterns reused: `ingest.py` `_PROVIDERS` provider table (analyze key routing), `config.py` `RELATIVE_PATH_ERROR` + `resolve_db_path` path-rejection philosophy (base-dir confinement), `edges.py` `valid_predicates` allowlist (rel-type validation), `_safe()` error-surface pattern (mcp_server).

**Library/framework pitfalls** — OWASP path-traversal cheat sheet: prefix checks are insufficient; must resolve symlinks (`realpath`) on both candidate and base before comparison. Parameterized Cypher (not string interpolation) for all values; allowlists for structure (labels, rel types, property names).

### Integration Surface Map

| Surface | Layer | Test | Notes |
|---------|-------|------|-------|
| sdk.query/paginated_query filter keys | unit | tests/test_sdk.py | ASCII + reserved-key rejection |
| sdk.traverse rel_type | unit | tests/test_sdk.py | allowlist; breaks :243 |
| search_engine runners entity_type | unit | tests/test_search_engine_gaps.py | in-function validation |
| supersede_point edge transfer | unit | tests/test_supersede_edges.py | two-pass validation, no partial transfer |
| analyze.llm_classify key routing | unit | tests/test_analyze.py | provider table, monkeypatched urlopen |
| analyze() error redaction | unit | tests/test_analyze.py | success-path `query` preserved |
| ingest _do_upgrade_all containment | integration | tests/test_ingest.py | TORTOISE_INGEST_BASE_DIR, realpath-both |
| sdk.ingest_corpus/index_sessions paths | integration | tests/test_sdk_group3.py | reject relative/`..`, base env |
| sdk.create_document/update_entity props | unit | tests/test_sdk.py | reject id/sourcePath |
| event-mint Document id | unit | tests/test_sdk.py | validate id format |
| quota enforcement | integration | tests/test_hosted_api.py, tests/test_mcp_http.py | REST 402 + MCP ERR_QUOTA, fail-closed |
| batch caps | unit | tests/test_sdk_group3.py, tests/test_sdk.py | checkpoint/file_decision/tags/target_ids |
| list_graphs scoping | integration | tests/test_mcp_http.py | team-scoped over HTTP |
| stub cap | unit | tests/test_projection.py | per-instance counter |
| Lite-mode path rejection | unit | tests/test_config.py | gap-fill resolve_db_path explicit arg |

### Verification Plan

Domain: code (security). Complexity: standard+. Layers: unit (most), integration (quota, ingest, mcp_http). E2E: none (no UI). After implementation: `python -m pytest tests/ -q` full suite; targeted: `-k "query or traverse or analyze or ingest or quota or stub or search_engine or supersede or mcp"`. Pre-existing failures to record and not regress: test_analyze.py::test_with_projection, test_bfs_audit.py::test_no_propagate_shock_callers, test_backup_e2e.py::test_backup_restore_e2e (all pre-existing on main).

---

## Task 1: `tortoise/security.py` — shared validation primitives

**Intent:** Single home for the genuinely shared security semantics (filter keys, rel types, entity types, path confinement, error redaction) so query/ingest/analyze surfaces cannot drift. Stdlib-only — no tortoise imports — so any module may import it without cycles.

**Acceptance:**
- `validate_filter_key(key)` raises ValueError unless `^[A-Za-z_][A-Za-z0-9_]*$` AND key not in `{kind, skip, limit}` AND not matching `^kind_\d+$` (matches `_expand_kind`'s actual `kind_0..kind_{n-1}` placeholders).
- `KNOWN_REL_TYPES` frozenset **derived from the codebase edge-type inventory** (RECURSIVE grep `-r -oE -- '-\[:[A-Za-z_]+' tortoise/`, union with edges.py:136 valid set, sdk.py:655 structural_rels, edges.py `_create_edges` rel map) — MUST include: IMPL, NAND, hasPart, CORRECTS, INPUT, TAGGED, aboutSubject, aboutObject, aboutEvent, aboutPoint, aboutAction, aboutDocument, extractedFrom, references, wasDerivedFrom, performs, produces, uses, authoredBy, ownedBy, managedBy, hasMember, holdsRole, memberOf, reportsTo, participatesIn, related, dependsOn, mitigated_by, INSTANTIATES, CONTAINS, BELONGS_TO, SUPPORTS, INFORMED_BY, FOR_TEAM, SUPERSEDES, supersedes, mitigates, resolves. `validate_rel_type(rt)` raises ValueError unless in KNOWN_REL_TYPES (case-sensitive: `"IMPL "`/`"impl"` rejected).
- **Drift test:** re-derive the inventory in the test (recursive grep) and assert `KNOWN_REL_TYPES ⊇ inventory ∪ structural_rels ∪ op_type-map` — self-verifying in CI.
- `validate_entity_type(et)` raises ValueError unless in `{"point","event","subject","document","object","operator","source"}`.
- `validate_document_id(doc_id)` accepts ULIDs AND operator basenames (leading `._-`, non-ASCII letters allowed); REJECTS: `..`, `/`, `\`, `\x00`, control chars, length > 255.
- `resolve_under_base(candidate: str, base: str | None) -> Path | None` — realpath() on BOTH candidate and base; returns resolved Path only if strictly under base; rejects `..` components, absolute-outside-base, symlink escapes, relative paths that escape base when resolved against CWD; returns None otherwise (caller skips + warns).
- `redact_error(e: Exception) -> str` — returns exception class name + generic safe message (no paths, hostnames, or cypher fragments).
- `env_int(name, default)` helper.

**Files:**
- Create: `tortoise/security.py`
- Test: `tests/test_security.py`

## Task 2: Filter-key validation in sdk.query / paginated_query

**Intent:** Close the tenant-reachable filter-key collision (kind/kind_N/skip/limit overwrite auto-generated params → silent wrong WHERE) and Unicode-key per-query errors. Regression: injection attempts rejected.

**Acceptance:**
- `sdk.query(kind=..., confidence=0.99)` still works; filter keys `createdAt`, `pointKind`, `wing`, `status` accepted.
- Filter keys `kind`, `skip`, `limit`, `kind_0`, `` "x`} DETACH DELETE (n) //" ``, `"x' OR 1=1"`, `émoji`, `中` all raise ValueError at both SDK and MCP layers.

**Files:**
- Modify: `tortoise/sdk.py` — `query` (~L846) and `paginated_query` (~L879): replace `isalnum` check with `validate_filter_key(key)`.
- Test: `tests/test_sdk.py` (TestQuery/TestPaginatedQuery) + `tests/test_mcp_server.py` (tortoise_query with hostile filters dict).

## Task 3: sdk.traverse rel_type allowlist

**Intent:** Kill the confirmed injection primitive (empirically executes DETACH DELETE via rel_type). Latent today (dead code — only tests call it) but the SDK is public.

**Acceptance:**
- `sdk.traverse(id, "IMPL")`, `"NAND"`, `"CORRECTS"`, `"aboutPoint"`, `"TAGGED"`, `"mitigated_by"` work.
- `sdk.traverse(id, "NONE_SUCH")` raises ValueError (UPDATE existing test `tests/test_sdk.py:243` which asserts `== []`).
- Injection payloads (`"IMPL]->(x:Point {id:'p2'}) DETACH DELETE x //"`, `"X}`) raise ValueError before any query runs.

**Files:**
- Modify: `tortoise/sdk.py` — `traverse` (L908): `validate_rel_type(relationship_type)` first; direction validated ∈ {"outgoing","incoming"}.
- Test: `tests/test_sdk.py` (update :243; add allowlist positives + injection negatives).

## Task 4: search_engine entity_type in-function validation

**Intent:** Defense-in-depth: the module-level runners are public functions; sdk allowlist exists upstream but the runners must not interpolate unvalidated labels.

**Acceptance:**
- `run_fts_query`, `run_vector_query`, `run_structural_query` raise ValueError for entity_type not in the 7 valid values (including payloads like `"Point; DETACH"`, case variants `"Point"`/`"POINT"`, trailing space `"point "`).
- Valid types still work (existing search_engine tests pass — also confirms no internal caller passes a capitalized type).

**Files:**
- Modify: `tortoise/search_engine.py` — `run_fts_query` (L104), `run_vector_query` (L179), `run_structural_query` (L280): `validate_entity_type(entity_type)` at function top.
- Test: `tests/test_search_engine_gaps.py` (invalid entity_type payloads raise ValueError).

## Task 5: supersede_point / create_operator edge-type validation (two-pass)

**Intent:** Close the latent edge-type interpolation in supersede_point's operator-edge transfer (sdk.py:640-675) and create_operator's edge creation (L734-742). Prevents partial transfers on invalid edge types.

**Acceptance:**
- `supersede_point` hoists the collect-validate pass to the TOP of the function — BEFORE the outdated-flag SET and CORRECTS CREATE (sdk.py:632-637) and BEFORE the transfer loop. Unknown edge type → ValueError with ZERO graph mutation (old point NOT marked outdated, NO CORRECTS edge, NO edges transferred).
- Legit transfer for IMPL/NAND/hasPart/CORRECTS + mitigated_by-edged points still works (regression for the live mitigate_operator feature). Points whose operator-edge set includes only known types supersede without error.
- Scope note: CONTAINS edges are Session→Point (hosted_api), never in the operator-edge transfer query — out of scope for validation (they are simply not transferred, as today).
- `create_operator` edge_type derivation already allowlisted via the op_type dict — no raw branch exists; no code change needed there (Task 3's rel-type validation is the defense).
- **Fault-injection test (P2):** monkeypatch `proj.g.query` to raise at the k-th transfer call; assert the EXACT committed state (old point outdated=true, CORRECTS edge present, exactly k operator edges transferred, no structural edges) — pins the documented residual partial state (the outdated-flag/CORRECTS writes precede the transfer loop by design; full transactional wrap is the filed follow-up). Test must be deterministic — no vacuous "or" branches.

**Files:**
- Modify: `tortoise/sdk.py` — `supersede_point` (L630-675): hoist collect-validate before ALL mutation.
- Test: `tests/test_supersede_edges.py` (mixed valid+invalid → ZERO mutations incl. no outdated flag/CORRECTS; mitigated-edged points transfer intact; fault-injection mid-transfer).

## Task 6: analyze.llm_classify provider-key table

**Intent:** Fix the confirmed key misrouting: OPENAI_API_KEY was being sent to api.deepseek.com. A key must NEVER be sent to a provider that didn't issue it.

**Acceptance:**
- Provider table `_LLM_PROVIDERS`: `{"DEEPSEEK_API_KEY": ("https://api.deepseek.com/v1/chat/completions", "deepseek-chat"), "OPENAI_API_KEY": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini")}`.
- `llm_classify` picks deepseek if DEEPSEEK_API_KEY set, else openai if OPENAI_API_KEY set, else returns None (keyword-only). The chosen key is ONLY sent to its own provider's URL.
- Test: monkeypatch env + fake `urllib.request.urlopen`; assert (a) only DEEPSEEK key set → request to api.deepseek.com with that key; (b) only OPENAI key set → request to api.openai.com with that key, NEVER deepseek; (c) neither → returns None, no outbound call; (d) both set → deepseek chosen (priority), openai key never on the deepseek request; (e) `OPENAI_API_KEY=""` (empty string, falsy) → treated as unset, no outbound; (f) provider DOWN (urlopen raises HTTPError/TimeoutError) → returns None (graceful keyword fallback), NO fallback call to the other provider.

**Files:**
- Modify: `tortoise/analyze.py` — `llm_classify` (L225-258).
- Test: `tests/test_analyze.py`.

## Task 7: analyze() error/cypher redaction

**Intent:** Stop leaking raw DB exceptions + full rendered Cypher to tenants via HTTP tortoise_analyze.

**Acceptance:**
- Error path returns `{"answer": f"Query error: {redact_error(e)}", ..., "query": None}` — no exception class internals, no paths/hostnames, no cypher text.
- Success path UNCHANGED (`"query": cypher` preserved → `tests/test_analyze.py:96-97` still passes).
- Unknown-pattern echo (`f"Unknown pattern: {pattern_name}"`) sanitized (pattern_name from LLM is untrusted — bound to known set or redacted).
- MCP `tortoise_analyze` adds a defensive scrub of `answer`/`query` fields before returning (defense-in-depth; redaction already happens in analyze()).

**Files:**
- Modify: `tortoise/analyze.py` (error returns + unknown-pattern echo), `tortoise/mcp_server.py` (tortoise_analyze scrub — error path only, success-path `query` unchanged).
- Test: `tests/test_analyze.py` (force Cypher error → no Traceback/paths; response has no raw cypher; unknown-pattern echo sanitized — LLM-returned hostile pattern name not echoed raw), `tests/test_security.py` (redact_error unit: strips paths/hostnames/cypher fragments), `tests/test_mcp_server.py` (inject raw-exception-shaped answer through tortoise_analyze → HTTP response has no exception internals — pins the MCP scrub independently).

## Task 8: Read-side base-dir confinement (upgrade-all + ingest_corpus + index_sessions)

**Intent:** Close the P1 host-file disclosure chain: tenant-controlled graph state (sourcePath OR d.id — both tenant-mutable) must never cause an operator CLI to read arbitrary host files.

**Acceptance:**
- New env `TORTOISE_INGEST_BASE_DIR`. `_do_upgrade_all` (ingest.py:238-251): resolve `coalesce(d.sourcePath, d.id)` via `resolve_under_base(candidate, base)`; None → skip with warning (one-time hint when env unset: "set TORTOISE_INGEST_BASE_DIR to enable re-upgrade"). ENV-UNSET default = fail-closed skip. Operator's own files under base still re-upgrade (positive test with env set + absolute paths).
- `ingest_corpus`/`index_sessions` (sdk.py:1830/3335): reject relative directories and `..` components outright; if `TORTOISE_INGEST_BASE_DIR` set, directory must be under base; `progress_file` must be under base when base set (or within CWD otherwise, no `..`).
- Symlink escape blocked (realpath on both sides).

**Files:**
- Modify: `tortoise/ingest.py` (`_do_upgrade_all`), `tortoise/sdk.py` (`ingest_corpus` path validation + progress_file) — use `tortoise.security.resolve_under_base`.
- Test: `tests/test_ingest.py` (upgrade-all containment matrix; **UPDATE existing `test_upgrade_all_without_transcript_does_not_crash` (L658-715) to set `TORTOISE_INGEST_BASE_DIR` to a temp root with absolute paths** — under env-unset fail-closed skip it would otherwise fail), `tests/test_sdk_group3.py` (ingest_corpus traversal rejection), `tests/test_security.py` (resolve_under_base unit matrix: `..`, absolute-escape, symlink, relative-escape, **sibling-prefix `/tmp/data` vs `/tmp/data_evil`**, trailing-slash variants, positive-under-base).

## Task 9: Write-side rejection of tenant-mutable id/sourcePath

**Intent:** Defense-in-depth on the write side: strip the properties that turn the graph into a file-read oracle before they can be persisted by tenants.

**Acceptance:**
- **Shared sanitize chokepoint:** `_sanitize_props(props)` in sdk.py routes through `create_point`, `update_point`, `create_document`, `update_entity`, `_create_entity`, `_update_entity` — strips BOTH `sourcePath` (camelCase) AND `source_path` (snakeCase — the projection's `_DOCUMENT_HANDLED` maps `source_path` → `d.sourcePath` via the explicit SET clause at entities.py:259, so the snake key is an equivalent bypass) and rejects `id` on the Document/entity surfaces AND `update_point` (`id` in props would mutate node identity via `SET n += {id:...}`) — ValueError with clear message. `create_point`'s explicit-id path (operator graph-scripts) is preserved (read side fails closed regardless). (`api.add_document`'s explicit `source_path` param is UNTOUCHED — operator flow preserved.)
- Event-mint Document branch (`projection/entities.py` `_upsert_event` L372-382): minted `:Document` id validated with `validate_document_id` — operator basenames (`.hidden.md`, `-dash.md`, `résumé.md`) accepted; `/etc/passwd`, `../x`, control chars rejected (ValueError at write; read side also fails closed).
- `update_entity` cannot set `id`/`sourcePath` (rejected keys).

**Files:**
- Modify: `tortoise/sdk.py` (`_create_entity` L3161, `_update_entity` L3212, `create_event` L3255), `tortoise/projection/entities.py` (L372-382 Document mint + `_persist_extra_props` guard for sourcePath).
- Test: `tests/test_sdk.py` (create_document/update_entity reject id/sourcePath AND source_path — `create_document(source_path="/etc/passwd")` → node has NO sourcePath property; update_point rejects id; create_point/update_point with props sourcePath stripped; create_event with objectType=Document safe/unsafe ids incl. `.hidden.md`, `-dash.md`, `résumé.md` accepted and `/etc/passwd`, `../x`, control chars rejected), `tests/test_mcp_http.py` (tenant create_document with sourcePath/source_path props → clean result, no oracle property on node), `tests/test_ingest.py` (operator add_document unaffected).

## Task 10: `tortoise/quota.py` — fail-closed team limits

**Intent:** Replace the REST fail-open `_check_team_limit` with a shared fail-closed helper used by BOTH REST and MCP, so the P1 unbounded-growth vector is closed with one counter semantics.

**Acceptance:**
- `tortoise/quota.py` (stdlib-only module-level imports; function-level sdk import):
  - `QuotaExceededError(Exception)` — limit breach; `QuotaCheckError(Exception)` — counting/config failure (distinct so REST maps 402 vs 500/503 correctly).
  - `resolve_team_limits(team_id) -> dict` reads the full Team node from the registry (`MATCH (t:Team {id:$id}) RETURN t.tier, t.max_points, t.max_api_keys, t.max_sessions`); **missing Team node → raise QuotaCheckError** (fail-closed, no silent defaults); defaults only for missing attributes: points 1000, api_keys 20, sessions 1000.
  - `enforce_team_limit(team_limits, resource, sdk=None)` — takes the limits dict (resolved ONCE by the caller: `get_current_team`/TeamResolutionMiddleware → `resolve_team_limits`, NEVER re-fetched per write); count via `MATCH (n) RETURN count(n)` on the team graph (or registry graph for api_keys); ANY counting exception → QuotaCheckError (fail-closed); count >= limit → QuotaExceededError. No team_id/limits → return cleanly (stdio/operator skip).
  - `MAX_SESSION_TURNS=500` (per /v1/sessions request), `MAX_EXTRACTIONS_PER_TURN=200` (bounds the per-turn extraction amplifier), `MAX_ANALYZE_LLM_PER_MIN=60` (per-team analyze LLM-call budget), `MAX_DREAM_FULL_PER_HOUR=6` (per-team REST /v1/dream?full=true budget).
- **REST /v1/sessions flood gate (P1):** /v1/sessions creates up to 1000 turn Points per request (deterministic CREATE ids `{session_id}_t{i}`) plus extraction Points, and is repeatable with a fixed session_id — the `sessions` check counts Session nodes only, so Points grow unboundedly (empirically ~160 nodes/turn via regex extraction: 30 dense turns → 4,832 nodes; with session_id omitted a fresh Session node is MERGEd per request). Fix:
  1. Turn cap ≤ MAX_SESSION_TURNS per request → 400 (checked FIRST).
  2. **Extraction-aware pre-write estimate:** pre-scan the conversation with the endpoint's own extraction regexes; `est = 2 (Session+Event) + Σ_turns (1 + min(n_decision_matches, MAX_EXTRACTIONS_PER_TURN) + min(n_claim_matches, MAX_EXTRACTIONS_PER_TURN))`; if `count + est > max_points` → 402 BEFORE any write.
  3. Per-turn extraction cap MAX_EXTRACTIONS_PER_TURN bounds the amplifier even when estimate is bypassed (defense-in-depth).
   Test: dense decision/claim content (e.g. "we should X. " × 80 per turn × 499 turns) → 402 with ZERO node growth (pin node count before/after); legit 2-turn request near the cap boundary still succeeds.
- **Relief path (P1):** `sdk.team_update` allowed-fields extended with `max_points`, `max_api_keys`, `max_sessions` (no control-plane HTTP surface exists today — `team_update` is SDK/registry-level; a REST control-plane surface is a follow-up). TTL test: limit change propagates within the documented 60s cache TTL (middleware gains an injectable `cache_ttl: float = 60.0` constructor param for tests). Relief test mechanism: unit-level via `quota.resolve_team_limits` (primary) + real-auth-path assertion that a raised `max_points` is honored by `get_current_team`.
- REST `hosted_api._check_team_limit` delegates to quota helper (same semantics, now fail-closed); `get_current_team` fetch extended to include max_points/max_api_keys/max_sessions so REST and MCP see IDENTICAL limits (route through `quota.resolve_team_limits` — one fetch).
- MCP: `TeamResolutionMiddleware` (mcp_auth.py) resolves the full team limits dict once (registry lookup via the same helper, cached 60s like the auth cache) and sets a `_current_team_limits` ContextVar alongside `_current_team_id`.
- **No team context (stdio/MCP without tt_ key):** `enforce_team_limit` returns cleanly (skip) — mirrors REST `if not team_id: return`. Batch caps remain unconditional.
- Batch-cap constants in quota.py: `MAX_CHECKPOINT_ITEMS=500`, `MAX_FILE_DECISION_OPTIONS=50`, `MAX_FILE_DECISION_EVIDENCE=100`, `MAX_TAGS_PER_POINT=50`, `MAX_OPERATOR_TARGETS=500` (enforced in `sdk.create_operator` — no REST create_operator endpoint exists).

**Files:**
- Create: `tortoise/quota.py`
- Modify: `tortoise/hosted_api.py` (`_check_team_limit` delegate + `get_current_team` fetch routed through `quota.resolve_team_limits` + /v1/sessions turn cap + extraction-aware points gate), `tortoise/sdk.py` (`create_operator` target_ids cap; **`team_update` allowed-fields += max_points/max_api_keys/max_sessions**), `tortoise/mcp_auth.py` (ContextVar + middleware resolution)
- Test: `tests/test_hosted_api.py` (fail-closed counting — monkeypatch count to raise → 500 not pass; count >= limit → 402; TEST_TEAM gains explicit `max_points`; /v1/sessions turn cap + points gate; team_update(max_points=) relief), `tests/test_quota.py` (unit: limits resolution incl. missing-Team-node → QuotaCheckError, fail-closed, api_keys at cap + revoke frees slot, first-write/missing-graph bootstrap, cross-team isolation with 2 teams).

## Task 11: MCP quota gating + batch caps

**Intent:** Apply quota to every node-creating MCP write tool so the tenant-reachable flood is bounded at the same counter REST uses.

**Acceptance:**
- `_QUOTA_GATED` frozenset in mcp_server.py: create_point, create_operator, create_event, create_subject, create_object, create_document, create_source, checkpoint, file_decision, update_entity, update_point, diary_write, mitigate_operator (all HTTP_ALLOWED node-creating tools).
- Quota check runs INSIDE the `_safe`-wrapped callable via a `_quota_gated(fn, resource)` decorator (pre-write check, then the tool body runs). `_safe` is extended to map `QuotaExceededError` → structured `{"error": msg, "code": ERR_QUOTA}` result dict (ERR_QUOTA = -32006) and `QuotaCheckError` → `{"error": msg, "code": ERR_QUOTA_SERVER}`; per the established MCP convention (tests/test_mcp_http.py:331 — JSON-RPC errors live INSIDE the tool result, HTTP 200), no HTTP-level 402 from tool execution; REST surfaces true HTTP 402 via FastAPI. Tests assert the result-dict code fields.
- Batch caps enforced: checkpoint items > 500 → error; file_decision options > 50 or evidence > 100 → error; tags > 50 on create_point AND update_point → error; create_operator target_ids > 500 → error.
- **Edge-creating tools ARE quota-gated** (create_edge, supersede, invalidate — all HTTP_ALLOWED): edge growth is the same graph-flood family as node growth (~120k edges/hour at 20 keys × 100 req/min). They call `_enforce_team_limit(team_id, "points")` pre-write like the node-creating tools. Rationale change: the quota bounds graph growth, period. The introspective scan includes edge-creating SDK calls (`.create_edge(`, `.supersede_point(`, `.invalidate_point(`). Note: `update_point`/`update_entity` are also gated even though they don't create nodes — accepted and documented: ANY tenant write is blocked at cap (fail-closed stance).
- **Introspective completeness test (structural):** scan each HTTP_ALLOWED tool's source (inspect.getsource) for node/edge-creating SDK method REFERENCES — **paren-LESS bound-callable names** because MCP tool bodies pass bound methods to `_safe` (e.g. `_safe(_get_team_sdk().supersede_point, old_id, new_id)`, `_safe(_get_team_sdk().create_point, ...)`): `.create_point`, `.create_operator`, `.create_event`, `.create_subject`, `.create_object`, `.create_document`, `.create_source`, `.checkpoint`, `.file_decision`, `.diary_write`, `.update_point`, `.update_entity`, `.mitigate_operator`, `.create_edge`, `.supersede_point`, `.invalidate_point` (each matches the bound-callable form `.name,`) AND for **indirect bulk writers** (`.ingest_corpus`, `.index_sessions`, `.backfill_v25`). **Non-vacuous sentinel:** assert `tortoise_supersede`'s and `tortoise_create_point`'s source specifically match ≥1 pattern each (pins the match is real). The `_quota_gated(fn, resource)` decorator PRESERVES the bound-callable style (it wraps the call, not the body) — no tool-body rewrite needed — assert every tool whose body creates/MERGEs nodes or edges (directly or via bulk writers) is in `_QUOTA_GATED` OR explicitly exempted in a documented `_QUOTA_EXEMPT` list (no exemptions expected today). Negative test: `tortoise_ingest_corpus`, `tortoise_index_sessions`, `tortoise_backfill_v25` remain EXCLUDED from HTTP_ALLOWED (or quota-gated if ever added). **Functional at-cap assertion for every tool in `_QUOTA_GATED`** (or one per resource type) alongside the structural scan. Convention note: new node/edge-creating tools MUST be added to `_QUOTA_GATED`.
- Stdio semantics pinned: (a) stdio with NO team context → quota skipped (write succeeds even above default cap — operator/trusted; `_current_team_id` is only ever set by TeamResolutionMiddleware, so stdio in production always hits this branch), batch caps still apply; (b) team context enforced — unit test injects the `_current_team_id`/`_current_team_limits` ContextVars directly (the unit seam; no production stdio team path exists); test both branches.
- Stdio-mode write below cap still succeeds (test pins TORTOISE_API_KEY unset for dev mode or uses the HTTP path).

**Files:**
- Modify: `tortoise/mcp_server.py` (16 tool bodies + `_QUOTA_GATED` + `_quota_gated` helper + ERR_QUOTA + **remove `tortoise_dream` from HTTP_ALLOWED** — whole-graph EP stabilization is operator/stdio-only; + **analyze LLM budget**: `tortoise_analyze` skips `llm_classify` beyond MAX_ANALYZE_LLM_PER_MIN per team, keyword-only fallback), `tortoise/mcp_auth.py` (ERR_QUOTA=-32006 and ERR_QUOTA_SERVER=-32007 registered alongside ERR_UNAUTHORIZED/ERR_RATE_LIMIT/ERR_EXCLUDED/ERR_REGISTRY)
- Test: `tests/test_mcp_http.py` (quota blocks at cap with team fixture; introspective completeness test incl. edge-creating tools; functional at-cap assertion per _QUOTA_GATED tool; cross-team isolation: team A at cap blocked, team B unaffected; quota holds under RATE_LIMIT_DISABLED=1; overshoot self-correction: team at max-1 + max checkpoint → bounded final count, next write blocked; stdio no-team write above cap succeeds; edge flood: team at point cap → create_edge/supersede/invalidate blocked; analyze LLM budget: N analyze calls past budget → no further outbound LLM calls (mocked urlopen); dream(full=True) absent from tools/list and rejected over HTTP; **true concurrency race: N parallel writers at cap-k → final count ≤ cap + N×max_batch and next write blocked** (reuse tests/test_embedded_concurrency.py subprocess pattern); **TTL cache-hit: limit change NOT visible until TTL expiry (old limit enforced pre-TTL), new limit enforced post-TTL; downgrade: lowering max_points below count blocks post-TTL**), `tests/test_mcp_server.py` (batch caps; tag VALUE validation: empty/whitespace/oversized/non-string rejected; checkpoint malformed item → clean error with zero writes before validation).

## Task 12: list_graphs tenant scoping

**Intent:** Stop cross-tenant graph-name enumeration via HTTP MCP.

**Acceptance:**
- `tortoise_list_graphs` over HTTP returns ONLY graphs owned by the calling team (`team_{team_id}` prefix + the team's own graph); stdio returns the full list (operator context).
- Decision: team-scope (not remove) — preserves the tool for stdio/operators and keeps HTTP functional with zero enumeration.

**Files:**
- Modify: `tortoise/mcp_server.py` (`tortoise_list_graphs` L645-648), `tortoise/sdk.py` (`list_graphs` L1206 — accept optional team scope filter; **exact `team_{id}` equality, not prefix matching**)
- Test: `tests/test_mcp_http.py` (HTTP list_graphs returns EXACTLY the calling team's graphs — two teams with prefix-related ids (e.g. `a` vs `ab`) assert no `team_ab` leak for `team_a`; never the root/legacy `tortoise` graph), `tests/test_mcp_server.py` (stdio full list).

## Task 13: Stub-node auto-creation cap

**Intent:** Bound the operator-surface stub flood: short-ID (non-ULID) source refs in OperatorAdded events auto-create Point stubs (edges.py:24-33) with no limit.

**Acceptance:**
- `FalkorProjection.__init__` gains `self._autocreated_stubs = 0`; `_create_edges` increments per stub created; env `TORTOISE_MAX_AUTOCREATED_STUBS` (default 500) caps per-instance stub creation; at cap: STOP creating stubs, log warning, skip edge creation to the missing node (fail-safe — no partial edge, no crash).
- Legit cross-file wiring below cap still creates stubs (test_projection.py `test_falkor_apply_operator_added_orphan_stubs` :321-338 still creates 1 stub for short id "42" below cap).

**Files:**
- Modify: `tortoise/projection/__init__.py` (L199-260 init), `tortoise/projection/edges.py` (`_create_edges` L14-58)
- Test: `tests/test_projection.py` (monkeypatch cap=2, apply 3+ OperatorAdded with short ids → only 2 stubs; warning logged; no partial edges).

## Task 14: Regression verification — Lite mode, context, full suite

**Intent:** Close out the issue's remaining O/I/T items with verification rather than new code.

**Acceptance:**
- Lite-mode path rejection gap-fill: `resolve_db_path("tortoise.db")` (explicit arg) raises ValueError matching RELATIVE_PATH_ERROR (add to tests/test_config.py if missing). NOTE: `resolve_db_path("~/x.db")` is INTENTIONALLY expanded (test_config.py:39 asserts tilde expansion) — verify instead that unexpanded `~` passed DIRECTLY to `FalkorProjection(path=...)` is rejected at projection/__init__.py:225-230.
- Context removal: confirm existing test that `create_point(context=...)` raises TypeError (test_stop_writes.py or add if missing).
- `allow_nonstandard_path` escape hatch: audit — confirm env/kwarg only, no tenant reachability (document in test or comment).
- Full suite: `python -m pytest tests/ -q` — new tests pass; pre-existing failures unchanged (test_analyze::test_with_projection, test_bfs_audit, test_backup_e2e — recorded, NOT introduced by this work).

**Files:**
- Modify: `tests/test_config.py`, `tests/test_stop_writes.py` (verify/extend)
- Test: full suite run.

---

## Rejected Alternatives (from solution-verify, with "when it WOULD have been better")

| Alternative | When it would have been better |
|---|---|
| Pure B — inline per-surface validation, no shared module | If the fix list were 2-3 sites with single semantics; with 12 items and 3 shared boundaries (filter keys, rel types, path confinement) duplication drifts |
| Pure C — projection-level choke-point enforcement | If projections were fresh-per-tenant with no legacy graphs; our shared projections carry no team context and C breaks the committed REST `MATCH (n)` counter semantics |
| Remove list_graphs from HTTP_ALLOWED entirely | If team-scoping were impossible; scoping preserves the tool for stdio and keeps HTTP functional |
| Name-pattern introspective test | If tool naming conventions were enforced mechanically; structural source-scan is stronger (adopted) |
| Static rel-type allowlist hand-written | It will drift (missed mitigated_by on the first pass); derived-from-inventory + unit test adopted |
| Transactional (MULTI/EXEC) supersede_point | Full atomicity is a robustness follow-up (filed separately); #329 scopes validation-before-mutation which removes the attacker-reachable invalid-edge-type corruption; mid-transfer DB failures documented + fault-injection test pins behavior |

## Runtime Prerequisites

| Prerequisite | Notes |
|---|---|
| `TORTOISE_INGEST_BASE_DIR` | NEW env. Unset = fail-closed skip in upgrade-all/ingest_corpus with hint. Operators with legacy relative sourcePaths must set it to their corpus root. **Existing upgrade-all tests must set this env** (test_ingest.py:658). |
| `TORTOISE_MAX_AUTOCREATED_STUBS` | NEW env. Default 500. Per-FalkorProjection-instance stub budget. |
| No new third-party deps | stdlib only |
| Embedded FalkorDBLite | Tests run without Docker (conftest temp DBs) |
| #49 context removal | Already merged (PR #155) — this plan's filter-key/rel-type work does not touch context |

## Acceptance Criteria (concrete, verifiable)

1. `pytest tests/test_security.py tests/test_quota.py tests/test_sdk.py tests/test_supersede_edges.py tests/test_search_engine_gaps.py tests/test_analyze.py tests/test_ingest.py tests/test_sdk_group3.py tests/test_projection.py tests/test_mcp_http.py tests/test_mcp_server.py tests/test_hosted_api.py tests/test_config.py -q` — all new/updated tests pass.
2. Injection attempts rejected: filter-key payloads, traverse rel_type payloads, search_engine entity_type payloads, supersede edge_type payloads → ValueError, no query executed.
3. Traversal attempts blocked: `..`/absolute/symlink/relative-escape in ingest_corpus, progress_file, upgrade-all sourcePath/d.id → skipped or ValueError; tenant `/etc/passwd` id/sourcePath rejected at write AND skipped at read.
4. Key routing correct: OPENAI key never on deepseek request; each provider receives only its own key; no key → keyword-only fallback.
5. Stub creation bounded: cap honored, no partial edges, warning logged.
6. MCP quota: all 13 node-creating tools gated (introspective test); REST fail-closed; batch caps enforced; list_graphs team-scoped over HTTP.
7. analyze() redaction: error path returns no exception internals/cypher; success path unchanged.
8. Full suite green except the 3 recorded pre-existing failures.


---

<!-- plan-review: cycles=3, status=clean, version=2.2.0 -->
<!-- plan-verify: 2 verifiers, cycle 3 clean (P1s closed: source_path snake bypass, scan pattern mismatch; P2s folded: /v1/dream budget, relief-path rescope, TTL injectable, update_point id rejection) -->
