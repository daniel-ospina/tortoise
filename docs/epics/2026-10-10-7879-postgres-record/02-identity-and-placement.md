---
title: "Interface E — the canonical id contract, and the field-level placement table (#7955)"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-11
updated: 2026-10-11
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #7955, epic #7879, interface E, canonical id contract, placement table, stable-name replay keys"
---

# Interface E — the canonical id contract, and the field-level placement table

**Issue:** **#7955** — *§17.5 the field-level PLACEMENT TABLE*. **Lane:** `c6-durability`. **Parent epic:** **#7879**.
**Ruling:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) — **Postgres is the system of record; FalkorDB is a derived hot cache.** §17's five are its consequences, **not new decisions**.
**Scope ruling:** owner, 2026-10-10 — **the only customer is the dogfood instance.** Migration safety apparatus is deliberately absent; this document adds none.
**Interface:** the workstream plan's **E** (`#7879` plan comment `6097743519` §2.5; local copy `/tmp/plan-storage.md`) · **units W1 (identity) and W2 (placement)**.
**Test-design consumers:** `#7967` surfaces **S1**, **S3**, **S6** (capstone **S5** is the criterion this table is the evidence for).
**Measured at:** the placement enumeration and its writer table are the **2026-10-10 pass** (recorded durably in `#7955` comment `6097993997`; local copy `/tmp/w2-placement.md`), not the head of this branch. The canonical id contract and the stable-name replay keys were **re-verified against the code** on 2026-10-11. ⚠️ **A code citation below is `file → symbol / query text`, never `file:line`** — line numbers drift with every unrelated edit, so the writer column names the file and the clause, and the load-bearing claims are symbol-pinned (§1.2, §1.3, §2.7) and machine-pinned by `tests/test_identity_contract.py`.

> ⚠️ **This is a contract, so it is test-pinnable.** Every claim below that a machine can check is pinned by `tests/test_identity_contract.py`. A claim without a pin is marked as prose.

---

## 0. Plan — before the prose (why this shape)

### 0.1 Confirmed problem

The two-store split cannot be built without two things it does not have: **(a)** a key both stores agree names the same node by, and **(b)** a per-field decision about which store owns each property. The issue states the second as an *enumeration*, and a prior measurement pass produced one (216 names → 185 PRIMARY / 23 DERIVED-VOLATILE / 8 UNRESOLVED, #7955 comment `6097993997`; **three measured classes corrected in review → 182 / 26 / 8, §2.4–§2.7**). The first is a genuine gap: **there is no recorded decision about the canonical id anywhere** — not in `STORAGE-ARCHITECTURE.md`, not on the graph (searched 2026-10-11: `canonical id` → the storage-architecture decision only, which *assumes* "the same canonical id as the graph node" without defining it).

The failure this prevents, named concretely: **a rebuilt edge that binds its sibling by a graph-resident id resolves to nothing, in silence** (`#2489`, `#5086`'s *"one interface that must not be broken"*), and a Postgres PK that disagrees with the graph's MERGE key turns every projection into a duplicate-node generator.

### 0.2 Alternatives considered, and why they lose

| Approach | Verdict |
|---|---|
| **A — new global id scheme** (e.g. a single `tortoise:<uuidv7>` minted by a new module, both stores re-key) | **Rejected.** It re-keys ~9,700 existing nodes and every id-holding edge — the *normalisation* work the scope ruling deliberately pushes out of scope. It buys nothing the existing scheme lacks: `ulid()` is already time-sortable, collision-safe and carried by every journal payload. |
| **B — derive the id from content** (hash the node's content) | **Rejected.** An id must be stable under content revision (`PointRevised`), and content-derived ids change on every edit — a rebuilt node would re-key and orphan every edge. Also breaks the `_entity_name_id` case where the id is a function of `name` already. |
| **C — the FalkorDB internal node id as the key** | **Rejected, and it is the defect this contract exists to prevent.** Internal ids die at rebuild (`#2489`); the journal deliberately never carries them. Replay would resolve to nothing. |
| **D — adopt the existing `id` property as canonical, define it precisely, and pin the replay keys** ✅ | **Chosen.** It is the smallest change that makes the interface true: the key already exists and is already journaled; what was missing is the *written contract* and the *enforcement*. Nothing is re-keyed, nothing is migrated, and no public surface changes. |

### 0.3 Chosen approach

1. **Write the canonical id contract** — the key property per label, the mint format, the stability rule, the cross-store rule, and the legacy rule — into this document (§1).
2. **Pin it with a test** (`tests/test_identity_contract.py`) so a violation fails CI rather than a rebuild (§1.6).
3. **Verify the stable-name replay keys against the code**, not against the plan's paraphrase (§1.3) — this produced a correction.
4. **Deliver the placement table per layer**, state the deciding test, and resolve the 8 UNRESOLVED rows (§2).
5. **Carry the `baseline_*` correction** into `STORAGE-ARCHITECTURE.md` §17.5 (§2.8).

### 0.4 Verification

- `tests/test_identity_contract.py` — pins the id format, the replay-key map, and the "never `target.id`" rule (§1.6).
- `uv run python tools/surface_manifest.py check` — proves **no public-surface change** (§1.6).
- The placement table is reconciled against the **EP scalar carve-out** (§2.2) and against the code's own declarations (`_POINT_DENY`, `_GRAPH_EVENT_TYPES`, §3's truth set).

### 0.5 Out of scope (explicitly)

- **Normalising the ~9,700 legacy ids.** Separate unit (W1b), see §1.4.
- **Postgres DDL / pgTAP.** That is `#7968` (the schema) — this contract is what it writes against.
- **No MCP tool or public SDK method is added, removed or renamed.** `tools/surface-guard.py` / `surface_manifest.py` are drift control, not consent; a surface change needs Daniel first.

---

## 1. Interface E — the canonical id contract

### 1.1 The key, per label — one stable key each

The graph does not key every node by one property, and pretending otherwise is what makes a naive Postgres schema duplicate rows. The contract is: **every node has exactly one stable key, and which property it is, is fixed by the label.**

| label | the key property | minted by | why this property |
|---|---|---|---|
| `:Point` | **`id`** | `tortoise/ids.py::ulid()` (or an accepted opaque legacy id) | the MERGE key in `create_point` / `_upsert_point_props`; the journal payload carries it on every event |
| `:Event` | **`eventId`** | caller / `ulid()` | the MERGE key (`MERGE (e:Event {eventId:$eid})`); `id` mirrors it |
| `:Subject` | **`name`** | the caller | the MERGE key — Subjects MERGE by name, and `id` may be a webhook stub ulid (`#1918`) |
| `:Object` | **`name`** | the caller | the MERGE key; `id` is `_entity_name_id(label, name)`, a pure function **of** the name |
| `:Source` | **`url`** | `source_identity.normalize_source_url` (S0a) | the MERGE key — Sources MERGE by canonicalised url; `id` mirrors `url` |

**The rule that follows from the table, and it is the one the schema needs:** a **foreign key is `(label, key)`**, never a bare id. `aboutDocument` is the worked example — it targets a `:Source`, so it resolves by `url`; the same string used as a `:Subject` would be a *different* node. This is why `STRUCTURAL_REL_LABELS` exists (§1.3).

**⚠️ Two key roles — the identity key above is NOT the structural replay key.** The table fixes the property a store mints / MERGEs a label by. §1.3 fixes a **second, narrower** map: the key a rebuilt structural edge resolves its sibling by. For `aboutPoint` / `aboutEvent` the replay key is the target's **`name`**, not `id` / `eventId` — `stub_key` returns `target.get('name')` for every derivable rel except `aboutDocument` / `extractedFrom` (which key on `url`), and `resolve_structural_target` matches a `:Point` / `:Event` by `MATCH (x:{label} {name:$key})` (`projection/edges.py`). So `name` is the **required key property** for a structural target of those two rels; an id-only `aboutPoint` / `aboutEvent` target emits **no descriptor** and the edge dies at rebuild. The two maps are deliberately different and must not be collapsed (the §1.5 pin fabricates `name` in the target dict, so it does **not** exercise this seam).

### 1.2 The mint format and the stability rule

**Format (minted `:Point` / `:Event` ids).** `ulid()` returns `<lowercase hex of the millisecond timestamp>-<12 lowercase hex>` — e.g. `1a121bfbe39-5a056891dd27`. It is validated on input by `_is_ulid` (`tortoise/sdk.py`), which accepts the canonical form (`^[0-9a-f]+-[0-9a-f]{12}$`) **and** a standard Crockford base32 ULID (26 chars). Entity handles minted by `_entity_name_id` are `<label[:3]>-<sha256[:26]>` (e.g. `sub-<hex26>`).

**Stability rule — four clauses, all enforceable:**

1. **Minted once, never changed.** An id is assigned at creation and is immutable for the node's lifetime.
2. **A rename is a new node.** A `:Subject`/`:Object`/`:Source` whose key changes is a different node; the old key is not rewritten.
3. **Supersede mints a new Point id and links old→new** (`PointSuperseded`); the old id is never edited, so the reasoning trail survives.
4. **The FalkorDB internal node id is NEVER a key** — it dies at rebuild, and the journal deliberately never carries it (`#2489`). An id is never derived from mutable content.

**Cross-store rule.** The same key names the same node in all three places: the journal carries it in the event payload (the `id` key of every `_GRAPH_EVENT_TYPES` event), Postgres holds it as the primary key of the layer's table (`#7968`), and the cache projection carries it as the node property. The contract exists so that `derived = replay(journal) ∪ preserved_config` (§3) holds *by construction* rather than by luck.

### 1.3 The second half of the same interface — the stable-name replay keys

A rebuilt structural edge must find its sibling by **stable name**, never by a graph-resident id. This is implemented in `tortoise/projection/edges.py` and is **verified against the code** here, not restated from the plan:

- **`STRUCTURAL_REL_LABELS`** maps each derivable structural rel to its **target label**: `aboutSubject→Subject`, `aboutObject→Object`, `aboutEvent→Event`, `aboutPoint→Point`, `aboutDocument→Source`, `extractedFrom→Source`.
- **`stub_key(rel, target)`** returns the replayable `(label, key)` — **`target.get('url')` for `aboutDocument` and `extractedFrom`, `target.get('name')` for all other derivable rels**, and `None` (skip emission) for an empty/non-string key. The function's own docstring states the rule: ***"NEVER `target.id`"***.
- **`resolve_structural_target(g, label, key, rel)`** is the replay-side resolver; it matches label-scoped by `name` (Subject/Event/Point) or `url` (Source), and it is the *single* resolution point (`sdk.py`'s 2b emission and the projection replay both import the same map — no duplicate key map to drift).

**⚠️ Correction to the plan / test-design as written — `coalesce(title, name)` is NOT a current replay key.** The plan §2.5, the test-design map's **S6** row, and `EXTRACTOR-V4` all still list the keys as `url` / `name` / `coalesce(title, name)`. That third key is **stale**: it was the **pre-D10** `aboutDocument` key, when a document was a `:Document` resolved by name-or-title. `#2489` moved it to **`url`** when a document became a `:Source` (a `:Source` resolves by `url`), and the code moved with it. Today the code returns `url` for `aboutDocument`, and a title-only target resolves to **nothing** (`stub_key` → `None`). The test at `tests/test_document_source_gold.py::…old_key == "coalesce(title, name)"` pins the **migration fixture's old key**, not the live key — the two are not in conflict, but a reader taking the string as current is wrong.

> **OVERRIDES:** none — this is an application of the recorded D10/`#2489` decision, not a departure from it. The correction is to the *prose that describes* the key, not to the key. (Recorded because §2.5/S6 read as authority to a lane that never opens `edges.py`.)

### 1.4 The legacy ids — the rule that makes the backfill safe later

~9,700 live ids do not match any minted shape: **8,700 `<hex>-<hex>`** (the current `ulid()` form), **1,000 `p-NNNNNNN`** (a legacy sweep format — **no current minter in the repo**), and **~30 `EV-*`** strings used as ids. The contract's position:

> **An id that does not match a minted shape is OPAQUE and VALID.** The contract governs **what new ids look like** and **how any id behaves** (stable · immutable · cross-store · label-keyed). It does **not** require existing ids to match a format, and it does **not** validate ids at read time — a validation gate on legacy data would refuse live rows for a spelling the current code never minted.

**The rule that makes normalising them a safe, later, one-shot migration** (W1b — **not done here**):

1. **The mapping is total and single-valued** — each legacy id maps to exactly one canonical id, because the legacy id is already the node's unique key; the rewrite is a *rename of the key value*, not a merge.
2. **The rewrite is a journal event, not a raw `SET`** — so `replay(journal)` reproduces it and `rebuild == live` still holds (`#5285`).
3. **It is cheap only while the only data is the dogfood instance** — after customers write rows under the old scheme it becomes a client migration, which the owner's launch criterion forbids. That is *why* the scope ruling says it may break the dogfood graph freely.

**Recorded priority:** the workstream plan (§0, §4) filed this at **P2**; the issue's **own correction comment** (`#7955` comment `6099602248`, 2026-10-10T16:18Z) raises it to **P1-severe** — *"the scope ruling removes the urgency of migration safety; it does not license launching with two id schemes."* Either way it is a **separate unit (W1b) and is not done in this deliverable**. The divergence between the plan's P2 and the issue's P1-severe is recorded here rather than silently resolved.

### 1.5 What pins the contract

**`tests/test_identity_contract.py`** — the enforcement, so the contract is real rather than prose:

| test | what it fails on |
|---|---|
| `test_ulid_shape_and_sortability` | `ulid()` drifting from `^[0-9a-f]+-[0-9a-f]{12}$`, or losing time-sortability |
| `test_ulid_and_crockford_accepted_junk_rejected` | `_is_ulid` accepting/rejecting the wrong spellings |
| `test_entity_handle_shape_and_determinism` | `_entity_name_id` drifting from `label[:3]-<hex26>`, or becoming non-deterministic (idempotent-MERGE guarantee) |
| `test_structural_rel_labels_is_the_pinned_map` | a rel added to `DERIVABLE_STRUCTURAL_RELS` without a label, or the label map changing silently |
| `test_stub_key_resolves_by_stable_name_never_id` | `stub_key` returning `target['id']` instead of `name`/`url` — **the exact regression `#2489`/`#5086` exist to prevent** |
| `test_about_document_key_is_url_not_coalesce_title` | a revert to the pre-D10 `coalesce(title, name)` key, which would resolve to nothing on a `:Source` |
| `test_stub_key_id_only_target_is_unresolvable` | emitting a null-key descriptor where the caller must SKIP |
| `test_every_derivable_rel_is_keyed_by_its_pinned_property` | a derivable rel whose key property is not the one `EXPECTED_KEY_PROP` pins, or that consults the other spelling (`name` / `url`) |

**The `target.id` pin is the load-bearing one.** It constructs a target `{"id": "<opaque graph id>", "name": "acme"}` and asserts `stub_key("aboutSubject", target) == ("Subject", "acme")` — a resolver that preferred `id` returns `("<opaque>", …)` and fails. No graph connection is needed, so the pin runs in the carve-out lane too.

### 1.6 What this does NOT change

- **No public surface change.** `tests/test_identity_contract.py` imports only existing private helpers; `surface_manifest.py check` is run to prove no MCP tool or public SDK method moved.
- **No legacy backfill** (§1.4).
- **No new id scheme** (§0.2 alternative A rejected).

---

## 2. The field-level placement table

### 2.1 The deciding test, stated precisely — and a contradiction in the document it applies

The issue names the mechanism: **§3's per-write test** — *"if the journal does not carry it, it is not derived — it is PRIMARY, and belongs in a truth-layer table"* (§17.5 quoting §3 at line 346). **But §2.1 of the plan states the inverse** — *"a field is PRIMARY **iff** the journal carries it"*. Read literally the two sentences are logical opposites: one says *non-journaled ⇒ PRIMARY*, the other says *PRIMARY ⇒ journaled*.

**The operative rule is the §3 two-branch one, made explicit** (this is what the table below applies, and the measurement already applied it):

1. **The journal carries the value verbatim** → **PRIMARY** (a record column; replay reconstructs it).
2. **Not carried, but deterministically recomputable from carried data** → **DERIVED** (EP scalars, `content_hash`, declared caches). *Recomputable* is the test, not "not journaled".
3. **Not carried, not recomputable, and a transient stamp/token** whose exact value need not survive replay → **VOLATILE** (a dirty flag, a run token, a best-effort removal marker).
4. **Not carried, not recomputable, and a fact the record must keep** → **PRIMARY-WITHOUT-CARRIER**: it belongs in the record, and the journal must be extended to carry it. This is a §3 **journal-completeness defect** (`#4240`/`#5089` class), **not** a placement of "derived". The table names each one.

Classes 3 and 4 are what §17.5's "9 of 87 derived/volatile" conflates; separating them is what resolves the 8 UNRESOLVED rows (§2.7).

### 2.2 The EP scalar carve-out — reconciled

§2.0's carve-out — **`ep_*`, `confidence`, `posterior_*`, `baseline_*` are recomputed from the structure and never stored as authority** — is applied as written, with **one documented narrowing**:

| name | carve-out says | this table | reconciliation |
|---|---|---|---|
| `ep_alpha`, `ep_beta` | DERIVED | DERIVED | ✓ holds — recomputed by the EP fold |
| `ep_dirty`, `ep_dirty_at` | DERIVED | DERIVED (volatile flag) | ✓ holds |
| `posterior_alpha`, `posterior_beta` | DERIVED | DERIVED | ✓ holds |
| `confidence` | DERIVED | DERIVED | ✓ holds — but see §2.9: the code persists a payload value into it today |
| `baseline_set` | DERIVED | DERIVED (a mirror once the baseline is journaled) | ✓ holds — see §2.7 #5: it is **not derivable today** |
| **`baseline_source`** | DERIVED | **PRIMARY when `='set-by-author'`; DERIVED otherwise** | ⚠️ **narrowing** — see §2.8 |
| `c_cal`, `lastDreamedAt` | (EP caches) | DERIVED | ✓ holds — correctly-implemented CQRS caches |

The narrowing is **not a reopening of §2.0**: the carve-out's own test is *"recomputed from the structure"*, and an **author-stated prior is not recomputed from anything** — it is the input. §2.0 does not reach it; §3's per-write test classifies it. Applying a decision correctly is not overriding it.

### 2.3–2.6 The table, per layer

**Read the "journal carrier" column as the *why*: it is §3's per-write test, source-cited.** A `PRIMARY` row names the event+key that carries it (or is marked `PRIMARY-WITHOUT-CARRIER`); a `DERIVED` row names the recomputation; a `VOLATILE` row names why the value need not survive replay. Weights are the **floor** the prior pass measured (recorded durably in `#7955` comment `6097993997`; local copy `/tmp/w2-placement.md`): the open-set `SET n += $props` passthrough makes the surface unbounded, exactly as §17.5's 87 is a floor.

**Layer assignment.** `Object`+`Subject` → **semantic**; `Point`+operators/edges → **epistemic**; `Event`+`Source`+`Session` → the **Episodic** layer. **Naming:** this document's “event stream” is the ontology's **Episodic** layer (`ONTOLOGY.md` §2), *not* the journal — the ontology itself uses “journal/event stream” for the reconstruction source, so read “event stream” here as the layer, never the record. ⚠️ `:Source` is filed under **Semantic** in `ONTOLOGY.md` §2 *and* is part of §3's truth set — the two are **different axes** (what-exists vs who-writes) and do not conflict; it is listed under the Episodic layer here because it is an append-only truth row the journal writes. The **procedural** layer owns **no property name of its own** (§2.6).

**LEGEND:** `P` = PRIMARY · `D` = DERIVED/VOLATILE · `U` = UNRESOLVED (as measured) · `P*` = PRIMARY-WITHOUT-CARRIER (journal it).

#### 2.3 Episodic (the plan's “event stream”) — `Event` · `Source` · `Session` (100 names · 94 P · 6 D · 0 U)

`Event` (30: 29 P / 1 D) · `Source` (49: 44 P / 5 D) · `Session` (21: 21 P).

*Source-layer note: `Source.reliability`, `reliabilityComponents`, `reliability_derived_at` are a **correctly-implemented CQRS cache** — the pattern the rest of the volatile set should follow; `__runId` is a **transient merge token** (stripped from the journal, best-effort removed) and is not a record field.*

#### 2.4 Semantic — `Object` · `Subject` (23 names · 20 P · 3 D · 0 U)

`Object` (16 P · 2 D) · `Subject` (4 P · 1 D).

*The properties are journaled on `ObjectRegistered` / `SubjectAdded` snapshots; `embedding` on both is **DERIVED** — the writer recomputes `encode_for_store(name)` and never reads `ev.get("embedding")` (`_upsert_object`/`_upsert_subject`, `projection/entities.py`), so it is class 2 (not carried, deterministically recomputable from the carried `name` — §2.1 rule 2), the same shape as `Point.content_hash`. The `Object` lifecycle-fold fields are split in §2.6: `supersededBy` is journal-carried verbatim (**PRIMARY**), `status` is a projection-owned **cache** (**DERIVED**), and `supersededAt` is **PRIMARY** — carried by the event's envelope `ts`, which the live fold must pass (§2.6).*

#### 2.5 Epistemic — `Point` · operators/edges (93 names · 68 P · 17 D · 8 U)

`Point` (74: 55 P / 13 D / 6 U) · edges/operators (19: 13 P / 4 D / 2 U).

*This is where the EP scalars live: `ep_alpha`, `ep_beta`, `ep_dirty`, `ep_dirty_at`, `posterior_alpha`, `posterior_beta`, `confidence`, `c_cal`, `lastDreamedAt`, and the four BP `msg_*`/`back_msg_*` slots — all DERIVED per the carve-out. `content_hash` is DERIVED (a pure function of `content`; deliberately stripped from the journal). The 8 UNRESOLVED are resolved in §2.7.*

#### 2.6 Procedural — the fold over the Episodic layer (0 own names)

**The procedural layer owns no property name.** Per `ONTOLOGY.md` §2 it is *“Event + folded `Object.status`”*: `Object.status`, `supersededBy` and `supersededAt` are the **fold targets of lifecycle events**, and they live on the **semantic `Object` row** — counted there in §2.4 (20 P · 3 D). They are **not one class**: the deciding test splits them.

- **`supersededBy` — PRIMARY.** The journal carries it verbatim (`ObjectSuperseded.supersedes_by`) and the fold copies it through unchanged (`str(ev.get("supersedes_by") or "")`) → §2.1 rule 1, a record column.
- **`status` — DERIVED (a cache).** It is journal-carried too (`ObjectRegistered.status`), but the ontology names it a cache in three places: §2 *“Object.status is a write-through cache of lifecycle events … a performance cache, folded keep-first per Object”*; the §2 state note *“status is a fold cache over the events — never the truth itself”*; §4.3 *“the event stream is the reconstruction source for Object.status (§11 cache doctrine)”*. The fold's own docstring says the same — *“Projection-owned cache of the event stream”* (`_fold_object_superseded`, `projection/entities.py`). A recorded carve-out naming a field a **cache** is exactly what §2.2's `confidence` rule handles: the journal value is not the record.
- **`supersededAt` — PRIMARY (the event envelope `ts` carries it).** The record carries the instant: `_emit_event("ObjectSuperseded", …)` stamps the JSONL envelope `ts` (`sdk.py`) and rebuild replays the raw journaled event, so `_journal_instant(ev)` restores the recorded instant. The **live** fold is the defect — it builds its event with no instant (`fold_ev = {"name": …, "supersedes_by": …}` in `commit_ops.py`) and so falls back to `_now_iso()`, minting a *different* read than the envelope it just emitted. The helper's own docstring states the divergence — *“a live caller that folds an event dict it built itself (`commit_ops`'s `fold_ev` for `ObjectSuperseded`) passes no `ts`, so the live write, the record's envelope `ts` and this helper each hold a different read.”* The fix is a **live-write fix, not a class change**: `commit_ops` must mint the instant once (the `recorded_ts` seam on `_emit_event`, #5048) and fold that same value, so live and replay agree.

**The rule.** A journal-carried value is PRIMARY unless **(a)** a recorded carve-out names it recomputed-from-structure or a **cache**, or **(b)** the journal carries no *reconstructible* value for it and it cannot be recomputed (class 4, not class 1). A divergence where the live writer **ignores a carrier the record already holds** is a live-write defect, not a class change. `confidence` is the carve-out; `Object.status` is the cache; `supersededAt` is the live-write defect case; `supersededBy` is the plain PRIMARY case; `updatedAt` and `temporal_reviewed` are the class-4 cases (non-carried and **not** recomputable — a recomputable non-carried value is class 2 DERIVED, never `P*`).

The work-state counters a session carries (`turn_count`, `commit_count`, …) live on the **`Session` row** — counted in §2.3. **Reconciliation:** a future schema must not look for a procedural table of its own; the procedural layer is a *view/read*, and its fields are classified where they physically live.

### 2.7 The 8 UNRESOLVED rows — resolved, each with the check that confirms it

The prior pass left 8 names unresolved because their polarity was contestable. Applying §2.1's four classes decides each. **All 8 are resolved by decision; where the decision itself depends on an empirical reading (a live-vs-rebuild diff), that reading is named as the confirmation the schema unit must run before relying on it.**

| # | property | layer | was | **resolved** | why (the test) | confirmation |
|---|---|---|---|---|---|---|
| 1 | `updatedAt` | epistemic (`Point`) | U | **P\*** (journal the write-time) | minted inside the fold (`_upsert_point_props` `n.updatedAt=$now`, `projection/entities.py`), so replay re-mints → `live ≠ replay`. It is a mutable fact the record must keep, not a recompute and not a stamp the record may lose → §2.1 class 4 | live `update_point` → `rebuild_all` → `DIFF n.updatedAt`; if it differs, add it to the `PointRevised`/`PointAdded` payload |
| 2 | `version` | epistemic (`Point`) | U | **DERIVED** | an ordinal counted by the fold over `PointRevised` — replay reconstructs it **iff** the journal is complete; it is `coalesce(n.version,0)+1`, i.e. a fold counter, not an independent fact | same live-vs-rebuild `DIFF n.version`; a divergence means a `PointRevised` is missing from the journal (`#5089`), not that `version` is a record column |
| 3 | `inherited_at` | epistemic (`Point`) | U | **VOLATILE** | it stamps **when** an inherited baseline was last recomputed (`_touch_inherited_at` `SET n.inherited_at = $ts`, `sdk.py`); like `reliability_derived_at` it stamps a derived recomputation — the value need not survive replay because the recomputation re-stamps it | rebuild → assert the recomputed inherited baselines carry a fresh `inherited_at` (the gate is re-applied, not replayed) |
| 4 | `baseline_source` | epistemic (`Point`) | U | **PRIMARY when `='set-by-author'`; DERIVED otherwise** | §2.8 — the author token records a non-reconstructible input | group by token → rebuild → count; any `set-by-author` row that disappears is the lost input |
| 5 | `baseline_set` | epistemic (`Point`) | U | **DERIVED** | once the baseline is journaled (§2.8's fix) it is a mirror of "a baseline exists" and carries no independent information. ⚠️ It is **not** derivable today: token-less legacy `baseline_set=true` rows predate the `#2199` token family, and `ep.py::_write_node` writes `ep_alpha` for **every** point (`CASE WHEN coalesce(n.baseline_set,false) THEN n.ep_alpha ELSE $a END`), so `ep_alpha IS NOT NULL` over-counts | falls out of #4's check |
| 6 | `temporal_reviewed` | epistemic (`Point`) | U | **P\*** (journal it) | a human review decision (`approve_merge` `SET n.temporal_reviewed = $a`, `sdk.py`), a *decision output* with no dedicated event found → §2.1 class 4; `reviewed` rides `PointPromoted`/`DedupeRecorded`, `temporal_reviewed` does not | grep the journal for a carrier after a live mining review; absent ⇒ add it to the mining event (mirror `DedupeRecorded`) |
| 7 | `IMPL.confidence` | epistemic (edges) | U | **PRIMARY** | carried verbatim by `DirectEdgeCreated` (`create_direct_edge` → `attrs["confidence"]`, `sdk.py`) — the caller's recorded edge attribute, §2.1 class 1 | already journaled; confirm the replay writes the same value (a naive `DirectEdgeCreated` replay test) |
| 8 | `IMPL.weight` | epistemic (edges) | U | **PRIMARY** | carried verbatim by `DirectEdgeCreated` (`create_direct_edge` → `attrs["weight"]`, `sdk.py`) | as #7 |

**The `IMPL.confidence` / `weight` tension is resolved by *home*, not by polarity:** the *edge attribute* is the caller's input (PRIMARY, on the edge); *EP confidence* is the propagated belief (DERIVED, on the operator). They are different fields with the same word; the schema must not collapse them.

**Final counts:** **216 names → 187 PRIMARY · 29 DERIVED/VOLATILE · 0 UNRESOLVED.** Arithmetic: the 2026-10-10 pass recorded **185 / 23 / 8**, and **three measured classes are corrected in review — `Object.status` is a projection-owned cache (§2.6), and `Object.embedding`/`Subject.embedding` are recomputed from the carried `name` (§2.4), so they move P→D: 185 − 3 = 182 P · 23 + 3 = 26 D/V · 8 U.** The eight resolutions then move **5 to PRIMARY** (`updatedAt` (P\*), `baseline_source`, `temporal_reviewed` (P\*), `IMPL.confidence`, `IMPL.weight`) and **3 to DERIVED/VOLATILE** (`version`, `inherited_at`, `baseline_set`): **182 + 5 = 187 P**, **26 + 3 = 29 D/V**, **8 − 8 = 0 U**.

### 2.8 The `baseline_*` correction to §17.5 (as delivered)

> **⛔ CORRECTION — §17.5 lists `baseline_set` / `baseline_source` together as unconditionally DERIVED/VOLATILE. `baseline_source` is not: when an author sets the prior (`baseline_source = 'set-by-author'`, the `set_point_baseline` default), the value is a NON-RECONSTRUCTIBLE human/agent input, and §3's own per-write test calls that PRIMARY. `baseline_set` stays **unconditionally DERIVED**, but **not because it is derivable today** — it is a mirror of baseline existence only **once the baseline is journaled** (the fix below). Both candidate derivations are false: token-less legacy `baseline_set=true` rows predate the `#2199` token family, and `ep.py::_write_node` writes `ep_alpha` for every point (`CASE … ELSE $a`), so `ep_alpha IS NOT NULL` over-counts.**

- **The token family** (`sdk.py`, `#2199`): `set-by-author` — *"NEVER recomputed by `_apply_source_inheritance`"*; `inherited-from-source` — recomputed per EP run; `system-default` — a deterministic default. **Only the first is an input; the other two are recomputable.**
- **Today the value is LOST on rebuild**, and the code says so itself: `set_point_baseline` (`sdk.py`) — *“the SAME statement also writes `ep_alpha`, `ep_beta`, `baseline_set` and `baseline_source`, and NO journaled event carries those four — they are in `_POINT_DENY` … An author-set baseline is therefore LOST on rebuild (and `_hydrate_evidence` then finds no prior, refusing the next EP run).”*
- **Placement:** `baseline_source` → **PRIMARY when `set-by-author`** (the record must keep the token and the α/β it names); `baseline_set` → **DERIVED** (a mirror once the baseline is journaled). The fix is to **journal the author-set baseline as an explicit input** (the §3 E3 pre-write); once the input is carried, `ep_alpha`/`ep_beta` stay DERIVED as the carve-out says.
- **Not a reopened decision.** The carve-out is for values *"recomputed from the structure"*; an author-stated prior is not. This applies §2.0, it does not override it.

This correction is also recorded on the graph (`CORR-17.5-baseline-author-set-is-primary-7955`) and carried into `STORAGE-ARCHITECTURE.md` §17.5.

### 2.9 Consequential findings the enumeration surfaces

These are **findings, not this unit's work**; each is a §3 invariant violation that the table makes visible, and each belongs to the journal-completeness root (`#4240` / `#5089`):

1. **`confidence` is not actually protected.** It is persisted from the payload by the fixed clause `n.confidence=coalesce($cf, n.confidence)` (`_upsert_point_props`, `projection/entities.py`) and sits in `_POINT_HANDLED`, **not `_POINT_DENY`** — so the deny-list does not guard a field `ONTOLOGY.md` §2 calls *“derived at read time … never stored independently”*. **Doc-vs-code disagreement, now visible.**
2. **Unjournalled raw `SET`s** (each a live≠rebuild divergence): `Point.ep_dirty`/`ep_dirty_at` and `inherited_at`; `Session.commit_count`/`draft_count`/`value_nodes_created`/`value_nodes_held`/`updated_at` (`_execute_commit_writes` / `_commit_region`, `hosted_api.py`); `Object.routed_team`/`routed_role`/`routed_product`/`complexity`/`ux_rating` (`ingest`, `connectors/github.py`); `Event.embeddingRepairFailedAt` (`_embedding_repair_attempt`, `sdk.py`).
3. **Point `updatedAt` live≠replay** (§2.7 #1) — same defect class `#5024` fixed for `Source`.
4. **`Source.reliability*`** is the **correct** CQRS pattern (declared, recomputed, never authoritative) — the template the volatile set should converge to.

**The reusable artifact:** the journal-carrier map (`_emit_event`/`_emit` call sites → payload keys) is what lets any future property be classified mechanically — *a key that appears in no journal payload is a PRIMARY-without-carrier candidate.* This document is the enumeration; that map is how it stays current.

---

## Appendix — the raw table, with every writer and carrier

The full per-property table (216 rows, with the writer clause and the journal carrier for each) is reproduced below from the measurement pass (recorded durably in `#7955` comment `6097993997`; local copy `/tmp/w2-placement.md`), grouped by layer. The pass recorded **185 P · 23 D/V · 8 U**; **three classes are corrected in review — `Object.status` is a projection-owned cache (`ONTOLOGY.md` §2/§4.3), and `Object.embedding`/`Subject.embedding` are recomputed from the carried `name` (§2.1 rule 2), so they move P→D → 182 P · 26 D/V · 8 U** (see the `status` and `embedding` rows, §2.4 and §2.6).
> ⚠️ **The classes below are the MEASURED (pre-resolution) classes** from the 2026-10-10 pass, and the writer column is cited as **`file → clause`** — never `file:line`, which drifts (see the header). The 8 `U` rows are resolved in §2.7 — read the resolution there, not the `U` here. Numbers are a **floor** (the open-set `SET n += $props` passthrough is unbounded).

### Episodic — `Event`

## Event (node) — 30 properties (PRIMARY 29 · DERIVED/VOLATILE 1 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py props['id']=eid; hosted_api.py SET e.id | EventRecorded.id | dual of eventId (#122) |
| `eventId` | P | projection/entities.py MERGE (e:Event {eventId:$eid}) | EventRecorded via id= kwarg | MERGE key |
| `eventKind` | P | projection/entities.py e.eventKind / $props | EventRecorded.eventKind |  |
| `subject` | P | projection/entities.py | EventRecorded.subject |  |
| `object` | P | projection/entities.py | EventRecorded.object |  |
| `startedAt` | P | projection/entities.py | EventRecorded.startedAt |  |
| `endedAt` | P | projection/entities.py | EventRecorded.endedAt |  |
| `parentEvent` | P | projection/entities.py (via $props) | `_EVENT_HANDLED`; no EventRecorded kwarg — open-set |  |
| `participants` | P | projection/entities.py | EventRecorded.participants |  |
| `classificationLevel` | P | projection/entities.py | EventRecorded.classificationLevel |  |
| `format` | P | projection/entities.py | EventRecorded.format |  |
| `embedding` | P | projection/entities.py e.embedding=CASE..vecf32 | EventRecorded payload via inner.embedding | stored; computed from subject/kind/object if absent |
| `eventStatus` | P | hosted_api.py; entities.py `_event_plain_merge` open-set | open-set passthrough ($props) | lifecycle |
| `name` | P | hosted_api.py | open-set passthrough |  |
| `summary` | P | hosted_api.py | open-set passthrough |  |
| `story_arc` | P | hosted_api.py | open-set passthrough |  |
| `keywords` | P | hosted_api.py | open-set passthrough |  |
| `capturedAt` | P | hosted_api.py | open-set passthrough |  |
| `sessionId` | P | hosted_api.py | open-set passthrough |  |
| `is_episodic` | P | hosted_api.py | open-set passthrough |  |
| `is_operator` | P | ranking.py | open-set passthrough | DEFENSIVE — a non-Point node carrying a Point flag |
| `session_id` | P | sdk.py | open-set passthrough |  |
| `source_file` | P | projection/entities.py; meeting guard | guard comparison prop (meeting branch) | §4.2 |
| `source_ref` | P | hosted_api.py | open-set passthrough |  |
| `updatedAt` | P | hosted_api.py | open-set passthrough |  |
| `mined_at` | P | mining.py | mining write (check carrier) |  |
| `mined_hash` | P | mining.py | mining write (check carrier) |  |
| `embeddingRepairFailedAt` | P | sdk.py SET e.embeddingRepairFailedAt | repair path raw write (check carrier) | ⚠ raw repair write |
| `confidence` | D | hosted_api.py | — | EP scalar on Event (should be derived) |
| `content` | P | hosted_api.py | — | event content |

### Episodic — `Source`

## Source (node) — 49 properties (PRIMARY 44 · DERIVED/VOLATILE 5 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py s.id=coalesce($id,$url); hosted_api.py | SourceCreated payload **ev (create_source) | identity |
| `url` | P | projection/entities.py MERGE (s:Source {url:$url}); edges.py | SourceCreated ev.url | MERGE key |
| `canonicalUrl` | P | projection/entities.py s.canonicalUrl=$cu; edges.py | SourceCreated ev (S0a/S0b #5012) | identity |
| `urlAliases` | P | projection/entities.py s.urlAliases=[$raw_url] | SourceCreated ev | identity aliases |
| `sourceKind` | P | projection/entities.py s.sourceKind=$sk | SourceCreated ev.sourceKind |  |
| `contentHash` | P | projection/entities.py hash-diff; sdk.py | SourceCreated/SourceVersioned.contentHash | version anchor |
| `title` | P | projection/entities.py | SourceCreated ev.title |  |
| `ingestedAt` | P | projection/entities.py s.ingestedAt=coalesce($ingestedAt,$now) | SourceCreated ev.ingestedAt (#5024) | recorded instant, not replay clock |
| `version` | P | projection/entities.py s.version=1 (and the version bump); sdk.py | SourceCreated/SourceVersioned (reproduced by the hash gate) | ordinal reproduced by the SAME hash-diff gate |
| `externalId` | P | projection/entities.py | SourceCreated ev.externalId |  |
| `format` | P | projection/entities.py | SourceCreated ev.format | D10: moved onto :Source |
| `sourcePath` | P | projection/entities.py s.sourcePath=coalesce($sp,..) | SourceCreated ev.source_path | #4.1 |
| `rawState` | P | projection/entities.py s.rawState=$rawState | SourceCreated ev.rawState (#3998 D30) | availability, third value on the record |
| `rawStateAt` | P | projection/entities.py | SourceCreated ev.rawStateAt |  |
| `_searchText` | P | projection/entities.py s.`_searchText`=$st | SourceCreated ev.`_searchText` | fulltext index text |
| `updatedAt` | P | projection/entities.py coalesce($updatedAt,$now); sdk.py ev[updatedAt] | SourceCreated/SourceVersioned ev.updatedAt (#5024) | ONE clock minted by the caller |
| `sourceDate` | P | `_SOURCE_EXTRA_PROPS`; create_source(sourceDate=) | SourceCreated ev.sourceDate | evidence-age clock |
| `credibilityTier` | P | `_SOURCE_EXTRA_PROPS`; projection/entities.py | SourceCreated ev.credibilityTier | #388 |
| `is_episodic` | P | `_SOURCE_EXTRA_PROPS`; projection/entities.py | SourceCreated ev.is_episodic | quota discriminator |
| `name` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.name |  |
| `team` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.team |  |
| `provenance_spans` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.provenance_spans |  |
| `domain` | P | `_SOURCE_EXTRA_PROPS` (D10 B6) | SourceCreated ev.domain |  |
| `summary` | P | `_SOURCE_EXTRA_PROPS`; hosted_api.py | SourceCreated ev.summary |  |
| `topics` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.topics |  |
| `status` | P | `_SOURCE_EXTRA_PROPS` (D10 B6) | SourceCreated ev.status |  |
| `embedding` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.embedding | vector retrieval leg |
| `about_entities` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.about_entities |  |
| `documentKind` | P | `_SOURCE_EXTRA_PROPS`; hosted_api.py | SourceCreated ev.documentKind | D10 document-is-a-Source |
| `document_kind` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.document_kind |  |
| `sessionId` | P | `_SOURCE_EXTRA_PROPS`; `_SOURCE_NODE_PROPS` | SourceCreated ev.sessionId | capture writer |
| `capturedAt` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.capturedAt | session-capture writer |
| `eventId` | P | `_SOURCE_NODE_PROPS`; hosted_api.py | SourceCreated ev.eventId |  |
| `session_id` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.session_id |  |
| `event_id` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.event_id |  |
| `source_path` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.source_path |  |
| `needs_extraction` | P | `_SOURCE_NODE_PROPS`; ingest.py | SourceCreated ev.needs_extraction | read back by ingest |
| `story_arc` | P | `_SOURCE_NODE_PROPS`; hosted_api.py | SourceCreated ev.story_arc |  |
| `document_knowledge_domain` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.document_knowledge_domain |  |
| `authored_by` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.authored_by | document provenance metadata |
| `owned_by` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.owned_by |  |
| `managed_by` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.managed_by |  |
| `governing_agreement` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.governing_agreement |  |
| `createdAt` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.createdAt |  |
| `__runId` | D | projection/entities.py s.__runId=$rid transient merge token | NEVER journalled (stripped, sdk.py excludes __runId) | transient merge token, removed best-effort — not a record field |
| `reliability` | D | sdk.py `_write_reliability_cache` | NOT carried | documented CQRS cache (#398) — recomputed |
| `reliabilityComponents` | D | sdk.py | NOT carried | cache |
| `reliability_derived_at` | D | sdk.py | NOT carried | cache timestamp |
| `previousContentHash` | D | sdk.py SourceVersioned kwarg | SourceVersioned.previousContentHash (event key, NOT a node prop) | `_SOURCE_HANDLED` excludes it — describes the transition, not the node |

### Episodic — `Session`

## Session (node) — 21 properties (PRIMARY 21 · DERIVED/VOLATILE 0 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | sdk.py MERGE (s:Session {id:$sid}); projection/entities.py | SessionRecorded.id | MERGE key |
| `created_at` | P | sdk.py s.created_at=coalesce(s.created_at,$now); projection/entities.py | SessionRecorded.created_at | first-writer-wins |
| `turn_count` | P | sdk.py s.turn_count=$tc; projection/entities.py | SessionRecorded.turn_count | latest capture wins |
| `is_episodic` | P | sdk.py s.is_episodic=true; projection/entities.py | SessionRecorded fold hardcodes true | session container flag (ONTOLOGY §4.5) |
| `harness` | P | sdk.py s.harness=$harness; projection/entities.py | SessionRecorded.harness | #1727 first-writer-wins |
| `capture_lane` | P | sdk.py s.capture_lane=coalesce(..); projection/entities.py | SessionRecorded.capture_lane (#3516) | producer lane |
| `client_captured_at` | P | sdk.py s.client_captured_at=coalesce(..); projection/entities.py | SessionRecorded.client_captured_at | #3516 client clock |
| `client_captured_at_source` | P | sdk.py CASE; projection/entities.py | SessionRecorded.client_captured_at_source | clock identity pair |
| `actor_user_id` | P | sdk.py s.actor_user_id=coalesce(..); projection/entities.py | SessionRecorded.actor_user_id (#2600) |  |
| `machine_id` | P | sdk.py s.machine_id=coalesce(..) | SessionRecorded.machine_id (hosted-only, #2599) |  |
| `model` | P | sdk.py s.model=coalesce(..) | SessionRecorded.model |  |
| `capture_redactions` | P | sdk.py SET s.capture_redactions; projection/entities.py | SessionRecorded.capture_redactions (#4911) | overwrite semantics |
| `entity_links_attempted` | P | sdk.py SET; projection/entities.py | SessionRecorded.entity_links_attempted (#3664) |  |
| `entity_links_created` | P | sdk.py; projection/entities.py | SessionRecorded.entity_links_created |  |
| `capture_ok` | P | hosted_api.py; projection/entities.py | SessionRecorded.capture_ok (#3722) | retry gate input |
| `capture_extractor` | P | hosted_api.py; projection/entities.py | SessionRecorded.capture_extractor |  |
| `commit_count` | P | hosted_api.py SET s.commit_count=coalesce+1 | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled → lost on rebuild |
| `draft_count` | P | hosted_api.py | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled |
| `value_nodes_created` | P | hosted_api.py | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled |
| `value_nodes_held` | P | hosted_api.py | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled |
| `updated_at` | P | hosted_api.py SET s.updated_at=$now | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled |

### Semantic — `Object`

## Object (node) — 18 properties (PRIMARY 16 · DERIVED/VOLATILE 2 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py ON CREATE o.id=$id; commit_ops.py | ObjectRegistered.id | identity |
| `name` | P | projection/entities.py MERGE (o:Object {name:$name}) | ObjectRegistered.name | MERGE key; #1155 |
| `objectKind` | P | projection/entities.py o.objectKind=coalesce($ok,'other') | ObjectRegistered.object_kind |  |
| `createdAt` | P | projection/entities.py o.createdAt=coalesce($ca,$now) | ObjectRegistered.createdAt | #2295/#2194 |
| `title` | P | projection/entities.py o.title=coalesce($title,'') | ObjectRegistered (open-set snapshot) |  |
| `embedding` | D | projection/entities.py o.embedding=CASE..vecf32; #4524 REMOVE | recomputed from `name` — `_upsert_object` calls `encode_for_store(name)` and never reads `ev.get("embedding")` (#7845) | **DERIVED** (§2.1 rule 2) — not a payload key on `ObjectRegistered`; recomputable from the carried `name`, same shape as `Point.content_hash` |
| `status` | D | projection/entities.py o.status=coalesce($st,'live'); `_fold_object_superseded` | ObjectRegistered.status; ObjectSuperseded (folded) | **projection-owned CACHE** (`_fold_object_superseded` docstring: *"Projection-owned cache of the event stream"*; `ONTOLOGY.md` §2/§4.3) — the measurement recorded P, corrected to D in review (§2.6) |
| `supersededBy` | P | projection/entities.py SET o.supersededBy=$sb | ObjectSuperseded.supersedes_by | #1350 — carried verbatim; the fold copies it through unchanged |
| `supersededAt` | P | projection/entities.py SET o.supersededAt=$sa; live `fold_ev` carries no `ts` (`commit_ops.py`) | ObjectSuperseded envelope `ts` | **PRIMARY** — the envelope `ts` is the record; the live `fold_ev` carries no `ts`, so it mints `_journal_instant(ev)` → `_now_iso()` ≠ the emitted envelope `ts` — a **live-write fix, not a class change** (§2.6) |
| `canonical_name` | P | sdk.py (read/alias match) — writer via open-set | open-set passthrough | topic alias |
| `repo` | P | sdk.py MERGE (o:Object) SET o.repo=$repo | connector write (check journal carrier) | github entity field |
| `issue_number` | P | sdk.py SET o.issue_number=$issue_number | connector write (check) | github entity field |
| `url` | P | sdk.py SET o.url=$url | connector write (check) | github entity field |
| `routed_team` | P | connectors/github.py SET o.routed_team | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled (#4240 class) |
| `routed_role` | P | connectors/github.py | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled |
| `routed_product` | P | connectors/github.py | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled |
| `complexity` | P | connectors/github.py | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled |
| `ux_rating` | P | connectors/github.py | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled |

### Semantic — `Subject`

## Subject (node) — 5 properties (PRIMARY 4 · DERIVED/VOLATILE 1 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py s.id=$id / coalesce; edges.py stub | SubjectAdded.id | #1918 canonical id wins |
| `name` | P | projection/entities.py MERGE (s:Subject {name:$name}) | SubjectAdded.name | MERGE key |
| `subjectKind` | P | projection/entities.py s.subjectKind=coalesce($sk,'other') | SubjectAdded.subject_kind |  |
| `createdAt` | P | projection/entities.py s.createdAt=coalesce($ca,$now) | SubjectAdded.createdAt | #2295 |
| `embedding` | D | projection/entities.py s.embedding=CASE..vecf32; #4524 REMOVE | recomputed from `name` — `_upsert_subject` calls `encode_for_store(name)` and never reads `ev.get("embedding")` (#7845) | **DERIVED** (§2.1 rule 2) — recomputable from the carried `name` |

### Epistemic — `Point`

## Point (node) — 74 properties (PRIMARY 55 · DERIVED/VOLATILE 13 · UNRESOLVED 6)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py MERGE (n:Point {id:$id}); sdk.py create_point CREATE map | PointAdded.point.id / PointPromoted.point.id / OperatorAdded.point.id | identity / MERGE key |
| `content` | P | projection/entities.py n.content=$content | PointAdded.point.content (full snapshot) | §3: JOURNAL — decision output |
| `is_operator` | P | projection/entities.py n.is_operator=$isop | PointAdded.point.is_operator | identity flag; #5048 |
| `op_type` | P | projection/entities.py n.op_type=$opt | OperatorAdded.point.op_type | operator kind |
| `pointKind` | P | projection/entities.py n.pointKind=coalesce($pk,..) | PointAdded.point.pointKind | kind |
| `status` | P | projection/entities.py n.status=coalesce($st,..,'live'); sdk.py | PointAdded.point.status, PointRevised, PointPromoted, PointRetracted | lifecycle, journaled by dedicated events |
| `authoredBy` | P | projection/entities.py n.authoredBy=coalesce($ab,..) | PointAdded.point.authoredBy (`_META_KEY` handled as edge) |  |
| `confidence` | D | projection/entities.py n.confidence=coalesce($cf,..); ep.py; dream.py | ConfidenceChanged {confidence}; ALSO written from PointAdded payload | §17.5 + §3: EP scalar 'never stored independently'. ⚠ TENSION: the fixed clause DOES persist a payload value (coalesce), so the code stores what the doc calls derived |
| `createdAt` | P | projection/entities.py n.createdAt=coalesce($ca,..,$now) | PointAdded.point.createdAt |  |
| `validFrom` | P | projection/entities.py | PointAdded.point.validFrom | #4021 — `PointSuperseded.valid_from` is the SUCCESSOR's window start (stored as the predecessor's `validTo`), never the predecessor's `validFrom`; it is covered by the `validTo` row |
| `validTo` | P | projection/entities.py; sdk.py | PointSuperseded.valid_to; PointInvalidated.valid_to |  |
| `updatedAt` | U | projection/entities.py n.updatedAt=$now | NOT carried — replay re-mints via `_now_iso`() | replay clock, not recorded instant → live≠replay; journal it or accept volatile |
| `embedding` | P | `_upsert_point_props` (`n.embedding = CASE WHEN $embedding_clear … ELSE vecf32($embedding) END`; `REMOVE n.embedding` rides the same query, `projection/entities.py`); `stamp_journal_embedding` (`sdk.py`) | PointAdded.point.embedding + embedding_model/revision/text_hash (#5004) | §3: JOURNAL IT — re-embed is a re-run not a replay |
| `embedding_verbatim` | P | projection/entities.py n.embedding_verbatim | PointAdded.point.embedding_verbatim | caller-owned vector storage form |
| `content_hash` | D | projection/entities.py n.content_hash=coalesce($ch,..) via `_content_hash`() | STRIPPED from journal: sdk.py `if k != 'content_hash'` | §3: RECOMPUTE — pure function of content (#2795 D2) |
| `direction` | P | projection/entities.py (op branch) n.direction=$dir | OperatorAdded.point.direction | operator edge direction |
| `label` | P | projection/entities.py n.label=$label | OperatorAdded.point.label | operator domain verb |
| `extractedFrom` | P | projection/entities.py SET n.extractedFrom=$ref | PointAdded.point.extractedFrom (edge authoritative) | query-convenience scalar; edges authoritative |
| `provenanceSource` | P | projection/entities.py SET n.provenanceSource=$sid | PointAdded.point.provenance.source_id | P1-2 cached provenance id |
| `is_episodic` | P | projection/entities.py n.is_episodic=$episodic | PointAdded.point.is_episodic | server-managed quota discriminator (#1486) |
| `sourceVersionTransit` | P | projection/entities.py n.sourceVersionTransit=$sv | PointAdded.point.sourceVersionTransit (#5256) | replay carrier for extractedFrom version |
| `mitigation_strength` | P | `sdk.py` `CREATE (m:Point {…, mitigation_strength:$s})`; `_revise_point` (`n.mitigation_strength = $mitigation_strength`, `projection/__init__.py`) | OperatorAdded.point.mitigation_strength (open-set) | #2315 graded dampener; edge read |
| `batch_id` | P | `sdk.py` (`SET n.batch_id = $bid`); `rebuild_all` (`SET p.batch_id = $bid`, pass-1b tail, `projection/__init__.py`); `mining.py` | BatchIdStamped (JSONL-only record) | #902 A4; restored by pass-2b |
| `version` | U | `update_point` (`n.version = coalesce(n.version, 0) + 1`, `sdk.py`) | not carried on PointRevised | counter; replay increments from a different base → divergence candidate |
| `promotedAt` | P | sdk.py SET n.promotedAt=$now | PointPromoted.point.promotedAt (full snapshot) | #785 reviewer-gated promotion |
| `reviewed` | P | `promote_point` (`SET n.status = 'live', n.reviewed = true, …`, `sdk.py`); `approve_merge` (`SET n.dedup_reviewed = $a, n.reviewed = true`, `sdk.py`) | PointPromoted.point.reviewed; DedupeRecorded | DERIVED reviewer flag per code comment, but rides the journaled snapshot → in record |
| `outdated` | P | sdk.py SET n.outdated=true; projection/entities.py | PointInvalidated {expired_at,valid_to}; PointSuperseded | lifecycle terminal flag; deny-listed from PointAdded passthrough |
| `expiredAt` | P | `invalidate_point` (`n.expiredAt = $now`, `sdk.py`); `projection/entities.py` | PointInvalidated.expired_at; PointSuperseded.expired_at |  |
| `search_keys` | P | `_fix_point_search_keys` (`SET n.search_keys = $flat`, `projection/__init__.py`) | PointAdded.point.search_keys (declared list prop) | declared payload prop; the FLATTENED value is a pure function |
| `speaker` | P | sdk.py; `_POINT_DECLARED_PROPS` | PointAdded.point.speaker | declared payload prop |
| `quote` | P | `_POINT_DECLARED_PROPS` | PointAdded.point.quote | declared payload prop |
| `when` | P | `_POINT_DECLARED_PROPS` | PointAdded.point.when | declared payload prop |
| `source_turn_id` | P | `_POINT_DECLARED_PROPS` | PointAdded.point.source_turn_id | declared payload prop |
| `tags` | P | sdk.py create_point `SET n += $props` (raw list); `_POINT_LIST_PROPS` empty | PointAdded.point.tags | #2897 — raw-list half-restore refused on replay (parity gap) |
| `span_start` | P | `_POINT_DECLARED_PROPS` (#5007 E4) | PointAdded.point.span_start | verbatim span pointer |
| `span_end` | P | `_POINT_DECLARED_PROPS` (#5007 E4) | PointAdded.point.span_end | verbatim span pointer |
| `annotator_bias` | P | `_POINT_DECLARED_PROPS` (#3689); OperatorAnnotated.bias | PointAdded.point.annotator_bias; OperatorAnnotated |  |
| `annotator_precision` | P | `_POINT_DECLARED_PROPS` (#3689) | PointAdded.point.annotator_precision; OperatorAnnotated.precision |  |
| `annotator_consistency` | P | `_POINT_DECLARED_PROPS` (#3689) | PointAdded.point.annotator_consistency; OperatorAnnotated.consistency |  |
| `annotator_directness` | P | `_POINT_DECLARED_PROPS` (#3689) | PointAdded.point.annotator_directness; OperatorAnnotated.directness |  |
| `goalState` | P | `_POINT_DECLARED_PROPS` (#7856) | PointAdded.point.goalState | closed vocabulary, separate from status |
| `source_harness` | P | hosted_api.py | open-set passthrough on PointAdded snapshot | capture provenance prop |
| `source_session` | P | hosted_api.py | open-set passthrough | capture provenance prop |
| `ingested_at` | P | hosted_api.py | open-set passthrough |  |
| `eventId` | P | hosted_api.py | open-set passthrough | link to Event |
| `sourceDate` | P | audit.py | open-set passthrough | evidence-age clock |
| `assessor` | P | sdk.py | open-set passthrough |  |
| `targetSource` | P | sdk.py | open-set passthrough |  |
| `grounding` | P | projection/grounding.py SET n.grounding=$g | open-set passthrough |  |
| `lme_question_id` | P | session_reinjection.py | open-set passthrough | eval-lane tag |
| `dedup_candidate` | P | sdk.py | DedupeRecorded.point | #784 |
| `dedup_method` | P | sdk.py | DedupeRecorded.point | #784 |
| `dedup_similarity` | P | sdk.py | DedupeRecorded.point | #784 |
| `dedup_target_id` | P | sdk.py | DedupeRecorded.point | #784 |
| `dedup_reviewed` | P | sdk.py | open-set / DedupeRecorded |  |
| `temporal_candidate` | P | mining.py | mining record (check)  | temporal-mining family |
| `temporal_target_ids` | P | mining.py | mining record (check) |  |
| `temporal_target_id` | P | mining.py | mining record (check) |  |
| `temporal_replacement` | P | mining.py | mining record (check) |  |
| `temporal_reviewed` | U | sdk.py SET n.temporal_reviewed, n.reviewed=true | no dedicated event found | confirm whether a journal carrier exists |
| `lastDreamedAt` | D | dream.py SET n.lastDreamedAt=$now | ConfidenceChanged via `_journal_belief_writeback` (lastDreamedAt) | EP-owned recompute stamp; deny-listed |
| `ep_alpha` | D | ep.py SET n.ep_alpha; sdk.py | NOT carried (fold drops; `_POINT_DENY`) | §17.5 |
| `ep_beta` | D | ep.py SET n.ep_beta; sdk.py | NOT carried | §17.5 |
| `baseline_set` | U | sdk.py SET n.baseline_set=true | NOT carried — sdk.py states the 4 baseline props are in `_POINT_DENY` and lost on rebuild | set-by-author prior is a non-reconstructible INPUT (§3 test ⇒ PRIMARY), inherited/system-default are recomputable (⇒ DERIVED). §17.5's blanket 'derived' is wrong for the first |
| `baseline_source` | U | sdk.py SET n.baseline_source=$src | NOT carried | same as baseline_set — separates set-by-author from inherited/system-default |
| `posterior_alpha` | D | ep.py SET n.posterior_alpha; sdk.py | ConfidenceChanged.posterior_alpha | §17.5; recomputed posterior |
| `posterior_beta` | D | ep.py SET n.posterior_beta; sdk.py | ConfidenceChanged.posterior_beta | §17.5; recomputed posterior |
| `ep_dirty` | D | sdk.py SET n.ep_dirty=null; `_mark_dirty` | NOT journalled (#5166, stated sdk.py) | §17.5; volatile dirty flag |
| `ep_dirty_at` | D | sdk.py SET n.ep_dirty_at=null | NOT journalled (#5166) | §17.5; volatile dirty timestamp |
| `inherited_at` | U | sdk.py SET n.inherited_at=$ts | NOT carried | per-run time gate for inherited baselines — an input to a decision, not a pure recompute; decide journal-vs-derive |
| `c_cal` | D | `_POINT_DENY` (calibration cache; written by calibration path) | deny-listed from passthrough | EP calibration cache |
| `_nid` | D | `_POINT_DENY` / projection replay bookkeeping | never a payload prop | replay bookkeeping, not a record field |
| `_graph_id` | D | `_POINT_DENY` / projection replay bookkeeping | never a payload prop | replay bookkeeping |
| `reason` | D | `_POINT_DENY` (D4) | deny-listed | dropped by design |

### Epistemic — operators / edges

## Edges / operators (IMPL/NAND/INPUT/references/extractedFrom/mitigated_by/about*) — 19 properties (PRIMARY 13 · DERIVED/VOLATILE 4 · UNRESOLVED 2)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `IMPL.idx` | P | `create_operator` (`CREATE (o)-[:IMPL {idx:$i}]->(s)`, `sdk.py`); `_create_edges` (`projection/edges.py`) | OperatorAdded (edge re-created by fold from inputs+idx) | position index; replayed from OperatorAdded.inputs |
| `NAND.idx` | P | same as IMPL | OperatorAdded |  |
| `INPUT.idx` | P | `create_operator` (`CREATE (s)-[:INPUT {idx:$i}]->(o)`, `sdk.py`); `_create_edges` (`MERGE (s)-[:INPUT {idx:$idx}]->(o)`, `projection/edges.py`) | OperatorAdded.inputs order |  |
| `IMPL.direction` | P | sdk.py create_direct_edge SET r += attrs{direction} | DirectEdgeCreated.direction; DirectEdgeRepoint.direction |  |
| `IMPL.confidence` | U | sdk.py attrs['confidence']=float(confidence) | DirectEdgeCreated (confidence kwarg) | EP also derives confidence → stored raw edge confidence may conflict with EP; classify |
| `IMPL.weight` | U | sdk.py attrs['weight']=float(weight) | DirectEdgeCreated.weight | same tension as confidence |
| `IMPL.label` | P | sdk.py attrs['label']=label | DirectEdgeCreated.label |  |
| `IMPL.batch_id` | P | sdk.py attrs['batch_id']=batch_id | DirectEdgeCreated.batch_id; BatchIdStamped |  |
| `NAND.direction/confidence/weight/label/batch_id` | P | same writers with rel_type=NAND | DirectEdgeCreated |  |
| `IMPL.msg_alpha` | D | `_flush_cache` (`SET r.msg_alpha = p.a, r.msg_beta = p.b`, `ep.py`) | NOT carried | BP message — recomputed each EP run; REMOVE'd by invalidate_messages |
| `IMPL.msg_beta` | D | `_flush_cache` (`ep.py`) | NOT carried | BP message |
| `IMPL.back_msg_alpha` | D | `_flush_cache` (`SET r.back_msg_alpha = p.a, r.back_msg_beta = p.b`, `ep.py`) | NOT carried | BP back-message |
| `IMPL.back_msg_beta` | D | `_flush_cache` (`ep.py`) | NOT carried | BP back-message |
| `references.sourceVersion` | P | `_anchor_on_create` → `link_source_to_entity` / `link_source_to_event` / `link_source_to_legacy_event` (`projection/edges.py`) | SourceCreated/SourceVersioned (sourceVersionTransit on the Point snapshot) | #5199 version anchor |
| `extractedFrom.sourceVersion` | P | `_link_source` (`_anchor_on_create("$v")`, `projection/edges.py`) | Point.sourceVersionTransit (#5256) | replay passes the Point's own transit |
| `mitigated_by.*` | P | projection/edges.py MERGE (s)-[:mitigated_by]->(o) — no props | OperatorAdded.point.mitigation_strength | no edge properties |
| `aboutSubject/aboutObject/aboutEvent/aboutPoint/aboutDocument.*` | P | `_create_about_edges` (`projection/edges.py`) — no props | about_entities / about* keys on the snapshot | no edge properties |
| `CONTAINS.*` | P | sdk.py turn loop MERGE (s)-[:CONTAINS]->(t) — no props | PointAdded envelope contains_session (#3947) | no edge properties |
| `authoredBy/ownedBy/managedBy/performs/produces/uses/participatesIn/TAGGED.*` | P | projection/entities.py, `_META_KEYS` handled as edges — no props | event.subject/object/uses/participants; about_entities | no edge properties |
