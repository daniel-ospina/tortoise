---
title: "Issue #3863 — MCP/SDK surface curation — Scoping"
type: decisions
domain: capability
doc_status: live
created: 2026-09-17
ownedBy: epistemic-team
---

# Scoping — #3863 · MCP tool surface + SDK endpoints: one curated list, and a gate on expansion

**Issue:** [#3863](https://github.com/daniel-ospina/tortoise/issues/3863) (owner-filed, required pre-issue)
**Owner instruction:** 2026-09-17 — *"holy fuck. catastrophic bloat. this needs a dedicated issue and session."*
**Absorbs:** #3835 (which tools to keep) · #3838 (the five non-resolving entries, **reframed**)
**Decided input (§0):** #3836 — when a name *is* retired, the caller receives **the result plus a warning naming the
replacement**; a prerequisite *for this list*, not part of it
**Still open with the owner:** #3837 (what "results with provenance" shows)
**Carried by the orchestrator, not by this issue:** #3849 — the `tortoise_ask` eval-only removal (already built,
serialized B2c-first, does not wait on this list)
**Measured at:** `4488de7d9` (`4488de7d949a5ea586e2b071be528c15d2bcdb78`, worktree `fix/3863-mcp-sdk-surface-curation` ← `origin/main`)
**Status:** SCOPING — awaiting owner approval of this scope before the deliverable list is written.

---

## 0. Decided inputs — what arrives settled, and what arrives unbuilt

These are inputs to the list, not decisions the list makes. Recorded here so the list does not re-open them and
does not mistake a *decided direction* for an *implemented mechanism*.

| Input | State | Effect on the list |
|---|---|---|
| **#3836 — retired-name UX** | **DECIDED** (orchestrator, 2026-09-17): the caller gets **the result PLUS a warning naming the replacement** — nothing breaks, and **the notice IS the measurement** (it tells us who still calls old names) | The list may recommend **FOLD** freely: a folded name stays callable and every caller is told. Two concrete cases exist today (`tortoise_paginated_query`, `tortoise_query_points_by_tag` — deprecated in **source description only**, so no calling agent ever sees it) |
| **#3836 mechanism** | **NOT BUILT.** No deprecation warning is emitted anywhere today (§5) | A decided direction is not an implemented mechanism: the list plans for it, but **FOLD execution still needs the mechanism**. The list must therefore be **phased** (§9) |
| **B2c scoreboard** (`99 / 94 / 5`) | **Corroborated independently** — the list's own execution of the resolution test reproduced 99 registered, 94 resolving, 5 dead declared bindings, and **all five behaviours exist one import away** | B2c's position also inverted to **delete the five declared bindings** (dead bindings and dead pack entries are the same question). Its test is **red by design, unmerged, NOT shipped**, and its **xfail is bound to #3863** — so the list must resolve it or say why not |
| **#3835, #3838** | **SUPERSEDED by this issue** | No removal, rename or binding change may be executed under them |
| **#3849 — `tortoise_ask` eval-only** | **Owner-directed, already decided, carried by the orchestrator**; built and green by B2c; serialized B2c-first | The list reflects it (`ask` = CUT / eval-only) and **does not gate it** |

> This is the **scope**, not the deliverable. Deliverable 1 is the list (one document: every MCP tool and
> every SDK endpoint, keep/cut/fold per entry). Deliverable 2 is the gate. The sequence is the owner's:
> **list → approved → gate → removals/renames.** Nothing is removed, renamed, deprecated or added before
> the list is approved.

---

## 1. What is in scope, and what is not

| In scope | Out of scope (explicitly) |
|---|---|
| Which MCP tools exist; which SDK endpoints exist | What any entry **does** — behaviour is unchanged by this issue |
| The one canonical name per job | #3836 — the retired-name UX (warning vs error) that any removal depends on |
| The duplicate clusters, named, with a keep/cut/fold verdict each | #3837 — the provenance payload in a query answer |
| The registry entries that do not resolve (registry drift) | REST route *internals* — the tenant REST surface is **derived** from the registry (`http_policy` + `RestSpec`), so curating the registry curates it |
| The rule that the surface cannot grow without explicit human approval | The private method bodies in `sdk.py` — internal, not agent-facing |

**The surface is three faces of one declaration.** `tortoise/tool_registry.py::TOOL_REGISTRY` is the single
source: the MCP surface derives from it (`mcp_server.py:3533-3540`), the tenant REST surface derives from it
(`http_policy` + `rest_spec`), and each entry names the SDK method it binds. The SDK is the second
declaration — the public methods of `TortoiseSDK` in `tortoise/sdk.py`.

---

## 2. The measured inventory (reproducible)

Every number below was produced at `4488de7d9` by executing the declaration, not by grepping prose. The
reproduction method is given so the number cannot drift into folklore again.

| # | Quantity | Value | How it was measured |
|---|---|---|---|
| 1 | Registered MCP tools | **99** | `len(TOOL_REGISTRY)`; 99 unique names |
| 2 | Entries with a declared `sdk_method` | **88** | `t.sdk_method != ""` |
| 3 | Entries bound by an in-server handler instead | **11** | `t.sdk_method == ""` — the 7 onboarding tools + `session_capture`, `graph_set_recording`, `overview`, `get` |
| 4 | Distinct declared SDK bindings | **87** | `get_point` is declared twice (`tortoise_get_point`, `tortoise_get_operator`) |
| 5 | Declared bindings that **resolve** | **82** | `callable(getattr(TortoiseSDK, t.sdk_method))` |
| 6 | Declared bindings that **do not resolve** | **5** | §4 — registry drift |
| 7 | Registered tools that are callable end-to-end | **94 / 99** | 82 resolving bindings + 11 handler-bound + 1 duplicate binding = 94 |
| 8 | Public SDK methods (`TortoiseSDK`) | **152** | AST class body ∩ not `_`-prefixed; **and** runtime `dir(TortoiseSDK)` callables (155 public attrs − 3 class constants) |
| 9 | Methods defined on `TortoiseSDK` (all) | **287** | 152 public + 132 private + 3 dunder |
| 10 | Public SDK methods with **no** registry binding | **70** | §2.1 — the SDK-only tail |
| 11 | Entries excluded from the tenant REST surface | **9** | `http_policy=False` |
| 12 | Entries registered on the hosted surface only | **1** | `tortoise_pack_install` (`hosted_only`) |
| 13 | Entries carrying a declared `RestSpec` route | **14** | the explicitly-declared REST routes |
| 14 | Deprecated aliases | **4** | §5 |

### 2.1 Resolution of the 151 vs 255 discrepancy — **the 100-method disagreement**

The tracker ([#1521](https://github.com/daniel-ospina/tortoise/issues/1521), filed 2026-08-20) claims **255
SDK methods**; our working figure is **151**. This is reproduced and resolved exactly — the two numbers count
different things, and both are correct about what they count.

**Where 255 comes from.** `#1521` counts **every method defined in the `TortoiseSDK` class body except
`__init__`**. Measured at the commit in force when the issue was filed (`c4a861ef5`, 2026-08-20):

| Basis at `c4a861ef5` | Count |
|---|---|
| Methods defined in the class body | 256 |
| `− __init__` | **255** ← the tracker's figure |
| …of which **public** (agent-callable) | 135 |
| …of which **private** internal helpers | 118 |
| …of which remaining dunders (`__enter__`, `__exit__`) | 2 |

So **118 of the tracker's 255 "methods" are private internal helpers** — `_create_entity`, `_post_commit`,
`_apply_source_inheritance`, and so on. They are not callable surface, they carry no MCP tool, and an agent
can never reach them. The tracker's "255 SDK methods" is a count of the *class*, not of the *surface*.

**Where 151/152 comes from.** The agent-callable surface: public methods only. At `4488de7d9` that is
**152**. The `151` working figure is one short of the measurement; the single reconciled candidate is
`152 − 1 = ask`, and `ask` is one the owner has **already ruled eval-only**
([#3849](https://github.com/daniel-ospina/tortoise/issues/3849) — remove from the MCP surface *and* the SDK).
That reconciliation is **recorded as inference, not measurement** — no recorded measurement basis for the
151 figure was found in the fleet state.

**Adopted basis for the deliverable list.**

> The document counts the **agent-callable surface**: **99 MCP tools** and **152 public SDK methods**, of
> which **87 are declared as MCP bindings** and **70 are public-but-unbound SDK-only**. The count of
> *methods defined on the class* (287 today; 255 in August) is reported **once, in this section**, as the
> number it is — and never again used as "the SDK surface".

**Why the 70 public-but-unbound methods matter (they are not all noise).** They include genuinely
agent-usable endpoints that simply never got an MCP tool (`supersede_point`, `delete_point`,
`create_or_update_point`, `batch_create_points`, `resolve_id`, `recall_subgraph`, `get_provenance_chain`,
`retrieval_legs`), the org/registry control plane (`org_list`, `graph_delete`, `membership_create`,
`apikey_revoke`, …), and protocol internals (`ulid`, `close`, `test_guard`). The list must classify all 70 —
they are roughly half the SDK surface and they are the half nobody has ever curated.

---

## 3. The duplicate clusters, named

### 3.1 MCP clusters (the ones the tracker named)

Each cluster below is verified by executing the registry and reading each handler body.

| Cluster | Width | Members | The one canonical name |
|---|---|---|---|
| **Fetch a thing by its id** | **4** | `tortoise_get` · `tortoise_get_point` · `tortoise_get_operator` · `tortoise_get_entity` | **`tortoise_get`** — added by epic #888 (PR #912) as the consolidating fetch; the three it consolidates were never retired |
| **Update** | **3** | `tortoise_update` · `tortoise_update_point` · `tortoise_update_entity` | **`tortoise_update`** |
| **Delete** | **3** | `tortoise_delete` · `tortoise_delete_point` · `tortoise_delete_entity` | **`tortoise_delete`** |
| **Create an entity-family node** | **5** (+1) | `tortoise_create_entity` · `tortoise_create_subject` · `tortoise_create_object` · `tortoise_create_event` · `tortoise_create_document` | **`tortoise_create_entity`** — its own description is `type: subject\|object\|event\|document`, i.e. it *is* the other four. Add `tortoise_create_point` and the create cluster is **6 tools for 2 jobs.** |

`tortoise_get` also folds three *semantically distinct* reads — `tortoise_get_events` (a list),
`tortoise_get_session` (by session_id), `tortoise_get_governance` (an ownership query). **Correction
(added after implementation): these three ARE in the cluster.** An earlier draft counted a fetch-by-id
width of four and treated the other three as "kept as distinct reads"; the shipped baseline clusters all
**seven** members and marks every non-canonical one `recommendation: merge` into `tortoise_get`.

Two near-clusters that are **not** duplicates and must not be folded: `tortoise_retract_point` and
`tortoise_invalidate` are **terminal-status / supersession** operations, not deletes — `tortoise_delete*` is
a hard `DETACH DELETE`, retraction is a tombstone. Folding them would be a behaviour change and is out of
scope.

### 3.2 SDK clusters (the same redundancy, one layer down)

The registry-level duplicates sit on top of SDK-level duplicates, and the list covers both:

- **Supersede / invalidate** — `supersede`, `supersede_point`, `invalidate_point` (3)
- **Delete a Point** — `delete`, `delete_point`, `delete_point_wrapped`
  (the registry binds the *wrapped* variant; the raw `delete_point` is unbound and still public)
- **Create / upsert a Point** — `create_point`, `create_or_update_point`, `batch_create_points`
- **Create an edge** — `create_edge`, `create_direct_edge`
- **Fetch** — `get_point`, `get_entity`, `get_events`, `get_session`, `get_owned_entities`

This is the `#1521` "generic + specific + wrapped" pattern exactly as the tracker described it, and the
`*_wrapped` variant is why one cluster member is invisible from the MCP side alone.

---

## 4. The five that do not resolve — **registry drift, not five missing features**

**This is the corrected framing and the document must not carry the old one.** The five behaviours already
exist and are callable. The MCP handlers already call them. What is dead is the **declared SDK binding** —
the `sdk_method` string in the registry points at a method that does not exist on `TortoiseSDK`. **Nothing
an agent can currently do is broken by this**, and `#3838`'s original "build five features or delete five
entries" framing is retracted (it premised on five missing features).

| Registry entry | Declared binding (dead) | The behaviour that exists, one import away | The MCP handler already calling it | Calls observed `[ours]` |
|---|---|---|---|---|
| `tortoise_packs_list` | `get_tenant_packs` | `pack_state.get_tenant_packs` | `mcp_server.py:1029-1030` | 119 |
| `tortoise_pack_install` | `upsert_tenant_manifest` | `pack_manifest_store.upsert_tenant_manifest` | `mcp_server.py:1053-1065` | 0 |
| `tortoise_entity_profile` | `entity_profile` | `navigation.entityProfile` | `mcp_server.py:1740-1746` | 187 |
| `tortoise_health` | `health` | `monitoring.metrics` | `mcp_server.py:1939` | 356 |
| `tortoise_analyze` | `analyze` | `analyze.analyze` | `mcp_server.py:2024+` | 0 |

The resolution test is executed, not inspected: iterate `TOOL_REGISTRY`, resolve `sdk_method` on
`TortoiseSDK`, and treat a handler-bound entry (`sdk_method == ""`) as resolved via its server handler.
Result at `4488de7d9`: **94 resolve, 5 dead** — identical to the B2 lane's refresh run.

> **Evidence caveat.** The call counts in this table are **our own dogfooding traffic** (§6), not user
> evidence. `tortoise_health` ×356 and `tortoise_entity_profile` ×187 are mostly our own test suites and
> probe runs. They establish that the *behaviours* are reached; they do **not** establish demand.

---

## 5. The four deprecated aliases — and the warning that does not exist

| Deprecated alias | Canonical | Declared in |
|---|---|---|
| `tortoise_paginated_query` | `tortoise_query(offset=, limit=)` | `tool_registry.py` (Epic #888) |
| `tortoise_query_points_by_tag` | `tortoise_query(tag=)` | `tool_registry.py` (Epic #888) |
| `tortoise_ingest_corpus` | `tortoise_index_files` | `tool_registry.py` |
| `tortoise_index_sessions` | `tortoise_index_files` | `tool_registry.py` |

All four carry a `description` string beginning `DEPRECATED`, and all four still function. **No deprecation
warning is emitted anywhere today** — not in the MCP response, not in the SDK, not in the REST body. The only
signal is prose in the tool description, which an agent sees only if it reads `tools/list` carefully. **The
ability to warn does not exist yet**; the mechanism that would emit one is #3836's decision and is **out of
scope here**. This is why #3835's recommended option (d) — consolidate but keep the old names working as
aliases that warn — **cannot be executed before this list is approved**.

Two of the four are additionally excluded from the tenant REST surface (`ingest_corpus`, `index_sessions`;
`http_policy=False`) because they walk the server filesystem with a user-supplied path.

Reconciled with §0: **#3836 is decided, the mechanism is not built.** Two of these four
(`tortoise_paginated_query`, `tortoise_query_points_by_tag`) are the concrete cases the decision names — they are
marked in **source description only**, which is exactly why no calling agent sees them today. The list therefore
treats these four as **FOLD-ready once the #3836 mechanism ships**, not as foldable today.

---

## 6. Where every usage number comes from — the honest limit

**There is no real-user usage evidence. The beta has not run.** Nothing in this document may be argued from
cohort behaviour, because there is no cohort.

What does exist, and how it is labelled:

| Source | What it is | Status | How it is labelled |
|---|---|---|---|
| `~/.tortoise/analytics_fallback.jsonl` — `mcp_tool_call` events | **6,385 events, 2026-08-11 → 2026-09-17, 38 distinct tools**; written by `mcp_server.py:188 _emit_mcp_tool_call_telemetry` → `hosted_api._track_analytics_event` (Supabase, with this JSONL as the offline fallback) | **Our own traffic** — test suites, probe runs, dogfooding. Split by `org_id`: null/empty 5,490 · `selfhost` 85 · named orgs 810 | **`[ours]`** everywhere it appears |
| PyPI / registry download or call counts | Not consulted; no such counter exists for this surface | — | — |

Three consequences the document must state rather than hide:

1. **A tool with zero `[ours]` calls is not "unused"** — 64 of the 99 registry entries have zero observed
   calls, and most of them are simply never exercised by our tests. Zero calls is *no evidence*, not
   evidence of no need.
2. **The distribution is distorted by our own suites** — `tortoise_create_point` (1,933), `tortoise_ask`
   (552), `tortoise_assess_source` (386) are test-harness shapes, not demand shapes.
3. **One genuinely load-bearing observation does come from this data**: `tortoise_team_create` was called
   **169** times in our own traffic, and **that name no longer exists in the registry** (renamed by #3622,
   the tenancy rename). That is direct evidence that a retired name can keep being called with no warning —
   exactly #3836's subject, recorded here so it is not lost.

Any keep/cut/fold verdict that leans on usage will say **`[ours]`** and give the count, or will say
**"no evidence"** and give the structural reason (redundant with X / unreachable / deprecated) instead.

---

## 7. The verdict scheme (one per entry, no entry unclassified)

Every one of the 99 MCP tools and 152 SDK methods gets **exactly one** verdict, a canonical name, a reason,
and an evidence tier. The reason is the deliverable; the verdict is only its conclusion.

| Verdict | Meaning | Evidence tier required |
|---|---|---|
| **KEEP** | Already the one canonical name for its job | structural (no sibling covers it) |
| **FOLD** | Redundant; retire in favour of the named canonical, old name frozen in the approved manifest | structural (a named sibling covers it — verified by executing both) |
| **CUT** | Should not be on the agent surface at all | structural (unreachable, eval-only, internal plumbing) or `[ours]` telemetry |
| **UNBOUND-KEEP** | Public SDK method with no MCP tool — keep as SDK-only, explicitly documented as such | structural (used by CLI/hosted/extractor internally) |

The **5 drift entries** and the **4 deprecated aliases** get a verdict like everything else (§4, §5), not a
separate category — the drift entries' verdicts are about the *declared binding*, not the behaviour.

## 8. Deliverable 2 — the gate (after the list is approved)

**Requirement:** the MCP tool surface and the SDK endpoints must not be expandable without **explicit human
approval** — enforced in code, documented, and reusing the fleet's existing declaration-registration gate
shape rather than inventing one.

**The shape being reused** (`scripts/ci/enforce-protocol-table.sh` + `enforcement/dangerous-ops.txt`): a
**declaration manifest** + a **guard that runs in CI** + **bidirectional coverage** — a declaration that is
not registered fails with a named error, and a registered thing that does not resolve also fails. That gate
already runs for dangerous-ops skills in this fleet and enforces exactly this pattern.

**Proposed enforcement, in that shape:**

1. A frozen **approved-surface manifest** (`surface/approved-surface.txt`) — one line per approved entry,
   `mcp <tool_name>` / `sdk <method_name>`, with the approving reference on the line.
2. `tools/surface-guard.py` — compares the manifest against the **live** declaration (`TOOL_REGISTRY` +
   `TortoiseSDK` public methods):
   - a live entry **not in the manifest** → **FAIL**, named (this is the expansion block);
   - a manifest entry **no longer live** → **FAIL**, named (removal also needs approval — matching the
     owner's sequence);
   - an **exemption** is allowed only via a line carrying a **stated rationale** and an approver reference;
     an exemption without a rationale is itself a named failure.
3. Wired into `.pre-commit-config.yaml` + the CI workflow alongside `redis-guard.py`, with a test file that
   pins both fail directions (mirroring `tools/redis-guard.py`'s fixture discipline).

**Serialization to respect (collision pre-flight, §10):** the four surface files are held by the **#3849**
(`tortoise_ask` eval-only removal) workstream, which is **in flight** in `.worktrees/3849-ask-eval-only`. The
gate adds **new files** and only **reads** the registry — it does not edit `tool_registry.py`,
`mcp_server.py`, `sdk.py` or `transport.py`. The manifest baseline still has to be cut **after** #3849 lands
(otherwise `tortoise_ask` is approved and then immediately vanished), and re-checked after the open PRs
touching `sdk.py` land.

## 9. Sequence, and what happens on approval

```
[1] THE LIST   ← researched + written (deliverable 1)             → owner approves
[2] THE GATE   ← manifest + surface-guard + docs (deliverable 2)  → after [1] is approved
[3] REMOVALS / RENAMES / ALIAS RETIREMENTS  ← executed against the approved list only
```

- **Nothing in [3] starts before [1] is approved.** No removal, rename, deprecation or addition.
- **[2] can be built and merged before [3]** — it only freezes the surface, it does not change it. It is the
  thing that makes [1]'s approval actually binding.
- **#3836 is settled in direction but its mechanism is not built** (§0, §5). So [3] is **phased by design**:
  step [3a] retires only the names with a proven replacement and lets the #3836 notices **measure who still
  calls them**; step [3b] deepens the cut using that measurement. That ordering is what makes the notice the
  evidence that sets the aggressiveness, instead of guessing it up front.
- **#3849's `tortoise_ask` removal is owner-directed, already built, and carried by the orchestrator** — it does
  not wait on this list. The list reflects it (`ask` = CUT / eval-only) and does not gate it.

### Coverage the list must resolve

B2c's resolution test is **red by design and unmerged**, and its **xfail is bound to #3863**. The list is not
written until it accounts for that binding: either it resolves the five dead declared bindings in a way the test
would go green on, or the list states the reason it does not.

---

## 10. Collision record (pre-dispatch, all surfaces)

- `python3 tools/collision_preflight.py 3863` → **COLLISION (keyword-only)**, 2 hits: merged PR **#3235**
  and branch `fix/2938-curate-surfaces`, both matching `curate`/`surface`. **Verified homonym, not live work
  on #3863**: #3235 is the *CI test-selection* surface manifest (`ci_selection.py`), an unrelated meaning of
  "surface". #3235 is MERGED and the branch is merged into `main`. **Proceed.**
- `python3 tools/collision_preflight.py 3849` → **COLLISION (exit 1)** on branch + worktree
  `.worktrees/3849-ask-eval-only`. Confirms #3849 is **in flight** and holds the serialization on the four
  surface files. Handled in §8.
- **Open-PR file scan** (59 open PRs at this snapshot, all enumerated): no open PR holds `tool_registry.py` or
  `transport.py`; **8 PRs touch `sdk.py`** (#2747, #2948, #2949, #3131, #3426, #3596, #3722) and **1 touches
  `mcp_server.py`** (#3780). None of them adds a registry entry on inspection.
- **Orchestrator verification (2026-09-17), the authority for "nothing is racing this list":** across the open
  PRs, four touch a declared-surface file — #3780 `mcp_server.py`, #3851 `mcp_auth.py`, #3722 `sdk.py`,
  #3596 `api.py` + `sdk.py` — and **NONE adds a tool or an endpoint**. **No surface expansion is in flight
  anywhere.** Both scans agree on the conclusion; they differ only in the open-PR count at their snapshots.
  Consequence: the approved manifest baseline can be cut once #3849 lands, with no need to wait on other lanes.
- **Reference correction (orchestrator context, 2026-09-17):** the fleet context described the `tortoise_ask`
  eval-only removal as **#3842**. **#3842 is CLOSED** — it is the earlier, superseded "does `ask` ship switched
  on" decision, closed as CORRECTED. The removal issue is **#3849** (OPEN, owner B2c, worktree
  `.worktrees/3849-ask-eval-only`). This document cites **#3849** throughout.

---

## 11. Open decisions for the owner at scope approval

| # | Decision | Recommendation |
|---|---|---|
| **S-a** | **Counting basis.** Adopt "agent-callable surface" — 99 MCP tools + 152 public SDK methods — and report the 287/255 class-method count exactly once (§2.1)? | **Yes** — the only reproducible basis, and the only one that describes what an agent can call |
| **S-b** | **Does the list classify all 70 public-but-unbound SDK methods**, not just the 87 registry-bound ones? | **Yes** — they are roughly half the SDK surface; leaving them out repeats the original mistake |
| **S-c** | **The 151 → 152 reconciliation** (`152 − ask`, already ruled eval-only by #3849) recorded as *inference*, since no measurement basis for 151 was found? | **Yes** — recorded as inference, never asserted as measurement |
| **S-d** | **Include our own dogfooding telemetry as `[ours]` evidence**, explicitly not cohort evidence, with the 64-zero-call caveat stated? | **Yes** — hiding it would be worse; labelling it is the requirement |
| **S-e** | **Gate strictness.** Bidirectional freeze (block additions *and* removals) or additions-only? | **Bidirectional** — the owner's own sequence forbids unapproved removal too; this means the manifest is deliberately re-cut after #3849 |
| **S-f** | **Document home.** `docs/product/mcp-sdk-surface.md` (sibling to `docs/product/answer-surface.md`), registered in `docs/00_index.md`? | **Yes** |
| **S-g** | **Phasing.** Write the list with an explicit **phase 1 (fold only where the replacement is proven) / phase 2 (deepen using the #3836 notices)** split, rather than a single flat cut depth? | **Yes** — #3836's notice *is* the measurement, so the cut depth should be set by it, not guessed |
