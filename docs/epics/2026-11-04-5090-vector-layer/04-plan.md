---
title: "Epic plan — the vector layer (#5090)"
type: plan
domain: planning
doc_status: draft
created: 2026-11-05
updated: 2026-11-06
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #5090, embed gate, exemption register, vector index inventory, test-design, capstone"
---

# Epic plan — #5090, the vector layer

**Stage:** 4 of 6 (`epic-workflow` → `epic-plan`), 8 substeps. **Head `641e5f1b37`.**
**Inputs:** `03-scope.md` (approved, §5) · `01-align.md` §8 (the settled record).

## Proportionality — decided explicitly, per `AGENTS.md`'s process-weight rule

The full 8-substep pipeline is designed for an epic that adds a capability. **This one removes an
inconsistency.** Its whole surface is: one config default, one declaration, one un-gated report, one
read-only census, two documents. So each substep below is run **and** scaled to that:

| substep | depth here | why |
|---|---|---|
| 1 Journeys | **light** — 2 personas, 5 journeys | no UI; the "users" are an agent and an operator |
| 2 Workflows | **light** — 3 flows | the write path, setup, and the census — all pre-existing flows with one seam each |
| 3 Prototype | **state diagram**, not HTML | non-GUI; `epic-plan` allows the markdown diagram for non-GUI features |
| 4 Data model | **none** — and that is a finding | **no schema change in this epic**; recorded so a reader does not look for one |
| 5 Architecture | **seams only** | no component moves; five seamy call sites named |
| 6 Interfaces | **full** | this is where the epic's real contract changes live |
| 7 Detailed E2E | **full** | the 7 scope cases expanded to executable form |
| 8 Coherence + risk | **full** | one reviewer, plus the risk table |

**The failure this prevents is named:** an 8-substep artefact set around five small changes would be
machinery around behaviour — and it would be *removed* by the next lane, which is the failure
`AGENTS.md` calls out in both directions. **What is deliberately kept:** full interface contracts and full
E2E, because those are the two places where "the metric went green and the promise stayed false" lives.

---

## 1. User journeys

**Persona A — the agent** (a calling LLM or workflow that issues a memory search).
**Persona B — the operator** (whoever runs the graph: the owner today, a customer later).

| # | journey | entry state | exit state |
|---|---|---|---|
| **J1** | **Agent searches for meaning.** Issues a semantic query. | prose rows carry vectors; identifier-only rows do not | results are all *semantically* retrievable rows; no identifier-only row is dressed as a semantic match |
| **J2** | **Agent searches a label with no index.** | four of five served labels have no vector index, and two of them (`Object` 7,859 + `Event` 4,798) hold the stored vectors the 2026-09-24 count found, which a query full-scans today (`Source`/`Subject` carry no INDEX, and that reading counted none — their writers do store `embedding`, so the live population is `--census`'s to measure) | the answer arrives for a label that carries vectors, **and the trace names `scan_fallback`** so a caller can see it degraded |
| **J3** | **Operator inspects the store's contract.** | setup has run | sees the serves/indexes pair for **every** served label — not only the broken ones |
| **J4** | **Operator audits a write.** | a row was written without a vector | every embedding writer is either gated or carries a **runtime-resolvable** exemption |
| **J5** | **Operator weighs removing dead vectors.** | the 2026-09-24 count put all 12,657 stored-but-unindexed vectors on `Object` + `Event`; `Source`/`Subject` have no vector index and that reading counted none, but their writers do store `embedding` — so the census measures the live population rather than assuming it | a dry-run census names the population, its bytes, and **exactly which query results would change** — and the surface call is requested, not taken |

**Edge cases (empty / error / degraded).** In E2E: a label with zero vectors; a store with **no** gap (the
case that is silent today — E2E-4); an unreadable index catalogue. **Contract, not E2E**, and named here so
the gap is not read as coverage: an **empty store** (no D-case instantiates one) and a **writer the census
cannot classify** (D3 asserts there are *zero* such writers, the inverse of exercising one; the refusal
contract is §6.2's).

---

## 2. Workflows

**W1 — the write path.** `sdk/api → encode_for_row → embed_gate.embedding_verdict → value_gate.RULES`.
*Seam:* the gate is consulted only when `embed_gate_enabled()` is true; **S1 makes that the default**.
*Failure mode:* a writer that never reaches `encode_for_row` — **S2 gives it a declared home**.

**W2 — store setup.** `Projection.__init__ → _ensure_indexes → _ensure_vector_index_api → _record_vector_index_inventory`.
*Seam:* the `if gap:` warning inside `_record_vector_index_inventory` is preserved; **S4 retains the served set — new state — and reports the pair
beside the measured indexed set**. *Failure mode:* the read is
**fail-open by requirement** — `Projection.__init__` calls it unguarded, so a raising catalogue read must
leave store construction healthy (already true; **pinned**, not changed).

**W3 — the census.** read-only over `CALL db.indexes()` + a per-label vector count. *Automation point:* the
census is re-runnable and writes nothing. *Manual intervention trigger:* the **surface call** — removing a
label's vectors removes a searchable mode; that is a human decision under `AGENTS.md`'s surface rule, and it
is handed on, not taken.

---

## 3. Prototype — a state diagram (non-GUI)

```text
                        ┌──────────────────────────────────────────────┐
  write a row ─────────▶│ gate enabled?  (S1 — becomes yes by default) │
                        └───────────────┬──────────────────────────────┘
                              yes       │            no
                    ┌───────────────────┴──────────────────┐
                    ▼                                      ▼
        ┌───────────────────────┐                 ┌─────────────────┐
        │ identifier_only?      │                 │ embedded anyway │
        │  (value_gate.RULES)   │                 │  (today)        │
        └──────┬─────────┬──────┘                 └────────┬────────┘
            yes│         │no                              │
               ▼         ▼                                 │
        ┌──────────┐ ┌──────────┐                         │
        │ NO       │ │ EMBEDDED │◀────────────────────────┘
        │ vector   │ └──────────┘
        │ + reason │
        └──────────┘

  setup ──▶ read db.indexes() ──▶ served vs indexed  ──▶ S4: report the PAIR
                                        │                    (always)
                                        └── gap ──▶ warning (today: only here)
```

**States represented:** gate on / off · identifier-only / semantic · indexed / unindexed · report with gap /
report without gap. **The state that did not exist before is "report without gap"** — that silence is S4.

---

## 4. Data model — **none, and that is the finding**

**This epic changes no schema.** No table, column, index, migration or record projection is added or altered.

- The **exemption register** (S2) is a **declaration in code**, not a table. It must be resolvable at
  runtime; it must **not** be a second source of truth for the gate's rules — §12.2a's constraint is
  `ONE rule set`, and this speaks to *writer classification*, a different axis.
- The **census** (S3) is read-only; it persists nothing.
- The **report** (S4) is an in-memory projection attribute, like the existing `_vector_index_api`.

⚠️ **Explicitly not decided here:** any `embedding` column on `public.object`/`public.subject`. The record
forbids one today (a `CHECK` in `20261012000002_record_semantic.sql`), and lifting it is **V1's** business,
not this epic's.

---

## 5. Architecture — seams, not components

**Nothing moves.** Five call sites are touched, each already the single owner of its concern:

| seam | file | change | risk |
|---|---|---|---|
| the gate's default | `tortoise/embed_gate.py` (`ENV_FLAG`, `embed_gate_enabled`) | shipped config sets it; **the default-reading code is unchanged** | a config default can be overridden per deployment — intended |
| the gate's consult point | `embed_gate.embed_gate_enabled()` in `tortoise/embeddings.py` | **untouched** | — |
| the exemption register | new leaf module (pattern: `tortoise/vector_write.py`, `tortoise/live.py`) | add | a leaf must not live under `tortoise/projection/` — that package's `__init__` is the head of the `projection → pack_state → sdk → projection` cycle, where a module-scope read of the config constants is a **reproduced** `ImportError` (`import tortoise.sdk` / `import tortoise.pack_manifest_store` break; `import tortoise.projection` alone still succeeds, which is why the cycle is easy to miss — `_config_classes()`'s docstring in `projection/__init__.py` documents it) |
| the inventory report | `_record_vector_index_inventory` in `tortoise/projection/__init__.py` | **retain the SERVED set** beside the already-unconditional `_vector_indexed_labels` (`self._vector_indexed_labels = measured & served`) and report the pair; the `if gap:` inside `_record_vector_index_inventory` keeps gating the **warning** only; keep fail-open | **the read is on the unguarded `Projection.__init__` path** — covered by an existing pin |
| the census | `tools/` (new, read-only) | add | must not run against production without `--dry-run` |

**Failure modes addressed:** (a) a raising catalogue read breaks store construction → **fail-open is
mandatory and already pinned**; (b) a second label declaration drifts → **the served set stays
`security.ENTITY_TYPE_LABELS`, one declaration**; (c) a deploy overrides the new default → **reported by the
existing probe**, not silent.

---

## 6. Interfaces

**6.1 `_record_vector_index_inventory` (S4) — behaviour change, signature unchanged.**
`(self, catalogue_rows=None) -> None` today; unchanged. The change is that it retains and exposes **the served set** beside the already-unconditional
`_vector_indexed_labels` (assigned on every successful read — there is no early return on an empty `gap`);
`if gap:` inside `_record_vector_index_inventory` keeps gating the warning only. Logging moves from *"warn on gap"* to *"report the pair at INFO; warn on gap"*, so the warning
semantics a caller may match on are preserved. **The pin that must STAY GREEN:**
`tests/test_4997_vector_index_parity.py::test_a_store_with_no_gap_does_not_warn` — it captures at
`logging.WARNING` and asserts no `#4997` warning record plus `_vector_indexed_labels == set(SERVED)`, and
S4 preserves both, so **it should survive unchanged**: S4 ADDS a no-gap INFO assertion rather than editing
this one. The child states this so the new assertion is not mistaken for a change to the existing guard.

**6.2 The exemption register (S2) — new, internal, no MCP/SDK surface.**
A frozen mapping from `module::function` to a classification record `{class, reason, owner_issue}` where
`class ∈ {recognised_producer, exempt}`. Lookup by the same `module::symbol` key `tools/dod_5090_probe.py`
already uses, so the instrument and the product cannot disagree about *which* writer a row is about.
**⛔ No `bypass` class** — an unclassified writer is a **refusal at export time**, not a default.

**6.3 Versioning / error responses.** No API version changes; no new error codes. The register raises on
duplicate keys (a duplicated declaration must not silently win), and the census exits non-zero on an
unreadable catalogue rather than reporting an empty population.

**6.4 Surface statement (⛔ the hard rule).** Nothing here adds or removes an MCP tool or public SDK method.
**S3 is the one place that could**: removing the embeddings held by the labels that have no vector index
empties the **dense leg** for those labels, so a query that matched only by meaning returns **zero rows**.

⚠️ **CORRECTED 2026-11-06 (`#11088`, measured)** — two things this paragraph used to get wrong:

1. **The population.** It named `entity_type='object'` / `'event'` — two values. The census measures
   **FOUR labels / FIVE `entity_type` values**: `:Event`, `:Object`, `:Source`, `:Subject` →
   `event`, `object`, `document`, `source`, `subject` — **on the population it ran on**; the affected
   set is whatever `--census` prints for the graph a removal applies to, because a label holding no
   vectors cannot be affected. Stating it for two of five under-reports the
   change, and under-reporting is the direction that matters for an approval request
   (`S3-VECTOR-CENSUS.md` §4.1/§7).
2. **The consequence.** It said those queries "return zero rows" — false while the FTS leg answers
   (`classify_query` turns every leg on for a text query), which is why the loss is **semantic recall**
   (`S3-VECTOR-CENSUS.md` §3.1; §7 scopes the `:Source` caveat).

(`Source`/`Subject` carry no vector INDEX, though both labels' writers do store `embedding`, so the census
measures the live population — §12.1b.) That is a surface change and **requires Daniel's approval before
it is implemented** — so it is carried in the child's body as a request, with the census as its evidence.

---

## 7. Detailed E2E test cases

Expanded from `03-scope.md` §4. Each names its layer.

| id | layer | setup | assertion | traces |
|---|---|---|---|---|
| **D1** | unit | fake graph; `TORTOISE_EMBED_GATE` **unset**; shipped config loaded | an identifier-only row is written with `embedding IS NULL`, and the write records the reason | S1 |
| **D2** | unit | same | a prose row is written **with** a vector; the two rows differ by `embedding IS NULL` | S1 |
| **D3** | unit | the register imported | every `module::symbol` in `WRITER_CENSUS` resolves to `gated` or a register entry; **zero** unclassified; **zero** `bypass` | S2 |
| **D4** | **guard** | a duplicated key in the register | export **raises** — a duplicate declaration cannot silently win | S2 |
| **D5** | unit | fake graph returning a catalogue with **no** gap | the serves/indexes pair is **reported** (today: silence) | S4 |
| **D6** | unit | fake graph returning a catalogue **with** a gap | the pair is reported **and** the gap warning still fires | S4 |
| **D7** | unit | `db.indexes()` **raising**, or **answering `None`** | `Projection.__init__` stays healthy; the attribute is `None`; DEBUG logged | S4 |
| **D7′** | unit | a catalogue that is **returned** but wholly **unparseable** | the pair is reported at INFO with an empty indexed half and the gap warning still fires — the *pre-existing* per-row-skip semantics (`#4997`), unchanged by `#11087` | S4 |
| **D8** | unit | fake graph | the emitted pairing is `ENTITY_TYPE_LABELS` (served) against the measured catalogue — **one declaration, not a second** | S4 |
| **D9** | live (skip-if-unavailable) | a lane container with `Point` indexed | `Point` reports indexed; `Event`/`Object`/`Source`/`Subject` report unindexed; **the census writes nothing** | S3 |
| **D10** | unit | a census over a label with N vectors | reports N and its bytes; states the affected `entity_type` values; **issues no write** | S3 |
| **D11** | unit — **negative** | a census pointed at an unreadable catalogue | exits non-zero, does **not** report an empty population | S3 |
| **D12** | doc/assert | the epic's artifact paths | the surface map and capstone exist and the capstone references every S-item | S5 |
| **D13** | unit — **negative** | a vector query on a label with no index | the leg trace names **`scan_fallback`**, and is distinguishable from an index hit **without** reading the source | S4 |

**Brittleness avoided:** no test asserts a *count* of writers (the population moves); tests assert the
**verdict** (classified / unclassified / duplicate). No test sleeps. No test asserts a log *string* where a
warning-vs-info **level** is the contract.

---

## 8. Coherence review + risk

### 8.1 Cross-substep drift

- **Journeys ↔ E2E:** J1–J5 map to D1–D2 (J1), D5–D8 (J3), D3–D4 (J4), D9–D11 (J5). **J2** (degraded search
  naming `scan_fallback`) is already **MET** by `#4999`/PR #5159 — **D13** is the regression pin for it; D6 asserts the pair and the
  gap warning, which is a different claim. **Every behavioural test maps to a journey**; D12 is the exception and is not one — it asserts S5's
  artifacts, and S5 is a workflow mandate with no user journey.
- **Data model ↔ interfaces:** the register is code, not a table — consistent with §4. The one place a
  schema would have entered (an `embedding` column on object/subject) is explicitly declined and assigned to V1.
- **Architecture ↔ interfaces:** the leaf-module requirement (§5) and the register's import contract (§6.2)
  agree; a register under `tortoise/projection/` would break both.

### 8.2 Risks

| # | risk | severity | mitigation |
|---|---|---|---|
| **R1** | The gate's new default rejects rows that **should** be embedded, silently reducing retrieval quality. | **high** | `#5008` ordered exactly this: **measure the rate before trusting it**. S1 is `measure then keep`, not `flip and hope`. The rate is recorded with its command, and D1/D2 pin both directions. |
| **R2** | The exemption register becomes a second source of truth and drifts from `value_gate.RULES`. | medium | It speaks to **writer classification**, never to *what earns a vector*. One rule set stays `value_gate.RULES` (verified: `embed_gate.RULE_SET is value_gate.RULES`). |
| **R3** | Un-gating the inventory report makes setup noisy on every health probe. | medium | `tortoise_health` builds a request-scoped SDK per call, and the report is emitted from `_ensure_indexes`, so it lands on the hosted per-request path. Resolved at **`#11311`**: the pair is **value-latched** per `(endpoint, graph)` (`_VECTOR_INDEX_PAIR_REPORTED`) — an unchanged pair reports **once**, a pair that **changes** (a repaired store, a label that lost its index) reports **again**, so dedup never becomes the silence this row's original wording — *"emitted per instance, not per request"* — would have allowed while leaving the noise. The existing **once-per-(process, graph)** `#4997` warning latch is untouched, and its key now carries the store's **port** as well as its host (`_stable_endpoint`). |
| **R4** | The census is mistaken for a licence to delete. | **high** | D10/D11 assert it **writes nothing** and refuses on an unreadable catalogue. The surface call is a request in the child, not an action. |
| **R5** | A reader treats this epic as having decided where vectors live. | medium | V1's location is **DECIDED (2026-11-07)**: every artifact names Postgres and `SEARCH-ARCHITECTURE.md`. What no artifact may claim is that the **WORK** is done — the `jsonb` → `vector(384)` migration, the index build and the backfill are `#4051`'s. S3's reconciliation **fixes the two neighbour bodies that already assume an answer**. |
| **R6** | The epic is closed while DoD row 4 (V1) is still `PARTIAL`. | medium | Row 4's owner is **§14.3 V1, which is ANSWERED (2026-11-07)**; what its remainder names is the **WORK** — the `jsonb` → `vector(384)` migration, the index build and the backfill, which are **`#4051`'s** and OPEN — and `MET` is unreachable for row 4 by construction, since a decision record cannot prove an implementation (`tools/dod_5090_probe.py` scores that leg `UNMEASURED`). §14.3 **V3** is a separate open question and is *not* what row 4 names. `05-decompose.md` §6 admits either **an OPEN issue or an open decision's own recorded home** at the closing head, so the two statements agree. |

### 8.3 Improvement opportunities (recorded, not scoped)

- The four unindexed labels make every search of them a full scan. **Indexing** them is one command — and it
  is exactly the reversal the brief forbids, so it stays V1's.
