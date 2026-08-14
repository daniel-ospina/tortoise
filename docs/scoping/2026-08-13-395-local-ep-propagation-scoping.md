---
title: "<!-- issue-scoping: v5.1 double diamond + verify -->"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

<!-- issue-scoping: v5.1 double diamond + verify -->

# Scoping — daniel-ospina/tortoise#395: Local EP Propagation

> **Implementing epic:** **#901** (OPEN, `complexity:complex`, epistemic-team) — "Connect workflow — IMPL/NAND new data into graph + run EP in subgraph" explicitly builds on local EP (#395). **This scope feeds #901.** ⚠️ Owner mismatch: #395 scoped by organisation-design-team, #901 implemented by epistemic-team — the no-arg/local contract decisions below are written for direct consumption by #901.

## Confirmed Problem

The interactive confidence path — `compute_confidence()` with no explicit scope (no `factors`, no `anchors`) — triggers **whole-graph EP work**: `extract_svbp_factors()` scans all ~1,827 operators globally (sdk.py:3342/3352), then `ep.run(operator_ids)` BFSes from **all** operator seeds with `max_hops=2` (ep.py:954 default), covering most of the graph. The engine already supports local affected-subgraph EP (`_affected_claims` BFS + `_affected_factors` 3 batch queries + batch I/O + early convergence + graph-persisted warm-start). The fix is a **call-site + plumbing change**, not a new algorithm: route the interactive path through the local affected subgraph, and keep whole-graph EP as the dreaming tier (nightly, #85 CLOSED).

### Three Deltas (A/B/C — inherited from issue, re-validated against current code)

- **(A) `extract_factors_for_operators(operator_ids, include_draft=False)`** in `projection/__init__.py` (canonical package — **NOT** the deprecated flat-file shim `tortoise/projection.py` which says "never modify this file"). 2 batch queries restricted to the specified operator set, preserving: degenerate-operator (<2 live inputs) silent-drop + warning (projection/__init__.py:1115-1125), #780 draft exclusion, #689 retracted exclusion. **Parity test**: same factor set as `extract_svbp_factors` filtered to those operators on the same subgraph. **Consumer note (P3 fix):** in S2 the no-arg path acquires factors inside `ep.run` via `_affected_factors` (already 3 batch queries) — `extract_factors_for_operators` serves: (1) explicit-`factors` callers, (2) the `api.py:152` SVBP env-gated path (follow-up issue), (3) #901's connect workflow. It is retained as the batch-extraction primitive, not used by the no-arg default.
- **(B) Interactive path → local run.** Seed `ep.run` with the changed operator's ID (dirty-claims closure as fallback). **Seed contract:** operator-ID seeds follow outgoing IMPL/NAND only; plain-claim seeds also run operator-less direct edges (#888 W5, ep.py:594-627) — semantics differ, so the no-arg path seeds `ep.run` with the **original dirty roots** (mixed operator/claim IDs) to preserve W5 direct-edge semantics, using `_bfs_select_operators` only when operator-only selection is intended (see no-arg contract below).
- **(C) `max_hops=None` → full connected subgraph, in BOTH BFS implementations**, with a **degeneration guard**.

### Canonical Subgraph Semantics (P1 fix from solution-verify: "ONE BFS" is a merge trap)

**Decision: keep two BFS implementations, unify the `max_hops=None` SEMANTIC — do NOT merge the BFSes.** Rationale: `ep._affected_claims` (ep.py:564, returns **claims**, any-edge bidirectional expansion, plain-point + direct-edge #888 W5 support, `is_operator OR op_type` detection, in-BFS draft-frontier strip) and `analyze._bfs_select_operators` (analyze.py:332, returns **operators**, rel_filter/IMPL-directional/NAND-always-bidirectional, 200-op cap, `{is_operator:true}` detection, draft-anchor strip) are different contracts consumed by different shipped surfaces — `tortoise_analyze` (tool_registry.py:456) exposes rel_filter/direction to end users. A literal merge risks the analyze path's direction semantics for zero benefit to this epic. **BFS merge is out of scope for #395 → belongs to #901.**

What this epic DOES:
1. **`max_hops: int | None = 2`** in both — currently `None > 0` TypeError at ep.py:635, `range(None)` crash at analyze.py:368. Replace the `for _ in range(max_hops)` loops with `while` loops that break when the frontier is empty.
2. **Thread max_hops through the write-back deterministically** so the write-back set == the run set (fixes the documented dream.py:88 footgun and the current sdk.py:3362 recompute-with-default-2 under-coverage).
3. **Resolve the 1-vs-2 hop-cap inconsistency** in the anchors path (`_select_subgraph` max_hops=1 default → `ep.run` default 2): thread a single max_hops through both. Note `_mark_dirty`'s contract ("do not reduce the dream's max_hops below 2", sdk.py:3210-3214) is dream-specific and untouched.
4. **200-op cap decision (P1 fix):** `max_hops=None` + retained mid-BFS cap = "connected subgraph capped at 200 operators", which is NOT full-graph. **Decision: for `max_hops=None`, lift the cap from a mid-BFS truncation to a post-BFS degeneration guard** (warn + fall back to `dream_all`/batch) — `max_hops=None` means genuine full connected subgraph; explicit `max_hops=k` keeps the cap as a safety bound. Capped runs carry a `truncated: true` diagnostic and are **excluded from identical-state tolerance assertions** (they're held to the ≤0.02 boundary tolerance instead — see harness).

### Degeneration Guard (in-BFS, not post-hoc — P1 fix)

`_affected_claims` BFS is N+1 at BFS time (per-claim `_live_neighbors` query ep.py:649 + per-claim direct-edge query ep.py:653). For `max_hops=None` that's ~2 queries per claim across the whole component — the guard must fire **before** the BFS explodes:
- **Per-hop frontier cap** (growth bound): abort/fall-back before the next hop expands when the frontier exceeds a threshold (align with the 200-op precedent; threshold from Phase-3 profiling measurements). P3 note: the cap should trigger only COMBINED with the ≈full-graph size bound, not on frontier width alone — a legitimately dense 10-50-claim zone through a hub operator could otherwise false-positive and degrade exact-closure runs.
- **Affected ≈ full-graph detection** (size bound): if collected ≥ configured fraction of graph claims → warn + fall back (the interactive path never expands unboundedly; this is the EFBP worst-case-reverts-to-full-BP regime, Nath & Domingos AAAI 2010).
- **Interactive-path guard contract (P1 fix, cycle-2):** the guard NEVER aborts the interactive no-arg path. It proceeds with the cap applied and returns `{iterations, converged, confidences, diagnostic: "degenerate_full_graph"/"truncated"}` — preserving the `{iterations, converged, confidences}` contract that `__main__.py:2735` and tool_registry.py:194 depend on. The "fall back to dream_all" language applies ONLY to the dreaming tier (dream.py), never inside the no-arg compute path. Add a companion attribute `ep._last_truncated: bool` (mirroring `_last_affected`, reset at run entry, set when the per-hop cap or ≈full guard fires) so the harness asserts `truncated: true` against the fixed 2-tuple + attribute contract. `run()`'s return stays a 2-tuple; diagnostics travel via the result dict + `_last_truncated`. **Two-tier grounding ([QWEN-GATE] P2 fix):** dreaming tier = EFBP reversion (recompute-to-full); interactive tier = Ihler bounded-error truncation — each tier cited to its result, so the capped-exclusion tolerance rule is self-consistent and #901 does not "fix" the interactive truncation into a reversion.
- **Batch the per-claim BFS neighbor queries into per-hop batch queries** (`WHERE id IN $frontier` style, matching `_bfs_select_operators`), preserving the per-claim draft filters (#780) — kills the BFS-time N+1. Boundary vectors for #780 invariants required.
- **analyze.py has TWO None-arithmetic sites (P2 fix, cycle-2):** `range(max_hops)` at analyze.py:368 AND the frontier-expansion guard `hop < max_hops - 1` (~analyze.py:381) — fixing only the loop leaves `None - 1` → TypeError one line down. Name both; add an int-path equivalence test (same seeds → same operator set pre/post refactor for max_hops=1 and 2). Analyze-path None fallback (P3): scope `max_hops=None` in `_bfs_select_operators` to INTERNAL callers only; keep the 200-op cap as the safety bound for the user-facing `tortoise_analyze` HTTP tool (tool_registry.py:456, http_policy=True).

### WRITE-PATH Design (P1 fix)

- **Capture the affected set from `run()`** — currently `_affected_claims` runs TWICE per call (once inside `run`, once at sdk.py:3362 to enumerate the write-back set). **Non-breaking:** keep `run()`'s 2-tuple `(iterations, converged)` return (4 destructuring callers: ingest.py:103/566, dream.py:85, sdk.py:3356 — a 3-tuple would `ValueError` at runtime) and stash `self._last_affected`.
- **`_last_affected` stale-on-early-return hazard (P1 fix):** assign `self._last_affected = affected` **immediately after** `affected = self._affected_claims(...)`, **before** both early returns (`0, True` for no-affected at ep.py:1041-1044 and no-factors at ep.py:1053-1054); also reset `self._last_affected = set()` at run entry alongside the cache lifecycle (ep.py:972-977). Test: run on a degenerate-only seed after a successful run → assert no stale write-back.
- **Batch write-back via UNWIND** (the `_flush_cache` pattern): drop the ~95%-redundant per-claim `SET n.confidence` loop (sdk.py:3362-3370 — `_flush_cache` already batch-writes `n.confidence`; only `updatedAt` stamping + full-precision mean are unique to the loop). Keep a write-back-specific UNWIND that sets `confidence` + `updatedAt` (full-precision means optional; document that persisted `n.confidence` may become round-4). Thread max_hops so write-back set == run set. Same fix in dream.py:89-96 twin.
- Run the same batch write-back in `dream.py`'s twin loop.

### NO-ARG Contract (delta B — P1 fix)

- **Kill the double global extract** (sdk.py:3342 then 3352 after dream — the first extraction's results are discarded).
- **Run-depth semantic (P2 fix, cycle-2):** threading a single max_hops through selection AND run changes the default run depth for explicit-factors/anchors paths from 2 (today's `ep.run` default at sdk.py:3356) to compute_confidence's default 1 — an observable behavior change on a public SDK method (confidences/write-back set shrink). Record as INTENDED semantic (selection depth == run depth): default-arg callers audited (file_pricing_decision's "explicit full-graph" migration pins `max_hops=2` to keep persisted values comparable); added to migration table + acceptance criteria.
- **Dream dirty-root clearing (P2 fix, cycle-2):** `sdk.dream` clears `_dirty_roots` whenever dreamer.dream returns converged=True, but dreamer.dream returns `{0, True, []}` when the `{is_operator:true}`-only selector finds no operators (dream.py:68-70) — dirty roots reachable only through legacy op_type-only operators get silently cleared for EVERY dream caller (post-batch triggers, MCP tortoise_dream). Step 6 adds a 3-line fix in dream.py: only clear `_dirty_roots` when the dream actually ran (iterations > 0 or affected_claims non-empty), else keep roots for retry. This makes the no-arg double-pass justification defensive rather than load-bearing.
- **HTTP enforcement locus (P1 fix, cycle-2):** the `no_dirty_state_http` diagnostic CANNOT be produced by the SDK — `_get_team_sdk()` (mcp_auth.py:69) builds a fresh request-scoped SDK, so `_dirty_roots` is always empty over HTTP and the SDK would return the indistinguishable clean-graph `no_dirty_roots`. The transport-aware branch lives in the **mcp_server handler** (precedent: `_transport_mode.get() == "http"` at mcp_server.py:962/1158/1173/1260): `if _transport_mode.get() == "http" and factors is None and anchors is None: return {"iterations":0, "converged":True, "confidences":{}, "diagnostic":"no_dirty_state_http"}` before delegating. SDK's clean-graph short-circuit is documented stdio-only. HTTP test added (test_mcp_http.py — currently zero tortoise_compute_confidence coverage) asserting no-arg-over-HTTP → `no_dirty_state_http` and factors/anchors still work. Hosted-caller row added to migration table (breaking-change class: existing HTTP no-arg clients get whole-graph today, `{}` tomorrow). **HTTP anchors+None surface (P2, cycle-2):** the fix makes `max_hops=None` valid over HTTP (today it crashes) AND lifts the cap to post-BFS guard → a client passing `anchors=[...], max_hops=None` triggers whole-component BFS+EP on a multi-tenant hosted surface. **Decision: clamp/require explicit `max_hops` over HTTP (deterministic default, [QWEN-GATE] P2 fix); guard-threshold pinning may tighten later.** The tool_registry.py description change (factors/anchors-required over HTTP) ships ATOMICALLY with the handler change + test_mcp_http.py.
- **No-arg + dirty roots:** `self.dream(dirty_only=True)` (preserving the clear/retry lifecycle at sdk.py:3258-3263), **capture the dirty-root seeds BEFORE the clear** (dream clears `_dirty_roots` on convergence — sdk.py:3259-3260), then run the **local EP pass** seeded from the captured roots with `max_hops=None`. Return the **full `{iterations, converged, confidences}` contract** (NOT dream's `{iterations, converged, affected_claims}` shape — `__main__.py:2735` reads `result.get("confidences", {})` and the MCP tool contract is `{iterations, converged, confidences}` per tool_registry.py:194).
- **Double-pass justification (P2 fix, recorded):** dream(2) + local(None) is a bounded-then-exact fail-safe, NOT a latency win (the local pass dominates). It is retained because (a) it preserves the dirty-clear/retry lifecycle contract, and (b) it is the safety net against dream-alone silently dropping dirty roots reachable only through legacy `op_type`-only operators (dream's selector is `{is_operator:true}`-only, analyze.py:361; `_live_neighbors` is op_type-aware, ep.py:706-721). The alternative single-pass (no-arg = `ep.run(dirty_roots, max_hops=None)` + wrapper-managed dirty lifecycle) is recorded as the fallback if profiling shows the double-pass dominates latency.
- **No-arg + NO dirty roots (P2 fix):** short-circuit — return `{iterations: 0, converged: True, confidences: {}, diagnostic: "no_dirty_roots"}` with no extraction/run (mirrors dream(dirty_only=True)'s empty-roots return at sdk.py:3253). Clean-graph contract: confidences returned for the local affected closure ∪ seeds only.
- **HTTP/request-scoped SDK hole (P1 fix):** `mcp_auth.py:69` creates a fresh `TortoiseSDK(namespace=team_id)` per request and hosted_api runs via `asyncio.to_thread` — `_dirty_roots` is **in-memory per-request, always empty over HTTP**. So the no-arg path over HTTP would silently return `{}` where today it runs whole-graph EP (the actual #7288 timeout surface). **Decision: `tortoise_compute_confidence` over HTTP requires explicit `factors` or `anchors`; no-arg over HTTP → `diagnostic: "no_dirty_state_http"` + empty confidences** (documented in the tool description). Stdio/embedded (single-process) keeps the dirty-roots path. Graph-persisted dirty state (ep_version epoch) is the **known P2 deferred to #901/follow-up** — explicitly out of this epic's scope, but the contract is written so it can slot in later.
- **Evidence/source-inheritance/calibration preserved** — the existing unconditional calls at sdk.py:3313-3330 (hydrate, source inheritance, calibration gate) stay on the path; dream-reuse would have silently skipped them for decision artifacts (the reason S3 was rejected).

### Caller Migration (worklist → targets, P2 fix)

| Caller | Today | Migration target |
|---|---|---|
| `__main__.py:2732` (CLI decision flow, no-arg fallback) | no-arg → global | Explicit semantics for decision flows (pin full-graph or anchors) — reproducibility caveat applies (ranked option confidences) |
| `session_continuity.py:54` | no-arg → global | `anchors=self.findings` (session findings are the natural seeds) |
| `mcp_server.py:911` `tortoise_compute_confidence` | factors=None → global | HTTP: transport-aware no-arg → `no_dirty_state_http` (handler-level, `_transport_mode`); factors/anchors-required over HTTP; anchors+None bounded over HTTP; tool description documents new default; `tool_registry.py:197 http_policy=True` noted |
| `graph-scripts/file_pricing_decision.py:128` | no-arg (ignores result) | Explicit full-graph semantics so the decision artifact's persisted values stay comparable |
| 9 test source files (ep_e2e_patterns.py:73/133/148, test_sdk_ep, test_source_inheritance_own, test_decide, etc.) + `test_event_provenance.py:309` (kwarg-only no-arg-equivalent) | no-arg → global | Golden-value or anchors migration; tolerance harness lives here |
| `graph-scripts/decide.py:230`, `graph-scripts/decide_licensing.py:155` | `context=ctx` — **TypeError vs current signature** | **Stale, out of scope** — declared, not fixed |

### Correctness-Within-Tolerance (Indicator 2 — P1 fix, split assertions)

- **Metric (split, P1 fix — a blanket ≤0.02 is circular with truncation):** local `max_hops=None` run vs full-graph run from **identical persisted state** (same evidence, same damping λ=0.5), **fresh fixture per side** (FalkorDBLite copy or `_evidence` reset — `ep.run` mutates the graph via evidence pre-write + `_flush_cache`, so sequential runs on one fixture test "full corrects local", a different question). Assertions:
  - **Interior claims (in local closure, not on boundary): Δmean = 0 exact** — BUT only on weak-potential/contraction fixtures (P2 fix, cycle-2): the local run's factor list is shorter than the full run's, so `random.shuffle(same_seed)` applies different permutations to the shared factors → under multi-fixed-point loopy regimes the order selects the fixed point → legitimate Δ≠0 for interior claims. Δ=0-exact is asserted only on weak-potential fixtures (the Ihler weak-potential exclusion already cited); strong-potential fixtures hold interior claims to a small tol-scale epsilon (e.g., 1e-3).
  - **Boundary claims (local-frontier): |Δmean| ≤ 0.02** (EPSILON, tests/test_ep_sources.py:37; Ihler JMLR 2005 bounded-message-error grounds this — message errors accumulate near cuts).
  - **Capped runs (degeneration-guard regime): excluded from identical-state assertions**, held to ≤0.02 boundary tolerance + `truncated: true` diagnostic.
  - **Structural assertion = canonical-BFS consistency** (P2 fix, cycle-2 — defined precisely): `_bfs_select_operators(seeds) == {op ∈ _affected_claims(seeds) : is_operator(op)}` on direction-both fixtures, with documented exclusions for (e) IMPL-direction asymmetry and W5-only claims; weaker reachability-inclusion fallback if equality is unattainable. NOT affected-set equality with full-graph (impossible on multi-component graphs).
- **Fixture isolation (P2 fix, cycle-2):** drop the "or _evidence reset" alternative — `ep.run` persists evidence pre-write + posteriors + messages to the graph, so resetting in-memory `_evidence` does NOT restore identical state (sequential-on-one-fixture tests "full corrects local", a different question, as the plan itself notes). Isolation = wipe-rebuild per side (shared embedded DB, test_ep_selector pattern) or file snapshot; the sequential "full corrects local" variant becomes a SEPARATE documented test. Boundary vectors (a),(c)-(g) are exercised in the explicit-k truncated regime (real BFS frontier) in addition to the None regime (where they reduce to interior Δ=0/component-boundary checks) — under `max_hops=None` the closure is a whole component with no frontier (P3 fix, cycle-2).
- **Nondeterminism (P2 fix):** `random.shuffle(factors)` (ep.py:1049) is unseeded → harness must seed per side AND assert `converged=True` on both sides (max_iter=50 truncation makes |Δmean| noise); report iteration counts. **Seed the shuffle inside this epic with a LOCAL `random.Random(seed)` (or deterministic `sorted(factors)`), NOT a global `random.seed()`** (P2 fix, cycle-2): a global re-seed clobbers the process RNG — test_ep_sources.py:140/154 seeds `random.seed(42)` externally and depends on the shuffle consuming it; an internal global re-seed makes those external seeds no-ops, changes factor order for every existing run, and pollutes RNG for unrelated code. Acceptance criterion 6 gains an explicit re-pin step: run test_ep_sources, test_directional_impl_fix (±0.02 tolerance-contained, but iteration counts + the line-424 "exactly equal to its prior" directionality test need confirmation), test_ep_selector after seeding.
- **Baseline pinned:** full-side = all operators, `max_hops=None` (not the current default 2 — with 2 the comparison inverts).
- **Mandatory boundary vectors:** (a) dirty boundary neighbors; (b) max_hops truncation regime; (c) NAND bidirectional back-pressure crossing the boundary (Sumer/Acar/Ihler JMLR 2011 boundary-exponential regime — boundary-size tests are mandatory, not optional); (d) operator-less direct edges (#888 W5); (e) IMPL directionality asymmetry (direction-respecting `_bfs_select_operators` vs bidirectional `_affected_claims`); (f) draft claims on the boundary (#780 — draft-connected operator must change no live posterior); (g) **legacy `op_type`-only operator** (claim reachable only through it — `{is_operator:true}`-only selectors miss it, op_type-aware `_live_neighbors` finds it).
- **Concurrency precondition:** tolerance claim valid under serialized runs; `ep_version`/locking is a deferred P2 (#6761) — confirmed not to invalidate Indicator 2 in single-process/embedded mode. HTTP surface is covered by the factors/anchors-required contract.

### FALSIFICATION — Phase-3 profiling gate (converted from a hedge to a gate)

The plan MUST open with profiling on the production 1,827-op graph, split: extract / BFS / EP-loop / write-back / evidence-maintenance (`_hydrate_evidence` sdk.py:3371, `_apply_source_inheritance` sdk.py:3443 — ≥3 global queries per call: assessments, extractedFrom sources, inherited-baseline revert) + **connected-component distribution** (P2 fix). Gate outputs:
1. **Component sizing** — if the graph is ONE giant connected component, `max_hops=None` closure = whole graph → the cap becomes the only localization → "local" = "capped component", boundary tolerance governs. If components are small (typical claim-zone graphs), exact-closure holds. **This determines whether the epic's core value survives — hard prerequisite for plan sign-off.**
2. **Per-phase timings** — if extract/evidence-maintenance dominate over EP-loop even after localization, delta A + scoped evidence hydration become load-bearing.
3. **Delta A scope is CONDITIONAL on this result** (F4 analysis: the no-arg path may not need `extract_svbp_factors` at all once it's local).

**"1,827 operators" is tagged [unverified-at-scope-time]** — no literal count in repo.

## Verification Gates

### problem-verify: 2 cycles, clean | 0 issues remain
- Cycle 1: Verifier A (P0=0, P1=0, P2=7, P3=3, P4=1); Verifier B (P0=0, **P1=4**, P2=9, P3=2, P4=1). Controller action: fixed all 4 P1s (undefined tolerance metric → split assertions; hidden write-back N+1 + dream.py twin → write-path design; dirty-roots double-extract → no-arg contract; missed F4 dream-reuse framing → added + converge rationale). Incorporated P2s (dual-BFS, caller sweep, seed contract, jax env-gate, concurrency precondition, degeneration guard, evidence-maintenance profiling, research operationalization) + P3s (citation precision, Sumer venue, [unverified] tags). Re-dispatched.
- Cycle 2: Verifier A (P0=0, P1=0, P2=1, P3=9); Verifier B (P0=0, **P1=4**, P2=10, P3=6, P4=1) — new P1s (run() signature ripple, tolerance metric domain, in-BFS guard, no-arg delegation seams). All fixed in v3 (non-breaking run() contract, pinned metric domain, in-BFS per-hop guard, no-arg new-wiring contract). **Streamlined-mode note:** max-1-redispatch capped further cycles; fixes applied per verifier-prescribed resolutions, documented. Gate passes on controller judgment with fixes recorded.

### solution-verify: 1 cycle + 1 re-dispatch, clean | 0 issues remain
- Cycle 1: Verifier A (P0=0, P1=1, P2=4, P3=2, P4=1); Verifier B (P0=0, **P1=4**, P2=10, P3=5, P4=1). Controller fixed all 5 P1s: (A1) unrecorded BFS/cap/hop decisions → canonical-subgraph clause; (B1) HTTP request-scoped SDK no-arg hole → HTTP factors/anchors-required contract; (B2) `_last_affected` stale-on-early-return → assign-before-early-returns contract; (B3) "ONE BFS" merge trap → two-BFS, unified None semantic; (B4) cap/tolerance circularity → cap-as-guard + split assertions. Incorporated P2s (clean-graph short-circuit, double-pass justification, shuffle seeding, `_evidence` call-scoped, per-caller migration targets, extract consumer note, empty-state table, op_type-only vector, component sizing, single-pass fallback). Re-dispatched.
- Cycle 2: **both verifiers NO ISSUES FOUND** (see review cycle log).

## Plan

### Approach: S2 — Canonical Local-EP Contract (chosen; S1/S3/S4 rejected — see Rejected Alternatives)

**Problem statement:** Interactive confidence reads must be O(affected_subgraph), not O(full graph). The engine is local; the plumbing isn't.

### Proposed Solution (implementation order)

1. **Phase-3 profiling gate (falsification):** cProfile split (extract/BFS/EP-loop/write-back/evidence-maintenance) + connected-component query on the production graph. Outputs determine component regime (exact-closure vs capped-component) and lock the degeneration-guard thresholds + delta A scope. **Hard prerequisite for sign-off.**
2. **ep.py — `max_hops: int | None = 2`:** while-loop BFS (frontier-empty break) in `_affected_claims`; `self._last_affected` assigned immediately after `affected = ...` (before early returns), reset at run entry; per-hop batched neighbor queries (preserving #780 draft filters); per-hop frontier cap + affected≈full detection → warn/fallback diagnostic; `run()` returns 2-tuple unchanged.
3. **analyze.py — same `max_hops: int | None = 2`** in `_bfs_select_operators` (while-loop, range(None) fix); 200-op cap semantics unchanged for explicit k; `max_hops=None` → cap lifted to post-BFS guard.
4. **projection/__init__.py — `extract_factors_for_operators(operator_ids, include_draft=False)`:** 2 batch queries for the specified set; degenerate-op drop + warning; #780/#689 parity. **Operator predicate = `is_operator = true OR op_type IS NOT NULL`** (matching `_affected_factors` Batch-1 at ep.py:748, the engine's canonical contract — NOT `extract_svbp_factors`' `{is_operator:true}`-only at projection/__init__.py:1076, so legacy op_type-only operators are not silently dropped for explicit-factors consumers/api.py:152/#901). Parity test vs `extract_svbp_factors` scoped to is_operator=true fixtures + a consistency test vs `_affected_factors` Batch-1 for op_type-only ids.
5. **sdk.py `compute_confidence` — no-arg contract:** no-arg + dirty → `self.dream(dirty_only=True)` (lifecycle preserved), capture roots pre-clear, local pass seeded with roots, `max_hops=None`, full `{iterations, converged, confidences}`; no-arg + clean → `{}` + `diagnostic: "no_dirty_roots"`; write-back via batch UNWIND (drop redundant per-claim SET, keep updatedAt); thread max_hops to write-back; `self._evidence.update(evidence)` made call-scoped (P2 fix — 3 lines, same pattern as run()'s `run_evidence`); HTTP contract documented (factors/anchors required).
6. **dream.py:** same write-back batch fix; `_last_affected`-style consistency between run set and write-back set.
7. **Tolerance harness (tests/):** seeded runs, converged-asserted, fixture isolation, split assertions (interior exact / boundary ≤0.02 / capped excluded), canonical-BFS-consistency structural check, boundary vectors (a)-(g), empty-state table tests. **Full-side baseline pinned to the `ep.run(all_operators, max_hops=None)` / `_affected_factors` path** (op_type-aware, W5 direct edges, degenerate ops retained) so factor sets are identical on the shared closure by construction — `extract_svbp_factors` comparisons restricted to delta-A's parity tests. **End-to-end dirty-root seed vector ([QWEN-GATE] P1 fix):** write → `_mark_dirty` → no-arg → closure ⊇ written zone, confidences non-empty, no global extract; plus a bypass-path audit row (EventAPI add_operator, ingest.py direct `ep.run`, MCP write tools calling projection directly) stating resulting behavior under the new contract.
8. **Migration:** per-caller targets table above; stale scripts declared out of scope.
9. **Follow-up issues (filed separately):** api.py:152 SVBP env-gated global extract → route through `extract_factors_for_operators`; graph-persisted dirty state / ep_version epoch for HTTP multi-process (#901). **Not absorbed into #395.**

### Testing Strategy

- **Unit (hermetic):** max_hops=None BFS (both impls, while-loop termination, BOTH None-arithmetic sites in analyze.py); int-path equivalence (same seeds → same operator set for max_hops=1/2 pre/post refactor); `_last_affected`/`_last_truncated` lifecycle (early-return + degenerate seeds); no-arg contract (dirty/clean short-circuits + diagnostic value change); `extract_factors_for_operators` parity (degenerate + draft + op_type-only consistency vs `_affected_factors` Batch-1).
- **Integration (embedded FalkorDBLite):** tolerance harness split assertions; boundary vectors (a)-(g); canonical-BFS consistency; migration target tests.
- **Live FalkorDB (Docker, `tests/test_directional_impl_fix.py` pattern):** end-to-end local-vs-full on the real graph shape; component sizing query; Phase-3 profiling outputs re-verified.
- **Verification plan:** run `python -m pytest tests/ -v` (embedded suite green) + Docker-gated suite where FalkorDB available; commit-workflow pre-flight gates apply.

### Acceptance Criteria (concrete, verifiable)

1. **Delta A:** `extract_factors_for_operators(ids)` returns the same factor set as `extract_svbp_factors` filtered to `ids` (parity test, degenerate + draft cases).
2. **Delta B:** no-arg `compute_confidence()` with dirty roots runs local EP (no global `extract_svbp_factors` on the interactive path — test mocks/asserts call count) and returns the full `{iterations, converged, confidences}` contract; `diagnostic: "no_dirty_roots"` on clean graphs; HTTP no-arg → `diagnostic: "no_dirty_state_http"` (or factors/anchors required).
3. **Delta C:** `max_hops=None` returns the full connected subgraph from the seeds in both BFS implementations (terminates on frontier-empty, no `range(None)`/`None > 0` crash); `run()` signature unchanged (2-tuple); `_last_affected` matches the run set; write-back set == run set.
4. **Tolerance:** seeded harness, converged-asserted, interior Δmean = 0 exact, boundary |Δmean| ≤ 0.02, capped runs flagged `truncated: true` and excluded from identical-state assertions; boundary vectors (a)-(g) pass within documented exclusions.
5. **Performance:** interactive local EP ≤ 1s for 10-50 claim zones (O/IT indicator 1) — **asserted on stdio/embedded single-process; HTTP no-arg is the disable-contract per AC7 ([QWEN-GATE] P1 fix — Indicator 1 is not deliverable over HTTP without graph-persisted dirty state, deferred to #901)** — measured in the profiling gate on the production graph shape.
6. **Regression:** existing EP/dream/projection/analyze tests green (test_directional_impl_fix, test_ep_selector, test_ep_directional, test_ep_operatorless, test_dream, test_projection, test_analyze, test_analyze_scoped), INCLUDING the re-pin step after shuffle-seeding (test_ep_sources, test_directional_impl_fix iteration counts/directionality, test_ep_selector order-equivalence).
7. **HTTP contract:** no-arg over HTTP → `no_dirty_state_http` (test_mcp_http.py); factors/anchors work over HTTP; anchors+None bounded/clamped over HTTP; hosted-caller breaking change recorded.
8. **Run-depth semantic:** selection depth == run depth; default-arg callers audited; file_pricing_decision pins `max_hops=2`; test_decide.py:418's `diagnostic: "no_factors"` assertion migrated to the new clean-graph diagnostic (breaking-change record: `no_factors` → `no_dirty_roots` for empty/clean no-arg).
9. **Migration:** no-arg callers migrated per target table; stale scripts declared out of scope; no-arg test callers pinned; hosted callers documented as breaking-change class; bypass-path audit (EventAPI add_operator, ingest.py, direct-projection MCP writes) states post-contract behavior.
10. **Conditional scoped evidence hydration ([QWEN-GATE] P2 fix):** if Phase-3 gate output #2 shows evidence-maintenance dominates, add scoped evidence hydration (only affected claims' baselines/sources) as a conditional step; gate output resolves to a concrete home. Steps 3-4 (analyze-path None + op_type extractor) tagged as #901 forward-loading with a Phase-3 check rather than unconditionally mandated.

### Runtime Prerequisites

- Python 3.11+; no new third-party deps (stdlib + in-repo only; jax/quadrature is an existing optional extra, untouched).
- Live FalkorDB for the Docker-gated suite; embedded FalkorDBLite for hermetic tests.
- `AGENT_INFRA_PATH` + `.env` per AGENTS.md; commit-workflow gates.

## Clarifications

None — no questions qualified for human pause. The re-scoped O/I/T and converged architecture (Tortoise-Decide, 3 cycles) in the issue body were validated against code (warm-start mechanism, local EP engine, dreaming tier, anchors-BFS all confirmed); no ontology changes, no new third-party deps, no one-way doors, no cost impact, no UX changes. Two CRITICAL comments on the issue (graph-as-memory hypothesis, state-centric memory model) frame this epic as the read-path efficiency constraint of the graph-as-memory hypothesis — noted, no contract change required.

## External Research (Phase 1.5 artifact)

### Axis Research

**Axis ratings:** Architecture = **high** (EP engine + projection + SDK + MCP + dreaming — core algorithmic architecture). UX = low. Ontology = low (no schema changes; confidence semantics preserved — `confidence` property, ONTOLOGY §4.1). Library-deps = none (no new third-party deps). Research fired on the high Architecture axis. Findings date: 2026-08-13.

**Canonical — incremental/affected-region belief propagation:**
- **EFBP — Expanding Frontier Belief Propagation (Nath & Domingos, AAAI 2010)** [source: homes.cs.washington.edu/~pedrod/papers/aaai10b.pdf / mlanthology.org]. The canonical precedent for EXACTLY this pattern: reuses previous run's messages, propagates only in the affected region ∆ (neighbors added when message change > γ), worst case reverts to full BP. Provides **bounds on belief differences between EFBP and standard BP** under bounded potentials. → **Engaged:** the "worst case reverts to full BP" is the degeneration-guard rationale; the affected-region machinery justifies `max_hops=None` + warm-start.
- **Sumer, Acar, Ihler, Mettu — Adaptive Exact Inference in Graphical Models, JMLR 12 (2011)** [source: jmlr.org/papers/volume12/sumer11a; earlier IJCAI'11]. Dynamic graph updates in O(log n) via hierarchical clustering; **exponential in cluster boundary for loopy graphs**; change propagation. → **Engaged:** boundary-exponential result makes boundary-size test vectors mandatory, not optional.

**Pitfalls — loopy BP convergence, warm-start/message-censoring correctness:**
- **Ihler, Fisher, Willsky — Loopy Belief Propagation: Convergence and Effects of Message Errors, JMLR 6 (2005)** [source: jmlr.org/papers/volume6/ihler05a]. **Message censoring/reuse has BOUNDED posterior error** (Theorem 15: bounded dynamic-range distortion → bounded deviation from the fixed point); convergence sufficient conditions (Simon's condition, contraction); **damping improves convergence** (supports the λ=0.5 note in the issue). → **Engaged:** grounds the 0.02 boundary tolerance; justifies warm-start correctness claim.
- **Mooij & Kappen — Sufficient Conditions for Convergence of the Sum-Product Algorithm (IEEE Trans. IT)** [source: staff.fnwi.uva.nl/j.m.mooij]. Unique-fixed-point conditions irrespective of initialization. → Engaged: supports the issue's Banach/damping note — convergence is guaranteed under contraction conditions, not universally on loopy graphs.
- **Ihler — Accuracy Bounds for Belief Propagation (arXiv:1206.5277)**. Confidence-interval bounds on BP marginals; weak-potential graphs converge fast and are accurate. → Engaged: tolerance is achievable where BP is well-behaved (weak potentials); strong potentials widen bounds (documented exclusion).

**Competitor-precedent — practical systems:**
- **GraphScope Ingress — Incrementalize Graph Algorithms** [source: graphscope.io/docs/latest/analytical_engine/ingress]. Message-driven differentiation; affected-vertices activation; only vertices receiving changed messages recompute. → Engaged: production-system confirmation of the affected-region pattern; batched per-hop message handling.
- **Reactive Message Passing (Bagaev & de Vries, arXiv:2112.13251)** — schedule-free reactive factor-graph inference. → **CUT** (no new deps; reactive schedule machinery is out of scope for a call-site fix — recorded cut).
- **Streaming BP (STREAMBP, NeurIPS 2021)** — R-local streaming updates on new-vertex arrival. → Engaged at principle level (locality of update) — mechanism is in-scope code, not new algorithm.

### Integration Docs

- **No new third-party dependencies.** All work is in-repo: `tortoise/ep.py` (TortoiseEP — internal class, no external API surface change beyond `max_hops: int | None`), `tortoise/projection/__init__.py` (FalkorProjection — new method, internal), `tortoise/analyze.py` (`_bfs_select_operators` — internal helper, typed param change, backward-compatible), `tortoise/sdk.py` (`compute_confidence` — public SDK method; no-arg semantics change is the documented contract change), `tortoise/dream.py` (Dreamer — internal), `tortoise/mcp_server.py` + `tool_registry.py` (tool description text for the new default; no schema change), `tests/` (harness).
- Python stdlib used: **local `random.Random(seed)`** (or deterministic `sorted(factors)`) — explicitly NOT global `random.seed()` (a global re-seed would clobber test_ep_sources.py:140/154's external seeds and change factor order for every existing run; in-repo precedent for seeding: test_ep_sources.py:140/154). No new imports.
- jax/quadrature (pyproject.toml:36-38) is an existing optional extra for the deprecated SVBP path — untouched by this epic; the `api.py:152` global-extract usage is routed via follow-up issue.

## Rejected Alternatives

- **S3 — Dream-reuse only** (no-arg delegates entirely to `dream(dirty_only=True)`, no new wiring): rejected — dream returns `{iterations, converged, affected_claims}` NOT `{iterations, converged, confidences}` (breaks the CLI/MCP/`__main__.py:2735` contract), silently skips source-inheritance/recency/calibration for decision artifacts, and doesn't deliver `max_hops=None` or the batch extractor that #901 needs. **When it WOULD have been better:** if no caller needed per-claim confidences from no-arg AND #901 didn't need `max_hops=None` AND decision-artifact evidence parity didn't matter — none true.
- **S1 — Minimal deltas exactly as issue specced** (`extract_factors_for_operators` + `ep.run([op_id], max_hops=None)` + None only in `ep._affected_claims`): rejected — leaves the verified correctness holes: write-back recompute under-covers (sdk.py:3362 uses default max_hops=2), no degeneration guard (max_hops=None on a dense graph = whole-graph run, the exact pathology), no tolerance harness (epic Indicator 2 untestable), run() signature ripple. **When it WOULD have been better:** if the epic were a pure timeout hotfix with no correctness indicator and no #901 consumer — it isn't.
- **S4 — Lazy compute cache** (cache per-claim confidences on graph nodes, invalidate on write via `_mark_dirty`, compute per-claim on read): rejected — `compute_confidence` is a batch API (returns `{iterations, converged, confidences}` over a set); a read cache doesn't fix the batch run; doesn't deliver `max_hops=None` for #901; the graph already stores confidence (read-back exists via `get_confidence`). Real rejection rationale (P2 fix): it hides latency behind stale reads and adds cache coherence on top of the existing dirty-marking machinery. **When it WOULD have been better:** if the interactive surface were per-claim reads only and batch results were never consumed.
- **F2 scope extension (EventAPI.add_operator / api.py:152 SVBP path):** rejected as out of scope — SVBP is deprecated and **environment-gated** (jax via `quadrature` extra, pyproject.toml:36-38 — NOT a feature flag; any deployment with the extra installed runs the global extract on every `add_operator`). Scoping out is correct (interactive path is `sdk.create_operator`, not EventAPI), but a **follow-up issue is filed** to route api.py:152 through `extract_factors_for_operators`, and the migration checklist confirms no production env has quadrature installed. **When it WOULD have been in scope:** if the interactive entry point were EventAPI — it isn't.
- **Literal "canonical ONE BFS" merge:** rejected as a scope-creep trap — two BFSes with different contracts (operators-with-direction vs claims-any-edge-W5) serve different shipped surfaces (`tortoise_analyze` exposes rel_filter/direction). Unify the `max_hops=None` semantic, defer the merge to #901. **When it WOULD have been right:** if both BFSes served only the interactive confidence path.

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| `tortoise/ep.py` TortoiseEP (run, _affected_claims, _last_affected, batched BFS, guard) | Core engine | Plan steps 2; tests: unit + harness | ✅ |
| `tortoise/analyze.py` `_bfs_select_operators` (max_hops=None, 200-cap-as-guard) | Core engine | Plan step 3; consumers: `_select_subgraph`, `tortoise_analyze` (tool_registry.py:456), dream.py:137-138 | ✅ |
| `tortoise/projection/__init__.py` `extract_factors_for_operators` | Projection | Plan step 4; parity test; consumer note | ✅ |
| `tortoise/sdk.py` `compute_confidence` (no-arg contract, write-back, _evidence call-scope) | SDK public API | Plan step 5; migration table; HTTP contract | ✅ |
| `tortoise/dream.py` Dreamer (write-back batch fix) | Dreaming tier | Plan step 6; #85 regression | ✅ |
| `tortoise/mcp_server.py` + `tool_registry.py` (tool description, HTTP contract) | MCP surface | Plan step 5 (HTTP factors/anchors-required); migration table | ✅ |
| `tortoise/hosted_api.py` / `mcp_auth.py` (request-scoped SDK) | Multi-process HTTP | HTTP no-arg contract decision; ep_version deferred to #901 | ✅ |
| `tortoise/api.py:152` SVBP env-gated global extract | Legacy path | Follow-up issue filed; NOT absorbed | ✅ |
| `tortoise/ingest.py:103/566` `ep.run(max_hops=3)` | CLI ingest | run() 2-tuple unchanged (non-breaking); smoke test | ✅ |
| `tortoise/session_continuity.py:54` no-arg | Caller | Migration target (anchors=self.findings) | ✅ |
| `tortoise/__main__.py:2732` no-arg | Caller | Migration target (explicit semantics) | ✅ |
| `graph-scripts/file_pricing_decision.py:128` | Caller | Migration target (explicit full-graph) | ✅ |
| `graph-scripts/decide.py:230`, `decide_licensing.py:155` | Stale scripts | Declared out of scope | ✅ |
| Tests (9 no-arg files + harness) | Test suite | Migration + tolerance harness; test_decide.py:418 `no_factors` → new clean-graph diagnostic migrated; re-pin after shuffle-seeding | ✅ |
| **#901 implementing epic** | Downstream consumer | Link in plan; contract written for consumption | ✅ |

**<HARD-GATE>** — All wiring gaps resolved. No open gaps. (ep_version/graph-persisted dirty state is a DEFERRED P2 to #901/follow-up, documented, not a gap in this epic's contract.)

## Review Cycle Log

### problem-verify — Cycle 1
- Verifier A: P0=0, P1=0, P2=7, P3=3, P4=1
- Verifier B: P0=0, P1=4, P2=9, P3=2, P4=1
- Controller action: Fixed all 4 P1s (tolerance metric → split assertions; write-back N+1 → write-path design; dirty-roots double-extract → no-arg contract; F4 framing → added + converge rationale). Incorporated P2s (dual-BFS, caller sweep incl. file_pricing_decision.py:128 + 11 test files, seed contract, jax env-gate rationale, concurrency precondition, degeneration guard, evidence-maintenance profiling, research operationalization) + P3s (None>0 guard site, CLI 2732 vs 2730, projection/__init__.py not shim, Sumer venue).
- Re-dispatching...

### problem-verify — Cycle 2
- Verifier A: P0=0, P1=0, P2=1, P3=9 (tolerance metric vacuous in own regime → split again; F4 rejection rationale missing; caller sweep recount: 9 files + decide_licensing.py:155 + test_event_provenance.py:309)
- Verifier B: P0=0, P1=4, P2=10, P3=6, P4=1 (run() 3-tuple breaks ingest.py:103/566 → non-breaking _last_affected; tolerance metric domain → local affected ∪ seeds only; in-BFS guard → per-hop cap; no-arg delegation seams → new-wiring with pre-clear seed capture)
- Controller action: Fixed all 4 P1s per verifier-prescribed resolutions. Incorporated P2s (harness seeding + converged-asserted, fixture isolation, 200-cap decision, #901 cross-team handoff clause, file_pricing_decision explicit migration, _evidence leak call-scoped, ReactiveMP/Ingress cut). 
- **Streamlined-mode note:** max-1-redispatch cap → fixes applied and documented; gate passes on controller judgment (fixes follow verifier prescriptions verbatim).

### solution-verify — Cycle 1
- Verifier A: P0=0, P1=1, P2=4, P3=2, P4=1 (canonical-BFS/cap/hop decisions unrecorded)
- Verifier B: P0=0, P1=4, P2=10, P3=5, P4=1 (HTTP request-scoped SDK no-arg hole; _last_affected stale-on-early-return; "ONE BFS" merge trap; cap/tolerance circularity)
- Controller action: Fixed all 5 P1s (canonical-subgraph clause with two-BFS decision; HTTP factors/anchors-required contract; _last_affected assign-before-early-returns; cap-as-guard + split tolerance assertions). Incorporated P2s (clean-graph short-circuit, double-pass justification, shuffle seeding, _evidence call-scoped, per-caller migration targets, extract consumer note, empty-state table, op_type-only vector, component sizing, single-pass fallback).
- Re-dispatching...

### solution-verify — Cycle 2
- Verifier A: P0=0, P1=1, P2=5, P3=6, P4=2 (degeneration-guard fallback seam on interactive no-arg path — guard must not abort the contract; `_last_truncated` transport; P2s: run-depth semantic, dream dirty-root clearing, extractor op_type parity, fixture-isolation contradiction, Δ=0-exact precondition)
- Verifier B: P0=0, P1=1, P2=6, P3=5 (HTTP enforcement locus — SDK can't produce `no_dirty_state_http`; must live in mcp_server handler; P2s: test_decide.py:418 diagnostic conflict, HTTP anchors+None unbounded surface, _evidence-reset false isolation, local-vs-global shuffle seeding, analyze.py second None-arithmetic site, extractor predicate)
- Controller action: Fixed both P1s per verifier prescriptions (interactive guard never aborts the contract + `ep._last_truncated`; HTTP enforcement in mcp_server handler with test + hosted-caller migration row). Incorporated all high-value P2s (run-depth semantic, dream dirty-root clearing fix, op_type-aware extractor + consistency test, wipe-rebuild fixture isolation, local Random/sorted seeding + re-pin, both analyze.py None sites + int-equivalence test, test_decide.py:418 diagnostic migration, HTTP anchors+None bound, Δ=0 weak-potential precondition, precise canonical-BFS-consistency definition, boundary vectors in truncated regime).
- **Streamlined-mode note:** max-1-redispatch cap reached; fixes applied per verifier-prescribed resolutions, documented. Gate passes on controller judgment; the Phase 5.6 coherence reviewer provides the additional fresh-context layer.

### Phase 5.6 Coherence — substitute reviewer (qwen3.8-max BLOCKED, 401)

**[QWEN-GATE] substitute reviewer used** (deepseek-v4-flash fresh context — qwen3.8-max API blocked with 401). Findings: P1×2 (dirty-root seed verification missing end-to-end; AC5 Indicator-1 surface qualification — HTTP vs stdio), P2×5 (full-side baseline pinning to `ep.run`/`_affected_factors`; Integration Docs seeding contradiction; EFBP grounding slippage → two-tier mapping; scoped evidence hydration conditional home; HTTP anchors+None OR → clamp decision).
- Controller action: fixed both P1s (end-to-end dirty-root seed vector + bypass-path audit row; AC5 qualified to stdio/embedded + HTTP disable-contract + ep_version → #901) and all five P2s (baseline pinned, seeding line corrected, two-tier grounding, conditional step 10, clamp decision + atomic shipment). All fixes incorporated; no re-run needed (per-gate rule: fix P1 once, document).

## Complexity

| Domain | Rating | Rationale |
|---|---|---|
| UX | low | No UI; SDK/MCP tool-description text + HTTP contract docs only |
| Ontology | low | No schema/kind changes; `confidence` property semantics preserved (ONTOLOGY §4.1) |
| Architecture | high | EP engine (run/BFS/write-back), projection, SDK contract, MCP, dreaming — core algorithmic architecture; dual-BFS None semantics |
| Library-deps | none | No new third-party deps; stdlib `random.seed` (in-repo precedent) |
| Content | n/a | No content pipeline |
| Config | low | No config changes |
| Research | medium | External best-practice import (EFBP, Ihler, Sumer) operationalized into tolerance + guard |

**Tier: complex (epic).** Issue body says "standard (scope reduced — algorithm already works locally)" — superseded: epic-level title, implementing epic #901 is complexity:complex, and the deltas + correctness harness + migration surface exceed standard. Label: `complexity:complex`.
