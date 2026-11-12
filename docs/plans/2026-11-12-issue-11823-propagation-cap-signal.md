---
title: "The propagation cap becomes visible — a graph-scoped reach signal for dream passes"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-12
updated: 2026-11-12
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: tortoise-ep, dream-scheduler
aboutObjects: "#11823, #6211, #8447, #903"
governingAgreement: "#11823"
---

<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/11823#issuecomment-6818871617 -->
<!-- plan-review: cycles=3, status=clean, version=2.4.0 -->

# The propagation cap becomes visible

**Issue:** #11823 (`bug`, `complexity:standard`, `P1-severe`).

## 1. Confirmed problem

A `dream()` pass has no signal that distinguishes *"the cascade converged over what it should
have"* from *"the cascade stopped at its radius"*. Two separate instruments are structurally
unable to answer it:

1. **The per-pass `coverage`** denominator is the pass's OWN radius —
   `coverage = len(affected) / len(self._window_closure(anchors, max_hops))`
   (`tortoise/sdk.py:33236`, `:33293`), documented as *"local → affected / window-reachable"*
   (`:32878`). A pass that consumes its window reports ≈1.0 regardless of how much of the graph it
   never reached. **Measured:** a write-path `local` pass on a 20-claim chain reported
   `coverage=0.857` (6 of the 7 claims in its own closure) while **6 of 20** live claims in the
   graph had a computed posterior.
2. **The graph-wide `coverage_pct`** numerator is a freshness STAMP, not a stored belief —
   *"fraction of live non-operator claims with a `lastDreamedAt` stamp"* (`:32110`) — over a
   denominator that includes a population for which **no belief can exist**: claims EP cannot reach.
   A stamp is written for exactly that population by the trivial-scan path (`dream.py:673-677`), so the
   ratio can reach 1.0 over claims with nothing to report. **Measured:** a full pass reported
   `coverage=1.0`, `coverage_pct=1.0` over a claim that ended `confidence=None`,
   `posterior_alpha=None`, with `lastDreamedAt` set. **Corrected framing (PLAN-VERIFY cycle 1):** the
   claim is not that `coverage_pct` "counts a computed claim as uncomputed" — it is that its
   population is the wrong one *and* its numerator is a proxy, so it cannot answer "how many claims
   have no belief". The new signal must therefore use (a) the EP-reachable population and (b) the
   repo's shared belief-presence predicate, not a private `IS NOT NULL`.

Cause: `ep._flush_cache` writes posteriors only for the nodes in `_node_cache` — the nodes the run
consumed (`tortoise/ep.py:579`) — and marks nothing dirty. A claim the pass did not reach is
therefore never recomputed, and no surface says how many such claims exist.

**Falsifier** (carried from the research block, one line): if after a write-path `local` pass either
(a) `coverage`'s denominator is a population the pass did not choose, or (b) `dream_health_check()`
reports a graph-scoped count of claims whose posterior was never computed, then the defect as stated
does not exist and this plan is withdrawn rather than patched. **Neither holds on this head** —
measured above and reproducible by the tests in §7.

**Confidence:** 90. The mechanism is read from the code, the numbers are measured on this box, and
the two instrument defects are independent of each other (each one alone produces the false PASS).

## 2. Alternatives considered

| # | Approach | Verdict |
|---|---|---|
| A | **Change the write-path pass to `stale-first`** (the issue body's proposed fix) | **REJECTED — it reverses a recorded decision.** `docs/epics/2026-08-13-903-dreaming-ep/04-plan.md:283` (D2-5): *"the write context never invokes scheduled/window mode — write → local only; window mode unreachable from the write path"*, and the epic's own risk table (`:365`) makes that the mitigating control for the embedded ≤500 ms latency budget. The contradiction test says a standard that contradicts a decision is not adoptable; the route is a **reopen**, which is raised on the issue (see §9). Not this lane's change. |
| B | **Make `coverage`'s denominator graph-scoped** for `local`/`stale-first` | **REJECTED.** Reverses the documented I1 coverage semantics (`:32878`) and the per-mode contract in the epic's interfaces section; also destroys the signal `coverage` legitimately carries ("how much of the window I consumed"). A ratio with two different meanings on one key is worse than the silence it fixes. |
| C | **Add `truncated`/`uncovered` keys to the `dream()` result** | **REJECTED.** `:33904`: *"on the dream result: the I1 local key-set is pinned exact and does not carry them (#6211 — see `_dream_local`)"*; pinned for all three modes by `tests/test_epic903_modes.py:15-22` (`LOCAL_KEYS`/`STALE_KEYS`/`FULL_KEYS`) and for the no-op path by `tests/test_dream.py:70-72`. |
| D | **Graph-scoped propagation record on the health surface** — carry `no_belief`/`belief_coverage_pct` (denominator = the EP-reachable claim population, which the pass does not choose) plus the last pass's `mode`/`max_hops`/`affected`/`window_reachable` | **CHOSEN.** It answers the question the issue titles, adds no key to any pinned key-set, changes no mode selection, reverses no decision. |
| E | **Emit a WARNING log from the pass** | **REJECTED.** `docs/architecture/BOUNDED-OPERATION-CONTRACT.md` §2a: *"Do not 'fix' any of these by adding a sentinel row, a magic string, or a warning log"* — a log is the "recorded somewhere the caller does not read" that the refusal clause forbids. |

**Disclosed tension, not papered over (corrected after PLAN-VERIFY cycle 1).**
`BOUNDED-OPERATION-CONTRACT.md:252` says a marker must be *"in the same object as the rows — not a
side channel, not a log line, not a metric"*, which argues for C. The first draft resolved it on a
premise that is **false**: that #6211 put the operator-cap report on the health surface. It did not
— #6211's report is on the `compute_confidence` **result** (`sdk.py:33626`, `_report`) and on the
Dreamer attribute; `dream_health_check()`'s dict carries no cap report (`:32082-32107`, and its
`operator_counts` is the post-cap size). The premise that DOES hold is the contract's own dict-body
rule: *"wrapping a **list** in an object is the return-type change; adding a field to an object body
is not"* (`BOUNDED-OPERATION-CONTRACT.md:206-207`, the `list_points`/`/v1/search` precedent).
`dream_health_check()` returns a plain `dict` with no key-set assertion anywhere in `tests/`, so an
additive field there is contract-compliant on the **approval** axis. That is not the axis §4.2
governs, though: `:252` is a **placement** rule ("in the same object as the rows"), and it does not
bind here because §4 governs markers on **bounded operations** — the §2 census rows are list-shaped
reads with a caller- or code-supplied bound — while this change adds a field to a **health record**,
not to the rows of a bounded read. The authority that does support the placement is the #3139
precedent in the same dict (`sdk.py:32088`: *"belief state WRITTEN — as distinct from
`last_pass_output`, which counts stamps"*): the repo has already ruled, on this exact surface and for
this exact reason, that belief observability belongs on the health record because the pass object's
key-set is pinned. The remaining conflict — `dream()`'s own object still carries no reach signal — is
recorded as a **conflict**, not as a satisfied contract, and is owned by #12099.

**Residual, recorded rather than absorbed (filed as #12099):** after this ships, the **graph-scoped**
belief gap is one call away on the health surface — but the **per-pass reach is not**: it is
process-scoped, so on the hosted door (`hosted_api.py:13199` builds a fresh SDK per request) its
`last_pass` half is `null`, and a caller who reads only `dream()`'s or `compute_confidence()`'s result
gets no reach signal on that object. `compute_confidence`'s result is superset-tolerant
(`tests/test_ep_local_395.py` asserts `<=`) and is the natural next site for the per-call half; it is
not done here because that result has three sub-paths with different pass shapes and no graph-scoped
denominator available without a per-read query.

## 3. Complexity + domains

`complexity:standard` (as labelled). Domains: `code` (runtime SDK), `docs-product` not touched.
UX low, ontology low, architecture medium (instrument semantics only; no mode, no schema, no surface
shape change that needs approval — `dream_health_check` returns a plain `dict`, so adding keys is
additive and is **not** an MCP/SDK surface change).

## 4. Integration Surface Map

| Surface | Kind | Test layer |
|---|---|---|
| `TortoiseSDK.dream_health_check()` dict | in-process read API (also served by `GET /v1/dream/health` and MCP `tortoise_graph_overview(section="dream_health")` — `tortoise_dream_health` is RETIRED, `config/surface-manifest.yml:877-881`) | unit, embedded (`tests/test_11823_*.py`) |
| `TortoiseSDK._dream_metrics` | in-process pass record; process-scoped, so empty on a fresh request-scoped SDK (see R4) | unit |
| `_metrics_coverage()` / new `_propagation_counts()` / new `_edged_claim_ids()` | private helpers + the graph reads they perform (`_edged_claim_ids` is extracted from `_reachable_claim_count`, which then reuses it **with its closure term intact** — no second copy of the edged predicate, no behaviour change) | unit + one query-shape test |
| `GET /v1/dream/health` | hosted read (returns the dict verbatim) | covered by the dict test; route test unchanged |
| existing alarm (`alarm_verdict`/`alarm_reason`) | behaviour that must NOT change | regression (`tests/test_epic903_observability.py`, `tests/test_dream_noop_3139.py`) |
| `tests/test_epic903_modes.py` `LOCAL_KEYS`/`STALE_KEYS`/`FULL_KEYS` | the I1 key-set pin, all three modes | must stay green |

## 5. Research

Pointer: the `<!-- research: v1 -->` comment on #11823
(<https://github.com/daniel-ospina/tortoise/issues/11823#issuecomment-6818871617>). The decisions it
supports: (a) the cap-report medium is the health surface, not the result dict (#6211 + I1 pin);
(b) `lastDreamedAt` is not a proxy for a computed belief (measured); (c) the mode change is a reopen,
not a fix (D2-5).

## 6. Wiring

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `_dream_local` / `_dream_stale_first` / `_dream_full` | 3 adapters that must each record the pass's reach | this change | ✅ |
| `_record_dream_metrics` | pass record writer | unchanged signature; reads a new metrics key | ✅ |
| `dream_health_check` | read surface (embedded, hosted, MCP all read THIS dict) | this change | ✅ |
| `_metrics_coverage` | existing stamp-coverage helper, must keep its value | unchanged behaviour, refactored to share one query | ✅ |
| `compute_confidence` no-arg path | calls `self.dream(...)`; reads `dreamer._last_selection_stats` only | no interaction (no result keys touched) | ✅ |
| `tests/test_dream.py` exact local key-set | contract pin | must stay green | ✅ |
| `tests/test_epic903_observability.py`, `tests/test_dream_noop_3139.py` | alarm + metrics regressions | must stay green | ✅ |
| `config/surface-manifest.yml` | MCP/SDK surface manifest | **no change** — `dream_health_check`'s return is a dict; no tool/method added, removed or renamed | ✅ |

## 7. Implementation steps (each with the test that proves it)

1. **`_edged_claim_ids()`** — the `edged` set lifted verbatim out of `_reachable_claim_count`
   (`sdk.py:33564-33574`), which then **reuses it and keeps its closure term literally**:
   `_reachable_claim_count` becomes `len(self._window_closure(anchors, max_hops) & self._edged_claim_ids())`
   — one spelling of the predicate, existing behaviour preserved by construction (corrected in
   PLAN-VERIFY cycle 2: a naive extraction that dropped `:33576`'s `return len(closure & edged)` would
   have inflated full-mode `coverage`'s denominator silently).
2. **Denominator semantics (corrected in cycle 3 — the two populations must be the SAME population).**
   `ep_reachable_claims` is its **own `:Point`-scoped `count(DISTINCT n)`**: live non-operator
   `:Point`s participating in ≥1 `IMPL|NAND` edge to a live point. It is **NOT** `len(_edged_claim_ids())`
   — that query is label-blind (`MATCH (n)-[r:IMPL|NAND]-(m)` + `epistemic_disjunction('n')` admits
   `Point|Subject|Object|Event`, `live.py:509`), so reusing it here would mix a `:Point` numerator with
   a wider denominator and produce `belief_coverage_pct > 1` and a NEGATIVE `no_belief` (measured on
   this head: `belief_coverage_pct = 5.0`, `no_belief = -8.0` on a 2-chain + 10 baselined islands
   fixture). A second query spelling is legitimate here precisely because it asks a different
   question; the extraction in step 1 exists only so `_reachable_claim_count` keeps ONE copy of ITS
   predicate.
   - `operator_less_claims` is likewise **its own count**: `MATCH (n:Point) WHERE <live> AND NOT
     (n)-[:IMPL|NAND]-() RETURN count(n)` — the `_trivial_stamp` population (`dream.py:673-677`). It is
     **not** `graph_live_claims - ep_reachable_claims`: the subtraction is wrong three ways (label-blind
     denominator, far-endpoint draft filter, and Points edged only to a draft that `_trivial_stamp`
     does not stamp — measured `-2` and `1`-vs-`scanned_count 0` on minimal fixtures).
   - Invariants (asserted, not merely documented): `0 ≤ no_belief ≤ ep_reachable_claims` and
     `0 ≤ belief_coverage_pct ≤ 1`.
   - ⚠️ `ep_reachable_claims` INCLUDES anchor-less epistemic components (`:Subject-[:IMPL]->:Object`,
     #7902) that a `:Point`-anchored pass can never clear, so `no_belief` may be non-zero on a
     converged graph — **a true statement** (no belief is stored), and the instrument may over-report,
     never under-report. Tests: `test_operator_less_claims_are_not_in_the_denominator` +
     `test_no_belief_stays_inside_its_invariants` (range, not identity).
3. **`_propagation_counts()`** — live non-operator `:Point`s, their `lastDreamedAt` stamps, and their
   **`belief_present` count via the SHARED predicate** `ep_has_belief_cypher` (`search_engine.py:1181`)
   — never a private `IS NOT NULL`, per `analyze.py:41-45` (*"The presence gate is
   `ep_has_belief_cypher`, the SHARED predicate — never a private sum written here"*): a stored
   `(0,0)` pair is not a belief, and a baseline prior is. `belief_present` is therefore a statement
   about **stored** belief presence, never about "this pass recomputed it" (cycle-2 wording fix).
   Tests: `test_a_zero_pair_is_not_a_belief` + `test_a_baseline_prior_is_a_belief` +
   `test_counts_match_an_independent_query`.
4. **`propagation` block in `dream_health_check()`** — `{graph_live_claims, ep_reachable_claims,
   belief_present, no_belief, belief_coverage_pct, stamped, operator_less_claims, unavailable,
   last_pass, last_pass_reason}`. `no_belief = ep_reachable_claims - belief_present` where BOTH terms are the
   `:Point`-scoped population of step 2/3 (so the subtraction is inside one population), and
   `belief_coverage_pct = belief_present / ep_reachable_claims` (0.0 when the denominator is 0).
   Integer casts are explicit (`sum(...)` returns a FLOAT on this engine — measured `3.0` — so the
   block publishes `int(...)`). `unavailable: <reason>` when the read fails (so "0 claims" and
   "could not read" never collapse — `BOUNDED-OPERATION-CONTRACT.md` §1a), and `last_pass: null` +
   `last_pass_reason: "no_pass_in_this_process"` when no pass ran in this process (the hosted
   per-request SDK — never a reassuring zero). Tests:
   `test_a_stamped_claim_with_no_belief_is_reported` (the mutation target),
   `test_unreadable_propagation_is_reported_not_zeroed`,
   `test_no_pass_in_this_process_is_null_not_zero`.
5. **The pass reach is recorded on EVERY pass arm, and BEFORE the no-op guard** (cycle-3 fix:
   `_guard_dream_progress` itself calls `_record_dream_metrics` and then RAISES — `sdk.py:33144` →
   `:33146` — so recording after the guard would report `"no_pass_in_this_process"` for the silent
   no-op, the worst reach there is and the very failure this instrument exists to expose; the
   adapters' closure is computed after the guard, so `_record_pass_reach` takes the window it already
   has, called **before** `_guard_dream_progress`). Arms: local / stale-first / full (before the
   guard) + `budget=0` (`_dream_noop`) + the no-dirty-roots return + the `silent_no_op` raise path.
   `BudgetExceededError` out of `dream_all` on an explicit full budget records nothing (the pass is
   refused before any work) → `last_pass` then describes the PREVIOUS pass; stated, not hidden.
   No extra query — the closure is already computed in the adapters. Tests:
   `test_the_pass_reach_is_recorded_without_a_graph_read` (query counter),
   `test_last_pass_names_the_cap` (`max_hops`), `test_last_pass_uncovered_is_graph_scoped`
   ("6 of 20, 14 uncovered"), `test_a_budget_zero_pass_is_recorded_not_absent`,
   `test_a_no_dirty_roots_pass_is_recorded_not_absent`, `test_a_silent_no_op_pass_records_its_reach`.
6. **`coverage_pct` unchanged, documented as stamp coverage** — Test:
   `test_stamp_coverage_keeps_its_value` (pins the EXACT value on a fixed fixture, not a range) +
   `test_stamp_coverage_and_belief_coverage_can_disagree`.
7. **No alarm change** — Test: `test_the_alarm_verdict_is_unchanged_by_the_new_fields`.

Mutation proof (PROVE): substitute `stamped` for `belief_present` → step 4's and step 6's tests go
red; widen the denominator to all live claims → `test_operator_less_claims_are_not_in_the_denominator`
goes red; make the numerator/denominator population-scoped differently (the cycle-3 bug) →
`test_no_belief_stays_inside_its_invariants` goes red; drop any `_record_pass_reach` call site →
step 5's corresponding test goes red; move the recording after `_guard_dream_progress` →
`test_a_silent_no_op_pass_records_its_reach` goes red; delete the `unavailable` arm → step 4's
unreadable test goes red; drop the closure term from `_reachable_claim_count` while extracting →
`test_reachable_claim_count_is_unchanged` goes red.

## 8. Rounds

**Rounds:** pending (filled at PLAN-VERIFY close).

### Review Scope Contract

**Class:** code

**Risk surface** — what this change can break, each with the check that would catch it:

- R1 the pinned I1 `dream()` result key-set changes → `tests/test_dream.py` exact key-set assertion +
  `test_dream_key_set_is_unchanged_by_this_change`
- R2 `coverage_pct` silently changes meaning → `test_stamp_coverage_keeps_its_value`, which pins
  the EXACT pre-change value on a fixed fixture. (`tests/test_epic903_observability.py:83` asserts a
  RANGE — `0.0 <= coverage_pct <= 1.0` — so it passes under any denominator and is NOT the
  discriminator; corrected after PLAN-VERIFY cycle 1.)
- R3 the pass hot path gains a graph read (write-path latency) → `test_the_pass_reach_is_recorded_without_a_graph_read`
  (query counter asserts zero new reads inside `dream()`)
- R4 the health surface raises, reports a fabricated 0, reports a reassuring zero for a pass that
  never ran in this process, OR reports "no pass ran" for a pass that DID run and reached zero
  (hosted builds a per-request SDK; `_dream_metrics` is process-scoped; `budget=0` and the
  no-dirty-roots arm return before `_record_dream_metrics`) →
  `test_unreadable_propagation_is_reported_not_zeroed`, `test_no_pass_in_this_process_is_null_not_zero`,
  `test_a_budget_zero_pass_is_recorded_not_absent` and `test_a_no_dirty_roots_pass_is_recorded_not_absent`
- R5 the alarm fires (or stops firing) on normal graphs because of the new fields →
  `test_the_alarm_verdict_is_unchanged_by_the_new_fields` + `tests/test_dream_noop_3139.py`
- R6 the new numbers are themselves self-referential again, invent a second belief-presence rule,
  mix populations (the cycle-3 bug), or state an acceptance criterion the code cannot satisfy →
  `test_the_denominator_is_the_graph_not_the_last_pass` (the denominator carries no `max_hops` and no
  window) + `test_no_belief_stays_inside_its_invariants` (`0 ≤ no_belief ≤ ep_reachable_claims`,
  `0 ≤ belief_coverage_pct ≤ 1` — RANGES, not the tautological identity) +
  `test_a_zero_pair_is_not_a_belief` (shared predicate, not `IS NOT NULL`) +
  `test_operator_less_claims_are_not_in_the_denominator` (its own `NOT (n)-[:IMPL|NAND]-()` count,
  disjoint from the denominator) + `test_reachable_claim_count_is_unchanged` (the extraction preserves
  `closure & edged`).
  ⚠️ The zero-claim is scoped, not general: on a fixture whose **whole** edged population sits in the
  full pass's `:Point`-anchored closure, a converged `full` pass leaves `no_belief == 0`. It is NOT
  asserted as a general property — the anchor-less `:Subject→:Object` component (`dream.py:786`,
  `sdk.py:33539`) is in the denominator and no pass can clear it.
- R7 the new reads make a polled endpoint expensive (`GET /v1/dream/health` is per-request; the
  denominator query is an edge traversal) → `test_the_propagation_read_is_three_aggregate_counts`
  (query counter: exactly **three** aggregate reads — `count(DISTINCT n)`+belief `sum` over the
  reachable set, `count`+stamp `sum` over live `:Point`s, and a `NOT (n)-[:IMPL|NAND]-()` `count` —
  all aggregates, never a per-vertex id materialisation on the polled path).
  ⚠️ **Corrected during BUILD (measured, not predicted):** the operator-less count cannot ride the
  same `RETURN` as the other two — `sum(CASE WHEN NOT (n)-[:IMPL|NAND]-() …)` is refused by the
  engine (`ResponseError: Type mismatch: expected Boolean or Null but was List`), so it is its own
  `count` query. Three reads, not two.

**Threat classes:** (not adversarial) — this is a read-only observability field; no gate, no
enforcement path, nothing that can fail open into a wrong verdict.

**Out of scope — must NOT be raised** (note, file, do not chase):

- pre-existing defects outside the diff · style/prose preference with no behaviour change
- the `stale-first` mode change (a D2-5 **reopen**, argued on the issue, not in this diff)
- architectures the plan did not propose · incidental test/fixture shape · `coverage`'s I1 semantics

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared
surface.

## 9. Open question for the owner (reopen, not part of this diff)

**D2-5 reopen.** Should the write-path pass become `stale-first`? The issue measures 20/20 in 1.5 s
for one `stale-first` invocation. Against: D2-5 (`04-plan.md:283`) and its ≤500 ms embedded control
(`:365`), and the epic's documented residual that write-triggered-only deployments have no daemon
(`:371`). This lane ships the visible cap either way; the mode change needs the owner's word because
it reverses a recorded decision.
