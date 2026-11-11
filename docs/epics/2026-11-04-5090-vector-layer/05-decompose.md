---
title: "Decomposition — epic #5090, the vector layer"
type: plan
domain: planning
doc_status: draft
created: 2026-11-05
updated: 2026-11-06
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #5090, child issues, MECE decomposition, test-design, capstone"
---

# Decomposition — epic #5090

**Stage:** 5 of 6 (`epic-workflow` → `epic-decompose`). **Head `641e5f1b37`.**
**Inputs:** `03-scope.md` (S1–S5) · `04-plan.md`.

---

## 1. MECE census — the universe first, then the cut

**The universe is the five approved scope items, and nothing else.** Enumerated before cutting, so the
partition invariant is checkable: **every S-item maps to ≥1 child; every child maps to exactly one S-item;
counts reconcile (5 items → 6 children, +1 because S5 is two artifacts).**

| S-item | child(ren) | count |
|---|---|---|
| **S1** the rule operative | **C3** | 1 |
| **S2** exemption register homed | **C2** | 1 |
| **S3** unretrievable vectors censused | **C5** | 1 |
| **S4** contract reported positively | **C4** | 1 |
| **S5** verification artifacts | **C1** (surface map) · **C6** (capstone) | 2 |
| | **total** | **6** |

**No overlap:** no child touches more than one S-item's surface. **No gap:** no S-item is unowned.

⚠️ **Reconciled against the PRIOR decomposition, not just the issue tracker.** `#5090` carries a
**2026-10-31** decomposition comment (`#5090` · `issuecomment-6550250938`; its own census pointer is
`#5087` · `issuecomment-6550245534`) whose child set was
**six** existing children — `#4997` · `#4999` · `#5096` · `#5407` · `#4333` · `#8419` — **plus one newly
filed** (`#10465`), which is why its own list carries seven ids. Per-child disposition, so "MECE" is a
claim and not an assertion:

| prior child | state | disposition here |
|---|---|---|
| `#4997` | CLOSED (PR #5434) | **not re-filed.** Landed the measurement half only; its residual is C4 (**corrected**: the served set is never retained) and its population question is C5. |
| `#4999` | CLOSED (PR #5159) | **not re-filed.** `scan_fallback` is MET; D13 is a regression pin only. |
| `#4333` | OPEN, native child | **not re-filed.** Owns the storage architecture; cited as the home of what this epic excludes. |
| `#8419` | OPEN, parented by neither | **not re-filed.** Root is the D10 fold (`#5088`); named in §4, not claimed. |
| `#5096` | CLOSED COMPLETED | **not re-filed.** Its **query** leg is exercised by D13 (`scan_fallback`); **no** D-case issues a traversal-leg query, and the epic adds no new leg. |
| `#5407` | CLOSED COMPLETED | **not re-filed.** The three label sets now agree — the premise D8 asserts (*one declaration, not a second*). |
| `#10465` | CLOSED | **not re-filed.** Its instrument is re-runnable and is reached through E2E-7's closing-head probe run (`03-scope.md` §4), which C6 carries. |
| `#10541` | CLOSED COMPLETED 2026-11-04 | **not re-filed.** Delivered the census; **C2** is the narrower, different residual — the exemption authority has no production home. |
| *"state the rule for what earns an embedding"* | **deliberately not filed 2026-10-31** | **still not filed.** Duplicates `#5008` (CLOSED). This lane reaches the same conclusion independently. |

---

## 2. The children

### C1 — **#11084** `test-design: the vector layer — integration-surface map`

- **O:** produce the surface map `epic-workflow`'s Test-Design Gate requires — every integration surface this
  epic touches, the test layer assigned to each, and the failure mode each guards.
- **I:** surfaces enumerated from `04-plan.md` §5–§6 (the gate default, the consult point, the register leaf,
  the inventory report, the census) with a layer per surface; every capability in scope mapped to ≥1 surface.
- **T:** the child issue's body **is** the map; every S1–S4 capability appears with a named layer; downstream
  children cite it.
- **Complexity:** standard · **Blocked by:** none · **Blocks:** C6.

### C2 — **#11085** `the embedding exemption register gets a production home; DoD rows 1–2 re-scored`

- **O:** move the authority for *"this writer is legitimately exempt"* out of a read-only measurement script
  and into the product, so "no silent bypass" is a property of the system rather than of a report.
- **I:** a runtime-resolvable register in `tortoise/` keyed `module::symbol`; **three** writers that
  carried **no declared exemption** classified (`hosted_backup::restore_graph`,
  `projection.entities::_event_plain_merge`, `projection::_rebuild_all_replay`); **zero** `bypass`; a
  duplicated key **raises**.
- **T:** a check that **must change** when this lands (the "zero unclassified" condition is **already true at HEAD** — at the plan head `641e5f1b37` the probe reported **15 of 15** UNKNOWN-labelled writers classified, 6 recognised producers / 9 exempt / 0 bypasses, so it could not distinguish completion from a no-op; since `#11274` widened discovery to the container-key shape the same census reads **19 of 19** UNKNOWN-labelled writers, 6 recognised producers / 13 exempt / 0 bypasses — the `N of M` denominator is the instrument's own: it counts the writers it LABELLED, not every writer discovery walks): the **three writers that carried no declared exemption** (`hosted_backup::restore_graph`, `projection.entities::_event_plain_merge`, `projection::_rebuild_all_replay`) now resolve to a register entry **imported from `tortoise/`** — **landed**, `#11085` — and the check's second half is still open: the probe's own declaration table must stop being **the only source**, i.e. the probe must read the product's register instead of owning one. DoD rows 1–2 are
  re-scored with the reading stated; the register is a leaf module with a cycle-safety note.
- **Complexity:** standard · **Blocked by:** none · **Blocks:** **C3** (arming the gate before the register
  exists would make three undeclared writers bypass silently — that ordering is the reason this is not one issue).
- ⚠️ **Not a second rule set.** It classifies *writers*; `value_gate.RULES` remains the only statement of
  what earns a vector.

### C3 — **#11086** `arm the EMBED gate by default and record the measured write-rate effect`

- **O:** make "what earns an embedding" the write path's operative rule rather than an opt-in flag.
- **I:** `TORTOISE_EMBED_GATE` set in each shipped config surface that exists (`.env.example`,
  `docker-compose.yml`, `config/`, CI workflows); the **measured** change in the embedding write rate
  recorded with the command that produced it.
- **T:** a deployment built from the shipped config rejects identifier-only rows **with no operator action**;
  the rate delta is in the issue with its command; both directions pinned (an identifier row gets no vector, a
  prose row does).
- **Complexity:** standard · **Blocked by:** **C2** · **Blocks:** none.
- ⚠️ **This is `#5008`'s own ordering**, not a new judgement: *"how many vectors the gate
  would remove on our data … draft-and-run on tens of rows first"* (the source opens that line with a
  warning marker). The *"rate effect is measurable before it is trusted"* line is **`#4899`'s**, about the
  `value_gate` flag — a different gate — and is not quoted here.

### C4 — **#11087** `report the vector contract positively — the serves/indexes pair for every label`

- **O:** make which labels the vector leg searches and which the store indexes a fact the system states,
  instead of one reconstructed by hand.
- **I:** `_record_vector_index_inventory` (in `projection/__init__.py`) emits the pair for **every** served
  label; today the `if gap:` inside `_record_vector_index_inventory` gates the **warning only**, and the served set is never retained.
- **T:** a store with **no** gap reports the pair; a store **with** a gap still warns; a raising/malformed
  catalogue leaves `Projection.__init__` healthy (fail-open preserved); **no second label declaration** —
  the served set stays `security.ENTITY_TYPE_LABELS`.
- **Complexity:** micro · **Blocked by:** none · **Blocks:** none.
- ⚠️ **Deliberate test change:** `tests/test_4997_vector_index_parity.py::test_a_store_with_no_gap_does_not_warn`
  — its *warning* claim stands, its *silence* claim is what this ends. Stated so it is not read as a regression.
- ⚠️ **Corrected premise, and it changes the size of this child.** `_vector_indexed_labels` is assigned **unconditionally** (`self._vector_indexed_labels = measured & served`); the `if gap:` inside `_record_vector_index_inventory` gates only the **`log.warning`**, and `test_a_store_with_no_gap_does_not_warn` (in `tests/test_4997_vector_index_parity.py`) already asserts the attribute for a no-gap store. **What is missing is the SERVED set** — it is never retained — so this needs **new state**, not one un-gated emit. It is still **not** a second inventory: it extends the one function that already reads the catalogue.

### C5 — **#11088** `census the unretrievable vectors, and carry the surface call for removing them`

- **O:** quantify the stored vectors that no index and no efficient query path can serve, and put the
  consequence of removing them in front of the owner — because removal is a **surface change**.
- **I:** a **read-only** dry-run census naming every label whose stored vectors have no vector index, with
  vector count and bytes, and **exactly which `entity_type` values would change** afterwards. ⚠️ **Corrected
  2026-11-06 (`#11088`, measured):** this read "would return zero rows". That holds only for a query that
  matched *only* by meaning — a lexically-matching query keeps answering from the FTS leg — so the
  delivered census names the affected `entity_type` SET and the loss as **semantic recall**. Evidence and
  the `:Source` caveat: `S3-VECTOR-CENSUS.md` §3.1/§7.
- **T:** the census runs without writing; it exits non-zero on an unreadable catalogue rather than reporting
  an empty population; the affected query paths are enumerated; **the removal is requested, not performed**.
- **Complexity:** standard · **Blocked by:** none (the census half) · **Blocks:** none.
- ⛔ **Why the removal is not in this child's authority:** `entity_type='object'` / `'event'` vector search
  **returns rows today** by full scan — and the census widened the affected population to **FOUR labels /
  FIVE `entity_type` values** (`event`, `object`, `document`, `source`, `subject`) **on the population it
  measured** (the set is whatever `--census` prints for the graph a removal applies to), because `:Source`
  maps to `document`+`source` (`S3-VECTOR-CENSUS.md` §4.1/§7). `Source`/`Subject` carry no vector **INDEX** — an index
  fact, not a claim about the property: both labels' writers DO store `embedding`, and the 2026-09-24 inventory
  reports no per-label vector count for them at all, so what they hold is what `--census` measures, never
  something inferred from the inventory (`S3-VECTOR-CENSUS.md` §5). Removing the vectors removes that answer —
  an MCP/SDK surface change, which `AGENTS.md` requires **Daniel's approval for before implementation**.
  The census is the evidence for that request; acting on it is not a lane's. **✅ ANSWERED 2026-11-10 (owner) for the two labels the ruling names — KEEP** — the `:Object`/`:Subject` embeddings are kept, no pass may remove them or stop writing them, and because **nothing contracts** for any label, the surface approval named above is not needed. ⚠️ **It does not name `:Event`, `:Source` or `:Document`** — the rest of the census's request — so those stand on the 2026-09-24 direction and no removal of them is authorised either. Recorded at `#11395` comment `6781921075` and mirrored into `STORAGE-ARCHITECTURE.md` §14.3 V3.
- ⚠️ **Contradiction test, recorded:** **no recorded decision authorises** this removal. The 2026-09-24
  direction is *"keep the vectors, make them searchable"* — for the unindexed labels that means adding
  indexes, i.e. more RAM and a surface change. The later **2026-10-08 billing ruling** removes the COST
  argument rather than granting one: retained bytes are revenue, so removing bytes moves the bill by
  **$0.00**. So the census is filed, the removal **was** the owner's decision — ✅ **answered KEEP on 2026-11-10 (`#11395` comment `6781921075`)**, so nothing is removed and the surface rule gates nothing — and the surviving work is the **index**, not a removal.
- ✅ **Also in this child — the reconciliation is DONE (re-pointed 2026-11-10), and this bullet previously said OWED:**
  both comments were re-pointed off *"pending V1"* — `#10976` →
  [comment `6779662659`](https://github.com/daniel-ospina/tortoise/issues/10976#issuecomment-6779662659),
  `#7879` → [comment `6779663836`](https://github.com/daniel-ospina/tortoise/issues/7879#issuecomment-6779663836) —
  to *"location decided (Postgres, `pgvector`, 2026-11-07); the work is `#4051`'s"*, each carrying the two
  caveats the old wording could not: **decided is not in force** (`TORTOISE_RECORD_VECTOR_LEG` defaults off)
  and **V1 did not decide V3**. ⚠️ **Corrected while closing out `#11395`:** a lane that cites `#4051`
  comment `6713312036` as the ruling inherits a **withdrawn** scope — its v1 *"search AND traversal in
  Postgres"* was withdrawn by `#4051` comment `6713838871` — and the live ruling is `SEARCH-ARCHITECTURE.md`
  items 1–4, with the text index staying in FalkorDB behind a measurement.

### C6 — **#11089** `capstone: clickthrough verification for the vector layer`

- **O:** the final end-to-end gate `epic-workflow`'s Capstone Gate requires — the epic cannot be marked
  complete without it passing.
- **I:** a checklist = the epic's E2E cases **+** every integration surface from C1, each verified through
  its assigned layer, against a running system rather than a reading of the diff.
- **T:** every row passes; a failing or missing capstone blocks the epic from concluding.
- **Complexity:** standard · **Blocked by:** **C1** · **Blocks:** epic closure.

---

## 3. Dependency order

```text
  C1  test-design map ──────────────────────────────▶ C6 capstone
  C4  report the contract        (independent, smallest)

  C2  exemption register ──▶ C3  arm the gate
  C5  census (removal REFUSED for :Object/:Subject, 2026-11-10)  (independent; starts now)
```

**Dispatch order:** **C4** and **C1** first (independent, and C1 unblocks C6); **C2** then **C3** (a real
ordering — arming before the register exists would let three undeclared writers bypass); **C5** in parallel
with C2/C3. **C6 last.**

**Nothing here is blocked on an owner decision.** C5's *removal* half is answered — the ruling refuses it for `:Object`/`:Subject` and no surface approval is sought (:136) — and its census half is not blocked either.

---

## 4. What is deliberately NOT filed — and why

| not filed | because |
|---|---|
| **"decide where the vector index lives" (V1)** | Home is **§14.3**; architecture owner is **`#4333`**; `#5090` is a **consumer** (changelog records the wrong-owner correction). **V1 was answered 2026-11-07** on method grounds (`docs/architecture/SEARCH-ARCHITECTURE.md` §1 — no decision rests on a measured proportion of our graph), so the input §17.4.6 names is **not an input to it**. |
| **create indexes for the four unindexed labels** | It **is** §14.3 **V3**'s content (V1 settled only WHERE the index lives), and it commits four further resident HNSW structures. The lane brief forbids it without a stated non-reversal; there is none. |
| **self-host FalkorDB** | **D11 deferred it; owner, 2026-11-05: not now.** Recorded, not scoped. |
| **embedding compression / quantization** | **§15's do-not-re-invent list** — *"no engine to apply it to"*. |
| **filtered-ANN recall measurement** | It is `#4051`'s (`SEARCH-ARCHITECTURE.md` §9 **M1**, a private-beta gate) — **not parked**: the location is decided (2026-11-07), so it has a consumer. Recorded in `02-research.md` so it is not lost. |
| **a "state the rule for what earns an embedding" child** | Duplicates **`#5008`** (CLOSED — the mechanism exists). Filing it is the failure the 2026-10-31 lane already refused. |
| **a DoD-scoring child** | **`#10465`** did it (CLOSED); the instrument is re-runnable. |
| **an economics child** | **`#4333`** (OPEN, native child) owns it. |
| **eviction / egress** | **`#10976`** (OPEN) — its census PR `#11004` is merged — and its own rule: *"a pass that cannot answer that must refuse, never sweep."* |
| **ingress** | **`#10000`** (`P0-blocker`). |
| **record authority** | **`#7879`** / **`#9548`**. |
| **`_upsert_document` retired-text embed** | **`#8419`** (OPEN) — root is the **D10 fold** (`#5088`). |
| **the `M1–M4` measurement ids** | Denote **two disjoint sets** across two live docs; §15 (`STORAGE-ARCHITECTURE.md`, "Not worth measuring") records three of `#4333`'s four as *not worth measuring*. Recorded in `03-scope.md` §7.3 (the review-gate notes) rather than re-filed. |

---

## 5. Wiring check

| consumer | affected? |
|---|---|
| `tortoise/sdk.py` (threads `vector_index_api`) | reads the attribute — **unchanged shape** |
| `tortoise/retrieval.py`, `degradation_chain`, `tools/longmem_eval/retrieve.py`, `tools/profile_read_path.py` | **no change** |
| `tools/dod_5090_probe.py` | reads the writer population — C2 makes it agree with a **product** declaration rather than an instrument one |
| `tortoise/mcp_server.py` / hosted REST | **no surface change in C1–C4, C6.** C5's removal half **would** be one, and is requested not taken |
| `config/ci-surfaces.yml` | touched only if a new test file needs a shard — **additive** |

**No MCP tool and no public SDK method is added or removed by C1–C4 or C6.** C5 carries the only one that
could, behind the mandated approval.

---

## 6. Verification plan (stage 6 hand-off)

The epic is **not** complete when these six merge. It is complete when:

1. every DoD row is `MET`, or names **an OPEN issue or an open decision's own recorded home** owning its remainder;
2. the capstone (C6) passes against a running system;
3. the surface map (C1) covers every capability, and each surface was verified through its assigned layer.

Row 4 (V1 decided and implemented) **will remain `PARTIAL`**, and that is the correct, recorded outcome —
not `MET`: `tools/dod_5090_probe.py` scores the `implemented` leg `UNMEASURED`, because a decision record
proves a decision, not an implementation, so `MET` is unreachable for this row by construction.
**V1 itself was answered on 2026-11-07**, so what its remainder names is the WORK — the
`jsonb` → `vector(384)` migration, the index build and the backfill, which are **`#4051`'s** for as long as it
is open. **An epic may close with a row whose remainder names a live OWNER — it may not close with a row naming nothing.**
§14.3 **V3**'s removal half is **settled KEEP for the two labels the ruling names — `:Object` and `:Subject`, 2026-11-10 (`#11395` comment `6781921075`)** — while `:Event`, `:Source` and `:Document` are **not covered by it** (the 2026-09-24 direction stands for them); what stays open for the ruled pair is only whether they are INDEXED, and neither is what row 4 names.
