---
title: "WS-A design — one write contract for the five doors"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-10
subjects.team: epistemic-team
aboutObjects: tortoise-projection, tortoise-sdk, tortoise-longmem-eval
aboutSubjects: tortoise-write-path-integrity, tortoise-declared-vs-stored
---

# WS-A — One write contract for the five doors

**Parent:** #2820 (WS-A write-path integrity) · **Status:** design v4, awaiting owner approval
**Design-side of:** #2795, #2813, #2814 · **Related:** #2788, #2742, #2825, #2873

> #2820 gate: *"Workstreams A/B/C all require owner approval of a written design before implementation."*
> This document is that design. **Nothing here is implemented until approved.** §6 lists the open
> decisions; §3's fixes are mechanical once the decisions land.
>
> **v2** incorporated a fresh-context design review (13 findings). Load-bearing corrections: D2's
> mechanism is specified (a naive passthrough would crash on dict-valued structural keys), D4 gains an
> explicit deny-list, `content_hash` is classified as **derived** (#2795 names it), and D5 switches to
> snapshot+restore so the P0 bulk-wipe guard stays engaged.
>
> **v3** incorporated a second review cycle (1 new P1, 2 new P2). Corrections: **OD6 rewritten** — v2's
> `replayable=False` for EP props would have silently dropped `confidence` on every rebuild (replay
> *restores* it from the journal; only the payload lane must be blocked); **`derive=` now points at
> cycle-free helpers** (referencing the sdk-private `_content_hash` from `contract.py` is an ImportError);
> **D5's edge claim scoped** to edge-free config labels (no edge-snapshot mechanism exists); the wipe
> guard test rephrased (config survives by restoration, not a filtered `DELETE`); D2's filter allows flat
> lists and its skip-set gains the MERGE key `id`.
>
> **v4** incorporated a third review cycle. Corrections: **OD6(a) scoped to journaled props** — only
> `confidence` is replay-restored; `posterior_alpha/beta` and `lastDreamedAt` are written by
> **unjournaled** EP/dream paths and are lost on rebuild (pre-existing → **#2884**); D7's durability
> assertion scoped accordingly. D2's "flat lists allowed" justification was **factually wrong**
> (`search_keys` is stored as a flat space-joined string; `tags` becomes `:Tag` + `TAGGED` edges) and
> is corrected to "scalar + declared `flatten=list` only". EP-owned set completed (`baseline_source`,
> `inherited_at`, `outdated`), and the second `DETACH DELETE n` site (`:1157`) noted.

---

## 1. Problem

A Point reaches the graph through **five doors**, and each door accepts a different subset of the
declared fields. Nothing declares which fields a Point has. The result is silent data loss that varies
by door, and a benchmark that measures a door the product does not use.

This is #2820's *Pattern 1 — Declared ≠ Stored*: the system reports something as stored that never
reaches the graph, with no error raised.

## 2. Evidence

### 2.1 The five doors

| # | Door | Entry point | Acceptance rule | `quote`/`when`/`search_keys`/`source_turn_id`? |
|---|------|-------------|-----------------|---|---|
| 1 | Commit endpoint | `hosted_api._execute_commit_writes` (`:7936`) | Layer-1 validate; unknown field → **422** | **yes** — enumerated at `:8100-8135` |
| 2 | v2 product capture | `sdk._extract_session_v2` (`:3809-3813`) | none | **no** — silent drop |
| 3 | M2 / EventAPI | `extractor.py` / `mining.py` → `projection._upsert_point_props` | fixed SET list | **no** — silent drop, even live |
| 4 | Direct SDK | `sdk.create_point` | `SET n += $props` | **yes** — but **lost on rebuild** (#2795) |
| 5 | Eval harness | `tools/longmem_eval/ingest_v2._write_payload` | fixed kwargs | **yes** — but **lost on rebuild** (#2795) |

**The product capture (door 2) is the outlier, not the rule.** Three of five doors already persist
these fields. That is what makes this a defect rather than a design choice.

The product writer *already has the values in hand*: the extraction payload carries all four
(`extractor_v2.py:3896-3923`) and `_CAPTURE_PASSTHROUGH_PROPS` (`sdk.py:500`) pulls them out — for the
**API response only**. The reply says "here is the quote, here is the date" while the node stores none
of it. That asymmetry is what made the gap invisible.

### 2.2 The declaration surface (more than four)

The "four declarations" framing is too narrow — the review found several more prop lists that must stay
in sync. The full surface:

| Declaration | Where | Role | D1 treatment |
|---|---|---|---|
| Layer-1 payload schema | `commit_schema.Point` (15 fields) | transient payload; `extra="forbid"` | **source of truth** for payload fields |
| Durable property table | `ONTOLOGY.md` §4.1 | graph properties | **diffed** against `contract.py` (parity test) |
| Replay/live contract | `projection/entities.py::_upsert_point_props` fixed SET list | rebuild **and** door 3 | **replaced** by `contract.py` |
| Capture response projection | `sdk.py:500 _CAPTURE_PASSTHROUGH_PROPS` | public API response whitelist | **kept separate** — see D1b |
| Extractor output contract | `extractor_v2.py` `OUTPUT_CONTRACT` / `_VERBATIM_OPTIONAL_FIELDS` (~`:1005-1024`, `:2189`) | prompt contract | **diffed** (these are what the prompt promises) |
| MCP boundary | `mcp_server.py:715 _SERVER_MANAGED_PROPS` | tenant boundary | **kept** (orthogonal: rejection, not persistence) |
| Commit kind props | `hosted_api.py:13222 _KIND_PROP_KEYS` | commit-door validation | **derived** from `contract.py` |

Currently `ONTOLOGY.md` §4.1 lacks `search_keys` and `source_turn_id`; `_upsert_point_props` also never
writes `speaker` although ONTOLOGY §4.1 lists it. #2742 is the same class (import-time snapshot vs
runtime registry).

**Derivation direction (D1):** `contract.py` is authoritative for *persistence*. `commit_schema.Point`
remains authoritative for the *payload shape*. `_CAPTURE_PASSTHROUGH_PROPS` remains a **separate output
projection** (D1b). Everything else is diffed, not merged.

### 2.3 Why the drift happened

The extractor is shared between product and eval; the **persistence writer was forked**. Each
benchmark-driven extraction improvement (#1533, #1535, #1538, #1539, #1544, #1763, #2165) was applied to
the contract + extractor + **eval writer**, and only sometimes to the product writer. Decisive:

```
$ git show --stat d4da83c57   # #1535 — atomic points, search_keys, speaker via source-turn
 tortoise/commit_schema.py       |  28 +   ← contract gains search_keys + source_turn_id
 tortoise/extractor_v2.py        | 201 +   ← extractor emits them
 tools/longmem_eval/ingest_v2.py |  37 +   ← eval persists them
 tortoise/hosted_api.py          |   4 +   ← commit door gains them
   (tortoise/sdk.py: NOT TOUCHED)          ← product capture writer never learns them
```

(The full stat also touches four test files. Abridged; the `sdk.py` omission is the point.)

`_CAPTURE_PASSTHROUGH_PROPS` (#1529) then whitelisted them into the **API response**, which made the
response look correct and hid the gap. No test compares response props to node props; no test compares
the two writers; and the write-path benchmark grades **prose** (`grading.py:363-389`), so a point scores
1.0 quote-fidelity while storing no `quote` prop.

This is the **write-path half of the inversion already done for retrieval**
(`docs/parity/2026-08-29-retrieval-inversion.md`). Persistence was simply never included.

### 2.4 External validation

- **Dual-write problem** (Confluent, DZone, Auth0): two independent writes with no atomic boundary
  *eventually diverge*. The standard fix is one authoritative write with everything else derived.
- **Anti-Corruption Layer** (Azure/AWS Architecture Center): translate at a boundary; do not let one
  side's semantics compromise the other. The right shape for benchmark instrumentation.
- *"Agent Benchmark Scores Are Measuring the Harness, Not the Model"* (focused.io) — our exact failure.
- **Zep / Graphiti** resolves contradictions by invalidating the **edge** (`valid_at`/`invalid_at`), and
  frames it as *"temporal edge invalidation rather than LLM-driven judgment"* — deliberately not a stored
  verdict. Reinforces D4.

---

## 3. Design

### D1 — One persisted-property declaration per layer (authoritative for persistence)

New module `tortoise/projection/contract.py`. Per entity layer (Point, Subject, Object, Document,
Event, Source), one declaration carrying an explicit `source`:

```python
POINT_PROPS: dict[str, Prop] = {
    # sourced from the payload
    "content":        Prop(str, required=True, source="payload", replayable=True),
    "quote":          Prop(str, max_len=200,   source="payload", replayable=True),
    "when":           Prop(str, max_len=40,    source="payload", replayable=True),
    "search_keys":    Prop(str, flatten=list,  source="payload", replayable=True),
    "speaker":        Prop(str,                source="capture", replayable=True),
    "source_turn_id": Prop(str | int,          source="payload", replayable=True),  # type pending OD1
    # derived — MUST be recomputed by the replay writer, never copied from the payload.
    # derive= MUST point at CYCLE-FREE helpers: tortoise.sdk imports tortoise.projection at
    # module top (sdk.py:34), so referencing the sdk-private _content_hash from contract.py
    # is an ImportError. Use tortoise/ids.py::content_hash (stdlib-only) and
    # tortoise/embeddings.py::compute_embedding (no tortoise.* imports).
    "content_hash":   Prop(str, source="derived", derive=ids.content_hash, replayable=True),
    "embedding":      Prop(vector, source="derived", derive=embeddings.compute_embedding,
                           guards="skip operators; require truthy content; wrap in vecf32",
                           replayable=True),
    # EP-owned. `replayable` MUST be honest (see OD6):
    #   confidence     — journaled (node snapshot at PointAdded/Promoted) → restored by replay
    #   lastDreamedAt  — UNJOURNALED → NOT restored (pre-existing gap, #2884)
    # All are payload_writable=False: the payload/instrumentation lane must never overwrite EP state.
    "confidence":     Prop(float, source="ep-owned", replayable=True,  payload_writable=False),
    "lastDreamedAt":  Prop(str,   source="ep-owned", replayable=False, payload_writable=False),
    ...
}
```

**Consumed by:** door 3's writer, door 4's validation, door 1's enumerated kwargs, door 2's passthrough,
and rebuild's replay. `ONTOLOGY.md` §4.1 gains `search_keys` + `source_turn_id`; a parity test diffs the
two tables.

**`content_hash` is the reason `source` matters.** #2795 names it explicitly, and it **cannot** be
restored by passthrough: `_emit_event` strips it from the journaled point (`sdk.py:2319-2325`), the #548
snapshot strips it (`projection/__init__.py:1219-1221`), and nothing recomputes it. It must be
**derived** from `content` in the replay writer.

### D1b — The capture response stays a separate output projection

`_CAPTURE_PASSTHROUGH_PROPS` is **not** merged into `POINT_PROPS`. It exists to stop internal
projection state (confidence, status, `operator`/`provenance` dicts) leaking into the public capture
response. D1 governs **persistence**; the response stays an explicit whitelist **subset**.

Consequently D7's parity check is **one-directional**: *every prop in the response exists on the node*
(not the reverse), asserted over the declared extraction-output subset.

### D2 — Replay preserves what it does not recognise — with an explicit skip-set and precedence

`_upsert_point_props` is the **shared live+replay writer** (its own docstring: *"Single source of truth
for Point property parity between apply() and rebuild_all()"*). So D2 changes **door 3's live behaviour
too** — intentional, since that symmetry is the point (door 3 drops props today).

A naive `SET n += $extra` is **wrong** and would crash. Required mechanics:

1. **Value-type filter: scalar only, plus exactly the declared `flatten=list` props.** FalkorDB
   rejects non-primitive property values. `operator` and `provenance` are dict-valued and deliberately
   never persisted (`entities.py:132-149` filters only `None`; `tortoise/api.py:64-75` and
   `sdk.py:5750-5753` put dicts into `event_point`) — exclude them.
   - `search_keys` **is** flattened by declaration: stored as a flat space-joined STRING
     (`_flatten_search_keys_prop`, `sdk.py:913-938`, applied at `:2387`/`:4147`), because FalkorDB's
     fulltext index does not index array-valued properties (`projection/__init__.py:2516-2540`
     migrates pre-R2 arrays back to strings). D1 declares it `Prop(str, flatten=list)`.
   - `tags` **is a genuine raw list node property** (`sdk.py:2578-2581` writes every prop key incl.
     `tags`; `sdk.py:4226-4237` keeps the `TAGGED` edges consistent with `n.tags`;
     `tests/test_sdk.py:726` asserts `["tags"] == ["alpha"]`). It is the **one known undeclared
     list prop**. D2's rule: `tags` is owned by its own `_sync_tags` path, is **not** passed through
     the generic filter, and **TAGGED-edge replay is out of scope** (today's behaviour — no
     regression). It must be declared explicitly in `contract.py` as
     `Prop(list, source="capture", replayable=False, owned_by="_sync_tags")` so the omission is
     **intentional and visible** rather than an accident of the filter.
   - Otherwise **an undeclared list is denied** — never written raw.
2. **Explicit `_POINT_HANDLED` skip-set** — every fixed SET-clause key, the MERGE key `id`
   (`MERGE (n:Point {id:$id})`, `entities.py:226` — *not* a SET clause, so not covered by item 1),
   structural keys (`operator`, `provenance`, `about_entities`, `extractedFrom`, `_nid`, `_graph_id`),
   and `_META_KEYS`.
3. **Key precedence / ordering.** The passthrough must never overwrite `updatedAt` (stamped with
   rebuild-now, load-bearing for the seq-gated folds — `entities.py:358`) or `embedding` (`vecf32`).
   Declared-props win; passthrough applies only to keys outside the fixed clauses.
4. **Non-persistable deny-list.** See D4 — `reason` must be on it.

On an unrecognised-but-primitive, non-denied prop: persist + emit a drift warning naming the key.

**Why preserve rather than fail-closed:** rebuild is the documented disaster-recovery path. A rebuild
that refuses to run because of an unrecognised prop turns a durability bug into an outage.

### D3 — Instrumentation is an explicit, declared input — not a forked writer

The eval harness stops owning a writer. The shared writer accepts:

```python
instrumentation: dict | None = None   # benchmark-only props, namespaced and declared
```

with `INSTRUMENTATION_PROPS` declared separately from `POINT_PROPS`, so benchmark fields
(`has_answer`, `answer_string_mark`, `lme_question_id`, `lme_session_index`, forged `createdAt`) are
*structurally incapable* of being mistaken for product fields.

**Why:** the fork existed because the harness needed to write benchmark-only data. That is a real need;
the fix is to make it an explicit input to one writer, not a second writer.

### D4 — `reason` is NOT persisted — enforced by a deny-list

`reason` (`NEW`/`REVISES`) is the extractor's **extraction-time opinion**. The structural truth is the
outgoing `CORRECTS` edge that `supersede()` writes.

Verified:
- no production Cypher reads the node property; the only two reads are eval tests self-labelled
  `# (observability)` (`tests/test_ingest_v2_consolidation.py:330`, `tests/test_longmem_runner.py:2008`);
- it is excluded from the content hash as an "LLM artifact" (`commit_schema.py:1054`);
- the commit door does not write it (`hosted_api.py:8114-8135`);
- `docs/ONTOLOGY.md:602` states the principle for the sibling case — *"`challenged` is NOT a state — it
  is a DERIVED condition"*.

**⚠️ Correction to design v1.** v1 claimed the opinion "already has a durable home in the JSONL event
log". **That is false.** The journal entry is a *snapshot of the graph node*
(`sdk.py:2628-2629` → `_emit_event(..., point=self.get_point(pid))`), and `reason` only reaches the
journal *because it was written as a node property*. Remove the property and it leaves the journal too.

So the decision is explicit: **the extraction-time opinion is discarded.** If it is wanted for audit, it
must be journaled deliberately as a non-persisted extraction record — a separate decision, **not** made
by this design.

**Enforcement:** omitting `reason` from `POINT_PROPS` is *not sufficient* under D2 — the passthrough
would persist it (the eval writer passes `reason=` today at `ingest_v2.py:235`, and any tenant can pass
it through the arbitrary-props lane). `reason` therefore goes on an explicit
**`NON_PERSISTABLE_PROPS` deny-list**, applied with a warning in the shared writer.

### D5 — Node classes: derived vs authoritative (fixes #2814)

`rebuild_all` currently runs `MATCH (n) DETACH DELETE n` (`projection/__init__.py:1301`) — wiping
configuration along with derived data.

| Class | Labels | Rebuild behaviour |
|---|---|---|
| **derived / rebuildable** | `:Point`, `:Object`, `:Subject`, `:Document`, `:Event`, `:Source` | wiped and replayed from the journal |
| **authoritative config** | `:PackManifest`, `:PackInstall`, future `:Pipeline`/`:PipelineStage`, graph settings | **preserved** |

**Mechanism: snapshot + restore, not a filtered wipe.** Two reasons:

1. **The P0 production guard depends on the wipe being unconditional.** `_is_bulk_wipe`
   (`projection/__init__.py:74-90`) is the only input to `_assert_test_graph` (`:2045-2075`). Adding a
   predicate to the DELETE (e.g. `AND n.x IS NULL`) reclassifies it as a *non*-bulk-wipe and silently
   **disengages the production guard**. Snapshotting first, then wiping unconditionally, keeps the guard
   engaged.
2. **Edges — scoped, not hand-waved.** `DETACH DELETE` of derived nodes strips any edge between a
   preserved config node and a derived node, and **no edge-snapshot mechanism exists** in either
   precedent — #548 and `:Batch` both snapshot node *properties* only (`:1188-1255`, `:1257+`).
   **D5 is therefore scoped to edge-free config labels.** Verified: no edge into
   `:PackManifest`/`:PackInstall` exists today — those nodes are MERGE-only (`pack_state.py:290`,
   `pack_manifest_store.py:210,224`). If a future `:Pipeline`/`:PipelineStage` needs
   config→derived edges, an edge snapshot `(src, tgt, rel, attrs)` + restore step is required —
   **out of scope here, flagged for #2792.**

This mirrors the existing, proven pattern in the same method: the #548 Point snapshot
(`:1188-1255`) and `:Batch` snapshot (`:1257+`).

> **Second wipe site.** `projection/__init__.py` has a *second* unconditional
> `MATCH (n) DETACH DELETE n` at `:1157` (`rebuild(log)`). Its only caller is test-side, so the
> live risk is low — but if it is ever wired to a product path it needs the same preservation rule.
> One clause, not a scope expansion.

Plus a distinguishable **`config_reset` marker**, so *"never configured"* and *"configured then wiped"*
stop being indistinguishable. This matters beyond tidiness: #2728 makes **absence meaningful** (no
selection marker ⇒ legacy union), so a silent wipe is a **permissions change**, not a settings reset.

A test must pin that the wipe statement **remains** the unconditional `MATCH (n) DETACH DELETE n`
(`:1301`) — config survives by **restoration**, never by a filtered `DELETE` — so `_is_bulk_wipe`
(`:74-90`) and the `_assert_test_graph` production guard (`:2045-2075`) stay engaged.

### D6 — Wire the four fields into product capture

`_extract_session_v2` passes `quote`, `when` (+ `validFrom`), `search_keys`, `source_turn_id` to
`create_point`. The mechanical half of #2813; a no-op once D1 lands.

### D7 — Parity tests (the durable guard)

| Test | Asserts |
|---|---|
| prop durability | every **journaled** `replayable` prop of every layer survives `rebuild_all` — including an operator point and an M2 point (the D2 skip-set cases) and a **derived** prop (`content_hash`). Unjournaled EP state (`posterior_alpha/beta`, `lastDreamedAt`) is **excluded** and tracked separately |
| writer parity | product writer ≡ eval writer for a shared payload (minus instrumentation) |
| response ⊆ node | every prop in the capture response exists on the node (one-directional, per D1b) |
| declaration parity | `ONTOLOGY.md` §4.1 ≡ `contract.py`; extractor `OUTPUT_CONTRACT` diffed |
| deny-list | `reason` never reaches a node from any door, with a warning |
| wipe guard | `_is_bulk_wipe(<new wipe>) is True` |
| benchmark coverage | `tests/eval/write_path` grades the declared props, not just prose |

---

## 4. What the design fixes

| Issue | How | Status |
|---|---|---|
| #2795 rebuild drops live-only props | D1 (`content_hash` **derived**) + D2 (passthrough for the rest, primitive-filtered) | **fully closed** — v1 missed `content_hash` |
| #2813 capture drops E3 fields; eval lane diverges | D1 + D3 + D6 — one writer, one declaration, instrumentation as input; D7 guards it | **closed in principle** |
| #2814 rebuild wipes config | D5 — snapshot+restore, `config_reset` marker, guard preserved | **closed in principle** |
| #2788 (event state nothing reads) | out of scope — same class, separate fix | — |
| #2742 (import-time snapshot drift) | out of scope — D1's parity test is the same pattern | — |

---

## 5. Sequencing

| # | Step | Closes | Risk |
|---|---|---|---|
| 1 | **D1** — `contract.py` + declaration-parity test. No behaviour change. | — | none |
| 2 | **D2** — skip-set + primitive filter + precedence + `content_hash` derivation + drift warning + prop-durability test. **Lands in the shared writer ⇒ also fixes door 3's live drop.** | #2795 | medium — touches the replay *and* live writer |
| 3 | **D6** — wire the four fields into product capture + response ⊆ node test. | #2813 (product half) | low |
| 4 | **D3** — shared writer + instrumentation input; delete `_write_payload`; repoint LongMemEval. | #2813 (eval half) | **high** — benchmark comparability; baselines re-blessed here (OD5) |
| 5 | **D4** — deny-list + remove the eval `reason=` write + re-point the two observability tests. | — | low |
| 6 | **D5** — snapshot+restore + `config_reset` marker + guard test. | #2814 | medium — touches the P0 guard surface |

Steps 1–3 are independently shippable. Step 6 is independent of 1–5. Step 5 can ride with step 4.

---

## 6. Open decisions for owner (decision register)

| ID | Question | Recommendation | Consequence if different |
|---|---|---|---|
| **OD1** | Canonical graph form of `source_turn_id`: integer index, or turn-node ref string? | **Ref string is currently load-bearing.** Eval turn ids are `lme:{qid}:s{si}:t{idx}` (`ingest_v2.py:211`; call sites `:587`/`:597`) — keyed on the **dataset question id**, not the graph `session_id` — and the read path that derives speaker does not fetch `lme_question_id` (`ingest.py:313-341`, `retrieve.py:469-506`). An integer helper would have to be `turn_node_id(qid, si, idx)`, requiring `lme_question_id` added to the read fetch. **Recommendation: defer the type decision to step 3; declare it `source_turn_id: Prop(str\|int, source="payload")` meanwhile.** | Hardcoding either form in D1 now risks a wrong declaration. |
| **OD2** | Config preservation mechanism: filtered wipe / snapshot+restore / refuse? | **Snapshot+restore** (D5) — keeps `_is_bulk_wipe` engaged. | A filtered wipe silently disengages the P0 production guard. Refusing blocks the recovery path. |
| **OD3** | Instrumentation: declared prop set or a separate `:Instrumentation` label? | **Declared prop set** (`INSTRUMENTATION_PROPS`), no new label. | A new label is an ontology change (WS-B) and needs its own rebuild class. |
| **OD4** | Door 3 (M2/EventAPI): fix in this pass? | **Yes** — D2 lands in the shared writer, so door 3 is fixed by construction. Making it rebuild-only would require a second code path (the divergence we are removing). | Deferring leaves door 3 dropping props live. |
| **OD5** | Re-bless eval baselines in step 4? | **Yes**, same PR, before/after numbers recorded on #2813. | Splitting leaves the harness reporting numbers from a path that no longer exists. |
| **OD6** | **NEW** — Which props may the *payload/instrumentation lane* write, vs which are EP-owned? | **Two separate rules, not one.** (a) **Replay restores only what is journaled.** `confidence` IS restored — pass-1a's `n.confidence=coalesce($cf, n.confidence)` (`entities.py:190`), `$cf` from the journaled node snapshot (`sdk.py:2629`; `get_point` returns raw `properties(n)`, `sdk.py:6062-6076`), and `ConfidenceChanged` is an explicit rebuild no-op (`projection/__init__.py:1513`) with no post-rebuild re-derivation hook. **`posterior_alpha` / `posterior_beta` / `lastDreamedAt` are NOT restored** — their writers (`ep.py:268`, `dream.py:258/446/456`) emit **no journal event at all** (grep `_emit_event` over ep/dream/analyze → empty; `ConfidenceChanged` is emitted nowhere), and the #548 snapshot skips log-covered points (`:1216`). This is a **pre-existing gap this design does not close** → **filed as #2884**. (b) The **payload/instrumentation lane must NOT overwrite** EP state — `payload_writable=False`. EP-owned/derived set = `confidence`, `c_cal`, `posterior_alpha`, `posterior_beta`, `ep_alpha`, `ep_beta`, `baseline_set`, `baseline_source`, `inherited_at`, `lastDreamedAt`, `expiredAt`, `outdated`. | v2's `replayable=False` would silently drop `confidence` on rebuild. v3's blanket "replay restores EP state" would make D7's durability test unsatisfiable for the unjournaled props. |
| **OD7** | **NEW** — Is the extraction-time `reason` opinion wanted for audit at all? | **Discard** (D4). If wanted, journal it deliberately as a non-persisted extraction record. | Choosing "journal it" is a small additional scope in step 5. |
| **OD8** | **NEW** — Where does the `config_reset` marker live: graph property on a singleton node, or operator node? | **Singleton `:GraphMeta` node property.** | An operator node would be rebuild-wiped with the derived class. |

---

## 7. Explicitly out of scope

- The **value layer** (#2782, #2817) and the **pack value-declaration surface** (#2818) — WS-B/WS-C.
- `:Pipeline` / `:PipelineStage` themselves (#2792) — this design only ensures they *survive* rebuild.
- #2788 (event-carried state nothing reads) and #2742 (import-time snapshot) — same class, separate fixes.
- Restoring the missing `docs/audit/2026-08-29-product-cohesion.md` (#2825).
