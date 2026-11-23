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
uv run python tools/extractor_cost_measure.py decompose \
    --evidence docs/evidence/14277-extractor-cost/measurements.jsonl \
    --out docs/evidence/14343-amplify/decomposition.md \
    --json-out docs/evidence/14343-amplify/decomposition.json
```

⚠️ **The live arms of this issue are BLOCKED on this box, and the block is environmental,
not a shortcut.** `compose` (which runs the real pipeline over one document to capture
prompts *and emissions*) needs a provider key. This cell has **none**: `OPENROUTER_API_KEY`,
`DEEPSEEK_API_KEY`, `VENICE_API_KEY`, `OPENAI_API_KEY` and `TORTOISE_API_KEY` are all unset,
there is no repo-root `.env`, and there is no `~/pi-keys.env` — the three homes
`.env.example` names are "evals → this repo-root `.env`", "fleet → `~/pi-keys.env` on the
workstation", "production → Fly secrets". `build_extractor_model()` therefore degrades to its
"no key at all" default, a single OpenRouter adapter, and every call returns
`401 Client Error: Unauthorized`. So the **before → after** measurement and the `#7949`
quality re-run are **not delivered here** — they need a key, on a box that has one. The
composition, the output breakdown and the lever sizes are all offline-measured and are
delivered in full.

---

## 1. The input constant, decomposed

Median document 5,211 tokens · 3.98 calls/document (s1 1.68, s2 1.16, s4 1.14).

| region | tokens/doc | share of the constant | what it is |
|---|---|---|---|
| **S2's embed list, re-sent to S4** | **14,164** | **32.5 %** | S4's prompt re-serializes S2's *entire* output (`json.dumps(embed_list, indent=1)`) |
| kind vocabulary (`master_list`) | 7,330 | 16.8 % | the pack kind catalogue + glosses, identical bytes on every S2 **and** S4 call |
| stage instructions (template prose) | 6,430 | 14.8 % | the three templates' own prose (edge semantics, value filter, carve-outs) |
| **S1's story, re-sent to S2 and S4** | **6,109** | **14.0 %** | S1's narrative is S2's user message *and* S4's `{story}` slot |
| mapping rules (`anti_routine`) | 4,467 | 10.3 % | `ANTI_ROUTINE_EXCLUSION` + `VALUE_FIDELITY_RULE` + `OPERATOR_SEMANTICS_RULE` + `SHORT_SESSION_FACT_RULE`, spliced into both mapping stages |
| output schema (`output_contract`) | 2,187 | 5.0 % | the JSON contract every stage must satisfy |
| memory granularity (S1 only) | 1,551 | 3.6 % | the per-namespace durability rules S1 keeps/drops by |
| date anchor | 653 | 1.5 % | the `when`/`startedAt` emission rules |
| chains | 547 | 1.3 % | the chain-position block |
| graph search results (S4) | 129 | 0.3 % | empty (`sdk=None`) — a **floor**; a populated graph adds candidates |
| **composition total** | **43,567** | 100 % | |
| *fitted constant (`#14295`)* | *45,503* | — | |
| **unattributed** | **1,936** | **4.3 %** | the fit's intercept is an OLS line, not a mean: the 128k-token document carries 27 % of the corpus's prompt tokens and pulls the intercept down. This bundle does not close that gap by pro-rating it — an unexplained remainder is a finding, and the finding here is that the fit and the composition agree to 4.3 %. |

**The document itself is not in that table.** It is the fit's *slope*: S1 sends the whole
document text once **per call**, and 34 of 50 documents take a **second** S1 call
(see §3), so at the median the document is paid for 8,754–11,250 tokens.

### What this says

1. **A third of the "input constant" is the pipeline's own S2 output coming back.** Nothing
   static explains it; S4 reads the list S2 just wrote, re-serialized at **1.23×** S2's own
   emission (the `indent=1` dump plus the post-S2 normaliser's added fields — this bundle
   does not separate those two, because separating them needs a real emission, §0).
2. **The static instruction set is 23,300 tokens/doc** — 53 % of the constant — and it is
   the same bytes on every call: the kind vocabulary (7,330) alone is 16.8 %.
3. **S1's story is charged three times per document**: once as S1's output, once as S2's
   user message, once as S4's `{story}` slot. 6,109 tokens of the input constant are a
   *re-read* of an emission that does not scale with the document (§2).

---

## 2. The output constant, decomposed

| stage | completion tokens/doc | share of output | calls/doc |
|---|---|---|---|
| s1 | 2,798 | 15.7 % | 1.68 |
| **s2** | **11,677** | **65.5 %** | 1.16 |
| s4 | 3,354 | 18.8 % | 1.14 |
| **total** | **17,830** | 100 % | |

`#14295`'s per-stage *input* fits say the same thing from the other side: S1 is
`1,909 + 2.056 × T` (R²=0.995, the only stage that reads the text), S2 is
`14,916 + 0.006 × T` (R²=0.000), S4 is `28,678 + 0.097 × T` (R²=0.009).

**A stage whose output does not scale with its input is emitting something that is not
derived from the input. Here is what it is deriving from.**

S2 emits the whole embed list, and what moves its size is the **item count** — a constant of
the record schema — not the document. The measured population is in `#1789`'s bundle
(`docs/evidence/1789-s4-reeemit-tax/`, 7 real sessions, 2 runs): **311 recorded items over 7
sessions** (266 of them in the four tax-surface sections, 29–49 items per session). At S2's
measured **10,066 completion tokens per call**, that is **~227 tokens per emitted record**.
The cost is therefore `items × record-shape`, and the record shape is the thing to look at:

| field on a `points[]` record | rough tokens | quality role |
|---|---|---|
| `content` | ~40 | the claim itself |
| `about_entities` | ~15 | linking |
| `slots` (subject/object/event, each `{name,kind,confidence}`) | ~25 | typed participation (#1418) |
| `quote` (verbatim, ≤200 chars) | ~55 | **E3 provenance** — the `#7949` verbatim anchor |
| `search_keys` (2–4 aliases) | ~20 | E3 resolution |
| `tier`, `when`, `supersedes`, `source_turn_id`, `pointKind` | ~30 | lifecycle/E2/E3 |
| JSON punctuation + `indent` | ~40 | — |

i.e. **roughly half of each record is provenance and participation metadata, not the claim.**
That is a quality *feature* (E2/E3), not slack — which is why this issue's lever is not
"emit less", and why any change here must clear `#7949` rather than an arithmetic target.

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
| **Bound / shape S2's emission** (the 14,164-token re-read is `items × ~227 tokens`) | the dominant term by construction: cutting the item count or the record shape by 30 % removes ~4,200 output + ~5,100 S4 input per document | **NO** — the records carry E2/E3 provenance; this is the `#7949` trade, and it is a measurement, not arithmetic |
| compact kind render (`TORTOISE_EXTRACTOR_PROMPT=compact`) | **−1,960 tokens/doc** (master render 3,187 → 2,335 per call × 2.30 mapping calls) | **NO** — compact mode **story-filters** the pack kinds: a document that misses every trigger word falls back to all packs, one that hits a single trigger drops the other packs. That is a vocabulary-visibility change, i.e. exactly the class of change `#7949` arbitrates. It is also already-shipped, opt-in, and off. |
| S1 truncation double-send | **−4,652 tokens/doc** (68 % × (T + 1,630)) | **NO** — a longer narrative is *more* content, but the story then grows inside both later prompts (6,109 tokens/doc today), so the net is a measurement, not arithmetic |
| `indent=1` → compact separators on S4's `embed_list_json` | bounded by the 1.23× gap (≈2,400 tokens/doc, 5 %) | arguably yes (pure whitespace), **but unverifiable here**: it changes prompt bytes and the `#7949` bar cannot be run without a key |

**Nothing was shipped.** Every lever above changes either the vocabulary the model sees or
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

# 2. before → after on the SAME corpus (arms are labels; the cache ladder is the axis)
uv run python tools/extractor_cost_measure.py run --docs 50 --arms before,after \
    --out docs/evidence/14343-amplify/measurements.jsonl
uv run python tools/extractor_cost_measure.py decompose \
    --evidence docs/evidence/14343-amplify/measurements.jsonl --arm after \
    --out docs/evidence/14343-amplify/decomposition-after.md
```

`compose` is in the tree and works offline (`--offline`, a canned emission: a **shape**
check, never a measurement — S4's `embed_list_json` region is a stub in that arm, and the
tool says so in its output).

---

## 5. Tokens per document, and what it buys

Unchanged by this bundle, and re-stated so the lever's value is on the record. From
`#14295`'s measured median (48,461 in + 16,338 out = **64,799 tokens/document**):

| tier | allowance | documents/month at 64,799 | at 45,000 (−30 %) | at 32,000 (−50 %) |
|---|---|---|---|---|
| solo | 3M | 46 | 66 | 93 |
| pro | 4M | 62 | 88 | 125 |
| team | 24M | 370 | 533 | 750 |

A 30 % cut — which the composition says is available *in principle* (S2's re-read plus the
kind vocabulary alone are 49 % of the input constant) — is worth **+43 %** documents per
allowance. That is the number `#14275` should be re-quoted at once a lever is measured green.
