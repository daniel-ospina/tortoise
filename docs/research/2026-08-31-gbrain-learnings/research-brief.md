# Epic Research Brief — adopt gbrain's measurable memory practices (epic #2080)

**Findings date:** 2026-09-01
**Source repos (primary):** `garrytan/gbrain` (MIT, v0.47.9.0 tip) and
`garrytan/gbrain-evals` (MIT) — full codebase walkthrough with file/line refs
in `raw-notes.md`.
**Epic:** #2080 "adopt gbrain's measurable memory practices" (complexity:
complex, epistemic-team)
**Method:** research skill (deep, external+internal) + epic-research structure.
Domain classification: **Complicated** (we know the questions to ask — hybrid
retrieval, planted-gold write evals, sealed-gold harnesses, receipts — we
needed expert answers from gbrain's code and its errata; the *solution space*
for W4's why-layer is Complex, flagged below). Internal knowledge base:
`docs/epistemic-layer-eval-spec.md` (P1–P10/G1–G8/R1–R8/B0–B2),
`docs/agent-reasoning-eval-battery.md` (Tiers 1–3),
`docs/research/2026-08-14-agentic-eval-landscape.md`, `tools/longmem_eval/`.
External: LongMemEval official evaluator (fetched), HaluMem (arXiv
2511.03506), memory-systems literature, consolidation theory. Adversarial:
README claims verified against eval code + the 35-agent audit's own errata.

---

## Strategy Context

### What gbrain actually proves (portable, in order of durability)

1. **The write path is measurable, and nobody else measures it.** Cat 35
   (24 fictional agent sessions, 173 planted salient units with verbatim
   anchors, 86 true-but-routine distractors, 2 attribution hazards, 3 lanes:
   verbatim control / facts / dream) scored 61.5% on first publication,
   **70.2% on a fresh pre-wave re-anchor, 88.1% post-fix-wave** (v0.47.8.0,
   PR #4742), strict 82.1%, all 20 sessions emitting, quote fidelity 82.7%,
   hallucination 7.0%, distractor leakage 1.2% (1/86), usability 90.8%
   (`gbrain-evals/docs/benchmarks/2026-08-16-brainbench-cat35-transcript-distill.md`).
   The same HaluMem table shows Mem0 at 42.9% and Supermemory at 41.5%
   extraction recall while both self-report 90%+ on read-path QA — the
   read/write gap is real and externally corroborated. Tortoise's
   `session_import`/`dream.py` pipeline has no planted-gold write-path eval
   (verified: no Cat-35 analogue in the repo). **W2 premise confirmed.**
2. **Volunteering memory at the right moment is a deterministic reflex, not
   an LLM behavior.** Cat 34's current committed CI baseline: know-to-ask
   failure **0.000 on all three harness seams (0/149)**, false fire 0.000 on
   all three, push precision 1.000, write-back 1.000, continuity 1.000,
   isolation violations 0. The reflex is regex entity extraction →
   alias/title/slug resolution → arm-confidence gate (alias 0.9 / title 0.8
   / surname 0.72 / slug-suffix 0.6) → 3-pointer budget → re-mention
   suppression, all zero-LLM, fail-open, 1.5s budget. **The first published
   run was 0.150 know-to-ask failure** — the current zeros are the result of
   a named fix wave (v0.46.15 "identity wave": lowercase-name alias arm +
   surname arm), not the starting state. This is exactly the "61.5% → fix
   wave → 88.1%" pattern the epic wants to replicate for Tortoise.
3. **The moat is the why-layer, which gbrain structurally cannot build.**
   gbrain's graph is typed edges (people/companies/concepts namespaces,
   wikilinks, facts fences) with zero belief semantics; its "gap analysis" is
   LLM prose. Tortoise's EP (support chains, NANDs, contestation, supersession,
   mitigations) is the raw material for recall that answers WHY — and
   Tortoise's own code confirms the gap: contestation is **"surfaced, never
   scored"** (`tortoise/mcp_server.py:1018`, `tortoise/ranking.py:583/768`).
   W4 is therefore not importing gbrain — it is building what gbrain
   structurally can't, on top of the eval discipline gbrain demonstrates.
4. **Benchmark discipline is the durable asset; the headline numbers are the
   weak part.** The 97.6% "SOTA" was any-hit R@5, walked back via erratum
   (official `recall_all@5` re-measurement still pending). The audit
   (239 findings: 17 critical — "scores wrong / eval measures nothing /
   crashes") found the shared metric helpers were wrong for everyone. What
   survived the audit — and is worth copying — is the *machinery*: sealed
   gold, fixtures_hash, receipts naming the commit, hermetic CI, corpus-bless
   mode, publish-bad-numbers-on-purpose.
5. **Tortoise already has the harder half.** EP property tests P1–P10,
   G1–G8, R1–R8, B0–B2, the Tier-1/2/3 reasoning battery, and a complete
   LongMemEval runner (with its own documented semantics divergences) — but
   only n=1 smoke reports are committed (repo-root
   `longmemeval_s_*.report.json` latest shows `"n": 1`, ci95 [0.207, 1.0]).
   The gap is published runs + sealed-key discipline, not machinery.

### Competitive position

- gbrain: local, single-user, markdown-first, self-host only, young with
  frequent breaking changes, no real permission model, "does not verify
  correctness or completeness" (external reviews, ⚠️ single-source class).
- Mem0/Supermemory: publish read-side 90%+, measured write-side 41–43%.
- MemPalace: publishes LongMemEval 96.6% raw / 98.4% held-out (Haiku
  reranker), LoCoMo 100% ("structurally guaranteed (top-k > sessions)").
- **Nobody publishes why-aware recall** (support chain + conflict structure
  + trade-offs surfaced at recall time). ⚠️ hypothesis (low confidence,
  external scan only): the W4 headline is genuinely novel — which is both
  the opportunity and the reason W7's discipline matters (nobody can audit a
  claim nobody else has made).

---

## UX Pattern Research

### gbrain's injection UX (W3/W4 pushed channel)

- Pointer block markdown (`src/core/context/retrieval-reflex.ts:577–586`):
  `## Brain pages mentioned this turn` + "Open the page before relying on
  details — do not answer from memory" + `- **Display** → \`slug\` —
  one-line synopsis (use get_page before relying on details)`.
- **Detect + point, NEVER auto-dump the body** — the agent makes the
  deliberate `get_page` call. This is the load-bearing UX decision: the
  reflex's job is *when to inject*, the agent's job is *what to read*.
- 3-pointer default budget, 5 max; re-mention suppression (already-surfaced
  slugs never re-injected); privacy-safe synopses (frontmatter summary else
  fenced-stripped first sentence, ≤160 chars, world-visibility only).
- Window salience (v0.43): extraction widens across last 4 turns; user-role
  mentions outrank assistant-only; the user's own surface form wins the label.
- Claude Code seam: `UserPromptSubmit` hook → `additionalContext`; Codex
  seam: static entity-index preamble + ≤1 per-turn fragment (preamble slugs
  deliberately not counted as injections — anti-gaming).
- **Anti-pattern documented in-repo:** the first claude-code hook had no
  conversation memory → 0.023 false-fire re-injections; fixed by shipping
  real cross-turn dedupe.

### Tortoise mapping for W3/W4

- Tortoise's seams are the MCP tools (`tortoise_search/ask/recall/analyze`),
  the `claude-hooks/session-{start,end}.sh` lifecycle hooks, and
  `session_import`. The W3 harness should replay fixtures against THESE
  seams, not abstract contracts.
- W4 pushed-channel UX: gbrain's pointer block is the proven shape — but
  Tortoise's version should inject *why-context* (one-line support count +
  contested flag + dig-deeper pointer), not just page pointers. Epic open
  question "dig-deeper pointers as a separate 'explore' block vs inline" —
  gbrain's block-with-synopsis is the precedent for a bounded block; inline
  would fight the answer text. Recommend: bounded "explore" block appended
  after the answer (ask surface) / after the pointer list (reflex), ≤3
  pointers, each one a labeled action ("read supports", "read the
  counterargument", "see what changed").
- Default-on vs default-off for auto-ingestion: gbrain has no onboarding
  surface precedent (CLI tool, single user). #1976's disclosure-checkpoint
  precedent remains the governing source for W6 — gbrain contributes nothing
  new here (SKIP).

---

## Workflow Pattern Research

### The fix-wave protocol (W2/W7 core)

gbrain's demonstrated loop: plant gold → run → publish the BAD number
(61.5%) → name the failure classes (triage misses on buried-signal
transcripts, paraphrase-inside-quote-marks, missing `idea` fact kind) → ship
a targeted fix wave → re-run on the SAME frozen corpus + pinned judge →
88.1%. Two bracketing runs because the published baseline was 62 commits
stale — an honest re-anchor before claiming the delta. **The benchmark must
be able to fail, and the failure must be published.** Tortoise's W2 gate
"CI-gated baseline that can fail" is exactly this.

### The sealed-gold / receipts / baselines loop (W3/W7)

1. Fixtures carry ONLY adapter-visible fields; a `gold` key inside a fixture
   turn is a validation error (`src/eval/brainbench/types.ts`, `fixtures.ts`).
2. `fixtures_hash` covers fixture AND gold files — a gold-only edit
   invalidates baselines (prevents silent answer-key changes).
3. Committed baseline `main.json` with `justification` REQUIRED to bless a
   regression (review-enforced in the PR diff); `--compare` gate in CI;
   corpus-bless mode for intentional fixture changes; config mismatch →
   `inconclusive`, never a rubber-stamp.
4. Every runner writes a validated receipt (run_status, verdict,
   failure_origin, gbrain_pin, hashes, resolved_config); umbrella aggregates
   receipts not exit codes; skipped never counts as pass.
5. Baseline refresh discipline: "include a `Why:` line in the commit body so
   future maintainers can audit the trail. Without that discipline, the gate
   degrades to rubber-stamp within months" (`gbrain-evals/baselines/README.md`).
6. Judge discipline: pinned judge prompt version in the receipt;
   judge-blindness (coverage judge sees paraphrase-level statements only,
   never verbatim anchors — salience scoring can't degrade into lexical
   matching); mechanical checks authoritative over judge output ("LLM judges
   are weakest at detecting unfaithfulness" — quote-fidelity, anchor
   grounding, claim segmentation, distractor scan all deterministic).
7. Errata policy: errors corrected with annotations, historical numbers
   preserved ("Issued errata instead of silently editing history").
8. comparison-systems.md rules: neutral cited tables; mechanism-named
   "vs" analysis; no win claims on benchmarks not run; metric-mixing
   explicitly flagged (QA-acc ≠ R@k ≠ recall_all@k).

### The 35-agent audit pattern

17 subsystem auditors + independent adversarial re-verification + a
completeness critic + outside-model review rounds; machine-readable findings
JSON with per-finding evidence. 237 confirmed / 2 refuted. This is the model
for W7's "self-auditing" posture and could be reused as a one-off for
Tortoise's own eval suite before publishing any headline.

---

## Tech Stack Research

### What maps 1:1, what needs adaptation (per workstream)

| gbrain artifact | Tech | Tortoise adaptation |
|---|---|---|
| Hybrid retrieval (FTS+vector+RRF, source boost, adaptive return) | Postgres ts_rank_cd, pgvector HNSW, RRF k=60, 0.7/0.3 cosine blend | Tortoise `search_engine.py` already has FTS+vector+structural+RRF + EP annotation. **ADAPT:** source-boost *prefix map* + adaptive return-sizing are cheap, useful additions; the intent-cap rationale ("rank1→rank2 gap is mechanical decay, not a separatrix" — `return-policy.ts:12–20`) is a warning against score-cliff heuristics. |
| Reflex (salience → resolve → gate → budget → suppress) | Regex + SQL arms, deterministic | **ADAPT for W3/W4:** the salience/resolve/gate/budget/suppress skeleton ports directly; the arm-confidence table is a hand-written salience model that Tortoise's EP can formalize (support+contentiousness scoring instead of alias-vs-title heuristics). |
| Dream write path (triage → synthesis; facts fence; receipts) | Cheap-model triage gates frontier synthesis; content-hash dedup; provenance stamping | **ADAPT for W2/W5:** Tortoise already dreams (EP stabilization) but has no triage-gate + verified-segment rescue on session→graph write-back; content-hash dedup keys and provenance stamping port directly. |
| MEMORY_VERBS_v1 | Frozen verb protocol, `protocol_version` in every response, enumerated error codes + `suggestion` | **ADAPT for W5:** a frozen, version-stamped write verb (provenance REQUIRED, kind enum, status branch contract) for the opt-in ingestion toggle. |
| BrainBench (Cat 34) harness | Fixture/gold/baseline JSON schemas, one in-memory DB per run, adapter-per-seam | **ADOPT for W3:** schema shapes + sealed-gold discipline + know-to-ask/false-fire metrics + holdout split are directly portable (MIT ideas, no attribution needed). |
| Cat 35 runner | 3 write lanes, verbatim control as judge ceiling, mechanical checks + blind judges, cost-bounded (HARD_STOP_USD) | **ADOPT for W2:** planted gold with verbatim anchors + distractors + hazards; lane design (verbatim control calibrates the judge); bootstrap CI; cost caps. |
| Receipts/baselines/CI gate | Validated receipts, committed baselines, corpus-bless, justification | **ADOPT for W7.** |
| comparison-systems.md | Neutral cited tables + mechanism-named analysis | **ADOPT for W7.** |
| Errata | Any-hit → recall_all erratum; audit trail | **ADOPT for W7** — and Tortoise must publish its OWN semantics divergences (dataset_audit.py already records them; they must be in every published report). |

### Licensing (gate already completed 2026-08-31, re-confirmed 09-01)

- Both repos MIT (Copyright 2026 Garry Tan). Dependency trees standard
  permissive. Eval corpora "fully made up and free to redistribute";
  PrecisionMemBench vendored artifacts MIT with ATTRIBUTION.md.
- Vendoring actual code/corpus files → preserve MIT notice + attribution, no
  endorsement implied. Reimplementing IDEAS (schema shapes, gold conventions,
  metric formulas, receipts discipline) → no obligation. **Recommendation:
  reimplement ideas, do not vendor code** — the ideas are small (schemas,
  metrics) and Tortoise's stack (Python/FalkorDB vs TS/PGLite) makes vendoring
  pointless. Any corpus file copied verbatim (e.g. a cat-35-style transcript
  corpus) must carry the MIT notice.
- gbrain is NOT on npm (the npm `gbrain` is an unrelated GPU JS lib) — a
  git-pinned SHA is the only install path if a runtime dependency ever
  appears (not recommended).

---

## Adversarial check — README claims vs the code/erratum

| Claim | Verdict | Evidence |
|---|---|---|
| "97.6% LongMemEval SOTA" | **OVERSTATED — walked back via erratum.** Any-hit R@5 ≠ official `recall_all@5`; for the 133 multi-session questions any-hit is strictly looser. Corrected full-500 number NOT re-measured yet. Runner now computes recall_all; re-measurement pending. | `gbrain-evals/docs/benchmarks/2026-05-07-longmemeval-s.md` erratum block; official `eval_utils.py`: `recall_all = all(doc in recalled_docs for doc in correct_docs)` (fetched). The README tables DO carry the any-hit annotation (corrected 2026-08-31) — the claim is flagged, but the top-of-README headline table still leads with "97.6% R@5" and the "Best published no-LLM retrieval score" framing. |
| "Cat 35: 61.5% first run → fix wave → 88.1%" | **TRUE with a caveat.** The 61.5% was published 2026-08-25 (v0.46.3.0); the fix-wave comparison was re-anchored against a FRESH pre-wave master at 70.2% (the published baseline was 62 commits stale). Honest delta: 70.2% → 88.1%. 88.1% [82.0–93.5] macro, strict 82.1%, 20/20 sessions emit, quote fidelity 82.7%, hallucination 7.0%, distractor leakage 1.2% (1/86), usability 90.8%, $6.36. Judge-calibration hand-scoring gate still open. | Cat 35 report update block (2026-08-31). |
| "Cat 34: 0 failures" | **TRUE for the current committed CI baseline (0/149 kta, false fire 0.000, push P 1.000, wb 1.000, continuity 1.000, isolation 0) — but the FIRST published run was 0.150 know-to-ask failure and 0.023 claude-code false fire.** The zeros are post-v0.46.15 fix-wave. Push recall is NOT 1.0: 0.906 (openclaw) / 0.552 (codex) — the README's "push precision 1.0" omits recall. | Cat 34 report update banner + first-run table. |
| "graph +30pts precision" | **PLAUSIBLE but loosely sourced.** It's a mechanism note in the LoCoMo section of comparison-systems.md ("worth ~30 points of precision over plain vector on our relational benchmark"), backed by the in-house 240-page relational benchmark (README: 97.9% R@5 / 49.1% P@5 vs plain vector "38 points less precision"). NOT a public-benchmark head-to-head; the exact benchmark doc should be cited before any Tortoise use. | `comparison-systems.md:159`; README relational row. |
| "Self-auditing suite, 239 findings" | **TRUE.** 237 confirmed + 2 refuted; 17 critical (wrong scores / measures nothing / crashes); four runners crashed against pinned gbrain; ~a dozen evals structurally could not fail; confounded A/B cells (ambient reranker env var). The audit is the most credible artifact in the repos. | `docs/audit/2026-08-31-eval-audit.md`. |
| "Benchmarks that bite back" | **TRUE and load-bearing.** 61.5%→88.1% is the demonstration. Tortoise's W2 gate must be able to fail the same way. | Cat 35 report. |

**Net:** gbrain's numbers are internally flagged and audited better than most
projects — but the *headline* framing ("SOTA", "0 failures", "+30pts")
overstates what the receipts support. For Tortoise's W7: publish receipts,
not headlines; cite the exact commit; name the metric variant (Tortoise's
own `dataset_audit.py` records 4 divergences from official LongMemEval —
including a per-question-fraction variant of the binary recall_all — that
must appear in every published report).

---

## Adopt / Adapt / Skip map per workstream

### W1 — Learnings map doc
- **ADOPT** (this brief + raw-notes). Verdicts above + licensing note.

### W2 — Write-path eval (Cat 35 port)
- **ADOPT** planted-gold shape: items with `kind ∈ {fact, idea, decision,
  vibe, entity}`, `verbatim_anchor`, `notability`, `depth_bucket`;
  distractors (true-but-routine) + hazards (attribution traps); sealed gold
  + `_manifest.json` sha256s.
- **ADOPT** lane design: verbatim control lane as judge ceiling (93% — the
  judge can't grade above it); facts lane + dream lane as the graded paths.
- **ADOPT** mechanical checks as authoritative over judge output:
  anchor-present substring, quote fidelity (all quotes must ground),
  hallucination denominator via claim segmentation, distractor scan,
  scaffold-contamination line-diff. Judge-blindness (no verbatim anchors to
  the salience judge).
- **ADAPT** lanes to Tortoise's pipeline: `session_import` → graph write-back
  (points + operators + EP updates) instead of gbrain pages; Tortoise's
  "page" = point-with-support. Salient-unit survival = does the point (or a
  REPHRASE-linked point) survive with provenance + EP update?
- **ADOPT** cost-bounded runner (HARD_STOP_USD) + BPRE default + `--full`
  opt-in; fix-wave protocol: publish the bad number, name failure classes,
  ship targeted fix, re-run same frozen corpus + pinned judge.
- **ADAPT** targets: epic says ≥80% salient-unit survival / zero distractor
  leakage — gbrain reached 88.1% with 1/86 distractor leakage (1.2%), so
  "zero leakage" needs a definition of leakage tolerance (single-item judge
  variance on borderline mentions was reported at 1/86). Recommend target:
  macro survival ≥80%, strict ≥75%, leakage ≤1 distractor per run, sessions
  emitting = 100%, quote fidelity ≥80%.

### W3 — Volunteering-memory harness (Cat 34 port) + why-layer
- **ADOPT** fixture/gold schema shapes, sealed-gold discipline (gold dir,
  `gold` key in fixture = validation error), `fixtures_hash` covering both,
  holdout split (~15%), one-DB-per-run hermetic replay, deterministic seed.
- **ADOPT** know-to-ask failure + false-fire anti-gaming pair; push
  precision/recall under a pointer budget; write-back fidelity +
  provenance accuracy; continuity pairs (writer fixture → write-back →
  reader fixture, scores on the reader cell); source-isolation violations at
  zero (Tortoise already has multi-source/team graph support — the
  cross-source gate ports directly).
- **ADOPT** harness seams = REAL integration points: MCP tools
  (search/ask/recall), claude-hooks, session_import — not abstract
  contracts. Env-key stripping for hermetic runs; anti-ambient-reranker
  discipline (Tortoise: strip provider keys so no silent LLM path).
- **ADAPT** gold conventions → EP rules. gbrain's gold encodes a salience
  model: re-mentions demote ("Great, thanks. That covers it." → no fire);
  below-notability openers don't fire ("Good morning! Hope the weekend was
  restful." → no fire); unknown entities don't fire; superseded facts
  shouldn't surface. In Tortoise these map to: **re-mention suppression =
  prior-context slug/title check (already in gbrain's resolver; Tortoise
  needs the same on MCP ask/search sessions); notability bar = EP
  confidence floor + no-support check; supersession = status filter
  (already in `tortoise_search` current-view defaults) + SUPERSEDE downrank.
  Decide in W3 scoping which stay harness annotations vs become EP rules —
  recommend EP rules for supersession (already exist) and re-mention
  (session-scoped), harness annotations for the notability bar.**
- **ADD (the Tortoise differentiator)** the why-layer suite: given ONLY the
  surfaced context for a state with planted conflicts (NANDs, superseded
  predecessors, contested alternatives), grade "what contradicts this?" /
  "why is this believed?" / "where do I dig deeper?" — conflict-surfacing
  rate ≥0.95 and dig-deeper navigation accuracy. gbrain has no analogue; the
  suite design must be Tortoise-original (the fixture generator + gold
  conventions for planted conflicts are new).
- **SKIP** gbrain's codex fragments seam unless Tortoise ships a codex
  integration (no evidence of one); keep the anti-gaming lesson (preamble
  slugs don't count).

### W4 — Why-aware recall (HEADLINE, existing surfaces only)
- **ADAPT** the reflex skeleton (when to inject, pointer budget, re-mention
  suppression) as the pushed channel — but the salience DECISION becomes EP
  support+contentiousness scoring, and the injected CONTENT becomes
  why-context (support chain + EP weight, active NANDs, supersession, one
  dig-deeper pointer), not page pointers.
- **Existing-tool fit audit (gate before build) — result:**
  - `tortoise_search` ALREADY returns: EP breakdown (confidence_mean,
    variance, contested, has_ep), evidence counts (impl/nand), 1-hop
    relationships with `related_content`, status + superseded_by +
    supersedes + valid_from/valid_to (bi-temporal), subject.
  - `tortoise_analyze` ALREADY answers "where is the disagreement?" /
    "what supports claim X?" (pattern-based {answer, raw, pattern}).
  - `tortoise_ask` reader already receives supersession validity markers
    (`[valid <from> → <to>]`, retrieval.py:274) and searches with
    `include_terminal=True`.
  - **GAPS (fill in-place, no new tool):**
    1. **Contentiousness is not a recall signal** — "Contestation is
       surfaced, never scored" (`mcp_server.py:1018`; `ranking.py:583,
       768`). W4's core: make variance/contested weight ranking (both a
       boost when conflict is RELEVANT to the query and a why-context trigger
       when a contested point is recalled).
    2. **No assembled support-chain narrative** — counts + 1-hop exist;
       the multi-hop "why is this believed" chain (with ledger evidence)
       needs assembly in the existing render path.
    3. **No trade-offs + mitigations for decision points** — the
       tortoise-decide surface holds alternatives + EP weights +
       mitigations; search/ask don't surface them. This is an EP data
       join, not a new surface.
    4. **No explicit dig-deeper navigation pointers** — raw material
       exists (relationships/related_content); assemble labeled pointers
       ("read supports", "read the counterargument (NAND)", "see what
       changed (superseded)") in the existing result/answer render.
    5. **ask is gated off by default** (#2013) — W4's ask-surface work
       must either wait for or coordinate with the ask exposure decision;
       search/analyze surfaces are unblocked today.
  - **Conclusion: no new tool needed.** All four W4 context types (why /
    conflict / trade-offs / dig-deeper) are fillable inside ask/analyze/
    search/MCP. If scoping finds the assembled why-block needs a distinct
    render shape, that's a render option inside an existing tool, not a new
    surface — and any genuinely new surface still requires Daniel's sign-off
    per the epic constraint.
- **ADAPT** recall signals: gbrain ranks by lexical+semantic relevance only;
  Tortoise adds confidence AND contentiousness (the epic's core thesis —
  a highly contended point is exactly when why-context matters most).

### W5 — Automatic memory ingestion
- **ADAPT** gbrain's write-path mechanics: content-hash dedup keys
  (extract-atoms), provenance REQUIRED + `source_session` stamping
  (MEMORY_VERBS `remember`), facts-fence reconciliation (extract-facts
  idempotency), triage-with-verified-segment rescue (only admit sub-gate
  content the judge's own quotes verify) — this is the anti-hallucination
  gate Tortoise should port into session→graph write-back.
- **ADAPT** a frozen, version-stamped write verb shape (protocol_version in
  every response; enumerated error codes + suggestion) — Tortoise's MCP
  write tools gain a stable contract that the eval harness and the opt-in
  ingestion both speak.
- **SKIP** gbrain's onboarding surface (CLI, no precedent) — #1976 governs
  W6 toggle placement; gbrain contributes nothing new.

### W6 — Onboarding + Settings toggles
- **SKIP gbrain entirely** (no product onboarding precedent; single-user
  CLI). #1976 is the governing dependency: build W6 against its output
  (Settings → Memory sources, agent just-in-time proposals, disclosure
  checkpoint), never the current wizard. Do not start until #1976 ships or
  is clearly blocked (epic constraint re-confirmed).

### W7 — Public benchmark discipline
- **ADOPT wholesale** the receipts/baselines/errata/comparison machinery:
  validated receipts naming the commit + pinned judge version + corpus hash;
  sealed answer keys at the boundary; `recall_all@5` official semantics
  (not any-hit); comparison-systems.md with mechanism-named rows, neutral
  cited tables, no win claims on benchmarks not run, metric-mixing
  explicitly flagged.
- **ADOPT** the audit-before-publish pattern (35-agent audit) as a one-off
  for Tortoise's own eval suite before any headline.
- **ADAPT** Tortoise's LongMemEval semantics record: `dataset_audit.py`
  already enumerates 4 divergences from official — the report gate (raise
  without the audit record) is the right pattern; extend it to the full
  500-Q sealed run with official `recall_all@5` keys. Publish the n=1 smoke
  reports' successor: a real 500-Q run with sealed keys + erratum policy +
  the exact commit named.

---

## Assumptions Register

| # | Assumption | Confidence | Source | Validation plan |
|---|---|---|---|---|
| A1 | gbrain's write-path measurement pattern transfers to Tortoise's session→graph pipeline (points + operators + EP updates ≈ pages + facts) | **medium** | Cat 35 + Tortoise pipeline read; unit of "salient unit" differs (page vs point) | W2 pilot: 2-transcript BPRE run against session_import; verify gold anchors survive into points |
| A2 | The reflex skeleton (salience → gate → budget → suppress → pointer block) ports to Tortoise's MCP/ask surfaces without a new tool | **medium** | gbrain resolver + Tortoise MCP read | W3 harness on Tortoise's actual seams; W4 fit audit above says gaps are in-place-fillable |
| A3 | EP confidence + contentiousness can score the "when to volunteer" decision at least as well as gbrain's arm-confidence table (0.000 kta baseline) | **medium (unverified)** | EP property tests exist but no reflex-grade eval | W3 why-layer + know-to-ask suites on EP-scored reflex vs gbrain's 0.150-first-run / 0.000-current benchmarks |
| A4 | Recall driven by contentiousness improves user-visible recall quality (the W4 thesis) | **low (unverified)** | No external precedent found (⚠️ hypothesis) | W4 eval: conflict-surfacing rate ≥0.95 on planted conflicts; A/B vs confidence-only ranking |
| A5 | Official `recall_all@5` on 500 LongMemEval questions is achievable at acceptable cost (embeddings cache, ~$2 first run per gbrain's erratum) | **high** | gbrain erratum; Tortoise runner exists (tools/longmem_eval) | W7 step-5 run via run_protocol.py (already a 9-step resumable state machine) |
| A6 | Tortoise's own semantics divergences (per-question fraction vs binary recall_all; _abs inclusion; assistant-role turns) can be reconciled with official metrics in one published run | **medium** | dataset_audit.py records 4 divergences | W7: compute BOTH official keys and legacy keys in the sealed run; document as variant (gbrain precedent) |
| A7 | Judge costs for a Cat-35-style suite are acceptable on CI (gbrain: ~$0.10 BPRE, $6.36 full) | **high** | gbrain receipts | Mirror BPRE default + full opt-in + HARD_STOP_USD |
| A8 | Re-mention suppression + supersession filtering already work in Tortoise's surfaces (search current-view, status fields) | **high** | mcp_server.py docstring, sdk.py lifecycle guards | W3/W4 scoping: confirm on the ask/search render paths |
| A9 | Vendoring gbrain code/corpora is unnecessary; reimplementing ideas carries no license obligation | **high** | MIT read, epic licensing gate | W1 doc records the gate; any copied corpus file carries MIT notice |
| A10 | W6 can wait for #1976 without blocking W2–W5/W7 | **high** | epic dependencies section; #1976 close to shipping | Do not start W6 until #1976 ships/blocked (epic constraint) |
| A11 | The why-layer suite (W3 add-on) is gradeable from surfaced context alone, without the full graph | **medium** | conflict-surfacing rate design; EP endpoints expose NANDs/contestation | W3 pilot: does surfaced context contain enough to answer the 3 why-questions? If not, the W4 context assembly must change first |
| A12 | Tortoise's ask surface gating (#2013) will resolve in favor of exposure (or W4 can route through search/analyze meanwhile) | **low (unverified)** | sdk.py:10505 "do not build production features on it until the reader-model decision is made" | W4 scoping: confirm the ask exposure decision; search/analyze surfaces are the unblocked path |

**Top 3 assumptions/risks for the epic:**
1. **A3/A4 (the W4 thesis is unverified and gbrain has no precedent)** — EP
   scoring the reflex + contentiousness-driven recall is the novel half; it
   needs its own eval before it can be claimed (that's exactly the W3 why
   suite + W7 discipline). Risk: the why-context assembly (A11) is harder
   than the reflex port.
2. **A2/A5 (existing surfaces + official metrics)** — the W4 no-new-tool
   constraint holds (fit audit says gaps are in-place-fillable), but the ask
   surface is gated (#2013, A12) and Tortoise's LongMemEval semantics
   diverge from official in 4 documented ways (A6) — both must be resolved
   before the headline numbers are publishable.
3. **A1 (unit mismatch in the write-path port)** — gbrain grades page
   artifacts; Tortoise grades points + operators + EP updates. "Salient-unit
   survival" must be defined at the point level (incl. REPHRASE-linked
   dedup) or the W2 target becomes ambiguous.

## Raw Notes

See `raw-notes.md` in this directory — the append-only codebase walkthrough
with file paths, line refs, quotes, and the line-ref quick index.
- **2026-09-01T09:31:08** [competitor] AXIS NOTE (W4 contention-driven recall): no shipping product found that surfaces belief conflicts/why at recall time (matches brief A4 low-confidence hypothesis). BUT academic precedent EXISTS for conflict-aware memory and controversy-aware retrieval: (1) Conflict-Aware Memory Primitive (arXiv 2608.08236) marks incompatible claims SUPERSEDED/CONTESTED with provenance at write time; (2) Graph-Native Cognitive Memory with formal belief revision (arXiv 2603.17244) surfaces current AND superseded beliefs at retrieval; (3) Belief Memory under partial observability (arXiv 2605.05583) keeps multiple candidate conclusions with probabilities surfaced together; (4) controversy-aware retrieval/reranking literature (CEUR-WS Vol-2936 paper-210, Webis axiomatic argument re-ranking, ACL 2024 indexical bias) boosts stance-labeled pro/con docs. Implication for scope: the W4 thesis is academically grounded (mechanism exists in IR literature) but remains novel as a shipped agent-memory product feature; the conflict-surfacing E2E is implementable (precedent), the why-layer product claim is still the differentiator. See scope doc ### Axis Research Notes.
