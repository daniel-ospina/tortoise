# Raw Notes — gbrain/gbrain-evals codebase walkthrough (epic #2080)

> Append-only evidence ledger behind `research-brief.md`. Entries are
> chronological (oldest → newest within this file). Source tags: `[gbrain]` =
> `/tmp/gbrain-deep-epic` (github.com/garrytan/gbrain @ v0.47.9.0 pin, clone
> 2026-08-31/09-01), `[evals]` = `/tmp/gbrain-evals-deep-epic`
> (github.com/garrytan/gbrain-evals), `[tortoise]` = this repo
> (daniel-ospina/tortoise), `[web]` = external. Clones were `--depth 1`, so
> line numbers are against the tip-of-branch snapshots, not a released tag.

---

## 2026-08-31T09:00Z — [gbrain] Repo shape + licensing

- Both repos MIT, `Copyright (c) 2026 Garry Tan` (LICENSE, 1-line MIT).
- gbrain `package.json`: `"name": "gbrain", "version": "0.47.9.0"`. **gbrain
  is NOT on npm**: `npm view gbrain` resolves to `gbrain@1.3.1` "GPU
  Javascript Library for Machine Learning" (stormcolor/gbrain) — unrelated.
  Git-pinned SHA is the only install path (epic's licensing note confirmed).
- gbrain is TypeScript/Bun, PGLite (embedded Postgres via WASM) or Postgres +
  pgvector, markdown files as source of truth. `src/` ~100 dirs.
- Key dirs for this research: `src/core/search/` (retrieval), `src/core/context/`
  (reflex), `src/core/cycle/` (dream), `src/core/verbs.ts` (MEMORY_VERBS),
  `src/eval/brainbench/` (Cat 34), `evals/brainbench/` (committed fixtures +
  gold + baselines), `src/commands/eval-brainbench.ts` (CI gate).

## 2026-08-31T09:05Z — [gbrain] Hybrid retrieval pipeline (W4/W7 relevance)

- `src/core/search/hybrid.ts` — header comment (lines 1–7): "Pipeline:
  keyword + vector → RRF fusion → normalize → boost → cosine re-score →
  dedup. RRF score = sum(1 / (60 + rank_in_list)). Compiled truth boost: 2.0x
  ... Cosine re-score: blend 0.7*rrf + 0.3*cosine".
- `RRF_K = 60` (line 68); `COMPILED_TRUTH_BOOST = 2.0` (line 75);
  `PRE_FUSION_POOL_FLOOR = 50` (line 78).
- `shouldBoostCompiledTruth(detail)` (line 102): boost applies ONLY at
  `detail === 'low'` — a documented past bug where the boost at default
  detail made search categorically compiled-truth-only (the comment block
  lines 90–101 is a great cautionary tale for Tortoise's own ranking knobs).
- `rrfFusionWeighted`/`rrfFusion` (lines 2798/2850): rank-score fusion,
  normalize by observed max, then boost compiled_truth 2.0x, skip boost for
  `unverified` auto-extracted stubs (issue #160).
- `cosineReScore` (line 2907): `0.7 * norm_rrf + 0.3 * cosine` blend, run
  before dedup. Chunkless rows (embed_skip'd oversized pages) route through
  the SAME blend with cosine=0 (line ~2950, #3695 fix — no structural 2x
  head start).
- `src/core/search/rerank.ts` — cross-encoder rerank slot between dedup and
  token budget (lines 1–20). Fail-open on every error class; logs to
  rerank-audit JSONL and returns original RRF order (lines 110–150). Default
  OFF in conservative/balanced modes, ON for tokenmax (line 34–39).
- `src/core/search/return-policy.ts` — adaptive return-sizing (v0.42):
  intent-driven caps (`entity`→entityMax=2, `temporal/event/general`→
  otherMax=6), `minKeep=1` failsafe. **Crucial design note (lines 12–20):**
  they measured that the rank1→rank2 score gap is ~identical whether rank-1
  is correct (0.602) or wrong (0.569) — "RRF's mechanical decay, not a
  trustworthy separatrix. The right belief is rank-1 in 94% of single-answer
  cases, so 'return a tight set' is the whole win; cliff-cutting just adds
  noise." Default OFF.
- `src/core/search/source-boost.ts` — `DEFAULT_SOURCE_BOOSTS` (lines 17–60):
  `originals/` 1.5, `writing/` 1.4, `concepts/` 1.3, `people|companies|deals/`
  1.2, `meetings/` 1.1, `media/articles/` 1.1, `yc|civic/` 1.0, `daily/` 0.8,
  `media/x/` 0.7, `openclaw/chat/` 0.5 (swamp), `archive/` 0.5 (demote-not-
  exclude, #1777), `extracts/` 0.3 (extract_receipt pages). Hard excludes:
  `test/`, `attachments/`, `.raw/` (lines 63–69). Env overrides
  `GBRAIN_SOURCE_BOOST`, `GBRAIN_SEARCH_EXCLUDE`.
- Other search layers (read-only note): `exact-lookup.ts` (exact tier),
  `two-pass.ts` (anchor expansion + chunk hydration), `query-intent.ts`
  (intent classification), `intent-weights.ts` (per-intent arm weights),
  `relational-recall.ts` (graph arm), `supersede-downrank` (`SUPERSEDE_PENALTY
  = 0.5`, hybrid.ts line 731 — superseded pages demoted 0.5x), `graph-signals.ts`,
  `token-budget.ts`, `autocut.ts`, `recency-decay.ts`, `query-cache.ts`.
- **Tortoise mapping:** Tortoise `search_engine.py` already has rrf_fusion
  (line 871), FTS + vector + structural arms, recency decay, EP annotation —
  the two pipelines are structurally similar; gbrain's differentiators are
  the source-boost *prefix map* (Tortoise has tiers but not slug-prefix
  boosts) and adaptive return-sizing (Tortoise: `tortoise_search limit`).

## 2026-08-31T09:15Z — [gbrain] The Reflex — per-turn volunteering (W3/W4)

- `src/core/context/reflex.ts` — orchestrator. `DEFAULT_WINDOW_TURNS = 4`
  (line 73); `TIMEOUT_MS = 1500` per-turn ceiling (line 106); zero-candidate
  fast path (regex only, no brain touch, lines 145–148); fully fail-open
  (line 192: `catch { return null; }`).
- Resolver ladder (lines 207–223): (1) host-injected `resolveEntities`
  (ctx.brainQuery — the OpenClaw plugin seam), (2) PGLite → serve resolve IPC
  socket, (3) Postgres → cached direct connection (one per process,
  cooldown 60s on failure, lines 228–266), (4) disabled.
- Kills switches: `GBRAIN_RETRIEVAL_REFLEX=false`, window turns,
  `GBRAIN_RETRIEVAL_REFLEX_LEXICAL_ARMS` (case-insensitive `false|0|off|no`
  parse, line 135).
- `src/core/context/entity-salience.ts` — zero-LLM extractor. Regex passes:
  @handles, capitalized token runs (`\p{Lu}`-anchored, up to 4 tokens),
  lowercase weak tokens (v0.46.15 identity wave), CJK n-grams (#3746).
  Budgets: `MAX_CANDIDATES = 12` strong, `MAX_WEAK_CANDIDATES = 32`,
  `MAX_CJK_WEAK_CANDIDATES = 24` (lines 80–92). HARD stopwords + SOFT
  common-words lists (lines 95–145). Precision-biased; documented limits
  (lines 9–32): lowercase names only resolve via the alias arm, no pronoun
  coreference. Header quotes the BrainBench receipt: "the v1 know-to-ask
  failure rate 0.150 measured against these limits went to 0.0000 after the
  v0.46.15 arms".
- Window salience (lines 312–402): `extractCandidatesFromWindow` — merge
  per-turn extraction over last N turns; weight = recency dominant +
  min(occurrences,4)*0.1 + user-role 0.15; strong strictly above weak.
- `src/core/context/retrieval-reflex.ts` — resolver. `DEFAULT_MAX_POINTERS =
  3` (line 40). Resolution arms + confidence (`ARM_CONFIDENCE`, lines 69–74):
  `alias: 0.9, title: 0.8, 'title-surname': 0.72, 'cjk-title': 0.72,
  'slug-suffix': 0.6`. Resolution ladder: alias-first exact unique
  (per-source; weak norms need GLOBAL uniqueness + fail-closed on partial
  visibility), then exact title / slug / slug-suffix, then surname arm
  (person pages only), then CJK exact-title/slug.
- Suppression: pointers whose slug (or, in window=1 mode, title whole-word)
  appears in PRIOR context are suppressed — current turn deliberately
  EXCLUDED from priorContextText (lines 116–122, 316–323). Window mode uses
  `slug-only` suppression (codex D7).
- Synopses are privacy-safe: frontmatter `summary` else body with takes/
  private-fact fences stripped, first prose sentence, ≤160 chars (lines
  345–375). "Never returns raw compiled_truth."
- Pointer block markdown (`renderPointerBlock`, lines 577–586): `## Brain
  pages mentioned this turn ... Open the page before relying on details — do
  not answer from memory.` then `- **Display** → \`slug\` — synopsis (use
  get_page before relying on details)`.
- ACCEPT-side-only feedback logging (lines 403–435): pointers that timed out
  client-side are never logged as injected (won't inflate "volunteered"
  counts or drag measured precision toward zero).
- `src/core/context/volunteer.ts` — push channel (volunteer_context op,
  reflex window path, `gbrain watch`). `VOLUNTEER_DEFAULT_MAX_PAGES = 3`,
  cap 5; `VOLUNTEER_DEFAULT_MIN_CONFIDENCE = 0.7`;
  `VOLUNTEER_SALIENCE_BOOST = 0.05` for ≥2-turn or newest-turn mentions
  (lines 20–25). At default gate, slug-suffix (0.6+0.05 < 0.7) never
  volunteers. Rationale is a deterministic template string, never raw
  conversation text.
- **Tortoise mapping (W3):** gbrain's reflex decision = deterministic
  entity-resolution + arm-confidence, NOT EP. Tortoise's EP can score the
  SAME decision with more signal: the volunteer gate maps to EP
  support+contentiousness; gbrain's `ARM_CONFIDENCE` is a hand-written
  salience model (alias>title>surname>slug) that EP confidence + variance
  can express more honestly. The 3-pointer budget + re-mention suppression +
  fail-open fast path are directly portable to Tortoise's MCP/session
  surfaces.

## 2026-08-31T09:25Z — [gbrain] Dream-cycle write path (W2/W5)

- `src/core/cycle/synthesize.ts` — triage → synthesis. Cheap-model triage
  gates frontier-model synthesis (header lines 3–47). `TRIAGE_VERSION = 2`,
  `DEFAULT_TRIAGE_THRESHOLD = 0.5`. Buried-signal rescue: a sub-gate score is
  admitted only when the triage judge's own quoted segments verify as
  substrings of the transcript (`triage-rescue.ts`, `DEFAULT_RESCUE_FLOOR`,
  `DEFAULT_RESCUE_MIN_SEGMENTS`). Idempotency via `dream:synth-v2:...:hash16`
  job keys. Hash-deterministic transcript chunker `splitTranscriptByBudget`
  (line 223) — "the back-half-of-budget search window is seeded ... always
  produces identical chunks" (D9 stable chunk identity).
- `src/core/cycle/extract-facts.ts` — facts-lane write-back: reads `## Facts`
  fence from entity pages, maps → DB rows, dedupes by canonical
  `(claim, source)` content key, reconciles page-scoped index (insert-only
  when possible, wipe/reinsert when stale; empty-fence guard Codex R2-#7).
- `src/core/cycle/extract-atoms.ts` — LLM extractor (atoms = salient units
  from transcripts/pages; dual-source merge, dedup by contentHash,
  `DEFAULT_EXTRACT_MAX_INPUT_CHARS = 50_000`).
- `src/core/extract/receipt-writer.ts` + `rollup-writer.ts` — extraction
  receipts + rollups (provenance trail).
- `src/core/cycle/link-manifest.ts` — link manifests (auto-linking between
  pages).
- `src/core/entities/resolve.ts` + `resolve-on-save.ts` — **the "self-wiring
  typed graph, zero-LLM"**: deterministic save-time entity resolution cascade
  `exact_page → alias_exact → fuzzy_match → fallback_slugify`
  (resolve-on-save.ts lines 24–31). No LLM in the resolution loop; page
  types (people/companies/concepts/...) come from slug namespaces.
- `src/core/cycle/phases/` — phase modules (`consolidate.ts`, etc.).
- **Tortoise mapping (W2/W5):** Tortoise's write path is `session_import/` +
  `dream.py` + `memory_orchestrator.py`. gbrain's portable ideas: planted-gold
  harness over the write path (Cat 35 shape), triage-with-verification
  (buried-signal rescue), content-hash dedup keys, provenance stamping
  (`source` + `source_session` on every fact row).

## 2026-08-31T09:35Z — [gbrain] MEMORY_VERBS_v1 (protocol)

- `src/core/verbs.ts` — `MEMORY_VERBS_VERSION = 1`; `VERB_NAMES = ['recall',
  'remember', 'entity', 'synthesize', 'forget', 'context_pack', 'delta']`
  (grew 5→7 additively, protocol_version stays 1). FROZEN input enum
  `FACT_KINDS = ['event','preference','commitment','belief','fact']`
  (lines 55–62); response widens to include `idea` (migration v145).
- `remember` verb: `fact` + `provenance` REQUIRED (max 500 chars —
  "provenance is a pointer, not a transcript"), `ttl` (duration shorthand or
  ISO, NOT ISO-8601 durations), `entity` canonicalized server-side, `kind`,
  `visibility` world|private. Branch on `status` (inserted|duplicate|
  superseded), never `status_text`. Errors carry enumerated codes + populated
  `suggestion` ("agents read it and self-correct").
- `src/core/verbs/conformance.ts` + `conformance-fixtures.ts` — protocol
  conformance fixtures (the verbs are tested like a wire contract).
- **Tortoise mapping (W5):** Tortoise has no frozen verb protocol for
  session write-back; its MCP surface is tool-per-operation. A frozen
  `protocol_version`-stamped write verb (provenance required, kind enum,
  status branch contract) is adaptable for the auto-ingestion toggle
  (opt-in + provenance + EP update).

## 2026-08-31T09:45Z — [gbrain] BrainBench Cat 34 harness (W3)

- `src/eval/brainbench/types.ts` — published interchange formats. Suites:
  `know-to-ask | push | write-back | continuity`; harnesses:
  `openclaw | claude-code | codex`; `SeamKind = 'production' | 'contract'`.
  Sealed-gold rule (header lines 12–18): "A `gold` key inside a fixture turn
  is a VALIDATION ERROR, not a convenience." `PublicTurn` is built by
  `toPublicTurn` which picks exactly the adapter-visible fields.
- Fixture shape (lines 44–103): `fixture_id, suites, category, holdout,
  sources[], active_source, seed_pages[], seed_facts[], turns[], continuity{pair_id, pair_role}`.
- Gold shape (lines 119–158): `TurnGold { should_retrieve, gold_slugs,
  acceptable_slugs, gold_facts[] }`; `GoldFactSpec { gist, fact,
  entity_slug, match_keywords[], kind }`; `ContinuityDecisionGold
  { decision_id, expected_slugs, match_keywords }`.
- `fixtures_hash` = sha256 over sorted relative-path + content of every
  fixture AND gold file (line 169–172) — a gold-only edit invalidates
  baseline comparisons.
- Baseline shape (lines 188–220): `{schema_version, fixtures_hash, config{
  include_holdout, llm, harnesses, suites}, justification?, cells,
  counts}`. `justification` REQUIRED "when a regression vs the prior baseline
  is being blessed (decision 4) — visible in the PR diff, review-enforced."
- `CompareVerdict = 'pass' | 'regression' | 'inconclusive'` (line 230);
  'inconclusive' on config mismatch (holdout-inclusive or --llm baseline is
  byte-plausible under the same fixtures_hash but incomparable — red-team
  finding).
- `src/eval/brainbench/harness.ts` — ONE in-memory PGLite for the whole run,
  `resetTables()` between fixtures (eng-review D9 — per-fixture WASM cold
  boots would blow the <2 min CI budget). Continuity pairs run writer →
  production write-back → reader on a shared brain; scores land on the
  READER's cell. Adapters receive only `AdapterFixtureView + PublicTurn`.
- `src/eval/brainbench/seed.ts` — deterministic seeding (Mulberry32, seed
  42); seed failures are decision-12: run exits 2 when non-empty.
- `src/eval/brainbench/metrics/know-to-ask.ts` — `know_to_ask_failure_rate`
  = missed/should_retrieve; **`false_fire_rate`** = injected/quiet ("Without
  it, 'always inject' games the failure rate" — the anti-gaming companion).
- `src/eval/brainbench/metrics/write-back.ts` — `write_back_fidelity` (gold
  facts surviving keyword probe) + `provenance_accuracy` (surviving facts
  carrying correct source).
- `src/eval/brainbench/adapters/openclaw.ts` — production seam: the shipped
  context-engine path byte-for-byte (3-pointer budget, prior-context
  suppression, markdown pointer-block wire shape).
- `src/eval/brainbench/adapters/claude-code.ts` — production seam (v0.46.15):
  drives the REAL `UserPromptSubmit` hook over real IPC — fixture turn
  becomes hook stdin JSON `{prompt, session_id, cwd}`, output
  `{hookSpecificOutput: {hookEventName: 'UserPromptSubmit',
  additionalContext}}`; run-scoped resolve-IPC server answers over a real
  unix socket with the real shared secret; 4-turn transcript window +
  cross-turn dedupe exercised for real. Test seams documented:
  `HookIo.configOverride` + `disablePushBanner` + `userPromptDeadlineMs`
  pinned generous (10s vs production 800ms — "the production 800ms deadline
  on a loaded CI runner would read as intermittent know-to-ask misses
  (flake, not signal)"). Hot-fact entity refs `[slug]` deliberately NOT
  counted as injections (would saturate false_fire).
- `src/eval/brainbench/adapters/codex.ts` — contract seam: static
  entity-index preamble computed once (slugs deliberately don't count as
  injections — "counting an index would game recall") + ≤1 per-turn
  fragment.
- Committed corpus in-repo: `evals/brainbench/fixtures/*.fixture.json` +
  `evals/brainbench/gold/*.gold.json` (gen-kta-pos/neg-001.., gen-adv-001..,
  cont-001.., gen-cont-001.., push fixtures). Categories kta-pos, kta-neg,
  push, write-back, continuity, multi-source, adversarial; ~15% holdout
  (excluded from gate, scored only in published runs).
- Real gold conventions (from committed files):
  - `gen-kta-neg-001`: "Good morning! Hope the weekend was restful." →
    should_retrieve **False**; "someone named Halcyon Cobblewick emailed
    about partnerships. Never heard of them." → **False** (unknown entity
    below notability bar).
  - `gen-kta-pos-001`: "What did Alarico Marrowfield say about the Lumenforge
    Systems deal?" → **True** both slugs; "Great, thanks. That covers it."
    → **False** (post-answer confirmation — re-mention suppression).
  - `cont-001-widget-pass`: continuity pair; reader turn gold
    `expected_slugs: ['companies/widget-co']` + `match_keywords:
    ['pass','widget-co']`.
- `src/commands/eval-brainbench.ts` — CI gate CLI. `--compare BASE [CURRENT]`
  gate; `--update-baseline FILE` bless mode (refuses on seed failures:
  "partial cells are not a baseline"); `--justification "reason"` REQUIRED
  when blessing a regression. `DEFAULT_BASELINE_RELATIVE =
  'evals/brainbench/baselines/main.json'`. Same-hash drift detection
  (two-PR gate poisoning). Verdict lives in a local variable because PGLite
  stomps `process.exitCode` (lines 11–13).
- **Tortoise mapping (W3):** fixture/gold/baseline schema shapes are directly
  portable (MIT ideas). Tortoise harness seams = the actual integration
  points: MCP tools (search/ask/recall), the claude-hooks/ session hooks,
  session_import. The sealed-gold discipline (gold dir separate, fixtures
  carry only adapter-visible fields) maps to Tortoise's eval corpus layout.
  The know-to-ask/false-fire pair is exactly the W3 suite the epic
  specifies; Tortoise would add the why-layer suite on top (conflict-
  surfacing rate, dig-deeper navigation).

## 2026-08-31T10:00Z — [evals] Cat 34 runner + receipts (W3/W7)

- `eval/runner/cat34-brainbench-memory.ts` — foreign-runner contract: drives
  the SUBPROCESS CLI `gbrain eval brainbench --harness all --suite all --json
  --out FILE`; imports zero gbrain internals. Env keys STRIPPED
  (`OPENAI_API_KEY, VOYAGE_API_KEY, ZEROENTROPY_API_KEY, GEMINI_API_KEY,
  GOOGLE_API_KEY, ANTHROPIC_API_KEY`) so the run is hermetic and gbrain's
  'balanced' mode cannot silently enable the zerank-2 reranker off an ambient
  key. Pass criteria graded HERE, not from subprocess exit (which is 0 even
  for gold failures without --compare): fresh result document (run-unique
  nonced path, mtime verified ≥ run start — stale-artifact defense,
  audit skillopt-cats-04), `result_schema_version === 1` contract
  validation (skillopt-cats-10), every cell `gold_failed === 0` AND
  `gold_total > 0` (vacuous cells = harness error, "a rig/corpus problem"),
  all four suites present, seed_failures empty. Verdict: pass/fail; skip
  (no checkout) exits non-zero unless `--allow-skip` /
  `BRAINBENCH_ALLOW_SKIP=1`.
- `eval/runner/receipt.ts` — every runner writes a validated receipt:
  `{schema_version, benchmark_version, category, run_status (completed|
  error|skipped|not_run), verdict (pass|partial|fail), failure_origin (sut|
  harness|dependency|judge), n_total, n_scored, completion_rate, errors[],
  publishable, gbrain_version, gbrain_pin, hashes{corpus, qrels, evaluator},
  resolved_config, started_at, finished_at}`. The umbrella runner aggregates
  RECEIPTS, not exit codes; skipped never counts as pass.
- `baselines/README.md` (evals repo) — `gbrain eval gate --baseline FILE`
  hermetic-synthetic NDJSON baselines: metadata header with label, embedded
  thresholds, source_hash, row_count, mean latency; stable query_hash per
  row. Refresh discipline (gbrain D4): "When a ranking change intentionally
  moves expected slugs ... include a `Why:` line in the commit body so future
  maintainers can audit the trail. Without that discipline, the gate
  degrades to rubber-stamp within months." Exit codes 0 pass / 1 breach /
  2 usage.
- **Tortoise mapping (W7):** the receipts/baselines discipline is the core
  W7 spec: sealed answer keys at the boundary, commit-named run receipts,
  errata-not-silent-edit, comparison-systems.md rules.

## 2026-08-31T10:10Z — [evals] Cat 35 runner + judges (W2)

- `eval/runner/cat35-transcript-distill.ts` — three write-path lanes:
  `verbatim` (runTranscriptsIngest only — control/floor, calibrates
  gold+judge), `facts` (ingest → runExtractConversationFactsCore),
  `dream` (triage → runPhaseSynthesize — headline). BPRE smoke default (2
  transcripts, Haiku, ~$0.10/81s); `CAT35_FULL=1` = 24×3 lanes, ~$6.20/29
  min (Sonnet judge). `CAT35_HARD_STOP_USD = 40` pre-flight. Isolated HOME
  via mkdtemp (CWE-377-safe). Deep imports pinned to the gbrain SHA
  (package.json pin).
- Gold file shape (lines 60–80): `{schema_version, transcript_id, scenario,
  variant (prose|long-noisy), expected_triage (high|low), session_id,
  base_ts, entities[], items[{item_id, kind (fact|idea|decision|vibe|
  entity), statement, verbatim_anchor, notability (high|medium|low),
  planted_turn, depth_bucket (early|middle|late)}], distractors[{id,
  statement, anchor, planted_turn}], hazards[{id, type, wrong_claim,
  anchor, planted_turn}]}`.
- Corpus: `eval/data/transcript-distill-v1/` — 24 transcripts (JSONL
  conversations + txt), gold dir, brain-scaffold (people/companies/concepts
  pages), `_manifest.json` (sha256 per file, generator: claude-opus-4-5,
  temperature 1, seed 350001, template_hash — full provenance),
  `judge-calibration-sample.json`.
- Real gold example (`gold/coding-reflection-01.json`): 8+ items across
  kinds, e.g. `{item_id: coding-reflection-01-g01, kind: fact, statement:
  "Bug QL-4471 in quartzlane was caused by two ingest workers claiming the
  same batch.", verbatim_anchor: "two workers were grabbing the same batch
  off the ingest queue", notability: high, planted_turn: 1, depth_bucket:
  early}`. Vibe item: anchor "I have been dreading this bug all week and
  now I feel ten pounds lighter".
- `eval/runner/cat35-checks.ts` — MECHANICAL checks, authoritative over
  judge output ("LLM judges are weakest at detecting unfaithfulness" —
  FABLES). `anchorPresent` (normalized-ws substring, case-insensitive —
  "is this phrase present" is a case-insensitive question, line 50);
  `quoteFidelity` (every blockquote + inline span ≥40 chars must be a
  normalized substring of the transcript); `hasWikilink`; `slugDisciplineOk`;
  `selfContainedOpening` (first paragraph ≥2 sentences AND ≥120 chars before
  any blockquote); `segmentClaims` (hallucination denominator — sentences +
  bullets, drops frontmatter/headings/blockquotes/code/interrogatives/<5
  words); `scanDistractors`; `addedContent` (line-level diff vs seeded
  scaffold — contamination fix: grade only what the dream lane ADDED);
  `compressionRatio` (chars/4 both sides); `weightedKappa` (linearly
  weighted Cohen's kappa over FULL/PARTIAL/ABSENT — unweighted kappa wrong
  for ordered labels); `bootstrapCI` (transcript-level, Mulberry32 seeded);
  `computeDelta` (comparability guard: mode + lanes + corpus must match).
- `eval/runner/cat35-judges.ts` — batched forced-tool-use judges; judge
  BLINDNESS: "the coverage judge sees paraphrase-level statements ONLY —
  never the verbatim anchors — so salience scoring cannot degrade into
  lexical matching" (lines 40–45). `CAT35_JUDGE_PROMPT_VERSION =
  '2026-08-16-v1'` pinned in the receipt. Model: haiku default, sonnet for
  full. Per-call token + cost accounting.

## 2026-08-31T10:20Z — [evals] LongMemEval runner + erratum (W7 — adversarial)

- `docs/benchmarks/2026-05-07-longmemeval-s.md` — **ERRATUM (2026-08-31)**
  top of file: "The recall numbers in this report are ANY-HIT recall@5, not
  the official LongMemEval `recall_all@5`. Our runner counted a question as
  recalled if ANY of its ground-truth sessions appeared in the top-5. The
  official evaluator requires ALL ground-truth sessions in the top-k
  (`all(doc in recalled_docs for doc in correct_docs)`). For single-session
  questions the two are identical; for the 133 multi-session questions
  (and part of temporal-reasoning) any-hit is strictly looser — the
  multi-session rows showing 100.0% below are the most inflated ... **The
  corrected full-500 number has not been re-measured yet** (requires OpenAI
  embeddings ~$2 first run) ... Expect the corrected headline to be equal or
  lower."
- Headline table: `gbrain-hybrid` 97.60% any-hit R@5; `gbrain-vector`
  97.40%; `gbrain-keyword` (BM25) 19.80%; hybrid+expansion 97.60% (Haiku
  query expansion = clean null result). "The gap between hybrid and
  vector-only on this dataset is 0.2 points. At top-5, vector-only retrieval
  is essentially as good as hybrid."
- `eval/runner/longmemeval.ts` — runner; `longmemeval-cache.ts` (embed
  cache), `longmemeval-aggregate.ts`, `longmemeval-batch.sh`,
  `longmemeval-chart.ts`.
- `docs/audit/2026-08-31-eval-audit.md` — 35-agent audit: **237 confirmed
  findings + 2 refuted (239 total)**: Critical 17 ("scores wrong / eval
  measures nothing / crashes"), Major 95 ("misleading metrics, silent skips,
  integrity leaks"), Minor 81, Improvements 44. Headline problems:
  (1) flagship LongMemEval number used the wrong metric (erratum published;
  runner now computes recall_all@5; re-measurement tracked in TODOS.md);
  (2) shared metric helpers wrong for everyone — recall could exceed 1.0
  (duplicate chunk rows double-counted), precision divided by returned-list
  length instead of k, LLM judge silently renormalized over whichever rubric
  criteria it returned; (3) four runners crashed against pinned gbrain while
  dependency floated on #master, ~a dozen evals structurally could not fail;
  (4) confounded comparisons — gbrain's default search mode silently enables
  a reranker when an unrelated env var is set; shootout shell env-prefix bug
  killed 4 of 7 cells with exit 127 while printing "done".
- **Adversarial verdict for epic #2080:** the README headline claims are all
  FLAGGED in-repo (comparison-systems.md rows carry the any-hit annotation;
  Cat 35 report carries honesty notes; PrecisionMemBench row carries a
  scores-drop-on-re-run banner + audit removed a seed-time shortcut). The
  epic's condensed claims ("97.6% SOTA ... walked back via erratum", "Cat 35
  88.1%", "Cat 34 0 failures", "graph +30pts precision") check out AS
  PUBLISHED WITH CAVEATS — details in the brief's adversarial section.

## 2026-08-31T10:30Z — [evals] Cat 35 result verification (W2 targets)

- `docs/benchmarks/2026-08-16-brainbench-cat35-transcript-distill.md` —
  Update 2026-08-31 (gbrain v0.47.8.0, fix wave PR #4742):

  | Metric (dream lane) | Published v0.46.3.0 | Pre-wave master `aa820c7f` | Post-wave `079941d2` |
  |---|---|---|---|
  | Salient-unit recall (macro) | 61.5% [45.0–77.6] | 70.2% [53.5–85.6] | **88.1% [82.0–93.5]** |
  | Strict (full-credit only) | 56.1% | 64.7% | 82.1% |
  | Sessions emitting pages (of 20) | 16 | 16 | **20** |
  | Quote fidelity (mechanical) | 45.4% | 54.2% (130/240) | **82.7% (115/139)** |
  | Claim hallucination | 14.1% | 14.0% | **7.0%** |
  | Distractor leakage | 0% | 1.2% (1/86) | 1.2% (1/86) |
  | Usability | 85% | 89.6% | **90.8%** |
  | Facts lane (macro) | 60.8% | 58.6% | 64.8% |
  | Verbatim control (judge ceiling) | 93.1% | 93.3% | 93.0% |
  | Cost | $6.20 | $6.23 | $6.36 |

- Both receipts record `gbrain_version: 0.47.7.0` because runs happened on
  the release branch BEFORE the version bump — "the SHAs are the binding
  identity". Pre-wave baseline was 62 commits stale when the wave started, so
  the honest delta is 70.2% → 88.1%, not 61.5% → 88.1% (the published 61.5%
  was a different, older state).
- Rescue mechanism: all four previously-missed transcripts still scored
  BELOW the 0.5 triage gate (0.45/0.35/0.42/0.42) — admitted via
  verified-segment rescue (judge's quoted segments verify as substrings),
  not score inflation. Pure-routine controls (max 0.18) stay below the 0.30
  rescue floor: zero false fires.
- Honesty notes: judge-scored deltas directional at n=1 per configuration;
  quote fidelity denominator dropped 240→139 because the repair pass strips
  quote marks from ungroundable spans; bracketing runs each record 1/86
  confirmed distractor where the published run recorded 0 (judge variance on
  a borderline mention, "reported as measured"); judge-calibration hand-
  scoring gate remains open.

## 2026-08-31T10:40Z — [evals] Cat 34 result verification

- `docs/benchmarks/2026-06-12-brainbench-memory.md` — first published run
  (kept as historical record) vs current CI baseline (update banner
  2026-08-31): know-to-ask failure **0.000 on all three seams (0/149 — the
  9 shared misses fixed upstream in v0.46.15.0)**, false fire **0.000 on all
  three (claude-code's 0.023 included)**, push recall **0.906 (openclaw) /
  1.000 (claude-code) / 0.552 (codex)** at precision 1.000 everywhere,
  write-back 1.000, continuity 1.000, isolation violations 0. Corpus grown
  since first run (149 kta / 96 push turns vs 146/94) — same-suite, not
  same-denominator.
- FIRST run: openclaw know-to-ask failure **0.150** (not 0!), push recall
  0.809; claude-code 0.150/0.660 + false fire 0.023; codex 0.150/0.447.
  "codex: a 1-fragment budget simply cannot cover 3-entity turns."
- So the README "0 know-to-ask failures" is TRUE for the current committed
  baseline (post v0.46.15 identity wave) but the headline silently skips the
  first-run 0.150 and the 0.023 claude-code false-fire history. Epic note:
  gbrain's own receipts carry the first-run number; README tables carry
  current-baseline numbers — cross-check against receipts before citing.

## 2026-08-31T10:50Z — [evals] comparison-systems.md rules (W7)

- Preamble: "the numbers tables are neutral and cited. The **'vs gbrain'**
  analysis under each table is explicitly our read ... with the mechanism
  named. Where gbrain loses, the loss and the reason stay in."
- LongMemEval table metric key corrected 2026-08-31: "R@k = session-level
  retrieval recall. The OFFICIAL evaluator computes recall_all@k — ALL of a
  question's ground-truth sessions must land in top-k — and that is what
  systems using the published evaluator report. The gbrain rows below were
  measured with a looser ANY-HIT variant ... and are flagged; corrected
  recall_all numbers are pending re-measurement."
- **Metric-mixing discipline**: "Mastra and Supermemory's numbers are
  end-to-end QA accuracy ... MemPal and the gbrain numbers are retrieval
  recall ... A system can have 100% retrieval recall and 60% QA accuracy if
  its answer model is bad, and vice versa. Don't compare them head-to-head
  without naming the gap." Supermemory ASMR (~99% QA-acc) is flagged
  experimental-not-production by its own authors.
- HaluMem table (write-path): Mem0 42.9%, Supermemory 41.5% extraction
  recall (HaluMem-Medium, arXiv 2511.03506) vs self-reported 90%+ read-path
  (Mem0 self-reports 92.5% LLM-judged on LoCoMo, arXiv 2504.19413). "The
  write path is where extraction quality actually lives; nobody publishes it
  voluntarily." SummHay (arXiv 2407.01370, human ceiling 56.1 joint) named
  as "the planted-gold protocol ancestor."
- Relational claim (line 159): gbrain's typed graph traversal "is worth ~30
  points of precision over plain vector on our relational benchmark" — a
  mechanism note in the LoCoMo section, NOT a published head-to-head table
  row on LoCoMo itself. The README headline "97.9% R@5 / 49.1% P@5 vs plain
  vector RAG 38 points less precision" is from the in-house relational
  benchmark (240-page corpus), not a public benchmark — check the exact
  benchmark doc before citing.
- LoCoMo: MemPal 100% row "structurally guaranteed (top-k > sessions) — needs
  caveat". No gbrain LoCoMo number, no claim.

## 2026-08-31T11:00Z — [tortoise] Internal state for the fit audit (W4)

- `tortoise/search_engine.py` — `SearchResult` (line 197) already carries:
  `ep: EpBreakdown {confidence_mean, evidence{impl_count, nand_count},
  contention, variance, contested, has_ep}`, `relationships` (operator edges
  with mechanism/predicate/related_content), `status` (live/superseded/
  deprecated/retracted/draft), `superseded_by`, `supersedes`, `valid_from/
  valid_to/expired_at` (bi-temporal), `subject`. `annotate_ep_batch` (line
  1173) + `get_relationships` (line 1235) + `fetch_point_epistemic_state`
  (line 1644) — all batch, non-N+1 Cypher.
- **KEY: `tortoise/mcp_server.py:1018`** — tortoise_search docstring:
  "Contestation is surfaced, never scored: contested claims carry
  ep.contested=true + ep.variance ... but are ranked exactly like any other
  claim with the same confidence (#580/#583)."
- **KEY: `tortoise/ranking.py:583`** — "separately as `incoming_nand` +
  `contested`, **never scored as support**"; `ranking.py:768` — "both are
  contention, surfaced never scored". The GraphRanker (`order_by='graph'`)
  blends similarity + persisted EP confidence + operator connectivity +
  30-day recency decay — variance/contested are annotations only.
- `tortoise/mcp_server.py` surfaces: `tortoise_search` (983, hybrid RRF + EP
  annotation + relationship_filter + traversal_path), `tortoise_ask` (1036,
  ONE bounded RAG pass → 12-field {answer, abstained, ..., evidence,
  context_tokens, ...}; **GATED #2013 — not served to hosted customers,
  OFF by default, TORTOISE_ENABLE_ASK=1**), `tortoise_recall` (1148),
  `tortoise_get_confidence` (1317), `tortoise_traverse` (1585, max_hops),
  `tortoise_analyze` (1782 — "where is the disagreement?" "what supports
  claim X?" pattern-based {answer, raw, pattern, query}), `tortoise_query`
  (708), `tortoise_query_points_by_tag` (938), `tortoise_search_sessions`
  (2048).
- `tortoise/retrieval.py:274` — superseded hits render `[valid <from> →
  <to>]` in the ask context; supersedes list w/ content snippets (286–288).
  No NAND/conflict structure in the ask reader context assembly (render_context).
- `tortoise/sdk.py:10505` — `ask()` is "the EVAL's reader path (the
  LongMemEval benchmark runs through the product reader) — do not build
  production features on it until the reader-model decision is made."
- **Fit-audit verdict (W4):** search already returns support/attack COUNTS,
  contested flag, variance, supersession + bi-temporal, 1-hop relationships
  w/ related_content. GAPS: (a) contentiousness is not a ranking signal
  (explicitly "surfaced, never scored" — #580/#583); (b) no trade-offs +
  mitigations for decision points (tortoise-decide surface data not in
  search/ask); (c) no explicit dig-deeper navigation pointers (raw material
  exists in relationships/related_content but no assembled "read these
  supports / this NAND / this superseded point" block); (d) no assembled
  "why is this believed" narrative (support chain content beyond 1 hop);
  (e) ask is gated off by default (#2013). All fillable IN-PLACE in the
  existing tools — no new surface needed for W4 headline; new surface only
  if the epic wants a dedicated "epistemic context" block (then human
  sign-off per epic constraint).

## 2026-08-31T11:10Z — [tortoise] LongMemEval infra (W7)

- `tools/longmem_eval/` — full runner (`run.py`), `ingest_v2.py`,
  `dataset.py`, `run_protocol.py` (9-step resumable state machine:
  code-review → micro-tests → 50-Q pilot → fixes → 500-Q baseline → fixes →
  50-Q confirmation → 1k owner-gated → follow-up; integrity gate with
  `JUSTIFIED_BASELINE_THRESHOLD = 0.02` recoverable-error rate), `evidence.py`
  (answer_string vs chunk evidence seams), `full_context.py`, `rerank.py`,
  `dataset_audit.py`.
- `tools/longmem_eval/dataset_audit.py` — Tortoise's OWN semantics
  divergence record vs official LongMemEval (lines 12–25): official excludes
  `_abs` from retrieval aggregates (paper-aligned `_paper@k` keys added);
  official indexes `role=='user'` turns only; official metrics are binary
  `recall_any/recall_all + ndcg` while Tortoise's is "a per-question
  fraction (documented variant)"; official code asserts `has_answer` on
  every user turn while the cleaned split marks it sparsely. Publication
  gate: "no turn_recall/evidence_recall number is published unless this
  record is present in the report methodology" (`build_report` raises
  ValueError without it).
- Committed reports: `longmemeval_s_*.report.json` at repo root — the latest
  (2026-08-28T203355Z) shows `"n": 1` per category, ci95 [0.207, 1.0] →
  **confirms the epic's claim: machinery exists, only n=1 smoke reports
  published, no 500-Q sealed run**.

## 2026-08-31T11:20Z — [tortoise] Write path inventory (W2/W5)

- `tortoise/session_import/` (parsers.py + __init__.py), `tortoise/
  memory_orchestrator.py` (cross-ontology routing: episodic/epistemic/
  semantic/docIndex; PATTERN_KEYWORDS NL classifier, "Replace with LLM
  dispatch when accuracy < 80%"), `tortoise/dream.py` (two-tier EP
  stabilization, fast path per-query impact-subgraph + slow path
  dream_all/dream_window stale-first scheduler, 200-op selector cap),
  `tortoise/session_continuity.py`, `tortoise/session_link.py`,
  `tortoise/session_indexer.py`, `tortoise/event_store.py`,
  `tortoise/extractor.py` + `extractor_v2.py`.
- **No planted-gold write-path eval exists for session→graph** (no Cat-35
  analogue in tests/ — verified by absence; the epic's W2 premise holds).
- `tortoise/claude-hooks/session-end.sh` + `session-start.sh` — existing
  session lifecycle hooks (a natural Tortoise harness seam for W3/W5).

## 2026-08-31T11:30Z — [tortoise] EP/eval spec inventory (context for W2–W4)

- `docs/epistemic-layer-eval-spec.md` — P1–P10 property tests (support
  attenuation, directed attack, rebut vs undercut, REPHRASE pooling,
  invalidation+supersession bi-temporal), G1–G8 graph-quality, R1–R8
  reasoning endpoints, adversarial tests, B0–B2 baselines; method rules:
  deterministic, confidence-delta-with-threshold, hermetic-where-possible,
  fail-loud on inversion. Includes a documented 2026-08-09 NAND potential
  fix (P0) — the engine's NAND was an agreement coupling, now
  position-aware attack potential.
- `docs/agent-reasoning-eval-battery.md` — Tier 1 single-session reasoning
  probes (R1 contradiction surfacing, R2 adversarial deliberation coverage,
  R3 epistemic calibration), Tier 2 longitudinal learning, Tier 3
  differential vs no-memory/long-context/generic-store. Null hypothesis:
  "Tortoise produces the same reasoning outcomes as a plain agent or a
  generic memory store, when recall is held constant."
- `docs/research/2026-08-14-agentic-eval-landscape.md` + `docs/drafts/
  2026-08-12-graph-as-memory-hypothesis.md` (E1–E3) — prior research
  context.

## 2026-08-31T11:40Z — [web] External verification

- Official LongMemEval evaluator (`src/retrieval/eval_utils.py`,
  xiaowu0162/LongMemEval): `recall_any = any(doc in recalled_docs for doc in
  correct_docs)`; `recall_all = all(doc in recalled_docs for doc in
  correct_docs)`; plus ndcg. **gbrain's erratum is accurate verbatim.**
- HaluMem (arXiv 2511.03506) — only published write-path benchmark
  (persona-chat memory points, FULL/PARTIAL/OMITTED at 1/0.5/0); Mem0 42.9%,
  Supermemory 41.5% on HaluMem-Medium per gbrain's cited table. Mem0
  self-reports 92.5% on LoCoMo (arXiv 2504.19413). Read-path
  self-reports ≫ write-path measured — the write-path gap gbrain exploits
  is real and externally corroborated.
- External reviews of gbrain (vectorize.io, gamgee.ai, slite.com,
  lucaberton.com): strong retrieval + measured write path; single-user,
  markdown-first, self-host only, young with frequent breaking changes, "no
  real permission model", "does not verify correctness or completeness of
  the brain" (slite). ⚠️ single-source class (blog reviews, not primary);
  low confidence — cited only as color.
- Theory-first (memory consolidation): sleep/rest replay drives systems
  consolidation (Nature Rev Neurosci nrn2084; Nature Neuro s41593-019-0467-3;
  s41583-019-0191-8) — validates the "dream" architecture both systems
  already have (gbrain dream distiller; Tortoise dream.py EP stabilization).
  Consolidation theory says replay transforms episodic → gist/semantic and
  integrates into networks — the gbrain Cat 35 dream lane and Tortoise's
  dreaming are the computational analogues; W4's why-layer (support chains)
  is closer to source-anchored justification than to consolidation theory —
  no external precedent found for belief-why recall as a product feature
  (⚠️ hypothesis: this is genuinely novel territory; competitive scan found
  nobody shipping it).

## 2026-08-31T11:50Z — [gbrain/evals] Licensing re-confirmation

- LICENSE files: MIT, Copyright (c) 2026 Garry Tan, both repos. No copyleft.
- npm `gbrain` is an unrelated GPU JS library (v1.3.1, MIT, stormcolor) —
  NOT Garry Tan's gbrain. Git-pinned SHA is the only install path.
- Eval corpus manifests carry per-file sha256 + generator provenance
  (claude-opus-4-5, seed, template_hash); transcript-distill-v1 manifest
  asserts MIT and (per README) corpora are "fully made up and free to
  redistribute"; PrecisionMemBench vendored artifacts carry ATTRIBUTION.md
  (tenurehq MIT per epic's licensing gate note).
- Obligation summary (matches epic's licensing gate): vendoring actual code/
  corpus files requires preserving the MIT notice + attribution, no
  endorsement implied; reimplementing IDEAS (fixture schema shape, gold
  conventions, metric formulas, receipts discipline) carries no obligation.

---

## Line-ref quick index (most-cited)

| Topic | File | Ref |
|---|---|---|
| RRF fusion + compiled-truth boost | gbrain `src/core/search/hybrid.ts` | L1–7, L68, L2850 |
| Adaptive return sizing rationale | gbrain `src/core/search/return-policy.ts` | L12–20 |
| Source boost map | gbrain `src/core/search/source-boost.ts` | L17–60 |
| Reflex orchestrator (timeout, fail-open) | gbrain `src/core/context/reflex.ts` | L106, L145–148, L192 |
| Entity salience budgets | gbrain `src/core/context/entity-salience.ts` | L80–92, L312–402 |
| Pointer budget 3, arm confidence | gbrain `src/core/context/retrieval-reflex.ts` | L40, L69–74 |
| Volunteer gate 0.7, boost 0.05 | gbrain `src/core/context/volunteer.ts` | L20–25 |
| BrainBench fixture/gold/baseline schema | gbrain `src/eval/brainbench/types.ts` | L44–230 |
| know-to-ask + false-fire | gbrain `src/eval/brainbench/metrics/know-to-ask.ts` | L1–60 |
| write-back fidelity + provenance | gbrain `src/eval/brainbench/metrics/write-back.ts` | L2–20, L231–232 |
| claude-code production seam | gbrain `src/eval/brainbench/adapters/claude-code.ts` | L1–70 |
| CI gate (compare/bless/justification) | gbrain `src/commands/eval-brainbench.ts` | L50–82, L362–375 |
| Cat 34 foreign runner + receipts | evals `eval/runner/cat34-brainbench-memory.ts` | L1–130 |
| Receipt schema | evals `eval/runner/receipt.ts` | L24–120 |
| Baseline refresh discipline | evals `baselines/README.md` | whole |
| Cat 35 gold shape | evals `eval/runner/cat35-transcript-distill.ts` | L60–80 |
| Cat 35 mechanical checks | evals `eval/runner/cat35-checks.ts` | L1–230 |
| Judge blindness | evals `eval/runner/cat35-judges.ts` | L40–45 |
| LongMemEval erratum (any-hit) | evals `docs/benchmarks/2026-05-07-longmemeval-s.md` | top |
| 35-agent audit | evals `docs/audit/2026-08-31-eval-audit.md` | whole |
| Cat 35 88.1% post-wave | evals `docs/benchmarks/2026-08-16-brainbench-cat35-transcript-distill.md` | Update block |
| Cat 34 first-run vs baseline | evals `docs/benchmarks/2026-06-12-brainbench-memory.md` | Update banner + table |
| Comparison rules + HaluMem | evals `docs/comparison-systems.md` | preamble, L130–190 |
| Tortoise contested never scored | tortoise `tortoise/mcp_server.py` | L1018 |
| Tortoise ranking contention | tortoise `tortoise/ranking.py` | L583, L768 |
| Tortoise search EP annotation | tortoise `tortoise/search_engine.py` | L197–269, L1173, L1235 |
| Tortoise ask gated + reader path | tortoise `tortoise/sdk.py` | L10505–10580 |
| Tortoise LME semantics audit | tortoise `tools/longmem_eval/dataset_audit.py` | L12–25 |
| Tortoise EP eval spec | tortoise `docs/epistemic-layer-eval-spec.md` | whole |
| Tortoise reasoning battery | tortoise `docs/agent-reasoning-eval-battery.md` | whole |
