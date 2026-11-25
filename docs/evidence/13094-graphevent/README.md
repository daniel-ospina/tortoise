---
title: "#13094 — the change feed's reader census, its measured residency, and why the move is a store re-home"
type: evidence
domain: platform
doc_status: draft
created: 2026-11-21
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#13094, #13083, #13082, #12894, #4664, #9025"
ownedBy: epistemic-team
---

# `:GraphEvent` — the reader census, the measured residency, and why the move is a STORE re-home (#13094)

This is `#13094`'s answer to the question its own body says must be answered **first**, and the
measurement `#13083` explicitly left UNMEASURED. It is written so the next lane starts from a
measurement instead of re-deriving one — which is the whole cost `#13094` has carried since the
census merged as `#14043`.

**Status, stated plainly: this PR (#14164) moves no bytes.** The shape is still written to the graph, and
the verdict below says why the *prescribed* mechanism cannot move it, what the actual route is, and what
it is worth. The move itself is **the remaining scope of `#13094`**, recorded on the issue
(`#13094` comment 7065026251) rather than filed as a peer ticket — the root is there, so the route stays
there. It is a store re-home (§7): a second durable store with cursor semantics, not a tier flip, and
shipping half of it would put the change feed's durability at risk for the class of defect this issue
exists to remove.

Everything here is a measurement or an executed assertion. Numbers carry the command that produced
them; claims carry the `file:line` that refutes them if wrong.

---

## 1. The reader census — every statement, classified

Produced by `uv run python tools/graphevent_home.py readers` (`readers.json` beside this file).
**16 rows — one per censused statement SITE —** in `tortoise/` read or maintain the `:GraphEvent`
label — including the three `ensure_event_schema` DDL statements, because a re-home has to recreate the
schema they describe, and the two whole-graph scans in the restore path, which carry the label without
naming it (that scan's text occurs four times in `hosted_backup.py`; the two rows pin the exact count in
`occurrences`, which covers the live read in `_restore_into_temp_verify_swap`).

| # | reader | `file:line` (this head) | what it needs | class |
|---|---|---|---|---|
| 1 | `read_after` — the ONLY row-producing read | `tortoise/event_store.py:992` | ordered range scan on `seq`, optional type filter, `LIMIT`, app-side dedupe | **serving** |
| 2 | `append_event` dedupe pre-check | `event_store.py:801` | key existence on `event_id` | maintenance |
| 3 | `append_event` — the write | `event_store.py:816` | an insert | maintenance |
| 4 | `ensure_event_schema` — `event_id` index | `event_store.py:410` | DDL: an exact-match index | maintenance |
| 5 | `ensure_event_schema` — unique constraint | `event_store.py:415` | DDL: uniqueness on `event_id` | maintenance |
| 6 | `ensure_event_schema` — `seq` index | `event_store.py:421` | DDL: a range index | maintenance |
| 7 | `_event_row_exists` (`#9208` loss discriminator) | `event_store.py:879` | key existence | maintenance |
| 8 | `purge_expired` | `event_store.py:1029` | predicate delete | maintenance |
| 9 | `purge_overflow` (count) | `event_store.py:1043` | whole-label count | maintenance |
| 10 | `purge_overflow` (delete) | `event_store.py:1053` | ordered, limited delete | maintenance |
| 11 | `capture_watermark` | `event_store.py:645` | aggregate `max, min` | maintenance |
| 12 | `_refresh_first_seq` | `event_store.py:1104` | aggregate `min` inside an RMW on the **sibling** label | maintenance |
| 13 | `webhook_delivery._head_seq` fallback | `webhook_delivery.py:1049` | aggregate `max` | maintenance |
| 14 | backup footprint | `hosted_backup.py:892` | aggregate `max, min` | maintenance |
| 15 | whole-graph scan (restore's dirty-target pre-check) | `hosted_backup.py:1382` | a label-agnostic read of the target graph | maintenance |
| 16 | whole-graph scan (restore's node-count verification) | `hosted_backup.py:1583` | a label-agnostic read of the graph | maintenance |

⚠️ **Rows 15–16 are re-derived by CONTENT here, not by shifting the census's addresses.** As merged,
`#13083` cited `:1182`/`:1383` for them; at this head they are `:1382`/`:1583` — a cumulative `+200`,
in FOUR insertion steps rather than one: `+2` (two import lines after the pin's `:58`/`:59`), then
`+60` after `:1035` (`@@ -1035,0 +1038,60 @@`) and `+89` after `:1175` (`@@ -1175,0 +1238,89 @@`)
— cumulative `+151`, which is where `:1333`/`:1534` came from — and then `+49` more, inserted inside
`_restore_retires_vector` before both statements by `#14596` (`3bbe5567df24a`), which is why an address
taken from any earlier head is short by exactly that much.

**Verdict: `record_only`-shaped.** Not one reader needs a graph capability — no traversal, no adjacency
walk, no fulltext query, no EP walk, no vector query. Every one is a key lookup, an ordered range scan,
an aggregate, a predicate delete, or the schema those depend on; those are SQL shapes paying RAM prices.
The **serving** row is exactly one statement *in this table* — so a re-home re-points one censused read
and not a family of them. ⚠️ That is a fact about the TABLE, not about the tree: the export endpoint's
own whole-graph scan (`hosted_api.py:36016`) is a serving reader under this census's own class rule and
is deliberately **not censused** (`#14204`; the exclusion is stated in §7 step 4 and in the restore row's
own `why`), so a re-point driven by this census alone would leave the export endpoint reading the old
home. A re-home must re-point it too.

**Classification, made checkable** rather than asserted in a document:

* `tests/test_13094_graphevent_home.py::test_every_declared_reader_still_names_its_statement_in_the_tree`
  — every row's statements must still resolve **in its file** (content-matched, whitespace-collapsed, so
  a re-indent is not a false red while a reword is a real one), and `min_occurrences` is asserted where
  one file holds the same statement twice. ⛔ **Where that is a proof and where it is not**: the
  `event_store.py` dedupe pair is the ONLY two occurrences of its text in that file, so deleting one reds
  the census; the two `hosted_backup.py` restore-side scans share their text with four occurrences in
  that file (one more live read at `:3652`, plus a docstring mention at `:3491`), so those rows pin the
  EXACT count — deleting any of the four reds (`occurrences`), which a `min_occurrences` floor did not
  do: measured, deleting BOTH censused sites left 2 and stayed green. What the pin still cannot do is
  say WHICH occurrence a row addresses — the `line` is that address and nothing grades it. ⚠️ Both of
  those rows are in `restore_graph`; **the export side is not censused at all** — the paged dump's node
  statement is `_DUMP_NODE_PAGE_QUERY` (`hosted_backup.py:454`) and the export endpoint's own
  whole-graph scan is `hosted_api.py:36016` (`_export_graph_snapshot`; the `:GraphEvent` branch is
  `:36046` and the append `:36054`), and no row carries either (`#14204`).
* `…::test_the_census_covers_the_labels_schema_as_well_as_its_readers` — the three DDL rows exist and are
  `maintenance`; an earlier cut of this census omitted them, which is how a re-home ships a store with no
  indexes.
* `…::test_no_reader_of_the_change_feed_needs_a_graph_capability` — **no** row may need a graph
  capability, in its `needs`/`why` prose **or in its own Cypher**. A future reader that genuinely needs
  one reds the census instead of being filed as `maintenance`; the Cypher arm is what makes that true,
  because a capability lives in the query rather than in the sentence describing it — and the pin keeps
  the prose-only case as an executed control, so a predicate that stops reading snippets reddens.
* `…::test_the_census_has_exactly_one_row_producing_reader` — a second producing reader changes the shape
  of the re-home, so the count is pinned.

⚠️ **What the census does NOT claim.** It is not an extractor: nothing strips every `GraphEvent`
occurrence out of the tree and compares itself to this table, so a NEW statement added tomorrow is
invisible until someone adds a row (the class `#13113` filed an extractor for). What it does guarantee is
the other direction — every declared row resolves, and a reworded or deleted statement goes red.

---

## 2. The measured residency — what the shape costs, in bytes

`#13083` is explicit that its own residency figure is **unmeasured** (that census document, which
carried this as its §5.1, was deleted on 2026-11-23 — `#11081` audit row B2): the payload is exact
(12,768 B over 67 `:GraphEvent` nodes ≈ **190 B/event**) and the RAM figure is run-dependent drift it
refused to quote. So it is measured here, against a real FalkorDB, with production's schema INTENT —
the two `CREATE INDEX` statements plus the `event_id` unique constraint that production's own path
never manages to create (see `measure_graphevent_residency.py`'s docstring and `#14212`,
which carries the measurement). ⛔ **BUT THE TWO SLOPES
QUOTED BELOW WERE MEASURED WITHOUT THAT CONSTRAINT**, and this is the sentence that has to say so
rather than leave it to a footnote: both committed JSONs record it REFUSED
(`schema[1].installed == false` — the constraint is the SECOND of the three statements, and `schema`
index 1 in both JSONs — the graph-key-slot defect the instrument names), so those slopes are
a **LOWER BOUND** — a graph without that constraint's index understates the cost, never overstates it
(the two plain `CREATE INDEX` statements WERE installed; only the constraint was refused) — and re-running the
instrument (it issues the constraint the module way, the only form that runs) is what would make them
a coverage claim about production's intent. ⚠️ Each JSON carries that reading as a `schema_note`, so a
reader who meets the recorded `refused` line in the artifact FIRST cannot take it for a server limit.

Instrument: `measure_graphevent_residency.py` (this directory), run at head `05d218fcaf` (recorded as
`tree.git_head` in both JSONs). Outputs: `measurement-payload88.json`, `measurement-payload190.json`.

**How it measures.** `INFO memory` `used_memory` at three populations (20k / 40k / 60k rows), and the
**marginal slope** between the last two. The slope is the quoted number: the graph's fixed cost (the two
`CREATE INDEX` statements, and the `event_id` unique constraint the script also issues — the same
three statements `event_store.ensure_event_schema` WRITES, though production's `g.query()` cannot parse
the constraint's module command, so **every slope quoted below is WITHOUT it**: neither committed JSON
installed the constraint, and the instrument's own docstring says so) is paid once and would otherwise be divided
into every node and reported as if it recurred.

| synthetic payload | slope | `GRAPH.MEMORY USAGE` at 60k rows |
|---|---|---|
| 88 B (the `PointAdded`-with-actor shape, unpadded) | **557.4 B/node** | 23 MB total, 8 MB indexes, 14 MB node attributes |
| 190 B (the `#13083` census's fixed-corpus measure: 67 `:GraphEvent` nodes, 12,768 B) | **597.2 B/node** | 19 MB node attributes, 8 MB indexes |

**Projected onto the shape that matters** — the label census's **72,488** `:GraphEvent` nodes
(`#13880`'s 2026-11-19 label census):

```text
72,488 × 597.2 B = 43.3 MB        (190 B payload, the census's fixed-corpus shape)
72,488 × 557.4 B = 40.4 MB        (88 B payload, the floor of the two)
```

**40–43 MB of resident RAM, on one tenant, for a shape no graph query reads.** Against `#13082`'s
own economics (~584× RAM/disk, ~78 MB solo budget) that is **half a solo tenant's entire memory
allowance**, which is why this is the census's largest single item.

### 2.1 The share — corrected, because the briefing's figure is arithmetically impossible

⚠️ **The share, and whose denominator it is.** The `#13094` dispatch brief states "72,488 nodes of
118,755 — **42 %** of the store", and 42 % cannot be derived from those two numbers: 72,488 / 118,755 =
**0.6104, i.e. 61 %**. Both figures are the BRIEF's, not the tree's, so the correction is arithmetic on
the brief's own pair, and it is stated that way rather than dressed up as a measurement. The
provenance of each number is below, once.

⛔ **And the tree's own recorded share is a DIFFERENT number from a different census, which is why none
of this is quoted as "the" share.** `graph-scripts/business_map_13880/business_map.json` records
**76,610 of 126,908 = 60 %** from a live Falkor read on 2026-11-20, and it carries the rule against
mixing the two (*"No share is derived from mixing them … only the second states a denominator"*): the
72,488 is a label census over the graph **exports** (2026-11-19) with no denominator recorded here. So
the projection below uses the node COUNT from that census and multiplies it by the measured slope — it
never divides by a node total — and 42 % is corrected to 61 % only as arithmetic on the brief's pair.

⚠️ **Provenance of the two numbers in that ratio — they are not the same artifact.** `72,488` is
committed and traceable: `#13880`'s 2026-11-19 label census, recorded in
`graph-scripts/business_map_13880/`. **`118,755` appears in no committed artifact other than this
disclosure** — its only source is the `#13094` dispatch brief itself, and the export label census the
72,488 comes from *states no denominator* (`business_map.json`: "only the second states a
denominator"). So the 61 % is arithmetic
over the brief's pair, stated because the brief's own percentage is wrong, **not** a figure reproducible
from a committed artifact. Nothing downstream depends on it — the projection is 72,488 × the slope, not
a share — so it is disclosed rather than leaned on.

⛔ **Do not mix the two censuses**, which is why the share is stated twice with its own denominator:
`#13880`'s **label census over the graph EXPORTS** (2026-11-19) counts **72,488**; a **live Falkor read a
day later** (2026-11-20) counts **76,610 of 126,908** = **60 %**. `graph-scripts/business_map_13880/
business_map.json` records both and the rule (*"No share is derived from mixing them … only the second
states a denominator"*). The projection above uses the label census's count (72,488) against the slope,
never against a denominator taken from the other surface.

### 2.2 What this measurement is NOT

It is a synthetic population on a throwaway graph, not a production reading; `used_memory` is
process-wide, so a tenant graph cannot be attributed per label (`graphevent_home.py residency` says so in
its own output rather than quoting a number it cannot attribute). The payload is uniform, where real
payloads vary; the slope was measured at two payload widths precisely so a reader can see it is not
payload-independent.

⚠️ **The quoted 190 B slope is the LOWEST estimate its own committed JSON supports, and that is
stated rather than smoothed.** The 88 B run is flat — 553.0 / 553.6 / 554.8 B/node at 20k / 40k / 60k,
so its marginal 557.4 is uncontroversial. The 190 B run is **not monotone**: 653.1 / 679.7 / 652.2
B/node over the schema cost, so the 20k→40k interval is 706.3 B/node and the quoted last interval
597.2 — the smaller of the two. The fixed cost §2 blames cannot account for the 55 B/node gap between
that quoted marginal and the 60k level: `schema_bytes` is 114,608 B, **1.9 B/node** at 60k. Read at the
60k *level* instead, the projection is **≈ 47 MB** rather than 43.3 MB — about 9 % higher.
**The verdict does not turn on which is quoted** — both sit near half a solo tenant's budget — so the
range is given and the repeat is follow-up on `#13094` rather than resolved by re-arguing the outlier.

---

## 3. Is `public.journal_event` already the home? **No — and not for a missing-column reason**

`#13094`'s body entertains the cheap answer: *"a record home very likely exists already … the move
may be a feed re-pointing."* The `#13083` census (deleted 2026-11-23, `#11081` audit row B2;
its text is in git history) refused to assume it and named the question. Here is the
answer, with the refutations executed rather than argued:

1. **A payload-only emit writes NO journal record at all.** `_emit_event` returns before the JSONL
   half when it carries no `point`, no `id`, and no `turns` (`sdk.py:16259`, the `return None` under the
   comment at `:16257`, contract restated at `event_store.py:58` in its module docstring and again as a
   comment at the append site (`:808`): *"a payload-only emit writes one with no JSONL record
   at all"*). This is not hypothetical: the turn hard-delete lane calls exactly that shape —
   `sdk._emit_event("PointRetracted", _deleted_point_payload(tid))` (`sdk.py:3831`) — and the
   surrounding comment states the split as a contract, *"Two stores, one meaning each"*.
   **Executed pin:** `tests/test_13094_graphevent_home.py::
   test_a_payload_only_emit_writes_the_store_and_no_journal_record` emits one, asserts the store holds
   `PointRetracted` while the journal holds **zero** rows, and then emits a POINT-carrying event as a
   positive control (it must reach the journal) so the empty half cannot pass for a fixture that never
   bound a journal.
2. **The two stores' `seq`s are different coordinates, deliberately.** `:GraphEvent.seq` comes from
   the change feed's own allocator (`event_store.next_seq` → `:GraphEventMeta.last_seq`);
   `journal_event.seq` is the REPLAY journal's ordinal, allocated by `record_allocator` (`#12894`) —
   and that migration's header forbids exactly this conflation: *"It does NOT authorise
   `:GraphEvent` to change coordinate (issued cursors are `:GraphEvent.seq`)"*
   (`supabase/migrations/20261116000001_record_cursor_allocator.sql:13`). Pinned by
   `test_the_two_stores_seq_coordinates_are_deliberately_different`.
3. **The event vocabularies differ.** `journal_event` has no `event_id` column at all — the store's
   dedupe key, the unique constraint and `read_after`'s app-side dedupe all rest on it
   (`event_store.py:415`, `:801`, `:997-1000`) — and its `event_type` vocabulary is the journal's
   (`EntityMutated` for a hard delete), not the store's 11 `_GRAPH_EVENT_TYPES`. A re-point would
   need a new column AND a vocabulary mapping, i.e. it is already not "a re-pointing".

The verb that answers this on a **real tenant**, per event type and by payload multiset, is
`tools/graphevent_home.py superset` — read-only, and it refuses to mint the graph it is asked about
(see §3.1). Exit `1` = not covered, exit `2` = **no verdict** (never "covered") — for an empty feed on
**either** side (an empty graph is the purged/mistyped case; an empty RECORD is the mistyped
`--org-id`/`--graph-id` or never-armed-scope case, and reporting that as "the record is short" would send
an operator to backfill a record that was never asked), and for a feed the cursor could **not prove it
reached the end of** — the walk ends with a `count()` comparison and a NULL-`seq` check, because three
shapes the store admits (a NULL `seq`, a `seq` stored as a string, a duplicated `(seq, event_id)` pair
past a page boundary) make an empty page indistinguishable from the end.

⚠️ **The count comparison also refuses on CONCURRENT MUTATION, and that is disclosed rather than
surprising.** The total is counted AFTER the walk, so a row appended in the tail window, or any row
DELETED after being read (retention purges delete `:GraphEvent`), yields `total != len(rows)` and exit
`2`. The direction is right — a feed that changed under the read is not something to certify — but it
means "no verdict" here can mean *"the feed moved while I read it"*, not *"the feed is broken"*.
Re-run before concluding anything about the tenant.

⚠️ Its own verdict carries the limit that matters: **even at `covered`, the
producers disagree** (refutation 1 is a property of the producer, not of any tenant's history), so a
`covered` tenant is not evidence that a re-point would not drop future events. And a type the record
carries under another name (`PointRetracted` / `EntityMutated`) is reported as a **named deficit**
(`mapped_types`, `row_coverage: not_comparable`) and makes the verdict `not_covered` — because "we cannot
compare it" is not "it is covered".

### 3.1 READ-ONLY is a mechanism here, not a promise

A Cypher read against a graph the server does not hold **creates** it — measured in this repo
(`tortoise/projection/__init__.py`: a plain `MATCH (n) RETURN 1 LIMIT 1` "ADDED it to `GRAPH.LIST`",
`#11113`), and reproduced while building this bundle. So both verbs require an **explicit** `--graph`
(there is no default: defaulting to `tortoise` would mint an empty, sweep-unowned graph on every
multi-tenant server) and refuse a name `GRAPH.LIST` does not carry — **before** binding it. The refusal
lives in the ONE reader both verbs call (`_graph_rows`), not in a verb: round 1 put it in `residency`
only, so `superset` — the verb whose verdict gates a re-home — was the one that could mint a graph.
`residency` ALSO carries a verb-level guard, and it is not redundant: its `MATCH (n)` totals read runs
**before** `_graph_rows`, so without that guard the totals read would mint the graph and `_graph_rows`
would then find "present" the empty graph it had just created. Pinned at the reader, at `superset` end
to end, and at `residency`'s own totals read
(`test_a_graph_the_server_does_not_hold_is_refused_before_it_is_read`,
`test_the_superset_verb_refuses_an_unknown_graph_too`,
`test_the_residency_verb_refuses_an_unknown_graph_before_reading_totals`).

---

## 4. Why the prescribed mechanism cannot express this move — and the `#4664` hazard

The `#13083` census prescribed: *"give it a `NODE_TABLES` record home with a tier column, then
tier it."* (That prescription was the deleted document's, not something the issue carries.)
That route is **wrong for this shape**, and the reason is structural, not administrative:

> `cache_projection.read_record_view` builds `SELECT * FROM public.{NODE_TABLES[label]}` for **every**
> label it is asked for (`cache_projection.py:3823-3845`), with no per-label special case. A record
> view is what `rebuild_from_record` materialises **into the graph**. So adding `:GraphEvent` to
> `NODE_TABLES` puts the shape on the PROJECTION path — while `#4664` establishes the opposite
> invariant: *a rebuild does NOT replay `:GraphEvent` rows; it comes back EMPTY and carries only the
> watermark* (`event_store.py` module docstring; pinned by
> `tests/test_rebuild_event_meta_watermark_4653.py:133`, which asserts the store is empty after
> `rebuild_all`).

**What is EXECUTED, and what is named.** The test patches `NODE_TABLES` + `KEY_PROPERTY` and shows two
things: the reader reaches straight for `public.journal_event` (the label-agnostic projection path — the
reason the route is on the wrong path at all), and then it REFUSES the row with
`UnknownRecordColumn`, because `_props_from_row` will not turn an unclassified column into a node
property (`cache_projection.py:2866`). So two locks are executed; the third — the record's **key
contract** (`node_identity`'s `CHECK (node_label IN (…))` excludes `GraphEvent` at
`20261012000001_record_event_stream.sql:55`; `KEY_PROPERTY` has no entry; `seq` is a bigint rather than
the non-empty **string** key a record node needs, and `event_id` is not a `journal_event` column) — is
named rather than executed, because reaching it needs the column classification this test proves is
missing.

⛔ **The honest consequence: the tier route needs three new declarations** (a table mapping, a placement
classification, a key contract), and `#4664` is why it must not get them: with all three, a
record-sourced rebuild would project the feed back into the graph. With only the first, a rebuild **fails
loudly** at the unclassified column rather than resurrecting the shape — which is the current, safe
state, and not a licence to add the other two.

**The correct mechanism is a STORE re-home**, which is a different thing from a tier: `:GraphEvent`
is not a projection of the record, it is a second durable store whose rows no replay reproduces.
That is why the `#13083` census says the work is *"prove `journal_event` is a superset — and if it is
not, add what is missing"*, and why this PR stops at "it is not, and here is what the real route
costs".

---

## 5. Replay proof — what a re-home must not break, and what replay does today

Replay works **because it never touches this shape**, and the proof is executable today:

* `tests/test_rebuild_event_meta_watermark_4653.py::test_rebuild_all_rederives_event_meta_watermark`
  — a real `rebuild_all` over three real emits. After the wipe the assertions are
  `_seqs(sdk) == []` (*"the rebuild does NOT replay `:GraphEvent` rows"*) and
  `_meta(sdk) == (3, 4)` — the watermark is **carried**, and `next_seq` continues above every seq
  already handed out. That is the whole replay contract for this shape: the ROWS are not replayed,
  the COORDINATE is carried.
* Therefore any re-home must keep two properties, and neither depends on where the rows live:
  1. `:GraphEventMeta` (the allocator/`first_seq` floor) stays where it is — the coordinate must
     survive a wipe (`capture_watermark`/`reestablish_watermark`), and moving 1 node buys nothing;
  2. `journal_event` is never written by the feed and never deleted (`#9025`, and the append-only
     trigger at `20261012000001_record_event_stream.sql:96-111`). A re-home writes a NEW table or
     extends the journal's schema — it never purges the journal, which is the only carrier of
     `Object.status`/`Subject.status`.

Both suites are re-run after this PR's last push; counts are in `WORKTREE-REPORT.md`.

---

## 6. What this PR changes

| file | what it is |
|---|---|
| `tools/graphevent_home.py` | the census as data (`READERS`), the residency reader, and the **`superset` verb** that answers `#13094`'s blocking question on a real tenant — read-only by mechanism (§3.1), with exit `2` for "no verdict" |
| `tests/test_13094_graphevent_home.py` | 40 pins: the census resolving (with `min_occurrences`), its DDL completeness, the unique-constraint row naming the dedupe layer that actually holds (with `event_store.py`'s own bytes as the arm, so a lane that fixes `#14212` the module way must re-derive the row), the capability vocabulary, the capability-arm control table as a SPECIFICATION (a LITERAL expected arm per shape — three RE-SPACED and two BACKTICK-QUOTED spellings so `_NO_WS` is discriminating on both bypasses, `apoc.` / `call algo` / both `db.idx.*` procedures each pinned by their own row, and every arm that a first-match ordering does NOT exempt (`*1..`, `*..`, `)<-`, `shortestpath`) — plus a completeness pin that every arm in the tool is either pinned or declared undecidable, and that the two sets are a PARTITION), the two producer refutations (executed, with a positive control), the `#4664` mechanism refusal (executed), the verdict's three deficit classes (missing type, missing row, mapped type), the empty-feed vacuity on BOTH sides, the paging cursor against duplicate and fractional `seq` (with two rows sharing `(seq, type, payload)` so the `event_id` half of the dedupe key is discriminating), the completeness counts (NULL `seq`, a short read, and a PRESENT non-string `event_id` — the arm that reds if the predicate is relaxed to `is None`), the graph-minting refusal at the reader, at `superset`, and at `residency`'s own totals read (including that the graph is refused BEFORE the journal is paged), which side the `no_verdict` message names, a capability-bearing reader making the derived census verdict say so, the exit-code contract, the measurement script's own refusal code, the measurement script's module command addressing its PROBE GRAPH KEY rather than a constraint name (reverting that slot once left the suite green while the census published a false server limit), and the same script refusing a run whose schema statement did not install — asserted at the ISSUANCE PATH (`execute_command`'s own argv, with a whitespace-bearing key) and at `main()`'s exit 2, plus that gate's ACCEPTING arm (a complete schema must still publish a measurement, so a gate that refuses ALWAYS cannot pass either), a run that COULD NOT RUN exiting 2 rather than escaping as a traceback (Python's uncaught-exception exit is 1, the code `1` this README reserves for a produced verdict), the committed census being the verb's own bytes (the drift pin), the export-scan cites OPENING on the def / branch / append they name — derived from this sentence, because the equality arm above compares artifact to tool and both carried the same offset-shifted numbers once, the guard's comment/literal pre-pass agreeing with the write gate's grammar on every non-backtick input while keeping the one backtick branch that grammar cannot express (so a `//` inside `\`a//b\`` cannot swallow a pattern), and this declared count matching the file's own count |
| `docs/evidence/13094-graphevent/measure_graphevent_residency.py` + 2 JSON outputs | the residency measurement above, reproducible; it refuses any graph name outside the `probe_13094` prefix with **exit 2**, and exits **2** again when a run's schema statement did not install (`schema_complete: false` — a population measured without the schema it claims must not be quoted as one); `1` is reserved for a produced verdict, so a refusal never shares it |
| `docs/evidence/13094-graphevent/readers.json` | the census table, as the verb emits it |

**#13094 changed no product code. No graph write changed. No record read changed. Nothing deleted.**
That is MEASURED, not restated, because the line counts move with every rebase. At THIS branch's head
the tree carries four changed `tortoise/*.py` files and the same command prints `27` — `#13094`'s
alone prints `0`, and the other three files are the product work of TWO PRs, only one of which is
folded in here: `#13722`'s own stamp-chain submission (4 non-comment lines in
`tortoise/hosted_api.py`), and `#14483`'s landing, which this branch carries because the `test (c)`
reds it repairs are `main`'s own (`13` in `tortoise/embed_worker.py`, `10` in
`tortoise/embed_writer_register.py`); `tortoise/activation_scorecard.py` is all-comment. None of that
is a drift in this record, and none of it is reachable by `#13094`'s own claim — which is what the
re-runnable form below scopes:
`git diff origin/main -- 'tortoise/*.py' | grep -E '^[+-]' | grep -vE '^(\+\+\+|---) ' | grep -vc '#'`
prints `0` at this PR's own head (`#13094`'s), so no changed body line in ITS diff is anything but a
comment. ⛔ THAT IS A CLAIM ABOUT THIS PR, NOT ABOUT THE TREE: the command diffs against whatever
`origin/main` is NOW, so a LATER PR's own product change under `tortoise/*.py` legitimately makes it
print a non-zero count here — that is the `27` above, TWO PRs' behavioural work and not a drift in
this record — and the graded figures below are re-derived by `--refresh` rather than by this
sentence. The one figure here that NO `--refresh` row and no test grades is the `27` itself, which is
why it names the PRs it comes from instead of standing alone. Each block carries a
count this PR re-derived
(`157` distinct cite targets above the pivot from `725` in all, over `208` citing files —
`uv run python tools/hosted_api_cite_census.py --json` prints every one of those three, and
`--check` grades the two note blocks against the same measurement, so none of them is a hand
count), not a
change to what the code does.

---

## 7. The route, and where it is recorded

The prize is `#13082`'s largest single item: **40–43 MB measured** on the largest legacy graph (61 % of
that census's nodes), for a shape no graph query reads. The route below is recorded as **`#13094`'s
remaining scope** (its comment 7065026251), in the order the constraints force:

1. **A record home that is NOT a projection.** A dedicated table mirroring the node's five properties
   (`seq`, `ts`, `type`, `event_id`, `payload`) **plus the three indexes/constraint the census's DDL rows
   name** — `journal_event` cannot be it (§3). `cache_tier` is not the mechanism (§4); a store-level home
   selection is.
2. **Mirror the writes first, read from the record only after coverage is measured.** `append_event`
   writes the record row when the record is armed; `read_after` keeps reading the graph until a
   census proves the record covers the graph's range. The graph stays the authority for as long as
   there is any doubt — the failure mode a half-done flip produces is a silent subscriber gap, which
   is strictly worse than the RAM.
3. **A backfill + cutover + strip verb**, preview-by-default, refusing to stop the graph write or
   delete a row unless the census proves coverage in the same process (the `#13610`/`#13796` pattern
   already used for vectors: a gate that is **measured, never asserted**).
4. **Re-point the four maintenance readers** that reach the label directly rather than through
   `read_after`: `_head_seq`'s fallback (`webhook_delivery.py:1049`), the backup footprint
   (`hosted_backup.py:892`), and the two restore-side scans (`:1382`/`:1583`). ⚠️ Neither is an
   export scan — both are in `restore_graph`, and the export side is NOT censused at all
   (`hosted_backup.py:454` and `hosted_api.py:36016`, #14204), so a re-point driven by this census
   alone would leave the export endpoint reading the old home.
5. **Prove it on a scratch tenant** with the `superset` verb before and after, and only then strip.
6. **Falsifiers, stated up front:** (a) with the record short of the graph, the cutover REFUSES and
   names both counts; (b) a payload-only emit after the cutover still reaches `poll_events` (the
   producer-mismatch case, which is the reason a plain re-point was never available); (c) a rebuild
   after the cutover still carries the watermark and leaves the store empty (`#4664` unchanged).

⛔ Out of scope and unchanged: no cap, no retention change (`#13084`), no bound on `journal_event`
(`#9025`), no MCP/SDK surface change.

---

## 8. Absences this lane must report

* `[mandate-preflight]` reported two files the brief orders a lane to read and which **do not exist on
  this box**: `~/.pi/agent/state/LANE-BRIEF-PREAMBLE.md` and `~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md`.
  Neither has a provisioning path (`#1193` owns the content, `#3377` the report), so the brief's own
  text was the authority here, and the graph was not consulted for a prior derivation of this root —
  this document is the derivation.
* The `superset` verb was **not** run against a production tenant: this box has no `TORTOISE_RECORD_DSN`
  and no `psycopg2` in the venv. Its logic, its deficit classes, its paging and its exit codes are
  pinned; the tenant run is the operator step, and the verb exits 2 (no verdict) without a DSN rather
  than guessing.
