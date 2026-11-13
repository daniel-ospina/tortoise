---
title: "`_`-preserving OR terms on the real path — #12298's arm ported and re-measured on both product surfaces (#12360)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-13
updated: 2026-11-13
subjects.team: epistemic-team
issue: "#12360"
aboutSubjects: sparse query tokenizer, FalkorDB fulltext index, RediSearch word characters, capture-time candidate prior, MCP tortoise_search / SDK tortoise_fts_query
aboutObjects: "#12298 M3 measurement, epic #12186, #11901 candidate-recall instrument, #12350 tokenizer contract, #8933 additive alias precedent, #2070 byte-identity control, SEARCH-ARCHITECTURE.md §6.2 / V1 deferral"
---

# `_`-preserving OR terms, on the real path

**Issue:** [#12360](https://github.com/daniel-ospina/tortoise/issues/12360) · **PR:**
[#12368](https://github.com/daniel-ospina/tortoise/pull/12368) · **Harness:**
`tools/underscore_or_terms_12360.py` · **Artifacts, this directory:** `artifact.json`,
`artifact-repeat.json` (an independent second run), `population.json` (a verbatim copy of
`#12298`'s pinned 300-name sample, sha256 `095d3aa5…`).

---

## 0. The verdict in one table

The arm from `#12298` is **ported into the shipped query construction** and re-measured there.
The two arms are the *same production path* with `TORTOISE_UNDERSCORE_TERMS` the only
difference — no harness-side term injection, no replica Cypher.

| surface (its own production window) | recall@3 before | after | Δ | paired p | regressions | nDCG@10 |
|---|---|---|---|---|---|---|
| **MCP / SDK search** — `sdk.tortoise_fts_query` (the door `tortoise_search` is registered to; REST `GET /v1/search`) | 0.7133 | **0.9633** | **+25.00 pts** | < 1e-22 | **0** | 0.6917 → 0.9273 |
| **capture prior** — `extractor_v2._fts_rows` (link-before-create, window 3) | 0.7133 | **0.9633** | **+25.00 pts** | < 1e-22 | **0** | 0.6917 → 0.9273 |
| *reference:* the FTS leg in isolation (`run_fts_query`) — the number `#12298` published | 0.7133 | 0.9633 | +25.00 pts | < 1e-22 | 0 | 0.6917 → 0.9273 |

**The falsifier on `#12360` is met on both surfaces** (≥ +10 points, visible on each, zero
regressions), and the isolation arm **reproduces `#12298`'s published 0.7133 → 0.9633 with
75 gains / 0 losses exactly** — so the port is faithful and the earlier measurement was not a
harness artifact.

⚠️ **The three rows are identical, and that is a finding, not a copy-paste.** The dense leg is
not installed on a plain lane (`sentence_transformers` absent), so the two fused surfaces are
the fused read in its **keyword-only** shape: one live leg, so RRF preserves the leg's order.
The artifact carries this as `dense_leg`. What the identity proves is the thing worth
proving — **both doors pass the raw text down and none of them re-tokenises it away** — and
what it does NOT prove is how the composite interacts with a live dense leg (declared in §5).

## 1. What was ported, exactly

`#12298`'s harness arm (`tools/m3_fts_leg_recall.py::recovery_terms`), verbatim in its run
test — *a maximal run of `[A-Za-z0-9_]` that still carries `_`, lowercased, length ≥ 2* —
moved out of the harness and into `tortoise/sparse.py::underscore_run_terms`, appended by
`build_or_query` as a **third additive tail** after the originals, the A4 expansion tail, and
the `#8933` alias/phrase tails.

`build_or_query` is the one seam: `run_fts_query` (`search_engine.py:2800`) builds through it,
`sdk.tortoise_fts_query` reaches `run_fts_query`, `extractor_v2._fts_rows` reaches
`sdk.tortoise_fts_query`, and the MCP registry binds `tortoise_search` to
`sdk_method="tortoise_fts_query"`. One builder, every reader.

⚠️ **The trap `#12360` warned about, confirmed by code reading.** `run_fts_query` REBUILDS the
query through `build_or_query`, so a term set handed to it is re-tokenised straight back:
`#12298`'s first arm passed `search|web|web_search` and measured **+0.33 pts**. That is why
the repair had to live *inside* the builder, and why every assertion in
`tests/test_12360_underscore_or_terms.py` is on the **emitted** `$query` parameter of the
Cypher that reaches `db.idx.fulltext.queryNodes` — never on the builder's input.

Three properties the port keeps:

* **Additive** — a term is only ever added to the OR union, so a query that matched before
  still matches. The zero-loss column is structural, which is why it is 0/75 both here and in
  `#12298`.
* **No write-path change** — RediSearch already stores the composite (that is the defect), so
  records written before this change are reachable.
* **Byte-identical for every query without an inner `_`** — pinned, including `#8933`'s own
  `ERR-4032` → `err|err4032|"err 4032"`.

Two deliberate **hardenings over the measurement arm**, both declared in the function's
docstring: an alnum-free run (`___`) is dropped, and the run is bounded at
`MAX_IDENTIFIER_LEN` (48). The population's longest run is 31, so the bound moved nothing.

## 2. Per class — where the effect lives (recall@3, before → after)

| class | n | before | after |
|---|---|---|---|
| definite description | 180 | 0.9778 | 0.9778 |
| identifier (`#n`, `snake_case`) | 90 | **0.2000** | **0.9444** |
| path | 30 | **0.6667** | **0.9333** |

`definite` is unchanged *and* is the falsifier's control: the fix does not lift prose, it
recovers the code-shaped names whose composite the tokenizer was destroying. The `#n` shapes
(`#2258`, `PR #12298`) carry no `_` and were always found — consistent with `#12298` §3.3.

Empty result sets at k=15: **21 → 0**.

## 3. The `#8933` identifier fixture (the DoD-level acceptance cases)

Five records written after the sweep (so they cannot move a document frequency under a
measured query), queried at the prior's production window and at k=15; the criterion is
**reachability**.

| stored name | before | after |
|---|---|---|
| `ERR-4032` | rank 2 | rank 2 |
| `INV-2024-0091` | rank 1 | rank 1 |
| **`abc_def`** | **never (0 rows)** | **1** |
| **`test_7949_capstone_verdict_check`** | **never at any k** | **4** (at k=15) |
| `ERR-4032 retry` (query `ERR-4032`) | rank 1 | rank 1 |

**3 of 5 → 5 of 5**, on every surface — the same split `#12298` measured. `abc_def` returning
**zero rows for its own name** is the crispest case in the lane, and
`tests/test_12360_underscore_or_terms.py::TestLiveReachability` pins it on a live graph with
the switch as the only difference between the arms.

## 4. Why the fix is a QUERY repair and not the tokenizer contract

`#12350` is the tokenizer contract, and its own analysis rejected making
`tokenize_sparse_query` keep `_`: that would change the fallback stacks' vocabularies
(`tortoise/bm25.py` tokenises the corpus with the same function) and the pseudo-relevance
expansion path too — a much wider blast radius for the same recall. `#12360` is the narrower
lever it named, and `test_tokenizer_still_splits_this_is_a_query_side_repair` pins that the
tokenizer was NOT changed here. **The two issues are therefore not duplicates**, and this PR
does not close `#12350`: the tokenizer's own contract (fallback-vocabulary equivalence, the
`#2070` byte-identity control, `#10122`'s advisory computing `name_tokens` and
`indexed_tokens` with the same tokenizer on both sides) is still its own job.

⚠️ `#10122`'s "stored but unfindable" advisory stays silent about exactly this class, because
it tokensises both sides with our splitter. `#12350` flagged that as a scoping decision for
whoever takes it; **this PR does not change the advisory**, so it still cannot see the case it
exists to report. Recorded here rather than quietly fixed (a "stored but unfindable" verdict
that starts firing on every `snake_case` name is a product-visible behaviour change that needs
its own measurement).

## 5. Declared gaps

* **`:Object` only.** The mechanism is label-independent (the index tokenises stored text on
  every label) and should be *worse* on `:Point`, where a composite inside `content` is the
  only term the index holds — a prediction, not a measurement.
* **The dense leg is absent here**, so the two fused surfaces are measured keyword-only, and
  the interaction between +25 pts of lexical recall and a live dense leg is unmeasured
  (`#11901` showed the dense leg *hurts* the definite class, so the interaction is not
  obviously additive).
* **The population is a sample of the strings captures contain**, not a census of `:Object`
  rows, and its quotas oversample identifiers (30 %) and paths (10 %). The composite headline
  is **stratified, never class-share weighted**; the production-share estimate is `#12298`
  §4.3's (`share(snake_case) × +91.8 pts`, with no measured lower bound).
* **The `_`-preserving run's index-side meaning is the vendor's.** That RediSearch treats `_`
  as a word character is measured on a live FalkorDB 4.20.6 (`#12298` §3.4 probe), not read
  from a document — so a tokenizer change in a future FalkorDB could retire this repair.
  `TORTOISE_UNDERSCORE_TERMS=0` is the bisect switch if it does.
* **`#11901`'s `rows_zero = 11`** vs this population's 21 is a different sample of the same
  defect, not a regression: the populations are not the same 300 names.

## 6. How to reproduce

```bash
uv run python tools/test_lane.py uri            # then eval the exported lines
uv run python tools/underscore_or_terms_12360.py run \
    --graph u12360-run$RANDOM \
    --out docs/research/2026-11-13-12360-underscore-or-terms/artifact.json
```

The harness refuses a reused graph (`Object.name` is ON CREATE, `#8919`), refuses a partially
built index (waits for `numDocuments == n`, refuses on timeout), and refuses a population file
without `id`/`name`/`cls` rows. Its two runs are committed; their per-(arm, surface) rank
vectors are identical.
