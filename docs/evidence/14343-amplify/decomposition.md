## The input constant, decomposed (#14343)

arm `cold` · 50 documents · median document 5,211.0 tokens · calls/doc 3.98 (s1 1.68, s2 1.16, s4 1.14)

### Static part of each prompt (offline render, exact)

| region | envelope | s1 | s2 | s4 |
|---|---|---|---|---|
| `anti_routine` | 0 | 0 | 1,942 | 1,942 |
| `chains_text` | 0 | 0 | 238 | 238 |
| `clause` | 93 | 0 | 0 | 0 |
| `date_anchor` | 0 | 78 | 227 | 227 |
| `embed_list_json` | 0 | 0 | 0 | 1 |
| `master_list` | 0 | 0 | 3,187 | 3,187 |
| `memory_granularity` | 0 | 923 | 0 | 0 |
| `output_contract` | 0 | 0 | 951 | 951 |
| `pack_namespaces` | 0 | 0 | 0 | 0 |
| `search_results` | 0 | 0 | 0 | 113 |
| `story` | 0 | 0 | 0 | 0 |
| `template_prose` | 0 | 629 | 2,762 | 1,903 |

### Per document — what the constant is made of

| region | tokens/doc | share |
|---|---|---|
| **S2's embed list, re-sent to S4** | 14,058 | 32.2% |
| kind vocabulary (`master_list`) | 7,330 | 16.8% |
| stage instructions (template prose) | 6,430 | 14.7% |
| **S1's story, re-sent to S2 and S4** | 6,323 | 14.5% |
| mapping rules (`anti_routine`) | 4,467 | 10.2% |
| output schema (`output_contract`) | 2,187 | 5.0% |
| memory granularity (S1 only) | 1,551 | 3.6% |
| date anchor | 653 | 1.5% |
| chains | 547 | 1.3% |
| graph search results (S4) | 129 | 0.3% |
| **composition total** | **43,675** | 100.0% |
| *fitted constant (this arm's own intercept)* | 45,503 | — |
| *unattributed (fit intercept − composition)* | 1,828 | — |

The document itself is NOT in that table: it is the fit's slope, 8,754 tokens/doc (1.68 × 5,211.0) against the fitted 11,250. S1 sends the WHOLE document once per call, and a majority of documents take a second S1 call, so the document is paid for ~2x — 34 of 50 documents here.

Solved dynamic regions: the S1 story is **2,656** tokens as the later stages see it, and S2's embed list is re-serialized into S4's prompt at **12,332** tokens — 1.23× S2's own emission.

### The output constant, decomposed

| stage | completion tokens/doc | share of output |
|---|---|---|
| s1 | 2,798 | 15.7% |
| s2 | 11,677 | 65.5% |
| s4 | 3,354 | 18.8% |
| **composition total** | **17,830** | 100.0% |
| *fitted constant (this arm's own intercept)* | 22,802 | — |
| **unattributed** | 4,972 | — |

⚠️ **Two estimators, both reported.** The rows are per-call MEDIANS × measured call counts (the median document's shape); the fitted row is the intercept of this arm's own per-document fit, and output's fit is nearly flat (slope 0.18, R²=0.02) so its intercept sits near the MEAN output per document (24,350), not the median. The mean is above the median because **S4's output is bimodal**: 13 of 50 documents (26.0%) average more than 6,000 completion tokens per S4 call (max 24,000 against a median of 2,942) — the delta contract complying on most documents and not on the tail. That tail is where the gap between the two estimators lives, so it is stated rather than smoothed.

Against the fitted constant, S2 carries **51.2%** of output (the composition gives 65.5% of the attributed part). Both are printed because the remainder is not attributable by construction.

⚠️ The per-SECTION token counts printed by `compose` are re-serializations of each section on their own, so they do not sum to the emission and carry no residual row. Stage totals are the measured `completion_tokens`; section sizes are shape, not an account.

Two objects are each paid more than once, and they are NOT the same object. **S2's embed list** is paid twice: once as S2's output and once as S4's largest input region. **S1's story** is paid three times: as S1's output, as S2's user message and as S4's `{story}` slot. Nothing in this table scales with the document: the item COUNT is what moves, and a document's length barely moves it (R²=0.000 on S2's input fit).

### The one information-neutral lever: `indent=1` on S4's embed-list region

Measured on **7 real sessions / 14 emissions / 580 items** from `tests/eval/write_path/receipts/`: the list S4 re-reads costs **77,561** tokens as today's `indent=1` dump and **52,346** with compact separators — **25,215 tokens (32.5%) that are pure whitespace**, 1,801 per emission. S2's own emission is 100.9 completion tokens per item, so the re-serialized copy is ~1.3x the emission it copies.

| session | arm | items | emission | `indent=1` | compact | saved |
|---|---|---|---|---|---|---|
| wp01_quarry_debug | raw | 47 | 4582 | 6,276 | 4,243 | 2,033 |
| wp01_quarry_debug | narrative | 51 | 5266 | 7,165 | 4,851 | 2,314 |
| wp02_lumen_refactor | raw | 45 | 4860 | 5,833 | 3,815 | 2,018 |
| wp02_lumen_refactor | narrative | 49 | 4424 | 5,998 | 4,084 | 1,914 |
| wp03_ember_design | raw | 39 | 3584 | 5,118 | 3,327 | 1,791 |
| wp03_ember_design | narrative | 46 | 4130 | 5,626 | 3,872 | 1,754 |
| wp04_aurora_perf | raw | 30 | 3213 | 4,412 | 2,975 | 1,437 |
| wp04_aurora_perf | narrative | 38 | 4187 | 5,696 | 3,880 | 1,816 |
| wp05_retro_writeup | raw | 45 | 4380 | 6,048 | 4,084 | 1,964 |
| wp05_retro_writeup | narrative | 54 | 5453 | 7,547 | 5,114 | 2,433 |
| wp06_quarry_rollout | raw | 43 | 4276 | 5,970 | 3,952 | 2,018 |
| wp06_quarry_rollout | narrative | 36 | 3263 | 4,346 | 3,058 | 1,288 |
| wp07_bluepeak_followup | raw | 20 | 3384 | 2,566 | 1,704 | 862 |
| wp07_bluepeak_followup | narrative | 37 | 3542 | 4,960 | 3,387 | 1,573 |

### Candidate levers, sized offline

| lever | size (tokens/doc) | quality-neutral? |
|---|---|---|
| compact master render (2,335 vs 3,187 tokens/call) | −1,960 | NO — compact mode story-filters the pack kinds, so a document that misses every trigger word drops to the all-packs fallback and a document that hits one drops the others — not quality-neutral |
| S1 truncation double-send (68.0% of documents take 2 S1 calls) | −4,652 | NO — a longer narrative is more content, not less — but it also enlarges the story both later stages re-read, so the net is a measurement, not arithmetic; the story's own growth is NOT sizeable offline |
