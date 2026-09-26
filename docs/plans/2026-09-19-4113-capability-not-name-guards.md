---
title: "Tool-surface guards test capability, not name (#4113)"
type: capability
domain: capability
doc_status: live
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
issue: 4113
created: 2026-09-19
---

# #4113 — Tool-surface guards test capability, not name

**Issue:** #4113 · **Level:** task (standalone, no fractal fields → fail-closed to standard) · **Domain:** capability (test infrastructure)
**Branch:** `fix/4113-capability-not-name-guards` · **Worktree:** `.worktrees/4113-capability` · **Base:** `origin/main` @ `3fc3806f1`

---

## 1. Confirmed problem

The guards named in the issue assert safety properties of the MCP/REST tool surface by comparing **hardcoded tool-name strings**. When the #3863/#3994 cutover renames or merges a tool, the strings stop matching reality and the guards become one of three worthless things:

| Guard | Failure mode on rename | Class |
|---|---|---|
| `test_tool_registry.py::test_derived_http_allowed_equals_literal` (exclusion loop) | `assert "<old name>" not in HTTP_ALLOWED` is **trivially true** — the name is gone | vacuous (fail-open). NB the *first* assertion (`derived == HTTP_ALLOWED`) is already tautological at HEAD — `get_http_allowed()` and the test both compute `{t.name for t in TOOL_REGISTRY if t.http_policy}` (#454). Disposition: keep it as the explicit "HTTP_ALLOWED stays registry-derived" pin (it *does* catch a future literal regression), and add the capability guard for the exclusions. |
| `test_tool_registry.py::test_http_policy_exclusions` | `assert name in by_name` → fails for a name that moved | false-red |
| `test_tool_registry.py::test_adapter_excluded_tool_still_registered` | `assert "<old name>" in registered` **trivially false** | false-red |
| `test_abuse_integration.py::test_destructive_mutating_tools_never_read_classified` | hardcoded set; a merged/renamed tool drops out of the check | false-red or vacuous |
| `test_abuse_integration.py::test_rw_annotated_http_tools_never_read_classified` | writer set is **derived from the registry annotation itself**; a merge that picks the read-only label makes `writers == ∅` → the guard **cannot fire while the write is still reachable** | vacuous (fail-open) |
| `test_abuse_integration.py::test_point_creating_wrap_sites_have_weights` | `src.find(f"_get_org_sdk().{method},")` returns `-1` once handlers merge behind a dispatch table | false-red |

**Root cause:** the guards key on the *surface label* (the tool name / handler text) while the property they protect is a property of the *operation*. The label is the thing the cutover changes; the operation is stable.

**Root fix (quality-over-convenience):** key every guard on the **operation** (`ToolDefinition.sdk_method`, the SDK method's own behaviour, and the handler call graph), derive the surface binding from the registry, and make staleness fail loudly in **both** directions. A tool name is *looked up*; the one place a name is *declared* is the write-surface map, where the declaration itself is the thing under test (§4.4).

This is category **A** under `issue-workflow` Gate 0: the failure mode is a **false PASS / inert enforcer**, not friction.

**Sibling guards (out of scope, filed):** the same class persists in `tests/test_mcp_http.py` (`test_every_node_creating_tool_is_quota_gated` etc. — hardcoded method substrings, non-transitive, fail-open `if fn is None: continue`). #4113 is tests-only and names the other two files, so those are filed as **#4121** and listed in §7 rather than silently left.

---

## 2. Alternatives considered

| # | Approach | Verdict |
|---|---|---|
| A | Keep name strings, add a rename-warning comment / CI grep for renamed names | **Rejected** — still name-keyed; only moves the vacuity |
| B | Consume `config/surface-manifest.yml` (the #3863 curated list) as the capability source | **Rejected as the primary source** — the manifest is itself keyed on tool names (rows `name:`), so a cutover merge must rewrite it, and a guard that trusts a mutable generated artifact re-stales exactly as the issue describes. (It remains useful *evidence* for the mapping; it is not the ground truth.) |
| C | **Derive capability from the operations themselves** — the SDK source (which methods mutate / reach the filesystem / mutate the control plane), the MCP handler call graph, the `_quota_gated` wrap sites, and the registry `sdk_method` binding | **CHOSEN** — survives any rename/merge; new writes and new privileged paths are caught because they appear in the SDK/handler ground truth, not in a name list |

**Adversarial check (why C does not reintroduce vacuity):** a guard derived from an artifact can go vacuous if the *artifact* is empty or over-broad. Every derived set therefore carries a **non-vacuity sentinel** (an anchor operation present, a known read absent) and an **over-inclusion sentinel**; every derivation takes an **injectable source/AST** so a guard predicate can be shown to **fail** on a synthetic violation (mutation-testing principle: an assertion that survives its own bug is dead).

---

## 3. Capability model (the ground truth)

A test helper `tests/tool_surface_capabilities.py` (not collected — no `test_` prefix; imported as a bare module, `tests/` has no `__init__.py` and pytest inserts the test dir on `sys.path`) derives facts by **AST analysis**. Every derivation takes an **injectable source text / module AST** (defaulting to the real file) so falsifiability tests can feed synthetic violations.

| Set | Derivation |
|---|---|
| `sdk_graph_mutators()` | `TortoiseSDK` methods whose body (transitively through `self.<m>()`, module-level helpers, `proj.apply`/`proj.create_*`/`proj.set_*`/… write helpers) contains a mutating Cypher clause (`CREATE\|MERGE\|SET\|DELETE\|REMOVE\|DROP\|DETACH DELETE`). Docstrings excluded. Visited-set traversal.  Schema DDL (`CREATE INDEX/CONSTRAINT`) is **not** a node mutation and is excluded (else `_get_registry`'s lazy index bootstrap would make every control-plane **read** a false-positive mutator). |
| `sdk_filesystem_methods()` | structural rule: a filesystem-walk call (`os.walk/scandir/listdir`, `Path(...).rglob/glob/iterdir`, `open`, `read_text/read_bytes`, or a recognised custom walker such as `index_walk.walk_markdown`) applied to a path that **is a parameter of the method or threaded from one**. **Transitive** — uses the same `self.<m>()` / module-helper traversal as `sdk_graph_mutators()` (so `index_directory`, which delegates to `_index_directory_locked`/`_index_load_progress`, is included). A declared `INTERNAL_PATH_READERS` exclusion list (`_probe_embedded_busy`, `session_index_health`) plus an over-inclusion sentinel pins it. |
| `sdk_operator_only_mutators()` | graph mutators that mutate through `self._get_registry()`, classified operator-only **UNLESS *all* of the registry labels they mutate (the union across the transitive body) are in the declared tenant-scoped allowlist** — i.e. **any** control-plane label keeps it operator-only (fail-closed). This is required because `org_create` mutates `:Team`/`:Membership` **and**, transitively via `_graph_create`, `:Graph`; the naive "every label is control-plane" reading would drop it. Named anchors: `org_create`, `membership_create`, `apikey_revoke`, `invitation_create`. |
| `handler_operations(tool, src=…)` | BFS over the `mcp_server.py` module-function call graph from the tool's handler, collecting **every SDK-method attribute reference** — the value-passed `_safe(_quota_gated(_get_org_sdk().create_point, …))` form (the dominant HEAD form), the direct-call form, and the local-alias form `sdk = _get_org_sdk(); sdk.<m>`. Follows tool→tool dispatch (the merged `tortoise_get` → subtools). Visited set (future-proofs against a mutual call). **Fails closed** on an unresolvable handler name (`register_all` silently skips one — the #2210 class) **and on an unresolvable dynamic dispatch** — a subscript-call on an unresolved mapping (`_HANDLERS[name](…)`), `getattr(<expr>)(…)`, or `functools.partial` over a table: a guard-relevant handler that invokes a callee the derivation cannot resolve to a module-level function is recorded UNRESOLVED and fails the guard (closes the table-dispatch merge shape the issue cites). |
| `quota_gated_wrap_sites(src=…)` | AST walk of `_quota_gated(…)` call sites → **per-site** records `{(method, lineno): has abuse_weight}` (a per-method dict is lossy: `mitigate_operator` already has two sites, and a future weighted/unweighted duplicate would be collapsed). A site whose first argument is not a statically-resolvable SDK-method attribute is recorded **UNRESOLVED and fails the guard** (fail-closed — the T4 dynamic path `_quota_gated(fn)`). |
| `registry_entries_by_method()` | `TOOL_REGISTRY` indexed by `sdk_method` |

Declared sets, each with a reason and an **exactness + liveness cross-check** (stale entry fails; new member without an entry fails):

- `HTTP_EXCLUDED_SDK_METHODS = {backfill_v25, dream}` — operator-only for a non-structural reason (`backfill_v25` migrates the whole schema; `dream` is CPU-heavy whole-graph EP — `#329`). Each must resolve to a real SDK method and its bound tool must be `http_policy is False`.
- `READ_THROUGH_WRITE_METHODS = {compute_confidence, get_confidence, get_tenant_packs}` — operations that **do** write on an internal self-heal/cache path but are read-classified (verified: `SET n.confidence`; lazy `dream` — docstring cites **#1157**; `get_tenant_packs` → `ensure_tenant_packs` `MERGE (p:PackInstall)`). Cross-check (`declared_set_violations`): each member resolves (SDK method, or declared non-SDK/dangling) and its bound tool is HTTP **read-only**. NOTE `get_tenant_packs` is imported as a bare name by its handler, so it is **documented-only** (not guard-2-detectable) — enforced via the #4122 decision; the entry keeps the posture visible and is not a live guard input. The posture is **tracked for a decision in #4122**; the guard does not override it (contradiction test).
- `NON_SDK_WRITER_OPERATIONS = {upsert_tenant_manifest, …}` — graph writers reached through helpers **outside** the `sdk.py` derivation boundary (`pack_manifest_store.py`, `mining.py`, the `hosted_api.py` onboarding/capture/graph-admin helpers). Each must be bound to a tool that is writer-annotated **and** in `WRITE_TOOL_NAMES` (e.g. `upsert_tenant_manifest` → `tortoise_pack_install`). A capability assertion, not just a name list — a reader-labelled tool whose write hides here fails.
- `NON_SDK_WRITER_TOOLS` — the 7 writer-annotated entries with an **empty** `sdk_method` (`session_capture`, `graph_set_recording`, `onboarding_demo_create`, `onboarding_seed`, `onboarding_session_recording`, `onboarding_github_connect`, `onboarding_github_index`) plus the 4 empty-binding reads (`overview`, `get`, `onboarding_state`, `onboarding_github_status`). Asserted exactly equal to the empty-binding set; writer ones writer-annotated + in `WRITE_TOOL_NAMES`.
- `NON_HTTP_WRITER_TOOLS = {tortoise_dream, tortoise_ingest_corpus, tortoise_org_create, tortoise_index_sessions, tortoise_backfill_v25}` — the writer-annotated, `http_policy=False` entries **not** in `WRITE_TOOL_NAMES`. The tool-level non-HTTP exemption in guard 2 is scoped to exactly this set.
- `DANGLING_SDK_DECLARATIONS = {analyze, entity_profile, get_tenant_packs, health, upsert_tenant_manifest}` — the five known-unresolvable `sdk_method` labels (the #3994 `fix-declaration` rows); each must resolve against the **declared boundary** (SDK attr or `NON_SDK_WRITER_OPERATIONS`).
- `UNBOUND_READ_OPERATIONS = {recall_gaps, recall_subgraph, …}` — operations a handler reaches that are reads with no registry binding (they need no binding; enumerated so the binding guard is scoped and non-vacuous).
- `INTERNAL_PATH_READERS` — FS-api users on internal/derived paths excluded from `sdk_filesystem_methods()`.
- `WEIGHT_BEARING_METHODS = {create_point, create_operator, mitigate_operator, file_decision, file_human_approval, diary_write, ingest, checkpoint}` — the 8 wrap sites that pass `abuse_weight` (R1 point-create metering). All other wrap sites must not.

---

## 4. Guards after the change

Operation is the unit; a tool name is *looked up* from the registry, never *asserted* — except the declared write-surface map (§4.4), whose staleness is itself the property under test.

1. **Operator-only capability is HTTP-excluded.** For every operation in `sdk_filesystem_methods() ∪ sdk_operator_only_mutators() ∪ HTTP_EXCLUDED_SDK_METHODS`, **and** for every such operation reached by any tool's `handler_operations()`, that **tool** has `http_policy is False` **and** its handler self-guards against HTTP reach (`_http_excluded_error()`) — because `HTTP_ALLOWED` governs only `tools/list`; the call path (`_wrapped_call_tool` → `_enforce_mcp_tool_scope`) keys on `WRITE_TOOL_NAMES`, so an `http_policy=False` tool **is still callable by name over HTTP** and must self-guard. Closes T2 for both legs.
2. **Write capability is write-classified or self-guarded.** For every operation reachable from a tool's `handler_operations()` that is a `sdk_graph_mutators()` member, the tool must carry a writer annotation **and** be in `WRITE_TOOL_NAMES` — unless (a) the operation is in `READ_THROUGH_WRITE_METHODS`, or (b) the carrying tool has `http_policy is False` **and** its handler calls `_http_excluded_error()` (self-guard), or (c) the tool is in `WRITE_TOOL_NAMES` already. Closes T1 in the value-passed, alias, **and table-dispatch** forms. Complemented by:
   - **(2b)** every writer-annotated tool is in `WRITE_TOOL_NAMES`, **or** carries `http_policy is False` *and* self-guards — the exemption set must equal the exact `NON_HTTP_WRITER_TOOLS` (all five self-guard on HEAD).
   - **(2c)** every empty-`sdk_method` writer is enumerated in `NON_SDK_WRITER_TOOLS`, and every `NON_SDK_WRITER_OPERATIONS` member resolves to a writer-annotated + `WRITE_TOOL_NAMES` tool.
3. **Binding resolution fails closed.** Every non-empty `ToolDefinition.sdk_method` resolves to a real `TortoiseSDK` attribute, modulo the exact `DANGLING_SDK_DECLARATIONS`; every registry entry resolves to a module-level handler; every **guard-relevant** operation reached by a handler (mutator / operator-only / filesystem; `_`-prefixed attrs excluded; unbound reads declared in `UNBOUND_READ_OPERATIONS`) is bound to a tool. Closes T3.
4. **Wrap-site classification is bidirectional** (the issue's explicit ask). The declared `method_to_tool` write-surface map is retained — it is a *maintenance declaration*, and the issue requires the inverse assertion *against a declared map*, so a derived map would make the inverse vacuous. Assertions: `wrapped_methods ⊆ map` (forward); `map ⊆ wrapped_methods` (the missing inverse — a declared write method that lost its wrap site fails); every mapped tool name resolves to a live registry entry (a rename fails **loudly**, not silently); every mapped tool is in `WRITE_TOOL_NAMES`. The wrap-site method set equals `WEIGHT_BEARING_METHODS` ∪ its complement exactly; an unresolvable site fails. Closes T4.
5. **Adapter registration by capability.** The names excluded from HTTP are *derived* from (1) and asserted registered on the MCP adapter (no literal name).
6. **Non-vacuity + falsifiability.** Every derived set has an anchor-in/anchor-out sentinel and an over-inclusion sentinel; each predicate has a synthetic-violation test that must fail it. Each derivation and predicate takes an **injectable synthetic source** (`_PROBE_SRC`, `_SDK_HELPER_DELEGATION_SRC`, `_BARE_IMPORT_SRC`) — the guard predicate is fed a synthetic SDK/handler source and must report the violation. Classes: **T1** read-labelled tool whose handler reaches `create_point` — value-passed, alias, **and table-dispatch** forms; **T2** HTTP tool whose handler reaches `ingest_corpus` (filesystem leg) **and** `org_create` (control-plane leg), plus a body-guard-less `http_policy=False` probe whose handler reaches `create_point` (must FAIL — the HTTP exemption is conditional on self-guard); **T3** stale `sdk_method`; **T4** `_quota_gated(fn)` dynamic site and a per-site weighted/unweighted duplicate.

**Acceptance criteria:** all guards green on the pre-cutover `origin/main`; each predicate demonstrably fails on a synthetic violation for its declared class; and a simulated tool rename makes guard 4 fail loudly (never silently pass).

---

## 5. Findings surfaced (filed, not silently absorbed)

- **#4121** — sibling name-keyed guards in `tests/test_mcp_http.py` (same class; out of scope here, folded in after this lands).
- **#4122** — read-through/self-heal writes on HTTP read-only tools: `tortoise_get_confidence`/`tortoise_compute_confidence` (#1157) **and** `tortoise_packs_list` (`get_tenant_packs` → `ensure_tenant_packs` MERGE). Decision pending; the guard records the current posture as an exact exception.
- `upsert_tenant_manifest` (`pack_manifest_store.py`) is a real graph writer outside the `sdk.py` boundary, reached by the HTTP `tortoise_pack_install` (already `WRITE_TOOL_NAMES`-classified) — covered by the declared `NON_SDK_WRITER_OPERATIONS` capability assertion.

**Derivation boundary (stated, not implied).** The mutator/FS/operator derivations cover `tortoise/sdk.py` and the `mcp_server.py` handler call graph. Writes mediated by other modules (`hosted_api.py`, `mining.py`, `pack_manifest_store.py`, `pack_state.py`) are covered instead by the declared `NON_SDK_WRITER_OPERATIONS` + `NON_SDK_WRITER_TOOLS` + `DANGLING_SDK_DECLARATIONS` capability assertions (and, for `get_tenant_packs`, the #4122 decision). The guard makes this boundary explicit: a new writer outside it must be declared, and a declared writer that stops matching fails. No absolute "no other gap exists" claim is made.

---

## 6. Phase 1.5 — External research (axis: Architecture = standard)

Internal (codebase-first): the closest precedent is `tests/test_mcp_http.py::TestIntrospectiveQuotaCompleteness::test_every_node_creating_tool_is_quota_gated`. **Correct characterization:** it is *not* a validated capability guard — it matches hardcoded method-name substrings (`".create_point", ".supersede_point", …`) against `inspect.getsource`, is non-transitive, and **fails open** on a missing handler (`if fn is None: continue`). It is evidence that source-scan drift guards exist in-repo, and evidence for why this change is needed (same vacuity — filed #4121). This plan does **not** adopt its shape.

External (one query, ⚠️ single-source practitioner + in-repo context → medium): the mutation-testing literature converges on **falsifiability** — a guard must be shown to fail against a seeded violation; "an assertion that survives its own bug is effectively dead". Adopted as the non-vacuity/falsifiability requirement (§3, §4.6). Adoption gate: **adopt** — no recorded decision contradicts it; it aligns with the issue's own "add the missing inverse assertion".

---

## 7. Threat surface (adversarial domain)

**In scope** — the bypass classes this change must make un-reachable through the guards:

- **T1** a merged tool carries `readOnlyHint=True` while one of its operations mutates graph state → a `graphs:read-only` MCP key writes.
- **T2** a merged tool carries `http_policy=True` while one of its operations is operator-only (tenant provisioning / schema migration / filesystem walk) → tenant HTTP reaches operator capability.
- **T3** a write operation is added/renamed in the SDK so the name-keyed guard stops checking it (inert enforcer).
- **T4** a wrap site is moved/merged so the text search misses it → the guard stops checking classification.

**Accepted behaviour per class:** a test that reproduces the class on a synthetic registry/SDK input and asserts the guard's predicate returns a failure; plus green CI on HEAD.

- **T1** covered by guard 2 (handler-derived mutating operation, value-passed, alias, **and table-dispatch** forms → writer classification) + falsifiability tests in all three forms.
- **T2** covered by guard 1 (handler-derived operator-only/FS operation → `http_policy is False` **and self-guarded**) + falsifiability tests for the filesystem leg (`ingest_corpus`), the control-plane leg (`org_create`), and a body-guard-less HTTP-excluded probe.
- **T3** covered by guard 3 (binding-resolution fail-closed) + falsifiability test with a stale `sdk_method`.
- **T4** covered by guard 4 (per-site AST wrap-site extraction against an injectable source, fail-closed on an unresolvable first arg) + falsifiability test with a `_quota_gated(fn)` site.

**Explicitly out of scope** (filed/adjacent, not this change): fixing the read-through writes (#4122); the sibling name-keyed guards in `tests/test_mcp_http.py` (#4121); auditing SDK writers with no MCP binding (e.g. `record_calibration`, `commit_session`) — they have no tool surface to bypass; static derivation across `hosted_api.py`/`mining.py`/`pack_manifest_store.py` (covered by declaration instead); the CPU-cost policy for `dream`.

**Bound:** `[ADVERSARIAL-BOUND]` cycle cap 2; acceptance = every declared class covered by a test + green CI; residuals filed from cycle 1.

---

## 8. Risks & mitigations

- **AST derivation fragility** → non-vacuity + over-inclusion sentinels; injectable sources; derivation failures fail closed.
- **Over-reach (false-red a legitimate surface)** → verified green on HEAD; the tool-level non-HTTP exemption, `INTERNAL_PATH_READERS`, and the every-label operator-only quantifier are explicit; declared sets are exactness-checked.
- **Synthetic-source injection** → each derivation/predicate takes an injectable source string; `lru_cache` is keyed on the source, so a synthetic source never pollutes the real-data cache.
- **Frozen-file conflict** → the change touches only `tests/`; the diff is `tests/` + the (untracked) plan doc; a pre-commit/CI check confirms no production file in the diff.

---

## 9. Plan (task breakdown)

Research path: §6 (external single-source + in-repo context). No third-party deps (stdlib `ast` only) → `writing-plans` Step B skip; Step A consumed §6.

### Task 1 — Capability derivation helper

**Intent:** One place that turns "what an operation does" into a fact, so no guard has to name a tool.
**Acceptance:** `tests/tool_surface_capabilities.py` imports clean as a bare module from both test files; each derivation has anchor-in/anchor-out + over-inclusion unit fixtures: `sdk_graph_mutators()` ⊇ `{create_point, delete, update}` and ∩ `{query, get_point, list_pointkinds}` = ∅; `sdk_filesystem_methods()` ⊇ `{ingest_corpus, index_directory}` (verifies the transitive traversal) and excludes `{_probe_embedded_busy, session_index_health}`; `sdk_operator_only_mutators()` ⊇ `{org_create, membership_create, apikey_revoke, invitation_create}` (verifies the fail-closed quantifier incl. the mixed-label `org_create`); every derivation accepts a synthetic source and reflects it.
**Files:** Create `tests/tool_surface_capabilities.py`; Test: `tests/test_tool_registry.py::TestCapabilityModel`.

### Task 2 — `test_tool_registry.py` guards

**Intent:** Replace the name-keyed exclusion/registration guards with the capability guards (§4.1, §4.3, §4.5).
**Acceptance:** `uv run pytest tests/test_tool_registry.py -q` green; a synthetic operator-only/FS tool with `http_policy=True` fails guard 1; a stale `sdk_method` fails guard 3; the adapter exclusion set is derived (no literal name); the `derived == HTTP_ALLOWED` pin is retained and documented.
**Files:** Modify `tests/test_tool_registry.py`.

### Task 3 — `test_abuse_integration.py` guards

**Intent:** Replace the hardcoded destructive set, the annotation-derived writer set and the `str.find` wrap-site probe with the handler-derived / wrap-site-AST / bidirectional guards (§4.2, §4.4).
**Acceptance:** `uv run pytest tests/test_abuse_integration.py -q` green; a synthetic read-labelled tool whose handler reaches `create_point` (value-passed **and** alias form) fails guard 2; a `_quota_gated(fn)` site fails guard 4; removing a `method_to_tool` entry or dropping a wrap site fails the bidirectional check; the literal `tortoise_onboarding_demo_create` assertion is folded into `NON_SDK_WRITER_TOOLS`.
**Files:** Modify `tests/test_abuse_integration.py`.

### Task 4 — Falsifiability + non-vacuity tests

**Intent:** Prove each predicate can fail (T1–T4), so the guards cannot silently pass.
**Acceptance:** each T1–T4 test constructs its synthetic violation (a synthetic SDK/handler source fed to the derivation/predicate) and asserts the predicate reports the failure; sentinels assert each derived set is non-empty, excludes a known read, and excludes control-plane reads; a rename-simulation asserts guard 4 fails loudly (both directions); `declared_set_violations` asserts every declared entry is live.
**Files:** Test: `tests/test_tool_registry.py`.

### Task 5 — Filed follow-ups; verify

**Intent:** Track the boundary findings.
**Acceptance:** #4121 and #4122 filed and referenced in the docstring/comments; full targeted test run green; `git diff --name-only` shows only `tests/` + the plan doc.
**Files:** no production file (issues #4121, #4122 — filed).

### Integration Surface Map

| Surface | Type | Failure mode if unguarded | Test layer |
|---|---|---|---|
| `TOOL_REGISTRY` ↔ `HTTP_ALLOWED` (`mcp_auth`) | in-process declaration | operator-only op exposed to tenant HTTP | unit (`test_tool_registry.py`) |
| `TOOL_REGISTRY` ↔ `WRITE_TOOL_NAMES`/`_QUOTA_GATED` (`mcp_server`) | in-process declaration | read-only key reaches a write | unit (`test_abuse_integration.py`) |
| `TOOL_REGISTRY.name` → `mcp_server` module global (handler map, `register_all`) | static (AST) | missing handler silently skipped (#2210) | unit |
| `ToolDefinition.sdk_method` → `TortoiseSDK` attribute (guard 3) | static (AST) | stale binding → inert enforcer | unit |
| `mcp_server` `_quota_gated(...)` source ↔ `method_to_tool` ↔ `WRITE_TOOL_NAMES` (guard 4) | static (AST) | wrap site moved/merged → extraction misses it, unmetered write | unit |
| registry `sdk_method` → declared `NON_SDK_WRITER_OPERATIONS` (guard 2c) | in-process declaration | reader-labelled tool hides an out-of-boundary write | unit |
| `TOOL_REGISTRY.name` → `GROUP_BY_NAME` / `_ONBOARDING_TOOL_NAMES` | in-process declaration | rename silently drops group / un-hides onboarding tools | out of scope (grouping pinned by `TestCurationGroups`) |
| `mcp_server` handler call graph | static (AST) | merged write reaches a read-labelled tool | unit |
| `TortoiseSDK` method body | static (AST) | new mutator/FS/control-plane op unclassified | unit |
| no network / DB / external service — tests import modules directly | — | — | — |

---

## 10. Plan-review record

`[ADVERSARIAL-BOUND] cycles=2 threats=4 covered=4 residuals=none`

- **Cycle 1** (2 reviewers): 3×P1 (T4 injection mechanism; declared-map/never-assert-names coherence; value-passed attribute-reference form) + P2s (operator-only complexity, FS over-inclusion, untestable acceptance, §8/§9 drift, sibling guard). All fixed.
- **Cycle 2** (2 reviewers): 3×P1 — the operator-only quantifier was self-contradictory on the mixed-label `org_create`; the table-dispatch merge shape was neither covered nor declared out of scope; and the `http_policy is False` exemption was unsound because HTTP-excluded tools **are callable by name** (only `tools/list` is filtered; the call path keys on `WRITE_TOOL_NAMES`) and must self-guard with `_http_excluded_error()`. All three applied deterministically per the reviewers' own named fixes; the remaining P2s (per-site wrap model, surface-map rows, FS transitive traversal, cross-check domain) are incorporated.

The adversarial bound (2 cycles) was reached on cycle 2, so the post-cycle-2 fixes are **not** re-reviewed here — they are the reviewers' own named corrections, and the actual implementation is independently reviewed at the `commit-workflow`/`code-review` gate.

<!-- plan-review: cycles=2, status=adversarial-capped(applied), version=2.3.0 -->
