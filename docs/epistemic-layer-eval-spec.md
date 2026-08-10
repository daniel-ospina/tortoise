> **UPDATE (2026-08-09) — P0 NAND fix IMPLEMENTED on feat/753-directed-nand (PR #795).**
> Investigation: φ_directed = exp(w·ca)·φ_nand is NOT message-equivalent on the
> target (the exp(w·ca) factor sits inside the marginalization integral —
> measured up to 17.6% difference). Directedness is structural (the back-message
> guard: unidirectional operators skip the source message). **Product-owner
> decision (2026-08-09): NAND stays BIDIRECTIONAL by default (logical mutual);
> `unidirectional` is the agent-declared directed attack.** 5 property tests landed (directed-attack-lowers-target, no-back-
> pressure, bidirectional-is-mutual, reinstatement,
> test_nand_defaults_to_bidirectional, test_mcp_tool_honors_direction,
> test_directed_nary_nand_source_to_targets_only). The
> §1 potential-replacement recommendation is superseded by the default flip;
> the remaining known weakness is weak mutual-contradiction coupling (+0.0024).

# Epistemic-Layer Effectiveness — Evaluation Spec (v1)

**Status:** Draft for review · **Owner:** Evaluation Designer (epistemic layer)
**Grounds:** `tortoise/ep.py`, `tortoise/quadrature.py`, `tortoise/weights.py`, `tortoise/source_credibility.py`, `tests/test_ep_directional.py` (E019), `tests/test_ep_sources.py` (#341), `tests/test_ep_nary_falsification.py` (#420), `docs/ONTOLOGY.md` §3.1 / §10.5, `docs/tortoise-product-success-eval.md` (agent-infra).
**Question this spec answers:** how do we *know* the claim graph + IMPL/NAND + confidence propagation actually reasons correctly — and what exact numbers mean "done"?

---

## 0. Evaluation objects and method

The system under test is the epistemic layer in three compositions, each with its own role:

| Object | What it is | Tested by |
|---|---|---|
| **EP engine** (`TortoiseEP` + `quadrature.phi_*`) | Beta-message belief propagation over IMPL/NAND factors | Property tests (§2) |
| **Graph as built by the pipeline** (extraction → operators → supersession → dream) | The *input* the engine reasons over | Graph-quality tests (§3) |
| **Query/endpoint surface** (`get_confidence`, `traverse`, `get_contested_claims`, ledger, bi-temporal) | What a user/agent actually experiences | Reasoning-endpoint scenarios (§4) |
| **Whole system under hostile input** | Noise, loops, Sybil, flapping | Adversarial tests (§5) |

**Method rules (non-negotiable):**
1. Every test is **deterministic**: fixed seed, fixed graph builder, fixed operator order where EP shuffles factors (pin `random.seed` inside the harness, or run EP with a frozen factor order).
2. Every assertion is **a confidence delta with a threshold**, not a vibe. Mean/delta thresholds are given below; they were calibrated against the real engine on 2026-08-09 (see §1) and must be re-locked in a calibration run on the target implementation before v1 sign-off.
3. **Hermetic where possible, Docker where necessary.** The hermetic pattern from `test_ep_nary_falsification.py` (stub `proj.g`, pre-populate `_node_cache`/`_msg_cache`, drive `_update_factor`/`run` directly) covers the factor arithmetic. SDK-level tests (`test_sdk_ep.py` pattern, temp `db_path`) cover end-to-end belief. Docker-gated tests (E019) must ALSO have a hermetic twin so the no-Docker CI suite still exercises the semantics.
4. **Fail loud on the two failure modes:** non-convergence (`converged=False`) is reported, never papered over; inverted behavior (a contradiction raising confidence) is a P0 blocker, not a threshold tweak.

---

## 1. Calibration baseline — measured behavior of the current engine (2026-08-09)

> **Baseline note (2026-08-09):** this section documents the PRE-#855 engine (product coupling, w=1.0 NAND, drift-inflated re-runs). The #855 fix (NAND base 8.0, difference coupling, drift fix #852) changes the measured values — T0↔T0 NAND now settles ~0.82 (was 0.915), T4 hit by T0 NAND → ~0.15. Verdicts marked 'INVERTED'/'P0 blocker'/'blocks v1' no longer describe the shipped engine.

All numbers measured by running the real code (`.venv/bin/python`, embedded FalkorDBLite, `compute_confidence()`: damping=0.5, n_quad=8, max_iter=50, tol=1e-4 (#855 tightened from 1e-3)). Tiers: T0=Beta(10,1)→0.909, T1=(5,1)→0.833, T2=(3,1)→0.750, T3=(2,1)→0.667, T4=(1.1,1)→0.524.

| Behavior | Measured today | Required (this spec) | Verdict |
|---|---|---|---|
| IMPL support: T0 premise → uniform conclusion | B: 0.500 → **0.714** (var 0.065) | ≥ 0.65 | ✅ works |
| IMPL attenuation: chain A(T0)→B→C | B 0.715, C 0.679 (Δ ≈ −0.036/hop) | 2-hop ≤ 1-hop, monotone | ✅ works |
| Echo chamber: mutual IMPL A↔B (both T0) | 0.909 → 0.922 (+0.013), converges in 3 iters | amplification < +0.03, converges | ✅ works (cap still needed) |
| Directed attack: T0 attacker → T4 target | 0.524 → **0.614** (target *rises* +0.09) | target drops ≤ −0.10 | ❌ **INVERTED** |
| Attack T0 → T0 target | 0.912 (inert, |Δ|<0.01) | target drops ≥ 0.03 | ❌ inert |
| Weak attacker (0.17) → T0 target | 0.906 (inert) | disbelieved attacker has no force (Δ < 0.02) | ✅ accidental |
| Dense attack: 5×T0 → T0 target | target **rises** to 0.924 | target < 0.15 | ❌ **INVERTED** |
| Reinstatement: C attacks A, A attacks B | B never drops, so nothing to restore | B recovers ≥ 0.03 | ❌ cannot occur |
| Mutual NAND T0↔T0 (contradiction) | both 0.915, **var 0.006** (not contested) | var > 0.04 → surfaced | ❌ contradiction invisible |
| NAND odd triangle A→B→C→A | converges trivially, all 0.916 | contested/UNDEC, honest report | ❌ no semantics |
| E019 no-false-cascade gates | suite Docker-skipped in this env | c2_drop < 0.005; b_drop < 0.02 | ⚠️ unverified live |
| Contested detection var > 0.04 | threshold exists (`ep.py:435`); today only weak-IMPL middling claims qualify | genuine contradictions surface | ⚠️ half-working |
| Anti-Sybil prior (log-law) | 1000×T4=0.666 < 1×T3=0.667; 10×T4=0.574 < 1×T0=0.909 | quality beats quantity, monotone | ✅ works at prior level |
| Grounding regression (unrelated claims) | no gate implemented in repo | mean shift < 0.01 | ⚠️ must add |

**Root cause (measured, not speculated):** the current `phi_nand(ca, cb) = exp(−w·(ca(1−cb)+cb(1−ca))/2)` is an *agreement* coupling — it is maximized when both claims sit at the same extreme (0,0) or (1,1), and minimized when they disagree. It is functionally a weak IMPL. A confident attacker therefore *raises* a middling target. The docstring claim "equal-quality contradiction returns to ~50%" is not what the math does. The engine also never had an asymmetric potential: the docstring notes the original asymmetric form `exp(−w·ca·(1−cb))` was replaced *because it was asymmetric* — which is exactly what directed attack requires.

**Consequence for v1:** property tests P2, P6, P8, P9 (below) are **v1 blockers** until the NAND potential is replaced with a position-aware, directed-attack potential (see §2 note on the candidate) and direction is honored in message passing. This is the single most important finding of the evaluation pass.

---

## 2. Property tests — the decisive behaviors (P1–P10)

Each test: setup (nodes/edges/baselines) → action → exact assertion. Deltas are measured between EP runs before/after the action on the *same* graph. Thresholds marked **[cal]** are calibrated to the target semantics (§1) and must be re-locked in the calibration run.

### P1 — Support transmission and attenuation (IMPL)
- **Setup:** A(T0, α=10,β=1) `IMPL→` B(uniform); chain variant adds B `IMPL→` C.
- **Assertions:**
  1. `mean(B) − 0.5 ≥ 0.15` (measured: 0.714). **[cal]**
  2. `mean(B) < 0.95` (never exceeds a ceiling near the source; support is attenuated, not copied).
  3. Chain: `mean(C) < mean(B)` and `mean(B) − mean(C) < 0.10` (attenuation monotone, bounded per hop). Measured: Δ=0.036.
  4. Weight ordering: with operator weight `w=0.5` vs `w=2.0` (via `weights.py` inputs), `mean_w2(B) > mean_w0p5(B)`. Monotone in w.
- **Worked numbers:** A(0.909)→B: B=0.714 (measured). Chain: B=0.715, C=0.679.

### P2 — Directed-attack asymmetry (NAND)
- **Setup:** A(T0) `NAND→` B(T2, 0.750), operator `direction="unidirectional"`.
- **Assertions:**
  1. `mean(B)_after − mean(B)_before ≤ −0.10` **[cal]** (target drops under attack; measured today: −0.344 with the candidate potential, +0.09 with the current one — this test fails today).
  2. `|mean(A)_after − mean(A)_before| < 0.01` (no back-message; the attacker's belief is untouched).
  3. Symmetry control: same graph with `direction="bidirectional"` → `|ΔA| > 0.01` (mutual contradiction pulls both).
  4. **Anti-inversion guard (P0):** `mean(B)_after ≤ mean(B)_before + 0.01`. Today: +0.09 → **fails, blocks v1**.
- **Worked numbers (candidate potential, single tilt):** T0→T4: 0.524→0.153; T0→T2: 0.750→0.406; T0→T1: 0.833→0.609; T0→T0: 0.909→0.835.

> **Note on the potential (spec contract, not an implementation prescription):** the layer must implement a *position-aware* attack potential, e.g. `φ_attack(ca, cb) = exp(−w·ca·cb)` — the attacker's belief *forbids* the target's belief; a disbelieved attacker (ca→0) exerts no force (φ→1). The current symmetric `phi_nand` is a **P0 blocker** (§1 root cause). Mutual contradiction is the same potential with bidirectional messaging. `svbp.py` stays archived; Beta posteriors under the attack potential remain unimodal, so EP's Beta projection is adequate and the old NAND-induced-bimodality rationale is superseded.

### P3 — Rebut vs undercut (edge-targeting NAND)
- **Setup:** A(T0) `IMPL→` B. Two attack modes: (a) **rebut** — claim C(T0) `NAND→` B ("B is false"); (b) **undercut** — claim C(T0) `NAND→` the *operator* A⇒B ("this inference is invalid"), which removes/strips A→B support without asserting ¬B.
- **Assertions:**
  1. Both modes reduce B: `mean(B)_rebut < mean(B)_undercut < mean(B)_before` **[cal]** (rebut hits the claim directly and is stronger than removing support).
  2. Undercut specificity: `|mean(A)_after − mean(A)_before| < 0.02` (the premise is untouched — only the *inference* is attacked).
  3. Rebut may (by design) send weak back-pressure to A in bidirectional mode; undercut never does.
- **Worked numbers:** B prior 0.714 (from P1). Rebut → B < 0.5 **[cal]**; undercut → B returns toward its no-support prior ≈ 0.5–0.55, A stays 0.909. If undercut and rebut produce the *same* posterior, the distinction is not implemented — fail.

### P4 — Support attenuation = premise conf × edge weight
- **Setup:** premise P at confidence c ∈ {0.6, 0.75, 0.91} (via baselines), `IMPL→` Q, weight w ∈ {0.5, 1.0, 2.0} on the operator.
- **Assertions:** for fixed w, `mean(Q)` is monotone increasing in c; for fixed c, monotone increasing in w; and `mean(Q) < mean(P) + 0.02` for w=1.0 (a target never exceeds a support-only premise). **[cal]**
- This pins "premise confidence × edge weight" as an ordered, damped transfer — the scalar ledger (§4 R3) must reproduce exactly these numbers as `prior + signed_message`.

### P5 — REPHRASE evidence pooling (dedup without deletion)
- **Setup:** claim X with baseline (5,1) (T1); claim R ("rephrase of X") with baseline (5,1); `REPHRASE` link R→X.
- **Assertions:**
  1. Pooling, no double-count: `effective_n(X)_with_R < effective_n(X)_independent_duplicate`, where the independent-duplicate control is X with a *second, separately-attributed* T1 source. REPHRASE must pool evidence like the same source appearing twice (log-cap, `aggregate_prior` semantics), not like two independent sources.
  2. `mean(X)_with_R > mean(X)_alone` (pooling adds evidence) but `mean(X)_with_R − mean(X)_alone < 0.10` (no multiplication). **[cal]**
  3. Deleting R does not change X's belief beyond a re-run tolerance `|Δ| < 0.005` (REPHRASE is not an evidence edge).
- **Status:** REPHRASE is not implemented; this test defines its contract. The log-cap math to reuse is `aggregate_prior` (§1 anti-Sybil row).

### P6 — Invalidation + supersession composition (bi-temporal)
- **Setup:** E(T0) `IMPL→` A; A `IMPL→` B. Action: `supersede_point(A, A')` (edge transfer per ONTOLOGY §3.1).
- **Assertions (structure, extending `test_supersede_edges.py`):**
  1. `mean(A') ≈ mean(A)` before supersession ± 0.02 **[cal]** (A' inherits A's belief: transferred edges + same evidence).
  2. After supersession, `mean(B)` changes by < 0.02 (the conclusion is preserved through replacement). **[cal]**
  3. A is `outdated:true` and its outgoing operator messages are zeroed: re-running EP with A in the graph produces `|Δ mean(B)| < 0.005` versus a graph where A was deleted (invalidation over deletion: A's ghost must not vote).
- **Bi-temporal assertion:** the graph must answer "was A believed at t1?" from history — either a timestamped belief snapshot or a replayed event log — and `belief(A, t1) − belief(A, now)` must be consistent with the invalidation event. If the layer ships no history, this test is **explicitly deferred** and R4 is marked not-shippable; do not fake it with current-state reads.

### P7 — Anti-Sybil tier dominance
- **Setup:** claim X supported by N sources of tier T: (a) 1×T0; (b) 10×T4; (c) 1000×T4; (d) 1×T3.
- **Assertions (prior level, `aggregate_prior` — already proven in `test_ep_sources.py`; keep as regression):**
  1. `mean(1×T0) > mean(10×T4)` (0.909 > 0.574, measured).
  2. `mean(1000×T4) < mean(1×T3)` (0.666 < 0.667, measured — quality beats quantity).
  3. Monotone in source count within a tier (log-concave marginals).
  4. **EP level:** through a chain (sources `IMPL→` X), the same ordering must survive EP propagation: `mean(X|1×T0) > mean(X|10×T4)` **[cal]**. If EP flattens the prior ordering, the layer is Sybil-vulnerable.

### P8 — Loop convergence (no oscillation, bounded fixed point)
- **Setup (three graphs):** (i) odd NAND triangle A→B→C→A; (ii) mutual IMPL ring P0→P1→P2→P3→P0; (iii) two claims with mutual NAND + mutual IMPL (mixed frustration).
- **Assertions:**
  1. `converged=True` within `max_iter` (default 50) on all three; measured: 2–3 iterations today.
  2. **Bounded oscillation:** for the last 5 iterations, `max over claims of |mean_t − mean_{t−1}| < 0.02`. **[cal]**
  3. Honest non-convergence: if a graph cannot converge (e.g., an odd NAND triangle under strict attack semantics may legitimately need a contested/UNDEC label instead), the run must return `(max_iter, False)` and the *query layer* must surface "undecided" — never a confident number. `test_ep_nary_falsification.py` already covers the honest-reporting path; extend to the triangle.

### P9 — Contested-claim surfacing
- **Setup:** claim X with balanced contradiction: X(T2, 0.750) attacked by C1(T0) `NAND→` X, and supported by E1(T0) `IMPL→` X.
- **Assertions:**
  1. `variance(X) > 0.04` (the `get_contested_claims` threshold, `ep.py:435`).
  2. `X ∈ get_contested_claims(0.04)`.
  3. Control: a strongly-settled claim Y (5×T0 IMPL, no NAND) has `variance(Y) < 0.04` and is NOT surfaced.
  4. Search annotation parity (`mcp_server.py` `ep.contested`): surfacing a contested claim must set `ep.contested=true` — "surfaced, never scored" (§#580/#583) is preserved: contested claims are not deprioritized by ranking.
- **Status:** threshold machinery exists; with the current NAND it can never fire on genuine contradictions (measured var 0.006 on mutual NAND). Blocked by P2's potential fix.

### P10 — No-false-cascade (E019 as a property)
- **Setup:** A and B both `IMPL→` shared conclusion C1; B `IMPL→` independent C2. A is invalidated by NAND.
- **Assertions (extend `test_ep_directional.py`, keep the exact gates):**
  1. `a_drop > 0.03`, `c1_drop > 0.001` (the invalidated argument and its conclusion move).
  2. `b_drop < 0.02`, `c2_drop < 0.005` isolated / `< 0.02` dense (3 shared conclusions, unidirectional) — unrelated structure does not move.
  3. Grounding gate: for a *separate disconnected component* (claims sharing no operators with the affected subgraph), `mean |Δ| < 0.01` across the whole component — this is the #7740 gate, codified as a test.
- **Status:** suite exists, Docker-gated, unverified live in this env. Must run in CI with FalkorDB; hermetic twin required for the no-Docker suite.

---

## 3. Graph-level quality tests (G1–G8)

These measure the *input* — whether the pipeline built an epistemic graph that CAN reason. Use a synthetic corpus with a known ground truth (gold derivation/contradiction structure) plus controlled noise, generated deterministically. Corpus v1: 60 claims, 20 premises, 25 IMPL edges forming 6 derivation trees, 10 contradiction pairs, 15 noise points (unrelated), 5 near-duplicate pairs.

| ID | Test | Assertion |
|---|---|---|
| G1 | **Contradiction recall** — for each of the 10 gold contradiction pairs, does a NAND edge exist (direct or via a chain ≤ 2)? | recall ≥ 0.8 (≥ 8/10) |
| G2 | **Contradiction precision** — of all NAND edges in the built graph, fraction that correspond to gold contradictions or are explicitly human-annotated | precision ≥ 0.7; no NAND between claims that are merely unrelated (allow annotated-user edges) |
| G3 | **Support recall** — every gold derivation tree edge has an IMPL path (direct preferred) | direct-edge recall ≥ 0.9; path recall = 1.0 |
| G4 | **Support precision** — no IMPL edge between gold-unrelated claims | ≤ 5% of IMPL edges false-positive |
| G5 | **Contested surface rate** — fraction of the 10 contradiction pairs where *at least one* member is surfaced by `get_contested_claims(0.04)` after EP | ≥ 0.9 (with P2 fixed; today ≈ 0) |
| G6 | **False contested rate** — fraction of the 15 noise claims surfaced as contested | ≤ 0.05 |
| G7 | **Grounding regression** — run EP on the full corpus; for 15 noise claims and a fresh disjoint batch, mean |Δconf| vs pre-run | ≤ 0.01 (the #7740 gate, graph-wide) |
| G8 | **Pollution dashboard invariants** (cheap, run every build): (i) NAND:IMPL edge ratio ∈ [0.02, 1.5] (a graph with 10× more NAND than IMPL is poisoning); (ii) isolated-claim rate ≤ 30% (claims that never attach to the graph are memory litter); (iii) mean variance of the corpus stays below 0.05 (graph is not globally undecided); (iv) `dream_all` converges and reports `converged_all=True` | all hold on corpus v1 |

**Corpus hygiene rule:** G1–G6 run on the synthetic corpus only (ground truth is known). G7 additionally runs on a real session dump (100–800k tokens, ~13/day user profile) as a weekly job, because synthetic corpora never contain the drift that real extraction produces.

---

## 4. Reasoning-endpoint acceptance scenarios (R1–R8)

End-to-end, through the public surface. Each scenario = setup → action → query → expected result with exact numbers. These are the *user-visible* acceptance tests.

### R1 — "Is claim X still believed after new evidence?"
- **Setup:** X(T2) supported by E(T0) `IMPL→` X → `mean(X) = 0.83` **[cal]**.
- **Action:** new evidence C(T0) `NAND→` X arrives (one write).
- **Query:** `get_confidence(X)`.
- **Accept:** `mean(X) ≤ 0.5` **[cal]**; `variance(X) > 0.04` (contested); the result carries the ledger lines for both E and C (§R3). If the answer is still > 0.6, the layer has not incorporated the new evidence — fail.

### R2 — "What contradicts my decision?"
- **Setup:** decision D(T1); two attackers C1, C2 `NAND→` D.
- **Query:** `traverse(D, "NAND", direction="incoming")`.
- **Accept:** returns exactly {C1, C2} with each attacker's confidence and tier; sorted by message strength (the strongest attacker first); ledger shows both negative lines. Zero NAND edges found for an undecided claim is a graph-quality failure (G1).

### R3 — "Why does the graph believe X?" — ledger-vs-EP consistency
- **Setup:** any claim with ≥ 3 incoming edges (mix of IMPL/NAND), after EP.
- **Query:** ledger explanation = prior line + one line per incoming operator: `signed Δ(α,β)` and `Δmean` per edge.
- **Accept (the core fidelity test):**
  1. **Reconstruction:** `β(X) ≡ prior + Σ message_lines` in natural-parameter space within `1e-3` on **100%** of sampled claims (this holds by construction of `_update_claim_posterior`; it is a regression guard against ledger/EP drift).
  2. **Coverage:** the ledger's Σ|Δmean| covers ≥ 95% of `|mean(X) − prior_mean(X)|` (every line explains part of the movement; no unexplained mass).
  3. **Faithfulness spot-check (human/LLM):** for 10 sampled claims, a reviewer reconstructs the dominant line's source (which premise/attacker moved the needle) from the ledger alone; ≥ 8/10 correct. This is the "explainability spot-check" from `docs/tortoise-product-success-eval.md` made quantitative.

### R4 — "Was X believed before the retraction?" (bi-temporal)
- **Setup:** X believed 0.85 at t1; retracted (invalidation) at t2.
- **Query:** `belief(X, t1)`.
- **Accept:** returns ≈ 0.85, not current-state. If no history layer ships in v1, R4 is **explicitly deferred** and the roadmap says so (do not substitute current-state reads). See P6 bi-temporal assertion.

### R5 — "What changed after the new session?"
- **Setup:** session dump ingested into an existing graph; EP run.
- **Query:** diff of pre/post confidences.
- **Accept:** exactly the claims in the session's 2-hop neighborhood moved (E019 isolation: unrelated |Δ| < 0.01, G7); newly-contested claims are listed with their variance; the diff report total affected ≤ session claims × neighborhood factor (bounded propagation, `_affected_claims` max_hops=2).

### R6 — "Should I trust this source?" (anti-Sybil at the endpoint)
- **Setup:** X supported by (a) 10 independent T4 sources vs (b) 1 T0 source.
- **Query:** `get_confidence(X)` both cases.
- **Accept:** `mean(X | 1×T0) > mean(X | 10×T4)` (0.909 vs 0.574 at prior level; ordering must survive EP — P7). A UI that shows "10 sources agree!" must show the tier-weighted number, not a raw count.

### R7 — "Why is this claim contested?"
- **Setup:** X with balanced E(T0) IMPL and C(T0) NAND (P9 graph).
- **Query:** `get_contested_claims(0.04)` then ledger(X).
- **Accept:** X surfaced with variance > 0.04; ledger shows the two opposing camps with their strengths (|Δmean| per camp); the explanation text states the stalemate ("supported by E @ 0.91, attacked by C @ 0.91") rather than a decisive number. Contested state must be *epistemic*, not evasive: the mean may be near 0.5 but the variance flag is the signal.

### R8 — "Undercut my inference" (edge-targeting at the endpoint)
- **Setup:** A(T0) `IMPL→` B(T2), C(T0) `NAND→` the operator (undercut, P3).
- **Query:** confidence of A, B, and the operator's status.
- **Accept:** B drops to its no-support posterior (≈ prior, 0.5–0.55) **[cal]**; A unchanged (|Δ| < 0.02); the operator is marked invalid/withdrawn so future queries don't propagate through it; B's ledger shows "support line withdrawn (undercut by C)" instead of a negative attack line. Distinguishes rebut (B attacked) from undercut (A→B attacked) in the ledger text.

---

## 5. Adversarial / robustness tests (A1–A8)

Hostile inputs a real deployment will see. These run on the same corpus + targeted adversarial graphs.

| ID | Threat | Setup | Assertion |
|---|---|---|---|
| A1 | **Oscillation (loopy NAND)** | Odd NAND triangles (3, 5 nodes), all T0 | converges ≤ max_iter; final-5-iter oscillation < 0.02 (P8); odd triangle honestly reports contested/UNDEC, never a confident number |
| A2 | **Noise loops (near-duplicates)** | 50 embedding-similar near-duplicate claims, all `IMPL→` same target | target's effective_n ≤ log-cap (same-source semantics, P5); `mean(target)` ≤ mean with 5 *independent* sources (duplicates must not multiply) **[cal]**; REPHRASE pass collapses ≥ 80% of the 50 into ≤ 10 canonical claims with no belief delta > 0.01 |
| A3 | **Echo chambers (mutual IMPL cycles)** | Ring of 4 mutual-IMPL claims, no external evidence | amplification < +0.03 over priors (measured today: +0.013 — cap is already near-compliant); converges; external counter-evidence (one NAND into the ring) must be able to move the whole ring down ≥ 0.05 (the ring is not a fortress) **[cal]** |
| A4 | **Undercut chains** | A⇒B⇒C with edge-attack on A⇒B | only B loses support (P3); C moves < 0.02 unless B⇒C is also cut — undercuts do not cascade through untouched inferences |
| A5 | **Confidence collapse under dense attack** | 5×T0 `NAND→` one T0 target | target < 0.15 **[cal]** (measured today: 0.924, i.e. **inverted** — P0 blocker, same root cause as P2); 20×T4 attackers must NOT collapse the target below 1×T0 attacker's effect (weak crowds are weaker than strong individuals, A2/A5 combined) |
| A6 | **Contested flapping (hysteresis)** | X attacked by C (toggle C's baseline between T0 and zero, 10 cycles) | X's label (live/contested) flips at most once per toggle *direction* (no oscillation between runs); after a full on/off cycle, X returns to within 0.02 of its original confidence (no ratchet); evidence changes produce monotone belief response (P8 boundedness at the scenario level) |
| A7 | **Sybil flood** | 100 weak sources (T4) `IMPL→` X vs 1 strong (T0) | P7 ordering holds through EP; no rank inversion at 100 (log-cap). Prior level measured: 100×T4 = 0.625 < 1×T0 = 0.909 |
| A8 | **Poisoning probe (product-success-eval carry-over)** | Inject a plausible-but-wrong point into 2% of retrievals | agent using the graph rejects ≥ 80% of poisoned claims at high confidence (confidence + NAND context), measured over 50 retrieval runs; poisoned claims that pass extraction must carry high variance (contested) — the layer surfaces, it doesn't silently believe |

---

## 6. Baseline comparisons — proving the layer beats alternatives

**Rule for all comparisons: same corpus, same scenarios, same thresholds, same seed.** Report per-scenario pass rates and the *delta*. The evaluation harness is a matrix runner (pattern: `tests/e018_harness.py` factorial runner) with one axis = layer under test.

### B0 — No graph (raw transcript recall)
- **Alternative:** retrieval over raw session transcripts, "last statement wins" belief, no propagation.
- **Metric:** **contested-resolution accuracy** — given a contradiction pair, does the answer expose BOTH claims + the conflict? Raw transcripts answer whichever statement is retrieved most recently (or by lexical match), silently; the graph answers with both claims, confidences, and a contested flag.
- **Honest test:** 20 contradiction pairs from real sessions; a blind grader scores whether the answer surfaces the conflict (recall) and states the current winner (accuracy). Accept: graph ≥ 0.85 on both; transcript baseline typically ≤ 0.5 (measure, don't assume).
- **Measurable advantage:** the graph's contested flag (variance > 0.04) is a *new signal* raw recall cannot produce. Advantage = contested-surfacing rate on the 20 pairs (graph ≥ 0.9, transcript = 0 by construction).

### B1 — Flat claim store (no propagation)
- **Alternative:** same extraction, static extractor confidence stored on each claim, zero IMPL/NAND propagation (edges exist but EP never runs).
- **Metric:** **responsiveness** — mutate a premise's baseline (evidence arrives/retracts); does the *conclusion's* confidence move? Flat store: no (0% responsive by construction). EP: conclusion moves ≥ 0.05 (P1). Accept: EP responsive on ≥ 90% of scenarios; flat store 0%.
- **Second metric:** **cascade isolation** — when a premise is invalidated, unrelated conclusions: flat store trivially passes (nothing moves); EP must pass too (P10 gates). This proves propagation is *cheap enough to be safe*: the honest test is "propagation must deliver responsiveness WITHOUT sacrificing isolation", not "propagation is better because more things move".
- **Measurable advantage:** Δ responsiveness (EP − flat) ≥ 0.90 across the scenario battery, at equal or better isolation (E019 gates green).

### B2 — Symmetric-attack (current EP, `phi_nand` as-is)
- **Alternative:** the engine today (symmetric agreement-NAND, §1).
- **Metric:** **directional suppression** — the P2 assertion battery (target drops, attacker untouched, dense attack collapses, reinstatement possible).
- **Honest test:** B2 *fails* P2/P5/A5 by construction (measured: inversion +0.09 on T4 target, dense attack +0.924). The epistemic layer's v1 must pass them. The comparison is not "we beat the old EP" — it is "the old EP demonstrably cannot express directed contradiction, and here is the quantitative gap (measured deltas §1)". This makes the baseline comparison *falsifiable*: if a reviewer re-runs B2 and finds symmetric NAND now passes P2, the claim "symmetric is inert" is dead — check the code, not the doc.

### Efficiency guard (not a baseline, a constraint)
- EP iterations ≤ 50 (default cap), embedded dream latency ≤ 500ms on the dirty subgraph (`dream.py` budget), affected-subgraph growth bounded by `_affected_claims(max_hops=2)` — propagation value must not cost responsiveness.

---

## 7. v1 acceptance criteria — the numbers that mean "the epistemic layer reasons correctly"

All thresholds **[cal]** re-locked in the calibration run on the target implementation; numbers below are the spec defaults measured/derived on 2026-08-09.

### Reasoning correctness (the decisive ones)
| # | Criterion | Number |
|---|---|---|
| AC1 | IMPL support transmission | premise 0.91 → direct conclusion ≥ 0.65 (measured 0.714) |
| AC2 | Directed attack suppression | T0 attacker → T4 target: Δ ≤ −0.10; → T0 target: Δ ≤ −0.03 |
| AC3 | Anti-inversion (P0) | NAND never raises the target (Δ ≤ +0.01) — **blocks v1 until fixed** |
| AC4 | Dense attack collapse | 5×T0 attackers → target < 0.15 (today: 0.924 — inverted) |
| AC5 | Reinstatement | attacked claim recovers ≥ 0.03 when its attacker is disabled |
| AC6 | No-false-cascade | unrelated C2 drop < 0.005 (isolated), < 0.02 (dense) — E019 green |
| AC7 | Grounding regression | mean shift of unrelated claims < 0.01 (the #7740 gate, codified) |
| AC8 | Contested surfacing | genuine contradiction pairs surfaced (var > 0.04) recall ≥ 0.9; false-contested ≤ 0.05 (G5/G6) |
| AC9 | Ledger fidelity | posterior ≡ prior + Σ lines within 1e-3 on 100% of samples; coverage ≥ 95% |
| AC10 | Convergence | 100% of adversarial graphs converge ≤ 50 iters; final-5 oscillation < 0.02; odd triangles report UNDEC honestly |
| AC11 | Anti-Sybil | 1×T0 > 10×T4 through EP; 1000×T4 < 1×T3 (prior-level already proven; EP-level must hold) |
| AC12 | Baseline deltas | responsiveness ≥ 0.90 over flat store at equal isolation; contested-surfacing advantage ≥ 0.9 over raw recall |

### Reasoning-endpoint acceptance (user-visible)
| # | Scenario | Accept |
|---|---|---|
| AC13 | R1 new-evidence query | mean(X) ≤ 0.5, variance > 0.04, ledger shows both lines |
| AC14 | R2 contradiction traversal | returns all incoming NAND with strengths, sorted |
| AC15 | R3 explanation | 100% reconstruction ≤ 1e-3; ≥ 8/10 faithfulness spot-check |
| AC16 | R5 session-delta | unrelated |Δ| < 0.01; affected set bounded (2-hop) |
| AC17 | R6 trust query | tier-weighted answer; ordering survives EP |
| AC18 | R8 undercut | B → no-support posterior; A unchanged; operator invalidated |

### Quality-of-life gates (regression, run every CI)
- E019 suite green with FalkorDB; hermetic twins green without it.
- G8 pollution invariants hold (NAND:IMPL ratio, isolation rate, mean variance).
- `dream_all` converged on corpus + real-session weekly job; latency within 500ms embedded budget.

---

## 8. Harness requirements

1. **Fixture corpus v1** (`tests/fixtures/epistemic_corpus_v1.py`): deterministic builder producing the gold structure (§3) + the P/R/A graphs as named scenarios. Every scenario is a function `(sdk) -> ids` so all tests share builders (pattern: `test_ep_directional.py` `build_shared_conclusion_graph`).
2. **Scenario runner** (`tests/epistemic_harness.py`): applies a scenario to a fresh SDK, runs the action, snapshots confidences before/after, emits a JSON report per scenario (`eval_results` pattern). Baseline matrix = runner × {layer under test}.
3. **Calibration mode:** a flag that *prints* measured deltas without asserting, for re-locking **[cal]** thresholds when the engine changes (never silently re-tune: threshold changes go in a reviewable table, §1).
4. **CI:** hermetic tier (no Docker) + Docker tier (E019, R scenarios on FalkorDB). The two tiers must cover the same assertions where possible.
5. **Seed pinning:** `random.seed` pinned in the harness because `ep.run` shuffles factors.

## 9. Blockers discovered during calibration (action items)

| # | Blocking | Evidence | Fix direction |
|---|---|---|---|
| B1 | Current `phi_nand` is an agreement coupling — contradictions are invisible or inverted | T0→T4 attack raises target +0.09; mutual NAND var 0.006; dense attack raises to 0.924 (measured §1) | Position-aware directed attack potential (e.g. `exp(−w·ca·cb)`), direction honored in message passing (§2 P2 note) |
| B2 | No REPHRASE/evidence-pooling semantics | feature absent | P5 contract + log-cap reuse of `aggregate_prior` |
| B3 | No ledger endpoint (explanation) | only `msg_alpha/beta` persisted on edges, no query surface | R3 contract; reconstruction is by construction — expose it |
| B4 | No bi-temporal history | supersede marks outdated, no belief history | P6/R4; defer explicitly if not shipping |
| B5 | Grounding gate (#7740) not codified as a test | no test asserts < 0.01 on unrelated claims | AC7/G7 |
| B6 | E019 unverified live (Docker-skipped) | suite skips without FalkorDB | CI Docker tier + hermetic twins |
| B7 | Odd-NAND-triangle semantics undefined | engine trivially converges to a confident number | P8/A1: contested/UNDEC labeling + honest non-convergence |

---

*Companion docs: `docs/tortoise-product-success-eval.md` (product-level metrics), `tests/test_ep_directional.py` (E019), `tests/test_ep_sources.py` (#341 anti-Sybil), `tests/test_ep_nary_falsification.py` (hermetic pattern), `docs/ONTOLOGY.md` §3.1/§10.5 (operator + cascade semantics).*
