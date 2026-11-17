# Capstone #13038 — the state model measured by customer value

**Parent:** #11824 · **Design of record:** `docs/plans/2026-11-15-state-lifecycle-and-pipelines.md`
· **Instrument:** `tools/capstone_state_value.py` · **Scenario (the key):** `narrative.json`
· **First scoreboard:** `scoreboard-local.md` (JSON: `run-local.json`)

This is the **value assessment** for the state / lifecycle / pipeline model. It is not a drift
test (code agrees with document) and not a unit suite (a function returns what its author
expected). It asks the only question the model exists for: **does the product tell the truth
about a business?** — what is current, what ended and how, what was planned versus what
happened, and whether a goal is actually met.

It is slow, on demand, and never on a CI path. It is allowed to fail; the first scoreboard
exists to show the gap, not to hide it.

---

## The first scoreboard — measured 2026-11-17, `tortoise-sdk` @ `6f162b9efc`

The measured tree is this lane's head, which carries `origin/main` @ `b8f56fdec4` (the
instrument records the exact commit in `run-local.json` → `header.product`).

```text
## Scoreboard: 3 right · 3 wrong · 0 blocked · 0 unjudged
```

| # | question | verdict | the product's answer (short form) | why |
|---|---|---|---|---|
| **Q1** | What are we currently committed to? | **wrong** | one point-scope search → 2 on-topic rows (the live Q4 pricing decision and the uptime judgement); two object-scope searches → 0 rows | the launch plan is never named — a customer gets the Q4 pricing decision and the uptime judgement but nothing that tells them a plan is outstanding |
| **Q2** | Did we ever plan a launch event, and did it happen? | **right** | `search` → the plan; `read_entity` → stage `scheduled`, slot `entry`, `isClosed=false`; `read_events` → the two uptime measurements only | planned and happened are told apart |
| **Q3** | Why did the pricing decision change, and what replaced it? | **right** | Q3 served as `superseded`; the live Q4 row states the replacement and carries `supersedes=[Q3]` | the successor is named, no rationale invented |
| **Q4** | Is the uptime goal met, and on what basis? | **right** | `judge` → `unmet`, comparator `>=`, threshold 99.9, reading 99.72 as of 2026-11-15 | achievement is measured from the reading, not declared from a status |
| **Q5** | What was planned and never happened? | **wrong** | both default-scope searches → **0 rows**; the cancelled call is reachable only by its id, and the by-id read shows slot `terminal`, `isClosed=true` | the customer's question gets an empty answer — the terminal plan is correctly *excluded*, but nothing lets the question be asked **of** terminal things (F1/F3) |
| **Q6** | Has anything we relied on been withdrawn? | **wrong** | both default-scope searches → 2 rows each with **zero** query-word overlap (score 0.0); in a *search* the retracted claim appears only with terminal scope forced | a customer is told nothing was withdrawn — **#13302** (no ended-scope read) + the failed-leg fallback |

Read it as: **3 of 6 customer questions get a truthful answer today**, and the three that fail
fail on **one shared gap seen from three sides — no read whose question is an ended/terminal one**:

* **Q5** — the terminal-slot plan is no longer *served as current* (it is excluded from the default
  scope, which is the currency rule working), but "what was planned and never happened?" returns
  **0 rows**: the customer is told nothing rather than told what ended.
* **Q6** — the same shape for withdrawals: the retracted claim is absent from both default-scope
  searches, and the answer the customer gets is two unrelated rows at score 0.0.
* **Q1** is a *reach* failure rather than a lifecycle one: the commitment question is answered from
  an `entity_type`-scoped read, so an Object-shaped commitment is missing unless the caller guesses
  its name (Q2's own call, which names the plan, does return it).

Two of the issue's predicted failures did **not** materialise at this head, and that is a result
worth recording rather than explaining away: **Q2** passes because a plan is a first-class Object
kind with a pipeline slot, and the node read carries `pipelineStageId` / `pipelineSlot` /
`isClosed`; **Q4** passes because the judgement writer records the state *together with the
reading and the target that produced it*. Those two shapes are shipped and visible; the scoreboard
says so.

The full text of the run — every product call, every payload, every signal, the ingestion log and
the target's retrieval posture — is in `scoreboard-local.md` (and machine-readably in
`run-local.json`).

---

## How it works

1. **A fixed narrative** (`narrative.json`, frozen before the run) containing, on purpose, every
   shape the model exists for: a *plan* (not an event), a *superseded* commitment with a named
   successor, a retirement with **nothing** replacing it, a plan that reached a **terminal stage
   with no occurrence**, a goal with a declared *target* and two readings (latest below target),
   and a *withdrawn* claim.
2. **Ingestion through the product's own write doors** — `create_object` / `update_knowledge`
   (lifecycle word), `create_point` / `supersede_point` / `retract_point`, `create_event`
   (goal readings), the pipeline stage writer (`pipeline.seed_pipeline` / `advance_stage`), and
   the goal judgement writer (`goal_judgement.judge_and_record`). No fixture reaches into the
   graph directly; if a door refuses, the ingestion log says so and the dependent question becomes
   `blocked` — the refs each recipe needs are declared in `RECIPE_REFS` and checked BEFORE the
   question is asked, because asking about a graph the scenario never created would hand a fresh
   reader an empty answer and score it as a product defect.
3. **The six customer questions asked through the product's own read surface** — `search`
   (`tortoise_fts_query`, the same call the hosted `/v1/search` route makes), `read_entity`
   (`get_entity`), `read_events` (`get_events`), and `judge` (`goal_judgement.judge`). Each recipe
   is declared in the tool, so a wrong answer is attributable to a call.
4. **Scored by a FRESH READER** — an agent given the narrative as its key, the six questions, the
   rubric and the product's payloads *including* the mechanical signals (was a retired id served?
   how many served rows share a word with the question? which leg was degraded?), which the judge
   prompt frames as evidence by which to check an answer — **never as the verdict**. The runner is
   not given. Verdict vocabulary: `right` / `wrong` / `blocked` / `unjudged`, where `blocked`
   means *the question could not be asked* and is never folded into `wrong`.

### Reproducing this baseline

```bash
# ONE COMMAND: a private throwaway FalkorDB, the ingest, the six questions, a FRESH READER,
# and the scoreboard. (`tools/test_lane.py run` runs PYTEST and so cannot take the
# instrument's own argv; rerun.sh uses the `uri` route with the safe assign-then-eval form.)
bash docs/evidence/13038-capstone/rerun.sh --judge fresh
```

To re-render from a stored reader output instead of spawning one — `--judge file --verdicts <file>`
on `rerun.sh`, or without touching the graph:

```bash
uv run python tools/capstone_state_value.py judge \
    --packet docs/evidence/13038-capstone/judge-packet.json \
    --verdicts docs/evidence/13038-capstone/verdicts-baseline.json \
    --out /tmp/capstone-rerender          # NOT the bundle directory — see below
```

⚠️ **The bundle directory is REFUSED, not merely discouraged.** `judge` rebuilds the header from
the packet's header, which was frozen before any verdicts were applied, so writing here would
replace `run-local.json` / `scoreboard-local.md` with a lower-fidelity copy (it drops
`header.judge`, and anything the run wrote after the packet) and silently make the `header` claims
below false — while exiting 0. It returns 2 with that reason when the resolved `--out` (or the
default, which is the packet's own directory) is this bundle. The committed board is produced by
`run --judge file` instead, which writes its own header; and the same `judge` command into `/tmp`
re-renders the identical six rows, which is the useful check.

`judge` also refuses a packet whose questions are not the frozen scenario's (a truncated packet
rendered a complete-looking board over a smaller denominator), and one that is not a judge packet at
all (exit 2, nothing written). Without that second guard a mistyped path — a `run-local.json`, a
`verdicts-baseline.json` — rendered a **0-row** scoreboard that kept the correct identity header and
the ingestion table while scoring nothing, and overwrote `run-{target}.json` /
`scoreboard-{target}.md` in the packet's own directory. An empty scoreboard is not a result:
`exit_code` treats an empty tally as a failure too, so that case cannot report success even if the
shape check were bypassed. For the same reason `run` refuses to publish a board that is **not
complete** into the bundle — whatever the cause (`--judge none`, a reader that failed, an empty
verdicts map, or a question left `blocked`): the rule is keyed on the produced tally
(`may_publish`, which delegates to `exit_code`), not on a flag. ⚠️ `blocked` is refused alongside
`unjudged`, on purpose: `blocked` means the run could not ask a question, so the board is not a
baseline a later run can be compared against. Point `--out` at a scratch directory to inspect it;
a refused run also drops its throwaway graph if `--drop-graph` was given. `judge` and `render --write` refuse the bundle outright, and `--verdicts-out` is refused there
unless the same invocation is also publishing its board: the baseline comes from ONE complete `run`,
and every file in this bundle — `run-local.json`, `scoreboard-local.md`, `judge-packet.json`,
`verdicts-baseline.json` — is written by it or not at all. (A refusal is decided BEFORE the packet is
written, so a refused run leaves all four untouched.)

`--judge fresh` spawns the fresh reader itself as `pi -p --no-session --no-tools`, in an EMPTY
sandbox directory rather than the repo root, and writes its verdicts. Both halves of that are
load-bearing: the reader is spawned while this run's *previous* `run-{target}.json` and
`scoreboard-{target}.md` are still on disk beside the packet, so a reader holding tools or sitting
in the repo could reproduce the previous verdicts and the instrument could not show that a slice
moved the product. If the reader cannot run (a refusal, a timeout, no parseable JSON) its
`verdicts` are empty, every row is recorded `unjudged`, and `exit_code` makes the run non-zero —
`A score is never invented to fill a row.` Both halves are pinned without a model or a subprocess
by `tests/test_capstone_state_value_13038.py`:
`test_a_reader_that_could_not_run_leaves_every_row_unjudged_and_exits_nonzero` (empty verdicts →
all `unjudged` → non-zero, with the clean-tally direction so it cannot pass on a constant) and
`test_blocked_is_never_folded_into_wrong` (parametrized over every judge verdict, `right`
included).

`--verdicts-out <path>` writes what the reader returned in the shape `--verdicts` reads, so the
re-score / re-render round-trip above is produced by the instrument rather than assembled by hand.
The verdict vocabulary is closed: a verdict outside `right`/`wrong`/`blocked`/`unjudged` (a
doctored `--verdicts` file, say) is recorded `unjudged` with the offending word named, never
rendered as a verdict and never dropped from the tally — whereas a document that is not a verdicts
map at all (a `run-local.json` passed as `--verdicts`) is REFUSED before anything is written.

**The committed board's header records how its verdicts were applied, not a reader command.**
`run-local.json` → `header.judge` is `{"mode": "file", "file": …/verdicts-baseline.json}`: the six
verdicts in that file were produced by a fresh `pi -p` reader given this scenario, and this run
applied them. A `--judge fresh` run — when the box's spawn gate admits a reader — records
`mode: fresh` with `judge_cmd: pi -p --no-session --no-tools` and spawns it in a throwaway
directory. The committed run applies stored verdicts because this box's spawn gate refused a
`--judge fresh` reader on 2026-11-17 (`[pi-gate] refusal #4`, exit 76, one-minute load above the
gate's bound) — the instrument then records every row `unjudged` and exits 1 rather than inventing
a score, so the reproducible one-command form is the `--judge file` one on `rerun.sh`.

**Cost:** one throwaway FalkorDB container (a fresh graph, `header.store`) and **17 ingestion ops**
(`run-local.json` → `ingest`); **20 product calls** (11 `search`, 6 `read_entity`, 2 `read_events`,
1 `judge`); **35.5 s** wall clock for the run above (`run-local.json` →
`header.duration_s`, 2026-11-17T20:25:19Z→2026-11-17T20:25:55Z) — the ingest + ask portion, because this run
applied stored verdicts. The reader itself is one `pi -p` agent invocation; the committed bundle
does not time it (a `--judge fresh` run records it as `judge.elapsed_s`). **No LLM tokens in the
ingest or the reads** — they are retrieval, entity reads and the goal comparator, so no provider key
and no model spend are needed there. The run deletes nothing by default (`--drop-graph` cleans up);
the graph name is printed in the scoreboard.

### What the committed artifacts let a reader check

Everything below is derivable from the four committed files, and nothing about how they were
produced is asserted beyond that:

* `run-local.json` → `header` is the run this bundle records (graph
  `capstone13038_20261117t202519z`, `2026-11-17T20:25:19Z → 2026-11-17T20:25:55Z`, 35.5 s) and
  `judge` says how its verdicts were applied (`mode: file` — the committed
  `verdicts-baseline.json`); a run scored with a fresh reader instead records `mode: fresh`,
  `elapsed_s` and `judge_cmd` — the exact invocation that produced them.
  `scoreboard-local.md` renders that header and those rows; `judge-packet.json` carries the same
  identity fields (`started`, `graph`, `product`, `store`, `narrative`, `retrieval_posture`) and the
  verdict-free payloads — its header IS this run's header, written before any verdicts are applied,
  so it has no `judge` key; `verdicts-baseline.json` carries the fresh reader's six verdicts, which
  this run applied. The tally is one value in the three files that carry verdicts — `run-local.json`
  (`tally`), `scoreboard-local.md` (the scoreboard line) and `verdicts-baseline.json`;
  `judge-packet.json` carries none. The value is 3 right · 3 wrong · 0 blocked · 0 unjudged.
* **Q1's cause must not be read as "Objects are structurally unreturnable".** Q2's own call refutes
  that — the plan Object *is* returned when the query matches its name. The recorded cause says
  only what the calls support: Q1's answer is assembled from an `entity_type`-scoped read, so an
  Object commitment is absent unless the caller guesses its name.
* **No judge, no verdict.** If the reader cannot run (a refusal, a timeout, no parseable JSON),
  every row is recorded `unjudged` and the run exits non-zero — both halves are pinned by
  `tests/test_capstone_state_value_13038.py`
  (`test_a_reader_that_could_not_run_leaves_every_row_unjudged_and_exits_nonzero`), and it is why
  no row is ever filled by guessing. A question the instrument could not ASK is `blocked` over
  every judge verdict, `right` included — a reader that answers `right` there is reading the
  packet's own framing, not a product response.
* **Q3's `right` is more favourable than the product's own structure warrants**, and the record
  says so: the replaced Point carries `status='superseded'` but no successor property (a Point
  names its successor by the `CORRECTS` edge, by design), and the signal this run records on it
  reads `names-its-successor=False`. The successor is legible because the *successor's* row carries
  `supersedes=[Q3]` and its content names what it replaced. A stricter scoring of "can the product
  name the successor from the replaced record's own structure" would fail Q3; the question as a
  customer asks it is satisfied. This is recorded as an observation, not used to move the verdict —
  and it is why Q3 is the row to re-read first when "change is legible" is next claimed.

---

## What the run measured about the product (findings, with the window the evidence is from)

| # | what was observed | evidence in this run | where it belongs |
|---|---|---|---|
| F1 | **A plan ended on a terminal slot is correctly EXCLUDED from the default scope — and that is all the customer gets.** The cancelled call carries `pipelineSlot='terminal'`, `pipelineOutcome='withdrawn'`, `isClosed=true`, and neither the question-shaped search nor the by-name search returns it; the state is visible only through a by-id `read_entity`. So the *currency* half holds (no retired plan served as current) while the *legibility* half does not: nothing answers "what was planned and never happened?". | Q5 calls `question` / `by_name` → 0 rows each; Q5 signals `by-id read_entity: stage='cancelled' slot='terminal' isClosed=True` and `NOT-SERVED: partner_call …` | the currency half is **#13034**'s subject; the legibility half is **#13302** (filed from this baseline). |
| F2 | **A retired Object is not served either — the exclusion works on both sides while the enumeration exists on neither.** The run creates the old onboarding flow and deprecates it (`deprecate_object` → `status=deprecated`); `search('onboarding flow', entity_type='object')` returns **0 rows**, exactly as the terminal plan does. The contrast is the *live* object: `search('launch event', entity_type='object')` → 1 row (Q2). | Q1 call `retired_flow` (0 rows) vs Q2 call `search` (1 row), plus the `object` → `deprecate_object` ingest ops | the same pair as F1: correct currency, missing enumeration (**#13302**). |
| F3 | **"What was withdrawn?" cannot be asked.** The retracted claim is excluded from both default-scope searches (correct for currency), and in a *search* it appears only when terminal scope is forced per call (Q6 `include_terminal`) — while a by-id `read_entity` returns it directly (Q6 `claim_node`), which is not a way to ask the question, because it needs the id already. There is no read whose *question* is "what ended, and how". | Q6 signals: `claim status='retracted' (a by-id read_entity)`; `include_terminal` vs `claim_node` | **#13302** (filed from this baseline). |
| F4 | **When the FTS leg finds nothing, the read falls back to a whole-corpus TF-IDF ranking and serves zero-overlap rows at score 0.0.** `search('has anything we relied on been withdrawn')` returned the Q4 pricing claim and an uptime statement (`score: 0.0, match: 'tfidf'`), neither sharing a word with the question. The read *does* label itself `degraded` — the defect is not a mislabel, it is that a customer-facing answer assembled from it looks like an answer. | Q6 signals: `NO-RELATION-TO-QUESTION: all 2 rows served … share no word with it`; `row.degraded = true` | recorded here rather than filed separately: the fallback is documented behaviour (`search_engine.fallback_tfidf`, "last resort"), so the repair is the read-surface one — **#13302** / a caller-side threshold — not a new ticket. |
| F5 | **A judgement is itself a served claim.** `record_judgement` mints a Point ("Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 …") and it then appears in **5 of the 6 point-scope searches** of this run (Q1 `committed_points`, Q3 `default`, Q3 `include_terminal`, Q6 `question`, Q6 `by_name`) and in none of the object-scope ones; the single exception is Q6's `include_terminal` search, whose only row is the retracted claim — so a "current commitments" answer contains a *judgement* alongside the commitment. | Q1/Q3/Q6 rows (`kind=statement`) | not a defect — the writer's design — but it is why Q1's answer is a decision plus a judgement rather than a list of commitments; worth knowing before reading Q1 as a pure lifecycle result. |
| F6 | **Target posture, not the state model:** the vector leg is absent (`no_embedder`) on this box, so every read is `degraded` and keyword-only — `header.retrieval_posture` reads vector 11 of 11 degraded, fallback 6 of 6 degraded, `fts` 0 of 11. The product's own **500 ms** collective read budget is the cap those reads run under; it was not exceeded in this run. | `header.retrieval_posture`, `header.notes` | a target property. It is recorded in the scoreboard header so a `wrong` row is never silently attributed to the state model when it was the read path. |

---

## What this run could not do (stated, not hidden)

* **The deployed API was not ingested into, deliberately.** The box's only credential is a
  *graph-scoped* key bound to the live graph a human relies on (`POST /v1/graphs` is
  session-authed, so a key cannot mint a disposable graph), and the REST surface has no
  pipeline-stage or goal-judgement writer — Q4 and Q5 could not be asked there at all. Writing the
  fixture's 17 write ops into a live agent-memory graph is contamination, not measurement. `--target
  deployed` therefore verifies reachability and **refuses** with that reason (exit 3). Making the
  deployed target usable needs a dedicated disposable graph plus ingestion adapters for the two
  writers — an addition to this tool, not a redesign.
* **Session capture was not exercised.** The SDK's recorder was retired (#4282); only the hosted
  `POST /v1/sessions` captures sessions, and it needs an extractor model. The narrative therefore
  enters through the **entity write doors**, which is the layer the state model governs.
* **The plan kind's pipeline is declared by the run**, because no shipped pack declares a pipeline
  for any kind yet (S3 declares the core default). `pipeline.seed_pipeline` is the shipped
  graph-override door, so this is the product's own mechanism — but it is a tenant declaration the
  run makes, and that is why it is in `narrative.json` (`declare_pipeline`) rather than implicit.

## How this scoreboard should move

Re-run after each slice lands; the numbers to watch are the three `wrong` rows, and each has an
owner already named in the table above.

| after | expect |
|---|---|
| **#13302** (one ended-scope read: "what ended, and how") | Q5 and Q6 both move to `right`: "planned and never happened" and "what was withdrawn" become questions a read can be asked, instead of an empty result and two unrelated rows. Q1 improves slightly (ended records enumerated consistently rather than excluded silently). |
| **#13034 / S1** (the currency predicate reads the position axis) | **already visible in this baseline**: an earlier run of this same scenario was served the cancelled plan as `status='live'` by a plain default-scope search; this run returns **0 rows** for both of Q5's searches. The currency half now holds in the payloads even though Q5's verdict stays `wrong` — which is the distinction the instrument exists to make (a fixed currency rule is not the same thing as an answerable question). |
| **#13100 / S7** (plans are their own kind, events are occurrences) | Q2 stays `right` but stops depending on a run-declared pipeline; Q5's enumeration becomes a declared read rather than a name search. |
| **S0 / S4** (the write door and the narrowed vocabulary) | nothing here should move the scoreboard — that is the point of measuring *behaviour*: a vocabulary slice that reds a drift test and moves no question has not yet been proven to help a customer. |

The instrument is the arbiter for that last row: **if a slice moves the drift test and not the
scoreboard, the scoreboard wins.**
