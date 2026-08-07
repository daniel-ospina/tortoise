---
title: "Issue #398 — Source Credibility Scoring: Scoping Output"
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
title: "Issue #398 — Source Credibility Scoring: Scoping Output"
aboutSubjects: organisation-design-team, epistemic-team
aboutObjects: Source, Point, Operator
---

<!-- issue-scoping: v5.1 double diamond + verify -->
# Issue #398 — Source Credibility Scoring: Scoping Output

## Confirmed Problem

Source credibility is a **dormant, triply-fragmented capability**: the validated discrete-tier model
(credibilityTier T0–T4 → Beta priors {(10,1),(5,1),(3,1),(2,1),(1.1,1)}; log-scale aggregation
`effective_pc = base_pc × log₂(N+1)`) never activates because production writes type-strings
(`github_issue`, `slack_message`, `linear_card`, `document`) into `sourceKind`, `_apply_source_inheritance`
reads an unwritten property (`credibilityTier`), and highest-tier-wins discards corroboration. The work is to
make the model real end-to-end: reconcile the vocabulary, implement log-scale aggregation through the real
graph path, derive Source reliability (0–1) at query time (tier + recency + reputation-weighted agent
assessments) as a write-through cache, make decay dynamic via provenance-marked baseline recompute, expose
per-agent assessment, and make the source-type vocabulary extensible — while keeping ep.py additive-only,
keeping `test_ep_sources.py` (#341 wave) and `test_event_provenance.py` recency tests green, and avoiding
connector changes, migrations, or per-operator complexity.

## Verification Gates

### problem-verify: 1 cycle, clean (2 verifiers, NO P0/P1; P2s incorporated)
- **problem-diverge:** 4 framings (dead-on-arrival wiring; staleness/invalidation; representation conflict;
  original) + full devil's-advocate report. All factual claims verified against code by verifiers.
- **problem-converge:** 2 independent agents converged (84/84) on "wire the dormant ontology-aligned
  mechanism end-to-end" — store the classifier (tier), derive the number (reliability) at query time.
- **Controller-incorporated P2s:** decay clock (sourceDate else ingestedAt); legacy posture (neutral until
  tiered, no auto-mapping — the forbidden c=0.447 pattern); vocabulary surfaces enumerated (operator-level
  sourceKind annotations = audit domain, out of scope); indicator-1 verification line.

### solution-verify: 2 cycles, clean (2 verifiers × 2 rounds)
- **Round 1:** 3 genuinely distinct approaches; converge picked Approach 2 (derivation module +
  provenance-marked baselines) on quality (testability, backward compat, ontology compliance). P1s found:
  NAND double-count; mitigation formula/carrier; pack-registry wiring.
- **Controller fixes:** NAND = positive-only (contradiction is EP's factor domain); edge mitigation dropped
  from v1 (no schema carrier — Situation 9 stays #341 prior-level); registry = standalone module registry
  (pack-manifest integration documented follow-up); assessments enter EP via pc-multiplier (indicator 2
  faithful); compare-and-skip guard; NULL→explicit safe default; reliability = documented cache; per-source
  decay; minimal MCP; scope trimmed; deferral rationale recorded.
- **Round 2 (re-verify):** P1s found on baseline 2×2 mapping and per-source-decay anti-Sybil composition.
- **Controller fixes (final):**
  - Baseline 2×2: `baseline_source IS NULL AND baseline_set IS NOT true` → inherit-eligible;
    `NULL AND baseline_set = true` → explicit (never clobbered); `'inherited'` → recompute w/ epsilon
    skip-guard (rel 1e-9, decay drifts continuously); `'explicit'` → never clobbered.
  - Tier precedence: explicit `credibilityTier` property > sourceKind tier-form (T0–T4) > registry default
    > None (neutral — no inheritance, preserving the opt-in guard).
  - Aggregation formula pinned: `pc_t = log₂(N_t+1) × decay_t × (Σ_{i∈t} base_pc(tier)·factor_i)/N_t` where
    `decay_t` keys on the TIER's MOST-RECENT source (sourceDate else ingestedAt), T0 exempt; N=1 degenerates
    to `base_pc × decay` (current formula — recency tests green). Per-source decay REJECTED (non-monotone).
  - Assessment factor: `factor = 1 + k·Σ_a (rep_a − 0.5)·(score_a − 0.5)`, clamped [0.1, 2.0],
    NaN/±inf → 1.0; zero assessments → 1.0 (exact tier priors). Enforced at API boundary AND read path.
  - Reliability invariant: exact-equality `get_source_reliability(url).mean == inherited prior mean` scoped
    to single-source points; shared `_compute_source_prior()` is the single source of truth for point write
    AND reliability cache. Never blend means in probability space.
  - Unknown sourceKind → neutral (no inheritance); `register_source_kind_default()` is the extension point.
  - Determinism: OWN tests compute expected values from runtime clock vs fixed epoch; distinct URLs
    mandatory (MERGE on url); per-call `recency_decay` (never env default).

### Qwen coherence check: substituted with fresh-context sub-agent (Qwen3.8-max unavailable) — see Phase 7
### Phase 7 parallel review: 3 reviewers (codebase/docs, devil's advocate, coherence) — N/A: UX (backend-only), Epic (standalone)

## Plan (approach 2 — derivation module + provenance-marked baselines)

**Problem Statement:** Source credibility is dormant and fragmented: the validated tier model never activates
(type-string sourceKind; unwritten credibilityTier), highest-tier-wins discards corroboration, frozen
baselines freeze decay, and no surface exposes reliability (0–1), agent assessments, or an extensible type
vocabulary. EP treats all production sources as neutral.

**Proposed Solution:** New pure-function module `tortoise/source_credibility.py` owns the model (TIER_PRIORS,
aggregate_prior, decay_factor, resolve_tier, assessment_factor, derive_reliability). `_apply_source_inheritance`
becomes a thin adapter: resolves tier per source (credibilityTier > sourceKind tier-form > registry > neutral),
aggregates positive evidence log-scale with per-tier decay (tier-most-recent) and assessment factor, persists provenance-marked
baselines (`baseline_source='inherited'`) recomputed per EP run (epsilon skip-guard), never clobbering explicit
baselines. `set_point_baseline` gains `source=` param. `get_source_reliability(url)` derives 0–1 (single shared
prior function) and writes through a cache (`reliability`, `reliabilityComponents`, `reliability_derived_at`).
`assess_source(url, assessor, score, rationale)` creates `pointKind="assessment"` Statement Points
(properties targetSource/assessor/score/rationale; per-(url,assessor) latest wins, older marked outdated)
weighted by `compute_reputation(assessor).mean`. Registry: `SOURCE_KIND_DEFAULTS` (T0–T4 identity + legacy
connector kinds) + `register_source_kind_default()`; pack-manifest `sourceKinds` integration = follow-up.
`create_source` gains `tier=` kwarg (dual-write rule: if sourceKind is tier-form, mirror to credibilityTier).
`calibrate_summary` surfaces untiered sources. Decay: existing 0.95^years T0-exempt formula, per-source aging,
keyed sourceDate else ingestedAt, recomputed per run — **no new decay curve; per-field/per-sourceType decay
explicitly deferred** (issue open question answered). NAND contradiction = EP factor domain (inheritance is
positive-only). Edge-level mitigation dropped from v1 (no schema carrier). ONTOLOGY.md + experiment doc
annotations land in the same PR.

## Implementation Tasks (7)

1. **Pure math module** — `tortoise/source_credibility.py` (TIER_PRIORS, aggregate_prior with pinned formula,
   decay_factor, resolve_tier, assessment_factor, derive_reliability) + `tests/test_source_credibility.py`
   (embedded, pure math: N=1 identity, diminishing returns, anti-Sybil 1000×T4≈1×T3, monotonic ordering,
   factor clamping/bounds).
2. **Baseline provenance** — `set_point_baseline(source=...)` persists `baseline_source`; 2×2 mapping;
   epsilon skip-guard; `tests/` marker tests (explicit never clobbered; inherited recomputes when sourceDate
   ages; no dirty churn on unchanged values).
3. **Log-scale aggregation adapter** — rewrite `_apply_source_inheritance` (positive-only, per-source
   resolve/factor, per-tier decay keyed on tier-most-recent, pinned formula, WHERE = inherit-eligible set); `tests/test_source_inheritance_own.py`
   (embedded, real path: create_source/_link_source → extractedFrom → _apply_source_inheritance →
   compute_confidence; 2×T4>1×T4, 1000×T4≈1×T3, monotonic tiers, legacy kind activation via registry default,
   explicit-never-clobbered, dynamic decay with fixed-epoch assertions, distinct URLs, per-call recency_decay).
4. **Reliability API + cache** — `get_source_reliability(url)` (single shared `_compute_source_prior()`);
   write-through `reliability`/`reliabilityComponents`/`reliability_derived_at`; consistency invariant test
   (single-source exact; multi-source formula).
5. **assess_source** — Statement Point creation, validation (score ∈ [0,1], rationale required), latest-wins
   supersession, reputation weighting (compute_reputation), factor clamp enforcement; tests.
6. **Registry + ingest tier + calibrate** — `SOURCE_KIND_DEFAULTS` + `register_source_kind_default()` +
   `resolve_source_tier()`; `create_source(tier=)` + `set_source_tier()`; `calibrate_summary` untiered
   surfacing; minimal MCP tools (tortoise_get_source_reliability, tortoise_assess_source, create_source tier);
   tests.
7. **Docs + stale tests** — ONTOLOGY.md (§2 pointKind vocabulary + assessment; §4.6 Source table:
   sourceKind=type vocabulary, credibilityTier=tier, reliability cache fields, sourceDate; §10 decay decision
   log; §11 derived-values-may-be-cached clause); experiment doc annotations (§1.2/§1.3/S8/S9 = prior-level
   reference model, not inheritance implementation; S8 reword; monotonicity S5/S6 source-addition-only);
   `tests/test_calibration.py` stale assertions fixed (T1→(5,1) not (8,2); multi-source → aggregated prior);
   plan doc registration.

## Rejected Alternatives

- **Static sourceType→reliability mapping** (issue-mandated rejection, c=0.447): registry defaults are
  advisory tier hints only, per-source overridable, unknown kinds neutral — never a static score table.
- **Stored continuous reliability property** (ontology §2/§11 violation, duplicates tier mechanism, write-once
  staleness): reliability is derived; the Source property is a documented cache.
- **Full decay deferral** (contradicts Indicator 1 + §10 permissive language + existing #122): existing light
  modulation retained; per-field/per-sourceType curves deferred.
- **Approach 1 (monolith in sdk.py)**: weaker testability, sourceKind facet semantics shift, assesses edge not
  in ontology §3.2. *Would be better* if a single-file PR were required.
- **Approach 3 (ingest-time read model)**: dual-write consistency burden, wider projection blast radius,
  violates no-connector-change. *Would be better* in a write-dominated graph with centralized ingest.
- **NAND-negative-pc in inheritance** (double-counts EP factor): contradiction is EP's domain.
- **Edge-level mitigation in v1** (no schema carrier): deferred.

## Wiring Check

| Touch Point | Type | Covered By | Status |
|-------------|------|------------|--------|
| `_apply_source_inheritance` / `set_point_baseline` | SDK core | Tasks 2–3 | ✅ |
| `source_credibility.py` pure math | new module | Task 1 | ✅ |
| `create_source` / `set_source_tier` | SDK API | Task 6 | ✅ |
| `get_source_reliability` / `assess_source` | SDK API + MCP | Tasks 4–5 | ✅ |
| `calibrate_summary` | SDK audit | Task 6 | ✅ |
| `compute_reputation` | SDK (existing) | consumed by Task 5 | ✅ |
| Pack registry (sourceKinds manifest) | deferred | follow-up, documented | ✅ (documented deferral) |
| Search facets (`sourceKind`) | untouched | backward compat | ✅ |
| Connectors | untouched | backward compat | ✅ |
| `ep.py` | untouched (additive-only) | zero diff | ✅ |
| `test_event_provenance.py` recency tests | regression gate | Tasks 1–3 | ✅ |
| `test_ep_sources.py` (#341) | compatibility | prior-level model intact | ✅ |
| `test_calibration.py` stale assertions | fix | Task 7 | ✅ |
| ONTOLOGY.md / experiment doc | docs | Task 7 | ✅ |

## Complexity

| Domain | Rating |
|--------|--------|
| TIER | standard |
| UX | low (backend-only, no UI) |
| Architecture | medium (new module, SDK adapter) |
| Ontology | medium (vocabulary reconciliation + doc update) |

---

## Addendum — Phase 7 Review Resolutions (controller, cycle 2)

### P0/P1 Resolutions (from 3 parallel reviewers)

| # | Finding (severity) | Controller Decision (pinned) |
|---|---|---|
| A | Legacy baselines freeze under 2×2 (P0) | Every NEW `set_point_baseline` write persists `baseline_source` ('explicit' when `source=` absent; 'inherited' or the source URL otherwise) — new explicit baselines are always distinguishable from legacy NULL+true. | `baseline_source IS NULL AND baseline_set = true` stays **explicit** (safe, never clobbered). `calibrate_summary` gains a "legacy inherited baseline detected — re-derive via set_source_tier or explicit set_point_baseline" advisory. NO migration (production never ran inheritance — dormant). Documented boundary. |
| B | Pinned formula non-monotone under within-tier age heterogeneity (P1) | **decay_t keys on the tier's MOST-RECENT source** (sourceDate/ingestedAt) — matches current code (sdk.py:1591-1604 "most recent ingestedAt for this source tier"). Adding ancient sources cannot lower a tier's decay; adding same-weight sources increases pc (monotone). Anti-Sybil intact. `pc_t = log₂(N_t+1) × decay_t × (Σ_i base_pc(tier)·factor_i)/N_t`. Doc's S3.1/S6 monotonicity re-annotated as "uniform-weight addition only" (assessment/decay are time-varying by design). Added test: adding an ANCIENT same-tier source does not decrease pc. |
| C | Assessment factor gaming / k unpinned (P1) | `k = 1.0` pinned: each assessor term (rep−0.5)(score−0.5) ∈ [−0.25,+0.25]; single assessor swing ±0.25 → factor [0.75,1.25]; clamps [0.1,2.0] require ~4+ assessors. Reputation **snapshotted at write time** (stored in reliabilityComponents). `outdated` assessments filtered from aggregation. Bounds + flooding tests added. Ownership/conflict checks: documented follow-up. |
| D | Registry defaults auto-tier production graph (P1) | `SOURCE_KIND_DEFAULTS` ships **T0–T4 identity keys + explicit None for ALL legacy/generic kinds** (`document`, `github_issue`, `slack_message`, `linear_card`). Legacy kinds stay neutral until explicitly registered via `register_source_kind_default()`. Task 3 test renamed: "activation via register_source_kind_default()". No auto-retiering on upgrade. |
| E | `pc_base` contract ambiguous (P1, codebase) | **`pc_base(tier) := alpha − 1`** (excess over neutral). Formula contract comment in `source_credibility.py`; Task 1 N=1 identity test asserts `aggregate_prior(tier, 1) == TIER_PRIORS[tier]` exactly. |
| F | Epsilon guard can't suppress decay writes (P2) | **Time-gated recompute**: inherited baselines recomputed at most once per `recompute_interval` (default 3600s; env `TORTOISE_EP_REINHERIT_INTERVAL`; param `recompute_interval` on `_apply_source_inheritance`, 0 = always). Bounds writes (≤24/day/point), deterministic within the interval, no dirty churn. Test: two calls within interval → no rewrite; interval=0 → recompute. |
| G | Assessment points need exclusion filters (P2) | Assessments are **property-linked** (`targetSource`) — NOT `extractedFrom` (avoids self-reference). `_apply_source_inheritance` and `calibrate_summary` suggestion pass add `WHERE n.pointKind <> 'assessment'`. Gate already skips non-evidence kinds. |
| H | MCP auth allowlist missed (P2) | Task 6 adds `mcp_auth.py HTTP_ALLOWED` rows + ToolAnnotations (tortoise_get_source_reliability = readOnlyHint; tortoise_assess_source = destructiveHint). |
| I | Ontology §3.4/§5 not covered + version bump (P2) | Task 7 extends to §3.4 parenthetical, §5 Source Kind Vocabulary (tier semantics moved to credibilityTier), §2→§5 ref fix, and **version bump v3.1 → v3.2 + changelog line** for the vocabulary change. |
| J | test_calibration.py Docker-bound (P2) | Task 7 migrates its fixture to embedded `TortoiseSDK(db_path)` (test_event_provenance pattern) so fixed assertions run Docker-free. |
| K | Reliability invariant float drift (P3) | Invariant tests use `pytest.approx(rel=1e-6)`; cache documented to lag point prior by drift threshold. |
| L | calibrate_summary effective-tier + suggestion text (P2) | calibrate_summary resolves EFFECTIVE tier (`resolve_tier`) per source; "untiered" only when all precedence paths neutral; suggestion text → `set_source_tier(url, tier)` / `create_source(url, tier=)`; tortoise_calibrate_summary docstring updated. |
| M | NAND real-path behavior untested (P2) | Task 3 adds an OWN test: two points (T0-sourced, T4-sourced) connected by a NAND operator → assert net confidence; document that EP-factor contradiction may differ from S8 prior-level values; pin measured tolerance. |
| N | sourceDate inert (P2) | `create_source` gains `sourceDate=` param (via **props passthrough + documented); decay keys sourceDate else ingestedAt. |
| O | Cache invalidation (P2) | `set_source_tier` / `assess_source` / `create_source(tier=)` refresh the reliability cache synchronously AND **dirty-mark the recompute gate timestamp** (same write events), so event-driven recomputes always land while pure time-churn (decay drift) stays gated. Task 3/4 tests either bypass the gate (`recompute_interval=0`) or compare against `_compute_source_prior()` directly. Test: reliability changes after tier change + after new assessment. |
| P | Multi-tenant registry scope (P2) | Registry = module-level defaults; documented that `register_source_kind_default()` is process-wide (hosted multi-tenant: acceptable for v1, per-namespace scoping = follow-up). |
| Q | Assessment points float w/o edge (P2) | Property-linked, excluded from calibrate/stats/inheritance; query surface documented (index on targetSource); outdated-assessment retention = follow-up. |
| R | Search facet mixing (P3) | Tier-form `sourceKind` allowed (canonical per ontology); facet on `sourceKind` filters coherently per value; documented. |
| S | audit.py remediation text (P3) | Task 7 updates `missing_sourceKind` fix text → `set_source_tier` / `create_source(tier=)`; note Point-level sourceKind tier annotations are legacy. |
| T | Batched assessment query (P3) | `_compute_source_prior` aggregates assessments in ONE batched query (GROUP BY targetSource, filter outdated). |
| U | MCP trimmable (P3) | Keep minimal MCP (2 tools + create_source tier passthrough); SDK-first. |

### Rejected in cycle 2
- **Per-source mean decay** (re-verifier R9): destroys anti-Sybil + monotonicity. Replaced by tier-most-recent decay (B).
- **Migration/backfill of legacy baselines**: violates no-migration constraint; production never ran inheritance. Advisory-only (A).
- **Legacy kind defaults** (github_issue→T2 etc.): the forbidden auto-mapping under a new name. Identity + None only (D).
- **NAND-negative-pc in inheritance**: double-counts EP factor (unchanged from cycle 1).
