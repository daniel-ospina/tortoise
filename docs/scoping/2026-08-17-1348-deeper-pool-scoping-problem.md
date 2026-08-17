# Scoping — Issue #1348 (problem-converge, fixed per problem-verify cycle 1)

## Confirmed Problem Definition (v2 — incorporates verifier P1/P2 fixes)

**The RRF fusion quality levers — production pool depth, fusion parameter k, and the
graph-informed GraphRanker signal — are unmeasured and the current eval cannot
isolate them.** Production pool depth is hardcoded (`str_limit = limit * 2`,
sdk.py:8725) and sits below the documented 50–100 RRF consensus sweet spot at
typical limits (pool 20 at limit=10); k is hardcoded at 60 with no corpus sweep;
GraphRanker (#25) exists but is unwired from the fused path and unscorable on the
synthetic oracle corpus as-is. Deliverable: configurable pool floor ≥50 per
strategy (str_limit only — returned limit unchanged), a documented k∈{20,60,100}
sweep, and an evidence-based GraphRanker verdict (adopt / hybrid / reject) with
honest validity bounds.

## Corrections pinned (from problem-verify cycle 1 — verifiers A + B)

1. **Fusion composition (P1 — factual correction to the issue body):** the issue's
   Context "RRF fusion is a consensus vote across 3 strategies (FTS / vector /
   TF-IDF)" is WRONG. Production fuses FTS + vector + structural
   (run.py:160-163 / sdk degradation_chain); TF-IDF is the all-fail fallback
   (sdk.py fallback_tfidf), deliberately excluded from fusion (full-corpus scan
   cost). **The plan must NOT "fix" fusion to include tfidf** — that would add a
   full-corpus in-memory scan per query inside the 500ms-capped critical path (a
   latency regression exactly what indicator 4 guards). The eval-corpus finding
   (tfidf 0.828 ≈ vector 0.8546 excluded while structural 0.0013 votes) is an
   EVAL-CORPUS artifact (95/100 oracle queries are kind-less → structural is a
   dead voter on Docker too; oracle_queries.json has kind on only 5/100), NOT a
   production bug. **No-file rule is CONDITIONAL:** if the Docker all-populated
   leg shows fused < best-single voter (CI excludes 0) AND per-tier deltas
   localize it to non-kind-less tiers, the composition finding is a real defect
   and MUST be filed as a separate issue — the artifact claim is scoped to the
   kind-less-heavy eval corpus.
   **Issue-body amendment (plan step 1):** correct #1348's Context fusion set
   statement, the "Depends on #317 slice 1" line, and indicator 3's "#1144
   labeled set" (which does not exist) in the GitHub issue body so downstream
   readers don't act on the wrong fusion claim.

2. **Eval environment pinning (P1):** pool-depth × k sweep verdicts are
   authoritative ONLY on Docker FalkorDB with FTS + vector + structural all
   populating (mirror #317 gate-record's "Docker FalkorDB prod" requirement).
   Redislite/embedded results are environment-conditional — the embedded FTS leg
   is real on the current engine (fulltext index exists; FTS populated 40/100
   queries in the baseline) but single-strategy or partially-populated runs are
   NOT authoritative. **The sweep report MUST include per-strategy population
   counts** so a single-strategy run self-declares as non-authoritative.

3. **GraphRanker verdict path (P1 — DEFAULT = corpus enhancement (a)):** on the
   #1144 synthetic oracle corpus ALL THREE GraphRanker signals are null:
   posterior_alpha/beta = `rng.randint(1,40)` (random, uncorrelated with topic —
   oracle generator site is synthetic_corpus.py:523-524, non-eval generator at
   :149-151; BOTH must be targeted), operator edges to random targets
   (connectivity uncorrelated with relevance), and createdAt = timestamp() at
   seed time (recency dead — every point identical). A measured "reject" on this
   corpus is the NULL expectation, not evidence.
   **Primary leg = (a) corpus enhancement, scoped to the SCORING signals:**
   GraphRanker's point boost reads `coalesce(n.confidence, 0.5)` + operator
   degree (ranking.py:190); α/β feed only the variance/contested ANNOTATION
   (deliberately not scored, ranking.py:176-178). So the enhancement must (a1)
   write topic-correlated `n.confidence` (e.g., target-topic points strong,
   distractors weak — via `compute_confidence`-equivalent at seed or derived
   from correlated posteriors), (a2) make operator-edge connectivity
   topic-correlated (target-topic points accrue IMPL edges), (a3) keep α/β
   correlation for the contested annotation, and (a4) state that recency stays
   dead (or spread createdAt if recency is to be exercised — default: leave
   dead, document). **Determinism guard:** `generate_oracle_points` shares one
   seeded rng stream across content/embedding/posterior draws — preserve the
   stream (map the same drawn values through topic membership, or use an
   isolated `random.Random(seed ^ salt)` for posteriors) or the whole seeded
   corpus changes and the committed baseline becomes non-comparable; state the
   old baseline is then non-comparable. The verdict then reads "confidence +
   connectivity signals add value (or not) on topic-correlated synthetic EP"
   with explicit synthetic-EP weak-proxy bounds.
   **Boundary = (b) null-test scoping (documented follow-up, NOT an equal-weight
   alternative):** verdict scoped to "graph signal adds no measurable value on
   random-EP structure; validation of all three signals requires a real-EP
   corpus" with the real-EP validation filed as a surfaced dependency (#317
   GATE INPUT B labeled set and/or the 50 authored queries against a genuinely
   populated graph via `--no-seed-corpus` — NOTE: a plain Docker run still
   seeds the synthetic corpus, so real-EP requires the flag; n≈50 power
   caveat). The (b) path alone must NOT close indicator 3 as a bare deferral —
   (a) is the default primary leg. Either way, the verdict MUST name its actual
   data source and which GraphRanker signals are live in it.

4. **Indicator-4 semantics (P2 — pool vs returned limit):** #1348 deepens the
   POOL only (`str_limit`; final returned limit stays at the caller's limit).
   Truncation happens BEFORE decoration (sdk.py:8851 `result_ids[:limit]` →
   step 6 EP annotation at 8853+) — the DECORATION component is flat, so
   **#1353 is NOT a hard dependency for the pool floor**. BUT: the E2E-8 verdict
   per #316 is the censored END-TO-END column (mix-weighted p95 ≤ 300ms at the
   customer surface; retrieval-only p95 reported separately) — the retrieval
   component DOES grow with pool depth (per-strategy DB LIMIT = str_limit,
   larger RRF input, step-5a/5d filter batches over the larger pool
   pre-truncation), so E2E-8 must still be re-measured at the new pool floor;
   the "not affected" claim is scoped to the DECORATION component only.
   Retrieval-only p95 reported separately per #316's pre-registration. Raising
   the RETURNED limit is OUT OF SCOPE (would require #1353). One path, not an
   OR — no vacuous close, no false block.

5. **Power / ceiling pre-registration (P2):** P@5=0.924 is near ceiling at
   depth 50 on the oracle corpus; the depth delta (20→100) may be unpowered or
   ceiling-capped at n≈100. Pre-register metrics with headroom: recall@10/20
   and per-tier (easy/medium/hard) deltas, plus a pre-registered outcome
   "insufficient power → verdict: needs real data" so a null result is
   interpretable. The shallow-pool leg (limit=10 → pool 20, the production
   status quo) MUST be measured fresh — the committed baseline (limit=50, pool
   50 in eval semantics) does not represent status quo, so a depth-curve
   {10, 25, 50, 100} rather than a binary before/after. **Pool-semantics note:**
   the eval runner passes `limit` straight to degradation_chain (run.py:150,
   NO ×2) while sdk.py:8725 doubles — eval pool == eval limit (baseline pool
   was 50, not 100), SDK pool = limit×2. The sweep report must state per-leg
   whether the x-axis is eval-limit or SDK str_limit (the runner likely needs a
   distinct str_limit knob to measure pool-at-fixed-returned-limit).

6. **Baseline note correction (P2):** `baseline-embedded-2026-08-17.json`
   notes 1 AND 2 and the run.py docstring are STALE — the embedded fulltext
   index exists and returns rows (provenance `indexes.fts: true`); per-query
   data shows FTS populated 40/100, structural 4/100, fused ≠ vector on 17/100
   queries (fused-vs-vector nDCG@10 delta CI −3.25..−0.83, excludes 0).
   In-scope doc task: correct notes 1 + 2 and the runner docstring to actual
   embedded FTS behavior, and require the k-sweep report to include the
   fused-vs-best-single-strategy delta (the eval already computes
   paired_vs_fused) as an explicit acceptance criterion.

7. **#1349 embedder coordination (P2):** #1349 (embedder swap, OPEN, being
   scoped elsewhere) touches the same measurement — the Docker vector leg uses
   the loaded embedder. Run #1348's Docker measurement legs AFTER #1349 lands
   OR pin the embedder for the duration; record the embedder in eval provenance
   (already partially captured). No code overlap; measurement-sequence overlap
   only.

8. **#1144 dependency status (P3):** #1144 is OPEN; its labeled relevance set
   does NOT exist yet (its own body: neither real query logs nor a labeled set
   exist today). The sweep runs on the oracle corpus (documented weak proxy per
   #1144's own scoping). GraphRanker verdict names its actual data source (per
   correction 3).

## Why This Framing (rejected alternatives)

- **F1 fusion-composition as primary** — rejected: the fused< vector deficit is
  largely an eval-corpus/embedded artifact (97/100 kind-less queries → structural
  dead voter; FTS leg environment-specific). The k-sweep brackets the question
  (k=20 steepens toward best voter, k=100 flattens toward noise) and the eval
  already outputs paired_vs_fused — the fusion-composition question is
  answerable from #1348's own deliverable, no separate issue.
- **F2 eval-validity as primary** — rejected: correct that GraphRanker is
  unscorable on the synthetic corpus, but discards the legitimately measurable
  arms (depth, k). Its constraint is adopted as a boundary (correction 3), not a
  replacement problem.
- **F3 original as primary** — rejected: misstates current state (eval already
  pools 50; production fuses FTS/vector/structural not FTS/vector/TF-IDF) and
  the "deeper pool improves quality" premise is unverified for a 2000-point
  corpus (baseline fused < vector at depth 50). Proceeding on it would tune a
  noise voter.

## Falsification Check

This definition is wrong if: (a) a Docker sweep shows nDCG@10 / recall@10
plateaus at limit≈50 → "deeper pool" has no quality headroom (configurability
still ships, depth claim dies); (b) a Docker run with all strategies populating
shows fused ≥ best single voter → the composition finding is an artifact and
fusion is healthy; (c) GraphRanker on topic-correlated-EP corpus shows zero lift
→ graph signal has no retrieval value (reject verdict with evidence).

## Confidence: 82

Facts verified at high confidence (all load-bearing claims code-verified by 2
independent verifiers); residual risk is Docker-parity runs overturning
embedded-based composition claims, which would re-shape indicator 3's verdict
but not the core (levers unmeasured, apparatus required).
