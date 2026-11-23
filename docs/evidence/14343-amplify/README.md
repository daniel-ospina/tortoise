---
title: "#14343 — what the extractor's 45,503-token input constant and its 22,802-token output constant are made of"
type: evidence
domain: platform
doc_status: final
created: 2026-11-23
subjects.team: epistemic-team
aboutSubjects: extractor v2 stage prompts (S1 story summary, S2 map-to-embed, S4 gap review)
aboutObjects: "#14343, #14295, #14277, #14275, #14219, #7949"
---

# `#14343` — the amplification, decomposed

`#14295` measured **how much** the extractor spends and fitted two numbers to it:
`tokens_in = 45,503 + 2.159 × T` and `tokens_out = 22,802 + 0.180 × T`. It could not
say what those constants are **made of**, so nothing could be shrunk without guessing.

This bundle answers that. It adds no assumption of its own: every number below is
either (a) `tiktoken` on a **real render** of the stage prompts, or (b) a measured token
count already in `#14295`'s `measurements.jsonl`.

**Reproduce (no provider call, no spend):**

```bash
# Requires the PRODUCTION render posture — `TORTOISE_S4_DELTA`,
# `TORTOISE_EXTRACTOR_PROMPT`, `TORTOISE_CLASSIFY_LATER`,
# `TORTOISE_REFERENTIALITY` and `TORTOISE_PACKS_DIR` all unset: each moves the
# S1/S2/S4 static prose, so a shell in compact/classify-later mode, with the
# referentiality contract armed, or on a revert-flagged S4_DELTA regenerates
# different §1/§2 numbers and the diff reads as rot. Measured per switch (the
# first three move 8–34 lines; `TORTOISE_REFERENTIALITY=1` moves
# `output_contract` 951 -> 1,019 and the S1 story 2,656 -> 2,588;
# `TORTOISE_PACKS_DIR` aimed at a valid alternative pack tree moves `master_list`
# 3,187 -> 2,601 and `memory_granularity` 923 -> 845).
# `tests/test_extractor_cost_measure.py` pins all FIVE — the fourth came from
# measuring the set rather than reading it, and the
# capstone door (`docs/evidence/7949-capstone/probes/capstone_measure.py`,
# `DOOR_PINNED_OFF`) already listed it as an ambient prompt-contract switch; the
# fifth is a PATH knob rather than a flag, which is why reading the flag list
# missed it. The bound is that `packs/` resolves from the in-tree tree. The
# `#14343` layout toggle (`TORTOISE_S4_EMBED_JSON`) is NOT in that set: measured,
# it moves no recorded number here — the embed-list region is priced from the
# receipts, not re-rendered. `TORTOISE_LABEL_ORDER` changes bytes but not token
# counts (3,187 either way), so it needs no pin.
uv run python tools/extractor_cost_measure.py decompose \
    --evidence docs/evidence/14277-extractor-cost/measurements.jsonl \
    --receipts tests/eval/write_path/receipts/7947-arms-2026-10-18 \
    --out docs/evidence/14343-amplify/decomposition.md \
    --json-out docs/evidence/14343-amplify/decomposition.json
```

⚠️ **The live arms of this issue are BLOCKED on this box, and the block is environmental,
not a shortcut** (`compose`'s measured arm and the `#7949` re-run both need a provider key).
The full diagnosis, and what is and is not delivered because of it, is **§0**.

---

## 0. What could not run here

**No provider key on this box.** `OPENROUTER_API_KEY` / `DEEPSEEK_API_KEY` /
`VENICE_API_KEY` / `OPENAI_API_KEY` / `TORTOISE_API_KEY` are all unset, there is no
repo-root `.env` and no `~/pi-keys.env` — the three homes the `.env.example` names.
`build_extractor_model()` therefore degrades to its "no key at all" default (a single
OpenRouter adapter) and every call returns `401 Client Error: Unauthorized`.

**Not delivered:** a model-run before → after, and the `#7949` quality re-run.
**Delivered:** the composition, the output breakdown, the exact lever sizes, and the lever
itself behind a flag. Every figure below is either an **offline render measurement** (exact,
`tiktoken` on the real prompt bytes) or **derived from `#14295`'s measured token counts and
the real S2 emissions in `tests/eval/write_path/receipts/`**. `compose` says which of the two
it is printing (`⚠️ OFFLINE / STUB TRANSPORT — NOT A MEASUREMENT`), because a stub whose
output is indistinguishable from a measurement is the false pass this bundle must not be.

---

## 1. The input constant, decomposed

Median document 5,211 tokens · 3.98 calls/document (s1 1.68, s2 1.16, s4 1.14).

| region | tokens/doc | share of the composition (42,509) | what it is |
|---|---|---|---|
| **S2's embed list, re-sent to S4** | **14,149** | **33.3 %** | S4's prompt re-serializes S2's *entire* output (`json.dumps(embed_list, indent=1)`) |
| kind vocabulary (`master_list`) | 7,330 | 17.2 % | the pack kind catalogue + glosses, identical bytes on every S2 **and** S4 call |
| stage instructions (template prose) | 6,430 | 15.1 % | the three templates' own prose (edge semantics, value filter, carve-outs) |
| **S1's story, re-sent to S2 and S4** | **5,066** | **11.9 %** | S1's narrative is S2's user message *and* S4's `{story}` slot — **both** pay the `#10311` untrusted-data envelope, 93 tokens a call. ⚠️ The 9 documents whose prompts carry a SOURCE TRANSCRIPT block have that block **subtracted per document before the medians**, so this row is narrative — not verbatim source text |
| mapping rules (`anti_routine`) | 4,467 | 10.5 % | `ANTI_ROUTINE_EXCLUSION` + `VALUE_FIDELITY_RULE` + `OPERATOR_SEMANTICS_RULE` + `SHORT_SESSION_FACT_RULE`, spliced into both mapping stages |
| output schema (`output_contract`) | 2,187 | 5.1 % | the JSON contract every stage must satisfy |
| memory granularity (S1 only) | 1,551 | 3.6 % | the per-namespace durability rules S1 keeps/drops by |
| date anchor | 653 | 1.5 % | the `when`/`startedAt` emission rules |
| chains | 547 | 1.3 % | the chain-position block |
| graph search results (S4) | 129 | 0.3 % | empty (`sdk=None`) — a **floor**; a populated graph adds candidates |
| **composition total** | **42,509** | 100 % | |
| *fitted constant (`#14295`)* | *45,503* | — | |
| **unattributed** | **2,994** | **6.6 % of the fitted constant** | the fit's intercept is an OLS line through 50 points, and one of them is far outside the others: the 127,914-token document is 9.3 % of the corpus's prompt tokens but 29.7 % of its *document* tokens, and it is a **high-leverage** point — drop it and the intercept falls 45,503 → 30,156 (R² 0.715 → 0.523). So the fit's intercept sits ABOVE the composition, and 2,994 is what an OLS line does at `T=0` where the composition is a measurement at the median. This bundle does not close that gap by pro-rating it — an unexplained remainder is a finding, and the finding here is that fit and composition agree to 6.6 %. ⚠️ **This row's share is OF THE 45,503 FIT, not of the 42,509 column above it** — it is the only row on a different denominator, so the column does not sum to 100 % and is not meant to: 2,994 / 42,509 would be 7.0 %, and the honest pairing is the fit's own. |

**The document itself is not in that table.** It is the fit's *slope*: S1 sends the whole
document text once **per call**, and 34 of 50 documents take a **second** S1 call
(see §3), so at the median the document is paid for 8,754–11,250 tokens.

### What this says

1. **A third of the "input constant" is the pipeline's own S2 output coming back.** Nothing
   static explains it; S4 reads the list S2 just wrote, re-serialized at **1.23×** S2's own
   emission (the `indent=1` dump plus the post-S2 normaliser's added fields — this bundle
   does not separate those two, because separating them needs a real emission, §0).
2. **The static instruction set is 23,165 tokens/doc** — 54.5 % of the composition (51 % of
   the 45,503 constant) — and it is the same bytes on every call: the kind vocabulary
   (7,330) alone is 17.2 % of the composition. (23,165 is the seven static rows summed; the
   `graph search results (S4)` row is **not** in it — that row is the empty `sdk=None`
   lookup, a *floor*, and adding it would give 23,294.)
3. **S1's story is charged three times per document**: once as S1's output, once as S2's
   user message, once as S4's `{story}` slot. 5,066 tokens of the input constant are a
   *re-read* of an emission that does not scale with the document (§2).

---

## 2. The output constant, decomposed

| stage | completion tokens/doc | share of the composition | calls/doc |
|---|---|---|---|
| s1 | 2,798 | 15.7 % | 1.68 |
| **s2** | **11,677** | **65.5 %** | 1.16 |
| s4 | 3,354 | 18.8 % | 1.14 |
| **composition total** | **17,830** | 100 % | |
| *fitted constant (this arm's own intercept)* | *22,802* | — | |
| **unattributed** | **4,972** | 27.9 % of the composition · 21.8 % of the fitted constant | |

**Two estimators, both reported — the 4,972 is not noise, and it is a result.** The rows are
per-call **medians** × the measured call counts (the median document's shape). The fitted
constant is the intercept of this arm's own per-document fit, and output's fit is essentially
flat (slope 0.180, R² 0.020), so that intercept sits near the **mean** output per document
(24,350) rather than the median. The gap is the tail described below: **S2 carries 51.2 % of
the fitted constant** and 65.5 % of the attributed composition, and both are printed because
the remainder cannot be attributed by construction.

**S4's output is bimodal on a document corpus, and the tail is the constant.** Per call the
median is 2,942 completion tokens — the shipped delta contract (#1789) holding — but **13 of 50
documents (26 %) average more than 6,000 completion tokens per S4 call, up to 24,000**, and the
mean (5,532) is 1.9× the median. (Those are DOCUMENTS, not calls — one entry per document at
≈1.14 calls each.)
That tail is why `#14295` reported 7,102 output tokens per S4 call while the median is 2,942:
it is not "S4 costs 7k", it is "S4 costs 3k on three quarters of documents and 10–24k on the
other quarter", and a quarter of documents is where the gap between the mean and the median —
i.e. most of the 4,972 above — comes from. `#1789`
measured the delta contract's *aggregate* saving on 26-turn sessions; this is the same
contract on **documents**, and it is not uniformly complying. That is a finding of this bundle,
not a lever: it is a re-emission of the embed list, which is the `#7949` channel.

⚠️ **Reconciliation with §4a/§5.** §2's 17,830 is `Σ(calls × median per call)`; §4a/§5's
16,338 is the **median of the per-document totals**. They are different estimators of the same
quantity and agree to 9 %; neither is a typo, and the fitted constant (22,802) is a third. The
headline bill in §5 uses the median-of-totals because that is the number `#14295` measured
a document at.

`#14295`'s per-stage *input* fits say the same thing from the other side: S1 is
`1,909 + 2.056 × T` (R²=0.995, the only stage that reads the text), S2 is
`14,916 + 0.006 × T` (R²=0.000), S4 is `28,678 + 0.097 × T` (R²=0.009).

**A stage whose output does not scale with its input is emitting something that is not
derived from the input. Here is what it is deriving from.**

S2 emits the whole embed list, and what moves its size is the **item count** — a constant of
the record schema — not the document. The measured population is the S2 emissions in the
write-path receipts (§4a): **580 items over 14 emissions, 58,544 completion tokens** across
the 7 sessions `#1789` measured — **100.9 completion tokens per emitted record**, and the
per-emission item count is 20–54. (`#1789`'s own `311` is the `narrative` arm alone; the
`raw` arm carries the rest, which is why both are counted here.) So the cost is
`items × record-shape` — and with a record at ~101 tokens, the shape below is most of it:

| field on a `points[]` record | rough tokens | quality role |
|---|---|---|
| `content` | ~25–40 | the claim itself |
| `about_entities` | ~10 | linking |
| `slots` (subject/object/event, each `{name,kind,confidence}`) | ~20 | typed participation (#1418) |
| `quote` (verbatim, ≤200 chars) | ~25–55 | **E3 provenance** — the `#7949` verbatim anchor |
| `search_keys` (2–4 aliases) | ~15 | E3 resolution |
| `tier`, `when`, `supersedes`, `source_turn_id`, `pointKind` | ~20 | lifecycle/E2/E3 |
| JSON punctuation | ~10 | — |

⚠️ Those per-field figures are the SHAPE's shares, not a measured partition — the emission is
not instrumented field-by-field, and a `points[]` record measured on its own is 210 tokens
(indent) / 141 (compact) at a median `content` length, because it carries a `quote` and
2–4 `search_keys`. What is measured is the **total**: 100.9 tokens per record. What follows
from the schema is that **provenance and participation metadata (`quote` + `search_keys` +
`slots` + `when`/`supersedes`/`source_turn_id`) are the larger half of every record**, which
is a quality *feature* (E2/E3), not slack — and why this issue's lever is not "emit less",
and why any change here must clear `#7949` rather than an arithmetic target.

---

## 3. The document term is double-charged by a truncation, not by the design

`2.159 × T` is larger than the 1.68 calls/doc a single send would cost, and the extra is not
"splitting a long document": the pipeline sends the **whole** document once per S1 call. The
cap is `_S1_MAX_TOKENS = 1500`; a narrative that hits it is **length-truncated**, which fires
`#2134`'s one-shot escalation — a **re-send of the identical prompt** at the 32,000-token
budget. Measured: **34 of 50 documents (68 %) take 2 S1 calls**, and the second call is the
same prompt.

Per call, offline render vs measured provider count (a validation of the composition):

| document | offline (cl100k) | measured per call | ratio | S1 calls |
|---|---|---|---|---|
| 439 tokens | 2,073 | 2,204 | 1.063 | 2 |
| 5,331 tokens | 6,965 | 7,208 | 1.035 | 2 |
| 127,914 tokens | 129,548 | 131,728 | 1.017 | 2 |

So S1's per-document input is `1.68 × (1,630 static + T)` and the mechanism that makes it
1.68 rather than 1.00 is a **truncation retry**, not a chunking policy.

---

## 4. Candidate levers, sized offline — none shipped, and why

| lever | size | quality-neutral? |
|---|---|---|
| **`indent=1` → compact separators on S4's `embed_list_json`** — ⚠️ **SHIPPED, default OFF** | **−4,598 tokens/doc (−7.1 %)**: 32.5 % of the 14,149-token region | **Information-neutral**: the list's content is byte-identical, only its layout differs. ⚠️ The *formatting* effect on the model is unmeasured here (§0), which is exactly why the default is today's bytes |
| **Bound / shape S2's emission** (the 14,149-token re-read is `items × ~101 tokens`) | dominant by construction: −30 % of the record removes ~3,500 output + ~4,200 S4 input per document | **NO** — half of each record is E2/E3 provenance (`quote`, `search_keys`, `slots`); this is the `#7949` trade, and it is a measurement, not arithmetic |
| compact kind render (`TORTOISE_EXTRACTOR_PROMPT=compact`) | **−1,960 tokens/doc** (master render 3,187 → 2,335 per call × 2.30 mapping calls) | **NO** — compact mode **story-filters** the pack kinds: a document that misses every trigger word falls back to all packs, one that hits a single trigger drops the other packs. That is a vocabulary-visibility change, i.e. exactly the class of change `#7949` arbitrates. It is also already-shipped, opt-in, and off. |
| S1 truncation double-send | **−4,652 tokens/doc** (68 % × (T + 1,630)) | **NO** — a longer narrative is *more* content, but the story then grows inside both later prompts (5,066 tokens/doc today), so the net is a measurement, not arithmetic |

**One lever was shipped, disarmed.** `TORTOISE_S4_EMBED_JSON=compact` switches S4's
embed-list serialization from `indent=1` to compact separators — the one change whose *content*
is provably identical. It is measured exactly below, and it ships **off**, because a prompt
whose bytes changed is a prompt whose effect on extraction is unmeasured, and `#7949` is the
arbiter of that. Arming it is one environment variable, on a box with a key, in the same session
as the capstone re-run.

Everything else was **not shipped**. Every lever above changes either the vocabulary the model sees or
the bytes of a prompt, and the capstone bar (`#7949` E1 volume / E2 correctness / E3
provenance / E4 operator edges) **cannot be re-run on this box** (§0). The brief's own rule
governs: *"A reduction that costs extraction quality is a different (worse) decision — report
it and stop rather than ship it."* Shipping an unmeasurable prompt change to production would
be exactly that decision taken silently.

**What is needed to finish:** a key on a box that has one (workstation `~/pi-keys.env`, or
`tools/run-with-eval-keys.sh` with a repo-root `.env`), then

```bash
# 1. the emission-side facts (one document, ~$0.05)
uv run python tools/extractor_cost_measure.py compose \
    --out docs/evidence/14343-amplify/compose-after.json

# 2. before → after on the SAME corpus. ⛔ The ARM IS A SETTING, NOT A LABEL:
#    `run --arms before,after` installs TORTOISE_S4_EMBED_JSON=compact for the
#    `after` batch and pins it OFF for the `before` batch, and every row records
#    `s4_embed_json` so a mislabelled measurement cannot hide.
#    ⚠️ The flag is PROCESS-GLOBAL (`os.environ`), so the arms run as SEQUENTIAL
#    BATCHES, not interleaved: the value is installed once per batch, before any
#    worker thread starts, and restored after the batch joins. That matters at
#    the default `--workers 6` — an install wrapped around the single call would
#    let a `before` thread trip over an `after` thread, which is a race rather
#    than an arm. (Until review cycle 9 nothing was installed at all and the same
#    render was measured twice, returning a −0 delta with no banner; cycle 10
#    moved the install out of the per-call path for the reason above.)
#    The resume key is (arm, kind, id), so a separate `--arms after` invocation
#    appends to the same file correctly.
uv run python tools/extractor_cost_measure.py run --docs 50 --arms before,after \
    --out docs/evidence/14343-amplify/measurements.jsonl
uv run python tools/extractor_cost_measure.py decompose \
    --evidence docs/evidence/14343-amplify/measurements.jsonl --arm after \
    --receipts tests/eval/write_path/receipts/7947-arms-2026-10-18 \
    --out docs/evidence/14343-amplify/decomposition-after.md
```

`compose` is in the tree and works offline (`--offline`, a canned emission: a **shape**
check, never a measurement — S4's `embed_list_json` region is a stub in that arm, and the
tool says so in its output).

---

## 4a. The one information-neutral lever, measured on real emissions

The write-path receipts (`tests/eval/write_path/receipts/7947-arms-2026-10-18/*.arms.json`,
the same 7 sessions `#1789` measured) carry the **real S2 embed lists**, so both
serializations are computed on the same data — 14 emissions, **580 items**:

| | tokens |
|---|---|
| `json.dumps(embed_list, indent=1)` — today | **77,561** |
| `json.dumps(embed_list, separators=(",", ":"))` | **52,346** |
| **pure whitespace** | **25,215 (32.5 %)**, 1,801 per emission |

The ratio is stable across every emission (29.6 %–35.0 %), so it is a property of the
serialization, not of one session. S2's own emission is **100.9 completion tokens per item**,
so the re-serialized copy is ~1.3× the emission it copies: the whitespace is *added* by the
prompt builder, not by the model.

**Before → after, in the measured unit.** The document corpus's embed region is
**12,412 tokens per S4 call** (solved in §1) — about twice the receipts' ~5,540, because a
document yields a longer list — which is **14,149 tokens per document** at the measured
1.14 S4 calls/document. ⚠️ The 32.5 % ratio is applied to the **per-document** figure, because
the before→after table below is per document: 32.5 % × 14,149 = **4,598**. (Applying it to
12,412 would give 4,034 — the per-CALL saving — which is a different row, not a different
answer.)

| | before | after (flag armed) | Δ |
|---|---|---|---|
| input tokens / median document | 48,461 | 43,863 | −4,598 |
| output tokens / median document | 16,338 | 16,338 (unchanged by construction) | 0 |
| **tokens / median document** | **64,799** | **60,201** | **−7.1 %** |
| documents / month · solo (3M) | 46 | 50 | +8.7 % |
| documents / month · pro (4M) | 62 | 66 | +6.5 % |
| documents / month · team (24M) | 370 | 399 | +7.8 % |

⚠️ The output column is "unchanged *by construction*" — the input to S4 changed, so what S4
*emits* is the thing the capstone measures, and it has not been measured here.

## 5. Tokens per document, and what it buys

Unchanged by this bundle, and re-stated so the lever's value is on the record. From
`#14295`'s measured median (48,461 in + 16,338 out = **64,799 tokens/document**):

| tier | allowance | documents/month at 64,799 | at 45,000 (−30 %) | at 32,000 (−50 %) |
|---|---|---|---|---|
| solo | 3M | 46 | 66 | 93 |
| pro | 4M | 62 | 88 | 125 |
| team | 24M | 370 | 533 | 750 |

A 30 % cut — which the composition says is available *in principle* (S2's re-read plus the
kind vocabulary alone are 50.5 % of the composition, 47 % of the constant) — is worth
**+43 %** documents per allowance. The one lever shipped here (disarmed) is worth **+8 %**
on its own. **Neither is quotable as a plan until the capstone re-runs green**: what is
measured here is the size of the lever, not its safety.
