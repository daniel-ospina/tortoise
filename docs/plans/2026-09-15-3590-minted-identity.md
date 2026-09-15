<!-- research-path: docs/epics/2026-09-10-2835-capability-registry/identity-decision.md -->
---
title: "#3590 — implementation plan (minted entity identity)"
type: engineering
subjects.team: epistemic-team
domain: platform
doc_status: draft
aboutSubjects: tortoise
aboutObjects: entity-identity
created: 2026-09-15
---

# Minted Entity Identity Implementation Plan (#3590)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Issue:** [#3590](https://github.com/daniel-ospina/tortoise/issues/3590) · **Epic:** [#2835](https://github.com/daniel-ospina/tortoise/issues/2835) · **Tier:** Complex
**Decision doc (the design this plan implements):** `docs/epics/2026-09-10-2835-capability-registry/identity-decision.md` (merged in PR #3584)
**Branch / worktree:** `feat/3590-minted-identity` · `.worktrees/feat/3590-minted-identity`
**Line-reference pin:** all `file:line` below resolve at worktree HEAD `f976d941d` (base `origin/main` @ `16cf6009d`). `git diff 1d53dfef8 f976d941d -- tortoise/sdk.py tortoise/projection/ tortoise/commit_ops.py tortoise/mcp_server.py` is empty; only `tortoise/hosted_api.py` moved (+42/−31) — so the `sdk.py`/`projection/` coordinates are valid on both, and the `hosted_api.py` ones resolve at `f976d941d` only.

**Goal:** Give every `Object`/`Subject` a durable opaque id minted once at creation, demote `name` to a mutable natural key held in a lookup index, move the `MERGE` key for those two labels from `{name}` to `{id}`, journal the real id in every event, journal renames as mutations on the stable id, journal deletion as a terminal event, and retire the derived id `obj-<sha26(name)>` — so the #3573 burial class is structurally impossible rather than handled.

**Team:** epistemic-team
**Architecture:** Five independently-mergeable slices over a live/dead-simple premise — **we start fresh**. There is no migration, no backfill, no dual-write and no expand/contract staging, because no Object/Subject exists anywhere in any production graph and no production Object/Subject journal exists (D8 below proves both). Slice 0 lands the `rebuild == live` + empty-non-folded-set invariant as a test harness (the load-bearing half of #3585; its fail-closed half is wired into the replay paths — D10). Slice 1 moves every `Object`/`Subject` write path onto one key (`id`) **and routes every mention through the name→id resolver**, while the mint is still `_entity_name_id`'s derived value — so same-name creates still yield one node with the same id, and the *producer-side* "second carrier" class (#3389) is closed: a projection stub and the canonical registration can no longer land on two nodes. (The delta from today is narrow but real and is **not** byte-identical: a mention that supplies no id resolves to the canonical node instead of minting a random-id twin. See P1-3 in the slice.) Slice 2 flips the id source from derived to minted and makes deletion terminal — the semantic core. Slice 3 journals rename. Slice 4 retires the derived id and the documented contracts that advertise it.

---

## Provenance, and the two deviations you should know about

| Item | Status |
|---|---|
| `issue-scoping` signature on #3590 | **ABSENT** — `gh issue view 3590 --comments` carries no `<!-- issue-scoping:`/`<!-- issue-planning:` marker (the issue has 0 comments). The workflow prescribes stopping here. **Deviation, declared:** the ratified design already exists as `identity-decision.md` (merged in PR #3584, the epic's Human Gate #1 answer) and the issue body already carries Goal / Scope / Explicitly-NOT-in-scope / Acceptance. That artifact *is* the requirements capture this gate exists to guarantee; re-running `issue-scoping` would re-litigate a ratified human gate. Proceed, and note the gap so downstream reviewers can judge it. |
| Epic doc reference (`**Epic:** docs/epics/…`) | Not present as a line in the issue body (the issue links epic #2835 as prose). The epic's architecture contract is the decision doc, cited above and read in full for this plan. |
| UI / UX gate (`workflow/01.5`) | **Skipped** — pure backend/data change; zero files under `src/components/`, `src/pages/`, `src/app/`, `src/layouts/`. |
| Collision pre-flight (`tools/collision_preflight.py 3590`) | `exit 1 COLLISION`, and the two hits are **self-referential**: `refs/heads/feat/3590-minted-identity` and this plan's own worktree. No open or closed PR mentions #3590 (`gh pr list --search 3590 --state all` → `[]`); the branch has no remote (`git ls-remote --heads origin 'feat/3590*'` → empty). No in-flight duplicate work. |
| `parallel_work_check.sh plan` | `C3: CLEAR no-board-skip: no board session — open-PR overlap check skipped` (`exit 0`). |
| `plan-review` gate (`workflow/05`) | **NOT YET CLEAN.** This document is `doc_status: draft` and carries no `<!-- plan-review: status=clean -->` signature. Adversarial review cycles r1–r3 have run (2026-09-15); each returned 5 P1s + P2s, **all accepted and applied in place** — see the `Review r1`/`r2`/`r3` rows below. The plan-review loop has **not** re-run to convergence after r3, so the document stays `draft`; Execution Handoff must not proceed until a fresh plan-review cycle runs clean. |
| Review r1 (2026-09-15) | **5 P1s + P2 findings applied; all accepted.** P1-1 deletion semantics unified to a tombstone on both live and replay (D5, S2 step 6/7). P1-2 R5 no-id-reuse enforced, not asserted (`IdReuseError` live + `tombstoned_ids` non-fold replay; D5, S2 step 3/6a/7). P1-3 S1's "byte-identical" claim withdrawn; resolver moved into S1 and four mention/stub tests re-derived there (S1). P1-4 D8 evidence re-verified across all three FalkorDB instances and the guard re-keyed from volatile counts to a `STARTS WITH`-based predicate (no `=~`). P1-5 #3590's fail-closed precondition wired into `rebuild`/`rebuild_all`/`recover_from_log`; id-keyed misses recorded; `materialize()` extended with the edge set. P2s: the name-keyed supersession fallback added to §C, rename onto a live holder refused (D3), the three `id OR name` union sites dispositioned (§B.1), the resolver race closed with a per-`(label,name)` lock, "live holder" defined against `live.TERMINAL_EXCLUDED_STATUSES` (D2), the non-existent `OBJECT_SEARCH_EXCLUDED_STATUS` replaced by the real symbols (U5), and all wrong `file:line` coordinates corrected (see every `S1`/`S2`/`S4` step). |
| Review r2 (2026-09-15) | **5 P1s + 8 P2s applied in place; all accepted. The loop has NOT re-run to convergence — `doc_status` stays `draft`.** P1-A the S4 guard made per-label (`(n:Object AND n.id STARTS WITH 'obj-') OR (n:Subject AND n.id STARTS WITH 'sub-')`) and D8's evidence table now records **both** totals; a live re-sweep confirmed 1 legacy Subject (D8). P1-B acceptance (a) made true, not scoped away: both retained name→id producers (`mining._object_id`, `_connect_issue_objects`'s fallback) become resolver **callers** (S1 key-parity via `_entity_key`, S2 mint-flip), and R5's reachability is re-grounded on the explicit-id channels, not mining's determinism. P1-C R5 moved to the `_upsert_object`/`_upsert_subject` choke point with one test per id-writing channel; `_connect_issue_objects`'s raw Cypher carve-out is closed explicitly. P1-D the S1 "adopted by resolution" claim and the "second carrier class dies structurally" headline moved to S2, where the resolver actually exists (`create_entity`'s Object/Subject branch is not on S1 step 6's three-site list, and id-keyed `MERGE` alone turns a raw name-stub into a second live same-name node — the two stub-adoption tests now live in S2). P1-E S0's non-vacuity owner named: S0 itself adds the recording-only `non_folded` recorder + `materialize()` hook, so its self-test has a source of non-folds (it is no longer "pure test addition"). P2s: `_LIVE_HOLDER` delegates to `live._terminal_excluded` (the literal treated `status IS NULL AND outdated=true` as live, and — worse — excluded `status='live'` with `outdated` NULL via three-valued logic) with the missing vector; the double-delete live arm gated on the live-holder predicate; every `id OR name` union site covered regardless of `=` vs `IN` (§B.1) and the descope sweep widened; `recover_from_log` keeps its report-never-raise contract and `_recover_or_raise` is the fail-loud caller (the finding's `_recover_or_fail` is a name mismatch — verified); `materialize()` derives its compared set from the model minus an explicit volatile allowlist, with a falsifying negative test; `apply()` and `rebuild_all`'s pass-1b gain an explicit unhandled-type recorder plus a vocabulary-parity test; the terminal `Deleted`/`Renamed` append is made fail-loud (not pinned to a swallow); and S4 is split so only the `_entity_name_id` deletion is gated by D8 (#3574 and #3586's missing test filed as standalone micro-issues). |
| Review r3 (2026-09-15) | **5 P1s + 6 P2s applied in place; all accepted. The loop has NOT re-run to convergence — `doc_status` stays `draft`.** P1-A the ninth-cycle seed: `_create_entity`'s canonical-id re-fetch **by name with no status filter** (`sdk.py:17006-17022`) was the one surviving name-keyed pick — not in §B, not in any slice's Files list, and hidden from both machine sweeps by its f-string label. It is now in §B with disposition **DELETE in S1** (under the id-keyed `MERGE` the fresh id always lands), with a delete-then-recreate regression pin and the S1 source-level assertion widened from `MERGE` only to name-keyed **reads**. P1-B R5's predicate is now **deletion-specific on BOTH engines** (live: a deletion marker — `status='retracted'` and/or `deletedAt IS NOT NULL`; replay: `tombstoned_ids`) instead of `TERMINAL_EXCLUDED_STATUSES`, which conflated supersession with deletion and would have raised live on the #1350 re-mention no-op (`tests/test_object_registered_journal.py:199-228`, now in S2's test table). P1-C `deletedAt` is now **carried in the `Deleted` payload** (one `ts` captured in `_delete_entity`, threaded into the live `SET`, the emitted line and `_fold_deleted`) so it **stays** in `materialize()`'s compared set, pinned by a falsifying negative test. P1-D the Atomicity claim corrected to option (b): post-apply emission **cannot** guarantee "never live-without-durable"; the claim is withdrawn, the residual (tombstone-live + journal-absent, the retry-returns-`False` semantics, journal-configured lanes only) is documented, and the test asserts only what is achievable. P1-E the journal-vocabulary parity test scoped to the dispatch surface the plan actually routes, the vocabulary **partitioned three ways**, and the pre-existing `apply()` gap (`PointSuperseded`/`PointInvalidated` cannot replay supersession) **filed as its own issue** rather than silently absorbed by S0. P2s: D8's predicate widened to **four** shapes (`obj-`, `sub-`, `obj_`, `id == name`) and **re-swept live this session** (8 hits, 3 of them `id == name` Subjects the old predicate could not see); §A.1 gains the two missing id-writing channels (`hosted_api.py:9403`, `onboarding/state.py:401`); D5's mixed `:Point:Object` claim made true by an explicit label-skip (the `Point` arm hard-deleted first); D9's `sdk.py:11508` site routed through `_emit_event` in S2 (its bare `proj.apply` left the Event + `performs` edge live-only) and added to U3; the lock claim weakened to **closed within a process** with the cross-process residual named in U7/D2; and the journal-growth cost of emit-on-every-call stated. |

---

## The model in one page (what changes, and what does not)

| Node kind | Identified by today | After this plan |
|---|---|---|
| `Point` | `id` (content-hash or ULID) | unchanged |
| `Document` | `id` (ULID / rel-path) | unchanged |
| `Event` | `eventId` | unchanged |
| `Source` | `url` | unchanged |
| **`Object`** | **`name`** — `MERGE (o:Object {name:$name})` (`projection/entities.py:523`) | **`id`** — minted ULID at creation |
| **`Subject`** | **`name`** — `MERGE (s:Subject {name:$name})` (`projection/entities.py:464`) | **`id`** — minted ULID at creation |

`Object.name` / `Subject.name` remain (indexed at `projection/__init__.py:2682`), but become a **natural key** — a mutable label resolved through a lookup, never the identity. `_entity_name_id`'s output (`obj-<sha26(name)>`) is retired.

Three facts that make this much smaller than it sounds, all re-verified this session against the worktree at `f976d941d`:

1. **`_is_entity_id` already accepts minted ULIDs.** `_ULID_RE` (`sdk.py:980`) is `^[0-9a-f]+-[0-9a-f]{12}$`; `_is_ulid` (`sdk.py:985-987`) ORs it with `_CROCKFORD_ULID_RE` (`sdk.py:982`); `_is_entity_id` (`sdk.py:997-1007`) ORs that with `_ENTITY_ID_RE` (`sdk.py:994`). `TortoiseSDK.ulid()` (`sdk.py:20134-20136`) delegates to `tortoise/ids.py::ulid()` (`ids.py:9-12`) → `"<hex-ts>-<12hex>"`, which satisfies `_ULID_RE`. **No recogniser change is needed** for the four `about*` guards at `sdk.py:17167/17171/17175/17179` or the ingest ref guard at `sdk.py:6928-6938`.
2. **The `MERGE`-key sites are a closed, enumerated set** — 7 `MERGE` sites across 3 files (`entities.py:464/523/868/908/966`, `edges.py:79`, `hosted_api.py:9403`), plus the paired edge anchors (see "Blast radius"). Not "everywhere".
3. **The retraction machinery #3573 reports against does not exist on `main`.** `grep -rn ObjectRetracted tortoise/` → **0 hits**; `_flush_object_folds` → **0 hits**. PR #3326 (which built it) is **closed, not merged**. So #3573's two P1 burials are *latent on that branch*, not live on `main`. On `main` the actual hole is the opposite: `_delete_entity` (`sdk.py:17075-17088`) is a bare `DETACH DELETE` that journals nothing, so **a deleted Object resurrects on rebuild** — pinned green by `tests/test_object_registered_journal.py:309-333`. This plan's job is not to patch #3326's fold; it is to make the fold shape that needs those heuristics impossible.

---

### Pattern Research

> **Findings date:** 2026-09-15
> Gate skipped: zero third-party dependencies in the plan — the change is pure in-repo Python (`hashlib`, `re`, `json`, `uuid` via `tortoise/ids.py`) over the in-repo FalkorDB wrapper (`tortoise/projection/`). No new library, no SDK upgrade, no external API. Per the skill's skip rule, sub-step B.0 (library docs) and B.1 (multi-call Perplexity gate) are both inapplicable.

**Prior-research intake (Step A — always runs).** The epics' research artifacts were read in full:

| Artifact | Used for |
|---|---|
| `docs/epics/2026-09-10-2835-capability-registry/identity-decision.md` | **The design.** Rules R1–R9, the R8/R9 detail sections, the R6 migration scope, and the `### Stage 3 — blast radius` survey (whose `file:line` citations were independently spot-checked against this worktree and match). |
| `docs/epics/2026-09-10-2835-capability-registry/00-align.md`, `research.md`, `prior-art-scan.md` | Confidence tiers and the negative findings (no mainstream rename-continuity for name-derived entity identity; non-reuse is the norm). Not re-litigated here. |
| Issue #3573 | The two P1 burial shapes (P1-A id/name disagreement; P1-B retraction-before-registration). Reproduced against PR #3326's head, not `main`. |
| Issue #3585 | Stage 0 — fail-closed + invariant. Its invariant half is adopted (Slice 0); its fail-closed half is descoped (D10). |
| Issue #3586 | `_is_entity_id` shape-only classification. Analysed in D7. |
| Issue #3589 | Test salvage from closed PR #3326; the (A)/(B)/(C) partition and the shared-vector-fixture mechanism. Adopted as this plan's fixture pattern (Slice 0) and its porting discipline (each slice's tests). |
| `docs/plans/2026-09-10-2779-org-display-name-vs-id.md` | **The slicing precedent.** Its `> **Why this ordering.** … any one slice can be abandoned mid-epic and `main` stays consistent` property is reproduced verbatim below. |

No fresh external queries were fired; the decision doc's external legs (and their confidence tiers, including the aborted third review gate) are the research base and are quoted with their tiers intact rather than re-derived.

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---|---|---|---|---|---|
| 1 | FalkorDB `MERGE`/`MATCH` on `Object`/`Subject` (7 `MERGE` sites across 3 files + paired edge anchors + 23 read sites, "Blast radius") | DB | Both | Integration (docker lane) | `MERGE (o:Object {id:$id}) ON CREATE SET …`; `_RESOLVE_BRANCHES` (`projection/__init__.py:2446-2452`) is `id`/`eventId`/`url` | Wrong key → duplicate node; `ON MATCH` id-write → id churn per mention; unindexed attribute → full scan |
| 2 | JSONL event journal (`tortoise/log.py`, via `sdk._emit_event` `sdk.py:2275`) | Event | Out | Integration | line `{type, id, name, …}`; `Renamed`/`Deleted` JSONL-only, absent from `_GRAPH_EVENT_TYPES` (`sdk.py:731-743`) and `CLAIM_EVENT_TYPES` (`shared_state/events.py:162-174`), no `docs/event-catalog.md` row (matching `ObjectRegistered`) | id not in the line → replay must guess (the root cause); id in the line but not the node → divergence; append failure swallowed (`sdk.py:2377-2388`; the sibling GraphEvent swallow is `:2330`) → live-but-not-durable |
| 3 | Replay engines: `FalkorProjection.apply` (`projection/__init__.py:1290`), `.rebuild` (`:1396`), `.rebuild_all` (`:1401`), `consistency.recover_from_log` (`consistency.py:36`), in-memory `fold`/`_apply_one` (`projection/__init__.py:695/635`; `:619` is `_norm`) | State | In | Integration + invariant harness | `rebuild_all` snapshots **Points only** (`MATCH (n:Point)`, `:1428`) — Objects/Subjects depend entirely on the journal | live ≠ replay; two equally-incomplete projections compare equal (the R9 vacuity trap) |
| 4 | Name→id resolution (natural key) | State | In | Unit + Integration | new resolver: exactly one live holder → that id; zero → mint (creation) or refuse (reference); ≥2 → refuse | guessing among same-name carriers = the #3573 burial; stale alias resolving to a new incarnation |
| 5 | Concurrency — two writers minting/registering the same name | Concurrent | Contested | Integration | `MERGE` on `id` is idempotent; the name→id lookup-then-mint is **not** atomic without a lock | two ids for one name; lost registration |
| 6 | MCP id contract (`mcp_server.py:2218/2226/2379/2384/2392`) | API | Both | Integration | "whatever string resolves" — `get_entity`/`update_entity`/`delete_entity` take an id; `create_object`/`create_subject` take a **name** | `id == f(name)` assumption breaks (D8); `delete_entity` semantics change (R4) |
| 7 | Hosted API id contract (`/v1/objects` `hosted_api.py:4522`, `/v1/subjects` `:4559`) | API | Both | Integration | route docstring at `:4526-4530` advertises *"deterministic id by name, idempotent (a repeat returns the canonical node)"* — still true under D2, but the **mechanism** changes | docstring drift; a client caching `id == f(name)` |
| 8 | `about*` edge wiring (`sdk.py:17164-17180` → `projection/edges.py:344` / `:271` / `:74`) | Auth-free API boundary | Both | Integration | four `_is_entity_id` guards; name fallback mints `_mint_subject_stub` (`edges.py:74-82`) | id-shaped non-matching value never resolves — **silent no-op or stub-mint is undetermined by reading** (#3586; U2 — the split-out micro-issue's test observes it) |
| 9 | `EventAPI.add_object`/`add_subject`/`add_event` (`api.py:247/255/337`) | Event | Out | Integration | `add_object(..., id=…)` exists; **`add_subject` has no `id=` override** | connector stubs unjournaled → rebuild re-mints with a fresh id |

### Bug Pattern Flags (from `test-design` Step 4)

- **Race conditions** — the name→id lookup-then-mint in surface 4 is check-then-act. Must be proven idempotent under concurrent `create_object("X")`; the existing idempotency tests (`tests/test_object_registered_journal.py:482`) are the seed.
- **Conditional guards** — the four `_is_entity_id` guards (surface 8) guard *business logic* (id vs name routing). Both sides must be tested; the **non-matching, id-shaped** case has **no test today** (`grep -rn _is_entity_id tests/` → one hit, a comment at `tests/test_ingest_validation.py:126`) — that is #3586's missing test, split out of S4 as a standalone micro-issue (P2).
- **Silent function skips** — `_emit_event` swallows append failures (`sdk.py:2377-2388`; the GraphEvent lane is `:2329-2331`); `_create_entity`'s existence probe fails **open to journaling** (`sdk.py:16870-16877`, `:16900-16905`); `FalkorProjection.apply` has **no** `else` arm for an unhandled `type` (`projection/__init__.py:1290-1394`). All three make "journalled"/"folded" a claim that must be verified at the artifact, not the call. **Fixes:** the terminal-event append becomes fail-loud (`strict_append`, S2/S3 — the non-terminal lanes keep best-effort); `apply()`/pass-1b gain an explicit `unhandled-event-type` recorder (S0, P2).
- **SQL/business logic in the DB** — the `MERGE`/`ON MATCH` semantics are the business logic here. They cannot be mocked; every claim is a docker-lane integration assertion.

### Checklist notes

- **Empty vs null**: `_upsert_object`/`_upsert_subject` early-return on falsy `id`/`name` (`entities.py:439-441`, `:499-501`) — a minted id is never falsy, but a rename-to-empty must still be rejected; test it.
- **Idempotency**: the existing contract "same name twice → one node" must be **preserved** through the new mechanism (D2), not weakened by re-typing it as "names may collide".
- **Ordering**: `rebuild_all` is a two-pass engine and `recover_from_log` is a one-pass `apply` replay. The invariant must hold on **both**, plus `FalkorProjection.rebuild`.
- **Atomicity — the poison is picked and named (P1-D).** The terminal `Renamed`/`Deleted` append is **post-apply** (mirroring `_create_entity`'s precedent, `sdk.py:16946`, and its phantom-event rationale at `:16965-16971`) and made **fail loud** (`strict_append`, S2/S3). **The earlier "never leaves live-without-durable or durable-without-live" claim is WITHDRAWN — post-apply cannot provide it:** the tombstone/rename lands first, so a failed append leaves **exactly live-without-durable**, and `strict_append` only makes it loud, not absent. The alternative (journal-first, then apply) was rejected because an apply failure then yields **durable-without-live** — a `Deleted` line whose id the fold *does* find and tombstone on replay while live stays live, i.e. an undetected divergence rather than a loud one. **Residual, stated:** (i) a failed append leaves the live tombstone/rename with no journal line; (ii) a **retry** of `delete_entity` then matches 0 live holders (the live-holder gate), emits nothing and returns `False` — the caller cannot distinguish "already deleted" from "never existed", and cannot complete the journal after the fact; recovery is a repair pass over the graph, not a re-call. (iii) **Availability boundary:** the terminal-journal lane exists in **journal-configured lanes only** — the hosted data plane passes **no** `event_log_path` (`hosted_api.py:3379`, D8 check 2), so hosted deletes emit nothing and carry no rebuild obligation.

### Verification Plan (test-routing)

**Domain(s):** code. **Complexity:** Architecture = high, Ontology = high, UX = low, Accessibility = low (issue labels: `complexity:complex`, `team:epistemic-team`, `enhancement`).

| # | Destination | Depth | Reason |
|---|---|---|---|
| 1 | `test-writing` (unit) | standard | The name→id resolver, the id-format contract, the invariant helper, and the shared vector fixture are pure logic |
| 2 | `test-integration` (docker lane) | **full** | Surfaces 1–5, 8, 9 — every claim is a graph/replay assertion. `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'` |
| 3 | `test-e2e` (smoke) | smoke | Surface 6/7: one MCP create→rebuild→get round-trip and one `/v1/objects` create→repeat→same-id round-trip |
| 4 | `code-review` architectural soundness | full | Architecture = high and this is a primary-key change |

| Skipped | Reason |
|---|---|
| `test-e2e` full | No multi-page UI flow |
| `ux-verification` | No UI files |
| `content` / `config` / `research` domains | Not applicable |

### Journey Test Map

```markdown
### Journey: an agent creates a named entity twice and gets one entity
1. **Step:** `create_object("Acme")` → **Acceptance:** a node with a minted id; no `obj-<sha26>` anywhere → **Test:** `tests/test_entity_identity.py::test_create_object_mints_opaque_id`
2. **Step:** `create_object("Acme")` again → **Acceptance:** the *same* id returned, no second node → **Test:** `::test_create_object_second_call_is_idempotent_by_name`
3. **Step:** rebuild from the journal → **Acceptance:** live and replay are equal, and the node id is the minted one → **Test:** `tests/test_rebuild_live_invariant.py`

### Journey: delete then re-create the same name
1. **Step:** `oid_a = create_object("Acme")["id"]; delete_entity(oid_a)` → **Acceptance:** a terminal `Deleted` line journaled with `oid_a` → **Test:** `tests/test_entity_identity.py::test_delete_journals_one_terminal_line`
2. **Step:** `oid_b = create_object("Acme")["id"]` → **Acceptance:** `oid_b != oid_a`; exactly one live node → **Test:** `::test_delete_then_recreate_mints_fresh_id` (S1 already deleted the name-keyed canonical-id re-fetch that made this test **flaky by row order** — P1-A)
3. **Step:** rebuild → **Acceptance:** replay reproduces "one live node, id == oid_b, oid_a present as a `status='retracted'` tombstone" → **Test:** `::test_delete_then_recreate_survives_rebuild`

### Journey: rename survives replay, the old name does not come back
1. **Step:** `update_entity(oid, name="New")` → **Acceptance:** one `Renamed(oid, "Old", "New")` line → **Test:** `tests/test_entity_identity.py::test_rename_is_journaled`
2. **Step:** rebuild → **Acceptance:** the node's name is `"New"`; `"Old"` resolves to nothing → **Test:** `::test_rename_survives_rebuild` (fixes #3377)

### Failure Modes
- Two live entities share a name → **Expected behavior:** by-name resolution **refuses** (records a non-folded entry and fails the run) rather than picking one → **Test:** `::test_ambiguous_name_resolution_fails_closed`
- A referenced name that no entity holds → **Expected behavior:** creation surfaces mint; reference surfaces fail closed → **Test:** `::test_reference_to_unknown_name_fails_closed`
- Live graph loses the node but the journal has it → **Expected behavior:** `rebuild == live` + empty non-folded set holds; the resurrection is gone → **Test:** `tests/test_rebuild_live_invariant.py::test_deleted_object_does_not_resurrect`
- An explicit-id writer re-registers a journaled-deleted id → **Expected behavior:** the write **fails closed** (`IdReuseError` live; a non-folded `id-reuse` entry on replay) — never a silent tombstone-adopt, never a resurrect → **Test:** `::test_id_reuse_after_delete_fails_closed`
- Deleted, then replayed, versus never created → **Expected behavior:** the tombstone materialises on both sides; the never-created name materialises nothing; the two must not compare equal → **Test:** `tests/test_rebuild_live_invariant.py::test_delete_then_replay_vs_never_created`
- A rename onto a live holder's name → **Expected behavior:** **refused** (`NameAlreadyHeld`, 409), because the alternative create-poisons the name with no by-name repair path → **Test:** `::test_rename_onto_live_holder_is_refused`
- A journal line that folds nothing (bare-name reference with no registration; id-keyed miss) → **Expected behavior:** a non-folded entry, and the replay entry point **fails closed** (`rebuild`/`rebuild_all` raise `UnfoldableEvents`; `recover_from_log` reports it and its fail-loud caller `_recover_or_raise` raises) → **Test:** `::test_id_keyed_miss_is_a_non_fold`, `::test_replay_entry_points_fail_closed_on_non_folded`
```

**Tech Stack:** Python 3.12, `uv`, pytest (docker FalkorDB lane per epic #1647 P4), FalkorDB (Cypher), JSONL event log, FastAPI (`hosted_api.py`), MCP server (`mcp_server.py`). No new dependencies.

---

## Design decisions

Each is a decision, not an assumption. The justified option is stated; the rejected option and its reason are stated with it.

### D1 — Minted id format: `tortoise.ids.ulid()` → `<hex-timestamp>-<12 hex>`

**Chosen.** `TortoiseSDK.ulid()` (`sdk.py:20134`) → `tortoise/ids.py::ulid()` (`ids.py:9-12`) → `f"{hex(int(time.time()*1000))[2:]}-{uuid4().hex[:12]}"`, e.g. `197f2a3b4c5-abcdef012345`.

**Why it is the right choice:**
- It satisfies `_is_ulid` (`sdk.py:980/985`), therefore `_is_entity_id` (`sdk.py:997-1007`), *with no recogniser change*. The four `about*` guards (`sdk.py:17167/17171/17175/17179`) and the ingest ref guard (`sdk.py:6928-6938`) keep working for id-valued props. Verified by reading the regexes, and the behaviour is already pinned by `tests/test_about_edges.py:176-246` (which exercises a matching prefixed id via `create_subject`).
- It is the **existing in-repo minter** — no new id scheme, no new dependency, consistent with `Document` (`sdk.py:17186`, `did = self.ulid()`) and `Event` (`sdk.py:17152-17154`, `eid = _server_id` then `eid = self.ulid()`).
- It is **shape-distinct** from the legacy alias: a minted id starts `[0-9a-f]`, `_ENTITY_ID_RE` (`sdk.py:994`) requires `^[a-z]{2,3}-`, so `obj-<26hex>` never matches a minted id.

**Rejected — `obj-<26hex>` (prefix-hex26).** It satisfies `_ENTITY_ID_RE`, so it *looks* cheaper, but it is **shape-identical to the legacy alias** that #3586 documents: `_is_entity_id` classifies by shape alone (`sdk.py:997-1007`), so during any window in which legacy aliases coexist a cached alias is routed down the id path and silently no-ops. D7 shows that window does not exist here — but choosing the ULID removes the question entirely and costs nothing.
**Rejected — a new prefix such as `eid-<26hex>`.** It would *fail* `_is_entity_id`, so an id-valued `aboutSubject` prop would fall through the `not _is_entity_id(value)` guard at `sdk.py:17167` into `_create_about_edges` (`edges.py:271`) and `_mint_subject_stub` (`edges.py:74-82`) — minting a spurious Subject whose id *and* name are the id string. The decision doc's Stage-3 survey deduced exactly this hazard; it is a deduction (no test covers a non-matching id — #3586), which is why adopting a format that satisfies the guard is the safe move.
**Rejected — Crockford base32 ULID.** Also accepted by `_is_ulid` (`sdk.py:982`), but it is not what `ids.ulid()` produces; introducing a second ULID dialect for no benefit.

### D2 — Name uniqueness: the name is a natural key over **live** entities, resolved then created

**Chosen.** `create_object(name)` / `create_subject(name)` (and `/v1/objects`, `/v1/subjects`, `tortoise_create_object`, `tortoise_create_subject`, the ingest `entities` list at `sdk.py:7846/7853`, `onboarding/seed.py:274`) resolve `name → id` **first**:
- exactly one **live** holder → **return it** (no mint, no second node) — this *preserves* today's idempotency contract, `tests/test_object_registered_journal.py`'s `#452` dedup semantics, and the route docstring at `hosted_api.py:4526-4530`;
- zero holders → **mint** a fresh ULID (D1) and register;
- **≥ 2 live holders → the name is ambiguous; by-name resolution refuses** (records a non-folded entry; the run fails — R8/R9). It never picks one.

**"Live holder" is defined once, against the repo's own vocabulary — by DELEGATION, not by a re-typed literal.** The predicate is **not** the ad-hoc `status IS NULL OR status <> 'retracted'` an early draft used (narrower than the canonical terminal set: (a) one live + one **superseded** holder reads as a *false* ambiguity, and (b) a lone superseded holder is returned as a live id even though recall/search exclude it), and it is **not** the hand-rolled `(n.status IS NULL OR NOT (n.status IN $terminal OR n.outdated = true))` literal a later draft carried — that literal has two defects the canonical predicate does not: it treats `status IS NULL AND outdated = true` as **live** where `live._terminal_excluded` (`live.py:38-56`) treats it as **dead**, and (by Cypher three-valued logic) it evaluates `NOT(false OR NULL)` → `NULL` for `status='live', outdated=NULL`, so it excludes live nodes. The resolver must return the id every read surface would return, so the predicate **delegates to `live._terminal_excluded("n.status")`** — one definition, no second copy to drift — carried in **one** shared helper (`projection/entities.py::_LIVE_HOLDER` / `_resolve_name`, S1 step 6) used by resolve, rename-refusal, and the ambiguity count alike. The two neighbouring frozensets (`_RECALL_OBJECT_EXCLUDED_STATUS` at `commit_ops.py:34`; `_RECALL_OBJECT_EXCLUDED_STATUSES` at `assembly.py:1017`) are **name-lookup** vocabularies for recall objects and are deliberately *not* reused as the identity predicate; the plan records the distinction instead of conflating them. Vectors `one_live_one_terminal_holder` (resolves to the live one; **not** ambiguous), `single_terminal_holder_reference` (refuses; never returns a terminal id), and `null_status_with_outdated_flag` (`status IS NULL AND outdated=true` must be treated as **dead**; the vector **fails** if the predicate is reverted to the literal) pin all three directions.

**Consequence — same-name coexistence is now ALLOWED**, which is the behaviour change the issue asks for. It is reachable by exactly one route, which is **not** the plain create path and **not** a rename (D3 refuses that): an **explicit-id** writer — `_connect_issue_objects` (`sdk.py:19427`, ids like `github-issue-{repo}-{number}` and `issue_<sha8>`), `tortoise/github_map.py:147-155` (`ObjectRegistered` with a connector id), `EventAPI.add_object(..., id=…)` (`api.py:255-269`, used by `mining.py:592`), and any other direct `{id:$id}` `MERGE`. Two explicit-id writers that happen to pick different ids for the same name are the only way two live same-name carriers arise; the resolver then refuses to pick one (R8).

**Why not the alternative — "names may freely collide, every create mints"** (`create_object("X")` twice → two nodes): it breaks the idempotency contract that `/v1/objects` advertises (`hosted_api.py:4526-4530`), breaks `#452`'s dedup guarantee pinned across `tests/test_object_registered_journal.py` and `tests/test_subjectadded_journal.py`, and breaks `onboarding/seed.py`'s re-run safety. That is a much larger behaviour change than the issue asks for, and it is not needed to make the burial class impossible.

**The "name index" needs no new structure.** The index is the `name` property plus the range index already created at `projection/__init__.py:2676-2690` (`CREATE INDEX FOR (n:{label}) ON (n.{prop})` for `Subject: (id, name)` and `Object: (id, name)`). No `:NameIndex` node, no parallel map, nothing to keep in sync and nothing to drift — which also means replay rebuilds it for free (it is node state read back from the journal). Stated as a decision because the alternative (a materialised alias index keyed `(alias_string, generation)`) was considered by the decision doc's R6 and is explicitly migration-scoped there. **One residual this structure cannot close (P2):** the name→id **create-time mint** is a check-then-act over a process-local lock (S2 step 1), so two **processes** can each mint for the same name and create a second live carrier — and since no unique name index is permitted (same-name coexistence is legal), there is no structural backstop. It is named in U7 and is acceptable pre-beta.

### D3 — What happens to the name index on rename: nothing, by construction

Because the index **is** the `name` property (D2), a rename is `MATCH (n:Object {id:$id}) SET n.name=$new` — a single atomic property write. There is no second structure to update, so there is no window in which the index and the node disagree, and no "remove the old entry" step to forget. The old name simply stops matching. The `Renamed(id, old, new)` journal line (Slice 3) carries `old` for the audit trail and replay-safety, not for index maintenance.

**Rename onto a name held by a live entity is REFUSED** (`NameAlreadyHeld`, mapped to a 409 at the MCP/HTTP boundary). This does **not** re-import "name IS identity": it keeps the natural key unique **among live holders**, which is exactly what the resolver's contract requires (one live holder per name, or refuse). The earlier draft allowed the rename and let ambiguity surface later, which is strictly worse: the write *succeeds*, the name is then **create-poisoned** (every subsequent `create_object(name)`/bare-name reference refuses), and there is **no by-name enumeration** to repair it — `_resolve_entity` matches `id`/`eventId`/`url` only (`projection/__init__.py:2452-2513`; `_RESOLVE_BRANCHES` `:2446-2452`), there is no name branch and no listing surface. A rename onto a name held only by **terminal** holders is allowed (the predicate is D2's `TERMINAL_EXCLUDED_STATUSES`, so a superseded/deleted holder does not block reuse of its name — which is also what makes R5's delete-then-recreate work). The refusal carries the candidate ids so the caller can target one explicitly; there is nothing else a caller *can* do, which is the point.

### D4 — `create_entity` callers that expect a deterministic id

They get a **minted** id. There is no third option: any deterministic-by-name id is the thing being deleted. The blast radius is small and enumerated:

| Caller | Today | After |
|---|---|---|
| `sdk.py:4011` (`_extract_session_v2` entity loop) | `create_entity("object", name)` → derived id | minted; the loop already only uses the return for edge wiring |
| `sdk.py:7846/7853` (`ingest` entities) | name-lookup for `existed`, then create | name-lookup becomes the D2 resolver; the `canonical = node.get("id") or name` fallback (`:7852`, `:7859`) **must not** fall back to the name — `id` is now always present (assert it) |
| `onboarding/seed.py:274/278` | `create_subject(name)` re-run idempotent | unchanged, because D2 resolves before minting |
| `mining.py:576/588/592` | `api.add_object(name, id=self._object_id(name))` — a **name-derived** `obj_<hash16>` id (`:530-537`) | **resolver caller (P1-B).** S1 routes the key through `_entity_key` (parity with the SDK's derived id, so both channels land on one node under the id-keyed `MERGE`); S2 drops the explicit `id=` and lets the resolve-or-mint resolver assign it. `_object_id` and its `content_hash` derivation are **deleted**; the re-fetch by name (`:588-591`) becomes a resolve. Re-run stays idempotent by resolution (D2), and delete→re-mine mints fresh — **no** `IdReuseError`, so mining's documented idempotent re-run is preserved, not broken. |
| `_connect_issue_objects` (`sdk.py:19427`, fallback `:19457-19462`) | `oid = f"{key.rstrip('s')}_{sha256(name)[:8]}"` when a metadata item carries no id, then a raw `MERGE (o:Object {id:$oid})` | **resolver caller (P1-B).** The fallback resolves `name` → id (mint only when there are zero live holders) instead of deriving; the write stays a raw `MERGE` but takes the id from the resolver, and carries the R5 guard (P1-C, see below — this is the one id-writing channel that does **not** pass through `_upsert_*`). Item-supplied connector ids (`github-issue-{repo}-{n}`, `item.get("id")`) are **unchanged** — they are explicit ids, not name derivations. |
| `commit_ops.py:498-500` | `_entity_name_id("Object", obj_name)` to synthesize an id for an id-less Object | **deleted** (Slice 4) — a fresh start has no id-less Objects |

For callers that genuinely need determinism, add a **server-only** `_server_id` channel to `create_object`/`create_subject`, mirroring the Event branch's (`sdk.py:17152`: explicit keyword, never reachable through the `props` passthrough, backstopped by `_sanitize_props(reject_id=True)` at `sdk.py:16792`). Also extend `EventAPI.add_subject` (`api.py:247`) with the `id=` override that `add_object` already has (`api.py:255-256`) — today `add_subject` is the one EntityAPI writer with **no** explicit-id channel, which is why `_upsert_subject`'s docstring (`entities.py:470-481`) has to accept a late random-ulid re-id.

### D5 — Deletion semantics: one terminal journaled event, replay folds it by id, re-creation mints fresh

`delete_entity` (→ `_delete_entity`, `sdk.py:17075-17088`) **tombstones** the node — `SET status='retracted', deletedAt=$ts` on the label that actually held the identity, replacing the `DETACH DELETE` arm **for the `Object`/`Subject` labels** (the other four arms keep today's `DETACH DELETE` behaviour; the `Point` arm gains only the identity-label skip of S2 step 6, and only `Object`/`Subject` identity is in this plan's scope) — and emits exactly one `Deleted(id, label, name, deletedAt=$ts)` JSONL line per matched node when an `event_log_path` is configured (mirroring the `ObjectRegistered` lane's opt-in: `sdk.py:16858`, emit at `:16972`). The label probe is taken **before** the status write.

> **P1-C fix — `deletedAt` is CARRIED, never allowlisted.** An earlier draft wrote the live tombstone with `deletedAt=$ts`, emitted `Deleted(id, label, name)` with **no** `deletedAt`, and let the fold stamp a rebuild-time `now_iso()`. Because `materialize()` compares the full property dict minus `{updatedAt, embedding}` (S0 step 2), every delete vector then fails `rebuild == live` for a **benign** reason — and the cheapest implementer response, allowlisting `deletedAt`, would mute the very invariant the plan built to be total. **Fix:** capture `ts` **once** in `_delete_entity`, thread it into **both** the live `SET` and the emitted payload, and have `_fold_deleted` use the **journaled** value (`ev.get("deletedAt")`), never a rebuild-time `now_iso()`. (`_emit_event` stamps its own envelope `ts` at emit time — `sdk.py:2339-2345` — a **different** `now_iso()` call, so the envelope cannot be the carrier.) `deletedAt` **stays in the compared set** precisely because it is now carried; a negative test asserts that a mismatched journaled `deletedAt` **breaks** the invariant, proving it is compared and not allowlisted.
>
> **P2 fix — a mixed `:Point:Object` node is NOT "taken by the Object path" as an earlier draft claimed.** `_delete_entity` iterates `Point → Subject → Object` (`sdk.py:17078`), so the **Point** arm's `DETACH DELETE` runs first and would hard-delete the node; the Object arm then matches 0 rows, emits no `Deleted`, and `total` is still > 0 from the Point arm, so replay (from the node's `ObjectRegistered` line) resurrects a node that was hard-deleted live — the exact divergence this plan exists to close. The **Point arm is therefore gated to skip identity labels** (`MATCH (n:Point {id:$id}) WHERE NOT (n:Object OR n:Subject) DETACH DELETE n`), so the identity label's tombstone write is the only write, exactly one `Deleted` is emitted, and replay reproduces the tombstone.

> **P1-1 fix — one deletion semantics for both live and replay.** An earlier draft ran the live `DETACH DELETE` "unchanged" while deciding the `Deleted` **fold** was a tombstone. Those cannot both hold: after a delete, live = *no node*, replay = *a node with `status='retracted'`*, so the S0 `materialize()` invariant (which includes `status`, S0 step 2) **fails on exactly the vectors S2 un-`xfail`s**, and the natural implementer response is to normalise the assertion — the vacuity this plan exists to prevent. **Decision (option (a)): live deletion also tombstones.** The `Object`/`Subject` `DETACH DELETE` arms go away, the node stays as a tombstone on both sides, and the two green-pins that assert "live node must be gone after delete" (`tests/test_object_registered_journal.py:322`, `tests/test_subjectadded_journal.py:485` — they match by name with **no** status filter, `_object_row`/`_subject_row`) are **re-derived** to assert *absent from live read surfaces* (the node exists with `status='retracted'`, which every recall/search predicate excludes) rather than *absent from the graph*. The rejected option (b) — hard-delete on both sides, i.e. `Deleted` folds as `DETACH DELETE` — is rejected on the same grounds the fold decision already gave: a hard delete makes replay order-sensitive and loses the id history R5 needs, and R7's log-position ordering does not restore either.
>
> **What `materialize()` does with tombstones (stated explicitly).** It **keeps** them: `status` is an identity-relevant property (S0 step 2) precisely so a burial is *visible to the invariant*. This is deliberate and is the only way R9 can see a `Deleted` — a `materialize()` that projected tombstones out would make "deleted" and "never created" compare equal, i.e. the same vacuity. The invariant's *recall* blindness is separate and intended: `'retracted'` is excluded by `live.TERMINAL_EXCLUDED_STATUSES`, so a buried node is invisible to recall/search while still present to the harness. A new vector `delete_then_replay_vs_never_created` pins the distinction: the deleted shape materialises a `status='retracted'` node on **both** live and replay, while a never-created name materialises **no** node — the two must not compare equal on either side.

- **JSONL-only, by construction**: absent from `_GRAPH_EVENT_TYPES` (`sdk.py:731-743`) and `CLAIM_EVENT_TYPES` (`shared_state/events.py:162-174`), and no row in `docs/event-catalog.md` — exactly the registration posture `ObjectRegistered`/`SubjectAdded` already have (0 rows in that catalog; verified).
- **Replay fold**: `apply()` (`projection/__init__.py:1290`) and the pass-1b loop in `rebuild_all` (`:1673-1678`) gain an id-keyed, idempotent `Deleted` branch that applies the **same** `SET status='retracted', deletedAt=$ts` the live path applies — where `$ts` is the **journaled** `deletedAt` (`ev.get("deletedAt")`), never a rebuild-time `now_iso()` (P1-C). Because the fold keys on the **journaled id**, it cannot bury a live node: a re-creation after deletion carries a **different id** (R5), so the fold's `MATCH (n:Object {id:$old_id})` misses the new node **by construction**. This is the structural kill for #3573's P1-A and P1-B: they both require the fold to reach a node *by name*; there is no name in the fold.
- **Terminal, not a tombstone-in-the-Kafka-sense**: the event stays in the journal forever and is always foldable; nothing is garbage-collected. (Decision doc "Source disagreements" §1 chose this sense deliberately.)
- **R5 — an id is never reused, and that is now ENFORCED, not asserted.** A **journaled `Deleted`** for an id is a permanent bar on re-registering that id: checked on the live path (`IdReuseError`, S2 step 6a) and re-checked in the fold (`tombstoned_ids` → non-folded `id-reuse` entry). **The predicate is DELETION-SPECIFIC on BOTH engines (P1-B):** live tests the node's **deletion marker** (`status='retracted'` **and/or** `deletedAt IS NOT NULL`), replay tests `tombstoned_ids`. It must **NOT** use `TERMINAL_EXCLUDED_STATUSES` (`live.py:33`) — that set conflates supersession with deletion, so re-registering a **superseded** id would **raise** `IdReuseError` live while **folding** as a no-op on replay: asymmetric, and it breaks the pinned #1350 guard (`tests/test_object_registered_journal.py:199-228` — a superseded re-mention is an expected **no-op**), reachable from §A.1's own explicit-id channels (a `github_map`/`_connect_issue_objects` idempotent re-sync) and from the emit-on-every-call rule (S2 step 3). A fresh mint cannot alias a previous incarnation because the mint is random (`_mint_entity_id`) *and* a reuse attempt fails closed. Without this, `[OR(id), Deleted(id), OR(id)]` replays the id as `retracted` while live leave it live — the same harm class as P1-A, new mechanism (P1-2).

**Why this must ship with Slice 2's mint (and not as its own slice):** minting without journaled deletion *creates* a new live/replay divergence — `create → delete → create` today yields one node on both sides (same derived id); under minting it yields one live node (`id2`) and **two** replayed nodes (`id1` resurrected + `id2`), and it turns `tests/test_object_registered_journal.py:309` / `:335` red. The two changes are one semantic step: minted identity is only meaningful if deletion is terminal and re-creation is a new stream.

### D6 — `supersededBy` stores the successor's **id**, not its name

`_fold_object_superseded` currently writes the successor's **name** truncated to 200 (`entities.py:610` computes `supersedes_by = str(ev.get("supersedes_by") or "")[:200]`; `:649` puts it in `common_params` as `"sb"`; the `supersededBy` CASE is `:641-647`), and `commit_ops.py:413` probes the successor with `MATCH (o:Object {name:$sb})`. A name-keyed `supersededBy` is a dangling reference the moment the successor is renamed. Under the new model the fold stores the successor **id**, and `commit_ops.py`'s successor probe (`:412-419`) becomes an id probe. Deliberately scheduled to Slice 3 alongside rename: changing `supersededBy` to an id while renames are still unjournaled would not help. The `#2164` accepted-divergence comment at `commit_ops.py:474-500` and the legacy-id synthesis at `:495-500` are deleted in Slice 4.

### D7 — `_is_entity_id` and #3586: the hazard is real but its window does **not** exist here

#3586's sharp consequence is: *a minted id and a legacy `obj-<sha26(name)>` alias are shape-identical, so a cached legacy handle is routed down the id path and silently no-ops.* **Under a fresh start it does not apply**, and the reason is auditable: the hazard requires a **legacy alias to exist and remain resolvable** while minted ids are in use. With D8's verification (no Object/Subject in any production graph, and no production Object/Subject journal), no `obj-<sha26(name)>` alias is ever created, so there is nothing for a stale handle to no-op against. There is no legacy-alias window to protect, and therefore **the alias-index/provenance check is not required** — only "mint ids that satisfy `_is_entity_id`" (D1), which holds by construction.

What **does** still belong here is #3586's *other* half — the **missing test**. No test in the repo exercises a **non-matching, id-shaped** value (`grep -rn _is_entity_id tests/` → one hit, a comment at `tests/test_ingest_validation.py:126`); the two tests that look like they cover it (`tests/test_about_edges.py:176-205`, `:221-246`) pin the **inverse** (a *matching* id). A **standalone micro-issue** (split out of S4, P2) lands the characterisation pin for the non-matching case so the guard's boundary is recorded, and re-states #3586's resolve-vs-refuse decision under the new model. **This plan does not close #3586** — it records why its P1 consequence is out of scope, and the **split-out micro-issue** (P2) lands its missing test.

### D8 — The hard unknown: replaying a pre-change event once the resolver only understands new ids

The decision doc's explicit gap: **no published guidance exists** for what a replay of a pre-change event means once the resolver only understands new ids — i.e. an old `ObjectRegistered(obj-<sha26(name)>, NAME)` line replayed by a resolver that no longer recognises `obj-<sha26>` as anything but an opaque string.

**Verdict: moot, because there is no pre-change event to replay. The claim is falsifiable, and here is the falsification:**

1. **No production graph holds a serviceable Object or Subject — verified by sweeping EVERY graph on EVERY instance, not a 4-graph sample, and by ALL FOUR legacy id shapes (P2).** An earlier draft checked four graphs on one instance, reported "0 Objects, 0 Subjects" and "1,595 graphs", keyed its guard to node counts that had already drifted, and — the P1-A defect — swept **only** `id STARTS WITH 'obj-'`. Legacy Subjects are minted `sub-<26hex>` (`sdk.py:1358`, `label[:3].lower()`), so the Object-only predicate provably could not see one and the evidence table reported only `obj-` totals. Corrected, re-swept 2026-09-15 (snapshot — see the drift note) across all **three** live FalkorDB instances, with both prefixes run per graph:

   | Instance | Graphs | `obj-` ids | `sub-` ids | Where the hits live |
   |---|---|---|---|---|
   | `falkordb` (`localhost:6379`, `AUTH falkordb`) | 1452 | **36** | **1** | `test_test_*`, `test_stamp_*`, `test_e25_dedup_*` |
   | `falkordb-16379` (`localhost:16379`, **no password configured**) | 854 | **5** | **0** | `graphops_measure_tmp` (4), `probe2517props` (1) |
   | `falkordb-eval` (`localhost:6380`, `AUTH falkordb`) | 6 | **0** | **0** | — |
   | **total** | | **41** | **1** | |

   The single legacy Subject is `sub-f0721f125a796ba13e66b8c9ce` (name `daniel`) in graph `test_test_6ee7e7c9b606`; the Object hits include `obj-7dd4e565a8ea26e6cf376a389f` (`the strategy`) across many `test_test_*`/`test_stamp_*` graphs and the four named `graphops_measure_tmp` Objects.

   **Every one of those 42 legacy-id nodes lives in a throwaway test/measurement/probe graph** (`test_test_*`, `test_stamp_*`, `test_e25_dedup_*`, `graphops_measure_tmp`, `probe2517props`, `verify_*`); none is in `tortoise`/`registry_tortoise`/`tortoise_test_matrix` or any service graph — the same conclusion holds for the Subject as for the Objects. So the **fresh-start decision itself stands — but it is confirmed on *substance* (every legacy node is a disposable artefact), not on counts.** The counts **drift every test run** (the plan's own earlier figures — 891 graphs / 20 `obj-` — were 1452 / 36 at the r2 re-sweep, from test-graph churn alone) and must never be the guard.
   **P2 — two MORE shapes were invisible to the two-prefix predicate, and one of them was found live.** The r3 re-sweep (2026-09-15, same session, all three instances, **widened** predicate) returned **8 hits**: `graphops_measure_tmp` (4 `obj-` Objects) and `probe2517props` (1 `obj-` Object) as before; **plus three `id == name` Subjects** — `agent-pi` in `test_proj125_9a328d44`, `test_proj125_feae921e`, `test_proj125_bccddb9d` — which the `obj-`/`sub-` predicate **provably could not see**. (`_mint_subject_stub`, `projection/edges.py:79`, does `ON CREATE SET s.id=$name` — the fourth shape.) `mining._object_id`'s `obj_<hash16>` (underscore) is the third shape: not one hit live, but the code path exists, so the predicate must see it. Graph counts had also drifted again (`falkordb` 1452 → **88**, `falkordb-eval` 6 → **7**), which is exactly why the counts are a snapshot and the predicate is the guard.

   | r3 instance (**widened** predicate) | Graphs | `obj-` | `sub-` | `obj_` | `id == name` | Where the hits live |
   |---|---|---|---|---|---|---|
   | `falkordb` (`:6379`) | **88** | 0 | 0 | 0 | 0 | — |
   | `falkordb-16379` (`:16379`) | 854 | **5** | 0 | 0 | **3** | `graphops_measure_tmp` (4), `probe2517props` (1), `test_proj125_*` (3 Subjects) |
   | `falkordb-eval` (`:6380`) | **7** | 0 | 0 | 0 | 0 | — |
   | **total** | | **5** | **0** | **0** | **3** | all throwaway test/measurement graphs |
   ⛔ **The guard is a PREDICATE, never a count — and it is PER-LABEL, over ALL FOUR legacy shapes.** The S4 precondition is: *"any legacy-shaped `Object`/`Subject` id exists in any **service** graph on any live instance ⇒ **S4 must not merge**."* The predicate is complete over both name-keyed labels and all four shapes:

   ```cypher
   MATCH (n) WHERE (n:Object  AND n.id STARTS WITH 'obj-')
              OR (n:Subject AND n.id STARTS WITH 'sub-')
              OR (n:Object  AND n.id STARTS WITH 'obj_')
              OR (n:Subject AND n.id STARTS WITH 'obj_')
              OR (n:Object  AND n.id = n.name)
              OR (n:Subject AND n.id = n.name)
   RETURN labels(n), n.id, n.name
   ```

   The six disjuncts are, in order: `_entity_name_id` Object, `_entity_name_id` Subject, `mining._object_id` (`obj_` + 16 hex — **underscore**, invisible to `STARTS WITH 'obj-'`), the same for a Subject, the `id == name` stub shape (`_mint_subject_stub`, `projection/edges.py:79`), and its Object twin. **Zero matches in every service graph** is required before S4, and a run that reports only an `obj-` total is not a pass (it cannot see a legacy Subject — nor the `obj_`/`id == name` shapes). **The r3 table above is the widened-predicate sweep, already run this session** (8 hits, all in throwaway graphs); the four-shape totals are therefore the ones that gate, and the earlier two-prefix table is kept only as the historical snapshot. **Re-run the widened predicate immediately before S4 merges** and record the totals — the *recorded counts must match the predicate that gates*. The check enumerates **every graph on every instance** (sweep `GRAPH.LIST` per instance, then the predicate per graph) and never uses `=~` — **FalkorDB does not support `=~` and fails silently (empty result, not an error)**, which is exactly how the original verification produced a false negative; use `STARTS WITH`/`ENDS WITH`/`CONTAINS`. (The `=~` hazard is filed as its own issue.)
2. **No production Object/Subject journal exists.** `event_log_path` is **opt-in per SDK construction** (`sdk.py:1786`) with no environment default (`grep -rn EVENT_LOG tortoise/` → 0 hits), and the hosted data-plane resolver `_data_sdk` (`hosted_api.py:3379`) passes **no** `event_log_path` — so the hosted lane never writes `ObjectRegistered`/`SubjectAdded`/`EventRecorded` JSONL at all. In-tree writers that do configure it are `mining.py:961/968` and tests.
3. **The only JSONL on disk is not the SDK journal.** `~/.tortoise/session-events/*.jsonl` (Aug 7 – Sep 15) parse with **no `type` key** (1,826 records in `2026-09-15.jsonl`, all `type=None`) — they are a different lane, not `EventLog` records.

**Therefore no resolver dual-read (`id`-first, then derived-alias→id) is planned, and no backfill is planned.** The conclusion is falsifiable by re-running the three checks **with the per-label predicate guard above**; **if the predicate matches any service graph before Slice 4 merges, Slice 4 must not merge** (it is the slice that removes `_entity_name_id`), and a dual-read resolver is required first. **The sweep must cover every graph on every instance, both labels — a partial list or an Object-only total is not a pass.**

### D9 — Producers that reference an entity by bare name must register it

The projection's speculative stub writers (`_event_plain_merge`'s `object` / `subject` / `uses` branches at `entities.py:908/966/868`) mint a node for a **bare name** on an `EventRecorded` line. Today determinism is free because the id is `f(name)` and the `MERGE` key is the name. Under minted ids a random mint inside the projection is **not replay-deterministic** — a rebuild would assign different ids than live.

**Chosen:** in Slice 2, every producer that emits an `EventRecorded` whose `subject`/`object`/`uses` is a bare name **must also emit the entity's journaled registration** (`ObjectRegistered`/`SubjectAdded`) carrying its id, before the event. Enumerated producers needing this: `tortoise/connectors/slack.py:267`, `tortoise/connectors/linear.py:190` and `:217`, `tortoise/sdk.py:11508` (`subject=agent_name`, no registration), `tortoise/mining.py:825`. `connectors/github.py:261/284` and `tortoise/github_map.py:155/256` already do this. Lanes where the entity is registered first need no change: the projection's name→id lookup finds the replayed registration and wires the edge with **no mint**.

> **P2 — the `sdk.py:11508` site needs more than a registration added.** That lane calls `proj.apply({"type": "EventRecorded", …})` **directly** (`sdk.py:11507-11515`) — it never calls `_emit_event`. Registering the Subject therefore makes the **entity** replayable but leaves the **Event node and the `performs` edge live-only**: replay never sees the `EventRecorded` line, so it never wires the edge. Under S0's `materialize()`-with-edges (D10) that is a live/replay divergence. **S2 routes this `EventRecorded` through `_emit_event`** (the journaled lane) rather than `proj.apply` directly; if any reason prevents that, the lane is recorded as an **accepted live-only divergence with an owner issue**, not silently left. The site is also added to U3's "run the suite first" discipline.

**Why not keep a name-derived stub id** (`sha256(f"{label}:{event_id}:{name}")`): it is replay-deterministic and would be a smaller change, but it is still a derivation over a name, and the issue's first acceptance criterion is *"No Object/Subject write path derives an id from a name."* Keeping one would make that criterion false-by-construction and would carry the `[a-z]{2,3}-<hex26>` shape the model is retiring. **Not chosen.** The same reason retires the two producers r1 had left as "unchanged" — `mining._object_id`'s `obj_<hash16>` and `_connect_issue_objects`'s `issue_<sha8(name)>`: both are name-derived and both become resolver **callers** (D4, P1-B). Scoping the criterion down to "no **new** derivation in the SDK write layer" was the alternative and is **rejected** — it would leave acceptance (a) false while the plan claims it, and it leaves mining's re-run to fail closed on R5.
**Why not refuse an unregistered reference outright with no producer fix:** it would silently drop connector edges, and "silently drops an edge" is the same harm class as "silently buries a node". The producers must be fixed, not the resolver.

### D10 — #3585's invariant half is load-bearing and lives here; its fail-closed half is descoped

The issue names #3585 ("Stage 0") as a precondition. **We adopt its invariant half and descope its fail-closed half, with the reason stated:**

- **Adopted (Slice 0):** the `(materialize(), non-folded-event set)` invariant — `rebuild == live` **and** the non-folded set empty — asserted, not asserted-by-eye, across the apply-based replay engines. The decision doc's R9 says plainly that the naive `rebuild == live` is defeated by R8: once a refusal exists, both sides skip the same event, produce the same equally-incomplete projection, compare equal, and pass green. This is the discipline that would have caught the #2977 cycles. **It belongs in this plan because it is the only thing that makes Slice 2's and Slice 3's claims verifiable rather than asserted.**
- **Descoped — only the per-shape gating, and #3585's precondition is now MET at the replay boundary (P1-5 fix).** An earlier draft claimed the unresolvable-event classes "do not arise". **They do**: a bare-name reference with no registration is exactly that class, and S2 step 5 builds a refusal path for it — so what is descoped is only the *per-shape* leak/burial gating, not the fail-closed behaviour itself. #3590 names the fail-closed behaviour as a **precondition** ("the projection must fail loudly rather than guess"), so run-failure is wired into the replay paths rather than left to test-time: `FalkorProjection.rebuild` and `.rebuild_all` **raise `UnfoldableEvents` on a non-empty non-folded set at end-of-replay**; `consistency.recover_from_log` keeps its documented **report-never-raise** contract (`consistency.py:36-56`, pinned by `tests/test_ops_safety.py:153-166`) and returns `{recovered: False, reason: ..., non_folded: [...]}` instead — its fail-loud caller `_recover_or_raise` (`projection/__init__.py:1227-1234`) raises on that result (the finding's `_recover_or_fail` is a name mismatch — verified). The live `apply` path records non-folds but does not raise — the harness asserts it. This also closes the second hole the earlier draft left: an **id-keyed miss** (a `Deleted`/`Renamed`/registration whose id no node carries) is now **recorded as a non-folded entry**, not a silent no-op — the earlier draft recorded non-folds only on the by-NAME path, so a dropped id-keyed write was invisible. The R8 assertion shape is preserved and strengthened; only the per-shape gating design is descoped.
- **What is explicitly *not* descoped:** a genuine ambiguity (≥2 live same-name carriers, D2), an unregistered reference (D9), and an **id-reuse attempt** (R5, P1-2) still **refuse and fail the run** — the harness must be able to see a non-empty set and fail. The harness therefore has a self-test proving it detects a deliberately injected non-folded entry (an assertion that cannot pass vacuously — the AGENTS.md "positive assertion of what was found" rule). **Because `materialize()` compares node properties, not edges**, a silently dropped *edge* would otherwise be invisible to the invariant — and the plan itself says a dropped edge is the same harm class as a buried node. `materialize()` therefore also materialises the **edge set** (relationship type + start/end node ids), so an edge drop cannot pass silently (S0 step 2).

---

## Task ordering (slices)

| Slice | Title | Depends on | Independently mergeable because |
|---|---|---|---|
| **S0** | The `rebuild == live` + empty-non-folded-set invariant harness, with the identity vector fixture and `xfail` pins for the known-bad shapes | — | **Recording-only production hook + tests. No behaviour change** — S0 adds the `non_folded` recorder and `materialize()` (P1-E), which record and never raise, plus the explicit `apply()` unhandled-type/malformed arms. The known-bad shapes (deleted-Object resurrection, unjournaled rename) are `xfail`-pinned **with owner issue refs** (#3377, #3573) so `main` stays green while the harness is live. |
| **S1** | One key: every `Object`/`Subject` write path `MERGE`s on `id`, and every mention routes through the name→id resolver | S0 | The mint is **unchanged** (`_entity_name_id`, still a function of the name), so same-name creates still yield the same id and the same node; the resolver dedups a mention onto that node instead of minting a random twin. This is **not** byte-identical (P1-3): a mention that supplies no id no longer creates a second same-name node, and **two** mention tests are re-derived in S1 (the two `create_entity` stub-adoption tests move to S2 — id-keyed `MERGE` alone would land a second same-name node for a pre-existing raw stub, P1-D). S0 is the guard that proves nothing else moved. |
| **S2** | Minted identity: mint the id (R1), name becomes a resolved natural key (R2), terminal journaled deletion (R4/R5), producers register what they reference (D9) | S1 | S1 already put every writer on the id key, so flipping the id source is a change to *one* function plus one new fold — not a rewrite. `create_object(name)` stays idempotent via D2's resolve-before-mint. |
| **S3** | Rename is a journaled mutation (R3), and `supersededBy` becomes an id (D6) | S2 | A rename event can only be replayed onto a stable id; with S1's derived ids it would be replayed onto an id that changes when the name does. It is independent of S4. |
| **S4** | Retire the derived id: delete `_entity_name_id`, its cross-name probe and the `commit_ops` synthesis; update the MCP/hosted/event-catalog contracts that advertise it | S2, S3 | Pure subtraction + documentation once nothing derives ids. Can be abandoned at any point before it merges without affecting S0–S3's correctness. **Split (P2):** #3574's `name[:200]` split and #3586's missing characterisation test are **standalone micro-issues**, not S4 deliverables — only the `_entity_name_id` deletion is gated by D8's predicate; (ii)/(iii) were held hostage by an irreversible precondition and (iii) is useful on `main` today. |

> **Why this ordering.** Our own precedent for a near-identical change (`docs/plans/2026-09-10-2779-org-display-name-vs-id.md`) split it so that **any one slice can be abandoned mid-epic and `main` stays consistent**. This plan reproduces that property and states it as a requirement, not an aspiration:
> - **S0 alone** is a test-only slice — abandoning it removes the guard, not the behaviour.
> - **S1 alone** preserves the node-level observable behaviour (the mint is still `f(name)`, same-name creates still dedup to one node); the delta is that a mention resolves to the canonical node rather than minting a random-id twin. Abandoning it leaves `main` with today's node shape and today's random-ulid journal noise.
> - **S2 alone** is the model flip; abandoning it after S1 leaves `main` with a single key and the old id source — coherent, green, and strictly better than today for the "second carrier" class.
> - **S3 alone** is additive (a new event type + one fold); abandoning it leaves #3377 open, as today.
> - **S4 alone** is subtraction; abandoning it leaves the derived id retired-but-present, which is dead code, not a broken state.
>
> The dependency arrows are **declared, not hidden**: S1 is the *only* reason S2 is small; S2's mint is the reason S3's rename replay is coherent; S3 is the reason S4's `supersededBy` change is safe. The Mikado question for each — *what must already be true?* — is answered in the "Depends on" column and re-stated per slice as a **Precondition** line.
> **The one coupling that must not be split** is D5: minting (S2a) and terminal deletion (S2b) ship together, because minting without journaled deletion *introduces* a new live/replay divergence and turns `tests/test_object_registered_journal.py:309`/`:335` red. That is why S2 is the largest slice.

---

## Slice 0 — The `rebuild == live` invariant harness (the load-bearing half of #3585)

**Intent:** make live/replay divergence a *positive assertion* before anything moves, so Slices 1–4 are verified rather than asserted. This is the discipline that would have caught all eight #2977 cycles (decision doc R9).

**Acceptance:** a parametrised harness that, for each in-scope shape, drives the shape through the live SDK, then replays the same journal through every apply-based engine, and asserts (a) `materialize()` matches on both sides and (b) the non-folded set is empty. The harness detects a deliberately injected non-folded entry. Shapes that are known-bad today are `xfail`-pinned with an owner issue reference; each `xfail` is removed in the slice that fixes it. **P1-E ownership:** S0 is *not* a pure test addition — it lands the minimal `non_folded` recorder + `materialize()` (recording only), because without a source of non-folds the self-test in step 3 cannot fail and the harness is vacuous by construction (AGENTS.md's forbidden case). S0's rollback is therefore "recording only, no behaviour change", not "test-only".

**Files:**
- Create: `tests/test_rebuild_live_invariant.py`
- Create: `tests/fixtures/entity_identity_vectors.json`
- Create: `tests/identity_vectors.py` (the shared loader)
- Modify: `tests/conftest.py` (add the engine-parametrisation fixture)
- Modify: `tortoise/projection/__init__.py` — the `non_folded` recorder (`_record_non_fold`) and `materialize()`; the `apply()` malformed-event arm and a new `else` unhandled-type arm record (P1-E, P2) — **records only, never raises**
- Modify: `tortoise/projection/entities.py` — the existing silent no-op arms (`_upsert_object`/`_upsert_subject` falsy-id/name early returns, `_event_plain_merge` misses) call `_record_non_fold` — recording only
- Modify: `docs/00_index.md` (register the new fixture) — **only if** `docs/00_index.md` enumerates `tests/fixtures/`; verify first; if it does not, skip this file
- Test: `tests/test_rebuild_live_invariant.py`

**Steps**

1. **Write the shape list into the shared fixture.** Follow the mechanism #3589 prescribes (`tests/fixtures/org_naming_vectors.json` is the in-repo precedent — a single vector file consumed by the Python and dashboard suites). Each vector is `{id, description, steps, expect_live, expect_replay, verdict, owner}`. Seed it with:
   - `create_object_twice` — idempotent single node
   - `create_subject_twice` — idempotent single node
   - `event_about_object_by_id` / `event_about_subject_by_name`
   - `delete_object` — **verdict: `xfail`, owner: #3573** (resurrection is today's behaviour)
   - `delete_then_recreate` — **verdict: `xfail`, owner: #3573**
   - `rename_then_rebuild` — **verdict: `xfail`, owner: #3377**
   - `connector_bare_name_object` (`slack`/`linear` shape) — **verdict: `xfail`, owner: #3589/D9**
   - `mixed_id_and_name_reference` — the #3573 P1-A shape (must **not** exist after S2)
   - `delete_then_replay_vs_never_created` — the P1-1 distinction: a delete materialises a `status='retracted'` node on **both** sides, a never-created name materialises **nothing**; the two must not compare equal
   - `id_reuse_after_delete` (`[OR(id), Deleted(id), OR(id)]`) — **verdict: `xfail`, owner: #3590/P1-2** today; must **fail closed** on every engine after S2
   - `one_live_one_terminal_holder` / `single_terminal_holder_reference` — the D2 "live holder" predicate vectors (one resolves to the live holder; the other refuses)
   - `null_status_with_outdated_flag` — `status IS NULL AND outdated=true` must be treated as **dead** (P2); the vector **fails** if the predicate is reverted to the hand-rolled literal
   - `unknown_event_type_in_journal` — an unhandled `type` records a non-fold (S0) and fails the run on every engine (S2) (P2)

2. **Write the invariant helper.** It must compare **all** entity labels, not only Points (note: `rebuild_all` snapshots Points only — `projection/__init__.py:1428` — so Objects/Subjects are journal-only and are exactly where divergence hides). Signature:

   ```python
   def assert_rebuild_equals_live(sdk, events_dir, *, engines=("rebuild", "rebuild_all", "recover_from_log")):
       """R9: (materialize(), non_folded) must match live on every apply engine."""
       live = materialize(sdk._get_proj())
       assert live.non_folded == set(), (
           f"non-folded events present on the LIVE path: {sorted(live.non_folded)}"
       )
       for engine in engines:
           proj = fresh_projection()
           non_folded = replay(proj, events_dir, engine=engine)
           assert materialize(proj) == live.state, f"{engine}: rebuild != live"
           assert non_folded == set(), f"{engine}: non-folded events {sorted(non_folded)}"
   ```

   `materialize()` must be **canonical and total** — and "total" must be **falsifiable**, not a hand-picked list (P2). The compared property set is derived from the model: **the full property dict of each entity node, minus an explicit volatile allowlist** (`updatedAt`, `embedding`). Do **not** enumerate the identity-relevant fields: the earlier literal list (`id`, `name`, `status`, `createdAt`, `objectKind`/`subjectKind`) silently omitted `outdated` (read by the live-holder predicate), `supersededBy` (D6 migrates name→id — the whole point of S3), `deletedAt` (D5 **carries** it — one captured `ts` threads through the live `SET`, the emitted line and the fold, so it must be compared and must agree; P1-C), and the `_persist_extra_props` payload (S1 step 2 re-targets that `MATCH` from `{name:$name}` to `{id:$id}` — exactly the edit that silently no-ops). A negative test pins the totality: mutate `supersededBy` (or drop one extra prop) on **one** side only and assert the invariant **fails** — otherwise "total" is unfalsified. **`status` is included on purpose, and tombstones are KEPT**: that is what lets R9 *see* a `Deleted` and what distinguishes "deleted then replayed" from "never created" (D5). **The edge set is materialised too** (relationship type + start/end node ids, sorted) — a properties-only comparison cannot see a silently dropped edge, and a dropped edge is the same harm class as a buried node (P1-5).
   **P2 — the recorder is wired into `apply()` and `rebuild_all`'s pass-1b loop.** `apply()` (`projection/__init__.py:1290`) currently has **no** `else` branch (verified: the `elif` chain ends at `SourceCreated`, `:1394`), so an unhandled `type` is dropped with no record, and its malformed arm (`:1296-1302`) logs and returns — live and replay drop it *identically*, so the invariant would pass on two equally-incomplete projections (R9's vacuity trap). Add `else: self._record_non_fold(journal_position, ev, shape="unhandled-event-type")` to `apply()` and the matching arm to `rebuild_all`'s pass-1b loop (`:1673-1678`), and have the malformed arm record too — **recording only at S0**. The vocabulary-parity test (`test_journal_vocabulary_has_a_dispatch_branch`) must be scoped to the **dispatch surface the plan actually routes**, not asserted over the whole vocabulary — an earlier draft required "every type in `_GRAPH_EVENT_TYPES` ∪ the JSONL-only set has an `apply()` **and** a pass-1b branch", which **cannot pass green on `main`**: verified against the worktree, `apply()` (`projection/__init__.py:1290-1394`) has **no** branch for `PointSuperseded`, `PointInvalidated`, `OperatorAnnotated`, `DedupeRecorded`, `DedupeRejected`, `BatchIdStamped`, and pass-1b (`:1600-1760`) has none for the last four. S0 **cannot** add real folds without changing behaviour (contradicting its declared recording-only rollback) and must not weaken the assertion to a vacuous pass. **The test therefore partitions the vocabulary explicitly** — "folded by `apply()`" / "folded by pass-1b only" / "GraphEvent-store or JSONL-only — not a fold" — and asserts a real branch only for the first two partitions **plus** `Deleted`/`Renamed` as S2/S3 land them. The pre-existing gap (`apply()` drops `PointSuperseded`/`PointInvalidated`, so a one-pass `recover_from_log` cannot replay supersession) is **filed as its own issue with an owner** — S0 must not silently absorb it (P1-E). The `unknown_event_type_in_journal` vector still pins the failure.

3. **Add the self-test that keeps the harness non-vacuous — and the recorder it needs (P1-E).** Inject a synthetic **unknown-type** (or malformed) event into a scratch journal; S0's `_record_non_fold` records it on the live/replay path, so `assert_rebuild_equals_live` **fails** and the failure message **names the offending journal position**. At S0 the recorder is the *only* source of non-folds (the id-keyed-miss and unregistered-reference records arrive in S2), which is why the recorder is an S0 deliverable — without it the self-test has nothing to detect and cannot fail (the exact vacuity AGENTS.md forbids). The vector's "the **run raises**" half is `xfail`-pinned at S0 and un-`xfail`ed at S2 (where `rebuild`/`rebuild_all` raise).

4. **Parametrise the engines.** `FalkorProjection.rebuild` (`projection/__init__.py:1396`), `FalkorProjection.rebuild_all` (`:1401`), `consistency.recover_from_log` (`consistency.py:36`). Note `recover_from_log` refuses when `db_count > 0` — `_node_count` at `consistency.py:66-71`, the refusal at `:77-79` — the helper must wipe first. `fold`/`_apply_one` (`projection/__init__.py:695/635`; `:619` is `_norm`) is **Object-blind by design** and is therefore excluded; say so in the docstring so its exclusion is a decision, not an oversight. **P2 — `recover_from_log` is contract-pinned to never raise** (`consistency.py:36-56`: "caught and reported in the result, never raised — the caller decides fail-loud policy"; pinned by `tests/test_ops_safety.py:153-166`). It therefore **returns** `{recovered: False, reason: ..., non_folded: [...]}` on a non-empty set, and the **fail-loud caller `_recover_or_raise`** (`projection/__init__.py:1227-1234` — the finding's `_recover_or_fail` is a name mismatch, verified) raises on that result. After S2, `FalkorProjection.rebuild`/`.rebuild_all` raise `UnfoldableEvents` directly; the helper catches the raise (or, for `recover_from_log`, the returned `non_folded`) and folds both into the same assertion.

5. **Mark the known-bad shapes `xfail(strict=True, reason="… — owner #NNNN")`.** `strict=True` is mandatory: it turns "unexpectedly passing" into a failure, so the `xfail` cannot silently outlive its bug. #3589's rule — *no permanently-skipped test without an owner* — is enforced by the `owner` field in the vector.

6. **Run the harness; expect the seeded `xfail`s to hold and everything else green.**

7. **Commit.** Stage the new/modified test files **and** the recording-only projection hook (`projection/__init__.py`, `projection/entities.py`).

**Tests (S0)**

| Test | What it pins |
|---|---|
| `tests/test_rebuild_live_invariant.py::test_invariant_detects_injected_non_folded_event` | the harness is not vacuous (positive assertion) |
| `::test_materialize_is_canonical_and_total` | two runs over the same graph produce identical materialisation (ordering + property set) |
| `::test_create_object_twice_rebuild_equals_live` | the baseline idempotent shape holds on all three engines today |
| `::test_create_subject_twice_rebuild_equals_live` | the Subject twin |
| `::test_rename_then_rebuild[xfail #3377]` / `::test_delete_then_recreate[xfail #3573]` | the known-bad shapes are recorded with owners, `strict=True` |
| `::test_delete_then_replay_vs_never_created` | the P1-1 tombstone distinction holds today and after S2 |
| `tests/identity_vectors.py::test_fixture_shape` | every vector carries `verdict` and `owner`; no `xfail` without an owner |
| `::test_materialize_detects_single_side_property_drift` | **P2** — mutating `supersededBy` (or dropping an extra prop) on **one side only** makes the invariant **fail**; "total" is falsifiable, not asserted |
| `::test_journal_vocabulary_has_a_dispatch_branch` | **P2/P1-E** — the vocabulary is **partitioned** (folded by `apply()` / folded by pass-1b only / not a fold) and every type in the first two partitions **plus** `Deleted`/`Renamed` has the branch its partition claims; no event type in the routed set is silently dropped by either engine. **Does not** assert a branch for the two pass-1b-only types or the four no-fold types (that assertion is red on `main` — the gap is filed, not absorbed) |
| `::test_unknown_event_type_is_recorded` | **P2** — an unknown `type` in the journal is recorded as a `shape='unhandled-event-type'` non-fold (the "run **raises**" half is `xfail`-pinned here, un-`xfail`ed at S2) |

**Verification (S0)**

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_rebuild_live_invariant.py -v
uv run pytest tests/ -q   # full docker lane — must be green (xfails hold)
```

**Rollback (S0):** revert the commit. The `non_folded` recorder and `materialize()` are **recording-only and behaviour-neutral** (they never raise, never change a write path); no production state, no journal format, no contract.

---

## Slice 1 — One key: every Object/Subject write path `MERGE`s on `id`, and every mention resolves

**Intent:** move the `MERGE` key for `Object` and `Subject` from `{name}` to `{id}` in **every** writer, **and** route every mention through the name→id resolver, while the mint is still `_entity_name_id`'s derived value. This is the groundwork that makes Slice 2 a mint-swap, and it closes the *producer-side* "second carrier" class (#3389) — a projection stub and the canonical registration can no longer land on two nodes, because they no longer use two different keys, **and** a mention that supplies no id (the `EventAPI.add_subject`/`add_object` lane) resolves to the canonical node instead of minting a random twin. That second half is the **P1-3 fix**: id-keying *alone* would turn today's name-dedup into a *second live node with the same name*.
**P1-D carve-out — adoption of a pre-existing raw/external stub is S2's job, not S1's.** S1's `create_entity` Object/Subject branch still computes `_entity_name_id` (`sdk.py:17122/17128`) and is **not** on step 6's resolver list; under id-keyed `MERGE` an existing same-name node carrying a *different* id (a raw stub, or an EventAPI mention that minted first) is therefore **not** adopted — it becomes a second live same-name node. That is exactly the shape the two `create_entity` stub-adoption tests (`tests/test_object_registered_journal.py:238`, `tests/test_subjectadded_journal.py:182`) construct, so those two tests and the "adopted by **resolution**" wording live in **S2** (where `_resolve_or_mint` exists), not here.

**Acceptance:** no `MERGE (o:Object {name:` or `MERGE (s:Subject {name:` remains in `tortoise/`; every Object/Subject write path keys on `id`; the S0 harness shows no change in materialised state for every vector **except** the two re-derived mention tests (the two `create_entity` stub-adoption tests move to S2 — P1-D), which is the honest S1 delta. **The "byte-identical" claim is withdrawn:** moving the key alone is *not* behaviour-preserving, because the one writer lane that supplies **no** id (`EventAPI.add_subject`/`add_object`, `api.py:247`/`:255-269`) mints a random `ulid()` and was previously deduped by `_upsert_*`'s MERGE-by-name; under id-keyed `MERGE` a second call lands a second live node with the same name. S1 therefore also lands the resolver and re-derives the mention tests **in S1** (the two `create_entity` stub-adoption tests move to S2 — P1-D) — this is what keeps the slice independently mergeable (`main` stays coherent with S1 alone).

**Precondition:** S0 merged (the harness is the guard for this slice).

**Files:**
- Modify: `tortoise/projection/entities.py` — `_upsert_object` (`:492-557`, `MERGE` at `:523`), `_upsert_subject` (`:436-490`, `MERGE` at `:464`), `_event_plain_merge` subject stub (`:868-873`), object stub (`:908-913`), `uses` stub (`:966-972`), lifecycle status folds (`:936-942`), `participatesIn` fallback (`:992`)
- Modify: `tortoise/projection/edges.py` — `_mint_subject_stub` (`:74-82`), `resolve_structural_target` Subject branch (`:140-145`), the non-stubbable anchor (`:160-161`)
- Modify: `tortoise/hosted_api.py` — session-capture step-3b `aboutObject` `MERGE` (`:9403`), and the Document link at `:9570`
- Modify: `tortoise/api.py` — `add_subject`/`add_object` default-id path routes through the resolver (`:247`/`:255-269`) — **the P1-3 fix**
- Modify: `tortoise/projection/entities.py` — add the local key helper **and** the `_LIVE_HOLDER` predicate + `_resolve_name` helper (below)
- Modify: `tortoise/mining.py` — `_reify_entities` (`:576/588/592/595`) keys through `_entity_key` instead of `_object_id`'s `obj_<hash16>` (P1-B)
- Modify: `tortoise/sdk.py` — `_connect_issue_objects`'s no-id fallback (`:19457-19462`) keys through `_entity_key` instead of `issue_<sha8(name)>` (P1-B); **and `_create_entity`'s name-keyed canonical-id re-fetch (`:17006-17022`) is DELETED (P1-A)**
- Test: `tests/test_entity_identity.py` (new), `tests/test_about_edges.py`, `tests/test_object_registered_journal.py`, `tests/test_subjectadded_journal.py`, `tests/test_ingest_bundle.py`, `tests/test_de2e1_entity_extraction.py` (mining key-parity, P1-B)

**Steps**

1. **Add one key-derivation helper and use it everywhere.** The projection must not import `sdk` at module scope (`tortoise/projection/__init__.py:255-257` imports `tortoise.config`/`tortoise.live`; `entities.py:14` imports `tortoise.live`) — a module-level `from tortoise.sdk import _entity_name_id` risks the `sdk → projection` cycle. Add the helper where the key is consumed:

   ```python
   # tortoise/projection/entities.py
   def _entity_key(label: str, name: str) -> str:
       """The Object/Subject MERGE key. Slice 1: still name-derived (parity with
       sdk._entity_name_id — same digest, same prefix). Slice 2 replaces this
       body with a name->id RESOLVE-OR-REFUSE lookup (the SDK write layer, not
       the projection, is the only minter) and deletes the derivation.
       Single definition so no writer can key on a different value."""
       from tortoise.sdk import _entity_name_id   # function-level: no import cycle
       return _entity_name_id(label, name)
   ```

   **Every** writer below calls `_entity_key`, never its own digest. That single-definition rule is the point of the slice — #3389 was a *second* carrier because two sites derived/assigned the id independently. **The r1-retained producers are writers too (P1-B):** `mining._reify_entities` (`mining.py:576/588/592`, the `obj_<hash16>` `_object_id`) and `_connect_issue_objects`'s no-id fallback (`sdk.py:19457-19462`, `issue_<sha8(name)>`) both call `_entity_key` instead of their own digest, so S1's id-keyed `MERGE` cannot land a second same-name carrier for the same name. S2 then converts both to `_resolve_name`/`_resolve_or_mint` **callers** so the mint flips with everything else (D4, P1-B). §A/E count these as id-writing channels.

2. **`_upsert_object` → `MERGE` by id.** Replace `:523-547`'s statement with an id-keyed `MERGE` that keeps every `ON CREATE`/`ON MATCH` clause and **drops the `o.id=coalesce($id, o.id)` `ON MATCH` term**. The stated rationale is **corrected**: once the pattern is `MERGE (o:Object {id:$id})`, a matched node **already has `o.id == $id`**, so the clause is a **provable no-op** — the earlier draft's "it makes the id mutable per mention" was wrong, because the term can only write the value it matched on. It is dropped as dead code whose *purpose* (adopting a name-stub) is now served by the resolver in step 6, not because it is harmful:

   ```cypher
   MERGE (o:Object {id:$id})
   ON CREATE SET o.name=$name, o.objectKind=coalesce($ok,'other'),
                 o.createdAt=coalesce($ca,$now), o.title=coalesce($title,''),
                 o.status=coalesce($st,'live'),
                 o.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE o.embedding END
   ON MATCH SET  o.objectKind=coalesce($ok,o.objectKind),
                 o.createdAt=coalesce(o.createdAt,$ca),
                 o.title=coalesce($title,o.title),
                 o.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE o.embedding END
   ```
   **Deliberate:** `ON MATCH` no longer writes `name` and no longer writes `id`. A re-mention must not be able to re-identify or rename the node — that is what the `Renamed` event is for (S3). **This is a behaviour change and it is the point of the slice**; the S0 harness, the resolver in step 6, and the re-derived `#452` dedup tests are its proof. Note the `status` clause stays `ON CREATE`-only (the #1350 clobber guard, `:534-540`).
   `_persist_extra_props` (`:554-557`) must re-target from `MATCH (n:Object {name: $name})` to `MATCH (n:Object {id: $id})` — as written it would silently no-op.

3. **`_upsert_subject` → `MERGE` by id**, the structural twin of step 2 (`:464-487` MERGE, `:488-490` extra-props re-target). Same `ON MATCH` narrowing.

3b. **Delete `_create_entity`'s name-keyed canonical-id re-fetch (P1-A — the ninth-cycle seed).** `_create_entity` ends (`sdk.py:17006-17022`) with `MATCH (n:{label} {{name: $name}}) RETURN n.id` over a **bare `{name}` pattern with no status filter**, taking `result_set[0][0]` — an **arbitrary row**. Under S1's id-keyed `MERGE` the fresh id always lands, so the re-fetch's premise ("MERGE by name → a fresh id never lands") is already dead; delete the block and return `self._get_entity(id_val)` on the canonical id unconditionally. It is not in the r2 read table, not in any slice's Files list, and invisible to both machine sweeps because the label is an f-string — which is exactly why it survived eight cycles. (`_get_entity` resolves `id` via `_resolve_entity`'s indexed branches, so the behaviour contract is unchanged.) If a future draft keeps it for any reason, it MUST route through `_resolve_name` and **never** return a terminal holder: under D2 it becomes a live coin flip that hands back a `status='retracted'` node on delete-then-recreate and wires `authoredBy`/`ownedBy`/`managedBy` onto the **dead** node (`sdk.py:17016-17021`). Add the pin `::test_create_returns_live_id_when_tombstone_and_live_share_a_name`.

4. **`_event_plain_merge` stubs → id-keyed.** Three sites:
   - subject stub (`:868-873`): `MERGE (s:Subject {id:$id}) ON CREATE SET s.name=$name, s.subjectKind='other'` with `$id = _entity_key("Subject", subj)`. The following edge `MATCH (s:Subject {id:$id}), (e:Event {eventId:$eid})` (`:873-876`) follows.
   - object `produces` stub (`:908-913`): `MERGE (o:Object {id:$id}) ON CREATE SET o.name=$obj, o.objectKind='other'` with `$id = _entity_key("Object", obj)`. **Do not add an `ON MATCH` id write** — the #1155-P1 comment at `:900-907` explains why the stub must not clobber the canonical id; with one key the point is moot, but keep the comment and update it.
   - `uses` stub (`:966-972`): `MERGE (o:Object {id:$id}) ON CREATE SET o.name=$name, o.objectKind=$kind ON MATCH SET o.objectKind=$kind`, `$id = _entity_key("Object", use_name)`.
   Each of the three paired edge `MATCH`es (`:873`, `:913`, `:972`) must switch its `{name:…}` anchor to `{id:…}` — a mismatched pair is the silent-no-op failure mode.

5. **`edges.py` — `_mint_subject_stub` and `resolve_structural_target`.** `_mint_subject_stub` (`:74-82`) currently does `MERGE (s:Subject {name:$name}) ON CREATE SET s.id=$name` — a **third** id convention (id == name). Change to `MERGE (s:Subject {id:$id}) ON CREATE SET s.name=$name, s.subjectKind='other'` with `$id = _entity_key("Subject", name)`, and switch `resolve_structural_target`'s Subject read (`:140-145`) and the final `MATCH (x:{label} {name:$key})` (`:160-161`) to the id key. The docstring at `:74-77` ("single create path for live + replay so a replayed descriptor mints a byte-identical stub") stays true and becomes true *by construction*.

6. **Route every mention through one resolver — the P1-3 fix.** Add a single name→id read helper and use it in **three** places: the lifecycle status folds (`:936-942`), the `participatesIn` fallback (`:992`), and — new in S1 — the **default-id path** of `EventAPI.add_subject`/`add_object` (`api.py:247`, `:255-269`). That last one is why the slice cannot be "byte-identical": under id-keyed `MERGE`, a second `add_subject(name)`/`add_object(name)` would mint a random `ulid()` and land on a **second live node with the same name** — the #3389 "second carrier" class S1 claims to close, reachable today from `extractor.py` (`api.add_subject` / `api.add_object`) and from the `_server_id` channel. `add_object`'s existing `id=` override is honoured verbatim; only the *default* (no explicit id) resolves.

   ```python
   # tortoise/projection/entities.py — the canonical live-holder predicate (D2)
   from tortoise.live import _terminal_excluded   # module scope is safe:
   # entities.py:14 already imports tortoise.live; no sdk cycle.

   def _LIVE_HOLDER(alias: str = "n") -> str:
       """The ONE live-holder predicate — DELEGATES to live._terminal_excluded
       (live.py:38-56) so the resolver, the rename refusal and the delete arm
       cannot drift from the read surfaces. Do NOT hand-roll this literal:
       `status IS NULL OR NOT (status IN $terminal OR outdated = true)` treats
       `status IS NULL AND outdated=true` as LIVE (canonical: DEAD) and, via
       Cypher three-valued logic, excludes `status='live', outdated=NULL`
       (NOT(false OR NULL) = NULL) — the exact defect D2 exists to fix (P2)."""
       return _terminal_excluded(f"{alias}.status")

   def _resolve_name(g, label: str, name: str) -> str | None:
       """Natural-key read: the single LIVE id holding *name* (D2 predicate),
       else None. Zero or >=2 live holders -> None here; S2 turns the >=2 case
       into the ambiguity refusal. Non-live holders (superseded/deprecated/
       archived/retracted/outdated) never resolve."""
       rows = g.query(
           f"MATCH (n:{label} {{name:$name}}) "
           f"WHERE {_LIVE_HOLDER()} RETURN n.id",
           params={"name": name}).result_set
       return rows[0][0] if len(rows) == 1 else None
   ```
   The predicate is the canonical one — `live._terminal_excluded` (`live.py:38-56`), which already folds `TERMINAL_EXCLUDED_STATUSES` (`live.py:33`) **and** the legacy `outdated=true` flag — not the narrower `_RECALL_OBJECT_EXCLUDED_STATUS` (`commit_ops.py:34`) / `_RECALL_OBJECT_EXCLUDED_STATUSES` (`assembly.py:1017`) name-lookup vocabularies (D2). It needs **no** `$terminal` parameter (the canonical clause inlines the status literals). In S2 the `>=2` case becomes the ambiguity refusal and `_resolve_name` grows a candidate-id return; **write the docstring now** so the S2 change is a body change, not a signature change.

7. **`hosted_api.py`.** Step-3b's `MATCH (e:Event …) MERGE (o:Object {name:$name}) MERGE (e)-[:aboutObject]->(o)` (`:9401-9405`) becomes id-keyed via the same `_entity_key`/`_resolve_name` pair. `:9570`'s `MATCH (p:Point {id:$pid}), (o:Object {name:$name})` switches its anchor to the resolved id. `hosted_api.py` already imports `TortoiseSDK`; do **not** duplicate the digest there — call `sdk`-side or import the helper.

8. **Run the S0 harness.** Expect a diff **only** on the mention/stub vectors, which are the re-derived ones; every other vector must show zero materialisation diff. Then run the full docker lane and confirm the four re-derived mention/stub tests pass with their new expectations.

**Tests (S1)**

| Test | What it pins |
|---|---|
| `tests/test_entity_identity.py::test_no_name_keyed_object_merge_remains` | a source-level assertion (`grep`-equivalent over `tortoise/`) that no `MERGE (o:Object {name:` / `MERGE (s:Subject {name:` survives — the slice's own acceptance, machine-checked. **Widened (P1-A):** it also asserts no surviving name-keyed **read** coordinate (a `MATCH … {name:$X}` that is the sole key, incl. f-string labels) outside §B's routed set — so a site like `_create_entity`'s re-fetch cannot hide behind an f-string |
| `::test_create_returns_live_id_when_tombstone_and_live_share_a_name` | **P1-A regression pin** — create a tombstone (`status='retracted'`) + a live node sharing a name; call `create_object(name)` **twice** and assert the returned id is the **live** one on **both** calls (the deleted one never comes back through the canonical-id re-fetch) |
| `::test_stub_and_canonical_share_one_node` | the #3389 shape: a connector-event stub followed by a canonical `create_object` yields **one** node, not two |
| `::test_upsert_on_match_does_not_rewrite_id_or_name` | a second `ObjectRegistered` for the same id does not change `id` or `name` |
| `tests/test_about_edges.py::test_create_event_prefixed_id_no_stub` (`:176`) | still green — the guard + resolution path unchanged |
| `tests/test_about_edges.py::test_create_event_name_still_resolves` (`:207`) | still green — name-valued `aboutSubject` still resolves |
| `tests/test_object_registered_journal.py` (all 15) / `tests/test_subjectadded_journal.py` (all) | the journal and live-vs-replay state hold the **new** mention semantics: one canonical node per name, no random-id twin |
| `tests/test_subjectadded_journal.py:594` `test_random_ulid_mention_between_sdk_creates_is_by_design` — **re-derived IN S1** | the by-design double-registration is **gone**: `add_subject(name)` resolves to the canonical id, so the SDK journal holds ONE `SubjectAdded`, not two |
| `tests/test_subjectadded_journal.py:236` (`test_eventapi_mention_adopts_created_at_on_stub`) — **re-derived IN S1** | the EventAPI mention resolves name→stub id and emits its registration with that id, so the stub is adopted by the id-keyed `ON MATCH`; the journaled id is the stub's |
| `tests/test_object_registered_journal.py:238` / `tests/test_subjectadded_journal.py:182` (`create_entity` stub adoption) — **moved to S2 (P1-D)** | `create_entity` still computes `_entity_name_id` in S1, so a raw stub (different id) is **not** adopted by id-keyed `MERGE` — it needs S2's `_resolve_or_mint`; see S2's test table |
| `::test_mention_does_not_mint_a_second_carrier` | the P1-3 regression pin: `create_object("X")` then `add_object("X")` (no explicit id) leaves exactly ONE live node |
| `tests/test_rebuild_live_invariant.py` (all) | the S0 guard: **zero** diff in materialised state on every vector except the re-derived mention/stub ones |
| `tests/test_ingest_bundle.py`, `tests/test_status_projection.py` | the ingest `existed` probes (`sdk.py:7846/7853`) and the status folds still agree |

**Verification (S1)**

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_entity_identity.py tests/test_about_edges.py \
  tests/test_object_registered_journal.py tests/test_subjectadded_journal.py \
  tests/test_rebuild_live_invariant.py -v
uv run pytest tests/ -q          # full docker lane
grep -rn "MERGE (o:Object {name\|MERGE (s:Subject {name" tortoise/   # expect: no output
```

**Rollback (S1):** revert the commit. The id source is unchanged, so there is no data or journal consequence; the derived id is still `f(name)` and any node keyed by it is still found by `_resolve_entity`'s `id` branch (`projection/__init__.py:2446`). A rollback is safe even mid-flight.

---

## Slice 2 — Minted identity: mint the id, make the name a resolved natural key, make deletion terminal

**Intent:** the model change. `Object`/`Subject` get a minted ULID at creation (R1); `name` becomes a natural key resolved through a lookup (R2); deletion is a terminal journaled event and re-creation mints fresh (R4/R5); producers register what they reference, so nothing is minted speculatively inside the projection (D9). This is the slice that makes #3573's burial shapes structurally impossible.

**Acceptance:** (a) no Object/Subject write path derives an id from a name — **including the two producers r1 had retained**: `mining._object_id` (`mining.py:530-537`) and `_connect_issue_objects`'s `sha256(name)[:8]` fallback (`sdk.py:19457-19462`) are converted to resolver **callers** (S1 key-parity, S2 mint-flip), not scoped out (P1-B); (b) `delete → recreate` yields two distinct ids and survives replay; (c) a deleted entity does **not** resurrect; (d) the S0 harness passes with the `#3573`/`#3377`-delete `xfail`s removed and the non-folded set empty; (e) `create_object(name)` twice is still idempotent; (f) ambiguous by-name resolution refuses and fails the run.

**Precondition:** S1 merged (every writer keys on `id`).

**Files:**
- Modify: `tortoise/sdk.py` — `create_entity` Object/Subject branches (`:17121-17134`), `_create_entity` (`:16765`, the two existence probes `:16855-16906`), `_delete_entity` (`:17075-17088`), `_update_entity` (`:17034-17072`), the ingest entity loop (`:7840-7862`), and `_connect_issue_objects`'s fallback + R5 guard (`:19457-19462`; P1-B/P1-C)
- Modify: `tortoise/projection/entities.py` — replace `_entity_key`'s body with resolve-or-mint; `_upsert_object`/`_upsert_subject` (name no longer the key); `_resolve_name` becomes the ambiguity-refusal site; add the `Deleted` fold
- Modify: `tortoise/projection/__init__.py` — `apply()` dispatch (`:1290-1394`), the pass-1b loop (`:1673-1678`), `_ensure_indexes` (`:2676-2690`) if the id index needs no change (it does not — `Object`/`Subject` already index `id`)
- Modify: `tortoise/api.py` — `add_subject` gains `id=` (`:247`), `add_object` unchanged (`:255`)
- Modify: `tortoise/connectors/slack.py:267`, `tortoise/connectors/linear.py:190`/`:217`, `tortoise/mining.py:825` — emit the registration before the event (D9)
- Modify: `tortoise/sdk.py:11507-11515` — route the `EventRecorded` through `_emit_event` instead of a bare `proj.apply` (P2; registering the Subject alone leaves the Event + `performs` edge live-only)
- Modify: `tortoise/mining.py` — `_reify_entities` drops `id=self._object_id(name)` and becomes a resolver caller; delete `_object_id` (`:530-537`) (P1-B)
- Modify: `tortoise/hosted_api.py` — `/v1/objects` (`:4522`) and `/v1/subjects` (`:4559`) docstrings; the `_data_sdk` lane needs no `event_log_path` change
- Modify: `tortoise/mcp_server.py` — `tortoise_create_object` (`:2226`), `tortoise_create_subject` (`:2218`) docstrings
- Test: `tests/test_entity_identity.py`, `tests/test_object_registered_journal.py` (**rewrite tests 14/15**, `:295-360`), `tests/test_subjectadded_journal.py`, `tests/test_rebuild_live_invariant.py` (drop the `#3573` `xfail`s), `tests/test_capture_session.py:1678-1741`, `tests/test_status_projection.py:230-401`, `tests/test_api.py`

**Steps**

1. **Add the resolve-or-mint resolver — one function, two callers.**

   ```python
   # tortoise/sdk.py (module level, near _entity_name_id)
   def _mint_entity_id() -> str:
       from .ids import ulid
       return ulid()
   ```

   and in the write path, one resolver consulted by *creation* only:

   ```python
   def _resolve_or_mint(self, label: str, name: str) -> tuple[str, bool]:
       """Return (id, created). Exactly one LIVE holder -> (its id, False).
       Zero -> (minted, True). Two or more -> AmbiguousEntityName. Never picks.
       The read+mint+register is serialized on a per-(label,name) lock: the
       resolve is check-then-act and D2 legalises same-name carriers, so no
       unique index can close the race (P2)."""
       key = (label, name)
       lock = _name_holder_locks.setdefault(key, threading.Lock())
       with lock:
           rows = self._get_proj().g.query(
               f"MATCH (n:{label} {{name:$name}}) "
               f"WHERE {_LIVE_HOLDER()} RETURN n.id",
               params={"name": name}).result_set
           if len(rows) == 1:
               return rows[0][0], False
           if len(rows) > 1:
               raise AmbiguousEntityName(name=name, label=label,
                                         candidate_ids=[r[0] for r in rows])
           return _mint_entity_id(), True
   ```

   `AmbiguousEntityName` is a new structured exception (naming the name and every candidate id) — the R8 "refuse, never guess" path. It is **not** a warning. **`_name_holder_locks` closes the P2 check-then-act race WITHIN A PROCESS**, mirroring the existing `_source_merge_locks` pattern (`sdk.py:1448`, `:1607-1610`) — one lock per `(label, name)`, held across read+mint+register, so two concurrent `create_object("X")` **in one process** cannot both see zero holders and both mint. **The claim is "closed within a process", NOT globally (P2):** the lock registry is a module-level `dict[str, threading.Lock]`, exactly like `_source_merge_locks`, so **two processes (or two SDK instances in different processes)** can each see zero holders and mint → two live same-name carriers → the name is **permanently create-poisoned** (every later `create_*`/bare-name reference refuses) **with no by-name repair path** (`_resolve_entity` has no name branch, `projection/__init__.py:2452-2513`). Since D2 forbids a unique name index (same-name coexistence is legal), this residual is **structural, not closable in this plan** — it is named here and in U7, and is **acceptable pre-beta** (0 customers, no production Object/Subject). The optional hardening — a **post-mint re-check** that re-reads the live holders and raises `AmbiguousEntityName` if a carrier appeared during the mint window — narrows the window without closing it, and is listed in "Open for the implementer". The concurrency test stays because the mechanism exists; it is **not** shipped as a test-without-mechanism. The `_LIVE_HOLDER` predicate is the shared one from S1 step 6 (D2) — one definition, so the resolver and the rename-refusal cannot drift.

2. **`create_entity` Object/Subject branches use the resolver.** Replace `_entity_name_id("Subject", name)` (`:17121`) and `_entity_name_id("Object", name)` (`:17127`) with `self._resolve_or_mint("Subject"/"Object", name)`. Add the `_server_id` keyword to `create_entity`'s Object/Subject paths (D4) — when supplied, it wins and is never reachable through `props` (the existing `"_server_id" in props` guard at `:17115` already covers the flatten).

3. **Narrow `_create_entity`'s existence probes to the id alone, and make them DELETION-aware (P1-B).** The two probes (`:16862` for Object, `:16892` for Subject) currently test `{id:$cid, name:$name}` — the `name` conjunct exists *only* to harden against a cross-name sha-digest collision, which cannot happen once the id is random (the comment at `:16847-16852` says so explicitly). Delete the name conjunct, keep the fail-open-to-journaling behaviour (`:16870-16877`) and its rationale — **but** the probe now branches on the **deletion marker** (P1-B: `status='retracted'` and/or `deletedAt IS NOT NULL`), **not** `TERMINAL_EXCLUDED_STATUSES`: if the id matches a **deleted** node the write **fails closed** (`IdReuseError`), while a **superseded/deprecated/archived/outdated** holder is accepted as the existing replay-idempotent no-op (the #1350 clobber guard, `tests/test_object_registered_journal.py:199-228`). Using `TERMINAL_EXCLUDED_STATUSES` here would raise live on a legitimate superseded re-mention — see step 6a.
   **P1-D — adoption must journal, or it is live-without-durable.** A resolved holder the journal does **not** carry (the raw-stub shape) must still be registered: the projection no longer mints (step 4), so a skipped registration leaves a node that replay cannot reproduce. The probe's skip therefore reduces to "this id is already journal-represented"; there is no cheap journal-id index, so the safe implementation is to **emit the registration on every `create_entity` call** — duplicate `ObjectRegistered`/`SubjectAdded` lines are replay-idempotent (the existing fail-open rationale), and the alternative is the live-without-durable class the plan exists to remove. The two `create_entity` stub-adoption tests (`tests/test_object_registered_journal.py:238`, `tests/test_subjectadded_journal.py:182`) are re-derived here (P1-D): the resolved stub id is the registration's id, the adopted live node carries the journaled `createdAt`, and `status` follows the lane's existing `_persist_extra_props` rule.
   **Journal-growth cost (P2), stated:** "emit on every call" makes the journal grow with **create-call count**, not entity count — repeated `onboarding.seed` re-runs, `extract_session_v2` loops, `/v1/objects` re-POSTs and connector re-syncs each append a duplicate registration line, and `rebuild` cost grows with it. The choice is kept (it is the safe one: skipping is live-without-durable), and the growth is bounded only if wanted — an in-process set of already-journaled ids in the SDK skips re-emitting within one process, at the cost of an unjournaled re-registration after a restart (the current behaviour). Record the measured line-count delta when S2 lands.

4. **Replace `_entity_key`'s body (from S1) with a resolve-or-REFUSE lookup.** The projection-side callers (the three `_event_plain_merge` stubs, `_mint_subject_stub`, `resolve_structural_target`) now **resolve** the name through `_resolve_name`; when it resolves to zero live holders they **do not mint** — they record a non-folded `unregistered-reference` entry and leave the write/edge unwired (step 5). The projection is never the minter: **the SDK write layer is the only place a new id is minted** (the resolver in step 1), which is what makes the mint journalable and replay-deterministic (D9). The hosted `hosted_api.py:9403` write path is a write path, so it calls the **SDK** resolver (step 1), not `_entity_key`.
   **The two r1-retained producers convert here (P1-B).** `mining._reify_entities` (`mining.py:576/588/592`) drops `id=self._object_id(name)` and calls the SDK resolver; `_object_id` and its `content_hash` derivation are **deleted**, and the re-fetch-by-name (`:588-591`) becomes a resolve. `_connect_issue_objects`'s no-id fallback (`sdk.py:19457-19462`) calls `_resolve_name` and mints only on zero live holders. Their re-run shapes join R5's blast radius (6a/6b): a re-run **resolves** to the live holder (idempotent, no duplicate), and a delete→re-mine mints a **fresh** id — no `IdReuseError` — so mining's documented idempotent re-run is **preserved**, not broken. `tests/test_de2e1_entity_extraction.py` (`_expected_object_id`, `:89/370-387`) is a #3589 (B) case: keep the scenario (punctuation-canonicalization stability), re-derive the expectation from the resolved id.

5. **Journal the stub mint in the write layer, not the projection (D9).** The projection cannot append to the JSONL log — `_emit_event` lives on the SDK (`sdk.py:2275`) — so it cannot mint: a projection-side mint would be replay-nondeterministic (step 4).
   - In `_event_plain_merge`, when a name resolves to **zero** holders, **refuse to mint**: record a non-folded entry naming `(journal position, label, name, failure shape='unregistered-reference')` and leave the edge unwired. Do **not** mint in the projection.
   - **5a — an id-keyed miss is a non-fold, not a silent no-op (P1-5).** Every fold that keys on an id (`Deleted`, `Renamed`, `ObjectRegistered`, `SubjectAdded`, and any `MATCH (n:{label} {id:$id})` that matches nothing) records a non-folded entry `(journal position, label, id, failure shape='missing-node')`. The earlier draft recorded non-folds only on the by-NAME path, so a dropped id-keyed write was invisible to the invariant — and a dropped edge is the harm class the plan itself calls out (D10).
   - In the producers enumerated in D9, emit the registration (`ObjectRegistered`/`SubjectAdded`) with a minted id **before** the `EventRecorded` line so the replay looks the name up and finds it.
   - This makes the projection's mint path reachable only from the SDK write path, where `_emit_event` can journal it.
   - **5b — the replay entry points FAIL CLOSED on a non-empty non-folded set (P1-5).** `FalkorProjection.rebuild` and `.rebuild_all` raise `UnfoldableEvents(non_folded=[...])` at end-of-replay when the set is non-empty — this is what makes #3590's fail-closed precondition true in production, not just at test time (D10). **`consistency.recover_from_log` must NOT raise** (P2): its docstring contracts "caught and reported in the result, never raised — the caller decides fail-loud policy", and `tests/test_ops_safety.py:153-166` pins it; it returns `{recovered: False, reason: ..., non_folded: [...]}` and `_recover_or_raise` (`projection/__init__.py:1227-1234`) raises. The live `apply` path records but does not raise; the harness asserts it.

6. **Make deletion terminal, and make it a TOMBSTONE on both sides (R4/R5; P1-1/P1-2 fixes).** In `_delete_entity` (`:17075-17088`):
   - probe the label(s) holding `id_val` **before** any write, capturing `(label, name)`;
   - **replace** each `Object`/`Subject` `DETACH DELETE n` arm with a tombstone write **gated on the live-holder predicate** — `MATCH (n:{label} {prop:$id}) WHERE {_LIVE_HOLDER()} SET n.status='retracted', n.deletedAt=$ts RETURN count(n)` — **where `$ts` is captured ONCE (`now_iso()`) at the top of `_delete_entity` and is the SAME value carried in the `Deleted` payload and used by `_fold_deleted` (P1-C)** — the same statement the replay fold applies (the four non-entity arms keep their existing behaviour, D5). **P2:** the `WHERE {_LIVE_HOLDER()}` conjunct is load-bearing — without it a **second** delete re-matches the tombstone, emits a second `Deleted`, and returns `True`, contradicting `test_double_delete_emits_once_and_returns_false`; gated, `count(n)=0` for an already-terminal node, so the caller emits nothing and `total` stays 0 → returns `False`. **P2 — the mixed-label ordering:** the label loop runs `Point → Subject → Object`, so the **Point arm must skip identity labels** (`MATCH (n:Point {id:$id}) WHERE NOT (n:Object OR n:Subject) DETACH DELETE n RETURN count(n)`); otherwise a `:Point:Object` node is hard-deleted by the Point arm first, `total` is > 0 from that arm, no `Deleted` is emitted, and replay resurrects it (the exact divergence this plan exists to close). The **identity label's** arm writes the tombstone, and exactly one `Deleted` is emitted;
   - when `total > 0` and an `event_log_path` is configured, emit **one** `Deleted(id=id_val, label=<label>, name=<name>)` line via `self._emit_event` (`:2275`), **post-apply** — mirroring `_create_entity`'s EventRecorded ordering (`:16946`) and its phantom-event rationale (`:16962-16971`). A repeated delete of a non-existent id emits nothing (there is nothing to terminalize). **P2 — the terminal append must be FAIL-LOUD, not swallowed.** `_emit_event`'s JSONL append swallows failures (`sdk.py:2377-2388`, "a log-write failure must not crash the caller"), so a failed append on `Deleted` would leave a **live tombstone with no journal line** — silent live-without-durable, the exact harm class this plan removes, on the new path. Add a `strict_append=True` (or `terminal=True`) parameter to `_emit_event` that **re-raises** the append failure, and pass it for `Deleted` (S2) and `Renamed` (S3); the existing best-effort swallow stays the default for the non-terminal lanes. Pin it with a test that injects an append failure and asserts the delete **raises** rather than returning `True` — **not** a test pinned to a swallow;
   - **6a — ENFORCE R5 (no id reuse) at the `_upsert_*` CHOKE POINT, not at `_create_entity` (P1-C).** A journaled `Deleted` for an id is a permanent bar on re-registering it. The bar lives in **`_upsert_object`/`_upsert_subject`** (`projection/entities.py:492`/`:436`, dispatched at `projection/__init__.py:1376`/`:1383`) — the single seam every `ObjectRegistered`/`SubjectAdded` writer passes through, funnel (`EventAPI.add_object` → `_emit` → `apply` → `_upsert_*`) or direct (`mining.add_object(id=…)`, `github_map` → `apply` → `_upsert_*`), on **live and replay** alike. `_create_entity`'s deletion-aware probe (step 3) is kept as a fast-path pre-check but **no longer carries the coverage claim**. **Live: `_upsert_*` probes the graph for a node carrying that id that the DELETION MARKER marks — `status='retracted'` and/or `deletedAt IS NOT NULL` → `IdReuseError`. It must NOT use `TERMINAL_EXCLUDED_STATUSES` (P1-B):** that set includes `superseded`/`deprecated`/`archived`/`outdated`, so a **superseded** id would raise live while folding as a no-op on replay — asymmetric, and it breaks the pinned #1350 guard (`tests/test_object_registered_journal.py:199-228`), which exists precisely because superseded re-mentions are expected and must be **accepted as no-ops** (reachable via an idempotent `github_map`/`_connect_issue_objects` re-sync of a superseded id, and via step 3's emit-on-every-call rule). Replay: `_fold_deleted` records the id in the per-projection `tombstoned_ids` set, and `_upsert_*` consults it → a non-folded `id-reuse` entry, **no** resurrect. **The raw-write carve-outs are closed explicitly (P2) — two, not one:** `_connect_issue_objects` (`sdk.py:19457-19462`) issues its own raw `MERGE (o:Object {id:$oid})`; `hosted_api.py:9403` (S1 resolve-or-mint) supplies an id outside `_upsert_*`; and `onboarding.state.write_onboards_edge` (`onboarding/state.py:401`) raw-`MERGE`s a caller-supplied `{id:$sid}` through neither `_upsert_*` **nor** `apply`. Each calls the same shared guard helper (`_reject_tombstoned_id(g, label, id)`, also used by `_upsert_*`) at its `MERGE`; the §A/E channel count (now **9**) must include all three. Without this, replay keeps the id `retracted` while live leaves it live — the same harm class as P1-A, new mechanism;
   - **6b — the covered-channel count equals the id-writing-channel count (P1-C).** Blast radius §A/E enumerates the id-writing channels; the S2 test `test_id_reuse_is_refused_on_every_id_writing_channel` is parametrised over exactly that set, so a channel added without a guard — or a guard that stops covering a channel — turns the count mismatch into a failure rather than a silent gap.
   - Register the type as JSONL-only: absent from `_GRAPH_EVENT_TYPES` (`sdk.py:731-743`), absent from `CLAIM_EVENT_TYPES` (`shared_state/events.py:162-174`), no `docs/event-catalog.md` row.

   > **Trace that motivates 6a (verified against the code).** `OR(cid,X)` → `Deleted(cid)` → `OR(cid,X)`. Live: tombstone then re-create with the same explicit id ⇒ live. Replay: `OR` creates `cid` live; `Deleted` sets it `retracted`; the second `OR` MERGEs on `cid` and (the `status` clause is `ON CREATE`-only, the #1350 clobber guard, step 2) does **not** rewrite `status` ⇒ stays `retracted`. **Replay buried where live is not.** It is reachable because `EventAPI.add_object(id=…)` (`api.py:255-269`), `add_subject(id=…)` (new in S2), `_server_id` (D4), `_connect_issue_objects`' **item-supplied** connector ids, and `github_map`'s `github-issue-{repo}-{n}` are all **explicit-id** channels — none is a name derivation, so removing `mining._object_id`'s determinism (P1-B) removes one *instance*, not the class.

7. **Add the `Deleted` fold to both replay paths.**
   - `apply()` (`projection/__init__.py:1290`): `elif t == "Deleted": self._fold_deleted(ev)`.
   - `rebuild_all`'s pass-1b loop (`:1673-1678`): the same branch.
   - `_fold_deleted` is an id-keyed idempotent property write: `MATCH (n:{label} {id:$id}) SET n.status='retracted', n.deletedAt=$ts` — **byte-identical to the live tombstone** (P1-1) — where `$ts` is the **journaled** `ev.get("deletedAt")`, **never** a rebuild-time `now_iso()` (P1-C; the envelope `ts` `_emit_event` stamps at `sdk.py:2339-2345` is a *different* `now_iso()` call and cannot serve) — and it adds the id to `tombstoned_ids` so a later registration of the same id is a non-folded `id-reuse` entry (P1-2). A miss is a non-folded `missing-node` entry, not a silent no-op (step 5a). **Decision: `status='retracted'` (a tombstone) — for both live and replay** — because a hard delete would lose the node's id history, make replay order-sensitive, and (the P1-1 defect) make live and replay disagree; the rejected hard-delete variant is recorded in D5. The label is **not** interpolated from the journal line unvalidated — validate it against the fixed label set (the `_resolve_entity` defense-in-depth precedent at `projection/__init__.py:2507-2512`).
   - Because the fold keys on the **journaled id**, and a re-creation carries a **different** id (R5), the fold can never touch the new incarnation. **That is the structural kill for #3573 P1-A and P1-B**: both shapes require the fold to reach a node by *name*; there is no name in the fold.

8. **Point the two `xfail`-pinned delete shapes at the fix, and re-derive the "gone" assertions to tombstone semantics (P1-1).** Remove `xfail` from `delete_object` and `delete_then_recreate` in `tests/fixtures/entity_identity_vectors.json`; they must now pass. Rewrite the two green-pins that assert the opposite:
   - `tests/test_object_registered_journal.py:309` (`test_deleted_object_resurrects_on_rebuild`) → invert: the deleted Object does **not** resurrect, and the `Deleted` line folds.
   - `tests/test_object_registered_journal.py:335` (`test_delete_recreate_replays_first_incarnation`) → invert: two ids, live node is the second, replay reproduces exactly that.
   - `tests/test_subjectadded_journal.py:480-520` (the Subject delete twins) → same inversion.
   - **`tests/test_object_registered_journal.py:322` / `tests/test_subjectadded_journal.py:485`** — the `assert not rows, "live node must be gone after delete"` pins (`_object_row`/`_subject_row` match by **name** with no status filter) go red under tombstone semantics. Re-derive to assert the node is **present with `status='retracted'`** and **absent from a live read surface** (a recall/search predicate), not absent from the graph.

9. **Update the identity-dependent tests to the new expectation.** These currently assert the id *is* the name-derived value; each gets a deliberate verdict (the #3589 (B) discipline — keep the scenario, re-derive the expectation):
   - `tests/test_object_registered_journal.py:134/270/318/345/451/482/701/752` (`assert line["id"] == _entity_name_id("Object", name)`) → assert `line["id"] == node_id` read from the graph, and separately assert the id is a ULID.
   - `tests/test_subjectadded_journal.py:116/213/286/480/505/629/635/642` → same.
   - `tests/test_capture_session.py:1704/1741/2051` → same (the `legacy-X`/`legacy-Y`/`mx-B` synthesis scenarios; keep the scenarios, re-derive).
   - `tests/test_status_projection.py:230/319/360/401` → same; note `:333`'s comment about "era's id shape (github-style, NOT `_entity_name_id`)" — that scenario becomes the *explicit-id* channel instead of a legacy exception.
   - `tests/test_object_registered_journal.py:113` — the unicode/cross-name-digit note: the `name` conjunct is gone (step 3), so re-state what the test now pins.

10. **Add the ambiguity, unregistered-reference and id-reuse failure tests** (Journey "Failure Modes"): two live same-name carriers → `AmbiguousEntityName`; a bare-name `EventRecorded` reference with no registration → a non-folded entry and a failing harness run; the `[OR(id), Deleted(id), OR(id)]` vector → `IdReuseError` live and a non-folded `id-reuse` entry in the fold, failing the run on every engine. Also add the "live holder" vectors: one live + one terminal holder resolves to the live one (**not** ambiguous); a lone terminal holder is **refused**, never returned.

11. **Update the documented contracts in the same commit as the behaviour** (a docstring that lies is a bug, and this repo files those):
    - `/v1/objects` (`hosted_api.py:4526-4530`): *"deterministic id by name"* → *"a minted opaque id; idempotent by name — a repeat returns the existing node"*.
    - `/v1/subjects` (`:4562-4566`) the same.
    - `mcp_server.py:2218/2226` docstrings.
    - `_upsert_object`/`_upsert_subject` docstrings (`entities.py:493`, `:437`): "MERGE by name (content-hash dedup)" → "MERGE by minted id; the name is a natural key".

12. **Run the S0 harness with the `#3573` `xfail`s removed; then the full docker lane.**

**Tests (S2)**

| Test | What it pins |
|---|---|
| `tests/test_entity_identity.py::test_create_object_mints_opaque_id` | the id matches `_ULID_RE` and is not `obj-<sha26>` |
| `::test_create_object_second_call_is_idempotent_by_name` | `#452` dedup preserved through the resolver |
| `::test_create_subject_mints_opaque_id` / `::test_create_subject_second_call_is_idempotent_by_name` | the Subject twin |
| `::test_rename_free_name_then_recreate_still_two_distinct_ids` | D2's coexistence rule (via the explicit-id/explicit-id route — **not** via rename, which D3 refuses) |
| `::test_delete_journals_one_terminal_line` | exactly one `Deleted`, with `id` **and** `name` |
| `::test_double_delete_emits_once_and_returns_false` | **P2** — the live arm is gated on `_LIVE_HOLDER()`: a second delete matches 0 live holders, emits **no** second `Deleted`, and returns `False` |
| `::test_delete_then_recreate_mints_fresh_id` | R5 |
| `::test_delete_then_recreate_survives_rebuild` | the S0 vector, un-`xfail`ed |
| `::test_delete_tombstones_live_and_replay_identically` | **P1-1** — live and replay both leave `status='retracted'`; no `DETACH DELETE` on either side |
| `::test_deleted_node_is_invisible_to_recall_but_present_to_materialize` | **P1-1** — recall/search exclude it; `materialize()` keeps it |
| `::test_id_reuse_after_delete_fails_closed` | **P1-2/P1-B** — `[OR(id), Deleted(id), OR(id)]` raises `IdReuseError` live and records a non-folded `id-reuse` entry in the fold. The live predicate is the **deletion marker** (`status='retracted'`/`deletedAt`), never `TERMINAL_EXCLUDED_STATUSES` |
| `::test_superseded_id_reregistration_is_a_noop_not_a_raise` | **P1-B** — re-register (via an explicit-id channel) a **superseded** id: live **does not raise** (the #1350 no-op) and replay folds without a non-fold; the #1350 clobber guard is preserved (`tests/test_object_registered_journal.py:199-228`). Guards against R5 reverting to `TERMINAL_EXCLUDED_STATUSES` |
| `::test_mismatched_journaled_deleted_at_breaks_invariant` | **P1-C** — a `Deleted` line whose journaled `deletedAt` differs from the live tombstone's **breaks** `rebuild == live`, proving `deletedAt` is **compared** and not allowlisted; the matching case passes |
| `::test_id_reuse_is_refused_on_every_id_writing_channel` | **P1-C** — parametrised over the §A/E id-writing channels: `create_entity`, `_server_id`, `EventAPI.add_object(id=…)`, `EventAPI.add_subject(id=…)`, `_connect_issue_objects`, `github_map`→`apply`, `mining.add_object(id=…)`, `hosted_api.py:9403`, `onboarding.state.write_onboards_edge` — each refuses (live `IdReuseError` / replay `id-reuse` non-fold), and the parameter-set size **equals** the §A/E channel count of **9** (6b) |
| `::test_post_delete_rerun_of_mining_resolves_and_remine_mints_fresh` | **P1-B** — mining's re-run resolves to the live holder (idempotent, no duplicate); delete→re-mine mints a fresh id (no `IdReuseError`) |
| `tests/test_object_registered_journal.py:238` `test_stub_adoption_journals_canonicalization` — **moved to S2, re-derived (P1-D)** | a raw name-stub (id `X`) resolved by `_resolve_or_mint` is **adopted**: the registration carries `X`, the merge key is `id`, and no second same-name node appears; live == replay |
| `tests/test_subjectadded_journal.py:182` (Subject twin) — **moved to S2, re-derived (P1-D)** | same, with the Subject lane's live `status='live'` (`_persist_extra_props`) asymmetry preserved |
| `::test_ambiguous_name_resolution_fails_closed` | ≥2 live same-name → `AmbiguousEntityName`, never a pick |
| `::test_one_live_one_terminal_holder_is_not_ambiguous` / `::test_lone_terminal_holder_reference_refuses` | **P2** — the D2 `live.TERMINAL_EXCLUDED_STATUSES` predicate, both directions |
| `::test_reference_to_unknown_name_fails_closed` | D9's unregistered reference → non-folded entry + failing run |
| `::test_id_keyed_miss_is_a_non_fold` | **P1-5** — an id-keyed `Deleted`/`Renamed`/registration whose id no node carries records `missing-node` on the LIVE and replay paths; not a silent no-op (S2 enables it; S0 already records unknown/malformed types) |
| `::test_deleted_append_failure_is_fail_loud` | **P1-D (option (b))** — an injected JSONL append failure on the terminal `Deleted` line **raises** (via `strict_append`), and the test asserts only what post-apply can deliver: **the delete raised, the live tombstone is present, and the journal line is absent**. It does **NOT** assert "no live-tombstone-without-journal" (unachievable post-apply) — that residual and its retry semantics (`a second delete returns False`) are documented in the Checklist Atomicity note. The non-terminal lanes keep the best-effort swallow |
| `::test_replay_entry_points_fail_closed_on_non_folded` | **P1-5/P2** — `rebuild`/`rebuild_all` raise `UnfoldableEvents`; `recover_from_log` **returns** `{recovered: False, ..., non_folded: [...]}` (never raises, per its contract) and `_recover_or_raise` raises; #3590's precondition is met in production |
| `::test_concurrent_create_object_same_name_is_idempotent` | surface 5's race, closed by the `_name_holder_locks` per-`(label,name)` lock |
| `tests/test_rebuild_live_invariant.py::test_delete_then_replay_vs_never_created` | **P1-1** — the two must not compare equal |
| `tests/test_object_registered_journal.py::test_deleted_object_resurrects_on_rebuild` **rewritten** → `test_deleted_object_does_not_resurrect` | the inverted green-pin |
| `tests/test_object_registered_journal.py::test_delete_recreate_replays_first_incarnation` **rewritten** → `test_delete_recreate_replays_two_incarnations` | the inverted green-pin |
| `tests/test_object_registered_journal.py:322` / `tests/test_subjectadded_journal.py:485` **re-derived** | **P1-1** — "live node must be gone" → "present as a tombstone, absent from read surfaces" |
| `tests/test_object_registered_journal.py:199-228` (`test_remenion_after_fold_does_not_journal_or_resurrect`) — **in S2's re-derived set (P1-B)** | a **superseded** name re-mentioned stays superseded, journals nothing, and does not resurrect — the #1350 clobber guard R5 must not break by treating supersession as deletion |
| `tests/test_subjectadded_journal.py` delete twins | Subject parity |
| `tests/test_rebuild_live_invariant.py` | the harness, with the `#3573` `xfail`s removed, green on all three engines; **edge-set** comparison included |
| `tests/test_capture_session.py`, `tests/test_status_projection.py`, `tests/test_ingest_bundle.py`, `tests/test_api.py` | the re-derived identity expectations |

**Verification (S2)**

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_entity_identity.py tests/test_object_registered_journal.py \
  tests/test_subjectadded_journal.py tests/test_rebuild_live_invariant.py -v
uv run pytest tests/ -q
grep -rn "MERGE (o:Object {name\|MERGE (s:Subject {name" tortoise/   # expect: no output
```

**Rollback (S2):** revert the commit. It reverses to S1's state (derived ids, tombstone-less deletes) — coherent and green. **No journal back-compat concern**: with `event_log_path` opt-in and no production Object/Subject journal (D8), the only journals in existence are test-scoped and are created by the reverted code. If a `Deleted` line *has* been written by a real caller before rollback, the reverted code has no `Deleted` branch and will ignore it — the resurrection bug returns, which is the pre-S2 behaviour, not a worse one. **A tombstone written by S2 stays in the graph after revert** (the reverted `Delete` path cannot see it via the name+status filters, so a re-`create` re-mints); recover by deleting the test graph, not by hand-editing nodes.

---

## Slice 3 — Rename is a journaled mutation (R3), and `supersededBy` becomes an id

**Intent:** `update_entity(id, name=…)` becomes a durable mutation on the stable id (fixes #3377), a rename onto a live holder's name is **refused** (D3), and the supersession fold's successor reference stops being a name (D6), so a rename can no longer dangle it.

**Acceptance:** a rename emits one `Renamed(id, old, new)` line; a rename onto a live holder's name is refused with `NameAlreadyHeld`; a rebuild reproduces the new name and the old name resolves to nothing; `supersededBy` holds the successor's id; the S0 harness's `#3377` `xfail` is removed.

**Precondition:** S2 merged (a rename can only replay onto a stable id).

**Files:**
- Modify: `tortoise/sdk.py` — `_update_entity` (`:17034-17072`), specifically the per-label write loop
- Modify: `tortoise/projection/entities.py` — `_fold_object_superseded` (`:559-680`, the `supersedes_by` computation `:610-612`)
- Modify: `tortoise/commit_ops.py` — the successor probe (`:412-419`) and the ref probe (`:434-437`)
- Modify: `tortoise/projection/__init__.py` — `apply()` dispatch (`:1290`), pass-1b loop (`:1673`)
- Test: `tests/test_entity_identity.py`, `tests/test_rebuild_live_invariant.py`, `tests/test_capture_session.py`

**Steps**

1. **Emit the rename event.** In `_update_entity`'s per-label loop (`:17066-17071`), when `props` carries `name` and a node actually matched: **first refuse if the new name is held by a different live node** (`_resolve_name` from S1 step 6; raise `NameAlreadyHeld`, D3 — the write must not be allowed to create ambiguity that later create-poisons the name with no by-name repair path), then read `(old_name)` before the `SET`, apply the existing `SET n += $p`, then — post-apply, and only if `new != old` — `self._emit_event("Renamed", id=id_val, old=old_name, new=new_name)`. One line per matched label, mirroring the delete lane's "probe before, emit after" shape (S2 step 6). Register `Renamed` JSONL-only (absent from `_GRAPH_EVENT_TYPES` and `CLAIM_EVENT_TYPES`).
2. **Add the `Renamed` fold** to `apply()` and the pass-1b loop: `MATCH (n:{label} {id:$id}) SET n.name=$new` with the label validated against the fixed set (the `_resolve_entity` defense precedent). The fold is id-keyed and idempotent; `$old` is carried for audit and for a mismatch check (if the live node's name is neither `$old` nor `$new`, record a non-folded entry — do not silently apply).
3. **Re-point `supersededBy` from the successor's name to its id, and DELETE the name-keyed fold fallback.** `_fold_object_superseded`'s `supersedes_by = str(ev.get("supersedes_by") or "")[:200]` (`:610-612`) becomes the successor's id; the fold writes it as `o.supersededBy = $sb`. The `:200` truncation disappears with it (`name[:200]` is a name-shaped concern; an id is 30 chars). `commit_ops.py`'s successor probe (`:412-419`) changes from `MATCH (o:Object {name:$sb})` to `MATCH (o:Object {id:$sb})`. Then **remove the name-keyed fallback at `entities.py:657-675`** (the `folded == 0 and matched == 0 and name` branch, incl. the final `MATCH (o:Object {name:$name})` at `:668`/`:673`) — under D2 it is the one remaining fold that reaches a node **by name** and can fold a dup-name carrier (blast-radius §C). An id-probe miss becomes a non-folded `missing-node` entry (S2 step 5a).
4. **Re-derive the supersession tests.** `tests/test_capture_session.py`'s supersession assertions and `tests/test_capture_session_supersession_e2e.py` pin the name-valued `supersededBy`; each is a #3589 (B) case — keep the scenario, re-derive the expectation to the id.
5. **Remove the `#3377` `xfail`** from `tests/fixtures/entity_identity_vectors.json`.
6. **Run the harness and the full lane.**

**Tests (S3)**

| Test | What it pins |
|---|---|
| `tests/test_entity_identity.py::test_rename_is_journaled` | exactly one `Renamed(id, old, new)` |
| `::test_rename_survives_rebuild` | the old name does not come back (#3377's exact symptom) |
| `::test_rename_to_same_name_emits_nothing` | no-op renames do not grow the journal |
| `::test_rename_of_missing_id_emits_nothing` | a rename that matched no node is not journaled |
| `::test_rename_onto_live_holder_is_refused` | D3 — `NameAlreadyHeld`, and the name is not create-poisoned |
| `::test_rename_onto_terminal_holder_is_allowed` | D3/D2 — a superseded/deleted holder does not block name reuse |
| `::test_superseded_by_holds_an_id` | D6 |
| `::test_supersession_missing_successor_id_is_a_non_fold` | the `entities.py:657-675` fallback is gone; a stale id records `missing-node` and folds neither carrier |
| `tests/test_capture_session_supersession_e2e.py` | the end-to-end supersession chain, re-derived |
| `tests/test_rebuild_live_invariant.py` | the `#3377` vector, un-`xfail`ed |

**Verification (S3)**

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_entity_identity.py tests/test_capture_session_supersession_e2e.py \
  tests/test_rebuild_live_invariant.py -v
uv run pytest tests/ -q
```

**Rollback (S3):** revert. The `Renamed` lines already in a journal become inert (a reverted `apply()` has no branch), which restores #3377's symptom — the pre-S3 state, not a worse one. The `supersededBy` shape reversion is a display-value change only.

---

## Slice 4 — Retire the derived id, and the contracts that advertise it

**Intent:** remove `_entity_name_id` and every dependency on it, and make every documented id contract describe the minted model.

> **Split (P2) — this slice carries ONE gated deliverable.** r1 bundled three: (i) delete `_entity_name_id`; (ii) close #3574's `name[:200]` split; (iii) land #3586's missing characterisation test. Only **(i)** is gated by D8's predicate — the plan itself says abandoning S4 leaves "dead code, not a broken state" — so (ii) and (iii) were held hostage by an irreversible precondition, and (iii) is useful on `main` **today**. **#3574 and #3586's missing test are filed as standalone micro-issues** (labels `complexity:micro`, team `epistemic-team`); each can land independently of this epic. Neither is an S4 acceptance criterion.

**Acceptance:** `grep -rn "_entity_name_id" tortoise/` returns only comments (ideally nothing); no docstring advertises `id == f(name)`; `commit_ops.py`'s legacy-id synthesis is replaced by an explicit refusal. **Not** S4 acceptance: #3574's truncation split, #3586's missing test (both split out).

**Precondition:** S2 and S3 merged (nothing derives an id any more). **This is the slice D8 says must not merge if the per-label legacy-id predicate (`obj-` Objects **or** `sub-` Subjects) matches any service graph on any live instance.** Both totals must be reported — an `obj-`-only run is not a pass (P1-A).

**Files:**
- Modify: `tortoise/sdk.py` — delete `_entity_name_id` (`:1351-1361`); update `_ENTITY_ID_RE`'s comment (`:990-996`) and `_is_entity_id`'s docstring (`:997-1007`); the ingest ref guard (`:6928-6938`)
- Modify: `tortoise/commit_ops.py` — delete the legacy-id synthesis (`:494-500`) and the `#2164` accepted-divergence comment it belongs to
- Modify: `docs/event-catalog.md` — add the `Renamed`/`Deleted` note as **non-rows** (matching `ObjectRegistered`/`SubjectAdded`, which have 0 rows), or extend the existing prose; do not create a row the registry does not carry
- Modify: `docs/plans/2026-09-11-2977-object-retraction.md` and `-scope.md` — a short "superseded by #3590" note (these docs describe a fold that was never merged and is now unnecessary)
- Test: `tests/test_ingest_validation.py`, `tests/test_entity_identity.py`

**Steps**

1. **Delete `_entity_name_id`** (`sdk.py:1351-1361`) and every importer. The `grep -rn _entity_name_id tortoise/ battery/ tests/` inventory is **43 hits: 8 in `tortoise/`, 1 in `battery/`, 34 in `tests/`** (the earlier draft's "12 production" was wrong). Of the 8 in `tortoise/`, **3 are comment/docstring/def** (`sdk.py:990` comment, `sdk.py:998` docstring, `sdk.py:1351` the definition) and **2 are the real call sites** (`sdk.py:17122/17128`); `commit_ops.py:495` is a comment, `:498` an import and `:500` a call (deleted in step 3). The 34 test hits were re-derived in S2/S3. `battery/runner/setup.py:87` references it only in a comment ("mirrors the SDK's `_entity_name_id` precedent") — update the comment.
2. **Update `_is_entity_id`'s docstring and the `#1516` comment** (`sdk.py:990-1007`) to describe two accepted shapes (ULID, and the legacy `prefix-hex26` *recogniser* which is retained for back-compat recognition even though nothing mints it) — or, if nothing can ever produce a `prefix-hex26` value, delete `_ENTITY_ID_RE` and its OR clause. **Decision: retain the recogniser, delete the derivation.** Reason: `Event`/`Point` ids from `github-issue-{repo}-{n}`-style writers and the existing `about*` tests use prefixed forms, and removing the recogniser is a separate, riskier change than this epic needs. State the retained recogniser as a deliberate carry-over in the docstring. If the implementer can prove no producer emits `[a-z]{2,3}-<hex26>`, delete it and file the follow-up; do not leave it undocumented.
3. **Delete the legacy-id synthesis in `commit_ops.py:494-500`.** After S2 there are no id-less Objects (every write path assigns an id), so the `legacy_no_id` branch is unreachable. Replace it with an explicit refusal (a non-folded entry) rather than a synthesis — so the "unreachable" claim is checked rather than assumed.
4. **Update the ingest ref guard's comment** (`sdk.py:6928-6938`) — the guard rejects a bundle-local `ref` shaped like a node id because `refs.get(x, x)` would shadow an existing node; with minted ids that reasoning is unchanged, so the guard stays and only the comment moves.
5. **Run the full lane plus the source-level greps.**

**Tests (S4)**

| Test | What it pins |
|---|---|
| `tests/test_entity_identity.py::test_no_entity_name_id_remains` | a source-level assertion: no `_entity_name_id` call site in `tortoise/` |
| `tests/test_entity_identity.py::test_id_shapes_recognised` | `_is_entity_id` accepts a `ulid()`, a Crockford ULID, and a `prefix-hex26`; rejects a plain name |
| `tests/test_ingest_validation.py` | the ref guard unchanged |
| `tests/test_entity_identity.py::test_no_docstring_advertises_deterministic_id_by_name` | a source-level assertion over `hosted_api.py`/`mcp_server.py` |

**Verification (S4)**

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/ -q
grep -rn "_entity_name_id" tortoise/ battery/    # expect: comments only, or nothing
grep -rn "deterministic id by name" tortoise/    # expect: no output
```

**Rollback (S4):** revert. S4 is subtraction + documentation; a revert restores dead code, not broken behaviour.

---

## Blast radius (every surface that keys on an entity id, enumerated)

### A. Write sites that `MERGE`/`MATCH` `Object`/`Subject` by **name** (the 7 `MERGE` sites across 3 files that S1 must convert, plus their paired edge anchors)

The `MERGE` sites are `entities.py:464/523/868/908/966`, `edges.py:79`, `hosted_api.py:9403` (**7 across 3 files**; the earlier draft's "11 across 4" was wrong). Rows 8–11 are the paired edge `MATCH`es / hosted `MATCH`es that must move in lockstep — a mismatched anchor pair is a silent no-op.

| # | Site | Current | S1 |
|---|---|---|---|
| 1 | `projection/entities.py:523` `_upsert_object` | `MERGE (o:Object {name:$name})` + `ON MATCH o.id=coalesce($id,o.id)` | `MERGE (o:Object {id:$id})`, no id/name on `ON MATCH` |
| 2 | `projection/entities.py:464` `_upsert_subject` | `MERGE (s:Subject {name:$name})` + `ON MATCH s.id=coalesce($id,s.id)` | `MERGE (s:Subject {id:$id})` |
| 3 | `projection/entities.py:868` `_event_plain_merge` subject stub | `MERGE (s:Subject {name:$name}) ON CREATE SET s.id=$id(random ulid)` | id-keyed |
| 4 | `projection/entities.py:908` `produces` Object stub | `MERGE (o:Object {name:$name}) ON CREATE SET o.id=$id(random ulid)` | id-keyed |
| 5 | `projection/entities.py:966` `uses` Object stub | `MERGE (o:Object {name:$name}) ON CREATE SET o.id=$id(random ulid)` | id-keyed |
| 6 | `projection/edges.py:79` `_mint_subject_stub` | `MERGE (s:Subject {name:$name}) ON CREATE SET s.id=$name` | `MERGE (s:Subject {id:$id})` |
| 7 | `projection/edges.py:140` `resolve_structural_target` Subject | `MATCH (s:Subject {name:$name})` | `{id:$id}` |
| 8 | `projection/entities.py:873` `performs` edge | `MATCH (s:Subject {name:$name})` | `{id:$id}` (pair with #3) |
| 9 | `projection/entities.py:913` / `:972` edge anchors | `MATCH (… {name:$name})` | `{id:$id}` (pair with #4/#5) |
| 10 | `hosted_api.py:9403` session-capture step-3b | `MERGE (o:Object {name:$name})` | resolve-or-mint by id |
| 11 | `hosted_api.py:9570` | `MATCH (p:Point {id:$pid}), (o:Object {name:$name})` | `{id:$oid}` |

### A.1 The id-writing channels — the R5 blast radius (P1-C)

Every channel that can **supply an id** to an `Object`/`Subject` write is enumerated here; R5 (no id reuse) is enforced at the `_upsert_*` choke point (S2 step 6a/6b) and, for the **three raw-write channels (5, 8, 9)**, via the shared `_reject_tombstoned_id` guard at each site; the guard-coverage test is parametrised over exactly this list, so the covered-channel count equals this count:

| # | Channel | Path to the graph | R5 guard site |
|---|---|---|---|
| 1 | `TortoiseSDK.create_entity("object"/"subject", …)` | `_create_entity` → `proj.apply` → `_upsert_*` | `_upsert_*` (choke point) + `_create_entity` fast-path probe |
| 2 | `create_object`/`create_subject` `_server_id` | `_create_entity` → `proj.apply` → `_upsert_*` | `_upsert_*` |
| 3 | `EventAPI.add_object(name, id=…)` | `_emit` → `projection.apply` → `_upsert_object` | `_upsert_object` |
| 4 | `EventAPI.add_subject(name, id=…)` (new in S2) | `_emit` → `projection.apply` → `_upsert_subject` | `_upsert_subject` |
| 5 | `_connect_issue_objects` (item-supplied connector id) | **raw `MERGE (o:Object {id:$oid})`** — does **not** pass through `_upsert_*` | shared `_reject_tombstoned_id` guard at its `MERGE` (one of three raw-write carve-outs — see rows 8, 9) |
| 6 | `github_map.issue_to_object` → `ObjectRegistered` dict | `apply` → `_upsert_object` | `_upsert_object` |
| 7 | `mining.add_object(id=…)` (no longer mints after P1-B) | `EventAPI` → `apply` → `_upsert_object` | `_upsert_object` |
| 8 | `hosted_api.py:9403` session-capture step-3b `aboutObject` | **raw `MERGE (o:Object {id:$oid})`** after S1's resolve-or-mint — does **not** pass through `_upsert_*` | shared `_reject_tombstoned_id` guard at its `MERGE` (guard-needed — raw write) |
| 9 | `onboarding.state.write_onboards_edge` (`onboarding/state.py:401`) | **raw `MERGE (s:Subject {id:$sid})`** with a caller-supplied id — bypasses both `_upsert_*` and `apply` | shared `_reject_tombstoned_id` guard at its `MERGE` (guard-needed — raw write); alternatively route through `_upsert_subject` |

**Count = 9.** Any new id-writing channel added without a guard (or any guard that stops covering one) fails `test_id_reuse_is_refused_on_every_id_writing_channel` by the count check — the guard cannot silently stop covering the surface it claims. **The two raw-write rows (8, 9) were missing (P2):** `hosted_api.py:9403` supplies an id from outside `_upsert_*`, and `onboarding/state.py:401` is a caller-supplied `MERGE (s:Subject {id:$sid})` that passes through neither `_upsert_*` **nor** `apply` — without them the count-equality test would pass while two raw write sites sat outside it.

### B. Read sites that resolve by **name** (disposition per site — routed, refused, or explicitly descoped)

| Site | Today | Disposition |
|---|---|---|
| `projection/entities.py:936`/`:942` (lifecycle status folds) | `MATCH … {name:$name}` | **Route** through `_resolve_name` (S1 step 6) |
| `projection/entities.py:992` (participants fallback) | `MATCH … {name:$name}` | **Route** through `_resolve_name` |
| `projection/edges.py:271` `_create_about_edges` (Subject→Object→Event→Document→Point auto-detect) | name auto-detect | **Route**; zero live holders → **refuse** (non-folded `unregistered-reference`, S2 step 5) |
| `projection/edges.py:310` `_try_about_edge` | name auto-detect | **Route** |
| `projection/edges.py:160-161` `resolve_structural_target` non-stubbable labels | `MATCH (x:{label} {name:$key})` | **Route** |
| `projection/entities.py:668`/`:673` `_fold_object_superseded` name fallback | name-keyed fold reach | **Delete in S3** once `supersededBy` is an id (§C) — under D2 it is the one fold that can still reach a node *by name* |
| `assembly.py:477` / `:1070` | name read — `:477` is a **list union** (`o.name IN $names OR o.id IN $names`) | **Route then refuse** at `:477` (see §B.1); `:1070` **Route** |
| `commit_ops.py:217` / `:233` / `:435` | name read — `:233` and `:435` are **list unions** (`o.id IN $ids OR o.name IN $names`) | **Route then refuse** at `:233`/`:435` (see §B.1); `:217` **Route** |
| `mining.py:576/588/592` | `add_object(…, id=self._object_id(name))` — a name-derived `obj_<hash16>` | **Resolver caller (P1-B, S1 key-parity / S2 mint-flip)** — the derivation is deleted; see D4/§A.1 |
| `onboarding/seed.py:148` | name read | **Route** |
| `sdk.py:7846`/`:7853` (ingest) | name lookup for `existed` | **Route** (D4: no `or name` fallback) |
| `sdk.py:4123` (capture aboutObject) | name read | **Route** |
| `sdk.py:17006-17022` `_create_entity` canonical-id re-fetch | **`MATCH (n:{label} {name:$name}) RETURN n.id` — a name-keyed coordinate with NO status filter, returning `result_set[0][0]` (an ARBITRARY row)** | **Delete in S1 (P1-A — the ninth-cycle seed).** Not a disjunction and not a `MERGE`, and its **f-string label** hides it from every literal-grep sweep, so it is listed here so it cannot hide again. Under the id-keyed `MERGE` the fresh id always lands, so `canonical_id = id_val` unconditionally and `_get_entity(id_val)` is the only correct return. If it were ever kept it MUST route through `_resolve_name` and must never return a terminal holder — under D2 a delete-then-recreate tombstone+live pair makes `result_set[0]` a coin flip that can return the **retracted** node and wire `authoredBy`/`ownedBy`/`managedBy` onto it. |
| `commit_ops.py:413` (supersession successor) | name probe | **Becomes an id probe in S3** (D6) |

**B.1 — every `id OR name` DISJUNCTION is a sharp site (P2).** These are not simple name lookups: a disjunction returns **multiple rows** and silently merges identities, so an id matching one node and a name matching another produce a union — no refusal, no error. The r1 revision covered **three** sites and keyed the sweep to `grep "OR .*\.name = \$"`; that is under-counted in two ways: (i) `sdk.py:8722` (the *same* `file_human_approval` function as `:8711`) is a second union, and (ii) the **list-parameter** forms `... OR o.name IN $names` are invisible to the `= $` sweep. The sharpest miss is neither operator form (P1-A): `_create_entity`'s canonical-id re-fetch (`sdk.py:17006-17022`) is a plain `MATCH (n:{label} {name:$name})` with an **f-string label**, so a literal grep for `{name:` and both operator sweeps skip it — it is dispositioned **Delete in S1** in the §B table. S1's source-level test is therefore extended from `MERGE`-only to cover name-keyed **reads** too, so no f-string label can hide a surviving name-keyed coordinate. Verified against the worktree, the complete set is:

| Site | Today's defect | Disposition |
|---|---|---|
| `sdk.py:8711` `file_human_approval` (approver Subject) | `s.id = $id OR s.name = $id`; `if not r:` passes on 2 matches (approves against an arbitrary/first match) | **Route then refuse**: resolve to exactly one live id, then the existing `if not r` guard; ≥2 → `AmbiguousEntityName` |
| `sdk.py:8722` `file_human_approval` (artifact) | `(n.id = $id OR n.name = $id)` over `:Object`/`:Document` — the same union, one statement later | **Route then refuse** (same rule) |
| `sdk.py:20079` `get_owned_entities` | `s.id = $sid OR s.name = $sid` — unions both same-name Subjects' owned entities | **Route then refuse** — never union two carriers |
| `sdk.py:20120` `get_org_structure` (members) | `s.id = $sid OR s.name = $sid` | **Route then refuse** |
| `sdk.py:20125` `get_org_structure` (roles) | `p.id = $sid OR p.name = $sid` | **Route then refuse** |
| `commit_ops.py:233` | `o.id IN $ids OR o.name IN $names` | **Route then refuse**, per ref (list union) |
| `commit_ops.py:435` (supersession successor probe, S3) | `o.id IN $ids OR o.name IN $names` | **Route then refuse**, per ref (list union); S3 makes it an id probe (D6) |
| `assembly.py:477` | `o.name IN $names OR o.id IN $names` | **Route then refuse**, per ref (list union) |

**Descoped, and a filed issue is required before S4 merges** (not silently left): any remaining `id`/`name` disjunction that is not in the table above. The sweep must see both operators — `grep -rnE "OR .*\.name ( = | IN ) \\$" tortoise/` **misses the `IN $names` forms**, so use an operator-agnostic sweep: `grep -rnE "\\b(id|name)\\b[^)]*\\bOR\\b[^)]*\\b(id|name)\\b" tortoise/`. S1's source-level test `test_no_name_keyed_object_merge_remains` is extended to assert the surviving name-keyed read set is exactly the table's routed set, and the count assertion (6b) includes these sites so an uncovered union is a failure, not a silent gap.

### C. `supersededBy` / `corrected_by`

- `supersededBy`: `projection/entities.py:610-612` and `:626-628` (fold), `commit_ops.py:412-419` (successor probe), `commit_ops.py:474-500` (legacy-id synthesis → **deleted, S4**), `commit_ops.py:617` (emit). **S3 re-points to the id.**
- **The name-keyed fold fallback (`projection/entities.py:657-675`, incl. the final `MATCH (o:Object {name:$name})` at `:668`/`:673`).** This is a *fold* that reaches a node **by name** — the class the plan claims to eliminate — and it was missing from the earlier blast radius. Today it fires only when the id branch matched nothing (`folded == 0 and matched == 0 and name`), for the legacy id-less-Object case. Under D2 (same-name coexistence via explicit-id writers) it becomes genuinely reachable and can fold a **dup-name carrier** or re-claim a terminal target under a different spelling. **Disposition: S2 records it as a non-folded `missing-node` entry instead of falling back once the synthesis is gone (S4 deletes `commit_ops.py:494-500`), and S3 removes the name fallback entirely** (an id probe that misses is a non-fold; P1-5). **Test:** two same-name carriers + one `ObjectSuperseded` naming a **stale/absent** id → the fold records a non-fold, folds neither carrier.
- `corrected_by` / invalidation: `sdk.py:4654-4740` `invalidate_point`, `mcp_server.py:1670-1676` `tortoise_invalidate(id, corrected_by_id)` — caller-supplied **strings** MATCHed directly through `_resolve_entity` (`projection/__init__.py:2452-2513`). The union is shape-agnostic (`_RESOLVE_BRANCHES`, `:2446`), so it does not constrain the format and needs no change; it inherits the id contract for free.
- **#3574 split out (P2).** The `name[:200]` split — `_connect_issue_objects`'s `name[:200]` (`sdk.py:19463`) vs `_upsert_object`'s full-name `MERGE` — is a **standalone micro-issue**, not an S4 deliverable. With the id as the key the truncation is no longer an identity concern, so the fix is one rule (cap at the HTTP/`CreateObjectRequest` boundary, `hosted_api.py:4514`, `max_length=200`; no truncation in the graph write path), but it is **not** gated by D8 and must not block S4.

### D. `about*` edges

`sdk.py:17164-17180` (the four guards) → `projection/edges.py:344-376` `create_about_edge` → `_resolve_entity` (`projection/__init__.py:2452-2513`, indexed per-label lookups on `id`/`eventId`/`url`). The fallback is `edges.py:271-305` `_create_about_edges` → `edges.py:74-82` `_mint_subject_stub`. `_is_entity_id` needs no change (D1/D7). The **missing** non-matching-id test is #3586; split out as a standalone micro-issue (P2).

### E. The `_upsert_object` id coalesce

`projection/entities.py:527` — `ON MATCH SET o.id=coalesce($id, o.id)`, plus the accepted trade-off documented at `:509-522` and mirrored on the Subject side at `:455-462`. **S1 removes the term** — see S1 step 2: with the pattern `{id:$id}` a matched node already has `o.id == $id`, so the clause is a **provable no-op**; it is removed as dead code whose stub-adoption purpose the S1 resolver now serves. That closes the `#1155`/`#3389` late-random-ulid re-id class structurally.

### F. The `_entity_name_id` cross-name probe

`sdk.py:16855-16906` — both probes match `{id:$cid, name:$name}`; the `name` conjunct exists **only** to harden against a cross-name sha-digest collision (`:16847-16852`). With a random id that correlation is gone. **S2 deletes the name conjunct** (keeping the probe and the fail-open-to-journaling behaviour).

### G. MCP / hosted id contract

`mcp_server.py:2379-2392` (`tortoise_get_entity`/`tortoise_update_entity`/`tortoise_delete_entity` — ids, "whatever string resolves") · `mcp_server.py:2218-2235` (`tortoise_create_object`/`tortoise_create_subject` — a **name**) · `hosted_api.py:4526-4530` `/v1/objects` and `:4562-4566` `/v1/subjects` — the docstring *"deterministic id by name, idempotent"* and the idempotency proof it advertises. **S2 updates the docstrings; D2 preserves the idempotency.**

### H. Ingest ref validation

`sdk.py:6928-6938` — rejects a bundle-local `ref` shaped like a real node id (`_is_entity_id`). Must stay in sync with the minted format. S4 updates the comment; the guard is unchanged (D1: a ULID is already accepted).

---

## Hard unknowns and what I could not determine

| # | Item | Status |
|---|---|---|
| U1 | **What a replay of a pre-change event means once the resolver only understands new ids** (the decision doc's explicit gap) | **Moot under a fresh start — and falsifiably so.** D8 records the three checks. The first is now a **per-label predicate over every graph on every instance over ALL FOUR legacy id shapes** (`obj-`, `sub-`, `obj_`, `id == name` — an `obj-`-only or two-prefix run is not a pass), **not** a node count — because the count-based guard drifted and `=~` fails silently. **Re-run those checks before S4 merges**; if the predicate matches any service graph, S4 must not merge and a dual-read resolver is required first. |
| U2 | **#3586's exact current behaviour for a non-matching, id-shaped `about*` value** — stub-minted, or a silent no-op? | **Could not determine by reading.** The decision doc's Stage-3 survey calls it a deduction, and it is: `_is_entity_id(value)` is `True`, so the `not _is_entity_id` guard at `sdk.py:17167` is `False` and `_create_about_edges` is skipped, and `create_about_edge` → `_resolve_entity` matches nothing → returns `False`. That reads as a **silent no-op**. The survey's alternate deduction (stub minted via `_mint_subject_stub`) would require the guard to be `False`, which it is not. **The plan therefore requires the split-out micro-issue's test to observe the behaviour by running it and pin what it observes** — no implementation may be written from either deduction. |
| U3 | **Whether every `slack`/`linear` test tolerates the D9 producer change** (registering the entity before the event, instead of relying on a projection stub) | **Not determined.** I confirmed those files emit `EventRecorded` with bare-name `object`/`subject` and no registration (`connectors/slack.py:267`, `connectors/linear.py:190`/`:217`), but I did not run their suites. **S2 must run them before changing the producers**, and if a test asserts the stub shape, it is a #3589 (B) case — keep the scenario, re-derive the expectation, and record the verdict. **The `sdk.py:11507-11515` lane joins this discipline (P2):** it calls `proj.apply` **directly** (`subject=agent_name`), so S2 must run its suite before routing the `EventRecorded` through `_emit_event`; if a caller or test depends on the live-only behaviour, it is recorded as an **accepted live-only divergence with an owner issue**, not silently changed. |
| U4 | **Whether any producer emits a `[a-z]{2,3}-<26hex>` id that is not `_entity_name_id`** (i.e. whether `_ENTITY_ID_RE` can be deleted rather than retained as a recogniser) | **Not fully enumerated.** Known id-producing writers after P1-B: `_connect_issue_objects` (`sdk.py:19461`, item/connector ids only — its no-id fallback becomes a resolver caller), `tortoise/github_map.py` (`ObjectRegistered` with a connector id), `EventAPI.add_object(id=…)` (`api.py:266`), `_server_id` (D4). `mining.py:592` no longer produces one (P1-B, S2). None is `[a-z]{2,3}-<26hex>`. S4's step 2 retains the recogniser and states why; a proof that nothing emits it would let a follow-up delete it. |
| U5 | **The exact `status` value a `Deleted` node should carry, and whether recall/search exclusions must learn it** | **Determined (the earlier draft named a symbol that does not exist).** `OBJECT_SEARCH_EXCLUDED_STATUS` does **not** exist (`grep -rn OBJECT_SEARCH_EXCLUDED tortoise/` → 0 hits). The real symbols are `_RECALL_OBJECT_EXCLUDED_STATUS` (`commit_ops.py:34` = `{superseded, deprecated, archived, retracted}`; read at `commit_ops.py:550`, `:602`), `_RECALL_OBJECT_EXCLUDED_STATUSES` (`assembly.py:1017` = the same set **plus `outdated`**; read at `assembly.py:1083`), and live `TERMINAL_EXCLUDED_STATUSES` (`live.py:33` = `{retracted, superseded, outdated, archived, deprecated}`, re-exported through `search_engine.py:31` and consumed across `search_engine`/`ep`/`fallback_snapshot`). `'retracted'` is in **all three**, so reusing it requires **no** downstream exclusion change — that is the plan's choice (S2 step 7, and the D2 "live holder" predicate). The alternative (a hard `DETACH DELETE` on replay) is rejected in D5. The implementer still confirms the exclusion tuples cover the label set actually used. |
| U6 | **Whether `rebuild_all`'s Point-only snapshot (`projection/__init__.py:1428`) should be extended to Objects/Subjects** | **Not in scope, but named.** It is not needed once Objects/Subjects are fully journaled (S2), and the S0 harness is precisely the test that would catch it if it were. If the harness cannot reach `rebuild == live` for a shape because of the snapshot gap, that is a **finding to file**, not a silent scope expansion. |
| U7 | **The cross-process same-name mint race** | **Structural residual, NOT closable in this plan (P2).** `_name_holder_locks` is a module-level `dict[str, threading.Lock]` — exactly like the `_source_merge_locks` it mirrors (`sdk.py:1448`) — so it serialises only **within one process**. Two processes (or two SDK instances in different processes) can each see zero live holders for a name and each mint, producing **two live carriers** and **permanently create-poisoning** the name (every later `create_*`/bare-name reference refuses, and there is **no by-name repair path** — `_resolve_entity` has no name branch). D2 forbids a unique name index because same-name coexistence is legal, so there is no structural backstop. **Accepted pre-beta** (0 customers, no production Object/Subject); the optional post-mint re-check ("Open for the implementer") narrows but does not close the window. Named here, in D2, and in S2 step 1. |

---

## Open for the implementer

| Item | Recommendation |
|---|---|
| `Deleted` as a tombstone (`status='retracted'`) vs a hard delete | tombstone — the exclusions already exist (U5); a hard delete makes replay order-sensitive |
| `_ENTITY_ID_RE` retained or deleted | retain as a **recogniser**, delete the derivation; add the proof-of-unused as a follow-up issue rather than folding an enumerable risk into this epic |
| `Renamed` label validation | validate against the fixed label set at the fold (the `_resolve_entity` defense-in-depth precedent, `projection/__init__.py:2507-2512`) — never interpolate the journal's label |
| The `AmbiguousEntityName` exception's public shape | structured fields (`label`, `name`, `candidate_ids`) so the MCP/HTTP boundary can map it to a 409 without string-matching |
| A by-name `id-preview` endpoint | not needed — D2 keeps `create_*` idempotent by name, so a client does not need to predict an id |
| `docs/00_index.md` registration for the new fixture | check whether that index enumerates `tests/fixtures/`; if it does, register `entity_identity_vectors.json`; if it does not, do not invent an entry |
| `#3574` (`name[:200]` split) and `#3586`'s missing test | **filed as standalone micro-issues** (split out of S4, P2) — neither is gated by D8's predicate and neither blocks this epic |
| Cross-process same-name mint (U7) | optional **post-mint re-check**: after minting, re-read the live holders and raise `AmbiguousEntityName` if a carrier appeared during the mint window. Narrows the race; does **not** close it (no unique name index, by design). Acceptable pre-beta without it |
| Journal growth from emit-on-every-call (S2 step 3) | keep the choice; optionally bound it with an **in-process set of journaled ids** so a repeated `create_entity` within one process does not re-append. Record the measured line-count delta when S2 lands |

**Product decisions to confirm before S2 ships** (do not block S0/S1):
1. Same-name coexistence is allowed (D2), reachable **only via explicit-id writers** — a rename onto a live holder's name is **refused** (D3). Plain `create_*(name)` stays idempotent. This is the issue's stated behaviour change, narrowed to remove the create-poisoning failure mode.
2. Deletion becomes a **terminal** event and re-creation mints a fresh id (R5) — a re-creation is therefore a *different* entity, not a resurrection. Any caller that today expects "delete then create returns the same id" is **not** preserved, deliberately. The deleted node remains in the graph as a `status='retracted'` tombstone (P1-1), invisible to recall/search.
3. `supersededBy` changes from a successor **name** to a successor **id** (D6) — a display-value change for any consumer rendering it.

---

## Rollback (epic level)

Each slice is a separate commit and reverts independently (per-slice rollback notes above). No slice carries a data migration, so **there is no data rollback in any slice** — the only durable artifacts are JSONL journal lines, and every line type this plan introduces (`Deleted`, `Renamed`) is inert under a reverted binary (no branch), which restores the pre-slice behaviour rather than a worse one. `doc_status` for this plan is `draft`; it becomes `live` only when `plan-review` returns `status=clean`.
