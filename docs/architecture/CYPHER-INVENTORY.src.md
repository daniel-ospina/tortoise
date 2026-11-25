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
| product surface (`tortoise/**/*.py`, test modules excluded) | **313** files scanned |
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
| non-Cypher door calls (excluded from every shape total) | 267 |
| **AST door calls** = census + non-Cypher | **1594** |

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
| every tracked surface (`--all-surface`) | **9,236** | **860** | tests, `graph-scripts/`, `archive/`, `eval/`, `docs/`. ⛔ **RE-DERIVED LAST AT THIS PR-LEVEL MERGE (`origin/main` `a7baa98d05` × `#14232`)** — `--all-surface --json` on the fully resolved tree reads **9,228 / 857 / 2,555** (sites / carrying files / scanned `.py`), against **8,889 / 772 / 2,171** measured by running the same tool in a detached worktree AT `origin/main` `a7baa98d05`: so the delta `+339` sites / `+85` carrying files / `+384` scanned files is NOT this branch's, it is main's `tests/`+`tools/` growth between the base the sentence below names and this one — re-measured, never carried, and stated as a delta of two readings rather than attributed file-by-file. A dated measurement (at THIS merged head), NOT a pin, and deliberately so: this population includes `tests/`, so it moves on any test edit and a pin on it would red an unrelated PR for a number this document does not rest on (`#13098` review P2 — it has read 8,791 / 8,908 / 8,935 / 8,939 / 8,950 / 8,964 / 8,979 / 9,008 / 8,994 / 9,055 / 9,058 / 9,077 / 9,082 / 9,097 / 9,110 / 9,132 / 9,134 / 9,150 / 9,170 / 9,186 here, and only `9,208` reproduces at this tree — the **2,549** files it scans are `main`'s **2,547** at this merge (`0467ba329d7`) plus this PR's TWO added files (`tortoise/projection_worker.py`, `tests/test_14185_projection_worker.py`), neither of which carries a site, so `files_with_sites` is **854** at BOTH trees (re-measured after the merges of `2c0d66e2ec`, `69908d006b` and `1b0db0489408` — TWO of which grew it (`2c0d66e2ec` added 2 `.py` files, `1b0db0489408` added 2) and one of which added NONE (`69908d006b`'s `.py` diff is three modifications and no addition, so it grew nothing: an earlier cut of this row said so and that qualifier was lost in a re-cut, which is why it is restored here rather than dropped); the row read 2,539 before the last of those, which is why the figure is re-derived rather than carried) — `tests/` included, which is why these numbers move on a test edit. ⛔ RE-DERIVED, not carried: an earlier revision of this row stated 9,186 / 849 / 2,533 with `main` at 2,527, and all four were stale by the time the branch merged — measured with `uv run python tools/cypher_inventory.py --all-surface --json` at this head, which is the only thing that answers it (the pin cannot: this row is deliberately unpinned). That cut's delta was **+20 sites and +5 carrying files**, enumerated rather than counted, per file with the tool's own parser: `tortoise/outside_view.py` 3, `tortoise/resident_pin.py` 3, `tests/test_14012_outside_view.py` 9, `tests/test_14012_resident_pin.py` 3 and `tests/test_14012_resident_cli.py` 2 — the sixth added file, `tools/resident.py`, carries none. ⛔ RE-MEASURED AGAIN, and the previous cut of this very sentence is the proof the re-derivation rule is load-bearing: it read `+17` / `…outside_view.py` 8 / `+11 added files` — an enumeration summing to 19 beside a claimed delta of 17 — because the sites/files pair had been re-measured while the enumeration and the scanned-file count had not. Every figure here is now `--all-surface --json`'s own output at the head that ships, per file, and `git ls-tree`'s `.py` count for the file delta (⛔ the six files marked "this lane's" above were NOT this lane's: they went to `main` in `e74cf1d13d` (#14161), which took `main` from **2,535** at `dea00903b50f` to 2,541; the merge parent `b4da1f5c01` read **2,542**, `main` had moved to **2,547** by this re-cut's merge base `0467ba329d7`, and this branch adds exactly TWO scanned files — measured by `--all-surface` at each tree, not the eleven an earlier cut claimed). ⛔ **RE-MEASURED A FOURTH TIME at this re-cut's merge (`#14222`, merged with `origin/main` `0467ba329d7`)** — the base this merge actually took, and its merge-base with this head. The previous cut named `5e83c2a2f697`, which is now one base behind: `main` added **TWO** `.py` files between them (`git diff --name-status 5e83c2a2f697 0467ba329d7 --diff-filter=A -- '*.py'` → `tools/extractor_cost_measure.py`, `tests/test_extractor_cost_measure.py`), which is exactly why the scanned-file count moved **2,545**→**2,547** while `sites` **9,208** and `files_with_sites` **854** are unchanged — neither new file carries a statement, so it joins the scanned population without joining the carrying one. Every figure below was re-derived at the base that shipped. ⛔ **The Appendix was RED on the PREVIOUS base (`70fbd1a27dd5`) — and `main` had ALREADY repaired it before this merge, so the attribution matters.** The earlier cut of this sentence named `main` as the red one, and at the base this row now names (`0467ba329d7`) that is false: measured `70fbd1a2`'s committed table against `main`'s, **400** rows differ and **378** of them are `tortoise/sdk.py` (of **386** cited sdk.py rows) — that is `main` RE-CUTTING the staleness its own `sdk.py` growth had created, and `tortoise/sdk.py` reads 62,785 lines at BOTH trees, so the 62,764 → 62,785 growth that caused it predates both. At `0467ba329d7` `main`'s table is GREEN, because `main`'s own `--markdown` re-cut shipped with it: measured `main`'s table against this branch's table **at that cut** (`0467ba329d7`'s own head, not the head you are reading), **exactly ONE** row differed — the single `tortoise/monitoring.py` row for `_probe_once._run_query`, which sat at `:4823` on `main` and moved when this branch changed that file — so this merge's regeneration repairs that ONE row, not 378, and the earlier "**379**" figure (which counted the same row against the pre-re-cut base) does not describe this head. ⛔ `--check` stays GREEN over all of it, because the pins are scalars and an insertion is not a new statement. (An earlier cut of this row named `10e51693f1`: it is an ANCESTOR of that base, **twelve** commits behind it (`git rev-list --count 10e51693f1..0467ba329d7` = 12), and the figure DID move — it reads **2,545** scanned files (`git ls-tree -r --name-only 10e51693f1 \| grep -c '\.py$'`), not 2,547, because the two `extractor_cost_measure` files postdate it — but a reader who re-derives at the newer sha re-derives at the tree that shipped, which is the only base this rule accepts.): `main` alone reads **9,208 / 854 / 2,547** and the merged tree reads **9,208 / 854 / 2,549** — sites and carrying files IDENTICAL at both trees, which is what a module that issues no Cypher must do, and the two extra scanned files are this PR's own. The number beside this row names the tree it does — and at THIS row's tree, `38f2ab7fc594` merged into a branch that merges `#14263`'s relation-PAIR leg, `--all-surface --json` reads **9,219 / 855 / 2,551** against **9,211 / 854 / 2,549** measured by running the same tool in a detached worktree AT `38f2ab7fc594` (so neither figure is arithmetic on the other); the `+8` sites, `+1` carrying file and `+2` scanned files are this branch's own, enumerated per file by the tool's parser: `tortoise/sdk.py` **1** (`_endpoint_kind_map`'s label-anchored read) and `tests/test_relation_guards_14263.py` **7** (`s._get_proj().g.query(…)`), while `tortoise/relation_guards.py` scans as a file that carries NO statement — which is why the carrying-file move is `+1` and not `+2`) ⛔ **RE-MEASURED at THIS PR (#14415), at the MERGED tree** (the round-9 head: `origin/main` `b2d1e9086a` plus the peer lane's round-8/9 fixes): `--all-surface --json` reads **9,236 / 860 / 2,564** on the merged tree against **9,233 / 859 / 2,561** measured by running the same tool in a detached worktree AT `origin/main` `b2d1e9086a` — so the `+3` sites and `+1` carrying file are this branch's own round-9 test additions, a delta of two readings rather than an attribution file-by-file, and, and the `+3` scanned files are exactly this PR's own (`tortoise/batch_ingest.py`, `tests/test_14219_batch_ingest.py`, `tests/test_14219_batches_api.py`), none of which contributes a census-VISIBLE site (the thirteen statements `batch_ingest.py` really issues are behind a door predicate this census does not match — see this PR's cut paragraph in §7, and `#14422`). The pre-merge reading on this branch was **9,228 / 857 / 2,560** against main's **9,228 / 857 / 2,557** at `f7b3e9a5a77b`; main's own +2 sites and +2 scanned files between those two bases are main's, not this branch's |

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
            (the 313 files the tool reports as `py_files`), scanned as WHOLE TEXT

door predicate   : re.compile(r"\.(?:ro_)?(?:query|execute)\s*\()")
verb predicate   : re.compile(r"\b(?:MATCH|MERGE)\s*\(", re.IGNORECASE)

1,648 occurrences of the door predicate across 112 files
2,402 occurrences of the verb predicate across 150 files   (upper-bound shape, see below)

1,648  regex door-pattern occurrences
  −54  occurrences on a line carrying NO door call (comments and docstrings)
= 1,594  occurrences on a line carrying a door CALL          [measured]
  − 0   of those, the AST did not record (residual)
= 1,594  AST door calls
= 1327  classified Cypher statements + 267 non-Cypher door calls
```

⛔ **The predicates, stated exactly as `rg_totals` runs them — and where a shell `rg` diverges.**
The population is every TRACKED `tortoise/**/*.py` except `tortoise/test_*.py` (313 files; the tool
prints it as `py_files`), scanned as WHOLE TEXT — so `\s` spans a newline, which a line-based `rg`
cannot do. Measured on this tree:

* `rg -o '\.(ro_)?(query|execute)\s*\(' <those 313 files>` → **1,648** across **112** files: exactly the
tool's number. A bare `rg … tortoise/` gives **1,654 / 113**, the 6 extra occurrences living in
the one `tortoise/test_*.py` that exists; `--glob '!tortoise/test_*.py'` reproduces 1,648 / 112 on
this tree, because
every excluded test module here is TOP LEVEL, so the glob's scope and `TEST_MODULE`'s scope coincide
— they would diverge if a nested `tortoise/**/test_*.py` ever appeared.
* `rg -io '\b(MATCH|MERGE)\s*\(' <those 313 files>` → **2,389** across **149** files, while the tool
reports 2,402 / 150. The 13-occurrence, one-file gap is the WHOLE-TEXT class — and ⚠️ it is **not**
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
predicate over the same 313 files finds exactly 13 — the verb-at-end-of-line scan alone finds **185**,
and the number is not a second version of the 13: **13** of those lines are the cross-line matches just
enumerated, and the other **172** are a COMMENT or docstring line that simply ENDS on the word (“any
failure on the read/parse/merge”, “first match”, “the old name-keyed MERGE”, `projection/__init__.py`'s
“(#13330 post-merge” that `#13399` added, …) with nothing following, which the case-insensitive
predicate counts and which is why the predicate has to be stated — `tortoise/ended.py` carries none of
them, so the count does not ride on that file — and none of the 13 is an executable Cypher literal
split by implicit
concatenation (`"… MATCH " "("` occurs nowhere in this tree): the break is prose or a wrapped Cypher
example INSIDE a docstring, which is why the tool's verb total is an upper bound.
(A bare case-sensitive `rg` gives 2,118, and `-i` without the exclusion 2,479 — ⛔ both over
`tortoise/` with **no type filter**, which is not the population this row is about and is why the
number beside it was un-reproducible before `#13098`'s review asked for the predicate. Over the 313
`.py` files the same two predicates read **2,115** and **2,389**; with `-t py` and the test-module
exclusion dropped they read **2,117** and **2,392**. The 87-occurrence 2,392→2,479 gap is entirely
`*.ts` / `*.mjs` / `*.sh` — enumerated, it is `tortoise/pi-hooks/tortoise-volunteer.test.ts` 49,
`tortoise/pi-hooks/tortoise-capture.test.ts` 32, `tortoise/pi-hooks/store-sync.test.mjs` 3,
`tortoise/pi-hooks/tortoise-capture.ts` 1, `tortoise/openclaw-plugin/index.test.mjs` 1,
`tortoise/claude-hooks/volunteer-turn.sh` 1 — so a bare `rg -o` that reads as one number is THREE, and the population decides which.)

The first three rows of the block are measured by the tool (`--json` → `rg_totals`); the **−54** and
the **= 1,594 on a door-call line** are both counts, not differences.⛔ The **residual** row is
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
| `plain` | **875** | read=589, write=286 |
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
| 387 | `tortoise/sdk.py` |
| 244 | `tortoise/hosted_api.py` |
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
| 2 | `plain` reads by id-list (`WHERE n.id IN $ids`) | same simplicity, one predicate more; needs an IN-list corpus | a large share of the 589 |
| 3 | `plain` aggregates / counts | no traversal, but a fan-out difference is easy to get wrong → needs a larger corpus and a variance check | a subset of the 589 |
| 4 | `plain` writes | **not a Cypher→SQL move** — these disappear with the node when `#13083` makes the shape `record_only` | 286 |
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

1. **Integrate before you measure** — fetch + fast-forward-merge `origin/main` into the branch,
   then re-cut the pins below in `docs/architecture/CYPHER-INVENTORY.src.md`, and re-check the prose
   tables in §0/§1/§3/§4, which quote the same numbers. ⛔ **Appendix A is NOT re-cut by
   hand**: it is appended from the tree at read time (`uv run python tools/cypher_inventory.py
   --render`), so there is nothing to regenerate and nothing to paste back.
2. **Re-run `--check` and commit the source's one-line pin update** in the same PR that added or
   removed the statement. A product PR that adds a query owns the pin update; the alternative (a
   census that only counts its own diff) is a snapshot wearing a census's name. ⛔ **The DOCUMENT is
   never committed** — `CYPHER-INVENTORY.md` is `--render`'s gitignored output, and committing it
   again is the conflict this file was split to remove (#13098).

⛔ **A cut whose ONLY effect is `files_scanned` is FOLDED into a neighbouring re-cut paragraph rather
than kept as its own heading, and that is a consolidation, not a loss.** When two cuts move the same
single operand on consecutive folds, the later one's paragraph carries the earlier one's rev and
reason in its own re-cut sentence (`#14423`'s `tortoise/drain_claim.py` is stated at `953c299c9b`'s
re-cut text, and `#14220`'s two embedder modules in the `Why 313 and not 310` clause below), because
two paragraphs whose only difference is the same `+1` on the same pin stop being a record and become
a restatement. ⚠️ So do NOT read `grep 'The cut this PR ('` as the population of main's cuts: a cut
missing from that list may be stated, re-derived, one paragraph over. A cut that moves any OTHER
pin, or any `file:line` row's payload, keeps its own heading.

**Re-cut at the merge with `origin/main` `953c299c9b`, where `files_scanned` reads `313` and NO OTHER PIN MOVED.** `main`'s `#14423` added `tortoise/drain_claim.py` (the per-spool drain claimer), which is product surface carrying no door call and no `MATCH`/`MERGE`, so it moves the scanned-file count and nothing else: `sites` **1327**, `files` **102**, `unresolved` **105**, `candidates` **589**, `partial` **321**, `non_cypher_door_calls` **266**, `ast_door_calls` **1593** and every shape and shape-role pin are unchanged. Measured, not inferred: `git ls-tree` on the COMMITTED tree reads **313** and the tool's own `--json` agrees, and `--check` prints DRIFT for `files_scanned` alone. Appendix A was regenerated with `--markdown` at the merged tree and is byte-identical to it. ⛔ The paragraph below describes an EARLIER base of this same branch and is deliberately NOT re-cut — this section's stated convention.

**The cut this branch (`feat/14219-batches`, PR #14415) makes — ONE scanned product file that carries NO census-visible statement, so exactly one pin moves; and its FIFTEEN real Cypher statements are ⛔ OUTSIDE this census, filed as `#14422` and stated here rather than implied.** `tortoise/batch_ingest.py` (`#14219` F4's bulk-ingest layer — the `:IngestBatch` / `:IngestBatchItem` bookkeeping, its per-item state machine and the drain) is product surface and the census SCANS it, so `files_scanned` 308→**309**, the same class of move `#14222`'s `projection_worker.py` and `#14284`'s `relation_guards.py` made. Every other product pin is unchanged and `--check` is green (**25 pinned counts match**): `files` **102**, `sites` **1327**, `unresolved` **105**, `candidates` **589**, `partial` **321**, `non_cypher_door_calls` **262**, `ast_door_calls` **1589**, and every shape and shape-role pin. ⛔ **That green is a false PASS for the new module, and the exclusion is the point of this paragraph:** `put_batch`, `put_batch_if_state`, `get_batch`, `list_recent_batches`, `put_item`, `put_item_if_state`, `items`, `get_item`, `batch_counts` and `find_settled_by_content_key` each issue ONE `MERGE`/`MATCH` on `:IngestBatch` / `:IngestBatchItem`, `discard` issues TWO (`MATCH … DETACH DELETE` of the item rows, then of the batch row), and `pending_batches` issues ONE statement carrying TWO — fifteen statements, every one keyed on `id` or `(batch_id, org_id)` with no traversal, no adjacency, no fulltext and no EP (the `plain` shape by §2's precedence) — but they hand them to an INJECTED callable (`GraphBatchStore._q` → `self._query(...)`, wired to `proj.g.query` in `hosted_api.py::_batch_store_for`), and §1's door predicate matches `.<query|ro_query|execute>(` only, so `self._query(` matches NOTHING and the documented **over-report** arm does not reach them either (it counts a site whose STATEMENT is unresolvable, and here the door CALL itself is invisible). `#14422` records the defect and the same blind spot on `subgraph.py` (8 more statements); ⛔ a reader who takes this module's Cypher surface from `sites` will miss all fifteen, and the enumeration above is the only place they are written down. §1's `rg` reconciliation MOVES while `sites` does not, which is the tell: the verb predicate counts sixteen occurrences (`rg -io` **2,385** across **149** files; case-sensitive **2,111**; `-t py` with the test-module exclusion dropped **2,111** and `-i` **2,386**; no type filter **2,112** and `-i` **2,468**), the tool's own whole-text verb total **2,398** over **150** files, while the door predicate stands at **1,647 / 112**. ⛔ **A VERB is not a STATEMENT, and the difference is the trap that produced this re-cut.** An earlier cut of this paragraph tallied "the nine occurrences" and landed low twice over, because two methods issue more than one verb: `discard` issues **two separate** `MATCH … DETACH DELETE` statements, and `pending_batches` issues **one** `UNION` statement that carries **two** `MATCH` clauses — and ⛔ **neither statement is cited by `file:line` here, deliberately**: this document used to, and every cite rotted on the next merge (the pair was written as `:919`/`:923`, re-cut to `:1140`/`:1144`, re-cut to `:1141`/`:1145`, and read `:1150`/`:1154` by the time the next reviewer opened it). At the head this paragraph was cut at the module reads **fifteen** statements carrying **sixteen** verb occurrences, and ⛔ neither number travels: re-take the pair at the head you ship with `grep -c 'self\._q(' tortoise/batch_ingest.py` for the statements and `rg -io '\b(MATCH|MERGE)\s*\(' tortoise/batch_ingest.py | wc -l` for the occurrences — a re-cut that follows a statement tally (or a `before→after` delta written in this document) lands one or two low, which is how the eight verb figures below §1 went stale at `80899d84eb6` and stayed stale until a fresh-context reviewer measured them. Appendix A is regenerated with `--markdown` at this branch's tree: **244 rows re-derived, all of them `tortoise/hosted_api.py`**, because this branch edits that file and every site below the edit moved (no row added, none removed — 1,329 lines either side). ⛔ **RE-CUT ONCE MORE AT THE MERGE with `origin/main` `0fbfbb5b21`, because that base had itself added a scanned file (`tortoise/capacity_notice.py`, 4 non-Cypher Postgres door calls and no Cypher site) — so the tree that ships reads `files_scanned` **310**, `non_cypher_door_calls` **266** and `ast_door_calls` **1593** (`1327` classified + `266`), and §1's door predicate **1,647 / 112** (bare `rg` over `tortoise/` **1,653 / 113**), while the verb figures are UNCHANGED by that file (**2,385 / 149** line-based, **2,398 / 150** whole-text) because it issues no `MATCH`/`MERGE`. The two files' contributions are separable rather than summed: `batch_ingest.py` moved `files_scanned` +1 and NOTHING else (no site, no door call, sixteen verbs), `capacity_notice.py` moved the door predicate +4/one file, `non_cypher_door_calls` +4 and `files_scanned` +1 — that is why `310 − 309` is two files and not one. ⛔ **RE-CUT AT THIS MERGE, on the COMMITTED tree with the index RESOLVED: `files_scanned` reads `313` — and NO OTHER PIN MOVED.** Why `313` and not `310`: `main`'s `#14186` (embedder seam batching) added `tortoise/embed_seam.py` and `tortoise/embed_worker.py`, and `main`'s later folds added further scanned product modules (`tortoise/drain_claim.py` from `#14423` among them), and this head carries all of them — `git ls-tree -r --name-only HEAD tortoise | grep '\.py$'` minus `tortoise/test_*.py` is **313**, and the tool's own `--json` says `files_scanned` **313** on that same tree, so the two agree by construction rather than by a re-read. ⚠️ A `312` read on an EARLIER tree of this branch WAS a measurement artefact, and that is the trap worth keeping: `surface_files()` enumerates through `git ls-files`, which reads the INDEX, so over an UNMERGED index a conflicted path is listed once per stage (`#13547`, this branch's own earlier merge: `sites` 1327→1815, `files_scanned` 309→314 over an unchanged tree). Both evidence sources are therefore required: `git ls-tree` on the COMMITTED tree and the tool's own reading must agree, and a `--check` run against an unresolved index is not a measurement at all. ⛔ Resolve the index FIRST, then measure. That separable-contribution reading is measured, not assumed: `--check` reported exactly ONE drift (`files_scanned`) and the tool's own output on the merged tree gave `sites` 1327, `files` 102, `non_cypher_door_calls` 266, `ast_door_calls` 1593, `partial` 321 — every one of them the pin below. Appendix A above is regenerated at THIS merged tree and is byte-identical to `--markdown`'s output at it, which `tests/test_13098_cypher_inventory.py::test_the_doc_appendix_is_the_tools_own_output` pins.

**The cut this PR (#14232, `fix/14232-ingest-ack-async`) makes — TWO new statement sites, both `plain` WRITEs, and nothing else moves.** Re-cut at this PR-level merge with `origin/main` `a7baa98d05` by RUNNING the tool on the merged tree, never by arithmetic on the markers. `tortoise/hosted_api.py::_write_capture_phase` is the phase write the ACK/complete split needs (`MATCH (s:Session {id:$sid}) SET s.capture_extraction_phase=$p, s.capture_extraction_phase_at=$at`) — a single indexed `SET` by session id, no traversal, no adjacency, no fulltext, no EP — and `_clear_capture_phase` is the phase retirement the RAISE arm of `_capture_tail_failed` needs (`MATCH (s:Session {id:$sid}) SET s.capture_extraction_phase=null, s.capture_extraction_phase_at=null`), without which a capture whose tail raised left every read surface publishing `extracting` with `outcome_known: false` for work nothing was doing. Both stay `graph-maintenance`, because they write graph state rather than a record row (`#13083`'s `record_only` shape is what would eventually move them). Measured on the merged tree against `origin/main` `a7baa98d05`'s own reading (`1325` / `plain` `873`, write `284`) by running the same tool in a detached worktree there: `sites` 1325→**1327**, `plain` 873→**875** (read **589** unchanged, write 284→**286**), and `ast_door_calls` 1587→**1589** (= 1327 + the unchanged 262 non-Cypher calls). `unresolved` **105**, `candidates` **589**, `partial` **321**, `files` **102**, `files_scanned` **308**, `adjacency` **293**, `ep` **32**, `ddl` **17**, `traversal` **3**, `fulltext` **2** and `non_cypher_door_calls` **262** are ALL unchanged — no existing statement was removed, re-classified or re-shaped, and neither new site is a candidate (the migration metric §0 is about does not move). §1's `rg` reconciliation moves with exactly the two new door calls and the two new `MATCH (`: door **1,641 / 111**→**1,643 / 111** whole-text, bare **1,649 / 112**, on call lines **1,587**→**1,589**, verb **2,379 / 149**→**2,381 / 149**; the line-based forms are re-measured there and read 2,095 / 2,368 / 2,097 / 2,371. Appendix A is regenerated with `--markdown` at this merged tree: it gains these two rows, and every `tortoise/hosted_api.py` row after them is re-derived rather than carried, because this branch also moves ~1 100 lines of that file.
**The two earlier re-cuts of this same cut are HISTORY, kept and not re-stated as the table's numbers.** Each was taken on its own tree as the site landed — `_write_capture_phase` first (`sites` 1324→1325, `plain` 872→873, write 284→285, at `origin/main` `5e83c2a2f6`), then `_clear_capture_phase` on the round-3 review fix (1325→1326, write 285→286) — and both typed `files_scanned` **306**, which is that base's figure and not this head's (**308**: `main` added two scanned `.py` files between those bases and this merge, and this branch adds none). ⛔ The pair is NOT added to the merged base's `1325`: that would double-count main's `_endpoint_kind_map` read (`candidates` 588→589, `plain` read 588→589, `partial` 320→321 — main's, measured at `a7baa98d05` and already inside its `1325`), which is exactly the arithmetic-across-parents this section forbids. The figures above are `--repin`-style readings of the tree that ships.
**The cut this branch (`chore/14263-pack-business-logic`, PR #14284) makes — ONE `plain` READ, ONE scanned file that carries no statement, and every other shape pin unchanged — re-cut by RUNNING the tool on the merged tree, never by arithmetic on the markers.** The branch adds one Cypher statement, `tortoise/sdk.py::_endpoint_kind_map`'s label-anchored endpoint sweep (`MATCH (n:{label}) WHERE n.id IN $ids RETURN n.id, labels(n)`), which is a `plain` **read** and a `candidate:SQL-shaped` lookup — §4 row 2's shape — so `sites` 1324→**1325**, `plain` 872→**873** (read 588→**589**, write **284** unchanged), `candidates` 588→**589**, `partial` 320→**321** (the label is interpolated, the `???` marker's class) and `ast_door_calls` 1586→**1587** (= 1325 + the unchanged 262 non-Cypher calls). `files_scanned` 307→**308**: `tortoise/relation_guards.py` (the verdict module — a pure function over the pack registry, issuing no Cypher and importing no store client) is product surface and the census SCANS it, which is the same class of move `#14222`'s `projection_worker.py` made; it carries no site, so `files` stays **102**. `unresolved` **105**, `adjacency` **293**, `ep` **32**, `ddl` **17**, `traversal` **3**, `fulltext` **2** and `non_cypher_door_calls` **262** are ALL unchanged — nothing here is a candidate for the record store beyond the one read, which is what §0's metric is about. §1's `rg` reconciliation moves by exactly that call and that file: door **1,640 / 111**→**1,641 / 111** whole-text, bare **1,646 / 112**→**1,647 / 112**, and the `-i` verb figure **2,377 / 149**→**2,379 / 149** — ⛔ `+2` and only ONE of them is a statement: the other is the docstring inside `_endpoint_kind_map` that QUOTES the bare `MATCH (n` form it deliberately does not issue, which is precisely the upper-bound class §1 names for this predicate. `--all-surface` is re-derived in the §1 table above (**9,219 / 855 / 2,551** on the merged tree against **9,211 / 854 / 2,549** measured in a detached worktree at `38f2ab7fc594`), and Appendix A is regenerated with `--markdown` at this merged tree. ⚠️ The merged base is `origin/main` **`38f2ab7fc594`** — eleven commits ahead of the `0467ba329d7` the `#14222` cut below names — so the pins here are the merged tree's, re-derived by `--check` (`25 pinned counts match`), not `main`'s at any earlier sha. ⛔ The paragraphs BELOW describe earlier cuts against THEIR OWN bases and are NOT re-cut, which is this section's stated convention.
**The re-cut `#14269` (`fix/main-14224-repair`) made — carried into this branch by the merge of that branch (`b49729383d`), so it is NOT "this PR's" cut: at this head it is byte-identical to `main`'s own, and the ⛔ note at the end of this paragraph is the re-measurement that says so — NO operand moves, and 378 `file:line` rows are re-derived anyway.** `--check` is green here (**25 pinned counts match**): no statement was added, removed, re-classified or re-shaped by that cut, so **at ITS base** `sites` **1324**, `files` **102**, `files_scanned` **306**, `unresolved` **105**, `non_cypher_door_calls` **262** and every shape pin were unchanged — ⛔ and `files_scanned` is the ONE operand that has moved since: the `#14222` row below is the mover that takes it 306 -> **307**, which is THIS head's figure and the one the pins in §0 carry, so the list above records that cut's base and not the head you are reading (typing 306 beside `--check is green here` was self-refuting: `--check` is green BECAUSE the pinned `files_scanned` is 307). What moved is only what the pins cannot see: `main`'s `#14260` (`f0e1ceb629`, `18:36:27`) inserted **22** lines into `tortoise/sdk.py` above 378 of its 386 statement sites (one hunk at `@@ -5713,8 +5713,29 @@`, so the EIGHT sites above it — `:3132`, `:3474`, `:3738`, `:4049`, `:4066`, `:4399`, `:4423`, `:5111` — did not move) and did not re-cut this appendix — while **eleven seconds earlier** `#14262` (`8e4ffedc2e`, `18:36:16`) HAD re-cut it, so the second landing rotted the first's table. ⛔ `main` at `70fbd1a27d` is RED on `tests/test_13098_cypher_inventory.py::test_the_doc_appendix_is_the_tools_own_output` (`rows: doc=1326 tool=1326; 378 row(s) differ`), and every row of that difference is `tortoise/sdk.py`: this re-cut is `--markdown`'s own output at this tree — **378 rows re-derived, 378 insertions / 378 deletions in Appendix A** (`git diff --numstat origin/main...HEAD -- docs/architecture/CYPHER-INVENTORY.md` printed **`381 379`** at the head this paragraph was written for, when `origin/main` was `70fbd1a27d` — Appendix A's 378/378 plus the three non-Appendix lines measured then: this paragraph's two, and the `@@ -66,7 +66,7 @@` re-anchor of §1's `--all-surface` row. ⛔ Two earlier revisions of this clause typed a CURRENT-head pair — `380 378` when the §1 line was still missing from it, and `3 1` when the `#14012` line below was still this branch's rewrite — and each was true at its own head and false at the next; that is this paragraph's own rule, re-TAKE never carry, being applied to the sentence that states it). ⛔ **NO figure is typed for this head's diff, deliberately**: a numstat is head-bound and rots exactly as those two did, so reproduce it at the head you merge: render the base and the head (`--render /tmp/base.md` in a worktree at the base, `--render /tmp/head.md` here) and diff the two Appendix A tables. ⛔ This sentence used to send the reader to `git diff --numstat origin/main HEAD -- docs/architecture/CYPHER-INVENTORY.md`, and after #13098's split that command is wrong in BOTH directions: on `main` as it stands the path is still TRACKED, so at any branch head before this split lands it prints a whole-file DELETION (`0` added, every line of the base removed — ⛔ the removed count is the BASE's line total, so no number is typed here) and a reader takes that for "the appendix was emptied"; once the split has landed the path is gitignored and absent from BOTH trees, so the same command prints NOTHING and reads as "no Appendix change" whatever moved. Two opposite false passes in prose, which is the class this file exists to refuse —  render both trees and diff THOSE artifacts instead. At this one, `main`'s own `#14258` (`fa4d1c8065`, `379`/`379` — Appendix A's `378`/`378` plus `1`/`1` on §1's `--all-surface` row) and the `#14012` line's re-cut to `main`'s bytes have both left the diff, and what remains is this paragraph and the blank line that ends it — no §1 hunk and no separate rewrite. ⛔ WHICH commit removed each: `main`'s own `8fa0662249` (`#14222`) made the same correction to that `#14012` line — at `fa4d1c8065` (`#14258`) it still read `2,376 / 149` and `2,435`, and `8fa0662249` reads `2,377 / 149` and `2,436`, byte-identical to this branch's paragraph — so that hunk left the diff by `main`'s bytes and not by a rewrite here, and `#14258`'s re-cut of Appendix A is what left no Appendix hunk. Measured against `origin/main` `588cf7aeb61f`. The red it records was real at `70fbd1a27d` and is repaired on `main` too — which repairs `main`'s red as well. ⚠️ **The twelve `search_engine.py` rows that were ALSO stale on this PR's previous head (`38c7001725`, where they read `annotate_ep_batch` at `:6141` and `subject_binding_available` at `:7217`) are ALREADY correct here** — `#14262`'s re-cut fixed them (`:6233` and `:7598`, the addresses this head carries) and this branch adopted that on the rebase onto `70fbd1a27d`, so no second edit was owed for them. The lesson is the one the pins keep teaching: two landings eleven seconds apart still owe two re-cuts, and only the byte comparison can say so.

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

**The cut this PR (#14552, `feat/14552-point-writers`) makes — ONE non-Cypher door call and NOT a
single Cypher statement — re-derived by RUNNING the tool at the tree merged with `origin/main`
`6a1190f5590`, never by arithmetic on the markers.** `tortoise/record_read.py::point_vector_keys` (the
per-id record ask the `:Point` write gate needed) issues
`cursor.execute(f"SELECT node_key FROM public.{POINT_TABLE} …")` — a statement built by an f-string,
which the census's static resolver folds to `non_cypher_door_calls` (`SQL_VETO`, the same arm
`#14002`'s presence read landed in). So `non_cypher_door_calls` 266→**267** and `ast_door_calls`
1593→**1594** (= `1327` classified + `267`), and ⛔ **every other pin is unchanged, because no graph
statement was added, removed, re-classified or re-shaped**: `sites` **1327**, `files` **102**,
`files_with_sites` **102**, `files_scanned` **313**, `partial` **321**, `unresolved` **105**,
`candidates` **589**, and every shape and shape-role pin — which is why the migration metric §0 is
about does not move.

⚠️ **Two things the counts do NOT show, stated rather than left to the reader.** (1) `record_read.py` carries NO statement site — it is not one of the 102 in Appendix A — and a
non-Cypher door call never becomes a site, so `files` and `files_with_sites` cannot move however many
door calls that file gains; that is why they are unmoved while the door-call pin is not. (2) Appendix A's ROW SET is
unchanged (1327 rows, none added, none removed), but **534 of them moved positionally** because this
PR edits, above sites those files carry, `tortoise/sdk.py` (**386** rows),
`tortoise/projection/__init__.py` (**93**), `tortoise/projection/entities.py` (**54**) and
`tortoise/record_vector.py` (**1**). ⛔ THE MECHANISM IS THE CUMULATIVE INSERTION ABOVE THE ROW, NOT
the file's net line delta — a row moves by the number of lines inserted above IT between
`6a1190f5590` and this head, so only the rows below every insertion move by the net delta. Measured
per file, and stated in full because §7's own paragraph 30 lines above says the same thing: sdk.py
**+40** for 384 of its 386 moved rows (**+31** for 2 that sit above a later insertion, **1** unmoved)
against a net **+40**; `projection/__init__.py` **+124** for 65 rows, **+55** for 20, **+54** for 8,
net **+124**; `entities.py` **+23** for all 54, net **+23**; `record_vector.py` **+8** for its 1, net
**+8** — 386 + 93 + 54 + 1 = **534**. ⛔ The checkable form is the RULE, not these buckets — and the command has to be one that
carries hunk POSITIONS: `git diff -U0 6a1190f5590 HEAD -- tortoise/<file> | grep '^@@'` gives each
hunk's `-a,b +c,d` pair, whose cumulative `+` total ABOVE a row is that row's shift (measured: this
reproduces 384/2/1 at +40/+31/+0 for `sdk.py`, and each of the other files' buckets
(`projection/__init__.py`'s three included)). ⚠️
`git diff --numstat 6a1190f5590 HEAD -- tortoise/` CANNOT answer it: it prints one `added deleted
path` line per FILE — so it confirms the four NETs (`+40`, `+124`, `+23`, `+8`) and says nothing
about where the insertions sit, which is exactly the distinction this paragraph exists to draw. The table is regenerated with `--markdown` at this tree and is byte-identical to the tool's output, which
`tests/test_13098_cypher_inventory.py::test_the_doc_appendix_is_the_tools_own_output` pins. (3) FOUR COUNT SURFACES BELOW §0 WERE ALREADY STALE AT THE MERGE BASE, and are re-cut to the
tool here because this PR is the cut that touches the file: §3's `plain` shape row read **872**
(read=588, write=284) against the tool's **875** (589/286) — a table that summed to 1324 against the
pinned `sites` 1327 — §2's blast-radius table listed `tortoise/sdk.py` at **386** (now 387) and
`tortoise/hosted_api.py` at **242** (now 244), and §4 — which derives its sizes from §3 — repeated
the pre-cut **588** twice and **284** once. None of the four is pinned by `--check`, which reads
only §0's scalars and the HTML-comment markers, so no arm had re-derived them since the cuts §7 below
records; they are fixed here rather than left as a contradiction a reader can compute without the
tool. (§1's `--all-surface` row is deliberately NOT in this set: it states the tree it was measured
at and moves on any test edit.) ⛔ The
`differential` job is the arm that caught this cut as missing: it reported
`DRIFT ast_door_calls: doc=1593 tool=1594` / `DRIFT non_cypher_door_calls: doc=266 tool=267` on the
pushed head, which is exactly the "a pin green at a branch head is red at the merge" case §7 names —
here the branch itself added the call.

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
<!-- cypher-inventory:files_scanned = 313 -->
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
<!-- cypher-inventory:non_cypher_door_calls = 267 -->
<!-- cypher-inventory:ast_door_calls = 1594 -->
<!-- cypher-inventory:partial = 321 -->

---

<!-- ⛔ THE ROWS BELOW ARE GENERATED, NEVER AUTHORED. -->
<!-- `tools/cypher_inventory.py --render` writes this document: the tracked source
     `docs/architecture/CYPHER-INVENTORY.src.md` (prose + the 25 pins, which is what
     `--check` reads) plus Appendix A's table, appended from the live tree. Never
     hand-edit the rows, and never paste them back into the source — both `--render`
     and `--check` REFUSE a source that carries a census row. -->

## Appendix A — every statement site, by shape
