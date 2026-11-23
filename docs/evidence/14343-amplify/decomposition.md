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
| **S2's embed list, re-sent to S4** | 14,164 | 32.5% |
| kind vocabulary (`master_list`) | 7,330 | 16.8% |
| stage instructions (template prose) | 6,430 | 14.8% |
| **S1's story, re-sent to S2 and S4** | 6,109 | 14.0% |
| mapping rules (`anti_routine`) | 4,467 | 10.3% |
| output schema (`output_contract`) | 2,187 | 5.0% |
| memory granularity (S1 only) | 1,551 | 3.6% |
| date anchor | 653 | 1.5% |
| chains | 547 | 1.3% |
| graph search results (S4) | 129 | 0.3% |
| **composition total** | **43,567** | 100.0% |
| *fitted constant (#14295)* | 45,503 | — |
| *unattributed (fit intercept − composition)* | 1,936 | — |

The document itself is NOT in that table: it is the fit's slope, 8,754 tokens/doc (1.68 × 5,211.0) against the fitted 11,250. S1 sends the WHOLE document once per call, and 34/50 documents take a second S1 call, so the document is paid for ~2x.

Solved dynamic regions: the S1 story is **2,656** tokens as the later stages see it, and S2's embed list is re-serialized into S4's prompt at **12,425** tokens — 1.23× S2's own emission.

### The output constant, decomposed

| stage | completion tokens/doc | share of output |
|---|---|---|
| s1 | 2,798 | 15.7% |
| s2 | 11,677 | 65.5% |
| s4 | 3,354 | 18.8% |
| **total** | **17,830** | 100.0% |

S2 emits the whole embed list, and it is the same object that becomes S4's largest input region — so one stage's emission is paid for THREE times: as output, as S1's story is re-read, and as the list is re-serialized. Nothing in this table scales with the document: the item COUNT is what moves, and a document's length barely moves it (R²=0.000 on S2's input fit).

### Candidate levers, sized offline

| lever | size (tokens/doc) | quality-neutral? |
|---|---|---|
| compact master render (2,335 vs 3,187 tokens/call) | −1,960 | NO — compact mode story-filters the pack kinds, so a document that misses every trigger word drops to the all-packs fallback and a document that hits one drops the others — not quality-neutral |
| S1 truncation double-send (68.0% of documents take 2 S1 calls) | −4,652 | NO — a longer narrative is more content, not less — but it also enlarges the story both later stages re-read, so the net is a measurement, not arithmetic; the story's own growth is NOT sizeable offline |
