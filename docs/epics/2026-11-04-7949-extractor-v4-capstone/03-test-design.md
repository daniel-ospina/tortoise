---
title: "Epic #7949 — Integration Surface Map and CI tier design"
type: engineering
domain: platform
doc_status: live
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: test design, CI cost, evals, capstone evidence
issue: daniel-ospina/tortoise#7949
stage: test-design
---

# Test Design — #7949 extractor v4 CAPSTONE

**Input:** `02-scope.md` (in-scope items 1–10; high-level E2E-1…E2E-8). **Produced by:** `test-design` (read in
full). **Filed as:** a child issue, per the Test-Design Gate.
**Scope of this artifact:** *where each test runs*, not just which layer it is — regular CI must stay fast, and
milestone evals are allowed to be slow.

---

## 0 · The CI-cost constraint, measured first (not assumed)

Every claim below comes from `config/ci-surfaces.yml`'s committed `durations:` map (a shard-layout input,
refreshed by `tools/ci_timing.py --refresh-durations` from junit sweeps) — not from a local reading, which the
map explicitly forbids as a layout input.

| fact | value |
|---|---|
| whole suite, measured weight | **12,734.8 s over 1,208 files** |
| capstone-adjacent files | **16 files, 64.3 s total — 0.5 % of the suite** |
| heaviest three | `test_v4_declared_mechanisms.py` **17.8 s** · `test_graph_census_7949.py` **11.1 s** · `test_7949_capstone_verdict_check.py` **11.1 s** |
| the eval lane already exists and is dispatch-only | `eval-suite-manual.yml` and `evals-on-demand.yml` — **both `workflow_dispatch` ONLY**, no `push`, no `pull_request`, no `schedule`; `on_demand:` in `ci-surfaces.yml` excludes `eval/**` + `longmem_eval/**` from merge gating (#10194, owner decision A 2026-10-27) |
| capstone files in `carve_out:` / `uri_requiring:` / `slow_files:` | **none** — they are classified DB-free, URI-free and fast |

⇒ **Two conclusions up front, and they change the design:**

1. **The capstone is not a CI-cost problem today** — 0.5 % of the suite. So the discipline below is about
   **not creating one**, not about shrinking an existing one.
2. **The right home for the expensive, non-deterministic work already exists**: the dispatch-only eval lane.
   Adding the A-row measurements there costs merge-time CI **nothing**, and matches the owner's own ruling
   that evals do not gate a merge.

---

## 1 · Integration surfaces (skill Step 1)

| Surface | Type | Data flow | The contract |
|---|---|---|---|
| **the pinned registration** (`preregistration.json` + checker) | State / config | Read + Write (amended) | `capstone-preregistration/v1`; `criteria[].id`, `denominator_floors`, `below_floor`, `corpus`, `amendments`; the A-rows enter as a **versioned amendment** quoting superseded text |
| **the verdict gate enum** (`capstone_verdict_check.py:135`) | State (closed vocabulary) | Read | `capstone-verdict-v1`; a **closed** `CRITERIA` tuple that refuses any id outside it (`:821`) — two schemas move, not one |
| **the corpus pin** (fixture + A-corpus) | State | Read + Write | **three shapes already exist** (`fixtures_hash` in the record; `corpus.sha256`/`items` demanded by `capstone_run.py:1177`; a derived `e1e2_corpus.json`) — the A-corpus must **unify the contract**, not add a fourth |
| **the A-corpus capture** (`POST /v1/sessions`) | External service | Out → graph | request = curated payloads; response = receipt; **402 quota** at `hosted_api.py:18205` (`if count + est > max_points`) / `:18216` (`quota_refusal_payload`); harness resolution `#3681` |
| **the graph the A-rows read** (hosted `live-v4` / per-run graph) | DB | Read | `:Object`/`:Event`/`:Point`/`:Subject` shapes; `hasPart` Object→Object (ONTOLOGY §3.7); `batch_id` on `RelationUpsert.attrs` |
| **the pack declaration → write path** (`semantics`/`hierarchies`) | State | Read | `relation_write_verdict` returns `writable`; **unread legs**: `semantics` (surfaced at `pack_registry.py:456-459`), `hierarchies` (validated only, `:1824-1838`) |
| **the declared-mechanism registry** (`config/v4-mechanisms.yml`) | Config | Read | 13 rows; `state`/`code:`/`tests:`/`symbols:`/`pins:`/`preconditions:`; guard `tests/test_v4_declared_mechanisms.py` |
| **the sealed gold** (`a-corpus/gold/`, hash-pinned) | State | Read | two label sets (direct, transitive); labeller identity recorded; sealed **before** the rollup exists |
| **the operator legal ≠** (unpatched `extract_session_v2`) | External (LLM) | Out | 15 planted edges; endpoint refs remapped; the only current test **monkeypatches** the extractor |
| **the customer readers** (search `subject`, `get_org_structure`) | API | Out | non-empty answer vs structurally-empty |
| **quota/headroom** (`#10976`) | External constraint | Read | 25,000-point cap; capture refused with 402 above it |

---

## 2 · Test layer **and run tier** per surface (skill Step 2 + the CI discipline)

The skill assigns a *layer*. This epic adds a second axis — **where it runs** — because a correct layer in the
wrong tier is how CI becomes unusable.

**Tier rule applied:** *merge-gating CI may only contain tests that are (a) offline, (b) deterministic, and
(c) under ~1 s of measured weight each.* Anything needing a graph, a model call, a quota, or a non-deterministic
composer goes to **Tier E** (the dispatch-only eval lane).

**18 rows below — numbered `1…17` plus `10a`.** `10a` is **E6**, added by **`#11083`** after the table was
numbered; it keeps its own label because that is the label `#11454` and the children cite for it. So the map is
**18 surfaces**, not 17 — the figure the earlier summary and the siblings carried.

| # | Surface | Test layer | **Runs where** | Why that tier | Key failure modes to test |
|---|---|---|---|---|---|
| 1 | A-row criteria in the registration | **contract** (unit, file-scan) | **CI** | pure JSON/markdown; the amendment is the thing that rots | a criterion id the verdict enum refuses; a threshold re-derived after a run; superseded text overwritten |
| 2 | verdict gate engine (`check()`/`load_blocker_states()`) | **unit** | **CI** | pure function over committed records (already 11.1 s; see §4) | a row whose `blocked_by` has CLOSED; a malformed record silently disarming V12 |
| 3 | corpus pin map (per-row `corpus_id`) | **contract** | **CI** | the pin is a file; the refusal is a hash compare | a row scored against another row's corpus; a pin that names no derivation |
| 4 | A-corpus capture through `POST /v1/sessions` | **integration (real door)** | **Tier E** | spends a point budget, needs the hosted stack, and is the claim itself | 402 quota; harness misattribution; the posted set ≠ the pin |
| 5 | **A1** edges + rollup vs sealed gold | **integration + eval** | **Tier E** | needs a graph **and** a model-proposed composer (D6) → not run-invariant | precision below bar; a transitive answer with an inherited part but no declared intermediates; a cycle inflating the answer |
| 6 | **A2** attach/mint/held | **integration** | **Tier E** | needs the graph + a real corpus | over-attachment (false merge, far harder to unpick); `held` collapsing into `minted` |
| 7 | **A3** chain-join + both S5 jobs | **integration** | **Tier E** | same | job 1 reported as job 2; the cheap metric up while the component arm flat |
| 8 | **A4** per-layer label coverage — the declared kinds present, and each thing/event carrying the label the ontology declares for it (the per-writer split and the typed/`statement`/`NULL` shares are **diagnostics**, not the criterion) | **integration (read-only)** | **Tier E** | it is a census over a live graph, reported **per layer** | **a declared kind absent** from the graph, or **a thing/event left unlabelled** — the failing run D-S4 defines. A typed share quoted without its `NULL` denominator is the *diagnostic*'s own error, not A4's verdict |
| 9 | **A5** Subjects + customer readers | **integration + e2e** | **Tier E** | needs a real capture to have any subjects | a fail-open probe reading as "no subjects" |
| 10 | **E4** operator edges, unpatched leg | **integration** | **Tier E** | pays 7-session LLM cost; the current test patches the extractor so it cannot certify | a patched path reported as the product |
| 10a | **E6** per-pack `declared → writable → written` census (`#11083`) | contract + census | **CI** for *declared/writable*; **Tier E** for *written* | *declared* and *writable* are file/graph facts; *written* needs a real capture — split the row where the split actually is | a delta that is named but not measured; a `writable` column silently empty because the vocabulary is static |
| 11 | **E5** raw recovery arm | **eval** | **Tier E** | non-deterministic composer; the arm is a rate | ratio passing while the absolute floor fails |
| 12 | pack declaration → write-path derivation (`semantics` consumer) | **contract** (unit) | **CI** | pure dict→predicate derivation; no graph | a second pack's predicate needing a code change; hard-coded predicate |
| 13 | `create_edge` anchor boundary (`#5213` option 3) | **unit** | **CI** | already pinned, 269 lines | an implementer "fixing" the documented boundary |
| 14 | declared-mechanism registry row | **config/contract** | **CI** | file + schema; already 17.8 s (see §4) | a row in a state its `code:`/`pins:` do not support |
| 15 | sealed gold integrity | **contract** | **CI** | hash + identity in a file; no graph | gold edited after a run; a hash that proves *when* but not *who* |
| 16 | **the rollup's ground-truth scoring** | **eval (milestone)** | **Tier E** | it IS the measurement | a gold fitted to the query by the same lane |
| 17 | performance / cost of the pipeline | **eval (milestone)** | **Tier E** | by definition not a correctness signal (precedent: `test_perf_lane_volunteer_context.py` is already `on_demand`) | a p95 on a shared runner read as a regression |

**Layer summary:** of the **18** rows, **7** are CI-only (1, 2, 3, 12, 13, 14, 15 — contract/unit, offline), **10**
are Tier E (4–11, 16, 17 — integration/eval against a real graph or model), and **1** is split (10a: `declared` and
`writable` are file/graph facts and run in CI; `written` needs a real capture and runs in Tier E). **No surface is
assigned "none"**, and **no Tier-E surface may be promoted into merge gating** to "make it real" — that is the trade
the owner already ruled on (#10194).

---

## 3 · Bug-pattern flags (skill Step 4)

| flag | where | required verification |
|---|---|---|
| **Silent function skips** | `subject_binding_available` is a **fail-OPEN** probe (`search_engine.py:4552`, consumed `sdk.py:8766/8819`); a fail-open probe makes "no subjects" indistinguishable from "never ran" | A5's case must report *which* state occurred — **Tier E**, asserted on the run record, not on a mock |
| **Conditional guards** | `_capture_lane()` returns `"v2"` unless `TORTOISE_SESSION_EXTRACTOR=m2`; the whole door claim hangs on a conditional | a CI-level pin on the selector itself (cheap, file-level) + Tier E evidence that the named lane ran |
| **Stale measurement artifact** | the shipped verdict table carries `E4 2/15` while `E1-E5-PROOFS.md` corrects to `5/15` | CI pin that **both** figures are named and the stale one is not used as a bar |
| **N+1 / cost blowup** | `capstone_measure.py` re-derives over 397 nodes; the E4 leg pays 7-session LLM cost | Tier E only; a CI pin on the *declared* point budget would be theatre |
| **Race / non-determinism** | D6 makes both connection sets model-proposed; the repo measured gold swinging `0,1,1,1,2 of 4` on identical code (`PRE-REGISTRATION.md` §1.4) | E2E-5's repeat policy: deterministic quantities identical, model-proposed arms reported with their disagreement |
| **Quota as a hidden gate** | capture returns **402** at 25,000 points (`hosted_api.py:18205` — `if count + est > max_points`) | an explicit `BLOCKED — capture refused 402` reporting state, so a capped run can never read as a zero or a pass |

**No pgTAP flag applies** — this epic touches no Postgres function; the state is the graph and the committed
record, and the skill's pgTAP rule is scoped to SQL business logic.

---

## 4 · Existing-test audit — what to change, refactor, or leave alone

The user's constraint includes this: don't add bloat, and fix what already costs. Each row names the failure the
check prevents (a check with no failure behind it is a removal candidate; a behaviour with no check is a
candidate for one).

| file | measured weight | verdict | why |
|---|---|---|---|
| **`test_graph_census_7949.py`** | **11.1 s** (19 tests; **15 drive a real `capture_session`** on the embedded lane) | ⛔ **REFACTOR — highest-value change** | An **integration test sitting in a fast shard**. Keep 3–4 offline pins in CI (the census arithmetic and the `:Object`-has-no-derivation-link fact) and move the 15 live-capture cases to **Tier E**. Recovers ~8–9 s of merge-time CI and stops the fast shard paying for a live graph. |
| **`test_v4_declared_mechanisms.py`** | **17.8 s** (133 defs) — heaviest of the 16 | 🔧 **REFACTOR — investigate imports first** | 133 cases over a YAML file should not cost 17.8 s. Likely per-test pack-registry reload / source re-parse. A session-scoped fixture (load packs + parse sources once) is the fix; **measure before and after** and take the weight from a junit sweep, never a local reading. |
| **`test_7949_capstone_verdict_check.py`** | **11.1 s** (161 defs) | 🔧 **REFACTOR — share the throwaway repo** | Its own note says the subprocess-bound ancestry arms dominate and that a no-promisor throwaway repo took the file from 520 s → 6 s once. The remaining cost is one repo build **per test**; a session-scoped repo fixture should cut it. |
| **`test_capstone_preregistration_9075.py`** | **9.1 s** | ✅ **KEEP** | It re-derives `fixtures_hash` over 7 sessions of fixtures + gold. That is the check that makes the whole record trustworthy; 9.1 s for the epic's foundation is correctly priced. |
| **`test_7949_capstone_measure.py`** | **0.2 s** though **6,904 lines / 159 cases** | ⛔ **DO NOT REFACTOR FOR COST** | It is already the cheapest file in the family. Its length is *prose pins* (README/§10 sentences), and those pins have caught real re-staling. Splitting it for size would move ~0.2 s and risk losing a pin — the wrong trade. ⚠️ Its `durations` row history is contradictory (an earlier duplicate-key episode left `0.2` beside a note claiming `11.1` as an "upper bound"); **the pin that forbids a duplicate `durations` key is the thing to trust, and it is currently satisfied** (verified: zero duplicate keys). |
| **`test_9889_capstone_probes.py`** (2.0 s) · **`test_7950_capstone_probe.py`** (3.1 s) | 5.1 s | 🔍 **AUDIT for overlap, do not delete blind** | Three "capstone probe" files exist for different bundles/epics (9889, 7950, 7949). Before adding the A-row pins, run an **overlap audit**: if two files pin the same sentence of the same bundle, unify; if they pin different bundles, leave them. Deleting a check that catches a real failure is worse than 2 s. |
| `test_extractor_v4_citation_rot.py` (3.2 s) · `…_s4_stage_claims.py` (3.0 s) · the five 0.1 s doc guards | 6.6 s | ✅ **KEEP** | Citation-rot and doc-claim guards are the cheapest defence in the repo against the failure this epic keeps producing (a claim true in a summary, stale in its copies). |

**Aggregate proposal:** ~**20 s** of merge-time CI recoverable from two refactors (`graph_census_7949` live half →
Tier E; one shared fixture in the verdict/pack tests), with **zero checks deleted**. The capstone would then cost
~44 s of 12,734.8 s (0.35 %).

**What NOT to do, stated so it is refused in advance:** do not move the **contract/unit** pins to the eval lane to
"keep CI clean" — they are the cheap early-warning system, and a schema drift found at a milestone instead of on
a PR is exactly the drift-cost the repo's own rules exist to avoid.

---

## 5 · The three tiers, written as a rule the lanes can follow

| tier | what belongs | gate | budget |
|---|---|---|---|
| **CI (merge-gating)** | offline, deterministic, file/record-scanned: registration, verdict engine, corpus-pin map, declaration→predicate derivation, anchor boundary, registry guard, gold integrity, doc/citation guards | **blocks a merge** | *p95 per file < ~1 s target; no graph, no model, no quota* |
| **Tier E — on-demand eval lane** (`evals-on-demand.yml` / `eval-suite-manual.yml`, `workflow_dispatch` only) | every A-row measurement, E1–E5 re-derivation, E4's unpatched leg, E5's raw arm, the rollup-vs-gold scoring | **never gates a merge** (#10194) | unbounded by CI; bounded by the point budget and `#10976`'s cap |
| **Milestone eval** (roadmap completion) | performance and cost of the pipeline; the full A-row sweep re-run as a release gate | **gates a milestone, not a PR** | run when a roadmap milestone completes |

**Rule for every new test this epic adds:** it lands in **CI** only if it is offline + deterministic + sub-second.
Otherwise it is a Tier E test, and it must **name the tier in its docstring** so the next lane does not promote it.

---

## 6 · Integration checklist (skill Step 3), scoped to what applies

| item | applies? | how it is covered |
|---|---|---|
| Contract defined? | ✅ | the registration schema + the verdict enum are the contracts; §2 rows 1–3 |
| Boundary values | ✅ | floor/REFUSED arms: `≥30` labelled pairs, `n > 0` for A4, `≥5` slots for A5 — each with the below-floor arm as a **test**, not narration |
| Empty vs null | ✅ | `REFUSED` ≠ `RED` ≠ `GREEN`; the record's own three-state vocabulary, pinned in CI |
| Timeout / 429 / 503 / 400 / malformed response | ✅ for the capture door | the 402 quota arm is the one that actually fires; timeout/5xx are the eval lane's to report (Tier E) |
| Atomic writes | ⚠️ N/A | the epic writes no multi-row transaction of its own (S6's atomicity is `#5087`'s) |
| Idempotent webhooks | ⚠️ N/A | no webhook surface |
| Concurrent access | ⚠️ N/A | no shared mutable state added by this epic |
| Ordering guaranteed | ✅ | gold sealed **before** the rollup; the before-arm measured **before** the after-arm |

---

## 7 · What this changes downstream

1. **Every A-row child's verification checklist must name its tier** (`CI` vs `Tier E`), or the reader will
   assume it runs on a PR and either bloat CI or distrust the map.
2. **C1's amendment gains three CI-eligible pins** (criterion ids vs the verdict enum; the per-row corpus map;
   the three-state `REFUSED` vocabulary) — all file-scanned, all sub-second.
3. **C2/C3/C5/C6's evidence is Tier E**, and their acceptance text says so — a green PR does not mean the row
   moved, and that must be legible in the issue, not discovered at acceptance.
4. **`test_graph_census_7949.py`'s live half moves to Tier E** — the only existing-test change this epic
   requires, and it is a *move*, not a deletion.
