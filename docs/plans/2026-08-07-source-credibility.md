---
title: "Source Credibility: Log-Scale Aggregation + Reliability Derivation — Implementation Plan"
type: data
domain: data
status: live
created: 2026-08-07
updated: 2026-08-07
ownedBy: epistemic-team
subjects:
  team: epistemic-team
doc_status: live
aboutSubjects: epistemic-team
aboutObjects: Source, Point, Operator
---

---
title: "Source Credibility: Log-Scale Aggregation + Reliability Derivation — Implementation Plan"
aboutSubjects: organisation-design-team, epistemic-team
aboutObjects: Source, Point, Operator
---

# Source Credibility: Log-Scale Aggregation + Reliability Derivation — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
> **Issue:** #398 · **Team:** organisation-design-team · **Complexity:** standard
> **Scoping:** docs/scoping-398-source-credibility.md (verified: problem-verify + solution-verify × 2 cycles + phase-7 × 2 cycles, all clean)

**Goal:** Activate the validated discrete-tier source credibility model (credibilityTier T0–T4 → Beta priors
{(10,1),(5,1),(3,1),(2,1),(1.1,1)}, log-scale multi-source aggregation) so EP inherits source quality through
the real graph path, plus a derived reliability (0–1) projection with dynamic decay and reputation-weighted
agent assessments.

**Architecture:** New pure-function module `tortoise/source_credibility.py` owns all credibility math;
`_apply_source_inheritance` becomes a thin adapter resolving tiers (credibilityTier > sourceKind tier-form >
registry default > neutral), aggregating positive evidence log-scale, and persisting provenance-marked
baselines (`baseline_source='inherited'`) recomputed per EP run (time-gated, dirty-marked on writes).
Reliability is a query-time derivation materialized as a documented cache on the Source node. `ep.py`
untouched (additive-only). No connector changes, no migration, no per-operator complexity.

### Pattern Research
Skipped — the plan touches zero third-party dependencies (pure stdlib: os, datetime, math; in-repo
FalkorDB projection). All external patterns (GRADE, journalism tiers, Daubert, Beta reputation, knowledge
half-life) were researched during the scoping tortoise-decide cycles and are recorded in the scoping doc.

### Integration Surface Map

| Surface | Layer | Test file | Notes |
|---|---|---|---|
| `source_credibility.py` pure math | unit (embedded) | `tests/test_source_credibility.py` | N=1 identity, anti-Sybil, monotonicity, factor clamps |
| `set_point_baseline` provenance marker | unit (embedded) | `tests/test_source_credibility.py` | 2×2 mapping, never-clobber, gate dirty-mark |
| `_apply_source_inheritance` real path | integration (embedded) | `tests/test_source_inheritance_own.py` | Source→extractedFrom→inheritance→EP; distinct URLs; per-call recency_decay |
| `get_source_reliability` + cache | integration (embedded) | `tests/test_source_inheritance_own.py` | consistency invariant, cache invalidation |
| `assess_source` + reputation | integration (embedded) | `tests/test_source_inheritance_own.py` | scoring, latest-wins, reputation weighting, clamps |
| registry + `create_source(tier=)` | integration (embedded) | `tests/test_source_inheritance_own.py` | register_source_kind_default activation, precedence |
| `calibrate_summary` surfacing | integration (embedded) | `tests/test_calibration.py` (fixture migrated to embedded) | effective-tier resolution, advisory text |
| MCP tools | unit (embedded) | `tests/test_mcp_server.py` + `tests/test_mcp_http.py` | registration + mcp_auth allowlist + annotations + HTTP-surface test |
| `list_sources` output | integration (embedded) | `tests/test_source_inheritance_own.py` | dual-write: sourceKind as passed, tier on credibilityTier |
| regression: recency suite | integration (embedded) | `tests/test_event_provenance.py` | must stay green unmodified |
| regression: #341 prior suite | integration (Docker) | `tests/test_ep_sources.py` | must stay green unmodified |
| ontology + experiment doc | docs | n/a | v3.2 bump + annotations |

---

### Task 1: Pure credibility math module + source-kind registry

**Intent:** Canonicalize the validated model (TIER_PRIORS, aggregation, decay, tier resolution, assessment
factor) into testable pure functions — the single source of truth #341 builds on — INCLUDING the
source-kind registry (`SOURCE_KIND_DEFAULTS` + `register_source_kind_default` + `resolve_source_tier`) that
Task 3's adapter depends on.

**Acceptance:** `tortoise/source_credibility.py` exports `TIER_PRIORS`, `pc_base(tier)`, `aggregate_prior`,
`decay_factor`, `resolve_tier`, `assessment_factor`, `derive_reliability` (formula: reliability =
`mean_from_beta(1 + pc_eff, 1)` of the modulated prior — pinned in Task 4 test (b); untiered+assessed =
reputation-weighted mean of scores), `SOURCE_KIND_DEFAULTS`
(T0–T4 identity + explicit None for ALL legacy kinds), `register_source_kind_default(kind, tier)`,
`resolve_source_tier(source_kind)`. `tests/test_source_credibility.py` passes embedded.

**Files:**
- Create: `tortoise/source_credibility.py`
- Create: `tests/test_source_credibility.py`

**Steps:**
1. Write `tests/test_source_credibility.py` (failing): N=1 identity (`aggregate_prior(tier,1) ==
   TIER_PRIORS[tier]`), strictly diminishing gains, anti-Sybil (1000×T4 pc ≈ 0.997 < T3 pc=1.0),
   monotonic prior mean in N (uniform weight), mixed-tier sum, `pc_base := alpha−1`, decay_factor
   (0.95^years, T0=1.0, sourceDate-else-ingestedAt, timezone-naive→UTC; future sourceDate → clamp 1.0;
   pre-epoch → clamp 1.0, no exception), resolve_tier precedence matrix (credibilityTier > sourceKind
   tier-form > registry > None) INCLUDING malformed tiers (`"T9"`, `"t1"`, `"T1 "`, `""` → None),
   assessment_factor k=1.0 bounds + clamps [0.1,2.0] + NaN/±inf→1.0, registry:
   `register_source_kind_default('github_issue','T2')` → `resolve_source_tier` returns T2; unknown →
   None; default registry has identity + None only.
2. Run → FAIL (module missing).
3. Implement `tortoise/source_credibility.py` with the pinned formula:
   `pc_t = log2(N_t+1) × decay_t × (Σ_{i∈t} base_pc(tier_i)·factor_i)/N_t` where `decay_t` keys on the
   TIER's most-recent source (T0 exempt). Formula contract comment: `pc_base := alpha − 1`.
4. Run → PASS.

### Task 2: Baseline provenance marker + recompute gate

**Intent:** Make "inherited vs explicit" first-class so inheritance is recomputable (dynamic decay) while
explicit baselines are permanently safe.

**Acceptance:** `set_point_baseline(claim_id, alpha, beta, *, source="explicit")` persists
`baseline_source`; `_apply_source_inheritance` recomputes inherited baselines with a **per-point time gate
persisted on the graph** (`n.inherited_at` property; default interval 3600s, env
`TORTOISE_EP_REINHERIT_INTERVAL`, param `recompute_interval`, 0=always) — points with NO baseline
(`baseline_source IS NULL AND baseline_set IS NOT true` — never-inherited, never-explicit) are ALWAYS
eligible (new points inherit immediately, no hour-long neutral window); `baseline_source='inherited'`
points recompute only when interval elapsed OR dirty-marked; `baseline_source='explicit'` (or legacy
`baseline_set=true`) is NEVER eligible; recompute write is skipped when
(alpha,beta) unchanged within rel 1e-9 (epsilon guard) — but the `inherited_at` stamp is ALWAYS refreshed
on a dirty-marked recompute (even when the value write is skipped), so dirty points settle after one pass. Write events (create_point with extractedFrom →
`_link_source`, extractedFrom edge deletion, `set_source_tier`, `assess_source`, `create_source(tier=)`)
dirty-mark the point/source gate timestamp. Gate is graph-persisted so multiple SDK instances on one graph
dedupe.

**Files:**
- Modify: `tortoise/sdk.py` (`set_point_baseline` ~1521-1538; `_apply_source_inheritance` WHERE + gate)
- Test: `tests/test_source_credibility.py`

**Steps:**
1. Failing tests: (a) explicit baseline persists `baseline_source='explicit'`; (b) inherited persists
   `'inherited'`; (c) explicit baseline never clobbered by stronger source; (d) inherited recomputes when
   sourceDate ages (interval=0); (e) two calls within interval → no rewrite (no dirty churn);
   (f) NULL+true (legacy) → never clobbered; (g) new point created from tiered source inherits
   IMMEDIATELY (interval>0, no stale neutral window); (h) extractedFrom edge deletion dirty-marks → revert
   to neutral within interval; (i) two SDK instances on one graph: second instance's within-interval
   compute dedupes (no ep_alpha rewrite); (j) epsilon guard: interval=0 recompute of unchanged values →
   no rewrite (rel 1e-9).
2. Implement (graph-persisted `inherited_at` per point; dirty-mark via `_mark_dirty` on write events).
   Run → PASS.

### Task 3: Log-scale aggregation adapter (real path)

**Intent:** Replace highest-tier-wins with the validated log-scale aggregation through the real graph path;
activate inheritance for tiered sources (including registry-resolved kinds).

**Acceptance:** `_apply_source_inheritance` aggregates positive evidence per the pinned formula
(consuming `aggregate_prior`/`resolve_tier` from Task 1); `tests/test_source_inheritance_own.py` passes
embedded with the OWN real-path invariants; `tests/test_calibration.py` fixture migrated to embedded and
stale assertions updated (moved here from Task 7 so the inheritance change lands with its regression suite
green).

**Files:**
- Modify: `tortoise/sdk.py` (`_apply_source_inheritance` ~1545-1612)
- Create: `tests/test_source_inheritance_own.py`

**Steps:**
1. Failing OWN tests (real path: create_source/_link_source → extractedFrom → _apply_source_inheritance;
   embedded `TortoiseSDK(db_path)`; distinct URLs; per-call `recency_decay=1.0` for aggregation-only cases;
   fixed-epoch + runtime-clock pattern for decay cases). **Assertions on `ep_alpha`/`ep_beta` via
   get_point for aggregation math (deterministic); operator scaffolding + EP confidences only for
   ordering/NAND tests:**
   - 2×T4 > 1×T4 (corroboration — fails under highest-tier-wins)
   - 100×T4 < 1×T2; 1000×T4 ≈ 1×T3 (|Δ|<0.05) — anti-Sybil
   - monotonic single-source tiers T4<T3<T2<T1<T0; T0−T4 gap > 0.30 (via EP confidences with operator chain)
   - adding an ANCIENT same-tier source does NOT decrease pc (tier-most-recent decay)
   - legacy kind activation via `register_source_kind_default('github_issue', 'T2')` (no credibilityTier)
   - explicit baseline never clobbered; removal of last extractedFrom reverts to neutral (idempotency)
   - dynamic decay: aging sourceDate lowers inherited prior on next run (interval=0)
   - NAND real path DIRECTIONAL: Point_A (T0-sourced) NAND Point_B (T4-sourced) vs Point_C (T4-sourced)
     NAND Point_B — assert the T0-sourced operand yields higher operator output than the T4-sourced one;
     document EP-factor contradiction may differ from S8 prior-level values; pin measured tolerance
   - assessment points (`pointKind='assessment'`) excluded from inheritance
   - malformed tier on Source (`credibilityTier='T9'`) → resolve None → excluded from per-tier N counts
     (does not shift other tiers' pc)
   - malformed sourceDate → no decay (safe), documented contract
2. Implement the adapter (positive-only; per-source resolve/factor; per-tier decay; WHERE =
   inherit-eligible set; `pointKind <> 'assessment'` filter; per-point gate from Task 2). Aggregation logic
   inline here; Task 4 extracts it into the shared `_compute_source_prior()` (single source of truth) and
   re-runs these tests green.
3. Migrate `tests/test_calibration.py` fixture to embedded `TortoiseSDK(db_path)` (test_event_provenance
   pattern) + fix stale assertions (T1→(5,1) not (8,2); multi-source → aggregated prior per pinned formula;
   add explicit-never-clobbered + untiered-surfacing).
4. Run → PASS. Recency suite green (N=1 degeneracy).

### Task 4: Reliability derivation API + cache

**Intent:** Expose the issue's headline capability — Source reliability (0–1) with dynamic temporal decay.

**Acceptance:** `get_source_reliability(url)` returns {reliability, components} derived from the shared
`_compute_source_prior()`; cache (reliability/reliabilityComponents/reliability_derived_at) written through;
**consistency-checked on read** (recompute when cache older than the interval OR when the source's
tier/sourceDate changed vs cached components — no indefinite staleness, including after raw graph writes);
invalidated on tier/assessment writes. Untiered source: no assessments → null with reason 'untiered';
untiered WITH assessments → assessment-only reliability (reputation-weighted mean of scores, documented as
display-only — untiered sources never feed EP).

**Files:**
- Modify: `tortoise/sdk.py` (add `get_source_reliability` near reputation section)
- Modify: `tortoise/source_credibility.py` (`derive_reliability`)
- Test: `tests/test_source_inheritance_own.py`

**Steps:**
1. Failing tests: (a) untiered+unassessed → null with reason; untiered+assessed → assessment-only
   reliability; (b) decayed tier mean matches `mean_from_beta(1+pc·decay, 1)`; (c) consistency invariant
   (single-source): `get_source_reliability(url).mean ≈ prior mean EP applied` — tolerance **rel ~5e-4**
   (matches #341 suite's proven EPSILON=1e-4 abs), compared against `_compute_source_prior()` directly;
   (d) cache invalidation via a manual `SET s.credibilityTier` raw write (consistency-check-on-read
   recomputes — no indefinite staleness); `create_source(tier=)`-driven invalidation moves to Task 6
   (create_source's tier kwarg lands there); assessment-driven invalidation moves to Task 5;
   `set_source_tier`-driven moves to Task 6; (e) multi-source asserts aggregation formula (not exact
   equality).
2. Implement `_compute_source_prior()` (single source of truth; includes the batched assessment
   aggregation query — ACTIVE in Task 4 and exercised by the untiered assessment-only test; only the factor
   application on TIERED sources is inert (1.0) until Task 5 wires assess_source) + `get_source_reliability`
   + write-through cache + read consistency-check. Refactor `_apply_source_inheritance` (Task 3) to consume
   `_compute_source_prior()`.
3. Run → PASS (Task 3 tests re-run green).

### Task 5: assess_source + reputation weighting

**Intent:** Per-agent track record on source assessment feeds reliability (indicator 3) via ontology §2
Statement Points.

**Acceptance:** `assess_source(url, assessor, score, rationale)` creates `pointKind='assessment'` Points
(properties targetSource/assessor/score/rationale), per-(url,assessor) latest wins (older marked
outdated), weighted by `compute_reputation(assessor).mean` (snapshot at write), factor clamped [0.1,2.0].

**Files:**
- Modify: `tortoise/sdk.py` (add `assess_source`)
- Modify: `tortoise/source_credibility.py` (assessment aggregation consumed by `_compute_source_prior`)
- Test: `tests/test_source_inheritance_own.py`

**Steps:**
1. Failing tests: creates assessment with correct props; score validation (non-numeric → clean ValueError;
   out-of-[0,1] → ValueError); rationale required; re-assessment by same assessor supersedes — **aggregation
   picks the LATEST active assessment per (targetSource, assessor) by createdAt (crash-safe: even if two
   active exist transiently after a partial write, only the latest counts — no double-count)**; high-
   reputation assessor shifts reliability more than neutral at equal score; factor clamp enforced at API
   boundary; zero-track-record assessor (rep 0.5) contributes 0; **reputation snapshot invariant:
   assess with low-rep assessor, raise assessor reputation via event graph, re-run reliability → factor
   unchanged (snapshot at write, stored in reliabilityComponents)**; cache invalidation + gate dirty-mark
   on assessment write; scaled latest-wins (≥1000 assessments, mixed outdated/active → correct active set).
2. Implement (create Point → mark older outdated → refresh cache; aggregation is
   latest-per-(url,assessor) by construction). Run → PASS.

### Task 6: Source-type registry + ingest tier + calibrate surfacing + minimal MCP

**Intent:** Extensible sourceType vocabulary (indicator 4); tier assignment at creation; audit surface.

**Acceptance:** (registry moved to Task 1.) `create_source(url, sourceKind, tier=None, sourceDate=None)`
dual-write rule (sourceKind tier-form → mirror to credibilityTier; legacy type string → tier only in
credibilityTier; **existing Source never gets sourceKind overwritten** — URL-collision ordering pinned);
`set_source_tier(url, tier)`; `calibrate_summary` resolves EFFECTIVE tier and surfaces untiered (suggestion
→ set_source_tier/create_source(tier=)); `tortoise_set_source_tier`, `tortoise_get_source_reliability`,
`tortoise_assess_source` MCP tools registered + mcp_auth HTTP_ALLOWED + ToolAnnotations
(get_source_reliability = NO readOnlyHint — it writes the cache; assess_source = destructiveHint).

**Files:**
- Modify: `tortoise/source_credibility.py` (registry data + functions)
- Modify: `tortoise/sdk.py` (`create_source`, `set_source_tier`, `calibrate_summary`)
- Modify: `tortoise/mcp_server.py` (tortoise_get_source_reliability, tortoise_assess_source,
  tortoise_set_source_tier, create_source tier passthrough) + `tortoise/mcp_auth.py` (HTTP_ALLOWED) +
  ToolAnnotations
- Test: `tests/test_source_inheritance_own.py`, `tests/test_mcp_server.py`, `tests/test_mcp_http.py`

**Steps:**
1. Failing tests: (a) `create_source(url, "github_issue", tier="T2")` → credibilityTier="T2", sourceKind
   unchanged; `create_source(url, "T0")` → sourceKind="T0" AND credibilityTier="T0"; `set_source_tier`
   never touches legacy type strings; (b) **URL collision: `create_point(extractedFrom=url)` first
   (auto-creates Source with sourceKind='document'), then `create_source(url, "T0", tier="T0")` → sourceKind
   PRESERVED ('document'), tier applied to credibilityTier — pinned ordering; conflicting tier args →
   last-wins documented; (c) registry-registered kind activates inheritance end-to-end (Task 1 registry);
   per-source credibilityTier beats registry default; (d) calibrate_summary: untiered source surfaced with
   set_source_tier suggestion; registry-tiered source NOT flagged; legacy-inherited advisory; assessment
   points excluded; (e) list_sources output after dual-write: sourceKind as passed, tier on
   credibilityTier; (f) MCP: tools registered (tests/test_mcp_server.py) + HTTP_ALLOWED membership +
   annotations (tests/test_mcp_http.py); HTTP-surface test calling tortoise_get_source_reliability and
   tortoise_assess_source over the team-resolution path (valid + invalid score error shape).
2. Implement. Run → PASS + test_calibration (embedded fixture) + mcp tests.

### Task 7: Ontology doc v3.2 + experiment doc annotations + stale test fixes

**Intent:** Align docs and tests with the now-active model; close the dormant-fragmentation loop.

**Acceptance:** ONTOLOGY.md bumped to v3.2 with changelog; experiment doc prior-level annotations;
docs/00_index.md registration; audit.py remediation text updated. (test_calibration fixture + assertions
moved to Task 3.)

**Files:**
- Modify: `docs/ONTOLOGY.md` (§2 pointKind vocabulary + assessment; §3.4 parenthetical; §4.6 Source table:
  sourceKind=type vocabulary, credibilityTier=tier, reliability cache fields, sourceDate; §5 Source Kind
  Vocabulary: tier semantics moved to credibilityTier + assessment kind; §10 decay decision log; §11
  derived-values-may-be-cached clause; version v3.1→v3.2 + changelog)
- Modify: `docs/ep-source-credibility-experiment.md` (§1.2/§1.3/S8/S9 = prior-level reference model used by
  #341 tests, NOT the inheritance implementation; S8 reworded to "negative pseudo-count priors reduce
  confidence"; S5/S6 monotonicity = uniform-weight addition only; decay composition note)
- Modify: `docs/00_index.md` (register this plan doc)
- Modify: `tortoise/audit.py` (missing_sourceKind remediation text → set_source_tier/create_source(tier=))

**Steps:**
1. Doc updates (ONTOLOGY v3.2, experiment doc annotations) + docs/00_index.md registration + audit.py
   remediation text.
2. Run full embedded suite → PASS. Commit.

---

## Test Strategy

**Embedded (bulk, no Docker):** `tests/test_source_credibility.py` (pure math), `tests/test_source_inheritance_own.py`
(OWN real-path integration), `tests/test_calibration.py` (after fixture migration), `tests/test_event_provenance.py`
(recency regression — unmodified), `tests/test_about_edges.py`, `tests/test_operator_source_*.py`.

**Docker (unavailable in this env; verified in CI):** `tests/test_ep_sources.py` (#341 prior-level suite —
must stay green unmodified; runs via conftest docker:// URI).

**Determinism rules:** distinct URLs per source (MERGE on url) EXCEPT the pinned URL-collision test;
per-call `recency_decay` (never env default); fixed-epoch sourceDate with runtime-clock expected-value
computation (mirrors test_event_provenance.py:351-358); `pytest.approx(rel=5e-4)` for DIRECT prior-math comparisons (ep_alpha/ep_beta via get_point; reliability
vs `_compute_source_prior()` — matching #341's DELTA=1e-4 exact-math scale); graph-transited EP-confidence
assertions use loose inequalities with ≥0.02-abs margins (matching #341's proven EPSILON=0.02 EP convergence
tolerance); aggregation math asserted via `ep_alpha`/`ep_beta` through get_point (deterministic).

## Acceptance Criteria (mapped to O/I/T)

| # | Criterion | O/I/T | Verification |
|---|---|---|---|
| AC-1 | Source gains reliability ∈ [0,1] with dynamic temporal decay | Indicator 1 | get_source_reliability + cache; OWN aging test (interval=0) |
| AC-2 | EP consumes resolved source credibility as base weights | Indicator 2 | OWN invariants (2×T4>1×T4, 1000×T4≈1×T3, monotonic, NAND path); consistency invariant; highest-tier-wins gone |
| AC-3 | Agent track record feeds reliability | Indicator 3 | assess_source Points; reputation weighting; latest-wins; clamps |
| AC-4 | Extensible sourceType vocabulary | Indicator 4 | register_source_kind_default; precedence; unknown→neutral; calibrate surfacing |
| AC-5 | Open question answered | — | decay decision log (deferred per-field/per-sourceType; dynamic light modulation) |
| AC-6 | Constraints | — | ep.py zero diff; #341 + recency suites green; no migration; no connector changes |

## Review Handoff

Run `plan-review` on this doc (2–3 reviewers proportional to Medium-High risk). Then execute via
`executing-plans` with `test-writing` per task. Commits via `commit-workflow`.

<!-- plan-review: cycles=3, status=clean, version=2.2.0 -->
<!-- Cycle 1: P1s (registry ordering, invalidation split, gate semantics, tolerance, MCP set_source_tier, cache staleness, crash-safe latest-wins) — fixed. Cycle 2: P2/P3s (create_source ordering, Task 6 set_source_tier tests, _compute_source_prior forward-ref, tolerance citation, eligibility wording, assessment aggregation activeness, inherited_at refresh) — fixed. Final verify: P1 (Task 6 deferred invalidation tests absent) + 3 P3s — fixed. -->
