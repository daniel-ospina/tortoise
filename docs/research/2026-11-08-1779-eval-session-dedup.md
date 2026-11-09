# #1779 — cross-question session dedup/caching in the LongMemEval harness: the measured overlap

**Date:** 2026-11-08 · **Issue:** #1779 (`chore(eval)`, P2-moderate, `not-launch-relevant`) ·
**Tree:** `feat/1779-tm` off `origin/main` @ `d3dadd9b37` · **Measurement-only: no cache was
built, no retrieval/parse semantics changed.**

The issue was filed as a #1746 follow-up with the measurement as its precondition: *"measure the
overlap first (what fraction of sessions/prompts repeat across the 50/500-Q instances), then decide
cache granularity (session-level extracted-embed reuse vs prompt-level response cache)"*. This
document is that measurement. It also retracts the number the harness has been justifying the
existing cache with.

---

## 0. Headline findings

1. **The "5-10× redundant encodes" the repo asserted is not measurable and is wrong.**
   The module docstring of `tools/longmem_eval/encode_cache.py` (line 4 **on `main`, before this
   PR** — this PR rewrote that line, so the citation is a historic location, not a live one),
   `tools/longmem_eval/README.md` and a comment in `run.py`'s HNSW spot-check (`:7383` **on
   `main`**, likewise rewritten here) all cited it; it traces to
   `docs/plans/2026-08-17-1349-embedder-selection.md:63`, where it was asserted, never measured.
   **Measured:** one pass over the 500-question S split emits **371,095** encode units of which
   **285,608** are distinct — a **1.299×** reuse factor, i.e. **23.0%** of those units are cache
   hits in one pass. ⚠️ A HIT share, not a strictly cross-question rate: see residual 6. The asserted figure overstated the real one by 4–8×. The denominator is the
   **dataset-derivable** set (turn Points + raw chunks): a `v2` run also embeds LLM-derived
   extraction Point texts (`ingest_v2` → `sdk.create_point`, and `encode_cache` wraps
   `tortoise.embeddings` process-wide), which no dataset-only census can count — see residual 5 in §6.
2. **Cross-question sessions DO repeat — 5,403 of 23,867 slots — but every repeated pair is
   re-dated.** 4,375 session contents repeat; of the **6,660** repeated pairs, **0** share a session
   date. The date is prompt-visible: `extractor_v2._date_anchor` (`extractor_v2.py:1455-1471`)
   renders *"**DATE ANCHOR — today is `<session_date>`**"* into the **system** prompt of S1
   (`run_s1`, `:1485-1651`) — the S1 system prompt is `S1_TMPL.replace("{date_anchor}",
   _date_anchor(session_date))` at `:1528` — and into S2 (`render_s2_prompt`, `:2174`) and S4
   (`render_s4_prompt`, `:3617`). So the
   **session-level** prompt identity is exactly **1.000×**, and a session-level extraction cache has
   nothing to hit. The **S1 prompt is a finer unit than the session** — `extract_session_v2` splits
   each session into EDU chunks and calls `run_s1` per chunk with the session date (`chunk_transcript`
   → `_arm_extract` → `run_s1`), so the response cache's identity is `(chunk text, session date)`,
   which is measured as its own cell: **124,318 distinct identities over 124,345 chunk prompts =
   1.0002×, i.e. 27 repeats, 0.02% of the leg**. So the two LLM avenues do **not** share one identity
   condition, and both land in the same place for the same reason: there is nothing to build —
   1.000× at chunk granularity is 27 prompts out of 124,345.
3. **The one realizable lever is the text→embedding cache that already exists**, at the measured
   **1.299×** — and its larger value is on REPEAT passes with the same `(model, prompt)`, where the
   disk cache is ~all hits (a crashed burn resumes off the same file). ⛔ The HNSW spot-check is NOT
   evidence for that: its two passes are the **winner and the control**, each with its own
   `cache_path_for` namespace, so neither can hit the other's entries.
4. **Recorded residual — NOT reducible by dedup:** the raw-chunk leg is **124,345 of 371,095** encode
   units (**33.5%**) and **no chunk text is ever a turn text** (`distinct` per leg sums to `distinct` for
   the total: 189,522 + 96,086 = 285,608), because a chunk renders `_session_transcript(window)`
   (`ingest.py:105-112`) while its turn Points render `_turn_text` (`[role] content`, `ingest.py:95-102`).
   So those units cannot be served from the turn Points' cache entries — but the cache DOES serve
   **28,259** of them (22.7% of the leg, **33.1% of every cross-unit hit**); what it cannot remove is the
   **96,086 distinct chunk texts**, which is a chunking/rendering decision (`chunk_turns`, #1540).
5. **The LLM-budget lever is provider-side prefix caching, and the harness already meters it.**
   Providers cache prompt *prefixes* on an exact match (Anthropic: "the system checks if a prompt
   prefix … is already cached"; order is tools → system → messages) — so the eval's constant
   rules/vocabulary prefix is discounted automatically for the seconds-to-minutes it repeats, with
   no code. The harness already captures and prices it: `tools/longmem_eval/usage.py` sanitizes
   `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`, and `costing.py:1035-1038` prices hits at
   `cache_read_per_1m`. A real run's report answers "what would prefix caching save" **today**; no
   new instrument is needed, and this lane built none.

---

## 1. The question, restated as three different identity conditions

"Cache repeated LLM work across eval questions" is three different caches with three different
keys. They do not share an answer, which is exactly why the measurement had to come first:

| avenue | cache key is | a hit requires |
|---|---|---|
| text→embedding (built: `encode_cache.py`) | `sha256(model_id + prompt_name + text)` | the same **text** |
| prompt-level response cache | the rendered **S1 chunk** prompt | the same **chunk text *and* session date** |
| session-level extraction reuse | the session's content | the same **transcript *and* session date** |

The bottom two rows are **not** one condition: the extraction cache keys on the whole session, while
S1 renders one prompt per **EDU chunk** (`extract_session_v2` → `chunk_transcript` → `run_s1`), so the
response cache's identity is chunk-level. P(a session repeats) ≤ P(a chunk repeats), so the session
cell can only *understate* prompt reuse — which is why the census measures the chunk cell separately
and the verdict is derived from it (an earlier round of this PR derived it from the session proxy; a
review caught that as a number asserted beyond its measured cell).

## 2. Method — measure the unit the product actually encodes

`tools/longmem_eval/haystack_overlap.py` (new) counts, per question, with the **product's own
renderers** rather than a re-implementation:

- session identity — `ingest._session_transcript(session)` (the text the ingest writes);
- session prompt identity — that transcript **+ `haystack_dates[i]`**;
- **chunk prompt identity** — the raw-chunk text **+ the same date**, i.e. the `(chunk text, date)`
  joint. This is the cell the response-cache verdict is derived from, because S1's prompt is rendered
  per EDU chunk. The exact S1 unit is not dataset-derivable (its chunks are `chunk_transcript` over
  **LLM-derived** EDUs), so the raw-chunk leg is the finest cell a dataset-only census can measure —
  the same "dataset-derivable only" boundary the encode-unit denominator states;
- encode units — one turn Point per turn (`ingest._turn_text`) and one raw chunk per
  `chunk_turns`-window (`ingest._session_chunks`, `DEFAULT_CHUNK_TURNS = 2`).

`_turn_text` was extracted from the two inline renderings (`ingest.py:219` and
`ingest_v2.py:615` on `main`, **before** this PR — their call sites are `ingest.py:229` and
`ingest_v2.py:616` at this head) in the same change, so both ingest legs and the census share one definition of
"the text a turn Point embeds" — the census cannot silently measure text the ingest never writes.

Pure and deterministic: no DB, no LLM, no keys, no network beyond the dataset cache. Reproduce:

```bash
python -m tools.longmem_eval.haystack_overlap            # human summary
python -m tools.longmem_eval.haystack_overlap --json      # the census payload
```

The measured S-split census is pinned as `CENSUS` in the module and re-derived by the `@slow` test
`tests/test_haystack_overlap_1779.py::test_census_on_real_s_split` (skipped in CI,
which has no dataset). A dataset refresh that changes the overlap therefore **reds a pin** instead
of quietly re-basing a published number — the failure mode that produced the "5-10×" fiction in the
first place.

## 3. Measured results (S split, 500 questions, `chunk_turns=2`, 2026-11-08)

| quantity | value |
|---|---|
| questions | 500 |
| session slots (sum over questions) | 23,867 |
| distinct session ids | 19,195 |
| distinct session contents | 18,464 → **reuse 1.2926×**, 5,403 duplicate slots |
| repeated pairs / pairs sharing a date | 6,660 / **0** |
| distinct (transcript, date) — distinct SESSION prompts | 23,867 = slots → **session prompt reuse 1.000×** |
| distinct (chunk text, date) — distinct **S1 prompts** | 124,318 of 124,345 → **1.0002×**, **27 repeats (0.02%)** |
| questions holding ≥1 repeated session | **500 / 500** |
| repeat histogram (contents × repeats) | 1×:14,089 · 2×:3,533 · 3×:688 · 4×:130 · 5×:18 · 6×:5 · 8×:1 |
| encode units — turn leg | 246,750 slots / 189,522 distinct → **1.302×** (23.2% hits) |
| encode units — chunk leg | 124,345 / 96,086 → **1.294×** (22.7% hits) |
| encode units — total | 371,095 / 285,608 → **1.299×** (**23.0% hits**) |
| chunk leg share of encode units | **33.5%** |

Two sanity readings that keep the 1.000× honest rather than an artifact of unique dates: the corpus
carries **18,360 distinct date values over 23,867 slots** (dates are heavily reused *across*
different sessions), and 13 duplicate slots are *within* a single question. The date population is
its own census cell (`CENSUS["dates"]`, printed by the CLI as `dates`) and is pinned like every
other published number — a number quoted in prose with no producer is the drift this document exists
to retire. The re-dating is a property of the duplication, not of the date population.

## 4. Verdict — the granularity decision #1779 asked for

| avenue | measured reuse | realizable | why |
|---|---|---|---|
| text→embedding (`encode_cache.py`) | **1.299×** (23.0% of units) | **yes** (built) | the key is the text; 23.0% of units hit in one pass, ~all on a repeat pass |
| prompt-level response cache | **1.0002×** (27 of 124,345 S1 chunk prompts = 0.02%) | **no** | S1 renders one prompt per chunk, and the `(chunk text, date)` cell is 124,318 distinct over 124,345 slots — the 27 repeats are `0.02%`, below the declared `MATERIAL_HIT_SHARE` of 1% |
| session-level extraction reuse | **1.000×** | **no** | 0 of 6,660 repeated pairs share a date; the date is prompt-visible |

`realizable` is `reuse > 1.0 AND hit_share >= MATERIAL_HIT_SHARE` (`haystack_overlap.py`), declared
once in the module and published in every verdict row: repeating is not the same as being worth
building, and 27 hits out of 124,345 is a rounding error of the LLM work. The floor sits an order of
magnitude below the cache this lane already ships (23.0%), so it can only reject an avenue that is
demonstrably weaker than the one already built.

Nothing is worth building at the LLM level for *cross-question* reuse, and the alternative that is
left — dropping or relocating the date anchor to make prompts repeat — is a **prompt-semantics**
change that would alter extraction (dates are the anchor for `when`/`startedAt`, E1/#1533) and
therefore the benchmark's comparability. That is out of scope for a cache, and it is out of scope
for this issue by its own scope guard ("Out of scope: any parse/retrieval semantics").

## 5. What changed in the tree (and what deliberately did not)

- **New:** `tools/longmem_eval/haystack_overlap.py` (census + derived verdict + CLI) and its test
  `tests/test_haystack_overlap_1779.py` (25 tests: 24 hermetic + the real-split pin — a
  count this document's §5 line must match, asserted by
  `test_the_research_doc_publishes_this_files_REAL_test_count`). The test file lives at `tests/`
  ROOT, not under `tests/longmem_eval/`, and that placement is load-bearing: `on_demand:` carries
  `longmem_eval/**`, and the subtraction is by PATH (`tools/ci_selection.py:4269`), so a file in
  that subtree is off every pre-merge leg — measured, a PR touching
  `tools/longmem_eval/ingest.py` selects the whole `eval` surface's test list and none from
  `tests/longmem_eval/` (re-derive: `ci_selection.select(["tools/longmem_eval/ingest.py"],
  "pull_request", manifest)` — no count is published here on purpose, because moving this very
  file changed it from 142 to 143 in the commit that documented it). At the
  root the 24 hermetic ratchets GATE, so a revert of the `_turn_text` extraction reds instead of
  shipping byte-identically (same precedent as `test_temporal_leg_non_eviction_receipt_11494.py`,
  `config/ci-surfaces.yml`). Registered on the `eval` surface for OWNERSHIP
  (`tools/longmem_eval/` is a `SOURCE_PATTERNS` entry of it); `python tools/ci_selection.py
  --integrity` exits 0.
  ⚠️ The `@slow` real-split PIN inside that file stays UNARMED: no CI lane re-derives it (the
  on-demand lane provisions the embedder and FalkorDB but never the dataset cache), so it skips in
  CI (measured: 24 passed, 1 skipped with an empty cache dir) and the numbers are re-derived only on
  a box holding the cached S split (§8).
- **Corrected:** the unmeasured "5-10×" in `encode_cache.py` (docstring), `README.md` (encode-cache
  section + a new measured-overlap table) and `run.py:7383` (comment) now cite the measured 1.299× /
  23.0% and name the census that produced it. Its ORIGIN —
  `docs/plans/2026-08-17-1349-embedder-selection.md:63` and its restatement at `:101` (`:96` before
  this PR's note) — keeps its
  original text (a plan is the dated record of what was believed) and gains a dated correction note
  at each site, so the next reader cannot take it as a current premise.
- **Refactored (no behaviour change):** `ingest._turn_text` — one definition of the turn Point's
  text, imported by `ingest.py`, `ingest_v2.py` and the census; a mechanical ratchet test reds if
  either leg re-introduces its inline twin (byte-identical output, so nothing else would notice).
- **Not done, on purpose:** no session-level cache, no prompt/response cache, no change to
  `chunk_turns`, the date anchor, or any retrieval/parse semantics.

## 6. Residuals and what would change the answer

1. **Chunk leg (33.5% of encode units).** NOT reducible by dedup: a chunk renders
   `_session_transcript(window)` while its turn Points render `_turn_text`, so the two legs are
   disjoint string populations and a chunk unit can never be served from a turn unit's cache entry
   (the cache still hits 28,259 chunk units — 33.1% of all hits). What is left is the 96,086 distinct
   chunk texts: a chunking/rendering decision — #1540's knob — and it changes what a raw chunk *is*,
   so it belongs to a retrieval-semantics issue, not here.
2. **Provider prefix caching.** Already discounted by the providers and already metered
   (`prompt_cache_hit_tokens` → `cache_read_per_1m`). Answerable from any real run's report; no
   measurement was possible in this lane (no LLM keys, no spend authorisation).
3. **The M split.** The S split is the design-locked target (#1144 axis 2). M has ~500 sessions per
   question, so its cross-question overlap may differ — one `--split m --download` invocation of the
   census answers it (2.7 GB download, so it is never implicit);
4. **A dataset refresh** changes any of the above numbers → the `@slow` pin reds and the docs get
   re-derived, which is the point of pinning them.
5. **The `v2` extraction leg is outside every share quoted here.** The census counts the texts the
   ingest renders per session (turn Points + raw chunks) because those are the only units derivable
   from the dataset. A `v2` run ALSO embeds LLM-derived extraction Point texts (`ingest_v2` →
   `sdk.create_point`; `encode_cache` wraps `tortoise.embeddings` process-wide), so the true
   whole-run denominator is larger and the published 23.0% is a SUBSET share.
   **Its reuse is UNMEASURED, and it could go either way.** The embedded text is the extracted claim
   string (`ingest_v2.py:216`), while the session date rides on separate point properties (`when` /
   `validFrom`, `:259-261`) — so a re-dated repeat COULD emit an identical claim string and hit a
   text-keyed cache, which would make the true share **larger** than 23.0%. Nothing in a
   dataset-only census can settle it; a run with `--model` + `ingest_v2` and the cache's own hit
   counters can.

6. **⛔ The 23.0% is a HIT share, not a strictly cross-question rate.** The census publishes BOTH
   cells, so neither has to be derived: `encode_units.total.hit_slots` = **85,487 of 371,095**
   (23.0%) counts every repeat, including the **1,064 units that repeat *inside* a single
   question's own haystack** (965 turn + 99 chunk — a repeated filler turn, and chunk texts two of
   that question's sessions share). Subtracting those leaves the strict cross-question reuse,
   **84,423 of 371,095 = 22.75%**. ⛔ An earlier revision of this note said 22.78% / 84,522: it
   subtracted only the TURN leg's 965 from the TOTAL's hits, so the chunk leg's 99 within-question
   repeats were still counted as cross-question — the derived-figure-without-its-own-producer class
   this note exists to retire, inside the note. The 23.0% figure answers "what fraction of encode
   units would a `(model, prompt, text)` cache serve in one pass", which is the number the decision
   needs and the one `encode_cache.py` acts on — but the qualifier matters for #1779's own question,
   so every live site says "cache hits in one pass" rather than "cross-question hits", and both
   cells are pinned (`within_question_hit_slots` / `cross_question_hit_slots` / `..._share`).
## 7. Research provenance

- **Internal (before deriving):** issue #1779 + its comment thread; `git log --all --grep` (no prior
  attempt at #1779; the only `#1779` hits in the log are another repo's numbering); the #1746 plan
  (`docs/plans/2026-08-26-1746-parse-error-robustness.md:22,228,314` — which sets the scope guard
  and names this issue as the follow-up) and `docs/plans/2026-08-17-1349-embedder-selection.md:63,96`
  (both `main`-era line numbers: `:96` moved to `:101` when this PR added its note at `:64-68`)
  (the origin of the "5-10×" claim); `tools/longmem_eval/{ingest,ingest_v2,encode_cache,run}.py`;
  `tests/test_dataset_audit.py` (the pinned-census + `@slow` pattern reused here).
  **Tortoise graph: NOT consulted — no graph was reachable on this cell**
  (`ConnectionError: 127.0.0.1:16379 refused`), so the graph-first step could not run; recorded here
  rather than skipped silently.
- **External — rung 1 unavailable:** `mcp_load seo-intelligence` failed (`Unknown MCP server`; the
  Perplexity MCP is not installed on this cell, and `exa` is forbidden by owner ruling 2026-10-31),
  so rung 2 was taken.
- **Rung 2 (`web_search`, `model="sonar"`, 2 attempted):** one query on content-hash embedding caches
  in eval harnesses returned 5 results (the convergent practice: key the cache on **normalized
  content hash + model version**, dedupe *before* embedding — which is exactly the key
  `encode_cache.py` already uses, so no change is indicated); the companion query on provider prompt
  caching returned **429** and was not retried on rung 2.
- **Rung 3 (`web_fetch`, primary source):** Anthropic prompt-caching docs —
  <https://docs.claude.com/en/docs/build-with-claude/prompt-caching> — confirm the identity
  condition this document turns on: caching is **prefix**-based ("checks if a prompt prefix … is
  already cached", order tools → system → messages) with a TTL, not a content-addressed response
  store. A per-session date placed in the system prompt therefore breaks the prefix match at that
  block, and does not merely miss a lookup.
- **Contradiction test:** no recorded decision governs eval caching. The nearest recorded constraints
  are the #1746 scope guard (parse/retrieval semantics are other lanes') and the owner ruling that
  the judge stays byte-verbatim for comparability (`judge.OFFICIAL_JUDGE_MODEL`, #8486); neither is
  contradicted here — this lane changes no prompt, no judge, and no retrieval code.

## 8. Provenance of the numbers

```bash
# the census (this document's table)
TORTOISE_LME_CACHE_DIR=~/.cache/tortoise-longmemeval \
  python -m tools.longmem_eval.haystack_overlap --json

# the tests (24 hermetic pass; the @slow pin only runs with the cached S split)
TORTOISE_TEST_CARVE_OUT=1 TORTOISE_LME_CACHE_DIR=~/.cache/tortoise-longmemeval \
  python -m pytest tests/test_haystack_overlap_1779.py -q -p no:cacheprovider
```

Dataset: `xiaowu0162/longmemeval-cleaned`, S split, digest-pinned by
`tools/longmem_eval/dataset.py:SPLIT_DIGESTS["s"]` (`d6f21ea9…a442`), 277 MB, never committed.
