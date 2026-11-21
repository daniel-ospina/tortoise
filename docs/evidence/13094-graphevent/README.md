# `:GraphEvent` — the reader census, the measured residency, and why the move is a STORE re-home (#13094)

This is `#13094`'s answer to the question its own body says must be answered **first**, and the
measurement `#13083` §5.1 explicitly left UNMEASURED. It is written so the next lane starts from a
measurement instead of re-deriving one — which is the whole cost `#13094` has carried since the
census merged as `#14043`.

**Status, stated plainly: this PR (#14164) moves no bytes.** The shape is still written to the graph, and the
verdict below says why the *prescribed* mechanism cannot move it, what the actual route is, and what
it is worth. The move itself is **the remaining scope of `#13094`**, recorded on the issue
(`#13094` comment 7065026251) rather than filed as a peer ticket — the root is there, so the route stays
there. It is a store re-home (§7): a second durable store with cursor semantics, not a tier flip, and
shipping half of it would put the change feed's durability at risk for the class of defect this issue
exists to remove.

Everything here is a measurement or an executed assertion. Numbers carry the command that produced
them; claims carry the `file:line` that refutes them if wrong.

---

## 1. The reader census — every reader, classified

Produced by `uv run python tools/graphevent_home.py readers` (`readers.json` beside this file).
**13 statements** in `tortoise/` name the `:GraphEvent` label.

| # | reader | `file:line` (this head) | what it needs | class |
|---|---|---|---|---|
| 1 | `read_after` — the ONLY row-producing read | `tortoise/event_store.py:992` | ordered range scan on `seq`, optional type filter, `LIMIT`, app-side dedupe | **serving** |
| 2 | `append_event` dedupe pre-check | `event_store.py:801` | key existence on `event_id` | maintenance |
| 3 | `append_event` — the write | `event_store.py:816` | an insert | maintenance |
| 4 | `_event_row_exists` (`#9208` loss discriminator) | `event_store.py:879` | key existence | maintenance |
| 5 | `purge_expired` | `event_store.py:1029` | predicate delete | maintenance |
| 6 | `purge_overflow` (count) | `event_store.py:1043` | whole-label count | maintenance |
| 7 | `purge_overflow` (delete) | `event_store.py:1053` | ordered, limited delete | maintenance |
| 8 | `capture_watermark` | `event_store.py:645` | aggregate `max, min` | maintenance |
| 9 | `_refresh_first_seq` | `event_store.py:1104` | aggregate `min` inside an RMW on the **sibling** label | maintenance |
| 10 | `webhook_delivery._head_seq` fallback | `webhook_delivery.py:1049` | aggregate `max` | maintenance |
| 11 | backup footprint | `hosted_backup.py:892` | aggregate `max, min` | maintenance |
| 12 | whole-graph export (dump side) | `hosted_backup.py:1333` | a label-agnostic dump | maintenance |
| 13 | whole-graph export (restore-side scan) | `hosted_backup.py:1534` | a label-agnostic dump | maintenance |

⚠️ **The line addresses in `#13083` §2.6 for rows 12–13 are stale at this head**: the census cites
`:1316`/`:1517`, and the statements are at `:1333`/`:1534`. Re-derived here rather than shifted by a
constant, because `hosted_backup.py`'s drift is not one offset (the census itself records `+2` below
its `:1038` and `+134` above it).

**Verdict: `record_only`-shaped.** Not one reader needs a graph capability — no traversal, no
adjacency walk, no fulltext query, no EP walk, no vector query. Every one is a key lookup, an ordered
range scan, an aggregate, or a predicate delete; those are SQL shapes paying RAM prices. The
**serving** row is exactly one statement, so a re-home re-points one read and not a family of them.

**Classification, made checkable** rather than asserted in a document:

* `tests/test_13094_graphevent_home.py::test_every_declared_reader_still_names_its_statement_in_the_tree`
  — every row's statement must still resolve **in its file** (content-matched, whitespace-collapsed,
  so a re-indent is not a false red while a reword is a real one).
* `…::test_no_reader_of_the_change_feed_needs_a_graph_capability` — **no** row may mention a graph
  capability in its `needs`/`why`. A future reader that genuinely needs one reds the census instead
  of being filed as `maintenance`.
* `…::test_the_census_has_exactly_one_row_producing_reader` — a second producing reader changes the
  shape of the re-home, so the count is pinned.

---

## 2. The measured residency — what the shape costs, in bytes

`#13083` §5.1 is explicit that its own residency figure is **unmeasured**: the payload is exact
(12,768 B over 67 `:GraphEvent` nodes ≈ **190 B/event**) and the RAM figure is run-dependent drift it
refused to quote. So it is measured here, against a real FalkorDB, with production's own schema.

Instrument: `measure_graphevent_residency.py` (this directory). Outputs:
`measurement-payload88.json`, `measurement-payload190.json`.

**How it measures.** `INFO memory` `used_memory` at three populations (20k / 40k / 60k rows), and the
**marginal slope** between the last two. The slope is the quoted number: the graph's fixed cost (two
`CREATE INDEX` statements and, where the server accepts it, the `event_id` unique constraint — the
same three statements `event_store.ensure_event_schema` issues) is paid once and would otherwise be
divided into every node and reported as if it recurred.

| synthetic payload | slope | `GRAPH.MEMORY USAGE` at 60k rows |
|---|---|---|
| 88 B (the `PointAdded`-with-actor shape, unpadded) | **557.4 B/node** | 23 MB total, 8 MB indexes, 14 MB node attributes |
| 190 B (`#13083` §4's measured tenant average) | **597.2 B/node** | 19 MB node attributes, 8 MB indexes |

**Projected onto the shape that matters** — 72,488 nodes, 42 % of the largest legacy graph's 118,755
(`#13083` §1):

```
72,488 × 597.2 B = 43.3 MB        (190 B payload, the tenant-average shape)
72,488 × 557.4 B = 40.4 MB        (88 B payload, the floor of the two)
```

**40–43 MB of resident RAM, on one tenant, for a shape no graph query reads.** Against `#13082`'s
own economics (~584× RAM/disk, ~78 MB solo budget) that is **half a solo tenant's entire memory
allowance**, which is why this is the census's largest single item.

⚠️ **What this measurement is NOT.** It is a synthetic population on a throwaway graph, not a
production reading; `used_memory` is process-wide, so a tenant graph cannot be attributed per label
(`graphevent_home.py residency` says so in its own output rather than quoting a number it cannot
attribute). The payload is uniform, where real payloads vary; the slope was measured at two payload
widths precisely so a reader can see it is not payload-independent.

---

## 3. Is `public.journal_event` already the home? **No — and not for a missing-column reason**

`#13094`'s body entertains the cheap answer: *"a record home very likely exists already … the move
may be a feed re-pointing."* `#13083` §6.3 refused to assume it and named the question. Here is the
answer, with the refutations executed rather than argued:

1. **A payload-only emit writes NO journal record at all.** `_emit_event` returns before the JSONL
   half when it carries no `point`, no `id`, and no `turns` (`sdk.py:16204`, contract restated in
   `event_store.append_event`'s own docstring: *"a payload-only emit writes one with no JSONL record
   at all"*). This is not hypothetical: the turn hard-delete lane calls exactly that shape —
   `sdk._emit_event("PointRetracted", _deleted_point_payload(tid))` (`sdk.py:3801`) — and the
   surrounding comment states the split as a contract, *"Two stores, one meaning each"*.
   **Executed pin:** `tests/test_13094_graphevent_home.py::
   test_a_payload_only_emit_writes_the_store_and_no_journal_record` emits one and asserts the store
   holds `PointRetracted` while the journal holds **zero** rows.
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
`tools/graphevent_home.py superset` (read-only on both stores; exit `1` = not covered, exit `2` =
could not run, never "covered"). ⚠️ Its own verdict carries the limit that matters: **even at
`covered`, the producers disagree** (refutation 1 is a property of the producer, not of any tenant's
history), so a `covered` tenant is not evidence that a re-point would not drop future events.

---

## 4. Why the prescribed mechanism cannot express this move — and the `#4664` hazard

`#13083` §5.2 prescribes: *"give it a `NODE_TABLES` record home with a tier column, then tier it."*
That route is **wrong for this shape**, and the reason is structural, not administrative:

> `cache_projection.read_record_view` builds `SELECT * FROM public.{NODE_TABLES[label]}` for **every**
> label it is asked for (`cache_projection.py:3822-3845`), with no per-label special case. A record
> view is what `rebuild_from_record` materialises **into the graph**. So adding `:GraphEvent` to
> `NODE_TABLES` would make a record-sourced rebuild **re-CREATE the shape from the record view** —
> while `#4664` establishes the opposite invariant: *a rebuild does NOT replay `:GraphEvent` rows; it
> comes back EMPTY and carries only the watermark* (`event_store.py` module docstring; pinned by
> `tests/test_rebuild_event_meta_watermark_4653.py:133`, which asserts the store is empty after
> `rebuild_all`).

There are three further locks on that route, each of which the test suite now holds:

| lock | what it refuses | where |
|---|---|---|
| the key contract | `node_identity`'s `CHECK (node_label IN (…))` has no `GraphEvent`, and `KEY_PROPERTY` has no entry — `seq` is a `bigint`, not the non-empty **string** key a record node needs; `event_id` (the other candidate) is not a column of `journal_event` | `supabase/migrations/20261012000001_record_event_stream.sql:55`; `cache_projection.node_from_row:2876-2886` |
| the placement table | `_props_from_row` REFUSES a column the placement table has not classified (`UnknownRecordColumn`) rather than becoming a node property | `cache_projection.py:2866` |
| the projection registries | `NODE_TABLES`/`KEY_PROPERTY` do not name the label today | `test_the_change_feed_has_no_record_home_in_the_projection_registries` |

The consequence is executed, not described:
`test_adding_the_shape_to_the_registries_would_project_it_into_the_graph` patches the two registries
and shows `read_record_view` reaching straight for `public.journal_event` (the reader is
label-agnostic — the fail-open arm) and then refusing on the unclassified column (the second lock).
So the tier route needs **three new declarations** — a table mapping, a key contract, a column
classification — and the first alone already breaks `#4664`.

**The correct mechanism is a STORE re-home**, which is a different thing from a tier: `:GraphEvent`
is not a projection of the record, it is a second durable store whose rows no replay reproduces.
That is why `#13083`'s own §6.3 says the work is *"prove `journal_event` is a superset — and if it is
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
| `tools/graphevent_home.py` | the census as data (`READERS`), the residency reader, and the **`superset` verb** that answers `#13094`'s blocking question on a real tenant — read-only, with exit `2` for "no verdict" |
| `tests/test_13094_graphevent_home.py` | the census completeness pin, the capability-vocabulary pin, the two producer refutations (executed), the `#4664` mechanism refusal (executed), the verdict/exit-code pins |
| `docs/evidence/13094-graphevent/measure_graphevent_residency.py` + 2 JSON outputs | the residency measurement above, reproducible |
| `docs/evidence/13094-graphevent/readers.json` | the census table, as the verb emits it |

**No product code changed. No graph write changed. No record read changed. Nothing deleted.**

---

## 7. The route, and where it is recorded

The prize is `#13082`'s largest single item: **40–43 MB measured** on the largest legacy graph (42 %
of its nodes), for a shape no graph query reads. The route below is recorded as **`#13094`'s remaining
scope** (its comment 7065026251), in the order the constraints force:

1. **A record home that is NOT a projection.** A dedicated table mirroring the node's five properties
   (`seq`, `ts`, `type`, `event_id`, `payload`) — `journal_event` cannot be it (§3). `cache_tier`
   is not the mechanism (§4); a store-level home selection is.
2. **Mirror the writes first, read from the record only after coverage is measured.** `append_event`
   writes the record row when the record is armed; `read_after` keeps reading the graph until a
   census proves the record covers the graph's range. The graph stays the authority for as long as
   there is any doubt — the failure mode a half-done flip produces is a silent subscriber gap, which
   is strictly worse than the RAM.
3. **A backfill + cutover + strip verb**, preview-by-default, refusing to stop the graph write or
   delete a row unless the census proves coverage in the same process (the `#13610`/`#13796` pattern
   already used for vectors: a gate that is **measured, never asserted**).
4. **Re-point the three maintenance readers** that reach the label directly rather than through
   `read_after`: `_head_seq`'s fallback (`webhook_delivery.py:1049`), the backup footprint
   (`hosted_backup.py:892`), and the export/restore scans (`:1333`/`:1534` — the export must either
   skip the label or be documented as not carrying a record-only shape).
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
