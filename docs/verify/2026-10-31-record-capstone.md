# The record, proven end to end on a live box — #9567 capstone (run 2026-10-31)

**Verdict: `PARTIAL` — turns as a graph tier are REMOVED and both prose tiers' record home is proven,
while the PROSE's absence from the graph is NOT proven on either tier (that needs #10389 + #10428 and
the `:Source.summary` route).**

Read that precisely: **no turn `Point` node and no turn `CONTAINS` edge exists on the armed arm** — that
is what was measured, and it is the tier §9.1/D1 names. The *prose itself* still reaches two nodes
(§4), so "raw turns exist in Postgres only" is **not** a claim this run supports.

Measured, not inferred: two arms on a live FalkorDB lane and a live Postgres carrying the repo's own
record migrations, with **one variable changed between them — the connecting role** — plus a third run
of the *same probe* on the **deployed tree**, so "what `main` contains" is not standing in for what the
deployment runs.

| | |
|---|---|
| Issue | #9567 (capstone, Scenario A/B evidence for D2, D3, D7) |
| Deployed code proven against | `b101355a8` — `deploy-hosted.yml` run [41235268490](https://github.com/daniel-ospina/tortoise/actions/runs/41235268490) (created 2026-10-31T19:39:48Z), job `deploy-api` step 12 `Deploy (retry through Fly lease/API races): success` completed **19:48:23Z** |
| Live deployment arming read | `curl -s https://tortoise-y4mjjq.fly.dev/health` → `"record":{"armed":true,"ok":true,"dsn":"adb0c38b",…}` (that process's own report, 2026-10-31T21:07Z) |
| FalkorDB | private lane `fdb-lane-44bd557920` on `127.0.0.1:16400` (Docker, `falkordb/falkordb-server:v4.20.6`), flushed to **0 nodes** before each arm |
| Record | Postgres 16 `p10000-record-pg` on `127.0.0.1:32988`, repo record migrations applied, tenant `org-9567c` / `g-9567c` (a `public.graphs` row was inserted first — the FK the projection relies on) |
| Doors used (product's own) | turns: `TortoiseSDK.capture_session`; narrative: `hosted_api._execute_commit_writes` → `sdk._emit_event("SessionNarrated")` (the #9555 carrier) |
| Instrument | `/tmp/t9567/probe.py`; wrappers `/tmp/t9567/run-{a,b,dep}.sh` (Arm A vs B differ only in the DSN's role; `PROBE_SID` labels the session) — ⚠️ **not committed**, see §7's reproducibility gap |
| Deployed-tree arm (B′) | `git archive b101355a8 \| tar -x -C /tmp/t9567-dep`, then the same probe with `PYTHONPATH=/tmp/t9567-dep`; the run prints `tortoise.__file__: /tmp/t9567-dep/tortoise/__init__.py`, and `sdk.py`/`hosted_api.py`/`record_projection.py`/`record_writer.py` there are sha256-identical to `git show b101355a8:` |
| Lane state | no production surface was written to, no config flipped |

## 1. The measured table

Counts are per tenant, captured **in the same run** (baseline → after), never a constant. Arm B started
from an all-zero baseline; **Arm A and Arm B′ did not** — the tenant was not reset between arms, so
Arm A's baseline was `point 3 / narrative 1 / journal_event 14` and B′'s was the same after Arm B. This
is why Arm A's `session` and `session_narrative` cells read 0 *for its own session id* (a measurement)
while its `journal_event` cell is marked **derived** (the append-only count does not move, so no new
journal row is evidence of anything).

| store | what landed | Arm A — DSN role **bypasses RLS** (`record`, owner) | Arm B — DSN role `tortoise_record_app` (NOBYPASSRLS) | Arm B′ — Arm B on the **deployed tree** |
|---|---|---|---|---|
| `public.point` (the transcript table; `node_key='<sid>_t<i>'`, `is_episodic`) | the two raw turns, verbatim | **0 rows** for `cap9567a-session` | **2 rows**: `_t0` 87 B, `_t1` 45 B | **2 rows**: `_t0` 87 B, `_t1` 45 B |
| `public.session_narrative` | the S1 narrative prose | 0 rows for that session | **1 row**, 129 B, `left(narrative,60) = 'NARRATIVE-ZEBRA-9567 the connected prose form of this captur'` | **2 rows**, 129 B each (`cap9567-session`, `cap9567d-session`) |
| `public.session` | the session row | 0 | 1 | **2** (cumulative: Arm B's + B′'s) |
| `public.journal_event` | the journal the record folds | **derived**: unchanged (append-only) | 14 | 14 (cumulative 28) |
| `monitoring.metrics()["record_write_failures"]` | the write's own counter | **`{'write_failed': 14}`** (was `{}`) | **`{}`** — zero | **`{}`** — zero |
| FalkorDB `_capture_turn_ids(proj, sid)` | the graph-side turn id space | `['cap9567a-session_t0','cap9567a-session_t1']` ← **fell back to the graph** | **`[]`** | **`[]`** |

Per-tenant `public.point` count moved **0 → 3** in Arm B (`_t0`, `_t1` + the extracted claim `pt_f07a…`,
27 B); `point_episodic` **0 → 2**. The block below is the **deployed-tree arm's** read (B′) — it filters
on B′'s own session id, so it shows B′'s two turns and nothing of Arm B's; Arm B's rows are the R2 block
further down. Owner read, tenant-filtered:

```console
$ docker exec p10000-record-pg psql -U record -d record -c \
  "select node_key, is_episodic, length(content), left(content,45) from public.point \
    where org_id='org-9567c' and node_key like 'cap9567d-session%' order by node_key"
   node_key             | is_episodic | length |                      left
------------------------+-------------+--------+-----------------------------------------------
 cap9567d-session_t0    | t           |     87 | [user] TURN-ZEBRA-9567 the raw transcript tex
 cap9567d-session_t1    | t           |     45 | [assistant] second turn with no marker at all

$ docker exec p10000-record-pg psql -U record -d record -c \
  "select org_id, session_id, length(narrative) from public.session_narrative where org_id='org-9567c'"
 org_id     | session_id        | length
------------+-------------------+--------
 org-9567c  | cap9567-session   |    129
 org-9567c  | cap9567d-session  |    129
```

And **through the RLS boundary** (the app role, `BEGIN; SET LOCAL ROLE tortoise_record_app;
set_config('app.current_org_id',…)`) the same rows are visible — so the write landed *inside* the
tenancy boundary that R6/§9.1 depend on, not around it. Verbatim, Arm B (unfiltered by session — this
is the whole tenant, which is why only B's rows appear):

```text
R2 (record, tortoise_record_app + app.current_org_id — the RLS boundary)
  point rows  : [('cap9567-session_t0', 87), ('cap9567-session_t1', 45),
                 ('pt_f07a9c5c90ddea2a7d85a197db70ef9eba2f8af07f0800d7eca2eb0543d642', 27)]
  narrative   : [('cap9567-session', 129)]
```

Arm B′'s R2 (later, same tenant, same probe) returned five point rows — `cap9567d-session_t0` (87),
`cap9567d-session_t1` (45), `cap9567-session_t0` (87), `cap9567-session_t1` (45), `pt_f07a9c5c…` (27)
— and two narrative rows (`cap9567-session`, `cap9567d-session`, 129 B each), i.e. the deployed-tree
arm added rows instead of replacing them.

## 2. The graph side — before and after, and why "absent" is not "empty"

Arm B (app role), full-graph fingerprint, so a zero on the marker query cannot be an empty graph:

The graph was flushed to **0 nodes** immediately before this arm (an explicit `MATCH (n) DETACH
DELETE n`; the probe prints Q5 only once, *after* the capture, so the 0 is a stated precondition, not
an instrument line):

```text
BASELINE (before the capture)        : {"point":0,"point_episodic":0,"session":0,
                                        "session_narrative":0,"journal_event":0}   ← record
Q5 after the capture                 : [[12]]
   by label: Point 1 · GraphEventMeta 1 · Source 2 · EpMeta 1 · GraphEvent 2 ·
             Object 1 · Event 3 · Session 1
Q4 _capture_turn_ids                 : []
Q1 every node whose text holds either marker (content | summary | story_arc):
   HIT [['Event'],  'd8f1e0377…', 0, 21, 129]
   HIT [['Source'], 'doc_d8f1e03…', 0, 21, 129]
   HIT [['Source'], None, 0, 80, 0]
   hits: 3
```

Legend for those three rows: `[labels, id, len(content), len(summary), len(story_arc)]` — the node's
label list, its `id` property, then the byte length of each of the three text properties **in that
order**. Read with the legend: row 1 is the `:Event` whose `story_arc` holds the 129 B narrative; row 2
is the transcript `:Source` carrying the same 129 B; row 3 is the `:Source` whose `summary` holds the
80 B of raw turn text (§4.2). Row 3's `id` prints `None` — that node carried no `id` value in this run
— so it cannot be attributed by id from this output; the `TURN-ZEBRA-9567 …` prefix in its `summary`
is what identifies it, and §4.2's claim is read off that prefix, not off node identity.

Arm A, same probe, same **lane flushed again to 0 nodes** (so its 14 is a fresh 12 + the two fallback
turn nodes, not Arm B's 12 + 2) — and the two turn nodes are back:

```text
Q5 after the capture                 : [[14]]
   by label: Point 3 · … · Session 1
Q4 _capture_turn_ids                 : ['cap9567a-session_t0', 'cap9567a-session_t1']
Q2 Session's outgoing edges          : CONTAINS -> Point cap9567a-session_t0 (87 B)
                                       CONTAINS -> Point cap9567a-session_t1 (45 B)
                                       CONTAINS -> Point pt_f07a9c5c… (27 B, the extracted claim)
```

⇒ **The role decides whether the write lands; the store decision follows the write's own outcome.**
Armed as the app role the write lands — turns in Postgres, no turn node, no turn `CONTAINS` edge. Armed
with a role that bypasses RLS the seam refuses (below), and `sdk._write_capture_turns`'s post-write
fallback (`tortoise/sdk.py:3438-3482`) puts the turns in the graph, per turn. Not the preflight:
`record_writer.record_holds_turns` asks journal-configured + armed + tenant + breaker and never reads
the role (`tortoise/record_writer.py:1358-1361`), which is why Arm A's two turn nodes exist **only**
because the fallback re-created them after the refusal.

## 3. The refusal proof — an unsupported shape is refused LOUDLY, and nothing is dropped

Arm A's DSN names a role the seam must not accept. The refusal is raised, named, counted, and the
turns were **not** silently dropped — they went to the graph instead ("a drop is not a fix"):

```text
the record write did not happen (write_failed) — the journal has the record and a replay heals the
record: the record write failed after 1 journal record(s) were materialized from position 43
(RecordProjectionError)
...
failures[1] : {'write_failed': 14}          ← was {} at the baseline of this arm
_capture_turn_ids : ['cap9567a-session_t0', 'cap9567a-session_t1']
```

The exception's own text (called directly, same DSN, same tenant):

```text
RecordProjectionError: the record's connection runs as a role that BYPASSES RLS — superuser,
BYPASSRLS, a member of such a role, or the owner of a record table. RLS is this record's tenancy
boundary (R6, epic #7879), so every read and write on that connection would cross tenants with the
policies never evaluated. Arm TORTOISE_RECORD_DSN with tortoise_record_app (docs/infra-runbook.md ->
"The record's connecting role"); refusing rather than applying a replay that cannot be scoped
(#9600 review cycle 2)
```

This is the exact failure mode the external leg warns about, and the shape our decision already
encodes: **an RLS test that runs as the owner/superuser proves nothing** (PostgreSQL docs, "Row
Security Policies": *superusers and roles with `BYPASSRLS` always bypass RLS, and table owners do so
unless `FORCE`*; atlasgo's RLS guide: *tests "can falsely pass when run as a superuser" — run them
under `SET ROLE` and assert the effective `current_user`*; Supabase: *`service_role` carries
`bypassrls`, never hand it to a client*). Our seam goes one step further than the convergent advice
and **refuses** the bypassing role rather than merely testing under a different one.

## 4. Residuals measured in the SAME armed run (both already tracked — not new roots)

1. **The narrative is still in BOTH stores.** `public.session_narrative` holds the 129 B (✅) **and**
   the `:Event` node carries the same 129 B in `story_arc`, as does the transcript `:Source` (`Q1` shows
   the `Source` hit; `Q3` on Arm B reads
   `[['d8f1e0377…', 129, 'NARRATIVE-ZEBRA-9567 the connected prose form'], ['ev_1a123a32…', 0, ''],
   ['ev_1edf43f6…', 0, '']]`). Root: the commit phase's raw-Cypher `SET … story_arc=$arc` on two nodes — the change
   **PR #10389** deletes (`Refs #10000`, `MERGEABLE`; its check surface at 2026-10-31T21:33Z carried two
   failing `test-slow` shards on run 41240532814 — `(d)` at 21:24:26Z, `(a)` at 21:32:42Z — with others
   still queued; another lane owns its forward merge, and this artifact does not read that rollup as a
   verdict). The `create_event` door is tracked
   separately as **#10428**. ⚠️ The citation is **time-scoped on purpose**: a later shard (`test-slow
   (e)`) also failed, at 21:41:27Z, after the instant pinned here — this artifact reports the surface it
   read at 21:33Z and never the rollup's current colour.
2. **A `:Source` still carries raw TURN text in `summary`** — 80 B, `'TURN-ZEBRA-9567 the raw transcript
   text …'` — written by the capture path itself (`sdk._session_source_metadata`). Not new information:
   it is recorded on **#10000** (last comment) as the second prose passthrough onto a node, and this
   run reproduces it independently on the deployed tree.

⇒ **`PARTIAL`**, and specifically: **the turn TIER is gone from the graph (no turn node, no turn
`CONTAINS` edge) and both prose tiers have a working Postgres home — proven; the prose's ABSENCE from
the graph is not proven on either tier.** The gaps are #10389 (+#10428) for `story_arc`, and the
`:Source.summary` route recorded on #10000, not this measurement. The probe was **not** narrowed to
make the composite claim pass.

## 5. What this covers of #9567's DoD, and what it does not

⛔ **#9567's rule 2 is not satisfied by this run, and the table says so rather than rounding up:** an
item counts as green only if its **falsifier was observed red**, and this run observed none (it is one
scenario-A pass with a role control, not a falsifier battery). Everything below is therefore
"measured, falsifier **NOT observed**" — which, by the issue's own rules, **does not count toward
10/10**.

| #9567 item | state |
|---|---|
| **D2** one `SessionRecorded` journal record produces exactly one `session` row; `record_write_failures` delta 0 | **measured green, falsifier NOT observed** — Arm B `session` 0→1, failures `{}` → `{}`; Arm B′ 1→2, failures `{}` → `{}`. Falsifier (reintroduce #9264 → `DatatypeMismatch` + `write_failed`) not run |
| **D3** (the issue's text) *"No write path bypasses the record — assert the capture writes journal → record → cache in that order, with no cache-only path"*, falsifier: *inject a cache write that skips the record → D3 must red* | **NOT MEASURED as written** — no ordering assertion was made. What Arms A/B measure is a *different* property: the capture's store choice follows the record's capability, and a refused record falls back to the graph **loudly** (refusal text + `write_failed` counter) rather than dropping the transcript. That is the #10000 fallback, not D3's falsifier |
| **D7** the narrative and the raw turn text come back from the record | **half, falsifier NOT observed** — both tiers are present and readable in the record, through the app role and the RLS boundary; "and nowhere else" fails on the narrative (**§4**, item 1). Falsifier (revert #9555 → the fetch must fail) not run |
| D1, D4–D6, D8–D12 | **NOT MEASURED here** — different scenarios (arm/replay/backup/tenancy/quota) and, for D11–D12, #9559 is still `FOLLOW` |

Nothing is claimed about a *production* capture: no production credential exists on this box, so
whether the hosted deployment's tenant resolves for a real capture — the `no_tenant` refusal measured
on #9548 in October — is **`UNMEASURED(no production credential on this box)`** — `/health` reports
`armed: true` and a *decline-only* counter set (`{}`), which is exactly what an inert-but-configured
deployment also prints, so it is not evidence of a write.

## 6. Relationship to the 2026-10-26 capstone run (a continuation, not a second opinion)

The committed `tests/test_9567_record_capstone.py` and `docs/verify/2026-10-26-record-capstone.md`
measured D1–D5, D9 (direct connection) and D10 (with D6's seam half), each with a named red **or a
cited prior red** — three of those rows (D2, D3, D4) were *not re-mutated in that run* and carry a prior
run's red. That artifact's own residual list names exactly the gap this run closes:

> *"**D7** narrative text — the §9.1 carrier is emitted by the layer-1 commit path, not
> `capture_session`; the reader returned `narrative=None` on a real capture"* — drive
> `hosted_api._execute_commit_writes` with a `story_arc` against the armed record, then read
> `session_text.narrative`.

Arms B/B′ took that **write** recipe (drive `_execute_commit_writes` with a `story_arc` against the armed
record), but read the result as **SQL**, not through the SDK reader: the evidence is
`SELECT session_id, length(narrative) FROM public.session_narrative` executed as
`tortoise_record_app` inside the RLS boundary (`R2` above). ⛔ So this run proves the **write path and
the record contents**, and it does **not** exercise `record_read.session_text(...).narrative`
(`tortoise/record_read.py:1029`) — that
reader leg is still unexercised **on a record that holds a narrative**: the prior run's `narrative=None`
was that reader executing against a row whose text column was NULL (the carrier did not exist yet), not a
row that existed and was silently dropped. This run proves the row is now written with its 129 B of
text; it does **not** prove that `record_read.session_text` maps that text back — i.e. the "reader returns
None for a row that holds the narrative" failure class is **neither tested nor ruled out here**. The row
itself is written by the carrier (`sdk._emit_event("SessionNarrated")`) from the commit path and is
visible through the app role's RLS boundary. So the composite D7 now reads:
**both prose tiers land in the record and are readable from it as the app role, and the turn TIER is
absent from the graph — while the PROSE is still reachable from two nodes, and the SDK reader leg is
still unexercised.** No measurement the 2026-10-26 artifact already
made is re-opened or contradicted; where the two overlap (**D2**, and **D7's raw-turn half** — the two
turns present in `public.point` and read back as the app role) this run is an independent second
measurement of the same state.

## 7. Reproduce

```bash
uv run python tools/test_lane.py uri            # private FalkorDB lane; exports 3 seam vars
# record: any Postgres with the repo's record migrations + a `public.graphs` row for the tenant
PROBE_SID=cap9567d-session /tmp/t9567/run-dep.sh   # deployed tree; run-b.sh for this tree, run-a.sh for Arm A
```

Arm A vs Arm B differ in **one** shell variable that can affect behaviour: `TORTOISE_RECORD_DSN`'s role
(`record` = table owner/BYPASSRLS ⊕ `tortoise_record_app` = NOBYPASSRLS); the wrappers also set
`PROBE_SID`, which only labels the session and cannot change a store decision. Both set
`TORTOISE_RECORD_ORG_ID` / `TORTOISE_RECORD_GRAPH_ID`
plus `TORTOISE_RECORD_SINGLE_TENANT=1` — the pair is refused outright without the declaration
(`record_writer._refused_tenant_env_note`), which is why it is stated here rather than left implicit.

⚠️ **Reproducibility gap, named rather than hidden: the instrument was not committed.** The probe and
its three wrappers lived under `/tmp/t9567/` and `/tmp/t9567-dep/` on the box that ran the arms, and
they were not captured into the repo before that box was reaped — so the command above is not runnable
from this tree, and a future reviewer cannot read which Cypher Q1–Q5 actually ask. That is the gap the
2026-10-26 artifact's D10 row records as *improved* ("the prior run measured it with a probe outside
the repo"), and this run did **not** inherit the improvement. Rewriting the probe from the pasted
output is not a repair: it would be an instrument that is not the one that ran, so the artifact will
not do it. What stays verifiable from the repo — and is all §6's claims rest on — are the two write
paths (`sdk._write_capture_turns`, `hosted_api._execute_commit_writes` → `sdk._emit_event`), the
record tables, and the SDK reader leg this run explicitly does not exercise.
