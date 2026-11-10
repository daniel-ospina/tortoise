---
title: Extractor v4 — Architecture
type: engineering
domain: platform
doc_status: draft
created: 2026-09-23
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: EXTRACTOR-V4-ARCHITECTURE.md, extractor pipeline, S2.2 VET, entity resolution
---

# Extractor v4 — Architecture

**Status:** design. **Home:** `#4894` (write-path redesign). **Predecessor:** `#1509` (extractor v3; ⚠️ **v3 is not absent from the tree: E2's carve-out (`STATE_VALUE_CARVE_OUT`, `#1534`) and E4 (`merge_embed_lists`) are load-bearing here**). **This document states the target design and the code facts it rests on, with their authority (`#N`, PR, or `file:line`).**

**⚠️ Code citations — revision pin.** Every `file.py:NNNN` reference here is pinned to commit **`c79ba1cf2`** — **except where a passage names a later pin.** ⚠️ **A `4c801fb18` pin covers:** the **S4 drift marker** (⚠️ *not* under any "§S4" heading — no such section exists, because the document's position is *"There is no S4"*; the marker lives under the **`### S2.2 — VET`** heading), its §7 `value_gate` lines, the **`packs/_template/manifest.yaml:260` citation** — which sits in this document's §S2.2 "the pack stops being prose" paragraph at **`:574`** (⚠️ **`574` is the *document's* line number; the manifest citation is `:260`** — do not read it as `manifest.yaml:574`), and the **§4.3 G1 row's `docs_miner.py` line**. Those are **not verifiable at the older pin** — `tortoise/docs_miner.py` does not exist at `c79ba1cf2`, and `packs/_template/manifest.yaml` was only 190 lines there (so a `:260` citation is out of range). ⚠️ **The reverse also happens: `kind_classifier.py:93` (§S2.3's `KindClassifier` row, `:652`) is correct ONLY at the DEFAULT pin `c79ba1cf2`** (it is `llm_tail: bool = True` there); at `4c801fb18` that line is `lam: float = LAMBDA`. Line numbers drift as the code moves; **the symbol name is authoritative and the line number is a convenience.** Verify against the symbol, not the number. **A third pin, `7d348d351`, covers:** §S0/§9's D10, T6 and Q3/Q5 passages, §S2.3's Jev row, §7's `:Subject`/`:Object` fix, the §D table's middle cell, and **§4.4's `_render_search_results` (`extractor_v2.py:2353` at `7d348d351`) citation**. ⚠️ §7's `VALUE_FIDELITY_RULE` / `_s2s4_rules()` / `_granularity_text()` numbers are **not** `7d348d351` ones either — they are `25b84d6599` numbers, which is the fifth pin below (`9098c49c10` moved those symbols to `:455`, `:556` and `:1444`). **A fourth pin, `653980445`, covers:** §4.3's *G5's destructive half* subsection — every `file.py:NNNN` number in it, which are re-resolved HEAD numbers (read at the default pin `c79ba1cf2`: five of the cited files are shorter than their cited line (`sdk.py`, `assembly.py`, `projection/entities.py`, `projection/__init__.py`, `commit_ops.py`), and `tortoise/definite_description_backfill.py` does not exist there, so an unpinned number in that passage is no citation at all). **§10's `_render_source_transcript` citations belong to that same `653980445` pin:** `extractor_v2.py:1755` (the `render_s2_prompt` call), `:1775` (the `+ ("\n\n" + transcript)` tail) and `:1738` (the over-cap `return ""`; `:1737` is the `_SOURCE_TRANSCRIPT_CAP` guard that reaches it) are exact there — and point at unrelated code at the default pin, where the same function is `:1248`–`:1261`, its `render_s2_prompt` call is `:1275` and the tail is `:1294`. **A fifth pin, `25b84d6599`, covers:** the §3 subsection *"What a source type may NOT vary — the extraction PROMPT"*, **and §7's `STATE_VALUE_CARVE_OUT` / `VALUE_FIDELITY_RULE` / `_s2s4_rules()` / `_granularity_text()` citations** — every one of those is read at that revision, where the file is 17,678 lines (6,255 at the default pin). ⚠️ **The numbers in those passages are all inside 6,255, so none is out of range at `c79ba1cf2` — the pin is needed because each one points at *unrelated code* there** (`:1228` is `"{chains_text}\n"`, `:1354` a docstring line, `:2185` `"S1 STORY",`; and §7's `:326` is `_build_master_from_brief` (its `:325` is blank), `:455` is blank, `:556` a docstring line, `:1444` `esc = _extractor_escalation_tokens(...)`). That is the opposite failure from the `653980445` rows above, where the cited files are shorter than their cited lines; do not apply the out-of-range test to this pin. The symbols are the authoritative form. **`tests/test_extractor_v4_citation_rot.py`** resolves the document's quoted-symbol citations against the tree and pins them, so a re-rotation reds a test instead of being discovered.
**Related:** `docs/architecture/STORAGE-ARCHITECTURE.md` · `#1026` (pack slots) · `#2281` (ask/answer over distilled knowledge) · `#2730` (entity attachment) · `#4333` (volume/economics) · `#4240` (edge durability) · `#4911` (secret redaction) · `#4899` (the gate).

---

## ⛔ USAGE IS DENOMINATED IN TOKENS, AND OVERAGE IS PURCHASED — decision, owner, 2026-09-26 (`#4495`)

**Read this before the rest of the document.** This document is about the *pipeline*; it is silent on **what
extraction costs the customer**, and that silence is now filled by a ruling:

- **Extraction usage is counted in TOKENS**, and a tier carries a **starter number of tokens**. *"similar for usage,
  we give them a number of 'tokens' but if they use too much, they have to purchase extraction overage."*
- **Extraction overage is PURCHASED**, with **prepaid credits** (the owner's reason is cashflow, in preference to
  approving a postpaid maximum spend).
- The subscription is priced on **features**; storage is billed separately in **MB/GB** — see the decision block in
  `STORAGE-ARCHITECTURE.md`.
- **Prices are NOT set here.** The owner *"calibrat[es] after the beta launch"*, from measured consumption.

**What this means for this document's own design work.** The tokens are the **extraction** lane's workload — the LLM
work this architecture specifies — so any change that alters extraction **volume** (batch size, the S1→S2 step count,
the VET pass, per-item vs per-batch judgment) now moves a **customer-visible meter**, not merely a cost. That is a
reason to prefer measurable steps over inferred ones: the checkability rule is now also a billing
surface.

**✅ Where the measurement is.** The
capture lane records token counters: the per-org ledger carries `capture_tokens_in` /
`capture_tokens_out` (added by `supabase/migrations/20261001000001_metering_capture_tokens.sql`) beside
`capture_calls` / `capture_cost_usd`, written by `metering.record_capture_usage` from the **same** counts the
analytics row carries — `#5045`. The **ask** lane separately records
`ask_tokens_in/out`. The **spend ceiling stays blind to the token counters by construction** —
`metering_cohort_spend` and `get_cohort_spend_usd` read **only** `ask_cost_usd` + `capture_cost_usd`, so adding
token counters cannot change the cap's behaviour. That separation is deliberate: this ruling is about the **unit**,
and the cap is a separate decision.

**The token counters' reader is an ENFORCEMENT reader** (`#4051` §1b is superseded — see the
changelog). `metering.get_capture_usage(org_id)` returns `capture_calls` / `capture_tokens_in` /
`capture_tokens_out` / `capture_tokens_total` / `capture_cost_usd` per org per metering window on BOTH
substrates (`:MeteringRecord` in the registry, or `supabase.metering_records`), degrading to a zero view and
never raising; `capture_extraction`'s declared `READER` binding in `tortoise/cost_dimensions.py` points at it.
It carries the presence fact a zero VIEW cannot express — `measured` + `unmeasured_reason` (`absent` /
`read_error` / `period_error`) — because a recorded ZERO is a reading (`#5045`) while an unreadable ledger
is not. A reader that returns a zero view for every failure cannot tell "unknown" from "nothing spent", and
a cap enforced on it fails OPEN for exactly the tenants whose meter is broken.

The reader has exactly ONE consumer: `allowance_gate.enforce_allowances`, reached from
`quota.enforce_org_limit` on both REST and MCP, so the `capture_extraction` allowance is enforced on the
capture request path (`tests/test_metering_capture_usage_reader.py`'s scan names that consumer exactly, and
reds if it is dropped). The spend ceiling above still cannot see the **token counters**
(`capture_tokens_in` / `capture_tokens_out` / `ask_tokens_in` / `ask_tokens_out`) by construction — it sums
`ask_cost_usd` + `capture_cost_usd`, so a change in capture *tokens* moves no cap while a change in capture
*cost* does. The metric set itself — the per-call metrics, the disclosure counters, the attribution and the
derived figures — is defined in `docs/ops/extraction-cost-metrics.md` (`#3840`); the consumption model that
consumes the reader is `docs/architecture/CONSUMPTION-MODEL.md` (`#4051`).

**⚠️ Open, and not decided here:** whether the token allowance and the storage allowance are enforced at the same
seam, and whether an extraction allowance is a **pre-spend admission** gate (like the points estimate) or a
**post-hoc** meter with overage. Those are pricing/enforcement questions — owner territory, post-beta calibration.
What the design fixes: the extraction dimension is checked at the **same admission seam** the node count uses
(`quota.enforce_org_limit`, ahead of it), reading the per-window token FLOW. The gate is **inert until an
allowance is configured** — it refuses nothing **on the allowance ground**, because no resolver emits an allowance
value. ⚠️ That is NOT the same as refusing nothing: every **gated** request (`points` / `documents` / `sessions`)
fills the missing allowance keys with one control-plane read, and a **fault** in that read is a `QuotaCheckError` →
HTTP 500 **even with no allowance configured** — see §6's "INERT IS ABOUT WHAT THE GATE REFUSES, NOT ABOUT WHAT IT
READS" in `docs/architecture/CONSUMPTION-MODEL.md`, and `tortoise/allowance_gate.py`'s module docstring. The
remaining question — whether that admission is charged against prepaid credits *at capture time* or reconciled per
window — is not answered by an admission check and stays with the owner.

> **OVERRIDES:** the **postpaid metered/auto-billing** default the field uses, and **`write_ops`** (the current
> metered-usage unit) as the unit for extraction consumption — replaced by **prepaid credits** and per-token
> extraction with purchased overage, because per-write-op accounting cannot express LLM work at all (extraction
> consumes provider tokens, not graph writes), and because the owner's cashflow reason favours **buying credits in
> advance** over approving a maximum spend. (Billing LLM work per token is itself the field default, so the marker
> names the unit we leave *and* the payment shape we depart from.)

---

## 1. Why v4 exists

### The measured problem

Production graph: **~25,000 quota nodes in 3–4 active days** (~27 per emitting session). Composition: **14,851 statements (59.5%)**, **7,863 Objects (31.5%)**, 2,233 operators (8.9%). **⚠️ Node counts move continuously — the Object figure is a snapshot and does not match every other figure in these documents; each one is date-stamped at its source.**

**The mechanism, found by audit (50 real instances, `#4894` comment 2026-09-23):** the extractor mints **an entity for the grammatical subject of each statement.**

| Object it created | the statement it came from |
|---|---|
| `the timeout command` | "timeout is not on macOS" |
| `the staging-only constraint` | "…staged only, no commit/push" |
| `the lane ownership rule` | "Lane ownership: #4668/#4648 belong to…" |
| `PR #465` · `issue #3775` · `§24` | pointers |
| `tests/test_frontmatter_validator.py` · `_TOOL_BY_NAME` · `gh api -X PATCH` | references |

**62.3% of Objects are `the <X>` (definite descriptions); 37.7% are referenced by exactly one point; zero duplicate names.** So the problem is **not** duplication — it is **writing things that were never entities.**

### v2 → v3 → v4

| | the question it answers | effect on volume |
|---|---|---|
| **v2** | did we extract it? | baseline |
| **v3** | did we extract it **correctly**? (dates, verbatim state values, atomic points, supersession) | ⬆️ **increases it** — E2 preserves more, E3 splits into more |
| **v4** (this document) | should we have extracted it **at all**? | ⬇️ **reduces it** |

**v3 has no discard, trim or gate anywhere.** It is a fidelity workstream. **v4 is a selection workstream.** ⚠️ **v3 is not absent from the tree: E2's carve-out (`STATE_VALUE_CARVE_OUT`, `#1534`) and E4 (`merge_embed_lists`, `:4845`) are load-bearing, and this document depends on them** (see §1, "v2 → v3 → v4").

⚠️ **v3's E4 rule is load-bearing:** `"S4 merges, not replaces"` — `merge_embed_lists(embed_list, s4)` runs at `extractor_v2.py:4845` and S2 is preserved.

---

## 2. Three tiers — and where each one lives

> **Numbering note:** the three tiers are described in the prose below, not as numbered
> headings, so this section's first numbered subsection is **§2.4** — the deliberate
> "fourth layer". The gap is intentional; §2.1–§2.3 have no headings.
>
> **⚠️ Plus a fourth, proposed 2026-09-23 — the verbatim raw fact. See §2.4.**

**The architecture has three tiers with different cost/quality trade-offs.**

| tier | what it is | **where it lives** | cost | retrievable? | lossy? |
|---|---|---|---|---|---|
| **Raw** | the verbatim turns / source material | **Postgres — NOT the graph** | expensive (13.8 MB of our 140 MB) | only verbatim, via its `Source` | not at all |
| **Narrative** | the S1 summary — *"what changed"* | **Postgres — NOT the graph** | cheap (a few KB per session) | **yes, full-text** | yes |
| **Entities** | **everything the graph holds** — `Source`, `Object`, `Subject`, `Event`, `Point` (+ operators) | **the graph** (record layer for `Source`/`Event`; derived layer for `Object`/`Subject`/`Point`) | the thing we must reduce | yes — and the claims are **arguable** | claims: intentionally. `Source`/`Event`: they are the truth |

⚠️ **The third tier is `Entities`, not "Claims" (owner, 2026-09-23).** The graph also holds `Source`, `Object`, `Subject` and `Event` — and **only some of those grow without bound.** The tiers are **Raw / Narrative / Entities**, and the column that matters is **where each physically lives**, because that is where the cost is.

### ⚠️ Storage rule for the first two tiers — they are OUTSIDE the graph (owner, 2026-09-23)

**Neither raw nor narrative belongs in the graph. Both live in Postgres.** ⚠️ **The owner's wording is "store in supabase" — and "supabase" there means the DATABASE, not the object bucket.** The overload *"has already caused a real misreading"* (`STORAGE-ARCHITECTURE.md` §9.1) — do not read either document's "supabase" as the object bucket.
> *"the narrative is not something we're suggesting to store in the graph (same as raw) but store in supabase."*

**Why this is not a detail:**

- **A narrative in the graph is a narrative in RAM.** The graph is the RAM-resident layer (`STORAGE-ARCHITECTURE.md` §2), so putting prose there pays memory prices forever for text that is only ever **read**, never **argued about**. **The narrative is a searchable string, not a belief** — it has no confidence, is not `NAND`-able, and takes part in no operator.
- **The graph keeps a reference, not the text.** The `Source` entity stays in the graph — it is the provenance anchor every claim points at — while the **heavy text** (turns and narrative) stays in Postgres — the owner's *"supabase"*, meaning the DATABASE, not the object bucket — reached through it.
- **It is far cheaper than the graph** — the storage research measured **~580× on disk-resident data** (⚠️ **580× is the disk-resident leg, not the vector index — do not quote a *"~1,000×"* figure, which has no source).** ⚠️ **And *"carries most of the retrieval value"* is NOT measured** — §10 of this document itself says *"No head-to-head measurement exists."* **The supported claim: the narrative is the middle ground that makes raw turns droppable.**
- **It is the middle ground** that makes raw turns droppable without losing *"what happened in this session?"*
- **It is connected by construction** (§4, S1) — so it links to Objects/Events/Points rather than being an island.
- It directly serves `#2281`: *"ask/answer surface reads ingest-time-distilled knowledge … with raw-turn fallback."* **The narrative IS that distilled layer.**

**⇒ D1:** the narrative is **not a graph node and not graph metadata** — it is **Postgres-stored text referenced by its `Source`** (the owner's *"supabase"* is the DATABASE, not the object bucket). It must never be a `Point` (it is not a claim) and never a `Document` node (it is not a discovered document). §9 carries D1.

### 2.4 A FOURTH layer — the verbatim raw fact (owner, 2026-09-23)
>
> *"we should perhaps add a 4th layer which is raw facts and also needs extracting … sometimes raw facts were being deformed and that's why memory systems sometimes keep them separate … it wouldn't be in the graph but just raw storage and maybe associated with the graph entity."*

**The problem it solves:** extraction **rephrases**. A `Point` is the model's rendering of what was said, not what was said. During the LongMemEval work, **raw facts were being deformed** — and that deformation is silently baked into the belief layer, where nothing downstream can detect it.

**The layer:** the **minimal verbatim span** each `Point` was derived from, kept **verbatim in Postgres (the owner's *"supabase"* = the DATABASE, not the object bucket)**, not in the graph, and **linked to the `Point`/`Object` it supports**. Distinct from the other three by granularity and purpose: <!-- v4-mechanism: span_link state: not-implemented -->

| layer | what it is | granularity | purpose |
|---|---|---|---|
| **Raw** | every turn of the session | whole session | conversation replay |
| **Narrative** | the S1 summary | whole session | *"what happened?"* — searchable, cheap |
| **Entities** | `Source`/`Object`/`Subject`/`Event`/`Point` | graph | belief, argument, reasoning |
| **⭐ Raw fact** *(proposed)* | **the exact span a Point came from** | **one claim** | **fidelity — the truth the Point must not drift from** |

#### ⭐ The experiment already exists — `#3011`, pre-registered and frozen 2026-09-11

**This is not a new idea here, and it already has external evidence behind it.** `docs/experiments/2026-09-11-abc-context-assembly-experiment.md` (frozen; metrics and falsification criteria may not change after the first run) tests exactly this as **arm C**:
> **Arm C — union:** Arm B's subgraph **plus the verbatim source turns each subgraph claim derives from, linked in place** (`C1 ⟵ session 12, turn 4`)

**And the external ablation it cites is strong:**
> **Verbatim beats derived** — arXiv 2601.00821v3 (controlled ablation): verbatim source beats extracted artefacts by **15.9 pts** (LoCoMo) / **22.0 pts** (LongMemEval-S); **union matches chunks; artefacts alone forfeit the gap**.

**⚠️ ⛔ The thresholds are as the frozen document has them:** `−5` belongs to **H4** as well as H3, and `15` belongs to H1 **and** to H5's falsification criteria.

**⭐ Verified against the frozen file (`docs/experiments/2026-09-11-abc-context-assembly-experiment.md`, §2 "Hypotheses (frozen)", unchanged since 2026-09-11):**

| # | hypothesis | frozen prediction |
|---|---|---|
| **H1** | Union beats subgraph-alone | **`C > B, ≥15 pts`** ✅ |
| **H2** | Verbatim beats subgraph-alone | **`A > B, ≥10 pts`** ✅ |
| **H3** | Union matches or beats verbatim alone | **`C ≥ A − 5 pts`** ✅ |
| **H4** | Subgraph-alone is sufficient *(falsifier)* | `B ≥ A − 5 pts` |
| **H5** | The renderer/selection path is the binding constraint *(falsifier)* | `B < A − 15 pts` **and** answer-bearing-claim presence ≥ 80% |

**All three values hold:** `−5` appears in **both** H3 and H4, and `15` appears in H1 **and** H5's falsifier — **"also present elsewhere" is not "not present here"**.

**⚠️ Still read them from the frozen doc before using them:** `docs/experiments/2026-09-11-abc-context-assembly-experiment.md`. **The falsifier's ROLE is the design claim: a result where verbatim alone beats the union is a statement about which arm to SERVE AT RETRIEVAL, not a verdict on the belief layer.**

#### ⛔ What the epistemic subgraph is actually FOR (owner, 2026-09-23) — and it is NOT retrieval
>
> *"the epistemic subgraph is also key for agents who want to change something to understand why it is the way it is, also for calculating what's valuable via EP (so making decisions) and audits too. so the facts are a very different objective than epistemic graph."*

**This matters because the `#3011` framing invites exactly the wrong reading.** If verbatim beats the subgraph **as retrieval context**, that says nothing about the subgraph's purpose — it says the subgraph was being tested as the wrong thing. **Its purposes are three, and none of them is *"be the reader's context"*:**

| what the epistemic layer is FOR | why raw facts cannot do it |
|---|---|
| **Understanding *why* something is the way it is** — so an agent can **change** it safely | raw text says what was said, never what **supports** or **attacks** it |
| **EP-based valuation → decisions** | a confidence is **computed over the operator graph**. Raw text has no confidence, and nothing to propagate over |
| **Audit** | an audit asks *"what did we believe, on what grounds, and what changed it"* — a chain of support and attack, not a transcript |

**⇒ The raw-fact layer and the epistemic layer are DIFFERENT OBJECTIVES, not competing options.** They answer different questions. **Serving verbatim at retrieval while keeping the epistemic layer for decisions, change-explanation and audit are not in tension — that IS the union arm's point.**

**⚠️ Consequence for this design, and it is a constraint:** the fourth layer must **not** be built as a cheaper *substitute* for `Point`s, and `Point` extraction must **not** be weakened on the grounds that the raw span is retained. **The span is the EVIDENCE; the `Point` + its operators are the ARGUMENT.** Both are kept, for different consumers.

⚠️ **The EP layer is evidenced externally and unmeasured on our stack.**

#### The mechanism is also already filed — `#2684`

**`#2684`** *"verbatim value-spans on value-bearing points (extractor value fidelity)"* already specifies the schema half: *"value-bearing claims (numbers, dates, prices, counts, quantities) MUST carry their exact value + the verbatim source span (source turn id + span)"*, surfaced by the assembly so a consumer can *"enumerate-then-sum/count"*. **Our 4th layer generalises that from value-bearing points to all points.**

**Related, and not to be duplicated:** `#2405` (paraphrase-tolerant survival leg — the deformation measurement) · `#2542` family (value-fidelity discipline, cited by `#2684`) · `#2104` (**REPHRASE** dedup) · `#2684` (span schema) · `#3011` (the A/B/C experiment) · `#2281` (**raw-turn fallback** — the same instinct from the read side) · `#2453` (operational values verbatim — §7).

**⚠️ COORDINATION NOT TO MISS: §2.4 WIDENS `#2684`'s scope** — from *value-bearing* `Point`s to *all* `Point`s. It is a deliberate widening, but **`#2684` is the other issue's scope and the change must be recorded there, not only here.**

**⚠️ Storage consequence, harmonised with `STORAGE-ARCHITECTURE.md` §9.1:** the raw fact is **Postgres text, not a graph node** (the owner's *"supabase"* = the DATABASE, not the object bucket), joined to its `Point` by a reference — exactly the pattern §9.1 uses for the narrative. **If the belief layer carries the span, it stops being cheap; if it carries a reference, the fidelity is free.** That is the design rule this layer depends on.

#### 2.4.1 ⭐ Why the LINK is the load-bearing half — and the pattern has a name (2026-09-24)

**Owner, 2026-09-24:** *"do we vectorise the source or link to it?"* **Both — but the order is the decision, and the link comes first.** <!-- v4-mechanism: span_link state: not-implemented -->

The mechanism is **parent-document retrieval** / **small-to-big**: *embed the small units; keep the parent by ID with no vector; fetch it by walking up.* The field converges on **link, not embed** — a parent is stored by ID **without** embedding, because the reference is what the reader needs, not a second copy of the thing. *(Databricks: "search children, return parents".)*

**⇒ This is the fourth layer's retrieval consequence, and it is measured:**
> **At answer time, the model gets the source's VERBATIM text, not the extracted claim.** Verbatim source beats LLM-extracted artefacts by **15.9 / 22.0 pts** (§2.4, arXiv 2601.00821) — **independent of whether anything is embedded.**

**Two rules this puts on the extractor:**

| # | rule | why |
|---|---|---|
| **1** | **Every committed `Point`/`Object` must carry a reachable link to its `Source` span.** A node that cannot walk up cannot be served as evidence | **the link is how the evidence reaches the model** — without it, the 15.9-pt finding is unreachable and all we can serve is the derived claim |
| **2** | **A `Source` MAY additionally carry one summary vector** (the S1 narrative, kept in Postgres) | it buys one query class nothing else serves — *"which SOURCE is about X"*. Framework practice, **not** a measurement. Storage §9.4 |

> **⛔ DRIFT MARKER — rule 1 above is the TARGET, not the shipped code.** The
> write path stamps `span_start`/`span_end` — the offsets
> `extractor_v2._value_verbatim_span` returns, appended to the point entry only
> when they are not `None` — only when **four** gates hold: the point carries a
> value, a source turn resolves, the quote is VERBATIM in it, **and** the
> addressed text itself carries a value. `source_turn_id` is **not** one of
> those gates and must not be read as one: the model path writes the key
> unconditionally (its value is `None` when no turn resolves), and the S5 mint
> path writes it only when a turn resolves — so "the link is stamped everywhere"
> is exactly the false-present reading this marker exists to prevent.
> Every other point **the extraction write path commits** is written with **no
> span** — a point resolved by the token-overlap band carries a `source_turn_id`
> and still no offsets, which is why the two are stated separately above and not
> as one "address". The coverage is a **pinned census**, and the number is quoted
> from that artifact because a reader can re-run it: on the sealed corpus
> (`tests/eval/write_path/test_value_span_census_2684.py`, 2026-10-25) it
> **re-derives** `CENSUS["spanned"] = 101` of the **112 value-bearing** points,
> while the **observed** count is **0 of the 217** committed points carrying a
> span key at all — the census's own `assert spanned == 0`, and runbook §6's
> statement that an observed rate *"needs an end-to-end re-ingest"* (**#8486**).
> A re-derivation of what the predicate *would* stamp is not a rate of what the
> corpus *has*, and the two must not be quoted as one number. ⚠️ When you re-run
> that file, its **separate** citation-resolution assertion — the one that
> resolves the census artifacts' `retrieval.py` span citation — is red on `main`
> at this head (a pre-existing drift in a different assertion, unrelated to
> `CENSUS`); the `CENSUS` pins themselves pass.
> ⚠️ "No span" is a property of the **extraction** path, not of every writer in
> the process: three public doors pass caller-supplied offsets through their
> generic props and validate them with `commit_schema.validate_span` —
> `TortoiseSDK.create_point`, `TortoiseSDK.update_point`, and
> `TortoiseSDK.update_entity` (the generic tenant / MCP surface, `_update_entity`),
> whose own comment names the half-span hazard. So an SDK or MCP caller can
> commit a span the four gates never saw. (Symbols, not line numbers, on purpose:
> this document's citation pin is the default `c79ba1cf2`, at which none of those
> offsets resolve — an unpinned HEAD number is no citation at all, per this
> document's own header.) So *"every
> committed `Point`/`Object` must carry a reachable link"* is unbuilt for the
> general point, and `config/v4-mechanisms.yml` records `span_link` as
> `not-implemented` with the widening owned by **#2684** (§2.4's own
> coordination note). A lane reading rule 1 as shipped will serve a claim that
> cannot walk up and call it evidence — that is the exact reading this marker
> exists to prevent.

**And what is explicitly NOT the mechanism:** *"are these two sources the same?"* is **never** answered by a vector — similarity detects the same **topic**, not the same **document**. Identity is **canonicalised URL + content hash** (storage §9.4 ③).

#### 2.4.2 Lifecycle and superseding in the raw-fact layer

**Owner, 2026-09-23:** *"i was talking about the verbatim raw facts layer and how we'd handle lifecycle/superseding there."*

**The rule: the raw-fact layer has NO lifecycle and NO status. It is APPEND-ONLY and immutable.**

**Why — and it is not a preference:**

1. **A raw fact is *evidence*. Evidence does not expire.** *"On 2026-09-19 p99 was 480 ms"* remains a **true record of what was said** after a later fact says 210 ms. The later fact supersedes a **belief**, never the **record that the belief was once held**.
2. **Superseding a raw fact would destroy the audit trail the layer exists to provide.** §2.4's whole purpose is *fidelity* — keeping the exact thing the `Point` must not drift from. A layer that can be rewritten is not a fidelity layer.
3. **Hindsight reached the same conclusion independently** — and stated it better than we had: *"A retain path that dropped a fact because it resembled one already stored would be a retain path you could not trust to have kept what you sent it."*

**⇒ So where does liveness live? On the `Point`, never on the span.**

```text
Point (graph — carries live / superseded / draft, EP confidence, operators)
   │  span reference  (point_id → source_turn_id + char span)
   ▼
raw fact (Postgres — immutable text. Carries NO status. Ever.)
```

**Three consequences that follow, and each is a rule:**

| # | rule | why |
|---|---|---|
| **1** | **Status is per-`Point`, not per-span.** One span may be cited by several `Point`s; they are superseded independently | the span is shared *evidence*; liveness attaches to the *claim*, not the quote |
| **2** | **Read direction is `Point → span`, never `span → is it live?`** | asking raw storage whether a fact is live has **no answer** — liveness is not derivable from raw text. It exists only in the graph |
| **3** | **Nothing is ever deleted on supersession.** The only deletion trigger is **GDPR / retention** | deleting superseded evidence is how the audit gets a hole in it |

**⚠️ Putting status on the raw fact would also break the storage invariant.** The layer is the **truth** (`derived = fold(truth)`, §2). *"This evidence is no longer current"* is a **judgment**, and judgments are **derived** — they belong to the `Point`, which is derived. A status flag on the truth layer would be a **judgment stored as truth**, and it would give the same lifecycle **two sources of truth**.

**✅ Adoptable, and it applies to the GRAPH side (Hindsight):** **invalidation by RELOCATION rather than a flag.** They move superseded rows to a separate archive *"so recall needs no state predicate… no query pays for your cleanup."* Snapshotted edges, reversible. **Our current `Object.status='superseded'` fold is the flag form** — every read carries `status != 'superseded'` as a predicate. **Relocation is the alternative worth testing when recall cost is measured**; it is not required now, and **our fold keeps the event stream as the reconstruction source**, which relocation must preserve.

**⚠️ Not to be confused with §5a.** §5a is about **graph** lifecycle (Objects, `CORRECTS`) and the refusal of recency-wins. **This subsection is about the raw-fact layer — where the answer is simpler: there is no lifecycle, by design.**

#### 2.4.3 Who carries the span? `Point`s only — measured, not assumed

**Owner, 2026-09-23:** *"are only points connected to raw facts or also objects/subjects/etc? not sure so asking."*

**Measured on the live graph (2026-09-23), by walking every edge in and out of each type:**

| type | how it reaches raw | mechanism | measured |
|---|---|---|---|
| **`Point`** | **directly** | `(Point)-[:extractedFrom]->(Source)` | **14,567 — the only edge type with instances that lands on a `Source`; `aboutSource` (`Point\|Event`→`Source`) is registered in `session_link.ENTITY_LINKED_TRIPLES` but has no instances** |
| **`Object`** | **only through a `Point`** | `(Point)-[:aboutObject]->(Object)` | 27,310 inbound |
| **`Event`** | through a `Source` | `(Source)-[:references]->(Event)` | 2,190 |
| **`Subject`** | through an `Event` | `(Subject)-[:performs/participatesIn]->(Event)` → `Source` | 16 |

**Two facts worth keeping separately:**

- **`extractedFrom` is a `Point`→`Source` edge** (`projection/entities.py:655`, `:1864`) — **not** an `Object`→`Source` edge.
- **ZERO edges originate from an `Object`** — 27,310 in, **0** out. Objects are **pure sinks**, reached only from `Point`s.

**Why the span still rides `Point` even though other edges can reach a `Source`:** the carrier is
the node that makes a *deformable claim* — a `Point` is the propositional unit whose truth is
re-ranked by EP, so it is the thing whose support has to be re-fetchable at answer time. `Object`
and `Subject` are referents, and `aboutSource` — registered for `Point` and `Event`
sources alike (`session_link.ENTITY_LINKED_TRIPLES`, mirrored in `projection/entities.py`) — has no
instances in the live graph, so no non-`Point` node reaches a `Source` today.
*(The carrier choice rests on the argument above, not on the edge census: "the only edge on a `Source`"
does not entail "`Point` is the only possible carrier" — that inference is an edge-type over-claim.)*

**⇒ Today, `Point`s are the sole raw-linked type. Everything else reaches raw transitively.**

**✅ And that is the right design — it is not an accident to be tidied. The reason:**
> **Span-level provenance exists to catch DEFORMATION, and only a claim can be deformed — in the sense that matters.**

A `Point` is a **paraphrase** of the text, and that is exactly where a **propositional** fidelity break happens — an inverted condition, a dropped qualifier, a number attached to the wrong subject. **The span is the check on the claim.** An `Object`'s failure is **referential, not propositional**: it either picks out the right thing or it does not, and the check for that is **whether it refers at all**, not whether it was rendered faithfully.

**⚠️ DO NOT WRITE "an Object is a name, so it cannot be deformed" — that claim is FALSE, and THIS DOCUMENT'S OWN §1 EVIDENCE FALSIFIES IT.** 62.3% of Objects are **synthesized definite descriptions** minted from a noun phrase (*"the timeout command"*) that need not appear verbatim anywhere, and the audit found an Object extracted from a sentence that was **negating its existence** (*"timeout is not on macOS"*). **A synthesized description can be wrong about what it names — it is a REFERENCE, and references fail.**

**⇒ The correct statement is narrower and survives: an Object's failure mode is NON-REFERENCE, and span-provenance is not the tool for it — a reference test is. The span catches what the claim CLAIMED; the reference test catches whether the name picks anything out.**

**⚠️ It would also be actively harmful, not merely redundant.** The Object side is already reachable (27,310 edges). Adding a direct `Object`→span link would create **a second route to the same evidence** *and* multiply it at every hub — the worst current hub has **123 referring `Point`s**, so one hub would gain 123 links carrying nothing that walking to the `Point` did not already give. **Same fan-out trap as §11 of `STORAGE-ARCHITECTURE.md`.**

**✅ Confirmed by the filed design, independently:** `#2684` scopes itself to **"value-bearing *claims* (numbers, dates, prices, counts, quantities)"** — i.e. **`Point`s**. **The span carrier was never intended to be an entity.** Our fourth layer widens `#2684` from value-bearing `Point`s to all `Point`s — **it does not widen it to another node type.**

**⚠️ Two real gaps this measurement exposed (both pre-existing, neither caused by this design):**

1. **`aboutSubject` = 0 edges**, and only **1** `Subject` exists. **The subject layer is effectively unwired** — Subjects reach the graph only via `performs`/`participatesIn` to Events (8 each).
2. **2,608 of 4,798 `Event`s (54%) have no `Source`.** ⚠️ **Do not read this as a defect without checking D2** — D2 rules *"do NOT mint a `Source` for events"*, so a self-anchoring lifecycle Event with no `Source` is **the intended shape**. Flagged as *worth confirming*, not as a bug.

#### ⭐ ✅ THE ENTITY-PROVENANCE DECISION

**Researched 2026-09-23; findings in §16.** Seven comparables were examined: **entity-level provenance is the FIELD NORM — 5 of 7 have it** (GraphRAG `Entity.text_unit_ids` · LightRAG `source_id` + `file_path` · Graphiti/Zep `MENTIONS` edges → episodes · Mem0 entity↔memories · ArcGIS property-value provenance). **The convergent pattern is a POINTER, not a payload, and the granularity is the chunk or episode — never character spans on entities.**

**⚠️ And the field falsifies the "names cannot be deformed" intuition a SECOND time** — entity linking's own pipeline is **mention → candidate → disambiguation**, and **the mention IS a span**. W3C PROV standardises it as `prov:mentionOf`. **So the intuition is not merely wrong in our graph; it is wrong as a general claim about entities.**

✅ **RECOMMENDED: keep `Point`-only as the SPAN carrier, and ADD entity ORIGINS ONLY — at most ONE reference per entity.** Reasons: it is the field's granularity (chunk/episode); it is ~**0.7–0.9 MB** at today's scale; and **it is what makes a non-referential entity DETECTABLE.** ⛔ **NOT per-mention links** — that is the 123× fan-out trap, and it is what §11 of the storage doc caps (owner: **200**).

**⭐ And the reason to add it is NOT duplication — our objects have ZERO duplicate names. It is NON-REFERENCE:** an entity minted from a negated sentence (*"timeout is not on macOS"*) has **no origin span to point at**, so an origins link is **the detector** for exactly the failure §1's audit found. **Precision matters here: this document must not claim provenance is for dedup.**

⚠️ **No backfill is possible** — origins are unrecoverable for existing entities (**zero** outbound edges, measured above). **New entities only; the existing 7,863 stay origin-less.**

#### 2.4.4 ⛔ Redaction and the append-only layer — the G4 reconciliation (`#8496`)

**G4 (`§4.3`) is the one gap in this document that is not a missing step: two rulings meet here and neither
can see the other's constraint.** `#4911` (redaction) and `§2.4.2` (append-only, ruled **2026-09-23**) are
both correct; §2.4.2 makes the raw-fact layer immutable *by design* — a raw fact that can be rewritten is
not evidence — and a control that removes a credential is, read naively, a rewrite. **The reconciliation is
stated here, as four decisions.**

| # | the item `#8496` requires stated | **the decision** | **the reason** |
|---|---|---|---|
| **R1** | **Where redaction sits relative to immutability** | ⭐ **Redact BEFORE the append.** The layer only ever holds already-redacted text; nothing on it is ever rewritten to remove a secret | the alternative — keep the raw, record a redaction separately — **keeps the secret** while recording that one was there, which is the exact failure G4 names (*immortal secrets*). Redaction-before-append is the only option under which the layer's immutability is **preserved rather than weakened**: a value that was never written needs no later edit |
| **R2** | **The already-persisted case** | a **retention erasure** — erase the value **in place**, substituting the **same** `[REDACTED:<kind>]` marker the write path emits; journal the erasure (span id + kind + when, **never the value**); the citing `Point`s keep their status | §2.4.2 rule 3 already names exactly **one** in-place class: **GDPR / retention**. A discovered credential is the strongest retention case — the data may not be retained. *Supersession* is the wrong verb (it preserves the old record, and the old record is the secret). The **same marker** as R1 means a reader cannot tell a late redaction from an early one, so the layer has one representation of a redacted span and no second-class marker exists |
| **R3** | **Scan coverage** | every *"the store is clean"* claim states coverage as **(scanned, total)**; a partial scan means *none found in the part examined*, **never** *clean*; full coverage means reading every page to the host's `hasMore=false` with `include_retracted=True` | the caveat is **a rule for reading a scan, not an open finding** — see below |
| **R4** | **The residency question** | the layer lives in the **Postgres truth store** (the DATABASE — not the graph, not the object bucket); it carries **no credential in the steady state** because R1 redacts before the append; its retention is the **same user-data retention and erasure path** the other user data uses — not a new residency set | §2.4 already fixes the home. The widening G4 warns about is in **volume, not sensitivity** — and R1 is what makes the widening safe: a larger raw store that never receives a credential is not a larger *secret* store |

**R1 and R2 are one rule at two times, and that is the whole reconciliation.** At *write* time a
credential never reaches the layer (R1); at *discovery* time, if one is already there, it is erased under
the retention trigger (R2). Both substitute the **identical marker**, so the layer never needs an answer
to *"was this caught on the way in, or later?"* — a consumer cannot accidentally treat the two
differently, and neither can a future lane.

**⛔ A *superseding redaction record* is REFUSED — and it is the option `#8496` names first.** It retains
the secret in order to preserve the evidence chain. But the evidence chain is kept *for fidelity*, and
there is no fidelity value in retaining a credential: the chain is preserved without the bytes by R2's
journal record and the marker. Keeping the bytes is the one outcome the control exists to prevent.

**⛔ *Accepted residual risk* is REFUSED as the default answer, and this is the point of R2.** "The layer
is immutable" is not a reason to retain a credential; the two *accepted* residuals are narrower and both
are named: (1) the **pre-redaction window** — a turn captured before the scrub existed, which is what R2
repairs; and (2) **rows a scan has not yet reached**, which R3 exists to keep honest. **A purge (delete
the row) is the FALLBACK for R2**, used only where a deployment's storage tier genuinely refuses an
in-place write; the span reference then resolves to a stated **purged** marker, so a reader still sees
evidence was removed rather than a dangling link or *"never existed"*.

**✅ R1–R4 request no new mechanism, and it is worth saying why.** The control is `#4911`'s, at
`_capture_turn_texts` / `_redact_turn_contents` — **the ONE definition of the stored turn text that the
writer itself calls** (`tortoise/sdk.py`) — *before* the write, with a **visible**
`[REDACTED:<kind>]` marker and a per-session `capture_redactions` count. The pattern table is
`redact_secrets` / `CREDENTIAL_KINDS` in `tortoise/security.py`, and it is **idempotent by construction**
(no rule's anchor can be satisfied inside the marker), so re-scanning already-redacted text neither
changes it nor inflates the count. **The v4 raw-fact writer routes through that same chokepoint; R2 is the
only redaction that can ever happen *after* a write, and it reuses the same marker rather than minting a
second one.**

**What R1 is NOT: a licence to weaken §2.4's fidelity goal.** Redaction is the **one sanctioned fidelity
loss on this layer** — and, like the 5,000-char cut (`#4897`), it is **visible**: the marker is
`[REDACTED:<kind>]`, never `***` and never a silent truncation, so a consumer can tell the text is not
complete. **Fidelity yields to non-retention of a credential; it never yields silently.**

**⚠️ R3 and the partial-scan caveat.** `#4911`'s *body* reported a scan of **10,000 of 19,960** turn nodes
(FalkorDB's default result cap). `#4911`'s **posted re-scan (2026-09-25)** read **21,281 of 21,281** turn
nodes to the host's `hasMore=false`, `include_retracted=True`, and found **0** anchored matches (the
*"170 nodes contain `sk-`"* figure is prose — `risk-`/`disk-`/`task-`). **So the coverage question is not
*"was the store scanned?"* — it was — but *"does every future clean claim carry its denominator?"*, which
is what R3 pins.** Two rules follow: the scan **must page to `hasMore=false`** (a default-capped read is a
partial scan wearing a clean verdict), and it **must run the same table as the write path**
(`redact_secrets` / `CREDENTIAL_KINDS`) — a parallel pattern list is a second source of truth for what a
credential *is*, and the two would drift apart silently.

**⚠️ Open: R1–R4 bind a design — the raw-fact persistence step is not written, so the control has to be
built WITH the layer.** The step is open (`§4.3` G1 — `#5064`); the span *schema* exists (`#5007`), so a
`Point` can carry `span_start`/`span_end`, but the raw-fact layer itself is not written. **Building the
layer and the control together is the design requirement — a control designed separately, later, cannot
bind the write path it was meant to govern.**

**OVERRIDES: not applicable.** Redacting at ingest is the common practice (a control at the write
boundary), so R1 runs *with* the grain; R2's in-place erasure is authorised by §2.4.2's own retention
trigger rather than against it; and R4 keeps the layer where §2.4 already put it. Nothing is being
overridden and no reopen is required.

---

## 3. Step 0 is not the only source-varying step

**The assumption that only step 0 varies is wrong (owner, 2026-09-23):** *"An asana task completed has a very specific format, or a github issue completed… I imagine we need to optimise some of the other steps too."*

A GitHub PR already **is** an ontology shape — title, body, state, number, timestamps, and explicit references. **For structured sources most of the work is mapping, not inference.**

| | transcript | GitHub PR | Asana task |
|---|---|---|---|
| "what changed" | **inferred** from dialogue | **stated** in title/body | **stated** in the status change |
| a candidate is | inferred | title + body | the task + its fields |
| an edge is | inferred | **`closes #N` — a real typed edge** | **`blocked by`** |
| an event is | inferred | open/close/merge — **already timestamped** | created/completed |

**⇒ A source type declares a MAPPING, and that mapping configures steps S0, S1, S2 and S5.** The `Source` type vocabulary exists and packs declare `extraction.sourceTypes` (§8). **The field→type mapping is the declaration the design must add.**

**⚠️ AND IT IS NOT MERELY UNDECLARED — THE READER HAS NO field→type MAPPING.** `_edus_from_conversation(conversation)` + `chunk_transcript(edus, target=chunk_size)` are **transcript-only**. There is **no field→type mapping in the reader at all** — so *"S0 is mechanical"* is true, and **S0 is also the step that must learn to read a GitHub PR.** **A lane must not assume the mapping exists because the field does.**

**Consequence to make explicit:** structured sources are **cheap and exact**; unstructured sources are **expensive and approximate**. That should be a declared property, not a surprise.

### ⭐ What a source type may NOT vary — the extraction PROMPT (owner decision, 2026-11-10)

⚠️ **Revision pin for this subsection: every `file.py:NNNN` number below is read at `25b84d6599`** (the `origin/main` revision it was measured against on 2026-11-10), **not** at the document's default pin `c79ba1cf2` — `tortoise/extractor_v2.py` is 17,678 lines at this revision and 6,255 at the default pin, so every one of these numbers is in range there but points at unrelated code. The symbol names remain the authoritative form.

**This amends the rule above by one clause, and the clause is the load-bearing one.** *"A source type declares a MAPPING"* reads, on its own, as licence for a **source-branded prompt set** — and that is exactly how the meeting epic (`#2696`) was justified: *"a 'meeting variant' that is a prompt-and-vocabulary variant costs little"*. **The mapping carries the structure — not the product identity — into S1 and S2. What they receive is the structure that mapping emitted, plus the text it emitted; what they must never receive is the identity of the product that text came from.** S5's typed edges remain the mapping's, per §3 above.

**Measured 2026-11-10 across eight products; the four with a bearing on this sentence** (the Onyx row's evidence and its CODE-VERIFIED grade: `#11081` `6750569282` items 1–2 and `6751381819` item 3; the decision itself: `6752174448`, corrected by `6752335134`):

| system | what selects per-source behaviour | grade |
|---|---|---|
| **Onyx** — ~60 connectors, including Gong, Fireflies and Zoom | the normalised path's prompts carry **zero** `DocumentSource` references; per-source prompts exist **only** on the federated path, where no canonical unit is ever produced | CODE-VERIFIED |
| **mem0** | one prompt on the extraction **call path** (`ADDITIVE_EXTRACTION_PROMPT`), and the output schema has **no type field at all** (`#11081` `6752174448`; its "no per-source specialisation" reading is graded **inferred**, not a verified absence) | CODE-VERIFIED for the schema; **the decision's wording** for "one prompt on the call path" — upstream `configs/prompts.py` defines several prompts selected by memory *target*, which is the reopen's evidence and is **not** adopted here; **INFERRED** for "no per-source specialisation" (`6750569282` grades that cell an inferred absence) |
| **Hindsight** | computes a shape (`fact_kind: "event" \| "conversation"`) and **deliberately does not store it** — the decision's wording is that its only consumer is the decision whether to set `occurred_start`/`occurred_end` | CODE-VERIFIED for "computes `fact_kind`" and "does not store it"; **the decision's wording** for "only consumer" — ⚠️ contested, see the reopen below |
| **Supermemory** | the discriminator is the **file format**, at parse/chunk (*"OCR for images, transcription for audio"*) — a parser selector rather than a prompt dispatcher | DOCS-STATED for the format half; **INFERRED** for "no per-type prompt" (the hosted **ingestion** half is not open source — Company Brain *is*, and its own docs say *"the ingestion side isn't part of it"*) |

⚠️ **These cells state the DECISION's wording, which is what this passage lands on — and a reopen contests two of them.** A reopen on `#11081` (`6754938117`, relayed on `#2696` as `6754939824`) argues from primary source that mem0 has four prompts selected by memory *target* rather than one, and that Hindsight's `fact_kind` has a second consumer (a `fact_type` fallback) besides the date decision. It changes nothing here on purpose: a docs passage may not silently override the record it rests on, so the route is that reopen. **Read the two cells as "what the decision says", not as settled measurement.**

**OVERRIDES:** per-source / connector-keyed prompt sets — Onyx's federated path (`SLACK_QUERY_EXPANSION_PROMPT`, `SLACK_DATE_EXTRACTION_PROMPT`) is the measured comparable that does key a prompt on the source brand, and the shipped `extraction.promptFragments` slot is the in-repo mechanism such a profile would reach for — this ruling forbids both routes, because the mapping carries the shape and the text and never the product identity, so one shared prompt is steered by labels instead of forked per source. **The marker's home is `#11081` `6755286041`** — the issue is what a lane actually reads, so that comment is where the line lives.

**⇒ Three constraints on any source-type profile (first applied to meetings, `#2696`). ⚠️ Both mechanism-bearing constraints are PROPOSALS `← NEW (proposal)` — the same marker §4.1 uses — not shipped behaviour:**

1. **Key on the shape the reader found, not on the product.** A meeting profile emits `speaker turn · section · action item · decision · status update · blocker · open question`, and the shared prompt is steered by those labels — so the same minutes arriving as a `.docx` export, a pasted body or a vendor API produce the same treatment. `← NEW (proposal)` — nothing emits those labels or steers a prompt by them today.
2. **Label a shape only when something downstream consumes it.** Hindsight discards `fact_kind` once it has done its job — the decision's wording says the date decision is its only one, and the reopen contests that count; a label nothing reads is write-path noise, and at S2.3 it is a name the classifier must disambiguate for nothing.
3. **`speaker` is a field on the unit plus a declaration in the extraction context** — never a new prompt. Hindsight's retain guidance does precisely this (*"Describe the speaker in each item's `context` … 'Customer Maria is speaking'"*), and it is the cheap fix for the fabricated-attendee class (`#8491`): attribute by declared speaker, never by guess. `← NEW (proposal)` — the unit is `{index, role, text}` (`_edus_from_conversation`) and no `extraction_context` exists in the tree; the prompt currently states the half that ships, *"NEVER emit a speaker/role on the point"*.

**What this does NOT change.** One extractor, one set of stage contracts, one commit path. No per-source branch, no registry, no forked stage — the connector contract is the registry-free one this codebase already states in `_connector_content_hash` (`projection/entities.py`): *"each connector emits its own payload shape … so keying the digest to the payload scopes it per kind by construction, with no registry to keep in sync."*

⚛ **The seams that ALREADY vary the prompt — and why the rule has to name them.** Pack-scoped prompt text is not fixed today, and **none of the routes below is a branch, a registry or a fork, so none of the three disclaimers above excludes them**: `memory_granularity` reaches **S1** through `_granularity_text()` (this document states it at `§S2.2`: *"That string is rendered into the S1 prompt and not enforced"*), and reaches **S2/S4** through the `MEMORY GRANULARITY` block the core-only and verbose master renders always emit — the **compact** render emits it only for a story that matches a `dev:` or `product-strategy:` trigger (`extractor_v2.py:1330-1336`), so on that path a pack's granularity need not reach S2/S4 at all; `extraction.promptFragments` — with `extraction.valueGate` — reaches S1 and S2/S4 through `_pack_hints_text()`. A pack declaring `sourceTypes: [conversation]` **and** `promptFragments` satisfies every other sentence of this passage and still ships exactly the source-branded prompt set the decision rules out. **So, plainly: pack-scoped prompt text keyed on the source type counts as varying the prompt.** A source-type profile may emit **labels and units**; it may not emit prompt text.

⚠️ **This clause is about CONTENT, not the declaration site — and this matters, because "declared for a *domain*" distinguishes nothing.** Every pack hint in the schema is declared per pack, and a pack *is* a domain: the renderer's own banner describes these hints as declared by the installed packs **for their own domain**, and `packs/agent-ops/manifest.yaml` already declares **both** `sourceTypes: [conversation]` **and** `memory_granularity`, so a meeting pack would satisfy "declared for a domain" too. What this rule does **not** reach is text stating the **domain's policy** — `memory_granularity`'s durable/ephemeral rule, a `valueGate` keep/drop list. What it **does** reach is text that tells the extractor how to read **this kind of source** — a meeting's speakers, a call's action items — and that is prompt text keyed on the source type whichever pack declares it, and however the pack names its domain.

⚠️ **That list is NOT exhaustive, and must not be read as one.** The master render also carries pack-authored prose by the same route: in the **verbose** render every `_group(...)` entry is emitted as `- <key> — <description>` (`extractor_v2.py:1354`), and in the **compact** render as `- <key> — <description>` only where the gloss disambiguates, bare `- <key>` otherwise (`:1314`, `_needs_gloss` at `:986`). **Any field a render emits is in scope, not only the two named above** — but which fields a given path emits differs: the **core-only** render is the verbose base *minus PACK KINDS and CHAINS* (`:1228`, stated again at `:1254`), and `render_s2_prompt`/`render_s4_prompt` pass `chains = ""` on that path (`:2185`, `:3639`), so pack-kind descriptions and chain notes do **not** reach S2/S4 when classify-later is on.

⚠️ **Not even the pack kind vocabulary survives that path, and this is the one that bites.** In this document's own terms the *declared vocabulary* **is** the pack kind set (§4.1's S2.3 row classifies *"against the declared vocabulary"*), and the core-only render drops PACK KINDS outright — it passes the pack **namespace** list instead (`pack_namespaces`, `:2189`), which is precisely why that slot exists. The compact render is **fail-open too**, and that is the sharper half of this warning: `_select_pack_kinds()` returns **all** kinds when there is no story, returns **all** when nothing matched, and selects a namespace **whole** whenever it has no `_PACK_TRIGGERS` entry — its own comment calls that *"a deliberate, fail-open one"* and notes that *"a new catalog pack is injected whole in compact mode until it is given a trigger entry"*. **A brand-new source-type namespace has no trigger entry, so it lands whole in every compact prompt.** So a per-source **vocabulary** addition is not prompt-neutral on either path: under classify-later the kind *names* are gone but the **namespace token** is not — `{pack_namespaces}`, built from the `pack_kinds` keys, is rendered into the **S2** prompt (`S2_TMPL_CORE_ONLY`: *"content whose kind would be a PACK kind ({pack_namespaces}) is NOT in the vocabulary above"*), which is a source-identifying string in the prompt the rule above forbids. S4 has no such slot, and its **default** contract does not re-emit **unchanged** S2 items — a *corrected* item is re-emitted and keeps its S2 kind **verbatim**, *"including PACK kinds NOT in your MASTER LIST"* (`_S4_DELTA_R2_NEW` / `_S4_DELTA_R6_NEW`; `TORTOISE_S4_DELTA` is on by default) — so the classification travels with them. Under compact the whole namespace is injected instead. What genuinely reaches **every** path unchanged is the core vocabularies, the user-personal-state block, and the carve-out and the vocabulary it names (`_compact_guarantee_tail`). **⇒ A profile's labels are not free on any render path, and the namespace token is what stands in for them where the names are gone (classify-later's S2), while the verbose and compact renders carry the namespace's kind *names* instead.**

⚠️ **Nothing enforces that today.** `extraction.promptFragments` and `extraction.valueGate` are dimensioned in `pack_registry.py`; `memory_granularity` is **not validated there at all**; and none of them is checked for source-branded instruction — so this clause is a review obligation rather than a gate.

⚠️ **Scope — this passage governs the DOCUMENT/TRANSCRIPT path only.** It says nothing about the pack-level `extraction.sourceTypes` activation predicate, which is declared and validated but has **no production caller** (`#11628`); do not read this as arming it.

---

## 4. The steps

### 4.0 What each step is FOR — the purpose test

**Owner review, 2026-09-23:** *"let's review the purpose of the steps because it seems the whole s1.5 is muddled."* This table is the answer, and it is what exposed the defect below: **two steps had no distinct purpose.**

| step | its ONE purpose | mechanical or model? |
|---|---|---|
| **S0 READ** | turn any source into one common shape + the mapping it carries, so later steps are source-agnostic | **mechanical** |
| **S1 NARRATE** | restate the input as connected prose in which the **relationships are stated**, using the input's own entity wording — so extraction **reads** relationships instead of **inventing** them | model |
| **S2.1 EXTRACT** | read the narrative (and the raw) and emit **candidate `Object`s (entities)**, **`Point`s**, **`Event`s** and their connections | model |
| **S2.2 VET** | **adversarially falsify** the candidates — entity or mere reference? atomic? did we account for everything the narrative said? | model (Jev) |
| **S2.3 CLASSIFY** | **assign each SURVIVING candidate its pack kind** — against the declared kind vocabulary | model (Jev), with a **mechanical kNN fast path** |
| **S3 RESOLVE** | **find what already exists for these candidates**, disambiguate using **the narrative as context**, **and attach** | mechanical + model (ambiguity only) |
| **S5 CONNECT** | (1) wire **this batch's own story**; (2) compare the batch **against the stored graph** — duplicate? lifecycle change? contradiction? | model decides, code writes |
| **S6a EMBED-DECISION** *(sub-step of S6, per row — runs BEFORE the write it belongs to)* | decide whether **this written row** earns a vector — the shared rule set's second question, so rent is paid only for text a semantic query could want | **mechanical** |
| **S6 COMMIT** | write entities + connections + metadata/lifecycle | **mechanical** |

**The test this table applies: can you state the step's purpose in one clause that no other step shares?** That test deleted **S4** and then deleted **S1.5** entirely — see §4.1.

**⇒ EIGHT steps: S0 · S1 · S2.1 · S2.2 · S2.3 · S3 · S5 · S6.** ⚠️ **The gap in the numbering is cosmetic and should be closed** (S1–S8) once the design settles; **until then the S-numbers are fixed identifiers — do not renumber** (already-posted comments on `#4894`/`#4899` reference them).

### 4.1 The step list

```text
S0  READ       source → common shape + THE MAPPING it carries      [per source type]
S0a NORMALIZE + HASH   mechanical, free — the hash both exact dedups share  ← NEW (proposal, §4.2)
S0b SOURCE DEDUP       URL-keyed today; the biggest missing volume lever    ← NEW (proposal, §4.2)
S1  NARRATE    shape → connected narrative, verbatim entity wording
S2.1 EXTRACT   narrative (+ raw) → candidate Object / Point / Event AND their connections  [LLM]
S2.2a-α EXACT DEDUP    within-batch + against-store; exact text only, mechanical  ← NEW (proposal, §4.2)
S2.2a-β NEAR-DUP       DETECT the pair and apply the §16.4 never-across guard —
                       NOTHING IS FUSED HERE (constraint 2)               ← NEW (proposal, §4.2)
S2.2b VET      the gate: adversarial review (validity + coverage) + the identifier-only
               DISCARD; may FLAG a merge (`MERGE`), never perform one        [Jev]
S2.3 CLASSIFY  each SURVIVOR → its pack kind, against the declared vocabulary      [Jev + kNN]
S3  RESOLVE    look up what exists for these candidates → disambiguate (narrative as
               context) → AND ATTACH the new context to the surviving entity — and the
               FUSION a `MERGE` flag asked for is executed HERE, after the gate
S5  CONNECT    two distinct sets: within this batch · to the graph
S6a EMBED-DECISION   per row, INSIDE S6 and before that row's write clause is
               built: EMBED / NO-EMBED for THIS row, asked of the SAME rule set
               S2.2b asks the DISCARD question of (§12.2a, one rule set two
               questions) — the second enforcement point, given a home (G2)
S6  COMMIT     create entities + connections + metadata/lifecycle
```

**Eight steps.** No S4 and no S1.5 — both fail the purpose test in §4.0. ⚠️ **The count is the TOP-LEVEL steps** (S0 · S1 · S2.1 · S2.2 · S2.3 · S3 · S5 · S6): `S0a`/`S0b` are sub-steps of S0, `S2.2a`/`S2.2b` sub-steps of S2.2 and `S6a` a sub-step of S6, so a sub-step does not make a ninth step. ⚠️ **§4.2's ordering table is the SAME list** — this block carries every id §4.2 lists; the two must never drift apart. ⚠️ **The numbering gap is deliberate for now:** the S-numbers are fixed identifiers, so **renumber only once the design settles.**

### S0 — READ  *(mechanical — no LLM)*

**In:** **a SOURCE** — a document, a code file, a meeting transcript, or a conversation. **Out:** the common shape the rest of the pipeline consumes, plus the field→type MAPPING that source type declares.

Normalises the shape **and applies the declared field→type mapping** (§3). Passes the mapping forward so later steps know what is already typed versus what must be inferred.

**✅ Confirmed mechanical (owner asked, 2026-09-23).** The live code does this in plain Python **before any model call**: `_edus_from_conversation(conversation)` segments the input into units, and `chunk_transcript(edus, target=chunk_size)` splits it for processing. **The first LLM call in the entire pipeline is S1** (`run_s1(...)`). So S0 is **parsing + mapping** — it must never be handed to a model, and it is exactly why structured sources (a GitHub PR) are cheap and exact while transcripts are not (§3).

#### ⭐ D10 and the version model — what the extractor must respect

**⭐ A document is a SOURCE, not a graph node (owner, 2026-09-24).** *"I am suggesting making them sources so our entity layer can be extracted from them… That way our entity layer becomes a proper abstraction over documents, code, meeting transcripts (all sources) — that's the reasoning/knowledge layer."*

**⇒ S0's input is a source FILE, uniformly, whatever it is.**
**⇒ D10 holds in both halves — ontology and code.** *Ontology:* `ONTOLOGY.md` v3.15. *Code:* the `:Document` NODE label is retired, and the `:Source` replay key is `url` (`tortoise/projection/edges.py:153`). **The consequence for the extractor stands: nothing downstream is document-specific.** A spec, a repo file and a call transcript differ only in their **mapping**, never in their treatment. **Full shape: `STORAGE-ARCHITECTURE.md` §9.3.** **One constraint applies regardless:** rebuild does not repair pre-existing graphs (`#2489`).

**⭐ The five consequences of D10 — ruled 2026-09-24, and the reasons they matter to the EXTRACTOR (full analysis: `STORAGE-ARCHITECTURE.md` §9.5).**

| # | ruling | why the extractor cares |
|---|---|---|
| **Q1** | **`aboutDocument` is KEPT; only its target label moves** (`:Document` → `:Source`) | ⛔ **It is NOT collapsed into `aboutSource`.** `aboutDocument` is in `DERIVABLE_STRUCTURAL_RELS` (`#2489`) and is resurrected at the old point by pass-2b; `aboutSource` is deliberately excluded. **Merging them would silently delete that rebuild guarantee.** Its **replay key** is `url` — the label and the key changed together, because a label retarget with an unchanged key resolves to nothing and **silently mis-points the rebuilt edge** |
| **Q2** | **Two classification axes, kept separate:** `sourceKind` (*what kind of source*) + `documentKind` (*what genre of document*) | **This is S2.3's target vocabulary.** ⚠️ **D10 does not kill `documentKind`:** it stops being an Object-subclass vocabulary and becomes a **GENRE axis over `sourceKind: document`.** **S2.3 assigns against the pack's declared kinds; the axes are not interchangeable, and `sourceKind: document` must not be treated as a genre** |
| **Q3** | `doc_status` is **RETIRED** (`tortoise/api.py:458`); `format` lives on `:Source`; `documentKind`/`title`/`topics`/`summary` kept; `content` leaves the graph | Retiring `doc_status` removes an unjournalled raw-`SET` write (`ingest.py:262-266`). Anything that read it switches to the read-the-entities lookup. `content` leaving the graph is the S0 input contract (§3) |
| **Q4** | The `documents` cap is **re-pointed at `:Source`** — not retired, not folded into the node cap | a `:Source` is **equally invisible to the main node cap**, so the reason the cap exists does not expire; retiring it **ungates `/v1/index/docs`** |
| **Q5** | Fold + change the replay key + migrate | There is nothing to migrate: **zero `:Document` nodes** exist, so the fold and the `coalesce(title, name)` → `url` rekey are free. The lesson to keep is that *"free TODAY"* is a **deadline**, not a state. |

**A re-fetched source that has changed is a NEW VERSION, not an edit.** `_upsert_source` bumps `updatedAt`/`version`/`contentHash` on a hash-differing `ON MATCH`; that transition is **journalled as `SourceVersioned`** (`#5024`, `tortoise/log.py:155`), whose fold delegates to the same `_upsert_source` fold `SourceCreated` rides — so `derived = replay(journal)` holds. **This is T6, and it carries S6 COMMIT's extractor-side obligation** (`STORAGE-ARCHITECTURE.md` §9.6: record `sourceVersion` on the extraction link).

#### ⭐ The version model — and the one thing S6 must do (owner ruling, 2026-09-24)

**`extractedFrom` records the SOURCE, not the source VERSION.** Identity (`url`) is not version (`contentHash`). **The version rides on the link as `sourceVersion`** — and it is **per-link**, because `extractedFrom` is many→many. Without the version on that edge, *"are these entities current?"* is **unanswerable from the graph** — *"are they trustworthy?"* is answerable today (confidence, NAND, supersession), but *"are they about the content we now hold?"* is not. **Both are reads; only one has an anchor.**

**S6's obligation, in one line:** when a run writes derived nodes, it must **record the version it read on the extraction link, as `sourceVersion`** (`ONTOLOGY.md` §4.6) — the `contentHash` of the version read. That is the whole extractor-side change — and it is cheap, because D30 keeps content out of the graph, so a version costs **three timestamps and a hash**.

**⚠️ Scope of the anchor — the anchor covers Events and Documents too (owner-approved 2026-09-25, `#5199`).** `sourceVersion` rides on `extractedFrom`, which is declared `Point → Source`. Derived **Events** and **Documents** reach their source through their own links, not through `extractedFrom`. The anchor therefore extends to the **derivation** `references` link — see `docs/architecture/STORAGE-ARCHITECTURE.md` §9.6 for what it costs (and for the paths where it is honest-absent rather than present), and `ONTOLOGY.md` §3.4/§4.6 for the model — ⚠️ **`ONTOLOGY.md` §4.6 is owner-gated and still states the pre-decision Point-only scope; `STORAGE-ARCHITECTURE.md` §9.6 is the operative record until that wording lands.** Identity/mention and referential-containment links are **not** version-scoped, because there is no version of an identity to compare.

| Rule | Statement |
|---|---|
| Identity | `url` — stable across versions |
| Version | `contentHash` — identifies a **version** of that identity |
| Raw content | **append-only** — a differing hash on re-fetch is a **new version, never an edit** |
| Extraction | **version-scoped** — the version read is recorded on the extraction link as **`sourceVersion`** |
| Version change | appends a **journal record** that **closes** the current version's window and opens the new one — never an overwrite |

**⭐ One node per `url` — the version history lives in the journal, and what is superseded is the FACTS.** `:Source` MERGEs on `url`, so exactly one node per source carries the **current** version; a version change creates no second node and rewrites no older one. No Source→Source supersession edge exists or is needed: successor facts attach to the standing `:Source` (`extractedFrom` is keyed by `url`), and the earlier facts are replaced through the ordinary `CORRECTS` mechanism. **The source is the identity; the entities are the belief.**

**⭐ The policy is B — mark stale now, supersede on re-inference (owner).** A re-fetched source's entities are **marked stale immediately** and **superseded only when re-inference produces their successors**. **Not A (immediate supersession)**: A withdraws the belief *before* producing its successor, so between the source changing and re-inference running the graph asserts **nothing** about a subject it previously had a position on — strictly worse than a stale-but-present belief. **`stale ≠ wrong`:** supersession is **additive**, never a delete.

**⚠️ Interval-closing is part of the WRITE, not a later repair.** An unclosed `validTo` reads as *"still true"* indefinitely — the field names this as **the #1 production bug** in temporal knowledge graphs. **And document-level alone would not answer the owner's question**: if you supersede only the document, the **facts are untouched** — *"what did we believe before?"* is a question about **facts**, which is why the version belongs on the **link**. The open question is `#5038`.

### S1 — NARRATE

**In:** the common shape. **Out:** a **connected narrative**.

**The format matters and is the owner's requirement (2026-09-23):**
> *"important for S1 to keep verbatim wording of Entities (… not separating by a list of objects, a list of events, etc. but by having the connection … 'Object A changed because of Points X,Y,Z and that happened through Event W' is the typical form, without inventing entities if the input has none itself."*

**Why it works:** the connections are **in the sentence**, so S2 *reads* them rather than re-inferring them. Re-inference from prose is where `the <X>` entities and mis-links come from. **Verbatim entity wording** also prevents paraphrase-duplication (`the plan doc` vs `the delivery plan`).

**⚠️ Caveat:** the narrative is the **transport, not the embedded unit.** What gets embedded is still the **atomic extracted claim**. A narrative sentence carrying three facts retrieves badly.

### S1.5 — ⛔ NOT A STEP (owner review, 2026-09-23)

**There is no S1.5.** Two candidate jobs were proposed for it, and **both are excluded, for different reasons** — the review half as a defect, the lookup half as a **mis-ordering**.

#### (a) The review half — a defect, not a step

*"Was S1 too coarse?"* is the wrong question: it asks about atomicity **on the wrong artifact** (atomicity is a property of the *items*, not of the prose), and it is **blind to the failure it targets** (if extraction drops 2 of 3 stated things, a narrative-side check sees a *fine* narrative and passes). **The question belongs at S2.2, on the items.**

#### (b) The lookup half — a mis-ordering, not a pre-extraction step (owner, 2026-09-23)
>
> *"does it make sense to do the lookup before we have entities? shouldn't we first find the entities and do the lookup and we can use the narrative to disambiguate when uncertain?"*

**No — it does not make sense, and the reason is causal: the lookup is keyed on entities, and entities do not exist until extraction produces them.**

| | lookup BEFORE extraction | lookup AFTER extraction |
|---|---|---|
| **keyed on** | **strings the narrative happens to name** | **the candidates extraction actually identified** |
| **knows types?** | no — prose words | **yes** — candidate entities with kinds |
| **can it find what matters?** | only what the prose chose to name | **everything extraction proposes, including things the narrative never named** |

**⇒ The lookup belongs in S3, keyed on candidates.** Looking up earlier means looking up **words from prose**, not entities — a strictly weaker operation that also **could not cover S3's key set**.

**The correctness argument must live in ONE place.** Splitting the dedup guarantee between *"extraction knew A existed"* and *"S3 resolved the candidate"* — two places that can disagree. **With the lookup inside S3, S3 is the single authority on whether a candidate is new or existing.** A guarantee held in one place is a guarantee; the same guarantee held in two is a bug waiting for them to diverge.

#### ⚠️ D4 is NARROWED along its own reasoning

**D4 (decided)** reads: *"One entity-keyed neighbourhood lookup after the narrative, consumed by **both** extraction **and** the judgment."*

**D4's stated justification is about salience** — *"something could be discarded even though it changes something that was already there"* — **which is a claim about the JUDGMENT, not about extraction.** That reason survives intact and the judgment keeps the neighbourhood.

**What D4 never justified is extraction consuming the lookup.** Its only reason was *"so extraction emits attach-to-A instead of create-A"* — and **that reason is now unnecessary, because S3 resolves authoritatively.**

**⇒ D4 is NARROWED, not reversed:** one entity-keyed neighbourhood lookup, **after** extraction, consumed by **the judgment** (salience) **and by S3** (resolution). **Extraction no longer consumes it.**

**✅ `DECIDED (narrowed)` — the owner ruled:** the lookup sits **after** extraction, and it is consumed by the salience judgment and by S3 only. **Extraction does not consume it.** This follows from D4's own stated reason — the extraction consumer was never covered by it.

>
> ✅ **Note — the lookup bug `#4511` does not gate this design.** It bit *when the lookup sat before extraction* (`_fts_rows` read `r.get("kind")` while the callee emits `point_kind`, so every prior's type returned **blank**). **The lookup sits in S3 (§4.2), so the bug is not a dependency of this design.** ✅ **MEASURED 2026-11-01 (§10, `#10463`) — and the answer is NO:** giving extraction the neighbourhood cut created nodes **284 → 247 (−13 %)** against a pre-registered **≥ 25 %** bar, while recovering **fewer** real facts (47 → 40) and **fewer** operator edges (171 → 139) — a smaller extraction, not a leaner one. ⚠️ **That is run 1, whose log the `#10463` ruling WITHDREW from the checked set; the base-registered re-measurement (`#10554`) is `253 → 250` (ratio `0.988`) with the untreated arm drifting `10.9 %` between runs, so the effect is below what the instrument resolves.** Bundle: `docs/evidence/5087-hypothesis/` — read its *"Which run's number"* block (§10) before quoting a figure.

### S2.1 — EXTRACT

**In:** the narrative **+** the raw (see the finding below). **Out:** candidate **`Object`s (entities)**, candidate **`Point`s**, candidate **`Event`s**, and their connections.

> ⚠️ **Vocabulary — "claim" is NOT a thing** (owner, 2026-09-23). `docs/ONTOLOGY.md` v3.14 (`#4369`) declares it: **"claim" is the sanctioned user-facing *gloss* for a belief `Point`** — *"It is **not a distinct kind**: no `claim` type and no `claim` pointKind, and no canonical node write value."* **The type is `Point`.** **Write `Object` and `Point`, never "entities + claims".**

This is v2's S2, with priors in hand. Still proposes only; decides nothing.

**✅ FINDING (2026-09-23) — the code hands extraction the raw alongside the story.** The call is `run_s2(model, story, master, session_date=…, edus=edus, …)` (`extractor_v2.py:16232-16235`) — ⚠️ **this row is the one passage cited HEAD-relative, not at a pin**; `tests/test_7949_capstone_measure.py` resolves it against the tree, so it moves with the code. **`edus` is the raw segmented input, passed together with the computed story.** **⇒ D3-Option 4 holds: the narrative is a **focus**, and the raw is present too.**

> ⚠️ **What this does to D3.** The open question is not *"may extraction consult the raw?"* — it does. It is the much smaller **"what does the narrative add that the raw does not?"** The measurement (§10) compares **narrative + raw** against **raw alone** — ⛔ **and that shape cannot test loss-by-replacement**, so the D3 question is now two: *what the narrative ADDS* (measured, §10) and *what it LOSES when it replaces the raw* (**not yet measured**). §10's verdict says so. **This paragraph named the ADD question correctly; the error was downstream** — the
registration then fixed the contrast with *"the same `edus`"* for both arms, which is what makes the falsifier
unfirable (block at the top of §10).

### S2.2 — VET *(the counter-filter; owner proposal)*
>
> *"S2 sounds ok, but seems like it just needs a counter filter (Jev doing adversarial review), so we have step S2.1 LLM entities list and S2.2 jev"*

**Jev, asked to FALSIFY, not confirm.** Two **levels**, and they are different shapes — one is many cheap decisions, the other is a single batch verdict. <!-- v4-mechanism: vet state: implemented -->

#### Level 1 — per-candidate (many, cheap, evaluated in parallel)

1. **Adversarial:** is this actually an entity, or a reference? → the `PR #465` / `the X` class (**62.3% of Objects**)
2. **Atomicity:** is this ONE claim, or several fused together? *(Atomicity is a property of the item — this is where it belongs, not on the narrative.)*
3. **Worth keeping — judged against THE PACK (`#1026`'s `valueGate`).** Not a generic value judgment: **is this durable, per the pack's own declaration?**

> **This is where the pack stops being prose.** `packs/dev/manifest.yaml` already declares `memory_granularity` — *"Durable: problem-family reasoning … Ephemeral: issue/PR numbers, CI status, test counts, commit hashes, tool workarounds, sprint mechanics."* That string is **rendered into the S1 prompt and not enforced** (`_granularity_text()`). **S2.2 is the step where the declaration becomes a gate** — the `valueGate` slot `#1026` declared (`packs/_template/manifest.yaml:260`), realised as an adversarial question. ⚠️ **The mechanical half is `tortoise/value_gate.py` (`#4899`), and it must default ON and stay subordinate to VET** (`TORTOISE_VALUE_GATE` unset must not mean *"the predicate never runs"*). So the work is a **default and a subordination, not a missing slot**.

#### Level 2 — per-batch: did this batch come out right? (a batch verdict, not a per-item one)

1. **Coverage:** **did we account for everything the narrative said?** If the narrative stated three things and extraction emitted one, that is a **loss** — and it is only visible by comparing the items **against** the narrative. ⚠️ *The "was S1 too coarse?" question belongs here, on the items — never on the narrative before extraction (§S1.5).*
2. **Level of abstraction:** is this session at the abstraction **this pack** asks for? A narrative that spent itself on CI status and commit hashes did **not** — even if every individual candidate passed Level 1.

**Both are batch questions.** Coverage needs the whole item set compared against the whole narrative; abstraction is a property of the batch, not of one candidate.

#### ⚠️ Owner question: if the narrative is poor, should we send it back? (2026-09-23)
>
> *"should that happen entity per entity or/and for narrative to be at the right level of abstraction as per the packs? basically, if the narrative is poor, should we send it back?"*

**YES — send it back. But the TRIGGER must be the outcome, not a speculative review of the prose.**

| | speculative pre-check (rejected) | **outcome-triggered re-narrate (proposed)** |
|---|---|---|
| **when** | before extraction, every session | **after S2.2, only when the batch verdict is poor** |
| **cost** | one model pass on **every** session | a pass **only on sessions that actually failed** |
| **basis** | a judgment about prose | **a measured keep-rate** — concrete |
| **recoverable?** | if the narrative is poor, extraction still ran on it | **re-narrate before extraction has produced anything terminal** |

**Why this is the right shape:** the whole reason a narrative-side review looked attractive is the case where the narrative is *so* poor that extraction yields nothing usable. **But that case is only distinguishable from a good session by looking at what extraction actually produced** — so the check belongs *after* extraction, and it should be **triggered by the yield**, not run speculatively beforehand.

**Mechanism (proposed):** if the batch verdict is poor — *e.g. VET discarded almost everything, or the keep-rate is below a threshold* — **re-narrate ONCE with the pack's `memory_granularity` emphasised, then re-extract.** ⚠️ **Bounded: once, then accept.** A model-call loop can oscillate — the second narrative can be worse than the first, and nothing in the design currently prevents that. **A retry, not a loop.**

**Outputs:** `KEEP` · `NOOP` · `DISCARD` · **`MERGE`** *(the VERDICT — a flag, §16.4)* · `SPLIT` *(for a fused item — extraction may then split it from the raw, which it already has)* · **`RENARRATE`** *(batch-level only)*. ⚠️ **`KEEP`/`NOOP`/`DISCARD`/`MERGE` are the four members §16.4 rules and `tortoise/vet_gate.py` implements (`OUTCOMES`); `SPLIT` is declared here and is NOT in that set — the gate cannot emit it, so read `SPLIT` as design intent, not as an implemented outcome.**

⚠️ **`MERGE-INTO-EXISTING` is deliberately NOT a VET output — and `MERGE` and `MERGE-INTO-EXISTING` are not the same thing.** VET **may** emit the **verdict** `MERGE` — *"fold into another candidate"* (`tortoise/vet_gate.py:OUTCOMES`), a **flag** and nothing more. VET may **not** emit the **action** `MERGE-INTO-EXISTING`. VET runs **before** S3 (§4.2), and S3 is **the single authority on whether a candidate is new or an existing node**. A VET stage that *performed* the merge would be deciding new-vs-existing itself — the VET/S3 circular dependency. **The FUSION is S3's action, executed after the gate; VET may only keep, discard, split, FLAG one merge candidate, or ask for a re-narration.** ⚠️ **The routing is `#5006`'s and it now exists**: `extractor_v2._merge_flagged_contents` carries a recorded `MERGE` verdict to S3, where `near_dup_merge.apply_merge_union` executes the union-merge. VET still never performs the fusion itself. **This distinction is what makes this paragraph and §4.2's ordering agree: `S2.2a-β` detects the pair, `S2.2b VET` records the verdict, `S3` executes the fusion — three sites, one operation each.**

**⚠️ Unmeasured, and it needs the small-sample-first method:** *whether re-narrating actually improves yield*, and *what keep-rate counts as "poor"*. Both are thresholds that must be set by hand on tens of real sessions with the owner correcting the rule — **not chosen in advance.**

**⚠️ There is no S4 — do not restore one.** *"S4 — granularity review"* is **not a step**: its purpose is identical to question 2 above. **The step list is the EIGHT steps of §4.1: S0, S1, S2.1, S2.2, S2.3, S3, S5, S6 — there is no S4 and no pre-load step; the lookup lives in S3 (§4.2).** ⚠️ **The count is EIGHT — §4.0 and §4.1 agree; do not write "seven".**

> **⛔ DRIFT MARKER — this sentence is about the v4 PROPOSAL, not the shipped code.**
> A lane reading *"do not restore one"* while looking at `run_s4` will conclude the stage is vestigial and
> delete it. **It is not vestigial: the live pipeline runs S4 whenever a story exists.**
>
> | | this design (v4) | shipped code |
> |---|---|---|
> | S4 | *"There is no S4"* | `merge_embed_lists(s2, s4)` in `extractor_v2.py`, whose union the embed pass consumes; `run_s4` is called in the v2 pipeline |
> | gating | removed from the design | the call sits under `if story:` with **no flag gate** — S4 runs whenever a story exists |
> | the S4 flag | — | ⚠️ `TORTOISE_S4_DELTA` selects the **delta vs complete-list contract of S4's OUTPUT**, **not** whether S4 runs |
> | stage order | the eight steps above | `run_s1` → `run_s2` → `S2.2 VET` → `S3 search_graph` → **`run_s4`** → `S5` |
>
> **⚠️ `TORTOISE_S4_DELTA=0` does NOT disable S4** — it selects the legacy complete-list contract. There is **no S4-specific off switch**: S4 is reached whenever the v2 pipeline runs a story, and on the route that reaches `extract_session_v2` it is skipped only as a **side effect of something else**. ⚠️ **Three such paths on that route, none of them an "S4 switch":** (i) **`TORTOISE_EXTRACTOR=v1`** routes to the legacy `value_extractor.extract_session` path, which has no S4 stage at all (`sdk.py`; `_commit_session_v1`); (ii) **passing `summary=`** takes that same v1 dispatch — `sdk.py` reads `if extractor == "v1" or summary is not None:`; (iii) **an empty conversation** returns before S1 (`if not edus: return {...}`, with `degraded: True`), so S4 is never reached. Other `capture_session` lanes — keyless/no-provider, already-captured replay, and `TORTOISE_SESSION_EXTRACTOR=m2` — never enter the v2 extractor at all, so they are not S4 skips of this route. ⚠️ **S4 is NOT bypassable only by leaving the v2 pipeline** — that would contradict the `if story:` row directly above, and missed paths (ii) and (iii).
>
> **Removing `run_s4` is a v4 migration deliverable, not a cleanup** — it is §4.3 **G5**'s S4 clause,
> tracked at **`#8500`**. ⚠️ **Not `#5333`**: that issue's own body scopes it to the deletion/relocation
> half and states it *must not absorb* other work, and it contains no reference to `S4`. **Never delete
> `run_s4` on the strength of this document alone.**
>
> ⚠️ **"S2.2 VET and S2.3 CLASSIFY already exist in the code" is NOT the test that the successor
> exists.** §4.0 gives S2.2 VET the coverage question whose answer S4's removal relies on, and both
> stages are present in the tree (`_vet_gate.apply_vet`, `kind_classifier.classify_items`) — and
> `classify_items` is wired into `extract_session_v2` **behind a flag that defaults OFF**
> (`TORTOISE_CLASSIFY_LATER`), so on the default arm it has never run. `apply_vet` is a different
> case **since `#10996`**: `TORTOISE_VET` still defaults OFF, but the S2.2 **mechanical** half
> (`TORTOISE_VALUE_GATE`) defaults **ON**, so the VET pass — and `apply_vet` — DOES run on the
> default arm, keeping everything the `#4899` identifier-only predicate does not fire on (measured:
> 0 drops of 61 Objects). Judge the fold by that, not by "the flags default OFF"; and presence is not
> sufficiency: the fold's **remedy** half IS implemented for ONE class and NOT for another (the two
> halves are stated in the S2.2b-ADD note below). VET's **coverage signal**
> only **detects** (`check_batch`'s `coverage_suspect` — reported by the gate, **acted on by the
> PIPELINE** since `#11002` (which is what `in v1` used to defer), and only for a
> narrative that produced **zero** candidates). `apply_vet` only **removes** — **except** the Layer-1
> entity RESTORE from `prior` (`vet_gate.py` **Rule 4**), which re-emits an entity an EARLIER pass
> discarded, needs a surviving candidate to reference it, and never sources a new candidate from the
> narrative (`OUTCOMES` = KEEP/NOOP/DISCARD/MERGE; `MERGE` is a verdict VET records and S3 applies —
> `#5006`). CLASSIFY
> only **labels** (`classify_items` returns a kind per **existing** id; `_apply_classify_kinds` adds no
> item). So **no stage the design folds S4's purpose into can emit a candidate a coverage check found
> missing** — the FOLD's two stages still cannot. ✅ **The successor is the bounded
> S2.2 re-narrate (§4.4, `#11002`)** — `_renarrate_trigger` → `_arm_extract`, the `emphasis` seam in
> `run_s1`: it fires **once**, re-extracts, and **unions both arms** with `merge_embed_lists` under
> E4's identity-key rules, so it CAN add a candidate the coverage check found missing and it preserves
> both arms' items (merges-not-replaces — the owner's ruling on `#11002`). The universal is scoped to the
> FOLD on purpose: an unqualified "no shipped stage" would deny the very capability the dash clause
> says is lost, since `run_s4` *is* a shipped stage. The pre-condition for removal is a stage
> that can ADD; the successor above is that stage (`tests/test_selection_step.py` pins the ADD proof
> and the union), and what still stands between it and the deletion is §4.4's **F3** measurement —
> `_s4_merge_stats`'s `added_by_s4 > 0` **while** the FIRST (pre-retry) arm's `coverage_suspect` is
> False **and no retry fired** (one arm, one flag, one base — see §4.4), i.e. an episode the retry
> cannot reach. **It has been MEASURED (2026-11-08) and it FIRES** — 4/7 sessions on the
> **`legacy` arm** (`TORTOISE_S4_DELTA=0`) in `docs/evidence/1789-s4-reeemit-tax/ab-20261107T023958Z.json`,
> 3/7 on that arm in the re-run, 5/7 across their union — **and 2/7 on either run of the
> `delta` arm, which is DEFAULT-ON** (`_s4_delta_enabled` resolves `env_flag(…, True)`), so
> `run_s4` **stays** on both readings and the removal (and the partial-gap stage it needs)
> moved to **#11534**. ⛔ **The arm must be named, because the number is arm-specific:** the
> two artifacts are one replay holding both arms, and the arms are different CONTRACTS (`legacy`
> re-emits, `delta` emits only what is missing), so their `added_by_s4` counts are not the same
> quantity. `docs/evidence/1789-s4-reeemit-tax/README.md` compares them as evidence about the gap
> channel (12/9 legacy vs 6/6 delta gap additions) and states that **neither arm's sensitivity to
> the real geometry is measured, so the token-saving RATIO — which is the saving — is undetermined
> in both signs**. A count without its arm is therefore a claim about neither.
> Not satisfiable by reasoning about the code: the artifacts are the instrument.
> (Pinned: `tests/test_extractor_v4_s4_stage_claims.py`, `tests/test_selection_step.py`.)
>
> ⛔ **WHAT `#11534` MEASURED IS THE TRIGGER, NOT THE STAGE — and it covers ONE of the two gap
> classes.** The PR ships two pieces inside S2.2: the DETECTOR `vet_gate.partial_gaps` — the
> statement/clause coverage detector `check_batch` now reports (a char span into the narrative, so a
> re-extraction has an address to read; it needs no `#2684` span layer) — and its consumer `S2.2b-ADD`
> (`extractor_v2.run_partial_gap_fill`, behind `TORTOISE_PARTIAL_GAP_FILL`, **default OFF**).
> ⛔ **Why the `-ADD` suffix:** `S2.2b` is the VET gate of §4.1/§4.2 — a RECORDED decision ("DECIDED
> 2026-09-25: S2.2b, i.e. HERE") and the value `tests/test_extractor_v4_step_list_consistency.py`
> pins as `GATE_ID`. One id may not name two stages, so the ADD stage is `S2.2b-ADD` everywhere it is
> labelled; the `S2.2b` occurrences that mean the gate are unchanged. On the
> F3 population (the seven recorded sessions) the **trigger** flags a span whose tokens overlap **8 of
> the 12 `points`** and **1 of the 9 `operators`** that `run_s4` adds. The operator half is not a
> tuning problem — an operator addition GENERALISES a claim the batch already carries ("a retry path
> that does not re-establish ownership … defeats any downstream completion check"), so no narrative
> span is uncovered even though the item is new, and a coverage detector cannot reach it by
> construction. **So the pre-condition above is met for ADD-from-an-uncovered-span and is NOT met for
> ADD-by-generalisation**, and `run_s4`'s removal therefore stays gated on the second half.
> **`S2.2b-ADD`'s own yield is NOT measured** — it depends on whether a model handed a flagged span emits
> the right item, which needs live sessions and a key, so the coverage half is met by a *trigger*, not
> by a demonstrated stage. The detector's own cost is measured too — 8.3% of 844 segments flagged —
> but the flagged population is MIXED (7 genuine clause-level elisions, 4 non-claims and 3
> structural fragments in the 14 of 35 the record enumerates), so the stage's YIELD is unmeasured;
> that, not a claim about which class dominates, is why it is armed OFF. The flag rate and the
> per-session read-through are `docs/evidence/11534-partial-gap/README.md` §2, and the explicit
> bounds are its §4.
> (Pinned: `tests/test_extractor_v4_s4_stage_claims.py`.)
> **On the step *names*:** the code's own stages are **S1–S5** (`[S1] STORY SUMMARY` … `[S5] EMBED`, module
> docstring), with `S2.2 VET` present in the production path. **`S0` and `S6` have no code
> counterpart** — those two are v4 design names. ⚠️ **`S2.3` DOES have one; it is DISABLED, not absent:** the
> classify pass is `kind_classifier.classify_items` (`kind_classifier.py`), wired in
> `extractor_v2.py`, gated by `TORTOISE_CLASSIFY_LATER` — **unset = "the LEGACY
> pipeline"**. **`S2.3`, `S3` and `S5` all have code counterparts even though the *string* `S2.3` is absent
> from `extractor_v2.py`** — `S3` and `S5` appear 51 and 22 times
> in `extractor_v2.py`, including as production stage comments.
>
> ⚠️ **No fourth exit (`return`/`raise`) appears between `extract_session_v2`'s entry and the S4 call beyond the enumerated empty-conversation one** — an exit on the `not story` side of the call's own guard is textually after the call and is **not** an S4 bypass, since S4 is already gated off there.
>
> ⚠️ **These marker claims are pinned mechanically, not by prose: `tests/test_extractor_v4_s4_stage_claims.py` resolves each enumerated claim above against the tree at HEAD** — a change that falsifies one reds a test instead of being discovered.

**Why Jev fits:** it is a **decision-only** model — it cannot return a value outside the supplied schema, so it cannot invent an entity or a name. Three primitives, **mixable in one call**: *Choice* (pick from a list), *Score*, *Noul* (0–1 probability). Doc: `https://docs.typesafe.ai` (API: `POST https://api.typesafe.ai/v1/systemone`, the fleet callers send the vendor alias `jev-latest` by owner ruling 2026-10-27, settled into agent-infra `main` by agent-infra#2914 the same day). ⚠️ Measured **$0.000243/item at 3 questions** (~$6/month at 25k items) — **~4–7× cheaper than a frontier LLM, not the vendor's claimed 40–400×** (self-tested). That measurement was taken on the host the fleet used *before* the 2026-10-26 vendor migration (`jevtypesafeai.com`), so treat the price as directional until it is re-measured on `api.typesafe.ai`.

**Fail-open on uncertainty** (keep, don't drop) — a wrong keep is noise; a wrong drop is memory loss.

### S2.3 — CLASSIFY (assign the pack kind — *after* the gate, on SURVIVORS only)

**Owner, 2026-09-23:** *"For the extraction journey, I think we're missing a Jev substep to classify as per the pack, no? maybe part of S2.2? or not sure if we already have it. Maybe more efficient (and reliable) as its own step with what survives S2.2."*

**✅ The design's classify pass is real: a flag-gated pass positioned after the gate.**

| what exists | where |
|---|---|
| **`KindClassifier.classify_items(items)`** → `{assignments: {id: {kind, margin, mode}}, stats, warnings}`, **never raises (fail-open)** | `kind_classifier.py:179` |
| **A closed vocabulary** — the stats counter `closed_vocab_rejects` exists | `kind_classifier.py:200` |
| **A mechanical kNN fast path** → margin gate → `nearMisses` rerank → **batched LLM only for the low-margin tail** | `#1695` |
| **The deferred pass** — `_collect_classify_items(embed_list)` → `classify_items` → `_apply_classify_kinds` | `extractor_v2.py:4467` / `:4492`, called at `:4803` and `:4888` |
| **The flag** — `TORTOISE_CLASSIFY_LATER`, unset today = *"the LEGACY pipeline"* | `extractor_v2.py:513`, `:4656` |
| **Jev requires an adapter, a prompt and a parser** — `llm_tail` is a **boolean** switch (`kind_classifier.py:93`), and the ambiguity tail is resolved by a hardcoded prompt plus a JSON parser — ✅ **now built, OFF by default: `s2_decision_seam.classify_decider` replaces the tail's DECISION SOURCE via `KindClassifier(tail_decider=…)`** | `kind_classifier.py:93`, `:309`; `s2_decision_seam.py` (`#5087`) |

**⇒ The step is `TORTOISE_CLASSIFY_LATER=1`, off by default.** Three things are wrong with its current placement, and they are the actual work:

**1. Classification happens INLINE — inside S2.1's big call.** When the flag is on, `core_only=classify_later` is passed to S2 (`:4782`) so the extractor **only assigns core kinds and leaves domain kinds `unclassified`**, and the separate pass fills them in. When it is off, the extraction LLM emits kinds as part of its prose output. **That is the wrong place for them** — see below.

**2. It must run on SURVIVORS.** ⚠️ `_collect_classify_items` collects from **`embed_list`** — the whole candidate set — and **`classify_consolidation` does not remove items from `embed_list`; it decides a write operation per point** (`:3994`). **So a `DISCARD` (`#4899`) would currently be classified anyway.** *Classifying a row you are about to throw away is wasted work **and** it is worse than wasted: **the discards are exactly the rows whose kind is least meaningful**, so they pollute the classifier's own signals.*
> **⇒ The wiring requirement: a discarded candidate must be removed from (or marked in) `embed_list`, or `_collect_classify_items` must skip it.** Without that, `#4899` and S2.3 ship in the wrong order and S2.3 pays for `#4899`'s savings.

**3. It should be its OWN step, not a S2.2 substep — and the reason is the fast path.**

- **As its own step, the cheap path works:** the kNN covers the easy majority with **no model call**, and only the low-margin tail is batched. **Inline, you pay the large extraction model to classify every item** — because the big call has already committed to emitting a kind in its prose.
- **It passes the §4.0 purpose test:** *"assign the pack kind"* is a clause **no other step shares.** S2.2 asks *"should this exist?"*; S2.3 asks *"what IS it?"* **Different questions, different failure modes** (S2.2 fails as *"we kept junk"*; S2.3 fails as *"we mislabelled it"*).
- **And a decision-only model STRUCTURALLY enforces the vocabulary.** A `Choice` over supplied kinds **cannot return a kind outside the pack** — whereas an extraction LLM emitting kinds inline can only be **hoped** to stay within the declared set. `#1026` requires *"entityCues must reference declared kinds"*; **a separate Jev step is what makes that requirement true rather than aspirational.**

**⚠️ Q2 — WHICH vocabulary does S2.3 assign against? (owner ruling, 2026-09-24; `STORAGE §9.5`)** Two axes exist and **they are not interchangeable**:

- **`sourceKind`** — *what kind of SOURCE is this?* (`document`, `conversation`, `github_issue`, `agentSession`) — **declared by the PACK** (`extraction.sourceTypes`), and it is a property of the **arrival**, decided at **S0** from the mapping, **not inferred by a model**.
- **`documentKind`** — *what GENRE of document is this?* (research, planDoc, apiSpec, transcript…) — **the vocabulary S2.3 assigns against.**

**The trap, named:** `sourceKind: document` is **a source type meaning "this arrived as a document"** — **it is not a genre.** Assigning `sourceKind` values as if they were kinds, or collapsing the two axes into one field, is a **category error** (the library world keeps document *type* and *genre* in **separate fields for decades**). **S2.3 assigns GENRE (and other pack kinds); it must never be asked to re-derive the source type**, which S0 already knows mechanically and for free.

**Position in the journey — after the gate, before resolution:**

```text
S2.1 EXTRACT  → S2.2 VET (gate) → S2.3 CLASSIFY → S3 RESOLVE → S5 CONNECT → S6 COMMIT
```

**Why before S3 and not after:** S3's *attach* step and S5's *chain* enforcement key on kinds (`_select_pack_kinds`, the chain enforcer), so classification must precede them. **And why after S2.2:** so only survivors are classified.

⚠️ **Position is settled: not a S2.2 substep, and after the gate.**

### 4.2 The ordering — cost per unit of SAFE rejection, NOT "cheapest first"

**Owner, 2026-09-23:** *"the point is that Jev could likely do the classification way cheaper, no? so fine if it's its own step. Although not sure if we should mechanically dedup first as that can reduce the set further? anyhow, check the ordering makes most sense."*

**Both halves are right, and checking the order found something worse than expected.**

#### ⛔ The order is inverted

Resolved by call graph:

| runs | where | what |
|---|---|---|
| 1st | `:4803` | classify pass on `embed_list` |
| 2nd | `:4888` | classify pass on `complete_list` (after S4's merge) |
| **last** | **`:4995`** | **`execute_embed(...)` — which contains `classify_consolidation` (`:3994`), i.e. the dedup/consolidation gate** |

**⇒ Today we classify the whole set TWICE and then dedup it.** The mechanical, free, set-reducing operation runs **dead last**, after the two most expensive passes. **Every ordering principle says the opposite.**

#### The rule is NOT "cheapest first" — it is cost per unit of SAFE rejection

**Stage order is decided by cost per unit of SAFE rejection — never by raw cost.** The formal result (research, `docs/research/2026-09-23-pipeline-stage-ordering/`):

> for a stage 1 with cost `c₁` and rejection rate `r₁`, and a stage 2 with `c₂`/`r₂` — **`1` before `2` ⟺ `c₁/r₁ < c₂/r₂`.**

**"Cheapest first" is the special case `r₁ ≥ r₂`** — a *sufficient* condition, not a necessary one. **A near-free filter with near-zero rejection sorts LAST under cost ordering and FIRST under the correct rule.**

**Two further rules outrank cost entirely:**

1. **Losslessness outranks cost** — a stage that discards nothing can run anywhere; and
2. ⛔ **A stage runs behind an audit gate, and its admissible actions are `KEEP` / `NOOP` / `DISCARD` / `MERGE`** — **merged only when nothing distinguishing is lost** (§16.4). **Near-duplicate claims are merged with Jev as arbiter (O4), on a high bar: a merge never crosses a difference in a number, a name, a negation, or a condition, and both sides' evidence survives.**

**⚠️ Recall compounds MULTIPLICATIVELY.** Two stages that each keep 95% silently lose ~10% of the durable set. **A change that discards claims is therefore measured for what it discarded** — `STORAGE-ARCHITECTURE.md` §15 lists **M6 — *"How many durable claims did we keep?"***. **This is a quality check, not a volume gate: there is no volume target, no recall floor and no node budget anywhere in this document** (§16.3 **O2**). The objective is **great recall and reasoning at an affordable cost**, reached by **manual, step-by-step review** until a calibration set exists. **The arithmetic above is a fact about composing stages, not a floor this pipeline owes** — ⚠️ **do not cite it to demand a retention floor, a node budget, or a reduction ratio.**

#### ⭐ THE BIGGEST LEVER IS MISSING FROM THIS DIAGRAM ENTIRELY — and it runs BEFORE S1

A four-way audit of the write path found **FOUR distinct dedups at FOUR different keys**, and the earliest one does not exist:

| id | key | where | exists? |
|---|---|---|---|
| **D-a** | **source** | **URL only** | ⚠️ **URL-keyed only** — a content-hash is a provenance anchor (`#4005`), **not a dedup key** |
| **D-b** | **within-batch exact** | `execute_embed` (`extractor_v2.py:3967-3969`) — the `_norm(content)`-keyed `point_ids` map, first wins | ✅ <!-- v4-mechanism: exact_dedup state: implemented --> |
| **D-c** | **vs store, raw content hash** | `sdk.py:12412` (`_find_point_by_content`; the shared predicate builder is `_dedup_match_clauses`, `:12364`) | ✅ |
| **D-d** | **semantic** | `classify_consolidation` (`extractor_v2.py:3021+`) — the paraphrase NOOP arm | ✅ <!-- v4-mechanism: against_priors_consolidation state: implemented --> |

**⇒ `S0a NORMALIZE + HASH → S0b SOURCE DEDUP` is the largest single volume lever available, and it is mechanical and free: `tortoise/source_identity.py` is its implementation (`normalize_source_url`, `resolve_source_key`), wired in `projection/entities.py` and acceptance-tested by `tests/test_source_dedup_registration.py`, which is why `config/v4-mechanisms.yml` carries it as `implemented`.** Catching the same conversation firehose twice is a **whole-narrative** save, not a claim-level one. What stays absent is narrower than the step: **D-a above is still URL-keyed**, and resolving a spelling to the `:Source` that owns its identity is not hashing the narrative. <!-- v4-mechanism: source_dedup state: implemented -->

#### The five correctness constraints

| # | constraint | consequence |
|---|---|---|
| 1 | **Exact dedup needs NOTHING** — identical text is identical, entity-free | **may run first** |
| 2 | **Non-exact (paraphrase) dedup needs semantic judgement** — and "dedup" here means **FUSION** (a candidate folded into another), never *detection* | **the FUSION only after the gate** — never before it. `S2.2a-β` **detects and refuses; it fuses nothing**, so its position above the gate is not a tension with this row |
| 3 | **The mechanical half of the gate needs NO graph** | **may precede S3** |
| 4 | ✅ **CLASSIFY needs no graph; the LOOKUP needs KINDS → CLASSIFY before RESOLVE** | It cited `_derive_queries` (`extractor_v2.py:1744-1778`) — which genuinely never reads `kind`, but **builds FTS query strings only; it is not the resolution lookup.** The lookup is **`resolve_entities` (`:2755`), called at `:4926`**, which builds `ent_refs` with `{"name":…, "kind": str(e.get("kind",""))}` and hands each to **`_find_existing_entity(entities, name, kind)` (`:2705`) — whose EXACT-MATCH branch folds and compares `kind`:** `str(e.get("kind","")).strip().lower() == kind_folded`, with a bare-form fallback only when unambiguous. **So the match half DOES need kinds.** ⚠️ **And the code agrees on the order: the classify pass runs at `:4803`/`:4888`; `resolve_entities` at `:4926` — after it.** |
| 5 | **Against-priors consolidation needs the lookup** | **cannot precede S3** |

**⚠️ Constraint 2 is what fixes the near-duplicate stage's SITE — it is not merely compatible with it.** The apparent disagreement — this table putting a `MERGE` at `S2.2a-β` (*before* the gate) while §S2.2 says *"merging is S3's decision, made after the gate"* — dissolves once the operations are separated: **each has exactly one site:**

| operation | site | who |
|---|---|---|
| **DETECT** the near-duplicate pair **+ apply the §16.4 never-across guard** | **`S2.2a-β`** | mechanical <!-- v4-mechanism: near_dup_detect state: implemented --> |
| record the **VERDICT** `MERGE` for a pair that survives the guard | **`S2.2b VET`** | the Jev arbiter (`vet_gate.py:OUTCOMES`) |
| execute the **FUSION** — the union of both sides' attachments | **`S3`** | the resolve/consolidation tail, after the gate |

The fusion cannot precede the gate: §S2.2 reserves new-vs-existing to S3, and the against-store leg needs the lookup anyway (constraint 5) — the fusion needs a *destination*, which only S3 has. **So no operation has two homes, and no passage in this document places a fusion before the gate.** ⚠️ **The flagged verdict's consumer is #5006's union-merge.** `tortoise/near_dup_merge.py` is the FUSION — `classify_consolidation` returns `MERGE` for a candidate S2.2b recorded a `MERGE` verdict for, `extract_session_v2` routes that verdict set from the VET stats into the consolidation pass, and the write path unions both sides' attachments onto the ONE survivor. The mechanical ladder (the never-across guard, the ~0.95 bar, a readable union) is re-checked inside the mechanism, so the verdict is necessary and never sufficient; with no arbiter injected there are no verdicts and the mechanism is inert, exactly like VET itself.

**⚠️ Constraint 4 stands, and it is the basis of the S2.3-before-S3 order.** **CLASSIFY runs after the gate** (so only survivors are classified) **and BEFORE S3** — because `_find_existing_entity` (`:2705`) uses `kind` as its exact-match discriminator, so a lookup that runs before classification cannot exact-match and degrades to the ambiguous bare-form path. ⚠️ **Moving it after S3 manufactures duplicates — the exact failure this document exists to prevent.**

#### The resulting order

```text
S0a NORMALIZE + HASH   mechanical, free                                      ← NEW
S0b SOURCE DEDUP       URL-keyed today; the biggest missing lever           ← NEW
S1  NARRATE
S2.1 EXTRACT
S2.2a-α EXACT DEDUP    within-batch + against-store                          ← NEW (store half)
S2.2a-β NEAR-DUP       DETECT + apply the §16.4 never-across guard — NO FUSION HERE (constraint 2)
S2.2b VET              the gate (identifier-only DISCARD) + adversarial      [Jev]
                       ↳ may record the VERDICT `MERGE` for a flagged pair; never the
                         ACTION `MERGE-INTO-EXISTING` — the fusion is S3's (§S2.2)
S2.3 CLASSIFY          kNN fast path → Jev tail, on SURVIVORS only            [kNN + Jev]
S3  RESOLVE + D-d      lookup → against-priors semantic dedup
S5  CONNECT
S6a EMBED-DECISION     per row, before its write clause is built — the second
                       question on the S2.2b rule set (G2, #5008)
S6  COMMIT
```

#### ⚠️ The against-store exact dedup has FOUR preconditions, and the fourth does not exist

1. **Key unification** — one hash, one normalization.
2. **Sound-only** — applies only where the key is provably exact.
3. **Union-merge** — merging **unions the attachments** (edges, provenance, sources).
4. ⛔ **An ATOMIC write** — `create_point(dedup=True)` is a **check-then-CREATE**, and there is **no uniqueness constraint on `Point`**. **Two concurrent writers both see "absent" and both create.** **Nothing in the current write path closes that window**; the Postgres target's single transaction does. <!-- v4-mechanism: atomic_point_write state: not-implemented -->

**⛔ AND THE MERGE RULE IS NOT NEGOTIABLE — UNION THE ATTACHMENTS; NEVER LOSE EITHER SIDE'S EVIDENCE.** Near-duplicate claims **may** be merged, arbitrated by **Jev over the claim plus the narrative and raw data**, **on a high bar and only when nothing distinguishing is lost** (→ §16.4). **A merge never crosses a difference in a number, a name, a negation or a condition, and both sides' evidence survives.**

**⚠️ Precision on the owner's "dedup first" — it SPLITS into two different operations at two different places:**

- **Source-level and within-batch exact dedup** — need no graph, **belong first.** They shrink the set for the gate, the classifier and the lookup.
- **Against-priors consolidation** (`classify_consolidation`) — needs the S3 lookup, so it **stays after S3.** Its docstring step 1 is *"NOOP (identical) — normalized content equals **a prior**"* — **a prior is a lookup result.**

**And the doc's "classify once" also fixes a double-classification**: today `:4803` and `:4888` both run, because the set is re-classified after S4's merge. **One pass, after the gate, on survivors.**

#### ✅ Jev for classification is cheap — but ⚠️ `llm_tail` is not the seam

> **STATUS (2026-10-26, `#5087`; prompts `-v2` since `#10117`): the seam now EXISTS — internal, and OFF by default.**
> `tortoise/s2_decision_seam.py` is the adapter + the two versioned prompts (`vet-v2`,
> `classify-v2` — both carrying the untrusted-data framing on every question, so an
> instruction embedded in session text has to run against an explicit trusted-side rule that it
> is **data**, which **reduces but does not remove** the ability of embedded text to steer a
> verdict: the vendor's own note says such content still "can move the answer", and §4 of
> `docs/architecture/S2-DECISION-SEAM.md` records that the clause does **not** make injection
> impossible) + the validating parser; it is consumed through the seams that already
> existed (`vet_gate.vet_candidates(arbiter=…)`, `kind_classifier.KindClassifier(tail_decider=…)`)
> and armed only by `TORTOISE_S2_DECISION_SEAM`. **What it does, what it deliberately does not
> do, and its measured cost per call: `docs/architecture/S2-DECISION-SEAM.md`.** The paragraph
> below is kept as the record of *why* the seam was needed, not as its current status.

**Classification is exactly ONE question** — a `Choice` over the supplied kinds. Measured Jev cost: **$0.000144 at 1 question** (`$0.000194` at 2q, `$0.000243` at 3q) — **~4–7× cheaper than a frontier model**, not the vendor's claimed 40–400×.

**⚠️ But the bigger saving is that most items never reach Jev at all.** The kNN + margin fast path covers the easy majority with **no model call**; only the **low-margin tail** is batched. So Jev's cost is bounded by the **tail size, not the item count**:
> **25k items/month at a ~20% tail → ~5,000 calls → ~$0.72/month.**

**And ⚠️ `llm_tail` is NOT a ready-made Jev seam.** It is a **BOOLEAN** (`kind_classifier.py:93`); the line cited as a seam is `:522`, the **offline-eval CLI disabling it**. The tail is a **hardcoded prompt + a JSON parser**, so **swapping Jev in requires an adapter, a prompt, and a parser** — it is a *small* piece of work, but it is work, not a config flag.

**⚠️ The "structural enforcement" argument is narrower than it appears.** `closed_vocab_rejects` **already exists** (the closed vocabulary is enforced), so Jev's `Choice` does not add a new guarantee the pipeline lacks — it removes a class of **malformed / off-vocabulary** emission. And a repo-wide search for `entityCues` returns **no READER** — `#1026` made the slot declarable, but nothing consumes it, so it is not an implemented classifier surface.

**⚠️ And a hard constraint this ordering must respect:** ⚠️ **`#4899`'s Phase-1 mechanical DISCARD has known false positives** — *"a bare `#\d{3,}` in a working note is discardable; **a PR number a decision turned on is not**"* ⚠️ **that quoted `#\d{3,}` is `#4899`'s *proposal*, not the predicate in the tree — the real one is `value_gate._REFERENCE = r"#\d{1,6}(?!\d)"` with a whole-residue test (§7's "ENFORCEMENT BY DEFAULT" paragraph). ⚠️ It differs in BOTH directions from the quoted pattern: `#12` and `See #12` ARE discarded, while a 7-digit run is NOT. Do not implement from this quotation** — and **only the lookup or a semantic judgment can tell them apart.** Phase 1 runs before the lookup deliberately, which is why `#4899` **gates it behind a flag, records every discard with its rule id, and requires the counterfactual to be recoverable.** **Any reordering that moves the gate earlier must preserve those three.**

### 4.3 Five requirements the step list must place

**The step list is the SHAPE, not a complete specification.** Five requirements of this architecture have no step to carry them. They are stated here so that each is placed deliberately rather than implied by the diagram.

**One carries a settled design:** G4's immutability-versus-redaction reconciliation is `§2.4.4` (R1–R4). **Two need an owner ruling:** G5's deletion/relocation half and its S4 clause — *"Which is authoritative until the migration lands?"*. **One needs a step and nothing else:** G1. **G2's step is placed in *G2's step — `S6a EMBED-DECISION`* below**, its rule set settled in `STORAGE-ARCHITECTURE.md` §12.2a; G3's step is declared in §4.4. ⚠️ **The destructive half's DESIGN question is answered below (*G5's destructive half*) — a pass may only add, never delete, and removal-from-recall is a journaled lifecycle fold alone — but the subsection authorises nothing: whether a relocation pass may ever RUN is still the owner's ruling (`#5333` task 1).**

| # | the gap | why it matters |
|---|---|---|
| **G1** → `#5064` (the span schema + passthrough exist, but **`extractor_v2.py` emits no `span_start`** (the repo's *document* extractor does — `docs_miner.py:373`)) · `#4894` (narrative) | **No step PERSISTS the narrative or the raw-fact span.** S6 is *"write entities + connections + metadata/lifecycle"*. §4.0 has no persistence purpose beyond S6. | ⛔ **D1 and §2.4 are not implementable from the steps as written.** If the narrative and the span are not written by a named step, they are not written. **Needs an explicit persistence step (or an explicit S6 sub-job).** |
| **G2** → `#5008` | **EMBEDDING-DECISION step: `S6a`** (*G2's step*, below). `STORAGE-ARCHITECTURE.md` §12.2a **decides** the `EMBED` gate exists (*"a row can be worth KEEPING but not worth EMBEDDING"*, *"the lever is what gets an embedding"*). The `EMBED` gate and the `KEEP` gate **share one rule set and enforce at TWO points** — so the second point is a named sub-step of S6, not an implicit property of the write. | Without a named home the two documents carry contradictory designs: a rule the storage doc calls its central lever, and a step list in which no step asks its question. |
| **G3** → `#8495` | **Per-step FAILURE POLICY** — **declared in §4.4:** every step states fail-open or fail-closed, the invariant that survives, and its retry/transaction boundary. | The S3 direction is the one that must be **REVERSED from the natural default**: on an unresolved lookup the candidate is `HOLD` — never asserted `new` — because the natural fail-open MANUFACTURES DUPLICATES, the exact failure S3 exists to prevent. §4.4 also gives partial S6 **one transaction boundary** (S3-attach · S5-connect · S6-commit) and the re-narrate tie-break (**keep the first unless the second is strictly better**). ⚠️ **`#5095` closed the RECORD half** — the renderer no longer says *"assume everything is new"*, the `link_before_create` record emits `found: null` for a candidate whose own query did not run, and the run reports an `identity` block. **The MINT half is now `#10997`'s**, for the operator endpoint: an unresolved identity is HELD with its edge and drained exactly once on a run whose lookup resolved — **nothing suppresses a create for an item the MODEL emitted**, and the store is opt-in, so §4.4's carry-forward is reachable only where a caller supplies one. |
| **G4** → `#8496` | ⛔ **No secret redaction + an append-only immutable raw layer = IMMORTAL SECRETS.** ✅ **RECONCILED IN `§2.4.4` (R1–R4): redact BEFORE the append; the already-persisted case is a retention erasure reusing the SAME marker; every clean-scan claim carries `(scanned, total)`; the layer keeps its Postgres home and its existing retention path.** | ⚠️ **`#4911` and `#8496` are one problem, not two** — §2.4.2 makes the raw layer **append-only by design**, so **a secret captured once is a secret retained forever.** Redaction must be designed *with* the layer, or the layer's immutability becomes a liability. **The control ships with the layer, which `G1` (`#5064`) still owes.** |
| **G5** → `#5333` · `#5059` · `#8500` (the S4 clause) | **No migration or backfill for the ~25k existing nodes.** | No backfill, no re-extraction path, and **no decision AUTHORISING a relocation pass (task 1)** — the DESIGN itself is recorded below (*G5's destructive half*): a pass may only add, a physical delete is foreclosed, and the ~4,900 `the <X>` rows stay in recall behind the status predicate (the report-only ledger is `#5059`'s; the relocating half's measured state is below). Also unaddressed: **what happens to the live S4 `merge_embed_lists` when S4 leaves the step list** — the code and the design must not disagree. |

**⚠️ And G2 is where the two documents bind:** `STORAGE-ARCHITECTURE.md` §12.2a's decided rule (*one rule set, two enforcement points*) **requires a step to hold the second point**, and `S6a` holds it. The next subsection states the step, its position, and the two premises that had to be corrected before the position could be stated truthfully.

**The five gaps and their homes:** **G1** `#5064` (the span schema + passthrough exist; what is missing is that *`extractor_v2.py` emits no `span_start`* — the repo's document extractor does (`docs_miner.py:373`), so this is scoped to the session/extraction pipeline, not the whole product) + `#4894` (narrative placement) · **G2** `#5008` (**`S6a`**, below) · **G3** `#8495` (**declared §4.4**) · **G4** `#8496` + `#4911` · **G5** `#5333` + `#5059` + `#8500` (the S4 clause). The owning epic is **`#5087`** (E1 — the selective write path) under tracking **`#5086`**; end-to-end acceptance is capstone **`#7949`**.

#### G2's step — `S6a EMBED-DECISION`: the decision, its position, and two premises corrected to place it

**The step asks ONE question and it is not `S2.2b`'s.** `S2.2b VET` asks *"should this candidate exist at all?"* (`KEEP` / `DISCARD`). `S6a` asks *"given that this row is being written, does it carry a vector?"* (`EMBED` / `NO-EMBED`). Both ask it of the **same rule set** (`embed_gate.RULE_SET is value_gate.RULES`), which is §12.2a's decision: a second, independently-derived rule set *"would be exactly the drift that criterion exists to prevent"*.

**Where it runs.** Per row, **inside `S6 COMMIT`, before that row's write clause is built** — so a `NO-EMBED` row is still written and simply carries no vector. Its implementation site is `tortoise/embeddings.py::encode_for_row`, and that is **ONE seam**, not a second enforcement point: the reads, backfills and repairs that legitimately re-encode (the re-encode tool, the batched turn writer) call the raw `encode_for_store` and are outside it, which is the boundary §12.2a asks for. The population of writers that reach the seam is `tools/dod_5090_probe.py`'s DoD row 1.

**Its position, stated as three ordering facts:**

| # | the fact | why it holds |
|---|---|---|
| 1 | **after `S2.2b VET`'s `DISCARD` is applied** | `vet_gate.apply_vet` **removes** a discarded candidate from `embed_list`, and that list is what S2.3, S3, S5 and the commit all read. So **`DISCARD` precedes `EMBED` as a consequence of the list**, not as a check inside the gate — a discard **that `apply_vet` actually applies** is never offered to `S6a`. ⚠️ *Applied*, not *verdict*: `apply_vet`'s Rule 2 **downgrades** a `DISCARD` to `KEEP` for an entity still referenced by a surviving candidate (`vet_gate.py::apply_vet`), so a downgraded candidate is kept and is therefore legitimately offered. `classify_consolidation` cannot resurrect one either: it runs inside the write pass and never re-adds a candidate to the list. |
| 2 | **no ordering constraint against `S2.3 CLASSIFY`** | G2 was first stated as *"S2.3's kNN path silently assumes every candidate is embedded"*. Measured against the tree, that is not so: `KindClassifier` encodes the **candidate's own text** (`self.encoder.encode`) and matches it against a **kind index** built from the pack's kind vocabulary (`kind_index.KindIndex.nearest`). It never reads a stored row vector, so whether a row carries one cannot change its answer, and `S6a` feeds nothing into it. |
| 3 | **before the row's write clause is built** | The seam returns the vector *before* the write is issued, so no row stores a vector the decision did not authorise, and a `NO-EMBED` verdict cannot be overtaken by a later pass that writes the same row. |

⚠️ **And not "before `execute_embed`", which is the order that reads as equivalent and is not.** `execute_embed` **issues no graph write**: it returns a `payload` dict (`extractor_v2.py::execute_embed`), and the commit that reaches `S6a` is the SDK / projection writer, *after* it returns. (Its optional `hold_store` argument can append a durable identity-hold record — `extractor_v2.py::_hold_endpoint` calls `hold_store.hold(...)` — but a hold record is not a graph row write, and the shipped call site passes no store at all.) Stating the constraint against `execute_embed` would place the step in a pass that writes no graph row — the placement the ordering is supposed to pin, asserted about the one step where it cannot be observed.

**The asymmetry between the two points, stated because it is load-bearing.** The **`VALUE`** gate is subordinate to `VET` by ARMING: its flag switches the pass on, and it runs *inside* `vet_gate.vet_candidates`, so on its own it would be inert (the `vet_enabled` local that guards that pass — its `_value_gate_enabled` term (`tortoise/extractor_v2.py::_value_gate_enabled`) — folds it in for exactly that reason). The **`EMBED`** gate is subordinate to `VET` by **PRECEDENCE**: it is read at submission, so folding it into that same `vet_enabled` local would arm nothing while appearing to arm the gate — and the precedence that actually protects it is fact 1 above, a discarded candidate being absent from the list before the seam is ever reached. Correcting the reading that both flags are "folded in" is not a re-decision; it is what the code does, and `tests/test_embed_gate_5008.py::test_the_VET_pass_folds_the_VALUE_gate_and_never_the_EMBED_gate` pins it.

**Pinned by code, not by this paragraph:** `tests/test_embed_decision_step_11086.py` drives a `DISCARD`ed candidate through the pass and through `execute_embed`'s payload, and asserts it is absent from **both** — the list every later step reads, and the payload the commit is handed. It is pinned as a **DELTA**: the same pair with the predicate's kill-switch pulled, where the candidate IS present in both, so the armed-arm assertion measures the removal rather than the input. It also asserts the `VET`-wins direction with both gates armed.

**Its failure policy is S6's, not a ninth declaration.** `S6a` is a sub-step of `S6 COMMIT`, so §4.4's `S6 COMMIT` row governs the transaction boundary it sits inside, and its own direction is **FAIL-OPEN**: no verdict — an unparseable candidate, a predicate that raises, an identifier merely *present* inside a longer claim — is an `EMBED`. The two mistakes are not symmetric. A false `NO-EMBED` silently destroys recall for a row nobody can re-find, while a false `EMBED` costs bytes and is recoverable from the text the node still carries, so this step may never be the reason a vector is missing. That is `tortoise/embed_gate.py::embedding_verdict`'s contract, stated here rather than left to a reader to infer from the module.

#### G5's destructive half — the target and the journaling arm EXIST; the bar, the arbiter and the RULING do not (`#5333`)

⚠️ **Revision pin for this subsection: every `file.py:NNNN` number below is read at `653980445`** (the `origin/main` revision it was re-measured against on 2026-10-22), **not** at the document's default pin `c79ba1cf2` — five of these files are shorter at the default pin (`sdk.py`, `assembly.py`, `projection/entities.py`, `projection/__init__.py`, `commit_ops.py`) and `tortoise/definite_description_backfill.py` does not exist there at all, so a reader who applies the header's default would resolve no citation on every line. The symbols are authoritative; the numbers are a convenience (`tests/test_extractor_v4_failure_policy.py::test_the_relocation_subsection_citations_name_their_revision_pin`).

**No destructive or relocating disposition exists, and none is authorised.** `tortoise/definite_description_backfill.py` keeps `DESTRUCTIVE_DISPOSITIONS` empty by construction (`:192`) and every record it emits carries `destructive=False`. What follows is the measured state a ruling would be made against; **it authorises nothing**.

**The target, if a relocation is ever authorised, is `Object.status='archived'`.** `archived` is already a member of the canonical Object terminal vocabulary (`commit_ops.OBJECT_TERMINAL_STATUSES`, `commit_ops.py:51`), aliased as the read-exclusion set (`_RECALL_OBJECT_EXCLUDED_STATUS`, `:59`), and the ONE status dispatcher routes the Object-family read legs through it (`search_engine._status_vocab_for`, `search_engine.py:93–133`). A relocated row therefore leaves recall and the search legs with **no new vocabulary and no new predicate**. **No purpose-built producer writes `archived`, and no `_ALLOWED_TRANSITIONS` transition does** — the writers that exist are the generic unvalidated `update_entity` path (below) and a **pack-declared status fold**, because a fold's status is validated against the DECLARING kind's vocabulary rather than a shared one: `_ALLOWED_TRANSITIONS` (`sdk.py:2942`) declares it terminal and the `:2955` entry records *"terminal (reserved — no v1 write path)"* — but **nothing consults that map**. It is a declarative **Point**-lifecycle spec and says so itself (*"NOT consulted by `update_point` per-call"*); `grep -rn _ALLOWED_TRANSITIONS tortoise/` returns the definition and no production read, so it can neither establish that the **Object** family has no writer nor bound what a write does. ⚠️ **The generic unvalidated write path CAN already place `archived` on an `:Object`** (see the write-side paragraph below), so **the number of existing `archived` Objects is UNMEASURED**, and enumerating it is a precondition of any relocation pass: rows may already sit at `archived`, placed by hand through `tortoise_update_entity` — **or by a pack-declared status fold** — silently out of recall. The no-new-vocabulary conclusion survives unchanged — the value and its routing already exist; what does not exist is any guarantee that nothing is already using them.

**The journaling story exists, and the *"the write must be a fold, not a raw `SET`"* precondition is met.** Two independent arms:

| arm | what it expresses | where |
|---|---|---|
| **generic status write** — the arm a relocation would use | `update_entity(<object>, status=…)` journals `EntityMutated`; the op classifier labels a status write `restatus` and the record carries the **applied** map. Replay is the **same** `SET n += $s` the live write ran, on the canonical label — Objects included | producer `sdk.py:33029` (`classify_entity_mutation_op`); fold `projection/__init__.py:18664` over `_ENTITY_MUTATION_STATE_OPS` (`:7383`) |
| **`ObjectSuperseded`** — the successor form, which does **NOT** fit a relocation | live CAS fold + rebuild pass-1b sweep | `projection/entities.py:4059`; `projection/__init__.py:14280`; producer `commit_ops.apply_supersessions` |

⚠️ **The second arm is unusable here by construction:** `apply_supersessions` only folds a live target onto a **visible, distinct successor** (`commit_ops.py:1408`), and a bare description has none — *"supersede it with nothing"* is not expressible. The relocation target is therefore `archived` **by generic status write**, never by `ObjectSuperseded`.

⚠️ **The raw-`SET` hazard is closed; one half of the write-side guard is still missing.** The unjournaled generic status path was audited and its pre-existing rows handled (`#3304`, closed 2026-10-06). There is **no Object-status vocabulary guard on the write side** — `_update_entity` still issues an unvalidated `SET n += $props` for an `:Object`, and the guard that would reject an undefined value (`OBJECT_STATUS_VALUES`) **is not on main** (`assembly.py:4104–4111`; the symbol appears only in comments). **The values it accepts are therefore not a closed set** — the `outdated` example on that line is one reachable value among many, and `archived` is reachable the same way (`update_entity(<object>, status="archived")` reaches the store with no vocabulary check in between), which is why the already-`archived` population above is unmeasured rather than zero. **It also cannot simply be a closed set**: pack-declared lifecycles write their own words (`_fold_object_status`, `projection/entities.py:4618`; `status_folds.BUILTIN_CONNECTOR_FOLDS`). A relocation pass must therefore validate its **own** target value — a status outside the terminal vocabulary is not terminal, so the row silently stays in recall while the pass reports success.

**The evidence bar and the arbiter (proposed).** A row is eligible only when it is **unreferenced** (`ref_count == 0`; an unreadable count is never zero — `dd.ref_count.unreadable`) **and** positively classified as a definite description (`dd.reclassify.bare_definite`) **and** arbitrated by **Jev over the row plus its narrative span** — §16.4 O4's arbiter. **Never a name pattern and never a bare reference count:** measured on the live graph 2026-09-25 (`#5059`), **≈52% of the `the <X>` rows are load-bearing** (2,570 of 4,901), so a bare name-pattern delete carries **≈48% precision on an irreversible delete** — worse than a coin flip — and 11 of the 12 most-referenced Objects match a drop pattern.

⚠️ **The bar is not buildable today, and that is a second gate behind the ruling.** The Jev seam did not exist when this paragraph was written: the classifier's `llm_tail` is a hardcoded prompt and a JSON parser, so swapping Jev in *"requires an adapter, a prompt, and a parser"* (§4.2). **That adapter now EXISTS (`#5087`, `tortoise/s2_decision_seam.py`, OFF by default — `docs/architecture/S2-DECISION-SEAM.md`)**, so what authorising this pass still buys is the *evidence bar* (unreferenced + positively bare-definite + arbitrated over the row **and its narrative span**) and the relocation itself — **not** the seam. The ruling this paragraph sits behind is unchanged and is still the owner's.

**What a user is told (proposed).** Reported, never silent: the row keeps its id, the report names the rule id and the statement the row was judged by, and the reversal is a journaled status write back to the value the journal already holds — the `#4899` counterfactual contract applied to a row that already exists. A row that cannot be positively classified stays `REPORT` and is never relocated — the **§4.4** per-step FAILURE POLICY form (`#8495`) of the S3 `HOLD` direction, applied here to the relocation pass. It is stated as a **proposal**: §4.4 declares the policy for the **eight** pipeline steps (§4.1's canonical set), so this is the same rule carried over, **not a ninth declaration**.

**The design decision (recorded 2026-10-26; `#5333`'s design half — the authorisation, task 1, remains the owner's, and this paragraph authorises nothing).** *What a pass may do:* **add** — and, before S6 COMMIT, drop its own candidates (S2.2 VET's `DISCARD` is selection: the row was never written) — and execute an authorised `MERGE` (D12/O4), whose union preserves both sides' evidence and attachments. *What a pass may never do:* after COMMIT, **no pass may remove a committed row from the graph, archive it as a storage action, or withdraw it from recall — except through the one journaled fold below, or through the D7 `ObjectSuperseded` arm where a visible successor exists — and no field of a committed row may be written except by an appended lifecycle Event** (D7: `ObjectSuperseded` where a visible successor exists, a journaled `restatus` where one does not — a raw `SET` onto `Object.status` is `#3304`'s defect class, unprojectable from the journal). **Where no visible successor exists, the ONLY permitted form of removal-from-recall is that fold**: a journaled `restatus` to `archived` — the `Object.status='archived'` target recorded above (*The target, if a relocation is ever authorised*) — which appends a lifecycle Event and keeps the row's id. (Where a visible successor **does** exist, `ObjectSuperseded` — permitted above — is the form, and its `status='superseded'` is removal-from-recall too: `OBJECT_TERMINAL_STATUSES` **is** the recall-exclusion set, `commit_ops.py:51`/`:59`. `ONLY` is scoped to the successorless case and never denies the D7 arm.) Until a relocation pass is authorised, **no PASS writes it** — the already-`archived` population recorded above is the exception that proves the scope: it **may have been** placed by hand through the generic unvalidated write path, **or by a pack-declared status fold**, and enumerating it stays a precondition of any relocation — so nothing here licenses a reading that `archived` is unused today. **A physical delete is foreclosed to every pass**: the recorded rule stands — *nothing is ever deleted on supersession; the only deletion trigger is GDPR / retention* (**§2.4.2** row 3 — that subsection's rule for the **raw-fact** layer, applied here to committed graph rows) — and Option C is closed on the recorded recommendation (*never*) and the measurement (≈48% precision on an irreversible delete; the 2026-10-24 capstone found real subject matter — dated constraints, design decisions — inside the class). *What must remain recoverable:* **the fold's event stream is the reconstruction source** (STORAGE-ARCHITECTURE §2/§9 — Postgres the system of record, the journal the rebuild input), so a relocation, if one is ever authorised, must be a journaled `EntityMutated` (`restatus`) Event on the canonical label: the row keeps its id, its prior status stays recoverable from the journal, the judging rule id and statement stay in the report, and reversal is a journaled status write back. **The flag fold already satisfies this invariant** — relocation would buy only the read-predicate cost (*"no query pays for your cleanup"*), which is why it stays deferred until recall cost is measured. *The cost of this decision, named:* every read carries the status predicate (the flag form's price); the ~4,901 `the <X>` rows stay in recall and keep distorting every count of the Object layer; and E1's share is moved by no non-destructive mechanism (measured 2026-10-24: the shipped gate drops 0 of 34 bare-definite names; the selection passes ON raise the share to 0.54 — cited to the `#7949` capstone pass as recorded on `#5333`, whose probe is `#9889`'s to land, so the figures are auditable there and are NOT re-derivable from a committed artifact; the committed measurement is of a different corpus — `docs/00_index.md`'s 15-of-25 / 0.0 pp leg — and does not contradict these) — the price of refusing an irreversible coin-flip. *Foreclosed:* Option C outright; Option D stays `#4894`'s (the store is not rebuilt from raw to fix this class); a closed write-side Object-status vocabulary (pack lifecycles own their words).

### S3 — RESOLVE (find what exists · disambiguate · attach)

**In:** the surviving candidates **+ the narrative (as context)**. **Out:** every candidate either **resolved to an existing entity** or **marked new**, with the new context attached.
> *"when we find duplicates we then attach the right context to them so e.g. if in S1 we have a narrative connecting to Object A and we find Object A is already in the graph, then we connect the new things to Object A via edges/operators."*

**S3 owns the lookup** (§4.2). The lookup is keyed on **candidates**, and candidates only exist **after** extraction.

**Three things happen, in this order:**

1. **Look up** what already exists for these candidates. *(Mechanical.)*
2. **Disambiguate** — for each candidate, is it the existing entity or a new one? **The narrative is the context that answers this.** *"Is this the same Object A?"* cannot be answered from a bare string — only from the surrounding prose. *(Jev — a `Choice` over the supplied candidates.)*
3. **Attach** — for a resolved candidate, connect the new context to the existing entity.

⚠️ **The narrative is an INPUT to this step.** S3's inputs must include the disambiguation context, not only *"candidates + what exists"* — that context is what the step needs most. **Owner, 2026-09-23:** *"we can use the narrative to disambiguate when uncertain."*

**Not find-and-report — find-and-attach.** Mechanical unless there is genuine ambiguity. **S3 is the single authority on whether a candidate is new or existing** (§4.2).

### S5 — CONNECT (two separate jobs, done in order)

**S5 does two things that look similar and are not.** Both only ever **decide** — nothing is written here. The writing is S6.

**Job 1 — wire up this batch's own story.** The narrative says *"Object A changed because of Points X, Y, Z, and it happened through Event W."* S5 checks that the pieces S2.1 pulled out actually agree with that sentence, and creates the connections **among the new things**: A ↔ W, W ↔ X/Y/Z. These are relationships **inside what we just read**.

**Job 2 — compare this batch against what is already stored.** For each new item: is it already in the graph? Does it **change the state** of something (D7 — a lifecycle change)? Does it **contradict** a belief the graph already holds? These are relationships **between the new and the old**.

**Why they are two steps and not one:** they fail in completely different ways. Job 1 fails as *"the batch doesn't hang together"* — a quality problem in the reading. Job 2 fails as *"we just duplicated something, or silently contradicted a live belief"* — a correctness problem in the graph. **A single step doing both would have to report both kinds of failure through one channel**, and they need different handling: Job 1 is a re-read, Job 2 is an operator (`IMPL`/`NAND`) or a supersession.

**Division of labour: the decision model decides, code writes.** Asking a model to emit a graph write directly is how you get an untyped or half-formed edge. **The model answers a narrow question; code turns the answer into a write.** *(This is D6 as ruled — the model proposes both sets.)*

### S6 — COMMIT

New entities + connections + **metadata**. The metadata includes **lifecycle events, which are appended — never written onto the Object** (§5).
**⭐ And every committed node carries its link to the `Source` span** (§2.4.1) — a node that cannot walk up to its source cannot be served as evidence, which is the measured half of the fourth layer (**verbatim beats derived by 15.9 / 22.0 pts**).

### 4.4 Per-step FAILURE POLICY (G3)

**Why this section exists.** The failure policy of every step must be stated: **an unstated default is not a neutral default — for S3 it is the exact defect the step exists to prevent.** This section **declares the policy for every step**, as a **specification to implement, not a description of what runs** (see the note closing this section, below).

**Three invariants survive every failure.** A step's policy is a specialisation of these, and a policy that violates one is wrong whatever it costs:

| # | the invariant | what a failure may do |
|---|---|---|
| **I1** | **An unresolved identity is NEVER asserted `new`.** Identity is S3's, and a duplicate is the one thing S3 exists to prevent. | **HOLD** the candidate — never mint it. |
| **I2** | **A failure never silently drops memory; it holds the item or records the loss.** A wrong keep is noise; a wrong drop is memory loss (S2.2's own rule, §7). | **HOLD**, or degrade **loudly** into the run's census. Silent absence is never an outcome. |
| **I3** | **A committed run is ALL-OR-NOTHING, and the journal is the truth** (`derived = replay(journal)` — `#4240` at the lifecycle layer, `#4716` at the operator layer). | Leave the journal, and therefore the derived graph, unchanged. A partial run is not an observable state. |

**`HOLD` is a run-level disposition, not a new VET outcome.** It is the set of candidates a run did **not** commit and did **not** discard — carried forward for a later run against the same graph. ⚠️ **It is deliberately NOT a member of `vet_gate.OUTCOMES`** (`KEEP` / `NOOP` / `DISCARD` / `MERGE`): the VET vocabulary is unchanged: a `HOLD` is not a judgment about validity — it is the absence of one.

**The policy, per step:**

| step | on failure | why (the failure it prevents) | retry / transaction boundary |
|---|---|---|---|
| **S0 READ** *(mechanical)* | **FAIL-CLOSED** — end the run before any model call or write. | §2.4.1: every committed node must walk to its `Source`; a node from a source whose mapping did not parse has no honest anchor. | Bounded retry for a **transient** I/O (fetch) failure only. A parse/mapping failure is an INPUT error — surfaced, never retried. |
| **S1 NARRATE** *(model)* | **FAIL-OPEN onto the RAW** — retry once, then proceed `narrative_absent` with the raw as the coverage reference, recorded `degraded`. | D3-Option 4: the narrative is the **focus**, the raw is the **source** (§S2.1: the live call already passes `edus`). The narrative is not the transport of record, so its failure must not become the run's failure (I2). | One retry. A run NEVER proceeds with neither a narrative nor the raw; S2.2's coverage check reads whichever artifact was actually available. |
| **S2.1 EXTRACT** *(model, per chunk)* | **FAIL-CLOSED per chunk, HOLD at the run level** — a chunk failing after its retry contributes no items and joins the run's `HOLD` set. | A failed chunk must never read as *"that chunk had nothing"* (I2). It is a **coverage gap**, and S2.2 Level 2 — not silence — is what makes it visible. | One re-prompt (the `_PARSE_RETRIES = 1` shape). Nothing is written for a held chunk. |
| **S2.2 VET** *(Jev)* | **FAIL-OPEN** — a failed Level-1 question returns `KEEP`; a failed Level-2 verdict triggers **no** re-narrate and the batch proceeds. | *"a wrong keep is noise; a wrong drop is memory loss"* (§S2.2). | A VET failure may NEVER produce `DISCARD`. The missing verdict is recorded, so the gate's coverage is inspectable. |
| **S2.2b RE-NARRATE** *(model, at most once)* | **FAIL-OPEN onto the FIRST ARM** — a retry that raises after the second arm has already re-bound the pipeline's state restores **every** binding it set and the first arm's capture ships unchanged. | A partial restore is worse than the raise: it would carry the second arm's list beside the first arm's narrative — a run that reports success and is internally inconsistent. The first arm's capture is complete by construction (it returned), so it is the only state safe to leave behind. | No retry of the retry (§S2.2: *once, then accept*). The failure is recorded on `stats["renarrate"]` (`fired: False`, `trigger`, `retry_failed`, both arms' metrics), on `errors`, and in `error_census` — never only in `warnings`, or the run reads clean. ⛔ **The union does NOT happen on this path**: the second arm's items are dropped rather than merged (`items`/`merged`/`decisions` are absent from the record), which is why F3's population is selected by `retry_failed` and not by `fired` alone. |
| **S2.3 CLASSIFY** *(Jev + kNN)* | **FAIL-OPEN, never raises** — the candidate keeps `unclassified`; the run records it in its census. | A wrong kind is worse than no kind: `_find_existing_entity` uses `kind` as its exact-match discriminator, and `_select_pack_kinds` plus the chain enforcer key on kinds — so a guessed kind corrupts BOTH resolution and enforcement silently, while `unclassified` is honest. | Classification is OFF the write path: an `unclassified` candidate still resolves (on the bare-form path, §4.2 constraint 4) and still commits; the degraded exact-match coverage is recorded, because §4.2 warns that an unmatched kind is where duplicates are manufactured. |
| **S3 RESOLVE** — the lookup *(mechanical)* | **FAIL-CLOSED on the identity assertion.** A degraded lookup returns *no priors* and must NEVER be read as *"these are new"*; the affected candidates are `HOLD`. | ⛔ **This is the whole of G3.** The natural fail-open marks every candidate `new` and **MANUFACTURES DUPLICATES** — the exact failure S3 exists to prevent. ✅ **The renderer half is closed (`#5095`): it no longer invites `new`** — it states that the lookup did not run (or did not complete), that identity is UNRESOLVED rather than absent, and that no `link_before_create` entry may report a search that did not happen. ⛔ **The MINT half is open, and the blanket create gate is still not enforced**: a Point or Entity the MODEL itself asserted is still created on an unresolved lookup (the S5-Job-2 half, `#10998`), so §4.4's `HOLD` is a disposition for the mint-before-wire path below, not a blanket create gate — I2's blanket suppression is deliberately NOT this mechanism's shape. That path is `#10997`'s: `_mint_endpoint` is licensed by a lookup that RESOLVED, and on `unresolved`/`partial` the ref is held with its edge and drained exactly once, so no `new` is asserted about a check that did not happen. | Bounded reconnect, then `HOLD`. A later pass re-runs the lookup; the candidates are neither minted nor discarded. |
| **S3 RESOLVE** — the disambiguation *(Jev `Choice`)* | **FAIL-CLOSED on `new`.** A failed disambiguation `HOLD`s the candidate — unless the lookup returned exactly ONE exact-key match, which attaches mechanically with no model. | An unvalidated attach fuses two distinct entities' evidence, violating §4.2's union-merge rule (*"never lose either side's evidence"*). Neither direction is safe without a decision, so the candidate waits. | The `Choice` is re-raisable. `HOLD`, never `new`. |
| **S3 RESOLVE** — the attach / fusion | **FAIL-CLOSED** — the run's commit fails as a whole (I3). | The fusion's write is S3's **action** but not a separate **write site**: §4.2's three-sites rule (`#5092`) says which step *decides* each operation, not that each writes in its own transaction. | Applied inside the single commit below. |
| **S5 CONNECT** — Job 1 (this batch's own story) | **FAIL-OPEN.** The nodes still commit; the missing intra-batch edges are recorded `connections_incomplete`. | Job 1's failure is *"the batch doesn't hang together"* — a **quality** problem in the reading, and the edges are re-derivable from the same narrative. | Re-derivable on a later pass; the record, not a silent hole, is what satisfies I2. |
| **S5 CONNECT** — Job 2 (the batch **against the stored graph**) | **FAIL-CLOSED on the write of the affected item.** An item whose duplicate / lifecycle / contradiction check did not complete is `HOLD`, not committed. | §S5's own words: *"we just duplicated something, or silently contradicted a live belief — a correctness problem in the graph."* Committing an unchecked item IS that failure. | Re-runnable against the same graph; the item re-enters on the next run. **No item is committed whose against-graph check did not complete.** |
| **S6 COMMIT** *(mechanical)* | **FAIL-CLOSED, ATOMIC.** One run = one journalled commit; a failure leaves the journal — and the derived graph — unchanged. | I3. A half-applied run is a duplicate/contradiction risk at the STEP level — the same class `#4240` (the missing journal) and `#4716` (the mint bypassing E7) address at the LAYER level. | **Transaction boundary: S3-attach/fusion · S5-connections · S6-entities+metadata are ONE transaction** — they are the writes of one run. Retry is an **idempotent re-submission of the identical payload**, so a retry can never double-write. |

**Sub-steps inherit their parent's direction, with the exceptions named.** `S0a NORMALIZE+HASH` fails **closed** as S0 (an unhashable source is an input error — no hash, no anchor); `S0b SOURCE DEDUP` fails **open**, because a missed source duplicate is absorbed by the `:Source` `url` MERGE (a re-fetch is a version, not a second node) — a cost, not a correctness failure. `S2.2a-α EXACT DEDUP` and `S2.2a-β NEAR-DUP DETECT` fail **open**: a missed exact match is caught by S3's own lookup, and a missed near-duplicate is a *wrong-keep* class failure (noise), never a fusion, because **nothing is fused before the gate** (§4.2 constraint 2). Fail-closed on detection would refuse a run for a problem detection was never going to solve.

**The re-narrate loop — bounded to once, with a deterministic tie-break.**

§S2.2 already bounds the loop (*"once, then accept"*) and names the trigger (a poor batch verdict, e.g. a keep-rate below a threshold). **The tie-break: which narrative is kept when the second is not better.** The rule:

> **Keep the FIRST narrative unless the second is STRICTLY better on the SAME metric that triggered the retry** — the keep-rate, computed the same way. **A tie keeps the FIRST.** Both rates are recorded, so the choice is inspectable.

**Why this direction:** the retry exists because the first narrative produced a poor *yield*, and *"a retry, not a loop"* means the replacement must **earn** acceptance. A tie is not an improvement, and a rule that kept the second on a tie would let the accepted keep-rate fall on a coin flip. **Invariant:** the metric and the comparison are fixed **before** the retry fires, and a re-narrate can never lower the accepted keep-rate.

**The trigger, and the ONE knob that arms it.** `extractor_v2._renarrate_trigger` reads three signals, in order: `coverage_suspect` on the S2 batch (*prose in, zero candidates out* — armed by construction and, **crucially, refused when S2 raised**, because a failed call's empty list is byte-identical to a genuinely empty one and is a transport failure the stage already retried); a batch-level `RENARRATE` from an injected arbiter (the token `vet_gate.BATCH_OUTCOMES` has always carried); and a keep-rate below a hand-set threshold. That third arm is armed **only** by **`TORTOISE_RENARRATE_KEEP_RATE`** — accepted when `0.0 < v <= 1.0`, and any other spelling (unset, empty, `0`, `-1`, `1.5`, `banana`) leaves it **DISARMED**. The knob is named here because §4.2 makes that threshold a HUMAN calibration over tens of real sessions, and a switch no document declares cannot be set by the operator it is written for. What a retried run decided rides `stats["renarrate"]`: `trigger`, the pick (`items` / `narrative`), `merged` (the union's accounting — `kept_from_first` / `added_by_second` / `corrected_by_second`), `decisions` (each arm's per-item verdicts, which have no other home — see the DISCARD half of §4.4's union rule below), `emphasis`, both arms' `chunks`, and each arm's `candidates` / `discarded` / `kept` / `keep_rate` / `coverage_suspect` / `s2_failed` — ⛔ where `coverage_suspect` is **three-state**: `True`/`False` are `check_batch`'s verdict on a call that answered, and **`None` means there is no verdict because S2 raised**. A reader counting `coverage_suspect is False` (F3, below) therefore cannot count a batch that never existed — the fail-closed direction for a count whose `0` is a deletion's green light.

**What the run COMMITS is the union of both arms' items — the tie-break decides the NARRATIVE only.** The shipped list is `merge_embed_lists` over the two arms (the second wins an identity-key collision, the first arm's items are preserved), so both arms' extractions reach the graph. ⛔ **This holds on a retry that COMPLETES; a FAIL-OPEN retry ships the first arm's capture alone** — the pick is discarded rather than published, so no `items`/`merged`/`decisions` ride the record and the second arm's extractions do **not** reach the graph (see the `S2.2b` row of the per-step policy above). Only ONE narrative is recorded, because exactly one `story`/`story_arc` reaches S4's prompt and `execute_embed`; which one is what the tie-break above settles. The union is re-vetted wholesale before it ships, so a first-arm **MERGE** flag on an item that SHIPS is re-derived rather than dropped — but a first-arm **DISCARD** is not: the item it removed is absent from the union, so the re-vet pass never judges it, and its verdict rides `stats["renarrate"]["decisions"]`. ⛔ Do not read the re-vet as a substitute for those verdicts; that is the `#5005` silent-discard reopened on the retried path. On the arm armed by construction (`coverage_suspect`) the first list is empty and the union IS the second arm's list; the merge is what keeps the disarmed keep-rate arm from silently dropping the losing narrative's items.

> **OVERRIDES:** §4.4's earlier *"the losing arm's extraction is discarded **with** its narrative"* — **retired by the owner's ruling of 2026-11-08 on `#11002`**: *"the retry must union both attempts with the same identity-key collision rules the existing merge uses — that is what makes it a successor rather than a replacement."* E4's merges-not-replaces property (`#1536`) must survive into the successor, which is why `run_s4`'s removal is gated on a successor that **preserves** that property — **a code change, tracked at `#11534`, NOT a measurement**: F3 was run on 2026-11-08 and it FIRES, so the measurement half is discharged and `run_s4` stays (see the F3 paragraph immediately below for the numbers and the two clauses that remain inferred).

**The removal's falsifier — F3, defined here because two places cite it by that name.**
`run_s4`'s removal (the `#8500` migration deliverable, executed under `#11002`) was gated on **one**
measurement — an eval-lane run, not a code change. **WHAT THAT INSTRUMENT ACTUALLY MEASURES, and
what it does not** — stated because the gap matters for an irreversible deletion. The two committed
`docs/evidence/1789-s4-reeemit-tax/` artifacts (7 real sessions each) record, per arm and per
session, `s2_items` / `s4_items` / `merged_items` / `added_by_s4` / `corrected_by_s4` and the
added/corrected item lists. They record **no `check_batch` block and no `stats["renarrate"]`**, and
that is structural rather than an omission: the instrument (`tools/s4_reeemit_tax.py`) replays
`run_s4` directly over each session's recorded S2 list and never calls `extract_session_v2`, so the
pipeline's coverage flag and its retry counter do not exist on those rows. So of F3's three clauses
**one is measured and two are not:** `added_by_s4 > 0` is measured — **4/7 primary, 3/7 re-run,
5/7 across the union on the `legacy` arm** (`TORTOISE_S4_DELTA=0`), and **2/7 on either run of the
`delta` arm**, which is DEFAULT-ON — while the flag clause is an INFERENCE from the artifacts'
own S2 item counts (29–49 in every one of the 28 session-runs) and the retry clause is a property
of the instrument's construction (no pipeline run ⇒ no retry). ⚠️ The inference is not free: the
artifact stores COUNTS, and `check_batch`'s `candidates` applies a stricter predicate (a `Mapping`
item with non-empty `name or content`), so the counts do not by themselves pin the flag. Both
readings being above zero is what the decision rests on, so `run_s4` **stays** and the removal
moved to **#11534** — where the pipeline-level half (`stats["renarrate"]["first"]
["coverage_suspect"]` and `fired` over a real `extract_session_v2` run) is recorded as the
measurement a successor owes. ⛔ **Name the arm whenever this number is quoted:** the two arms are
DIFFERENT CONTRACTS (`legacy` re-emits, `delta` emits only what is missing), so their `added_by_s4`
counts are not the same quantity — the evidence README compares them as evidence about the gap
channel (12/9 legacy vs 6/6 delta gap additions) and states that **neither arm's sensitivity to the
real geometry is measured, so the token-saving RATIO is undetermined in both signs**.

On the frozen corpus, count the sessions
where `_s4_merge_stats`'s `added_by_s4 > 0` **while the FIRST (pre-retry) arm's `coverage_suspect` is
False** — **restricted to sessions where the retry did not fire**
(`stats["renarrate"]["fired"]` is `False`). ⛔ **`fired: False` describes what shipped, not how
many arms ran.** A retry whose tail raised publishes `fired: False` as well, with the arm-choosing
`trigger` and a `retry_failed` string beside it, and its first arm's capture restored binding by
binding — so such a run belongs in this population, but must be selected deliberately, by
`retry_failed`, rather than by the flag alone. ⛔ **Whose flag, spelled out, because a retry makes it
two arms.** A retried run reaches `check_batch` twice, and the SECOND arm is an independent S2
run whose flag can be `True` again (measured: an empty second yield flags `coverage_suspect=True`
on both arms) — so counting against the pipeline's published `result["vet"]["s2"]`, which is the
SECOND arm's block, attributes the wrong arm's verdict to the session. What makes the retry
unreachable for this count is the `fired is False` RESTRICTION, not any property of the second
arm's flag. ⛔ **But the record does NOT carry it on MOST of this population — which is why this clause is
still an INFERENCE here.** `stats["renarrate"]` is `{"fired": False}` ALONE when the retry never
started (`extractor_v2.py`'s `renarrate_stats`, pinned in `tests/test_selection_step.py`), so a
no-trigger row has no `first` block and no `coverage_suspect` to read. The first arm's own
`coverage_suspect` — `stats["renarrate"]["first"]["coverage_suspect"]` — exists whenever the retry
STARTED: on a fired run, and equally on a fail-open run, whose `fired: False` the flag alone cannot
separate from a no-trigger row. Measured on that path, the record is
`{"fired": False, "trigger": "coverage_suspect", "retry_failed": …, "first": {…, "coverage_suspect":
true}}`. F3's restriction therefore removes the FIRED rows but not the fail-open ones; those carry
`first` and are selected by `retry_failed`, as above. The nearest substitute
is **not** the same quantity: `vet_candidates` (`vet_gate.py`) applies `check_batch` to the list AFTER
the `route_mentions` removal in `extractor_v2.py`, whereas the arm's flag is `check_batch` on the
PRE-`route_mentions` S2 output — and `route_mentions` can shrink that list to zero, so a session that
held a mention can read `False` on the arm and `True` on the substitute. Since F3 counts only
`coverage_suspect is False` sessions, reading the substitute can only REMOVE episodes from a count
whose `0` is the green light to delete `run_s4`. Recording the first arm's flag on the NON-FIRED
return, so F3 reads one surface on every run, is therefore part of what **#11534** owes — and until
it exists, this clause is quoted as an inference and never as a measurement. ⛔ **And whose BASE, which is the second half of the same trap.**
`added_by_s4` is `merged - s2_items`, and on a retried run `s2_items` is the UNION (both arms), so
S4's contribution would be measured against a bigger base and understated — on precisely the
population where a retry fired for a reason other than coverage (an injected arbiter). Restricting
the count to non-retried sessions makes both halves unambiguous: one arm, one flag, one base. The
population is therefore "S4 added items, the coverage check did not flag the batch, and no retry ran".
That is an episode where S4 ADDED items in a case the coverage check could not flag — so the bounded
re-narrate above cannot reach it, because the retry's armed-by-construction arm is exactly
`coverage_suspect`. `> 0` means **do not delete `run_s4`**: the retry is a successor for the
total-loss case only, and the removal's pre-condition is a stage that can ADD in the cases S4
currently rescues. `0` over the corpus is the removal's green light. ⛔ It is not satisfiable by
reasoning about the code — the count comes from a run — so no lane may delete `run_s4` on the
strength of this section alone. (The label **F3** is the one the `#11002` issue's risk table and the
drift marker over the step list already use; this paragraph is its definition of record.)

**⚠️ This section is a SPEC, not a description of what runs — and `#5095` closed HALF of the gap.** The load-bearing instance is S3. `search_graph` degrades instead of raising (correct, `#3679`), and its renderer used to tell the model *"no existing items available; assume everything is new"* (`_render_search_results`, `extractor_v2.py:2353` at `7d348d351` — the revision this citation has always been pinned to) — the inverse of I1, and the duplication-manufacturing direction G3 names.

**What runs now (`#5095` + `#10997`).** The claim about a check that never happened is gone from both artefacts, and the run reports what it did not establish:

- **The lookup's state is READ, not assumed** — `tortoise/failure_policy.py` (`lookup_state`) returns `resolved` / `partial` / `unresolved` from `search_graph`'s new `query_coverage` + `query_results`, which report the queries that actually ran. `degraded` alone cannot express it: the degrade handler is reachable only from the FIRST query (so a degraded lookup has **zero** priors), while a failure on query 2..N is swallowed and returns `degraded: False` with a genuinely **partial** prior set. `partial` is the common state, not an edge case: a real S2 output derives 18 queries against the shipped budget of 15, and the legs are iterated `object → event → point`, so the leg a truncation starves is the **point** one — the dedup leg.
- **The prompt states the truth** — `_render_search_results` renders `failure_policy.lookup_note`, which says the check did not run (or did not complete), that existence is **UNRESOLVED not absent**, that nothing may be treated as new, that no `link_before_create` entry may report a search that did not happen (omit it) — and, because this clause is read by the S4 GAP REVIEWER, it keeps the **emission** requirement: the candidates must still be emitted.
- **The record states the truth** — `execute_embed`'s `link_before_create` uses `failure_policy.created_record`, whose `found` is `false` **only** when THAT candidate's own query ran and matched nothing; otherwise it is `None`. Per candidate, not per run: a bounded lookup leaves some candidates checked and others not.
- **The run reports it** — `extract_session_v2` returns an always-present `identity` block (state, reason, the coverage counters, per-class candidate and unresolved counts) and warns loudly when a candidate's own identity query did not run (and, separately, when the lookup was bounded but every candidate's query ran, and when the lookup did not run at all with no candidate to check — three different facts, and each gets its own sentence **on a run that reaches this block**; the empty-conversation early return precedes it and has no candidate, so it emits no `S3 identity` line). Nothing is suppressed.

**What runs now — the MINT half (`#10997`).** Until `#10997`, the one place the run still asserted `new` was S5's mint-before-wire pre-pass (`_mint_endpoint`, `#2552`): an operator endpoint the model did not also emit was materialised as a `statement` Point **from the model's ref**, unconditionally — the pre-pass never read `lookup_state`, so on an unresolved (or `partial`) lookup it fabricated a Point asserting an identity nothing had checked, which is the duplicate manufacturer G3 names.

That pre-pass is now **licensed by a lookup that RESOLVED and by nothing else**: on `unresolved`/`partial` the endpoint is **HELD** — together with its **edge** (`#2552`: holding the endpoint alone trades a duplicate for a lost operator edge, which I2 forbids) — and reported on the run (`held`, `stats.held` as an **identity** count, and one warning naming the count, the state and whether it persisted). Nothing is minted and nothing is dropped. On a later run whose lookup resolves, the hold is **drained exactly once**: `claim` is the only path to a mint, `confirm` binds the key to the id it minted and refuses a second, different one, a key is confirmed **only after the `#2552` prune** keeps its Point (otherwise the claim is released back to `pending`, so a pruned item is never resolved into nothing), a claim left behind by a process that **died** between `claim` and the settle is **reclaimed** by the next drain (an idle scan over the record's own `claimed_at`, `HOLD_CLAIM_IDLE_SECONDS` — `pending()` is the drain's entire input, so a claim nothing releases is an item no later run can see), and a RESOLVED key is never re-armed by a later ambiguous run. The store is `tortoise/identity_hold.py` — an append-only per-graph JSONL whose home is argued in its docstring: **not the graph** (a held candidate is not a belief, and a node would let the next lookup resolve the ambiguity against the held copy of itself), **not the journal** (`derived = replay(journal)` folds graph state), **not Postgres** — the `capture_spool` convention. It is **opt-in** (`hold_store=`, `open_store()`); a run with no store still reports the hold (`persisted: False`) and says so, so the limitation is loud instead of silent.

**What is still absent, and it is the whole of the remaining code half:** the `HOLD` gate for a **Point or Entity the MODEL emitted** — the S5-Job-2 create gate, which is *not* `execute_embed({})` (that is the sole carrier for retractions, supersessions and chain notes, so suppressing it wholesale drops memory I2 forbids dropping); and, because the store is opt-in, a capture that never supplies one cannot carry its held set forward. `#5087` (E1) remains the code-owner of both, and `config/v4-mechanisms.yml` keeps `failure_policy` at `not-implemented` for exactly this reason. <!-- v4-mechanism: failure_policy state: not-implemented -->

---

## 5. The connection and lifecycle model

**⭐ The owner's statement of what the connection layer is FOR (2026-09-24)** — and it is the product justification for this whole section:
> *"the core idea of what we do is being able to understand how an Object relates to events, subjects and points so we can not only have 'what is' but why and the context around it (who, when)."*

**⇒ Two consequences, and the second one is measured.**

1. **The connection layer is not a nice-to-have — it is the product.** *"What is"* is retrieval; *"why, who, when"* is the **graph** around the entity. **A claim with no edges is a fact without context.**
2. **⭐ Traversal is cheap enough to be the default mechanism for it — measured: 1.55–2.35 ms** (storage §12.1d). *"Which claims are about this entity, which event carries it, which source it came from"* is a walk from a node we already hold. **No similarity is involved once the node is in hand** — so the why/who/when value **does not depend on vectorising the entity at all** (storage §12.2c). **What a vector is for is FINDING the entry node, not reasoning from it.**

**The owner's requirement (2026-09-23):**
> *"if a PR replaces a previous Object by a new one, or simply kills a previous object, we need to connect that in the graph appropriately so the graph doesn't keep the old object live. And adding the reasons why (epistemics) is important, and the PR becomes the event connected to both that provides the context of when the change happened."*

**This is the ontology's state-centric model (§2) exactly:**

```text
Object A  (superseded — was live)
    ▲
    │
  Event W   ← the PR. Carries "when". The connector.
    │
    ▼
Object B  (new — live)
    ▲
    │
Points X, Y, Z   ← the reasons. Carry "why".
```

**Two mechanics make it work, both already recorded in the ontology:**

1. **Lifecycle changes are appended as Events and never written onto the Object.** `Object.status` is a **fold cache** over lifecycle events (§4.2, §2) — *the event stream is the reconstruction source.* **So the graph never edits Object A; it appends "A was superseded by W," and A's live-ness changes on read.** This is the same property that makes the store rebuildable.
2. **The Event carries "when"** — which is why **the PR should *be* the Event**, not merely be referenced by it. For structured sources this is free: the PR already has its state and timestamp.

### Detecting a lifecycle change — three routes, in order of reliability

1. **The source says it** — `closes #465`, a state transition. Free, exact. *(This is the source-mapping point, §3.)*
2. **The lookup says it** — S3 finds Object A live and the narrative says it changed.
3. **Jev classifies it** — *"does this input change the state of an existing entity, and how?"* — a **Choice** over `created / superseded / deprecated / unchanged`, with a probability. This is Jev's *intent routing* pattern.

---

## 5a. Supersession — the gap is DETECTION, not mechanism

**Owner, 2026-09-23:** *"we'll likely need to implement some superseding system or something like other memory systems do for facts, no? where the more recent fact wins? or the fact attached to the right entity base / done entity lifecycle?"*

**⚠️ The mechanism is not the gap — supersession is a first-class, journaled mechanism, so nothing new needs building for it.** Measured on the live graph (2026-09-23):

| what | where (code) | live count |
|---|---|---|
| `sdk.supersede(old_id, new_id)` / `supersede_point` | `sdk.py:5509` / `:5527` | — |
| `apply_supersessions` — *"the ONE consumer-side discipline"* (`producer side: extractor_v2._supersession_records`) | `commit_ops.py:299` | — |
| `CORRECTS` edge — `(new:Point)-[:CORRECTS]->(old:Point)` | `subgraph.py:28` | **74** |
| `Object.status='superseded'` + `supersededBy` (a **fold**, not a field) | `projection/entities.py:1497` | **32 of 7,867 Objects (0.4%)** |
| superseded `Point`s | — | **74** |

**⇒ The mechanism fires ~32 times against 7,867 Objects (a later snapshot than the 7,863 at §1).** It is not missing — **nothing detects the change.** That is the finding, and it is the same gap §5 names: **the extractor must produce the supersession record, and today it almost never does.** Building a supersession system is not the work; **wiring detection into extraction is.**

**Two forms exist and they are different, deliberately:**

- **Point-level:** the `CORRECTS` edge. ⚠️ For **Points there is no `superseded_by` property** — `subgraph.py:28` records that it is **derived by following `CORRECTS`**. (`Object`s *do* carry `supersededBy` as a fold cache.)
- **Entity-level:** an `ObjectSuperseded` **lifecycle Event** + `_fold_object_superseded` → `Object.status`. **Appended, never written onto the Object** (D7).

### ⛔ "The more recent fact wins" is REFUSED — contradiction test

**There is a recorded decision on exactly this, and it says no.** **`#2354`** — *"uncertainty verdict layer — close/keep/reject on correction conflicts via quarantine+promote/retract; **sweep never auto-resolves**"*:
> *"the agent loop can drain a decision queue deterministically and **the sweep NEVER auto-resolves**. Auto-accept applies only when EP confidence clears the threshold; everything else queues."* … *"no path auto-resolves a queued decision"*

**Rejected on two independent grounds, either sufficient:**

1. **It contradicts a recorded decision.** Per the contradiction test the route is **a reopen, argued with the owner** — never a quiet adoption.
2. **It is the wrong mechanism for us, and the reason is knowable.** Recency-precedence is what **Hindsight** uses — `"the fact with the LATEST mentioned_at is authoritative"` — and they use it **because they deleted `confidence_score`** (migration 2026-04-02). **We kept confidence** (owner ruling). Recency is the hedge you reach for **when you have no contention layer**; we have one.

**⚠️ And the reason it matters beyond consistency: a recency rule is SILENT.** It resolves a conflict **without leaving a record**, so *"why is it this way?"* stays unanswerable — the exact property the epistemic layer exists to provide (§2.4). **A `CORRECTS` edge or an `ObjectSuperseded` Event IS the record.** A tiebreak is the absence of one.

### ✅ The owner's second option is what we already have — and it is the right one

**"the fact attached to the right entity / the entity lifecycle" is the design, not an alternative:** the fact attaches via `aboutObject`, and the **Object's lifecycle** decides live-ness — with `Object.status` as a **fold over appended lifecycle Events**, so the event stream stays the reconstruction source.

**⚠️ One trap to design around, which follows from §5's model:** a fact that **caused** A's death must **not** die with A. **Blind propagation of staleness down `aboutObject` would kill the reasons along with the thing they killed** — and the reasons are exactly what makes the change auditable. **The rule is: the Object's status propagates to *descriptive* facts about it, never to the *epistemic* operators that judged it.**

---

## 6. Where Jev sits (whole pipeline)

| step | Jev's job | shape |
|---|---|---|
| **S2.2** | entity or reference? atomic? did we account for what the narrative said? | Noul + Score — **adversarial** |
| **S3** | is this the same entity as that? | **Choice** over supplied candidates |
| **S5** | does this change a lifecycle? contradict a belief? | **Choice** over `created/superseded/deprecated/unchanged` |

**All of these can be evaluated in ONE call, in parallel, against the same state** — Jev evaluates its questions concurrently, so the second question costs no extra latency. **But it cannot be one *atomic* question per the vendor's own doctrine:** a 28-item test produced 5 self-contradictions (confident on level, unconfident on keep), which is the signal that a bundled question is not atomic.

---

## 7. Constraints the architecture must respect

### Atomicity is required by the epistemic layer — NOT an optimisation

**Owner, 2026-09-23:** *"we need to have the right granularity for Points to be logical. If we have many arguments mixed together, it's not possible to NAND/IMPL nor modulate relevance."*

**Operators connect only epistemic targets** (any of `:Point`/`:Subject`/`:Object`/`:Event`; see `ONTOLOGY.md` §8 `OVERRIDES`) — you cannot `NAND` a paragraph, and the write path admits all four target types. EP confidence propagates over a **factor graph of atomic claims**; a fused claim gives one confidence number to several distinct beliefs.

**⇒ Volume is not the metric — quality is.** v3's move to atomic Points (`E3`) is a **requirement of the epistemic layer**: operators act on individual claims. **De-duplication is authorised** — near-duplicate `MERGE`, arbitrated by Jev over the claim plus its narrative/raw data, with the admissible set `KEEP` / `NOOP` / `DISCARD` / `MERGE` (§16.4). **Aggregation is forbidden** — `FUSE` (combining two *distinct* claims into one new claim) would give a single confidence number to several distinct beliefs, which is what the belief layer exists to avoid. **No volume target exists in this document.**

### State values and numeric facts — the carve-out is NARROWER than it looks

v3's **E2** requires state-value facts be preserved verbatim (*"personal best 5K time = 27:12, as of <date>"*) rather than filtered as "counts are noise." `STATE_VALUE_CARVE_OUT` in `_granularity_text()` is the code anchor for exactly this.

**⇒ Any mechanical filter MUST carry this carve-out, or it will drop the facts users care about most.** See `STORAGE-ARCHITECTURE.md` §7 for the low-cost storage question. <!-- v4-mechanism: state_value_carve_out state: implemented -->

#### ✅ The carve-out MUST cover decision-relevant numbers

A carve-out scoped only to **user-personal state** (personal bests, schedules, preferences) and **explicitly telling the mapper to DROP process metrics as "mechanics tokens"** — *"counts/logistics are ephemeral"* — is **right for personalisation memory and starving for engineering-decision memory**: on a number-dense session (latencies, durations, counts) it drops exactly the values the decision turns on. ⚠️ **`STATE_VALUE_CARVE_OUT` is therefore extended to retain operational values** — the table below carries the two classes.

**⇒ There are TWO value classes, and the policy must keep BOTH** — the table below states which:

| class | example | today | must be |
|---|---|---|---|
| **Personal state** | *"personal best 5K = 27:12"* | retained (E2 carve-out — **`#1534`**) | retained |
| **Operational / decision values** | *"p99 latency 480 ms at 3k rps"* | **RETAINED** | retained |

**Operational values are DURABLE.** `STATE_VALUE_CARVE_OUT` (`extractor_v2.py:325-341`) carries an operational-value paragraph — *"a concrete value that is the SUBJECT of a decision, observation, or plan is DURABLE too… Measurements, deadlines/freezes, thresholds/TTLs, versions, and counts are carried VERBATIM"* — covering *"the p95 hit 4.2 seconds"* and *"a ten minute TTL"*. The rule reaches the model: a `VALUE_FIDELITY_RULE` (`:455`) goes into the S2/S4 `{anti_routine}` slot via `_s2s4_rules()` (`:556`), and `_granularity_text()` (`:1444`) appends `STATE_VALUE_CARVE_OUT` to S1.

**⇒ The design requires ENFORCEMENT BY DEFAULT — the mechanism is not the gap.** The **S2.2b** identifier-only predicate is the rule in `tortoise/value_gate.py`: `value_gate._REFERENCE` (`#\d{1,6}(?!\d)`) with `identifier_only()` as a whole-residue test, imported by `extractor_v2.py:72`, toggle `_value_gate_enabled()` (`:724`). ⛔ **THE GATE SITE IS `S2.2b`, NOT `E7`** — the `^#\d{3,}$`-style anchored pattern is not the rule. **Enforcement by default is required**, and the predicate is **subordinate to VET** (`_value_gate_enabled()` folds into `vet_enabled`). ⛔ **This is a default and a subordination — not a missing mechanism. Do not build `valueGate`.**

**⚠️ The mechanical DISCARD (§16 — `value_gate.py`) must be defined against the two-class table above, not against the prompt string alone** — otherwise it discards operational values harder. The carve-out protects them, so **the gap is enforcement-by-default, not policy.**

#### Where numeric facts should live (owner, 2026-09-23) — structured, so they are never re-processed
>
> *"getting numeric facts stored somehow in a table and available for search so they're not re-processed … an optimisation pattern for better recall that relates to the extractor and needs to be harmonised here."*

**⚠️ PLACEMENT IS DECIDED (owner, 2026-10-06): a state/numeric value is a STRUCTURAL FIELD ON AN ENTITY, not a `Point`** (see `STORAGE-ARCHITECTURE.md` **§7**). Numeric facts (amounts, durations, counts, measurements) are stored as **structured rows** — subject + attribute + value + unit + date — **rather than only as prose inside a claim.** They stay searchable, and **a query never has to re-read prose or re-run extraction to recover a number.**

- It compounds with **D8** (state values → structural storage, testing whether embeddings can be skipped).
- It is **the same question `STORAGE-ARCHITECTURE.md` §7 raises from the storage side** — the two sections must be read together.
- **⚠️ Harmonise, do not duplicate.** `#2453` (retain operational values verbatim) · `#1534` (E2 state-value Tier-A extraction) · `#2817` (no numeric/locale canonicalisation exists — PT-BR/EN amounts, blocks the value layer) · `#2782` (money needs amount + currency on entities) · `#2521` (numeric aggregation across sessions, counted under supersession) · `#2820` (tracking map — its **"state & value model"** workstream owns all of this).

**⇒ The extractor decides what is retained; the storage layer decides where it goes. Neither can be built alone.**

---

## 8. Reuse these — do not rebuild

| thing | where |
|---|---|
| **`Projection`** — a **two-method** event-sourced Protocol (`apply`, `rebuild`) with `InMemoryProjection` as an existing second implementation | `tortoise/projection/__init__.py` |
| **`classify_consolidation`** — pure, no LLM, no graph I/O, post-extraction/pre-write; emits `noops`/`deletions` on a channel not written to the graph | `tortoise/extractor_v2.py` (E7/`#1539`) |
| **`KindClassifier`** — kNN top-5 → margin gate → `nearMisses` rerank (no LLM) → batched LLM for the low-margin tail → fail-open + census. ✅ The tail's **decision source** is injectable (`tail_decider=…`, `#5087`, OFF by default): `s2_decision_seam.classify_tail` is the adapter + prompt + parser this row used to say did not exist. `llm_tail` remains the boolean switch for *whether* the tail runs at all — it is not the seam, and swapping the decider changes nothing about it (§4.2). | `tortoise/kind_classifier.py` (`#1695`) |
| **S2/S4 merge** | `merge_embed_lists` (def `extractor_v2.py:2284`; **called at `:4845`**) |
| **Pack enforcement machinery** — `chains` with `enforcement` levels, `nearMisses`, `subclassOf`, `equivalentTo`, `extraction.sourceTypes` | `packs/*/manifest.yaml`; 20+ files reference each |
| **The narrative prompt slot** — S1's granularity rule is injected here | `_granularity_text()` |

---

## 9. The design decisions

| # | decision |
|---|---|
| **D1** | The narrative lives in **Postgres, referenced by its `Source`** — **not in the graph**, not a `Document` node, not a `Point`. The graph holds the **reference**; the **text** stays out of the graph, and therefore out of RAM. |
| **D2** | A GitHub PR is an **`Object` (WorkItem)**, its merge is an **`Event`**, and its body is a **`Source`**. A completed task is a **lifecycle `Event` whose status is the fold** — never a second node. |
| **D3** | **The narrative is a *focus* and the raw text is the *source*** — extraction may consult the raw text. **STILL PROVISIONAL after two runs** (`#7947`; §10) — **the measurement cannot decide it**, for three independent reasons: both arms receive the raw transcript, so the falsifier cannot fire for the loss it was written to test; the decisive raw arm ran `partial`; and n=25 cannot resolve anything finer than ~10 pp — one holdout qualifier IS 10 pp — with the bound subset flipping sign between the tuning and holdout halves. Run 1 (the pre-registered run) lands in branch 3 by the rule; run 2 meets branch 2 but cannot be certified. Both stay provisional; cost recorded. **What would settle it** — two arms whose carriers differ (narrative-replaces-transcript vs raw-transcript-suppressed), on clean receipts, with ~100 bound qualifiers AND independent sessions (§10). |
| **D4** | **Salience is relational** — one **entity-keyed neighbourhood lookup**, consumed by the **salience judgment** and by **S3** (resolution). **Extraction does not consume it.** |
| **D5** | **One adversarial reviewer — one step, two questions, one artifact**: item validity **and** coverage, both at **S2.2**. |
| **D6** | **Two distinct connection sets** — within-batch and to-graph, both **proposed by the model**. |
| **D7** | **Lifecycle is appended Events plus folded status**, with the PR *as* the Event. |
| **D8** | **A state value is an attribute of the entity it describes — not a belief.** A belief can be argued with; an attribute cannot. So a value carries no confidence and is not a `Point`. Whether such values need an embedding is **open**, and turns on whether the queries users actually need are structural or semantic. |
| **D9** | **The lookup follows extraction, and S3 owns it** — keyed on candidates, with the narrative as disambiguation context. There is **no pre-load and no S1.5**. |
| **D10** | **A document is a `Source`, not a graph node.** `:Document` folds into `:Source`, content moves to raw storage, and **liveness is a read of its entities** (high confidence, not superseded, not NANDed). The entity layer is **uniform over documents, code and transcripts** — *"that's the reasoning/knowledge layer."* Five consequences follow: **`aboutDocument` is kept**, with only its label retargeted (**not** collapsed into `aboutSource`, which would delete a rebuild guarantee); **two axes are kept** (`sourceKind` + `documentKind`); **`doc_status` is retired**, `format` moves, and `content` leaves the graph; the **`documents` cap counts document-bearing `:Source` nodes**; the **`:Source` replay key is `url`**. |
| **D11** | **Hosted FalkorDB is the store**; optimisation is deferred. **Pricing self-hosting comes first** — SSPLv1, the same engine, no rewrite. |
| **D12** | **Near-duplicate `MERGE` is authorised**, with **Jev as arbiter** over the claim plus the narrative and raw data. **A merge must never cross a difference in: a number or quantity, a named entity, a language, a date or scope, a negation, a condition, a load-bearing connective whose role changes, or a role inversion.** The predicate is `_boundary`. |
| **D13** | **The working method for each step is draft → run → look → refine → repeat** — *"until good."* |

### The provenance rule derived from D2

**Do NOT mint a `Source` for events.** No system found uses an event as the *primary* provenance anchor — the anchor is always a document or chunk, with the activity as a *second dimension*. (PROV *permits* naming an event node — `prov:Generation` etc. may carry an identifier — and Cognee has `PipelineRun`; both keep the document as the anchor.) **No source states a reason for not doing it** — reported as a strong negative finding, not a rule.
**⚠️ And PROV has no `Source` class at all** — in PROV "source" is a *relation* (`hadPrimarySource`, `wasDerivedFrom`). Our `Source`-as-a-class deviates from PROV.

### ⛔ Adopted-vs-refused on the research (contradiction test)

- ✅ **PROV's `Entity ⊥ Activity` disjointness is CONSISTENT** with our ontology (Event and Object are already separate core types). No conflict — usable.
- ⛔ **REFUSED: GraphRAG's consolidation rule** — *"If the provided descriptions are contradictory, please resolve the contradictions and provide a single, coherent summary."* A resolved contradiction is **one** claim, not two, and the losing side has no node left to support or contradict. **Directly destructive of the epistemic layer.** Reported as a **counter-precedent**, never a candidate.

---

## 10. The narrative-first hypothesis — tested, and the better argument

The owner's hypothesis: *"narrative matters more for us because we're focused on logic/contentions, they're not."*

**VERDICT: partly defensible — the premise is true, the inference is unevidenced.**

- ✅ **The premise holds.** GraphRAG **erases** contention (consolidation), Graphiti **replaces** it (`invalid_at` = valid-time termination; the loser is dead, not argued against), Mem0 **accumulates** it without ever asserting a relation. **None holds two live claims in mutual tension with typed support/attack and independent confidence.** Tortoise is genuinely distinct.
- ⛔ **The inference does not follow.** **Zero** systems anywhere extract support/attack relations from a machine-generated narrative — and the field that *does* model support/attack between claims, **computational argumentation, extracts from RAW text.** Universally, as far as the search reached.
- ⛔ **Competitor practice cannot be recruited as evidence either way** — no competitor has *any* stated reason for extracting from raw. It was never a decision. **"Nobody asked."**
- ⛔ **Zero precedents for summarise-then-extract.** The one apparent candidate is the *reverse* direction (input = raw long document, output = a compressed graph).
- ⛔ **No head-to-head measurement exists** (raw vs summary, for entities, relations *or* claims).

### ⭐ The stronger argument the evidence leaves open — the real justification for narrative-first

**It is not "contention vs facts." It is CROSS-SOURCE CLAIM NORMALISATION:**

- Argument mining's claims are **co-located, co-authored, single-document** — nothing needs resolving across sources.
- **Tortoise's IMPL/NAND edges join claims ACROSS sources** (sessions, issues, PRs, meeting notes). That requires **(a) recognising two independently-worded propositions as the same claim** before any support/attack edge can join them, and **(b) normalising hedging, scope and stance.**
- **A synthesis step is the classical mechanism for exactly that.**

**⇒ This is a better argument than the stated one, it is untested, and the evidence neither confirms nor refutes it.**

### A second reframing worth keeping

**Tortoise's input is largely DIALOGUE, not documents.** Turning a transcript into a narrative is arguably **discourse reconstruction**, not compression — making the GraphRAG comparison apples-to-oranges. If so, narrative-first may be an **input constraint, not a design choice.**

### Why D3-Option 4 is the right call regardless

Option 4 (narrative as *focus*, raw as *source*) **hedges precisely this unresolved question** — it does not require the narrative to be lossless, because extraction can go back to the raw for what matters. **The research justifies the choice rather than merely permitting it.**

### ⚠️ The falsifier to measure

The research names it: **does the summary lose the hedging / scope / stance information that a claim's confidence depends on?** Adjacent evidence (no head-to-head study exists): summaries can be unfaithful, hallucination propagates across summary chains, and summarising raises precision while **dropping recall**. **Any measurement must be run on OUR corpus — none of these transferred cleanly.**

#### ⛔ FIRST, WHAT THIS DESIGN CANNOT TEST — the premise is §S2.1's, the consequence is drawn here

§S2.1 recorded the premise on **2026-09-23**; the consequence was drawn on **2026-10-18**, in review of the
first run. This block governs how every number below may be read.

**Both arms receive the full raw transcript.** `render_s2_prompt()` appends
`_render_source_transcript(edus)` to the S2 prompt **whenever the transcript is under
`_SOURCE_TRANSCRIPT_CAP` (8,000 chars — these blocks are 3,801–5,311, well under it)**, and both arms
pass the same `edus` (`tortoise/extractor_v2.py:1755`, the `+ ("\n\n" + transcript)` tail at `:1775`).
⚠️ **Above the cap the block is omitted entirely** (`:1738`, the `return ""` under the `:1737` `_SOURCE_TRANSCRIPT_CAP` guard), and the arms would then differ by the whole
transcript — i.e. a long session would land in exactly the replace-configuration this section says was never
run. None of these seven does. So the
prompts differ **only in the `story` slot**: arm A is *raw story + raw transcript*, arm B is *narrative story +
raw transcript*. **The receipts prove it from the other side, exactly:** in all 7 sessions the narrative arm's prompt is
smaller, and the difference **equals the story difference** — Δprompt_tokens tracks Δstory_chars at
≈4.2 chars/token (per session 4.04–4.55; aggregate 4.23): wp01 −2146 chars / −512 tok; wp02 −996 / −224; wp03 −1422 / −342;
wp04 −1389 / −305; wp05 −1032 / −250; wp06 −2030 / −502; wp07 −1776 / −414. **No length-visible component but the
`story` slot differs between the two ~9,000-token prompts.** ⚠️ That arithmetic rules out a differing
section of any size, but it is blind to a SAME-LENGTH difference — and one exists: under
`TORTOISE_LABEL_ORDER=shuffle` the label order is seeded **from the story** (`extractor_v2._label_order_rng`),
so the arms would see different label orders at identical prompt length. It was observed unset for run 2 and is
unknown for run 1 (see the limits below). Had
the narrative REPLACED the transcript, the prompt would have shrunk by the transcript's own length — the block is
3,801–5,311 chars, i.e. ~900–1,300 tokens, not the 224–512 observed.

**Consequence, and it is worse than "both arms carry the raw": the raw arm receives the transcript TWICE** —
its `story` is `_edus_to_text(edus)` and the same text is appended again as the `SOURCE TRANSCRIPT` block — so
the raw arm sees the conversation at roughly double the exposure of the narrative arm. The harness's own framing
("exactly one value differs") is true of the `story` slot and understates the asymmetry.

**So this harness measures what the narrative ADDS on top of the raw — which is exactly D3-Option 4's shipped
configuration — and it does NOT measure whether a narrative could REPLACE the raw.** A narrative that dropped a
hedge would still have that hedge in the transcript, so S2 could preserve it anyway: **the falsifier cannot fire
for the reason it was written to test.** "The falsifier did not fire" is therefore a much weaker statement than
it looks, and must never be quoted as "summaries do not lose hedging" — that counterfactual was not run.

**⚠️ AND ONE DECISIVE ARM RAN INCOMPLETELY.** In run 2 the `wp07_bluepeak_followup` **raw** receipt carries
`partial: true` — the extractor's rung-4 partial-accept path fired and the tail of the model's list was
dropped (`s2_stats.partial`, `recovery: {}`, no `s2_out_tokens`), which production itself treats as an error
(*"S2 output partial — truncated tail dropped (embed list incomplete)"*). That is the worst cell in the corpus
(6 claims vs the narrative's 16) and **it contributes 3 of the holdout's 6 raw losses**. The scorer read only
`embed_list` and the harness printed only exceptions, so **nobody saw it** — the flag was in the receipt, unread
(both now consume it: the scorer refuses to certify a branch on it, the harness prints it). **A single qualifier there flips
the holdout Δ from +10.0 to +20 pp, i.e. across the branch-1 trigger** — which is enough to disqualify this run
from certifying a branch, and it is why the scorer now refuses to certify one on a partial receipt.

#### ✅ MEASURED, RUN 1 — 2026-10-10 (`#7947`). ⚠️ Its MAGNITUDE did not replicate; see the run-2 block below before quoting any number here

**Design (pre-registered before the first call:** `#7947` comment `6102402694`**).** Two arms differing in exactly one
value: the shipped pipeline feeds S2 either `story = compile_stories(chunk_stories)` (**narrative**) or
`story = _edus_to_text(edus)` (**raw**), with the same pinned model (`deepseek/deepseek-v4-flash`), the same
`run_s2` (so one prompt scaffolding), the same chunking and the same `edus`. Corpus: the planted-gold
write-path corpus (`tests/eval/write_path/`, wp01–wp07); split fixed as **wp01–wp04 tuning / wp05–wp07
holdout**. 25 planted qualifiers (hedge/scope/stance, annotated into the gold as `planted_qualifiers` before
the run), 90 planted units. Harness: `tests/eval/write_path/raw_vs_narrative.py` + `score_qualifiers.py`.

| arm | qualifier loss | distortion (of claims that survived) | recall | precision | claims emitted |
|---|---|---|---|---|---|
| **raw** | 80.0% (20/25) | 44.4% | 54.4% (49/90) | 62.8% | 113 |
| **narrative** | **48.0%** (12/25) | **27.8%** | **80.0%** (72/90) | 59.9% | 152 |

Holdout alone (wp05–wp07): raw 100% loss / 42.4% recall / 2 of the 2 surviving claims distorted; narrative
**40% loss / 66.7% recall**, precision parity (49.2% vs 50.0%), 0 of 6 distorted. Artifact:
`tests/eval/write_path/results/7947-score-all.json` — ⚠️ **not reproducible from committed artifacts:** run 1's
per-session arm outputs were not retained (the receipts convention came in with run 2), so this file is a
record of that run, not something the repo can re-derive. Run 2's is re-derivable. (The `instrument.note`
string in BOTH files was corrected in place on 2026-10-18 — it formerly claimed the lexical error "can only
under-credit preservation", which the two-sided bullet below contradicts. That is the instrument's
DESCRIPTION, not a measurement: no number, classification or verdict changed — run 1's file carries the
correction by hand precisely because its receipts are gone.)

**⚠️ Run 1's numbers are reported, not withdrawn — but the SECOND run shows the magnitude is not a stable
result.** One qualifier out of ten moves the holdout figure by 10 pp, and the raw arm moved by 28 pp between
runs. **Read run 2 for what replicates; treat run 1's gap as one draw from a wide distribution.**

**⚠️ LIMITS that bound BOTH runs — the first are part of the registration, the rest were found during
the work and are declared here rather than dropped:**

- **n = 25 qualifiers across 7 sessions, of which 10 are holdout.** One qualifier is 10 pp on the holdout and
  4 pp pooled, so neither run can resolve a difference smaller than about one qualifier.
- **9 of 25 qualifiers (4 of the 10 holdout) carry no `bound_claim`** — a warrant, a hypothesis or a scope
  limit on a clause that no planted unit carries. For those the scorer anchors on the qualifier's own words,
  so `claim_present` normally *entails* `marker_present`: the subset can report `preserved` or `absent` but
  almost never `flattened` (**0 of 18 unbound cells in run 2**; 1 of 18 in run 1, and that one only because
  `only` is in `STOP`). It measures **whether the phrase survived at all, not whether it survived on its
  claim** — a LOOSER test than the bound subset, not a stricter one, and 4 of the 10 decision-input
  qualifiers are of this kind. The two are reported separately below because they do not measure the same
  thing.
- **The instrument is lexical, and its error is TWO-SIDED.** It can **under-credit preservation** (an arm
  may keep a hedge in vocabulary the marker list lacks and be scored `flattened`) — the conservative
  direction for a loss metric — and it can **over-credit it**: `marker_present` is an unbounded substring
  test over a table that mixes space-suffixed markers (`"may "`, `"one "`, `"just "`, `"before "`) with bare
  stems (`"only"`, `"except"`, `"single "`, `"support"`, `"review"`, `"decision"`, `"likely"`), so
  `"commonly"` satisfies scope's `"only"`, `"exception"` satisfies `"except"` and `"unlikely"` satisfies
  hedge's `"likely"`, turning a `flattened` cell into `preserved` and thus UNDERSTATING loss. That second
  direction is **not arm-neutral** — the arm emitting more claim text has more chances to contain a
  mid-word hit, and the narrative emits 25–35 % more claims — so it leans toward the arm whose loss this
  measurement treats as the risk. It is **latent on these runs, not a wrong number**: every `preserved` row
  in both committed score files was checked and none is carried by a mid-word hit (the three preserved
  `scope` rows are the literal "the admin screen is the only place…" and "defaults off until the perf review
  signs off" ×2). Word-bounding the match would remove the class, but the marker table is the REGISTERED
  instrument, so that belongs in a newly registered run rather than a silent re-scoring of these two.
- **One annotation binds a warrant to the claim it warrants, deliberately.** `wp02 q_01` ("the team signed off
  last Friday") is *also* planted as the fact `wp02 u_02`, but its `bound_claim` is `u_01`, the migration
  decision — so it scores `flattened` when the warrant and the decision survive as separate claims. That is the
  intended reading (a warrant is a *qualifier on* the decision it supports) and it is recorded here rather than
  re-bound: re-binding it after the runs would be editing the answer key to suit the result. It affects 1 of 25
  qualifiers, in the TUNING half, so the decision input is untouched.
- **The registered judge-arbitration pass over the decisive `flattened` cases was NOT run**, and the
  registration's repeat-run variance check was never exercised: the deterministic instrument was judged to
  need no session-to-session variance estimate. That judgement is exactly what the two runs then tested —
  and the raw arm moved 28 pp on the same corpus. Declared, not quietly skipped. (`score_qualifiers.py` has
  no `--judge` flag; if it grows one, this paragraph is what it must retire.)
- **The registered SECONDARY arm was never run.** Registration §2 also fixed a robustness arm — "a **fixed**
  sample of real fleet sessions, drawn and listed by id at first run", scored by the pinned judge on *"was a
  qualifier present in the raw and absent or flattened in the output?"*. No session ids were drawn, no judge
  pass exists, and neither run covers it. (It would not have decided anything — §7 says only the primary
  endpoint decides — but it is registered work that did not happen.)
- **The registered outcome taxonomy was reduced from six to three.** Registration §4 defines
  `preserved | weakened | strengthened | flattened | absent | unknown`; `classify()` emits only
  `preserved | flattened | absent`. `loss = flattened + absent` is unaffected, **but `weakened` and
  `strengthened` are both scored `preserved`**, so the instrument **over-credits** a softer or harder
  rendering as preservation — an error in the same direction as the lexical one, and undeclared until now.
- **The receipt pins the model, chunk size and session date but NOT the environment that shapes the S2
  prompt.** `TORTOISE_EXTRACTOR_PROMPT`, `TORTOISE_LABEL_ORDER` / `TORTOISE_LABEL_ORDER_SEED`, and the two
  stage caps `TORTOISE_EXTRACTOR_MAX_TOKENS` / `TORTOISE_EXTRACTOR_ESCALATION_TOKENS` (whose documented
  failure mode is *silent tail loss via partial-accept*) all change the scaffolding or the truncation
  behaviour. **Run 2's console showed the first three UNSET — that observation is not in the receipts**, so
  it cannot be re-verified from the repo, and run 1's environment is unknown. The harness now persists these
  five into every receipt (`env_pins`), and a console print no longer has to be taken on trust —
  **but that landed WITH this write-up, so it covers neither run**: the seven committed `arms-2026-10-18`
  receipts carry no `env_pins`, and run 1's are gone. The claim for these numbers is therefore "one value
  differed **given an unchecked environment**", and any repeat run will carry its own proof.

#### ⚠️ REPLICATED 2026-10-18 — the NON-FIRING replicates (as built it could not have been otherwise); the MAGNITUDE does not

Re-run of the identical pre-registered protocol: same corpus, same gold `planted_qualifiers`, same pinned
model id, same `SESSION_DATE`, same chunk size, same harness — **no parameter changed**. Output receipts are
committed this time (`tests/eval/write_path/receipts/7947-arms-2026-10-18/`, the 7 per-session arm outputs) so
the scoring is re-runnable offline, and it reproduces the committed score exactly:

```bash
python -m tests.eval.write_path.score_qualifiers \
  --sessions wp01_quarry_debug wp02_lumen_refactor wp03_ember_design wp04_aurora_perf \
             wp05_retro_writeup wp06_quarry_rollout wp07_bluepeak_followup \
  --arms-dir tests/eval/write_path/receipts/7947-arms-2026-10-18 \
  --out /tmp/7947-score.json
```

Score file: `tests/eval/write_path/results/7947-score-replication-2026-10-18.json`.

| arm | qualifier loss | distortion | recall | precision | claims emitted |
|---|---|---|---|---|---|
| **raw** | 52.0% (13/25; 10 absent, 3 flattened) | 20.0% | 78.9% (71/90) | 65.5% (78/119) | 119 |
| **narrative** | **48.0%** (12/25; 8 absent, 4 flattened) | 23.5% | **81.1%** (73/90) | 63.1% (94/149) | 149 |

Holdout alone (wp05–wp07): raw 60.0% loss (6/10) / 66.7% recall / 51.1% precision / 0 of 4 survivors
distorted; narrative 70.0% loss (7/10) / 69.7% recall / 51.9% precision / **2 of 5 survivors distorted**.

**The split, and the subsets the decision turns on** — the tuning half is reported so a reader can see the
split did work, never as part of the decision input:

| split | subset | n | raw loss | narrative loss | Δ | other |
|---|---|---|---|---|---|---|
| **holdout** (decision input) | all qualifiers | 10 | 6 (60%) | 7 (70%) | **+10.0 pp** | Δprecision +0.8, Δrecall +3.0 |
| holdout | **bound** (anchored to a planted unit) | 6 | 3 (50%) | 4 (67%) | **+16.7 pp** | past branch 1's trigger |
| holdout | unbound (own words as anchor) | 4 | 3 (75%) | 3 (75%) | 0 pp | |
| tuning | all qualifiers | 15 | 7 (47%) | 5 (33%) | **−13.3 pp** | Δprecision **−5.3 pp** (outside the ±5 tolerance), Δrecall +1.8 |
| tuning | bound | 10 | 3 | 2 | −10.0 pp | |
| tuning | unbound | 5 | 4 | 3 | −20.0 pp | |

**The subset does not hold its sign across the split, and the two halves disagree on two metrics, not one:**
the bound qualifiers favour the narrative by 10.0 pp on tuning and raw by 16.7 pp on the holdout, and the
tuning half's Δprecision (−5.3 pp) is outside the registered ±5 tolerance while the holdout's (+0.8) is
inside it. That is what noise looks like at this n — the subset is not a finding in either direction, and
neither is the registered total.

**What replicates:**

- **The falsifier does not fire on the registered metric, in either run** — *and as built it could not have:
  both arms carry the raw transcript (⛔ block above), so a narrative-borne loss has nowhere to show up.*
  Neither run shows the narrative losing materially more hedging/scope/stance than raw *on the holdout Δ the
  registration fixed* — ⚠️ **on a reported SUBSET one run does**: the holdout's bound qualifiers are
  **+16.7 pp** against the narrative (table above), which is past branch 1's trigger. Read "does not fire" as
  "not on the registered total", never as "no subset moves".
- **Pooled loss coincided — 48.0% (12/25) in both runs.** ⚠️ **That coincidence is a net, not stability:** the
  narrative's per-session loss changed in **6 of 7 sessions** (wp01 2/3→1/3, wp02 3/4→2/4, wp03 2/4→1/4,
  wp05 1/3→2/3, wp06 2/4→3/4, wp07 1/3→2/3; only wp04 held), moving −1,−1,−1,0,+1,+1,+1 and cancelling. **Do
  not read the matching total as "the narrative is the stable arm";** the honest statement is that the two
  arms' totals are indistinguishable at this n.
- **The narrative emitted more claims in AGGREGATE in both runs** (152 vs 113; 149 vs 119, +35% and +25%) — the
  premise that bears on E1's volume question, whose verdict belongs to `#7949`. ⚠️ **Per session it is 7 of 7
  in run 1 but 6 of 7 in run 2:** `wp06_quarry_rollout` inverted (raw 24 claims vs narrative 15). So the E1
  premise rests on the aggregate, not on per-session dominance.
- **Absence dominates the loss in both arms of both runs** (pooled absent raw 16→10, narrative 7→8;
  flattened raw 4→3, narrative 5→4), so the dominant failure is that the claim is never emitted, not that the qualifier is
  mangled. ⚠️ **On run 2's holdout subset that flips:** narrative distortion is **2/5 (40%) vs raw 0/4**, and
  the pooled distortion column is volume-sensitive (see the instrument note below).

**What does NOT replicate — and it is the whole of run 1's headline:**

- **The raw arm moved 28 pp on the same corpus** (loss 80.0% → 52.0%; recall 54.4% → 78.9%). Its precision
  rose too (62.8% → 65.5%). **Run 1's weak raw arm may not be a property of the raw input** — the confound
  below is the other live explanation, and two runs cannot separate them.
- **"The narrative recalls materially more" is unsupported.** The holdout recall gap went from 24.2 pp to
  **3.0 pp** — and run 1's raw holdout recall (42.4%) sits *below* run 2's raw figure on the same holdout
  (66.7%), so run 1 may have under-measured the raw arm rather than the narrative out-performing it.
- **Δ(loss) on the holdout went from −60 pp to +10 pp** — a 70 pp swing on one dataset.

**⚠️ THE INSTRUMENT HAS ONE KNOWN VOLUME SENSITIVITY, and it matters for the distortion column.** `classify()`
searches the class marker in the **union of every claim that matches the anchor** (≥60% of the anchor's content
words), so an arm that emits more claims — and the narrative emits 25% more — gets more chances to have *some*
matched claim carry the marker, which biases its distortion **down**. Restricting the marker search to the
single best-matching claim moves pooled narrative distortion **23.5% → 29.4%** (raw unchanged at 20.0%); the
holdout figures are unchanged (three holdout qualifiers DO have a two-claim match, but the extra matched claim
carried no marker in either arm, so the union and the best match agree there). **The direction of the
metric's verdict therefore survives, but its margin does not** — distortion is a weak secondary reading here.
⛔ **The primary is LESS exposed, not unexposed:** `loss = flattened + absent`, and while `absent` never reads a
marker, `flattened` does, so the same volume bias can only push a cell from `flattened` to `preserved`. Measured:
the best-match restriction moves tuning Δloss **−13.3 → −6.7 pp** and pooled **−4.0 → 0.0 pp**, and leaves
the **holdout Δ unchanged at +10.0 pp** — it can move the decision input's neighbours, not the decision input.

**⚠️ A confound that must be stated, and it is not sampling noise:** the repo's extractor moved between the
two runs (`main` advanced 8 days), so part of the swing may be code, not sampling. The comparison is valid
**within** each run (both arms fed the same code, the same prompts and the same `edus`, differing only in
`story`), but the **run-to-run** delta cannot be attributed to any one cause.

#### ⛔ ⇒ D3-Option 4 STAYS PROVISIONAL — the measurement cannot decide it, and that IS the verdict

**This question needs no owner verdict, and that is a recorded protocol, not an interpretation:**
`EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md` §16 (the decision-protocol run, 2026-09-23) carried the row — quoted
here **as it stood before this change amended its second and fourth cells** —

| decision | contradiction test | needs the owner? | artifact / action |
|---|---|---|---|
| **D3: raw vs narrative** | No contradiction (D3 is provisional) | **no** | ✅ The owner already approved the measurement. **The measurement decides** — not a gate |

**Apply the registered rule to each run, in the order they happened.** The registration was written
**before run 1** (`#7947` comment `6102402694`), so run 1 is the pre-registered run and run 2 is a repeat.

| run | holdout Δ(loss) | Δprecision | Δrecall | branch 1 (fires if Δ > +10 or a P/R drop > 5) | branch 2 (Δ within ±10, P/R within ±5) | what the rule returns |
|---|---|---|---|---|---|---|
| **run 1** — the registered run (2026-10-10) | **−60.0 pp**† | −0.8 pp | +24.2 pp | does not fire | does **not** apply (Δ is −60) | **branch 3: stays provisional** |
| **run 2** — repeat (2026-10-18) | **+10.0 pp** | +0.8 pp | +3.0 pp | does not fire | **applies literally** | **branch 2, but not certifiable** (below) |

† Run 1's magnitude is a **RECORD, not a derivation**: it comes from the committed
`results/7947-score-all.json`, and run 1's per-session arm receipts were not retained, so the figure cannot be
regenerated from this repository the way run 2's can. The caveat is repeated here because a table row is what gets
quoted — a derivation-shaped number with no provenance signal reads as reproducible.

**⇒ Run 1 — the run the registration actually fixed — lands in branch 3 by the rule's own terms, and that is
the verdict: D3-Option 4 stays PROVISIONAL.** No post-hoc rule is needed to get there: the narrative looked
*better* on the holdout by 60 pp, which is neither "the falsifier fired" nor "near parity", and the rule's third
branch is exactly "anything else → provisional". The measured cost is recorded in §10. ⚠️ And run 1's own
magnitude did not replicate, which is the reason to keep reading rather than to settle.

**Run 2 is where it gets uncomfortable, and the honest description is not "branch 3 applies".** Its holdout
Δ lands exactly on the branch-2 ceiling and **branch 2 applies literally** — but it cannot be *certified*, and
that is a different statement:

- **The registration has no branch for "parity met, but the run cannot be trusted".** Inventing one and calling
  it branch 3 would misdescribe the rule, so `score_qualifiers.py` records the outcome as
  `provisional (branch 2 met, not certifiable; branch 3 not reached)` and says so on the console. The EFFECT is
  branch 3's (stays provisional); the ROUTE is not.
- **The decisiveness of a repeat is itself unallocated.** A pre-registered single run is the run the rule was
  written against; a repeat is evidence, not a replacement. Had run 2 been clean, that would have been an
  argument for reopening the question — it is not, because it is not clean.
- ⚠️ **The one incomplete receipt biases the two LEGS in OPPOSITE directions, so "the bias flatters the
  narrative" was only half the story.** `wp07`'s truncated raw arm drops a tail of claims, and the two legs
  react to that differently:
  - **Δloss (pro-narrative).** Dropping claims can only remove opportunities to count a loss, so raw's loss
    is **overstated** and Δ = narrative − raw is **under**stated: the true holdout Δ is ≥ +10 pp, and one
    qualifier moving in that cell puts it at +20 pp — past branch 1.
  - **Δprecision (contra-narrative).** Precision is a RATIO, so dropping a tail of *unmatched* claims
    **raises** raw precision (78/119 = 65.5% → 78/109 = 71.6%), which **lowers** Δprecision (63.1 − 71.6 =
    −8.5 pp) and pushes branch 1 the other way. That is why the scorer's contamination gate is one-sided per
    LEG: only a dirty narrative receipt suppresses the loss leg, while the P/R leg is suppressed by a dirty
    receipt in either arm.

  **So the parity reading is not conservative, and it is not uniformly flattering either** — one channel
  flatters the narrative and the other works against it, on the same receipt. The honest statement is that
  the run cannot be certified in either direction, which is what the verdict records.

**⛔ Why neither run can settle it — and the first reason is a defect in the REGISTRATION, not in the run:**

1. **The registration specified a design whose falsifier cannot fire for the question it was written about.**
   §S2.1 (2026-09-23) recorded that the code passes the raw alongside the story and concluded *"the measurement
   (§10) must compare **narrative + raw** against **raw alone**"* — and the registration's own line says both arms
   use *"the same `edus`"*. So the registered contrast measures **what the narrative ADDS**, which is the smaller
   question §S2.1 itself asked; it does not measure **whether a narrative could REPLACE the raw**, which is what
   D3's provisional flag is actually about. Both arms carry the full transcript (block at the top of this
   section), so a narrative that dropped a hedge would still have it available. **A non-firing result here says
   nothing about replacement** — and that was true before any data existed, which is why it cannot be waved away
   as a caveat on one run.
2. **The decisive arm of the repeat ran partially.** `wp07` raw is `partial: true` and supplies 3 of the
   holdout's 6 raw losses; one qualifier moving there turns +10.0 pp into +20 pp — branch 1. This disqualifies
   **certifying a branch**, not the run: the run happened and its numbers are recorded.
3. **n = 25 (10 holdout) is narrower than the effect it must resolve, and the subset flips sign.** One qualifier
   is 10 pp on the holdout — the width of the entire registered band; the bound-qualifier subset is +16.7 pp on
   the holdout and −10.0 pp on the tuning half. Neither direction is a finding at this n.

**⇒ Therefore: Option 4 stays PROVISIONAL.** Run 1 gets there by the rule (branch 3); run 2 gets there because
branch 2 cannot be certified. **Both runs arrive at the same verdict by different routes, and neither is a
failure of the work** — it is the question, not the answer, that turned out to be mismeasured.

**What the two runs DO establish, stated at the strength the instrument supports:** *within the shipped shape
(narrative + raw vs raw alone), the two arms are indistinguishable at this n* — pooled 52.0% vs 48.0% in
run 2, 80.0% vs 48.0% in run 1, and **the incomplete raw receipt biases the two legs in opposite directions**
(its dropped tail overstates raw's loss, understating Δ, while the same dropped tail of unmatched claims raises
raw's precision, overstating the precision drop — see the per-leg note above). That is **not** evidence that a
narrative could replace the raw, and it is **not** an established "no degradation": the holdout's bound subset
is +16.7 pp the wrong way.
Given the instrument, **neither parity nor superiority is established.** §S2.4's raw-fact layer and §10's
statement that raw is required independently (a summary cannot see prior state; re-extraction is always from
raw) are untouched and do not depend on this result, so the design continues on Option 4's *shipped shape* while
the flag stays up.

**What would actually settle it — the next measurement, not another reading of these two runs:**

1. **Replace, don't add:** two arms that differ ONLY in what carries the conversation — narrative with the
   transcript suppressed (`edus=None`) vs **raw with the transcript suppressed too**. That is the only
   configuration in which a narrative-borne loss can show up at all, and it is a different experiment from this
   one, not a re-run of it. ⛔ **It does NOT control for length, and the next lane must not think it does:** with
   the transcript suppressed the raw arm's `story` IS the whole conversation, so the prompts differ by Δstory
   (1.0–2.1 k chars on these fixtures) — and a shorter input yields fewer claims per se, which is a competing
   explanation for any qualifier the narrative appears to lose. **Add a length-matched control: the raw story
   truncated to the narrative's character count.** Without it the experiment cannot separate a compression loss
   from an input-length effect, and it would produce a second unfalsifiable result.
2. **Refuse partial receipts** (the scorer now does) and re-run `wp07`'s raw arm if it degrades again.
3. **More qualifiers AND more independent sessions.** ~100 annotated **bound** qualifiers in these fixtures would
   make a 10 pp band resolvable to one qualifier — but it would not touch the variance that actually moved: the
   raw arm swung 28 pp between two runs on this same corpus, which is a **run-level** shift (it moves every
   qualifier in a session at once), not qualifier sampling. The registered repeat-run variance check, never run,
   is what bounds that; independent sessions are what average it out.
4. Optionally the registered secondary arm (a fixed fleet-session sample, judge-scored).

**⚠️ One more thing the measurement shows, and it is not about the arms:** qualifier survival is **poor
regardless of input** (48–80% loss). §10's question therefore has a real answer — claim-confidence information
*is* being lost at the S2 layer — and the narrative is not what causes it. That is a fidelity finding for the
S2 work, not for D3.

**⇒ And a SEPARATE premise is in question, and it is E1's.** The narrative arm emitted **more** claims in
aggregate in both runs (152 vs 113; 149 vs 119); per session it led in 7 of 7 sessions in run 1 and 6 of 7 in
run 2 (`wp06_quarry_rollout` inverted). So the narrative is **not** a volume-reduction mechanism at the S2
layer, which leaves E1's volume premise unsupported. **This is NOT a demand for a volume target** — §16.3's O2
forbids one outright ("no node budget anywhere in this document") and §1's volume row is a direction, not a
promise. Recorded here because §10 produced it; the E1 verdict belongs to `#7949`.

### ✅ A STATED reason for raw extraction — 2026-09-23

Hindsight is the first system found with a *stated* reason, and it supports **Option 4**:

- **A summary cannot see prior state, so its counts and absences are batch-scoped and misleading.**
- **Summaries are marked UNTRUSTED downstream** — *"a reading aid, not evidence"* — editing on their strength is forbidden.
- **Re-extraction is expected and always from raw:** changing the ingest config *"means reprocessing documents, which re-extracts from the original text and therefore discards any curation you had already done."* **Raw is the re-derivable ground truth; curation on derived facts is not.**

**⇒ Option 4 is evidence-backed.** The narrative stays the cheap searchable tier and a *focus*; extraction keeps access to raw as the source.

### ⛔ The S3 prior-injection hypothesis — TESTED 2026-11-01, and it is FALSE (`#10463`)

`#5087`'s central hypothesis — *"telling extraction what already exists in the graph (S3
prior-injection) reduces what it creates"* — was unmeasured. It is measured now, as a pre-registered
two-arm ablation on the frozen `tests/eval/write_path` corpus (`fixtures_hash
sha256:dd7e8520…`, 7 sessions), pre-registration commit `a1075dfc2`, bundle
`docs/evidence/5087-hypothesis/`.

**RESULT: `HYPOTHESIS-FALSIFIED`.** Two runs on this corpus report **`0.870`** (run 1) and **`0.988`**
(run 2, the run whose ordering provenance is whole); **neither clears the pre-registered `<= 0.75`
bar.** ⛔ **Quote `0.988` — run 1's log was withdrawn from the checked set by the ruling on `#10463`,
and the `0.870` magnitude overstates the effect.** Read *"Which run's number"* below before quoting
either figure; the table that follows is run 1's, retained because it is what the withdrawn log says.

| | S3-OFF | S3-ON | bar |
|---|---:|---:|---|
| `created_new_nodes` (extraction claims + minted Object/Event) | 284 | 247 | ratio `<= 0.75` → **0.870, NOT met** |
| planted units recovered (of 90) | 47 | 40 | must not fall → **FELL** |
| operator-type edges | 171 | 139 | must not fall → **FELL** |
| model calls | 21 | 27 | `<= 2.00 ×` → 1.29 × |
| arm integrity | 7/7 suppressed | 7/7 resolved | 14/14 |

**What this changes.** S3's prior injection does **not** reduce what extraction creates by the margin
the design assumed: it cuts created nodes **13 %** and recovered real content **15 %**, so
created-per-real-fact moves 6.04 → 6.18 — a smaller extraction, not a leaner one — and it costs
**more** (21 → 27 model calls) because S4 has more to review. The per-session picture is not uniform
(range 0.706–1.029; 5 of 7 sessions fell, one rose, one flat), and the one session that is blind in both arms
(`wp01`, fresh graph) still shows −13 %, which is the run-to-run noise floor at this n.

### ⚠️ Which run's number — run 1's log was WITHDRAWN, and run 2's `0.988` is the figure the epic quotes

**Run 1** (`s3-ablation-a1075dfc.log`, ratio `0.870`, the table above) was ruled **withdrawn from the
checked set** (`#10463`, ruling comment `6564634454`, carried by PR `#10544` as `108cec779`): its
pre-registration and its log landed in ONE squash commit (`cfa3de857`), so the ordering property —
the registration strictly precedes the scored log — could not be shown from the merged history. Its
**contents are untouched** and **no number in it is retracted**; it sits in
`docs/evidence/5087-hypothesis/withdrawn/` beside a note,
`docs/evidence/5087-hypothesis/withdrawn/WITHDRAWN.md`, that states exactly what is and is not
retracted, and the guard's glob does not read that directory.

**Run 2** is the re-measurement the ruling ordered — the registration already published on the base
branch, bars read from the registration *commit* (`--prereg a1075dfc2`), corpus hash re-derived, fresh
graph per arm (`s3-ablation-a83c23a8.log`, PR `#10554` as `45120abbd`):

| | S3-OFF | S3-ON | bar |
|---|---:|---:|---|
| `created_new_nodes` | **253** | **250** | ratio `<= 0.75` → **0.988, NOT met** |
| planted units recovered (of 90) | 41 | 44 | must not fall → met |
| operator-type edges | 141 | 166 | must not fall → met |
| model calls | 21 | 27 | `<= 2.00 ×` → 1.29 × |
| arm integrity | 7/7 suppressed | 7/7 resolved | 14/14 |

**The verdict agrees; the magnitude does not, and `0.870` overstated it.** `0.870` reads as *"priors
cut creates by about a seventh"*; `0.988` reads as *"no measurable effect — 253 vs 250"*. The reason
the second reading is the honest one is a fact about the INSTRUMENT, not a preference for a friendlier
number: the **untreated** arm drifted **284 → 253** between the two runs (**31** nodes, **10.9 %**),
about **ten times** the 3-node ON−OFF difference the second run measured, so this instrument cannot
resolve an effect below roughly **11 %** between runs. The defensible sentence is therefore *"no
measurable effect at ~0.99, well inside the ~11 % between-run drift this instrument shows on its
untreated arm"* — ⛔ **not** *"priors cut creates by 1 %"*. If one number is needed, quote the pair
(`253 / 250`) beside the drift, or quote `0.870` and `0.988` together as the range this corpus
supports. `0.988` is the figure the epic's record quotes (`#5087`).

⚠️ **A verdict is a verdict, not a prompt for a re-run.** `#10463`'s own falsifier rule applies: a
ratio near 1.0 closes the issue and changes the epic's plan. It is **never re-run until it agrees**.
The one re-run that happened after the first was ordered by the ruling on `#10463` and existed to
repair an **ordering provenance**, not to chase a number — and it came back *worse* for the hypothesis
(`0.988` against `0.870`), which is what a sanctioned re-measurement looks like and what an
agreement-seeking one could not have produced. The epic's plan point that assumed prior-injection buys
volume must now argue from something else — and the tension rule it registered as a guard is exactly
what failed in run 1: the smaller writes were smaller, not better. **Run 2 does not reproduce even
that**: its guards met (real facts rose 41 → 44, edges 141 → 166), so on the base-registered run the
treatment neither bought volume nor cost it — it did nothing measurable.

⚠️ **One instrument gap this run found and did NOT fix here** (it is a follow-up, not a production
edit in the measurement PR): the `capture_session` lane's receipt carries **no `search` block**, so
`#5087`'s indicator 3 — S3 state "asserted from the run receipt" — is not satisfiable on that lane at
this head. The probe asserts it from the value the S3 function actually returned per session
(recorded by a wrapper around the real function / the OFF stub), which the bundle discloses.

---

## 11. Verification against Hindsight (Vectorize) — 2026-09-23

**Why checked:** Hindsight's paper proposes `world` / `experience` / **`opinion`-with-confidence** / `observation` — the closest comparable to our contention model. Repo `vectorize-io/hindsight` (**MIT**) read at HEAD `f00ad1fe`; paper arXiv **2512.12818**; ACL 2026 demo `2026.acl-demo.27`.

### VERDICT: it COMPLICATES our model — a comparable built our closest feature, then deleted it

| | |
|---|---|
| **Paper (Dec 2025)** | `opinion` = `(t, c, τ)` with **`c ∈ [0,1]` confidence**, updated by an LLM verdict `Assess(o,f) → {reinforce, weaken, contradict, neutral}` via `c ± α` |
| **Shipping code (2026-09)** | **`opinion` REMOVED. `confidence_score` COLUMN DROPPED** (migration dated **2026-04-02**). Constraint is now `fact_type IN ('world','experience','observation')`. |
| **Trail** | 2026-01-15 delete opinions → 01-21/26 `mental_model` → renamed `observations` → **04-02 drop `confidence_score`** |
| ⚠️ **The vendor still advertises it** | The blog and the **July 2026 ACL camera-ready abstract** still claim *"evolving opinions with confidence scores"* — **three months after the column was dropped.** The docs caught up; the marketing did not. |

### ⛔ No typed contention relation has ever existed

```python
CheckConstraint("link_type IN ('temporal','semantic','entity','causes','caused_by','enables','prevents')")
```

**Seven edge types — none adversarial.** The `contradict` verdict was never persisted as an edge.

### What replaced confidence — and it is genuinely cheaper

1. **`proof_count`** — *"not an increment counter, but a **count of distinct source facts, recomputed** each time new sources fold in."*
2. **`Trend`** — a qualitative band computed **algebraically from evidence timestamps**: `STABLE / STRENGTHENING / WEAKENING / NEW / STALE`.

**Neither is assigned at write time; both are derived from the evidence. Neither can be attacked.**

### The comparison that matters

| system | two conflicting claims | contention relation? | confidence? |
|---|---|---|---|
| **GraphRAG** | erased | ❌ | ❌ |
| **Graphiti/Zep** | replaced (`invalid_at`) | ❌ | ❌ |
| **Mem0** | accumulated, no relation | ❌ | ❌ |
| **Hindsight (paper)** | accumulated + scalar-updated | ❌ | ✅ per-opinion float |
| **Hindsight (code)** | **replaced/deleted — latest `mentioned_at` wins** | ❌ | ❌ **removed** |
| **Ours** | **held in tension** | ✅ typed IMPL/NAND | ✅ per-claim, **propagated** |

**No comparable holds two conflicting claims in tension with a typed relation.**

### Where Hindsight refuses to erase — in prose, not the schema

At answer time the rule is recency-precedence (*"the fact with the LATEST `mentioned_at` is authoritative"*) — **but when the temporal rule cannot settle it, the model is told to name the conflict:**
> *"When the data is genuinely ambiguous: SAY SO in your answer. Name the conflicting facts. … Acknowledging ambiguity is a successful answer, not a failure mode."*

**⚠️ So their contention hedge is a sentence the model writes and then discards — not a stored object.** They consider that sufficient in production and on SOTA benchmarks.

---

## 12. Adoptable vs REFUSED (contradiction test applied)

### ✅ ADOPTABLE — contradicts no recorded decision

| finding | why we want it |
|---|---|
| **Raw layer append-only on purpose** | *"A retain path that dropped a fact because it resembled one already stored would be a retain path you could not trust to have kept what you sent it."* Our own argument, independently arrived at. |
| **Anti-collapse rule in dedup** | *"If they differ in ANY important detail — a number/quantity, a named entity or language, **a negation**, or a condition — set `action` to `keep`."* A deliberate refusal to collapse a difference; mirrors our atomicity constraint. |
| **Invalidation by RELOCATION, not a flag** | `{"state":"invalidated"}` *"does not set a flag to be filtered later. It **moves the row out of the active table into a separate archive** … So recall needs no state predicate… **no query pays for your cleanup**."* Causal edges snapshotted onto the archived row. **Reversible.** Composes with D7. |
| **Provenance ids are an ALLOWLIST, never an exclusion list** | Because `based_on` carries ids addressing *different tables*, an exclusion-list check *"would report every one of them as missing."* A transferable bug we should not rediscover. |
| **Evidence carries exact quotes** | `ObservationEvidence{memory_id, quote, relevance, timestamp}` — *"Each evidence item must include an exact quote."* The same instinct as our `quote` field (`#1509` E3). |
| **Every derived belief carries a machine-readable back-reference** | `source_memory_ids` + `based_on` is what lets them detect a conclusion that lost its grounding. |
| **Extraction from raw; summaries marked untrusted** | ✅ **confirms D3-Option 4** (§10). |
| **No event node in provenance** | ✅ **confirms D2's rule** — the anchor is always a document/utterance. |

### ⛔ REFUSED — contradicts a recorded decision ⇒ a REOPEN, not an adoption

The contention + confidence model **is a recorded decision** (`ONTOLOGY.md` §3.1 *"evaluations … are Statements (Points) with EP confidence — not edges"*; the operator layer; `#1509` §2.4 ontology-first). **Hindsight's practice contradicts it, so it cannot be adopted.**

> ### ✅ REOPEN CLOSED — the owner ruled on 2026-10-06: we KEEP EP confidence; `proof_count` and `Trend` are NOT adopted
>
>
> ⚠️ **Two claims, and only one is established.**
>
> **(1) The representational difference is a FACT.** Hindsight's edge constraint is seven types — `temporal, semantic, entity, causes, caused_by, enables, prevents` — **none adversarial**, and its `contradict` verdict was never persisted as an edge (§11). It therefore **structurally cannot** store a contention **as a typed relation** — that is a fact about its schema, not a claim that it cannot hold two opposing statements at all (§11 records that it refuses to erase them *in prose*). We can and do store one as a typed relation (typed `IMPL`/`NAND`). *"Can it represent a contention at all"* is settled **by construction** — and that is the load-bearing part of the ruling.
>
> **(2) The outcome difference is UNMEASURED.** That propagation over a typed graph produces *better answers* than scalar drift remains **argument, not measurement** — the two challenge answers below say so explicitly, and it stays true. Challenges **1** (they built it and deleted it in ~4 months) and **4** (justify the cost on audit, adjudication, or propagation) still want a **measurement**. **Nothing here may be read as *"we proved propagation beats scalar drift."***
>
> **The ruling closes the DIRECTION, not the MEASUREMENT.**

The five challenges this comparison raises:

1. **⭐ They built `opinion`+confidence and deleted it in ~4 months.** If we cannot say what we do *differently* from `Assess(o,f) → c ± α`, then "per-claim scalar confidence updated by LLM evidence-assessment" is a **demonstrated dead end**, not a novel design.
   **→ Our answer:** theirs is **evidence-driven scalar drift** — each new fact independently bumps each opinion it resembles, with **no support graph to traverse**. Ours is **propagation over a typed factor graph.** Different mechanisms. **But the difference is currently argued, not measured.**
2. **No typed support/attack exists in the nearest comparable** — 7 edge types, none adversarial. The burden is on us to show why an **edge** beats a **prompt rule + recency precedence**.
3. **They resolve; we hold.** *"the page says Y — and can say why — instead of preserving both a paragraph apart."* A **deliberate ruling against our shape.**
4. **Their hedge is in prose and they consider it sufficient** at production scale. **Our contention-as-graph-structure is a real cost that must be justified on something their benchmark does not measure: audit, adjudication, or propagation.**
5. **⭐ `proof_count` + `Trend` may be the cheap 80%.** A recomputed distinct-source count plus evidence-recency bands deliver a corroboration signal with **zero** LLM belief-judgement and **zero** propagation arithmetic. **These are the parts Hindsight kept.**
   **→ Our answer:** `proof_count` measures corroboration by **independent mentions**; EP confidence measures **whether a claim survives its attackers**. Hindsight cannot represent *"ten supporting mentions AND contradicted by a stronger argument"* — it has no place to put the attack. **But this too is argument, not measurement.**

### The sharpest open question this leaves

**Hindsight's answer to "how do you avoid erasing a real disagreement?" is: keep both at the evidence layer, resolve at the conclusion layer, refuse to pick when the temporal rule does not settle it — and say so in words. Our answer is to make the disagreement itself a stored object.** Nothing found contradicts that being *possible*; nothing found supports it being *necessary*; and the one system that had the closest feature **removed it.**

### Not established

- **No independent evaluation or critique of Hindsight was found.** Everything beyond the ACL peer review is vendor-reported; the benchmark claims are **vendor-asserted** (**not** verified here).
- **The reason for the deprecation is NOT public** — the code shows the arc, not the reasoning. Whether it was a *quality* judgement (confidence wasn't useful) or an *architecture* migration (folded into mental models, which then lost the scalar) is **unresolved**. If we want it before touching our own confidence field, it is not in the repo or the paper — **it would have to be asked of the maintainers.**
- All code findings are **static reads**; no retain/recall cycle was executed.

## 13. How Hindsight actually finds connections and relevance — read from the code (2026-09-23)

**This is the section that matters** — *not* their confidence field. Hindsight's connection machinery is read straight from `engine/retain/link_utils.py` (45 KB), `link_creation.py`, `causal_links.py`, and `engine/search/link_expansion_retrieval.py`. All static reads at HEAD `f00ad1fe`.

### 13.1 The shape: bounded seeds, then bounded expansion

Recall starts from **`GRAPH_SEED_LIMIT = 20` semantic seeds**, then *expands* through the stored signals. It never searches everything and never asks a model whether two facts relate.

### 13.2 There are exactly THREE connection signals

| signal | how it is produced | score |
|---|---|---|
| **Entity** | at **query time**, a self-join through a `unit_entities` mapping table — **no stored edge** | `COUNT(DISTINCT entity_id)` shared with the seed set |
| **Semantic** | **precomputed at insert time** — a kNN graph, each new fact linked to its nearest existing facts **above a cosine threshold** | the cosine similarity (`weight`) |
| **Causal** | **LLM-extracted during fact extraction** | `weight + 1.0` — *"boosted as highest-quality signal"* |

**That is all of it.** Two of the three are mechanical; only causality needs a model.

### 13.3 ⭐ Where the LLM is allowed to connect — and the bound is brutally tight

The whole causal prompt is:

```text
Link facts with causal_relations (max 2 per fact). target_index must be < this fact's index.
Type: "caused_by" (this fact was caused by the target fact)
```

**Read the three constraints:** `target_index` refers to a fact **in the same extraction batch**; it must be **earlier in the batch** (`< this fact's index`); and a fact may propose **at most 2** such links.

**⇒ The LLM never sees the graph and is never asked to find connections to it.** Cross-batch connection is **mechanical only** (ANN + entity join). The model is a *within-batch, backwards-only, fan-out-2* proposer.

### 13.4 Every expansion is bounded, and one bound is a timeout

| bound | value |
|---|---|
| seeds | `GRAPH_SEED_LIMIT = 20` |
| ANN neighbours per fact | `top_k = 50` |
| **causal links per fact** | **2** |
| temporal links per fact | `MAX_TEMPORAL_LINKS_PER_UNIT = 20` |
| **entity fan-out** | **`graph_per_entity_limit = 200`** (a LATERAL cap, *"to prevent high-fanout entities from exploding the self-join intermediate rows"*) |
| **when over budget** | **`graph_expansion_timeout` DROPS entity expansion entirely** |

**They treat connection fan-out as a resource to be bounded, not a quality signal to be maximised** — and they would rather lose a whole arm of recall than exceed the budget.

### 13.5 Relevance = fusion of independent legs, not one judgement

Recall legs (semantic · BM25 · graph · temporal) are merged by **reciprocal rank fusion** (`k = 60`) and then **reranked** by a cross-encoder. **No LLM decides whether a fact is relevant.** Relevance emerges from independently-ranked lists being combined.

### 13.6 ⭐⭐ THE FINDING THAT BITES US: they materialised entity edges — then DELETED them

From migration `e9b2c7d1f3a4_drop_entity_memory_links.py` (2026-05-26):
> *"Entity edges are no longer stored in `memory_links`. The /graph endpoint derives them on demand from `unit_entities`, and recall already used the `unit_entities` self-join. **Storing entity rows duplicated state we never read from the link table — on a 10k-unit benchmark bank, entity rows were 53% of all link rows (~190 MB after indexes) and recall never touched them.**"*

**Our `aboutObject` is the single largest edge type in our graph at 27,310 edges.** This is the comparable's measured answer to exactly that shape, and the answer is: **the edge rows were 53% of all link rows, recall never read them, so they were deleted and the relationship is derived at query time from a mapping table.**

⚠️ **This does not automatically transfer** — their `/graph` endpoint *does* derive the edges on demand, so the capability is preserved — and **the measurement of that precondition (2026-09-23) SPLITS the answer** (full enumeration in §13.8 question 2, and `STORAGE-ARCHITECTURE.md` §13). **The default FTS/vector/RRF retrieval lane does not read the edge — and the three modules that measurement covers (`retrieval.py`, `search_engine.py`, `session_reinjection.py`) are the whole of it, so that scope IS the claim rather than a sample of the product. `tortoise/ranking.py` is NOT in that list, and it is where the product ranking reads live; at least NINETEEN *customer-callable* sites DO** — ⚠️ **and the door count is NOT restated here.** §13.8 gives it, derived from `STORAGE-ARCHITECTURE.md` §13's reach cells by the guard; a second copy of that figure in this paragraph is how this document once carried a door total that disagreed with §13.8 a screen below it. **A restated count is a copy that rots on the next pass** — the rule §13 already applies to the prior-art row. ⇒ **Hindsight's premise holds for the lane those three files bound and NOT for the default recall door (`tortoise_recall` → `recall_state`, which ranks and enriches straight through `ranking.py`), so the comparable's conclusion does NOT transfer.**

### 13.7 Two more transferable mechanics

- **Precompute the expensive lookup OUTSIDE the write transaction** (`pre_computed_ann_links`, "Phase 1"): *"Runs on a separate connection OUTSIDE the write transaction to avoid holding locks during expensive HNSW index probes."*
- **Split ANN queries per `fact_type` so the planner uses partial HNSW indexes** — *"Without the fact_type filter, the planner falls back to sequential scan (~50x slower)."*

### 13.8 What this does to our design

| our step | their practice | verdict |
|---|---|---|
| **S5 CONNECT — two sets, within-batch *and* to-graph** (D6) | LLM does **within-batch only**, max 2, backwards; **to-graph is mechanical** (ANN + entity join) | ⚠️ **A direct challenge to D6** — D6 is the decision that stands; the challenge is evidence for the owner, **not** a change we may make. |
| **S3 MATCH → AND ATTACH** | entity resolution is a **join key**, and the link rows were deleted as pure storage cost | ✅ **consistent** — and it says our attach step should cost little. |
| **minting an `Object` per entity** | entities are extracted to **link facts** (*"Extract anything that could help link related facts together"*) and kept in a mapping table | ⚠️ **The strongest available support for the `^the <X>` finding.** Their entity extraction *purpose* is linking; ours has become node creation. |
| **connection fan-out** | bounded everywhere, with a timeout that drops an arm | ✅ **The cap is the design** — `PER_ENTITY_FANOUT_CAP = 200` (`tortoise/fanout.py`), consumed by `assembly.py` / `subgraph.py`. The cap sits above the current worst hub (123), so it binds nothing today and cannot lose data now. <!-- v4-mechanism: fanout_cap state: implemented --> |

**⇒ The three questions below: question 1 is OPEN; question 2 resolves to a STORED edge; question 3 resolves to a hard fan-out cap of `PER_ENTITY_FANOUT_CAP = 200`:**

1. **Should the LLM be allowed to propose connections to the graph at all**, or should cross-batch connection be mechanical (embedding + entity) with the model confined to within-batch, as Hindsight confines it?
2. **Should `aboutObject` be a stored edge or a derived join?** ✅ **MEASURED 2026-09-23 — the answer is SPLIT; the design answer is KEEP the stored edge.** **The default FTS/vector/RRF retrieval lane does not read it** (`grep -in aboutObject tortoise/retrieval.py tortoise/search_engine.py tortoise/session_reinjection.py` → exit 1, case-insensitive) — those three files are the whole of what that measurement covers, and `tortoise/ranking.py` sits outside them: it reads the edge literally and through its `ABOUT_EDGE_TYPES` alternation, and the SDK's `_ABOUT_TYPES` carries it into the `recall_state` enrichment readers behind the default recall door (`tortoise_recall`). **So Hindsight's premise holds for the lane those three files bound, and NOT for the default recall door.** **At least NINETEEN sites are customer-callable, reached through at least TWENTY-TWO distinct doors** — ten MCP tools, eight SDK methods and four REST routes, counted separately because each is an independently callable entry point (the breakdown is derived from `STORAGE-ARCHITECTURE.md` §13's reach cells by `tests/test_7948_about_object_read_sites.py`, so neither figure is hand-counted). `recall_state` (MCP `tortoise_recall(mode="state")`) reads the edge on FOUR paths — `ranking.py::StateRanker._fetch_point_signals` (literal), `ranking.py::StateRanker._fetch_object_signals`, `sdk.py::TortoiseSDK._state_related_objects` and `sdk.py::TortoiseSDK._state_related_points` (the last three through an `about*` alternation CONSTANT, so no grep finds them); the other FIFTEEN sites are: `belief_timeline` (MCP `tortoise_belief_timeline`) · `topic_summarize` — the reader is `topic_summarization.py::_retrieve_topic_neighborhood`, which builds the edge name at RUN TIME (SDK `topic_summarize` + hosted REST ⚠️ **its MCP twin `tortoise_topic_summarize` is registered nowhere and never served; the capability survives on the SDK + REST doors**) · `recall_subgraph` — `ranking.py::SubgraphExpander._neighbors`, whose default `completeness="full"` walks **every** relationship type via `MATCH (n)-[r]->(m)`, so it reads the edge **without naming it** and is therefore invisible to any grep-based enumeration · graph-ordered `search` — the reader is `ranking.py::GraphRanker._fetch_event_signals`, reached by SDK `tortoise_fts_query(entity_type="event", order_by="graph")` (MCP `tortoise_search`) · `navigation.py::_hop_query` — the SECOND all-edge walk, behind MCP `tortoise_entity_profile` and `tortoise_traverse` · `mcp_server.py::_preview_supersede` — the supersede DRY RUN, which matches every `SUPERSEDE_STRUCTURAL_RELS` member · `mcp_server.py::_preview_delete_edges`, the delete's blast radius, which matches every incident edge of the doomed nodes · `hosted_api.py::_export_graph_snapshot`, the org export, which enumerates every edge with no rel-type filter · **and the FIVE all-edge reads on the backup door and the backfill** — the dump behind `hosted_backup.py::dump_graph` (its `_DUMP_EDGE_PAGE_QUERY` pages **every** edge out as data into the per-org encrypted backup), the whole-graph counts behind `hosted_backup.py::_independent_counts` (its `_COUNT_ALL_EDGES_QUERY`), `hosted_backup.py::restore_graph` and `hosted_backup.py::_restore_copy_settled` (which verify a dump, take a restore's baseline and settle it), and `sdk.py::TortoiseSDK.backfill_v25` (the all-edge count handed back as `report["edge_count"]`). ⚠️ **The backup readers are the ones the ruling turns on:** §13's durability bullet is about this edge class, and the backup is the durability mechanism, so *would a `SET`-free derive change what the customer can restore?* is answerable only from these rows. **Two are CALL-TIME doors, and no name-based scan can see either:** `sdk.py::TortoiseSDK.traverse` fills the relationship type from a CALLER ARGUMENT (gated only by `validate_rel_type` against `KNOWN_REL_TYPES`, which carries the about\* family), so `aboutObject` is read whenever a caller asks for it · `sdk.py::TortoiseSDK.ingest` fills it from the bundle's `relation`, allowlisted against `_VALID_EDGE_PREDICATES` (about\* family included) — the connection DUPLICATE PROBE, a read on the write path. Between them they are why the class is DERIVED rather than declared: `tests/_about_call_time_census.py` resolves the type slot through loop variables, `for rel in CONSTANT` loops and parameter defaults, and the guard reds when a derived site owns neither a row nor a stated exclusion. **Outside this nineteen, two are default-ON on the ingestion path:** `extractor_v2._enrich_point_priors` and `mining._temporal_wire` (**2-hop, and it mints a NAND**). **Off by default:** `_entity_key_expansion_pass` (`entity_key_expansion: bool = False`) and the connected-assembly reads (`TORTOISE_ASK_CONNECTED_ASSEMBLY` default OFF). ⚠️ **Three readers of the family are deliberately NOT in this nineteen: `sdk.py::TortoiseSDK.supersede_point`, `sdk.py::TortoiseSDK._promote_incident_operators` and `domain_validators.py::validate_portfolio_shape`** — `tortoise_supersede(dry_run=False)` (the default) reaches `supersede_point` and it is a public `TortoiseSDK` method, so it IS a door into the edge; but it MOVES the edges rather than reading them to answer a customer, so `STORAGE-ARCHITECTURE.md` §13 rows it as `write path`; `_promote_incident_operators` only GATES a promotion on the endpoint status of the edges it walks; and `validate_portfolio_shape` DOES read the edge for a customer (MCP `tortoise_validate_domain` / SDK `validate_domain`) but its dispatch is a `@domain_validator` REGISTRY lookup (`spec["fn"](graph)`), so no static reference chain runs from either door to it and §13 classes it `ops audit` rather than assert an attribution nothing verifies. ⚠️ **That third exclusion is why the count above is a FLOOR and not an exact figure** — the true count of customer-callable readers is larger by at least that row, and the figure is stated as *at least* for that reason. **36 test files MENTIONED the edge at the pin; 59 did at `26aff163c`** (a count of files that mention it — the only thing a grep can measure) (`rg -l aboutObject tests/ | wc -l`). ⚠️ **A name-based enumeration undercounts the customer-callable surfaces: only THREE of the nineteen spell the edge out, so a `grep aboutObject` finds three and misses the other SIXTEEN** — the NINE all-edge walks (`recall_subgraph` and `navigation._hop_query`, which walk **every** relationship type, plus `_preview_delete_edges`, `_export_graph_snapshot`, `_DUMP_EDGE_PAGE_QUERY`, `_COUNT_ALL_EDGES_QUERY`, `restore_graph`, `_restore_copy_settled` and `backfill_v25`, which enumerate or count every edge with no rel-type filter), `topic_summarize` (the name is built at run time), and the FOUR constant-carried reads — `_fetch_object_signals`, `_state_related_objects`, `_state_related_points` and `_preview_supersede`. *(A citation here is a convenience, never the authority: `tests/test_extractor_v4_citation_rot.py` resolves every quoted symbol against the file it cites at HEAD, and `STORAGE-ARCHITECTURE.md` §13's table — resolved against the code by `tests/test_7948_about_object_read_sites.py` — is the authoritative enumeration.)* **Deleting the edge would silently break at least NINETEEN customer-callable read sites across at least TWENTY-TWO doors → the comparable's conclusion does NOT transfer.** The structural door stays open **for every UNBOUND link** (the SHARE of the rows that are unbound is not measured — `STORAGE-ARCHITECTURE.md` §11.1): the generic write path emits `MERGE (s)-[:aboutObject]->(t)` and never a `SET` (`projection/edges.py`). An entity link the subject binder wrote also carries `confidence` — `session_link.link_entity`, `subject_binding.bind_point_subjects`, reproduced by the `projection/entities._fold_entity_linked_reason` replay fold; and the record-layer projection, the backup restore and — since `#9417` — the supersede transfer re-materialize that same attribute set by property MAP (`SET r += $props` / `SET r = $p` / `ON CREATE SET nr += $attrs`): `cache_projection.relation_statement`, `hosted_backup.restore_graph` and `sdk.supersede_point`, whose structural transfer carries the OLD edge's properties (whitelisted to the `RelationUpsert.attrs` vocabulary) on `ON CREATE`, so an `about*` edge keeps the binder's `confidence` across a supersede while an edge the successor already holds is left untouched. ✅ **`#10139` then closed the last exemption, so ZERO census rows are declared FALSE POSITIVES.** The rebuild replay `projection._rebuild_all_replay` WAS the last one: its structural-`DirectEdgeRepoint` fold (whose `validate_rel_type`-allowlisted `etype` CAN be `aboutObject`) used to re-create the transferred edge with a separate BARE `MERGE` until `#10139`, so `live == rebuild` did not hold for a transferred `confidence`. Since `#10139` the descriptor carries the retired edge's attributes and that fold re-applies them with `ON CREATE SET nr += $attrs` — the same precedence as the live transfer — so the writer carries `confidence` on an about edge and is a counted MAP-form writer. Its OTHER `SET r += $attrs` (the `DirectEdgeCreated` fold) was always safe and still is: its ATTRIBUTE-CARRYING producer `create_direct_edge` is restricted to `{IMPL, NAND}`, while its second producer since `#9553`, `sdk._record_structural_edge`, journals whatever predicate the structural-edge mints wrote (the whole `about*` family included) and emits NO vocabulary key, so there it is a gate over a fixed attribute vocabulary — the empty attribute map, not a relation restriction — that keeps that statement off an about edge. ⚠️ Both `SET r += $attrs` statements still exist, and `NOT_AN_ABOUT_WRITER` is kept as an EMPTY table with its pinned statement premises rather than deleted, so the first returning row is nameable and must arrive with its own premise. The record-layer projection also stamps the positional `idx` inline (`MERGE (src)-[r:{rel.rel_type} {idx:$idx}]->(tgt)`, `cache_projection.relation_statement` (the symbol is authoritative; the line number is deliberately omitted)), the one NAMED property beyond `confidence` (the record-map writers' further attributes are an open set the guard cannot bound), which is a position and not a claim attribute — so deriving loses `confidence` and would also have to re-derive `idx`. The ruling below is unchanged. `STORAGE-ARCHITECTURE.md` §13 carries the measured property set and §11.4 the recorded tension with `#1370`. ⚠️ **And deriving is what creates the fan-out problem** — a join on a hub with ~1,200 claims yields ~1,200 rows *per anchor*, which is exactly why Hindsight carries a `LIMIT 200` and a timeout that drops the whole arm. **See `STORAGE-ARCHITECTURE.md` §13 for the full classification and the durability finding, and §11 for why the connection layer is cheap to keep.**
3. **Should every expansion have a hard fan-out cap**, with the option to drop an arm under budget pressure?

---

## 14. Not established — do not treat as known

- **Whether the neighbourhood lookup changes extraction's output is MEASURED — it does not (§10, `#10463`).** Run 1: created nodes fell **284 → 247 (−13 %)** against the pre-registered **≥ 25 %** bar, with both guards — recovered real facts (47 → 40) and operator edges (171 → 139) — falling in the same direction. ⚠️ **Run 1's log was WITHDRAWN from the checked set by that issue's ruling; the base-registered re-measurement (`#10554`) reports `253 → 250` (ratio `0.988`), guards MET rather than fallen — an effect too small for this instrument to resolve against a `10.9 %` between-run drift on the untreated arm.** Either way *"extraction no longer consumes the lookup"* is backed by a number rather than by an argument, and the design conclusion is unchanged: the lookup is not load-bearing, S3 resolves the entity authoritatively instead (§4.2), so extraction does not need to emit *"attach to A"*. **What IS still unmeasured: whether S3's disambiguation is accurate**, and the cheap test is to replay a handful of real sessions **by hand** and show the candidates matched vs created (small-sample-first).
- The `~8.6 Objects/session` figure is **inferred**, not directly measured — `extractedFrom` points at `:Source`, which carries no `id`, so Objects could not be grouped per session.
- **Nothing measures how much of the surviving 2,963 non-`the` Objects is still junk** — `PR #465` proves a non-`the` Object can also be a bare reference.
- The `^the <X>` **pattern** is a **floor**, not the whole fix.

---

## 15. Open questions

**Cross-batch connection — by LLM or mechanically — is open** (§13.8). The other two questions this document raises are answered by the design: **hard fan-out caps apply** (200, `PER_ENTITY_FANOUT_CAP`, §13.8 question 3), and **`aboutObject` is a STORED edge**, not a derived join (§13.8 question 2, §16) — the one remaining unknown, *are those tools ever called?*, cannot be answered pre-launch and is therefore deferred. <!-- v4-mechanism: fanout_cap state: implemented -->

---

## 16. Decisions and where their record lives

**The decisions are below, and they are the design.** §16.3 states the six answers;
§16.4 states what `MERGE` means on near-duplicates. The record of how they were
reached, and of the review cycles that followed, is in
**`EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md`**.

### 16.3 The six decisions (owner, 2026-09-24)

**Full detail: `STORAGE-ARCHITECTURE.md` §14.1.**

| # | ✅ ANSWER | what changes in THIS document |
|---|---|---|
| **O1** | **Durability and rebuildability are two different questions and both hold: DURABILITY = the store's backup; REBUILDABILITY = the journal.** | *The journal is the derived layer's rebuild source; it is not a durability mechanism.* ⚠️ **`rebuild_all` treats an incomplete log as the authority — that is a defect.** |
| **O2** | ⛔ **No volume target.** *"we're not optimising stupidly for a number… this is not a corporate OKR setting exercise. We need to balance multiple things at each step of the pipeline and on each architecture decision."* | **No volume target, no recall floor, no node budget anywhere in this document.** The objective is **great recall and reasoning at an affordable cost**; the method is **manual, step-by-step review** until a calibration set exists. ⚠️ **This constrains *volume targets* only, not §4.2's ordering objective.** §4.2 states its own objective at `:706`: *"The rule is NOT 'cheapest first' — it is **cost per unit of SAFE rejection**"* (§4.2 heading, `:688`). |
| **O3** | ✅ **Emit the DECLARED outcome word (`DISCARD`), matching the engine's existing classifier.** | S2.2's outputs use the declared vocabulary. |
| **O4** | ✅ **Merge IS authorised — with Jev as arbiter over the claim plus narrative/raw data.** *This supersedes the `OVERRIDES:` ruling on `#4899` ("never merging two claims into one"); the later owner ruling replaces the earlier one, which is the only valid way to reverse a recorded decision.* | The admissible set is **`KEEP` / `NOOP` / `DISCARD` / `MERGE`** (MERGE bounded below). ⚠️ **`FUSE` — combining two distinct claims into one new claim — is FORBIDDEN.** |
| **O5** | ✅ **Draft and iterate:** *"we draft something (a prompt, a step of the extraction workflow, etc) and run it and see the result then refine and run again, until good."* | **The working method for every step in §4.1: draft → run → look → refine → repeat.** |
| **O6** | ✅ **A document is a SOURCE** — *"making them sources so our entity layer can be extracted from them… that's the reasoning/knowledge layer."* | **S0 reads a SOURCE, not a `Document` node.** Documents, code files, meeting transcripts and conversations are **the same kind of input**; the entity layer is uniform over them. **Document liveness is a READ of its entities, never a stored field.** See `STORAGE-ARCHITECTURE.md` §9.3. |

### 16.4 ⭐ O4 applied — what "merge near-duplicates" means here, bounded by the evidence

**Owner ruling:** *"yes we can merge near duplicate claims. maybe we can have jev with the claim and the narrative/raw data/both arbiter that."* <!-- v4-mechanism: near_dup_merge state: implemented -->

**Design, as bounded by the research (research §16.3 — `STORAGE-ARCHITECTURE.md`):**

- **The arbiter is Jev**, and the input is **both claims plus the surrounding context** — the narrative, the raw span, or both. *(Which of the three is exactly the thing to settle by the draft-and-run loop, O5. Do not decide it by argument.)*
- **The bar is HIGH, and this is evidence-driven, not caution:** multiple independent sources find over-aggressive consolidation *"destroys specific details needed for factual QA"*, and **one production system deliberately sets a high threshold because a false merge costs MORE than keeping near-duplicates.** Practical thresholds cluster at **~0.95**.
- ⛔ **A merge must NEVER happen across a difference in: a number or quantity, a named entity, a language, a negation, a condition, a load-bearing connective whose role changes across the pair, or a role inversion.** (This is Hindsight's own anti-collapse rule, and it is the concrete form of *"never when something distinguishing is lost"*.) The last two classes are read from the PAIR, not the token: a connective is frame (syntax) or an operator (meaning) by the same spelling, so the boundary compares the two sides' semantic-slot occupancy and refuses a slot each side fills with a member the other lacks — `and`/`or`, `then`/`else`, `than`/`as`, `to`/`from`, `but`/`so`. It is reported as a `substituted_content` difference — or as another identity dimension when the pair differs in that too, since `_boundary` reports the first of `sorted(identity)` (`nor` is also a negator, so `negation`; a co-differing condition reports `condition`). A role inversion is the same PAIR read a different way: the content skeleton is a SET, so two noun phrases that exchange slots around a predicate — or around a frame relator, as in `the key is in the lock` against `the lock is in the key` — carry one content multiset and two meanings, and the boundary refuses the exchange when the FRAME multiset is unchanged (so nothing was re-framed — the legitimately reordering `#4652` pair drops `from`, and an active/passive pair adds the auxiliary and `by`) — (`#5139`, residuals `#5325`), a token sits between the two blocks in EACH claim (the shared content middle, a frame relator, or both; the two-sided read keeps the verdict independent of capture order and the pair is canonicalised so a multi-decomposition pair cannot flip with it, while adjacent blocks are a RE-FLOW kept foldable), and no coordinating conjunction sits in the span the exchange straddles (`_block_exchange` / `_coordination_between`; `#5131`). The coordination exemption is deliberately BROAD — a coordination anywhere inside the span folds, including inside the shared middle and one joining two clauses, a pinned FAIL-OPEN — and it is enumerated by `_COORDINATION_MEMBERS` (`and`/`or`/`nor`/`but`/`yet`), a deliberate SUBSET of the clause-relation slot that excludes `so`; an import-time check refuses `_COORDINATION_MEMBERS` no longer being a subset of that slot (it does not detect the slot growing a NEW member, which the literal pin is what catches). A comma-list reorder whose only separation is a determiner is refused FAIL-CLOSED, because the token stream cannot tell it from a reduced relative. Any relator that is not a coordination is read as an exchange, so a SYMMETRIC relation's arguments in exchanged order is refused FAIL-CLOSED — both claims are kept — whether the relator is a copula (`the owner is the manager` / `the manager is the owner`), a reciprocal verb (`the addon pairs with the plugin`), or a symmetric predicate (`alice is married to bob`, `the file matches the pattern`); telling a symmetric relator from a directional preposition would need semantics the guard does not keep. Whether such a REORDER is refused is decided by ONE condition: `_block_exchange` accepts a decomposition only when a token sits strictly BETWEEN the two content blocks in EACH claim. Where the blocks are adjacent on either side the reorder is a RE-FLOW and keeps FOLDING (FAIL-OPEN in the delete direction) — `from the depot we shipped the crate` against `we shipped the crate from the depot`, `she drove the car to the office on tuesday` against `she drove the car on tuesday to the office`, and `the red car hit the truck` against `the car hit the red truck` (that last pair DOES differ — which car hit which — so its fold is the FAIL-OPEN pinned in the tests). Where the gap holds on BOTH sides the same surface shape is refused (both claims kept) — `in staging the alpha engine processed the delta record` against `the alpha engine processed the delta record in staging`, and `we shipped the crate from the depot to the store` against `we shipped the crate to the store from the depot`. The verdict is read from the PAIR's own spans, never from the moved phrase or its head token: `from the depot we shipped the crate` folds while `from the depot the alpha engine processed the delta record` is refused — same moved phrase, same frame head, opposite verdicts. Telling a moved-phrase reorder from a re-assigned slot needs the phrase's SPAN, which this walk does not keep. The same FAIL-CLOSED direction covers a pair the scan cannot decide: longer than `_BLOCK_EXCHANGE_MAX_TOKENS` (the scan is not attempted) or `_BLOCK_EXCHANGE_BUDGET` decompositions without a verdict — the scan is bounded so one comparison cannot run away on the capture path. These cases are the ones FOUND AND PINNED so far and the list is NOT a closed set: a construction the reading cannot distinguish from a `P M Q -> Q M P` exchange is refused (both claims kept), while one covered by a declared exemption above — the adjacency RE-FLOW (which is also what folds a multi-token block whose decomposition closes the gap) or the coordination straddle, both FAIL-OPEN in the DELETE direction — still folds and can lose a rival. "Not listed" is not "safe". It, too, is reported as `substituted_content`. The authoritative predicate is `_boundary`: its value branch covers a number/quantity or a date, and its identity branch reports one of `_IDENTITY_DIMENSIONS` (`negation`, `condition`, `scope`, `language`, `substituted_content` — `unreadable` is the fail-closed sentinel, not a class). The connective class is enumerated by `_CONNECTIVE_SLOTS` / `_CONNECTIVE_MEMBERS`. This list is a reading aid, not the enumeration.
- **⇒ The admissible outcomes are `KEEP` / `NOOP` / `DISCARD` / `MERGE` — where MERGE preserves BOTH sides' evidence and attachments.**

**✅ Implemented (#5006) — the FUSION, at the site §4.2 gives it.** `tortoise/near_dup_merge.py` executes it: ONE survivor (the prior, its claim byte-identical — **never fused**), the **union** of both sides' attachments written onto it, and the losing document's provenance kept as a `(Session)-[:CONTAINS]->(Point)` edge with its own quote on the merge record. Two things to read before changing it:

- **The bar is `MERGE_MIN_OVERLAP = 0.95`, and it is not a tuning knob.** It is this section's evidence (*"practical thresholds cluster at ~0.95"*) and the direction of error is asymmetric. It sits **far above** `NOOP_MIN_OVERLAP = 0.45` deliberately: `#9424` made the paraphrase band REFUSE a fold that would drop an attachment, and the fusion is the authorised alternative for that case **only when the pair is near-identical**. A mid-band paraphrase pair still goes to `ADD` — both claims survive.
- **The verdict is required; the mechanics are never sufficient.** A pair is *eligible* when the never-across guard finds no difference and the overlap clears the bar; it is *admissible* only when S2.2b recorded `MERGE` for it (D12: the arbiter answers *"are these the same claim?"*, the guard only answers *"is anything distinguishing lost?"*). With no arbiter injected the mechanism is **inert** — the same injected-seam posture VET ships with — and the confidence that it *ran* lives in `tests/test_near_dup_merge_5006.py`, which drives the real classifier, the real write path and a real graph.

**⚠️ And the honest caveat:** the evidence says merging can LOSE information. **The owner has authorised it; the bar and the never-across-differences list are how it is made safe.** If the draft-and-run loop shows merges losing detail, *that* is the signal to raise the bar — not a number to hit.
