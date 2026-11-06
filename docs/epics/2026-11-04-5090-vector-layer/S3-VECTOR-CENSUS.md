---
title: "S3 — the vector census: which stored vectors no index and no query path can serve"
type: plan
domain: planning
doc_status: draft
created: 2026-11-05
updated: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: "the vector layer of the Tortoise memory graph"
aboutObjects: "epic #5090, child #11088, decision V1 (§12.1c), #4333, #10976, #7879"
---

# S3 — the vector census

**Scope item:** `03-scope.md` **S3** · **Child:** `#11088` · **Epic:** `#5090` ·
**Head this was written at:** `af864a42a6` (`origin/main`).

**What this document is:** the evidence for the surface-approval request `#11088` carries. It records
the census instrument's contract, the population it was measured on, the numbers, and the exact
`entity_type` values whose query results would change if the unindexed vectors were removed. It does
**not** decide anything: the removal is an **MCP/SDK surface change** and is
**requested, not performed** (`AGENTS.md` → MCP/SDK Surface Approval).

---

## 1. The instrument

```bash
uv run python tools/dod_5090_probe.py --census --json \
  --uri docker://:pw@host:6379/<graph> --graph <graph>
```

| exit | meaning |
|---|---|
| `0` | the census ran. Whatever the numbers, they are a measurement |
| `2` | the instrument **could not measure** — no URI, no graph name, an embedded/absent engine (no index API), an unreachable engine, or an unreadable catalogue |

⛔ **`2` is not a red row.** A verdict-derived code would let a caller read "could not measure" as "the
finding is bad"; the two are kept apart at the exit boundary (measured below, §6 row 2's three routes).

**Reads only.** Every query goes through `GRAPH.RO_QUERY`, which the engine rejects for a write — the
"stores nothing" doctrine is the engine's rule, not this file's memory — and the graph **name is
proved to exist** before any query, because a catalogue read on a name that is not there
materialises an empty graph (a write, reported as a measured zero).

---

## 2. What a row says

Per **label** (the population is `CALL db.labels()` — *not* the served set and *not* the indexed set,
so a label that stores vectors with neither a query path nor an index is reported rather than omitted
by construction):

| field | meaning |
|---|---|
| `stored` | nodes whose `embedding` is not null |
| `indexed` | `CALL db.indexes()` holds a **VECTOR** index on that label's `embedding` (an index on another field does not count) |
| `unindexed` | the stored remainder — `UNMEASURED(...)` when the coverage cannot be proved (`#4280` dimension disagreement, unreadable width) |
| `payload.derived_payload_B` | `stored × width × 4 B`. The width is **probed from a stored vector**; when a covering index exists its `dimension` must agree, and a disagreement refuses. **No covering index at all** → the figure is derived from the probed width alone and `how` says the width was not cross-checked; a **covering index whose `dimension` is unreadable** → `UNMEASURED`, because the row reports `indexed: true` and must not also claim coverage |
| `affected_entity_types` | the `entity_type` values the product translates onto this label |
| `removal_changes_query_results` | `true` iff the label stores unindexed vectors **and** some `entity_type` reaches it; `false` when it does not; **`UNMEASURED(...)`** when either input is unmeasured (a consequence is never claimed from a hole) |

**⛔ `entity_type` is not 1:1 with a label, and that is why the row carries it.** `security.entity_label`
sends **two** entity types to `:Source` (`document`, `source`) and **two** to `:Point` (`point`,
`operator`). A census that named only labels would state the wrong population for the surface question
— either overstating what one query loses or understating how many callers lose it. The mapping is read
from `security.VALID_ENTITY_TYPES` through `entity_label`; there is no second table.

**⛔ Bytes for the unindexed labels were the gap this child closed.** The byte figure is derived from a
*width*, not from an index. Before this change it was derived from the covering index's `dimension`, so
every label the issue exists to quantify reported
`UNMEASURED(no VECTOR index on embedding — nothing to derive from)` — the population with no index had
no byte figure, while the stored width had already been probed one branch earlier. The figure is now
`stored × probed width × 4 B`, with `how` stating that with **no index there is nothing to cross-check
the width against**; an unprobeable width still refuses.

---

## 3. The surface consequence

Carried as a **field** of the census (`surface_consequence`), not only as prose, so a machine consumer
cannot read a census as an authorisation:

```json
"requested_not_performed": true,
"sdk_method": "tortoise_fts_query",
"runner": "tortoise.search_engine.run_vector_query"
```

The callers are **derived**, not listed: `tool_registry.TOOL_REGISTRY` (the ONE declaration of the
MCP/REST surface) for tools bound to `sdk_method == "tortoise_fts_query"`, **unioned** with an AST scan
of `tortoise/mcp_server.py` for handlers that pass `entity_type` into that method.

| tool | route | named by |
|---|---|---|
| `tortoise_search` | `GET /v1/search` | registry binding **and** handler forwards `entity_type` |
| `tortoise_query` | MCP only | handler forwards `entity_type` into the hybrid engine (its registry binding is `paginated_query`, so a binding-only derivation **misses it**) |

⚠️ **The binding-only derivation under-reports, and under-reporting is the dangerous direction here:**
it states a smaller consequence for a surface change than the change actually has. `tortoise_query`
binds `paginated_query` (its structural arm) and still forwards a `text=` argument to
`tortoise_fts_query(entity_type=…)`. The AST union is what makes the second tool appear — pinned by
`tests/test_dod_5090_probe.py::test_the_surface_named_is_the_one_that_can_pass_entity_type`.

**The path, in full:**

```text
MCP tortoise_search / tortoise_query,  REST GET /v1/search
  → TortoiseSDK.tortoise_fts_query(entity_type=…)
    → search_engine.classify_query()  (a text query turns the vector leg ON, independent of entity_type)
      → search_engine.degradation_chain(entity_type=…)        # forwards into every leg
        → search_engine.run_vector_query(label=entity_label(entity_type))
          → CALL db.idx.vector.queryNodes(<label>, 'embedding', …)
             ⛔ on a label with no vector index this raises → brute-force full scan
```

So `entity_type='object'` / `'event'` searches **return rows today by full scan** — the mechanism key
in the leg trace reports `ok` for that path, which is why the scan is unobservable to a caller
(`#4999` fixed the *reporting*; it did not fix the scan). Removing those labels' vectors makes those
queries return **zero rows**.

---

## 4. The measurement taken here

**Population: a lane container.** It is a real FalkorDB (`v4.20.6`, the repo-pinned image), seeded with
**nine embeddings in total** at counts `2/2/3/1/1` over `Event`/`Object`/`Point`/`Source`/`Subject`,
written through the engine's own `vecf32`, with the `Point` vector index created through the product's own DDL form. **The counts
below are that population's, not production's** — they demonstrate the instrument and they establish the
*label/index* facts, which are properties of the engine and the product's index DDL rather than of a row
count. The production counts are §5's open item, and are **not** substituted with these.

The transcript below is the tool's **text mode**, pasted unedited:

```text
$ uv run python tools/dod_5090_probe.py --census --uri <lane-uri> --graph census11088_demo
Census — graph `census11088_demo` at 2026-11-06T21:18:55+00:00 (CALL db.indexes()):
  command: uv run python tools/dod_5090_probe.py --census --uri docker://:pw@host:6379/<graph> --graph <graph>
  Event      stored=2            indexed=False  unindexed=2            payload=3072
             entity_type(s) served by this label: ['event'] — removal changes query results: True
  Object     stored=2            indexed=False  unindexed=2            payload=3072
             entity_type(s) served by this label: ['object'] — removal changes query results: True
  Point      stored=3            indexed=True   unindexed=0            payload=4608
             entity_type(s) served by this label: ['operator', 'point'] — removal changes query results: False
  Source     stored=1            indexed=False  unindexed=1            payload=1536
             entity_type(s) served by this label: ['document', 'source'] — removal changes query results: True
  Subject    stored=1            indexed=False  unindexed=1            payload=1536
             entity_type(s) served by this label: ['subject'] — removal changes query results: True
  surface consequence — removal is REQUESTED, not performed; it needs the owner's approval (MCP/SDK surface):
    affected entity_type values: ['document', 'event', 'object', 'source', 'subject']
    path: tortoise_query (MCP only (no REST route)) / tortoise_search (GET /v1/search) → tortoise_fts_query(entity_type=…) → tortoise.search_engine.run_vector_query
  population: CALL db.labels()
```

(`--json` emits the same numbers as the report's `census` object, with `labels`, `payload`,
`affected_entity_types`, `removal_changes_query_results` and `surface_consequence` — it prints no table.)

### 4.1 The answer to the question `#11088` left to measurement

The issue deliberately refuses to assume what `Source` and `Subject` hold (*"do not state 'Source/Subject
hold no vectors' — state what the census finds"*). **They hold vectors, and both are served.**

- `_upsert_document` merges the document node as a `:Source` (D10) and writes `s.embedding`;
  `_upsert_subject` computes and writes one (`#7845`).
- Both labels are in `security.ENTITY_TYPE_LABELS`' served set (`document`/`source` → `:Source`,
  `subject` → `:Subject`), and neither carries a vector index.
- The engine confirms it independently: the product's own inventory warning fires on the same graph —
  *"`#4997`: vector search on `<graph>` is unindexed for label(s) `['Event', 'Object', 'Source',
  'Subject']` (indexed: `['Point']`) — those reads silently full-scan."*

**⇒ The affected set is FIVE `entity_type` values over FOUR labels, not the two (`object`, `event`) the
issue's framing leads with.** `:Point` answers for `point` and `operator` and is the only indexed
label, so it contributes nothing to the removable payload.

---

## 5. The production population — NOT taken here, and why

**This lane's box has no route to the production graph** (no `TORTOISE_DB_URI`; the hosted engine's
credential is a repo secret this box does not hold). The instrument is the deliverable that can be
taken anywhere the graph *is* reachable, and the one thing this document must not do is fill that hole
with a number measured somewhere else — that is the exact failure mode `#5090` exists to correct.

**Take it with:**

```bash
uv run python tools/dod_5090_probe.py --census --json \
  --uri docker://:<pw>@<host>:6379/<graph> --graph <graph>
```

**The last production reading on record is 2026-09-24** (§12.1b): `:Object` 7,859 and `:Event` 4,798
stored-but-unindexed embeddings — **12,657 vectors**, cited by `#5090` and by `STORAGE-ARCHITECTURE.md`
§12.1b/§12.1c as a dated prior. It is **not a current figure**, the tool's own docstring says it is not
to be re-quoted, and §4.1 above is the first reason why: that reading's "no vectors on `Source`/`Subject`"
half cannot be reconciled with what both labels' writers store. Treat it as the shape of the answer,
never as the answer.

---

## 6. Verification — every row with its command and its output

**Row 1 — the census writes nothing.** Guarded twice. `tests/test_dod_5090_probe.py`:
`test_the_census_reads_through_the_READ_ONLY_verb` (a read through `graph.query` fails the test) and
`test_the_LIVE_census_names_the_unindexed_labels_and_writes_NOTHING`, which snapshots the label set,
node count, edge count, the engine's index catalogue and every per-label embedding count **before and
after** the call and asserts they are identical.

```text
$ uv run python tools/test_lane.py run -- tests/test_dod_5090_probe.py -q
110 passed, 1 skipped in 18.52s
```

**Row 2 — an unreadable catalogue exits non-zero and does not report an empty population.**
Three routes, all `2`, none reporting a population:

```text
$ uv run python tools/dod_5090_probe.py --census --json            # no URI
{"census": {"error": "UNMEASURED(no --uri and no TORTOISE_DB_URI)"}}   exit=2

$ … --uri 'docker://:@127.0.0.1:1/nope' --graph nope                # unreachable engine
dod-5090-probe: UNMEASURED(ConnectionError: Error 111 connecting to 127.0.0.1:1. Connection refused.)
                                                                        exit=2

$ … --uri 'file:///tmp/x.db'                                        # embedded: no index API
census: an embedded/absent engine has no index API — pass a docker:// or bolt:// URI
        (embedded would report indexed=False for every label, which is NOT the same fact)
                                                                        exit=2
```

Plus `test_the_census_refuses_to_report_zero_for_an_unreadable_store` and
`test_the_census_does_not_claim_coverage_it_could_not_measure` (unit), which pin the same contract on
the row fields.

**Row 3 — every affected `entity_type` is enumerated, with the SDK/MCP callers named.**
`test_every_affected_entity_type_is_derived_from_the_ONE_mapping` (the one-to-many mapping, both
directions), `test_the_census_states_the_consequence_of_removing_each_label` (per-row sets +
`surface_consequence`), `test_the_surface_named_is_the_one_that_can_pass_entity_type` (both tools, the
AST union), and the live test's assertions on a real engine — output in §4.

**Row 4 — `#10976` and `#7879` carry the "pending V1" qualification.** By comment on each issue, so a
lane holding either issue's body is not collided with:

- `#10976` → [comment](https://github.com/daniel-ospina/tortoise/issues/10976#issuecomment-6695675706):
  (c)'s *"belongs in Postgres per §12.1c"* → *"whose location is **pending V1** (§12.1c `NOT DECIDED —
  owner's call`)"*. The eviction candidate itself is untouched: an index is rebuildable by
  construction, so `93 of the 195 MB` stays a candidate **whatever V1 decides**.
- `#7879` → [comment](https://github.com/daniel-ospina/tortoise/issues/7879#issuecomment-6695676215):
  *"vectors out"* → *"the vector layer — its census and surface request; where the vectors live is
  pending V1"*.

The contradiction they are qualified against, verbatim from `STORAGE-ARCHITECTURE.md` §12.1c:

> **⚠️ NOT DECIDED — owner's call.** It is an open decision (§14.3 V1), because it changes what the
> graph holds.

---

## 7. The request this evidence supports — and everything it does not do

**Requested:** the owner's decision on whether to **remove** the `embedding` property from the
unindexed labels (`:Event`, `:Object`, `:Source`, `:Subject`). Doing so makes
`tortoise_search` / `tortoise_query` / `GET /v1/search` return **zero rows** for `entity_type ∈
{event, object, document, source, subject}` where they return rows today by full scan. That is an
MCP/SDK surface change, and no lane may take it.

**⚠️ The cost argument is GONE, in both directions — do not resurrect it.** The 2026-10-08 billing
ruling makes tiers capped and priced per feature with **retained bytes as revenue**, so removing these
bytes moves the bill by **$0.00**. An earlier draft of `#11088` attributed *"the customer is not paying
for a thing they can never retrieve"* to that ruling; that sentence appears in no recorded source and
was withdrawn. **No recorded decision authorises this removal, and none forbids the census.**

**Not done, deliberately:**

- no vector index is created for any label — that **is** decision V1 (§12.1c / §14.3, `#4333`);
- no vector is removed, and this tool has no flag that could;
- no MCP tool and no public SDK method is added, removed or renamed;
- no cost model is re-derived (§16.1 / §17.4 record it), no placement is re-decided (§14.3), no
  quantization is proposed (§15's do-not-re-invent list), and self-hosting is not revisited (D11 +
  owner, 2026-11-05).

Refs `#5090` · `#4997` · `#10976` · `#7879` · `#4333` · `#10465` · `#10541`
