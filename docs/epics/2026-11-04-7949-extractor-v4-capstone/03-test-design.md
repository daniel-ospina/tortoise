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
| whole suite, measured weight | **14,222.8 s over 1,317 files** (`durations_captured_at: 2026-11-09T09:15:00Z`) |
| capstone-adjacent files | **16 files, 155.5 s total — 1.093 % of the suite** (Σ over the `durations:` rows matching `*capstone*` / `*7949*` / `*v4_declared*` / `*graph_census*`; the sixteen are named below, so the number is reproducible) |
| heaviest three | `test_9664_capstone_verdict_ledger.py` **39.0 s** · `test_graph_census_7949.py` **21.5 s** · `test_capstone_preregistration_9075.py` **20.9 s** (all sixteen are ranked in the table below) |
| the eval lane already exists and is dispatch-only | `eval-suite-manual.yml` and `evals-on-demand.yml` — **both `workflow_dispatch` ONLY**, no `push`, no `pull_request`, no `schedule`; `on_demand:` in `ci-surfaces.yml` excludes `eval/**` + `longmem_eval/**` from merge gating (#10194, owner decision A 2026-10-27). ⚠️ **Dispatch-only is not the same as able to run this epic's measurements** — neither workflow can execute an A-row (§5.2a of `04-plan.md`, restated in conclusion 2 below) |
| capstone files in `carve_out:` / `uri_requiring:` / `slow_files:` | `carve_out:` holds **one**: `test_pack_logic_writeprobe_11083.py` (`config/ci-surfaces.yml:9823`) — the **E6** lane this artifact maps at §2 row `10a`, which builds a `TortoiseSDK(<tmpfile>)` and is carve-out for exactly that reason. `uri_requiring:` and `slow_files:` hold none |

⛔ **The family row is re-derived here, and the sixteen files are named so the next reader can check it.**
Rounds 4–5 re-derived every other figure in this table and left this one at a **stale 64.3 s** — which the rest
of the artifact already refuted: §4 prices `21.5 + 19.2 + 10.0 + 20.9 + 4.1 = 75.7 s` across **five** of the
sixteen, i.e. 1.18× the stated total for all of them. At `durations_captured_at` the same predicate returns **the
same sixteen files, Σ = 155.5 s = 1.093 %**:

| file | s | | file | s |
|---|---:|---|---|---:|
| `test_9664_capstone_verdict_ledger.py` | 39.0 | | `test_7949_capstone_verdict_check.py` | 10.0 |
| `test_graph_census_7949.py` | 21.5 | | `test_7949_capstone_measure.py` | 4.1 |
| `test_capstone_preregistration_9075.py` | 20.9 | | `test_7950_capstone_probe.py` | 3.9 |
| `test_v4_declared_mechanisms.py` | 19.2 | | `test_capstone_1976_report_claims.py` | 1.1 |
| `test_7949_c2_door.py` | 17.5 | | `test_9664_capstone_connector_to_record.py` | 0.9 |
| `test_9567_record_capstone.py` | 15.5 | | `test_capstone_a_rows_10982.py` | 0.9 |
| `test_graph_census_8882.py` | 0.6 | | `test_9889_capstone_probes.py` | 0.2 |
| `test_7949_e5_section10_writeback.py` | 0.1 | | `test_7949_e5_population_reconcile.py` | 0.1 |

Four of the sixteen postdate this artifact (`test_9664_capstone_verdict_ledger.py`, `test_7949_c2_door.py`,
`test_capstone_a_rows_10982.py`, `test_7949_e5_population_reconcile.py`). The **twelve that predate it still sum
to 98.0 s**, so the erratum is not "new tests arrived" — the row was wrong when it was written.

⇒ **Two conclusions up front, and they change the design:**

1. **The capstone is not a CI-cost problem today** — **1.1 %** of the suite (155.5 s of 14,222.8 s). So the
   discipline below is about **not creating one**, not about shrinking an existing one. The direction of that
   conclusion survives the corrected magnitude; only the number moved.
2. **The right TIER for the expensive, non-deterministic work is already decided — the dispatch-only eval lane —
   but that lane does not yet exist as a runnable home for this epic's measurements, and this artifact must not
   send a child to it as if it did.** `04-plan.md` §5.2a establishes it from the workflows: `evals-on-demand.yml`
   carries **no `secrets.` reference anywhere** and runs **pytest over test files** while `capstone_run.py` is a
   script; `eval-suite-manual.yml` has a docker FalkorDB URI but **no hosted-door credentials**, so it cannot
   reach `POST /v1/sessions`. **No existing workflow can run an A-row.** The tier rule stands (evals never gate a
   merge — `#10194`), and **building the job that carries the hosted secret and the `corpus`/`head`/`budget`
   inputs is C2's deliverable** (§5.2a); until it ships, a Tier-E run is a **credentialed operator lane with the
   same gates**, not a workflow dispatch.

---

## 1 · Integration surfaces (skill Step 1)

| Surface | Type | Data flow | The contract |
|---|---|---|---|
| **the pinned registration** (`preregistration.json` + checker) | State / config | Read + Write (amended) | `capstone-preregistration/v1`; `criteria[].id`, `denominator_floors`, `below_floor`, `corpus`, `amendments`; the A-rows enter as a **versioned amendment** quoting superseded text |
| **the verdict gate enum** (`capstone_verdict_check.py:162`) | State (closed vocabulary) | Read | `capstone-verdict-v1`; a **closed** `CRITERIA` tuple that refuses any id outside `RECOGNISED_CRITERIA` (`= (*CRITERIA, *E6_CRITERIA, *A_CRITERIA)`, `:191`; refused at `:970-976` — so A1–A5 and `E6` are accepted) — two schemas move, not one |
| **the corpus pin** (fixture + A-corpus) | State | Read + Write | **three shapes already exist** (`fixtures_hash` in the record; `corpus.sha256`/`items` demanded by `capstone_run.py`'s G1 — the fail-closed read is `if not frozen.get("sha256")`, `:1186`; a derived `e1e2_corpus.json`) — the A-corpus must **unify the contract**, not add a fourth |
| **the A-corpus capture** (`POST /v1/sessions`) | External service | Out → graph | request = curated payloads; response = receipt; **402 quota** at `hosted_api.py:18205` (`if count + est > max_points`) / `:18216` (`quota_refusal_payload`); harness resolution `#3681` |
| **the graph the A-rows read** (hosted `live-v4` / per-run graph) | DB | Read | `:Object`/`:Event`/`:Point`/`:Subject` shapes; `hasPart` Object→Object (ONTOLOGY §3.7); `batch_id` on `RelationUpsert.attrs` |
| **the pack declaration → write path** (`semantics`/`hierarchies`) | State | Read | `relation_write_verdict` returns `writable`; **unread legs**: `semantics` (surfaced at `pack_registry.py:456-459`), `hierarchies` (validated only, `:2341-2355`) |
| **the declared-mechanism registry** (`config/v4-mechanisms.yml`) | Config | Read | 14 rows (13 when this map was written; `#11717` added `mention_record`); `state`/`code:`/`tests:`/`symbols:`/`pins:`/`preconditions:`; guard `tests/test_v4_declared_mechanisms.py` |
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

**18 rows below — numbered `1…17` plus `10a`.** `10a` is **E6**, added after the table was
numbered (when `#11673` registered the row), and it keeps its own label so the children that already cite `10a` do not have to be renumbered — **not**
because the tracker carries it: `#11454`'s body still reads *"17 surfaces mapped; 8 CI-eligible, 9 Tier E"* and
names neither `10a` nor E6 (correction relayed to that issue, DECISION RELAY; the stale `17 / 8 / 9` is the issue's,
not this artifact's). So the map is **18 surfaces — 7 CI, 10 Tier E, 1 split** — not 17, the figure the earlier
summary and the siblings carried.

| # | Surface | Test layer | **Runs where** | Why that tier | Key failure modes to test |
|---|---|---|---|---|---|
| 1 | A-row criteria in the registration | **contract** (unit, file-scan) | **CI** | pure JSON/markdown; the amendment is the thing that rots | a criterion id the verdict enum refuses; a threshold re-derived after a run; superseded text overwritten |
| 2 | verdict gate engine (`check()`/`load_blocker_states()`) | **unit** | **CI** | pure function over committed records (already 10.0 s; see §4) | a row whose `blocked_by` has CLOSED; a malformed record silently disarming V12 |
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
| 14 | declared-mechanism registry row | **config/contract** | **CI** | file + schema; already 19.2 s (see §4) | a row in a state its `code:`/`pins:` do not support |
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
| **Silent function skips** | `subject_binding_available` is a **fail-OPEN** probe (`search_engine.py:6607`, consumed `sdk.py:9540`/`:9593` — a symbol-keyed pair, so a later insertion above either one moves the helper, not the claim); a fail-open probe makes "no subjects" indistinguishable from "never ran" | A5's case must report *which* state occurred — **Tier E**, asserted on the run record, not on a mock |
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
candidate for one). ⚠️ **The `test_`-def and line counts are taken over tree state, so each one carries the head it
was read at** — `900d4aafc6` for every count in this table, re-read there because the counts move with merges that
touch no line these docs own (`#11759` added three functions to `test_v4_declared_mechanisms.py` mid-review). A
count without a head stamp is indistinguishable from a current one; that is how the first version of this table
went stale without going wrong.

| file | measured weight | verdict | why |
|---|---|---|---|
| **`test_graph_census_7949.py`** | **21.5 s** (20 tests; **16 drive a real `capture_session`** on the embedded lane, 4 are offline) | ⛔ **REFACTOR — highest-value change** | An **integration test sitting in a fast shard**. Keep the **4** offline pins in CI (the census arithmetic) and move the **16** live-capture cases to **Tier E** — including the `:Object`-has-no-derivation-link case, which takes the `captured` fixture and therefore cannot stay an offline pin. ⛔ **No recovered-seconds figure is quoted, because none is measured**: the `durations` map prices the FILE (21.5 s), not its live/offline split, so the *"~8–9 s"* carried here from an older 11.1 s reading is **unsourced**, and a proportional guess (21.5 × 16/20 ≈ 17 s) would be a different unmeasured number — substituting it would repeat the defect. The move's point is that a live graph stops being paid for in the fast shard; measure the split before pricing it. |
| **`test_v4_declared_mechanisms.py`** | **19.2 s** (**153** defs, AST-counted at `900d4aafc6` — 150 at this bundle's review head, 140 when the table was first measured; `#11759` added three) | 🔧 **REFACTOR — investigate imports first** | 153 cases over a YAML file should not cost 19.2 s. Likely per-test pack-registry reload / source re-parse. A session-scoped fixture (load packs + parse sources once) is the fix; **measure before and after** and take the weight from a junit sweep, never a local reading. |
| **`test_7949_capstone_verdict_check.py`** | **10.0 s** (**165** defs, AST-counted at `900d4aafc6` — 163 when first measured; `#11673` added two) | 🔧 **REFACTOR — share the throwaway repo** | Its own note says the subprocess-bound ancestry arms dominate and that a no-promisor throwaway repo took the file from 520 s → 6 s once. The remaining cost is one repo build **per test**; a session-scoped repo fixture should cut it. |
| **`test_capstone_preregistration_9075.py`** | **20.9 s** | ✅ **KEEP** | It re-derives `fixtures_hash` over 7 sessions of fixtures + gold. That is the check that makes the whole record trustworthy; 20.9 s for the epic's foundation is correctly priced. |
| **`test_7949_capstone_measure.py`** | **4.1 s** over **12,297 lines / 231 test functions** (re-read at `900d4aafc6`; the `0.2 s` in an earlier revision belongs to `test_9889_capstone_probes.py` — a duplicate-key episode left the two readings adjacent) | ⛔ **DO NOT REFACTOR FOR COST** | It is still one of the cheaper files in the family per line (4.1 s over 12,297 lines). Its length is *prose pins* (README/§10 sentences), and those pins have caught real re-staling. Splitting it for size would move ~0.2 s and risk losing a pin — the wrong trade. ⚠️ Its `durations` row history is contradictory (an earlier duplicate-key episode left `0.2` beside a note claiming `11.1` as an "upper bound"); **the pin that forbids a duplicate `durations` key is the thing to trust, and it is currently satisfied** (verified: zero duplicate keys). |
| **`test_9889_capstone_probes.py`** (0.2 s) · **`test_7950_capstone_probe.py`** (3.9 s) | 4.1 s | 🔍 **AUDIT for overlap, do not delete blind** | Three "capstone probe" files exist for different bundles/epics (9889, 7950, 7949). Before adding the A-row pins, run an **overlap audit**: if two files pin the same sentence of the same bundle, unify; if they pin different bundles, leave them. Deleting a check that catches a real failure is worse than 2 s. |
| `test_extractor_v4_citation_rot.py` (7.0 s) · `…_s4_stage_claims.py` (5.0 s) · the **four** 0.1 s doc guards (`…_step_list_consistency`, `…_doc_citations`, `…_failure_policy`, `…_redaction_reconciliation`) | **12.4 s** (7.0 + 5.0 + 4 × 0.1) | ✅ **KEEP** | Citation-rot and doc-claim guards are the cheapest defence in the repo against the failure this epic keeps producing (a claim true in a summary, stale in its copies). ⚠️ This row said *"the five 0.1 s doc guards"* and 12.5 s; the map prices **four** at 0.1 (`…_citation_rot` is 7.0 and `…_s4_stage_claims` 5.0 — they are the two named beside it, not part of the count). |

**Aggregate proposal:** the two refactors (`graph_census_7949` live half → Tier E; one shared fixture in the
verdict/pack tests) are proposed on their **shape**, not on a recovered-seconds figure — **no measured split exists
for either** (see the two rows above), so this paragraph states none, and **zero checks are deleted**. ⛔ **§4
prices a named population, and its rows must not be re-summed against §0's family total.** The rows above are
`21.5 + 19.2 + 10.0 + 20.9 + 4.1 + (0.2 + 3.9) + 12.4` = **92.2 s**; that set **adds** the two doc-guard files
(`…_citation_rot`, `…_s4_stage_claims` — neither matches §0's family predicate) and **omits nine family files**:
`39.0 + 17.5 + 15.5 + 1.1 + 0.9 + 0.9 + 0.6 + 0.1 + 0.1` — the audited family rows sum to **79.8 s** and the
unaudited ones to **75.7 s**, which is how 79.8 + 75.7 = **155.5 s** reconciles with §0's family row. Different
populations, both stated; neither is a total for "the capstone".

**What NOT to do, stated so it is refused in advance:** do not move the **contract/unit** pins to the eval lane to
"keep CI clean" — they are the cheap early-warning system, and a schema drift found at a milestone instead of on
a PR is exactly the drift-cost the repo's own rules exist to avoid.

---

## 5 · The three tiers, written as a rule the lanes can follow

| tier | what belongs | gate | budget |
|---|---|---|---|
| **CI (merge-gating)** | offline, deterministic, file/record-scanned: registration, verdict engine, corpus-pin map, declaration→predicate derivation, anchor boundary, registry guard, gold integrity, doc/citation guards | **blocks a merge** | *p95 per file < ~1 s target; no graph, no model, no quota* |
| **Tier E — on-demand eval lane** (`evals-on-demand.yml` / `eval-suite-manual.yml`, `workflow_dispatch` only) | every A-row measurement, E1–E5 re-derivation, E4's unpatched leg, E5's raw arm, the rollup-vs-gold scoring | **never gates a merge** (#10194) | unbounded by CI; bounded by the point budget and `#10976`'s cap. ⚠️ **The tier exists; the runner does not yet.** Neither workflow today can execute an A-row (`04-plan.md` §5.2a: `evals-on-demand.yml` has no `secrets.` reference and runs pytest over test files while `capstone_run.py` is a script; `eval-suite-manual.yml` has no hosted-door credentials). **C2 owns the job**; until it ships, a Tier-E run is a credentialed operator lane with the same gates |
| **Milestone eval** (roadmap completion) | performance and cost of the pipeline; the full A-row sweep re-run as a release gate | **gates a milestone, not a PR** | run when a roadmap milestone completes |

**Rule for every new test this epic adds:** it lands in **CI** only if it is offline + deterministic + sub-second.
Otherwise it is a Tier E test, and it must **name the tier in its docstring** so the next lane does not promote it.

---

## 6 · Integration checklist (skill Step 3), scoped to what applies

| item | applies? | how it is covered |
|---|---|---|
| Contract defined? | ✅ | the registration schema + the verdict enum are the contracts; §2 rows 1–3 |
| Boundary values | ✅ | floor/REFUSED arms: `≥30` labelled pairs, **`≥1` labelled node for A4** (the floor **this amendment** registers — `a4_labelled_nodes: 1`, `04-plan.md` §4.1 / AM7; ⚠️ the RECORD does not carry it yet: `preregistration.json`'s A4 row still reads `floor: a4_typed_statements` with `blocked_by: #10988`, retired by §4.4's retired-fields table and rewritten by C1 — do not grep the record and take the old floor as registered; *the population being non-empty* is a second, separate arm — `n > 0` over things and events passes on a graph where every one of them is unlabelled, which is the below-floor run A4 exists to red),  **`≥30` named actors for A5** (the registered floor `a5_actors`; *bindable subject slots* is A5's **metric**) — each with the below-floor arm as a **test**, not narration |
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
