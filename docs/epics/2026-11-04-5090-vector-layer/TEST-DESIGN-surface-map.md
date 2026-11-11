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
| **SF2** | the gate's **consult point**: `encode_for_row` (`tortoise/embeddings.py:1228`) ← every graph writer | code seam | in | unit | exactly two outcomes — `EMBED` ⇒ `encode_for_store`; `NO-EMBED` ⇒ `None`; `EMBED` is the whole behaviour while the flag is unset. Deliberately **not** routed through it: `encode_batch_for_store` (turn writers), `session_indexer.compute_session_embedding` (`tortoise/session_indexer.py:591`, written via `:753` — the session `:Event` producer the probe itself names in `UNGATED_CALLS`, `tools/dod_5090_probe.py:200`) and `graph-scripts/backfill_embeddings.py` (the re-encode tool) | **A new writer calls `encode_for_store` directly and re-inherits the unenforced state `#5008` is about** — and nothing fails: the row simply carries a vector nobody asked for. Mirror risk: gating the batch or backfill arm makes a *repair* unable to restore a vector. |
| **SF3** | the row → store: `embedding` present vs `NULL`, live **vs** replay | store (FalkorDB) | both | live-docker + unit | `tortoise/vector_write.py:178 clear_embedding` / `:200 set_embedding_clause` carry the PRESERVE-on-null and WIPE-on-content-edit arms; `:153 clear_then_set_prefix` is the atomic in-query clear-then-set the session and backfill writers prefer. A null vector preserves; a non-null one must land | **Live and replay disagree about whether the same text carries a vector** (the `#5004` divergence class); or a re-write resurrects a vector the gate just refused; or a content edit leaves a vector for text the node no longer holds; or a re-write's **new** vector is silently dropped, which is the defect `clear_then_set_prefix` exists to prevent. |
| **SF4** | the **exemption register**: `module::symbol` → `{class, reason, owner_issue}`, importable from `tortoise/` | code seam (new leaf) | in | unit + guard | `class ∈ {recognised_producer, exempt}`; **no `bypass` class**; a duplicate key **raises** at import; keys are the same `module::symbol` the probe already uses | **The authority for "this writer is legitimately exempt" lives only inside a read-only instrument**, so a writer added to the product is exempt in a report and unknown to the system — the silent bypass S2 exists to end. |
| **SF5** | the register's **import boundary**: leaf module ⇄ `tortoise/projection/__init__.py` (the head of the `projection → pack_state → sdk → projection` cycle) | import graph | in | guard | a leaf must **not** live under `tortoise/projection/`; pattern: `tortoise/vector_write.py`, `tortoise/live.py` | **A module-scope read of the config constants from inside `tortoise/projection/` raises `ImportError` on `import tortoise.sdk`** — and `import tortoise.projection` **alone still succeeds** (`projection/__init__.py:1466`), so the obvious smoke check passes and only callers break. |
| **SF6** | the **instrument ⇄ the product**: `tools/dod_5090_probe.py::WRITER_CENSUS` (`:260`) vs the register | code | both | contract | both classify the *same* writer population by the *same* key; today 15 of 15 classified, 6 recognised producers / 9 exempt / 0 bypasses | **The instrument and the product disagree about which writer a row is about** — the probe keeps printing "15 of 15 classified" while the product refuses an export. A green report over a refusal. |
| **SF7** | the engine's **index catalogue** → `_record_vector_index_inventory` (`tortoise/projection/__init__.py:18556`) | DB (engine) | in | unit (fake catalogue) + live-docker | `CALL db.indexes()` rows: `[0]` label, `[2]` a `types` mapping (`{'embedding': ['VECTOR']}`); rows shorter than 3 columns, a non-dict `types`, or a non-list type value are **skipped, not fatal**. The creating arm beside it — `_ensure_vector_index_api` (`:19776`, DDL at `:19830` / `:19848`) and the width it declares (`required_embedding_dim`, `:19879`), which every gated write passes to `encode_for_row` — is read **through this catalogue**: a failed creation only logs a WARNING and construction proceeds | **A malformed or partial catalogue row is read as "this label has no vector index"**, so the store reports a gap that is not real and an operator re-indexes a healthy label — or, on a `None` response, reports *nothing at all*. |
| **SF8** | the **fail-open boundary**: `_record_vector_index_inventory` ← `FalkorProjection.__init__` (unguarded) + two `hosted_api` per-request paths | call edge | in (a raise) | unit (negative) + guard | `rows is None` ⇒ attribute `None` + DEBUG, return; any exception ⇒ attribute `None` + DEBUG, return; never raises, never writes. **Fail-open is a requirement, not a tolerance** | **A hostile or unavailable catalogue breaks store construction**, so every request fails because a diagnostic read failed. The listed failure mode of the "fix" is identical to the bug: tightening it is the regression. |
| **SF9** | the **served set as ONE declaration**: `security.ENTITY_TYPE_LABELS` (`tortoise/security.py:165`) | constant | in | unit + guard | `served = set(ENTITY_TYPE_LABELS.values())` (`:18617` today) — the reporting path never re-declares it | **The reporting function re-declares the served labels and the two drift**, so a label silently leaves the report and its vectors are never mentioned again. |
| **SF10** | the **no-gap arm** — the state that does not exist: `served` is a local at `:18617`, never retained; only a gap is ever reported | in-memory attribute | out | unit | NEW: retain `served` beside the already-unconditional `_vector_indexed_labels` (`:18635`) and report the **pair**; `if gap:` (`:18642`) keeps gating the **warning only** (`:18647`), so warning semantics a caller may match on are preserved | **A healthy store says nothing, so "the store states its contract" is true only where it is broken** — and the existing pin `tests/test_4997_vector_index_parity.py:410 test_a_store_with_no_gap_does_not_warn` gets **edited** to make the new behaviour green, weakening a guard into a tautology instead of adding a second assertion. |
| **SF11** | the **census**: catalogue + per-label vector count → a report | DB (engine) | in (read-only) | live-docker (skip-if-unavailable) + unit (negative) | writes nothing; exits **non-zero** on an unreadable catalogue rather than reporting an empty population; a per-label count loop is a deliberate, bounded N+1 over the **served** labels — never a per-row read | **A census pointed at an unreadable catalogue reports an empty population** — "nothing to remove" — the false-PASS direction that hands the owner an approval request with no evidence in it; or the census **writes**, acting on the surface decision it exists only to request. |
| **SF12** | the query leg's **mechanism trace**: `scan_fallback` vs `index` (`tortoise/search_engine.py:1115`) → the caller | trace / caller | out | contract | the leg's mechanism key carries one of `index` / `scan` / `scan_fallback`; `scan_fallback` **only** when an index attempt actually failed (`:2611`) | **A full scan is reported as an index hit**, so degradation is invisible and "the answer says so" cannot be checked without reading the source — the promise E2E-6 makes. ⚠️ **Crossed by C6 only** (regression pin; `#4999` landed it and no child changes it). |
| **SF13** | the **MCP / public SDK surface**, and the HTTP routes that wrap the same SDK calls (`tortoise/hosted_api.py`: `POST /v1/objects` `:11923`, `/v1/subjects` `:11991`, `/v1/points` `:12038`, `GET /v1/search` `:12775`): `entity_type='object'` / `'event'` vector search | product surface | both | live-docker + contract | adding or removing an MCP tool or a public SDK method needs **Daniel's approval first** (`AGENTS.md`); `tools/surface_manifest.py check` and `tools/surface-guard.py` are **drift controls**, a green run is **not consent**. The `contract` half is the routing/manifest shape; the **answer** — "this `entity_type` returns these rows today" — exists only in an engine holding the vectors, so the row carries a **live** arm no fake-graph contract test can substitute for | **The vectors are removed (or the query path changes) and the call returns zero rows where it returned rows yesterday**, with the manifest, the guard and every test green — the surface shrank by an approval nobody recorded. |
| **SF14** | the **DoD probe → the DoD rows** (`tools/dod_5090_probe.py:2284 main`) | instrument | out | doc-assert + contract | each row is scored from a reading **the same run** produced, with the arm read back and written into the record | **A row reads `MET` off a probe run whose environment it did not exercise** — the arm not read back, or a metric quoted from a page rather than from the holdings. |
| **SF15** | **the artifacts ↔ S1–S5**: this map + the capstone (`#11089`) | documents | out | doc-assert | every S-item names ≥1 surface; every surface names ≥1 child; re-checked mechanically by `tools/test_design_map_check.py` (§8) | **The capstone cites a capability no surface covers, or a surface no layer tests** — so the checklist passes having verified nothing. This is the failure the whole lane exists to make impossible. |

---

## 2. Surfaces this epic deliberately does **not** map, and why

"None" is not a valid layer for a surface in scope — but a surface **out of scope** is not a hole, it is
someone else's row. Each of these is named so a later reader does not read its absence as coverage.

| excluded | owner | why not here |
|---|---|---|
| the read path's assembly / traversal / rerank | `#5009` | This epic adds no retrieval mechanism. SF12 pins the trace it inherits, nothing more. |
| **where** the vectors live (V1) | `STORAGE-ARCHITECTURE.md` §14.3 · architecture `#4333` | `#5090` is a **consumer** of the vector layer, not its owner; V1 was answered 2026-11-07 on method grounds (`docs/architecture/SEARCH-ARCHITECTURE.md` §1 — no decision rests on a measured proportion of our graph), and the input §17.4.6 names is not an input to it. |
| creating indexes for `Object` / `Event` / `Source` / `Subject` | **§14.3 V3** | It is §14.3 **V3**'s content (V1 settled only WHERE the index lives) and it commits four resident HNSW structures. **No child in this epic creates one.** |
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
| **S3** unretrievable vectors censused, removal decided | **SF11**, **SF12**, **SF13**, SF14 | SF11 `live-docker`+`unit` proves counts/bytes per label, **no writes**, and a non-zero exit on an unreadable catalogue; SF12 `contract` proves the degraded path is *visible* (`scan_fallback`), which is the consequence the removal would change; SF13 `live-docker`+`contract` proves the `entity_type` answer **against an engine that holds the rows** — the `contract` half alone proves only the routing — which is the surface-change request's evidence. |
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
| **C5** | **#11088** | **SF11**, **SF13** | adds the read-only census; carried the surface-approval request for the removal — ⚠️ **foreclosed 2026-11-10: the ruling is KEEP (`#11395` comment `6781921075`), so nothing is removed and no approval is owed** |
| **C6** | **#11089** | **SF12**, **SF14**, **SF15** | walks **every** surface in this table through its assigned layer, against a running system |

---

## 5. Crosswalk — `04-plan.md` §5/§6 seam → surface

Completeness check in the other direction: **every seam the plan names has a row.**

| plan § | seam / interface | surface(s) | note |
|---|---|---|---|
| §5 row 1 | the gate's default (`embed_gate.py`, `ENV_FLAG`, `embed_gate_enabled`) | **SF1** | the default-*reading* code is unchanged; only shipped config moves |
| §5 row 2 | the gate's consult point (`embeddings.py:1268`; the enclosing def `encode_for_row` is `:1228`, SF2 cites the **def** because that is the symbol a writer calls) | **SF2** | untouched by this epic — mapped because an untouched seam is where a new writer silently bypasses |
| §5 row 3 | the exemption register (new leaf) | **SF4**, **SF5** | placement is the risk, so the import boundary is its own surface |
| §5 row 4 | the inventory report (`projection/__init__.py:18491` → **`:18556`** at this head) | **SF7**, **SF8**, **SF9**, **SF10** | one function, four surfaces: the read, the failure mode, the declaration, the new state |
| §5 row 5 | the census (new, read-only) | **SF11** | must not run against production without `--dry-run` |
| §6.1 | `_record_vector_index_inventory` behaviour change, signature unchanged | **SF10**, **SF8** | logging moves *warn-on-gap* → *report the pair at INFO; warn on gap* |
| §6.2 | the register's contract; **no MCP/SDK surface** | **SF4**, **SF13** | `class` never `bypass`; an unclassified writer is a refusal at export |
| §6.3 | versioning / error responses (register raises on duplicates, census exits non-zero) | **SF4**, **SF11** | no API version change, no new error codes |
| §6.4 | the surface statement — S3's removal is the one thing that could change it | **SF13** | carried the approval request — ⚠️ **foreclosed 2026-11-10 (KEEP ruling), so the surface does not change**; **SF13 is the row a capstone must not skip** |
| §7 | D1–D13, each already naming its layer | **SF1**, **SF2**, **SF3**, **SF4**, **SF6**, **SF8**, **SF9**, **SF10**, **SF11**, **SF12**, **SF15** | D1/D2→SF1–SF3 · D3/D4→SF4+SF6 · D5–D8→SF10/SF9/SF8 · D9–D11→SF11 · D12→SF15 · D13→SF12 (=`scan_fallback` pin). The cell lists the surfaces rather than saying "distributed": R5 now refuses a crosswalk row whose surface cell names nothing (the `R5 <label>: names no surface` violation), because a prose-only linkage is the one thing the gate cannot check — this row was the live false RED that arm was debugged against, and `§7` is now in `PLAN_SEAMS` so deleting the row is refused too |

---

## 6. Integration-checklist notes (the items this epic must not skip)

Straight from the `test-design` checklist, restricted to the items that actually bite here.

| checklist item | where it bites | what the map requires |
|---|---|---|
| **empty vs null** | SF7 / SF8 / SF10 | two different **facts**, exercised separately: the engine's response being `None` (`result_set is None` ⇒ attribute `None` + DEBUG, `:18608`) versus an **empty** catalogue (`[]` ⇒ attribute `set()`). ⚠️ The parameter is not the seam — `catalogue_rows` (`:18606`) falls through to `self.g.query("CALL db.indexes()").result_set` when it is `None`, so a test cannot reach the `None` attribute by *passing* `None`; it must make the **query return** `None`. ⚠️ The two arms are reached by TWO different harnesses, and confusing them is how a test claims the `None` branch while asserting the empty one: `index_rows` (`tests/test_4997_vector_index_parity.py:138`) coerces `None` to `[]` (`self._index_rows = index_rows if index_rows is not None else []`), so it can reach **only** the empty arm — the `None` arm is `test_none_result_set_means_not_measured` (`:321`), whose `_NoneGraph.query` returns `_MockResult(None)` and which asserts the attribute is `None`. |
| **malformed response** | SF7 | `len(row) < 3`, `types` not a dict, a type value not a list — each is *skipped*, and a skip must not silently become "no index exists" for that label in the reported pair. |
| **boundary values** | SF10 / SF11 | served == indexed (the no-gap case), 0 vectors on a label, a label with vectors and no index, and a catalogue that names a label outside `ENTITY_TYPE_LABELS` (must not enter `measured`, which is `measured & served`). |
| **service down / timeout** | SF8 | a raising read is covered (fail-open) **and a hanging one is a raising one**: the docker client is built with `**_bounded_server_socket_kwargs()` (`projection/__init__.py:11163` — `socket_timeout` 10.0 s default at `:408`, `retry` pinned to zero via `_no_server_retry()`), and the embedded lane passes `socket_timeout=read_to` (`:11103`), so a non-answering engine raises within the bound and lands on the existing `except Exception` ⇒ attribute `None`, store healthy. ⚠️ The one unbounded shape is the **documented per-lane opt-out** `TORTOISE_DB_SOCKET_TIMEOUT=none/off/false/0` (`:123-125`, where the spellings and the *“(NOT recommended: it restores the unbounded-hang failure mode)”* note live) — name it in the test's own notes rather than treating it as coverage. |
| **concurrent access** | SF10 | the once-per-`(endpoint, graph)` warning latch is `_VECTOR_INDEX_GAP_LOCK` + the process-global `_VECTOR_INDEX_GAP_WARNED`. The **new INFO pair is value-latched, not key-latched** (`#11311`, resolving plan R3's *"must be per-instance, not latched"*): `_VECTOR_INDEX_PAIR_LOCK` + `_VECTOR_INDEX_PAIR_REPORTED[(endpoint, graph)] = (serves, indexes)`. A **key-only** latch would reintroduce the silence R3 was protecting — a repaired store could never announce the repair — so the assertion is two `Projection`s over two graphs (both report) **plus** one store reporting twice with a CHANGED pair (both report) and twice with an UNCHANGED pair (one report). The latch key carries the store's **port**, not just its host: two stores on one loopback host (`:6379`, `:16379`) are two stores (`event_store._store_identity`). |
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
| **R5** | a plan §5/§6/§7 seam missing from the §5 crosswalk (a seam matches only on the row's **whole** first cell, so `\| §5 row 10 \|` cannot stand in for `\| §5 row 1 \|` — a prefix match let one digit off hide the pinned crosswalk), a crosswalk row that names a surface with no row in §1, a crosswalk row whose **surface cell names nothing** (the third "names nothing" arm, beside R1's and R4's), **a crosswalk row that is not the table's exact 4 columns** (a **deleted** surface cell shifts the note into the surface column, where the note's own words satisfy the arm beside it), or a row an editor **splits out of the table** with an inserted alignment line |

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

**Measured on this branch** — `af864a42a` is the main-line tree the anchors below are resolved
against; the checker output is this change's own:

```text
test-design-map-check: 15 surface(s) · 5 capabilit(y/ies) · 6 child(ren) · 10/10 plan seam(s) crosswalked · layers=contract,doc-assert,guard,live-docker,unit
test-design-map-check: CLEAN — every rule holds
  ok   delete the SF13 row (the surface-change surface) → violation (R1 SF13: pinned surface is missing from the surface table)
  ok   blank SF10's layer (the new state S4 exists for) → violation (R2 SF10: no test layer assigned)
  ok   drop the #11088 child row (SF11/SF13 become orphans) → violation (R4 #11088: no row in the child table)
  ok   delete the S3 capability row → violation (R1 S3: no row in the capability table)
  ok   drop the §6.4 surface-statement seam from the crosswalk → violation (R5 §6.4: missing from the §5/§6 crosswalk)
  ok   restate SF2's layer as a mechanism name → violation (R2 SF2: layer 'encode_for_row' is not one of ['contract', 'doc-assert', 'guard', 'live-docker', 'unit'])
  ok   shorten SF2's failure to a mechanism restatement → violation (R3 SF2: failure cell is 3 word(s) (min 8) — a restated mechanism is not a named failure)
  ok   delete the SF13 row AND every mention of it (a consistent shrink) → violation (R1 SF13: pinned surface is missing from the surface table)
  ok   retype a crosswalk surface as one that has no row → violation (R5 §6.4: names SF99, which has no surface row)
  ok   blank the §6.4 surface cell (a crosswalk row that names nothing) → violation (R5 §6.4: names no surface)
  ok   rename the §7 label AND blank its surface cell (a row the gate cannot see) → violation (R5 §7: missing from the §5/§6 crosswalk)
  ok   add a crosswalk row with no `§` label and a blank surface cell → violation (R5 extra row: names no surface)
  ok   blank the §7 surface cell AND split it off with a bare separator (a row in an ungradeable block) → violation (R5 §7: names no surface)
  ok   retype §6.4's surface cell as the bare token `surface` (a row impersonating the header) → violation (R5 §6.4: names no surface)
  ok   delete the §6.4 surface cell outright (a crosswalk row one column short) → refusal (§6.4 crosswalk row has 3 column(s); the table needs exactly 4 (plan § · seam / interface · surface(s) · note) — a DELETED cell shifts the note into the surface column, where its own words satisfy the arm below)
  ok   append an ordinary §5 table with a `surface` column (must stay CLEAN) → clean (verdict unchanged — 0 violation(s) before and after)
  ok   splice a raw `|` into SF2's contract cell (a row that is no longer 7 columns) → refusal (SF2 has 8 column(s); the table needs exactly 7 (SF · surface · type · flow · layer · contract · failure) — a `|` inside a cell shifts every column after it)
  ok   splice a `\\|` into SF2's contract cell (escaped backslash + REAL delimiter) → refusal (SF2 has 8 column(s); the table needs exactly 7 (SF · surface · type · flow · layer · contract · failure) — a `|` inside a cell shifts every column after it)
  ok   append a duplicate `## 1.` section carrying a clean copy (a decoy that hides a shrink) → refusal (section `## 1.` appears twice — two sections cannot share a number, and grading the last one would hide whatever the first one lost)
  ok   delete the child table's section heading → refusal (section `## 4.` is missing — cannot check the map)
  ok   relabel `§5 row 1` as `§5 row 10` (a pinned seam one digit off — only a whole-cell match refuses it) → violation (R5 §5 row 1: missing from the §5/§6 crosswalk)
self-test: 21/21 mutation(s) behaved as designed — the checker is not a rubber stamp
```

⚠️ **The split-table case is the one this review added, because the selection was a bypass.** The
crosswalk is graded line by line inside §5: a `|` line is a row unless it is an alignment row, IS the
crosswalk header by shape, or belongs to a whole well-formed table that is provably not the crosswalk (a
different separator width, and no row whose **first cell begins with** a pinned seam token —
`_is_foreign_table`). It used to be graded
**block by block**, and a block counted as the crosswalk only when the line directly **above** its
separator was a header — so a **blank line** plus `|---|---|---|---|` inserted above `| §7 | … |` started
a block that opened with a separator and had **no** header, which was excluded from grading and left
every row beneath it unread. (The blank line is load-bearing: at the pre-fix checker the same separator
inserted with no blank line only extends the crosswalk block and reds `R5 ---: names no surface`,
measured.) Measured both ways
on the same document: blank §7's surface cell and the map reds `R5 §7: names no surface` (exit 1); blank
it **and** insert that blank line with the separator and the pre-fix checker reported `CLEAN` (exit 0)
while the fixed one still reds (exit 1).

The header exemption is therefore **shape**, not a position. A `|` line is the header only when all three
of these hold at once (`_is_crosswalk_header`): four cells, a first cell reading `plan §`, and a third
cell naming surfaces. There is **no** positional condition in it — nothing in the shipped checker looks at
the line below a candidate header — so it must not be described as one, and an editor must not add one
believing it was always there. What it closes is the content-only exemption: a crosswalk row whose
surface cell was rewritten to the bare token `surface` impersonated the header and skipped **both** R5
arms. Under the pre-fix BLOCK selection that needed the row in a header's POSITION (measured then: the
cell rewrite alone red `R5 §6.4: names no surface`, exit 1; the same rewrite with a blank line inserted
**above** the row and `|---|---|---|---|` inserted **immediately below** it graded `CLEAN`, exit 0 —
and it is the **blank line** that does the work, not the side the separator sits on. With a blank line two
shapes were swallowed, for two different reasons: separator immediately BELOW the row — the rewritten row
itself satisfied the pre-fix CONTENT header test (its surface cell read `surface`), so it was skipped as
the header; separator as the block's OWN FIRST line — `_separator(block) == 0` fails `separator > 0`, so
that block could never be recognised as crosswalk-headed and was excluded outright. Both graded `CLEAN`
(measured at the same pre-fix checker, exit 0 for each). The one shape that still reds is a blank line
BETWEEN the row and its separator (`R5 §6.4: names no surface`, exit 1) — the row then sits alone in a
separator-less block, which is graded. At this head position buys nothing:
a `|` line is graded unless it is an alignment row, IS the header by shape, or sits in a **well-formed
block that is provably not the crosswalk at all** — a different separator width, no row whose first cell
**begins with** a pinned seam token (`_is_foreign_table`) — and the rewritten `§6.4` row keeps a seam label
in its first cell, not `plan §`, so it reds (exit 1). The self-test case above pins the `plan §` requirement — drop that
one condition and the case reports `clean`, so `--self-test` reds (measured) — and there is no
alignment-row condition **in the header predicate** to pin, because that predicate has none. Three
costs are accepted in this file's own direction — a false RED costs a re-run, never a gate that reports
green over an ungraded **required** crosswalk row (no block whose row begins with a pinned seam token is
ever exempt, so a required row cannot be hidden that way): a non-crosswalk table **of the crosswalk's own
width** placed inside §5 is graded as if it were the crosswalk, and its rows red
`R5 <label>: names no surface` (measured: a 4-column table of a header plus **two** data rows gives three
such violations on an otherwise clean map); a WELL-FORMED table of a different width with no first cell
beginning with a pinned seam token stays `CLEAN` (measured at 2, 3 and 5 columns), while one that carries
such a token, or whose rows are ragged, is not exempt and is graded instead — refusing the row as
unreadable (exit 2) or reding `R5 <label>` (exit 1), measured for each shape; and **rewording the
crosswalk header** reds for the same reason (measured:
`| plan section | seam / interface | surface names | note |` → `R5 plan section: names no surface`).
**One hole is open and is not one of those exemptions:** a `|` line indented by 1–3 spaces belongs to no
block at all — blocks are built from lines starting with `|` at column 0 — so it is never graded, and an
invalid indented row grades `CLEAN` (measured; markdown still renders it as a row). A *required* row
cannot hide that way (its seam token is then missing from the graded rows and R5 reds), but a row with
no seam token can. Filed as #11415, with the fix direction (left-strip before the block test, and a
self-test mutation that indents a blanked row).
A second table belongs in its own section.

The `--self-test` arm exists because a checker that passes on everything is worse than no checker:
it applies the **twenty-one** mutations an editor actually makes (remove a row, **remove a row and every
mention of it**, blank a cell, blank a **crosswalk** row's surface cell, **split a blanked crosswalk row
out of the table with an inserted separator**, **retype a crosswalk row's surface cell as the bare token
`surface` so it impersonates the header**, **delete a crosswalk row's surface cell** (one column short),
**rename a crosswalk row and blank its cell**, **add a crosswalk row with no `§` label and a blank cell**,
**append an ordinary §5 table with a `surface` column and require it to STAY CLEAN**, restate a failure
as a mechanism, retype a layer as a mechanism, retype a crosswalk surface as one that has no row,
**splice a raw `|` into a cell**, **splice a `\\|` into a cell**, drop a child, drop a capability, forget
a seam, destroy the section structure, **append a duplicate section-number decoy**) to an in-memory copy
and **requires each mutation's stated outcome** — twenty must be caught, and one, a **NEGATIVE
control**, must leave the verdict UNCHANGED. That is the same discipline the epic applies to its own probe
— *an instrument that cannot fail is not an instrument.* "Every mention of it" is meant literally: the
case deletes the mentions, because the first version of the case **renamed** them to `SF1` and so
never built the consistent shrink its own label claimed.

**The width case and its parity sibling are load-bearing, and that is measured, not asserted.** The
arm that matters most is the one covering the rules added *last*. With `parse_surface_rows`
monkeypatched back to the previous `< 7` floor — the exact false PASS the exact-7 rule closes — the arm
exits **1**: the shifted row parks a one-word value in the failure column, **R3** fires, and both
refusal cases report it. ⚠️ State the mechanism rather than overclaiming it: what the cases prove is
that a `|`-splitting relaxation **reds the arm**, not that the exact-7 refusal is the only rule that
could notice. ⚠️ The floor is why re-opening the false PASS is no longer a one-place edit: at the
shipped floor, deleting the width case alone exits 1
(`self-test: 20 mutation(s) registered, at least 21 required`), and deleting it,
**reverting the width rule's `!= 7` to `< 7`**, *and* lowering the floor to the population that is left
still exits 1, because the parity case beside it catches the same relaxation. ⚠️ Name all three edits: deleting the case and
lowering the floor alone leaves the **rule** intact, so the parity case is still refused by the rule it
splices past, and the arm exits **0** (measured).

**The crosswalk needs the same exact-width rule, and without it there was a live false PASS.** A §5
crosswalk row is four columns and `row[2]` is its surface cell, but nothing checked that width. **Deleting**
that cell — not blanking it — leaves a three-cell row whose NOTE moves into `row[2]`, so `surface_tokens()`
reads §6.4's own *"**SF13 is the row a capstone must not skip**"* and the row is graded against a surface it
never assigned: `CLEAN — every rule holds`, exit **0**, on the REQUIRED `docs` job (measured at `182172b4f`,
for `§6.4` and for `§7` — the two rows whose note names surfaces). The shipped cases **blanked** the cell;
none **deleted** it, which is the whole difference. The width arm refuses the row as unreadable (exit 2),
the same posture as the exact-7 rule for §1, and the case `delete the §6.4 surface cell outright` is what
keeps it closed. Both directions are measured: the three delete shapes (§6.4, §7, §5 row 2) refuse; the
blank / rename-and-blank / extra-row shapes still violate (exit 1); and a CORRECT map under padding
re-wrap, a reworded §5 heading, CRLF with trailing spaces, or an ordinary two-column table appended after a
blank line all stay `CLEAN` — the arm adds no false RED.

⚠️ **"No false RED" is measured, not presumed — and it was wrong once.** The crosswalk is
selected by header SHAPE: four cells whose first names the plan section and whose third names
surfaces. Content hints ALONE (a third cell reading `surface`, or a first cell reading `plan §`)
graded an ordinary §5 table as the crosswalk, so its 3-cell rows were refused as "one column short"
— a false RED on the required check blaming a deleted cell nobody deleted (measured at `3b642c7ca`:
an appended `| area | kind | surface |` table, exit 2, `CLEAN` before the per-block selection
landed). Requiring both cells AND the four-column width closes it — but only for a table of a DIFFERENT
width: appended `surface` / `surfaces` tables stay `CLEAN` (measured), while a 4-column table
appended to §5 is graded as if it were the crosswalk and reds (`R5 area: names no surface`,
measured, and the same statement appears above), which is the fail-closed direction this section
already claims. A second, validly-headed crosswalk table is still graded (its 3-cell row refuses,
its blank cell violates) and a deleted header still fails CLOSED.

The false-RED direction has its own self-test case, `append an ordinary §5 table with a
surface column (must stay CLEAN)`, and it is a **NEGATIVE control**: the one case whose expected
outcome is that the verdict is UNCHANGED, which is why the summary line counts `N/N mutation(s)
behaved as designed` rather than "refused". It is compared against the verdict the SHIPPED map already
earns, not against an absolute "clean" — an absolute comparison failed the control on any map carrying
a single unrelated violation and accused the checker (`1/21 NOT caught`) of the document's fault
(measured). It is load-bearing in the measured direction: restoring header matching that ignores the seam tokens makes
it FAIL (`expected clean, got refusal`, exit 1). ⚠️ Like the width rule it is a ONE-place guarantee,
not a three-place one — reverting the shape test, deleting the case and lowering the floor to 16 leaves
the arm green with the false RED back (measured: self-test exit 0, the `surface`-column table exit 2),
while deleting the case alone is already red (`20 mutation(s) registered, at least 21 required`).

**The parity case is the one this review added, because the rule that had no case was the parity
rule.** `cells()` splits on backslash **parity** — a pipe delimits only after an **even** run of
backslashes, which is CommonMark's rule — so a single-escaped `\|` stays inside its cell, while `\\|`
is an escaped backslash followed by a **real delimiter** and splits. Reverted to the pre-parity
`(?<!\\)\|` test, the arm exits **1** on the spliced `\\|` row (`expected refusal, got clean`) — and
that is the direction that matters: pre-parity, that row parsed as 7 cells and scored CLEAN (the false
PASS), and a correct 7-cell row whose cell *ends* with `\\` immediately before the delimiter was refused
as 6 columns (the false RED). Removing the parity case alone tripped the same floor, so undoing the
rule costs two edits as well. What neither case closes is an **additive** widening: a token appended to
`ALLOWED_LAYERS` keeps both arms green (measured — adding `mechanism` and grading a row with it
passed), because the arm mutates the *document* and cannot see the allow-list. That one is review's
job, and it is a one-line diff a reviewer can see.

**It runs on every edit, not only when someone remembers.** Both arms are wired into CI's `docs` job
(`.github/workflows/ci.yml`, beside `tools/substrate_map_check.py`): the check, **and** `--self-test` —
a rule that is quietly weakened in a way a *document* mutation can reach (a reverted width or parity
rule) reds the self-test rather than passing silently, because the arm nothing runs is not a guard.
⚠️ **One rule in that class is NOT covered, and this paragraph used to claim it was.**
`MIN_FAILURE_WORDS` is a one-character edit (`8` → `7`) and the arm stays green through it — measured
sweep: `8` → `21/21` exit 0, `7` → exit 0, `6` → exit 0, `5` → exit 0, `4` → exit 0, `3` → exit 1
(`1/21 NOT caught`). The reason is in the population, not the arm: the only word-floor case shortens a
failure cell to **3** words, so a floor any higher than 3 still refuses it. The floor is therefore
review-visible only, and a reviewer reading a `MIN_FAILURE_WORDS` diff is the whole control. What the
arm does cover here is the *width* rule (`!= 7` → `< 7` → exit 1, `2/21 NOT caught`) and the parity rule
(reverted `_is_escaped` → exit 1). That job is the **REQUIRED,
path-filter-free** context, and it is the only one that can see a docs-only edit, because such an edit
selects no test surface. So a lane that deletes a row here gets a red `docs` check rather than a green
one and a smaller checklist. `.github/workflows/*` is a surface worth naming explicitly: the change that
added this line touches a workflow file, which a lane's own preflight cannot prove it may write —
and that is stated in a PR **comment**, not in the body, because the body carries the review
attestation and must not be edited after a review is recorded. ⚠️ **A residual worth stating,
measured rather than estimated.** The
population pin lives inside the tool an editor edits, so no self-test case mutates it — but a pin edit
**on its own is not silent**: dropping a surface from `SURFACES` while its row is still there reds the
check arm every time (`VIOLATION R1 SF<n>: surface is not in the pinned population SF<lo>..SF<hi>`,
exit 1, measured for SF1/SF2/SF5/SF10/SF12/SF15). ⚠️ The range it prints is the SURVIVING pin, not a
constant: an interior drop reads `SF1..SF15`, while dropping SF1 reads `SF2..SF15` and dropping SF15
reads `SF1..SF14` (measured) — grep the `SF<n>` that moved, not the range. Dropping SF13 from the pin **and** building the consistent
shrink is refused by BOTH arms, but only the check arm changed behaviour: before `R5`'s "names
nothing" arm existed that edit graded **CLEAN** (exit 0, measured at `1381578fd^`), and it now
reds `R5 §6.4: names no surface` (exit 1, measured). The self-test arm refuses the same edit in
BOTH states, and never by "not caught": two of its cases lose their mutation target, so it reports
`2/12 case(s) UNTESTABLE` before the arm and `2/21 case(s) UNTESTABLE` after it (each exit 1) — it
names a moved target instead of accusing the checker, which is a different statement from a rule
that fails to fire. The pin is silent only when the shrink edits it
**consistently with the row** *and* the row it empties is not a crosswalk row R5 reads — the
three-place edit **the first bullet** measures. The two arms therefore cover different surfaces, and the
honest limit is:

- **Two places beyond the row itself, for a surface no self-test case targets and no child names
  alone** — delete a surface row, **every mention of it** (R1/R4/R5 all referentially check what they
  read, so a row cannot go alone) *and* drop it from `SURFACES` — stays **green on both arms**
  (measured on SF5 and on SF12: `14 surface(s) · 5 capabilit(y/ies) · 6 child(ren) · 10/10 plan seam(s)
  crosswalked · layers=…` then `CLEAN — every rule holds`, exit 0, *and* `21/21 mutation(s) behaved as designed`,
  exit 0). **Nine** of the fifteen surfaces are in this class (SF3, SF4, SF5, SF6, SF7, SF8, SF9, SF12,
  SF14 — SF1, SF2, SF11 and SF13 red on the R5 arm, because their §5/§6 cells name one surface each,
  and SF10 reds on the self-test arm), and it is the
  case the paragraph above
  measures: deleting the row *without* its mentions is already a violation, so once the row and its
  mentions are gone the **pin is the edit neither arm sees**.
- **For SF2, SF10 or SF13 — the three surfaces a mutation targets — the SELF-TEST arm also reds, one
  step EARLIER than a deleted case would**, and it reds as `UNTESTABLE` rather than as "not caught":
  delete the row those cases target and the cases lose their mutation target (measured: SF13
  `2/21 case(s) UNTESTABLE`, SF2 `4/21`, SF10 `1/21` — each exit 1, and the summary line names a moved
  mutation target instead of accusing the checker), before any case is deleted. The negative control
  does not add to these counts: it asserts the verdict is UNCHANGED rather than globally clean, so a
  shrink leaves it passing instead of making it accuse the checker (`1/21 NOT caught` was exactly that
  false diagnosis before the control went verdict-relative). It is an ADDITION, not a
  substitute: SF2's `§5 row 2` cell and §13's `§6.4` cell each name one surface, so the check arm reds on
  the same edit (`R5 §5 row 2: names no surface`, `R5 §6.4: names no surface`, measured).
- **SF15 reds one step earlier too, on the CHECK arm** — it is the one surface a child names and
  nothing else (`| **C1** | **#11084** | **SF15** |`). Deleting SF15's row and every mention of it
  leaves C1 touching no surface, so `R4 #11084: touches no surface` fires (exit 1) before the pin or
  any case is involved — SF15 is why the silent class is nine rather than ten.
- **The floor is what stops the green being bought back.** Also deleting the cases that name the surface
  (SF13 has three: two delete-row cases and the `#11088` child-row case) leaves `18 mutation(s)
  registered, at least 21 required`, exit 1 — so a shrink is red once it touches a named surface, but
  `MIN_SELF_TEST_MUTATIONS` is not the first thing to fire, and it reds a shrunk population instead of
  printing a reassuring `self-test: 0/0 … not a rubber stamp`.

So the claim this arm supports is narrower than "a silent shrink is impossible": for one of the nine
surfaces that no case targets and no child names alone, a shrink is **silent**, and a reviewer sees it
only in the row-and-pin diff.

This is what lets **#11089** cite the map as its checklist: the capstone walks `SF1…SF15`, and a
deleted row, a blanked layer, or a consistently-shrunk population reds the check instead of quietly
shrinking the checklist.

---

## 9. Findings recorded while building this map

**F1 — the epic docs' line cites have drifted at this head, and two of the differences a first
pass read as drift are NOT.** The stale `projection/__init__.py` cites live in **five** artifacts, not
three — the full inventory, so a later lane can tell whether it is looking at a new one:
`01-align.md:33` (`:18491`), `02-research.md:61` (`:18570`), `03-scope.md:66` (`:18570`, `:18577`,
`:18582`), `04-plan.md:138` (`:18491`, `:18570`, `:18577`), and `05-decompose.md:104` (`:18491`),
`:105` (`:18577`), `:112` (`:18570`, `:18577`) — plus `embeddings.py:1268` in `04-plan.md` §5. At
`af864a42a` the `projection/__init__.py` cites resolve to **`:18556`, `:18635`, `:18642` and `:18647`**
— a real move, because the docs were cut at `641e5f1b37` and `main` moved. Enumerate with
`grep -rn ':18491\|:18570\|:18577\|:18582' docs/epics/2026-11-04-5090-vector-layer/*.md | grep -v
TEST-DESIGN`; re-run it before adding a six. **Not silently corrected in the plan** (it is
a record of a head): this map cites the live lines and names the delta. But the distinction between a
move and a **citation choice** is the point of saying any of it, and two files are byte-identical between
the two heads, so a difference in their numbers is never drift:

- `embeddings.py` — IDENTICAL (`git diff --quiet 641e5f1b37 af864a42a -- tortoise/embeddings.py` → exit
  0). `:1268` is `if embed_gate.embed_gate_enabled():` at **both** heads; `:1228` is the enclosing
  `def encode_for_row`. SF2 cites the def because the def is what a writer calls, the plan cites the
  consult line, and both are right.
- `tests/test_4997_vector_index_parity.py` — IDENTICAL. The epic docs' `:415` is the *assertion* inside
  `test_a_store_with_no_gap_does_not_warn`; this map cites `:410`, the *test def*. A citation choice.

⚠️ A first draft of this finding got two attributions wrong, and they are recorded rather than quietly
dropped, because a finding that was wrong once is evidence about how the next one should be checked: it
attributed a `tools/dod_5090_probe.py:801` cite to the epic docs (`:801` occurs in no epic doc — it is
prose inside a docstring; `flag_in_shipped_configs` is `:911`), and it attributed `:18582` to
`04-plan.md` and to `05-decompose.md:112` (`grep -rn 18582 docs/epics/2026-11-04-5090-vector-layer/` →
`03-scope.md:66` only).

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
`TORTOISE_DB_SOCKET_TIMEOUT=none/off/false/0` (`:123-125`, *"NOT recommended: it restores the unbounded-hang failure mode"*): a deployment that takes it trades
the bound for a store-setup stall. Recorded as a **condition a reader should know about**, not as a
defect — and recorded at all because the refutation is the useful part: a map that had shipped the first
version would have sent a lane to add a timeout that is already there.

---

## 10. Provenance

| fact | command / source |
|---|---|
| no test-design artifact existed for this epic | `ls docs/epics/2026-11-04-5090-vector-layer/` → `01-align.md`…`05-decompose.md`, no test-design file; the only `#11084` commits at the time of this check were `e91c4e0a5` (the decomposition) and `f2c6f1bce` (its review pass) — `git show --stat --format='%h %s' e91c4e0a5 f2c6f1bce` shows both touch only the epic's prose (01–05 + two storage docs), so neither is a test-design artifact. ⚠️ Deliberately a **dated measurement on named shas**, not a live `--grep`: a `--grep` count moves as commits land (this branch's own commits match, and its squash subject matches after landing), and scoping it `--not <this branch>` cannot work either — `e91c4e0a5` is in this branch's ancestry, so that form excludes the very commit it names |
| the substrate map's contrary claim | `docs/architecture/SUBSTRATE-REBUILD-MAP.md:84,322,375` |
| every `SF` row's anchor resolves at this head | `rg -n` on each cited path at `af864a42a` |
| the map's own claims parse | `uv run python tools/test_design_map_check.py` |
| no MCP tool / public SDK method added or removed | this change is one document + one read-only checker; `git diff --stat` |

Refs #5090 · Closes #11084
