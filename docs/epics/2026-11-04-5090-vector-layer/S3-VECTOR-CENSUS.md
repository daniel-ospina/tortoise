---
title: "S3 — the vector census: which stored vectors no index and no efficient query path can serve"
type: plan
domain: planning
doc_status: draft
created: 2026-11-06
updated: 2026-11-06
subjects.team: epistemic-team
aboutSubjects: "the vector layer of the Tortoise memory graph"
aboutObjects: "epic #5090, child #11088, decision V1 (§12.1c), #4333, #10976, #7879"
---

# S3 — the vector census

**Scope item:** `03-scope.md` **S3** · **Child:** `#11088` · **Epic:** `#5090` ·
**Base:** `af864a42a6` (`origin/main` at branch point) · **Measured at:** this branch's head at review
round 8 (`2026-11-06`) — the tree §4's transcript and §6's commands were taken on. ⚠️ The two are
different trees and must not be conflated: the census fields this document describes do not exist at
the base, so a reader who re-runs §6 against `af864a42a6` reads a tree that never held the instrument
(see `tools/finding_provenance.py`).

**What this document is:** the evidence for the surface-approval request `#11088` carries — ⚠️ **and that request is answered for TWO of the four labels it names: the disposition was ruled KEEP on 2026-11-10 (`#11395` comment `6781921075`) for `:Object`/`:Subject`, so no removal is proposed for those two and §7 carries the answered state; the measurement below is kept as the record.** ⚠️ **The ruling names those two labels and NO others** — it does not reach `:Event` or `:Source`, which §7 states: for them the 2026-09-24 direction (*"keep the vectors, make them searchable"*) is the record, and it authorises no removal either. So no removal is proposed for any of the four, but **on two different grounds**, and never by widening the ruling's two labels to the request's four. It records
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

Per **label**. The population is the **union** of the graph's own labels (`CALL db.labels()`), the
labels the catalogue reports an index on, and the served set — deliberately *wider* than any one of
them, so a label that stores vectors with neither a query path nor an index is reported rather than
omitted by construction. ⚠️ One consequence, so no row is misread: a **served** label the graph does
not have still appears (with `stored=0`), and that row means "no such label here", **not** "this label
exists and holds nothing". The `population` field names `CALL db.labels()` — the read that was taken —
not the union that formed the row set:

| field | meaning |
|---|---|
| `stored` | nodes whose `embedding` is not null |
| `indexed` | `CALL db.indexes()` holds a **VECTOR** index on that label's `embedding` (an index on another field does not count) |
| `unindexed` | the stored remainder — `UNMEASURED(...)` when the coverage cannot be proved (an unreadable index `dimension`, an unreadable or disagreeing stored width, an unreadable stored count, `#4280` dimension disagreement) |
| `payload.derived_payload_B` | `stored × width × 4 B`. The width is **probed from the stored vectors** — up to `WIDTH_PROBE_SAMPLES` (5) of them, **chosen by the engine**: the probe query carries no `ORDER BY`, so "the first 5" is not a property it has and a re-run of the same graph may read a different slice — which is why `how` states it (`… from 5 sampled row(s) chosen by the engine (the probe carries no ORDER BY) …`): the window can move between runs, and the sample size alone does not say which rows it held. A disagreeing sample refuses the figure outright, so where a figure derived from the PROBED WIDTH stands (the no-covering-index arm), the engine-chosen window is the one thing `how` must disclose — a figure taken from a covering index uses `stored × dimension` and does not carry that disclosure, but that is **not** the same as being immune to the window: it is published only where the PROBED width agrees with the index dimension (`… probed width 384 agrees`), and a sample that catches a disagreement refuses instead (`UNMEASURED(stored widths disagree (…))`), so one run of the same graph can publish and the next refuse. The census does not establish that an indexed label holds a single width, so that invariant is not assumed here. More than one row is probed at all because one row's width is not the label's: a graph that went through an `EMBEDDING_DIM` rotation holds vectors of two widths, and `stored × width` would then multiply one sampled row across every row. Sampled widths that **disagree** → `UNMEASURED(stored widths disagree (…))`, never a product. ⚠️ **Declared residual:** a sample window that misses a narrower minority width cannot refuse; in the no-covering-index arm `how` states the sample size (`… comes from 5 sampled row(s) …`) — an observation, not a claim of exhaustiveness — and in the covering-index arm nothing does, so there the residual is invisible in the output. When a covering index exists its `dimension` must agree, and a disagreement refuses. **No covering index at all** → the figure is derived from the probed width alone and `how` says the width was not cross-checked; a **covering index whose `dimension` is unreadable** → `UNMEASURED`, because the row reports `indexed: true` and must not also claim coverage; an **unreadable catalogue row** → `UNMEASURED` as well, because `not covering` cannot tell "no index" from "an index we could not see" |
| `indexes_on_label` | **every** index kind the catalogue reports for the label, not only VECTOR — `{"FULLTEXT": ["_searchText"]}` or `{"VECTOR": ["embedding"]}` or `none`. Read from the same `CALL db.indexes()` rows at no extra query cost, because §3.1's deciding datum (does this label carry a FULLTEXT index?) is a **non-vector** kind — and the census used to promise the run settled it while parsing VECTOR rows only |
| `affected_entity_types` | the `entity_type` values the product translates onto this label |
| `removal_changes_query_results` | `true` iff the label stores unindexed vectors **and** some `entity_type` reaches it; `false` when it does not; **`UNMEASURED(...)`** when either input is unmeasured (a consequence is never claimed from a hole) |

A label whose consequence is `UNMEASURED` is carried in
`surface_consequence.unmeasured_labels` and **never dropped**: `labels` (and
`entity_types_whose_query_results_change`) is a **MEASURED** set, so a non-empty `unmeasured_labels`
means that set is not the whole population. Both the JSON and the text path say so.

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

The callers are **derived**, not listed, from **three** sources unioned — because each one alone
under-reports in a different direction:

1. `tool_registry.TOOL_REGISTRY` (the ONE declaration of the MCP/REST surface) for tools bound to
   `sdk_method == "tortoise_fts_query"`;
2. an AST scan of `tortoise/mcp_server.py` for **handlers** that pass `entity_type` into that method;
3. an AST scan of `tortoise/sdk.py` for **SDK methods** that *reach* the leg with a *fixed*
   `entity_type` — a class neither of the other two can see, because the handler never forwards the
   argument. Reach is **transitive over name-resolved in-module calls**, not one hop — and that is the whole claim: a reach that passes a leg-reaching function **by reference** (`_safe(self._review_pool, …)`, a callback, `functools.partial`, `getattr(self, name)`) cannot be traversed, so such a shape is reported as an `UNMEASURED(declared bound: …)` entry rather than guessed into the caller list. **A name-resolved call is never declared** — the closure already owns it, so declaring one would caveat a complete table (and it really does own it: a call sitting in a nested `class` **body** is the enclosing method's edge, because that body executes when the method does, so the three walks — `direct`, the closure, the mention scan — all treat a class body as the function's), and neither is a local that merely reuses an entrant's name. **What the declaration covers, exactly:** a mention of a leg-reaching function that is not called by a name the closure can resolve and not bound by a function-scope name, anywhere in a function **body** — where a function-scope name means a parameter, a `Name` store, an `except … as` binding or an import alias, and — since `#11397` — ALSO a `*args`/`**kwargs` name, a `match` pattern binding, a name bound by a nested `def`/`class` statement, and a name `del`eted in the body: each of those binds in the method's own scope, so a later mention of it is not a reach. ⚠️ **NOT a lambda parameter**, and the difference is the one a comprehension already has: a lambda parameter binds in the LAMBDA's scope, so it shadows only inside that lambda's own body (its DEFAULTS are evaluated in the enclosing scope and ARE mentions), while a mention of the same name elsewhere in the method is still a reach — treating it as function-scope suppressed a genuine reach, so it is tracked as a SCOPE rather than a shadowing name (`#11397` review). ⚠️ Still NOT modelled, and stated rather than implied: `global`/`nonlocal` (a name whose scope this walk does not track) and a binding in a nested CLASS BODY — the two walks disagree about `class` on purpose (a class body EXECUTES with the method, so a mention in it is walked and reported; a binding in it is not treated as function scope) — so a mention reusing one of those two is still declared spuriously. A comprehension is its own scope for its element, its `if` clauses and its later `for` clauses, ⚠️ **but its OUTERMOST iterable is evaluated in the enclosing one** — so `[x for _pool in _pool]` names the METHOD's `_pool` and IS declared, while `[x for _pool in rows]` does not. ⚠️ **Outside the walk, and therefore NOT reported:** a nested `def`'s decorators and parameter defaults (`def inner(cb=self._review_pool)` *is* a real reach, evaluated when the outer method runs). A mention **inside** a comprehension body is walked and reported **unless it reuses that comprehension's own target name** — the target binds in the comprehension's own scope, so a `Load` of it refers to that binding, not to the method; the target itself is not a mention and is not reported. ⚠️ The **outermost iterable is the exception**: it is evaluated in the enclosing scope, so a mention there *is* reported. ⚠️ And the accepted **reference forms** are a fixed set: a `self` / `super()` / `type(...)` attribute, a bare name, and `getattr(self, …)` — note that only the first and third are *receivers*; a bare name is matched as a mention, so it must literally be the helper's own name. Since `#11397` the RECEIVER set is wider than `self`: a classmethod's own first parameter (`cls._pool`), the enclosing class NAME (`S._pool`), `self.__class__._pool` (⚠️ the BASE must itself be an in-module receiver: `other.__class__._pool` is a FOREIGN object's class, not this module's method, and accepting any `*.__class__` published a spurious caveat) and `getattr(self.__class__, '_pool')` are all in-module receivers, alongside `super()` and `type(self)` — each of them was a real reach DROPPED with no entry before that fix. ⚠️ What is still unreported, and named here rather than left to a reader: a rebinding that hides the receiver (`alias = self; alias._pool`), and a foreign object that happens to be named exactly like the enclosing class. Those are gaps in the instrument, not in its promise — the promise is scoped to the body walk. **At this head the declaration names one method**: `volunteer_context` passes the leg ITSELF (`_search_fn=self.tortoise_fts_query`) into the volunteer pipeline, which calls it with `entity_type="point"` — so that reach is a real fixed-type use of the leg that the closure cannot traverse, and it is reported rather than dropped. A name-resolved `self.tortoise_fts_query(…)` call is still not declared. The test that pins this asserts that every passer the run declares is named here, so a method that starts passing the leg by reference without a doc mention reds — it does NOT pin the count, so a second passer whose name already appears in §3 of this document would not red (a residual of the `#11397` class, not of the instrument). ⚠️ The receiver-blind matches are the over-report direction, and there are two of them, at different sites — pre-existing behaviour, unchanged here. A call naming the LEG with a foreign receiver (`self.conn.tortoise_fts_query(…)`) makes its caller an entrant through the call scan's `_is_target`, which matches `node.attr == VECTOR_LEG_SDK_METHOD` with no receiver check; the closure's own `func.attr` match is what credits that shape for a NON-leg helper (`self.conn._review_pool(…)`). ⚠️ Do not look for the first shape in the closure: `direct` is built with `if fn is leg: continue`, so the leg is never in it and the closure's edge to the leg is inert — a receiver check added there would change nothing for a leg call while the over-report continued from `_is_target`. Reach is not one hop: `review_connections` →
   `_review_add`/`_review_prune` → `_review_pool` → the leg, and a scan keyed on the enclosing
   function would have named the private helper (not a registry `sdk_method`) and silently dropped
   the tool. A call that omits the kwarg runs the SDK's own default (`entity_type="point"`), which is
   as much of an entrant as an explicit one; a non-literal argument is reported as
   `UNKNOWN(<expr>)`, never guessed into a type — and such a tool's consequence reads
   `UNMEASURED(...)`, never `[]`.

| tool | route | asks the leg for | named by | results change |
|---|---|---|---|---|
| `tortoise_search` | `GET /v1/search` | the caller's `entity_type` | registry binding **and** handler forwards `entity_type` | **yes** — the caller may choose any affected type |
| `tortoise_query` | MCP only | the caller's `entity_type` | handler forwards `entity_type` into the hybrid engine (its registry binding is `paginated_query`) | **yes** — same reason |
| `tortoise_recall` | MCP only | `object` **and** `point` | its SDK method (`recall_state`) calls the leg with a fixed `entity_type` | **yes** — its **default** `mode="state"` read queries `:Object` (`object_centric` defaults `True`) |
| `tortoise_search_sessions` | MCP only | `event` | its SDK method (`search_sessions`) calls the leg with a fixed `entity_type` | **yes** — `:Event` |
| `tortoise_review_connections` | MCP only | `point` **via a private helper** (`_review_add`/`_review_prune` → `_review_pool`) | reachability from its SDK method to the leg | no — `:Point` is the one indexed label |
| `tortoise_issue_insight` | `GET /v1/issue-insight` | `point` | its SDK method calls the leg with a fixed `entity_type` | no — `:Point` is the one indexed label |
| `tortoise_suggest_entry_points` | MCP only | `point` (the SDK default, no kwarg) | its SDK method calls the leg without `entity_type` | no — same reason |

The census computes that last column per tool (`surface_consequence.surface[].reaches_affected_labels`)
from the measured set, so "which tools change" is an answer rather than a reading — and a tool that is
an entrant **without** a consequence is named with `[]` rather than dropped: dropping it would hide a
future consequence behind a name that was never printed.

⚠️ **The binding-only derivation under-reports, and under-reporting is the dangerous direction here:**
it states a smaller consequence for a surface change than the change actually has. `tortoise_query`
binds `paginated_query` (its structural arm) and still forwards a `text=` argument to
`tortoise_fts_query(entity_type=…)`; and `tortoise_recall` forwards nothing at all while reaching
`:Object` on its default path. Each source is what makes the next tool appear — pinned by
`test_the_surface_named_is_the_one_that_can_pass_entity_type` and
`test_a_tool_whose_SDK_method_fixes_entity_type_is_named`.

**The path, in full:**

```text
MCP tortoise_search / tortoise_query / tortoise_recall / tortoise_search_sessions,  REST GET /v1/search
  → TortoiseSDK.tortoise_fts_query(entity_type=…)
    → search_engine.classify_query()  (a text query turns the vector leg ON, independent of entity_type)
      → search_engine.degradation_chain(entity_type=…)        # forwards into every leg
        → search_engine.run_vector_query(label=entity_label(entity_type))
          → CALL db.idx.vector.queryNodes(<label>, 'embedding', …)
             ⛔ on a label with no vector index this raises → brute-force full scan
```

So `entity_type='object'` / `'event'` searches **return rows today by full scan**. Two keys in the
leg trace describe that path, and they say different things: `reason` reports **`ok`**, while
`mechanism` reports **`scan_fallback`** — so since `#4999` (PR `#5159`) the scan *is* observable to a
caller without reading the source; what `#4999` did not do is fix the scan. ⚠️ Neither key values the
cost: `ok` is not "cheap", and `scan_fallback` is a mechanism name, not a warning.

### 3.1 ⛔ What removal actually changes — measured, and NOT "zero rows"

An earlier draft of this document, following the issue body's own wording, said that removing these
labels' vectors "makes those queries return **zero rows**". **That is false, and a surface request
resting on it would be a request on a false premise.** Measured on the pinned
`falkordb-server:v4.20.6` with `:Object` carrying a `name` fulltext index (the inventory §12.1b records)
and one embedding, through the same `degradation_chain(entity_type="object", is_embedded=False)` path
the diagram above draws:

```text
WITH the vectors     : fts=2, vector=1                               → 3 rows
AFTER removing them  : fts=2, vector=0 (reason=no_embeddings)         → 2 rows
```

`classify_query` turns **every** leg on for a text query (`{"fts": True, "vector": True,
"structural": True}`), and `run_fts_query` serves `Object`/`Event`/`Subject` by
`entity_type.capitalize()` and `document` → `:Source`. The FTS leg therefore answers whenever the label
carries a fulltext index — and the DDL declares one on **all five** labels
(`projection._FULLTEXT_INDEX_LABEL_FIELDS`: `Point` `content`,`search_keys` · `Event` `subject`,`name` ·
`Subject` `name` · `Object` `name` · `Source` `_searchText` per `#3518`).

⚠️ **Two of §12.1b's rows disagree with that DDL, and the discrepancy is stated rather than smoothed
over.** §12.1b's 2026-09-24 inventory records `Source` FULLTEXT as **`—`** (and lists a `:Document`
label, which the D10 fold retired); the `Source → _searchText` declaration landed *after* that reading.
So for `:Object` / `:Event` / `:Subject` the lexical leg is corroborated by both, the measurement above
exercised `:Object`, and **for `:Source` / `document` the two disagree** — on a graph where §12.1b's
inventory still holds, those two types DO lose everything and the "zero rows" framing would be right for
them. That is a measurement to take on the graph a removal is actually proposed for, not an assumption;
and the census answers it **in the same run**: `indexes_on_label` reports every index kind the
catalogue holds for the label (`{"FULLTEXT": ["_searchText"]}`, `{"VECTOR": ["embedding"]}`, or
`none`), so a `:Source` carrying a fulltext index — or not — is printed beside the count it qualifies.
Read `FULLTEXT` on the label **and** on the field the FTS leg searches; §4's transcript shows the line
for `:Object`.

So after removal:

- a query that matched **only by meaning** — the semantic case the dense leg exists for — returns
  **zero** rows: that is the real loss;
- a query with **lexical** matches keeps answering from the FTS leg and RRF fusion, with the dense
  contribution gone — on every label the graph's own catalogue shows a fulltext index for;
- the structural leg is unaffected.

**The surface consequence is therefore: five `entity_type` values lose semantic recall** — including the
answers no lexical match could produce — **not the loss of retrieval.** Stated this way because the
weaker claim is the true one, and because the owner is being asked to rule on the true one.

---

## 4. The measurement taken here

**Population: a lane container.** It is a real FalkorDB (`v4.20.6`, the repo-pinned image), seeded with
**nine embeddings in total** at counts `2/2/3/1/1` over `Event`/`Object`/`Point`/`Source`/`Subject`,
written through the engine's own `vecf32`, with the `Point` vector index created through the product's own DDL form, plus the `:Object` `name` **fulltext** index §3.1's measurement uses (so the same transcript answers the FTS question too). **The counts
below are that population's, not production's** — they demonstrate the instrument and they establish the
*label/index* facts, which are properties of the engine and the product's index DDL rather than of a row
count. The production counts are §5's open item, and are **not** substituted with these.

The transcript below is the tool's **text mode**, pasted unedited — with one line ADDED at `#11337`'s head, since this section's run predates it: the `⛔ surface: UNMEASURED(declared bound: …)` line, which the surface block carries at this head because `volunteer_context` passes the leg by reference (it is conditional, not unconditional — a tree with no such shape publishes no such line, and the `declared bound:` marker is also what tells this entry apart from a **scan failure**). It is marked rather than silently woven in, because an "unedited" transcript that does not reproduce is worse than a dated one:

```text
$ uv run python tools/dod_5090_probe.py --census --uri <lane-uri> --graph census11088_demo
Census — graph `census11088_demo` at 2026-11-06T23:21:34+00:00 (CALL db.indexes()):
  command: uv run python tools/dod_5090_probe.py --census --uri docker://:pw@host:6379/<graph> --graph <graph>
  Event      stored=2            indexed=False  unindexed=2            payload=3072
             entity_type(s) served by this label: ['event'] — removing the UNINDEXED remainder changes query results: True
  Object     stored=2            indexed=False  unindexed=2            payload=3072
             entity_type(s) served by this label: ['object'] — removing the UNINDEXED remainder changes query results: True
             other indexes on this label: FULLTEXT(name)
  Point      stored=3            indexed=True   unindexed=0            payload=4608
             entity_type(s) served by this label: ['operator', 'point'] — removing the UNINDEXED remainder changes query results: False
  Source     stored=1            indexed=False  unindexed=1            payload=1536
             entity_type(s) served by this label: ['document', 'source'] — removing the UNINDEXED remainder changes query results: True
  Subject    stored=1            indexed=False  unindexed=1            payload=1536
             entity_type(s) served by this label: ['subject'] — removing the UNINDEXED remainder changes query results: True
  surface consequence — removal is REQUESTED, not performed; it needs the owner's approval (MCP/SDK surface):
    affected entity_type values: ['document', 'event', 'object', 'source', 'subject']
    ⛔ surface: UNMEASURED(declared bound: volunteer_context (tortoise_fts_query) — a leg-reaching function is taken by reference here, which this scan follows as a name-resolved call only; the caller list and its entity-type set may be short by that reach)
    path: tortoise_issue_insight (GET /v1/issue-insight, entity_type=point) / tortoise_query (MCP only (no REST route)) / tortoise_recall (MCP only (no REST route), entity_type=object|point) / tortoise_review_connections (MCP only (no REST route), entity_type=point) / tortoise_search (GET /v1/search) / tortoise_search_sessions (MCP only (no REST route), entity_type=event) / tortoise_suggest_entry_points (MCP only (no REST route), entity_type=point) → tortoise_fts_query(entity_type=…) → tortoise.search_engine.run_vector_query
    tools whose results change: tortoise_query (any (the caller chooses)) / tortoise_recall (['object']) / tortoise_search (any (the caller chooses)) / tortoise_search_sessions (['event'])
  population: CALL db.labels()
```

The two tools whose results change that a caller would **not** guess from the argument they pass are
`tortoise_recall` (default `mode="state"` reads `:Object`) and `tortoise_search_sessions` (`:Event`);
they appear because §3's third source reads the SDK's own call sites. `tortoise_issue_insight`,
`tortoise_review_connections` (which reaches the leg through `_review_pool`) and
`tortoise_suggest_entry_points` are entrants with `reaches_affected_labels: []` — they ask the leg for
`point` only, and `:Point` is indexed. On a graph with **nothing** removable, that column is `[]` for
every tool, including the caller-chosen ones: nothing changes, and the census does not say otherwise.

(`--json` emits the same numbers as the report's `census` object, with `labels`, `payload`,
`affected_entity_types`, `removal_changes_query_results`, `indexes_on_label` and `surface_consequence`
— it prints no table.)

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

**The last production reading on record is 2026-09-24** (`STORAGE-ARCHITECTURE.md` §12.1b): `:Object`
7,859 and `:Event` 4,798 stored-but-unindexed embeddings — **12,657 vectors**, cited by `#5090` and by
§12.1b/§12.1c as a dated prior. It is **not a current figure**, the tool's own docstring says it is not
to be re-quoted, and §4.1 above is the first reason why.

⚠️ **The reason is stated precisely, because the obvious version of it is wrong.** §12.1b's table is an
**index** inventory (`VECTOR field(s)`) and it says so — its own `:Source` footnote reads *"that is an
INDEX fact, not a claim about the property … what the label holds is measured by
`tools/dod_5090_probe.py --census`, never inferred from the inventory"*. So the dated reading never
claimed `Source`/`Subject` hold no vectors. What is stale is the **inference** a reader draws from
**§12.1's** additive legs (`Point ~31 MB + Object ~11.8 MB + Event ~7.2 MB ≈ the whole 33,580 / 51.6 MB`
— the legs are §12.1's table, and the 33,580 / 51.6 MB total they are reconciled against is §12.1a's
measured total, so the two sections are easy to confuse), which leaves nothing for the other two labels —
while both labels' writers DO store `embedding` (§4.1).
Treat the 12,657 as the shape of the answer, never as the answer.

---

## 6. Verification — every row with its command and its output

**Row 1 — the census writes nothing.** Guarded twice. `tests/test_dod_5090_probe.py`:
`test_the_census_reads_through_the_READ_ONLY_verb` (a read through `graph.query` fails the test) and
`test_the_LIVE_census_names_the_unindexed_labels_and_writes_NOTHING`, which snapshots the label set,
node count, edge count, the engine's index catalogue and every per-label embedding count **before and
after** the call and asserts they are identical.

⚠️ **The guarantee that FORBIDS a write is the read-only verb** — `GRAPH.RO_QUERY` is rejected
server-side for a write, and the fakes (`_NoWriteCensusGraph`, `_WriteVerbForbidden`) fail on any use of
`graph.query`. The live snapshot is a corroborating observation, not the proof: it cannot see a
same-shape property mutation (`SET n.embedding = vecf32($x)` on an existing node leaves every counter it
reads unchanged). Stated because a test docstring calling that "byte-identical" claims more than the
assertion measures.

```text
$ uv run python tools/test_lane.py run -- tests/test_dod_5090_probe.py -q
121 passed, 1 skipped in 42.50s   # the #11088 transcript this row was cut from
                                 # this tree: 183 collected
```

(115 collected before #11088's round 7; the file collects **183 in the tree this document sits in** —
nine added by #11337 over the then-122 baseline, nineteen by `#11444`'s writer-register falsifiers, one
by `#11471`'s GATED-writer bypass arm, fourteen by `#11492`, one by `#11437`, five by `#11747`
(which brought the cache projection into row 2's served-set comparison — the write path the source
discovery could not see, because its store clause interpolates the property name. Three of the five
close review findings on that change: the falsifier for the probe's DIRECT-WRITER door — the only
guard on a node the CALLER builds, which the probe had not measured — the pin that a record label
with no `KEY_PROPERTY` HOLDS the row instead of taking the whole report down with a `KeyError`, and
the falsifier for the PLANTED property set, without which reverting that derivation to the literal
`embedding` red no row at all), seven by `#11455` (all seven are row-1 shipped-default tests), and one by the round-9 fresh review ON `#11455`'s change
(`#11415`/`#11455` landed it; no sha is restated here, for the reason the paragraph
below gives about branch heads), which SCORES the `#11455` clause itself (`test_the_row_VERDICT_reads_the_DEPLOYMENT_not_the_any_surface_bool`
— reverting `on_by_default = deployment["on"]` to the any-surface bool left every other row-1 test
green, so the artefact `#11086` item 4 is ticked on was pinned nowhere), one by `#12284`
(`test_a_duplicate_literal_key_in_the_census_dicts_cannot_land_silently` — `#9314`'s missing
pytest-lane catcher for the two census dicts: a duplicated literal key in
`WRITER_CENSUS`/`CENSUS_EVIDENCE` collapses before any code can see it, so only `ruff`'s `F601`
caught `#11996`'s pair, post-merge), and three by `#12265`'s generalisation of the same pin
(`test_the_duplicate_key_detector_catches_a_duplicate`,
`test_no_module_declares_the_same_dict_key_twice` and
`test_the_scan_REPORTS_a_duplicate_it_finds` — the falsifier, the scan over `tortoise/`,
`tools/` and `tests/`, and the RED arm driven over a planted tree (a duplicate must be NAMED with
its file and both line numbers, an unparseable module must be named rather than skipped green, and
a clean module must contribute nothing) — and the scan's reach is deliberately not "repo-wide" and
asserts no file count (`battery/`, `graph-scripts/`, `docs/`, `eval/` and several smaller roots sit
outside the three it walks, and a file count there has already gone stale once), so the three make a
silently-shadowed declaration key a PYTEST red on any tree that holds both rows AND whose diff
selects this file, including the MERGE tree, where the duplicate is actually born and where
`main-lint.yml`'s ruff gate (`#2174`) only reds it after the fact; the scan also found the live
`# noqa: F601` duplicate in `tests/test_commit_schema.py`, which the census-scoped pin cannot see —
and which lint itself does NOT catch, because the suppression is on the repeated line)
(`122 + 9 + 19 + 1 + 14 + 1 + 5 + 7 + 1 + 1 + 3 = 183`). ⚠️ No figure here names what `main` collects TODAY:
which of those additions are already merged moves with `main`, so any "`main` at N" would be a moving
reference this paragraph forbids — the total is bound to THIS TREE alone): seven
row-2 resolution pins (register resolves every writer; the unserved-label falsifier; the
derived-label-wins arm; the runtime-label hold; the unreadable register holding as `UNMEASURED`; the
malformed-`stores` hold; the register's own `bypass` hold), one that holds the row on a census
`bypass` even when the register RESOLVES that writer, one that stops a declaration narrowing a
`PARTIAL` two-label MEASUREMENT, one that holds a declaration CONTRADICTING the writer's own Cypher,
one that pins the LIVE reader's `stores` plumbing (the product-register → reader seam every injected
fixture skipped), one that pins the `unexplained` note itself so a `PARTIAL` cannot name nobody,
and one that drives row 2's `ungated` candidate arm (whether a DECLARED `ungated` writer is scored
from its declaration or blamed as an undeclared one), one that drives the two-label **DERIVATION**
itself (every other `partial`-hold fixture injects the sentinel string, so nothing tied the
derivation to `PARTIAL_LABEL_PREFIX` — see the note below), and one added by `#11437` that drives
the residual pin's polarity as an executor over its own row set rather than a table in a message —
and the file reads 182 passed, 1 skipped on the docker lane at this head. The
skip is the `<3.12` re-derive. The three `main` reds this row recorded for a day are CLEARED:
`#12045`'s undeclared `tortoise.api::_emit` / `tortoise.api::_verify_record_vector_width` writers were
the width verifier and a docstring-quoted call, and `#12042` (`b367c54214a2`) CLASSED both
`CENSUS_EXEMPT` and declared them exempt in `tortoise/embed_writer_register.py` (they are still in
the population — `embedding_writers()` reports them as `unresolved_write` — but exemption is what
reading (b) accepts), so row 2 no longer HOLDS on them, and **no nodeid here reds on this tree**.
⛔ THAT IS A READING OF THIS TREE, NEVER OF `main`, and the sentence this replaces got it wrong
inside a day: it pinned `origin/main` `25127b61b61c`, a sha that PREDATES `#12113` (`18d43e0462`),
whose `#11858` door guard put an `embedding=` into `tortoise.sdk::create_source`'s refusal MESSAGE —
`_EMBED_WRITE` reads raw source, so that prose made the door an undeclared writer and three of
these nodeids red again at `origin/main` `edcac493a5` (measured there: `3 failed, 168 deselected`).
A branch sha is a moving tree — the same discipline the paragraph below already imposes on the
population COUNT — so the claim is bound to the tree it was measured on, and the change that
clears it carries the declaration.
⛔ The SPLIT is lane-bound, and the same tree reads **181 passed, 2 skipped** under
`TORTOISE_TEST_CARVE_OUT=1` with no `TORTOISE_DB_URI` — the second skip is
`tests/_live_utils.py`'s live-graph fixture, which only the docker lane satisfies. Both splits add
to the same `183` the pin re-derives, so **neither is a stale figure**: a carve-out run that
"corrects" 182/1 to 181/2 rewrites a true record into a false one, and the pin deliberately does
not cover the split for exactly that reason. ⚠️ The COUNT is bound to THIS TREE, never to a branch
head: a figure
stated as *"at #11337's head"* is stale against the same tree it sits in as soon as one more test is
added, and `test_the_census_doc_collects_what_the_file_collects` re-derives it from the file's own
`--collect-only` so the two cannot disagree.)

**Row 2 — an unreadable catalogue exits non-zero and does not report an empty population.**
Three routes, all `2`, none reporting a population. ⚠️ These are **excerpts**, not full transcripts:
each line quotes the `census` object (or the stderr line) that carries the refusal, not the whole JSON
report `--census --json` emits:

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
`surface_consequence`, and each tool's `reaches_affected_labels`),
`test_the_surface_named_is_the_one_that_can_pass_entity_type` (both forwarded-argument tools, the AST
union), `test_a_tool_whose_SDK_method_fixes_entity_type_is_named` (the fixed-type entrants and the
default they run), and the live test's assertions on a real engine — output in §4.

**Row 4 — `#10976` and `#7879` carried the "pending V1" qualification** (re-pointed 2026-11-10 — see the SUPERSEDED note below). By comment on each issue, so a
lane holding either issue's body is not collided with:

- `#10976` → [comment](https://github.com/daniel-ospina/tortoise/issues/10976#issuecomment-6695675706):
  (c)'s *"belongs in Postgres per §12.1c"* → *"whose location is **pending V1** (§12.1c `NOT DECIDED —
  owner's call`)"*. The eviction candidate itself is untouched: an index is rebuildable by
  construction, so `93 of the 195 MB` stays a candidate **whatever V1 decides**.
- `#7879` → [comment](https://github.com/daniel-ospina/tortoise/issues/7879#issuecomment-6695676215):
  *"vectors out"* → *"the vector layer — its census and surface request; where the vectors live is
  pending V1"*.

⚠️ **SUPERSEDED 2026-11-07.** §12.1c now reads *"✅ DECIDED 2026-11-07 — the vector index does NOT live in
FalkorDB"* (`docs/architecture/SEARCH-ARCHITECTURE.md`), so the *"pending V1"* premise of the two comments
above no longer holds: the location is decided (Postgres). ✅ **Both comments WERE re-pointed on 2026-11-10** — `#10976` →
[`6779662659`](https://github.com/daniel-ospina/tortoise/issues/10976#issuecomment-6779662659),
`#7879` → [`6779663836`](https://github.com/daniel-ospina/tortoise/issues/7879#issuecomment-6779663836) — so the
reconciliation `05-decompose.md` carried as OWED is discharged. The wording they now carry is
"the vector layer — its census and surface request; its location is decided and the WORK is #4051's", not
"pending V1". The sentence below is the state those comments were qualified against, kept
as the record of it and **not** as current text:

> **⚠️ NOT DECIDED — owner's call.** It is an open decision (§14.3 V1), because it changes what the graph
> holds.

---

## 7. The request this evidence supports — and everything it does not do

> ✅ **ANSWERED 2026-11-10 — this section is the RECORD of the request, not a live ask.** The owner ruled
> **KEEP**: the `:Object`/`:Subject` embeddings stay, no pass may remove them or stop writing them, and
> because **nothing contracts** no surface approval follows (`#11395` comment `6781921075`, mirrored into
> `STORAGE-ARCHITECTURE.md` §14.3 V3). ⚠️ **The ruling names those two labels and no others**, so the
> request below for `:Event` and `:Source` is **not** answered by it — the 2026-09-24 direction (*"keep the
> vectors, make them searchable"*) is the record that covers them, and it authorises no removal either.
> ⚠️ **The census itself is a dated measurement and is deliberately NOT
> edited to match** — what follows is what was requested before the ruling existed.

**Requested:** the owner's decision on whether to **remove** the `embedding` property from the
unindexed labels (`:Event`, `:Object`, `:Source`, `:Subject`). The affected surface is the **four tools
whose results the census measures as changing** — `tortoise_search` and `tortoise_query` (for
`entity_type ∈ {event, object, document, source, subject}`), `tortoise_recall` (its default
`mode="state"` read, `:Object`) and `tortoise_search_sessions` (`:Event`), plus `GET /v1/search` —
and what they lose is **semantic recall**: a query that matched only by meaning
returns zero rows afterwards, while a lexically-matching query keeps answering from the FTS leg (§3.1
is the measurement, and the reason it is stated this way rather than as "zero rows"). `tortoise_recall`
and `tortoise_search_sessions` are in this list because §3's third source reads the **SDK's** call
sites; a caller would not guess them from any argument it passes. ⚠️ §3.1 also
records where the record DISAGREES: on a graph whose `:Source` fulltext index matches §12.1b's dated
inventory rather than the DDL declaration, `document`/`source` would lose everything — so the decision
should be taken on a census of the graph it applies to (the same run answers it:
`indexes_on_label`). That is an MCP/SDK surface change, and no lane
may take it.

**⚠️ The cost argument is GONE, in both directions — do not resurrect it.** The 2026-10-08 billing
ruling makes tiers capped and priced per feature with **retained bytes as revenue**, so removing these
bytes moves the bill by **$0.00**. An earlier draft of `#11088` attributed *"the customer is not paying
for a thing they can never retrieve"* to that ruling; that sentence appears in no recorded source and
was withdrawn. **No recorded decision authorises this removal, and none forbids the census.**

**Not done, deliberately:**

- no vector index is created for any label **on any product graph** — that **is** decision V1 (§12.1c / §14.3, `#4333`). (The live test creates one on its own throwaway lane graph and deletes the graph in a `finally`; that is a test fixture, not a graph anyone queries.)
- no vector is removed, and this tool has no flag that could;
- no MCP tool and no public SDK method is added, removed or renamed;
- no cost model is re-derived (§16.1 / §17.4 record it), no placement is re-decided (§14.3), no
  quantization is proposed (§15's do-not-re-invent list), and self-hosting is not revisited (D11 +
  owner, 2026-11-05).

Refs `#5090` · `#4997` · `#10976` · `#7879` · `#4333` · `#10465` · `#10541`
