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
| product surface (`tortoise/**/*.py`, test modules excluded) | **312** files scanned |
| files carrying at least one statement site | **102** |
| **statement sites (this census)** | **1327** |
| of those, shape **`plain`** — no traversal, no adjacency, no fulltext, no EP | **875** (589 read, 286 write) |
| **`plain` READS — the migration candidates** | **589** |
| `adjacency` (edge pattern, fixed length) | 293 |
| `ep` (the epistemic scalars) | 32 |
| `traversal` (variable length) | 3 |
| `fulltext` | 2 |
| `ddl` | 17 |
| `unresolved` (statement not statically resolvable) | 105 |
| non-Cypher door calls (excluded from every shape total) | 266 |
| **AST door calls** = census + non-Cypher | **1593** |

> **The finding, with its count: 589 of 1327 product statements (44.4 %) need none of the
> four graph-only capabilities.** They are lookups, listings, filters, aggregates and ordered reads —
> and they are served from a store that costs ~584× as much per GB as the record (`#13082`). That is
> not an adjective; it is 589 statements.

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
| product surface (this census) | **1327** | **102** | `tortoise/**/*.py`, test modules excluded |
| every tracked surface (`--all-surface`) | **9,228** | **857** | tests, `graph-scripts/`, `archive/`, `eval/`, `docs/`. ⛔ **RE-DERIVED LAST AT THIS PR-LEVEL MERGE (`origin/main` `a7baa98d05` × `#14232`)** — `--all-surface --json` on the fully resolved tree reads **9,228 / 857 / 2,555** (sites / carrying files / scanned `.py`), against **8,889 / 772 / 2,171** measured by running the same tool in a detached worktree AT `origin/main` `a7baa98d05`: so the delta `+339` sites / `+85` carrying files / `+384` scanned files is NOT this branch's, it is main's `tests/`+`tools/` growth between the base the sentence below names and this one — re-measured, never carried, and stated as a delta of two readings rather than attributed file-by-file. A dated measurement (at THIS merged head), NOT a pin, and deliberately so: this population includes `tests/`, so it moves on any test edit and a pin on it would red an unrelated PR for a number this document does not rest on (`#13098` review P2 — it has read 8,791 / 8,908 / 8,935 / 8,939 / 8,950 / 8,964 / 8,979 / 9,008 / 8,994 / 9,055 / 9,058 / 9,077 / 9,082 / 9,097 / 9,110 / 9,132 / 9,134 / 9,150 / 9,170 / 9,186 here, and only `9,208` reproduces at this tree — the **2,549** files it scans are `main`'s **2,547** at this merge (`0467ba329d7`) plus this PR's TWO added files (`tortoise/projection_worker.py`, `tests/test_14185_projection_worker.py`), neither of which carries a site, so `files_with_sites` is **854** at BOTH trees (re-measured after the merges of `2c0d66e2ec`, `69908d006b` and `1b0db0489408` — TWO of which grew it (`2c0d66e2ec` added 2 `.py` files, `1b0db0489408` added 2) and one of which added NONE (`69908d006b`'s `.py` diff is three modifications and no addition, so it grew nothing: an earlier cut of this row said so and that qualifier was lost in a re-cut, which is why it is restored here rather than dropped); the row read 2,539 before the last of those, which is why the figure is re-derived rather than carried) — `tests/` included, which is why these numbers move on a test edit. ⛔ RE-DERIVED, not carried: an earlier revision of this row stated 9,186 / 849 / 2,533 with `main` at 2,527, and all four were stale by the time the branch merged — measured with `uv run python tools/cypher_inventory.py --all-surface --json` at this head, which is the only thing that answers it (the pin cannot: this row is deliberately unpinned). That cut's delta was **+20 sites and +5 carrying files**, enumerated rather than counted, per file with the tool's own parser: `tortoise/outside_view.py` 3, `tortoise/resident_pin.py` 3, `tests/test_14012_outside_view.py` 9, `tests/test_14012_resident_pin.py` 3 and `tests/test_14012_resident_cli.py` 2 — the sixth added file, `tools/resident.py`, carries none. ⛔ RE-MEASURED AGAIN, and the previous cut of this very sentence is the proof the re-derivation rule is load-bearing: it read `+17` / `…outside_view.py` 8 / `+11 added files` — an enumeration summing to 19 beside a claimed delta of 17 — because the sites/files pair had been re-measured while the enumeration and the scanned-file count had not. Every figure here is now `--all-surface --json`'s own output at the head that ships, per file, and `git ls-tree`'s `.py` count for the file delta (⛔ the six files marked "this lane's" above were NOT this lane's: they went to `main` in `e74cf1d13d` (#14161), which took `main` from **2,535** at `dea00903b50f` to 2,541; the merge parent `b4da1f5c01` read **2,542**, `main` had moved to **2,547** by this re-cut's merge base `0467ba329d7`, and this branch adds exactly TWO scanned files — measured by `--all-surface` at each tree, not the eleven an earlier cut claimed). ⛔ **RE-MEASURED A FOURTH TIME at this re-cut's merge (`#14222`, merged with `origin/main` `0467ba329d7`)** — the base this merge actually took, and its merge-base with this head. The previous cut named `5e83c2a2f697`, which is now one base behind: `main` added **TWO** `.py` files between them (`git diff --name-status 5e83c2a2f697 0467ba329d7 --diff-filter=A -- '*.py'` → `tools/extractor_cost_measure.py`, `tests/test_extractor_cost_measure.py`), which is exactly why the scanned-file count moved **2,545**→**2,547** while `sites` **9,208** and `files_with_sites` **854** are unchanged — neither new file carries a statement, so it joins the scanned population without joining the carrying one. Every figure below was re-derived at the base that shipped. ⛔ **The Appendix was RED on the PREVIOUS base (`70fbd1a27dd5`) — and `main` had ALREADY repaired it before this merge, so the attribution matters.** The earlier cut of this sentence named `main` as the red one, and at the base this row now names (`0467ba329d7`) that is false: measured `70fbd1a2`'s committed table against `main`'s, **400** rows differ and **378** of them are `tortoise/sdk.py` (of **386** cited sdk.py rows) — that is `main` RE-CUTTING the staleness its own `sdk.py` growth had created, and `tortoise/sdk.py` reads 62,785 lines at BOTH trees, so the 62,764 → 62,785 growth that caused it predates both. At `0467ba329d7` `main`'s table is GREEN, because `main`'s own `--markdown` re-cut shipped with it: measured `main`'s table against THIS head's table, **exactly ONE** row differs — the single `tortoise/monitoring.py` row for `_probe_once._run_query`, which sat at `:4823` on `main` and moved when this branch changed that file — so this merge's regeneration repairs that ONE row, not 378, and the earlier "**379**" figure (which counted the same row against the pre-re-cut base) does not describe this head. ⛔ `--check` stays GREEN over all of it, because the pins are scalars and an insertion is not a new statement. (An earlier cut of this row named `10e51693f1`: it is an ANCESTOR of that base, **twelve** commits behind it (`git rev-list --count 10e51693f1..0467ba329d7` = 12), and the figure DID move — it reads **2,545** scanned files (`git ls-tree -r --name-only 10e51693f1 \| grep -c '\.py$'`), not 2,547, because the two `extractor_cost_measure` files postdate it — but a reader who re-derives at the newer sha re-derives at the tree that shipped, which is the only base this rule accepts.): `main` alone reads **9,208 / 854 / 2,547** and the merged tree reads **9,208 / 854 / 2,549** — sites and carrying files IDENTICAL at both trees, which is what a module that issues no Cypher must do, and the two extra scanned files are this PR's own. The number beside this row names the tree it does — and at THIS row's tree, `38f2ab7fc594` merged into a branch that merges `#14263`'s relation-PAIR leg, `--all-surface --json` reads **9,219 / 855 / 2,551** against **9,211 / 854 / 2,549** measured by running the same tool in a detached worktree AT `38f2ab7fc594` (so neither figure is arithmetic on the other); the `+8` sites, `+1` carrying file and `+2` scanned files are this branch's own, enumerated per file by the tool's parser: `tortoise/sdk.py` **1** (`_endpoint_kind_map`'s label-anchored read) and `tests/test_relation_guards_14263.py` **7** (`s._get_proj().g.query(…)`), while `tortoise/relation_guards.py` scans as a file that carries NO statement — which is why the carrying-file move is `+1` and not `+2`) |

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
            (the 309 files the tool reports as `py_files`), scanned as WHOLE TEXT

door predicate   : re.compile(r"\.(?:ro_)?(?:query|execute)\s*\()")
verb predicate   : re.compile(r"\b(?:MATCH|MERGE)\s*\(", re.IGNORECASE)

1,643 occurrences of the door predicate across 111 files
2,381 occurrences of the verb predicate across 149 files   (upper-bound shape, see below)

1,643  regex door-pattern occurrences
  −54  occurrences on a line carrying NO door call (comments and docstrings)
= 1,593  occurrences on a line carrying a door CALL          [measured]
  − 0   of those, the AST did not record (residual)
= 1,593  AST door calls
= 1327  classified Cypher statements + 266 non-Cypher door calls
```

⛔ **The predicates, stated exactly as `rg_totals` runs them — and where a shell `rg` diverges.**
The population is every TRACKED `tortoise/**/*.py` except `tortoise/test_*.py` (311 files; the tool
prints it as `py_files`), scanned as WHOLE TEXT — so `\s` spans a newline, which a line-based `rg`
cannot do. Measured on this tree:

* `rg -o '\.(ro_)?(query|execute)\s*\(' <those 311 files>` → **1,647** across **112** files: exactly the
tool's number. A bare `rg … tortoise/` gives **1,653 / 112**, the 6 extra occurrences living in
the one `tortoise/test_*.py` that exists; `--glob '!tortoise/test_*.py'` reproduces 1,647 / 112 on
this tree, because
every excluded test module here is TOP LEVEL, so the glob's scope and `TEST_MODULE`'s scope coincide
— they would diverge if a nested `tortoise/**/test_*.py` ever appeared.
* `rg -io '\b(MATCH|MERGE)\s*\(' <those 311 files>` → **2,368** across **148** files, while the tool
reports 2,381 / 149. The 13-occurrence, one-file gap is the WHOLE-TEXT class — and ⚠️ it is **not**
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
predicate over the same 311 files finds exactly 13 — the verb-at-end-of-line scan alone finds **182**,
and the number is not a second version of the 13: **13** of those lines are the cross-line matches just
enumerated, and the other **169** are a COMMENT or docstring line that simply ENDS on the word (“any
failure on the read/parse/merge”, “first match”, “the old name-keyed MERGE”, `projection/__init__.py`'s
“(#13330 post-merge” that `#13399` added, …) with nothing following, which the case-insensitive
predicate counts and which is why the predicate has to be stated — `tortoise/ended.py` carries none of
the 182, so the count does not ride on that file — and none of the 13 is an executable Cypher literal
split by implicit
concatenation (`"… MATCH " "("` occurs nowhere in this tree): the break is prose or a wrapped Cypher
example INSIDE a docstring, which is why the tool's verb total is an upper bound.
(A bare case-sensitive `rg` gives 2,098, and `-i` without the exclusion 2,453 — ⛔ both over
`tortoise/` with **no type filter**, which is not the population this row is about and is why the
number beside it was un-reproducible before `#13098`'s review asked for the predicate. Over the 308
`.py` files the same two predicates read **2,095** and **2,368**; with `-t py` and the test-module
exclusion dropped they read **2,097** and **2,371**. The 82-occurrence 2,371→2,453 gap is entirely
`*.ts` / `*.mjs` / `*.sh` — enumerated, it is `tortoise/pi-hooks/tortoise-volunteer.test.ts` 49,
`tortoise/pi-hooks/tortoise-capture.test.ts` 27, `tortoise/pi-hooks/store-sync.test.mjs` 3,
`tortoise/pi-hooks/tortoise-capture.ts` 1, `tortoise/openclaw-plugin/index.test.mjs` 1,
`tortoise/claude-hooks/volunteer-turn.sh` 1 — so a bare `rg -o` that reads as one number is THREE, and the population decides which.)

The first three rows of the block are measured by the tool (`--json` → `rg_totals`); the **−54** and
the **= 1,589 on a door-call line** are both counts, not differences.⛔ The **residual** row is
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
| `plain` | **872** | read=588, write=284 |
| `adjacency` | **293** | read=192, write=101 |
| `ep` | **32** | read=21, write=11 |
| `ddl` | **17** | ddl=17 |
| `traversal` | **3** | read=3 |
| `fulltext` | **2** | read=2 |
| `unresolved` | **105** | unresolved=105 |

**What the unresolved bucket is.** 105 sites hand a door a statement built where no static reader
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
hand, so they are counted and named rather than classified by guesswork. 321 further sites
are **partial**: the literal half carries the shape (e.g.
`"MATCH (op:Point)-[:" + safe_rel + "]->(t)"` is an adjacency read) and the digest shows `{}`/`???`
where the folded part was.

**Where they live** — the top files, which is also a flip's blast radius:

| sites | file |
|---|---|
| 386 | `tortoise/sdk.py` |
| 242 | `tortoise/hosted_api.py` |
| 93 | `tortoise/projection/__init__.py` |
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
| 2 | `plain` reads by id-list (`WHERE n.id IN $ids`) | same simplicity, one predicate more; needs an IN-list corpus | a large share of the 588 |
| 3 | `plain` aggregates / counts | no traversal, but a fan-out difference is easy to get wrong → needs a larger corpus and a variance check | a subset of the 588 |
| 4 | `plain` writes | **not a Cypher→SQL move** — these disappear with the node when `#13083` makes the shape `record_only` | 284 |
| — | `adjacency` / `traversal` / `ep` / `fulltext` | **do not flip without a reopen on `#4051`** | 330 |

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
  (2) are `stays`, each citing its section. `adjacency` (293) is `measure`, not `move`.
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

**The cut this PR (#14423, `fix/14342-d1-drain`, D1 of #14342) makes — ONE newly SCANNED file that carries NO statement, so exactly one pinned number moves and no `file:line` row is added or removed.** Re-cut at this PR-level merge with `origin/main` `0fbfbb5b21` by RUNNING the tool on the merged tree, never by arithmetic on the markers. `tortoise/drain_claim.py` (the `#14350` drain lease: create-if-absent via `os.link`, the three staleness rules, and compare-and-delete takeover) is product surface and the census SCANS it, so `files_scanned` 309→**310** — the same class of move `#14222`'s `projection_worker.py` and `#14011`'s `usage_signal.py` made. It issues no Cypher and imports no store client (it is a file-lease over a spool directory), which is why `files` stays **102**, `sites` **1327**, `unresolved` **105**, `candidates` **589** and every shape pin are unchanged — `--check` prints `25 pinned counts match` with only that one operand edited, so the migration metric §0 is about does not move and no entry became a candidate. ⛔ **THE 248 `file:line` ROWS THAT DO MOVE ARE NOT THIS PR'S STATEMENTS, and the rows say so:** the table is regenerated with `--markdown` at this merged tree, and a row-by-row comparison of the old and new Appendix A shows **1327 rows on both sides, ZERO payload differences, and exactly 248 rows differing in `file:line` alone — 240 of them `tortoise/hosted_api.py` and 8 `tortoise/__main__.py`**. The 240 are `main`'s (`#14038`'s capacity-notice path and `#14418`'s S1 base edit move ~1 200 lines of `hosted_api.py`), and the 8 are this PR's own insertion of `session drain`'s claim wiring into `__main__.py` (`:13544` → `:13547` is the sample a reviewer can re-check); the pins cannot see either, which is exactly why the appendix is graded byte-for-byte by `tests/test_13098_cypher_inventory.py::test_the_doc_appendix_is_the_tools_own_output`. §1's `rg` reconciliation does not move: this PR adds neither a door CALL nor a `MATCH`/`MERGE` outside the spool's own text. ⛔ **RE-CUT ONCE MORE, AFTER THE PEER LANE'S FIXES MERGED INTO THIS BRANCH**, by RUNNING `--markdown` on that merged tree: **1 327 rows on both sides, ZERO payload differences, and exactly 8 rows differing in `file:line` alone — all 8 `tortoise/__main__.py`**, because this branch's own `session drain` wiring grew by five lines in the second fix cycle (`:13547` → `:13552` is the sample to re-check). No pin moves (the pinned counts are unchanged: `sites` 1 327, `files` 102, `unresolved` 105, `candidates` 589, every shape total and role split), so `--check` prints `25 pinned counts match` before and after the re-cut and cannot see the 8 addresses — only the byte comparison can. ⚠️ **`files_scanned` is pinned at 310 and §0's row now SAYS 310**: it printed 309 from the moment `tortoise/drain_claim.py` was scanned, i.e. the pin was correct and the row beside it was the stale one — the reverse of the drift that key was added to catch, and the reason the two must be re-derived in the same edit. ⛔ **AND AGAIN AFTER THE PEER LANE MERGED `origin/main` INTO THIS BRANCH** (`655ff58c`), which is the cut that matters most here because `main` moved ~233 `hosted_api.py` addresses under it: regenerated with `--markdown` on the merged tree — **1 327 rows on both sides, ZERO payload differences, 237 rows differing in `file:line` alone** (233 `tortoise/hosted_api.py`, 2 `tortoise/record_projection.py`, 1 `tortoise/monitoring.py`, 1 `tortoise/record_allocator.py`) — and **no pin moves**: `--check` prints `25 pinned counts match` on both sides of the re-cut, because every one of those rows keeps its shape, role and verdict and only its address moves. That is the whole reason the appendix is graded byte-for-byte and the pins are not sufficient: an address-only drift is invisible to all 25 pinned numbers and is exactly what a reviewer needs to read.
**The cut this PR (#14220, `fix/14186-embedder-seam-batching`) makes — `files_scanned` 309→**311** and NOTHING else.** The embedder seam adds two product-surface modules, `tortoise/embed_seam.py` (the backing arbiter, its bounded collection window and its two remote clients) and `tortoise/embed_worker.py` (the out-of-process encoder). ⛔ Neither issues a Cypher statement — they hand text to `EmbeddingModel`/an HTTP endpoint, not to a query door — so they are scanned and carry no site: `files` stays **102** and every shape pin below is untouched. This is the same class of move `#14222`'s `projection_worker.py` and `#14284`'s `relation_guards.py` made, and it is the reason `files_scanned` is pinned: the population can grow without a single statement moving, and before that pin existed `--check` stayed green through it (`#13098` review P3). §1's reconciliation is re-derived on this merged tree (`main` `0fbfbb5b`): `ast_door_calls` **1,593** = **1,327** classified Cypher + **266** non-Cypher, door pattern **1,647 / 112**, verb **2,381 / 149**. ⛔ None of that movement is this PR's: the two new modules match no door predicate and add no site, so `sites` **1,327**, `files` **102**, `non_cypher_door_calls` **266** and every shape pin come from `main` — `tortoise/capacity_notice.py` (#14038) landed four non-Cypher door calls in one new scanned file after the block above was last cut, which is why §1's door figure reads 1,647 / 112 here and not the 1,643 / 111 it carried.

**The cut this PR (#14232, `fix/14232-ingest-ack-async`) makes — TWO new statement sites, both `plain` WRITEs, and nothing else moves.** Re-cut at this PR-level merge with `origin/main` `a7baa98d05` by RUNNING the tool on the merged tree, never by arithmetic on the markers. `tortoise/hosted_api.py::_write_capture_phase` is the phase write the ACK/complete split needs (`MATCH (s:Session {id:$sid}) SET s.capture_extraction_phase=$p, s.capture_extraction_phase_at=$at`) — a single indexed `SET` by session id, no traversal, no adjacency, no fulltext, no EP — and `_clear_capture_phase` is the phase retirement the RAISE arm of `_capture_tail_failed` needs (`MATCH (s:Session {id:$sid}) SET s.capture_extraction_phase=null, s.capture_extraction_phase_at=null`), without which a capture whose tail raised left every read surface publishing `extracting` with `outcome_known: false` for work nothing was doing. Both stay `graph-maintenance`, because they write graph state rather than a record row (`#13083`'s `record_only` shape is what would eventually move them). Measured on the merged tree against `origin/main` `a7baa98d05`'s own reading (`1325` / `plain` `873`, write `284`) by running the same tool in a detached worktree there: `sites` 1325→**1327**, `plain` 873→**875** (read **589** unchanged, write 284→**286**), and `ast_door_calls` 1587→**1589** (= 1327 + the unchanged 262 non-Cypher calls). `unresolved` **105**, `candidates` **589**, `partial` **321**, `files` **102**, `files_scanned` **308**, `adjacency` **293**, `ep` **32**, `ddl` **17**, `traversal` **3**, `fulltext` **2** and `non_cypher_door_calls` **262** are ALL unchanged — no existing statement was removed, re-classified or re-shaped, and neither new site is a candidate (the migration metric §0 is about does not move). §1's `rg` reconciliation moves with exactly the two new door calls and the two new `MATCH (`: door **1,641 / 111**→**1,643 / 111** whole-text, bare **1,649 / 112**, on call lines **1,587**→**1,589**, verb **2,379 / 149**→**2,381 / 149**; the line-based forms are re-measured there and read 2,095 / 2,368 / 2,097 / 2,371. Appendix A is regenerated with `--markdown` at this merged tree: it gains these two rows, and every `tortoise/hosted_api.py` row after them is re-derived rather than carried, because this branch also moves ~1 100 lines of that file.
**The two earlier re-cuts of this same cut are HISTORY, kept and not re-stated as the table's numbers.** Each was taken on its own tree as the site landed — `_write_capture_phase` first (`sites` 1324→1325, `plain` 872→873, write 284→285, at `origin/main` `5e83c2a2f6`), then `_clear_capture_phase` on the round-3 review fix (1325→1326, write 285→286) — and both typed `files_scanned` **306**, which is that base's figure and not this head's (**308**: `main` added two scanned `.py` files between those bases and this merge, and this branch adds none). ⛔ The pair is NOT added to the merged base's `1325`: that would double-count main's `_endpoint_kind_map` read (`candidates` 588→589, `plain` read 588→589, `partial` 320→321 — main's, measured at `a7baa98d05` and already inside its `1325`), which is exactly the arithmetic-across-parents this section forbids. The figures above are `--repin`-style readings of the tree that ships.
**The cut this branch (`chore/14263-pack-business-logic`, PR #14284) makes — ONE `plain` READ, ONE scanned file that carries no statement, and every other shape pin unchanged — re-cut by RUNNING the tool on the merged tree, never by arithmetic on the markers.** The branch adds one Cypher statement, `tortoise/sdk.py::_endpoint_kind_map`'s label-anchored endpoint sweep (`MATCH (n:{label}) WHERE n.id IN $ids RETURN n.id, labels(n)`), which is a `plain` **read** and a `candidate:SQL-shaped` lookup — §4 row 2's shape — so `sites` 1324→**1325**, `plain` 872→**873** (read 588→**589**, write **284** unchanged), `candidates` 588→**589**, `partial` 320→**321** (the label is interpolated, the `???` marker's class) and `ast_door_calls` 1586→**1587** (= 1325 + the unchanged 262 non-Cypher calls). `files_scanned` 307→**308**: `tortoise/relation_guards.py` (the verdict module — a pure function over the pack registry, issuing no Cypher and importing no store client) is product surface and the census SCANS it, which is the same class of move `#14222`'s `projection_worker.py` made; it carries no site, so `files` stays **102**. `unresolved` **105**, `adjacency` **293**, `ep` **32**, `ddl` **17**, `traversal` **3**, `fulltext` **2** and `non_cypher_door_calls` **262** are ALL unchanged — nothing here is a candidate for the record store beyond the one read, which is what §0's metric is about. §1's `rg` reconciliation moves by exactly that call and that file: door **1,640 / 111**→**1,641 / 111** whole-text, bare **1,646 / 112**→**1,647 / 112**, and the `-i` verb figure **2,377 / 149**→**2,379 / 149** — ⛔ `+2` and only ONE of them is a statement: the other is the docstring inside `_endpoint_kind_map` that QUOTES the bare `MATCH (n` form it deliberately does not issue, which is precisely the upper-bound class §1 names for this predicate. `--all-surface` is re-derived in the §1 table above (**9,219 / 855 / 2,551** on the merged tree against **9,211 / 854 / 2,549** measured in a detached worktree at `38f2ab7fc594`), and Appendix A is regenerated with `--markdown` at this merged tree. ⚠️ The merged base is `origin/main` **`38f2ab7fc594`** — eleven commits ahead of the `0467ba329d7` the `#14222` cut below names — so the pins here are the merged tree's, re-derived by `--check` (`25 pinned counts match`), not `main`'s at any earlier sha. ⛔ The paragraphs BELOW describe earlier cuts against THEIR OWN bases and are NOT re-cut, which is this section's stated convention.
**The re-cut `#14269` (`fix/main-14224-repair`) made — carried into this branch by the merge of that branch (`b49729383d`), so it is NOT "this PR's" cut: at this head it is byte-identical to `main`'s own, and the ⛔ note at the end of this paragraph is the re-measurement that says so — NO operand moves, and 378 `file:line` rows are re-derived anyway.** `--check` is green here (**25 pinned counts match**): no statement was added, removed, re-classified or re-shaped by that cut, so **at ITS base** `sites` **1324**, `files` **102**, `files_scanned` **306**, `unresolved` **105**, `non_cypher_door_calls` **262** and every shape pin were unchanged — ⛔ and `files_scanned` is the ONE operand that has moved since: the `#14222` row below is the mover that takes it 306 -> **307**, which is THIS head's figure and the one the pins in §0 carry, so the list above records that cut's base and not the head you are reading (typing 306 beside `--check is green here` was self-refuting: `--check` is green BECAUSE the pinned `files_scanned` is 307). What moved is only what the pins cannot see: `main`'s `#14260` (`f0e1ceb629`, `18:36:27`) inserted **22** lines into `tortoise/sdk.py` above 378 of its 386 statement sites (one hunk at `@@ -5713,8 +5713,29 @@`, so the EIGHT sites above it — `:3132`, `:3474`, `:3738`, `:4049`, `:4066`, `:4399`, `:4423`, `:5111` — did not move) and did not re-cut this appendix — while **eleven seconds earlier** `#14262` (`8e4ffedc2e`, `18:36:16`) HAD re-cut it, so the second landing rotted the first's table. ⛔ `main` at `70fbd1a27d` is RED on `tests/test_13098_cypher_inventory.py::test_the_doc_appendix_is_the_tools_own_output` (`rows: doc=1326 tool=1326; 378 row(s) differ`), and every row of that difference is `tortoise/sdk.py`: this re-cut is `--markdown`'s own output at this tree — **378 rows re-derived, 378 insertions / 378 deletions in Appendix A** (`git diff --numstat origin/main...HEAD -- docs/architecture/CYPHER-INVENTORY.md` printed **`381 379`** at the head this paragraph was written for, when `origin/main` was `70fbd1a27d` — Appendix A's 378/378 plus the three non-Appendix lines measured then: this paragraph's two, and the `@@ -66,7 +66,7 @@` re-anchor of §1's `--all-surface` row. ⛔ Two earlier revisions of this clause typed a CURRENT-head pair — `380 378` when the §1 line was still missing from it, and `3 1` when the `#14012` line below was still this branch's rewrite — and each was true at its own head and false at the next; that is this paragraph's own rule, re-TAKE never carry, being applied to the sentence that states it). ⛔ **NO figure is typed for this head's diff, deliberately**: a numstat is head-bound and rots exactly as those two did, so run `git diff --numstat origin/main HEAD -- docs/architecture/CYPHER-INVENTORY.md` and `git diff -U0 … | grep '^@@'` at the head you merge. At this one, `main`'s own `#14258` (`fa4d1c8065`, `379`/`379` — Appendix A's `378`/`378` plus `1`/`1` on §1's `--all-surface` row) and the `#14012` line's re-cut to `main`'s bytes have both left the diff, and what remains is this paragraph and the blank line that ends it — no §1 hunk and no separate rewrite. ⛔ WHICH commit removed each: `main`'s own `8fa0662249` (`#14222`) made the same correction to that `#14012` line — at `fa4d1c8065` (`#14258`) it still read `2,376 / 149` and `2,435`, and `8fa0662249` reads `2,377 / 149` and `2,436`, byte-identical to this branch's paragraph — so that hunk left the diff by `main`'s bytes and not by a rewrite here, and `#14258`'s re-cut of Appendix A is what left no Appendix hunk. Measured against `origin/main` `588cf7aeb61f`. The red it records was real at `70fbd1a27d` and is repaired on `main` too — which repairs `main`'s red as well. ⚠️ **The twelve `search_engine.py` rows that were ALSO stale on this PR's previous head (`38c7001725`, where they read `annotate_ep_batch` at `:6141` and `subject_binding_available` at `:7217`) are ALREADY correct here** — `#14262`'s re-cut fixed them (`:6233` and `:7598`, the addresses this head carries) and this branch adopted that on the rebase onto `70fbd1a27d`, so no second edit was owed for them. The lesson is the one the pins keep teaching: two landings eleven seconds apart still owe two re-cuts, and only the byte comparison can say so.

**The cut this PR (#14222, `fix/14185-projection-worker`) makes — ONE scanned file that carries NO statement, so exactly one number moves.** `tortoise/projection_worker.py` (#14185's record-fold loop: an in-memory queue and a daemon thread in front of the caller's own `project` callable) is product surface and the census SCANS it, so `files_scanned` 306→**307**. It issues no Cypher and imports no store client — it chooses *when* a fold runs and never *what* it does, every statement still coming from `record_projection.replay` through the caller — which is why `files` stays **102**, `sites` **1324**, `unresolved` **105**, `candidates` **588** and every shape pin are unchanged, and why no Appendix A row is added. **This branch was RED here before it touched the pin** — the file landed with the earlier commits on this same branch and the pin was not re-cut with it, which is what `differential` reported: `DRIFT files_scanned: doc=306 tool=307`. The row-by-row re-derivation §1 asks for does not move either: the new module carries neither a door CALL nor a `MATCH`/`MERGE`, so the `rg` reconciliation stands at door **1,640 / 111** and bare **1,646 / 112**. ⚠️ The verb figure is **2,377 / 149** at this rebased head — it read **2,376** before `main`'s advance, which added one IN-LINE LOWERCASE occurrence (`#14277`'s `the cache split rides the same merge (see ...)` prose line). A case-insensitive count moves with it and a case-sensitive one does not, which is why §1's `2,091` halves are unchanged while its `-i` halves (and this block's own line above) moved by one. The `#14012` paragraph below is re-cut to the same **2,377** in this same diff, because it too describes this tree. ⛔ The paragraphs BELOW it are NOT re-cut and must not be: they describe earlier cuts against THEIR OWN bases (`2,365 / 147`, `2,353 / 146`, `2,346 / 145`, `2,343 / 144`, `2,338 / 144`), which is this section's stated convention — they are history, not the table. A reader who "finishes" that re-cut would overwrite correct per-base measurements with this head's number.

**The cut this PR (#14012, `feat/14012-pin-and-outside-view`) makes — SIX new statement sites in TWO new product files — two `plain` READS (what is outside), two `plain` WRITES (the pin declaration and its removal) and two `unresolved` — re-cut by RUNNING the tool on the tree merged with `origin/main` `05d218fcaf`, never by arithmetic on the markers.** The two modules are `tortoise/outside_view.py` (the view built from the projection's own state: which nodes are outside the graph, and what they looked like) and `tortoise/resident_pin.py` (the bounded pin: cap, degree ceiling, closure unit), and between them they issue six statements, measured per file by the census's own parser: `outside_view.py` **3**, `resident_pin.py` **3**. Each is a lookup, a listing, or a helper that hands the door a parameter — no traversal, no adjacency, no fulltext, no EP — so `sites` 1318→**1324**, `plain` 868→**872** (read 586→**588**, write 282→**284**), `candidates` 586→**588**, `files` 100→**102**, `files_scanned` 304→**306**, and `ast_door_calls` 1580→**1586** (= 1324 + the unchanged 262 non-Cypher calls). `adjacency` **293**, `ep` **32**, `ddl` **17**, `traversal` **3**, `fulltext` **2** and `non_cypher_door_calls` **262** are unchanged. ⚠️ **`unresolved` 103→105 and `partial` 318→320 move WITH the sites, and the two moves have DIFFERENT causes — say them apart, because an earlier revision of this paragraph attributed both to one mechanism and the census prints the opposite.** The two **`unresolved`** rows are the two `_rows(graph, statement, params)` HELPERS (`outside_view.py:239`, `resident_pin.py:507`) — the whole statement is a parameter, so the static reader cannot fold it and it lands in the documented **over-report** arm, with `shape_unresolved` / `shape_unresolved_unresolved`. The two **`partial`** rows are the two `plain` READS (`outside_view.py:284` `resident_keys`, `:298` `resident_count`), whose statement carries a `{}` for the label / key property: the literal halves keep the shape and the `???` marker is what `partial` counts. ⛔ The two `unresolved` sites are NOT migration candidates and the number §0 calls the finding does not move for them: an unresolvable site is never a candidate, which is why `plain` moved by 4 while `sites` moved by 6. ⛔ **And the row count is a bound on DOOR CALLS, not on statements**: `resident_pin` funnels **three** statements through its `_rows` helper and `outside_view` **three** record statements through its own beside two graph reads it issues directly (`_rows(cur, …)` at `:531` / `:533` / `:616`; `_rows(` at `resident_pin.py:518` / `:597` / `:857` — counted at the call sites, because an earlier cut of this paragraph said *five*, which no reading supports), so six rows is what the census can SEE, not everything these modules issue — which is exactly what the `unresolved` arm is for, and why no `file:line` row may be read as "this module issues this many statements". §1's `rg` reconciliation moves with exactly these six door calls and two files: door **1,640 / 111** whole-text, bare **1,646 / 112** (the 6 extra occurrences live in the one `tortoise/test_*.py`), verb **2,377 / 149**; the line-based `rg` forms are re-measured in §1 and read 2,091 / 2,093 / 2,436, all of which moved with the six `MATCH`-bearing statements. Appendix A is regenerated with `--markdown` at this merged tree — it gains these six rows and every existing row is re-derived rather than carried, because this branch also edits `tortoise/cache_evictor.py` and `tortoise/outside_view.py` bodies whose line numbers shift the rows after them.
**The cut this PR (#14068, `fix/12943-cache-projection-residual`) makes — one non-Cypher door call and two scanned files, with no new statement site — re-cut by RUNNING the tool on the tree merged with `origin/main` `32441c280d0`.** `tortoise/cache_graph.py` declares a tenant's cache graph by reading the registry through `get_control_plane().query("graphs")`, which the census folds to `non_cypher_door_calls` (the resolved statement is the table name `"graphs"`, which carries no Cypher clause — §1's non-Cypher arm, not the `unresolved` over-report arm), so `non_cypher_door_calls` 262→**263** and `ast_door_calls` 1580→**1581** (= 1318 + 263). `sites` **1318**, `unresolved` **103**, `files` **100**, `plain` **868** / `candidates` **586** / `partial` **318** and every other shape are unchanged: no Cypher statement was added, removed, re-classified or re-shaped, so the migration metric §0 is about does not move, and no Appendix A row changed beyond its `file:line`: the main-move shifted **374** of the 1,318 rows (0 differ in anything else, order preserved) and the review-fix commits that followed moved **11** more, so the table is regenerated with `--markdown` at this tree. ⚠️ **Re-measured a third time at the merge with `origin/main` `ad79129cb5`**: `main`'s four intervening commits moved **4** more rows and all four are `file:line` only — `tortoise/cache_evictor.py:504/514` → `:669/689` and `tortoise/fallback_snapshot.py:577/588` → `:700/711`, both from `#14067`'s eviction-path change — so the table is regenerated again here rather than carried from either head. §1's `rg` reconciliation moves by exactly the one door-call occurrence and the one file: door **1,635 / 109** whole-text, and verb **2,365 / 147** unchanged (the new call is not a `MATCH`/`MERGE`). ⚠️ **The bare figure in §1 is corrected here, and it was already two low before this cut**: the one `tortoise/test_*.py` in the tree carries exactly **6** door-pattern occurrences, so bare `tortoise/` is 1,635 + 6 = **1,641 / 110** — the previous `1,638 / 109` did not reconcile with the “6 extra occurrences” sentence beside it. ⛔ **`files_scanned` was already RED on `main` before this branch touched it** — the pin said **302** while the tool reported **303**, because `tortoise/usage_signal.py` (`#14065`) landed as a scanned file carrying no door call and the pin was not re-cut with it; `--check` at `origin/main` `32441c280d0` reports `DRIFT files_scanned: doc=302 tool=303`. This cut re-derives the pin from the merged tree, so it repairs `main`'s red as well: **304** = 303 + `tortoise/cache_graph.py`.
**A fifth re-cut, by this lane's merge fix — ONE non-Cypher door call FEWER, and no statement site moved.** The three record verbs (`record-reconcile`, `record-restore`, `record-serve`) now share ONE connection construction (`tortoise/__main__.py::_connect_record`), because passing `options=…` to `connect()` REPLACES the DSN's own `options` — and that is where the record's application role rides (`options=-c role=tortoise_record_app`), which is why the verbs answered "record is unreadable (RecordProjectionError)" on `test-record-postgres` for a DSN that worked everywhere else (#9862's finding, re-found by #14068's armed legs). Unifying the construction removed the serve verb's Python-level `cursor().execute("SELECT set_config('statement_timeout', …)")`, which the census folds to `non_cypher_door_calls` (the resolved statement is SQL, not Cypher): `non_cypher_door_calls` 263→**262** and `ast_door_calls` 1581→**1580** (= 1318 + 262). `sites` **1318**, `unresolved` **103**, `candidates` **586**, `files` **100**, `files_scanned` **304** and every shape are unchanged — the removed call never was a statement site, so no Appendix A ROW changed for it; the surviving `tortoise/__main__.py` rows moved with the helper's insertion, and the table is regenerated at this tree with `--markdown`. §1's `rg` reconciliation moves by exactly that one occurrence: door **1,634 / 109** whole-text, **1,580** on call lines, verb **2,365 / 147** unchanged (the removed line carried no `MATCH`/`MERGE`).
**The cut this PR (#14011, `feat/14011-usage-signal`) makes — a file that carries NO statement, which is why exactly one number moves.** `tortoise/usage_signal.py` (the window-local per-node usage counter that `SEARCH-ARCHITECTURE.md` §7 requires as its own artifact) is product surface and the census SCANS it, so `files_scanned` 302→**303** — the same class of move `state_intent.py` made in the #13557 row below (a scanned file carrying nothing). `files` stays **100** (it counts files that CARRY a site, and this one carries none: the module issues no Cypher and imports no store client, which its own test pins), `sites` **1318**, `unresolved` **103**, `non_cypher_door_calls` **262**, `ast_door_calls` **1580**, `partial` **318** and every shape pin are unchanged — no Cypher statement was added, removed, re-classified or re-shaped, so the migration metric §0 is about does not move. §1's `rg` reconciliation is also unchanged (door **1,634 / 108** whole-text, bare **1,638 / 109**, verb **2,365 / 147**): the new file carries neither a `MATCH`/`MERGE` nor a door CALL. Appendix A gains no row for this file either — it lists SITES, and this file contributes none — **but its existing rows are re-derived in this same diff**: `tortoise/sdk.py`'s insertions (`#14065`, merged WITHOUT a census re-cut, so `main`'s rows carry the pre-insertion `file:line`) shift the line number of every site after them, and this PR re-cuts them at this head (363 `tortoise/sdk.py` rows move on each side; e.g. `sdk.py:17949` → `18003`). ⚠️ Five further rows move for a cause that is NOT `sdk.py`'s insertions: `main` grew the files they cite — `tortoise/cache_evictor.py` ×2 (`504`→`669`, `514`→`689`), `tortoise/fallback_snapshot.py` ×2, `tortoise/monitoring.py` ×1 — so this PR re-cuts those too, and none of those three files is touched by this PR. ⛔ Read the two halves as one fact: **no ROW is added or removed by this file, and the ROW SET is not stable while `sdk.py` grows** — which is why `--markdown` output, not arithmetic on the old table, is what `tests/test_13098_cypher_inventory.py` grades.

**The cut this PR (#14002, `feat/14002-record-only-journal`) makes — one non-Cypher door call AND one unresolved site, both from the read-side classifier — re-cut by RUNNING the tool on the tree merged with `origin/main` `a296e6741e`, not by arithmetic on the markers.** `record_projection._journal_is_export` asks the RECORD whether it owns a graph's ordinal with `cursor.execute(CURSOR_PRESENCE_SQL, …)` — a statement named by a module constant, exactly the shape the census folds to `non_cypher_door_calls` — and its savepoint bookkeeping (the transaction-hygiene fix for review cycle 4, P2) issues `cursor.execute(statement)` from a helper whose SQL is a PARAMETER, which lands in the documented **over-report** arm (`unresolved`). So `sites` 1317→**1318**, `unresolved` 102→**103** with `shape_unresolved` and `shape_unresolved_unresolved` (`unresolved` was already 102 at this branch's previous head — the review cycle that added the presence read did not re-cut it, which is the drift `--check` reported on the `differential` job: `doc=1578 tool=1579` / `doc=261 tool=262`), `non_cypher_door_calls` 261→**262** (`record_projection.py` already carries sites, so `files` **100** and `files_scanned` **302** do not move), and `ast_door_calls` 1578→**1580** (= 1318 + 262). `plain` **868** / `candidates` **586** / `partial` **318** and every other shape are unchanged — no Cypher statement was added, removed, re-classified or re-shaped, which is why the migration metric §0 is about does not move. §1's `rg` reconciliation moves with the same two lines: door **1,634 / 108** whole-text, bare **1,638 / 109**, verb **2,365 / 147** unchanged (neither line carries a `MATCH`/`MERGE`). Appendix A is regenerated with `--markdown` at this tree.

**The cut this PR (#13906, `feat/13906-vector-move`) makes — one site, and it is a RE-CLASSIFICATION rather than a new query — re-cut at `origin/main` `ef1d6f7561` after the rebase.** The parity work moved `vector_search`'s kNN `SELECT` out of the function body into `tortoise/record_vector.py::knn_statement`, because the harness that EXPLAINs that read must plan the **same string** the read runs (the reviewed defect: an instrument that re-spells the SELECT can only prove something about its own spelling). The door call did not move and no statement was added — but the census's static resolver no longer follows it: the argument is now a value returned by another function, so the site lands in the documented **over-report** arm (`unresolved`) instead of the `SQL_VETO` arm that used to fold it to `non_cypher_door_calls`. The two operands therefore move by ONE in OPPOSITE directions and the reconciliation cannot lose or gain a call: `sites` 1316→**1317**, `unresolved` 101→**102** (the `shape_unresolved` and `shape_unresolved_unresolved` pins with it), `non_cypher_door_calls` 262→**261**, `files` 99→**100** (`record_vector.py` is the file that starts CARRYING a site); `ast_door_calls` **1578** (= 1317 + 261) and `files_scanned` **302** are unchanged, as are `plain` **868** / `candidates` **586** (which is why the migration metric §0 is about does not move: an `unresolved` site is never a candidate), `adjacency`, `ep`, `ddl`, `traversal`, `fulltext` and `partial` **318**. §1's `rg` reconciliation is unchanged at this tree (door **1,632 / 108** whole-text, verb **2,365 / 147**, bare **1,638 / 109**) because no door CALL line was added or removed — only the text it is handed. ⚠️ **This paragraph was re-cut TWICE**: the branch merged `origin/main` twice while it ran, and a rebase onto `ef1d6f7561` moved every Appendix A `file:line` row again — so the numbers above were derived by RUNNING the tool at the tree being merged, never carried from the branch head, and each earlier value (1325 / 102 / 110 / 261 / 1586 at the pre-rebase head) was this same cut against an earlier base. ⚠️ The paragraphs below describe earlier cuts against their own bases, so no number in them is the table's any more; §0, §3 and the pins carry the newest cut above (now `#14222`'s).

**The cut at the `origin/main` integration that carries this PR's merge (`#13557`/`#13558` ×
`#13610` × `#12244`):** re-derived by RUNNING the tool on the merged tree, never by arithmetic on the
markers — and ⛔ **the merge IS the measurement**, because a `pull_request` job checks out the merge
commit and a pin green on each parent can be red on their union (§7's own first paragraph). `main`
brought two cuts this branch never measured, and this paragraph records both rather than losing them
in the conflict: **`#13610`**'s `FalkorProjection._label_vector_count` (base `0d5bb6506f`) — one
`plain` read and, separately, one non-Cypher door call — and **`#12244`** (`1b1d108ca423`) — the
shared collapse behind `hosted_api._execute_commit_writes`, whose holder read and dangling-pointer
existence read are `plain` reads and whose edge write by entity is an `adjacency` write. With this
branch's own `commitments.py` site, from the common base `1319`:
`sites` **1316** = 1319 + 1 (`#13610`) + 3 (`#12244`) + 1 (this PR) − 8 (see below);
`plain` **868** (read **586**, write **282**) = 865 + 1 + 2;
`adjacency` **293** (read **192**, write **101**) = 292 + 1;
`unresolved` **101** = 108 + 1 − 8 (see below) — this PR's concatenated statement, the only
`unresolved` any of the two cuts adds;
`non_cypher_door_calls` **262** = 261 + 1 (`#13610`'s method is a door call AND a statement site,
which is why the two operands move together);
`partial` **318** = 315 + 1 + 2; `ast_door_calls` **1578** = 1316 + 262;
`files` **99** = 100 + 1 (`commitments.py`) − 2 (see below);
`files_scanned` **302** = 300 + 2 (`commitments.py`, plus `state_intent.py` which carries none).

⛔ **THE −8 IS NOT LOST COVERAGE — it is eight census FALSE POSITIVES that `#12901` removed.** The
door predicate matches `.<door>(` by NAME, and the SDK's own public wrapper `sdk.query(kind=…)`
answers to it while passing no statement at all (its `kind=` keyword is not one of the statement
carriers, so every one of these landed in `unresolved`). `#12901`'s final batch privatised that
wrapper (`sdk._query`), so the matcher no longer recognises it: 3 sites in `sdk.py`
(`self._query(kind=…)`), 4 in `tortoise_client.py` and 1 in `session_continuity.py`, and the latter
two files stop carrying any site at all (hence `files` 101 → 99). No Cypher statement left the
product — the true door sites are all still here, and the `unresolved` bucket is now 101 sites that
really do hand a door a statement this reader cannot follow.

§1's `rg` reconciliation is re-quoted from the tool at this tree (door **1,632 / 108** whole-text,
verb **2,365 / 147**, bare **1,638 / 109**), and the `--all-surface` row re-derived: **9,102 / 836**
across **2,507** scanned files. Appendix A is regenerated with `--markdown`, and `--check` prints
**`25 pinned counts match`**. ⚠️ The two paragraphs below describe each parent's cut against ITS OWN
base, so their numbers are relative and no longer the table's — the tables and pins carry THIS one.

**The cut for `#13557`/`#13558` (the reachable state reads):** `origin/fix/13128-occurrence-kind-spellings`
**`5f73f582a0`**, the read-layer PR this lane is deliberately based on (so it inherits that PR's changes
instead of colliding with them) — re-derived by RUNNING the tool on this tree, never by arithmetic on
the markers. The change adds **one** Cypher statement site and **no** non-Cypher door call:
`tortoise/commitments.py::build_cypher` is the live-commitment enumeration `#13557` needed, and it is
built by concatenation (the WHERE comes from `live._terminal_excluded` taken with ITS OWN defaults — the
exact De Morgan dual of the `live._terminal_expression` the ended read selects by; review round 1
removed a per-family vocabulary here, because a family seam on one side of a duality is how one record
ends up in both populations), so it lands as `unresolved` rather than `plain`. And it adds a
second scanned file that carries NO site (`tortoise/state_intent.py`, the question→population
detector), which is why `files_scanned` moves by two while `files` moves by one. So `sites`
1319→**1320**, `unresolved` 108→**109** (the `shape_unresolved` and
`shape_unresolved_unresolved` pins with it), `ast_door_calls` 1580→**1581**, `files` 100→**101**,
`files_scanned` 300→**302**; `plain` **865** (read **583**, write **282**), `candidates` **583**,
`non_cypher_door_calls` **261** and `partial` **315** are all unchanged — nothing here is a candidate
for the record store, which is the metric §0 is about. §1's `rg` reconciliation was re-measured at
that head (door **1,635 / 110** whole-text, verb **2,353 / 146**, bare **1,641 / 111**), and the
`--all-surface` row was re-derived rather than carried: **9,110 / 836** (2,425 files scanned), of which
exactly **3** sites and **2** carrying files are this lane's — enumerated, not inferred:
`tortoise/commitments.py` (the concatenated statement, `unresolved`),
`tests/test_13557_13558_reachable_reads.py` (the availability probe's `RETURN 1` read and the raw
legacy-row write the duality test needs). The rest of the move from the row's previous **9,082 / 827**
is drift this lane did not cause; the base with its three files absent is **9,107 / 834** (2,422
scanned). Appendix A is regenerated with `--markdown` here, so ⛔ **it moved far more than one row**.

**And the re-cut at this branch's merge with `origin/main` `9d25456544`:** the base this lane was
deliberately built on (`fix/13128-occurrence-kind-spellings`) landed as `#13604`'s squash commit
`18d406258f` while the lane ran, so the branch was integrated forward instead of being left to open a
PR that reverts `main`. **NO pin moved** — `--check` prints `25 pinned counts match` at both heads,
because `main`'s twenty commits insert no statement site — while **159 Appendix A rows** moved:
`main` inserts lines into `tortoise/mcp_server.py`, `tortoise/sdk.py` and `tortoise/tool_registry.py`
above existing sites, which shifts every `file:line` row beneath them while the counts hold. This is
the `file:line`-versus-SCALAR gap this section names, from yet another side — and Appendix A is
regenerated at the merged tree, not carried from the branch head.

⚠️ **AND THESE PINS ARE CUT AT A FEATURE BRANCH'S MERGE, NOT AT THE `main` IT WILL LAND ON, WHICH IS
WHY THE FINAL MERGE MUST RE-CUT THEM AGAIN IF `main` MOVES.** §7's rule above is that the pins are absolute counts of `tortoise/**` and move with the base:
`origin/main` is ahead of this lane's base, so the merge commit's census is not the one measured here,
and `--check` on the merge will report the difference. That is the documented procedure's step 1, not a
defect in this cut — but it is stated here rather than left for the merge to discover, because a pin
that reads green at a branch head and red at the merge is the failure this section exists to name.

**This PR's cut (#12244):** `origin/main` **`1b1d108ca423`**, forward-merged into
`fix/12244-merge-collapse` — re-derived by RUNNING the tool on the merged tree, never by arithmetic on
the markers. The change adds **three** Cypher statement sites and **no** non-Cypher door call, all in
`hosted_api._execute_commit_writes`' §6a `aboutObject` anchor and the shared collapse it now uses
(`entity_identity._collapse_resolved_holders`), which had to read its holders before attaching so that
a merged pair (one entity, two live rows) yields ONE edge:

* the holder read — `MATCH (o:Object) WHERE {space} RETURN o.id` — `plain` **read**,
  `candidate:SQL-shaped` (an id-list lookup, §4 row 2);
* the edge write by entity — `MATCH (p:Point {id:$pid}), (o:Object) WHERE o.id IN $ids
  MERGE (p)-[:aboutObject]->(o)` — `adjacency` **write**, `stays:graph-maintenance`;
* the existence read behind the collapse's dangling-pointer fallback —
  `MATCH (n:{label}) WHERE n.id IN $ids RETURN n.id` — `plain` **read**, `candidate:SQL-shaped`. It
  answers "does the id the hop returned still name a node of this label", because
  `follow_resolved_from` returns its target without proving it exists, and an id-keyed attach against
  a dead pointer writes nothing at all.

So `sites` 1320→**1323**, `plain` 866→**868** (read 584→**586**, write **282** unchanged), `adjacency`
292→**293** (read **192** unchanged, write 100→**101**), `candidates` 584→**586**, `partial`
316→**318**, and `ast_door_calls` 1582→**1585** (`1323` classified + the unchanged `262` non-Cypher
door calls). `files` **100**, `files_scanned` **300**, `unresolved` **108**, `non_cypher_door_calls`
**262** and every other shape are unchanged (all three sites live in files that already carry sites).

⚠️ **§1's reconciliation block was TWO BEHIND its own pins at that base, and this cut repairs it
by measurement rather than by arithmetic.** Re-running the tool's own predicate over
`origin/main` `1b1d108ca423` and over this tree gives **1,636 → 1,639** door occurrences; the block had
published `1,634` and `= 1,580 AST door calls` against pins that already said `1582` — the same
staleness, unmasked because the block's numbers were not pinned. The verb predicate reads **2,360
across 146 files** at this tree, and the tool run over that base prints `2,355` / `146` — the block it
carried published `2,351` / `145`, four occurrences and a file behind the tool on its OWN tree. The
tool is the authority for both, so both here are re-quoted from its output rather than corrected by
hand. Appendix A is regenerated with
`--markdown`, and `--check` prints **`25 pinned counts match`**.

**This PR's cut (#13610):** at base `0d5bb6506f`, re-derived by RUNNING the tool on this tree. The
change adds **one** Cypher statement site and **no** non-Cypher door call: `FalkorProjection.
_label_vector_count` (`tortoise/projection/__init__.py`) — the dense cutover's coverage denominator, a
`count(n)` over the label's vector-bearing nodes. It is a `plain` **read** candidate by this census's
classification, and it is a deliberate one: it is the cheap verification step of expand → backfill →
verify → contract (`#13610`), asked at most once per tenant per label per cache TTL, and the record
side of the same comparison is one `count(*)` on the table that already serves the label. So `sites`
1319→**1320**, `plain` 865→**866** (read 583→**584**, write **282** unchanged), `candidates`
583→**584**, `partial` 315→**316**, and the reconciliation's other operand moved by one as well —
`non_cypher_door_calls` 261→**262** and `ast_door_calls` 1580→**1582** — because the new method is a
door call as well as a statement site, which is what `--check` reports when the two are counted
together. `files` **100**, `files_scanned` **300**, `unresolved` **108** and every other shape are
unchanged (the method lives in a file that already carries sites). Appendix A is regenerated with
`--markdown`, and `--check` prints **`25 pinned counts match`**.

**This PR's cut:** `origin/main` **`9e9fc23276`**, forward-merged into
`feat/11609-p3-sdk-40` — re-derived by RUNNING the tool on the final merged tree, never by arithmetic
on the markers. This PR adds **five** Cypher statement sites and **no** non-Cypher door call, all of
them in the six tenancy methods #11609 P3 gave a `def` to (`tortoise/sdk.py`), each a read against the
control plane's own registry: `list_keys` (the `APIKey` row read), `revoke_key` (the owning-org read
that must happen BEFORE the write), `list_members` (`Membership` rows for the tenant), and
`_membership_remove` — which carries **two** sites, a read of the target's current role and the
`SET m.status='removed'` write. So `sites` 1314→**1319**, `plain` 860→**865** (read 579→**583**,
write 281→**282**), `candidates` 579→**583**, and `ast_door_calls` 1572→**1577**;
`non_cypher_door_calls` **258**, `unresolved` **108**, `partial` **315**, `files_scanned` **298** and
`files_with_sites` **100** are all unchanged (the six methods live in a file that already carries
sites). `main`'s five commits since this branch's base (`9e9fc23276` included) move **no** census
scalar — the drift `--check` reported before this regeneration was this PR's five sites and nothing
else. Appendix A is regenerated with `--markdown` at this merged tree, so ⛔ **it moved far more than
five rows**: the six methods are inserted around `tortoise/sdk.py:50151–51100`, so every `sdk.py` row
below them shifts — the same `file:line`-versus-SCALAR gap described above, from yet another side.
`--check` reports **`25 pinned counts match`**.

**This merge's base:** `origin/main` **`b64290529b`** (`#13466`), integrated here after the cut
above — re-derived by RUNNING the tool on the final merged tree, never by arithmetic on the
markers. **No Cypher-census SCALAR moved**: `sites` **1319**, `plain` **865** (read **583** /
write **282**), `candidates` **583**, `unresolved` **108**, `partial` **315**, `files_scanned`
**299** and `files_with_sites` **100** are unchanged, because the eleven tenancy methods sit in a
file that already carried sites. What DID move is the reconciliation's other operand: `main` adds
**one** non-Cypher door call, so `non_cypher_door_calls` 258→**259** and `ast_door_calls`
1577→**1578**. §1's `rg` reconciliation is re-measured at this tree — door **1,632 / 109**
whole-text, verb **2,338 / 144**, bare **1,638 / 110** — and `--check` prints **`25 pinned counts
match`**. Appendix A is regenerated with `--markdown` here, so ⛔ **it moved far more than one
row**: `main`'s `#13466` edits `tortoise/` above and below existing sites, which shifts every
`file:line` row beneath them while the COUNT holds — the `file:line`-versus-SCALAR gap this
section describes, from yet another side. ⚠️ The paragraph above says `non_cypher_door_calls`
stays 258; that was true of THIS PR's own diff at its cut and is no longer true of the merged
tree, which is why the number is re-cut here rather than carried.

**And a SECOND integration at the same cut:** the branch's own next commit, **`2454724a4b`**
(the actor rows reading the store auth reads; the graph scope walking claims), adds **193** net
lines to `tortoise/sdk.py` (`198` inserted, `5` removed) and **no** census statement site, so every
SCALAR above still holds — `--check` prints **`25 pinned counts match`** — while **173 Appendix A
rows** moved: **+19** for **87** of them, **+60** for `1`, **+97** for `4`, **+116** for `2` and
**+193** for **79**, because the insertions sit at five different depths and the shifts are
cumulative (`tortoise/sdk.py:40774` → **`40793`** is one of the `+19` rows). That is
the `file:line`-versus-SCALAR gap this section describes, from the fifth side: the pins were GREEN
over an index whose rows no longer resolve, and only re-running `--markdown` shows it. Appendix A
is therefore regenerated here a second time at the same head.

**And a THIRD integration:** `origin/main` **`549bad0c77`** (`#13331`/`#13345`, the live-write
chokepoint). **No Cypher-census SCALAR moved** again (`sites` **1319**, `plain` **865**, read
**583** / write **282**, `candidates` **583**, `unresolved` **108**, `partial` **315**,
`files_with_sites` **100**), and this time the OTHER operand moved by more than one: `main` adds
**two** non-Cypher door calls and **one** scanned file, so `non_cypher_door_calls` 259→**261**,
`ast_door_calls` 1578→**1580** and `files_scanned` 299→**300**. §1's `rg` reconciliation is
re-measured at this tree (door **1,634 / 109** whole-text, verb **2,338 / 144**, bare **1,640 /
110**), and Appendix A is regenerated with `--markdown`. `--check` prints **`25 pinned counts
match`**.

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
(`@@ -7742,9 +7761,11 @@` **+2**), which moves nothing.

**Cut by `#13447` (PR #13456), at the merge of `origin/main` `b2760e4fe8`:** `files_scanned`
298→**299**, and NOTHING else moves. The seam module `tortoise/derived_embedding.py` is a new
product-surface file, so the population grows by one, while it carries **0** statement sites, **0**
door calls and **0** `MATCH|MERGE` predicate matches — measured at this tree, `rg -n 'MERGE'
tortoise/derived_embedding.py` finds nothing: the header comment that used to quote
`MERGE (s:Source {url:$id})` was removed when main's callable-table resolution rewrote that header,
so the module contributes nothing to the `rg` side either. So `sites`, `files_with_sites`,
`unresolved`, `candidates`, `partial` and both door totals are unchanged and `--check` re-derives
`25 pinned counts match`. §1's reconciliation is re-measured at this tree: door **1,626 / 109**
(unchanged, exactly as the paragraph above predicted), and the verb predicate reads **2,333 / 144**
against the tool's **2,346 / 145** — the same one-file, 13-occurrence WHOLE-TEXT gap, which this
module neither widens nor narrows. ⛔ `#13447`'s review asked for this re-pin
because the required `differential` leg (`tools/cypher_inventory.py --check`) reads the two numbers
against each other and reds on `DRIFT files_scanned: doc=298 tool=299`. Appendix A is regenerated with
`--markdown` on this merged tree, so every paragraph above and the table it describes are one cut. ⛔
That is now **two** merges in a row on which the appendix rotted green — the `b8723c0f16` paragraph
above is the first — which is the argument for the byte-compare arm, not for re-cutting by hand.

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

**The cut this branch (#13337, `fix/12943-cache-projection-residual`) makes — ONE non-Cypher door
call and TWO scanned files, and no Cypher statement at all.** Re-cut by RUNNING the tool on the tree
**merged with `origin/main` `f392bb67d5`**, never by arithmetic on the markers. `tortoise/cache_graph.py`
is new and carries no statement text: its `get_control_plane().query("graphs", select=…, filters=…)`
is the one non-Cypher door call, so `non_cypher_door_calls` 262→**263**, `files_scanned` 302→**304**,
and `ast_door_calls` 1580→**1581** (= 1318 + 263). `sites` **1318**, `unresolved` **103**,
`candidates` **586**, `files` **100**, `partial` **318** and every shape are unchanged — nothing in
this change is a query, so the migration metric §0 is about does not move.
⚠️ **The second scanned file is `main`'s, not this branch's.** `#14011` (PR #14065, main
`ac57b2d38a`) added `tortoise/usage_signal.py` and did not re-cut this doc, so `--check` was already
reporting `files_scanned: doc=302 tool=303` on `main` before this branch merged it. One re-cut carries
both, which is why a paragraph for one change explains two files.

**The cut this PR (#14012) makes — the SIX statement sites in TWO new product files — and it is the
first cut in this document whose new `plain` READS are migration candidates.** Re-cut by RUNNING the
tool on this tree, never by arithmetic on the markers.
`tortoise/resident_pin.py` (the bounded pin, `SEARCH-ARCHITECTURE.md` §7) and
`tortoise/outside_view.py` (what the fast layer does not hold) are product surface and each carries
sites, so `files` 100→**102** and `files_scanned` 304→**306**; `sites` 1318→**1324** and
`ast_door_calls` 1580→**1586** (= 1324 + 262, `non_cypher_door_calls` **262** unchanged — neither file
opens a non-Cypher door). The six sites split three ways: **two `plain` READS** — the resident key
list (`MATCH (n:{label}) RETURN n.{key_prop} AS key LIMIT {bound}`) and the resident count
(`MATCH (n:{label}) RETURN count(n)`) — which is why `plain` 868→**872** (read 586→**588**),
`candidates` 586→**588** and `partial` 318→**320** (both interpolate an identifier, the shape the
`???` marker exists for); **two `plain` WRITES** — the one `:Meta` declaration (`MERGE … SET n +=
$props`) and its removal (`MATCH (n:Meta {key:$key}) DETACH DELETE n`) — which keep `plain` write
282→**284** and stay `graph-maintenance`, because a pin declaration IS graph state and `#13083`'s
`record_only` shape is what will eventually move them; and **two `unresolved`** — the two
`_rows(graph, statement, params)` helpers, whose statement is a parameter, landing in the documented
over-report arm so `unresolved` 103→**105**. `adjacency` **293**, `ep` **32**, `ddl` **17**,
`traversal` **3** and `fulltext` **2** are unchanged, because none of the six is an edge pattern,
an epistemic scalar or a DDL statement. ⚠️ **The two new candidates are the migration metric moving
for a FEATURE, not for a cleanup**: this PR adds reads that are SQL-shaped by construction (they
count and list one tenant's cache keys), and the honest census counts them rather than exempting
them.

**The cut the capacity-notice merge (#14038, `feat/13998-threshold-emails`) makes — ONE new scanned
file that carries NO statement, and FOUR non-Cypher door calls.** Re-cut by RUNNING the tool on the
merged tree, never by arithmetic on the markers. `tortoise/capacity_notice.py` (#13998's copy,
thresholds, render and decision) opens **two** non-Cypher doors and `tortoise/supabase_control.py`
gains **two** more (its durable-mark read and its compare-and-swap write), so
`non_cypher_door_calls` 262→**266**, `files_scanned` 307→**309** (this lane's one new module plus
`#14263`'s `tortoise/relation_guards.py`, the cut recorded in §6 below) and `ast_door_calls`
1586→**1591** (= 1325 + 266). `sites` **1325** — `#14263`'s one `plain` read, this merge adding none
— `files` **102**, `unresolved` **105**, `candidates` **589**, `partial` **321** and every shape are
as the merged tree measures them; nothing THIS merge adds is a Cypher statement, so the migration
metric §0 is about does not move for it. §1's `rg` reconciliation is re-measured at this tree (door
**1,645 / 112** whole-text, verb **2,379 / 149**, bare **1,651 / 113**), and Appendix A is regenerated
with `--markdown` at this head — the two new files' six rows are in it, so a reader can resolve every
one.

<!-- cypher-inventory:sites = 1327 -->
<!-- cypher-inventory:files = 102 -->
<!-- cypher-inventory:files_scanned = 312 -->
<!-- cypher-inventory:unresolved = 105 -->
<!-- cypher-inventory:candidates = 589 -->
<!-- cypher-inventory:shape_adjacency = 293 -->
<!-- cypher-inventory:shape_ddl = 17 -->
<!-- cypher-inventory:shape_ep = 32 -->
<!-- cypher-inventory:shape_fulltext = 2 -->
<!-- cypher-inventory:shape_plain = 875 -->
<!-- cypher-inventory:shape_traversal = 3 -->
<!-- cypher-inventory:shape_unresolved = 105 -->
<!-- cypher-inventory:shape_plain_read = 589 -->
<!-- cypher-inventory:shape_plain_write = 286 -->
<!-- cypher-inventory:shape_adjacency_read = 192 -->
<!-- cypher-inventory:shape_adjacency_write = 101 -->
<!-- cypher-inventory:shape_ep_read = 21 -->
<!-- cypher-inventory:shape_ep_write = 11 -->
<!-- cypher-inventory:shape_ddl_ddl = 17 -->
<!-- cypher-inventory:shape_traversal_read = 3 -->
<!-- cypher-inventory:shape_fulltext_read = 2 -->
<!-- cypher-inventory:shape_unresolved_unresolved = 105 -->
<!-- cypher-inventory:non_cypher_door_calls = 266 -->
<!-- cypher-inventory:ast_door_calls = 1593 -->
<!-- cypher-inventory:partial = 321 -->

---

## Appendix A — every statement site, by shape

| file:line | fn | shape | role | verdict | statement |
|---|---|---|---|---|---|
| `tortoise/__main__.py:777` | `_cmd_reconcile` | plain | read | candidate:SQL-shaped | `UNWIND $ids AS eid MATCH (e:Event {eventId:eid}) RETURN e.eventId` |
| `tortoise/__main__.py:2223` | `_cmd_init` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/__main__.py:2247` | `_cmd_init` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/__main__.py:13842` | `_cmd_verify` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: 'test-verify'}) RETURN p` |
| `tortoise/__main__.py:13850` | `_cmd_verify` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: 'test-verify'}) RETURN p.content` |
| `tortoise/__main__.py:13855` | `_cmd_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point {id: 'test-verify'}) DELETE p` |
| `tortoise/__main__.py:14488` | `_cmd_backfill` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point) WHERE p.status IS NULL SET p.status = 'live' RETURN …` |
| `tortoise/__main__.py:14490` | `_cmd_backfill` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point) WHERE p.createdAt IS NULL SET p.createdAt = $now RET…` |
| `tortoise/__main__.py:16341` | `_cmd_doctor` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/__main__.py:18072` | `_cmd_key_create` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) RETURN t.id, t.name` |
| `tortoise/__main__.py:18101` | `_cmd_key_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (:TeamMeta {name: $name, created: $now})` |
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
| `tortoise/api.py:2932` | `EventAPI.add_object` | plain | read | candidate:SQL-shaped | `MATCH (n:Object {id:$id}) RETURN count(n)` |
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
| `tortoise/cache_evictor.py:704` | `_delete_nodes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `UNWIND $rows AS r MATCH (n) WHERE id(n) = r.id AND n.{} = r.key DETA…` |
| `tortoise/cache_evictor.py:724` | `_delete_nodes` | plain | read | candidate:SQL-shaped | `UNWIND $rows AS r MATCH (n) WHERE id(n) = r.id AND n.{} = r.key RETU…` |
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
| `tortoise/commitments.py:271` | `enumerate_live_commitments` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
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
| `tortoise/ended.py:365` | `enumerate_ended` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/entity_identity.py:309` | `_adopt_idless_holder` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{}) WHERE ({}) AND {} AND n.id IS NULL SET n.id = $id, n.ca…` |
| `tortoise/entity_identity.py:479` | `_collapse_resolved_holders` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id` |
| `tortoise/entity_identity.py:500` | `_collapse_resolved_holders` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids AND {} RETURN n.id` |
| `tortoise/entity_identity.py:1135` | `_name_holder_ids` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE {} {}RETURN n.id` |
| `tortoise/entity_identity.py:1184` | `_endpoint_idents` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/entity_identity.py:1223` | `_require_unique_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE {} AND n.id = $id RETURN n.id` |
| `tortoise/entity_identity.py:1390` | `_record_if_any_holder` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE {} AND {} RETURN n.id` |
| `tortoise/entity_identity.py:1457` | `resolve_entity_id` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$value}) RETURN n.id` |
| `tortoise/entity_identity.py:1569` | `resolve_document_target_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n:Object OR (n:Source AND n.documentKind IS NOT NUL…` |
| `tortoise/entity_identity.py:1581` | `resolve_document_target_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n:Object OR (n:Source AND n.documentKind IS NOT NUL…` |
| `tortoise/entity_identity.py:1601` | `resolve_document_target_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n:Object OR (n:Source AND n.documentKind IS NOT NUL…` |
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
| `tortoise/extractor_v2.py:3030` | `_enrich_point_priors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point) WHERE p.id IN $ids OPTIONAL MATCH (p)-[:aboutObject]…` |
| `tortoise/extractor_v2.py:3083` | `_entity_facts` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, n.resolved_from, n.name…` |
| `tortoise/extractor_v2.py:6996` | `_entity_verdict_facts` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, n.resolved_from, n.dedu…` |
| `tortoise/extractor_v2.py:7029` | `_entity_verdict_facts` | plain | read | candidate:SQL-shaped | `MATCH (d:{}) WHERE d.resolved_from IN $ids RETURN d.resolved_from` |
| `tortoise/fallback_snapshot.py:700` | `build_snapshot` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator = false OR n.is_operator IS NUL…` |
| `tortoise/fallback_snapshot.py:711` | `build_snapshot` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator = false OR n.is_operator IS NUL…` |
| `tortoise/goal_judgement.py:1232` | `latest_readings` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator RETURN c…` |
| `tortoise/goal_judgement.py:1236` | `latest_readings` | plain | read | candidate:SQL-shaped | `{} AND id(e) >= $cursor RETURN id(e), e.id, e.value, e.unit, e.start…` |
| `tortoise/goal_judgement.py:1357` | `live_judgements` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE ??? RETURN properties(p) ORDER BY coalesce(p.e…` |
| `tortoise/goal_judgement.py:1591` | `_ensure_judgement_edge` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$i})-[:INPUT]->(op:Point)-[:IMPL]->(g) WHERE g.id…` |
| `tortoise/goal_judgement.py:1657` | `_extra_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$i})-[:INPUT]->(op:Point)-[:IMPL]->(g) WHERE g.id…` |
| `tortoise/goal_judgement.py:1758` | `_supersession_evidence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$i})-[r]-() RETURN type(r) LIMIT 1` |
| `tortoise/graph_delete_guard.py:255` | `safe_graph_delete` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n) DETACH DELETE n` |
| `tortoise/hosted_api.py:707` | `_write_capture_phase` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_extraction_phase=$p, s.cap…` |
| `tortoise/hosted_api.py:2175` | `_iter_registered_orgs._read_page` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:2343` | `_iter_registered_orgs` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) WHERE t.deleted_at IS NULL RETURN t.id, t.name` |
| `tortoise/hosted_api.py:3832` | `_lifespan._graph_provider` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) WHERE g.kind = 'custom' AND coalesce(g…` |
| `tortoise/hosted_api.py:7566` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (t:Team { id: $id, name: $name, tier: 'free', created_at: $no…` |
| `tortoise/hosted_api.py:7585` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey { id: $id, org_id: $org_id, key_hash: $hash, key_pr…` |
| `tortoise/hosted_api.py:7612` | `provision_tenant` | plain | read | candidate:SQL-shaped | `MATCH (m:TeamMeta) RETURN count(m)` |
| `tortoise/hosted_api.py:7641` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (m:Membership { id: $id, user_id: $user_id, org_id: $org_id, …` |
| `tortoise/hosted_api.py:7687` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) DETACH DELETE t` |
| `tortoise/hosted_api.py:7688` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id: $id}) DETACH DELETE k` |
| `tortoise/hosted_api.py:7691` | `provision_tenant` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id: $id}) DETACH DELETE m` |
| `tortoise/hosted_api.py:9885` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey) WHERE k.key_prefix = $prefix AND k.revoked_at IS NU…` |
| `tortoise/hosted_api.py:9894` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey) WHERE k.revoked_at IS NULL AND (k.expires_at IS NUL…` |
| `tortoise/hosted_api.py:9927` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey) WHERE k.revoked_at IS NULL AND (k.expires_at IS NUL…` |
| `tortoise/hosted_api.py:9949` | `get_current_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.last_used_at = $now` |
| `tortoise/hosted_api.py:9976` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.tier, t.max_users, t.max_graphs, t…` |
| `tortoise/hosted_api.py:10014` | `get_current_org` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.namespace` |
| `tortoise/hosted_api.py:10660` | `_assert_graph_owned` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.namespace` |
| `tortoise/hosted_api.py:11403` | `_read_stored_session_harness` | plain | read | candidate:SQL-shaped | `OPTIONAL MATCH (s:Session {id:$sid}) RETURN s.harness AS harness` |
| `tortoise/hosted_api.py:14036` | `_capture_session_probe_off_loop._probe` | plain | read | candidate:SQL-shaped | `OPTIONAL MATCH (s:Session {id:$sid}) RETURN count(s) AS n, s.capture…` |
| `tortoise/hosted_api.py:15185` | `list_points` | plain | read | candidate:SQL-shaped | `MATCH (n:Point){} WHERE ??? RETURN properties(n) ORDER BY n.createdA…` |
| `tortoise/hosted_api.py:15354` | `get_point._cache_props` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: $id}) WHERE {} RETURN properties(p)` |
| `tortoise/hosted_api.py:15372` | `get_point._cache_sees_node` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: $id}) RETURN properties(p)` |
| `tortoise/hosted_api.py:16094` | `org_info` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE NOT n.id IN $demo_ids RETURN count(n)` |
| `tortoise/hosted_api.py:17035` | `register_user` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) WHERE t.email = $email RETURN t.id` |
| `tortoise/hosted_api.py:17176` | `register_user` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (t:Team { id: $id, name: $name, email: $email, tier: 'free', …` |
| `tortoise/hosted_api.py:17193` | `register_user` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey { id: $id, org_id: $org_id, key_hash: $hash, key_pr…` |
| `tortoise/hosted_api.py:17252` | `register_user` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) DETACH DELETE t` |
| `tortoise/hosted_api.py:17255` | `register_user` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id: $id}) DETACH DELETE k` |
| `tortoise/hosted_api.py:17749` | `_seed_demo_graph` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: '_demo_sentinel'}) RETURN p.id` |
| `tortoise/hosted_api.py:17758` | `_seed_demo_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:Point {id:$id}) SET p.content=$c, p.pointKind=$k, p.is_oper…` |
| `tortoise/hosted_api.py:17767` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}) MERGE (t:Tag {name:$tag}) MERGE (p)-[:TAGG…` |
| `tortoise/hosted_api.py:17790` | `_seed_demo_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET s.created_at=$now, s.turn_count=0` |
| `tortoise/hosted_api.py:17883` | `_seed_demo_graph` | ep | write | stays:ep-carveout (§6.3) | `MERGE (p:Point {id:$id}) SET p.content=$c, p.pointKind=$k, p.is_oper…` |
| `tortoise/hosted_api.py:17892` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}) MERGE (t:Tag {name:$tag}) MERGE (p)-[:TAGG…` |
| `tortoise/hosted_api.py:17902` | `_seed_demo_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:Point {id:$id}) SET p.content=$c, p.pointKind=$k, p.is_oper…` |
| `tortoise/hosted_api.py:17911` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}) MERGE (t:Tag {name:$tag}) MERGE (p)-[:TAGG…` |
| `tortoise/hosted_api.py:17920` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (c:Point {id:'epis_claim1'}), (f:Point {id:'sem_fact_layers'})…` |
| `tortoise/hosted_api.py:17925` | `_seed_demo_graph` | adjacency | write | stays:graph-maintenance | `MATCH (w:Point {id:'proc_wf1'}), (c:Point {id:'epis_claim1'}) MERGE …` |
| `tortoise/hosted_api.py:17931` | `_seed_demo_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (p:Point {id:'_demo_sentinel', content:'demo-sentinel', point…` |
| `tortoise/hosted_api.py:18380` | `_ensure_graph_exists` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.kind, g.status` |
| `tortoise/hosted_api.py:18535` | `_key_created_analytics` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid}) RETURN m.user_id LIMIT 1` |
| `tortoise/hosted_api.py:18568` | `_rotatable_key_row` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id: $id}) RETURN k.org_id, k.name, k.graph_id, k.sc…` |
| `tortoise/hosted_api.py:18604` | `_claim_key_revocation` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id: $tid, id: $id}) WHERE k.revoked_at IS NULL …` |
| `tortoise/hosted_api.py:18630` | `_force_revoke_key_row` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id: $tid, id: $id}) SET k.revoked_at = $now` |
| `tortoise/hosted_api.py:19374` | `list_api_keys` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id: $tid, graph_id: $gid}) RETURN k.id, k.key_p…` |
| `tortoise/hosted_api.py:19385` | `list_api_keys` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id: $tid}) RETURN k.id, k.key_prefix, k.created…` |
| `tortoise/hosted_api.py:19515` | `revoke_api_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id: $id}) RETURN k.org_id, k.revoked_at` |
| `tortoise/hosted_api.py:19531` | `revoke_api_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.revoked_at = $now` |
| `tortoise/hosted_api.py:19798` | `toggle_api_key_enabled` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id: $id}) RETURN k.org_id, k.revoked_at, k.created_…` |
| `tortoise/hosted_api.py:19863` | `toggle_api_key_enabled` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.name = $name` |
| `tortoise/hosted_api.py:19869` | `toggle_api_key_enabled` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.scopes = $sc` |
| `tortoise/hosted_api.py:20573` | `_capture_abandoned_marker` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_ok=false, s.capture_extrac…` |
| `tortoise/hosted_api.py:20613` | `_clear_capture_phase` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_extraction_phase=null, s.c…` |
| `tortoise/hosted_api.py:22322` | `_capture_session_impl._report_capture._link_off_loop` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.entity_links_attempted=$a, s.entit…` |
| `tortoise/hosted_api.py:22361` | `_capture_session_impl._report_capture._session_alive` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN count(s)` |
| `tortoise/hosted_api.py:22406` | `_capture_session_impl._report_capture._sweep_orphaned_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (s:Session {id:$sid}) WITH count(s) AS present WHERE …` |
| `tortoise/hosted_api.py:22440` | `_capture_session_impl._report_capture._sweep_orphaned_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (s:Session {id:$sid}) WITH count(s) AS present WHERE …` |
| `tortoise/hosted_api.py:22475` | `_capture_session_impl._report_capture._sweep_orphaned_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (s:Session {id:$sid}) WITH count(s) AS present WHERE …` |
| `tortoise/hosted_api.py:22972` | `_capture_session_impl._report_capture._enrichment_read` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, coalesce(n.pointKind…` |
| `tortoise/hosted_api.py:23124` | `_capture_session_impl._report_capture` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_ok=$ok, s.capture_extracto…` |
| `tortoise/hosted_api.py:23780` | `_load_commit_graph_state` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN s.is_episodic, coalesce(s.value_n…` |
| `tortoise/hosted_api.py:23791` | `_load_commit_graph_state` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids RETURN p.id, p.content` |
| `tortoise/hosted_api.py:23796` | `_load_commit_graph_state` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) RETURN o.name, o.objectKind` |
| `tortoise/hosted_api.py:23800` | `_load_commit_graph_state` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true})-[r]->(t) WHERE type(r) IN ['IMPL'…` |
| `tortoise/hosted_api.py:23862` | `_load_commit_graph_state` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true, op_type:'IMPL'}) MATCH (o)-[:IMPL …` |
| `tortoise/hosted_api.py:23909` | `_store_commit_telemetry` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (r:CommitRecord {client_commit_id:$cid}) SET r.telemetry=$t, r…` |
| `tortoise/hosted_api.py:23987` | `_prevalidate_supersede_window` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom` |
| `tortoise/hosted_api.py:24007` | `_prevalidate_supersede_window` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom, n.createdAt` |
| `tortoise/hosted_api.py:24483` | `_execute_commit_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (r:CommitRecord {client_commit_id:$cid}) WITH collect…` |
| `tortoise/hosted_api.py:24555` | `_execute_commit_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$did}) SET s.id=coalesce(s.id, $did), s.sourceK…` |
| `tortoise/hosted_api.py:24665` | `_execute_commit_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId:$eid}) SET e.id=$eid, e.eventKind='AgentSess…` |
| `tortoise/hosted_api.py:24677` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (e:Event {eventId:$eid}), (s:Source {url:$did}) MERGE (e)-[:pr…` |
| `tortoise/hosted_api.py:24692` | `_execute_commit_writes` | ep | write | stays:ep-carveout (§6.3) | `MERGE (e:Event {eventId:$eid}) SET e.id=$eid, e.eventKind=$ek, e.nam…` |
| `tortoise/hosted_api.py:24704` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (e:Event {eventId:$eid}), (s:Source {url:$did}) MERGE (e)-[:pr…` |
| `tortoise/hosted_api.py:24843` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (a:Source {url:$u1}), (b:Source {url:$u2}) MERGE (a)-[:referen…` |
| `tortoise/hosted_api.py:24912` | `_execute_commit_writes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point {id:$pid}) SET p.updatedAt=$now` |
| `tortoise/hosted_api.py:25038` | `_execute_commit_writes._terminal_state` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:25150` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/hosted_api.py:25279` | `_execute_commit_writes` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.name = $n AND {} RETURN o.id` |
| `tortoise/hosted_api.py:25306` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (e:Event {eventId:$eid}), (o:Object {id:$oid}) MERGE (e)-[:abo…` |
| `tortoise/hosted_api.py:25461` | `_execute_commit_writes` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE ({}) AND o.name = $exact RETURN o.id` |
| `tortoise/hosted_api.py:25515` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}), (o:Object) WHERE o.id IN $ids MERGE (p)-[…` |
| `tortoise/hosted_api.py:25525` | `_execute_commit_writes` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}), (o:Object) WHERE ({}) AND o.name = $exact…` |
| `tortoise/hosted_api.py:26011` | `commit_session._commit_region` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET s.is_episodic=true, s.created_at=coa…` |
| `tortoise/hosted_api.py:26109` | `commit_session._commit_region` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (r:CommitRecord {client_commit_id:$cid}) SET r.write_ops_bille…` |
| `tortoise/hosted_api.py:26327` | `list_sessions` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session) WHERE ??? OPTIONAL MATCH (s)-[:CONTAINS]->(p:Point…` |
| `tortoise/hosted_api.py:26671` | `get_session_detail` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN s.id, s.created_at, s.turn_count,…` |
| `tortoise/hosted_api.py:26711` | `get_session_detail` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE (p.pointKin…` |
| `tortoise/hosted_api.py:26808` | `get_session_detail` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'…` |
| `tortoise/hosted_api.py:26927` | `get_session_detail` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE (p.pointKin…` |
| `tortoise/hosted_api.py:26951` | `get_session_detail` | plain | read | candidate:SQL-shaped | `MATCH (src:Source {url:$url}) RETURN src.url, src.sourceKind, src.ev…` |
| `tortoise/hosted_api.py:27120` | `_scorecard_for_org._read_graph` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:27123` | `_scorecard_for_org._read_graph` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:28063` | `_session_count_by_harness` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) WHERE s.harness = $h RETURN count(s)` |
| `tortoise/hosted_api.py:28068` | `_session_count_by_harness` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) WHERE s.harness IS NULL RETURN count(s)` |
| `tortoise/hosted_api.py:28182` | `delete_session._delete_session_sync` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN s.id, s.harness` |
| `tortoise/hosted_api.py:28207` | `delete_session._delete_session_sync` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE p.eventId I…` |
| `tortoise/hosted_api.py:28212` | `delete_session._delete_session_sync` | plain | read | candidate:SQL-shaped | `MATCH (src:Source {url:$url}) RETURN src.eventId` |
| `tortoise/hosted_api.py:28225` | `delete_session._delete_session_sync` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) DETACH DELETE p R…` |
| `tortoise/hosted_api.py:28243` | `delete_session._delete_session_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event) WHERE e.eventId IN $ids DETACH DELETE e` |
| `tortoise/hosted_api.py:28252` | `delete_session._delete_session_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (src:Source {url:$url}) DETACH DELETE src` |
| `tortoise/hosted_api.py:28256` | `delete_session._delete_session_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) DETACH DELETE s` |
| `tortoise/hosted_api.py:28329` | `_user_memberships` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active'}) WHERE m.org_id …` |
| `tortoise/hosted_api.py:28360` | `_membership_org` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, org_id:$tid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:28438` | `_org_node` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:28667` | `_count_active_free_memberships` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active'}) WHERE m.org_id …` |
| `tortoise/hosted_api.py:28726` | `_owned_free_org_ids` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active', role:'owner'}) W…` |
| `tortoise/hosted_api.py:29105` | `_eager_provision_org_graph` | plain | read | candidate:SQL-shaped | `MATCH (m:TeamMeta) RETURN count(m)` |
| `tortoise/hosted_api.py:29416` | `_create_org_registry_lane` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, role:'owner'}) WHERE m.created_at…` |
| `tortoise/hosted_api.py:29437` | `_create_org_registry_lane` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {name:$name}) RETURN t.id, t.graph_name, t.tier, t.del…` |
| `tortoise/hosted_api.py:29455` | `_create_org_registry_lane` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$oid, user_id:$uid}) WHERE m.status = 'a…` |
| `tortoise/hosted_api.py:29821` | `_rollback_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) DETACH DELETE g` |
| `tortoise/hosted_api.py:30355` | `_apply_graph_recording_override` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.id, g.kind, coalesce(g.status…` |
| `tortoise/hosted_api.py:30977` | `_apply_graph_rename` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.id, g.kind, coalesce(g.status…` |
| `tortoise/hosted_api.py:31598` | `delete_graph` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.kind` |
| `tortoise/hosted_api.py:31756` | `_graph_row_probe` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.kind, coalesce(g.sta…` |
| `tortoise/hosted_api.py:31799` | `_rollback_restore_name_race` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) SET g.status = 'deleted', g.d…` |
| `tortoise/hosted_api.py:31834` | `_trash_name_conflict` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid, name:$name}) RETURN g.id, coalesce(g.st…` |
| `tortoise/hosted_api.py:32277` | `_membership_role` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, org_id:$tid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:32487` | `_require_owner` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, org_id:$tid}) RETURN m.role, m.st…` |
| `tortoise/hosted_api.py:32652` | `invite_to_org` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:32671` | `invite_to_org` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN count(m)` |
| `tortoise/hosted_api.py:32676` | `invite_to_org` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {org_id:$tid}) WHERE i.accepted_at IS NULL AND (…` |
| `tortoise/hosted_api.py:32699` | `invite_to_org` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {org_id:$tid, email:$email}) WHERE i.accepted_at…` |
| `tortoise/hosted_api.py:32719` | `invite_to_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (i:Invitation {id:$id, org_id:$tid, email:$email, role:$role,…` |
| `tortoise/hosted_api.py:32728` | `invite_to_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:Membership {org_id:$tid, user_id:$fake}) ON CREATE SET m.ro…` |
| `tortoise/hosted_api.py:32739` | `invite_to_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.email_sent_at = $now` |
| `tortoise/hosted_api.py:32796` | `invite_info._registry_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation) WHERE i.accepted_at IS NULL AND (i.status IS NU…` |
| `tortoise/hosted_api.py:32856` | `invite_info` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:32906` | `_sweep_terminal_invite_placeholders` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {org_id:$tid, email:$email}) WHERE i.accepted_at…` |
| `tortoise/hosted_api.py:32930` | `_delete_fake_invite_membership` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, user_id:$fake}) DELETE m` |
| `tortoise/hosted_api.py:33009` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation) WHERE i.accepted_at IS NULL AND (i.status IS NU…` |
| `tortoise/hosted_api.py:33034` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:33053` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:33074` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:33080` | `accept_invite` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN count(m)` |
| `tortoise/hosted_api.py:33097` | `accept_invite` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:33345` | `_registry_pending_invite_by_token` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation) WHERE i.accepted_at IS NULL AND (i.status IS NU…` |
| `tortoise/hosted_api.py:33366` | `_registry_otp_issue` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:33391` | `_registry_otp_verify` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.otp_hash, i.otp_expires_at, i…` |
| `tortoise/hosted_api.py:33407` | `_registry_otp_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.otp_hash = null, i.otp_expires_a…` |
| `tortoise/hosted_api.py:33415` | `_registry_otp_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.otp_attempts = COALESCE(i.otp_at…` |
| `tortoise/hosted_api.py:33420` | `_registry_otp_verify` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.otp_attempts` |
| `tortoise/hosted_api.py:33425` | `_registry_otp_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.otp_hash = null, i.otp_expires_a…` |
| `tortoise/hosted_api.py:33433` | `_registry_otp_verify` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.otp_attempts = 0` |
| `tortoise/hosted_api.py:33506` | `_registry_mismatch_accept_v2` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:33514` | `_registry_mismatch_accept_v2` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:33523` | `_registry_mismatch_accept_v2` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:33529` | `_registry_mismatch_accept_v2` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN count(m)` |
| `tortoise/hosted_api.py:33550` | `_registry_mismatch_accept_v2` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:33757` | `invite_otp` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:33811` | `resend_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.org_id, i.email, i.role, i.ac…` |
| `tortoise/hosted_api.py:33842` | `resend_invite` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:33855` | `resend_invite` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:33894` | `expire_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.org_id, i.status, i.accepted_…` |
| `tortoise/hosted_api.py:33907` | `expire_invite` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.status IS NULL OR i.status = '…` |
| `tortoise/hosted_api.py:33979` | `list_pending_invites_for_me` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {email:$email}) WHERE i.accepted_at IS NULL AND …` |
| `tortoise/hosted_api.py:34008` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.id, i.org_id, i.email, i.role…` |
| `tortoise/hosted_api.py:34033` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:34047` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:34062` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/hosted_api.py:34068` | `_registry_accept_by_id` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN count(m)` |
| `tortoise/hosted_api.py:34084` | `_registry_accept_by_id` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.accepted_at IS NULL AND (i.sta…` |
| `tortoise/hosted_api.py:34162` | `decline_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.email, i.org_id, i.status, i.…` |
| `tortoise/hosted_api.py:34179` | `decline_invite` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) WHERE i.status IS NULL OR i.status = '…` |
| `tortoise/hosted_api.py:34185` | `decline_invite` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN i.status, i.accepted_at` |
| `tortoise/hosted_api.py:34271` | `list_members` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid}) WHERE m.status = 'active' OR m.st…` |
| `tortoise/hosted_api.py:34314` | `remove_member` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) RETURN m.role` |
| `tortoise/hosted_api.py:34322` | `remove_member` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) SET m.status='remov…` |
| `tortoise/hosted_api.py:34365` | `change_member_role` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) RETURN m.role` |
| `tortoise/hosted_api.py:34373` | `change_member_role` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) SET m.role=$role` |
| `tortoise/hosted_api.py:34419` | `_org_members_sync` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, status:'active'}) RETURN m.user_id…` |
| `tortoise/hosted_api.py:34469` | `_export_graph_snapshot` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN labels(n), properties(n)` |
| `tortoise/hosted_api.py:34499` | `_export_graph_snapshot` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a)-[r]->(b) RETURN labels(a), coalesce(a.id, a.name), type(r)…` |
| `tortoise/hosted_api.py:35057` | `_stamp_import_prop` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.{} = $v` |
| `tortoise/hosted_api.py:35341` | `_apply_import_pack_config` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (p:PackInstall {namespace: $ns}) SET p.version = $version, p.s…` |
| `tortoise/hosted_api.py:35825` | `_soft_delete_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL SET k.revo…` |
| `tortoise/hosted_api.py:35830` | `_soft_delete_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, status:'active'}) SET m.status='re…` |
| `tortoise/hosted_api.py:35835` | `_soft_delete_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {org_id:$tid}) WHERE (i.status IS NULL OR i.stat…` |
| `tortoise/hosted_api.py:35841` | `_soft_delete_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.deleted_at=$now, t.grace_hours=$gh` |
| `tortoise/hosted_api.py:36418` | `_org_graph_names` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.namespace` |
| `tortoise/hosted_api.py:36585` | `_drop_org_acl_users` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.id` |
| `tortoise/hosted_api.py:36634` | `_purge_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid}) DETACH DELETE m` |
| `tortoise/hosted_api.py:36638` | `_purge_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id:$tid}) DETACH DELETE k` |
| `tortoise/hosted_api.py:36642` | `_purge_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {org_id:$tid}) DETACH DELETE i` |
| `tortoise/hosted_api.py:36646` | `_purge_registry_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) DETACH DELETE t` |
| `tortoise/hosted_api.py:36853` | `_purge_deleted_orgs_locked` | plain | read | candidate:SQL-shaped | `MATCH (t:Team) WHERE t.deleted_at IS NOT NULL AND t.deleted_at < $cu…` |
| `tortoise/hosted_api.py:37110` | `reconcile` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey) WHERE k.created_via = 'bootstrap' AND k.revoked_at …` |
| `tortoise/hosted_api.py:37116` | `reconcile` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now` |
| `tortoise/hosted_api.py:37499` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (t:Team {id:$id, name:$name, tier:'free', created_at:$now, ba…` |
| `tortoise/hosted_api.py:37508` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp,…` |
| `tortoise/hosted_api.py:37514` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (m:Membership {org_id:$tid, user_id:$uid, role:'owner', statu…` |
| `tortoise/hosted_api.py:37522` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (s:SignupToken {token_hash:$th, lookup_key:$lk, org_id:$tid, …` |
| `tortoise/hosted_api.py:37546` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) DETACH DELETE t` |
| `tortoise/hosted_api.py:37547` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {org_id:$id}) DETACH DELETE k` |
| `tortoise/hosted_api.py:37548` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$id}) DETACH DELETE m` |
| `tortoise/hosted_api.py:37549` | `agent_signup` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:SignupToken {org_id:$id}) DETACH DELETE s` |
| `tortoise/hosted_api.py:38973` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active'}) WHERE m.org_id …` |
| `tortoise/hosted_api.py:38987` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, org_id:$tid, status:'active'}) RE…` |
| `tortoise/hosted_api.py:38994` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.tier, t.suspended_at` |
| `tortoise/hosted_api.py:39035` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, created_via:'bootstrap', created_by:$u…` |
| `tortoise/hosted_api.py:39073` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.cre…` |
| `tortoise/hosted_api.py:39088` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.cre…` |
| `tortoise/hosted_api.py:39105` | `session_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) WHERE k.revoked_at IS NULL SET k.revoked_a…` |
| `tortoise/hosted_api.py:39124` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.cre…` |
| `tortoise/hosted_api.py:39149` | `session_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) WHERE k.revoked_at = $now SET k.revoked_at…` |
| `tortoise/hosted_api.py:39188` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND k.crea…` |
| `tortoise/hosted_api.py:39196` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, created_via:'recovery', created_by:$ui…` |
| `tortoise/hosted_api.py:39211` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, created_via:'bootstrap', created_by:$u…` |
| `tortoise/hosted_api.py:39224` | `session_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now` |
| `tortoise/hosted_api.py:39233` | `session_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.cre…` |
| `tortoise/hosted_api.py:39247` | `session_key` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp,…` |
| `tortoise/hosted_api.py:39972` | `_get_onboarding_state` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.onboarding_state` |
| `tortoise/hosted_api.py:40047` | `_graph_recording_override` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) WHERE coalesce(g.status, 'act…` |
| `tortoise/hosted_api.py:40062` | `_graph_recording_override` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid, kind:'default'}) WHERE coalesce(g.statu…` |
| `tortoise/hosted_api.py:40306` | `_read_onboarding_state_and_version` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.onboarding_state, coalesce(t.state…` |
| `tortoise/hosted_api.py:40357` | `_cas_write_onboarding_state` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) WHERE coalesce(t.state_version, 0) = $expec…` |
| `tortoise/hosted_api.py:40459` | `_write_onboarding_state` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.onboarding_state = $state` |
| `tortoise/hosted_api.py:42301` | `_org_name` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.name` |
| `tortoise/hosted_api.py:42318` | `_OrgSeedSurface.query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_api.py:42981` | `public_demo._seed_demo_sync` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id: '_demo_sentinel'}) RETURN p.id` |
| `tortoise/hosted_api.py:44889` | `_github_credentials` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.github_token_enc, t.github_org` |
| `tortoise/hosted_api.py:44919` | `_org_email` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.email` |
| `tortoise/hosted_api.py:44943` | `_write_org_email` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.email = $email` |
| `tortoise/hosted_api.py:45267` | `github_callback` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.github_token_enc = $tok, t.github_org…` |
| `tortoise/hosted_api.py:45413` | `_store_github_org` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.github_org = $org` |
| `tortoise/hosted_api.py:45439` | `_clear_github_credentials` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.github_token_enc = null, t.github_org…` |
| `tortoise/hosted_api.py:46665` | `_prior_stored_turn_count` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid}) OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point…` |
| `tortoise/hosted_api.py:46733` | `_relink_sessions_after_index` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session)-[:CONTAINS]->(t:Point) WHERE t.pointKind='event' R…` |
| `tortoise/hosted_api.py:46746` | `_relink_sessions_after_index` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.entity_links_attempted=$a, s.entit…` |
| `tortoise/hosted_api.py:49511` | `_billing_customer_email` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.email` |
| `tortoise/hosted_api.py:49550` | `_billing_customer_email` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id:$id}) RETURN k.created_by` |
| `tortoise/hosted_api.py:49555` | `_billing_customer_email` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) RETURN k.created_by LIMIT 1` |
| `tortoise/hosted_api.py:49612` | `_billing_checkout_sync` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.subscription_status, t.stripe_custo…` |
| `tortoise/hosted_api.py:49673` | `_billing_checkout_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.stripe_customer_id=$cid` |
| `tortoise/hosted_api.py:49678` | `_billing_checkout_sync` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.stripe_customer_id=$cid, t.customer_em…` |
| `tortoise/hosted_api.py:49817` | `_billing_checkout_new_org_sync` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {name:$name}) RETURN count(t)` |
| `tortoise/hosted_api.py:49875` | `_billing_portal_sync` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.stripe_customer_id` |
| `tortoise/hosted_api.py:50022` | `_org_id_for_stripe_customer` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {stripe_customer_id:$cid}) RETURN t.id` |
| `tortoise/hosted_api.py:50201` | `_webhook_apply_event._set` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t += $props` |
| `tortoise/hosted_api.py:50512` | `webhooks_stripe` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.tier` |
| `tortoise/hosted_api.py:50516` | `webhooks_stripe` | plain | read | candidate:SQL-shaped | `MATCH (w:WebhookEvent {event_id:$id}) RETURN w.first_seen` |
| `tortoise/hosted_api.py:50522` | `webhooks_stripe` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (w:WebhookEvent {event_id:$id, first_seen:$now, type:$type})` |
| `tortoise/hosted_backup.py:496` | `_paged_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_backup.py:515` | `_independent_counts` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE NOT any(l IN labels(n) WHERE l IN $skip_labels) AND …` |
| `tortoise/hosted_backup.py:522` | `_independent_counts` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:546` | `_graph_has_any_node` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN id(n) LIMIT 1` |
| `tortoise/hosted_backup.py:673` | `dump_graph` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE id(n) IN $ids RETURN id(n), labels(n), properties(n)` |
| `tortoise/hosted_backup.py:843` | `_read_event_meta` | plain | read | candidate:SQL-shaped | `MATCH (m:GraphEventMeta) RETURN max(m.last_seq), max(m.first_seq)` |
| `tortoise/hosted_backup.py:891` | `_restore_event_meta` | plain | read | candidate:SQL-shaped | `MATCH (e:GraphEvent) RETURN max(e.seq), min(e.seq)` |
| `tortoise/hosted_backup.py:915` | `_restore_event_meta` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:GraphEventMeta) SET m.last_seq = $last, m.first_seq = $firs…` |
| `tortoise/hosted_backup.py:932` | `_first_live_node_id` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE id(n) = $id RETURN count(n)` |
| `tortoise/hosted_backup.py:1333` | `restore_graph` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN labels(n), properties(n)` |
| `tortoise/hosted_backup.py:1344` | `restore_graph` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:1359` | `restore_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (n:{}) SET n = $p` |
| `tortoise/hosted_backup.py:1361` | `restore_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (n) SET n = $p` |
| `tortoise/hosted_backup.py:1401` | `restore_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `UNWIND $rows AS r MATCH (n:{} {__dump_id:r.id}) ???SET ???` |
| `tortoise/hosted_backup.py:1412` | `restore_graph` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE n.__dump_id IS NOT NULL RETURN n.__dump_id` |
| `tortoise/hosted_backup.py:1468` | `restore_graph` | adjacency | write | stays:graph-maintenance | `MATCH (a {__dump_id:$s}), (b {__dump_id:$d}) CREATE (a)-[r:{}]->(b) …` |
| `tortoise/hosted_backup.py:1474` | `restore_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n) WHERE n.__dump_id IS NOT NULL REMOVE n.__dump_id` |
| `tortoise/hosted_backup.py:1476` | `restore_graph` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:1534` | `restore_graph` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN labels(n), properties(n)` |
| `tortoise/hosted_backup.py:2304` | `_stamp_backup_latest` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.backup_latest_at = $ts` |
| `tortoise/hosted_backup.py:2323` | `_stamp_backup_restored` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.backup_restored_at = $ts` |
| `tortoise/hosted_backup.py:2703` | `_audit_copied_boolean_indexes._index_has_is_operator` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/hosted_backup.py:2713` | `_audit_copied_boolean_indexes._probe` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_operator = false RETURN count(n)` |
| `tortoise/hosted_backup.py:2716` | `_audit_copied_boolean_indexes._probe` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE NOT n.is_operator RETURN count(n)` |
| `tortoise/hosted_backup.py:2733` | `_audit_copied_boolean_indexes._drop_boolean_indexes` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/hosted_backup.py:2949` | `_promote_payload_fork_free` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/hosted_backup.py:3262` | `_restore_copy_settled` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/hosted_backup.py:3264` | `_restore_copy_settled` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/hosted_backup.py:3268` | `_restore_copy_settled` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:3270` | `_restore_copy_settled` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/hosted_backup.py:3463` | `count_data_nodes` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE NOT any(l IN labels(n) WHERE l IN $skip_labels) AND …` |
| `tortoise/hosted_backup.py:3602` | `_restore_into_temp_verify_swap` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN labels(n), properties(n)` |
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
| `tortoise/mcp_auth.py:1717` | `OrgResolutionMiddleware.dispatch` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id: $id}) SET k.last_used_at = $now` |
| `tortoise/mcp_auth.py:1736` | `OrgResolutionMiddleware.dispatch` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.suspended_at` |
| `tortoise/mcp_server.py:11667` | `_preview_delete_edges` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a)-[r]-(b) WHERE ID(a) IN $nids OR ID(b) IN $nids RETURN ID(r…` |
| `tortoise/mcp_server.py:11695` | `_preview_orphan_tags` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t:Tag) OPTIONAL MATCH (t)<-[:TAGGED]-(q) WITH t, [x IN collec…` |
| `tortoise/mcp_server.py:11708` | `_preview_delete_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN ID(n)` |
| `tortoise/mcp_server.py:11714` | `_preview_delete_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[:TAGGED]->() RETURN count(*) > 0` |
| `tortoise/mcp_server.py:11769` | `_preview_delete_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {{}:$id}) RETURN ID(n)` |
| `tortoise/mcp_server.py:11855` | `_preview_retract_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) WHERE ??? RETURN count(n)` |
| `tortoise/mcp_server.py:11913` | `_preview_invalidate` | plain | read | candidate:SQL-shaped | `MATCH (b:Point {id:$o}) RETURN count(b)` |
| `tortoise/mcp_server.py:11915` | `_preview_invalidate` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point {id:$n}), (b:Point {id:$o}) WHERE NOT (a)-[:CORRECTS]…` |
| `tortoise/mcp_server.py:12103` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r]->(new:Point {id:$n}) RETURN …` |
| `tortoise/mcp_server.py:12107` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (new:Point {id:$n})-[r:INPUT]->(op:Point {is_operator:true}) R…` |
| `tortoise/mcp_server.py:12121` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r]->(o:Point {id:$old}) RETURN …` |
| `tortoise/mcp_server.py:12142` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$old})-[r:INPUT]->(op:Point {is_operator:true}) R…` |
| `tortoise/mcp_server.py:12171` | `_preview_supersede` | plain | read | candidate:SQL-shaped | `MATCH (o:Point {id:$old}), (n:Point {id:$new}) RETURN o.validFrom, n…` |
| `tortoise/mcp_server.py:12188` | `_preview_supersede` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN ID(n)` |
| `tortoise/mcp_server.py:12202` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (new:Point {id:$n})-[r]->(t) RETURN type(r), ID(t)` |
| `tortoise/mcp_server.py:12205` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t)-[r]->(new:Point {id:$n}) RETURN type(r), ID(t)` |
| `tortoise/mcp_server.py:12227` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t)-[r:IMPL\|NAND]->(o:Point {id:$old}) RETURN type(r), t.id, …` |
| `tortoise/mcp_server.py:12229` | `_preview_supersede` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids AND coalesce(p.is_operator,false)…` |
| `tortoise/mcp_server.py:12270` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$old})-[r:{}]->(t) RETURN type(r), coalesce(t.id,…` |
| `tortoise/mcp_server.py:12309` | `_preview_supersede` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$old}) RETURN count(n)` |
| `tortoise/mcp_server.py:12314` | `_preview_supersede` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$old})-[r]-() RETURN count(r)` |
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
| `tortoise/mining.py:98` | `_batch_node` | plain | read | candidate:SQL-shaped | `MATCH (b:Batch {id:$id}) RETURN properties(b)` |
| `tortoise/mining.py:154` | `_upsert_batch` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (b:Batch {id:$id}) SET ???` |
| `tortoise/mining.py:182` | `list_quarantined` | plain | read | candidate:SQL-shaped | `MATCH (b:Batch {status:$st}) RETURN properties(b) ORDER BY b.updated…` |
| `tortoise/mining.py:336` | `ConversationMiner.mine` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids SET n.batch_id = $bid` |
| `tortoise/mining.py:555` | `ConversationMiner._temporal_wire` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.content, n.pointKind, n.status` |
| `tortoise/mining.py:575` | `ConversationMiner._temporal_wire` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.validFrom = $vf` |
| `tortoise/mining.py:584` | `ConversationMiner._temporal_wire` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$pid})-[:aboutObject]->(o:Object)<-[:aboutObject]…` |
| `tortoise/mining.py:638` | `ConversationMiner._temporal_wire` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.temporal_target_ids` |
| `tortoise/mining.py:645` | `ConversationMiner._temporal_wire` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.temporal_candidate = true, n.temporal…` |
| `tortoise/mining.py:754` | `ConversationMiner._reify_entities` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids SET n.eventId=$eid` |
| `tortoise/mining.py:1289` | `mine_corpus_with_sdk` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.file_hash, e.mined_hash` |
| `tortoise/mining.py:1379` | `mine_corpus_with_sdk` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId:$eid}) SET e.mined_hash = $h, e.mined_at = $…` |
| `tortoise/mining.py:1451` | `EpSafeCommit.verify_draft_only` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.status` |
| `tortoise/mining.py:1482` | `EpSafeCommit.verify_no_auto_wire` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true})-[r]->(n:Point) WHERE n.id IN $ids…` |
| `tortoise/mining.py:1490` | `EpSafeCommit.verify_no_auto_wire` | plain | read | candidate:SQL-shaped | `MATCH (o:Point {id:$id}) RETURN o.status` |
| `tortoise/mining.py:1508` | `EpSafeCommit.verify_no_auto_wire` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$oid})-[r]->(s:Point) RETURN s.id, s.status` |
| `tortoise/monitoring.py:5336` | `_probe_once._run_query` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/navigation.py:95` | `_resolve_root` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/navigation.py:205` | `entityProfile` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/navigation.py:319` | `tortoise_traverse` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/near_dup_merge.py:426` | `apply_merge_union` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/near_dup_merge.py:507` | `apply_merge_union` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE ({}) AND o.name = $exact RETURN o.id` |
| `tortoise/near_dup_merge.py:569` | `apply_merge_union` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}), (o:Object) WHERE (({}) AND o.name = $exac…` |
| `tortoise/near_dup_merge.py:602` | `apply_merge_union` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE {} RETURN o.id LIMIT 1` |
| `tortoise/oauth.py:1352` | `_rollback_minted` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/oauth.py:1497` | `_live_family_for_code` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/oauth.py:3112` | `sweep_oauth_retention` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/oauth.py:3116` | `sweep_oauth_retention` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/seed.py:172` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/seed.py:174` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/seed.py:176` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/selfhost.py:130` | `_SdkSeedSurface.query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/state.py:695` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/state.py:698` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/onboarding/state.py:700` | `_run` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/outside_view.py:239` | `_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/outside_view.py:284` | `resident_keys` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN n.{} AS key LIMIT {}` |
| `tortoise/outside_view.py:298` | `resident_count` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN count(n)` |
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
| `tortoise/planned_occurrence.py:498` | `create_plan` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$o}) RETURN o.objectKind` |
| `tortoise/planned_occurrence.py:559` | `create_plan` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$o}) RETURN properties(o)` |
| `tortoise/planned_occurrence.py:1022` | `_resolve_plan` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$o}) RETURN o.objectKind, o.status, coalesce(o.o…` |
| `tortoise/planned_occurrence.py:1116` | `planned_without_occurrence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Object) WHERE p.objectKind IN $kinds OPTIONAL MATCH (e:Even…` |
| `tortoise/postgres_bytes.py:344` | `_meter_statement` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:2003` | `read_config_reset` | plain | read | candidate:SQL-shaped | `MATCH (n:Meta {key:$key}) RETURN properties(n)` |
| `tortoise/projection/__init__.py:2023` | `set_config_reset_marker` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (n:Meta {key:$key}) SET n += $props` |
| `tortoise/projection/__init__.py:2041` | `clear_config_reset` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Meta {key:$key}) DETACH DELETE n` |
| `tortoise/projection/__init__.py:2064` | `_capture_config_snapshot` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:2137` | `_capture_onboarding_snapshot` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN properties(n)` |
| `tortoise/projection/__init__.py:2170` | `_capture_onboarding_snapshot` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:{})-[:{}]->(s:{}) RETURN n.org_id, s.step_id` |
| `tortoise/projection/__init__.py:2288` | `_capture_team_meta` | plain | read | candidate:SQL-shaped | `MATCH (m:TeamMeta) RETURN properties(m)` |
| `tortoise/projection/__init__.py:2424` | `_capture_pack_selection` | plain | read | candidate:SQL-shaped | `MATCH (s:PackSelection) RETURN properties(s)` |
| `tortoise/projection/__init__.py:12132` | `FalkorProjection._probe_ok` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN 1 LIMIT 1` |
| `tortoise/projection/__init__.py:13315` | `FalkorProjection._apply_event` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'live'` |
| `tortoise/projection/__init__.py:13759` | `FalkorProjection._episodic_point_ids` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_episodic = true RETURN n.id` |
| `tortoise/projection/__init__.py:14206` | `FalkorProjection._replay_log` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:TeamMeta) ON CREATE SET m += $props` |
| `tortoise/projection/__init__.py:14242` | `FalkorProjection._replay_log` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:PackSelection) ON CREATE SET s += $props` |
| `tortoise/projection/__init__.py:14278` | `FalkorProjection._replay_log` | plain | read | candidate:SQL-shaped | `MATCH (s:PackSelection) RETURN properties(s)` |
| `tortoise/projection/__init__.py:14464` | `FalkorProjection._restore_write` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:14628` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r:IMPL\|NAND]->() WHERE r.msg_alpha IS NOT NULL OR r.msg_b…` |
| `tortoise/projection/__init__.py:14646` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) RETURN properties(n)` |
| `tortoise/projection/__init__.py:14724` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[r:{}]->(m:Point) RETURN m.id ORDER BY r.id…` |
| `tortoise/projection/__init__.py:14772` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (b:Batch) RETURN properties(b)` |
| `tortoise/projection/__init__.py:14783` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.batch_id IS NOT NULL RETURN p.id, p.batch_id` |
| `tortoise/projection/__init__.py:14961` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) RETURN properties(s)` |
| `tortoise/projection/__init__.py:14965` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session)-[:CONTAINS]->(p:Point) RETURN s.id, p.id` |
| `tortoise/projection/__init__.py:15374` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n)` |
| `tortoise/projection/__init__.py:15970` | `FalkorProjection._rebuild_all_replay` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.embedding = NULL, n.content_hash = NU…` |
| `tortoise/projection/__init__.py:16381` | `FalkorProjection._rebuild_all_replay` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'live'` |
| `tortoise/projection/__init__.py:17090` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$i}) RETURN o.name` |
| `tortoise/projection/__init__.py:17312` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id:$i}) RETURN p.id LIMIT 1` |
| `tortoise/projection/__init__.py:17333` | `FalkorProjection._rebuild_all_replay` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'live'` |
| `tortoise/projection/__init__.py:17752` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:{})-[:{}]->(s:Subject) RETURN n.org_id, s.id` |
| `tortoise/projection/__init__.py:17929` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (m:TeamMeta) RETURN properties(m)` |
| `tortoise/projection/__init__.py:18087` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (s:PackSelection) RETURN properties(s)` |
| `tortoise/projection/__init__.py:18584` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (p:{}) WHERE p.id IN $ids RETURN p.id` |
| `tortoise/projection/__init__.py:18599` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a}), (b:{} {id:$b}) MERGE (a)-[:{}]->(b)` |
| `tortoise/projection/__init__.py:18612` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (t:{}) WHERE t.id = $v RETURN ID(t) LIMIT 1` |
| `tortoise/projection/__init__.py:18622` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a})-[r:{}]->(b:{} {id:$b}) SET r += $attrs` |
| `tortoise/projection/__init__.py:18660` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point)-[r]->(old:Point {id:$id}) WHERE (op.is_operator = t…` |
| `tortoise/projection/__init__.py:18685` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (old:Point {id:$id})-[ri:INPUT]->(op:Point) WHERE (op.is_opera…` |
| `tortoise/projection/__init__.py:18723` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id}), (new:Point {id:$nid}) MERGE (op)-[:{}]…` |
| `tortoise/projection/__init__.py:18729` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id}), (new:Point {id:$nid}) MERGE (op)-[:{} …` |
| `tortoise/projection/__init__.py:18734` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id})-[r:{}]->(old:Point {id:$oid}) WHERE ID(…` |
| `tortoise/projection/__init__.py:18750` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$nid}), (op:Point {id:$op_id}) MERGE (new)-[:IN…` |
| `tortoise/projection/__init__.py:18756` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$nid}), (op:Point {id:$op_id}) MERGE (new)-[:IN…` |
| `tortoise/projection/__init__.py:18761` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$oid})-[ri:INPUT]->(op:Point {id:$op_id}) WHERE…` |
| `tortoise/projection/__init__.py:18789` | `FalkorProjection._rebuild_all_replay._src_is_terminal` | plain | read | candidate:SQL-shaped | `MATCH (x:Point {id:$sid}) WHERE {} RETURN 1 LIMIT 1` |
| `tortoise/projection/__init__.py:18870` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (t:Point {id:$id}) RETURN ID(t) LIMIT 1` |
| `tortoise/projection/__init__.py:18889` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old})-[r:{}]->(t) WHERE ID(t) = $tid DELETE r` |
| `tortoise/projection/__init__.py:19003` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old})-[r:{}]->(t) WHERE ID(t) = $tid DELETE r` |
| `tortoise/projection/__init__.py:19098` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (s:Point {id:$sid}), (t) WHERE ID(t) = $tid MERGE (s)-[nr:{}]-…` |
| `tortoise/projection/__init__.py:19132` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (p:{}) WHERE p.id IN $ids RETURN p.id` |
| `tortoise/projection/__init__.py:19149` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a}), (b:{} {id:$b}) MERGE (a)-[:{}]->(b)` |
| `tortoise/projection/__init__.py:19158` | `FalkorProjection._rebuild_all_replay` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a})-[r:{}]->(b:{} {id:$b}) SET r += $attrs` |
| `tortoise/projection/__init__.py:19261` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r:IMPL\|NAND]->() WHERE r.msg_alpha IS NOT NULL OR r.msg_b…` |
| `tortoise/projection/__init__.py:19572` | `FalkorProjection._rebuild_all_replay` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) RETURN count(n)` |
| `tortoise/projection/__init__.py:19575` | `FalkorProjection._rebuild_all_replay` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r]->() RETURN count(r)` |
| `tortoise/projection/__init__.py:20056` | `FalkorProjection._count_projected` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:20106` | `FalkorProjection.query` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:20176` | `FalkorProjection._wipe_all_nodes` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n) DETACH DELETE n` |
| `tortoise/projection/__init__.py:20448` | `FalkorProjection._label_vector_count` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.embedding IS NOT NULL RETURN count(n)` |
| `tortoise/projection/__init__.py:20601` | `FalkorProjection._record_vector_index_inventory` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:20950` | `FalkorProjection._resolve_entity` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21153` | `FalkorProjection._schema_is_current` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:21306` | `FalkorProjection._point_fts_field_missing` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:21346` | `FalkorProjection._event_fts_index_is_current` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:21383` | `FalkorProjection._array_valued_fts_prop_exists` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21399` | `FalkorProjection._array_fts_prop_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21439` | `FalkorProjection._fix_point_array_fts_props` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21457` | `FalkorProjection._fix_point_array_fts_props` | unresolved | unresolved | unclassified:statement not resolvable statically | `CALL {}('Point')` |
| `tortoise/projection/__init__.py:21474` | `FalkorProjection._fix_point_array_fts_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:Meta {key:'point_fts_v2'}) SET m.v = true` |
| `tortoise/projection/__init__.py:21522` | `FalkorProjection._create_fulltext_index` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21755` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:Point) ON (n.{})` |
| `tortoise/projection/__init__.py:21793` | `FalkorProjection._ensure_indexes_declared` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:21813` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:Point) ON (n.lastDreamedAt)` |
| `tortoise/projection/__init__.py:21836` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:21880` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:{}) ON (n.{})` |
| `tortoise/projection/__init__.py:21895` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (s:Session) ON (s.actor_user_id)` |
| `tortoise/projection/__init__.py:21914` | `FalkorProjection._ensure_indexes_declared` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (s:Session) ON (s.id)` |
| `tortoise/projection/__init__.py:21956` | `FalkorProjection._ensure_indexes_declared` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:Meta {key:'point_fts_v2'}) SET m.v = true` |
| `tortoise/projection/__init__.py:22013` | `FalkorProjection._ensure_indexes_declared` | plain | read | candidate:SQL-shaped | `MATCH (m:Meta {key:'event_fts_v2'}) RETURN 1` |
| `tortoise/projection/__init__.py:22047` | `FalkorProjection._ensure_indexes_declared` | unresolved | unresolved | unclassified:statement not resolvable statically | `CALL {}('Event')` |
| `tortoise/projection/__init__.py:22058` | `FalkorProjection._ensure_indexes_declared` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (m:Meta {key:'event_fts_v2'}) SET m.v = true` |
| `tortoise/projection/__init__.py:22264` | `FalkorProjection._ensure_vector_index_api` | ddl | ddl | stays:graph-maintenance | `CALL db.indexes()` |
| `tortoise/projection/__init__.py:22357` | `FalkorProjection._ensure_vector_index_api` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/__init__.py:22682` | `FalkorProjection._create_vector_index_api` | ddl | ddl | stays:graph-maintenance | `CALL db.idx.vector.createNodeIndex('Point', 'embedding', {}, 'HNSW')` |
| `tortoise/projection/__init__.py:22694` | `FalkorProjection._create_vector_index_api` | ddl | ddl | stays:graph-maintenance | `CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) OPTIONS {dimensio…` |
| `tortoise/projection/__init__.py:22792` | `FalkorProjection.backfill_document_search_text` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source) WHERE s.documentKind IS NOT NULL AND s._searchText …` |
| `tortoise/projection/__init__.py:22895` | `FalkorProjection._apply_annotator` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET {} RETURN n.id` |
| `tortoise/projection/__init__.py:23049` | `FalkorProjection._revise_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.content, n.search_keys` |
| `tortoise/projection/__init__.py:23367` | `FalkorProjection._revise_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET {}` |
| `tortoise/projection/__init__.py:23433` | `FalkorProjection._delete_entity_by_id` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) DETACH DELETE n RETURN count(n)` |
| `tortoise/projection/__init__.py:23610` | `FalkorProjection._fold_entity_mutation` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {{}:$id}) RETURN count(n)` |
| `tortoise/projection/__init__.py:23628` | `FalkorProjection._fold_entity_mutation` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) SET n += $s RETURN count(n)` |
| `tortoise/projection/__init__.py:23731` | `FalkorProjection.extract_svbp_factors` | plain | read | candidate:SQL-shaped | `MATCH (o:Point) WHERE o.is_operator = true AND {} AND {} RETURN o.id…` |
| `tortoise/projection/__init__.py:23754` | `FalkorProjection.extract_svbp_factors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point)-[r:IMPL\|NAND]->(c) WHERE o.is_operator = true AND {…` |
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
| `tortoise/projection/edges.py:3203` | `_EdgeHandlers.create_edge` | traversal | read | stays:traversal (§3) | `MATCH (t:{} {{}:$tid}) MATCH (s:{} {{}:$sid}) MATCH path = (t)-[:own…` |
| `tortoise/projection/edges.py:3221` | `_EdgeHandlers.create_edge` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {{}:$sv}) MATCH (t:{} {{}:$tv}) MERGE (s)-[:{}]->(t) RET…` |
| `tortoise/projection/edges.py:3243` | `_EdgeHandlers.create_owned_by` | traversal | read | stays:traversal (§3) | `MATCH (s:{} {{}:$sid}) MATCH (t:{} {{}:$tid}) MATCH path = (s)-[:own…` |
| `tortoise/projection/entities.py:3238` | `_EntityHandlers._upsert_point_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (n:Point {id:$id}) ???SET ???` |
| `tortoise/projection/entities.py:3269` | `_EntityHandlers._upsert_point_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET ???` |
| `tortoise/projection/entities.py:3273` | `_EntityHandlers._upsert_point_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.provenanceSource=$sid` |
| `tortoise/projection/entities.py:3491` | `_EntityHandlers._upsert_point_edges` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.tags` |
| `tortoise/projection/entities.py:3635` | `_EntityHandlers._fold_entity_linked_reason` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {id:$sid}), (t:{} {id:$tid}) MERGE (s)-[:{}]->(t) RETURN…` |
| `tortoise/projection/entities.py:3641` | `_EntityHandlers._fold_entity_linked_reason` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {id:$sid}), (t:{} {id:$tid}) MERGE (s)-[e:{}]->(t) SET e…` |
| `tortoise/projection/entities.py:3904` | `_EntityHandlers._fold_session_recorded` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET {} RETURN count(s)` |
| `tortoise/projection/entities.py:3981` | `_EntityHandlers._link_session` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET s.is_episodic=true` |
| `tortoise/projection/entities.py:3985` | `_EntityHandlers._link_session` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/projection/entities.py:4042` | `_EntityHandlers._fold_session_deleted` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (src:Source {url:$url}) DETACH DELETE src` |
| `tortoise/projection/entities.py:4048` | `_EntityHandlers._fold_session_deleted` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) DETACH DELETE s RETURN count(s)` |
| `tortoise/projection/entities.py:4071` | `_EntityHandlers._point_status` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/projection/entities.py:4077` | `_EntityHandlers._delete` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) DETACH DELETE n` |
| `tortoise/projection/entities.py:4139` | `_EntityHandlers._retract` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'retracted'{}{}, {} RETURN c…` |
| `tortoise/projection/entities.py:4412` | `_EntityHandlers._merge_corrects_edge` | adjacency | write | stays:graph-maintenance | `MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) MERGE (a)-[:COR…` |
| `tortoise/projection/entities.py:4549` | `_EntityHandlers._fold_point_superseded` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status='superseded', n.outdated=true,…` |
| `tortoise/projection/entities.py:4579` | `_EntityHandlers._decay_point_belief` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET {} RETURN n.id LIMIT 1` |
| `tortoise/projection/entities.py:4664` | `_EntityHandlers._fold_point_invalidated` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.outdated=true, n.validTo=$vt, n.valid…` |
| `tortoise/projection/entities.py:4777` | `_EntityHandlers._fold_confidence_changed` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/entities.py:4860` | `_EntityHandlers._fold_edge_message_changed` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/projection/entities.py:4868` | `_EntityHandlers._fold_edge_message_changed` | adjacency | write | stays:graph-maintenance | `MATCH ()-[r:IMPL\|NAND]->() REMOVE r.msg_alpha, r.msg_beta, r.back_m…` |
| `tortoise/projection/entities.py:4910` | `_EntityHandlers._fold_edge_message_changed` | adjacency | write | stays:graph-maintenance | `MATCH (p)-[r:IMPL\|NAND]-() WHERE {} AND p.id IN $ids REMOVE r.msg_a…` |
| `tortoise/projection/entities.py:4919` | `_EntityHandlers._fold_edge_message_changed` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point)-[conn:IMPL\|NAND]->(p) WHERE {} AND p.id IN $ids AN…` |
| `tortoise/projection/entities.py:5054` | `_EntityHandlers._upsert_subject` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Subject {id:$key}) ON CREATE SET s.id=$id, s.name=$name, s.…` |
| `tortoise/projection/entities.py:5236` | `_EntityHandlers._upsert_object` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (o:Object {id:$key}) ON CREATE SET o.id=$id, o.name=$name, o.o…` |
| `tortoise/projection/entities.py:5429` | `_EntityHandlers._fold_object_superseded` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Object {id:$id}) SET o.status='superseded', o.supersededBy=…` |
| `tortoise/projection/entities.py:5685` | `_EntityHandlers._upsert_document` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$id}) ???SET s.id=coalesce(s.id, $id), s.canoni…` |
| `tortoise/projection/entities.py:6219` | `_EntityHandlers._fold_object_status` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$n}) WHERE toLower(toStringOrNull(o.objectKind))…` |
| `tortoise/projection/entities.py:6228` | `_EntityHandlers._fold_object_status` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Object {id:$n}) WHERE toLower(toStringOrNull(o.objectKind))…` |
| `tortoise/projection/entities.py:6246` | `_EntityHandlers._fold_object_status` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$n}) WHERE toLower(toStringOrNull(o.objectKind))…` |
| `tortoise/projection/entities.py:6449` | `_EntityHandlers._fold_object_stage` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$n}) RETURN o.pipelineStageId, o.pipelineStageKe…` |
| `tortoise/projection/entities.py:6481` | `_EntityHandlers._fold_object_stage` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Object {id:$n}) SET o += $props` |
| `tortoise/projection/entities.py:6560` | `_EntityHandlers._event_plain_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props, ??? ON MA…` |
| `tortoise/projection/entities.py:6598` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (s:Subject {id:$id}), (e:Event {eventId:$eid}) MERGE (s)-[:per…` |
| `tortoise/projection/entities.py:6678` | `_EntityHandlers._event_plain_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$id}) ON CREATE SET s.id=$id, s.title=$id, s.so…` |
| `tortoise/projection/entities.py:6689` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (s:Source {url:$id}), (e:Event {eventId:$eid}) MERGE (e)-[:pro…` |
| `tortoise/projection/entities.py:6705` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (o:Object {id:$id}), (e:Event {eventId:$eid}) MERGE (e)-[:prod…` |
| `tortoise/projection/entities.py:6819` | `_EntityHandlers._event_plain_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Object {id:$id}) SET o.objectKind=coalesce($kind, o.objectK…` |
| `tortoise/projection/entities.py:6824` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (o:Object {id:$id}), (e:Event {eventId:$eid}) MERGE (e)-[:uses…` |
| `tortoise/projection/entities.py:6837` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (s:Subject {id: $sid}), (e:Event {eventId: $eid}) MERGE (s)-[:…` |
| `tortoise/projection/entities.py:6855` | `_EntityHandlers._event_plain_merge` | adjacency | write | stays:graph-maintenance | `MATCH (s:Subject {id: $sid}), (e:Event {eventId: $eid}) MERGE (s)-[:…` |
| `tortoise/projection/entities.py:7009` | `_EntityHandlers._materialize_connector_source` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source)-[:references]->(e:Event {eventId: $eid}) RETURN s.u…` |
| `tortoise/projection/entities.py:7016` | `_EntityHandlers._materialize_connector_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url: $url}) ON CREATE SET s.sourceKind = $sk, s.tit…` |
| `tortoise/projection/entities.py:7074` | `_EntityHandlers._materialize_connector_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url: $url}) WHERE s.sourceKind IN $ck AND (s.conten…` |
| `tortoise/projection/entities.py:7122` | `_EntityHandlers._materialize_connector_source` | adjacency | write | stays:graph-maintenance | `MATCH (old:Source)-[r:references]->(e:Event {eventId: $eid}) WHERE o…` |
| `tortoise/projection/entities.py:7144` | `_EntityHandlers._materialize_connector_source` | adjacency | write | stays:graph-maintenance | `MATCH (s:Source {url: $url}), (o:Object {id: $oid}) MERGE (s)-[:refe…` |
| `tortoise/projection/entities.py:7184` | `_EntityHandlers._event_guarded_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props` |
| `tortoise/projection/entities.py:7189` | `_EntityHandlers._event_guarded_merge` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId: $eid}) WHERE e.eventKind = 'meeting' AND e.…` |
| `tortoise/projection/entities.py:7196` | `_EntityHandlers._event_guarded_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId: $eid}) SET e += $props` |
| `tortoise/projection/entities.py:7206` | `_EntityHandlers._event_guarded_merge` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId: $eid}) RETURN e.source_file` |
| `tortoise/projection/entities.py:7212` | `_EntityHandlers._event_guarded_merge` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId: $eid}) WHERE e.eventKind = 'meeting' AND e.…` |
| `tortoise/projection/entities.py:7219` | `_EntityHandlers._event_guarded_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId: $eid}) SET e += $props` |
| `tortoise/projection/entities.py:7224` | `_EntityHandlers._event_guarded_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props` |
| `tortoise/projection/entities.py:7336` | `_EntityHandlers._upsert_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `OPTIONAL MATCH (_prev:Source {url: $url}) WITH _prev, properties(_pr…` |
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
| `tortoise/record_allocator.py:660` | `execute_allocation` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/record_backup.py:512` | `require_bypassing_role` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/record_projection.py:8191` | `_export_statement` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/record_projection.py:8470` | `replay` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/record_reconcile.py:581` | `scan_cache_view` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN properties(n)` |
| `tortoise/record_restore.py:478` | `_target_nodes_before` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN count(…` |
| `tortoise/record_vector.py:2899` | `vector_search` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/rerank.py:555` | `_fetch_embeddings` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND n.embedding IS NOT NULL RETUR…` |
| `tortoise/residency.py:243` | `read_declaration` | plain | read | candidate:SQL-shaped | `MATCH (n:Meta {key:$key}) RETURN n.coverage, n.hot_keys` |
| `tortoise/residency.py:283` | `declare_residency` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (n:Meta {key:$key}) SET n += $props` |
| `tortoise/residency.py:291` | `clear_residency` | plain | read | candidate:SQL-shaped | `MATCH (n:Meta {key:$key}) RETURN 1` |
| `tortoise/residency.py:297` | `clear_residency` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Meta {key:$key}) DETACH DELETE n` |
| `tortoise/resident_pin.py:507` | `_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/resident_pin.py:668` | `_write` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (n:Meta {key:$key}) SET n += $props` |
| `tortoise/resident_pin.py:997` | `unpin` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Meta {key:$key}) DETACH DELETE n` |
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
| `tortoise/sdk.py:3162` | `_capture_turn_ids` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) WHERE t.is_episod…` |
| `tortoise/sdk.py:3504` | `_write_capture_turns` | adjacency | write | stays:graph-maintenance | `UNWIND $turns AS turn MERGE (t:Point {id: turn.id}) WITH t, turn, t.…` |
| `tortoise/sdk.py:3768` | `_write_capture_turns` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) WHERE t.id IN $id…` |
| `tortoise/sdk.py:4079` | `_write_capture_turns` | adjacency | write | stays:graph-maintenance | `UNWIND $turns AS turn MERGE (t:Point {id: turn.id}) WITH t, turn, t.…` |
| `tortoise/sdk.py:4096` | `_write_capture_turns` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_redactions=$redactions` |
| `tortoise/sdk.py:4429` | `_write_session_and_turns` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid}) OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point…` |
| `tortoise/sdk.py:4453` | `_write_session_and_turns` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Session {id:$sid}) SET {}` |
| `tortoise/sdk.py:5141` | `_fetch_rerank_pool_texts` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.{} IN $ids RETURN n.{}, {}` |
| `tortoise/sdk.py:7161` | `_stored_goal_ending_view` | adjacency | read | measure:across shapes (#13083 owns placement) | `??? OPTIONAL MATCH (s)-[:CORRECTS]->(n) RETURN n.status, n.goalState…` |
| `tortoise/sdk.py:8224` | `_raise_update_point_status_error` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:8239` | `_raise_update_point_status_error` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:11647` | `_stamp_capture_provenance` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:11695` | `_capture_stamp_targets` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:11736` | `_capture_ep_target_ids` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids AND n.posterior_alpha IS NULL AND…` |
| `tortoise/sdk.py:11827` | `_apply_capture_ingest_ep` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND (n.is_operator IS NULL OR n.i…` |
| `tortoise/sdk.py:11836` | `_apply_capture_ingest_ep` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids SET n.status = 'live'` |
| `tortoise/sdk.py:11870` | `_apply_capture_ingest_ep` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:11878` | `_apply_capture_ingest_ep` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Point {id:$oid}) SET o.status = 'live'` |
| `tortoise/sdk.py:12430` | `_holds_role_available` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (:Subject)-[r:holdsRole]->(:Subject) RETURN count(r)` |
| `tortoise/sdk.py:12985` | `_refuse_inverted_point_window` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom, n.validTo` |
| `tortoise/sdk.py:13161` | `_money_views_for_update` | plain | read | candidate:SQL-shaped | `MATCH (n{} {}:$id}}) RETURN n.amountMinor, n.currency, n.minorUnitEx…` |
| `tortoise/sdk.py:13220` | `_stored_value_shapes` | plain | read | candidate:SQL-shaped | `MATCH (n{} {}:$id}}) RETURN n.amountMinor, n.currency, n.minorUnitEx…` |
| `tortoise/sdk.py:13274` | `_refuse_inconsistent_validity_precision` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom, n.validTo, n.validFromP…` |
| `tortoise/sdk.py:13899` | `_mechanism_conflicts_for_pairs` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:14035` | `_live_operator_mechanism_pairs` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:15754` | `TortoiseSDK._ensure_registry_indexes` | ddl | ddl | stays:graph-maintenance | `CREATE INDEX FOR (n:{}) ON (n.{})` |
| `tortoise/sdk.py:15926` | `TortoiseSDK.poll_events` | plain | read | candidate:SQL-shaped | `MATCH (m:GraphEventMeta) RETURN m.first_seq` |
| `tortoise/sdk.py:17178` | `TortoiseSDK._event_raw_write_door` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:18088` | `TortoiseSDK._create_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$id})-[:extractedFrom]->(s:Source) RETURN DISTINC…` |
| `tortoise/sdk.py:18915` | `TortoiseSDK._create_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (n:Point {???})` |
| `tortoise/sdk.py:19129` | `TortoiseSDK._resolve_id` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id: $id}) RETURN n.id, n.content, n.pointKind, n.sta…` |
| `tortoise/sdk.py:19141` | `TortoiseSDK._resolve_id` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.content CONTAINS $id_str RETURN n.id, n.cont…` |
| `tortoise/sdk.py:19380` | `TortoiseSDK._stamp_entity_link_counters` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.entity_links_attempted=$a, s.entit…` |
| `tortoise/sdk.py:19420` | `TortoiseSDK._stamp_capture_attempt_state` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Session {id:$sid}) SET s.capture_ok=$ok, s.capture_extracto…` |
| `tortoise/sdk.py:19686` | `TortoiseSDK._capture_session` | plain | read | candidate:SQL-shaped | `OPTIONAL MATCH (s:Session {id:$sid}) RETURN count(s) AS n, s.capture…` |
| `tortoise/sdk.py:20553` | `TortoiseSDK._capture_session` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, coalesce(n.pointKind…` |
| `tortoise/sdk.py:20793` | `TortoiseSDK._extract_session_llm` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[:INPUT\|IMPL\|NAND]-(o:Point) WHERE o.is_o…` |
| `tortoise/sdk.py:20844` | `TortoiseSDK._extract_session_llm` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) DETACH DELETE n` |
| `tortoise/sdk.py:20933` | `TortoiseSDK._extract_session_llm` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/sdk.py:21051` | `TortoiseSDK._read_capture_passthrough_props` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$pid}) RETURN {}` |
| `tortoise/sdk.py:21662` | `TortoiseSDK._extract_session_v2` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE ({}) AND o.name = $exact RETURN o.id` |
| `tortoise/sdk.py:21716` | `TortoiseSDK._extract_session_v2` | adjacency | write | stays:graph-maintenance | `MATCH (p:Point {id:$pid}), (o:Object) WHERE (({}) AND o.name = $exac…` |
| `tortoise/sdk.py:21722` | `TortoiseSDK._extract_session_v2` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/sdk.py:21827` | `TortoiseSDK._extract_session_v2` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.content, n.eventId` |
| `tortoise/sdk.py:21851` | `TortoiseSDK._extract_session_v2` | adjacency | write | stays:graph-maintenance | `MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) MERGE (s)-[:CONTAIN…` |
| `tortoise/sdk.py:22428` | `TortoiseSDK._materialize_session_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (s:Source {url:$url}) SET ??? RETURN s.validFrom, s.is_episodi…` |
| `tortoise/sdk.py:23487` | `TortoiseSDK._update_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.content, n.search_keys` |
| `tortoise/sdk.py:23608` | `TortoiseSDK._update_point` | plain | read | candidate:SQL-shaped | `OPTIONAL MATCH (n:Point {id:$id}) RETURN count(n), coalesce(head(col…` |
| `tortoise/sdk.py:23637` | `TortoiseSDK._update_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:23885` | `TortoiseSDK._update_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point:Object {id:$id}) RETURN count(n) > 0` |
| `tortoise/sdk.py:24158` | `TortoiseSDK._update_point._promote_witness` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status, n.outdated` |
| `tortoise/sdk.py:24255` | `TortoiseSDK._update_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point:Object {id:$id}) WHERE (n.status IS NULL OR n.status …` |
| `tortoise/sdk.py:24288` | `TortoiseSDK._update_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point:Object {id:$id}) SET n += $props, n.version = coalesc…` |
| `tortoise/sdk.py:24313` | `TortoiseSDK._update_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) WHERE (n.status IS NULL OR n.status = 'draf…` |
| `tortoise/sdk.py:24361` | `TortoiseSDK._update_point._write_props` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n += $props RETURN count(n)` |
| `tortoise/sdk.py:24400` | `TortoiseSDK._update_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET ??? RETURN count(n)` |
| `tortoise/sdk.py:24566` | `TortoiseSDK._update_point._sync_witness` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) WHERE t.name IN $names …` |
| `tortoise/sdk.py:24670` | `TortoiseSDK._delete_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN count(n) > 0` |
| `tortoise/sdk.py:24679` | `TortoiseSDK._delete_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})-[:TAGGED]->() RETURN count(*) > 0` |
| `tortoise/sdk.py:24812` | `TortoiseSDK._delete_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) DETACH DELETE n` |
| `tortoise/sdk.py:24910` | `TortoiseSDK._delete_point` | adjacency | write | stays:graph-maintenance | `MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t` |
| `tortoise/sdk.py:25016` | `TortoiseSDK._delete_landed_anyway` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:25105` | `TortoiseSDK._assert_lifecycle_guard` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:25156` | `TortoiseSDK._assert_lifecycle_guard` | plain | read | candidate:SQL-shaped | `MATCH (n:Point:Event {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:25209` | `TortoiseSDK._lifecycle_terminal_marker` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:25290` | `TortoiseSDK._assert_window_start_not_inverted` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom` |
| `tortoise/sdk.py:25449` | `TortoiseSDK._invalidate_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.outdated = true, n.updatedAt = $now, …` |
| `tortoise/sdk.py:25465` | `TortoiseSDK._invalidate_point` | adjacency | write | stays:graph-maintenance | `MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) MERGE (a)-[:COR…` |
| `tortoise/sdk.py:25704` | `TortoiseSDK._supersession_label` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN 1 AS rank, count(n) AS hits UNION MA…` |
| `tortoise/sdk.py:25855` | `TortoiseSDK._supersede_entity._rows` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN count(n) AS rows, collect(n.status) AS …` |
| `tortoise/sdk.py:25891` | `TortoiseSDK._supersede_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN count(n) AS hits` |
| `tortoise/sdk.py:26234` | `TortoiseSDK._supersede_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point)-[r]->(old:Point {id:$old_id}) WHERE (op.is_operator…` |
| `tortoise/sdk.py:26305` | `TortoiseSDK._supersede_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point) WHERE op.id IN $op_ids AND (op.is_operator = true O…` |
| `tortoise/sdk.py:26629` | `TortoiseSDK._supersede_point._direct_incident_edges` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t)-[r:IMPL\|NAND]->(old:Point {id:$old_id}) RETURN type(r), r…` |
| `tortoise/sdk.py:26644` | `TortoiseSDK._supersede_point._direct_incident_edges` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids AND (p.is_operator = true OR p.op…` |
| `tortoise/sdk.py:26669` | `TortoiseSDK._supersede_point._epistemic_label` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN ID(n) LIMIT 1` |
| `tortoise/sdk.py:26702` | `TortoiseSDK._supersede_point._claim_classes_for` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN coalesce(n.claimClass, 'asserted')` |
| `tortoise/sdk.py:26906` | `TortoiseSDK._supersede_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom, n.createdAt, n.validFro…` |
| `tortoise/sdk.py:26931` | `TortoiseSDK._supersede_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.validFrom` |
| `tortoise/sdk.py:27041` | `TortoiseSDK._supersede_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (old:Point {id:$old_id})-[ri:INPUT]->(op:Point) WHERE (op.is_o…` |
| `tortoise/sdk.py:27081` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id}), (new:Point {id:$new_id}) MERGE (op)-[:…` |
| `tortoise/sdk.py:27087` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id}), (new:Point {id:$new_id}) MERGE (op)-[:…` |
| `tortoise/sdk.py:27097` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (op:Point {id:$op_id})-[r:{}]->(old:Point {id:$old_id}) WHERE …` |
| `tortoise/sdk.py:27120` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$new_id}), (op:Point {id:$op_id}) MERGE (new)-[…` |
| `tortoise/sdk.py:27126` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$new_id}), (op:Point {id:$op_id}) MERGE (new)-[…` |
| `tortoise/sdk.py:27131` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old_id})-[ri:INPUT]->(op:Point {id:$op_id}) WH…` |
| `tortoise/sdk.py:27202` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (a)-[r:{}]->(b) WHERE ID(r) = $rid DELETE r` |
| `tortoise/sdk.py:27252` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (t:{} {id:$tid}), (new:{} {id:$new_id}) MERGE (t)-[nr:{}]->(ne…` |
| `tortoise/sdk.py:27259` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$a})-[r:{}]->(b:{} {id:$b}) SET r += $attrs` |
| `tortoise/sdk.py:27270` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old_id})-[r:{}]->() WHERE ID(r) = $rid DELETE …` |
| `tortoise/sdk.py:27276` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH ()-[r:{}]->(old:Point {id:$old_id}) WHERE ID(r) = $rid DELETE …` |
| `tortoise/sdk.py:27332` | `TortoiseSDK._supersede_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN ID(n)` |
| `tortoise/sdk.py:27345` | `TortoiseSDK._supersede_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (old:Point {id:$old_id})-[r:{}]->(target) RETURN id(target), t…` |
| `tortoise/sdk.py:27441` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old_id})-[r:{}]->(t) WHERE id(t) = $tid DELETE…` |
| `tortoise/sdk.py:27579` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (new:Point {id:$new_id}), (t) WHERE id(t) = $tid MERGE (new)-[…` |
| `tortoise/sdk.py:27585` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (old:Point {id:$old_id})-[r:{}]->(t) WHERE id(t) = $tid DELETE…` |
| `tortoise/sdk.py:27598` | `TortoiseSDK._supersede_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.status = 'superseded', n.outdated = t…` |
| `tortoise/sdk.py:27609` | `TortoiseSDK._supersede_point` | adjacency | write | stays:graph-maintenance | `MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) MERGE (a)-[:COR…` |
| `tortoise/sdk.py:27757` | `TortoiseSDK._retract_point._retract_witness` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status, n.outdated` |
| `tortoise/sdk.py:27772` | `TortoiseSDK._retract_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) WHERE {} SET n.status = 'retracted', n.upda…` |
| `tortoise/sdk.py:27853` | `TortoiseSDK._promote_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN properties(n)` |
| `tortoise/sdk.py:27942` | `TortoiseSDK._promote_point._cas_witness` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:27951` | `TortoiseSDK._promote_point` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) WHERE n.status = 'draft' SET n.status = 'li…` |
| `tortoise/sdk.py:28014` | `TortoiseSDK._promote_point` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.id` |
| `tortoise/sdk.py:28024` | `TortoiseSDK._promote_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->(:Point {id:$cand…` |
| `tortoise/sdk.py:28065` | `TortoiseSDK._promote_point` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:28107` | `TortoiseSDK._promote_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:NAND]->(:Point {id:$cand}), (o…` |
| `tortoise/sdk.py:28117` | `TortoiseSDK._promote_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->(:Point {id:$cand…` |
| `tortoise/sdk.py:28215` | `TortoiseSDK._promote_incident_operators` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:28230` | `TortoiseSDK._promote_incident_operators` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {id:$oid})-[r]->(s) WHERE {} RETURN s.id, s.status, c…` |
| `tortoise/sdk.py:28272` | `TortoiseSDK._promote_incident_operators` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (o:Point {id:$oid}) WHERE o.status = 'draft' SET o.status = 'l…` |
| `tortoise/sdk.py:28388` | `TortoiseSDK._belief_timeline` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {pointKind:'decision'})-[:aboutObject]->(o:Object) WH…` |
| `tortoise/sdk.py:28403` | `TortoiseSDK._belief_timeline` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (cur:Point)-[:CORRECTS]->(old:Point {pointKind:'decision'}) WH…` |
| `tortoise/sdk.py:28429` | `TortoiseSDK._belief_timeline` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:NAND]->(:Point {id:$a}), (op)-…` |
| `tortoise/sdk.py:28439` | `TortoiseSDK._belief_timeline` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (:Point {id:$a})-[:CORRECTS]->(:Point {id:$b}) RETURN count(*)` |
| `tortoise/sdk.py:28511` | `TortoiseSDK._dedup_content_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND (n.is_operator IS NULL OR n.i…` |
| `tortoise/sdk.py:28565` | `TortoiseSDK._dedup_content_candidates` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.dedup_candidate = true, n.dedup_metho…` |
| `tortoise/sdk.py:28573` | `TortoiseSDK._dedup_content_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:28633` | `TortoiseSDK._list_dedup_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.dedup_candidate = true AND n.dedup_reviewed …` |
| `tortoise/sdk.py:28658` | `TortoiseSDK._list_dedup_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.temporal_candidate = true AND n.temporal_rev…` |
| `tortoise/sdk.py:28739` | `TortoiseSDK.approve_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id: $id}) RETURN labels(n), properties(n)` |
| `tortoise/sdk.py:28752` | `TortoiseSDK.approve_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN properties(n)` |
| `tortoise/sdk.py:28775` | `TortoiseSDK.approve_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.temporal_reviewed = $a, n.reviewed = …` |
| `tortoise/sdk.py:28792` | `TortoiseSDK.approve_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.dedup_reviewed = $a, n.reviewed = tru…` |
| `tortoise/sdk.py:28799` | `TortoiseSDK.approve_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:28805` | `TortoiseSDK.approve_merge` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->(:Point {id:$cand…` |
| `tortoise/sdk.py:28913` | `TortoiseSDK._approve_entity_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.dedup_candidate, n.temporal_candid…` |
| `tortoise/sdk.py:28969` | `TortoiseSDK._approve_entity_merge` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.id` |
| `tortoise/sdk.py:29623` | `TortoiseSDK._create_operator` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:29789` | `TortoiseSDK._create_operator` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (o:Point {id:$id, is_operator:true, op_type:$op, direction:$d…` |
| `tortoise/sdk.py:29824` | `TortoiseSDK._create_operator` | adjacency | write | stays:graph-maintenance | `MATCH (o:Point {id:$oid}), (s) WHERE {} AND s.id = $sid CREATE (o)-[…` |
| `tortoise/sdk.py:29849` | `TortoiseSDK._create_operator` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Point {id:$sid}) WHERE s.status IS NULL SET s.status = 'liv…` |
| `tortoise/sdk.py:30173` | `TortoiseSDK._mitigate_operator` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {id:$id})-[r:mitigated_by]->(m:Point) WHERE {} RETUR…` |
| `tortoise/sdk.py:30302` | `TortoiseSDK._mitigate_operator` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (m:Point {id:$id, content:$c, pointKind:'statement', mitigati…` |
| `tortoise/sdk.py:30314` | `TortoiseSDK._mitigate_operator` | adjacency | write | stays:graph-maintenance | `MATCH (m:Point {id:$mid}), (op:Point {id:$oid}) CREATE (m)-[:IMPL]->…` |
| `tortoise/sdk.py:30453` | `TortoiseSDK._query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN properties(n)` |
| `tortoise/sdk.py:30526` | `TortoiseSDK._paginated_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN count(n)` |
| `tortoise/sdk.py:30549` | `TortoiseSDK._paginated_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN properties(n) ORDER BY n.createdAt D…` |
| `tortoise/sdk.py:30648` | `TortoiseSDK._get_point._cache_answer` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN properties(n)` |
| `tortoise/sdk.py:31545` | `TortoiseSDK._summarize_structure` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:$k}) WHERE (n.is_operator IS NULL OR n.is_…` |
| `tortoise/sdk.py:31551` | `TortoiseSDK._summarize_structure` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:31556` | `TortoiseSDK._summarize_structure` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator = true OR n.op_type IS NOT NULL…` |
| `tortoise/sdk.py:31582` | `TortoiseSDK._list_pointkinds` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:31610` | `TortoiseSDK._list_sources` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source) WHERE s.documentKind IS NULL OPTIONAL MATCH (p:Poin…` |
| `tortoise/sdk.py:31629` | `TortoiseSDK._list_tags` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (t:Tag) OPTIONAL MATCH (p:Point)-[:TAGGED]->(t) RETURN t.name …` |
| `tortoise/sdk.py:31647` | `TortoiseSDK._query_points_by_tag` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag}) RETURN properties(p) …` |
| `tortoise/sdk.py:32202` | `TortoiseSDK._point_bearing_ids` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids AND coalesce(p.is_operator, false…` |
| `tortoise/sdk.py:32655` | `TortoiseSDK._fetch_endpoint_info` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE n.id IN $vals OR n.url IN $vals OR n.eventId IN $val…` |
| `tortoise/sdk.py:32699` | `TortoiseSDK._terminal_holder_among` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND {} RETURN n.id LIMIT 1` |
| `tortoise/sdk.py:32855` | `TortoiseSDK._find_terminal_dedup_hit` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {content_hash:$ch}) WHERE {} RETURN n.id LIMIT 1` |
| `tortoise/sdk.py:32872` | `TortoiseSDK._find_terminal_dedup_hit` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {content_hash:$ch}) WHERE {} RETURN n.id LIMIT 1` |
| `tortoise/sdk.py:32889` | `TortoiseSDK._find_terminal_dedup_hit` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN n.id LIMIT 1` |
| `tortoise/sdk.py:33002` | `TortoiseSDK._terminal_content_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} AND n.content_hash IN $hashes RETURN n.cont…` |
| `tortoise/sdk.py:33040` | `TortoiseSDK._terminal_content_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} AND n.content_hash IS NULL AND n.content IN…` |
| `tortoise/sdk.py:33194` | `TortoiseSDK._entity_name_holders` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, {} AS term, coalesce(n.…` |
| `tortoise/sdk.py:35852` | `TortoiseSDK._ingest` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN count(s)` |
| `tortoise/sdk.py:36451` | `TortoiseSDK._ingest` | adjacency | write | stays:graph-maintenance | `MATCH (o:Point {id:$oid}), (t) WHERE {} AND t.id = $inp MERGE (o)-[r…` |
| `tortoise/sdk.py:36572` | `TortoiseSDK._ingest` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$pid})-[:extractedFrom]->(s:Source {url:$url}) RE…` |
| `tortoise/sdk.py:36593` | `TortoiseSDK._ingest` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a)-[r:{}]->(b) WHERE (a.id = $f OR a.eventId = $f OR a.url = …` |
| `tortoise/sdk.py:36738` | `TortoiseSDK._stamp_batch_id` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN n.batch_id` |
| `tortoise/sdk.py:36748` | `TortoiseSDK._stamp_batch_id` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.batch_id = $bid` |
| `tortoise/sdk.py:36854` | `TortoiseSDK._list_batch` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {batch_id:$bid}) RETURN n.id, n.pointKind, n.status, …` |
| `tortoise/sdk.py:36867` | `TortoiseSDK._list_batch` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a)-[r:IMPL\|NAND]->(b) WHERE {} AND {} AND r.batch_id = $bid …` |
| `tortoise/sdk.py:36909` | `TortoiseSDK._list_batches` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.batch_id IS NOT NULL WITH n.batch_id AS bid,…` |
| `tortoise/sdk.py:36917` | `TortoiseSDK._list_batches` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH ()-[r:IMPL\|NAND]->() WHERE r.batch_id IS NOT NULL WITH r.batc…` |
| `tortoise/sdk.py:37047` | `TortoiseSDK._create_direct_edge` | plain | read | candidate:SQL-shaped | `MATCH (p:{}) WHERE p.id IN $ids RETURN p.id, coalesce(p.is_operator,…` |
| `tortoise/sdk.py:37145` | `TortoiseSDK._create_direct_edge` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:{} {id:$src})-[r:{}]->(b:{} {id:$tgt}) RETURN count(r)` |
| `tortoise/sdk.py:37157` | `TortoiseSDK._create_direct_edge` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$src}), (b:{} {id:$tgt}) MERGE (a)-[r:{}]->(b)` |
| `tortoise/sdk.py:37182` | `TortoiseSDK._create_direct_edge` | adjacency | write | stays:graph-maintenance | `MATCH (a:{} {id:$src})-[r:{}]->(b:{} {id:$tgt}) SET r += $attrs` |
| `tortoise/sdk.py:37195` | `TortoiseSDK._create_direct_edge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Point {id:$id}) WHERE s.status IS NULL SET s.status = 'live…` |
| `tortoise/sdk.py:37461` | `TortoiseSDK._find_operator` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true, op_type:$op}) OPTIONAL MATCH (o)-[…` |
| `tortoise/sdk.py:37556` | `TortoiseSDK._find_operator` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point {is_operator:true, op_type:$op}) OPTIONAL MATCH (o)-[…` |
| `tortoise/sdk.py:37643` | `TortoiseSDK._apply_ingest_mitigation` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {id:$oid})-[:mitigated_by]->(m:Point) RETURN m.id, m…` |
| `tortoise/sdk.py:37782` | `TortoiseSDK._reapply_operator_promotion` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Point {id:$id}) WHERE s.status IS NULL SET s.status = 'live…` |
| `tortoise/sdk.py:37987` | `TortoiseSDK._file_human_approval` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND n.is_operator = false RETURN …` |
| `tortoise/sdk.py:38170` | `TortoiseSDK._raw_entry_for_point` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$pid})-[:extractedFrom]->(src:Source) RETURN prop…` |
| `tortoise/sdk.py:38223` | `TortoiseSDK._provenance` | plain | read | candidate:SQL-shaped | `MATCH (s:Subject {id:$sid}) RETURN properties(s)` |
| `tortoise/sdk.py:38246` | `TortoiseSDK._provenance` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r), labels(n)[0], p…` |
| `tortoise/sdk.py:38547` | `TortoiseSDK._cross_lens_pool` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:extractedFrom]->(s:Source) WHERE p.is_operator = f…` |
| `tortoise/sdk.py:38603` | `TortoiseSDK._cross_lens_pairs` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.id IN $ids AND p.embedding IS NOT NULL RETUR…` |
| `tortoise/sdk.py:38683` | `TortoiseSDK._review_pool` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.is_operator = false AND {} AND (n.outdated I…` |
| `tortoise/sdk.py:38800` | `TortoiseSDK._connected_pairs` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r1]->(a:Point), (op)-[r2]->(b:P…` |
| `tortoise/sdk.py:38809` | `TortoiseSDK._connected_pairs` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point)-[r]-(b:Point) WHERE a.id IN $ids AND b.id IN $ids AN…` |
| `tortoise/sdk.py:38908` | `TortoiseSDK._epistemic_edges` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r:IMPL\|NAND]->(p:Point) RETURN…` |
| `tortoise/sdk.py:38936` | `TortoiseSDK._epistemic_edges` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point)-[r:IMPL\|NAND]->(b:Point) WHERE a.is_operator = fals…` |
| `tortoise/sdk.py:38966` | `TortoiseSDK._review_prune` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.status, coalesce(n…` |
| `tortoise/sdk.py:38980` | `TortoiseSDK._review_prune` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Point)-[:CORRECTS]->(o:Point) WHERE o.id IN $ids RETURN o.i…` |
| `tortoise/sdk.py:39017` | `TortoiseSDK._review_prune` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.is_operator = false AND {} AND {} AND {} WIT…` |
| `tortoise/sdk.py:39066` | `TortoiseSDK._review_prune` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r:NAND]->(n:Point) WHERE n.is_o…` |
| `tortoise/sdk.py:39339` | `TortoiseSDK._retry_cap_verdict` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id = $id AND n.ep_dirty = true WITH n, (n.ep…` |
| `tortoise/sdk.py:39383` | `TortoiseSDK._prune_nonexistent_dirty_roots` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:39704` | `TortoiseSDK._stamp_counts` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:39850` | `TortoiseSDK._propagation_counts` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:IMPL\|NAND]-() WHERE (n.is_operator IS NULL OR n.i…` |
| `tortoise/sdk.py:39859` | `TortoiseSDK._propagation_counts` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[r:IMPL\|NAND]-(m) WHERE (n.is_operator IS NULL OR n…` |
| `tortoise/sdk.py:39981` | `TortoiseSDK._ep_epoch` | ep | read | stays:ep-carveout (§6.3) | `MATCH (m:EpMeta) RETURN m.ep_version` |
| `tortoise/sdk.py:40004` | `TortoiseSDK._hydrate_dirty_roots` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.ep_dirty = true AND n.id IS NOT NULL AND NOT…` |
| `tortoise/sdk.py:40120` | `TortoiseSDK._sweep_dirty_roots` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids AND n.ep_dirty = true WITH n, ($r…` |
| `tortoise/sdk.py:40225` | `TortoiseSDK._unsweepable_dirty_roots` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, (n.is_operator = tru…` |
| `tortoise/sdk.py:40251` | `TortoiseSDK._unsweepable_dirty_roots` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point)-[:IMPL\|NAND]->(n:Point) WHERE n.id IN $ids AND (o.i…` |
| `tortoise/sdk.py:40273` | `TortoiseSDK._unsweepable_dirty_roots` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:IMPL\|NAND]-(d) WHERE n.id IN $ids AND {} RETURN D…` |
| `tortoise/sdk.py:40302` | `TortoiseSDK._reverse_bfs_neighbors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:IMPL\|NAND]->(p:Point) WHERE p…` |
| `tortoise/sdk.py:40320` | `TortoiseSDK._reverse_bfs_neighbors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:IMPL\|NAND]->(op:Point {is_operator:true}) WHERE p…` |
| `tortoise/sdk.py:40346` | `TortoiseSDK._reverse_bfs_neighbors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:IMPL\|NAND]->(c) WHERE op.id I…` |
| `tortoise/sdk.py:40403` | `TortoiseSDK._advance_ep_version` | ep | write | stays:ep-carveout (§6.3) | `MERGE (m:EpMeta) SET m.ep_version = coalesce(m.ep_version, 0) + 1 RE…` |
| `tortoise/sdk.py:40488` | `TortoiseSDK._mark_dirty` | ep | write | stays:ep-carveout (§6.3) | `UNWIND $ids AS pid MATCH (n:Point {id: pid}) WHERE NOT coalesce(n.st…` |
| `tortoise/sdk.py:40520` | `TortoiseSDK._mark_dirty` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:40534` | `TortoiseSDK._mark_dirty` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.id IN $ids AND n.ep_dirty = true SET n.ep_di…` |
| `tortoise/sdk.py:40760` | `TortoiseSDK._auto_dream_mode` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {is_operator:true}) RETURN count(n)` |
| `tortoise/sdk.py:40830` | `TortoiseSDK._count_ep_factors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[:IMPL\|NAND]->(t:Point) WHERE (…` |
| `tortoise/sdk.py:40885` | `TortoiseSDK._count_ep_factors` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (a:Point)-[r:IMPL\|NAND]->(b:Point) WHERE (a.is_operator IS NU…` |
| `tortoise/sdk.py:41222` | `TortoiseSDK._window_closure` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:41260` | `TortoiseSDK._window_closure._hop` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:41285` | `TortoiseSDK._window_closure._hop` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:41310` | `TortoiseSDK._window_closure` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Point)-[r:IMPL\|NAND]->(c) WHERE o.id IN $ids AND {} AND (o…` |
| `tortoise/sdk.py:41376` | `TortoiseSDK._reachable_claim_count` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:41410` | `TortoiseSDK._edged_claim_ids` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n)-[r:IMPL\|NAND]-(m) WHERE {} AND {} AND (n.is_operator IS N…` |
| `tortoise/sdk.py:41866` | `TortoiseSDK._compute_confidence` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:42068` | `TortoiseSDK._hydrate_evidence` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NU…` |
| `tortoise/sdk.py:42140` | `TortoiseSDK._stamp_authored_word` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET {} RETURN n.id` |
| `tortoise/sdk.py:42293` | `TortoiseSDK._set_point_baseline` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point {id: $id}) SET n.ep_alpha = $a, n.ep_beta = $b, n.bas…` |
| `tortoise/sdk.py:42350` | `TortoiseSDK._set_point_baseline` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:42357` | `TortoiseSDK._set_point_baseline` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE n.id = $id RETURN labels(n)` |
| `tortoise/sdk.py:42472` | `TortoiseSDK._invalidate_inheritance_gate` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids REMOVE n.inherited_at` |
| `tortoise/sdk.py:42642` | `TortoiseSDK._apply_source_inheritance` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {pointKind:'assessment'}) WHERE (p.outdated IS NULL O…` |
| `tortoise/sdk.py:42682` | `TortoiseSDK._apply_source_inheritance` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:extractedFrom]->(s:Source) WHERE (n.is_operator IS…` |
| `tortoise/sdk.py:42826` | `TortoiseSDK._apply_source_inheritance` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source)-[:references]->(o:Source) RETURN s.url, o.url` |
| `tortoise/sdk.py:42841` | `TortoiseSDK._apply_source_inheritance` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:extractedFrom]->(s:Source) WHERE n.id IN $ids RETU…` |
| `tortoise/sdk.py:42869` | `TortoiseSDK._apply_source_inheritance` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.baseline_source = 'inherited-from-source' AN…` |
| `tortoise/sdk.py:42892` | `TortoiseSDK._apply_source_inheritance` | ep | write | stays:ep-carveout (§6.3) | `MATCH (n:Point {id:$id}) REMOVE n.ep_alpha, n.ep_beta, n.baseline_se…` |
| `tortoise/sdk.py:42937` | `TortoiseSDK._apply_source_inheritance` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point {id:$id}) RETURN n.baseline_source, n.inherited_at, c…` |
| `tortoise/sdk.py:43103` | `TortoiseSDK._touch_inherited_at` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.inherited_at = $ts` |
| `tortoise/sdk.py:43171` | `TortoiseSDK._calibrate_summary` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE {} AND n.id IS NOT NULL AND toLower(typeof(n.i…` |
| `tortoise/sdk.py:43627` | `TortoiseSDK._uncalibrated_ep_participants` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids AND {} RETURN n.id` |
| `tortoise/sdk.py:43751` | `TortoiseSDK._find_points_by_key` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN n.id` |
| `tortoise/sdk.py:44087` | `TortoiseSDK._fold_source_evidence` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point {id:$id}) SET n.extractedFrom = $ref, n.extractedFrom…` |
| `tortoise/sdk.py:44200` | `TortoiseSDK._find_point_by_content` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {content_hash:$ch}) WHERE {} RETURN n.id` |
| `tortoise/sdk.py:44220` | `TortoiseSDK._find_point_by_content` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN n.id` |
| `tortoise/sdk.py:44448` | `TortoiseSDK._semantic_dedup` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:$kind}) WHERE (n.is_operator IS NULL OR n.…` |
| `tortoise/sdk.py:44519` | `TortoiseSDK._diary_read` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:'diary', authoredBy:$agent, wing:$wing}) W…` |
| `tortoise/sdk.py:44526` | `TortoiseSDK._diary_read` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:'diary', authoredBy:$agent}) WHERE n.is_op…` |
| `tortoise/sdk.py:44542` | `TortoiseSDK._status` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n) LIMIT 1` |
| `tortoise/sdk.py:44760` | `TortoiseSDK._ingest_corpus` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN properties(e)` |
| `tortoise/sdk.py:45039` | `TortoiseSDK._ingest_corpus` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.file_hash` |
| `tortoise/sdk.py:45189` | `TortoiseSDK._suggest_entry_points` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE {} RETURN n.id, n.content, n.pointKind` |
| `tortoise/sdk.py:45279` | `TortoiseSDK._session_context` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {pointKind:'diary'}) WHERE n.is_operator = false RETU…` |
| `tortoise/sdk.py:45284` | `TortoiseSDK._session_context` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_operator = false RETURN properties(n) ORD…` |
| `tortoise/sdk.py:45289` | `TortoiseSDK._session_context` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT…` |
| `tortoise/sdk.py:45301` | `TortoiseSDK._session_context` | ep | read | stays:ep-carveout (§6.3) | `MATCH (n:Point) WHERE n.confidence IS NOT NULL AND n.is_operator = f…` |
| `tortoise/sdk.py:45399` | `TortoiseSDK._issue_insight` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = fals…` |
| `tortoise/sdk.py:45831` | `TortoiseSDK._annotate_ask_hits` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (ev:Event) WHERE e…` |
| `tortoise/sdk.py:47053` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `RETURN 1` |
| `tortoise/sdk.py:48156` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, n.{}` |
| `tortoise/sdk.py:48212` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.{} = $kind {} AND n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:48221` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.{} IN [{}] {} AND n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:48270` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids AND n.status IN $statuses RETURN …` |
| `tortoise/sdk.py:48340` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.pointKind, n.conte…` |
| `tortoise/sdk.py:49029` | `TortoiseSDK._tortoise_fts_query` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (sess:Session)-[:C…` |
| `tortoise/sdk.py:49154` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Event) WHERE n.eventId IN $ids RETURN n.eventId, n.subject,…` |
| `tortoise/sdk.py:49165` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Subject) WHERE n.id IN $ids RETURN n.id, n.name, n.subjectK…` |
| `tortoise/sdk.py:49176` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Source) WHERE n.url IN $ids AND n.documentKind IS NOT NULL …` |
| `tortoise/sdk.py:49201` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Object) WHERE n.id IN $ids RETURN n.id, n.name, n.objectKin…` |
| `tortoise/sdk.py:49222` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {is_operator: true}) WHERE n.id IN $ids RETURN n.id, …` |
| `tortoise/sdk.py:49233` | `TortoiseSDK._tortoise_fts_query` | plain | read | candidate:SQL-shaped | `MATCH (n:Source) WHERE n.url IN $ids RETURN n.url, n.title, n.source…` |
| `tortoise/sdk.py:49630` | `TortoiseSDK._search_keys_prf_expansion` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, coalesce(n.search_ke…` |
| `tortoise/sdk.py:49780` | `TortoiseSDK._entity_key_alias_harvest` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (o:Object) WHERE o.id IN $ids OPTIONAL MATCH (o)<-[:aboutObjec…` |
| `tortoise/sdk.py:50137` | `TortoiseSDK._expand_relationships` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/sdk.py:50298` | `TortoiseSDK.get_historical_knowledge` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {id:$id}) RETURN p.content, p.validFrom, p.validTo, p…` |
| `tortoise/sdk.py:50327` | `TortoiseSDK.get_historical_knowledge` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point {id:$id})-[:CORRECTS]->(old:Point) RETURN old.id ORDE…` |
| `tortoise/sdk.py:50947` | `TortoiseSDK._state_counter_evidence` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (c:Point)-[r:NAND]->(n:Point) WHERE n.id IN $ids RETURN n.id, …` |
| `tortoise/sdk.py:50979` | `TortoiseSDK._state_arguments` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r:IMPL\|NAND\|hasPart]-(n:Point…` |
| `tortoise/sdk.py:51027` | `TortoiseSDK._state_mitigations` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true})-[r:mitigated_by]->(m:Point) WHER…` |
| `tortoise/sdk.py:51061` | `TortoiseSDK._state_related_objects` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[a:???]->(t) WHERE n.id IN $ids RETURN n.id, labels(…` |
| `tortoise/sdk.py:51078` | `TortoiseSDK._state_related_points` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[a:???]->(o:Object) WHERE o.id IN $ids RETURN o.id, …` |
| `tortoise/sdk.py:51176` | `TortoiseSDK._recall_gaps` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {is_operator:true}) WHERE n.id IN $ids RETURN n.id` |
| `tortoise/sdk.py:51290` | `TortoiseSDK._resolve_subgraph_seed` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n.id = $seed OR n.url = $seed) AND labels(n)[0] IN …` |
| `tortoise/sdk.py:51395` | `TortoiseSDK._org_create` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {idempotency_key:$ik}) RETURN t.id, t.name` |
| `tortoise/sdk.py:51420` | `TortoiseSDK._org_create` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {name:$name}) RETURN count(t) > 0` |
| `tortoise/sdk.py:51445` | `TortoiseSDK._org_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (t:Team {id:$id, name:$name, ???graph_name:$gn, createdAt:$no…` |
| `tortoise/sdk.py:51454` | `TortoiseSDK._org_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) SET t.idempotency_key = $ik` |
| `tortoise/sdk.py:51480` | `TortoiseSDK._org_create` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {user_id:$uid, status:'active'}) WHERE m.org_id …` |
| `tortoise/sdk.py:51492` | `TortoiseSDK._org_create` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN t.graph_name` |
| `tortoise/sdk.py:51534` | `TortoiseSDK._org_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id:$id}) DETACH DELETE t` |
| `tortoise/sdk.py:51586` | `TortoiseSDK._graph_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (g:Graph {id:$gid, org_id:$tid, name:$name, kind:$kind, names…` |
| `tortoise/sdk.py:51689` | `TortoiseSDK.list_memory_graphs` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN properties(g) ORDER BY CASE g.k…` |
| `tortoise/sdk.py:51756` | `TortoiseSDK._graph_count` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) WHERE g.status IS NULL OR g.status <> …` |
| `tortoise/sdk.py:51774` | `TortoiseSDK._graph_delete` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.kind` |
| `tortoise/sdk.py:51783` | `TortoiseSDK._graph_delete` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) SET g.status = 'deleted', g.d…` |
| `tortoise/sdk.py:51809` | `TortoiseSDK.restore_memory_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) WHERE g.status = 'deleted' AN…` |
| `tortoise/sdk.py:51825` | `TortoiseSDK._trash_graphs` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid, status:'deleted'}) WHERE coalesce(g.kin…` |
| `tortoise/sdk.py:51904` | `TortoiseSDK.update_memory_graph` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {org_id:$tid}) RETURN g.id, g.kind, coalesce(g.status…` |
| `tortoise/sdk.py:51922` | `TortoiseSDK.update_memory_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) SET g.name = $name` |
| `tortoise/sdk.py:51927` | `TortoiseSDK.update_memory_graph` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (g:Graph {id:$gid, org_id:$tid}) SET g.recording = $v` |
| `tortoise/sdk.py:51939` | `TortoiseSDK._graph_key_ids` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, graph_id:$gid}) RETURN k.id` |
| `tortoise/sdk.py:51961` | `TortoiseSDK._graph_active_key_count` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid, graph_id:$gid}) WHERE k.revoked_at IS …` |
| `tortoise/sdk.py:51971` | `TortoiseSDK._org_get` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id:$id}) RETURN properties(t)` |
| `tortoise/sdk.py:52158` | `TortoiseSDK._membership_create` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid}) WHERE m.status = 'active' RETURN …` |
| `tortoise/sdk.py:52171` | `TortoiseSDK._membership_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (m:Membership {id:$id, user_id:$uid, org_id:$tid, role:$role,…` |
| `tortoise/sdk.py:52178` | `TortoiseSDK._membership_create` | adjacency | write | stays:graph-maintenance | `MATCH (m:Membership {id:$mid}), (t:Team {id:$tid}) CREATE (m)-[:BELO…` |
| `tortoise/sdk.py:52209` | `TortoiseSDK._verify_hashed_lookup` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.key_prefix = $prefix RETURN n.{}, properties(n)` |
| `tortoise/sdk.py:52223` | `TortoiseSDK._verify_hashed_lookup` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN n.{}, properties(n)` |
| `tortoise/sdk.py:52370` | `TortoiseSDK.list_keys` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) RETURN k.id, k.key_prefix, k.graph_id…` |
| `tortoise/sdk.py:52452` | `TortoiseSDK.revoke_key` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id:$id}) RETURN k.org_id` |
| `tortoise/sdk.py:52503` | `TortoiseSDK.list_members` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid}) WHERE m.status IN ['active', 'inv…` |
| `tortoise/sdk.py:52553` | `TortoiseSDK._membership_remove` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) RETURN m.role` |
| `tortoise/sdk.py:52564` | `TortoiseSDK._membership_remove` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) SET m.status='remov…` |
| `tortoise/sdk.py:52692` | `TortoiseSDK._apikey_create` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp,…` |
| `tortoise/sdk.py:52699` | `TortoiseSDK._apikey_create` | adjacency | write | stays:graph-maintenance | `MATCH (k:APIKey {id:$kid}), (t:Team {id:$tid}) CREATE (k)-[:BELONGS_…` |
| `tortoise/sdk.py:52790` | `TortoiseSDK._apikey_revoke` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id:$id}) RETURN k.revoked_at, k.org_id` |
| `tortoise/sdk.py:52799` | `TortoiseSDK._apikey_revoke` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now` |
| `tortoise/sdk.py:52860` | `TortoiseSDK._apikey_verify` | plain | read | candidate:SQL-shaped | `MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.namespace` |
| `tortoise/sdk.py:52910` | `TortoiseSDK._signup_token_lookup` | plain | read | candidate:SQL-shaped | `MATCH (n:SignupToken {lookup_key:$lk}) RETURN properties(n)` |
| `tortoise/sdk.py:52971` | `TortoiseSDK._signup_token_recover` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp,…` |
| `tortoise/sdk.py:53001` | `TortoiseSDK._signup_token_recover` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL AND (k.exp…` |
| `tortoise/sdk.py:53013` | `TortoiseSDK._signup_token_recover` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now` |
| `tortoise/sdk.py:53044` | `TortoiseSDK._signup_token_revoke` | plain | read | candidate:SQL-shaped | `MATCH (n:SignupToken {lookup_key:$lk}) RETURN properties(n)` |
| `tortoise/sdk.py:53070` | `TortoiseSDK._signup_token_revoke` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:SignupToken {lookup_key:$lk, org_id:$tid}) SET n.revoked_at…` |
| `tortoise/sdk.py:53076` | `TortoiseSDK._signup_token_revoke` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:SignupToken {token_hash:$th, org_id:$tid}) SET n.revoked_at…` |
| `tortoise/sdk.py:53092` | `TortoiseSDK._invitation_list` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {org_id:$tid}) RETURN i.id, i.email, i.role, i.c…` |
| `tortoise/sdk.py:53109` | `TortoiseSDK._invitation_get_by_id` | plain | read | candidate:SQL-shaped | `MATCH (i:Invitation {id:$id}) RETURN properties(i)` |
| `tortoise/sdk.py:53124` | `TortoiseSDK._invitation_revoke` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (i:Invitation {id:$id}) SET i.status = 'revoked'` |
| `tortoise/sdk.py:53152` | `TortoiseSDK._sweep_invite_ghost_memberships` | plain | read | candidate:SQL-shaped | `MATCH (m:Membership) WHERE m.user_id STARTS WITH 'invite-' OPTIONAL …` |
| `tortoise/sdk.py:53173` | `TortoiseSDK._sweep_invite_ghost_memberships` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (m:Membership {org_id:$tid, user_id:$uid}) DELETE m` |
| `tortoise/sdk.py:54043` | `TortoiseSDK._label_reachable_by` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {{}:$id}) RETURN count(n)` |
| `tortoise/sdk.py:54315` | `TortoiseSDK._refuse_ambiguous_rename` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id: $id}) RETURN n.name` |
| `tortoise/sdk.py:54655` | `TortoiseSDK._assert_supersession_names_a_successor` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN n.id AS id, n.status AS st, n.supersede…` |
| `tortoise/sdk.py:54711` | `TortoiseSDK._assert_supersession_names_a_successor` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN n.id AS id, ID(n) AS nid, …` |
| `tortoise/sdk.py:54754` | `TortoiseSDK._assert_supersession_names_a_successor` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE ID(n) IN $nids WITH n MATCH (n)<-[:CORRECTS]-(…` |
| `tortoise/sdk.py:55232` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE e.eventId = $id OR e.id = $id RETURN count(e)` |
| `tortoise/sdk.py:55279` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id = $id OR n.eventId = $id OR n.url = $id R…` |
| `tortoise/sdk.py:55388` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN n.status` |
| `tortoise/sdk.py:55442` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE e.eventId = $id OR e.id = $id RETURN count(e)` |
| `tortoise/sdk.py:55560` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN 1 AS rank, count(n) AS hits, sum(CAS…` |
| `tortoise/sdk.py:55635` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.id IN $ids RETURN collect(DISTINCT n.status)…` |
| `tortoise/sdk.py:56085` | `TortoiseSDK._update_entity` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:Point) WHERE n.id IN $ids SET n += $s RETURN count(n)` |
| `tortoise/sdk.py:56398` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:{} {id:$id}) RETURN count(n) AS hits, collect(DISTINCT n.st…` |
| `tortoise/sdk.py:56731` | `TortoiseSDK._update_entity` | plain | read | candidate:SQL-shaped | `MATCH (n:Subject {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:57087` | `TortoiseSDK._update_entity` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) SET n += $p RETURN count(n)` |
| `tortoise/sdk.py:57142` | `TortoiseSDK._update_entity` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) SET n += $props RETURN [k IN $keys \| properti…` |
| `tortoise/sdk.py:57196` | `TortoiseSDK._delete_entity` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (n:{} {{}:$id}) DETACH DELETE n RETURN count(n)` |
| `tortoise/sdk.py:57598` | `TortoiseSDK.create_entity._np_lands` | plain | read | candidate:SQL-shaped | `MATCH (n:Object {id:$id}) RETURN count(n)` |
| `tortoise/sdk.py:58258` | `TortoiseSDK._nudge_candidates` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE NOT n.id IN $ex RETURN n.id, n.content, n.name…` |
| `tortoise/sdk.py:58642` | `TortoiseSDK._search_sessions` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE e.eventId IN $ids RETURN properties(e)` |
| `tortoise/sdk.py:58728` | `TortoiseSDK._search_sessions` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) WHERE {} RETURN properties(e) ORDER BY e.startedAt D…` |
| `tortoise/sdk.py:58743` | `TortoiseSDK._get_events` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventKind: $ek}) RETURN properties(e) ORDER BY e.sta…` |
| `tortoise/sdk.py:58749` | `TortoiseSDK._get_events` | plain | read | candidate:SQL-shaped | `MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT…` |
| `tortoise/sdk.py:58794` | `TortoiseSDK._get_session` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventKind: 'AgentSession'}) WHERE e.session_id = $si…` |
| `tortoise/sdk.py:58828` | `TortoiseSDK._read_captured_session` | plain | read | candidate:SQL-shaped | `MATCH (s:Session {id:$sid}) RETURN s.id, s.created_at, s.turn_count,…` |
| `tortoise/sdk.py:58863` | `TortoiseSDK._read_captured_session` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE (p.pointKin…` |
| `tortoise/sdk.py:58929` | `TortoiseSDK._read_captured_session` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'…` |
| `tortoise/sdk.py:58986` | `TortoiseSDK._read_captured_session` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE (p.pointKin…` |
| `tortoise/sdk.py:58998` | `TortoiseSDK._read_captured_session` | plain | read | candidate:SQL-shaped | `MATCH (src:Source {url:$url}) RETURN src.url, src.sourceKind, src.ev…` |
| `tortoise/sdk.py:59458` | `TortoiseSDK._index_gate_read` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.contentHash, s.version` |
| `tortoise/sdk.py:59467` | `TortoiseSDK._index_gate_read` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.embedding, e.embeddingRepair…` |
| `tortoise/sdk.py:59480` | `TortoiseSDK._index_gate_read` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$did}) WHERE s.documentKind IS NOT NULL RETURN …` |
| `tortoise/sdk.py:59486` | `TortoiseSDK._index_gate_read` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source {url:$url})-[:references]->(n) RETURN count(n)` |
| `tortoise/sdk.py:59677` | `TortoiseSDK._index_process_unit._sf_lookup` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.source_file` |
| `tortoise/sdk.py:59933` | `TortoiseSDK._index_process_unit` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.embedding` |
| `tortoise/sdk.py:59959` | `TortoiseSDK._index_process_unit` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN e.embedding` |
| `tortoise/sdk.py:60025` | `TortoiseSDK._index_source_merge` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.__runId, s.version` |
| `tortoise/sdk.py:60034` | `TortoiseSDK._index_source_merge` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) REMOVE s.__runId` |
| `tortoise/sdk.py:60153` | `TortoiseSDK._session_event_write` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (e:Event {eventId:$eid}) ON CREATE SET e += $props, ??? ON MAT…` |
| `tortoise/sdk.py:60176` | `TortoiseSDK._record_embedding_marker` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId:$eid}) SET e.embeddingRepairFailedAt = $ts` |
| `tortoise/sdk.py:60379` | `TortoiseSDK._embedding_repair_attempt` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId:$eid}) SET e.embeddingRepairFailedAt = $ts` |
| `tortoise/sdk.py:60389` | `TortoiseSDK._embedding_repair_attempt` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (e:Event {eventId:$eid}) ???SET ???, e.embeddingRepairFailedAt…` |
| `tortoise/sdk.py:60397` | `TortoiseSDK._embedding_repair_attempt` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId:$eid}) RETURN properties(e)` |
| `tortoise/sdk.py:60486` | `TortoiseSDK._backfill_probe` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN 1` |
| `tortoise/sdk.py:60490` | `TortoiseSDK._backfill_probe` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source {url:$url})-[:references]->(e:Event {eventId:$eid}) …` |
| `tortoise/sdk.py:60568` | `TortoiseSDK._session_index_health` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventKind:'AgentSession'}) RETURN e.eventId, e.file_…` |
| `tortoise/sdk.py:60760` | `TortoiseSDK._connect_issue_objects` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MERGE (o:Object {id:$oid}) ON CREATE SET o.objectKind=$okind, o.name…` |
| `tortoise/sdk.py:61621` | `TortoiseSDK._set_source_tier` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.sourceKind` |
| `tortoise/sdk.py:61630` | `TortoiseSDK._set_source_tier` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) SET s.credibilityTier = $t, s.sourceKind…` |
| `tortoise/sdk.py:61635` | `TortoiseSDK._set_source_tier` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) SET s.credibilityTier = $t` |
| `tortoise/sdk.py:61676` | `TortoiseSDK._compute_source_prior` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.credibilityTier, s.sourceKind, …` |
| `tortoise/sdk.py:61690` | `TortoiseSDK._compute_source_prior` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {pointKind:'assessment'}) WHERE p.targetSource = $url…` |
| `tortoise/sdk.py:61749` | `TortoiseSDK._get_source_reliability` | plain | read | candidate:SQL-shaped | `MATCH (s:Source {url:$url}) RETURN s.reliability, s.reliabilityCompo…` |
| `tortoise/sdk.py:61787` | `TortoiseSDK._get_source_reliability` | plain | read | candidate:SQL-shaped | `MATCH (p:Point {pointKind:'assessment'}) WHERE p.targetSource = $url…` |
| `tortoise/sdk.py:61891` | `TortoiseSDK._assess_source` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (p:Point {pointKind:'assessment'}) WHERE p.targetSource = $url…` |
| `tortoise/sdk.py:61939` | `TortoiseSDK._invalidate_inheritance_gate_for_source` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[:extractedFrom]->(s:Source {url:$url}) RETURN n.id` |
| `tortoise/sdk.py:61954` | `TortoiseSDK._write_reliability_cache` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) SET s.reliability = $r, s.reliabilityCom…` |
| `tortoise/sdk.py:61971` | `TortoiseSDK._clear_reliability_cache` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (s:Source {url:$url}) REMOVE s.reliability, s.reliabilityCompo…` |
| `tortoise/sdk.py:62011` | `TortoiseSDK._compute_reputation` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Subject)-[:performs]->(e:Event) MATCH (e)-[:IMPL]->(p:Point…` |
| `tortoise/sdk.py:62026` | `TortoiseSDK._compute_reputation` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Subject)-[:performs]->(e:Event) MATCH (e)-[:NAND]->(p:Point…` |
| `tortoise/sdk.py:62389` | `TortoiseSDK._endpoint_kind_map` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.id IN $ids RETURN n.id, coalesce(n.pointKind, n…` |
| `tortoise/sdk.py:62724` | `TortoiseSDK._get_owned_entities` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Subject) WHERE s.id = $sid WITH s MATCH (s)<-[:ownedBy]-(e)…` |
| `tortoise/sdk.py:62817` | `TortoiseSDK._calibration_marker_recorded` | plain | read | candidate:SQL-shaped | `MATCH (m:Meta {key:$key}) RETURN m.recordedAt` |
| `tortoise/sdk.py:63113` | `TortoiseSDK.check_connection` | plain | read | candidate:SQL-shaped | `MATCH (n) RETURN count(n) LIMIT 1` |
| `tortoise/sdk.py:63153` | `TortoiseSDK.check_connection` | plain | read | candidate:SQL-shaped | `MATCH (k:APIKey {id:$id}) RETURN k.org_id, k.graph_id, k.scopes, k.r…` |
| `tortoise/search_engine.py:1846` | `read_currency_links` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[ef:extractedFrom]->(s:Source) WHERE n.id IN $ids RE…` |
| `tortoise/search_engine.py:1945` | `read_reference_currency_links` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Source)-[ref:references]->(n) WHERE n.id IN $ids OR n.event…` |
| `tortoise/search_engine.py:3034` | `run_fts_query` | fulltext | read | stays:text-index (§6.2; #13099 measured, decision stands) | `CALL db.idx.fulltext.queryNodes('{}', $query) YIELD node, score ???R…` |
| `tortoise/search_engine.py:3167` | `run_fts_query` | fulltext | read | stays:text-index (§6.2; #13099 measured, decision stands) | `CALL db.idx.fulltext.queryNodes('{}', $query) YIELD node, score ???R…` |
| `tortoise/search_engine.py:3582` | `run_bm25_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE {} RETURN n.{}, coalesce(n.content, n._searchText…` |
| `tortoise/search_engine.py:4036` | `run_vector_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE {} RETURN count(*), count(n.embedding)` |
| `tortoise/search_engine.py:4323` | `run_vector_query._query_nodes` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/search_engine.py:4369` | `run_vector_query._index_yields_any_node` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/search_engine.py:4621` | `run_vector_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.embedding IS NOT NULL??? WITH vecf32($query_vec…` |
| `tortoise/search_engine.py:4659` | `run_vector_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.embedding IS NOT NULL RETURN count(*)` |
| `tortoise/search_engine.py:4703` | `run_vector_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE n.embedding IS NOT NULL AND {} IS NOT NULL RETURN…` |
| `tortoise/search_engine.py:4894` | `run_structural_query` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) WHERE {} RETURN n.{} ORDER BY {} LIMIT $limit` |
| `tortoise/search_engine.py:4967` | `expand_structural_hops` | traversal | read | stays:traversal (§3) | `MATCH (seed:Point) WHERE seed.id IN $seeds WITH seed MATCH path = (s…` |
| `tortoise/search_engine.py:6256` | `annotate_ep_batch` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (n)<-[r:IMPL]-(c:P…` |
| `tortoise/search_engine.py:6616` | `get_relationships` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/search_engine.py:7035` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids WITH n MATCH (n)-[r:IMPL\|NAND\|h…` |
| `tortoise/search_engine.py:7059` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (n)-[r:CORRECTS]->…` |
| `tortoise/search_engine.py:7094` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids WITH op M…` |
| `tortoise/search_engine.py:7110` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids WITH op M…` |
| `tortoise/search_engine.py:7191` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids CALL { WI…` |
| `tortoise/search_engine.py:7221` | `get_relationships_bounded` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids WITH op M…` |
| `tortoise/search_engine.py:7621` | `subject_binding_available` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)-[r:aboutSubject]->(:Subject) RETURN count(r) AS c UN…` |
| `tortoise/search_engine.py:7672` | `fetch_point_epistemic_state` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (n)-[:aboutSubject…` |
| `tortoise/search_engine.py:7880` | `filter_by_relationship` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:{}) WHERE n.{} IN $ids{} WITH n MATCH (n)<-[r1:hasPart\|IMP…` |
| `tortoise/search_engine.py:7928` | `filter_by_traversal_predicate` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:{}) WHERE n.{} IN $ids{} WITH n MATCH (n)<-[r:hasPart\|IMPL…` |
| `tortoise/selfhost_api.py:343` | `list_points` | plain | read | candidate:SQL-shaped | `MATCH (n:Point){} WHERE ??? RETURN properties(n) ORDER BY n.createdA…` |
| `tortoise/selfhost_api.py:398` | `search` | plain | read | candidate:SQL-shaped | `MATCH (n:Point) WHERE n.is_operator = false AND toLower(n.content) C…` |
| `tortoise/session_import/parsers.py:291` | `_hermes_rows` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/session_indexer.py:272` | `_graph_entity_keywords` | plain | read | candidate:SQL-shaped | `MATCH (n) WHERE (n:Object OR n:Subject) AND n.name IS NOT NULL RETUR…` |
| `tortoise/session_indexer.py:745` | `main` | plain | read | candidate:SQL-shaped | `MATCH (e:Event {eventId: $eid}) RETURN properties(e)` |
| `tortoise/session_link.py:168` | `_resolve_targets` | plain | read | candidate:SQL-shaped | `MATCH (o:Object {id:$id}) RETURN count(o)` |
| `tortoise/session_link.py:184` | `_resolve_targets` | plain | read | candidate:SQL-shaped | `MATCH (o:Object) WHERE o.objectKind='pm:issue' AND o.name ENDS WITH …` |
| `tortoise/session_link.py:363` | `link_entity` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:{} {id:$sid})-[:{}]->(t:{} {id:$tid}) RETURN count(s)` |
| `tortoise/session_link.py:371` | `link_entity` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {id:$sid}), (t:{} {id:$tid}) MERGE (s)-[:{}]->(t) RETURN…` |
| `tortoise/session_link.py:378` | `link_entity` | adjacency | write | stays:graph-maintenance | `MATCH (s:{} {id:$sid}), (t:{} {id:$tid}) MERGE (s)-[r:{}]->(t) SET r…` |
| `tortoise/session_reinjection.py:556` | `source_session_chunk_pass` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (seed:Point) WHERE seed.id IN $seed_ids WITH seed MATCH (s:Ses…` |
| `tortoise/session_schema.py:941` | `audit_session_retrofit` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) RETURN count(s)` |
| `tortoise/session_schema.py:947` | `audit_session_retrofit` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session) WHERE s.turn_count IS NOT NULL OPTIONAL MATCH (s)-…` |
| `tortoise/session_schema.py:957` | `audit_session_retrofit` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) WHERE s.turn_count IS NOT NULL RETURN count(s)` |
| `tortoise/session_schema.py:984` | `audit_session_retrofit` | plain | read | candidate:SQL-shaped | `MATCH (s:Session) WHERE s.capture_ok = true RETURN count(s)` |
| `tortoise/session_schema.py:1000` | `audit_session_retrofit` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (s:Session) WHERE s.entity_links_created IS NOT NULL OPTIONAL …` |
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
| `tortoise/supabase_control.py:730` | `SupabaseControlPlane.query_all._read_page` | unresolved | unresolved | unclassified:statement not resolvable statically | `<unresolved>` |
| `tortoise/supabase_control.py:835` | `_registry_abuse_write` | plain | write | stays:graph-maintenance (candidate when #13083 makes the shape record_only) | `MATCH (t:Team {id: $id}) SET t.{} = $value` |
| `tortoise/supabase_control.py:858` | `_registry_abuse_read` | plain | read | candidate:SQL-shaped | `MATCH (t:Team {id: $id}) RETURN t.flagged_at, t.suspended_at` |
| `tortoise/taxonomy.py:20` | `taxonomy` | plain | read | candidate:SQL-shaped | `MATCH (n:{}) RETURN count(n)` |
| `tortoise/taxonomy.py:30` | `list_topics` | plain | read | candidate:SQL-shaped | `MATCH (n:Point {id:$id}) RETURN properties(n)` |
| `tortoise/taxonomy.py:42` | `list_topics` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})<-[r1:IMPL]-(op:Point {is_operator:true})-[r…` |
| `tortoise/taxonomy.py:49` | `list_topics` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point {id:$id})<-[r1:NAND]-(op:Point {is_operator:true})-[r…` |
| `tortoise/topic_summarization.py:191` | `_retrieve_topic_neighborhood` | plain | read | candidate:SQL-shaped | `MATCH (e:{}) WHERE toLower(coalesce(e.name, '')) CONTAINS toLower($t…` |
| `tortoise/topic_summarization.py:229` | `_retrieve_topic_neighborhood` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (p:Point)-[:{}]->(e:{} {id: $eid}) WHERE p.is_operator = false…` |
| `tortoise/topic_summarization.py:256` | `_retrieve_topic_neighborhood` | plain | read | candidate:SQL-shaped | `MATCH (p:Point) WHERE p.is_operator = false AND {} AND (toLower(coal…` |
| `tortoise/topic_summarization.py:293` | `_retrieve_topic_neighborhood` | adjacency | read | measure:across shapes (#13083 owns placement) | `MATCH (n:Point)<-[r1:IMPL\|NAND]-(op:Point {is_operator:true})-[r2:I…` |
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
