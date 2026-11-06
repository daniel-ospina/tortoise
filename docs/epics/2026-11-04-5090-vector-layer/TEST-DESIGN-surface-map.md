---
title: "Integration surface map — epic #5090, the vector layer"
type: test-design
domain: planning
doc_status: draft
created: 2026-11-06
updated: 2026-11-06
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #5090, #11084, #11085, #11086, #11087, #11088, #11089, embed gate, exemption register, vector index inventory, census, capstone"
---

# Integration surface map — #5090, the vector layer

**Gate artifact** for `epic-workflow`'s **Test-Design Gate**. Issue: **#11084** (child **C1** of
`05-decompose.md`). Inputs: `03-scope.md` §1 (S1–S5), §4 (E2E-1…7) · `04-plan.md` §5 (seams), §6
(interfaces), §7 (D1–D13) · `05-decompose.md` §2 (C1–C6). **Head `af864a42a`** — every cite below is
resolved at that head, not at the `641e5f1b37` the epic docs were written at (see §9 finding **F1**).

> **Why this exists, in one line.** `S5` requires this artifact and the capstone; neither had ever
> existed for this epic, and **a capstone whose checklist says "verify end-to-end" with nothing behind
> it is worse than no capstone — it reads as passed.** This map is what makes the capstone's rows
> checkable: every surface, the layer that tests it, and the failure that layer prevents.
>
> **What it is not.** Not tests (each child writes its own), not a surface change, and not a decision
> about **where** vectors live — that is **V1** (`STORAGE-ARCHITECTURE.md` §12.1c / §14.3, architecture
> owner **`#4333`**), and it is out of this epic.

⚠️ **Namespace.** Surfaces are **`SF<n>`**. `S1–S5` are the scope items, `E2E-1…7` the scope's cases,
`D1–D13` the plan's cases, `C1–C6` the children. A fifth `S`-namespace is what forced a crosswalk in a
sibling epic (`#7967`); avoided here by fiat.

## How to read a row

- **layer** is one of `{unit, contract, live-docker, doc-assert, guard}`. `unit` = no engine, fake
  graph. `contract` = the shape a caller may rely on (key names, row shape, exit codes). `live-docker` =
  a real FalkorDB, skip-if-unavailable. `doc-assert` = the artifact/record is the subject. `guard` = a
  structural invariant that must fail loudly when violated (import cycle, duplicate key, missing
  declaration).
- **failure** names the **consequence** the layer prevents — what is wrong in the world if that surface
  is untested. It never restates the mechanism; the mechanism is in **contract**.

---

## 1. The surfaces

| SF | surface | type | flow | layer | contract | failure it prevents |
|---|---|---|---|---|---|---|
| **SF1** | the gate's **default**: shipped config → `embed_gate.embed_gate_enabled()` (`tortoise/embed_gate.py:104,107`) | config / env | in | doc-assert + unit | the flag is `TORTOISE_EMBED_GATE`; the config-surface enumerator is `tools/dod_5090_probe.py:911 flag_in_shipped_configs` (a prose-only mention — `export FOO=1` in a doc string — is *not* a shipped ON) | **A deployment built from the shipped config still has the gate off and CI is green**, because an unset flag and a config surface that does not exist are the same reading. S1's headline claim is then false in production while every test passes. |
| **SF2** | the gate's **consult point**: `encode_for_row` (`tortoise/embeddings.py:1228`) ← every graph writer | code seam | in | unit | exactly two outcomes — `EMBED` ⇒ `encode_for_store`; `NO-EMBED` ⇒ `None`; `EMBED` is the whole behaviour while the flag is unset. Deliberately **not** routed through it: `encode_batch_for_store` (turn writers) and `graph-scripts/backfill_embeddings.py` (the re-encode tool) | **A new writer calls `encode_for_store` directly and re-inherits the unenforced state `#5008` is about** — and nothing fails: the row simply carries a vector nobody asked for. Mirror risk: gating the batch or backfill arm makes a *repair* unable to restore a vector. |
| **SF3** | the row → store: `embedding` present vs `NULL`, live **vs** replay | store (FalkorDB) | both | live-docker + unit | `tortoise/vector_write.py:178 clear_embedding` / `:200 set_embedding_clause` carry the PRESERVE-on-null and WIPE-on-content-edit arms | **Live and replay disagree about whether the same text carries a vector** (the `#5004` divergence class); or a re-write resurrects a vector the gate just refused; or a content edit leaves a vector for text the node no longer holds. |
| **SF4** | the **exemption register**: `module::symbol` → `{class, reason, owner_issue}`, importable from `tortoise/` | code seam (new leaf) | in | unit + guard | `class ∈ {recognised_producer, exempt}`; **no `bypass` class**; a duplicate key **raises** at import; keys are the same `module::symbol` the probe already uses | **The authority for "this writer is legitimately exempt" lives only inside a read-only instrument**, so a writer added to the product is exempt in a report and unknown to the system — the silent bypass S2 exists to end. |
| **SF5** | the register's **import boundary**: leaf module ⇄ `tortoise/projection/__init__.py` (the head of the `projection → pack_state → sdk → projection` cycle) | import graph | in | guard | a leaf must **not** live under `tortoise/projection/`; pattern: `tortoise/vector_write.py`, `tortoise/live.py` | **A module-scope read of the config constants from inside `tortoise/projection/` raises `ImportError` on `import tortoise.sdk`** — and `import tortoise.projection` **alone still succeeds** (`projection/__init__.py:1466`), so the obvious smoke check passes and only callers break. |
| **SF6** | the **instrument ⇄ the product**: `tools/dod_5090_probe.py::WRITER_CENSUS` (`:260`) vs the register | code | both | contract | both classify the *same* writer population by the *same* key; today 15 of 15 classified, 6 recognised producers / 9 exempt / 0 bypasses | **The instrument and the product disagree about which writer a row is about** — the probe keeps printing "15 of 15 classified" while the product refuses an export. A green report over a refusal. |
| **SF7** | the engine's **index catalogue** → `_record_vector_index_inventory` (`tortoise/projection/__init__.py:18556`) | DB (engine) | in | unit (fake catalogue) + live-docker | `CALL db.indexes()` rows: `[0]` label, `[2]` a `types` mapping (`{'embedding': ['VECTOR']}`); rows shorter than 3 columns, a non-dict `types`, or a non-list type value are **skipped, not fatal** | **A malformed or partial catalogue row is read as "this label has no vector index"**, so the store reports a gap that is not real and an operator re-indexes a healthy label — or, on a `None` response, reports *nothing at all*. |
| **SF8** | the **fail-open boundary**: `_record_vector_index_inventory` ← `FalkorProjection.__init__` (unguarded) + two `hosted_api` per-request paths | call edge | in (a raise) | unit (negative) + guard | `rows is None` ⇒ attribute `None` + DEBUG, return; any exception ⇒ attribute `None` + DEBUG, return; never raises, never writes. **Fail-open is a requirement, not a tolerance** | **A hostile or unavailable catalogue breaks store construction**, so every request fails because a diagnostic read failed. The listed failure mode of the "fix" is identical to the bug: tightening it is the regression. |
| **SF9** | the **served set as ONE declaration**: `security.ENTITY_TYPE_LABELS` (`tortoise/security.py:165`) | constant | in | unit + guard | `served = set(ENTITY_TYPE_LABELS.values())` (`:18617` today) — the reporting path never re-declares it | **The reporting function re-declares the served labels and the two drift**, so a label silently leaves the report and its vectors are never mentioned again. |
| **SF10** | the **no-gap arm** — the state that does not exist: `served` is a local at `:18617`, never retained; only a gap is ever reported | in-memory attribute | out | unit | NEW: retain `served` beside the already-unconditional `_vector_indexed_labels` (`:18635`) and report the **pair**; `if gap:` (`:18642`) keeps gating the **warning only** (`:18647`), so warning semantics a caller may match on are preserved | **A healthy store says nothing, so "the store states its contract" is true only where it is broken** — and the existing pin `tests/test_4997_vector_index_parity.py:410 test_a_store_with_no_gap_does_not_warn` gets **edited** to make the new behaviour green, weakening a guard into a tautology instead of adding a second assertion. |
| **SF11** | the **census**: catalogue + per-label vector count → a report | DB (engine) | in (read-only) | live-docker (skip-if-unavailable) + unit (negative) | writes nothing; exits **non-zero** on an unreadable catalogue rather than reporting an empty population; a per-label count loop is a deliberate, bounded N+1 over the **served** labels — never a per-row read | **A census pointed at an unreadable catalogue reports an empty population** — "nothing to remove" — the false-PASS direction that hands the owner an approval request with no evidence in it; or the census **writes**, acting on the surface decision it exists only to request. |
| **SF12** | the query leg's **mechanism trace**: `scan_fallback` vs `index` (`tortoise/search_engine.py:1115`) → the caller | trace / caller | out | contract | the leg's mechanism key carries one of `index` / `scan` / `scan_fallback`; `scan_fallback` **only** when an index attempt actually failed (`:2611`) | **A full scan is reported as an index hit**, so degradation is invisible and "the answer says so" cannot be checked without reading the source — the promise E2E-6 makes. ⚠️ **Crossed by C6 only** (regression pin; `#4999` landed it and no child changes it). |
| **SF13** | the **MCP / public SDK surface**: `entity_type='object'` / `'event'` vector search | product surface | both | contract | adding or removing an MCP tool or a public SDK method needs **Daniel's approval first** (`AGENTS.md`); `tools/surface_manifest.py check` and `tools/surface-guard.py` are **drift controls**, a green run is **not consent** | **The vectors are removed (or the query path changes) and the call returns zero rows where it returned rows yesterday**, with the manifest, the guard and every test green — the surface shrank by an approval nobody recorded. |
| **SF14** | the **DoD probe → the DoD rows** (`tools/dod_5090_probe.py:2284 main`) | instrument | out | doc-assert + contract | each row is scored from a reading **the same run** produced, with the arm read back and written into the record | **A row reads `MET` off a probe run whose environment it did not exercise** — the arm not read back, or a metric quoted from a page rather than from the holdings. |
| **SF15** | **the artifacts ↔ S1–S5**: this map + the capstone (`#11089`) | documents | out | doc-assert | every S-item names ≥1 surface; every surface names ≥1 child; re-checked mechanically by `tools/test_design_map_check.py` (§8) | **The capstone cites a capability no surface covers, or a surface no layer tests** — so the checklist passes having verified nothing. This is the failure the whole lane exists to make impossible. |

---

## 2. Surfaces this epic deliberately does **not** map, and why

"None" is not a valid layer for a surface in scope — but a surface **out of scope** is not a hole, it is
someone else's row. Each of these is named so a later reader does not read its absence as coverage.

| excluded | owner | why not here |
|---|---|---|
| the read path's assembly / traversal / rerank | `#5009` | This epic adds no retrieval mechanism. SF12 pins the trace it inherits, nothing more. |
| **where** the vectors live (V1) | `STORAGE-ARCHITECTURE.md` §14.3 · architecture `#4333` | `#5090` is a **consumer** of the vector layer, not its owner; the deciding input cannot be measured today (§17.4.6). |
| creating indexes for `Object` / `Event` / `Source` / `Subject` | **V1** | It commits four resident HNSW structures and is exactly V1's content. **No child in this epic creates one.** |
| eviction / egress of the cache | `#10976` (`#11004` merged) | its own rule: a pass that cannot answer must refuse, never sweep. |
| raw turns / narrative ingress | `#10000` | larger than this epic; `P0-blocker`. |
| record authority / arming | `#7879` · `#9548` | authority, not the vector contract. |
| `_upsert_document` embeds retired text | `#8419` | root is the D10 fold (`#5088`). |

---

## 3. Every capability S1–S5 → ≥1 surface

| capability (`03-scope.md` §1) | surfaces | the layer that proves it, and what it proves |
|---|---|---|
| **S1** the rule is operative, not opt-in | **SF1**, **SF2**, **SF3**, SF14 | SF1 `doc-assert` proves a *shipped* config sets the flag (not that someone exported it in a shell); SF2 `unit` proves both directions (identifier row ⇒ `NULL`, prose row ⇒ vector); SF3 `live-docker` proves the two rows are actually distinguishable **in the store**, and that a re-write does not resurrect a refused vector. |
| **S2** the exemption register has a production home | **SF4**, **SF5**, **SF6**, SF14 | SF4 `unit`+`guard` proves the three undeclared writers resolve to a register imported **from `tortoise/`**, zero `bypass`, duplicates raise; SF5 `guard` proves the leaf placement (the only test that catches the import cycle); SF6 `contract` proves the instrument reads the product's declaration instead of owning one. |
| **S3** unretrievable vectors censused, removal decided | **SF11**, **SF12**, **SF13**, SF14 | SF11 `live-docker`+`unit` proves counts/bytes per label, **no writes**, and a non-zero exit on an unreadable catalogue; SF12 `contract` proves the degraded path is *visible* (`scan_fallback`), which is the consequence the removal would change; SF13 `contract` proves the `entity_type` answer today, which is the surface-change request's evidence. |
| **S4** the store reports its contract positively | **SF7**, **SF8**, **SF9**, **SF10**, SF12 | SF10 `unit` is the capability itself (the no-gap pair is reported); SF9 `guard` proves one declaration, not two; SF7 `unit` proves a malformed row is skipped rather than fatal; SF8 `unit` (negative) + `guard` proves fail-open survives — including the **existing** parity pin staying green. |
| **S5** the verification artifacts exist | **SF15** (+ SF14) | SF15 `doc-assert` proves this map and the capstone exist **and that the map's own claims parse** (`tools/test_design_map_check.py`); SF14 proves the closing-head probe scores rows from a run that read its own arm back. |

---

## 4. Every surface → the child that touches it

This table is what makes the issue's third check answerable — **no surface is listed that no child
touches** — and it is enforced, not asserted (§8, rule R4).

| child | issue | surfaces it changes | what it does to them |
|---|---|---|---|
| **C1** | **#11084** | **SF15** | produces this map (the artifact the other rows are checked against) |
| **C2** | **#11085** | **SF4**, **SF5**, **SF6** | adds the register leaf, moves the authority out of the probe, proves the import boundary |
| **C3** | **#11086** | **SF1**, **SF2**, **SF3** | sets the flag in each shipped config, measures the write-rate delta, pins both directions |
| **C4** | **#11087** | **SF7**, **SF8**, **SF9**, **SF10** | retains the served set, reports the pair, keeps the warning and the fail-open |
| **C5** | **#11088** | **SF11**, **SF13** | adds the read-only census; carries the surface-approval request for the removal |
| **C6** | **#11089** | **SF12**, **SF14**, **SF15** | walks **every** surface in this table through its assigned layer, against a running system |

---

## 5. Crosswalk — `04-plan.md` §5/§6 seam → surface

Completeness check in the other direction: **every seam the plan names has a row.**

| plan § | seam / interface | surface(s) | note |
|---|---|---|---|
| §5 row 1 | the gate's default (`embed_gate.py`, `ENV_FLAG`, `embed_gate_enabled`) | **SF1** | the default-*reading* code is unchanged; only shipped config moves |
| §5 row 2 | the gate's consult point (`embeddings.py:1268` → **`:1228`** at this head) | **SF2** | untouched by this epic — mapped because an untouched seam is where a new writer silently bypasses |
| §5 row 3 | the exemption register (new leaf) | **SF4**, **SF5** | placement is the risk, so the import boundary is its own surface |
| §5 row 4 | the inventory report (`projection/__init__.py:18491` → **`:18556`** at this head) | **SF7**, **SF8**, **SF9**, **SF10** | one function, four surfaces: the read, the failure mode, the declaration, the new state |
| §5 row 5 | the census (new, read-only) | **SF11** | must not run against production without `--dry-run` |
| §6.1 | `_record_vector_index_inventory` behaviour change, signature unchanged | **SF10**, **SF8** | logging moves *warn-on-gap* → *report the pair at INFO; warn on gap* |
| §6.2 | the register's contract; **no MCP/SDK surface** | **SF4**, **SF13** | `class` never `bypass`; an unclassified writer is a refusal at export |
| §6.3 | versioning / error responses (register raises on duplicates, census exits non-zero) | **SF4**, **SF11** | no API version change, no new error codes |
| §6.4 | the surface statement — S3's removal is the one thing that could change it | **SF13** | carries the approval request; **SF13 is the row a capstone must not skip** |
| §7 | D1–D13, each already naming its layer | distributed | D1/D2→SF1–SF3 · D3/D4→SF4+SF6 · D5–D8→SF10/SF9/SF8 · D9–D11→SF11 · D12→SF15 · D13→SF12 (=`scan_fallback` pin) |

---

## 6. Integration-checklist notes (the items this epic must not skip)

Straight from the `test-design` checklist, restricted to the items that actually bite here.

| checklist item | where it bites | what the map requires |
|---|---|---|
| **empty vs null** | SF7 / SF8 / SF10 | two different **facts**, exercised separately: the engine's response being `None` (`result_set is None` ⇒ attribute `None` + DEBUG, `:18608`) versus an **empty** catalogue (`[]` ⇒ attribute `set()`). ⚠️ The parameter is not the seam — `catalogue_rows` (`:18606`) falls through to `self.g.query("CALL db.indexes()").result_set` when it is `None`, so a test cannot reach the `None` attribute by *passing* `None`; it must make the **query return** `None`. ⚠️ The two arms are reached by TWO different harnesses, and confusing them is how a test claims the `None` branch while asserting the empty one: `index_rows` (`tests/test_4997_vector_index_parity.py:138`) coerces `None` to `[]` (`self._index_rows = index_rows if index_rows is not None else []`), so it can reach **only** the empty arm — the `None` arm is `test_none_result_set_means_not_measured` (`:321`), whose `_NoneGraph.query` returns `_MockResult(None)` and which asserts the attribute is `None`. |
| **malformed response** | SF7 | `len(row) < 3`, `types` not a dict, a type value not a list — each is *skipped*, and a skip must not silently become "no index exists" for that label in the reported pair. |
| **boundary values** | SF10 / SF11 | served == indexed (the no-gap case), 0 vectors on a label, a label with vectors and no index, and a catalogue that names a label outside `ENTITY_TYPE_LABELS` (must not enter `measured`, which is `measured & served`). |
| **service down / timeout** | SF8 | a raising read is covered (fail-open) **and a hanging one is a raising one**: the docker client is built with `**_bounded_server_socket_kwargs()` (`projection/__init__.py:11163` — `socket_timeout` 10.0 s default at `:408`, `retry` pinned to zero via `_no_server_retry()`), and the embedded lane passes `socket_timeout=read_to` (`:11103`), so a non-answering engine raises within the bound and lands on the existing `except Exception` ⇒ attribute `None`, store healthy. ⚠️ The one unbounded shape is the **documented per-lane opt-out** `TORTOISE_DB_SOCKET_TIMEOUT=none/off/0` (`:118`, marked *not recommended*) — name it in the test's own notes rather than treating it as coverage. |
| **concurrent access** | SF10 | the once-per-`(process, graph)` warning latch is `_VECTOR_INDEX_GAP_LOCK` + the process-global `_VECTOR_INDEX_GAP_WARNED`. The **new INFO pair must be per-instance, not latched** (plan R3) — two `Projection`s over two graphs is the assertion, and a latch on the pair would reintroduce the silence. |
| **atomic writes** | SF11 | the census writes **nothing**; "writes nothing" is asserted structurally (no write clause, read-only handle), not by trusting the docstring. |
| **ordering** | SF4 | register resolution happens at **import**, before any writer runs — a lazy registration would let a writer bypass between import and first use. |

## 7. Bug-pattern flags (`test-design` step 4)

| pattern | detected here | required verification |
|---|---|---|
| **conditional guards** | five: `embed_gate_enabled()` (SF2) · `if gap:` (SF10) · `_ver is not None and _ver[0] < 4` + `_is_embedded` (SF7/SF8) · `isinstance(types, dict)` skips (SF7) · `if not first_report` latch (SF10) | both sides of every guard; a test for the *off* side is not optional, because the off side is today's behaviour |
| **silent function skips** | `_ensure_vector_index_api`'s early `return`s (embedded backend, <4.x engine) leave the attribute unset — **absent, and indistinguishable from "no gap"** | SF10's assertion must distinguish *reported-clean* from *never measured*; `None` is the token, and it must reach the caller |
| **N+1 queries** | the census's per-label vector count is a bounded loop over the **served** labels (5 today) | keep it per-**label**, never per-row; a per-row read over `Object`'s 7,859 rows is the shape to red on |
| **race conditions** | the warning latch above; `_VECTOR_INDEX_GAP_WARNED` is process-global and never pruned | two projections, two graphs, one process — assert the pair is emitted for both and the warning latched once |
| **stale closures** | n/a | no React / no callbacks with captured state in this layer |
| **SQL business logic (pgTAP)** | the skill's rule is about Postgres functions; **this repo has none on this path.** Its intent — *mock cannot verify engine behaviour* — maps to **`live-docker`** here: a fake catalogue cannot prove FalkorDB's `db.indexes()` row shape | SF3, SF7, SF11 each carry a real-engine arm with skip-if-unavailable |

---

## 8. The map is checked, not trusted

`tools/test_design_map_check.py` parses **this document** and refuses on:

| rule | refusal |
|---|---|
| **R1** | an S-item in §3 with no surface, or a surface named in §3/§4 that has no row in §1 — **and** a surface missing from the **pinned population** `SF1…SF15`, or present but unpinned |
| **R2** | a §1 row whose layer is empty or outside `{unit, contract, live-docker, doc-assert, guard}` |
| **R3** | a §1 row whose failure cell is empty or under 8 words (a mechanism restated is not a failure) |
| **R4** | a §1 surface named by **no** child in §4 (the issue's "no surface that no child touches") |
| **R5** | a plan §5/§6 seam missing from the §5 crosswalk |

⚠️ **R1's population pin is the load-bearing half**, and it is here because review measured the hole:
deleting the `SF13` row **and every mention of it** leaves every referential check above satisfied, so
the first version of this tool exited `0` on a map with fourteen surfaces. A checklist that can lose a
row by *editing it consistently* is exactly the shrink this artifact exists to prevent — so the surface
population is pinned the way `S1–S5` and `#11084–#11089` already were, and the self-test carries that
mutation (`SF13` deleted row-and-mentions) as its own case.

```bash
uv run python tools/test_design_map_check.py          # exit 0 = clean, 1 = violations, 2 = unreadable
uv run python tools/test_design_map_check.py --self-test   # is the checker a rubber stamp?
```

**Measured at this head (`af864a42a` + this change):**

```text
test-design-map-check: 15 surface(s) · 5 capabilit(y/ies) · 6 child(ren) · 9/9 plan seam(s) crosswalked · layers=contract,doc-assert,guard,live-docker,unit
test-design-map-check: CLEAN — every rule holds
  ok   delete the SF13 row (the surface-change surface) → violation (R1 SF13: pinned surface is missing from the surface table)
  ok   blank SF10's layer (the new state S4 exists for) → violation (R2 SF10: no test layer assigned)
  ok   drop the #11088 child row (SF11/SF13 become orphans) → violation (R4 #11088: no row in the child table)
  ok   delete the S3 capability row → violation (R1 S3: no row in the capability table)
  ok   drop the §6.4 surface-statement seam from the crosswalk → violation (R5 §6.4: missing from the §5/§6 crosswalk)
  ok   restate SF2's layer as a mechanism name → violation (R2 SF2: layer 'encode_for_row' is not one of ['contract', 'doc-assert', 'guard', 'live-docker', 'unit'])
  ok   shorten SF2's failure to a mechanism restatement → violation (R3 SF2: failure cell is 3 word(s) (min 8) — a restated mechanism is not a named failure)
  ok   delete the SF13 row AND every mention of it (a consistent shrink) → violation (R1 SF13: pinned surface is missing from the surface table)
  ok   delete the child table's section heading → refusal (section `## 4.` is missing — cannot check the map)
self-test: 9/9 mutation(s) refused — the checker is not a rubber stamp
```

The `--self-test` arm exists because a checker that passes on everything is worse than no checker:
it applies the nine deletions an editor actually makes (remove a row, **remove a row and every mention
of it**, blank a cell, retype a layer as a mechanism, drop a child, drop a capability, forget a seam,
destroy the section structure) to an in-memory copy and **requires each to be caught**. That is the same
discipline the epic applies to its own probe — *an instrument that cannot fail is not an instrument.*

**It runs on every edit, not only when someone remembers.** The check is wired into CI's `docs` job
(`.github/workflows/ci.yml`, beside `tools/substrate_map_check.py`) — that job is the **REQUIRED,
path-filter-free** context, and it is the only one that can see a docs-only edit, because such an edit
selects no test surface. So a lane that deletes a row here gets a red `docs` check rather than a green
one and a smaller checklist. `.github/workflows/*` is a surface worth naming explicitly: the change that
added this line touches a workflow file, which a lane's own preflight cannot prove it may write —
so the PR body says so.

This is what lets **#11089** cite the map as its checklist: the capstone walks `SF1…SF15`, and a
deleted row, a blanked layer, or a consistently-shrunk population reds the check instead of quietly
shrinking the checklist.

---

## 9. Findings recorded while building this map

**F1 — the epic docs' line cites have drifted at this head.** Spread across three artifacts, not one:
`04-plan.md` §5 cites `projection/__init__.py:18491`, `:18570`, `:18577` and `embeddings.py:1268`;
`03-scope.md:66` (S4) and `05-decompose.md:112` (C4) cite `:18570`, `:18577`, `:18582`; `04-plan.md`
§5 additionally cites `embeddings.py:1268`. At `af864a42a` these resolve to **`:18635`, `:18642`,
`:18647` and `:1228`** respectively (`:18491` → `:18556`). ⚠️ **Not every difference here is
drift**, and the distinction is the point of the finding: the epic docs'
`tests/test_4997_vector_index_parity.py:415` is the *assertion* inside
`test_a_store_with_no_gap_does_not_warn`, and this map cites `:410`, the *test def* — that file
is **byte-identical** between `641e5f1b37` and `af864a42a` (`git diff --quiet ... -- tests/test_4997_vector_index_parity.py` → IDENTICAL), so it is a citation choice, not a move, and
this map names it as such rather than reporting a five-line drift that does not exist. Cause: the epic docs were cut at `641e5f1b37` and
`main` moved. **Not silently corrected in the plan** (it is a record of a head); this map cites the live
lines and names the delta, so a reader can tell a real move from a typo. ⚠️ A first draft of this finding
also attributed a `tools/dod_5090_probe.py:801` cite to the epic docs and attributed `:18582`/`:415` to
`04-plan.md`; **both were false** and were corrected in review (`:801` occurs in no epic doc — it is prose
inside a docstring; `flag_in_shipped_configs` is `:911`). Recorded rather than quietly dropped, because a
finding that was wrong once is evidence about how the next one should be checked.

**F2 — `docs/architecture/SUBSTRATE-REBUILD-MAP.md` states the opposite of this artifact's existence**
(`:84` *"No test-design issue and no capstone exist for E4"*, `:322` ❌, `:375` D6). That was true when
it was cut (`4f68a2aa7`); **#11084 now exists and this is its artifact.** Recorded here rather than
edited into a dated, test-guarded cut — a lane reading the substrate map should follow the pointer, not
find a second, competing status cell. The capstone half of the claim **remains true** (`#11089` is
filed but not yet an artifact).

**F3 — `CALL db.indexes()` IS bounded, and the bound is what makes SF8 sufficient.**
⚠️ **This finding was written the other way round first, and the review refuted it:** the read is *not*
unbounded. The docker lane builds its client with `**_bounded_server_socket_kwargs()`
(`projection/__init__.py:11163`), which resolves `socket_timeout` to `_DB_SOCKET_TIMEOUT_DEFAULT = 10.0`
(`:408`) and pins `retry` to zero via `_no_server_retry()`; the embedded lane passes
`socket_timeout=read_to` (`:11103`). So an engine that accepts the connection and never answers **raises
inside the bound**, and the raise is already on the fail-open arm (`:18636`) — attribute `None`, store
construction continues. That is exactly SF8's contract, so **no C4 follow-up is owed here** and none is
filed. The single unbounded shape is the documented per-lane opt-out
`TORTOISE_DB_SOCKET_TIMEOUT=none/off/0` (`:118`, *"NOT recommended"*): a deployment that takes it trades
the bound for a store-setup stall. Recorded as a **condition a reader should know about**, not as a
defect — and recorded at all because the refutation is the useful part: a map that had shipped the first
version would have sent a lane to add a timeout that is already there.

---

## 10. Provenance

| fact | command / source |
|---|---|
| no test-design artifact existed for this epic | `ls docs/epics/2026-11-04-5090-vector-layer/` → `01-align.md`…`05-decompose.md`; `git log --all --format='%h %s' --grep="#11084"` → two commits (`e91c4e0a5` the decomposition, `f2c6f1bce` its review pass), both prose in PR #11091 — neither is a test-design artifact |
| the substrate map's contrary claim | `docs/architecture/SUBSTRATE-REBUILD-MAP.md:84,322,375` |
| every `SF` row's anchor resolves at this head | `rg -n` on each cited path at `af864a42a` |
| the map's own claims parse | `uv run python tools/test_design_map_check.py` |
| no MCP tool / public SDK method added or removed | this change is one document + one read-only checker; `git diff --stat` |

Refs #5090 · Closes #11084
