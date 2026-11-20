# Capstone #13038 — the state model, before and after this strand

The instrument is described in `README.md` one level up and was built in #13038.
This directory holds the two runs that bracket the implementation work, so the
"6 right" claim is checkable from the tree rather than from a report.

The two halves were **not** scored the same way — the before arm's verdicts come
from a committed baseline file, the after arm's from a fresh reader — so the pair
is a comparison of two states of the product, not one measurement applied twice.
`## Before` and `## Honest limits` say exactly what that does and does not let a
reader claim; `## The artefact digests` makes the "verbatim, unedited" guarantee
checkable.

## What the board measures

Six questions a customer would ask of a business memory, scored right/wrong
against a frozen narrative (`narrative.json`), and judged **against that
narrative as the key** — not by substring matching and not by internal fields.
Each row carries a "what a wrong answer means" line, so a verdict is about what
the answer does to a customer.

## Before — `3 right · 3 wrong · 0 blocked · 0 unjudged` (the baseline's verdicts, applied to this run's payloads)

`scoreboard-before.md` · `run-before.json` · product **`9e9fc232`**, run
**2026-11-18T16:40:50Z → 16:44:05Z** (local target, 195.2 s).

Per-question verdicts: **Q1, Q5, Q6 wrong**; Q2, Q3, Q4 right.

⛔ **That board is not a judgement of `9e9fc232`.** The run was scored with
`--judge file`, so every verdict comes from `verdicts-baseline.json`, and for a
row that was asked and not blocked the instrument takes the verdict straight
from that map (`tools/capstone_state_value.py`: `verdict = j.get("verdict") or
"unjudged"`). For a run of this shape the tally is therefore a **constant of the
verdict file** — 3 right · 3 wrong, whatever the product returned — and the six
`verdict`/`because` pairs in `run-before.json` are byte-identical to `.Q1`–`.Q6`
of that file. Its `_provenance` names the packet it was read from —
`docs/evidence/13038-capstone/judge-packet.json`, graph
`capstone13038_20261117t184234z` — and that packet's own
`header.provenance.product.commit` is `446bd09a`: a different run, at a different
product. (`_provenance` itself carries no product commit; the attribution is one
hop away, in the packet.) Nothing about `9e9fc232` entered the verdicts.

So read this board as a **comparison reference** — which is what the instrument's
documented `--judge file` mode provides — and not as evidence that this product
scored 3/6. What this run *is* evidence for is the payloads in `run-before.json`:
what `9e9fc232` returned, call by call.

Four disclosures that bound any use of it:

- **The judge was `--judge file`, not a fresh reader.** `run-before.json` →
  `judge: {mode: "file", file: "…/verdicts-baseline.json"}`. The verdict prose
  is a re-used baseline; where it disagrees with this run's payload, the payload
  wins — the third and fourth bullets are two clauses that do, and the boards
  themselves are left as the instrument emitted them.
- **The read path was degraded in every read that ran a retrieval leg.** Of the
  13 reads carrying a `read_status`, 11 are `degraded`; the other 2 are the
  leg-less `ended_scope` searches (Q5, Q6), which run no legs at all and report
  `available` for that reason alone. Per leg: `vector` 11/11 (`load_timeout`,
  `load_in_flight`), `fallback` 9/9, `fts` 4/11. The instrument says explicitly
  that a row failing because unrelated content came back is the read path, not
  the model.
- **Q5's baseline prose is refuted by this run's own payload.** It says — in the
  board's table row and detail, and in `run-before.json` →
  `rows[Q5].because` — that *"the only row served for the cancelled call carried
  status 'live' as a current result"*. This run's payload for that row records
  `served_for_the_cancelled_call: []` and `the_cancelled_plan.status: null`:
  nothing was served for the cancelled call, and the plan's status is null, not
  `live`.
- **Q1's baseline `cause` names a leg this run never ran.** It attributes the
  failure partly to *"the `valid_time` leg reporting `state_read_incomplete`"*,
  but Q1's three calls run `vector` / `fts` / `structural` / `fallback` only —
  `valid_time` appears nowhere in Q1 — and `state_read_incomplete` occurs only
  inside that inherited clause, nowhere in this run's payloads. The run's sole
  `valid_time` read — Q3's `include_terminal` search, reason
  `no-prefer-latest-intent` — is **not** degraded, exactly as the board's own
  posture table says. What Q1's payload does support is the rest of that
  sentence: the object search returned 0 rows, and the point search reports
  `read_status=degraded` on `fallback` / `fts` / `vector`.

**A fresh-judged 3/6 does exist — at a different product.** `13302/README.md`
records `bf3a7b8e77bf` (the parent commit of #13302) scored by a fresh reader at
81.0 s: *3 right · 3 wrong — Q1, Q5, Q6 wrong*. That is the same tally as an
actual judgement, taken at `bf3a7b8e77bf` rather than at `9e9fc232`. Re-running
the before arm with `--judge fresh` at `9e9fc232` is the only way this board
becomes a judgement of it (`rerun.sh`, ~2–4 min by `## How to re-run`).

## After — `6 right · 0 wrong · 0 blocked · 0 unjudged`

`scoreboard-after.md` · `run-after.json` · product **`28ca66878d`**, run
**2026-11-20T17:10:09Z → 17:12:51Z** on `main` (local target, 161.5 s), judge
**fresh** (`pi -p --no-session --no-tools`, 84.0 s).

**Zero degraded legs on this run** — `vector`, `fts`, `structural` and
`valid_time` all report `degraded: 0` in `run-after.json` →
`header.retrieval_posture.legs`. That is not the same as "every leg reported
ok", which the numbers do not support once the **reason tokens** are read
alongside the counts: `vector` `["ok"]` 9/9; `fts` `["ok", "empty_results"]` 9/9;
`structural` **`["empty_results"]` 9 of 9**; `valid_time` `["no-stale-entries",
"no-prefer-latest-intent"]` 5/5 with no `ok` at all. Only `vector` reported `ok`
on every one of its runs.

⚠️ **The read path is not clean either — but the instrument's own wording for why
is wrong on this run.** Six row-level signals fire, two each on Q1, Q5 and Q6
(`current commitments`, `onboarding flow`, `what was planned and never happened`,
`thursday partner call`, `has anything we relied on been withdrawn`,
`beach club confirmed booking`), and each ends *"a degraded read ranked the corpus
rather than answering"*. That sentence is emitted from the lexical overlap test
alone (`tools/capstone_state_value.py`, the `NO-RELATION-TO-QUESTION` branch),
with no reference to `read_status` or to any degraded leg — so on a run whose
every leg is `degraded: 0` it attributes a ranking to a degradation that did not
happen. What the signals measure is **zero query-word overlap**: every served row
shares no word with the query.

That is a lexical fact about the rows, not a verdict about them, and two of the
six flag the **target records**. The `current commitments` rows are the 12
December launch plan, the Q4 uptime goal and its 99.9 % target — records the
board's own Q1 verdict counts towards *"exactly the current set"*, not evidence
of a bad read. The `has anything we relied on been withdrawn` rows are the
retracted beach-club claim and the superseded Q3 decision, which is precisely
what that question asks for. So the accurate statement: **the six answers are
right, while for six of the queries the served rows share no word with the
question text.** Whether any of those is a read-path defect is not established by
an overlap heuristic, and this bundle does not claim it is.

## What the delta can and cannot be attributed to

⚠️ **Two things bound this section before it starts.**

1. **The two halves were scored differently** — a verdict file from another
   packet at another product (before), a fresh reader (after). A judge is not an
   inert ruler, so the 3 → 6 pair is a comparison of two states *under two
   judges*, not one measurement applied twice.
2. **Q1 is JUDGE-VARIABLE, and that is already a recorded decision this bundle
   does not re-open.** `13302/README.md` measures it at one fixed product
   (`353c225703af`): `wrong` at 92.9 s and `right` at 97.2 s, on payloads
   differing *only in ids and timestamps*, and states that the claim its change
   supports is *"Q5 and Q6 flip, Q1–Q4 do not regress"*, not "everything is right
   now". Consistent with that, **Q1 moves `wrong` → `right` in this pair too and
   is attributed to no slice here.** The four slices below are offered as the
   explanation of the **Q5/Q6** flip only.

Also reconciled, because the tree would otherwise read as 6 → 3 → 6: **a 6/6
board was already committed before this one** — `13302/scoreboard-after-run2.md`
(product `353c225703af`, run `2026-11-18T03:00:10Z`, fresh reader 97.2 s), beside
its own 5/1 board for the *same* commit. That product is a **#13302 branch head
`main` does not contain** (`9e9fc232…353c225703af` diverged: ahead 1, behind 36),
so it is not a prior state of `main` and this bundle is not a regression claim;
and its product differs from this bundle's before arm, so it cannot be the
baseline here. The 5/1 → 6/0 step inside `13302/` and the 3 → 6 step here are two
different comparisons, and in both the moving row at the same product is Q1.

Among slices **absent from the before product** — a necessary condition for
explaining the delta, never proof that a slice did — ancestry gives:

| slice | issue / PR | status in the before product |
|---|---|---|
| a MENTION of a plan is not its OCCURRENCE | **#13578** | **absent** — merged 2026-11-18T19:15Z, after the before run |
| the writer refuses a kind's RENDERING, not its `__eq__` | **#13586** | **absent** — merged 2026-11-18T21:31Z |
| the occurrence-kind spellings are EVIDENCE, both legal forms matched | **#13604** | **absent** — merged 2026-11-18T23:24Z |
| commitments by kind; endings reached by asking | **#13833** | **absent** — merged 2026-11-19T21:49Z |

These were **already in** `9e9fc232`, so they cannot account for the delta and
should not be credited with it: the currency rule reading the position axis
(#13034 / #13075, merged 2026-11-17), `goalState`↔`status` consistency
(#13035 / #13065, merged 2026-11-17), and plans-vs-events (#13100 / #13115,
merged 2026-11-17). Note #13034 itself is still **OPEN** — its measured fix (the
predicate reading the position axis) shipped in #13075.

## How to re-run

```bash
bash docs/evidence/13038-capstone/rerun.sh --out /tmp/capstone-now --judge fresh
```

~2–4 minutes against a throwaway local FalkorDB. The wrapper refuses to publish
an unjudged board into this bundle, because an all-`unjudged` run is not a
baseline.

## The artefact digests

The four artefacts are committed **verbatim** — the instrument's own output, not
re-rendered and not annotated by hand. Every correction above lives in this
README, precisely so that the two `.json` records and the two boards stay
checkable bytes. Their `sha256` digests:

| file | `sha256` |
|---|---|
| `run-before.json` | `488243bcf5f7a822416da0a5a16f5370eb7a28dbeeeccd27068aa8f37c5be555` |
| `run-after.json` | `88c731627a9968e5a97321bf4c6f4b82b4d43f7e6438776962ef85004f3c3a89` |
| `scoreboard-before.md` | `394eb77a26b77c97408a7d58d28e0a9bec345e03f18452f4c1bb8197b5a5816a` |
| `scoreboard-after.md` | `8e1868fd31cd7bd40baa45e15a089a96f887b79f945c0b559d6fc58079b07e82` |

```bash
sha256sum run-before.json run-after.json scoreboard-before.md scoreboard-after.md
```

This README's own digest is recorded in the commit message instead — a file
cannot carry its own `sha256` and still be unchanged by carrying it.

## Honest limits

- **Six questions**, chosen to exercise the shapes the state model governs. Not
  a general quality score: 6/6 means these six are right, not that every read
  path is.
- The judge is an **LLM reader**, so a verdict is a judgement, not an assertion.
  The per-question evidence — what the product returned, which reads ran, which
  legs degraded, and each row-level signal — is in the `.json` beside each
  board, so a disputed verdict can be re-read rather than argued.
- The two runs are **different products scored by different judges** (a
  transported baseline file, then a fresh reader), so this is a comparison of two
  states of the product, not a controlled A/B. Anything else that landed between
  them is in the delta and not attributed here.
- **The before arm's verdicts are not this product's**, so the 3 in 3 → 6 is a
  comparison reference rather than a measurement. The measured half of the before
  arm is its payloads, and two baseline clauses (Q5's, Q1's `cause`) are
  contradicted by them — the payload wins, and both are named in `## Before`.
- **One payload signal is unverified and belongs here rather than in a
  conclusion.** `run-after.json` → `rows[Q6].calls[0]` asks
  `search('has anything we relied on been withdrawn', point,
  include_terminal: false)` — with no `ended_scope` — and returns rows whose
  `status` is `retracted` and `superseded` on the `keyword_only` retrieval path,
  while Q3's identical `include_terminal: false` call on the `hybrid` path
  returns live rows only. If the default scope is meant to exclude terminal
  records, that is the "retired things served as current" failure this strand
  closes on. Whether the `keyword_only` path is *meant* to differ is
  **UNVERIFIED** — it needs execution, not a document read. The settling command
  is a live `search(query='has anything we relied on been withdrawn',
  entity_type='point', include_terminal=False)` against a graph holding a
  retracted point.
- **The six `NO-RELATION-TO-QUESTION` signals are a word-overlap heuristic that
  mislabels its own cause on this run** (see `## After`); they are reported here
  as recorded, not as a read-path diagnosis.
