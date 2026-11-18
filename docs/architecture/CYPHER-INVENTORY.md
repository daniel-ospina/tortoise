---
title: "The Cypher surface, inventoried by shape — and a differential harness that can fail (#13098)"
type: architecture
domain: platform
doc_status: draft
created: 2026-11-17
updated: 2026-11-17
issue: "#13098"
subjects.team: epistemic-team
aboutSubjects: Cypher surface, FalkorDB, Postgres record, differential testing, Cypher→Postgres migration
aboutObjects: SEARCH-ARCHITECTURE.md §3 / §6.2 / §6.3, #13082, #13083, #13099, #12458, #7879, #7970, #12894
---

# The Cypher surface, inventoried by shape (#13098)

**Issue:** [#13098](https://github.com/daniel-ospina/tortoise/issues/13098) · **Roadmap:** `#13082`
phase 1 · **Tools:** `tools/cypher_inventory.py` (the census) and `tools/cypher_diff_harness.py`
(the differential harness) · **Companion lane:** `#13083` classifies **shapes** by their readers;
this document classifies **statements**.

---

## 0. The numbers, first

| what | count |
|---|---|
| product surface (`tortoise/**/*.py`, test modules excluded) | **299** files scanned |
| files carrying at least one statement site | **100** |
| **statement sites (this census)** | **1314** |
| of those, shape **`plain`** — no traversal, no adjacency, no fulltext, no EP | **860** (579 read, 281 write) |
| **`plain` READS — the migration candidates** | **579** |
| `adjacency` (edge pattern, fixed length) | 292 |
| `ep` (the epistemic scalars) | 32 |
| `traversal` (variable length) | 3 |
| `fulltext` | 2 |
| `ddl` | 17 |
| `unresolved` (statement not statically resolvable) | 108 |
| non-Cypher door calls (excluded from every shape total) | 259 |
| **AST door calls** = census + non-Cypher | **1573** |

> **The finding, with its count: 579 of 1314 product statements (44.1 %) need none of the
> four graph-only capabilities.** They are lookups, listings, filters, aggregates and ordered reads —
> and they are served from a store that costs ~584× as much per GB as the record (`#13082`). That is
> not an adjective; it is 579 statements.

---

## 1. Why the count had to be re-measured

`#13098`'s body cites **3,015 Cypher statements across 123 files** from "the archaeology". Four
figures for this surface were already published, and
`docs/epics/2026-10-10-7879-postgres-record/01-test-design.md:71` records that the first two **must
not be compared or summed** because their units differ:

| source | figure | unit | method |
|---|---|---|---|
| `#7879` / `#7970` | 1,074 sites / 57 files (`697` anchored + `377` whole-graph) | read sites | **unpublished** |
| re-derivation attempt | 1,525 literals / 89 files | string literals | different unit, explicitly not comparable |
| `#12894` D-01 | 458 / 50 files | mutating `.query(` sites | one regex over source |
| `#13082` / `#13098` body | 3,015 / 123 files | "Cypher statements" | **no published method** |

**None of the four is reproducible from this tree, and 3,015 does not reproduce from any predicate in
`tools/cypher_inventory.py`.** The two numbers this tool can produce, each with a method that is in
the box, are:

| mode | statement sites | files | note |
|---|---|---|---|
| product surface (this census) | **1314** | **100** | `tortoise/**/*.py`, test modules excluded |
| every tracked surface (`--all-surface`) | **8,951** | **826** | tests, `graph-scripts/`, `archive/`, `eval/`, `docs/`. A dated measurement (at THIS merged head), NOT a pin, and deliberately so: this population includes `tests/`, so it moves on any test edit and a pin on it would red an unrelated PR for a number this document does not rest on (`#13098` review P2 — it has read 8,791 / 8,908 / 8,935 / 8,939 / 8,950 / 8,964 / 8,979 / 9,008 / 8,994 / 9,055 / 9,058 here, none of which but the last reproduces at this tree; the number beside this row names the tree it does) |

3,015 sits between them, so it is not a rounding error on either population — it has no predicate
here. **This document therefore does not adopt it, and does not reconcile TO it.** It replaces it
with a count that is regenerated and *pinned* (§7), so the next lane does not have to guess which of
five numbers is the surface.

### The census method, in one paragraph

`tools/cypher_inventory.py` walks `git ls-files` (the index — never a recursive walk), parses each
file's AST, and finds every call to a query door (`query` / `ro_query` / `execute`). The statement
text is resolved statically through module and function constants, `+`-concatenation, `%`-formatting
and f-strings; an interpolation or a local binding it cannot fold is kept as `{}` / `???` and the
site is marked **partial** — never dropped. A door call whose text resolves to nothing is counted as
**unresolved** (the over-report polarity `#9124` established: "not provably read-only is write"
becomes here "not provably resolvable is unresolved"). Door calls whose statement is not Cypher at
all — a Postgres `execute`, a driver call — are counted as **non-Cypher** and excluded from every
shape total, so a total cannot be inflated by them.

### The reconciliation, measured (not inferred)

The predicates, stated exactly as `rg_totals` runs them — a shell `rg` reproduces NEITHER number, for
two reasons given below the block, so the block does not pretend it does:

```text
population: every tracked `tortoise/**/*.py` except `tortoise/test_*.py`
            (the 299 files the tool reports as `py_files`), scanned as WHOLE TEXT

door predicate   : re.compile(r"\.(?:ro_)?(?:query|execute)\s*\()")
verb predicate   : re.compile(r"\b(?:MATCH|MERGE)\s*\(", re.IGNORECASE)

1,627 occurrences of the door predicate across 109 files
2,346 occurrences of the verb predicate across 145 files   (upper-bound shape, see below)

1,627  regex door-pattern occurrences
  −54  occurrences on a line carrying NO door call (comments and docstrings)
= 1,573  occurrences on a line carrying a door CALL          [measured]
  − 0   of those, the AST did not record (residual)
= 1,573  AST door calls
= 1314  classified Cypher statements + 259 non-Cypher door calls
```

⛔ **The predicates, stated exactly as `rg_totals` runs them — and where a shell `rg` diverges.**
The population is every TRACKED `tortoise/**/*.py` except `tortoise/test_*.py` (299 files; the tool
prints it as `py_files`), scanned as WHOLE TEXT — so `\s` spans a newline, which a line-based `rg`
cannot do. Measured on this tree:

* `rg -o '\.(ro_)?(query|execute)\s*\(' <those 299 files>` → **1,627** across **109** files: exactly the
tool's number. A bare `rg … tortoise/` gives 1,633 / 110, the 6 extra occurrences living in
`tortoise/test_*.py`; `--glob '!tortoise/test_*.py'` reproduces 1,627 / 109 on this tree, because
every excluded test module here is TOP LEVEL, so the glob's scope and `TEST_MODULE`'s scope coincide
— they would diverge if a nested `tortoise/**/test_*.py` ever appeared.
* `rg -io '\b(MATCH|MERGE)\s*\(' <those 299 files>` → **2,333** across **144** files, while the tool
reports 2,346 / 145. The 13-occurrence, one-file gap is the WHOLE-TEXT class — and ⚠️ it is **not**
string concatenation, which this paragraph claimed until now: all 13 CROSS-LINE matches (a line that
ends on the verb whose next line opens the parenthesis) are a comment, docstring or wrapped Cypher
example — **ten** are wrapped English sentences / docstring prose (e.g. `oauth.py`'s “limits match” /
“(#329): preserve None”, and `record_projection.py`'s “an INLINE property MATCH / (`(s:Session
{id:$sid})`)”, whose parenthesis OPENS a parenthetical rather than continuing a statement) and
**three** are wrapped Cypher EXAMPLES (`projection/__init__.py`, `record_projection.py`,
`vector_write.py`), where the predicate's `(` is the next clause of the SAME statement. Enumerated,
not inferred — and ⛔ **BY COMMAND, never by
citing `file:line` in this paragraph**: a line number written here rots on the next main-move (this
sentence carried one that a merge shifted, and a reader sent to it finds prose with no `MATCH (` in
it). Re-enumerate at your head by keeping only whole-text matches whose span contains a newline. That
predicate over the same 299 files finds exactly 13 — the verb-at-end-of-line scan alone finds **178**,
and the number is not a second version of the 13: **13** of those lines are the cross-line matches just
enumerated, and the other **165** are a COMMENT or docstring line that simply ENDS on the word (“any
failure on the read/parse/merge”, “first match”, “the old name-keyed MERGE”, …) with nothing following,
which the case-insensitive predicate counts and which is why the predicate has to be stated
the predicate has to be stated — and none of the 13 is an executable Cypher literal split by implicit
concatenation (`"… MATCH " "("` occurs nowhere in this tree): the break is prose or a wrapped Cypher
example INSIDE a docstring, which is why the tool's verb total is an upper bound.
(A bare case-sensitive `rg` gives 2,066, and `-i` without the exclusion 2,405 — ⛔ both over
`tortoise/` with **no type filter**, which is not the population this row is about and is why the
number beside it was un-reproducible before `#13098`'s review asked for the predicate. Over the 299
`.py` files the same two predicates read **2,063** and **2,333**; with `-t py` and the test-module
exclusion dropped they read **2,065** and **2,336**. The 69-occurrence 2,336→2,405 gap is entirely
`*.ts` / `*.mjs` / `*.sh` — enumerated, it is `tortoise/pi-hooks/tortoise-volunteer.test.ts` 49,
`tortoise/pi-hooks/tortoise-capture.test.ts` 14, `tortoise/pi-hooks/store-sync.test.mjs` 3,
`tortoise/pi-hooks/tortoise-capture.ts` 1, `tortoise/openclaw-plugin/index.test.mjs` 1,
`tortoise/claude-hooks/volunteer-turn.sh` 1 — so a bare `rg -o` that reads as one number is THREE, and the population decides which.)

The first three rows of the block are measured by the tool (`--json` → `rg_totals`); the **−54** and
the **= 1,573 on a door-call line** are both counts, not differences.⛔ The **residual** row is
the only derived one — it is the gap between the regex's own line-based count and the AST's node
count, printed so a reader can see the arithmetic close, and a NON-zero there is the signal that the
two measurements have stopped agreeing. It is also the value the first version of this tool silently
absorbed: the review caught that version closing over a 52-statement undercount. The row is now
falsifiable rather than decorative — `rg_totals` carries `lines_where_regex_and_ast_disagree`, a
PER-LINE cross-check the totals structurally cannot express (a balanced pair of opposite errors
leaves every total above closing perfectly), pinned at 0 by the census suite. An earlier revision of
this paragraph called the residual "a measurement, not a convenience", which reversed the row it
names (`#13098` review P3). The
`MATCH|MERGE` figure is **not** a statement count and is printed only because a reader will run it:
one statement can contain two node patterns, and a statement built with `UNWIND` contains none, so it
is an upper-bound shape, never a census.

---

## 2. The shape, and the verdict that follows from it

Shape is decided by **what the statement needs**, in this precedence (first match wins — the order is
the point, because a statement that needs a walk also matches "plain"):

| shape | test | needs | verdict |
|---|---|---|---|
| `ddl` | `CREATE/DROP INDEX\|CONSTRAINT`, `CALL db.indexes` | graph maintenance | **stays** |
| `fulltext` | `db.idx.fulltext.*` | the in-graph text index | **stays** — `SEARCH-ARCHITECTURE.md` §6.2; `#13099` measured the gate and the decision stands |
| `vector` | `db.idx.vector.*` | ANN over embeddings | **moved** — §3's one decided move (`#11642`); a leftover is an exception |
| `traversal` | `-[…*…]-`, `shortestPath` | a variable-length walk | **stays** — §3 |
| `adjacency` | an edge pattern, fixed length | pattern match over adjacency | **measure** — `#13098` calls it "case by case"; `#13083` owns placement |
| `ep` | `ep_*`, `posterior_*`, `baseline_*`, `confidence` | EP state (arithmetic in Python) | **stays** — §6.3's carve-out |
| `plain` | none of the above | nothing graph-shaped | **candidate** (read) / **stays** (write) |

⛔ **`ddl` is FIRST, and the order matters as much as the tests.** A statement that CREATES a
fulltext or vector index names one (`CALL db.idx.fulltext.createNodeIndex(…)`), so the DDL arm has to
claim it before the text-index arms see the name; a `queryNodes` QUERY matches no DDL arm and falls
through to `fulltext`. This table previously listed `fulltext`/`vector` ahead of `ddl`, which was the
reverse of `classify()` and of what `test_shape_precedence` pins — a doc that named the wrong
precedence while the code did the right thing (`#13098` review F4).

**Role** separates a read from a write, because the verdict is shape × role. A `plain` **write**
maintains the derived copy: it stays until `#13083` makes that shape `record_only`, at which point the
write disappears with the node it maintained — moving it to SQL is not the same operation.

**Role is decided by the presence of a write clause ANYWHERE in the statement**, not by a line-start
keyword: `WRITE_VERB` handles the leading form and `WRITE_INLINE` the inline one, and the inline arm
covers every spelling a real statement uses — `SET n.x = …`, `SET n += …`, `SET n = …`, `SET n:Label`,
the folded `SET {}` / `SET ???` that a concatenated literal leaves behind, and an inline
`MERGE (…)` / `CREATE (…)` / `DELETE` / `REMOVE` / `DETACH DELETE`. ⛔ The first version of that arm
matched only the dotted form, so `MATCH (n:Point {id:$id}) SET n += $props` and every inline `MERGE`
read as a **read** — i.e. a graph write published as a migration candidate, the one direction the
verdict flips. It inflated this table's `plain` read count by 20 and the `adjacency` read count by 68
(`#13098` review P2; `test_an_inline_write_clause_is_a_write_not_a_candidate` pins each spelling, and
`test_a_read_clause_is_still_a_read` pins that the widened arm does not swallow plain reads).

**The verdict function cites §3 and §6.2/§6.3 rather than re-deciding them.** Nothing in this
inventory proposes moving a shape the recorded decision keeps. Where the inventory *suggests*
something §3 keeps could leave (`adjacency`), the verdict is `measure`, and the route is `#13083`'s
placement table — or, for a shape §3 names explicitly, a reopen argued on `#4051`. Not this doc.

---

## 3. Counts by shape × role

| shape | total | roles |
|---|---|---|
| `plain` | **860** | read=579, write=281 |
| `adjacency` | **292** | read=192, write=100 |
| `ep` | **32** | read=21, write=11 |
| `ddl` | **17** | ddl=17 |
| `traversal` | **3** | read=3 |
| `fulltext` | **2** | read=2 |
| `unresolved` | **108** | unresolved=108 |

**What the unresolved bucket is.** 108 sites hand a door a statement built where no static reader
can follow: a **parameter** (`def _count(proj, query: str)` in `tortoise/audit.py:247`), a
**call-built** string (`proj.g.query(cypher, …)` where `cypher` is an argument), a helper's return
value, or — since `#13098` review P1 — a `CALL` whose procedure NAME the text cannot prove
(`CALL {}('Point')` where the name was folded out of a loop variable, and `CALL apoc.…` where
read-vs-write is the plugin's business). The last arm exists because two of these WERE published as
migration candidates: `tortoise/projection/__init__.py` loops over `("db.idx.fulltext.drop",
"db.idx.fulltext.dropIndex")`, the resolver folded the name, and a fulltext-index DROP was counted
as a `plain` READ — the one direction a verdict flips. Both of its un-folded spellings classify
`ddl`, which is what showed the loss was in the folding and not in the DDL arm. They are not
"probably fine" — they are exactly the sites a flip would have to re-derive by
hand, so they are counted and named rather than classified by guesswork. 315 further sites
are **partial**: the literal half carries the shape (e.g.
`"MATCH (op:Point)-[:" + safe_rel + "]->(t)"` is an adjacency read) and the digest shows `{}`/`???`
where the folded part was.

**Where they live** — the top files, which is also a flip's blast radius:

| sites | file |
|---|---|
| 384 | `tortoise/sdk.py` |
| 240 | `tortoise/hosted_api.py` |
| 92 | `tortoise/projection/__init__.py` |
| 54 | `tortoise/projection/entities.py` |
| 42 | `tortoise/projection/edges.py` |
| 32 | `tortoise/hosted_backup.py` |
| 25 | `tortoise/search_engine.py` |
| 24 | `tortoise/metering.py` |
| 21 | `tortoise/mcp_server.py` |
| 16 | `tortoise/commit_ops.py` |
| 16 | `tortoise/mining.py` |
| 15 | `tortoise/event_store.py` |

---

## 4. The flip order: frequency × simplicity

The order a campaign should take, derived from §3 rather than chosen:

| # | class | why here | size |
|---|---|---|---|
| 1 | `plain` reads already served by `record_serve` (`point-by-id`, `node-by-key`) | simplest shape (one node, one key) **and** the seam serving it already shipped with a parity rule | 2 reads, measured in §5 |
| 2 | `plain` reads by id-list (`WHERE n.id IN $ids`) | same simplicity, one predicate more; needs an IN-list corpus | a large share of the 579 |
| 3 | `plain` aggregates / counts | no traversal, but a fan-out difference is easy to get wrong → needs a larger corpus and a variance check | a subset of the 579 |
| 4 | `plain` writes | **not a Cypher→SQL move** — these disappear with the node when `#13083` makes the shape `record_only` | 281 |
| — | `adjacency` / `traversal` / `ep` / `fulltext` | **do not flip without a reopen on `#4051`** | 329 |

### The first candidate, measured

`point-by-id` — the anchored read behind `GET /v1/points/{id}`, already enumerated in
`record_serve.ANCHORED_READS` as **served**. Corpus: the shared edge corpus (a null property, a null
in an ORDER BY, unicode + pipe + trailing space, a retired row, an operator row, an empty tenant) in
**both** stores; 2,000 rows for the timing arm.

| arm | p50 | p95 |
|---|---|---|
| Cypher (`MATCH (n:Point {id:$id}) RETURN …`) | **5.14 ms** | 8.02 ms |
| SQL, **cold connect** per call (what `record_serve` does today) | **14.26 ms** | 16.60 ms |
| SQL, **pooled** connection (`#9558`'s state) | **0.95 ms** | 1.79 ms |

**Verdict: equal, and faster — behind the pooled connect only.** The diff is **empty** (§5) and the
pooled SQL arm is **5.4× faster** than the Cypher form. ⛔ The cold-connect arm is **2.8× SLOWER**
than Cypher, so *the same flip served on a cold connection would be a latency regression*: the win is
`record_serve` + the pooled `_default_connect`, not SQL by itself. And the SQL form is nowhere near
the cold-restore penalty (`0.03 s` index-dropped / `1.585 s` index-kept) — **0.95 ms is ~32× under
the cheaper penalty**, so a restore never pays this back.

`point-listing` (filter + order + limit) measured the same way: Cypher **5.27 ms**, SQL pooled
**1.85 ms**, cold **15.40 ms** — the same shape of answer, and the same NULL-ordering trap (§5).

**So the first flip is proposed, not taken, and it is a one-statement flip behind an existing seam.**
It is not in this PR: the harness authorises *this candidate*, and the flip should land with the
graph-side implementation deleted in the same change (`#13098` §3), which is a caller-visible decision
at `record_serve`'s `MODE_PREFER` arming — a placement decision `#13083` owns.

---

## 5. The differential harness — and the proof it can fail

`tools/cypher_diff_harness.py` runs a candidate's **Cypher** form and its **SQL** form against **the
same tenant data** in **both** stores, then diffs **rows · order · null handling · row shape**. It
prints the graph lane and the record DSN it used, and its exit codes are the verdict:

| exit | meaning |
|---|---|
| **0** | the diff is EMPTY — the two forms are the same answer |
| **1** | the diff is NON-EMPTY — every difference named by kind |
| **2** | **could not run** (no lane, no DSN, a missing corpus edge, an inapplicable mutation, no `psql`, a record schema that never built) — never a pass |
| **3** | **no verdict produced** — the invocation did not diff the two forms (`--bench` measures, `--list` lists). Never a pass either |

⛔ **`3` exists because the alternative was a lie.** `--bench` used to `return 0` — this tool's word
for *the two forms are the same answer* — before the corpus-edge check and without ever calling
`diff()`. A caller (or a future CI step) keying on `0` read a **timing run as agreement**, and a
corpus missing a declared edge was measured instead of refused. Both are the same defect class the
module header argues against ("a harness that cannot run must not read as agreement"), one branch
over (`#13098` review).

### The comparison rule, and why each half is there

* **Rows.** Count first (a dropped row is not a value difference), then per-value.
* **Order** — compared **only when the statement declares an order** (the candidate's `ordered` flag,
  never inferred from what the engines returned). An unordered statement is compared as a multiset;
  asserting a sequence there would red on a legitimate planner difference.
* **Null handling, three-way.** A property absent on a node and a column that is SQL NULL are the same
  answer; `''`, `0`, `false` and `'None'` are each **different** from it. This is not pedantry — it is
  the defect class this candidate actually has:
  > **MEASURED.** `ORDER BY n.created_at DESC` on the graph places a NULL `created_at` **FIRST**, and
  > Postgres' own default for `DESC` is also **NULLS FIRST**, so the two forms agree — *until* a
  > hand-port adds the well-intentioned `NULLS LAST`, which reverses the ROW ORDER while every value
  > still matches. A value-only diff cannot see that; this one reports `order` and names the rows.
* **Row shape** — the declared column list, compared name by name, so an extra, missing or renamed
  column is a difference even when every value matches.
* **Types** — a value's *family* must match (`number`/`str`/`bool`/`list`/`dict`/`null`);
  `Decimal` and `float` compare numerically inside the `number` family. A family change (int↔str,
  bool↔int) is a difference — that is what silent coercion looks like.

### The two runs (both required — a harness that never failed is not proven)

Run against a **real FalkorDB lane** (`tools/test_lane.py`, `docker://…:16407`) and a **real record
DSN** — Postgres 16 with the product's own `supabase/migrations/*.sql` applied
(`--bootstrap-record`), i.e. the real `public.point` / `public.node_identity` / `public.graphs`
schema, not a stub table:

| run | candidate | result |
|---|---|---|
| agreement | `point-by-id` | **diff EMPTY**, exit **0** |
| agreement | `point-listing` | **diff EMPTY**, exit **0** |
| agreement | `point-by-absent-id` | **diff EMPTY**, exit **0** — the EMPTY result: 0 rows on both sides, columns still compared |
| mismatch | `point-listing --mutate null-order` | **1 difference** (`kind: order` — the row set matches, the sequence does not), exit **1** |
| mismatch | `point-by-id --mutate drop-null` | **1 difference** (`row-count 1 vs 0`), exit **1** |
| refusal | `point-by-id --mutate null-order` | exit **2** — the mutation cannot apply, so it is refused rather than reported as a clean run |
| refusal | no `--graph-uri` / `--record-dsn` | exit **2** |
| no verdict | `--bench 2000` | exit **3** — a measurement is not an agreement verdict |

The corpus is an argument, not a convenience: every candidate **declares** the edge cases it cares
about (`requires`), and the harness reads the **seeded stores** back to confirm each one is present in
both — a pin that reads its own declaration cannot fail for the thing it names (`#11859`). The
EMPTY-result edge is the exception that proves the rule and is stated rather than implied: no `requires`
entry can assert an *absence*, so it is a **candidate** (`point-by-absent-id`) whose id is in neither
store, and the pin that keeps it honest is a test asserting that id stays out of the shared corpus. An
earlier cut of this doc described an `org-other` empty-**tenant** query that no candidate performed —
the edge was declared and never compared (`#13098` review F2).

**Bootstrapping the record (`--bootstrap-record`).** Supabase owns the `auth` schema and the
`anon`/`authenticated`/`service_role` roles, and the product's migrations reference all four. On a
bare `pgvector/pgvector:pg16` those references abort the chain that creates `public.node_identity`,
so `public.point` never materialises and every seed refuses; the harness therefore creates that
minimal surface first. **MEASURED** (2026-11-17, 85 committed migrations): without it **69 files
fail** and there is no `public.point`; with it **84/85 apply** and the record schema is complete (the
one remaining failure is `blog_cms.sql`, which needs Supabase Storage's `storage.buckets` and is not
part of the record). A failed file is reported, and the run is exit **2** only if `public.point` is
absent afterwards — a partial apply that builds the record is a pass, and `psql` not being on `PATH`
is `2` ("could not run"), never `1` ("the two forms differ").

⚠️ **What the harness does NOT prove**, stated rather than implied: it compares one candidate's own
two forms over one tenant's corpus. It does not compare against the *production* graph, it does not
measure retrieval quality (that is `#13099`'s instrument), and it cannot detect a **shared** defect —
if both forms answer the same wrong thing, the diff is empty and says so honestly.

---

## 6. What this lane does not decide

* **No flip is taken here.** The first candidate is *proposed with a measurement* (§4) and the harness
  authorises it; landing it deletes the Cypher form in the same PR, which is a caller-visible change
  owned by `#13083`'s placement table.
* **No shape §3 keeps is proposed for a move.** `traversal` (3), `ep` (32), `fulltext`
  (2) are `stays`, each citing its section. `adjacency` (292) is `measure`, not `move`.
* The text index is **not** re-decided: `#13099` measured the gate and §6.2's STATUS note records that
  the decision stands; adopting from that measurement is a reopen on `#4051`, not a lane's move.
* `cache_projection.py` and `cache_tier` are untouched — `#13083` owns them.

---

## 7. The pins (so this doc cannot drift silently)

`uv run python tools/cypher_inventory.py --check` re-runs the census and compares it with the counts
below; it exits **1** on drift (including a pin the tool no longer computes). That is what makes this
a census rather than a snapshot of one afternoon.

⛔ **THE PINS ARE ABSOLUTE COUNTS OF `tortoise/**`, SO THEY MOVE WITH THE BASE — and the refresh is
part of the PR that moves them.** A `pull_request` job checks out the **merge** commit, so a pin green
at this branch's own head can be red at the merge the moment `main` adds or removes a single
statement: this very PR was red that way (a branch green at `1295` measured `1301` on the merge, six
statements having landed on `main` after its merge-base). The procedure is not optional and has two
halves:

1. **Integrate before you measure** — `git fetch origin main && git merge origin/main`, then re-cut
   BOTH the pins below and Appendix A (`uv run python tools/cypher_inventory.py --markdown`), and
   re-check the prose tables in §0/§1/§3/§4, which quote the same numbers.
2. **Re-run `--check`** and commit the regenerated doc in the same PR that added or removed the
   statement. A product PR that adds a query owns the one-line pin update; the alternative (a census
   that only counts its own diff) is a snapshot wearing a census's name.
3. ⛔ **`git add` the merge's resolutions BEFORE the census runs — an unresolved path is listed once
   PER STAGE.** `git ls-files` (what `surface_files` walks) emits stages 1, 2 and 3 of a conflicted
   path, so a cut taken mid-merge reads one file as three and its statement sites TRIPLE: measured
   on `#13466`'s third `origin/main` integration, `tortoise/indexer/github_indexer.py` reported 33
   sites instead of 11 and `sites` read a phantom **1336** against the true **1314** — the exact +22
   that a stale pin carried until it was re-cut. `--check` cannot see it (the pins and the appendix
   agree with each other, both being the same wrong cut), so the trap is silent; `main`'s
   `42ffce6cc2` records the same failure from the other side. Resolve, `git add`, THEN measure.

**This regeneration's base:** `origin/main` **`340d500491`** — the base this branch last merged
before the one it landed on, **`6264dc670c`**. `main` inserted nothing under `tortoise/` between those
two commits (`git diff --name-only 340d500491 6264dc670c -- tortoise/` → empty), so the census is
IDENTICAL at both trees: a fresh `--markdown` at either prints the same 1,313 rows. ⛔ The base is
named as a PAIR of commits and never as `… origin/main`: a sentence about the live remote tip is
false the moment the next PR lands — this one included, which is how the revision of this paragraph
that said `git diff --stat 340d500491 origin/main -- tortoise/` was empty went stale within minutes
of its own merge (`#13348`'s post-merge review). For the same reason **every row figure in this
section is bound to the commits it compares, never to "this tree"** — re-measure at the head you are
reading before quoting one.
**No census SCALAR moved**: `--check` reports **`25 pinned counts match`**, and §1's `rg`
reconciliation re-measured at the base it landed on reproduces it exactly (door **1,625 / 108**, verb
**2,343 / 144** whole-text, bare **1,631 / 109**), so every scalar below is the `9d13dc1bf7` cut's,
carried through unchanged. **Appendix A moved anyway — 226 of its 1,313 rows were stale**
(`340d500491`'s committed table against a fresh cut at `340d500491`'s OWN tree), across
three files, and **all 226 come from ONE commit**: `main`'s **`c51afee5a8`** (`#13392`), which
inserted lines above hundreds of statement sites. Each file's shift is one hunk, read off the
commit rather than inferred:

| file | stale rows | shift | the single hunk that moved them |
|---|---|---|---|
| `tortoise/hosted_api.py` | 220 | all **+22** | `@@ -14402,16 +14402,38 @@` (16 → 38 lines) |
| `tortoise/sdk.py` | 4 | all **+17** | `@@ -59275,9 +59275,26 @@` (9 → 26 lines) |
| `tortoise/cache_projection.py` | 2 | all **+3** | `@@ -719,9 +719,12 @@` (9 → 12 lines) |

Regenerated here with `uv run python tools/cypher_inventory.py --markdown` at this tree. That is the
`file:line`-versus-SCALAR gap this section describes, reached from the other side: the pins were
GREEN over the stale appendix, and only re-running the generator shows it.

**This PR's cut:** `origin/main` **`48585c94ff`**, forward-merged into `fix/13302-ended-and-how`
(PR #13416) — re-derived by RUNNING the tool on the final merged tree, never by arithmetic on the
markers. This PR adds **one** Cypher statement site and **no** non-Cypher door call:
`tortoise/ended.py` (#13302, the ended-scope read) carries `build_cypher`'s ONE enumeration statement,
whose f-string interpolation the resolver cannot fold, so the site classifies as `unresolved` — which
is why `unresolved` moves WITH `sites` while `plain`/`candidates` do not. On the merged tree: `sites`
1313→**1314**, `files_scanned` 297→**298**, `files_with_sites` 99→**100**, `unresolved` 107→**108**,
`ast_door_calls` 1571→**1572**, `non_cypher_door_calls` **258** (unchanged), `plain` **860** (read
**579** / write **281**, unchanged), `candidates` **579**, `partial` **315**. §1's `rg` reconciliation
re-measured here (door **1,626 / 109**, verb **2,346 / 145** whole-text, bare **1,632 / 110**), and
Appendix A is regenerated with `--markdown` — ⛔ **it moved far more than the one new row**, because
this branch's `tortoise/sdk.py` insertion (the `ended_scope` parameter and its refusal block, ~117
lines above the read) shifts every `sdk.py` row below it; that is the same `file:line`-versus-SCALAR
gap the paragraph above describes, from the third side. ⚠️ The paragraph above also says `main` has not
touched `tortoise/` since `340d500491`; that was true of ITS base, and `main`'s `48585c94ff` (`#13348`)
now does touch `tortoise/indexer/github_indexer.py` — measured, it moves **no** census scalar
(`non_cypher_door_calls` stays 258), so this cut's numbers are unaffected. `--check` reports **`25
pinned counts match`**.

**This re-cut's base:** `origin/main` **`ae54df166d`**, merged here — re-derived by RUNNING the tool
on the final merged tree, never by arithmetic on the markers. **No census SCALAR moved** (`--check`
reports **`25 pinned counts match`**), and **Appendix A moved by exactly ONE row**: this test's
appendix arm reddened on the merge over
`tortoise/monitoring.py:4670` → the tool measuring `4787` (**+117**). All of it comes from ONE commit,
`main`'s **`b8723c0f16`** (`#13430`), which added **132** lines to that file, **117** of them above the
single site (two hunks — `@@ -806,6 +806,89 @@` **+83** and `@@ -2816,6 +2899,40 @@` **+34** —
whose sum is the row's shift); the other **15** sit BELOW it, in two hunks (`@@ -7622,6 +7739,12 @@`
**+6**, `@@ -7832,6 +7955,15 @@` **+9**), and move nothing. ⚠️ The **`340d500491`**
"this regeneration's base" paragraph above says `main`
had touched `tortoise/` not at all since `340d500491`; that was true of ITS base, and `b8723c0f16`
now does — the `file:line`-versus-SCALAR gap this section describes, from a fourth side: every SCALAR
stayed green while the appendix had already rotted, because line numbers are only graded by the
byte-compare arm.

**And the SAME arm reddened a second time at merge,** when this branch integrated `origin/main`
**`8cc31bbb9e`**: one more row moved, `tortoise/monitoring.py:4787` → **`4806`** (**+19**), and again
**no census SCALAR moved** (`--check` reports **`25 pinned counts match`**). All of it comes from ONE
commit, `main`'s **`1afeab3763`** (`#13454`), which touched `tortoise/monitoring.py` `30 +/9 −` — net
**+21** lines, of which **19 are above the site** in three hunks (`@@ -818,7 +818,9 @@` **+2**,
`@@ -826,9 +828,24 @@` **+15**, `@@ -884,8 +901,10 @@` **+2**) and **2 are below** it
(`@@ -7742,9 +7761,11 @@` **+2**), which moves nothing. Appendix A is regenerated with `--markdown` on
this merged tree, so this paragraph and the table it describes are one cut. ⛔ That is now **two**
merges in a row on which the appendix rotted green — the `b8723c0f16` paragraph above is the first —
which is the argument for the byte-compare arm, not for re-cutting by hand.

**Where these SCALARS were last cut:** `origin/main` **`9d13dc1bf7`**, the last of this branch's
forward merges (`0774035055`, then `9d13dc1bf7`) — re-derived by RUNNING the tool on the FINAL
merged tree, never by arithmetic on the markers. `main`'s two commits between them move no census
scalar, so every number below is the `0774035055` cut's, re-measured here and unchanged. **This cut CORRECTS a pair it inherited stale rather than moving it.** The branch's previous
cut carried `ast_door_calls` **1573** / `non_cypher_door_calls` **260**, which were the `34bd33d22a`
tree's two scalars; `main`'s `#13249` (`bf3a7b8e77`) then converted the org purge's two
`public.graphs` reads from `query` to `query_all`, and `query_all` is **not** a `DOORS` entry
(`tools/cypher_inventory.py:148`), so each conversion REMOVES one door call and adds no statement
site. The branch merged that commit (`56142ad8f1`) without re-recording the two scalars, so on this
merged tree they read **1571 / 258** while every other scalar holds: `sites` **1313** (`plain` **860**
— read **579**, write **281**), `candidates` **579**, `unresolved` **107**, `partial` **315**,
`files_scanned` **297**, `files_with_sites` **99**. §1's `rg` reconciliation is re-measured here (door
**1,625 / 108**, verb **2,343 / 144** whole-text, bare **1,631 / 109**), `--all-surface` reads
**9,055 / 820**, Appendix A is regenerated with `--markdown`, and `--check` reports **`25 pinned counts
match`**.

⛔ **Neither parent's scalar is the answer, and this cut is the measured proof:** this head's parent
read 1573 / 260 while `main`'s own cut at `bf3a7b8e77` recorded 1569 / 258, and the merged tree reads
**1571 / 258** — the branch's two Cypher sites (1313 = 1311 + 2) plus `main`'s two removed door calls.
A lane that carried either parent's figure forward, or reused the branch's pre-merge cut, would have
written a pin `--check` then accepted. Only running the tool says which tree you are holding.

**The cut before that:** `origin/main` **`34bd33d22a`**, forward-merged into the branchfor the third time (`54682177bc`, then `34bd33d22a`) — neither merge moved a count: they add no Cypher
statement site, so every scalar is unchanged from the `a218f8867b` cut and only Appendix A's
`file:line` rows move, which is why Appendix A is regenerated at each merge rather than trusted.

**Where the cut before that landed:** `origin/main` **`54682177bc`**, forward-merged into the branch
that re-cut them (PR #13290, `fix/embed-boundary`) on top of its own `a218f8867b` cut. That second
merge moved **no** count — it landed docs and a metering read that add no Cypher statement site and
no door call — so every scalar below is unchanged from the `a218f8867b` cut and only Appendix A's
`file:line` rows moved, which is why Appendix A is regenerated again here (`--check` → `25 pinned
counts match`). The `a218f8867b` cut, kept as the record of it:

**Where these numbers were cut:** `origin/main` **`a218f8867b`**, merged into the branch that
re-cut them (PR #13290, `fix/embed-boundary`) — re-derived by RUNNING the tool on that merged tree,
never by arithmetic on the markers. Two facts compose in this cut, and both were measured:

* **this PR** adds **two** Cypher statement sites, both `plain`/`read`, both in
  `tortoise/search_engine.py` (§7 names them) — and no non-Cypher door call;
* **`main`'s** move since the branch's previous cut adds **two non-Cypher** door calls (`#9559`'s
  `metering.get_postgres_residency`, the per-org residency reader's two control-plane reads) and
  **no** Cypher statement site.

So on the merged tree: `sites` 1311→**1313**, `plain` 859→**860** (read 578→**579**; the 281 write
half is unchanged), `candidates` 578→**579**, `unresolved` 106→**107**, `partial` 314→**315**,
`ast_door_calls` 1571→**1573**, and `non_cypher_door_calls` **260** (main's, carried through
unchanged). `files_scanned` stays 297 and `files_with_sites` 99: this PR's two new sites live in a
file that already carries sites, and so does the residency reader. §3's per-file table is re-ordered
(`tortoise/search_engine.py` 23→25), the `rg` reconciliation in §1 is re-measured on this merged tree
(1,626 / 2,342 with its predicates unchanged), Appendix A is regenerated with `--markdown`, and
`--check` reports **`25 pinned counts match`**.

> **The `rg` and `--all-surface` figures were re-run, not carried.** `rg -o
> '\.(ro_)?(query|execute)\s*\('` over the 297 product files reads **1,626 / 108**, `rg -io
> '\b(MATCH|MERGE)\s*\('` reads **2,329 / 141**, and `--all-surface` reads **8,954 / 812** on this
> tree. Every one of them moved from the previous cut, which is the reason the procedure above says
> "integrate before you measure" rather than "add your own diff to the number you inherited".

The paragraph below is `main`'s cut, kept as the record of it.

**Where THESE numbers were cut:** `#9559`'s branch, merged with `origin/main` **`9cb527275a`**, and
re-derived from that merged tree — not by arithmetic on the markers. The branch adds two non-Cypher
door calls (`metering.get_postgres_residency`, the per-org residency reader's two control-plane
reads) and **no** Cypher statement site, so `ast_door_calls` **1569→1571** and
`non_cypher_door_calls` **258→260** while `sites`/`plain`/`candidates` are unchanged (1311 / 859 /
578 — the 578 is the `plain` READ half, which is also the migration-candidate count). `files_scanned`
stays 297 and `files_with_sites` 99: the reader lives in a file that already carries sites. The
`rg` reconciliation in §1 is re-measured on this tree (1,626 / 2,330 / 1,632 bare / 2,064 / 2,402),
Appendix A is regenerated with `--markdown`, and `--check` reports **`25 pinned counts match`**.

The paragraph below is `main`'s cut that this merge brings in, kept as the record of it — the branch
merged it at `56142ad8f1` but never recorded its scalars, which is why the cut above CORRECTS them
rather than moving them.
**Where THESE numbers were cut:** `#13249`'s branch, merged with `origin/main` **`bf3a7b8e77`**, and
re-derived from that merged tree — not by arithmetic on the markers. The branch converts the two
`public.graphs` reads in the org purge from `query` to `query_all`, and `query_all` is **not** a
`DOORS` entry (`tools/cypher_inventory.py:148`), so each conversion REMOVES one door call and adds no
statement site: `ast_door_calls` **1571→1569** and `non_cypher_door_calls` **260→258** while
`sites`/`plain`/`candidates` are unchanged (1311 / 859 / 578 — the 578 is the `plain` READ half, which
is also the migration-candidate count). `files_scanned` stays 297 and `files_with_sites` 99: both
conversions land in a file that already carries sites. The `rg` reconciliation in §1 is re-measured on
this tree (1,622 / 2,330 / 1,628 bare / 2,064 / 2,402 — measured at THAT tree: `bf3a7b8e77` reads
1,624 / 1,630 bare, and this branch's two conversions are what take it to 1,622 / 1,628. §1 carries the
figures at the head you are reading, which later commits have moved by one), Appendix A is regenerated with `--markdown`,
and `--check` reports **`25 pinned counts match`**.

⚠️ THREE of those `rg` figures moved by ONE at `#13265` (`34bd33d22a`), for the same reason as the row
below and from the same commit: `tortoise/record_writer.py:1723` is a COMMENT line containing
`MERGE (s:Source {url:$id})`, and this census counts LINES — so AT THAT HEAD the tool read 2,343 / 143
where it read 2,342 / 142, and `rg` read 2,330 / 142, 2,064 and 2,402 where it read 2,329 / 141, 2,063
and 2,401. ⛔ The other TWO did NOT move, and the reason is the predicate, not the commit: the DOOR
figure (`1,624` / `108` at `34bd33d22a`; `1,623` / `108` at `31bddb1dd9`) and the bare-door figure
(`1,630` → `1,629`) are drawn from a predicate `#13265` did not touch — it added **0** door calls and
exactly **1** verb occurrence (`git show 34bd33d22a -- tortoise/ | grep '^+' |
grep -cE '\.(ro_)?(query|execute)\s*\('` → `0`; `… | grep -oiE '\b(MATCH|MERGE)\s*\('` → `1`).
The 13-occurrence one-file whole-text gap and the ten-prose/three-Cypher split are UNCHANGED by it
(the added match is single-line, not cross-line, and §1 counts the same 13 sites — ten prose, three
Cypher examples). `record_projection.py` is the one file on both sides of that split: one of its two
matches wraps a Cypher example, where the next line opens the `MATCH` pattern, and the other wraps
prose, where the next line opens a parenthetical.
⛔ **AND IT MOVED AGAIN, TWICE, BEFORE THIS PR COULD MERGE — each figure bound to the ONE tree it was
measured on.** At `origin/main` **`31bddb1dd9`** the appendix `main` carried was stale in **96 of its
1,311 rows** over exactly two files: `#13330` (`3bcb6c6531`) added `+438 / −47` to
`tortoise/projection/__init__.py` (92 rows) and `#13353` (`8c03c0aab7`) added `+33` to
`tortoise/sdk.py` (4 rows) — and the row SETS were identical keyed by `(fn, shape, statement)`, so
that one was pure renumbering. At `origin/main` **`7b293004b9`**, the tree the cut BEFORE this one was
measured on, `main`'s appendix is stale in **1 of its 1,313 rows** — `tortoise/record_projection.py` moved from
`:7826` to `:7897` — and **`--check` reports `25 pinned counts match` on that tree too**. That is the
whole point: the pins are 25 SCALARS, the appendix is ~1,313 `file:line` rows, so neither staler head
could be caught by a pin. Reproduce either with `git show <base>:docs/architecture/CYPHER-INVENTORY.md`
versus a fresh `--markdown` **at that same base** — a cut taken at a later tree is a different pair
and reproduces a different number.

⚠️ That regeneration is not decorative at this head: `main`'s `#13115` (`9f327e1435`) moved code
inside files that already carried sites, so re-cutting the appendix moves ROWS while every SCALAR
stays put. Measured at the base it landed on **`6264dc670c`** against the appendix `origin/main` carried at
`34bd33d22a`: **787 rows added and 785 dropped** of 1,313 (`git show 34bd33d22a:docs/architecture/CYPHER-INVENTORY.md`
versus a fresh `--markdown`), because `main` inserted lines above hundreds of statement sites in
`tortoise/sdk.py` and `tortoise/hosted_api.py` — and **not one of the 25 pins moved**. That is the
exact `file:line`-versus-SCALAR gap this section describes, caught by regenerating the appendix
rather than by trusting the pins: `--check` passes on both trees while the appendix's `file:line`
column is stale on one of them.

⚠️ **THIS cut's own drift, measured against the appendix `origin/main` carried at `340d500491`:**
**237 rows added and 237 dropped** of 1,313 (`git show 340d500491:docs/architecture/CYPHER-INVENTORY.md`
versus a fresh `--markdown` at the MERGE tree **`48585c94ff`**, whose tree carries this branch's hunks). The 226
above are `main`'s; the extra **11** are
THIS BRANCH's own — the `#13348` hunks, which landed on `main` at `48585c94ff` — and they are a
different cause, not a rounding of the same one: these
comment/docstring hunks insert lines above EVERY ONE of that file's statement sites — at three
heights, so the shifts are not uniform (six rows move **+5**, one **+10**, four **+16**) — which is
what moves all 11 of that file's rows in one re-cut (a fresh cut at `340d500491`'s tree against a
fresh cut at `48585c94ff` differs in exactly those 11, and in no other file). 226 + 11 is the whole 237, and **not one of
the 25 pins moved** in either half. ⛔ `237/237` is bound to the MERGE tree because that is the only
tree that can produce it: measured on the base it landed on (`6264dc670c`, which carries none of this
branch's hunks) the same two commands give **`226/226`** — this branch's own 11 rows do not exist
there. (`787/785`, above, is NOT tree-sensitive: `34bd33d22a` against a fresh cut reads 787/785 at
`6264dc670c` **and** at `48585c94ff`.) Two figures, two different dependencies — and only one of them
can be got wrong by cutting at the wrong tree. ⛔ One note to keep straight, because it is where two
earlier revisions went wrong in opposite directions: the 11 are `#13348`'s own hunks that LANDED on
`main` at `48585c94ff` (`git diff --numstat 48585c94ff^ 48585c94ff -- tortoise/indexer/github_indexer.py`
→ `51 35`), so the `This PR's cut` paragraph above — which names `main`'s `48585c94ff` (#13348) as
touching that file while moving no census SCALAR — and this sentence agree: between `340d500491` and `6264dc670c`
that file takes no change at all (`git diff --name-only 340d500491 6264dc670c -- tortoise/` → empty),
and everything the 11 rows reflect arrives with `48585c94ff`. An earlier revision got the tree binding right and then defended
the attribution with "`main` inserted nothing into that file", which is true only of the
`340d500491 → 6264dc670c` interval and false of the commit the figure is measured at; the revision
before that mis-bound the figure. Same defect class this section names, twice.
⚠️ Earlier revisions of this sentence are RETIRED rather than reconciled, because each named a unit,
a metric or a base it did not measure: one said "31 of 1,313 rows" (`1,313` was that tree's LINE
count, not its row count — at `34bd33d22a` the table carried **1,311 rows over 1,313 lines**, while at
the base it landed on, `6264dc670c`, it carries **1,313 rows over 1,315 lines** — and `31` was
attributed to no metric), and another said "137 of 1,311 rows", measured against a `origin/main` that `#13317` has
since regenerated. ⛔ Every count in this paragraph is reproducible only from the tree named beside
it: the recipe that yields `237/237` at `48585c94ff` prints `226/226` at `6264dc670c`, so a reader who
re-cuts without checking which tree a figure was bound to will "correct" the figure that is right.
A count taken off the wrong unit, off a base that has since moved, or off an unnamed metric is the
defect class this paragraph exists to name.
**The cut before that, `main`'s own, kept as the record of it** — the PINS by `main`'s
**`05fe46e94e`**, re-derived at `origin/main` **`b8f56fdec4`**. ⚠️ Both columns below are the CENSUS's
own measurements at those two commits, not the marker values — the markers `main` carried into the
re-cut were themselves stale, which is why the two sets differ:

| what | measured at `534f3e1bee` (parent of the re-cut) | measured at `b8f56fdec4` |
|---|---|---|
| `sites` | 1309 (its marker said **1308**) | 1311 |
| `files` | 98 | 99 |
| `shape_plain` | 857 (its marker said **856**) | 859 |
| `ast_door_calls` | 1567 (its marker said **1565**) | 1569 |
| `files_scanned` | 297 | 297 (unchanged) |

`tools/cypher_inventory.py --check` was **RC=1** at `534f3e1bee` on six counts, so `05fe46e94e`
corrected six, not three: the three rows above (their stale markers in parentheses) **plus**
`candidates` (575→576), `shape_plain_read` (575→576) and `non_cypher_door_calls` (257→258).
(`files` 98→99 did NOT drift: the population stayed at 297 scanned files, and `tortoise/api.py`
gained its FIRST statement site inside `#12910`'s own diff — a file newly carrying sites, not a
count that was wrong.) Re-derived by running the tool at both commits and diffing the sets of
carrying files; nothing here is arithmetic on the marker values.

⛔ **Appendix A, however, was NOT re-cut by that commit, and this is the defect that keeps
recurring here:** the pins are 25 SCALARS, so an insertion inside a file that already carries
sites moves the `file:line` rows and moves no count. Measured at `b8f56fdec4`: `--check` prints
`25 pinned counts match` while **6 appendix rows name lines that no longer carry their statement** —
five in `tortoise/metering.py` (`measure_write_ops`, `_record_graph_storage_reading_locked`,
`get_graph_storage_reading`, `record_postgres_bytes_written`, and the reading writer below them)
and one in `tortoise/postgres_bytes.py`. This change regenerates Appendix A with `--markdown` at
that head (verified byte-identical to a fresh run) and adds
`tests/test_13098_cypher_inventory.py::test_the_doc_appendix_is_the_tools_own_output` so the next
main-move cannot repeat it — which is step 1 of the procedure above enforced instead of trusted.

⛔ **And neither of those two carriers covers the numbers this file QUOTES, which is the third place
the same cut can leave stale.** The pins are 25 scalars and the appendix is `file:line` rows; §0's
`--all-surface` row (not a pin, deliberately), §1's `rg` cross-check and the prose that explains it
are neither. Measured at `a218f8867b`, with `--check` printing `25 pinned counts match`: that row
still said `8,908 / 805` against `8,939 / 810` on this tree, §1's bare case-sensitive count quoted the
NO-TYPE-FILTER population (`2,060` at that head, where the row is about `.py` files), and its stated
CAUSE for the 13-occurrence gap was wrong outright — see
§1. Re-derive the quoted prose too, not just the two carriers that are checked.

<details><summary>the previous cut (kept as history)</summary>

`origin/main` **`97fd713ffb`** — `sites` 1306→1308, `plain`
854→856 (read 573→575; the 281 write half is unchanged), `candidates` 573→575, `files_scanned`
296→297, `ast_door_calls` 1563→1565, the `rg` reconciliation in §1 re-measured on this tree, and
Appendix A regenerated with `--markdown`.

⛔ **The two sites this cut adds are READ-side, and one of them is not statically resolvable.** The
`#8430` review's F2 fix asks the engine once whether any stored row is readable at the query vector's
width before it lets a zero-row scan report `empty_results`; that probe's statement interpolates the
label and the seam expression, so it lands as a `plain`/`read` site marked **partial**. The #8429
review's empty-cohort probe (`_index_yields_any_node`) resolves to nothing and lands in
`unresolved` — which is why `unresolved` and `partial` moved with `sites` rather than one of them
standing still.

⛔ **The cut this one replaces is why the paragraph exists at all.** `#13133` re-cut
these pins to `1306` while it landed the four sites that make the tree measure `1308`: it INCREMENTED
the pin it inherited (`1302` + its own four) instead of re-deriving from the merged tree, so the two
`plain`/`read` sites `#13143` had already added to `tortoise/projection/entities.py` — `1302`→`1304`,
measured on `7c1df6e7d6` — stayed absorbed only on paper. Measured, not inferred: `--json` at
`7c1df6e7d6` reports 1304 sites with 0 in `tortoise/planned_occurrence.py`; at `97fd713ffb` it reports
1308 with that one file carrying exactly 4, and `tortoise/projection/entities.py` reads 54 at BOTH.
The numbers above are re-derived from the merged tree; this PR's own two sites and main's two
non-Cypher door calls are the whole difference from `97fd713ffb`'s re-derivation.

</details>

`.github/workflows/cypher-diff.yml` runs the **differential harness steps first and this census step
LAST**, deliberately: a base-drift pin must still red the job (the doc's numbers are then wrong), but
it must not be able to mask whether the harness can fail at all — which is exactly what it did before
the order was fixed (`#13098` review F8).

⛔ **This cut adds ONE non-Cypher door call and no statement site (`#13422`).** The folded arm of
`record_vector.vector_search` prefetches the label's rows before its kNN, and that prefetch is a
Postgres `SELECT` issued through the shared `execute` door — so it lands in `non_cypher_door_calls`
(258→259) and `ast_door_calls` (1572→1573), exactly the bucket that exists for a Postgres statement
sharing a graph door, while `sites`, `files` and every `shape_*` pin are unchanged. It is written AT
the call site rather than behind the module's `_rows_as_dicts` helper for that reason: a helper taking
`sql` as a parameter makes every call site an UNRESOLVABLE door call, which lands in `unresolved` and
reads as an unclassified CYPHER statement in the bucket a migration candidate is searched for
(measured: the helper form reported `unresolved` 108→109, `sites` 1314→1315 and `files` 100→101).

**This cut's base:** `origin/main` **`130e20fdaf`**, merged here (the PR had gone `CONFLICTING` on this
very file, and the conflict was resolved by REGENERATING it, never by picking a side: the pins and
Appendix A are that merged tree's own `--markdown` output, and §0/§1's prose was re-measured at that base with
the predicates it states — whole-text **`2,374 / 147`**, line-based **`2,361 / 146`** — not carried
from either parent and not shifted by a delta applied to a number that was already there). The prose
is the THIRD carrier with no check behind it — exactly the defect the paragraph above names — so the
quoted counts are re-derived here and the bare/`.py`/`-t py` variants with them.

**This merge's cut (`51ab22cb1c` integrating `origin/main` `501270a4bf`):** the CONFLICT was resolved by
REGENERATING this file, never by picking a side. Two things were measured rather than assumed, and the
first one is a defect this cut REPAIRS:

* ⛔ **The pins were 22 sites stale at the pre-merge head, and the appendix had rotted with them — as a
  matched pair, which is why nothing inside the doc disagreed.** Measured on a detached worktree at
  `51ab22cb1c` with this branch's own tool: `sites` **1314**, `files` **100**, `files_scanned` **298**,
  `partial` **315**, `non_cypher_door_calls` **259** — against pins that read **1336** and an Appendix A
  of exactly **1336** data rows. `--check` printed **9 DRIFT lines** there. §7's own neighbouring
  paragraph already said `1314`, so the document contradicted ITSELF: the pins and the table had been
  re-cut at a tree that no longer exists on this branch, and the prose had not. A matched pin/table pair
  is not evidence of a fresh cut — it is what a stale cut looks like from the inside.
* ✅ **The merges themselves move no statement site.** `origin/main` at `501270a4bf`, measured alone,
  reads **1314 / 100 / 298** with **315** partial and **258** non-Cypher; this branch pre-merge reads
  **1314 / 100 / 298**, **315** partial and **259** non-Cypher. `main` then advanced to
  **`895d4dde3d`** while this lane worked, and integrating it moves exactly ONE scalar — `files_scanned`
  **298→299** — because it adds one product module (`tortoise/derived_embedding.py`) that carries **no**
  Cypher statement site (`files_with_sites` stays **100**, every `shape_*` and `partial` pin unchanged).
  So the only census delta in this whole PR is the one the paragraph above already attributes
  (`#13422`'s prefetch `SELECT`, non-Cypher 258→259 / `ast_door_calls` 1572→1573). A PEER lane's own
  integration commits (`033f82b0a1`, `56ab87e73a`) were then merged on top, and they move no census
  scalar either — measured on this final tree, which reads **1314 / 100 / 299** exactly as it did
  before they were taken. Every number in this section is therefore re-derived at ONE tree, the one
  that carries them.

Re-measured on the merged tree with the predicates §1 states, NOT carried and NOT shifted by a delta:
door **1,627 / 109**; verb **2,346 / 145** whole-text and **2,333 / 144** line-based (the 13-occurrence,
one-file whole-text gap unchanged); bare `rg … tortoise/` door **1,633 / 110**; bare case-sensitive verb
**2,066** and `-i` without the exclusion **2,405**; over the 299 product `.py` files **2,063 / 2,333**;
with `-t py` and the test-module exclusion dropped **2,065 / 2,336** (the 69-occurrence, six-file
`.ts`/`.mjs`/`.sh` gap is unchanged, enumerated in §1); verb-at-end-of-line **178**. Appendix A is this
tree's own `--markdown` output (**1314** rows), and `--check` reports **`25 pinned counts match`**.

<!-- cypher-inventory:sites = 1314 -->
<!-- cypher-inventory:files = 100 -->
<!-- cypher-inventory:files_scanned = 299 -->
<!-- cypher-inventory:unresolved = 108 -->
<!-- cypher-inventory:candidates = 579 -->
<!-- cypher-inventory:shape_adjacency = 292 -->
<!-- cypher-inventory:shape_ddl = 17 -->
<!-- cypher-inventory:shape_ep = 32 -->
<!-- cypher-inventory:shape_fulltext = 2 -->
<!-- cypher-inventory:shape_plain = 860 -->
<!-- cypher-inventory:shape_traversal = 3 -->
<!-- cypher-inventory:shape_unresolved = 108 -->
<!-- cypher-inventory:shape_plain_read = 579 -->
<!-- cypher-inventory:shape_plain_write = 281 -->
<!-- cypher-inventory:shape_adjacency_read = 192 -->
<!-- cypher-inventory:shape_adjacency_write = 100 -->
<!-- cypher-inventory:shape_ep_read = 21 -->
<!-- cypher-inventory:shape_ep_write = 11 -->
<!-- cypher-inventory:shape_ddl_ddl = 17 -->
<!-- cypher-inventory:shape_traversal_read = 3 -->
<!-- cypher-inventory:shape_fulltext_read = 2 -->
<!-- cypher-inventory:shape_unresolved_unresolved = 108 -->
<!-- cypher-inventory:non_cypher_door_calls = 259 -->
<!-- cypher-inventory:ast_door_calls = 1573 -->
<!-- cypher-inventory:partial = 315 -->

---

## Appendix A — every statement site, by shape

| file:line | fn | shape | role | verdict | statement |
|---|---|---|---|---|---|
| `tortoise/__main__.py:672` | `_cmd_reconcile` | plain | read | candidate:SQL-shaped | `UNWIND $ids AS eid MATCH (e:Event {eventId:eid}) RETURN e.eventId` |
| `tortoise/__main__.py:1857` | `_cmd_init` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/__main__.py:1881` | `_cmd_init` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/__main__.py:13152` | `_cmd_verify` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: 'test-verify'}) RETURN p` |
| `tortoise/__main__.py:13160` | `_cmd_verify` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: 'test-verify'}) RETURN p.content` |
| `tortoise/__main__.py:13165` | `_cmd_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point {id: 'test-verify'}) DELETE p` |
| `tortoise/__main__.py:13798` | `_cmd_backfill` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point) WHERE p.status IS NULL SET p.status = 'live' RETURN …` |
| `tortoise/__main__.py:13800` | `_cmd_backfill` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point) WHERE p.createdAt IS NULL SET p.createdAt = $now RET…` |
| `tortoise/__main__.py:15651` | `_cmd_doctor` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/__main__.py:17258` | `_cmd_key_create` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) RETURN t.id, t.name` |
| `tortoise/__main__.py:17287` | `_cmd_key_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (:TeamMeta {name: $name, created: $now})` |
| `tortoise/acl_graph_users.py:455` | `_registry_store_credential` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) SET g.acl_user=$u, g.acl_pass…` |
| `tortoise/acl_graph_users.py:477` | `_stored_acl_password` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.acl_pass` |
| `tortoise/acl_graph_users.py:705` | `credential_for_graph` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.acl_user, g.acl_pass` |
| `tortoise/aggregate.py:568` | `collect_anchor_census` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.id IN $ids RETURN o.id, o.name` |
| `tortoise/aggregate.py:574` | `collect_anchor_census` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Object) WHERE o.id IN $ids OPTIONAL MATCH (o)<-[:aboutObjec…` |
| `tortoise/analyze.py:739` | `_bfs_select_operators._derived_live_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point) WHERE op.id IN $ids WITH * MATCH (op)-[:IMPL\|NAND]…` |
| `tortoise/analyze.py:845` | `_bfs_select_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:???]->(target) WHERE op.id IN …` |
| `tortoise/analyze.py:995` | `_bfs_select_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point) WHERE op.id IN $ops WITH * MATCH (op)-[r:IMPL\|NAND…` |
| `tortoise/analyze.py:1066` | `_stale_first_claims` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/analyze.py:1080` | `_stale_first_count_stamped` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/analyze.py:1148` | `analyze` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/analyze.py:1223` | `mean_grounding` | ep | read | stays:ep-carveout (§6.3) | `MATCH (p:Point) WHERE p.is_operator = false AND {} RETURN coalesce(p…` |
| `tortoise/analyze.py:1256` | `grounding_snapshot` | ep | read | stays:ep-carveout (§6.3) | `MATCH (p:Point) WHERE p.is_operator = false AND {} RETURN p.id, p.co…` |
| `tortoise/api.py:2933` | `EventAPI.add_object` | plain | read | candidate:SQL-shaped | `MATCH (n:Object {id:$id}) RETURN count(n)` |
| `tortoise/assembly.py:2011` | `docker_resolver_port.exact_objects` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.id IN $names {}RETURN o.id, o.name` |
| `tortoise/assembly.py:2017` | `docker_resolver_port.exact_objects` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.name IN $names {}RETURN o.id, o.name` |
| `tortoise/assembly.py:2065` | `docker_resolver_port.excluded_exact_objects` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE (o.name IN $names OR o.id IN $names) AND o.st…` |
| `tortoise/assembly.py:2131` | `docker_resolver_port.fts_objects` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.id IN $ids RETURN o.id, o.name` |
| `tortoise/assembly.py:2142` | `docker_resolver_port.alias_objects` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:aboutObject]->(o:Object) WHERE p.search_keys IS NO…` |
| `tortoise/assembly.py:2581` | `docker_walker_port.state_rows` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.id IN $ids RETURN o.id, o.name, o.status, o…` |
| `tortoise/assembly.py:2608` | `docker_walker_port.spine_rows` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Object {id:$oid})<-[:aboutObject]-(p:Point) RETURN 'point' …` |
| `tortoise/assembly.py:2631` | `docker_walker_port.spine_rows` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Object {id:$oid})<-[:aboutObject]-(e:Event) RETURN 'event' …` |
| `tortoise/assembly.py:2681` | `docker_walker_port.day_rows` | ep | read | stays:ep-carveout (§6.3) | `MATCH (p:Point) WHERE p.when STARTS WITH $day OR p.createdAt STARTS …` |
| `tortoise/assembly.py:2701` | `docker_walker_port.day_rows` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE e.startedAt STARTS WITH $day OR e.when STARTS …` |
| `tortoise/assembly.py:2766` | `docker_walker_port.window_rows` | ep | read | stays:ep-carveout (§6.3) | `MATCH (p:Point) WHERE ??? RETURN 'point' AS kind, p.id, p.content, p…` |
| `tortoise/assembly.py:2796` | `docker_walker_port.window_rows` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE ??? RETURN 'event' AS kind, e.eventId, e.name,…` |
| `tortoise/assembly.py:4487` | `_probe_visible_successors` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.name IN $names RETURN o.name AS nm, o.statu…` |
| `tortoise/audit.py:247` | `_count` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/audit.py:342` | `_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/audit.py:2744` | `audit_graph._legacy_repair` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {id:{}})-[r:mitigates]->{} RETURN count(r), collect(…` |
| `tortoise/backup.py:617` | `restore._refuse_non_empty_destination` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/backup.py:690` | `restore` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/backup_sweep.py:428` | `query_label_counts` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE {} UNWIND labels(n) AS lbl RETURN lbl, count(*) ORDE…` |
| `tortoise/backup_sweep.py:490` | `enumerate_orgs` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) RETURN t.id` |
| `tortoise/backup_sweep.py:515` | `enumerate_eligible_orgs` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) WHERE t.tier <> 'free' AND t.backup_enabled = true RE…` |
| `tortoise/backup_sweep.py:595` | `enumerate_org_graphs` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN properties(g)` |
| `tortoise/backup_sweep.py:1011` | `_probe_graph._read` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/backup_sweep.py:2018` | `enumerate_org_tombstones` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid, status:'deleted'}) WHERE coalesce(g.kin…` |
| `tortoise/backup_sweep.py:2275` | `_row_still_tombstoned` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) WHERE g.status = 'deleted' AN…` |
| `tortoise/backup_sweep.py:2302` | `_stamp_purged` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid, status:'deleted'}) SET g.purge…` |
| `tortoise/billing.py:733` | `apply_limits` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.tier=$tier, t.max_users=$max_users, t.…` |
| `tortoise/billing.py:907` | `mirror_subscription` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET ???` |
| `tortoise/billing.py:1013` | `reconcile_org` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.subscription_id, t.stripe_customer_…` |
| `tortoise/billing.py:1138` | `org_billing_identity` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.id, t.tier, t.subscription_id, t.st…` |
| `tortoise/billing.py:1504` | `reconcile_subscriptions` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) RETURN t.id, t.tier, t.subscription_id, t.stripe_cust…` |
| `tortoise/cache_evictor.py:504` | `_delete_nodes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `UNWIND $rows AS r MATCH (n) WHERE id(n) = r.id AND n.{} = r.key DETA…` |
| `tortoise/cache_evictor.py:514` | `_delete_nodes` | plain | read | candidate:SQL-shaped | `UNWIND $rows AS r MATCH (n) WHERE id(n) = r.id AND n.{} = r.key RETU…` |
| `tortoise/cache_projection.py:3893` | `_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/cache_projection.py:4239` | `project_records` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/capture_fold.py:701` | `read_fold_evidence` | ep | read | stays:ep-carveout (§6.3) | `MATCH ({}:Point {}}) RETURN {}.extractedFrom, {}.extractedFromAll, {…` |
| `tortoise/capture_fold.py:791` | `read_fold_evidence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (d:Point {id:$dup})-[r]->(t) RETURN type(r), head(labels(t)), …` |
| `tortoise/capture_fold.py:833` | `read_fold_evidence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s)-[r]->(d:Point {id:$id}) RETURN type(r), head(labels(s))` |
| `tortoise/capture_fold.py:903` | `_canonical_view` | ep | read | stays:ep-carveout (§6.3) | `MATCH ({}:Point {}}) RETURN {}.extractedFrom, {}.extractedFromAll, {…` |
| `tortoise/capture_fold.py:920` | `_canonical_view` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (:Point {id:$id})-[:extractedFrom]->(s) RETURN coalesce(s.id, …` |
| `tortoise/capture_fold.py:1062` | `_carried` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (:Point {id:$canon})-[r:{}]->(t {id:$target}) WHERE $label IN …` |
| `tortoise/claim_class.py:226` | `classes_for_ids` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, coalesce(n.claimClas…` |
| `tortoise/cohort_cost.py:300` | `cohort_org_ids` | plain | read | candidate:SQL-shaped | `MATCH (o) WHERE (o:Organization OR o:Team) AND o.created_at > $since…` |
| `tortoise/commit_idempotency.py:88` | `CommitRecordStore.get` | plain | read | candidate:SQL-shaped | `MATCH (r:CommitRecord {client_commit_id: $cid}) RETURN r.session_id …` |
| `tortoise/commit_idempotency.py:130` | `CommitRecordStore.acquire` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (r:CommitRecord {client_commit_id: $cid}) ON CREATE SET r.sess…` |
| `tortoise/commit_idempotency.py:193` | `CommitRecordStore.discard_attempt` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (r:CommitRecord {client_commit_id:$cid}) WHERE r.status = 'par…` |
| `tortoise/commit_idempotency.py:215` | `CommitRecordStore.update` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (r:CommitRecord {client_commit_id: $cid}) SET r.status = $stat…` |
| `tortoise/commit_ops.py:494` | `apply_payload_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true, op_type:$t})-[:{} {idx:0}]->(s) WH…` |
| `tortoise/commit_ops.py:557` | `apply_payload_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true}) WHERE o.op_type = $t WITH o MATCH…` |
| `tortoise/commit_ops.py:696` | `_supersession_fold_order` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.name IN $names RETURN o.name, o.id` |
| `tortoise/commit_ops.py:719` | `_supersession_fold_order` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.id IN $ids RETURN ID(o), o.id, o.name` |
| `tortoise/commit_ops.py:722` | `_supersession_fold_order` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.name IN $names RETURN ID(o), o.id, o.name` |
| `tortoise/commit_ops.py:962` | `_id_claimant_counts` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, count(*)` |
| `tortoise/commit_ops.py:1080` | `_entity_rows` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN {} LIMIT 2` |
| `tortoise/commit_ops.py:1332` | `_successor_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE {} RETURN n.id, n.name, n.status` |
| `tortoise/commit_ops.py:1380` | `_exact_subject_ref_ids` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject) WHERE {} AND s.name = $ref RETURN s.id` |
| `tortoise/commit_ops.py:1406` | `_subject_family_holds_ref` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject) WHERE s.id = $ref??? RETURN s.id LIMIT 1` |
| `tortoise/commit_ops.py:1484` | `_apply_subject_supersession` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject) WHERE s.id = $ref RETURN ID(s), s.id, s.name, s.st…` |
| `tortoise/commit_ops.py:1526` | `_apply_subject_supersession` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject) WHERE {} RETURN ID(s), s.id, s.name, s.status, s.s…` |
| `tortoise/commit_ops.py:1765` | `_apply_subject_supersession` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Subject {id:$id}) SET s += $s RETURN [k IN $keys \| propert…` |
| `tortoise/commit_ops.py:1965` | `apply_supersessions` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.status, coalesce(n…` |
| `tortoise/commit_ops.py:2128` | `apply_supersessions` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.id = $ref RETURN ID(o), o.id, o.name, o.sta…` |
| `tortoise/commit_ops.py:2134` | `apply_supersessions` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE {} RETURN ID(o), o.id, o.name, o.status, o.su…` |
| `tortoise/connectors/github.py:326` | `GitHubConnector.ingest` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Object {id: $id}) SET o.routed_team = $team, o.routed_role …` |
| `tortoise/connectors/github.py:352` | `GitHubConnector.ingest` | adjacency | write | stays:graph-maintenance | `MATCH (o:Object {id: $oid}) MATCH (s:Subject {id: $sid}) MERGE (o)-[…` |
| `tortoise/consistency.py:2712` | `_graph_view` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/consistency.py:3051` | `check_consistency` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) RETURN count(n)` |
| `tortoise/consistency.py:3563` | `recover_from_log._node_count` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/coverage_loop.py:363` | `facet_census` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Object) WHERE o.id IN $ids OPTIONAL MATCH (o)<-[:aboutObjec…` |
| `tortoise/cypher_guard.py:2061` | `_UnsupportedOperatorGuardedQueries.query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/cypher_guard.py:2066` | `_UnsupportedOperatorGuardedQueries.ro_query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/decide_wiring.py:64` | `find_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s {id:$sid})-[ri:INPUT]->(op:Point {is_operator:true})-[ro]->…` |
| `tortoise/decide_wiring.py:85` | `find_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point)-[r]->() WHERE op.id IN $ids AND r.idx > 0 RETURN op…` |
| `tortoise/deployment.py:66` | `health` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n) LIMIT 1` |
| `tortoise/derived_invalidation.py:1588` | `reconcile_goal_states` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE NOT n:Point AND toStringOrNull(n.goalState) IN $word…` |
| `tortoise/derived_invalidation.py:1610` | `reconcile_goal_states` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE toStringOrNull(n.goalState) IN $words RETURN c…` |
| `tortoise/derived_invalidation.py:1717` | `reconcile_goal_states` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE n.id = $id RETURN n.id LIMIT 1` |
| `tortoise/docs_miner.py:441` | `_read_doc_state` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$did}) RETURN 1` |
| `tortoise/docs_miner.py:446` | `_read_doc_state` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.contentHash` |
| `tortoise/docs_miner.py:452` | `_read_doc_state` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.extractedFrom = $did RETURN count(p)` |
| `tortoise/docs_miner.py:497` | `_ensure_entities` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.externalId = $ext RETURN o.id` |
| `tortoise/docs_miner.py:739` | `mine_docs` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$did}) RETURN 1` |
| `tortoise/docs_miner.py:763` | `mine_docs` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id:$id}) RETURN 1` |
| `tortoise/domain_validators.py:152` | `validate_chain_integrity` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (uc:Point) WHERE uc.pointKind IN $uc_kinds AND {} OPTIONAL MAT…` |
| `tortoise/domain_validators.py:173` | `validate_chain_integrity` | plain | read | candidate:SQL-shaped | `MATCH (uj:Point) WHERE uj.pointKind IN $uj_kinds AND {} AND uj.cover…` |
| `tortoise/domain_validators.py:196` | `validate_chain_integrity` | plain | read | candidate:SQL-shaped | `MATCH (wf:Point) WHERE wf.pointKind IN $wf_kinds AND {} AND wf.enabl…` |
| `tortoise/domain_validators.py:225` | `validate_chain_integrity` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/domain_validators.py:240` | `validate_chain_integrity` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {status:'draft'}) WHERE n.is_operator = false AND NOT…` |
| `tortoise/domain_validators.py:623` | `validate_portfolio_shape` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE toLower(o.objectKind) IN $kinds AND o.status …` |
| `tortoise/domain_validators.py:683` | `validate_portfolio_shape` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (e:Event) WHERE toLower(e.eventKind) IN $mark_forms OPTIONAL M…` |
| `tortoise/dream.py:480` | `Dreamer._ep_run_batch` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/dream.py:732` | `Dreamer._trivial_stamp` | adjacency | write | stays:graph-maintenance | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/dream.py:740` | `Dreamer._trivial_stamp` | adjacency | write | stays:graph-maintenance | `MATCH (n:Point) WHERE n.id IN $ids AND (n.is_operator IS NULL OR n.i…` |
| `tortoise/dream.py:836` | `Dreamer.dream_all` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/embedding_residual_scan.py:276` | `_count_graph` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.embedding IS NOT NULL RETURN count(n)` |
| `tortoise/ended.py:358` | `enumerate_ended` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/entity_identity.py:309` | `_adopt_idless_holder` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{}) WHERE ({}) AND {} AND n.id IS NULL SET n.id = $id, n.ca…` |
| `tortoise/entity_identity.py:437` | `_collapse_resolved_holders` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids AND {} RETURN n.id` |
| `tortoise/entity_identity.py:981` | `_name_holder_ids` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE {} {}RETURN n.id` |
| `tortoise/entity_identity.py:1030` | `_endpoint_idents` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/entity_identity.py:1069` | `_require_unique_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE {} AND n.id = $id RETURN n.id` |
| `tortoise/entity_identity.py:1236` | `_record_if_any_holder` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE {} AND {} RETURN n.id` |
| `tortoise/entity_identity.py:1303` | `resolve_entity_id` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$value}) RETURN n.id` |
| `tortoise/entity_identity.py:1415` | `resolve_document_target_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n:Object OR (n:Source AND n.documentKind IS NOT NUL…` |
| `tortoise/entity_identity.py:1427` | `resolve_document_target_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n:Object OR (n:Source AND n.documentKind IS NOT NUL…` |
| `tortoise/entity_identity.py:1447` | `resolve_document_target_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n:Object OR (n:Source AND n.documentKind IS NOT NUL…` |
| `tortoise/entity_resolver.py:290` | `_read_entities` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IS NOT NULL AND n.resolved_from IS NULL AND …` |
| `tortoise/entity_resolver.py:680` | `follow_resolved_from` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN n.resolved_from` |
| `tortoise/entity_resolver.py:727` | `find_live_holder` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IS NOT NULL AND (n.status IS NULL OR NOT n.s…` |
| `tortoise/entity_resolver.py:842` | `_identity_row` | plain | read | candidate:SQL-shaped | `MATCH ({}:{} {id:$id}) ???` |
| `tortoise/entity_resolver.py:859` | `_entity_label` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN n.id` |
| `tortoise/entity_resolver.py:918` | `_survivor_fixpoint` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN n.resolved_from` |
| `tortoise/entity_resolver.py:936` | `_dependents_of` | plain | read | candidate:SQL-shaped | `MATCH (d:{}) WHERE d.resolved_from = $id RETURN d.id` |
| `tortoise/ep.py:458` | `TortoiseEP._hop_query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/ep.py:804` | `TortoiseEP._read_node` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/ep.py:829` | `TortoiseEP._write_node` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/ep.py:846` | `TortoiseEP._read_message` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$oid})-[r:{}]->(c) WHERE {} AND c.id = $cid RETUR…` |
| `tortoise/ep.py:862` | `TortoiseEP._write_message` | adjacency | write | stays:graph-maintenance | `MATCH (o:Point {id:$oid})-[r:{}]->(c) WHERE {} AND c.id = $cid SET r…` |
| `tortoise/ep.py:897` | `TortoiseEP._read_back_message` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/ep.py:917` | `TortoiseEP._write_back_message` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/ep.py:970` | `TortoiseEP._is_strong` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/ep.py:1299` | `TortoiseEP._update_claim_posterior` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point)-[r:{}]->(c) WHERE {} AND c.id = $cid RETURN coalesce…` |
| `tortoise/ep.py:1308` | `TortoiseEP._update_claim_posterior` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point)-[r:{}]->(b) WHERE a.id = $cid AND {} AND coalesce(r.…` |
| `tortoise/ep.py:2614` | `TortoiseEP.get_contested_claims` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:{}) WHERE ??? WITH n, coalesce(n.posterior_alpha, n.ep_alph…` |
| `tortoise/ep.py:2701` | `TortoiseEP.invalidate_messages` | adjacency | write | stays:graph-maintenance | `MATCH ()-[r:IMPL\|NAND]->() REMOVE r.msg_alpha, r.msg_beta, r.back_m…` |
| `tortoise/ep.py:2715` | `TortoiseEP.invalidate_messages` | adjacency | write | stays:graph-maintenance | `MATCH (p)-[r:IMPL\|NAND]-() WHERE {} AND p.id IN $ids REMOVE r.msg_a…` |
| `tortoise/ep.py:2750` | `TortoiseEP.invalidate_factor_messages` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point)-[conn:IMPL\|NAND]->(p) WHERE {} AND p.id IN $ids AN…` |
| `tortoise/event_store.py:410` | `ensure_event_schema` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:GraphEvent) ON (n.event_id)` |
| `tortoise/event_store.py:421` | `ensure_event_schema` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:GraphEvent) ON (n.seq)` |
| `tortoise/event_store.py:478` | `next_seq` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:GraphEventMeta) ON CREATE SET m.last_seq = 0, m.first_seq =…` |
| `tortoise/event_store.py:556` | `capture_watermark` | plain | read | candidate:SQL-shaped | `MATCH (m:GraphEventMeta) RETURN max(m.last_seq)` |
| `tortoise/event_store.py:644` | `reestablish_watermark` | plain | read | candidate:SQL-shaped | `MATCH (e:GraphEvent) RETURN max(e.seq), min(e.seq)` |
| `tortoise/event_store.py:656` | `reestablish_watermark` | plain | read | candidate:SQL-shaped | `MATCH (m:GraphEventMeta) RETURN max(m.last_seq)` |
| `tortoise/event_store.py:737` | `reestablish_watermark` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:GraphEventMeta) ON CREATE SET m.last_seq = $last, m.first_s…` |
| `tortoise/event_store.py:800` | `append_event` | plain | read | candidate:SQL-shaped | `MATCH (e:GraphEvent {event_id:$id}) RETURN count(e)` |
| `tortoise/event_store.py:815` | `append_event` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (e:GraphEvent {seq:$seq, ts:$ts, type:$type, event_id:$event_…` |
| `tortoise/event_store.py:878` | `_event_row_exists` | plain | read | candidate:SQL-shaped | `MATCH (e:GraphEvent {event_id:$id}) RETURN count(e)` |
| `tortoise/event_store.py:991` | `read_after` | plain | read | candidate:SQL-shaped | `MATCH (e:GraphEvent) WHERE e.seq > $after RETURN properties(e) ORDER…` |
| `tortoise/event_store.py:1028` | `purge_expired` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:GraphEvent) WHERE n.ts < $cutoff WITH n LIMIT 10000 DETACH …` |
| `tortoise/event_store.py:1043` | `purge_overflow` | plain | read | candidate:SQL-shaped | `MATCH (n:GraphEvent) RETURN count(n)` |
| `tortoise/event_store.py:1052` | `purge_overflow` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:GraphEvent) WITH n ORDER BY n.seq ASC LIMIT $overflow DETAC…` |
| `tortoise/event_store.py:1102` | `_refresh_first_seq` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:GraphEventMeta) OPTIONAL MATCH (e:GraphEvent) WITH m, min(e…` |
| `tortoise/export.py:125` | `collect_pack_config` | plain | read | candidate:SQL-shaped | `MATCH (p:PackInstall) RETURN p.namespace, p.version, p.source, p.sta…` |
| `tortoise/export.py:132` | `collect_pack_config` | plain | read | candidate:SQL-shaped | `MATCH (m:PackManifest {namespace: $ns}) RETURN m.yaml` |
| `tortoise/extractor_v2.py:3020` | `_enrich_point_priors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point) WHERE p.id IN $ids OPTIONAL MATCH (p)-[:aboutObject]…` |
| `tortoise/extractor_v2.py:3073` | `_entity_facts` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, n.resolved_from, n.name…` |
| `tortoise/extractor_v2.py:6986` | `_entity_verdict_facts` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, n.resolved_from, n.dedu…` |
| `tortoise/extractor_v2.py:7019` | `_entity_verdict_facts` | plain | read | candidate:SQL-shaped | `MATCH (d:{}) WHERE d.resolved_from IN $ids RETURN d.resolved_from` |
| `tortoise/fallback_snapshot.py:577` | `build_snapshot` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator = false OR n.is_operator IS NUL…` |
| `tortoise/fallback_snapshot.py:588` | `build_snapshot` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator = false OR n.is_operator IS NUL…` |
| `tortoise/goal_judgement.py:1232` | `latest_readings` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator RETURN c…` |
| `tortoise/goal_judgement.py:1236` | `latest_readings` | plain | read | candidate:SQL-shaped | `{} AND id(e) >= $cursor RETURN id(e), e.id, e.value, e.unit, e.start…` |
| `tortoise/goal_judgement.py:1357` | `live_judgements` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE ??? RETURN properties(p) ORDER BY coalesce(p.e…` |
| `tortoise/goal_judgement.py:1591` | `_ensure_judgement_edge` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$i})-[:INPUT]->(op:Point)-[:IMPL]->(g) WHERE g.id…` |
| `tortoise/goal_judgement.py:1657` | `_extra_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$i})-[:INPUT]->(op:Point)-[:IMPL]->(g) WHERE g.id…` |
| `tortoise/goal_judgement.py:1758` | `_supersession_evidence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$i})-[r]-() RETURN type(r) LIMIT 1` |
| `tortoise/graph_delete_guard.py:255` | `safe_graph_delete` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n) DETACH DELETE n` |
| `tortoise/hosted_api.py:1740` | `_iter_registered_orgs._read_page` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:1908` | `_iter_registered_orgs` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) WHERE t.deleted_at IS NULL RETURN t.id, t.name` |
| `tortoise/hosted_api.py:3341` | `_lifespan._graph_provider` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) WHERE g.kind = 'custom' AND coalesce(g…` |
| `tortoise/hosted_api.py:6934` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (t:Team { id: $id, name: $name, tier: 'free', created_at: $no…` |
| `tortoise/hosted_api.py:6953` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey { id: $id, org_id: $org_id, key_hash: $hash, key_pr…` |
| `tortoise/hosted_api.py:6980` | `provision_tenant` | plain | read | candidate:SQL-shaped | `MATCH (m:TeamMeta) RETURN count(m)` |
| `tortoise/hosted_api.py:7009` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (m:Membership { id: $id, user_id: $user_id, org_id: $org_id, …` |
| `tortoise/hosted_api.py:7055` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) DETACH DELETE t` |
| `tortoise/hosted_api.py:7056` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id: $id}) DETACH DELETE k` |
| `tortoise/hosted_api.py:7059` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id: $id}) DETACH DELETE m` |
| `tortoise/hosted_api.py:9078` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey) WHERE k.key_prefix = $prefix AND k.revoked_at IS NU…` |
| `tortoise/hosted_api.py:9087` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey) WHERE k.revoked_at IS NULL AND (k.expires_at IS NUL…` |
| `tortoise/hosted_api.py:9120` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey) WHERE k.revoked_at IS NULL AND (k.expires_at IS NUL…` |
| `tortoise/hosted_api.py:9142` | `get_current_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.last_used_at = $now` |
| `tortoise/hosted_api.py:9169` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.tier, t.max_users, t.max_graphs, t…` |
| `tortoise/hosted_api.py:9207` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.namespace` |
| `tortoise/hosted_api.py:9853` | `_assert_graph_owned` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.namespace` |
| `tortoise/hosted_api.py:10572` | `_read_stored_session_harness` | plain | read | candidate:SQL-shaped | `OPTIONAL MATCH (s:Session {id:$sid}) RETURN s.harness AS harness` |
| `tortoise/hosted_api.py:13198` | `_capture_session_probe_off_loop._probe` | plain | read | candidate:SQL-shaped | `OPTIONAL MATCH (s:Session {id:$sid}) RETURN count(s) AS n, s.capture…` |
| `tortoise/hosted_api.py:14347` | `list_points` | plain | read | candidate:SQL-shaped | `MATCH (n:Point){} WHERE ??? RETURN properties(n) ORDER BY n.createdA…` |
| `tortoise/hosted_api.py:14516` | `get_point._cache_props` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: $id}) WHERE {} RETURN properties(p)` |
| `tortoise/hosted_api.py:14534` | `get_point._cache_sees_node` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: $id}) RETURN properties(p)` |
| `tortoise/hosted_api.py:15226` | `org_info` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE NOT n.id IN $demo_ids RETURN count(n)` |
| `tortoise/hosted_api.py:16167` | `register_user` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) WHERE t.email = $email RETURN t.id` |
| `tortoise/hosted_api.py:16308` | `register_user` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (t:Team { id: $id, name: $name, email: $email, tier: 'free', …` |
| `tortoise/hosted_api.py:16325` | `register_user` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey { id: $id, org_id: $org_id, key_hash: $hash, key_pr…` |
| `tortoise/hosted_api.py:16384` | `register_user` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) DETACH DELETE t` |
| `tortoise/hosted_api.py:16387` | `register_user` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id: $id}) DETACH DELETE k` |
| `tortoise/hosted_api.py:16881` | `_seed_demo_graph` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: '_demo_sentinel'}) RETURN p.id` |
| `tortoise/hosted_api.py:16890` | `_seed_demo_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:Point {id:$id}) SET p.content=$c, p.pointKind=$k, p.is_oper…` |
| `tortoise/hosted_api.py:16899` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}) MERGE (t:Tag {name:$tag}) MERGE (p)-[:TAGG…` |
| `tortoise/hosted_api.py:16922` | `_seed_demo_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET s.created_at=$now, s.turn_count=0` |
| `tortoise/hosted_api.py:17015` | `_seed_demo_graph` | ep | write | stays:ep-carveout (§6.3) | `MERGE (p:Point {id:$id}) SET p.content=$c, p.pointKind=$k, p.is_oper…` |
| `tortoise/hosted_api.py:17024` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}) MERGE (t:Tag {name:$tag}) MERGE (p)-[:TAGG…` |
| `tortoise/hosted_api.py:17034` | `_seed_demo_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:Point {id:$id}) SET p.content=$c, p.pointKind=$k, p.is_oper…` |
| `tortoise/hosted_api.py:17043` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}) MERGE (t:Tag {name:$tag}) MERGE (p)-[:TAGG…` |
| `tortoise/hosted_api.py:17052` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (c:Point {id:'epis_claim1'}), (f:Point {id:'sem_fact_layers'})…` |
| `tortoise/hosted_api.py:17057` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (w:Point {id:'proc_wf1'}), (c:Point {id:'epis_claim1'}) MERGE …` |
| `tortoise/hosted_api.py:17063` | `_seed_demo_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (p:Point {id:'_demo_sentinel', content:'demo-sentinel', point…` |
| `tortoise/hosted_api.py:17512` | `_ensure_graph_exists` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.kind, g.status` |
| `tortoise/hosted_api.py:17667` | `_key_created_analytics` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid}) RETURN m.user_id LIMIT 1` |
| `tortoise/hosted_api.py:17700` | `_rotatable_key_row` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id: $id}) RETURN k.org_id, k.name, k.graph_id, k.sc…` |
| `tortoise/hosted_api.py:17736` | `_claim_key_revocation` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id: $tid, id: $id}) WHERE k.revoked_at IS NULL …` |
| `tortoise/hosted_api.py:17762` | `_force_revoke_key_row` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id: $tid, id: $id}) SET k.revoked_at = $now` |
| `tortoise/hosted_api.py:18506` | `list_api_keys` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id: $tid, graph_id: $gid}) RETURN k.id, k.key_p…` |
| `tortoise/hosted_api.py:18517` | `list_api_keys` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id: $tid}) RETURN k.id, k.key_prefix, k.created…` |
| `tortoise/hosted_api.py:18647` | `revoke_api_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id: $id}) RETURN k.org_id, k.revoked_at` |
| `tortoise/hosted_api.py:18663` | `revoke_api_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.revoked_at = $now` |
| `tortoise/hosted_api.py:18930` | `toggle_api_key_enabled` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id: $id}) RETURN k.org_id, k.revoked_at, k.created_…` |
| `tortoise/hosted_api.py:18995` | `toggle_api_key_enabled` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.name = $name` |
| `tortoise/hosted_api.py:19001` | `toggle_api_key_enabled` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.scopes = $sc` |
| `tortoise/hosted_api.py:19697` | `_capture_abandoned_marker` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_ok=false, s.capture_extrac…` |
| `tortoise/hosted_api.py:21213` | `_capture_session_impl` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.entity_links_attempted=$a, s.entit…` |
| `tortoise/hosted_api.py:21249` | `_capture_session_impl._session_alive` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN count(s)` |
| `tortoise/hosted_api.py:21294` | `_capture_session_impl._sweep_orphaned_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (s:Session {id:$sid}) WITH count(s) AS present WHERE …` |
| `tortoise/hosted_api.py:21328` | `_capture_session_impl._sweep_orphaned_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (s:Session {id:$sid}) WITH count(s) AS present WHERE …` |
| `tortoise/hosted_api.py:21363` | `_capture_session_impl._sweep_orphaned_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (s:Session {id:$sid}) WITH count(s) AS present WHERE …` |
| `tortoise/hosted_api.py:21615` | `_capture_session_impl` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, coalesce(n.pointKind…` |
| `tortoise/hosted_api.py:21758` | `_capture_session_impl` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_ok=$ok, s.capture_extracto…` |
| `tortoise/hosted_api.py:22185` | `_load_commit_graph_state` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN s.is_episodic, coalesce(s.value_n…` |
| `tortoise/hosted_api.py:22196` | `_load_commit_graph_state` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids RETURN p.id, p.content` |
| `tortoise/hosted_api.py:22201` | `_load_commit_graph_state` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) RETURN o.name, o.objectKind` |
| `tortoise/hosted_api.py:22205` | `_load_commit_graph_state` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true})-[r]->(t) WHERE type(r) IN ['IMPL'…` |
| `tortoise/hosted_api.py:22267` | `_load_commit_graph_state` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true, op_type:'IMPL'}) MATCH (o)-[:IMPL …` |
| `tortoise/hosted_api.py:22314` | `_store_commit_telemetry` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (r:CommitRecord {client_commit_id:$cid}) SET r.telemetry=$t, r…` |
| `tortoise/hosted_api.py:22392` | `_prevalidate_supersede_window` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom` |
| `tortoise/hosted_api.py:22412` | `_prevalidate_supersede_window` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom, n.createdAt` |
| `tortoise/hosted_api.py:22852` | `_execute_commit_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (r:CommitRecord {client_commit_id:$cid}) WITH collect…` |
| `tortoise/hosted_api.py:22924` | `_execute_commit_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$did}) SET s.id=coalesce(s.id, $did), s.sourceK…` |
| `tortoise/hosted_api.py:23026` | `_execute_commit_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId:$eid}) SET e.id=$eid, e.eventKind='AgentSess…` |
| `tortoise/hosted_api.py:23038` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (e:Event {eventId:$eid}), (s:Source {url:$did}) MERGE (e)-[:pr…` |
| `tortoise/hosted_api.py:23053` | `_execute_commit_writes` | ep | write | stays:ep-carveout (§6.3) | `MERGE (e:Event {eventId:$eid}) SET e.id=$eid, e.eventKind=$ek, e.nam…` |
| `tortoise/hosted_api.py:23065` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (e:Event {eventId:$eid}), (s:Source {url:$did}) MERGE (e)-[:pr…` |
| `tortoise/hosted_api.py:23204` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (a:Source {url:$u1}), (b:Source {url:$u2}) MERGE (a)-[:referen…` |
| `tortoise/hosted_api.py:23273` | `_execute_commit_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point {id:$pid}) SET p.updatedAt=$now` |
| `tortoise/hosted_api.py:23399` | `_execute_commit_writes._terminal_state` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:23511` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/hosted_api.py:23640` | `_execute_commit_writes` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.name = $n AND {} RETURN o.id` |
| `tortoise/hosted_api.py:23667` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (e:Event {eventId:$eid}), (o:Object {id:$oid}) MERGE (e)-[:abo…` |
| `tortoise/hosted_api.py:23800` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}), (o:Object) WHERE ({}) AND o.name = $exact…` |
| `tortoise/hosted_api.py:24285` | `commit_session._commit_region` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET s.is_episodic=true, s.created_at=coa…` |
| `tortoise/hosted_api.py:24383` | `commit_session._commit_region` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (r:CommitRecord {client_commit_id:$cid}) SET r.write_ops_bille…` |
| `tortoise/hosted_api.py:24598` | `list_sessions` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session) WHERE ??? OPTIONAL MATCH (s)-[:CONTAINS]->(p:Point…` |
| `tortoise/hosted_api.py:24941` | `get_session_detail` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN s.id, s.created_at, s.turn_count,…` |
| `tortoise/hosted_api.py:24979` | `get_session_detail` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE (p.pointKin…` |
| `tortoise/hosted_api.py:25076` | `get_session_detail` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'…` |
| `tortoise/hosted_api.py:25195` | `get_session_detail` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE (p.pointKin…` |
| `tortoise/hosted_api.py:25219` | `get_session_detail` | plain | read | candidate:SQL-shaped | `MATCH (src:Source {url:$url}) RETURN src.url, src.sourceKind, src.ev…` |
| `tortoise/hosted_api.py:25383` | `_scorecard_for_org._read_graph` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:25386` | `_scorecard_for_org._read_graph` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:26326` | `_session_count_by_harness` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) WHERE s.harness = $h RETURN count(s)` |
| `tortoise/hosted_api.py:26331` | `_session_count_by_harness` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) WHERE s.harness IS NULL RETURN count(s)` |
| `tortoise/hosted_api.py:26445` | `delete_session._delete_session_sync` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN s.id, s.harness` |
| `tortoise/hosted_api.py:26470` | `delete_session._delete_session_sync` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE p.eventId I…` |
| `tortoise/hosted_api.py:26475` | `delete_session._delete_session_sync` | plain | read | candidate:SQL-shaped | `MATCH (src:Source {url:$url}) RETURN src.eventId` |
| `tortoise/hosted_api.py:26488` | `delete_session._delete_session_sync` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) DETACH DELETE p R…` |
| `tortoise/hosted_api.py:26506` | `delete_session._delete_session_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event) WHERE e.eventId IN $ids DETACH DELETE e` |
| `tortoise/hosted_api.py:26515` | `delete_session._delete_session_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (src:Source {url:$url}) DETACH DELETE src` |
| `tortoise/hosted_api.py:26519` | `delete_session._delete_session_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) DETACH DELETE s` |
| `tortoise/hosted_api.py:26592` | `_user_memberships` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active'}) WHERE m.org_id …` |
| `tortoise/hosted_api.py:26623` | `_membership_org` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, org_id:$tid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:26701` | `_org_node` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:26930` | `_count_active_free_memberships` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active'}) WHERE m.org_id …` |
| `tortoise/hosted_api.py:26989` | `_owned_free_org_ids` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active', role:'owner'}) W…` |
| `tortoise/hosted_api.py:27368` | `_eager_provision_org_graph` | plain | read | candidate:SQL-shaped | `MATCH (m:TeamMeta) RETURN count(m)` |
| `tortoise/hosted_api.py:27679` | `_create_org_registry_lane` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, role:'owner'}) WHERE m.created_at…` |
| `tortoise/hosted_api.py:27700` | `_create_org_registry_lane` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {name:$name}) RETURN t.id, t.graph_name, t.tier, t.del…` |
| `tortoise/hosted_api.py:27718` | `_create_org_registry_lane` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$oid, user_id:$uid}) WHERE m.status = 'a…` |
| `tortoise/hosted_api.py:28084` | `_rollback_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) DETACH DELETE g` |
| `tortoise/hosted_api.py:28614` | `_apply_graph_recording_override` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.id, g.kind, coalesce(g.status…` |
| `tortoise/hosted_api.py:29236` | `_apply_graph_rename` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.id, g.kind, coalesce(g.status…` |
| `tortoise/hosted_api.py:29857` | `delete_graph` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.kind` |
| `tortoise/hosted_api.py:30015` | `_graph_row_probe` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.kind, coalesce(g.sta…` |
| `tortoise/hosted_api.py:30058` | `_rollback_restore_name_race` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) SET g.status = 'deleted', g.d…` |
| `tortoise/hosted_api.py:30093` | `_trash_name_conflict` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid, name:$name}) RETURN g.id, coalesce(g.st…` |
| `tortoise/hosted_api.py:30536` | `_membership_role` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, org_id:$tid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:30746` | `_require_owner` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, org_id:$tid}) RETURN m.role, m.st…` |
| `tortoise/hosted_api.py:30911` | `invite_to_org` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:30930` | `invite_to_org` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN count(m)` |
| `tortoise/hosted_api.py:30935` | `invite_to_org` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {org_id:$tid}) WHERE i.accepted_at IS NULL AND (…` |
| `tortoise/hosted_api.py:30958` | `invite_to_org` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {org_id:$tid, email:$email}) WHERE i.accepted_at…` |
| `tortoise/hosted_api.py:30978` | `invite_to_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (i:Invitation {id:$id, org_id:$tid, email:$email, role:$role,…` |
| `tortoise/hosted_api.py:30987` | `invite_to_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:Membership {org_id:$tid, user_id:$fake}) ON CREATE SET m.ro…` |
| `tortoise/hosted_api.py:30998` | `invite_to_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.email_sent_at = $now` |
| `tortoise/hosted_api.py:31055` | `invite_info._registry_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation) WHERE i.accepted_at IS NULL AND (i.status IS NU…` |
| `tortoise/hosted_api.py:31115` | `invite_info` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:31165` | `_sweep_terminal_invite_placeholders` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {org_id:$tid, email:$email}) WHERE i.accepted_at…` |
| `tortoise/hosted_api.py:31189` | `_delete_fake_invite_membership` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, user_id:$fake}) DELETE m` |
| `tortoise/hosted_api.py:31268` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation) WHERE i.accepted_at IS NULL AND (i.status IS NU…` |
| `tortoise/hosted_api.py:31293` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:31312` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:31333` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:31339` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN count(m)` |
| `tortoise/hosted_api.py:31356` | `accept_invite` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:31604` | `_registry_pending_invite_by_token` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation) WHERE i.accepted_at IS NULL AND (i.status IS NU…` |
| `tortoise/hosted_api.py:31625` | `_registry_otp_issue` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:31650` | `_registry_otp_verify` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.otp_hash, i.otp_expires_at, i…` |
| `tortoise/hosted_api.py:31666` | `_registry_otp_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.otp_hash = null, i.otp_expires_a…` |
| `tortoise/hosted_api.py:31674` | `_registry_otp_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.otp_attempts = COALESCE(i.otp_at…` |
| `tortoise/hosted_api.py:31679` | `_registry_otp_verify` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.otp_attempts` |
| `tortoise/hosted_api.py:31684` | `_registry_otp_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.otp_hash = null, i.otp_expires_a…` |
| `tortoise/hosted_api.py:31692` | `_registry_otp_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.otp_attempts = 0` |
| `tortoise/hosted_api.py:31765` | `_registry_mismatch_accept_v2` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:31773` | `_registry_mismatch_accept_v2` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:31782` | `_registry_mismatch_accept_v2` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:31788` | `_registry_mismatch_accept_v2` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN count(m)` |
| `tortoise/hosted_api.py:31809` | `_registry_mismatch_accept_v2` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:32016` | `invite_otp` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:32070` | `resend_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.org_id, i.email, i.role, i.ac…` |
| `tortoise/hosted_api.py:32101` | `resend_invite` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:32114` | `resend_invite` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:32153` | `expire_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.org_id, i.status, i.accepted_…` |
| `tortoise/hosted_api.py:32166` | `expire_invite` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.status IS NULL OR i.status = '…` |
| `tortoise/hosted_api.py:32238` | `list_pending_invites_for_me` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {email:$email}) WHERE i.accepted_at IS NULL AND …` |
| `tortoise/hosted_api.py:32267` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.id, i.org_id, i.email, i.role…` |
| `tortoise/hosted_api.py:32292` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:32306` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:32321` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:32327` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN count(m)` |
| `tortoise/hosted_api.py:32343` | `_registry_accept_by_id` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:32421` | `decline_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.email, i.org_id, i.status, i.…` |
| `tortoise/hosted_api.py:32438` | `decline_invite` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.status IS NULL OR i.status = '…` |
| `tortoise/hosted_api.py:32444` | `decline_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.status, i.accepted_at` |
| `tortoise/hosted_api.py:32530` | `list_members` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid}) WHERE m.status = 'active' OR m.st…` |
| `tortoise/hosted_api.py:32573` | `remove_member` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) RETURN m.role` |
| `tortoise/hosted_api.py:32581` | `remove_member` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) SET m.status='remov…` |
| `tortoise/hosted_api.py:32624` | `change_member_role` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) RETURN m.role` |
| `tortoise/hosted_api.py:32632` | `change_member_role` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) SET m.role=$role` |
| `tortoise/hosted_api.py:32678` | `_org_members_sync` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN m.user_id…` |
| `tortoise/hosted_api.py:32728` | `_export_graph_snapshot` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN labels(n), properties(n)` |
| `tortoise/hosted_api.py:32758` | `_export_graph_snapshot` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a)-[r]->(b) RETURN labels(a), coalesce(a.id, a.name), type(r)…` |
| `tortoise/hosted_api.py:33316` | `_stamp_import_prop` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.{} = $v` |
| `tortoise/hosted_api.py:33600` | `_apply_import_pack_config` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:PackInstall {namespace: $ns}) SET p.version = $version, p.s…` |
| `tortoise/hosted_api.py:34084` | `_soft_delete_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL SET k.revo…` |
| `tortoise/hosted_api.py:34089` | `_soft_delete_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, status:'active'}) SET m.status='re…` |
| `tortoise/hosted_api.py:34094` | `_soft_delete_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {org_id:$tid}) WHERE (i.status IS NULL OR i.stat…` |
| `tortoise/hosted_api.py:34100` | `_soft_delete_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.deleted_at=$now, t.grace_hours=$gh` |
| `tortoise/hosted_api.py:34677` | `_org_graph_names` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.namespace` |
| `tortoise/hosted_api.py:34844` | `_drop_org_acl_users` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.id` |
| `tortoise/hosted_api.py:34893` | `_purge_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid}) DETACH DELETE m` |
| `tortoise/hosted_api.py:34897` | `_purge_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id:$tid}) DETACH DELETE k` |
| `tortoise/hosted_api.py:34901` | `_purge_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {org_id:$tid}) DETACH DELETE i` |
| `tortoise/hosted_api.py:34905` | `_purge_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) DETACH DELETE t` |
| `tortoise/hosted_api.py:35112` | `_purge_deleted_orgs_locked` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) WHERE t.deleted_at IS NOT NULL AND t.deleted_at < $cu…` |
| `tortoise/hosted_api.py:35369` | `reconcile` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey) WHERE k.created_via = 'bootstrap' AND k.revoked_at …` |
| `tortoise/hosted_api.py:35375` | `reconcile` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now` |
| `tortoise/hosted_api.py:35758` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (t:Team {id:$id, name:$name, tier:'free', created_at:$now, ba…` |
| `tortoise/hosted_api.py:35767` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp,…` |
| `tortoise/hosted_api.py:35773` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (m:Membership {org_id:$tid, user_id:$uid, role:'owner', statu…` |
| `tortoise/hosted_api.py:35781` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (s:SignupToken {token_hash:$th, lookup_key:$lk, org_id:$tid, …` |
| `tortoise/hosted_api.py:35805` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) DETACH DELETE t` |
| `tortoise/hosted_api.py:35806` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id:$id}) DETACH DELETE k` |
| `tortoise/hosted_api.py:35807` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$id}) DETACH DELETE m` |
| `tortoise/hosted_api.py:35808` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:SignupToken {org_id:$id}) DETACH DELETE s` |
| `tortoise/hosted_api.py:37232` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active'}) WHERE m.org_id …` |
| `tortoise/hosted_api.py:37246` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, org_id:$tid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:37253` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.tier, t.suspended_at` |
| `tortoise/hosted_api.py:37294` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, created_via:'bootstrap', created_by:$u…` |
| `tortoise/hosted_api.py:37332` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.cre…` |
| `tortoise/hosted_api.py:37347` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.cre…` |
| `tortoise/hosted_api.py:37364` | `session_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) WHERE k.revoked_at IS NULL SET k.revoked_a…` |
| `tortoise/hosted_api.py:37383` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.cre…` |
| `tortoise/hosted_api.py:37408` | `session_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) WHERE k.revoked_at = $now SET k.revoked_at…` |
| `tortoise/hosted_api.py:37447` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND k.crea…` |
| `tortoise/hosted_api.py:37455` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, created_via:'recovery', created_by:$ui…` |
| `tortoise/hosted_api.py:37470` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, created_via:'bootstrap', created_by:$u…` |
| `tortoise/hosted_api.py:37483` | `session_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now` |
| `tortoise/hosted_api.py:37492` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.cre…` |
| `tortoise/hosted_api.py:37506` | `session_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp,…` |
| `tortoise/hosted_api.py:38231` | `_get_onboarding_state` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.onboarding_state` |
| `tortoise/hosted_api.py:38306` | `_graph_recording_override` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) WHERE coalesce(g.status, 'act…` |
| `tortoise/hosted_api.py:38321` | `_graph_recording_override` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid, kind:'default'}) WHERE coalesce(g.statu…` |
| `tortoise/hosted_api.py:38565` | `_read_onboarding_state_and_version` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.onboarding_state, coalesce(t.state…` |
| `tortoise/hosted_api.py:38616` | `_cas_write_onboarding_state` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) WHERE coalesce(t.state_version, 0) = $expec…` |
| `tortoise/hosted_api.py:38718` | `_write_onboarding_state` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.onboarding_state = $state` |
| `tortoise/hosted_api.py:40480` | `_org_name` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.name` |
| `tortoise/hosted_api.py:40497` | `_OrgSeedSurface.query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:41155` | `public_demo._seed_demo_sync` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: '_demo_sentinel'}) RETURN p.id` |
| `tortoise/hosted_api.py:43044` | `_github_credentials` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.github_token_enc, t.github_org` |
| `tortoise/hosted_api.py:43074` | `_org_email` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.email` |
| `tortoise/hosted_api.py:43098` | `_write_org_email` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.email = $email` |
| `tortoise/hosted_api.py:43422` | `github_callback` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.github_token_enc = $tok, t.github_org…` |
| `tortoise/hosted_api.py:43568` | `_store_github_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.github_org = $org` |
| `tortoise/hosted_api.py:43594` | `_clear_github_credentials` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.github_token_enc = null, t.github_org…` |
| `tortoise/hosted_api.py:44820` | `_prior_stored_turn_count` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid}) OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point…` |
| `tortoise/hosted_api.py:44888` | `_relink_sessions_after_index` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session)-[:CONTAINS]->(t:Point) WHERE t.pointKind='event' R…` |
| `tortoise/hosted_api.py:44901` | `_relink_sessions_after_index` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.entity_links_attempted=$a, s.entit…` |
| `tortoise/hosted_api.py:47666` | `_billing_customer_email` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.email` |
| `tortoise/hosted_api.py:47705` | `_billing_customer_email` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id:$id}) RETURN k.created_by` |
| `tortoise/hosted_api.py:47710` | `_billing_customer_email` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) RETURN k.created_by LIMIT 1` |
| `tortoise/hosted_api.py:47767` | `_billing_checkout_sync` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.subscription_status, t.stripe_custo…` |
| `tortoise/hosted_api.py:47828` | `_billing_checkout_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.stripe_customer_id=$cid` |
| `tortoise/hosted_api.py:47833` | `_billing_checkout_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.stripe_customer_id=$cid, t.customer_em…` |
| `tortoise/hosted_api.py:47972` | `_billing_checkout_new_org_sync` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {name:$name}) RETURN count(t)` |
| `tortoise/hosted_api.py:48030` | `_billing_portal_sync` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.stripe_customer_id` |
| `tortoise/hosted_api.py:48177` | `_org_id_for_stripe_customer` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {stripe_customer_id:$cid}) RETURN t.id` |
| `tortoise/hosted_api.py:48356` | `_webhook_apply_event._set` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t += $props` |
| `tortoise/hosted_api.py:48667` | `webhooks_stripe` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.tier` |
| `tortoise/hosted_api.py:48671` | `webhooks_stripe` | plain | read | candidate:SQL-shaped | `MATCH (w:WebhookEvent {event_id:$id}) RETURN w.first_seen` |
| `tortoise/hosted_api.py:48677` | `webhooks_stripe` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (w:WebhookEvent {event_id:$id, first_seen:$now, type:$type})` |
| `tortoise/hosted_backup.py:496` | `_paged_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_backup.py:515` | `_independent_counts` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE NOT any(l IN labels(n) WHERE l IN $skip_labels) AND …` |
| `tortoise/hosted_backup.py:522` | `_independent_counts` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:546` | `_graph_has_any_node` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN id(n) LIMIT 1` |
| `tortoise/hosted_backup.py:673` | `dump_graph` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE id(n) IN $ids RETURN id(n), labels(n), properties(n)` |
| `tortoise/hosted_backup.py:843` | `_read_event_meta` | plain | read | candidate:SQL-shaped | `MATCH (m:GraphEventMeta) RETURN max(m.last_seq), max(m.first_seq)` |
| `tortoise/hosted_backup.py:891` | `_restore_event_meta` | plain | read | candidate:SQL-shaped | `MATCH (e:GraphEvent) RETURN max(e.seq), min(e.seq)` |
| `tortoise/hosted_backup.py:915` | `_restore_event_meta` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:GraphEventMeta) SET m.last_seq = $last, m.first_seq = $firs…` |
| `tortoise/hosted_backup.py:932` | `_first_live_node_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE id(n) = $id RETURN count(n)` |
| `tortoise/hosted_backup.py:1184` | `restore_graph` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN labels(n), properties(n)` |
| `tortoise/hosted_backup.py:1195` | `restore_graph` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:1210` | `restore_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (n:{}) SET n = $p` |
| `tortoise/hosted_backup.py:1212` | `restore_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (n) SET n = $p` |
| `tortoise/hosted_backup.py:1252` | `restore_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `UNWIND $rows AS r MATCH (n:{} {__dump_id:r.id}) ???SET ???` |
| `tortoise/hosted_backup.py:1263` | `restore_graph` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE n.__dump_id IS NOT NULL RETURN n.__dump_id` |
| `tortoise/hosted_backup.py:1319` | `restore_graph` | adjacency | write | stays:graph-maintenance | `MATCH (a {__dump_id:$s}), (b {__dump_id:$d}) CREATE (a)-[r:{}]->(b) …` |
| `tortoise/hosted_backup.py:1325` | `restore_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n) WHERE n.__dump_id IS NOT NULL REMOVE n.__dump_id` |
| `tortoise/hosted_backup.py:1327` | `restore_graph` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:1385` | `restore_graph` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN labels(n), properties(n)` |
| `tortoise/hosted_backup.py:2140` | `_stamp_backup_latest` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.backup_latest_at = $ts` |
| `tortoise/hosted_backup.py:2159` | `_stamp_backup_restored` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.backup_restored_at = $ts` |
| `tortoise/hosted_backup.py:2539` | `_audit_copied_boolean_indexes._index_has_is_operator` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/hosted_backup.py:2549` | `_audit_copied_boolean_indexes._probe` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_operator = false RETURN count(n)` |
| `tortoise/hosted_backup.py:2552` | `_audit_copied_boolean_indexes._probe` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE NOT n.is_operator RETURN count(n)` |
| `tortoise/hosted_backup.py:2569` | `_audit_copied_boolean_indexes._drop_boolean_indexes` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_backup.py:2785` | `_promote_payload_fork_free` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/hosted_backup.py:3098` | `_restore_copy_settled` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/hosted_backup.py:3100` | `_restore_copy_settled` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/hosted_backup.py:3104` | `_restore_copy_settled` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:3106` | `_restore_copy_settled` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:3299` | `count_data_nodes` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE NOT any(l IN labels(n) WHERE l IN $skip_labels) AND …` |
| `tortoise/hosted_backup.py:3438` | `_restore_into_temp_verify_swap` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN labels(n), properties(n)` |
| `tortoise/indexer/github_indexer.py:493` | `_current_statement_rows` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {externalId:$eid}) WHERE {} RETURN n.id, n.content_ha…` |
| `tortoise/indexer/github_indexer.py:1012` | `GitHubIndexer._event_exists` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.eventId` |
| `tortoise/indexer/github_indexer.py:1057` | `GitHubIndexer._entity_registered` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN n.id` |
| `tortoise/indexer/github_indexer.py:1121` | `GitHubIndexer._frozen_created_payload` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.eventKind, e.endedAt` |
| `tortoise/indexer/github_indexer.py:1177` | `GitHubIndexer._next_transition_id` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE e.eventId STARTS WITH $prefix RETURN e.eventId` |
| `tortoise/indexer/github_indexer.py:1248` | `GitHubIndexer._project_issue` | adjacency | write | stays:graph-maintenance | `MATCH (o:Object {id:$oid}) MATCH (s:Subject {id:$sid}) MERGE (o)-[:a…` |
| `tortoise/indexer/github_indexer.py:1360` | `GitHubIndexer._object_state_to_issue_state` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$id}) RETURN o.status, o.isClosed, o.pipelineSlo…` |
| `tortoise/indexer/github_indexer.py:1730` | `GitHubIndexer._next_version` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {externalId:$eid}) RETURN count(n)` |
| `tortoise/indexer/github_indexer.py:1743` | `GitHubIndexer._link_about_object` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}), (o:Object {id:$oid}) MERGE (p)-[:aboutObj…` |
| `tortoise/indexer/github_indexer.py:1785` | `GitHubIndexer.backfill_legacy_closed` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE e.eventId ENDS WITH '-created' AND (e.eventKin…` |
| `tortoise/indexer/github_indexer.py:1795` | `GitHubIndexer.backfill_legacy_closed` | plain | read | candidate:SQL-shaped | `MATCH (x:Event) RETURN x.eventId` |
| `tortoise/ingest.py:214` | `_run_ep_propagation` | plain | read | candidate:SQL-shaped | `MATCH (o:Point) WHERE o.is_operator = true RETURN o.id` |
| `tortoise/ingest.py:220` | `_run_ep_propagation` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.is_operator = false RETURN n.id, n.confidenc…` |
| `tortoise/ingest.py:432` | `_do_upgrade` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url: $id}) WHERE s.documentKind IS NOT NULL RETURN …` |
| `tortoise/ingest.py:548` | `_do_upgrade` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url: $id}) SET s.needs_extraction = false REMOVE s.…` |
| `tortoise/ingest.py:721` | `_discover_pending_documents` | plain | read | candidate:SQL-shaped | `MATCH (s:Source) WHERE s.documentKind IS NOT NULL AND CASE WHEN s.do…` |
| `tortoise/ingest.py:908` | `_do_upgrade_all` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url: $id}) SET s.needs_extraction = false REMOVE s.…` |
| `tortoise/live.py:1321` | `epistemic_labels_of` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/live.py:1358` | `epistemic_sweep` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/mcp_auth.py:1716` | `OrgResolutionMiddleware.dispatch` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.last_used_at = $now` |
| `tortoise/mcp_auth.py:1735` | `OrgResolutionMiddleware.dispatch` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.suspended_at` |
| `tortoise/mcp_server.py:10897` | `_preview_delete_edges` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a)-[r]-(b) WHERE ID(a) IN $nids OR ID(b) IN $nids RETURN ID(r…` |
| `tortoise/mcp_server.py:10925` | `_preview_orphan_tags` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t:Tag) OPTIONAL MATCH (t)<-[:TAGGED]-(q) WITH t, [x IN collec…` |
| `tortoise/mcp_server.py:10938` | `_preview_delete_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN ID(n)` |
| `tortoise/mcp_server.py:10944` | `_preview_delete_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[:TAGGED]->() RETURN count(*) > 0` |
| `tortoise/mcp_server.py:10999` | `_preview_delete_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {{}:$id}) RETURN ID(n)` |
| `tortoise/mcp_server.py:11066` | `_preview_retract_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) WHERE ??? RETURN count(n)` |
| `tortoise/mcp_server.py:11124` | `_preview_invalidate` | plain | read | candidate:SQL-shaped | `MATCH (b:Point {id:$o}) RETURN count(b)` |
| `tortoise/mcp_server.py:11126` | `_preview_invalidate` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point {id:$n}), (b:Point {id:$o}) WHERE NOT (a)-[:CORRECTS]…` |
| `tortoise/mcp_server.py:11314` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r]->(new:Point {id:$n}) RETURN …` |
| `tortoise/mcp_server.py:11318` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (new:Point {id:$n})-[r:INPUT]->(op:Point {is_operator:true}) R…` |
| `tortoise/mcp_server.py:11332` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r]->(o:Point {id:$old}) RETURN …` |
| `tortoise/mcp_server.py:11353` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$old})-[r:INPUT]->(op:Point {is_operator:true}) R…` |
| `tortoise/mcp_server.py:11382` | `_preview_supersede` | plain | read | candidate:SQL-shaped | `MATCH (o:Point {id:$old}), (n:Point {id:$new}) RETURN o.validFrom, n…` |
| `tortoise/mcp_server.py:11399` | `_preview_supersede` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN ID(n)` |
| `tortoise/mcp_server.py:11413` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (new:Point {id:$n})-[r]->(t) RETURN type(r), ID(t)` |
| `tortoise/mcp_server.py:11416` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t)-[r]->(new:Point {id:$n}) RETURN type(r), ID(t)` |
| `tortoise/mcp_server.py:11438` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t)-[r:IMPL\|NAND]->(o:Point {id:$old}) RETURN type(r), t.id, …` |
| `tortoise/mcp_server.py:11440` | `_preview_supersede` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids AND coalesce(p.is_operator,false)…` |
| `tortoise/mcp_server.py:11481` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$old})-[r:{}]->(t) RETURN type(r), coalesce(t.id,…` |
| `tortoise/mcp_server.py:11520` | `_preview_supersede` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$old}) RETURN count(n)` |
| `tortoise/mcp_server.py:11525` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$old})-[r]-() RETURN count(r)` |
| `tortoise/memory_orchestrator.py:181` | `_queryOntology` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/memory_orchestrator.py:183` | `_queryOntology` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/metering.py:303` | `_metering_anchor` | plain | read | candidate:SQL-shaped | `MATCH (o) WHERE (o:Organization OR o:Team) AND o.id = $id RETURN o.s…` |
| `tortoise/metering.py:615` | `record_unmetered_increment` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (u:MeteringUnmeteredIncrement {org_id: $tid, lane: $lane, drop…` |
| `tortoise/metering.py:625` | `record_unmetered_increment` | plain | read | candidate:SQL-shaped | `MATCH (u:MeteringUnmeteredIncrement {org_id: $tid, lane: $lane, drop…` |
| `tortoise/metering.py:676` | `get_unmetered_increments` | plain | read | candidate:SQL-shaped | `MATCH (u:MeteringUnmeteredIncrement {org_id: $tid}) RETURN u.lane, u…` |
| `tortoise/metering.py:718` | `get_unmetered_increment_total` | plain | read | candidate:SQL-shaped | `MATCH (u:MeteringUnmeteredIncrement) WHERE u.org_id IN $ids RETURN c…` |
| `tortoise/metering.py:839` | `record_write_ops` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) SET m…` |
| `tortoise/metering.py:858` | `record_write_ops` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/metering.py:1145` | `_record_ask_usage_locked` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) SET m…` |
| `tortoise/metering.py:1219` | `get_ask_usage` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/metering.py:1343` | `get_capture_usage` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/metering.py:1487` | `get_write_op_yield` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/metering.py:1607` | `_record_embedding_usage_locked` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) SET m…` |
| `tortoise/metering.py:1692` | `get_embedding_usage` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/metering.py:1812` | `_record_retrieval_usage_locked` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) SET m…` |
| `tortoise/metering.py:1900` | `get_retrieval_usage` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/metering.py:2036` | `_record_capture_usage_locked` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) SET m…` |
| `tortoise/metering.py:2126` | `get_cohort_spend_usd` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord) WHERE m.org_id IN $ids AND m.period_start <…` |
| `tortoise/metering.py:2218` | `get_current_usage` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/metering.py:2235` | `get_current_usage` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $tid}) RETURN t.tier` |
| `tortoise/metering.py:2476` | `measure_write_ops` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/metering.py:2715` | `_record_graph_storage_reading_locked` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) SET m…` |
| `tortoise/metering.py:2899` | `get_graph_storage_reading` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/metering.py:3015` | `record_postgres_bytes_written` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) SET m…` |
| `tortoise/metering.py:3068` | `get_postgres_bytes_written` | plain | read | candidate:SQL-shaped | `MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) RETUR…` |
| `tortoise/migrate_db.py:77` | `_count_nodes` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/migrate_kinds.py:296` | `relabel_legacy_documents` | plain | read | candidate:SQL-shaped | `MATCH (n:Document) RETURN ID(n), n.id, n.url, n.ingestedAt, n.create…` |
| `tortoise/migrate_kinds.py:309` | `relabel_legacy_documents` | plain | read | candidate:SQL-shaped | `MATCH (n:Document:Source) RETURN ID(n)` |
| `tortoise/migrate_kinds.py:324` | `relabel_legacy_documents` | plain | read | candidate:SQL-shaped | `MATCH (s:Source) RETURN ID(s), s.url, s.canonicalUrl, s.urlAliases` |
| `tortoise/migrate_kinds.py:429` | `relabel_legacy_documents` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Document) WHERE ID(n) = $nid SET n:Source SET n.url=$url, n…` |
| `tortoise/migrate_kinds.py:511` | `_do_migrate` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {{}: $old}) RETURN count(n)` |
| `tortoise/migrate_kinds.py:530` | `_do_migrate` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {{}: $old}) RETURN count(n)` |
| `tortoise/migrate_kinds.py:536` | `_do_migrate` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}: $old}) SET n.{} = $new RETURN count(n)` |
| `tortoise/mining.py:93` | `_batch_node` | plain | read | candidate:SQL-shaped | `MATCH (b:Batch {id:$id}) RETURN properties(b)` |
| `tortoise/mining.py:149` | `_upsert_batch` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (b:Batch {id:$id}) SET ???` |
| `tortoise/mining.py:177` | `list_quarantined` | plain | read | candidate:SQL-shaped | `MATCH (b:Batch {status:$st}) RETURN properties(b) ORDER BY b.updated…` |
| `tortoise/mining.py:331` | `ConversationMiner.mine` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids SET n.batch_id = $bid` |
| `tortoise/mining.py:550` | `ConversationMiner._temporal_wire` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.content, n.pointKind, n.status` |
| `tortoise/mining.py:570` | `ConversationMiner._temporal_wire` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.validFrom = $vf` |
| `tortoise/mining.py:579` | `ConversationMiner._temporal_wire` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$pid})-[:aboutObject]->(o:Object)<-[:aboutObject]…` |
| `tortoise/mining.py:633` | `ConversationMiner._temporal_wire` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.temporal_target_ids` |
| `tortoise/mining.py:640` | `ConversationMiner._temporal_wire` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.temporal_candidate = true, n.temporal…` |
| `tortoise/mining.py:749` | `ConversationMiner._reify_entities` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids SET n.eventId=$eid` |
| `tortoise/mining.py:1287` | `mine_corpus_with_sdk` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.file_hash, e.mined_hash` |
| `tortoise/mining.py:1377` | `mine_corpus_with_sdk` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId:$eid}) SET e.mined_hash = $h, e.mined_at = $…` |
| `tortoise/mining.py:1449` | `EpSafeCommit.verify_draft_only` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.status` |
| `tortoise/mining.py:1480` | `EpSafeCommit.verify_no_auto_wire` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true})-[r]->(n:Point) WHERE n.id IN $ids…` |
| `tortoise/mining.py:1488` | `EpSafeCommit.verify_no_auto_wire` | plain | read | candidate:SQL-shaped | `MATCH (o:Point {id:$id}) RETURN o.status` |
| `tortoise/mining.py:1506` | `EpSafeCommit.verify_no_auto_wire` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$oid})-[r]->(s:Point) RETURN s.id, s.status` |
| `tortoise/monitoring.py:4806` | `_probe_once._run_query` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/navigation.py:95` | `_resolve_root` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/navigation.py:205` | `entityProfile` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/navigation.py:319` | `tortoise_traverse` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/near_dup_merge.py:403` | `apply_merge_union` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/near_dup_merge.py:484` | `apply_merge_union` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE ({}) AND o.name = $exact RETURN o.id` |
| `tortoise/near_dup_merge.py:524` | `apply_merge_union` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}), (o:Object) WHERE (({}) AND o.name = $exac…` |
| `tortoise/near_dup_merge.py:557` | `apply_merge_union` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE {} RETURN o.id LIMIT 1` |
| `tortoise/oauth.py:1352` | `_rollback_minted` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/oauth.py:1497` | `_live_family_for_code` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/oauth.py:3049` | `sweep_oauth_retention` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/oauth.py:3053` | `sweep_oauth_retention` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/seed.py:164` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/seed.py:166` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/seed.py:168` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/selfhost.py:130` | `_SdkSeedSurface.query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/state.py:694` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/state.py:697` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/state.py:699` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/pack_manifest_store.py:466` | `upsert_tenant_manifest` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:PackManifest {namespace: $ns}) SET m.name = $name, m.versio…` |
| `tortoise/pack_manifest_store.py:483` | `upsert_tenant_manifest` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:{} {namespace: $ns}) SET p.version = $version, p.status = $…` |
| `tortoise/pack_manifest_store.py:523` | `get_tenant_manifests` | plain | read | candidate:SQL-shaped | `MATCH (m:PackManifest) RETURN m.namespace, m.name, m.version, m.stat…` |
| `tortoise/pack_manifest_store.py:538` | `_get_tenant_manifest_yamls` | plain | read | candidate:SQL-shaped | `MATCH (m:PackManifest) RETURN m.namespace, m.yaml ORDER BY m.namespa…` |
| `tortoise/pack_manifest_store.py:550` | `delete_tenant_manifest` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:PackManifest {namespace: $ns}) DELETE m` |
| `tortoise/pack_manifest_store.py:552` | `delete_tenant_manifest` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:{} {namespace: $ns, source: 'custom'}) SET p.status = 'remo…` |
| `tortoise/pack_state.py:471` | `ensure_tenant_packs` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:PackInstall {namespace: $ns}) SET p.version = $version, p.s…` |
| `tortoise/pack_state.py:554` | `_read_installs` | plain | read | candidate:SQL-shaped | `MATCH (p:PackInstall) RETURN p.namespace, p.version, p.status, p.sou…` |
| `tortoise/pack_state.py:803` | `graph_kind_namespaces` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE {}n.{} IS NOT NULL RETURN DISTINCT n.{}` |
| `tortoise/pack_state.py:878` | `_read_selection` | plain | read | candidate:SQL-shaped | `MATCH (s:PackSelection) RETURN s.revision, s.configured_at, s.update…` |
| `tortoise/pack_state.py:920` | `_cas_first_selection` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:PackSelection) ON CREATE SET s.revision = $rev, s.configure…` |
| `tortoise/pack_state.py:947` | `_bump_selection` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:PackSelection) WHERE s.revision = $exp SET s.revision = $re…` |
| `tortoise/pack_state.py:1143` | `set_graph_packs` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:PackInstall {namespace: $ns}) SET p.version = coalesce($ver…` |
| `tortoise/pack_state.py:1159` | `set_graph_packs` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:PackInstall {namespace: $ns}) SET p.status = $removed, p.up…` |
| `tortoise/pipeline.py:528` | `_read_override` | plain | read | candidate:SQL-shaped | `MATCH (p:Pipeline {pipelineKey:$k}) RETURN p.defaultStageId, p.sourc…` |
| `tortoise/pipeline.py:535` | `_read_override` | plain | read | candidate:SQL-shaped | `MATCH (s:PipelineStage {pipelineKey:$k}) RETURN s.stageId, s.label, …` |
| `tortoise/pipeline.py:613` | `current_stage` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$oid}) RETURN o.name, {}, o.pipelineStagePending` |
| `tortoise/pipeline.py:641` | `unknown_stage_candidates` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.pipelineStagePending IS NOT NULL AND o.pipe…` |
| `tortoise/pipeline.py:647` | `unknown_stage_candidates` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.pipelineStagePending IS NOT NULL RETURN o.n…` |
| `tortoise/pipeline.py:675` | `_write_stage` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:PipelineStage {stageKey:$sk}) SET s.pipelineKey=$k, s.stage…` |
| `tortoise/pipeline.py:697` | `_touch_pipeline` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Pipeline {pipelineKey:$k}) SET p.version=coalesce(p.version…` |
| `tortoise/pipeline.py:718` | `_write_pipeline` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:Pipeline {pipelineKey:$k}) SET p.kind=$kind, p.defaultStage…` |
| `tortoise/pipeline.py:878` | `rename_stage` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:PipelineStage {stageKey:$sk}) SET s.label=$label` |
| `tortoise/pipeline.py:924` | `reorder_stages` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:PipelineStage {stageKey:$sk}) SET s.order=$order` |
| `tortoise/pipeline.py:947` | `retire_stage` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:PipelineStage {stageKey:$sk}) SET s.status=$status, s.retir…` |
| `tortoise/pipeline.py:964` | `set_default_stage` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Pipeline {pipelineKey:$k}) SET p.defaultStageId=$d` |
| `tortoise/pipeline.py:989` | `_resolve_object` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$oid}) RETURN o.id, o.name, o.pipelineStageId, o…` |
| `tortoise/planned_occurrence.py:407` | `create_plan` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$o}) RETURN o.objectKind` |
| `tortoise/planned_occurrence.py:468` | `create_plan` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$o}) RETURN properties(o)` |
| `tortoise/planned_occurrence.py:850` | `_resolve_plan` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$o}) RETURN o.objectKind, o.status, coalesce(o.o…` |
| `tortoise/planned_occurrence.py:924` | `planned_without_occurrence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Object) WHERE p.objectKind IN $kinds OPTIONAL MATCH (e:Even…` |
| `tortoise/postgres_bytes.py:344` | `_meter_statement` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:1952` | `read_config_reset` | plain | read | candidate:SQL-shaped | `MATCH (n:Meta {key:$key}) RETURN properties(n)` |
| `tortoise/projection/__init__.py:1972` | `set_config_reset_marker` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (n:Meta {key:$key}) SET n += $props` |
| `tortoise/projection/__init__.py:1990` | `clear_config_reset` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Meta {key:$key}) DETACH DELETE n` |
| `tortoise/projection/__init__.py:2013` | `_capture_config_snapshot` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:2086` | `_capture_onboarding_snapshot` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN properties(n)` |
| `tortoise/projection/__init__.py:2119` | `_capture_onboarding_snapshot` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:{})-[:{}]->(s:{}) RETURN n.org_id, s.step_id` |
| `tortoise/projection/__init__.py:2237` | `_capture_team_meta` | plain | read | candidate:SQL-shaped | `MATCH (m:TeamMeta) RETURN properties(m)` |
| `tortoise/projection/__init__.py:2373` | `_capture_pack_selection` | plain | read | candidate:SQL-shaped | `MATCH (s:PackSelection) RETURN properties(s)` |
| `tortoise/projection/__init__.py:12051` | `FalkorProjection._probe_ok` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n) LIMIT 1` |
| `tortoise/projection/__init__.py:13234` | `FalkorProjection._apply_event` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'live'` |
| `tortoise/projection/__init__.py:13678` | `FalkorProjection._episodic_point_ids` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_episodic = true RETURN n.id` |
| `tortoise/projection/__init__.py:14125` | `FalkorProjection._replay_log` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:TeamMeta) ON CREATE SET m += $props` |
| `tortoise/projection/__init__.py:14161` | `FalkorProjection._replay_log` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:PackSelection) ON CREATE SET s += $props` |
| `tortoise/projection/__init__.py:14197` | `FalkorProjection._replay_log` | plain | read | candidate:SQL-shaped | `MATCH (s:PackSelection) RETURN properties(s)` |
| `tortoise/projection/__init__.py:14383` | `FalkorProjection._restore_write` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:14547` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r:IMPL\|NAND]->() WHERE r.msg_alpha IS NOT NULL OR r.msg_b…` |
| `tortoise/projection/__init__.py:14565` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) RETURN properties(n)` |
| `tortoise/projection/__init__.py:14643` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[r:{}]->(m:Point) RETURN m.id ORDER BY r.id…` |
| `tortoise/projection/__init__.py:14691` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (b:Batch) RETURN properties(b)` |
| `tortoise/projection/__init__.py:14702` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.batch_id IS NOT NULL RETURN p.id, p.batch_id` |
| `tortoise/projection/__init__.py:14880` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) RETURN properties(s)` |
| `tortoise/projection/__init__.py:14884` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session)-[:CONTAINS]->(p:Point) RETURN s.id, p.id` |
| `tortoise/projection/__init__.py:15293` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/projection/__init__.py:15889` | `FalkorProjection._rebuild_all_replay` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.embedding = NULL, n.content_hash = NU…` |
| `tortoise/projection/__init__.py:16300` | `FalkorProjection._rebuild_all_replay` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'live'` |
| `tortoise/projection/__init__.py:17009` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$i}) RETURN o.name` |
| `tortoise/projection/__init__.py:17231` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id:$i}) RETURN p.id LIMIT 1` |
| `tortoise/projection/__init__.py:17252` | `FalkorProjection._rebuild_all_replay` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'live'` |
| `tortoise/projection/__init__.py:17647` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:{})-[:{}]->(s:Subject) RETURN n.org_id, s.id` |
| `tortoise/projection/__init__.py:17824` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (m:TeamMeta) RETURN properties(m)` |
| `tortoise/projection/__init__.py:17982` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (s:PackSelection) RETURN properties(s)` |
| `tortoise/projection/__init__.py:18479` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (p:{}) WHERE p.id IN $ids RETURN p.id` |
| `tortoise/projection/__init__.py:18494` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a}), (b:{} {id:$b}) MERGE (a)-[:{}]->(b)` |
| `tortoise/projection/__init__.py:18507` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (t:{}) WHERE t.id = $v RETURN ID(t) LIMIT 1` |
| `tortoise/projection/__init__.py:18517` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a})-[r:{}]->(b:{} {id:$b}) SET r += $attrs` |
| `tortoise/projection/__init__.py:18555` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point)-[r]->(old:Point {id:$id}) WHERE (op.is_operator = t…` |
| `tortoise/projection/__init__.py:18580` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (old:Point {id:$id})-[ri:INPUT]->(op:Point) WHERE (op.is_opera…` |
| `tortoise/projection/__init__.py:18618` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id}), (new:Point {id:$nid}) MERGE (op)-[:{}]…` |
| `tortoise/projection/__init__.py:18624` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id}), (new:Point {id:$nid}) MERGE (op)-[:{} …` |
| `tortoise/projection/__init__.py:18629` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id})-[r:{}]->(old:Point {id:$oid}) WHERE ID(…` |
| `tortoise/projection/__init__.py:18645` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$nid}), (op:Point {id:$op_id}) MERGE (new)-[:IN…` |
| `tortoise/projection/__init__.py:18651` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$nid}), (op:Point {id:$op_id}) MERGE (new)-[:IN…` |
| `tortoise/projection/__init__.py:18656` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$oid})-[ri:INPUT]->(op:Point {id:$op_id}) WHERE…` |
| `tortoise/projection/__init__.py:18684` | `FalkorProjection._rebuild_all_replay._src_is_terminal` | plain | read | candidate:SQL-shaped | `MATCH (x:Point {id:$sid}) WHERE {} RETURN 1 LIMIT 1` |
| `tortoise/projection/__init__.py:18765` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (t:Point {id:$id}) RETURN ID(t) LIMIT 1` |
| `tortoise/projection/__init__.py:18784` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old})-[r:{}]->(t) WHERE ID(t) = $tid DELETE r` |
| `tortoise/projection/__init__.py:18898` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old})-[r:{}]->(t) WHERE ID(t) = $tid DELETE r` |
| `tortoise/projection/__init__.py:18993` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (s:Point {id:$sid}), (t) WHERE ID(t) = $tid MERGE (s)-[nr:{}]-…` |
| `tortoise/projection/__init__.py:19027` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (p:{}) WHERE p.id IN $ids RETURN p.id` |
| `tortoise/projection/__init__.py:19044` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a}), (b:{} {id:$b}) MERGE (a)-[:{}]->(b)` |
| `tortoise/projection/__init__.py:19053` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a})-[r:{}]->(b:{} {id:$b}) SET r += $attrs` |
| `tortoise/projection/__init__.py:19156` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r:IMPL\|NAND]->() WHERE r.msg_alpha IS NOT NULL OR r.msg_b…` |
| `tortoise/projection/__init__.py:19467` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) RETURN count(n)` |
| `tortoise/projection/__init__.py:19470` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/projection/__init__.py:19951` | `FalkorProjection._count_projected` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:20001` | `FalkorProjection.query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:20071` | `FalkorProjection._wipe_all_nodes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n) DETACH DELETE n` |
| `tortoise/projection/__init__.py:20404` | `FalkorProjection._record_vector_index_inventory` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:20753` | `FalkorProjection._resolve_entity` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:20956` | `FalkorProjection._schema_is_current` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:21109` | `FalkorProjection._point_fts_field_missing` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:21149` | `FalkorProjection._event_fts_index_is_current` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:21186` | `FalkorProjection._array_valued_fts_prop_exists` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21202` | `FalkorProjection._array_fts_prop_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21242` | `FalkorProjection._fix_point_array_fts_props` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21260` | `FalkorProjection._fix_point_array_fts_props` | unresolved | unresolved | unclassified:statement not resolvable statically | `CALL {}('Point')` |
| `tortoise/projection/__init__.py:21277` | `FalkorProjection._fix_point_array_fts_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:Meta {key:'point_fts_v2'}) SET m.v = true` |
| `tortoise/projection/__init__.py:21325` | `FalkorProjection._create_fulltext_index` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21558` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:Point) ON (n.{})` |
| `tortoise/projection/__init__.py:21596` | `FalkorProjection._ensure_indexes_declared` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21616` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:Point) ON (n.lastDreamedAt)` |
| `tortoise/projection/__init__.py:21639` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:21683` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:{}) ON (n.{})` |
| `tortoise/projection/__init__.py:21698` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (s:Session) ON (s.actor_user_id)` |
| `tortoise/projection/__init__.py:21717` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (s:Session) ON (s.id)` |
| `tortoise/projection/__init__.py:21759` | `FalkorProjection._ensure_indexes_declared` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:Meta {key:'point_fts_v2'}) SET m.v = true` |
| `tortoise/projection/__init__.py:21816` | `FalkorProjection._ensure_indexes_declared` | plain | read | candidate:SQL-shaped | `MATCH (m:Meta {key:'event_fts_v2'}) RETURN 1` |
| `tortoise/projection/__init__.py:21850` | `FalkorProjection._ensure_indexes_declared` | unresolved | unresolved | unclassified:statement not resolvable statically | `CALL {}('Event')` |
| `tortoise/projection/__init__.py:21861` | `FalkorProjection._ensure_indexes_declared` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:Meta {key:'event_fts_v2'}) SET m.v = true` |
| `tortoise/projection/__init__.py:22067` | `FalkorProjection._ensure_vector_index_api` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:22160` | `FalkorProjection._ensure_vector_index_api` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:22485` | `FalkorProjection._create_vector_index_api` | ddl | ddl | stays:graph-maintenance | `CALL db.idx.vector.createNodeIndex('Point', 'embedding', {}, 'HNSW')` |
| `tortoise/projection/__init__.py:22497` | `FalkorProjection._create_vector_index_api` | ddl | ddl | stays:graph-maintenance | `CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) OPTIONS {dimensio…` |
| `tortoise/projection/__init__.py:22595` | `FalkorProjection.backfill_document_search_text` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source) WHERE s.documentKind IS NOT NULL AND s._searchText …` |
| `tortoise/projection/__init__.py:22698` | `FalkorProjection._apply_annotator` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET {} RETURN n.id` |
| `tortoise/projection/__init__.py:22852` | `FalkorProjection._revise_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.content, n.search_keys` |
| `tortoise/projection/__init__.py:23170` | `FalkorProjection._revise_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET {}` |
| `tortoise/projection/__init__.py:23236` | `FalkorProjection._delete_entity_by_id` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) DETACH DELETE n RETURN count(n)` |
| `tortoise/projection/__init__.py:23413` | `FalkorProjection._fold_entity_mutation` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {{}:$id}) RETURN count(n)` |
| `tortoise/projection/__init__.py:23431` | `FalkorProjection._fold_entity_mutation` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) SET n += $s RETURN count(n)` |
| `tortoise/projection/__init__.py:23534` | `FalkorProjection.extract_svbp_factors` | plain | read | candidate:SQL-shaped | `MATCH (o:Point) WHERE o.is_operator = true AND {} AND {} RETURN o.id…` |
| `tortoise/projection/__init__.py:23557` | `FalkorProjection.extract_svbp_factors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point)-[r:IMPL\|NAND]->(c) WHERE o.is_operator = true AND {…` |
| `tortoise/projection/edges.py:345` | `_mint_subject_stub` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject {id:$id}) RETURN ID(s), s.id LIMIT 2` |
| `tortoise/projection/edges.py:365` | `_mint_subject_stub` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Subject {name:$name}) ON CREATE SET s.id=$name, s.subjectKi…` |
| `tortoise/projection/edges.py:371` | `_mint_subject_stub` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject {name:$name}) RETURN ID(s), s.id LIMIT 2` |
| `tortoise/projection/edges.py:435` | `_mint_source_stub` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$url}) ON CREATE SET s.sourceKind=$sk, s.title=…` |
| `tortoise/projection/edges.py:540` | `resolve_structural_target` | plain | read | candidate:SQL-shaped | `MATCH (x:{} {url:$url}) RETURN ID(x), x.id LIMIT 2` |
| `tortoise/projection/edges.py:580` | `resolve_structural_target` | plain | read | candidate:SQL-shaped | `MATCH (x:{} {{}:$id}) RETURN ID(x), x.{} LIMIT 2` |
| `tortoise/projection/edges.py:600` | `resolve_structural_target` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject {name:$name}) RETURN ID(s), s.id LIMIT 2` |
| `tortoise/projection/edges.py:653` | `resolve_structural_target` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) WHERE s.documentKind IS NOT NULL RETURN …` |
| `tortoise/projection/edges.py:667` | `resolve_structural_target` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN ID(s), s.id LIMIT 1` |
| `tortoise/projection/edges.py:689` | `resolve_structural_target` | plain | read | candidate:SQL-shaped | `MATCH (x:{} {name:$key}) RETURN ID(x), x.id LIMIT 2` |
| `tortoise/projection/edges.py:928` | `resolve_source_versions` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.contentHash` |
| `tortoise/projection/edges.py:1040` | `source_change_notices` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[r:extractedFrom]->(s:Source {url:$url}) WHERE r.sou…` |
| `tortoise/projection/edges.py:1051` | `source_change_notices` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[r:extractedFrom]->(s:Source {url:$url}) WHERE r.sou…` |
| `tortoise/projection/edges.py:1681` | `_EdgeHandlers._create_edges` | plain | read | candidate:SQL-shaped | `MATCH (s) WHERE ??? AND s.id = $sid RETURN count(s) > 0` |
| `tortoise/projection/edges.py:1698` | `_EdgeHandlers._create_edges` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (s:Point {id:$sid}) SET s.content='[missing]', s.is_operator=…` |
| `tortoise/projection/edges.py:1709` | `_EdgeHandlers._create_edges` | plain | read | candidate:SQL-shaped | `MATCH (s) WHERE ??? AND s.id = $sid RETURN count(s) > 0` |
| `tortoise/projection/edges.py:1724` | `_EdgeHandlers._create_edges` | adjacency | write | stays:graph-maintenance | `MATCH (o:Point {id:$oid}), (s) WHERE {} AND s.id = $sid MERGE (o)-[:…` |
| `tortoise/projection/edges.py:1788` | `_EdgeHandlers._create_edges` | adjacency | write | stays:graph-maintenance | `MATCH (o:Point {id:$oid}), (s) WHERE ??? AND s.id = $sid MERGE (s)-[…` |
| `tortoise/projection/edges.py:1796` | `_EdgeHandlers._create_edges` | adjacency | write | stays:graph-maintenance | `MATCH (o:Point {id:$oid}), (s) WHERE ??? AND s.id = $sid MERGE (s)-[…` |
| `tortoise/projection/edges.py:1905` | `_EdgeHandlers._sync_tags` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}) MERGE (t:Tag {name:$tag}) MERGE (p)-[:TAGG…` |
| `tortoise/projection/edges.py:1913` | `_EdgeHandlers._sync_tags` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) RETURN t.name` |
| `tortoise/projection/edges.py:1920` | `_EdgeHandlers._sync_tags` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid})-[r:TAGGED]->(t:Tag {name:$tag}) DELETE r` |
| `tortoise/projection/edges.py:1932` | `_EdgeHandlers._sync_tags` | adjacency | write | stays:graph-maintenance | `MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t` |
| `tortoise/projection/edges.py:2129` | `_EdgeHandlers._resolve_subject_fold_target` | plain | read | candidate:SQL-shaped | `MATCH (e:Subject) WHERE e.id IN $ids RETURN ID(e), e.id, e.name ORDE…` |
| `tortoise/projection/edges.py:2207` | `_EdgeHandlers._resolve_about_edge_target` | plain | read | candidate:SQL-shaped | `MATCH (e:Source) WHERE (e.url = $name OR e.title = $name) AND e.docu…` |
| `tortoise/projection/edges.py:2249` | `_EdgeHandlers._resolve_about_edge_target` | plain | read | candidate:SQL-shaped | `MATCH (e:{} {name:$name}) RETURN ID(e), coalesce(e.id, e.eventId, e.…` |
| `tortoise/projection/edges.py:2344` | `_EdgeHandlers.wire_about_link` | adjacency | write | stays:graph-maintenance | `MATCH (n:{} {{}:$sid}), (t) WHERE ID(t)=$tid MERGE (n)-[:{}]->(t) RE…` |
| `tortoise/projection/edges.py:2411` | `_EdgeHandlers.create_about_edge` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {{}:$sv}) MATCH (t:{} {{}:$tv}) MERGE (s)-[:{}]->(t) RET…` |
| `tortoise/projection/edges.py:2796` | `_EdgeHandlers._count_about_out` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Event) WHERE s.id=$sv OR s.eventId=$sv WITH s MATCH (s)-[r:…` |
| `tortoise/projection/edges.py:2906` | `_EdgeHandlers._link_source` | adjacency | write | stays:graph-maintenance | `MATCH (n:{} {}), (s:Source {url:$url}) MERGE (n)-[r:extractedFrom]->…` |
| `tortoise/projection/edges.py:3015` | `_EdgeHandlers.link_source_to_entity` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$url}) ON CREATE SET s.sourceKind=$sk, s.title=…` |
| `tortoise/projection/edges.py:3046` | `_EdgeHandlers.link_source_to_entity` | adjacency | write | stays:graph-maintenance | `MATCH (s:Source {url:$url}), (e:Source {}) MERGE (s)-[r:references]-…` |
| `tortoise/projection/edges.py:3052` | `_EdgeHandlers.link_source_to_entity` | adjacency | write | stays:graph-maintenance | `MATCH (s:Source {url:$url}), (e:Source {}) MERGE (s)-[:references]->…` |
| `tortoise/projection/edges.py:3079` | `_EdgeHandlers.link_source_to_event` | adjacency | write | stays:graph-maintenance | `MATCH (s:Source {url: $url}), (e:Event {eventId: $eid}) MERGE (s)-[r…` |
| `tortoise/projection/edges.py:3100` | `_EdgeHandlers.link_source_to_legacy_event` | adjacency | write | stays:graph-maintenance | `MATCH (s:Source {url: $url}), (e:Event {eventId: $eid}) MERGE (s)-[r…` |
| `tortoise/projection/edges.py:3111` | `_EdgeHandlers.edge_stats` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_operator = true RETURN count(n)` |
| `tortoise/projection/edges.py:3114` | `_EdgeHandlers.edge_stats` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r:IMPL]->() RETURN count(r)` |
| `tortoise/projection/edges.py:3117` | `_EdgeHandlers.edge_stats` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r:NAND]->() RETURN count(r)` |
| `tortoise/projection/edges.py:3120` | `_EdgeHandlers.edge_stats` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r:INPUT]->() RETURN count(r)` |
| `tortoise/projection/edges.py:3179` | `_EdgeHandlers.create_edge` | traversal | read | stays:traversal (§3) | `MATCH (t:{} {{}:$tid}) MATCH (s:{} {{}:$sid}) MATCH path = (t)-[:own…` |
| `tortoise/projection/edges.py:3197` | `_EdgeHandlers.create_edge` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {{}:$sv}) MATCH (t:{} {{}:$tv}) MERGE (s)-[:{}]->(t) RET…` |
| `tortoise/projection/edges.py:3219` | `_EdgeHandlers.create_owned_by` | traversal | read | stays:traversal (§3) | `MATCH (s:{} {{}:$sid}) MATCH (t:{} {{}:$tid}) MATCH path = (s)-[:own…` |
| `tortoise/projection/entities.py:3139` | `_EntityHandlers._upsert_point_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (n:Point {id:$id}) ???SET ???` |
| `tortoise/projection/entities.py:3170` | `_EntityHandlers._upsert_point_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET ???` |
| `tortoise/projection/entities.py:3174` | `_EntityHandlers._upsert_point_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.provenanceSource=$sid` |
| `tortoise/projection/entities.py:3392` | `_EntityHandlers._upsert_point_edges` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.tags` |
| `tortoise/projection/entities.py:3536` | `_EntityHandlers._fold_entity_linked_reason` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {id:$sid}), (t:{} {id:$tid}) MERGE (s)-[:{}]->(t) RETURN…` |
| `tortoise/projection/entities.py:3542` | `_EntityHandlers._fold_entity_linked_reason` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {id:$sid}), (t:{} {id:$tid}) MERGE (s)-[e:{}]->(t) SET e…` |
| `tortoise/projection/entities.py:3805` | `_EntityHandlers._fold_session_recorded` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET {} RETURN count(s)` |
| `tortoise/projection/entities.py:3882` | `_EntityHandlers._link_session` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET s.is_episodic=true` |
| `tortoise/projection/entities.py:3886` | `_EntityHandlers._link_session` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/projection/entities.py:3943` | `_EntityHandlers._fold_session_deleted` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (src:Source {url:$url}) DETACH DELETE src` |
| `tortoise/projection/entities.py:3949` | `_EntityHandlers._fold_session_deleted` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) DETACH DELETE s RETURN count(s)` |
| `tortoise/projection/entities.py:3972` | `_EntityHandlers._point_status` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/projection/entities.py:3978` | `_EntityHandlers._delete` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) DETACH DELETE n` |
| `tortoise/projection/entities.py:4040` | `_EntityHandlers._retract` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'retracted'{}{}, {} RETURN c…` |
| `tortoise/projection/entities.py:4313` | `_EntityHandlers._merge_corrects_edge` | adjacency | write | stays:graph-maintenance | `MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) MERGE (a)-[:COR…` |
| `tortoise/projection/entities.py:4450` | `_EntityHandlers._fold_point_superseded` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status='superseded', n.outdated=true,…` |
| `tortoise/projection/entities.py:4480` | `_EntityHandlers._decay_point_belief` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET {} RETURN n.id LIMIT 1` |
| `tortoise/projection/entities.py:4565` | `_EntityHandlers._fold_point_invalidated` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.outdated=true, n.validTo=$vt, n.valid…` |
| `tortoise/projection/entities.py:4678` | `_EntityHandlers._fold_confidence_changed` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/entities.py:4761` | `_EntityHandlers._fold_edge_message_changed` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/entities.py:4769` | `_EntityHandlers._fold_edge_message_changed` | adjacency | write | stays:graph-maintenance | `MATCH ()-[r:IMPL\|NAND]->() REMOVE r.msg_alpha, r.msg_beta, r.back_m…` |
| `tortoise/projection/entities.py:4811` | `_EntityHandlers._fold_edge_message_changed` | adjacency | write | stays:graph-maintenance | `MATCH (p)-[r:IMPL\|NAND]-() WHERE {} AND p.id IN $ids REMOVE r.msg_a…` |
| `tortoise/projection/entities.py:4820` | `_EntityHandlers._fold_edge_message_changed` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point)-[conn:IMPL\|NAND]->(p) WHERE {} AND p.id IN $ids AN…` |
| `tortoise/projection/entities.py:4937` | `_EntityHandlers._upsert_subject` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Subject {id:$key}) ON CREATE SET s.id=$id, s.name=$name, s.…` |
| `tortoise/projection/entities.py:5112` | `_EntityHandlers._upsert_object` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (o:Object {id:$key}) ON CREATE SET o.id=$id, o.name=$name, o.o…` |
| `tortoise/projection/entities.py:5305` | `_EntityHandlers._fold_object_superseded` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Object {id:$id}) SET o.status='superseded', o.supersededBy=…` |
| `tortoise/projection/entities.py:5527` | `_EntityHandlers._upsert_document` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$id}) ???SET s.id=coalesce(s.id, $id), s.canoni…` |
| `tortoise/projection/entities.py:6053` | `_EntityHandlers._fold_object_status` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$n}) WHERE toLower(toStringOrNull(o.objectKind))…` |
| `tortoise/projection/entities.py:6062` | `_EntityHandlers._fold_object_status` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Object {id:$n}) WHERE toLower(toStringOrNull(o.objectKind))…` |
| `tortoise/projection/entities.py:6080` | `_EntityHandlers._fold_object_status` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$n}) WHERE toLower(toStringOrNull(o.objectKind))…` |
| `tortoise/projection/entities.py:6283` | `_EntityHandlers._fold_object_stage` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$n}) RETURN o.pipelineStageId, o.pipelineStageKe…` |
| `tortoise/projection/entities.py:6315` | `_EntityHandlers._fold_object_stage` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Object {id:$n}) SET o += $props` |
| `tortoise/projection/entities.py:6381` | `_EntityHandlers._event_plain_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props, ??? ON MA…` |
| `tortoise/projection/entities.py:6419` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (s:Subject {id:$id}), (e:Event {eventId:$eid}) MERGE (s)-[:per…` |
| `tortoise/projection/entities.py:6499` | `_EntityHandlers._event_plain_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$id}) ON CREATE SET s.id=$id, s.title=$id, s.so…` |
| `tortoise/projection/entities.py:6510` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (s:Source {url:$id}), (e:Event {eventId:$eid}) MERGE (e)-[:pro…` |
| `tortoise/projection/entities.py:6526` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (o:Object {id:$id}), (e:Event {eventId:$eid}) MERGE (e)-[:prod…` |
| `tortoise/projection/entities.py:6640` | `_EntityHandlers._event_plain_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Object {id:$id}) SET o.objectKind=coalesce($kind, o.objectK…` |
| `tortoise/projection/entities.py:6645` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (o:Object {id:$id}), (e:Event {eventId:$eid}) MERGE (e)-[:uses…` |
| `tortoise/projection/entities.py:6658` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (s:Subject {id: $sid}), (e:Event {eventId: $eid}) MERGE (s)-[:…` |
| `tortoise/projection/entities.py:6676` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (s:Subject {id: $sid}), (e:Event {eventId: $eid}) MERGE (s)-[:…` |
| `tortoise/projection/entities.py:6830` | `_EntityHandlers._materialize_connector_source` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source)-[:references]->(e:Event {eventId: $eid}) RETURN s.u…` |
| `tortoise/projection/entities.py:6837` | `_EntityHandlers._materialize_connector_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url: $url}) ON CREATE SET s.sourceKind = $sk, s.tit…` |
| `tortoise/projection/entities.py:6895` | `_EntityHandlers._materialize_connector_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url: $url}) WHERE s.sourceKind IN $ck AND (s.conten…` |
| `tortoise/projection/entities.py:6943` | `_EntityHandlers._materialize_connector_source` | adjacency | write | stays:graph-maintenance | `MATCH (old:Source)-[r:references]->(e:Event {eventId: $eid}) WHERE o…` |
| `tortoise/projection/entities.py:6965` | `_EntityHandlers._materialize_connector_source` | adjacency | write | stays:graph-maintenance | `MATCH (s:Source {url: $url}), (o:Object {id: $oid}) MERGE (s)-[:refe…` |
| `tortoise/projection/entities.py:7005` | `_EntityHandlers._event_guarded_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props` |
| `tortoise/projection/entities.py:7010` | `_EntityHandlers._event_guarded_merge` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId: $eid}) WHERE e.eventKind = 'meeting' AND e.…` |
| `tortoise/projection/entities.py:7017` | `_EntityHandlers._event_guarded_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId: $eid}) SET e += $props` |
| `tortoise/projection/entities.py:7027` | `_EntityHandlers._event_guarded_merge` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId: $eid}) RETURN e.source_file` |
| `tortoise/projection/entities.py:7033` | `_EntityHandlers._event_guarded_merge` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId: $eid}) WHERE e.eventKind = 'meeting' AND e.…` |
| `tortoise/projection/entities.py:7040` | `_EntityHandlers._event_guarded_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId: $eid}) SET e += $props` |
| `tortoise/projection/entities.py:7045` | `_EntityHandlers._event_guarded_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props` |
| `tortoise/projection/entities.py:7157` | `_EntityHandlers._upsert_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (_prev:Source {url: $url}) WITH _prev, properties(_pr…` |
| `tortoise/projection/grounding.py:19` | `_GroundingMixin.compute_grounding` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {status: 'live'}) RETURN n.id ORDER BY n.id` |
| `tortoise/projection/grounding.py:45` | `_GroundingMixin.compute_grounding` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point {status: 'live'})-[:{}]->(b:Point {status: 'live'}) R…` |
| `tortoise/projection/grounding.py:68` | `_GroundingMixin.compute_grounding` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.pointKind IN ['resolution-event','resolution…` |
| `tortoise/projection/grounding.py:82` | `_GroundingMixin.compute_grounding` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.grounding=$g` |
| `tortoise/projection/grounding.py:93` | `_GroundingMixin.stale_points` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_operator = false AND coalesce(n.updatedAt…` |
| `tortoise/projection/propagation.py:45` | `_PropagationMixin.propagate_shock` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point {id:$id}) SET n.confidence=$v` |
| `tortoise/projection/propagation.py:80` | `_PropagationMixin._compute_confidence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[r:IMPL]-(m:Point) RETURN coalesce(m.confid…` |
| `tortoise/projection/propagation.py:85` | `_PropagationMixin._compute_confidence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[r:NAND]-(m:Point) RETURN coalesce(m.confid…` |
| `tortoise/projection/propagation.py:100` | `_PropagationMixin._confidence` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point {id:$id}) RETURN coalesce(n.confidence, 0.5)` |
| `tortoise/projection/propagation.py:108` | `_PropagationMixin._node_status` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/projection/propagation.py:137` | `_PropagationMixin._neighbors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})<-[:IMPL\|NAND]-(op:Point)-[:IMPL\|NAND]->(m…` |
| `tortoise/quota.py:853` | `resolve_org_limits` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.tier, t.max_users, t.max_graphs, t.…` |
| `tortoise/quota.py:919` | `api_key_occupies_slot` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id: $tid, id: $kid}) WHERE k.revoked_at IS NULL…` |
| `tortoise/quota.py:991` | `stored_source_document_kinds` | plain | read | candidate:SQL-shaped | `MATCH (n:Source {{}:$id}) RETURN n.documentKind` |
| `tortoise/quota.py:1187` | `_count_resource` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id: $tid}) WHERE k.revoked_at IS NULL AND (k.cr…` |
| `tortoise/quota.py:1194` | `_count_resource` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id: $tid}) WHERE m.status IS NULL OR m.stat…` |
| `tortoise/quota.py:1200` | `_count_resource` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id: $tid}) WHERE g.status IS NULL OR g.status <>…` |
| `tortoise/quota.py:1235` | `_count_resource` | plain | read | candidate:SQL-shaped | `MATCH (s:Source) WHERE s.documentKind IS NOT NULL AND s.documentKind…` |
| `tortoise/quota.py:1245` | `_count_resource` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) RETURN count(s)` |
| `tortoise/quota.py:1305` | `_count_resource` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n:Point AND (n.is_episodic IS NULL OR n.is_episodic…` |
| `tortoise/ranking.py:855` | `fetch_entity_signals` | adjacency | read | measure:across shapes (#13083 owns placement) | `??? IN $ids OPTIONAL MATCH (p)-[a:???]->(n) WHERE {} AND p.id IS NOT…` |
| `tortoise/ranking.py:1496` | `GraphRanker._fetch_event_signals` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (e:Event) WHERE e.eventId IN $ids OPTIONAL MATCH (e)-[:aboutOb…` |
| `tortoise/ranking.py:2871` | `SubgraphExpander._neighbors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n) WHERE n.id IN $frontier WITH n MATCH (n)-[r{}]->(m) WHERE …` |
| `tortoise/ranking.py:2883` | `SubgraphExpander._neighbors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n) WHERE n.id IN $frontier WITH n MATCH (m)-[r{}]->(n) WHERE …` |
| `tortoise/ranking.py:2901` | `SubgraphExpander._enrich` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n) WHERE n.id IN $ids RETURN n.id, labels(n)[0] AS label, coa…` |
| `tortoise/record_allocator.py:534` | `_transition` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/record_allocator.py:658` | `execute_allocation` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/record_backup.py:512` | `require_bypassing_role` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/record_projection.py:7918` | `replay` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/record_reconcile.py:569` | `scan_cache_view` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN properties(n)` |
| `tortoise/record_restore.py:474` | `_target_nodes_before` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN count(…` |
| `tortoise/rerank.py:555` | `_fetch_embeddings` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND n.embedding IS NOT NULL RETUR…` |
| `tortoise/residency.py:243` | `read_declaration` | plain | read | candidate:SQL-shaped | `MATCH (n:Meta {key:$key}) RETURN n.coverage, n.hot_keys` |
| `tortoise/residency.py:283` | `declare_residency` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (n:Meta {key:$key}) SET n += $props` |
| `tortoise/residency.py:291` | `clear_residency` | plain | read | candidate:SQL-shaped | `MATCH (n:Meta {key:$key}) RETURN 1` |
| `tortoise/residency.py:297` | `clear_residency` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Meta {key:$key}) DETACH DELETE n` |
| `tortoise/review_verdicts.py:193` | `VerdictLayer.ep_posterior` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point {id:$id}) RETURN n.posterior_alpha, n.ep_alpha, n.bas…` |
| `tortoise/review_verdicts.py:212` | `VerdictLayer._candidate_props` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN properties(n)` |
| `tortoise/review_verdicts.py:294` | `VerdictLayer.mark_queued` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point {id:$id}) SET n.reviewDecision=$decision, n.reviewDec…` |
| `tortoise/review_verdicts.py:319` | `VerdictLayer.pending` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE ??? RETURN properties(n) ORDER BY n.id` |
| `tortoise/review_verdicts.py:451` | `VerdictLayer.decide._promotion_landed` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/review_verdicts.py:482` | `VerdictLayer.decide._roll_back_review_write` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.{} = $prior, n.reviewed = $reviewed` |
| `tortoise/review_verdicts.py:514` | `VerdictLayer.decide` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.{} = 'close', n.reviewed = true` |
| `tortoise/review_verdicts.py:520` | `VerdictLayer.decide` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.reviewDecision=$decision, n.reviewVer…` |
| `tortoise/review_verdicts.py:532` | `VerdictLayer.decide` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.reviewVerdict=$verdict, n.reviewVerdi…` |
| `tortoise/review_verdicts.py:572` | `VerdictLayer.sweep` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {batch_id:$b}) WHERE n.temporal_candidate = true RETU…` |
| `tortoise/review_verdicts.py:579` | `VerdictLayer.sweep` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.temporal_candidate = true AND n.temporal_rev…` |
| `tortoise/sdk.py:3093` | `_capture_turn_ids` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) WHERE t.is_episod…` |
| `tortoise/sdk.py:3411` | `_write_capture_turns` | adjacency | write | stays:graph-maintenance | `UNWIND $turns AS turn MERGE (t:Point {id: turn.id}) WITH t, turn, t.…` |
| `tortoise/sdk.py:3675` | `_write_capture_turns` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) WHERE t.id IN $id…` |
| `tortoise/sdk.py:3979` | `_write_capture_turns` | adjacency | write | stays:graph-maintenance | `UNWIND $turns AS turn MERGE (t:Point {id: turn.id}) WITH t, turn, t.…` |
| `tortoise/sdk.py:3996` | `_write_capture_turns` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_redactions=$redactions` |
| `tortoise/sdk.py:4329` | `_write_session_and_turns` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid}) OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point…` |
| `tortoise/sdk.py:4353` | `_write_session_and_turns` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET {}` |
| `tortoise/sdk.py:5041` | `_fetch_rerank_pool_texts` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.{} IN $ids RETURN n.{}, {}` |
| `tortoise/sdk.py:7061` | `_stored_goal_ending_view` | adjacency | read | measure:across shapes (#13083 owns placement) | `??? OPTIONAL MATCH (s)-[:CORRECTS]->(n) RETURN n.status, n.goalState…` |
| `tortoise/sdk.py:8124` | `_raise_update_point_status_error` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:8139` | `_raise_update_point_status_error` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:11527` | `_stamp_capture_provenance` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:11575` | `_capture_stamp_targets` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:11616` | `_capture_ep_target_ids` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids AND n.posterior_alpha IS NULL AND…` |
| `tortoise/sdk.py:11707` | `_apply_capture_ingest_ep` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND (n.is_operator IS NULL OR n.i…` |
| `tortoise/sdk.py:11716` | `_apply_capture_ingest_ep` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids SET n.status = 'live'` |
| `tortoise/sdk.py:11750` | `_apply_capture_ingest_ep` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:11758` | `_apply_capture_ingest_ep` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Point {id:$oid}) SET o.status = 'live'` |
| `tortoise/sdk.py:12310` | `_holds_role_available` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (:Subject)-[r:holdsRole]->(:Subject) RETURN count(r)` |
| `tortoise/sdk.py:12828` | `_refuse_inverted_point_window` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom, n.validTo` |
| `tortoise/sdk.py:13004` | `_money_views_for_update` | plain | read | candidate:SQL-shaped | `MATCH (n{} {}:$id}}) RETURN n.amountMinor, n.currency, n.minorUnitEx…` |
| `tortoise/sdk.py:13063` | `_stored_value_shapes` | plain | read | candidate:SQL-shaped | `MATCH (n{} {}:$id}}) RETURN n.amountMinor, n.currency, n.minorUnitEx…` |
| `tortoise/sdk.py:13117` | `_refuse_inconsistent_validity_precision` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom, n.validTo, n.validFromP…` |
| `tortoise/sdk.py:13687` | `_mechanism_conflicts_for_pairs` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:13823` | `_live_operator_mechanism_pairs` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:15490` | `TortoiseSDK._ensure_registry_indexes` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:{}) ON (n.{})` |
| `tortoise/sdk.py:15662` | `TortoiseSDK.poll_events` | plain | read | candidate:SQL-shaped | `MATCH (m:GraphEventMeta) RETURN m.first_seq` |
| `tortoise/sdk.py:16757` | `TortoiseSDK._event_raw_write_door` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:17657` | `TortoiseSDK.create_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$id})-[:extractedFrom]->(s:Source) RETURN DISTINC…` |
| `tortoise/sdk.py:18385` | `TortoiseSDK.create_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (n:Point {???})` |
| `tortoise/sdk.py:18583` | `TortoiseSDK.resolve_id` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id: $id}) RETURN n.id, n.content, n.pointKind, n.sta…` |
| `tortoise/sdk.py:18595` | `TortoiseSDK.resolve_id` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.content CONTAINS $id_str RETURN n.id, n.cont…` |
| `tortoise/sdk.py:18834` | `TortoiseSDK._stamp_entity_link_counters` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.entity_links_attempted=$a, s.entit…` |
| `tortoise/sdk.py:18874` | `TortoiseSDK._stamp_capture_attempt_state` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_ok=$ok, s.capture_extracto…` |
| `tortoise/sdk.py:19140` | `TortoiseSDK._capture_session` | plain | read | candidate:SQL-shaped | `OPTIONAL MATCH (s:Session {id:$sid}) RETURN count(s) AS n, s.capture…` |
| `tortoise/sdk.py:20007` | `TortoiseSDK._capture_session` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, coalesce(n.pointKind…` |
| `tortoise/sdk.py:20247` | `TortoiseSDK._extract_session_llm` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[:INPUT\|IMPL\|NAND]-(o:Point) WHERE o.is_o…` |
| `tortoise/sdk.py:20298` | `TortoiseSDK._extract_session_llm` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) DETACH DELETE n` |
| `tortoise/sdk.py:20387` | `TortoiseSDK._extract_session_llm` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/sdk.py:20505` | `TortoiseSDK._read_capture_passthrough_props` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$pid}) RETURN {}` |
| `tortoise/sdk.py:21110` | `TortoiseSDK._extract_session_v2` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE ({}) AND o.name = $exact RETURN o.id` |
| `tortoise/sdk.py:21134` | `TortoiseSDK._extract_session_v2` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}), (o:Object) WHERE (({}) AND o.name = $exac…` |
| `tortoise/sdk.py:21140` | `TortoiseSDK._extract_session_v2` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/sdk.py:21245` | `TortoiseSDK._extract_session_v2` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.content, n.eventId` |
| `tortoise/sdk.py:21269` | `TortoiseSDK._extract_session_v2` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/sdk.py:21846` | `TortoiseSDK._materialize_session_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$url}) SET ??? RETURN s.validFrom, s.is_episodi…` |
| `tortoise/sdk.py:22897` | `TortoiseSDK.update_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.content, n.search_keys` |
| `tortoise/sdk.py:23018` | `TortoiseSDK.update_point` | plain | read | candidate:SQL-shaped | `OPTIONAL MATCH (n:Point {id:$id}) RETURN count(n), coalesce(head(col…` |
| `tortoise/sdk.py:23047` | `TortoiseSDK.update_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:23295` | `TortoiseSDK.update_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point:Object {id:$id}) RETURN count(n) > 0` |
| `tortoise/sdk.py:23531` | `TortoiseSDK.update_point._promote_witness` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status, n.outdated` |
| `tortoise/sdk.py:23628` | `TortoiseSDK.update_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point:Object {id:$id}) WHERE (n.status IS NULL OR n.status …` |
| `tortoise/sdk.py:23661` | `TortoiseSDK.update_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point:Object {id:$id}) SET n += $props, n.version = coalesc…` |
| `tortoise/sdk.py:23686` | `TortoiseSDK.update_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) WHERE (n.status IS NULL OR n.status = 'draf…` |
| `tortoise/sdk.py:23734` | `TortoiseSDK.update_point._write_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n += $props RETURN count(n)` |
| `tortoise/sdk.py:23773` | `TortoiseSDK.update_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET ??? RETURN count(n)` |
| `tortoise/sdk.py:23939` | `TortoiseSDK.update_point._sync_witness` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) WHERE t.name IN $names …` |
| `tortoise/sdk.py:24043` | `TortoiseSDK.delete_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN count(n) > 0` |
| `tortoise/sdk.py:24052` | `TortoiseSDK.delete_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[:TAGGED]->() RETURN count(*) > 0` |
| `tortoise/sdk.py:24185` | `TortoiseSDK.delete_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) DETACH DELETE n` |
| `tortoise/sdk.py:24283` | `TortoiseSDK.delete_point` | adjacency | write | stays:graph-maintenance | `MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t` |
| `tortoise/sdk.py:24389` | `TortoiseSDK._delete_landed_anyway` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:24478` | `TortoiseSDK._assert_lifecycle_guard` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:24529` | `TortoiseSDK._assert_lifecycle_guard` | plain | read | candidate:SQL-shaped | `MATCH (n:Point:Event {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:24582` | `TortoiseSDK._lifecycle_terminal_marker` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:24663` | `TortoiseSDK._assert_window_start_not_inverted` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom` |
| `tortoise/sdk.py:24822` | `TortoiseSDK.invalidate_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.outdated = true, n.updatedAt = $now, …` |
| `tortoise/sdk.py:24838` | `TortoiseSDK.invalidate_point` | adjacency | write | stays:graph-maintenance | `MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) MERGE (a)-[:COR…` |
| `tortoise/sdk.py:25077` | `TortoiseSDK._supersession_label` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN 1 AS rank, count(n) AS hits UNION MA…` |
| `tortoise/sdk.py:25228` | `TortoiseSDK._supersede_entity._rows` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN count(n) AS rows, collect(n.status) AS …` |
| `tortoise/sdk.py:25264` | `TortoiseSDK._supersede_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN count(n) AS hits` |
| `tortoise/sdk.py:25607` | `TortoiseSDK.supersede_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point)-[r]->(old:Point {id:$old_id}) WHERE (op.is_operator…` |
| `tortoise/sdk.py:25678` | `TortoiseSDK.supersede_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point) WHERE op.id IN $op_ids AND (op.is_operator = true O…` |
| `tortoise/sdk.py:26002` | `TortoiseSDK.supersede_point._direct_incident_edges` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t)-[r:IMPL\|NAND]->(old:Point {id:$old_id}) RETURN type(r), r…` |
| `tortoise/sdk.py:26017` | `TortoiseSDK.supersede_point._direct_incident_edges` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids AND (p.is_operator = true OR p.op…` |
| `tortoise/sdk.py:26042` | `TortoiseSDK.supersede_point._epistemic_label` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN ID(n) LIMIT 1` |
| `tortoise/sdk.py:26075` | `TortoiseSDK.supersede_point._claim_classes_for` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN coalesce(n.claimClass, 'asserted')` |
| `tortoise/sdk.py:26279` | `TortoiseSDK.supersede_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom, n.createdAt, n.validFro…` |
| `tortoise/sdk.py:26304` | `TortoiseSDK.supersede_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom` |
| `tortoise/sdk.py:26414` | `TortoiseSDK.supersede_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (old:Point {id:$old_id})-[ri:INPUT]->(op:Point) WHERE (op.is_o…` |
| `tortoise/sdk.py:26454` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id}), (new:Point {id:$new_id}) MERGE (op)-[:…` |
| `tortoise/sdk.py:26460` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id}), (new:Point {id:$new_id}) MERGE (op)-[:…` |
| `tortoise/sdk.py:26470` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id})-[r:{}]->(old:Point {id:$old_id}) WHERE …` |
| `tortoise/sdk.py:26493` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$new_id}), (op:Point {id:$op_id}) MERGE (new)-[…` |
| `tortoise/sdk.py:26499` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$new_id}), (op:Point {id:$op_id}) MERGE (new)-[…` |
| `tortoise/sdk.py:26504` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old_id})-[ri:INPUT]->(op:Point {id:$op_id}) WH…` |
| `tortoise/sdk.py:26575` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (a)-[r:{}]->(b) WHERE ID(r) = $rid DELETE r` |
| `tortoise/sdk.py:26625` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (t:{} {id:$tid}), (new:{} {id:$new_id}) MERGE (t)-[nr:{}]->(ne…` |
| `tortoise/sdk.py:26632` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a})-[r:{}]->(b:{} {id:$b}) SET r += $attrs` |
| `tortoise/sdk.py:26643` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old_id})-[r:{}]->() WHERE ID(r) = $rid DELETE …` |
| `tortoise/sdk.py:26649` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH ()-[r:{}]->(old:Point {id:$old_id}) WHERE ID(r) = $rid DELETE …` |
| `tortoise/sdk.py:26705` | `TortoiseSDK.supersede_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN ID(n)` |
| `tortoise/sdk.py:26718` | `TortoiseSDK.supersede_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (old:Point {id:$old_id})-[r:{}]->(target) RETURN id(target), t…` |
| `tortoise/sdk.py:26814` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old_id})-[r:{}]->(t) WHERE id(t) = $tid DELETE…` |
| `tortoise/sdk.py:26952` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$new_id}), (t) WHERE id(t) = $tid MERGE (new)-[…` |
| `tortoise/sdk.py:26958` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old_id})-[r:{}]->(t) WHERE id(t) = $tid DELETE…` |
| `tortoise/sdk.py:26971` | `TortoiseSDK.supersede_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'superseded', n.outdated = t…` |
| `tortoise/sdk.py:26982` | `TortoiseSDK.supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) MERGE (a)-[:COR…` |
| `tortoise/sdk.py:27130` | `TortoiseSDK.retract_point._retract_witness` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status, n.outdated` |
| `tortoise/sdk.py:27145` | `TortoiseSDK.retract_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) WHERE {} SET n.status = 'retracted', n.upda…` |
| `tortoise/sdk.py:27226` | `TortoiseSDK.promote_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN properties(n)` |
| `tortoise/sdk.py:27315` | `TortoiseSDK.promote_point._cas_witness` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:27324` | `TortoiseSDK.promote_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) WHERE n.status = 'draft' SET n.status = 'li…` |
| `tortoise/sdk.py:27387` | `TortoiseSDK.promote_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.id` |
| `tortoise/sdk.py:27397` | `TortoiseSDK.promote_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->(:Point {id:$cand…` |
| `tortoise/sdk.py:27438` | `TortoiseSDK.promote_point` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:27480` | `TortoiseSDK.promote_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:NAND]->(:Point {id:$cand}), (o…` |
| `tortoise/sdk.py:27490` | `TortoiseSDK.promote_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->(:Point {id:$cand…` |
| `tortoise/sdk.py:27588` | `TortoiseSDK._promote_incident_operators` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:27603` | `TortoiseSDK._promote_incident_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$oid})-[r]->(s) WHERE {} RETURN s.id, s.status, c…` |
| `tortoise/sdk.py:27645` | `TortoiseSDK._promote_incident_operators` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Point {id:$oid}) WHERE o.status = 'draft' SET o.status = 'l…` |
| `tortoise/sdk.py:27761` | `TortoiseSDK.belief_timeline` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {pointKind:'decision'})-[:aboutObject]->(o:Object) WH…` |
| `tortoise/sdk.py:27776` | `TortoiseSDK.belief_timeline` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (cur:Point)-[:CORRECTS]->(old:Point {pointKind:'decision'}) WH…` |
| `tortoise/sdk.py:27802` | `TortoiseSDK.belief_timeline` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:NAND]->(:Point {id:$a}), (op)-…` |
| `tortoise/sdk.py:27812` | `TortoiseSDK.belief_timeline` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (:Point {id:$a})-[:CORRECTS]->(:Point {id:$b}) RETURN count(*)` |
| `tortoise/sdk.py:27884` | `TortoiseSDK._dedup_content_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND (n.is_operator IS NULL OR n.i…` |
| `tortoise/sdk.py:27938` | `TortoiseSDK._dedup_content_candidates` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.dedup_candidate = true, n.dedup_metho…` |
| `tortoise/sdk.py:27946` | `TortoiseSDK._dedup_content_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:28006` | `TortoiseSDK._list_dedup_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.dedup_candidate = true AND n.dedup_reviewed …` |
| `tortoise/sdk.py:28031` | `TortoiseSDK._list_dedup_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.temporal_candidate = true AND n.temporal_rev…` |
| `tortoise/sdk.py:28112` | `TortoiseSDK.approve_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id: $id}) RETURN labels(n), properties(n)` |
| `tortoise/sdk.py:28125` | `TortoiseSDK.approve_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN properties(n)` |
| `tortoise/sdk.py:28148` | `TortoiseSDK.approve_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.temporal_reviewed = $a, n.reviewed = …` |
| `tortoise/sdk.py:28165` | `TortoiseSDK.approve_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.dedup_reviewed = $a, n.reviewed = tru…` |
| `tortoise/sdk.py:28172` | `TortoiseSDK.approve_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:28178` | `TortoiseSDK.approve_merge` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->(:Point {id:$cand…` |
| `tortoise/sdk.py:28286` | `TortoiseSDK._approve_entity_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.dedup_candidate, n.temporal_candid…` |
| `tortoise/sdk.py:28342` | `TortoiseSDK._approve_entity_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.id` |
| `tortoise/sdk.py:28968` | `TortoiseSDK.create_operator` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:29134` | `TortoiseSDK.create_operator` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (o:Point {id:$id, is_operator:true, op_type:$op, direction:$d…` |
| `tortoise/sdk.py:29169` | `TortoiseSDK.create_operator` | adjacency | write | stays:graph-maintenance | `MATCH (o:Point {id:$oid}), (s) WHERE {} AND s.id = $sid CREATE (o)-[…` |
| `tortoise/sdk.py:29194` | `TortoiseSDK.create_operator` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Point {id:$sid}) WHERE s.status IS NULL SET s.status = 'liv…` |
| `tortoise/sdk.py:29440` | `TortoiseSDK.mitigate_operator` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {id:$id})-[r:mitigated_by]->(m:Point) WHERE {} RETUR…` |
| `tortoise/sdk.py:29569` | `TortoiseSDK.mitigate_operator` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (m:Point {id:$id, content:$c, pointKind:'statement', mitigati…` |
| `tortoise/sdk.py:29581` | `TortoiseSDK.mitigate_operator` | adjacency | write | stays:graph-maintenance | `MATCH (m:Point {id:$mid}), (op:Point {id:$oid}) CREATE (m)-[:IMPL]->…` |
| `tortoise/sdk.py:29720` | `TortoiseSDK.query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN properties(n)` |
| `tortoise/sdk.py:29793` | `TortoiseSDK.paginated_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN count(n)` |
| `tortoise/sdk.py:29816` | `TortoiseSDK.paginated_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN properties(n) ORDER BY n.createdAt D…` |
| `tortoise/sdk.py:29915` | `TortoiseSDK.get_point._cache_answer` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN properties(n)` |
| `tortoise/sdk.py:30641` | `TortoiseSDK.summarize_structure` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:$k}) WHERE (n.is_operator IS NULL OR n.is_…` |
| `tortoise/sdk.py:30647` | `TortoiseSDK.summarize_structure` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:30652` | `TortoiseSDK.summarize_structure` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator = true OR n.op_type IS NOT NULL…` |
| `tortoise/sdk.py:30678` | `TortoiseSDK._list_pointkinds` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:30706` | `TortoiseSDK._list_sources` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source) WHERE s.documentKind IS NULL OPTIONAL MATCH (p:Poin…` |
| `tortoise/sdk.py:30725` | `TortoiseSDK._list_tags` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t:Tag) OPTIONAL MATCH (p:Point)-[:TAGGED]->(t) RETURN t.name …` |
| `tortoise/sdk.py:30743` | `TortoiseSDK.query_points_by_tag` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag}) RETURN properties(p) …` |
| `tortoise/sdk.py:31298` | `TortoiseSDK._point_bearing_ids` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids AND coalesce(p.is_operator, false…` |
| `tortoise/sdk.py:31751` | `TortoiseSDK._fetch_endpoint_info` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE n.id IN $vals OR n.url IN $vals OR n.eventId IN $val…` |
| `tortoise/sdk.py:31795` | `TortoiseSDK._terminal_holder_among` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND {} RETURN n.id LIMIT 1` |
| `tortoise/sdk.py:31951` | `TortoiseSDK._find_terminal_dedup_hit` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {content_hash:$ch}) WHERE {} RETURN n.id LIMIT 1` |
| `tortoise/sdk.py:31968` | `TortoiseSDK._find_terminal_dedup_hit` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {content_hash:$ch}) WHERE {} RETURN n.id LIMIT 1` |
| `tortoise/sdk.py:31985` | `TortoiseSDK._find_terminal_dedup_hit` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN n.id LIMIT 1` |
| `tortoise/sdk.py:32098` | `TortoiseSDK._terminal_content_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} AND n.content_hash IN $hashes RETURN n.cont…` |
| `tortoise/sdk.py:32136` | `TortoiseSDK._terminal_content_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} AND n.content_hash IS NULL AND n.content IN…` |
| `tortoise/sdk.py:32290` | `TortoiseSDK._entity_name_holders` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, {} AS term, coalesce(n.…` |
| `tortoise/sdk.py:34916` | `TortoiseSDK.ingest` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN count(s)` |
| `tortoise/sdk.py:35464` | `TortoiseSDK.ingest` | adjacency | write | stays:graph-maintenance | `MATCH (o:Point {id:$oid}), (t) WHERE {} AND t.id = $inp MERGE (o)-[r…` |
| `tortoise/sdk.py:35552` | `TortoiseSDK.ingest` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$pid})-[:extractedFrom]->(s:Source {url:$url}) RE…` |
| `tortoise/sdk.py:35573` | `TortoiseSDK.ingest` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a)-[r:{}]->(b) WHERE (a.id = $f OR a.eventId = $f OR a.url = …` |
| `tortoise/sdk.py:35675` | `TortoiseSDK._stamp_batch_id` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.batch_id` |
| `tortoise/sdk.py:35685` | `TortoiseSDK._stamp_batch_id` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.batch_id = $bid` |
| `tortoise/sdk.py:35791` | `TortoiseSDK.list_batch` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {batch_id:$bid}) RETURN n.id, n.pointKind, n.status, …` |
| `tortoise/sdk.py:35804` | `TortoiseSDK.list_batch` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a)-[r:IMPL\|NAND]->(b) WHERE {} AND {} AND r.batch_id = $bid …` |
| `tortoise/sdk.py:35846` | `TortoiseSDK.list_batches` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.batch_id IS NOT NULL WITH n.batch_id AS bid,…` |
| `tortoise/sdk.py:35854` | `TortoiseSDK.list_batches` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r:IMPL\|NAND]->() WHERE r.batch_id IS NOT NULL WITH r.batc…` |
| `tortoise/sdk.py:35984` | `TortoiseSDK.create_direct_edge` | plain | read | candidate:SQL-shaped | `MATCH (p:{}) WHERE p.id IN $ids RETURN p.id, coalesce(p.is_operator,…` |
| `tortoise/sdk.py:36082` | `TortoiseSDK.create_direct_edge` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:{} {id:$src})-[r:{}]->(b:{} {id:$tgt}) RETURN count(r)` |
| `tortoise/sdk.py:36094` | `TortoiseSDK.create_direct_edge` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$src}), (b:{} {id:$tgt}) MERGE (a)-[r:{}]->(b)` |
| `tortoise/sdk.py:36109` | `TortoiseSDK.create_direct_edge` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$src})-[r:{}]->(b:{} {id:$tgt}) SET r += $attrs` |
| `tortoise/sdk.py:36122` | `TortoiseSDK.create_direct_edge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Point {id:$id}) WHERE s.status IS NULL SET s.status = 'live…` |
| `tortoise/sdk.py:36372` | `TortoiseSDK._find_operator` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true, op_type:$op}) OPTIONAL MATCH (o)-[…` |
| `tortoise/sdk.py:36467` | `TortoiseSDK._find_operator` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true, op_type:$op}) OPTIONAL MATCH (o)-[…` |
| `tortoise/sdk.py:36554` | `TortoiseSDK._apply_ingest_mitigation` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {id:$oid})-[:mitigated_by]->(m:Point) RETURN m.id, m…` |
| `tortoise/sdk.py:36614` | `TortoiseSDK._reapply_operator_promotion` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Point {id:$id}) WHERE s.status IS NULL SET s.status = 'live…` |
| `tortoise/sdk.py:36819` | `TortoiseSDK._file_human_approval` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND n.is_operator = false RETURN …` |
| `tortoise/sdk.py:37002` | `TortoiseSDK._raw_entry_for_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$pid})-[:extractedFrom]->(src:Source) RETURN prop…` |
| `tortoise/sdk.py:37055` | `TortoiseSDK._provenance` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject {id:$sid}) RETURN properties(s)` |
| `tortoise/sdk.py:37078` | `TortoiseSDK._provenance` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r), labels(n)[0], p…` |
| `tortoise/sdk.py:37379` | `TortoiseSDK._cross_lens_pool` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:extractedFrom]->(s:Source) WHERE p.is_operator = f…` |
| `tortoise/sdk.py:37435` | `TortoiseSDK._cross_lens_pairs` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids AND p.embedding IS NOT NULL RETUR…` |
| `tortoise/sdk.py:37514` | `TortoiseSDK._review_pool` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.is_operator = false AND {} AND (n.outdated I…` |
| `tortoise/sdk.py:37631` | `TortoiseSDK._connected_pairs` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r1]->(a:Point), (op)-[r2]->(b:P…` |
| `tortoise/sdk.py:37640` | `TortoiseSDK._connected_pairs` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point)-[r]-(b:Point) WHERE a.id IN $ids AND b.id IN $ids AN…` |
| `tortoise/sdk.py:37739` | `TortoiseSDK._epistemic_edges` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r:IMPL\|NAND]->(p:Point) RETURN…` |
| `tortoise/sdk.py:37767` | `TortoiseSDK._epistemic_edges` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point)-[r:IMPL\|NAND]->(b:Point) WHERE a.is_operator = fals…` |
| `tortoise/sdk.py:37797` | `TortoiseSDK._review_prune` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.status, coalesce(n…` |
| `tortoise/sdk.py:37811` | `TortoiseSDK._review_prune` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Point)-[:CORRECTS]->(o:Point) WHERE o.id IN $ids RETURN o.i…` |
| `tortoise/sdk.py:37848` | `TortoiseSDK._review_prune` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.is_operator = false AND {} AND {} AND {} WIT…` |
| `tortoise/sdk.py:37897` | `TortoiseSDK._review_prune` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r:NAND]->(n:Point) WHERE n.is_o…` |
| `tortoise/sdk.py:38170` | `TortoiseSDK._retry_cap_verdict` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id = $id AND n.ep_dirty = true WITH n, (n.ep…` |
| `tortoise/sdk.py:38214` | `TortoiseSDK._prune_nonexistent_dirty_roots` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:38535` | `TortoiseSDK._stamp_counts` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:38681` | `TortoiseSDK._propagation_counts` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:IMPL\|NAND]-() WHERE (n.is_operator IS NULL OR n.i…` |
| `tortoise/sdk.py:38690` | `TortoiseSDK._propagation_counts` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[r:IMPL\|NAND]-(m) WHERE (n.is_operator IS NULL OR n…` |
| `tortoise/sdk.py:38812` | `TortoiseSDK._ep_epoch` | ep | read | stays:ep-carveout (§6.3) | `MATCH (m:EpMeta) RETURN m.ep_version` |
| `tortoise/sdk.py:38835` | `TortoiseSDK._hydrate_dirty_roots` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.ep_dirty = true AND n.id IS NOT NULL AND NOT…` |
| `tortoise/sdk.py:38951` | `TortoiseSDK._sweep_dirty_roots` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids AND n.ep_dirty = true WITH n, ($r…` |
| `tortoise/sdk.py:39056` | `TortoiseSDK._unsweepable_dirty_roots` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, (n.is_operator = tru…` |
| `tortoise/sdk.py:39082` | `TortoiseSDK._unsweepable_dirty_roots` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point)-[:IMPL\|NAND]->(n:Point) WHERE n.id IN $ids AND (o.i…` |
| `tortoise/sdk.py:39104` | `TortoiseSDK._unsweepable_dirty_roots` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:IMPL\|NAND]-(d) WHERE n.id IN $ids AND {} RETURN D…` |
| `tortoise/sdk.py:39133` | `TortoiseSDK._reverse_bfs_neighbors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:IMPL\|NAND]->(p:Point) WHERE p…` |
| `tortoise/sdk.py:39151` | `TortoiseSDK._reverse_bfs_neighbors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:IMPL\|NAND]->(op:Point {is_operator:true}) WHERE p…` |
| `tortoise/sdk.py:39177` | `TortoiseSDK._reverse_bfs_neighbors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:IMPL\|NAND]->(c) WHERE op.id I…` |
| `tortoise/sdk.py:39234` | `TortoiseSDK._advance_ep_version` | ep | write | stays:ep-carveout (§6.3) | `MERGE (m:EpMeta) SET m.ep_version = coalesce(m.ep_version, 0) + 1 RE…` |
| `tortoise/sdk.py:39319` | `TortoiseSDK._mark_dirty` | ep | write | stays:ep-carveout (§6.3) | `UNWIND $ids AS pid MATCH (n:Point {id: pid}) WHERE NOT coalesce(n.st…` |
| `tortoise/sdk.py:39351` | `TortoiseSDK._mark_dirty` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:39365` | `TortoiseSDK._mark_dirty` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids AND n.ep_dirty = true SET n.ep_di…` |
| `tortoise/sdk.py:39591` | `TortoiseSDK._auto_dream_mode` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {is_operator:true}) RETURN count(n)` |
| `tortoise/sdk.py:39661` | `TortoiseSDK._count_ep_factors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:IMPL\|NAND]->(t:Point) WHERE (…` |
| `tortoise/sdk.py:39716` | `TortoiseSDK._count_ep_factors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point)-[r:IMPL\|NAND]->(b:Point) WHERE (a.is_operator IS NU…` |
| `tortoise/sdk.py:40053` | `TortoiseSDK._window_closure` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:40091` | `TortoiseSDK._window_closure._hop` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:40116` | `TortoiseSDK._window_closure._hop` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:40141` | `TortoiseSDK._window_closure` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point)-[r:IMPL\|NAND]->(c) WHERE o.id IN $ids AND {} AND (o…` |
| `tortoise/sdk.py:40207` | `TortoiseSDK._reachable_claim_count` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:40241` | `TortoiseSDK._edged_claim_ids` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n)-[r:IMPL\|NAND]-(m) WHERE {} AND {} AND (n.is_operator IS N…` |
| `tortoise/sdk.py:40625` | `TortoiseSDK.compute_confidence` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:40827` | `TortoiseSDK._hydrate_evidence` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NU…` |
| `tortoise/sdk.py:40899` | `TortoiseSDK._stamp_authored_word` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET {} RETURN n.id` |
| `tortoise/sdk.py:41052` | `TortoiseSDK.set_point_baseline` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point {id: $id}) SET n.ep_alpha = $a, n.ep_beta = $b, n.bas…` |
| `tortoise/sdk.py:41109` | `TortoiseSDK.set_point_baseline` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:41116` | `TortoiseSDK.set_point_baseline` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE n.id = $id RETURN labels(n)` |
| `tortoise/sdk.py:41218` | `TortoiseSDK._invalidate_inheritance_gate` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids REMOVE n.inherited_at` |
| `tortoise/sdk.py:41388` | `TortoiseSDK._apply_source_inheritance` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {pointKind:'assessment'}) WHERE (p.outdated IS NULL O…` |
| `tortoise/sdk.py:41428` | `TortoiseSDK._apply_source_inheritance` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:extractedFrom]->(s:Source) WHERE (n.is_operator IS…` |
| `tortoise/sdk.py:41572` | `TortoiseSDK._apply_source_inheritance` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source)-[:references]->(o:Source) RETURN s.url, o.url` |
| `tortoise/sdk.py:41587` | `TortoiseSDK._apply_source_inheritance` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:extractedFrom]->(s:Source) WHERE n.id IN $ids RETU…` |
| `tortoise/sdk.py:41615` | `TortoiseSDK._apply_source_inheritance` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.baseline_source = 'inherited-from-source' AN…` |
| `tortoise/sdk.py:41638` | `TortoiseSDK._apply_source_inheritance` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point {id:$id}) REMOVE n.ep_alpha, n.ep_beta, n.baseline_se…` |
| `tortoise/sdk.py:41683` | `TortoiseSDK._apply_source_inheritance` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point {id:$id}) RETURN n.baseline_source, n.inherited_at, c…` |
| `tortoise/sdk.py:41849` | `TortoiseSDK._touch_inherited_at` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.inherited_at = $ts` |
| `tortoise/sdk.py:41917` | `TortoiseSDK.calibrate_summary` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE {} AND n.id IS NOT NULL AND toLower(typeof(n.i…` |
| `tortoise/sdk.py:42373` | `TortoiseSDK._uncalibrated_ep_participants` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids AND {} RETURN n.id` |
| `tortoise/sdk.py:42497` | `TortoiseSDK._find_points_by_key` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN n.id` |
| `tortoise/sdk.py:42833` | `TortoiseSDK._fold_source_evidence` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.extractedFrom = $ref, n.extractedFrom…` |
| `tortoise/sdk.py:42946` | `TortoiseSDK._find_point_by_content` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {content_hash:$ch}) WHERE {} RETURN n.id` |
| `tortoise/sdk.py:42966` | `TortoiseSDK._find_point_by_content` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN n.id` |
| `tortoise/sdk.py:43194` | `TortoiseSDK._semantic_dedup` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:$kind}) WHERE (n.is_operator IS NULL OR n.…` |
| `tortoise/sdk.py:43265` | `TortoiseSDK._diary_read` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:'diary', authoredBy:$agent, wing:$wing}) W…` |
| `tortoise/sdk.py:43272` | `TortoiseSDK._diary_read` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:'diary', authoredBy:$agent}) WHERE n.is_op…` |
| `tortoise/sdk.py:43288` | `TortoiseSDK._status` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n) LIMIT 1` |
| `tortoise/sdk.py:43506` | `TortoiseSDK.ingest_corpus` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN properties(e)` |
| `tortoise/sdk.py:43775` | `TortoiseSDK.ingest_corpus` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.file_hash` |
| `tortoise/sdk.py:43925` | `TortoiseSDK._suggest_entry_points` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN n.id, n.content, n.pointKind` |
| `tortoise/sdk.py:44015` | `TortoiseSDK.session_context` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:'diary'}) WHERE n.is_operator = false RETU…` |
| `tortoise/sdk.py:44020` | `TortoiseSDK.session_context` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_operator = false RETURN properties(n) ORD…` |
| `tortoise/sdk.py:44025` | `TortoiseSDK.session_context` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT…` |
| `tortoise/sdk.py:44037` | `TortoiseSDK.session_context` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.confidence IS NOT NULL AND n.is_operator = f…` |
| `tortoise/sdk.py:44135` | `TortoiseSDK.issue_insight` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:44244` | `TortoiseSDK.issue_insight` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:44567` | `TortoiseSDK.annotate_ask_hits` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (ev:Event) WHERE e…` |
| `tortoise/sdk.py:45515` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/sdk.py:46198` | `TortoiseSDK.tortoise_fts_query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:46505` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, n.{}` |
| `tortoise/sdk.py:46561` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.{} = $kind {} AND n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:46570` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.{} IN [{}] {} AND n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:46619` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND n.status IN $statuses RETURN …` |
| `tortoise/sdk.py:46689` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.pointKind, n.conte…` |
| `tortoise/sdk.py:47378` | `TortoiseSDK.tortoise_fts_query` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (sess:Session)-[:C…` |
| `tortoise/sdk.py:47503` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Event) WHERE n.eventId IN $ids RETURN n.eventId, n.subject,…` |
| `tortoise/sdk.py:47514` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Subject) WHERE n.id IN $ids RETURN n.id, n.name, n.subjectK…` |
| `tortoise/sdk.py:47525` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Source) WHERE n.url IN $ids AND n.documentKind IS NOT NULL …` |
| `tortoise/sdk.py:47550` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Object) WHERE n.id IN $ids RETURN n.id, n.name, n.objectKin…` |
| `tortoise/sdk.py:47571` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {is_operator: true}) WHERE n.id IN $ids RETURN n.id, …` |
| `tortoise/sdk.py:47582` | `TortoiseSDK.tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Source) WHERE n.url IN $ids RETURN n.url, n.title, n.source…` |
| `tortoise/sdk.py:47979` | `TortoiseSDK._search_keys_prf_expansion` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, coalesce(n.search_ke…` |
| `tortoise/sdk.py:48129` | `TortoiseSDK._entity_key_alias_harvest` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Object) WHERE o.id IN $ids OPTIONAL MATCH (o)<-[:aboutObjec…` |
| `tortoise/sdk.py:48469` | `TortoiseSDK._expand_relationships` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:48630` | `TortoiseSDK.get_historical_knowledge` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id:$id}) RETURN p.content, p.validFrom, p.validTo, p…` |
| `tortoise/sdk.py:48659` | `TortoiseSDK.get_historical_knowledge` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$id})-[:CORRECTS]->(old:Point) RETURN old.id ORDE…` |
| `tortoise/sdk.py:49279` | `TortoiseSDK._state_counter_evidence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (c:Point)-[r:NAND]->(n:Point) WHERE n.id IN $ids RETURN n.id, …` |
| `tortoise/sdk.py:49311` | `TortoiseSDK._state_arguments` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r:IMPL\|NAND\|hasPart]-(n:Point…` |
| `tortoise/sdk.py:49359` | `TortoiseSDK._state_mitigations` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r:mitigated_by]->(m:Point) WHER…` |
| `tortoise/sdk.py:49393` | `TortoiseSDK._state_related_objects` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[a:???]->(t) WHERE n.id IN $ids RETURN n.id, labels(…` |
| `tortoise/sdk.py:49410` | `TortoiseSDK._state_related_points` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[a:???]->(o:Object) WHERE o.id IN $ids RETURN o.id, …` |
| `tortoise/sdk.py:49508` | `TortoiseSDK.recall_gaps` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {is_operator:true}) WHERE n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:49524` | `TortoiseSDK.recall_gaps` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:49622` | `TortoiseSDK._resolve_subgraph_seed` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n.id = $seed OR n.url = $seed) AND labels(n)[0] IN …` |
| `tortoise/sdk.py:49727` | `TortoiseSDK.org_create` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {idempotency_key:$ik}) RETURN t.id, t.name` |
| `tortoise/sdk.py:49752` | `TortoiseSDK.org_create` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {name:$name}) RETURN count(t) > 0` |
| `tortoise/sdk.py:49777` | `TortoiseSDK.org_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (t:Team {id:$id, name:$name, ???graph_name:$gn, createdAt:$no…` |
| `tortoise/sdk.py:49786` | `TortoiseSDK.org_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.idempotency_key = $ik` |
| `tortoise/sdk.py:49812` | `TortoiseSDK.org_create` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active'}) WHERE m.org_id …` |
| `tortoise/sdk.py:49824` | `TortoiseSDK.org_create` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.graph_name` |
| `tortoise/sdk.py:49866` | `TortoiseSDK.org_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) DETACH DELETE t` |
| `tortoise/sdk.py:49918` | `TortoiseSDK._graph_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (g:Graph {id:$gid, org_id:$tid, name:$name, kind:$kind, names…` |
| `tortoise/sdk.py:49945` | `TortoiseSDK.list_memory_graphs` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN properties(g) ORDER BY CASE g.k…` |
| `tortoise/sdk.py:50012` | `TortoiseSDK._graph_count` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) WHERE g.status IS NULL OR g.status <> …` |
| `tortoise/sdk.py:50030` | `TortoiseSDK._graph_delete` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.kind` |
| `tortoise/sdk.py:50039` | `TortoiseSDK._graph_delete` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) SET g.status = 'deleted', g.d…` |
| `tortoise/sdk.py:50065` | `TortoiseSDK.restore_memory_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) WHERE g.status = 'deleted' AN…` |
| `tortoise/sdk.py:50081` | `TortoiseSDK._trash_graphs` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid, status:'deleted'}) WHERE coalesce(g.kin…` |
| `tortoise/sdk.py:50160` | `TortoiseSDK.update_memory_graph` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.id, g.kind, coalesce(g.status…` |
| `tortoise/sdk.py:50178` | `TortoiseSDK.update_memory_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) SET g.name = $name` |
| `tortoise/sdk.py:50183` | `TortoiseSDK.update_memory_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) SET g.recording = $v` |
| `tortoise/sdk.py:50195` | `TortoiseSDK._graph_key_ids` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, graph_id:$gid}) RETURN k.id` |
| `tortoise/sdk.py:50217` | `TortoiseSDK._graph_active_key_count` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, graph_id:$gid}) WHERE k.revoked_at IS …` |
| `tortoise/sdk.py:50227` | `TortoiseSDK._org_get` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/sdk.py:50414` | `TortoiseSDK._membership_create` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid}) WHERE m.status = 'active' RETURN …` |
| `tortoise/sdk.py:50427` | `TortoiseSDK._membership_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (m:Membership {id:$id, user_id:$uid, org_id:$tid, role:$role,…` |
| `tortoise/sdk.py:50434` | `TortoiseSDK._membership_create` | adjacency | write | stays:graph-maintenance | `MATCH (m:Membership {id:$mid}), (t:Team {id:$tid}) CREATE (m)-[:BELO…` |
| `tortoise/sdk.py:50465` | `TortoiseSDK._verify_hashed_lookup` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.key_prefix = $prefix RETURN n.{}, properties(n)` |
| `tortoise/sdk.py:50479` | `TortoiseSDK._verify_hashed_lookup` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN n.{}, properties(n)` |
| `tortoise/sdk.py:50574` | `TortoiseSDK._apikey_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp,…` |
| `tortoise/sdk.py:50581` | `TortoiseSDK._apikey_create` | adjacency | write | stays:graph-maintenance | `MATCH (k:APIKey {id:$kid}), (t:Team {id:$tid}) CREATE (k)-[:BELONGS_…` |
| `tortoise/sdk.py:50595` | `TortoiseSDK._apikey_revoke` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id:$id}) RETURN k.revoked_at, k.org_id` |
| `tortoise/sdk.py:50604` | `TortoiseSDK._apikey_revoke` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now` |
| `tortoise/sdk.py:50665` | `TortoiseSDK._apikey_verify` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.namespace` |
| `tortoise/sdk.py:50715` | `TortoiseSDK._signup_token_lookup` | plain | read | candidate:SQL-shaped | `MATCH (n:SignupToken {lookup_key:$lk}) RETURN properties(n)` |
| `tortoise/sdk.py:50776` | `TortoiseSDK._signup_token_recover` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp,…` |
| `tortoise/sdk.py:50806` | `TortoiseSDK._signup_token_recover` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.exp…` |
| `tortoise/sdk.py:50818` | `TortoiseSDK._signup_token_recover` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now` |
| `tortoise/sdk.py:50849` | `TortoiseSDK._signup_token_revoke` | plain | read | candidate:SQL-shaped | `MATCH (n:SignupToken {lookup_key:$lk}) RETURN properties(n)` |
| `tortoise/sdk.py:50875` | `TortoiseSDK._signup_token_revoke` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:SignupToken {lookup_key:$lk, org_id:$tid}) SET n.revoked_at…` |
| `tortoise/sdk.py:50881` | `TortoiseSDK._signup_token_revoke` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:SignupToken {token_hash:$th, org_id:$tid}) SET n.revoked_at…` |
| `tortoise/sdk.py:50897` | `TortoiseSDK._invitation_list` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {org_id:$tid}) RETURN i.id, i.email, i.role, i.c…` |
| `tortoise/sdk.py:50914` | `TortoiseSDK._invitation_get_by_id` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN properties(i)` |
| `tortoise/sdk.py:50929` | `TortoiseSDK._invitation_revoke` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.status = 'revoked'` |
| `tortoise/sdk.py:50957` | `TortoiseSDK._sweep_invite_ghost_memberships` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership) WHERE m.user_id STARTS WITH 'invite-' OPTIONAL …` |
| `tortoise/sdk.py:50978` | `TortoiseSDK._sweep_invite_ghost_memberships` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) DELETE m` |
| `tortoise/sdk.py:51846` | `TortoiseSDK._label_reachable_by` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {{}:$id}) RETURN count(n)` |
| `tortoise/sdk.py:52113` | `TortoiseSDK._refuse_ambiguous_rename` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id: $id}) RETURN n.name` |
| `tortoise/sdk.py:52453` | `TortoiseSDK._assert_supersession_names_a_successor` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN n.id AS id, n.status AS st, n.supersede…` |
| `tortoise/sdk.py:52509` | `TortoiseSDK._assert_supersession_names_a_successor` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id AS id, ID(n) AS nid, …` |
| `tortoise/sdk.py:52552` | `TortoiseSDK._assert_supersession_names_a_successor` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE ID(n) IN $nids WITH n MATCH (n)<-[:CORRECTS]-(…` |
| `tortoise/sdk.py:53030` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE e.eventId = $id OR e.id = $id RETURN count(e)` |
| `tortoise/sdk.py:53077` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id = $id OR n.eventId = $id OR n.url = $id R…` |
| `tortoise/sdk.py:53186` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:53240` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE e.eventId = $id OR e.id = $id RETURN count(e)` |
| `tortoise/sdk.py:53358` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN 1 AS rank, count(n) AS hits, sum(CAS…` |
| `tortoise/sdk.py:53433` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN collect(DISTINCT n.status)…` |
| `tortoise/sdk.py:53883` | `TortoiseSDK._update_entity` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids SET n += $s RETURN count(n)` |
| `tortoise/sdk.py:54196` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN count(n) AS hits, collect(DISTINCT n.st…` |
| `tortoise/sdk.py:54529` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:Subject {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:54828` | `TortoiseSDK._update_entity` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) SET n += $p RETURN count(n)` |
| `tortoise/sdk.py:54883` | `TortoiseSDK._update_entity` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) SET n += $props RETURN [k IN $keys \| properti…` |
| `tortoise/sdk.py:54937` | `TortoiseSDK._delete_entity` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) DETACH DELETE n RETURN count(n)` |
| `tortoise/sdk.py:55318` | `TortoiseSDK.create_entity._np_lands` | plain | read | candidate:SQL-shaped | `MATCH (n:Object {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:55969` | `TortoiseSDK._nudge_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE NOT n.id IN $ex RETURN n.id, n.content, n.name…` |
| `tortoise/sdk.py:56220` | `TortoiseSDK._search_sessions` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE e.eventId IN $ids RETURN properties(e)` |
| `tortoise/sdk.py:56306` | `TortoiseSDK._search_sessions` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE {} RETURN properties(e) ORDER BY e.startedAt D…` |
| `tortoise/sdk.py:56321` | `TortoiseSDK.get_events` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventKind: $ek}) RETURN properties(e) ORDER BY e.sta…` |
| `tortoise/sdk.py:56327` | `TortoiseSDK.get_events` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT…` |
| `tortoise/sdk.py:56372` | `TortoiseSDK.get_session` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventKind: 'AgentSession'}) WHERE e.session_id = $si…` |
| `tortoise/sdk.py:56406` | `TortoiseSDK._read_captured_session` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN s.id, s.created_at, s.turn_count,…` |
| `tortoise/sdk.py:56436` | `TortoiseSDK._read_captured_session` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE (p.pointKin…` |
| `tortoise/sdk.py:56502` | `TortoiseSDK._read_captured_session` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'…` |
| `tortoise/sdk.py:56559` | `TortoiseSDK._read_captured_session` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE (p.pointKin…` |
| `tortoise/sdk.py:56571` | `TortoiseSDK._read_captured_session` | plain | read | candidate:SQL-shaped | `MATCH (src:Source {url:$url}) RETURN src.url, src.sourceKind, src.ev…` |
| `tortoise/sdk.py:57028` | `TortoiseSDK._index_gate_read` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.contentHash, s.version` |
| `tortoise/sdk.py:57037` | `TortoiseSDK._index_gate_read` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.embedding, e.embeddingRepair…` |
| `tortoise/sdk.py:57050` | `TortoiseSDK._index_gate_read` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$did}) WHERE s.documentKind IS NOT NULL RETURN …` |
| `tortoise/sdk.py:57056` | `TortoiseSDK._index_gate_read` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source {url:$url})-[:references]->(n) RETURN count(n)` |
| `tortoise/sdk.py:57247` | `TortoiseSDK._index_process_unit._sf_lookup` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.source_file` |
| `tortoise/sdk.py:57489` | `TortoiseSDK._index_process_unit` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.embedding` |
| `tortoise/sdk.py:57515` | `TortoiseSDK._index_process_unit` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.embedding` |
| `tortoise/sdk.py:57581` | `TortoiseSDK._index_source_merge` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.__runId, s.version` |
| `tortoise/sdk.py:57590` | `TortoiseSDK._index_source_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) REMOVE s.__runId` |
| `tortoise/sdk.py:57696` | `TortoiseSDK._session_event_write` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId:$eid}) ON CREATE SET e += $props, ??? ON MAT…` |
| `tortoise/sdk.py:57719` | `TortoiseSDK._record_embedding_marker` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId:$eid}) SET e.embeddingRepairFailedAt = $ts` |
| `tortoise/sdk.py:57904` | `TortoiseSDK._embedding_repair_attempt` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId:$eid}) SET e.embeddingRepairFailedAt = $ts` |
| `tortoise/sdk.py:57914` | `TortoiseSDK._embedding_repair_attempt` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId:$eid}) ???SET ???, e.embeddingRepairFailedAt…` |
| `tortoise/sdk.py:57922` | `TortoiseSDK._embedding_repair_attempt` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN properties(e)` |
| `tortoise/sdk.py:58011` | `TortoiseSDK._backfill_probe` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN 1` |
| `tortoise/sdk.py:58015` | `TortoiseSDK._backfill_probe` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source {url:$url})-[:references]->(e:Event {eventId:$eid}) …` |
| `tortoise/sdk.py:58093` | `TortoiseSDK.session_index_health` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventKind:'AgentSession'}) RETURN e.eventId, e.file_…` |
| `tortoise/sdk.py:58285` | `TortoiseSDK._connect_issue_objects` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (o:Object {id:$oid}) ON CREATE SET o.objectKind=$okind, o.name…` |
| `tortoise/sdk.py:59146` | `TortoiseSDK._set_source_tier` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.sourceKind` |
| `tortoise/sdk.py:59155` | `TortoiseSDK._set_source_tier` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) SET s.credibilityTier = $t, s.sourceKind…` |
| `tortoise/sdk.py:59160` | `TortoiseSDK._set_source_tier` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) SET s.credibilityTier = $t` |
| `tortoise/sdk.py:59201` | `TortoiseSDK._compute_source_prior` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.credibilityTier, s.sourceKind, …` |
| `tortoise/sdk.py:59215` | `TortoiseSDK._compute_source_prior` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {pointKind:'assessment'}) WHERE p.targetSource = $url…` |
| `tortoise/sdk.py:59274` | `TortoiseSDK.get_source_reliability` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.reliability, s.reliabilityCompo…` |
| `tortoise/sdk.py:59312` | `TortoiseSDK.get_source_reliability` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {pointKind:'assessment'}) WHERE p.targetSource = $url…` |
| `tortoise/sdk.py:59416` | `TortoiseSDK._assess_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point {pointKind:'assessment'}) WHERE p.targetSource = $url…` |
| `tortoise/sdk.py:59464` | `TortoiseSDK._invalidate_inheritance_gate_for_source` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:extractedFrom]->(s:Source {url:$url}) RETURN n.id` |
| `tortoise/sdk.py:59479` | `TortoiseSDK._write_reliability_cache` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) SET s.reliability = $r, s.reliabilityCom…` |
| `tortoise/sdk.py:59496` | `TortoiseSDK._clear_reliability_cache` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) REMOVE s.reliability, s.reliabilityCompo…` |
| `tortoise/sdk.py:59536` | `TortoiseSDK.compute_reputation` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Subject)-[:performs]->(e:Event) MATCH (e)-[:IMPL]->(p:Point…` |
| `tortoise/sdk.py:59551` | `TortoiseSDK.compute_reputation` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Subject)-[:performs]->(e:Event) MATCH (e)-[:NAND]->(p:Point…` |
| `tortoise/sdk.py:59951` | `TortoiseSDK.get_owned_entities` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Subject) WHERE s.id = $sid WITH s MATCH (s)<-[:ownedBy]-(e)…` |
| `tortoise/sdk.py:60044` | `TortoiseSDK._calibration_marker_recorded` | plain | read | candidate:SQL-shaped | `MATCH (m:Meta {key:$key}) RETURN m.recordedAt` |
| `tortoise/sdk.py:60340` | `TortoiseSDK.check_connection` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n) LIMIT 1` |
| `tortoise/sdk.py:60380` | `TortoiseSDK.check_connection` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id:$id}) RETURN k.org_id, k.graph_id, k.scopes, k.r…` |
| `tortoise/search_engine.py:1846` | `read_currency_links` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[ef:extractedFrom]->(s:Source) WHERE n.id IN $ids RE…` |
| `tortoise/search_engine.py:1945` | `read_reference_currency_links` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source)-[ref:references]->(n) WHERE n.id IN $ids OR n.event…` |
| `tortoise/search_engine.py:2977` | `run_fts_query` | fulltext | read | stays:text-index (§6.2; #13099 measured, decision stands) | `CALL db.idx.fulltext.queryNodes('{}', $query) YIELD node, score ???R…` |
| `tortoise/search_engine.py:3110` | `run_fts_query` | fulltext | read | stays:text-index (§6.2; #13099 measured, decision stands) | `CALL db.idx.fulltext.queryNodes('{}', $query) YIELD node, score ???R…` |
| `tortoise/search_engine.py:3525` | `run_bm25_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE {} RETURN n.{}, coalesce(n.content, n._searchText…` |
| `tortoise/search_engine.py:3938` | `run_vector_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE {} RETURN count(*), count(n.embedding)` |
| `tortoise/search_engine.py:4170` | `run_vector_query._query_nodes` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/search_engine.py:4216` | `run_vector_query._index_yields_any_node` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/search_engine.py:4468` | `run_vector_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.embedding IS NOT NULL??? WITH vecf32($query_vec…` |
| `tortoise/search_engine.py:4506` | `run_vector_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.embedding IS NOT NULL RETURN count(*)` |
| `tortoise/search_engine.py:4550` | `run_vector_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.embedding IS NOT NULL AND {} IS NOT NULL RETURN…` |
| `tortoise/search_engine.py:4741` | `run_structural_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE {} RETURN n.{} ORDER BY {} LIMIT $limit` |
| `tortoise/search_engine.py:4814` | `expand_structural_hops` | traversal | read | stays:traversal (§3) | `MATCH (seed:Point) WHERE seed.id IN $seeds WITH seed MATCH path = (s…` |
| `tortoise/search_engine.py:5988` | `annotate_ep_batch` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (n)<-[r:IMPL]-(c:P…` |
| `tortoise/search_engine.py:6348` | `get_relationships` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/search_engine.py:6531` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids WITH n MATCH (n)-[r:IMPL\|NAND\|h…` |
| `tortoise/search_engine.py:6555` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (n)-[r:CORRECTS]->…` |
| `tortoise/search_engine.py:6578` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids WITH op M…` |
| `tortoise/search_engine.py:6600` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids WITH op M…` |
| `tortoise/search_engine.py:6682` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids CALL { WI…` |
| `tortoise/search_engine.py:6735` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids WITH op M…` |
| `tortoise/search_engine.py:7064` | `subject_binding_available` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[r:aboutSubject]->(:Subject) RETURN count(r) AS c UN…` |
| `tortoise/search_engine.py:7115` | `fetch_point_epistemic_state` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (n)-[:aboutSubject…` |
| `tortoise/search_engine.py:7323` | `filter_by_relationship` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:{}) WHERE n.{} IN $ids{} WITH n MATCH (n)<-[r1:hasPart\|IMP…` |
| `tortoise/search_engine.py:7371` | `filter_by_traversal_predicate` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:{}) WHERE n.{} IN $ids{} WITH n MATCH (n)<-[r:hasPart\|IMPL…` |
| `tortoise/selfhost_api.py:343` | `list_points` | plain | read | candidate:SQL-shaped | `MATCH (n:Point){} WHERE ??? RETURN properties(n) ORDER BY n.createdA…` |
| `tortoise/selfhost_api.py:386` | `search` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_operator = false AND toLower(n.content) C…` |
| `tortoise/session_continuity.py:24` | `SessionContinuity.start` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/session_import/parsers.py:291` | `_hermes_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/session_indexer.py:272` | `_graph_entity_keywords` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n:Object OR n:Subject) AND n.name IS NOT NULL RETUR…` |
| `tortoise/session_indexer.py:745` | `main` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId: $eid}) RETURN properties(e)` |
| `tortoise/session_link.py:168` | `_resolve_targets` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$id}) RETURN count(o)` |
| `tortoise/session_link.py:184` | `_resolve_targets` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.objectKind='pm:issue' AND o.name ENDS WITH …` |
| `tortoise/session_link.py:363` | `link_entity` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:{} {id:$sid})-[:{}]->(t:{} {id:$tid}) RETURN count(s)` |
| `tortoise/session_link.py:371` | `link_entity` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {id:$sid}), (t:{} {id:$tid}) MERGE (s)-[:{}]->(t) RETURN…` |
| `tortoise/session_link.py:378` | `link_entity` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {id:$sid}), (t:{} {id:$tid}) MERGE (s)-[r:{}]->(t) SET r…` |
| `tortoise/session_reinjection.py:556` | `source_session_chunk_pass` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (seed:Point) WHERE seed.id IN $seed_ids WITH seed MATCH (s:Ses…` |
| `tortoise/session_schema.py:848` | `audit_session_retrofit` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) RETURN count(s)` |
| `tortoise/session_schema.py:854` | `audit_session_retrofit` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session) WHERE s.turn_count IS NOT NULL OPTIONAL MATCH (s)-…` |
| `tortoise/session_schema.py:864` | `audit_session_retrofit` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) WHERE s.turn_count IS NOT NULL RETURN count(s)` |
| `tortoise/session_schema.py:891` | `audit_session_retrofit` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) WHERE s.capture_ok = true RETURN count(s)` |
| `tortoise/session_schema.py:907` | `audit_session_retrofit` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session) WHERE s.entity_links_created IS NOT NULL OPTIONAL …` |
| `tortoise/session_source_migration.py:235` | `_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/session_source_migration.py:235` | `_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/session_source_migration.py:421` | `_merge_metadata` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (new:Source {url:$v}) ON CREATE SET new.id = $v, new.canonical…` |
| `tortoise/session_source_prose_migration.py:461` | `_read_source_rows` | plain | read | candidate:SQL-shaped | `MATCH (s:Source) OPTIONAL MATCH (sess:Session {id: s.sessionId}) RET…` |
| `tortoise/session_source_prose_migration.py:476` | `_read_source_rows` | plain | read | candidate:SQL-shaped | `MATCH (s:Source) OPTIONAL MATCH (sess:Session {id: s.sessionId}) RET…` |
| `tortoise/session_source_prose_migration.py:1033` | `reconcile_session_source_prose` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) WHERE id(s)=$nid AND coalesce(s.summary,…` |
| `tortoise/session_source_prose_migration.py:2151` | `classify_fts_hits` | plain | read | candidate:SQL-shaped | `MATCH (s:Source) WHERE s.url IN $urls RETURN s.url, s.sourceKind, s.…` |
| `tortoise/session_source_prose_migration.py:2247` | `source_index_is_empty` | plain | read | candidate:SQL-shaped | `MATCH (s:Source) WHERE s.documentKind IS NULL AND coalesce(s._search…` |
| `tortoise/session_source_prose_migration.py:2283` | `lexicon_control_read` | plain | read | candidate:SQL-shaped | `MATCH (s:Source) WHERE s.summary STARTS WITH $prefix AND s._searchTe…` |
| `tortoise/source_identity.py:249` | `resolve_source_key` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {canonicalUrl: $cu}) RETURN s.url ORDER BY s.url LIM…` |
| `tortoise/source_identity.py:274` | `resolve_source_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url: $u}) WHERE s.canonicalUrl IS NULL SET s.canoni…` |
| `tortoise/source_identity.py:282` | `resolve_source_key` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {canonicalUrl: $cu}) RETURN s.url ORDER BY s.url LIM…` |
| `tortoise/subgraph.py:372` | `_q` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/subject_binding.py:326` | `_resolve_target` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN n.{}` |
| `tortoise/subject_binding.py:459` | `bind_point_subjects` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Point {id:$sid})-[r:{}]->(t:{} {id:$tid}) RETURN count(r)` |
| `tortoise/subject_binding.py:482` | `bind_point_subjects` | adjacency | write | stays:graph-maintenance | `MATCH (s:Point {id:$sid})-[r:{}]->(t:{} {id:$tid}) SET r.confidence=…` |
| `tortoise/subject_binding.py:515` | `audit_subject_binding` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) RETURN count(p)` |
| `tortoise/subject_binding.py:518` | `audit_subject_binding` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (:Point)-[r:aboutSubject]->(:Subject) RETURN count(r), count(r…` |
| `tortoise/subject_binding.py:524` | `audit_subject_binding` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:aboutSubject]->(:Subject) RETURN count(DISTINCT p)` |
| `tortoise/subject_binding.py:528` | `audit_subject_binding` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject) RETURN count(s)` |
| `tortoise/subject_binding.py:613` | `_seed_gate_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (n:{} {id:$id}) SET n.name=$name, n.{}=$kind` |
| `tortoise/subject_binding.py:672` | `quality_gate_metrics` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:Point {id:$pid})` |
| `tortoise/subject_binding.py:678` | `quality_gate_metrics` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (:Point {id:$pid})-[:aboutSubject]->(s:Subject) RETURN s.name` |
| `tortoise/supabase_control.py:688` | `SupabaseControlPlane.query_all._read_page` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/supabase_control.py:793` | `_registry_abuse_write` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.{} = $value` |
| `tortoise/supabase_control.py:816` | `_registry_abuse_read` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.flagged_at, t.suspended_at` |
| `tortoise/taxonomy.py:20` | `taxonomy` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN count(n)` |
| `tortoise/taxonomy.py:30` | `list_topics` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN properties(n)` |
| `tortoise/taxonomy.py:42` | `list_topics` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})<-[r1:IMPL]-(op:Point {is_operator:true})-[r…` |
| `tortoise/taxonomy.py:49` | `list_topics` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})<-[r1:NAND]-(op:Point {is_operator:true})-[r…` |
| `tortoise/topic_summarization.py:191` | `_retrieve_topic_neighborhood` | plain | read | candidate:SQL-shaped | `MATCH (e:{}) WHERE toLower(coalesce(e.name, '')) CONTAINS toLower($t…` |
| `tortoise/topic_summarization.py:229` | `_retrieve_topic_neighborhood` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:{}]->(e:{} {id: $eid}) WHERE p.is_operator = false…` |
| `tortoise/topic_summarization.py:256` | `_retrieve_topic_neighborhood` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.is_operator = false AND {} AND (toLower(coal…` |
| `tortoise/topic_summarization.py:293` | `_retrieve_topic_neighborhood` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)<-[r1:IMPL\|NAND]-(op:Point {is_operator:true})-[r2:I…` |
| `tortoise/tortoise_client.py:297` | `query_prior_research` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/tortoise_client.py:340` | `query_existing_strategies` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/tortoise_client.py:356` | `query_existing_visions` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/tortoise_client.py:357` | `query_existing_visions` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/vector_write.py:1534` | `clear_embedding` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH {} REMOVE {}.embedding` |
| `tortoise/webhook_delivery.py:236` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/webhook_delivery.py:241` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/webhook_delivery.py:1048` | `event_head_seq._events_head` | plain | read | candidate:SQL-shaped | `MATCH (e:GraphEvent) RETURN max(e.seq)` |
| `tortoise/webhook_delivery.py:1059` | `event_head_seq` | plain | read | candidate:SQL-shaped | `MATCH (m:GraphEventMeta) RETURN max(m.last_seq)` |
| `tortoise/webhook_delivery.py:1120` | `due_subscription_count` | plain | read | candidate:SQL-shaped | `MATCH (s:ContextSubscription) WHERE s.disabled_at IS NULL AND (s.nex…` |
| `tortoise/weights.py:244` | `compute_operator_weight` | plain | read | candidate:SQL-shaped | `MATCH (o:Point {id:$id}) RETURN o.op_type, coalesce(o.annotator_bias…` |
| `tortoise/weights.py:279` | `compute_operator_weight` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$id})-[r:IMPL\|NAND]->(p:Point) WHERE p.is_operat…` |
| `tortoise/weights.py:305` | `compute_operator_weight` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$id})-[r:{}]->(:Point) RETURN abs(coalesce(r.msg_…` |
| `tortoise/weights.py:353` | `compute_operator_weight` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$id})-[:mitigated_by]->(m:Point) WHERE {} RETURN …` |
| `tortoise/why.py:1250` | `assemble_why_blocks` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/why.py:1312` | `assemble_why_blocks` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/why.py:1331` | `assemble_why_blocks` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/why.py:1351` | `assemble_why_blocks` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/why.py:1363` | `assemble_why_blocks` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/why_snapshot.py:147` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/why_snapshot.py:151` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/why_snapshot.py:563` | `precompute_for_dream` | plain | read | candidate:SQL-shaped | `MATCH (s:ContextSubscription) WHERE s.disabled_at IS NULL RETURN cou…` |
