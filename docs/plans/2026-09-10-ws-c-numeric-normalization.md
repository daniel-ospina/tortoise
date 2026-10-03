---
title: "WS-C design — numeric & locale normalization for extracted values"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-10
updated: 2026-09-10
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutObjects: tortoise-extractor, tortoise-commit-schema, tortoise-projection, tortoise-longmem-eval
aboutSubjects: tortoise-value-layer, tortoise-state-and-value-model
governingAgreement: "#2817"
---

# WS-C — Numeric & locale normalization

**Parent:** #2820 (WS-C — state & value model) · **Issue:** #2817 · **Status:** design v1, awaiting owner approval
**Feeds:** #2782 (money representation) · **Related:** #2813, #2795, #2818, #2730, #2725, #2687, #2453, #2747
**Sibling design:** `docs/plans/2026-09-10-write-contract-five-doors.md` (WS-A) — this design is consistent with its D1 and §11/D1c doctrines.

> #2820 gate: *"Workstreams A/B/C all require owner approval of a written design before implementation."*
> This document is that design. **Nothing here is implemented until the owner approves it.** §6 lists the
> open decisions; §9 sequences the work; §2.4 records the verifier's verdict.

**Version history**

- **v1** — initial design. Produced from (a) an internal-evidence pass over the extraction → commit →
  projection lanes at HEAD `6ee029d62`, (b) the three research comments already filed on #2782
  (`~/.swarm/research/2026-09-09-typed-values-and-lifecycles.md`,
  `…-numeric-storage-audit.md`, and the extraction-side intersection report), and (c) a fresh external
  research pass on money representation, locale-aware parsing, ISO 4217, and extraction-system practice.
  A fresh-context verifier attacked the conclusions before publication (§2.4).
- **v1a** — incorporated the first fresh-context verifier cycle (14 findings: 2 P0, 8 P1, 4 P2).
  Load-bearing corrections: the `M` magnitude **contradiction between D4 and the test table** (one of the
  two P0s; the other P0 was a verifier misread of minor-unit scaling — the table is now explicitly
  labelled **minor units**); a **unit requirement** for every normalized value (a bare decimal has no
  `valueKind` in D6 and is now dropped as `no_value_kind`); a **leading-zero exception** and an explicit
  **malformed-pattern fallback** in D3; a **numeric-span extraction** step (unit suffixes like `800ms`
  were unaddressed); **sub-minor precision** (never silently rounded); the `minorUnitExponent`
  cache-doctrine tension named rather than hidden; the `replay_source` divergence from WS-A justified;
  the **external-client path** specified (the server always derives; a client-supplied value is only a
  cross-check); and the backfill's id claim scoped. Verifier verdict transcribed in §11.
- **v1b** — incorporated the second verifier cycle (5 findings: 1 P1, 4 P2, all accepted). Corrections:
  the `.5` contradiction introduced by v1a (it appeared in both the leading-zero exception and the
  malformed fallback); `sub_minor_precision` and `unsupported_kind` added to the `normalizationStatus`
  enum (a drop reason must always be representable as a status); `sourceTurnId` and `period` declared in
  the `POINT_PROPS` fragment (`period` is now a first-class field with a closed value set, separate from
  `qualifier`). Cycle-2 verdict in §11.
- **v1c** — incorporated the third verifier cycle (1 P2, accepted). Correction: `ambiguous_date` was
  listed as a value-level drop reason while having no `normalizationStatus` member — and no value-level
  outcome can produce it. Resolved **deliberately**: date ambiguity is a **warning** that suppresses
  `asOf` (the amount still normalizes), never a value drop, so `ambiguous_date` is removed from both
  sets; T1 row 37 and a D7 note pin the behaviour. Cycle-3 verdict in §11.

---

## 1. Problem

There is **no numeric or locale normalization anywhere in the system.** `grep -rn -E
"milh(ão|ao)|amountMinor|amount_minor|valueKind|value_kind|parse_number|normalize_number" tortoise/ tools/`
returns zero hits. The only structured-value precedent is the **date gate** (`_valid_iso_date`,
`extractor_v2.py:773-781`, mirrored by `Point._iso_date_prefix`, `commit_schema.py:296-305`).

So every amount arrives as a raw string and stays one. `"R$ 1.234,56"`, `"1,234.56"` and `"1234.56"`
are three different strings that mean one number; `"R$ 1.234,56/mês"` and `"1.2k"` are not numbers at
all to this system. That means:

- **Comparison is lexicographic.** `"1.234"` (PT-BR, 1234) sorts before `"1,234.56"` but after
  `"999"`. Nothing can rank or range-filter an amount.
- **Dedup cannot see values.** Two points asserting `R$ 1.234,56` and `1234.56 BRL` are unrelated
  strings, and the prose-based supersession comparator (`_fact_value_contradiction`,
  `extractor_v2.py:2339`) sees only token differences — which happens to fire today, but for the wrong
  reason and only when the wording also differs.
- **Aggregation is impossible.** `aggregate.py` treats `amount`, `cost`, `price`, `money`, `worth`,
  `paid`, `spent` as **measure words to strip** (`aggregate.py:175-181`), not values to read.
- **Locale misparse is a silent 1000× error.** `R$ 1.234,56` read with EN conventions yields `1.234`
  (one and a bit), not `1234.56`. There is no error path.
- **#2782 has no producer.** The approved value layer (#2782) needs `amountMinor`/`currency`/`asOf`
  from prose. Without a deterministic normalizer those fields are born from an LLM guess at locale
  arithmetic, or not at all.

This is #2820's **Pattern 4 — Fixed vs flexible, unresolved**: we never decided which value semantics
are canonical and which are tenant-configurable, so neither is built.

## 2. Evidence

### 2.1 Internal — where the value must be produced, validated and stored

Five write lanes handle an extracted number, and they disagree (this is WS-A's problem, restated for
values):

| # | Lane | Entry point | What happens to an undeclared numeric field today |
|---|---|---|---|
| L1 | v2 S1 story | `extractor_v2.py:667` `run_s1` | Prose only; S1 has no JSON contract. **Stays prose.** |
| L2/L4 | v2 S2 map / S4 gap review | `OUTPUT_CONTRACT` (`extractor_v2.py:1005`), `_s2s4_rules:284` | No value field in the contract; `_verbatim_match:2213` compares S2/S4 field-by-field |
| L5 | **v2 S5 embed (deterministic)** | `execute_embed:3586`; `pt_entry` at `:3896-3907` | **The only place a deterministic normalizer can live.** Already hosts `_valid_iso_date` + `_resolve_source_turn:3423` |
| L7/L8 | M2 / EventAPI + ConversationMiner | `EventAPI.add_point:120` → `_upsert_point_props` | Silent drop, **even live** (fixed SET list) |
| L10 | Commit endpoint (Layer 1) | `commit_schema.Point:269`, `extra="forbid"` | **422 — the whole payload is rejected** |
| L11 | v2 product capture | `sdk._extract_session_v2:3809-3820` | Silent drop — passes only `kind, content, id, dedup, session_id, is_episodic, status, extractedFrom` (no value props) |
| L11 | SDK `create_point` direct | `sdk.py:2577-2581` | **Stored live, lost on rebuild** |
| L12 | Projection replay | `_upsert_point_props` (`entities.py:151`, fixed list `:179-192`) | Point is the only label with **no** extras pass |

**Key precedents to mirror — both already exist and both are named in #2817:**

- `_valid_iso_date` (`extractor_v2.py:773-781`): deterministic S5 gate. *"Anything else ('next tuesday',
  'null', '') is junk — the caller drops it with a warning"* — the exact never-guess posture this design
  adopts for amounts.
- `Point._iso_date_prefix` (`commit_schema.py:296-305`): the Layer-1 mirror, so *"a direct client cannot
  store garbage"*.

**The decisive constraint — prose is load-bearing.** The extractor carries an explicit three-place
**VALUE FIDELITY** rule (`extractor_v2.py:228-241`, rendered into S1/S2/S4 via `_granularity_text:719`
and `_render_master:524`):

> when a point, entity, or event references a concrete value … WRITE THE EXACT VALUE INTO THE CONTENT.
> Never paraphrase, round, generalize, or drop it … The number is the memory; a vague restatement is a
> lost fact.

and the retention graders read **only** `content` (`tests/eval/write_path/grading.py:24-27`,
`schema.py:128-142`, `tests/eval/harness/grading.py:32-45`; the snapshot query is `MEMORY_ROW_QUERY`,
`runner.py:284-288`). The sealed gold anchors spell numbers as **words** (`"twelve million events for
Halcyon"`, `"thirty percent of alerts are unactionable"`). **A design that moves a number out of prose
silently zeroes its retention metric.** The typed value must therefore be an *additive dual-write*.

**The storage constraint.** FalkorDB persists scalars and scalar arrays only — **no map/object
properties** (verified in the #2782 audit against FalkorDB's data-types page; the map rejection path is
`sdk.py:953-956`). Any `{amount, currency}` shape must **flatten to scalars or be reified as a node**.
There is also **no decimal type** in FalkorDB.

**Absence is currently ambiguous.** Nothing distinguishes *"this claim had no number"* from *"this claim
had a number we could not parse"* from *"this claim had a number we parsed"* — the same class of defect
as #2814. §D8 fixes that.

### 2.2 External — money representation

| Finding | Sources | Confidence |
|---|---|---|
| **Never store money as binary float.** `0.1` has no exact binary representation; repeated arithmetic drifts. | PostgreSQL numeric-types docs (float is approximate, `numeric`/`decimal` exact); Crunchy Data "Working with Money in Postgres"; practitioner write-ups | **High** (3+ independent categories) |
| **Integer minor units are the reliable canonical form** — addition/subtraction are exact at a known scale, and the value is indexable. | Crunchy Data; practitioner fintech write-ups; ISO 4217 minor-unit guides; `currency-core` docs | **High** |
| **Currency is part of the value, not a global default** — money is amount **+** currency as one unit of meaning (DDD's `Money` value object). | Martin Fowler `Money` value-object guidance; #2782 research brief; PostgreSQL `money`-type docs (locale-dependent → unsuitable for multi-currency) | **High** |
| **The minor-unit exponent is not 2 everywhere** — ISO 4217 permits 0, 1, 2, 3 (and 4 for some). JPY = 0, USD/BRL = 2, KWD/BHD = 3. | ISO 4217:2015 (minor-unit column, "decimal relationship"); ISO 4217 Wikipedia overview; Adyen currency-code table; neonpay "Representing money at scale is hard" | **High** |
| **`amountMinor` is only well-defined with an explicit scale** — "do not assume two decimals everywhere"; keep the scale explicit. | neonpay; `pratikdhanave.com` fintech handbook; #2817 failure mode 6 | **Medium** ⚠️ emerging (practitioner sources, consistent) |
| **Cryptocurrencies have no ISO 4217 code** and can need 8+ decimals. | ISO 4217 Wikipedia overview; `currency-core` docs | **Medium** ⚠️ emerging |

### 2.3 External — locale-aware number parsing and magnitude words

| Finding | Sources | Confidence |
|---|---|---|
| Parsing is **locale-driven**; the same punctuation means different things in `en-US` and `pt-BR` (`1,234` = 1234 in EN, ≈1.234 in PT-BR; `1.234` the mirror image). | ICU design notes on number parsing; ICU4J number-parsing-problems; Android `NumberFormat`; Microsoft globalization number-formatting docs | **High** |
| Libraries **do not guess a universal meaning**; they parse with the active locale (ICU treats exactly one accepted decimal-like character as the decimal sign and the rest as grouping). | ICU design/number-parsing; ICU `UNUM_PARSE_DECIMAL_MARK_REQUIRED` docs | **High** |
| Finance abbreviations collide with SI: **`M` can mean thousands in fixed-income/Roman notation and millions in SI**, while **`MM` means millions** in finance. | Chicago Manual of Style Q&A on abbreviations; Corporate Finance Institute "MM (Millions)" | **High** |
| **`billion` is ambiguous across languages/scales** — short scale 10⁹ vs long scale 10¹². **PT-BR uses the short scale (`bilhão` = 10⁹); PT-PT uses the long scale (`mil milhões` = 10⁹, `bilião` = 10¹²).** | Wikipedia "Long and short scales"; Portuguese grammar references; languagesandnumbers.com | **High** |

The corpus is Brazilian-Portuguese venture material (`#2725`), so the PT-BR short-scale reading is the
operative one — but the design states it rather than assuming it.

### 2.4 Verifier verdict

> **Status:** recorded after the fresh-context verifier cycle (§11). The verifier was a fresh `task`
> sub-agent with no memory of this session; its findings are incorporated below and the verdict is
> transcribed verbatim in §11.

### 2.5 Source confidence summary

| Claim | Tier | Sources |
|---|---|---|
| Float is wrong for money | High | ISO/Postgres/practitioner (3 categories) |
| Integer minor units + explicit scale | High | Postgres/Crunchy/practitioner/ISO |
| Per-currency exponent (0/2/3) | High | ISO 4217 + payment processors |
| Locale-driven parsing; no universal guess | High | ICU + Microsoft |
| `M` (10³ finance vs 10⁶ SI) and `MM` (10⁶) collision | High | Chicago Manual + CFI |
| PT-BR short scale (`bilhão` = 10⁹) | High | Wikipedia + grammar refs |
| "Keep the scale explicit" as an engineering rule | Medium ⚠️ emerging | practitioner-only, consistent |
| Storage of the exponent per-value (vs deriving from a table) | **Design judgement** — see OD1 | — |

---

## 3. Scope — which surfaces normalize and which do not

| Surface | In scope? | What changes |
|---|---|---|
| **Extraction output (S2/S4 prompt)** | **Yes — hints only** | Emit the verbatim value + `valueKind`/`currency` **hints**. **Never ask the model to compute minor units** (LLMs are unreliable at locale arithmetic; #2817 fix direction 6). |
| **Extraction output (S5 `execute_embed`)** | **Yes — the derivation point** | New deterministic `normalize_value(...)`. This is the one choke point. |
| **The commit door (Layer 1)** | **Yes** | New `Point` validator mirroring `_iso_date_prefix`, so a direct client cannot store a malformed typed value. |
| **The v2 capture seam (L11)** | **Yes** | The value must be passed through to `create_point` — without this, nothing is observable on the product path. |
| **Projection / rebuild (L12)** | **Yes (dependency)** | The value must survive `rebuild_all`. This is WS-A's D2 Point-extras pass — **this design depends on it, it does not duplicate it.** |
| **Search / filter** | **Partial** | Add typed range/equality filters and an indexable numeric prop. The **prose** token heuristics stay exactly as they are — `retrieval.py` treats currency symbols as content by design (`_pkg_norm:835-845`, "CURRENCY SYMBOLS ARE CONTENT", #2687) and that must not be "fixed". |
| **Dedup keys** | **No (point ids) / Yes (supersession)** | Point ids are `pt_<sha>` over **content** (`point_content_id`, `commit_schema.py:1129`). Because `content` stays verbatim, **ids are unaffected**. But `_fact_value_contradiction` must additionally read typed values so a changed amount still registers as REVISES. |
| **Aggregation** | **Yes** | New: `SUM(amountMinor)` grouped by `currency`. Cross-currency sums are **refused**, not approximated (no FX in this design). |
| **Currency conversion / FX** | **No — explicitly out** | A rate is a separate, time-varying fact with its own provenance. |
| **Prose `content` / `quote`** | **No** | Stays verbatim. See D7. |
| **EP confidence floats** | **No** | A different value class (probabilities), already typed as floats by design. |
| **Eval gold / baselines** | **No** | Not re-sealed. Dual-write keeps the graders green (§D7). |
| **Pack per-kind value declarations** | **No — WS-B (#2818)** | This design declares *point-level* value props in WS-A's `contract.py`. "Which kinds may carry which value fields" is the property-declaration surface, owned by WS-B. |

---

## 4. Design decisions

### D1 — Canonical representation: integer minor units + explicit currency + an explicit exponent

A normalized money value is **three scalars**:

```python
valueKind:        "money"          # discriminator
amountMinor:      int              # integer minor units, e.g. 123456 for R$ 1.234,56
currency:         str              # ISO 4217 alpha-3, canonical uppercase, e.g. "BRL"
minorUnitExponent: int             # 2 for BRL, 0 for JPY, 3 for KWD
```

**Why integer minor units.** Exact addition and subtraction, indexable, range-filterable, and no float
drift (research §2.2). This is the unanimous external recommendation and it matches #2782's already
filed research brief.

**Why not `Decimal`.** FalkorDB has no decimal type. A Python `Decimal` would have to be stored as a
string — unindexable and unsummable — which defeats the reason to normalize at all.

**Why not float.** `0.1 + 0.2 != 0.3` in binary floating point. A wrong amount is the highest-consequence
output class: it becomes durable belief state and propagates through EP.

**Why the exponent is explicit — and how this relates to the §11 doctrine.** `amountMinor: 123456` is
meaningless without knowing the scale. ISO 4217 supplies the scale per currency, but (a) FalkorDB reads
should not silently depend on a runtime table, and (b) a stored value must remain interpretable if the
table ever changes. Storing the exponent makes the value **self-describing**, which is the same posture
as storing the currency itself. The normalizer derives it from a **vendored, version-pinned ISO 4217
table**; the Layer-1 validator checks `(currency, minorUnitExponent)` **consistency** against that table
so the two fields cannot drift.

> **This is a deliberate, named departure from a strict reading of §11, not a silent one.** §11 says a
derived value is a cache and the derivation is the truth. Here the exponent is treated as part of the
**unit definition of the value** (like `unit: "ms"` on a duration or the currency itself), not as a
cached derivation *of the amount*. The ISO table is the **authoring source**; the stored exponent is the
value's declared unit; the consistency check makes any table drift visible rather than absorbing it.
> If the owner prefers the strict reading, **OD1**'s derive-only alternative removes the tension at the
> cost of a runtime table dependency in every reader and no self-describing stored value.

**Sub-minor precision is never silently rounded.** `R$ 0,123` (0.123 reais) cannot be expressed in
centavos. The normalizer **drops** it with `sub_minor_precision` rather than rounding — consistent with
the never-guess posture. Rounding is a policy decision, not a parse (see **OD11**).

**Range guard.** Python integers are unbounded but a FalkorDB int property is **int64**. The normalizer
rejects `|amountMinor| > 2**63 - 1` (the value **after** exponent scaling — the scaled value is what is
stored) with a warning rather than write a value the store cannot hold. (Adversarial test case, §8.)

### D2 — The raw string is the evidence; the normalized value is a derivation

This design **adopts** the repo's own cache doctrine rather than inventing a rule. `docs/ONTOLOGY.md`
§11 (v3.2, #398):

> **Derived values may be CACHED, never authoritative** … the derivation is the truth, the cache is a
> performance artifact.

Which §11 clauses transfer, and which do not (scoping the citation deliberately, as WS-A D1c does):

| §11 clause | Transfers? | Why |
|---|---|---|
| "may be **CACHED**" | **yes** | a value that is not persisted cannot be indexed or summed in FalkorDB |
| "**recomputed on write events**" | **yes** | the normalizer runs on every write (S5) |
| "consistency-checked on read" | **yes, as a canary** | recompute `normalize(verbatim)` and compare; a mismatch is drift (D9) |
| "stamped with `reliability_derived_at`" | **no** | the version stamp `normalizerVersion` is the analogue and is more useful (it names the *code*, not the clock) |
| "never authoritative" | **partly — do not overclaim** | the provenance of truth is the **verbatim string**; `amountMinor` is authoritative *for comparison and aggregation*, but never overrides the evidence |

**Consequence:** the canonical form always carries the verbatim it was derived from (D7). The raw prose
is never overwritten, never removed, never "migrated away". If the derivation and the evidence ever
disagree, **the evidence wins** and the derivation is recomputed.

### D3 — Locale disambiguation: pattern first, anchor second, never a guess

The number format is decided in this order:

0. **Numeric-span extraction (before any separator rule).** The normalizer first isolates a **candidate
   span**: an optional sign, digits and separators, an optional magnitude suffix (`k`, `mil`, `milhão`,
   `MM`, `bn`, …), an optional currency prefix/suffix, and an optional unit suffix (`%`, `x`, `ms`,
   `s`, `min`, `h`, `day`, `week`, `month`, `year`, and the quantity units in D6). Everything outside
   the span is retained as `qualifier` (e.g. `post-money`, `under`, `per month`) or left in prose. A
   span with **no unit dimension at all** is dropped as `no_value_kind` (D6) — so `1234.56` alone is not
   normalized, while `R$ 1234.56` is. This step is what makes `800ms` parseable: `ms` is a unit suffix,
   not a separator, and the digit/unit boundary is where the span ends.
1. **Pattern (strongest).** If the string contains **both** `.` and `,`, the **rightmost separator is
   the decimal separator** and the other is grouping. `1.234,56` → 1234.56; `1,234.56` → 1234.56. This
   is unambiguous and locale-independent.
2. **Digit-count rule.** With a single separator type:
   - appearing **2+ times** → grouping, provided *every* group after the first is exactly three digits
     (`1.234.567` → 1234567; `1,234,567` → 1234567);
   - appearing **once** and followed by **1, 2 or ≥4 digits** → decimal separator (`1.5` → 1.5;
     `1.23` → 1.23; `1,2345` → 1.2345 — a grouping separator must be followed by exactly three digits);
   - appearing **once**, followed by **exactly 3 digits**, **and the integer part is not a bare `0`** →
     **ambiguous** (`1.234` = 1234 PT-BR *or* 1.234 EN; `1,234` the mirror image);
   - **leading-zero exception:** when the integer part is exactly `0` (or absent, e.g. `.5`), the
     separator **cannot** be a grouping separator — no locale writes a grouping group prefixed by `0` —
     so it is the decimal separator. `0.123` → 0.123, `0,123` → 0.123. (This is a correctness fix: a
     naive application of the 3-digit rule would drop a value that is unambiguous in every locale.)
   - **fallback:** a pattern that satisfies **none** of the above (`1,23,456`, `1.2.3`, a stray
     separator such as a trailing `5.`) is **malformed** → drop `malformed`. (`.5` is **not**
     malformed — the leading-zero exception above covers it as `0.5`.)
   - inside a **currency with a 3-digit exponent** (KWD/BHD/OMR), a single separator followed by
     exactly 3 digits is **still ambiguous** — `KWD 1.234` could be 1234 or 1.234. **Drops**, unless
     the leading-zero exception applies (`KWD 0.123` → 0.123).
3. **Anchor (moderate).** If the pattern is ambiguous, a locale-diagnostic anchor may resolve it:
   | Anchor | Reads as | Strength |
   |---|---|---|
   | `R$` | PT-BR (dot = grouping) | moderate |
   | space grouping (`1 234,56`) | PT-BR / FR | **strong** (pattern, not anchor) |
   | apostrophe grouping (`1'234.56`) | CH | **strong** (pattern) |
   | `USD`/`BRL`/`EUR`/`GBP` code | resolves the **currency**, only weakly the format | weak |
   | `$` | **not** locale-diagnostic (USD/CAD/AUD/BRL/MXN) | none |
   | `€` | European, but not a specific locale | none (insufficient) |
4. **No resolution → drop with a warning.** Never guess.

**Why the rightmost-separator rule is stated as a heuristic.** ICU does *not* do this — it parses with
the active locale and treats exactly one accepted decimal-like character as the decimal sign. We have no
active locale (there is no session-locale input today, and adding one is **OD5**), so we use the
pattern, and we say so. This is a deliberate, documented deviation from ICU, not a claim of equivalence.

**The cheapness argument that makes "drop" correct.** Because the prose keeps the number verbatim
(D7), dropping the typed value **loses nothing that was stored**. The cost of a dropped value is a
missing typed projection; the cost of a wrong value is durable false belief with no error. Therefore
the bias is unambiguously toward **drop, never guess** — this is the same posture as
`_resolve_source_turn` ("a wrong model index must never win", `extractor_v2.py:3427`).

### D4 — Magnitude words and suffixes: closed table, ambiguous forms drop

| Form | Value | Verdict |
|---|---|---|
| `k`, `K` | 10³ | accept |
| `mil` | 10³ (PT) | accept |
| `milhão`, `milhões`, `mi` | 10⁶ (PT short scale) | accept |
| `bilhão`, `bilhões` | 10⁹ (PT-BR short scale) | accept |
| `bn` | 10⁹ | accept |
| `MM` | 10⁶ (finance: M×M) | accept |
| `M` | **10³ (finance) vs 10⁶ (SI) — genuinely ambiguous** | **drop** (OD2) |
| `B` | 10⁹ (finance) vs byte | **drop** (unit collision) |

The `M` collision is real and documented (Chicago Manual of Style; Corporate Finance Institute): in
fixed-income notation `$150M` can mean $150,000 while `$150MM` means $150,000,000. A silent 10³ error
is exactly the failure class this design exists to prevent. **Recommendation: drop bare `M`.** The owner
may choose a domain default instead (**OD2**), in which case the inference is recorded on the value.

> **The cost of dropping is real, and is stated rather than hidden.** A dropped `M` means the amount is
> absent from typed comparison and aggregation (D11 reads only `normalizationStatus = "ok"`), even
> though the prose retains it for recall. Given a Brazilian venture corpus where `R$ 5M` overwhelmingly
> reads as 5 milhões, this is a genuine loss — the trade is a **known gap in aggregation** against a
> **silent 10³ error in durable belief state**. This design takes the gap; it does not claim the trade is
> free. **OD2 is therefore a high-impact owner call, not a formality.**

**Words are in scope.** The eval gold anchors spell numbers as words (`"twelve million events for
Halcyon"`), and a word→digit normalizer is a superset of what the gold expects, so adding it is safe.

**`bilião` (PT-PT long scale, 10¹²) is dropped, not guessed** — the corpus is PT-BR, but silently
reading a PT-PT `bilião` as 10⁹ would be a 1000× error. Recorded with the drop reason.

### D5 — Currency: explicit per value, ISO 4217, never a graph-level default

- Canonical currency is an **ISO 4217 alpha-3 code**, uppercased (`brl` → `BRL`).
- Symbols map to codes **only when the mapping is unique**: `R$` → BRL, `£` → GBP, `€` → EUR.
  `$` → **ambiguous** (USD/CAD/AUD/BRL/MXN) → **drop the currency, keep the number prose-only**;
  `¥` → **ambiguous** (JPY/CNY) → drop.
- **There is no graph-level default currency.** Currency mixing is the most-reported failure mode in
  the value-layer research, and a default would silently mislabel every unanchored amount.
- A currency code that has no ISO 4217 entry (crypto, `BTC`) → **drop with a warning** (no ISO code
  exists, and the exponent convention is not portable). Explicitly out of scope.
- **No FX conversion anywhere in this design.** Two amounts in different currencies are **never** summed.

### D6 — The canonical model is a flat, tagged union

FalkorDB cannot store a map, so the `values[]` **array-of-objects** shape floated in the #2782
intersection report is **not storable** as a node property. The canonical form is therefore a **flat
tagged union**: a discriminator plus a disjoint scalar field set, validated with the discriminator's
branch only.

| `valueKind` | Fields | Example |
|---|---|---|
| `money` | `amountMinor` int, `currency` str, `minorUnitExponent` int | `R$ 1.234,56` → `123456`, `BRL`, `2` |
| `percent` | `basisPoints` int | `12%` → `1200` |
| `multiple` | `ratioNumerator` int, `ratioDenominator` int | `12x` → `12`, `1` |
| `duration` | `durationMinor` int, `durationUnit` str (`ms\|s\|min\|h\|day\|week\|month\|year`) | `800ms` → `800`, `ms` |
| `quantity` | `quantityMinor` int, `quantityExponent` int, `unit` str | `1.5kg` → `15`, `1`, `kg` |

Common to every value: `valueKind`, `verbatim` (required, D7), `sourceTurnId`, `asOf`, `qualifier`,
`localeAnchor`, `magnitudeSource`, `normalizerVersion`, `normalizationStatus`.

**A value must carry a unit dimension.** `valueKind` is required and there is **no bare-number kind**:
`1234.56` with no currency, `%`, `x`, time unit or quantity unit is **dropped as `no_value_kind`** and
left prose-only. This is deliberate: an unanchored decimal has no meaning to compare or aggregate, and
inventing a `number` kind would give the schema a branch nothing could ever use. It also keeps D6 honest
as a closed tagged union (the verifier found this gap in v1 — D6 listed five kinds with no branch for a
bare decimal, while the test table produced one).

**Unit tables (closed, version-pinned):**

| Dimension | Accepted units (canonical) | Notes |
|---|---|---|
| duration | `ms`, `s`, `min`, `h`, `day`, `week`, `month`, `year` | `month`/`year` are calendar units; **never** silently converted to days |
| quantity | a small allowlist (`kg`, `g`, `km`, `m`, `cm`, `mm`, `L`, `mL`, `GB`, `MB`, `KB`, `TB`, `unit`) plus pack-declared units (WS-B #2818) | an unknown unit → `unsupported_kind` drop |
| percent | `%` | basis points |
| multiple | `x`, `×` | exact rational |

- **`percent` uses basis points, not `0.12` and not `12`** — the issue's failure mode 3. `0.12` written
  without a `%` is not a percentage and is left prose-only.
- **`multiple` uses an exact rational** — never a float. `12x` is not `12.0%`.
- **`month`/`year` durations are calendar units, not fixed second counts.** Stored with the unit name;
  no silent conversion to days.
- **v1 sequences `money` and `percent` first** (the two with a real consumer: #2782 and the
  venture-pack state model). `duration`/`multiple`/`quantity` are declared here but implemented in a
  later step (**OD6**).
- **One value per point in v1.** A point asserting multiple distinct numbers (`0.5-2%`) is
  **out of scope for v1** — see OD3 for the reification option (`:Value` node).

### D7 — Where the raw form stays: additive dual-write, `verbatim` on the value

- **`content` and `quote` are never modified.** The VALUE FIDELITY rule and the retention graders
  (`grading.py:24-27`) both depend on the number being in the prose. This is the single most important
  constraint in the design; violating it silently zeroes measured retention.
- The typed value carries **`verbatim`** — the **exact source substring** it was normalized from, unmodified.
  `verbatim` is **required**; a value without its verbatim is not admissible (the
  normalize-then-lose-the-source path is the classic silent-corruption route named in #2817).
- The typed value carries **`sourceTurnId`**, reusing the existing `_resolve_source_turn` semantics
  (`extractor_v2.py:3423-3455`) — the quote is the anchor, the model index is advisory.
- The typed value carries **`asOf`** — the assertion/validity date, **distinct from `Point.when`**. #2817
  failure mode 8: reusing `when` for `asOf` is wrong when the assertion date differs from the fact's
  occurrence/validity date. `asOf` defaults from `when` **only as an explicit fallback**, and the
  fallback source is recorded (`asOfSource`) so it is never mistaken for a stated date (**OD8**). An
  `asOf` read from prose in a **locale-ambiguous** form (`01/02/2024`) is **never guessed**: it is left
  absent with an `ambiguous_date` warning, and the amount still normalizes (D8).
- **`period`** is a declared value field, distinct from `qualifier`. `R$ 1.234,56/mês` yields
  `period = "perMonth"` (closed set: `perDay | perWeek | perMonth | perQuarter | perYear | total`);
  `qualifier` holds free-ish domain qualifiers (`post-money`, `under`, `approximately`). Keeping them
  apart matters for aggregation: a monthly amount and a one-off amount are not summable without an
  explicit conversion, and folding `perMonth` into a free-text `qualifier` would hide that.
- `Point.when` is **never** written by this design.

### D8 — Failure behaviour: `ok` or drop-with-a-record; no silent coercion

`normalize_value(...)` returns exactly one of:

```python
ValueResult.ok(value)                  # parsed
ValueResult.dropped(reason, detail)    # deliberately not parsed
```

and never a best-effort guess. The reason is from a closed set:
`ambiguous_separator | ambiguous_currency | ambiguous_magnitude | unsupported_kind |
sub_minor_precision | malformed | overflow | no_value`.

**The drop is recorded, not swallowed.** The point carries a declared property:

```python
normalizationStatus: str   # "none" | "ok" | "ambiguous_separator" | "ambiguous_currency"
                           # | "ambiguous_magnitude" | "unsupported_kind" | "sub_minor_precision"
                           # | "malformed" | "overflow" | "no_value"
```

`normalizationStatus` is a **superset** of the drop-reason set: it also carries `none` (no number was
present) and `ok` (a value was produced). The two enums must stay in sync — a drop reason that is not
representable as a status is a bug (this is exactly what cycle 2 caught for `sub_minor_precision` and
`unsupported_kind`).

**Date ambiguity is a warning, not a value drop.** An ambiguous `asOf` (e.g. `01/02/2024` — 2 Jan or
1 Feb?) does **not** invalidate the amount: the value is still `ok`, `asOf` is simply left **absent**,
and an `ambiguous_date` **warning** rides the existing `warnings[]` channel. `ambiguous_date` is
therefore deliberately **not** a member of the drop-reason/status sets — a status enum member that no
value-level outcome can produce is itself the bug the sync rule exists to prevent (cycle 3 caught this
in the other direction).

so *"was never a number"* (`none`) is distinguishable from *"was a number we could not parse"*
(`ambiguous_*`, `malformed`) — the same "absence must be meaningful" principle as #2814. The
human-readable warning also rides the existing `warnings[]` channel that the capture response and
`execute_embed` already surface.

**`0` must be distinguishable from absent.** `R$ 0,00` normalizes to `amountMinor: 0`; a null/absent
`amountMinor` means "no value". Tests must pin this (a `coalesce`-style write must not treat `0` as
missing).

### D9 — Determinism, idempotency, and replay

- **Pure function.** `normalize_value(verbatim, hints)` depends only on its arguments. No clock, no
  network, no process-global locale, no environment. `decimal` context is pinned locally (never the
  caller's).
- **Idempotent.** Re-normalizing the same verbatim yields the identical result. The replay writer and
  the backfill job can run it any number of times.
- **Cycle-free module.** The normalizer lives in a **new stdlib-only module `tortoise/values.py`**
  (`re`, `unicodedata`, `decimal` — no `tortoise.*` imports), so WS-A's `contract.py` can reference
  `values.normalize` in a `derive=` slot without the import cycle that WS-A hit with `_content_hash`.
- **Version-pinned.** `values.NORMALIZER_VERSION: int` is bumped whenever the parse behaviour changes,
  and the version is **stored on each value** (`normalizerVersion`). A behaviour change is therefore a
  **migration**, not a silent drift: a query can find every value produced by an older version. This
  mirrors WS-A's `CONTENT_HASH_VERSION` reasoning (`content_hash` mints ids; here the version names the
  derivation).
- **Golden vectors.** A pinned test asserts `normalize_value("<fixture>").value == <pinned dict>` for a
  fixed fixture set, so an accidental behaviour change fails loudly.
- **Replay rule, and why it diverges from WS-A's `content_hash`.** The value props are
  `replay_source="journal"` (WS-A D1's enum): the value is computed on write and **restored from the
  journal snapshot**, not recomputed on replay. WS-A's derived props (`content_hash`, `embedding`,
  `updatedAt`) use `replay_source="derived"` (recomputed). **The divergence is deliberate and
  justified:** `content_hash` *mints ids* (`pt_<sha>`), so it must be recomputed to stay consistent
  with id minting (WS-A D1c #2) — a stale hash produces duplicates. A normalized value mints nothing and
  is read by nothing that must agree with a freshly-computed form. Recomputing it on replay would let a
  `NORMALIZER_VERSION` bump **silently rewrite historical values** during a disaster-recovery rebuild,
  which is exactly the silent-rewrite class this design refuses. Restoring keeps history stable and
  attributable (`normalizerVersion` is stored per value).
- **Recompute-and-compare canary.** Because the value is restored rather than recomputed, drift is
  detected **positively**: the replay writer recomputes `normalize(verbatim)` and compares, emitting a
  **WARN + a counter** (never a hard failure) on mismatch — the same shape as WS-A OD10, and never a
  failure because a `PointRevised` legitimately changes content after the journaled snapshot. The canary
  is the `"consistency-checked on read"` clause of the §11 doctrine (D2).

### D10 — Dedup and supersession

- **Point ids are unaffected.** Ids are `pt_<sha256(content)>` (`point_content_id`,
  `commit_schema.py:1126`). Since `content` stays verbatim (D7), no id changes and no migration of ids
  is needed. This is a deliberate consequence of dual-write.
- **`_fact_value_contradiction` gains a typed leg.** The comparator
  (`extractor_v2.py:2339`, driving the REVISES/supersede record at `:3865-3877`) currently compares
  prose tokens. It must additionally compare typed values when both sides carry one: **same entity +
  same frame + different `amountMinor` ⇒ contradiction**, regardless of how similarly the prose reads.
  Without this, a changed amount that happens to be phrased alike stops registering as a value change.
- **`amountMinor` is never part of any MERGE key.** Identity is #2730's problem (`MERGE` by `name`,
  `projection/entities.py:511`); the value layer must not paper over it and must not be smuggled into
  identity.

### D11 — Aggregation

- New aggregation surface: `SUM(v.amountMinor)` **grouped by `v.currency`**.
- **Cross-currency sums are refused with an explicit error**, never converted and never silently
  summed. (No FX in this design; a rate is its own fact with its own provenance and `asOf`.)
- Aggregation reads only `normalizationStatus = "ok"` values. Dropped values contribute nothing and are
  reported in the result as a coverage count, so a low-coverage sum is visible rather than authoritative.
- `aggregate.py`'s existing measure-word stripping (`:176-179`) is **left alone** — it answers a
  different question (counting mentions), and changing it is a separate decision.

### D12 — Declaration surface: use WS-A's D1, do not invent a parallel mechanism

Per WS-A **D1**, `tortoise/projection/contract.py` is the per-layer declaration authoritative for
persistence. The value props are **declared there** in `POINT_PROPS` — with `source`, `replay_source`,
and the type/cap information — exactly like the other Point props. `ONTOLOGY.md` §4.1 gains the value
rows and a parity test diffs the two tables (WS-A D7's declaration-parity row).

```python
# fragment of POINT_PROPS (WS-A contract.py) — value layer only.
# `source="derived"`: every value prop is derived server-side (D13); a client-supplied value is a
# cross-check, never the source. `replay_source="journal"` per D9.
"valueKind":           Prop(str, source="derived", replay_source="journal", replayable=True),
"amountMinor":         Prop(int, source="derived", replay_source="journal", replayable=True),
"currency":            Prop(str, source="derived", replay_source="journal", replayable=True),
"minorUnitExponent":   Prop(int, source="derived", replay_source="journal", replayable=True),
"basisPoints":         Prop(int, source="derived", replay_source="journal", replayable=True),
"ratioNumerator":      Prop(int, source="derived", replay_source="journal", replayable=True),
"ratioDenominator":    Prop(int, source="derived", replay_source="journal", replayable=True),
"durationMinor":       Prop(int, source="derived", replay_source="journal", replayable=True),
"durationUnit":        Prop(str, source="derived", replay_source="journal", replayable=True),
"quantityMinor":       Prop(int, source="derived", replay_source="journal", replayable=True),
"quantityExponent":    Prop(int, source="derived", replay_source="journal", replayable=True),
"unit":                Prop(str, source="derived", replay_source="journal", replayable=True),
"sourceTurnId":        Prop(str, source="derived", replay_source="journal", replayable=True),
"period":              Prop(str, source="derived", replay_source="journal", replayable=True),
"verbatim":            Prop(str, max_len=200, source="derived", replay_source="journal", replayable=True),
"asOf":                Prop(str, source="derived", replay_source="journal", replayable=True),
"asOfSource":          Prop(str, source="derived", replay_source="journal", replayable=True),
"qualifier":           Prop(str, source="derived", replay_source="journal", replayable=True),
"localeAnchor":        Prop(str, source="derived", replay_source="journal", replayable=True),
"magnitudeSource":     Prop(str, source="derived", replay_source="journal", replayable=True),
"normalizationStatus": Prop(str, required=True, source="derived", replay_source="journal", replayable=True),
"normalizerVersion":   Prop(int, source="derived", replay_source="journal", replayable=True),
```

> **Dependency note (not a design choice).** Without WS-A's **D2** Point-extras pass, **all of these
> are written live and lost on the next `rebuild_all`** — the empirically-confirmed drop in the #2782
> audit. This design therefore cannot be implemented before WS-A D2 lands, or it will ship a value
> layer that does not survive a rebuild. #2817's own body already names this ("delivery blocked by
> #2813/#2795").

**Per-kind value declarations** ("which kinds may carry `amountMinor`") are **WS-B #2818** — the
property-declaration surface. This design deliberately does **not** add a `values` key to
`VALID_KINDDEF_KEYS` (`pack_registry.py:110-113`) or touch the pack manifest.

### D13 — Commit-id coupling: use the additive #1350 pattern

`client_commit_id` is a hash over `canonical_payload(...)` (`commit_schema.py:1040`), and
`_point_canonical` (`:1002`) folds optional fields in **only when present** — that is the #1350 additive
pattern, adopted verbatim for `search_keys` and `source_turn_id`.

Typed value fields follow the same rule:

- **A payload that carries no values keeps a byte-identical `client_commit_id`** — every pre-change
  client and every already-written commit stays valid. This is the highest-risk coupling in the whole
  area (a canonical change without the additive guard 422s every old commit), so it has a **golden-vector
  test** (§8).
- **A payload that carries values** gets an id that includes them (the payload genuinely changed).
- **`normalizationStatus`, `normalizerVersion`, `asOfSource`, `localeAnchor` and `magnitudeSource` are
  NOT in the canonical** — they are server-side derivations, and including them would make the id depend
  on server code version.
- **The canonical is a pure function of the submitted payload, never of graph state.** A backfill (§7)
  writes props onto nodes and therefore **cannot change any `client_commit_id`** — old commit records
  stay valid, and a re-submission of an old payload keeps its id. The server must not inject backfilled
  values into the canonical on re-submission, or it would defeat L1 replay detection.

**The external-client path (and why the server always derives).** The v2 extractor computes the value at
S5 server-side, so the client and server trivially agree. An **external client** (the HTTP commit door,
or a direct `create_point` caller) has no access to `tortoise/values.py`. The rule is therefore:

1. **The server always derives the value from `verbatim` + hints.** `verbatim` is the contract; the
   typed value is a derivation of it, not an independently trusted input.
2. **A client MAY supply the typed value** (this is #2817's indicator 2: a direct client cannot store
   garbage). The Layer-1 mirror validates its **structure** (D6: non-negative exponent, ISO-4217 shape,
   `int` not `float`, in-range), which is exactly the `_iso_date_prefix` posture.
3. **A client-supplied value that disagrees with the server's derivation is rejected**, not silently
   trusted or silently overwritten. The error names both readings. This closes the verifier's
   "external client sends `amountMinor=999999` that does not match the prose" hole.
4. **A client-supplied value is included in the canonical when present** (the client computed its id
   over it, so the id must be reproducible); a server-derived value is included only when the payload
   carries one. This keeps the additive rule coherent on both paths.

---

## 5. What the design fixes

| Issue / defect | How this design addresses it | Status |
|---|---|---|
| **#2817** — no numeric/locale normalization exists | D1–D11: one deterministic S5 normalizer + Layer-1 mirror + verbatim provenance + comparator leg | **closed in principle** |
| **#2782** — money has no representation; extractor cannot fill it | D1/D5/D6 produce `amountMinor`/`currency`/`asOf`/`valueKind` deterministically; D12 declares them; D10 wires the comparator | **producer closed; storage blocked by WS-A D2** |
| **#2820 Pattern 4** (fixed vs flexible unresolved) | D1–D6 make the canonical semantics explicit; per-tenant configurability is a separate decision (#2792) | **canonical semantics decided** |
| **#2687** — value-safety guard on retrieval (`_pkg_differ_value_critical`) | typed values give the guard a real comparator instead of a token heuristic (D10) | **improved** |
| **#2814-class** "absence is not meaningful" | D8's `normalizationStatus` makes *unparseable* distinguishable from *absent* | **closed in principle** |
| **#2725** venture pack "released to date / 75% spent" | D11's per-currency `SUM()` is the aggregation primitive; identity remains #2730 | **primitive provided** |
| **Silent locale misparse (`R$ 1.234,56` → 1.234)** | D3/D8: ambiguous patterns drop with a recorded reason; a 1000× error cannot be written silently | **closed in principle** |

---

## 6. Open decisions for owner (decision register)

| ID | Question | Recommendation | Consequence if different |
|---|---|---|---|
| **OD1** | Store `minorUnitExponent` on the value, or derive it from a pinned ISO 4217 table at read time? | **Store it** (self-describing; no silent runtime dependency) **and** validate consistency against the vendored table. | Derive-only is leaner (one field) but every reader depends on the table being present and correct; a table change silently reinterprets historical values. |
| **OD2** | Bare `M` magnitude: drop as ambiguous, or default to 10⁶? | **Drop** (D4). The finance 10³ reading is documented and a 10³ error is the worst class. | Defaulting to 10⁶ matches SI/startup usage and recovers common `$5M` inputs, at the cost of a silent 10³ error on fixed-income text. |
| **OD3** | Where do typed values live on Points: flat sibling props, or a reified `:Value` node? | **Flat sibling props** for v1 (FalkorDB cannot store a map, and one value per point covers the current corpus). Introduce `:Value` when multi-value-per-point is needed. | A `:Value` node supports multiple values + independent provenance but adds a label, a rebuild class, and a traversal to every read. |
| **OD4** | Which write lanes get the normalizer in v1? | **v2 S5 (product) + the commit door + the v2 capture seam.** M2/EventAPI and the v1 `value_extractor` are follow-ons. | Including M2/v1 widens v1 scope substantially; excluding M2 leaves a live drop path unfixed. |
| **OD5** | May a session/document locale hint resolve an otherwise-ambiguous pattern? | **No in v1.** Add the hint only with a real locale input surface. | A hint recovers more values but introduces a new input surface and a new failure mode (a wrong hint silently misparses). |
| **OD6** | Does v1 implement all five `valueKind`s, or money + percent first? | **Money + percent first**; the other three are declared now and implemented later. | All five in one step is a larger prompt/validator change with no current consumer for duration/multiple/quantity. |
| **OD7** | Cross-currency aggregation: refuse, or convert with an explicit rate? | **Refuse** (D11). | Converting requires a rate source with its own `asOf` and provenance — a separate feature. |
| **OD8** | `asOf` source when the text does not state a date: fall back to `when`, to the session date, or leave absent? | **Fall back to `when` when present; otherwise leave absent** and record `asOfSource` so a fallback is never mistaken for a stated date. | Defaulting to the session date would stamp an assertion date the text never asserted. |
| **OD9** | Do typed values enter `client_commit_id`'s canonical? | **Yes, using the additive #1350 when-present pattern** (D13), with a golden-vector test. | Excluding them makes the id blind to a changed value; including them unconditionally breaks every existing commit. |
| **OD10** | Backfill historical prose now, or derive on next write? | **Offline idempotent backfill sweep** (§7), gated on WS-A D2 landing. | Next-write-only leaves the historical corpus unqueryable indefinitely. |
| **OD11** | Sub-minor precision (`R$ 0,123`): drop, or round to the currency's exponent? | **Drop** (`sub_minor_precision`) in v1 — never round silently (D1). | Rounding needs a documented direction (half-even vs half-up) applied consistently on every write path; that belongs with #2782's rounding decision, not with the parser. |

---

## 7. Migration / backfill

Existing points already hold numbers, as prose. Nothing is rewritten; values are **added**.

1. **No id migration.** `content` is untouched (D7), so `pt_<sha>` ids and `client_commit_id`s are
   unchanged (D13).
2. **Backfill is a deterministic sweep**, not a read-time derivation. Rationale: FalkorDB needs a
   materialized value to index and `SUM()`; a read-time derivation cannot carry an index
   (the same reasoning as WS-A's PostgreSQL generated-column analysis). The sweep:
   - finds candidate spans in `content`/`quote` with the same value pattern the normalizer uses,
   - normalizes each span with `values.normalize`,
   - writes the value props + `normalizationStatus` **only where they are absent or differ** (idempotent;
     safe to re-run),
   - **never touches `content`/`quote`**, and
   - reports coverage: `{scanned, normalized, dropped_by_reason}`.
3. **Blocked by WS-A D2.** Writing values that the replay writer drops would produce a backfill that
   silently evaporates on the next `rebuild_all`. The sweep ships **after** the Point-extras pass.
4. **A graph-write job.** Per `AGENTS.md`, any Tortoise graph write invokes the `how-to-use-tortoise`
   skill; the backfill is an owner-visible operation and is not run without approval.
5. **Version-aware.** Values stamped with an older `normalizerVersion` are either left alone (stable) or
   recomputed by an explicit re-backfill; the default is **leave alone + report the count**, so a
   version bump never silently rewrites history.

---

## 8. Test strategy

**T1 — Table-driven unit tests, per failure mode.** Every row is a concrete case; `(verbatim, expected)`
or `(verbatim, expected_drop_reason)`. **Money rows are stated in integer minor units** (the stored
form) — e.g. `123456` means R$ 1.234,56, not R$ 123.456,00. `exp` is `minorUnitExponent`.

| # | Input | Expected | Failure mode it pins |
|---|---|---|---|
| 1 | `R$ 1.234,56` | `123456 BRL exp=2` | PT-BR money |
| 2 | `1,234.56 BRL` | `123456 BRL exp=2` | EN money (anchored) |
| 3 | `R$ 1234.56` | `123456 BRL exp=2` | currency-anchored decimal |
| 4 | `1,234` | **drop `ambiguous_separator`** | the core ambiguity |
| 5 | `1.234` | **drop `ambiguous_separator`** | mirror ambiguity |
| 6 | `R$ 1.5` | `150 BRL exp=2` | 1-digit tail ⇒ decimal |
| 7 | `R$ 1.234.567` | `123456700 BRL exp=2` | PT-BR grouping, 2+ separators (1,234,567 reais = 123,456,700 centavos) |
| 8 | `1,234,567 BRL` | `123456700 BRL exp=2` | EN grouping |
| 9 | `KWD 1.234` | **drop `ambiguous_separator`** | 3-digit exponent does not disambiguate |
| 10 | `KWD 0.123` | `123 KWD exp=3` | **leading-zero exception** (0.123, not 123,000) |
| 11 | `R$ 0,123` | **drop `sub_minor_precision`** | 0.123 reais is not expressible in centavos; never round silently |
| 12 | `R$ 1.234` | `123400 BRL exp=2` (anchor resolves) | moderate anchor |
| 13 | `$100` | **drop `ambiguous_currency`** | `$` collision |
| 14 | `R$ 100` | `10000 BRL` | unique symbol |
| 15 | `¥1000` | **drop `ambiguous_currency`** | JPY/CNY collision |
| 16 | `JPY 1000` | `1000 JPY exp=0` | 0-exponent currency |
| 17 | `R$ 1.2k` | `120000 BRL` | `k` suffix |
| 18 | `R$ 1,5 milhão` | `150000000 BRL` | PT-BR magnitude word |
| 19 | `R$ 2 bilhões` | `200000000000 BRL` (= R$ 2×10⁹ in centavos) | PT-BR short scale |
| 20 | `R$ 2 bilião` | **drop `ambiguous_magnitude`** | PT-PT long scale |
| 21 | `$5M` | **drop `ambiguous_magnitude`** (OD2) | `M` collision |
| 22 | `USD 5MM` | `500000000 USD` | finance `MM` = 10⁶ |
| 23 | `8M EUR post-money` | **drop `ambiguous_magnitude`** (OD2) — *not* resolved by the qualifier | `M` is not disambiguated by a qualifier |
| 24 | `8MM EUR post-money` | `800000000 EUR` + `qualifier=post-money` | qualifier |
| 25 | `12%` | `basisPoints 1200` | percent |
| 26 | `12x` | `multiple 12/1` | not a percent |
| 27 | `0.5-2%` | **drop `unsupported_kind`** (range) | ranges |
| 28 | `under 800ms` | `duration 800 ms` + `qualifier=under` | qualifier + duration (span extraction, D3 step 0) |
| 29 | `R$ 1.234,56/mês` | `123456 BRL` + `period=perMonth` | trailing period |
| 30 | `-R$ 500` | `-50000 BRL` | negatives |
| 31 | `R$ 0,00` | `amountMinor 0`, status `ok` | **zero ≠ absent** |
| 32 | `R$ 99.999.999.999.999.999.999` | **drop `overflow`** | int64 bound (after scaling) |
| 33 | `1,23,456` / `1.2.3` / `5.` | **drop `malformed`** | D3 fallback (`.5` is **not** here — see row 36) |
| 34 | `1234.56` (no unit) | **drop `no_value_kind`** | no unit dimension (D6) |
| 35 | `BRL 1.234,56` vs `BRL 1,234.56` | **identical value dict** (modulo `verbatim`) | locale parity within a valid money context (bare `1.234,56` would drop `no_value_kind`, row 34) |
| 36 | `.5` | `0.5` (leading-zero exception; **not** `malformed`) | the `.5` boundary, pinned |
| 37 | `R$ 100 (01/02/2024)` | `10000 BRL`, `asOf` **absent**, warning `ambiguous_date`, status `ok` | date ambiguity does not drop the value (D8) |

**T2 — Verbatim round-trip.** `value.verbatim` is a byte-exact substring of the source; normalizing
twice yields an identical result; no `content`/`quote` byte changes.

**T3 — Drop-not-guess.** A parametrized test asserts that every ambiguous fixture above is **absent**
from the graph (no value props) **and** `normalizationStatus` names the reason. Explicitly: no fixture
may produce a value differing from the human reading.

**T4 — Layer-1 parity mirror.** A test mirrors `_valid_iso_date ↔ _iso_date_prefix`: for a fixture set,
`values.normalize` accepts iff `Point` validation accepts, and a malformed typed value (negative
exponent, non-ISO currency, float `amountMinor`, overflow) is rejected at the contract.

**T5 — Determinism and purity.** Same input ⇒ same output across process boundaries; no environment
dependence (run under `LC_ALL=C` and `LC_ALL=pt_BR.UTF-8`, identical results); a golden-vector test pins
`NORMALIZER_VERSION` behaviour.

**T6 — Commit-id golden vector.** A payload with no values keeps its pre-change `client_commit_id`; a
payload with values round-trips its id (client-computed == server-computed).

**T7 — Supersession.** `_fact_value_contradiction` fires REVISES on a changed `amountMinor` even when
the prose is near-identical; and does **not** fire on an unchanged `amountMinor`.

**T8 — Rebuild durability.** After `rebuild_all`, every value prop survives (depends on WS-A D2); the
recompute-and-compare canary reports drift as a WARN + counter, never a hard failure.

**T9 — Eval coupling.** The write-path benchmark's prose anchors are unchanged (dual-write holds); no
gold re-seal.

**T10 — Aggregation.** `SUM` groups by currency; a cross-currency sum raises an explicit error; dropped
values are excluded and counted; `0` contributes `0` and is not treated as missing.

**T11 — Declaration parity.** `ONTOLOGY.md` §4.1 value rows ↔ `contract.py` `POINT_PROPS`, as a
**diff** with the known differences enumerated (WS-A D7's pattern), plus `normalizerVersion` present on
every value.

---

## 9. Sequencing

| # | Step | Depends on | Risk |
|---|---|---|---|
| 0 | **WS-A D1** (`contract.py` + declaration parity) — value props declared | — | none (no behaviour change) |
| 1 | **`tortoise/values.py`** — pure normalizer + `NORMALIZER_VERSION` + table-driven unit tests (T1–T3, T5) | 0 | low — new module, no callers |
| 2 | **S5 wiring** — `execute_embed` calls the normalizer, folds the value into `pt_entry`, emits warnings | 1 | medium — touches the extractor's deterministic stage |
| 3 | **Layer-1 mirror** — `Point` validators + `_point_canonical` additive fold + T4/T6 | 2 | medium — an id regression 422s old commits |
| 4 | **Capture seam + projection** (WS-A D2) — value props reach `create_point` and survive rebuild; T8 | 3 | high — blocked on WS-A |
| 5 | **Comparator** — `_fact_value_contradiction` typed leg; T7 | 4 | low |
| 6 | **Search/filter + aggregation** — typed range filters + per-currency `SUM`; T10 | 4 | medium — new read surface |
| 7 | **Backfill sweep** (§7) | 4 | medium — graph-wide write, owner-visible |
| 8 | **`duration`/`multiple`/`quantity`** (OD6) | 1 | low — additive kinds |

Steps 0–2 are independently shippable and change no read behaviour. Step 4 is the gate: before it, the
value layer is not durable, and #2817's own body says so.

---

## 10. Explicitly out of scope

- **Currency conversion / FX.** No rates, no cross-currency arithmetic (OD7).
- **Identity / MERGE-by-name.** Same-named money items under different parents still collapse — that is
  **#2730**, and this design must not paper over it.
- **Cryptocurrency / non-ISO units.**
- **Multi-value points** (a point asserting several distinct numbers) — **OD3**.
- **Pack per-kind value declarations** — **WS-B #2818** (the property-declaration surface).
- **Temporal types** (intervals, period arithmetic) beyond the `duration` `valueKind`.
- **Re-sealing the eval gold or baselines** — dual-write makes this unnecessary.
- **Read-time value synthesis** (`retrieve.py`/`assembly.py` surfacing values into the reader's
  context) — a real follow-on, not this design. Note the consequence: **the typed layer adds zero
  measured retention today** because the graders read prose only. That is expected and acceptable; it is
  not an argument for moving numbers out of prose.

---

## 11. Verifier verdict

**Cycle 1 — fresh-context adversarial verifier** (a separate `task` sub-agent with no session memory;
read-only; instructed to attack, not praise). **14 findings: 2 P0, 8 P1, 4 P2.**

| Finding | Sev | Disposition |
|---|---|---|
| `M` contradiction: D4 says drop bare `M`, but T1 row 24 resolved `8M EUR` to 8,000,000 | **P0** | **Accepted — real.** Table rebuilt: `8M EUR post-money` now **drops** (`ambiguous_magnitude`); the qualifier is tested with `8MM EUR post-money` (rows 23/24). |
| T1 row 17 arithmetic (`2 bilhões` → 200,000,000,000) | **P0** | **Rejected — verifier misread.** The table is in **minor units**: R$ 2×10⁹ = 200,000,000,000 centavos. The table is now **explicitly labelled** minor units, and row 19 states the conversion, so the misread cannot recur. |
| Bare decimals (`1234.56`, `1.5`) have no `valueKind` in D6's tagged union | P1 | **Accepted — real gap.** D6 now **requires a unit dimension**; a bare number drops `no_value_kind` (T1 row 34). Rows 3/6 re-anchored to `R$`. |
| `0.123` false-negative under the 3-digit ambiguity rule | P1 | **Accepted — real.** D3 gains a **leading-zero exception** (T1 row 10). |
| No fallback for malformed patterns (`1,23,456`, `1.2.3`, `5.`, `.5`) | P1 | **Accepted.** D3 gains an explicit `malformed` fallback (T1 row 33). |
| `800ms` has no separator, so D3 never extracts it | P1 | **Accepted — real gap.** D3 gains **step 0 — numeric-span extraction** with unit suffixes; D6 gains closed unit tables. |
| D1 (store exponent) vs D2 (§11 cache doctrine) tension | P1 | **Accepted, resolved by naming it.** The exponent is reframed as part of the **unit definition**, not a cached derivation of the amount; the departure is stated explicitly; **OD1** keeps the derive-only alternative. |
| `replay_source="journal"` diverges from WS-A's `derived` for `content_hash` | P1 | **Accepted, justified.** Documented in D9: `content_hash` mints ids and *must* recompute; a value mints nothing and restoring keeps history stable under a `NORMALIZER_VERSION` bump. |
| External-client path can send an unverified `amountMinor` | P1 | **Accepted — real hole.** D13 now specifies: the server **always derives** from `verbatim`; a client value is a **cross-check** and a mismatch is **rejected**. |
| "Dropping bare `M` costs nothing" overstates the trade | P1 | **Accepted.** D4 now states the real cost (an aggregation gap) and flags OD2 as high-impact. |
| `duration`/`quantity` units absent from the magnitude table | P1 | **Accepted.** D6 unit tables added. |
| Row 6's "money minor 150 for 2-exp currency" parenthetical was misleading | P2 | **Accepted.** Rows rewritten in consistent minor units. |
| L11's arg list omitted positional `kind, content` | P2 | **Accepted.** Corrected. |
| `hosted_api.py:8090` citation under-described | P2 | **Accepted.** Now cited as the commit door's point writes, `:8090-8120`. |

**Verifier's own verdict (verbatim):**

> `VERDICT: 14 issues (2 P0, 8 P1, 4 P2) — The design is structurally sound but has two P0
> arithmetic/contradiction errors in its test table that would lead to incorrect implementation if
> followed literally. The P1 issues are design tensions (cache doctrine contradiction, external client
> path gap, bare-number valueKind undefined, leading-zero false negative) that the doc acknowledges but
> does not resolve. Recommend owner review before approval, particularly for Row 17's 100× error, the M
> contradiction, the bare-number valueKind gap, and the external-client path.`

**Disposition summary:** 13 of 14 accepted and fixed; 1 (row 17) rejected as a verifier misread of
minor-unit scaling, with the underlying clarity problem fixed by labelling. The verifier's structural
judgement — *safe to send to the owner with the named fixes* — is the basis for this document's status
as an approval artifact. Cycle 2 (re-review of the v1a edits) is recorded below once run.

### Cycle 2

**Fresh-context verifier on the v1a revision. 5 findings: 0 P0, 1 P1, 4 P2 — all accepted.**

| Finding | Sev | Disposition |
|---|---|---|
| `.5` appeared in **both** the new leading-zero exception and the malformed fallback | P1 | **Accepted — real edit-introduced contradiction.** `.5` removed from the fallback; a new T1 row 36 pins `.5` → `0.5`. |
| `sub_minor_precision` missing from D8's reason set **and** the `normalizationStatus` enum | P1→P2 | **Accepted — implementation-blocking.** Both added; D8 now states the status enum is a **superset** of the drop-reason set and that they must stay in sync. |
| `unsupported_kind` missing from the `normalizationStatus` enum | P2 | **Accepted.** Added. |
| `sourceTurnId` named in D6/D7 but absent from the D12 fragment | P2 | **Accepted.** Declared. |
| `period` used in T1 row 29 but absent from the D12 fragment | P2 | **Accepted.** Now a declared field with a closed value set, documented in D7 and distinct from `qualifier`. |

The verifier also confirmed independently: all 18 money rows' arithmetic against minor units; the
`.5`/leading-zero boundary aside, D3 is coherent; D12's `valueKind`-not-required vs
`normalizationStatus`-required asymmetry is **correct** (a dropped value has no `valueKind` but always a
status); the D13 external-client rule has no remaining hole; the D9 canary is consistent; the D1
characterization of §11 matches WS-A D1c; and all 14 cycle-1 dispositions are accurately transcribed.

**Cycle-2 verdict (verbatim):**

> `VERDICT: 5 issues (1 P1, 4 P2) — The design is structurally sound and the cycle-1 fixes are correctly
> applied except for one edit-introduced contradiction (`.5` in both the leading-zero exception and the
> malformed fallback) and three enum/declaration gaps … The arithmetic and doctrine checks are clean.
> Safe to send to the owner as the approval artifact for #2817, provided the `.5` contradiction and the
> D8 enum gap are resolved first.`

Both blockers are resolved above. Cycle 3 (confirmation re-review) is recorded below once run.

### Cycle 3

**Fresh-context confirmation verifier. 1 finding (P2) — accepted.** It confirmed all 6 cycle-2 deltas
correct (`.5` boundary, both enum additions, `sourceTurnId`, `period` in all three places, §11
accuracy), the row numbering contiguous 1–36, every D12 prop cross-checked against every other section
with none unused, and no end-to-end contradiction — then found `ambiguous_date` in the drop-reason set
but not in the `normalizationStatus` enum, **by the document's own sync rule**.

**Disposition:** resolved by **removing** `ambiguous_date` from both sets and stating why — an ambiguous
`asOf` does not invalidate the amount, so it is a warning that suppresses `asOf`, not a value drop. A
status member no outcome can produce is the same class of bug the sync rule exists to catch. T1 row 37
and a D7 note pin it.

**Cycle-3 verdict (verbatim):**

> `VERDICT: 1 issue (0 P0, 0 P1, 1 P2) — This design is safe to send to the owner as the approval
> artifact for #2817. The one remaining finding is a latent enum inconsistency (P2) with no test case or
> code path that produces it; the owner should note it but it does not block approval.`

### Cycle 4

**Fresh-context final confirmation verifier (the last permitted cycle). 1 finding (P2) — accepted.**
It confirmed: the v1c delta applied correctly and in both directions (the reason set and the status enum
are now in sync, with `ambiguous_date` surviving only as a warning code); T1 row 37 coherent with D7 and
OD8 (a *stated but ambiguous* date is not the *absent* case the `when` fallback covers); rows 1–37
contiguous; no dangling cross-references; every D12 prop used and every prose-named prop declared; all
§11 dispositions accurate; and three spot-checked research claims genuinely supported by their sources.
Its finding: T1 row 35 asserted an "identical value dict" for two **bare** decimals, contradicting D6's
`no_value_kind` rule (row 34).

**Disposition:** fixed — row 35's inputs are now currency-anchored (`BRL 1.234,56` vs `BRL 1,234.56`),
which is also the more useful parity case (the production failure mode is a currency-denominated amount,
not a bare decimal).

**Cycle-4 verdict (verbatim):**

> `VERDICT: 1 issue (0 P0, 0 P1, 1 P2) — This design is safe to send to the owner as the approval
> artifact for #2817. The one remaining finding is a test-table inconsistency (P2) … All v1c deltas are
> correctly applied, all cross-references resolve, all §11 dispositions are accurate, and the
> arithmetic, coherence, and research-claim checks are clean.`

### Convergence

Four cycles ran (cap reached). Result: **20 findings — 2 P0, 10 P1, 8 P2** — of which **19 accepted and
fixed** and 1 rejected as a verifier misread (T1 row 17's minor-unit scaling, with the underlying clarity
problem fixed by labelling). **No P0 or P1 issue remains open.** Cycle 4's single P2 was fixed after the
cycle; the cap therefore prevents a fifth confirmation pass, so the final state is *fixed-and-unconfirmed*
for that one row only — a one-line test-table change with no design content. This is the only residual
the owner should be aware of, and it is recorded here rather than hidden.

**Recommended owner-review focus (agreed across all verifier cycles):** the `M` decision (**OD2**), the
bare-number drop (**D6**), `asOf` sourcing (**OD8**), whether v1 should cover all five value kinds
(**OD6**), and the exponent-storage departure (**OD1**).

---

## 12. Sources

**Internal (repo):**
- `docs/ONTOLOGY.md` §11 (v3.2, #398) — derived-value cache doctrine; §4.1 Point durable properties.
- `docs/plans/2026-09-10-write-contract-five-doors.md` — WS-A D1 (property declaration), D1c (§11
  doctrine), D2 (Point extras pass), D7 (parity tests).
- #2820 (problem map), #2817 (this issue), #2782 + its three research comments
  (`~/.swarm/research/2026-09-09-typed-values-and-lifecycles.md`,
  `…-numeric-storage-audit.md`, extraction-side intersection report).
- Code: `tortoise/extractor_v2.py` (`_valid_iso_date:773`, `_resolve_source_turn:3423`,
  `execute_embed:3586`, `pt_entry:3896`, `_fact_value_contradiction:2339`, `OUTPUT_CONTRACT:1005`,
  `VALUE_FIDELITY_RULE:228`), `tortoise/commit_schema.py` (`Point:269`, `_iso_date_prefix:296`,
  `canonical_payload:1035`, `_point_canonical:1105`, `compute_client_commit_id:1105`),
  `tortoise/projection/entities.py:151-228`, `tortoise/sdk.py:2577`,
  `tortoise/hosted_api.py:8090-8120` (the commit door's point writes via `sdk.create_point`),
  `tortoise/retrieval.py:808-845`, `tortoise/aggregate.py:175-181`, `tortoise/pack_registry.py:110-113`.

**External:**
- PostgreSQL — [Numeric Types](https://www.postgresql.org/docs/current/datatype-numeric.html),
  [Monetary Types](https://www.postgresql.org/docs/current/datatype-money.html).
- Crunchy Data — [Working with Money in Postgres](https://www.crunchydata.com/developers/playground/working-with-money-in-postgres).
- ISO — [ISO 4217:2015](https://www.iso.org/standard/64758.html), [currency codes](https://www.iso.org/iso-4217-currency-codes.html);
  [Wikipedia ISO 4217](https://en.wikipedia.org/wiki/ISO_4217) (short/long scale, crypto note).
- [Adyen currency codes & minor units](https://docs.adyen.com/development-resources/currency-codes).
- neonpay — [Representing money at scale is hard](https://www.neonpay.com/blog/representing-money-at-scale-is-hard);
  [pratikdhanave.com fintech handbook](https://pratikdhanave.com/blog/posts/fintech-handbook-01-representing-money.html).
- Unicode ICU — [number parsing design](https://icu.unicode.org/design/number-parsing),
  [ICU4J number-parsing problems](https://icu.unicode.org/design/number-parsing/icu4j-number-parsing-problems),
  [format/parse user guide](https://unicode-org.github.io/icu/userguide/format_parse/).
- Microsoft — [Number formatting / globalization](https://learn.microsoft.com/en-us/globalization/locale/number-formatting).
- [Chicago Manual of Style — `M`/`MM`](https://www.chicagomanualofstyle.org/qanda/data/faq/topics/Abbreviations/faq0094.html);
  [Corporate Finance Institute — MM (Millions)](https://corporatefinanceinstitute.com/resources/fixed-income/mm-millions/).
- [Wikipedia — Long and short scales](https://en.wikipedia.org/wiki/Long_and_short_scales);
  [Portuguese numbers overview (PT-PT vs PT-BR)](https://elon.io/grammar/portuguese-portugal/numbers/overview).
- Martin Fowler — Money value object (`https://martinfowler.com/`).
