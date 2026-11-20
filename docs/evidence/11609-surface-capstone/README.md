---
title: "MCP/SDK surface capstone — #13036 (the 27/40 verification)"
type: evidence
domain: platform
doc_status: live
created: 2026-11-20
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#13036, #11609, epic #4282, docs/product/canonical-mcp-tools.md, docs/product/beta-sdk-surface.md, tools/surface_capstone_probe.py, tools/surface-target-check.py"
---

# The surface capstone (#13036) — every approved tool called, every method called

**Measured at `fe4381ae65`** (this branch; `origin/main` was `a296e6741e` when the
lane started). Every row below carries the command that produced it.

## Verdict, in one line

**NOT 27/40 — the SDK half is complete (40/40 declared names resolve, 39 live, 0
surplus), and the MCP half is 40 live tools against an approved 27: four approved
names have no tool of that name (three carry the capability under a different door,
**one — `get_historical_knowledge` — has no MCP door at all**) and seventeen live
names are off-target, each held because the destination it should retire into is not
live yet.**

So this capstone does **not** close `#13036`. It closes the *verification*: the
surface's compliance state is now measured rather than asserted, the tool that
measures it is committed, and the remaining gap is enumerated name by name with an
owner.

## How to reproduce

```bash
uv run python tools/surface-target-check.py                    # V1 — the counts
uv run python tools/surface_capstone_probe.py                  # V2 — one real call per approved name
uv run python tools/test_lane.py run -- tests/test_13036_capstone_v2.py -q   # V2 — payload contract, per arm
uv run python tools/bridge_table.py --check                    # V4 — the discriminator table
uv run python tools/test_lane.py run -- \
  tests/test_11609_read_containers.py tests/test_11609_write_containers.py \
  tests/test_11609_sources_containers.py tests/test_11609_confidence_arms.py \
  tests/test_11609_create_arms.py tests/test_11609_door_retirement.py \
  tests/test_11609_structural_blockers.py tests/test_11609_sdk_40.py -q      # V4
uv run python tools/surface-guard.py                            # V6
uv run python tools/sdk_surface_declaration.py check             # V6
uv run python tools/surface_manifest.py check                    # V6

```

`tools/surface_capstone_probe.py` is new in this lane. It is the instrument this
issue asked for: **one real invocation per approved name**, MCP through
`create_http_app` (the same authenticated tenant door `hosted_api` mounts — bearer
auth, org resolution, `http_policy` exclusion and the transport wait bound all
apply), with the latency of the call itself and a shape verdict that reads the
SEEDED content back out of the answer.

---

## V1 — the targets are met? **FAIL (MCP leg). SDK leg PASS.**

```text
$ uv run python tools/surface-target-check.py     # exit 1
  target:    27 MCP tools / 40 SDK methods
  live:      40 MCP tools / 40 SDK methods   ← the DECLARED names that resolve
             SDK declaration: 40/40 names resolve (39 of 39 method rows + the row-1 entry point)
             fallback view (not the verdict): class carries 39 public SDK method(s)
  MCP tools    live   40 → target   27   (Δ +13, 1.5x) OVER TARGET
  SDK methods  live   40 → target   40   (Δ +0, 1.0x) OK

```

Three tools, three different counting axes, and **none of them is 27**:

| axis | measured | what it answers |
|---|---|---|
| `TOOL_REGISTRY` rows | **40** | what the server can answer |
| `tools/list` advertised over tenant HTTP | **35** | what an agent SEES (5 rows are `http_policy=False`) |
| approved target | **27** | the owner's ruling (#3540, 2026-10-17) |

### The diff, in BOTH directions

`APPROVED` is `docs/product/beta-sdk-surface.md` rows 3–27 + row 40 + the
`graph_set_recording` ruling + the #3540 `manage_sources` amendment — the
composition `docs/product/canonical-mcp-tools.md` states at 27.

**Missing — 4 approved names have no live tool of that name:**

| approved | what is actually served | owner |
|---|---|---|
| `get_historical_knowledge` | **nothing** — `TortoiseSDK.get_historical_knowledge` exists and answers (SDK row 7 PASS) but no MCP tool binds it | **`#13036`** — this needs a new tool, which is a SURFACE CHANGE and therefore needs Daniel's approval first (`AGENTS.md` → MCP/SDK Surface Approval) |
| `poll_events` | `tortoise_events_poll` (registry `sdk_method=poll_events`) — capability present, NAME differs | `#13036` (rename) |
| `link_entities` | `tortoise_create_edge` + `tortoise_create_operator` — capability present, SPLIT in two | `#13036` (consolidate) |
| `delete_knowledge` | `tortoise_delete` — the name difference is the RULING: owner 2026-10-25 (#9660), *"the absorbed names stay retired-forward entries, not new siblings"*, canonical row 18 executed by keeping the single entry | none — **by ruling, not a gap** |

**Extra — 17 live names are off the approved list**, and every one is a *held* door whose
recorded reason is a **checked population**, not prose: `tests/test_11609_door_retirement.py`
holds the two tables and asserts `len(PROTECTED) + len(NO_CONTAINER_YET) == 17`, so a live
door dropped from either table reds there instead of surviving as a comment.
(`config/surface-manifest.yml`'s registry block quotes the same composition; it is not the
definition — the tables are.)

* **`NO_CONTAINER_YET` — 7**, whose destination is not live on `main` yet: `delete`
  (`delete_knowledge`, executed by *keeping* `tortoise_delete` — the #9660 ruling),
  `create_edge` / `create_operator` (`link_entities`), `events_poll` (`poll_events`),
  `ingest` (`write_knowledge_batch`), `list_graphs` (inside `overview`), `org_create`
  (account tenancy is not on the MCP — settled 2026-09-22).
* **`PROTECTED` — 10**, retained by a property this file READS BACK out of the code rather
  than by a missing destination: eight keep their `-> list[dict]` envelope (`search_sessions`,
  `suggest_entry_points`, `list_batches`, `list_namespaces`, `diary_read`,
  `expand_relationships`, `check_structure`, `calibrate_summary` — the #8482 repair), `search`
  is held by that same envelope (`GET /v1/search` is a hand-written route its row does not
  create, so the row's `rest_spec` cell is a RECORD, not a retention reason), and `query` is
  the paged destination of two already-retired pointers.

`calibrate_summary` · `check_structure` · `create_edge` · `create_operator` ·
`delete` · `diary_read` · `events_poll` · `expand_relationships` · `ingest` ·
`list_batches` · `list_graphs` · `list_namespaces` · `org_create` · `query` ·
`search` · `search_sessions` · `suggest_entry_points`

23 approved-and-live + 17 off-target = 40. Reaching 27 is therefore
`40 − 17 + 4 = 27`. **Seven of the 17 are blocked on a destination arm that does not exist
yet** (e.g. `tortoise_ingest` waits for `write_knowledge_batch`); the other **ten** are
retained by a recorded property — a `-> list[dict]` envelope, or a paged destination — not
by a missing destination, so a lane that retires one has to remove the property that holds
it, not wait for a container.

> ⚠️ **The `40` is not the `+13` the tool prints.** `surface-target-check.py` compares
> counts, so a name MISSING and a name EXTRA offset each other: the true gap is
> **4 missing + 17 extra**, not 13 of either. The count axis cannot see this, and
> saying "13 over" would be the same defect `#4185` was filed about.

## V2 — every one of the 27 tools answers? **22 answered, 4 measured refusals, 1 absent**

Full table: [`probe-tables.md`](probe-tables.md) (generated, one row per approved name).

```text
$ uv run python tools/surface_capstone_probe.py
  tools  answered: 22/27   (refused-by-design 4)
  SDK    answered: 27/40   (unmeasured 12)
  retired shim: 22/22 families publish the notice; 9/9 archived names are not-found

```

* **22 answered** with the seeded content readable out of the payload — 19 at the
  exact approved name, 2 through the door named in the missing table above
  (`poll_events`, `link_entities`), 1 through the #9660 kept entry
  (`delete_knowledge` → `tortoise_delete`, driven preview-then-confirm: `deleted:
  true` and the row is gone on read-back).
* **4 REFUSED — a measured answer, not a skipped one:**
  * `index_sources_from_directory`, `mine_knowledge_from_directory`,
    `refresh_confidence` → `-32004 This tool is not available over HTTP`, which is
    the registry's own `http_policy=False` doing its job. The capability is served
    by the SDK / hosted REST; recorded so nobody reads "refused" as "broken".
  * `approve_merge` → *"Point … is not a dedup/temporal candidate"* — the refusal
    arm. Its SUCCESS arm needs an ambiguity pair the product refuses to manufacture
    (`create_object` dedups on `canonical_name`), and it is recorded as **ARM GAP**
    rather than passed.
* **1 absent** — `get_historical_knowledge` (above).

Independent confirmation at the ARM level: `tests/test_13036_capstone_v2.py`
→ **123 passed**. That suite drives one real invocation per LIVE tool through the
same authenticated transport and asserts the payload per discriminator arm
(`type=`, `kind=`, `section=`, `mode=`), with 4 `UNDRIVEN` entries whose refusal it
also drives. It is dual-registered in `config/ci-surfaces.yml` (`core` + `api`).

### The 40 approved SDK methods — 27 answered, 12 unmeasured, 1 arm gap

| class | n | detail |
|---|---|---|
| answered in-process | **27** | rows 1–27 of `tortoise/__all__.py`; e.g. `check_confidence` (0.32 s), `record_decision` (0.87 s) — latencies in the table |
| `UNMEASURED — control plane` | **12** | rows 28–39 (memory graphs and keys, members). They call the Supabase control plane, which needs a hosted credential this box does not carry. Calling them against an embedded store would measure a different code path than a customer meets (`#2952`) |
| `ARM GAP` | **1** | `approve_merge` — success arm unreachable on any seed the product will accept |

One asymmetry worth recording, because it is the kind of thing a payload table hides:
`TortoiseSDK.check_confidence(claim_id=…)` with its DEFAULT
(`require_calibration=None`) **raises** `CalibrationError` on a graph that carries
uncalibrated points, while the MCP door for the same row answers. With
`require_calibration=False` the method answers too (`{mean, variance, alpha, beta,
effective_n}`). Both halves are in the table.

## V3 — the retired names still answer? **PASS — 22/22 families publish the notice, 9/9 archived names are not-found**

The per-name table is [`probe-tables.md`](probe-tables.md) §V3; it is driven by the
committed probe, one retired name per destination family with a request that is
VALID for the door's own old signature.

* **Population: 68 shim-served + 40 advertised = 108 resolvable names**
  (`RETIRED_USE_INSTEAD` has 68 entries, and `bridge_table.py --check` independently
  counts "108 registry tools").
* **One retired name per destination-container family, 22 of 22 families:** every one
  published the #3883 notice, in BOTH places it is published —
  `_meta.tortoise.retired` (`{name, retired, use_instead, message}`) and a second
  `content` block carrying `RETIRED TOOL: … Call <replacement> instead`.
  18 of the 22 also returned their previous behaviour in the same call; the other 4
  refused for a reason that is itself the documented answer — `-32004` for the three
  `http_policy=False` doors (`tortoise_index_files`, `tortoise_mine_conversations`,
  `tortoise_dream`) and a named precondition for
  `tortoise_onboarding_github_index` (*no GitHub connection*). The probe records the
  refusal rather than scoring it as a broken shim.
  Examples:

```text
  tortoise_get            → `_meta`: use_instead="tortoise_get_entity(id, type=...)"  + the get_entity payload
  tortoise_list_batch     → use_instead="tortoise_list_knowledge(kind=\"batch\", id=...)"
  tortoise_paginated_query→ use_instead="tortoise_query(offset=..., limit=...)"
  tortoise_issue_insight  → use_instead="tortoise_search_knowledge(title=..., body=..., mode=\"issue_insight\")"

```

  **All 68 `use_instead` pointers name a tool that is LIVE in `TOOL_REGISTRY`**
  (measured: 68/68, 0 pointing at a retired or absent name) — a pointer to a retired
  name would be a dead end, and one hop is the shim's documented limit.

* **Archived-without-destination names (9) all return the documented not-found**
  `Unknown tool: '<name>'`: the four `archive/mcp-tools/b1-removed-no-destination/`
  tools (`tortoise_analyze`, `tortoise_backfill_v25`, `tortoise_packs_list`,
  `tortoise_pack_install`) and the five removed `tortoise_onboarding_*` tools. No
  shim, no notice, no silent empty answer.

## V4 — no capability silently dropped? **PASS, after repairing its own suite**

```text
$ uv run python tools/test_lane.py run -- tests/test_11609_*.py -q
  450 passed  (read/sources/confidence/create containers + door_retirement +
               structural_blockers + sdk_40)
  44 passed   (write containers)
$ uv run python tools/bridge_table.py --check
  OK: docs/product/bridge-table.md is current (108 registry tools)

```

⛔ **This row could not be measured when the lane started**: 4 of the write-container
cases were RED on `origin/main` (`a296e6741e`), so the suite that owns "no absorbed
capability was dropped" was itself failing. Repaired in this lane (test-side only —
no product code, no surface change):

| failure | cause | verdict |
|---|---|---|
| `…retract_arm_delegates_to_the_sdk_retract_point` | the test built its stub with `retract_point=`, the name #12901 PRIVATISED | stale stub — the product is fine: over the door, `tortoise_update_knowledge(id, valid_until=…)` lands `status: "retracted"` |
| `…invalidate_arm_delegates_to_the_sdk_invalidate_point` | same, `invalidate_point=` vs `_invalidate_point` | stale stub |
| `test_action_mitigate_CARRIES_credibility` | same, `mitigate_operator=` vs `_mitigate_operator` | stale stub — over the door, `tortoise_adjust_relationship(action="mitigate", credibility="high")` carries the credibility into the mitigation Point |
| `test_no_production_module_reaches_a_retired_sdk_name` | the census matched a retired NAME on ANY receiver, and the retired names collide with the Cypher client's own methods (`query`, `list_graphs`, `link_source_to_entity`): HUNDREDS of offenders, 0 SDK call sites | test-instrument false red; narrowed with each ambiguous name MEASURED, and the residual declared in the code |

The absorbed-capability arms themselves are unchanged and green, including the ones
that would catch a container that dispatches on `kind=` but ignores three of its
members (substitution to per-arm payload assertions in
`tests/test_13036_capstone_v2.py`).

## V5 — the ontology holds? **PASS on the reachable graph; the fleet graph is UNMEASURED**

```text
tortoise_graph_overview(section="pointkinds")
  → [{"kind": "statement", "count": 4, "pack": ""},
     {"kind": "decision",  "count": 1, "pack": ""}]

```

`statement` dominant, `pack: ""` where no pack kind is used — the shape `#10977`
records. Static check: no container name is written as a `pointKind` anywhere in
`tortoise/` (searched the 17 off-target container names against `pointKind=` /
`"pointKind":` literals — zero hits).

⚠️ **UNMEASURED — the fleet graph** (`app.premiselabs.co`): this box carries no
tenant credential (see *Production reachability* below), so the row is measured on a
throwaway embedded store rather than on the graph the fleet writes. A row that cannot
be measured is recorded as itself, not as a pass.

## V6 — docs and declaration cannot drift? **3 of 4 gates green; the 4th needed a RE-CUT; the target gate is not in required CI**

| gate | result |
|---|---|
| `tools/surface-guard.py` | **OK** — declaration matches the frozen baseline: 40 MCP tools, 39 public SDK methods (0 exempt) |
| `tools/sdk_surface_declaration.py check` | **OK** — the declaration is the owner's 40, the live class matches exactly |
| `tools/bridge_table.py --check` | **OK** — current, 108 registry tools |
| `tools/surface_manifest.py check` | **OK after a re-cut** — see below |
| `tools/surface-target-check.py` in required CI | **NO** — deliberately outside it while the gap is open, and the gap IS open |

**The re-cut, stated plainly.** `surface_manifest.py check` was RED on this branch
with 7 rows reported "hand-edited", and GREEN on `origin/main` — because the new
`tools/surface_capstone_probe.py` CALLS seven approved SDK methods, so the caller
scan now derives `other-tooling` where it previously derived "none outside tests".
The fix is the tool's own maintenance path (`surface_manifest.py cut`), and the diff
is **derived rows only**: 7 rows' `class`/`dependency`/`reason` + `cut_at_commit`.
`counts`, `high_water_counts` and every per-row `approved` flag are unchanged —
verified in the diff, so this is not "editing the manifest to make the check pass",
and no surface decision moves.

---

## Production reachability, recorded because it bounds what could be called

```text
$ curl -s -m 45 -w '%{http_code} %{time_total}' https://api.premiselabs.co/health
  200  2.6 s   "status": "degraded"    db.ok=true (155 ms / 560 ms)
               refusals: 19-24 in 120 s, by_surface {"mcp": 0, "rest": 19-24}, refusing=true
               loop_blocks: 23 episodes, last 5.19 s in socket/ssl connect
$ curl -s -m 20 -X POST https://api.premiselabs.co/mcp ...              → 401 in 3.4 s

```

* The MCP door is ALIVE (a 401 with the documented bearer message in ~3 s), and its
  refusal counter is **0** in every sample while the REST surface is being refused
  (`rest: 19-24`). That is consistent with the global order's reading and is worth
  saying: the surface this issue is about was NOT the surface refusing.
* **No tenant credential exists on this box**, so no authenticated call against
  production was possible — the tenant door was exercised in-process instead
  (`create_http_app`, the same app `hosted_api` mounts). This is the same limitation
  the 12 control-plane SDK rows carry, and it is why V5's fleet-graph half is
  `UNMEASURED`.
* `GET /v1/search` 504s (#10893) were therefore not exercised, and **no failure in
  this document is attributed to the search blocker** — nothing here depends on it.

## What remains, and who owns it

| # | what | owner |
|---|---|---|
| 1 | `get_historical_knowledge` has no MCP tool — an approved capability an MCP caller cannot reach | **`#13036`** (surface change → needs Daniel's approval first) |
| 2 | 17 off-target live tools, each held on a missing destination arm; 4 approved names absent (2 of them name-spellings) | **`#11609`** (the retirement waves) — this document is its capstone evidence |
| 3 | 12 approved SDK methods unmeasured (control plane) | needs a hosted credential on the box; re-run `tools/surface_capstone_probe.py` where one exists |
| 4 | `approve_merge` success arm unreachable from any seed the product accepts | recorded ARM GAP; a dedup-candidate seed is the missing piece |
| 5 | V5's fleet-graph half | same credential blocker |
| 6 | production REST refusals / `GET /v1/search` 504 | **`#10893`** |
