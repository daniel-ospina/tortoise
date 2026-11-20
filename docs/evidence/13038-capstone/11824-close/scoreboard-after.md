# Capstone #13038 — the state model measured by customer value

**Run:** 2026-11-20T17:10:09Z → 2026-11-20T17:12:51Z (target `local`, 161.5s)
**Product:** tortoise-sdk @ 28ca66878d7c617a2f28a8025ddaa40352adfcb2
**Store:** `docker://<redacted>@127.0.0.1:16393/tortoise_test_matrix` · graph `capstone13038_20261120t171009z`
**Scenario:** `docs/evidence/13038-capstone/narrative.json` (frozen 2026-11-16)

## Scoreboard: 6 right · 0 wrong · 0 blocked · 0 unjudged

| # | question | verdict | what the product answered | why |
|---|---|---|---|---|
| **Q1** | What are we currently committed to? | **right** | search('what are we currently committed to — pricing, onboarding, launch and uptime', point) → 2 rows 2/2 on-topic · search('current commitments', object) → 3 rows 0/3 on-topic · search('onboarding flow', object) → 2 rows 0/2 on-topic | The served rows give exactly the current set — Q4 pricing at $39/seat (live, superseding Q3), the 12 December launch plan (scheduled, not closed), and the Q4 uptime goal with its 99.9% target — and neither the superseded Q3 pricing nor the retired onboarding flow appears as current. |
| **Q2** | Did we ever plan a launch event, and did it happen? | **right** | search('launch event', object) → 3 rows 1/3 on-topic · entity → {"pipelineStageId": "scheduled", "pipelineSlot": "entry", "isClosed": false} · events → 2 | The launch plan node is served with stage 'scheduled', slot 'entry', isClosed false — a plan that has not occurred — so planned and happened are distinguishable. |
| **Q3** | Why did the pricing decision change, and what replaced it? | **right** | search('pricing decision Q3 Q4', point) → 2 rows 1/2 on-topic · search('pricing decision Q3 Q4', point, include_terminal) → 4 rows 2/4 on-topic · entity → {"status": "superseded"} · entity → {"status": "live"} | The payload shows Q3 ($29/seat) as superseded and Q4 ($39/seat) as live and naming the Q3 id it supersedes, so the successor is identified without inventing a rationale. |
| **Q4** | Is the uptime goal met, and on what basis? | **right** | entity → {"status": "live", "goalState": "unmet"} · judge → unmet (99.72 vs >= 99.9) · events → 2 | The judgement states unmet on the basis of the November reading 99.72 compared with the >= 99.9 target (comparison false), i.e. measured, not declared by a status word. |
| **Q5** | What was planned and never happened? | **right** | search('what was planned and never happened', object) → 2 rows 0/2 on-topic · search(None, object, ended_scope) → 2 rows · search('thursday partner call', object) → 2 rows 0/2 on-topic · entity → {"pipelineStageId": "cancelled", "pipelineSlot": "terminal", "isClosed": true} | The ended-scope read plus the by-id read serve the Thursday partner call on 6 November as stage 'cancelled', slot 'terminal', isClosed true, ending 'withdrawn' with no occurrence, so the cancelled plan is found and not reported as upcoming. |
| **Q6** | Has anything we relied on been withdrawn? | **right** | search('has anything we relied on been withdrawn', point) → 2 rows 0/2 on-topic · search(None, point, ended_scope) → 2 rows · search('beach club confirmed booking', point) → 2 rows 0/2 on-topic · search('beach club confirmed booking', point, include_terminal) → 4 rows 1/4 on-topic · entity → {"status": "retracted"} | The beach-club confirmation is served as status 'retracted' with an endedAt (and is absent from the live current-commitment rows), so it is stopped being counted and the withdrawal is stated. |

## Per-question detail

### Q1 — What are we currently committed to? → **right**

- **what the product answered:** `search('what are we currently committed to — pricing, onboarding, launch and uptime', point)` → 2 rows [related-to-question: 2/2]: *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.040719196192490745); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.04058699101004759) · `search('current commitments', object)` → 3 rows [related-to-question: 0/3]: *Launch event — 12 December at the beach club* (kind=core:plan, status=None, rrf=None) [stage=entry/]; *Q4 uptime* (kind=core:goal, status=live, rrf=None); *Uptime target — 99.9% or better* (kind=core:target, status=None, rrf=None) · `search('onboarding flow', object)` → 2 rows [related-to-question: 0/2]: *Uptime target — 99.9% or better* (kind=core:target, status=live, rrf=0.538411527050795); *Q4 uptime* (kind=core:goal, status=live, rrf=0.524774219713759)
- **evidence it cited / signals:** search: read_status='available'; search: read_status='available'; search: read_status='available'; found: q4_pricing (1a1f9f09af9-d7fe6d17be05); NO-RELATION-TO-QUESTION: all 3 rows served for 'current commitments' share no word with it — a degraded read ranked the corpus rather than answering; NO-RELATION-TO-QUESTION: all 2 rows served for 'onboarding flow' share no word with it — a degraded read ranked the corpus rather than answering
- **verdict reason:** The served rows give exactly the current set — Q4 pricing at $39/seat (live, superseding Q3), the 12 December launch plan (scheduled, not closed), and the Q4 uptime goal with its 99.9% target — and neither the superseded Q3 pricing nor the retired onboarding flow appears as current.
- **what a wrong answer means:** If the replaced Q3 pricing decision or the retired onboarding flow appears, the product serves retired things as current — the trust failure this model exists to remove.

### Q2 — Did we ever plan a launch event, and did it happen? → **right**

- **what the product answered:** `search('launch event', object)` → 3 rows [related-to-question: 1/3]: *Q4 uptime* (kind=core:goal, status=live, rrf=0.02459016393442623); *Uptime target — 99.9% or better* (kind=core:target, status=live, rrf=0.024193548387096774); *Launch event — 12 December at the beach club* (kind=core:plan, status=live, rrf=0.01639344262295082) · `read_entity` → {"isClosed": false, "name": "Launch event \u2014 12 December at the beach club", "objectKind": "core:plan", "pipelineOutcome": "", "pipelineSlot": "entry", "pipelineStageId": "scheduled"} · `read_events` → 2 events
- **evidence it cited / signals:** search: read_status='available'; stage='scheduled' slot='entry' isClosed=False status=None; found: launch_plan (1a1f9f00f66-8ecdb948ecbf)
- **verdict reason:** The launch plan node is served with stage 'scheduled', slot 'entry', isClosed false — a plan that has not occurred — so planned and happened are distinguishable.
- **what a wrong answer means:** If 'planned' and 'happened' cannot be told apart, plans are being stored as facts — the answer would let an agent claim an event occurred.

### Q3 — Why did the pricing decision change, and what replaced it? → **right**

- **what the product answered:** `search('pricing decision Q3 Q4', point)` → 2 rows [related-to-question: 1/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.040719196192490745); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.04058699101004759) · `search('pricing decision Q3 Q4', point, include_terminal)` → 4 rows [related-to-question: 2/4]: *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=0.04058699101004759); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0404631798074421); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.039938556067588324); *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=0.0234375) · `read_entity` → {"content": "Q3 pricing: the entry tier is $29 per seat.", "status": "superseded", "validTo": "2026-11-20T17:10:57.529685+00:00"} · `read_entity` → {"content": "Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 decision.", "status": "live"}
- **evidence it cited / signals:** search: read_status='available'; search: read_status='available'; replaced node status='superseded', names-its-successor=True (by edge — the successor's own row '1a1f9f09af9-d7fe6d17be05' names it (a Point's successor is the CORRECTS edge)); found: q4_pricing (1a1f9f09af9-d7fe6d17be05)
- **verdict reason:** The payload shows Q3 ($29/seat) as superseded and Q4 ($39/seat) as live and naming the Q3 id it supersedes, so the successor is identified without inventing a rationale.
- **what a wrong answer means:** If the successor cannot be named, change is illegible — the agent cannot explain a change to a human.

### Q4 — Is the uptime goal met, and on what basis? → **right**

- **what the product answered:** `read_entity` → {"goalState": "unmet", "name": "Q4 uptime", "objectKind": "core:goal", "status": "live"} · `judge` → {"state": "unmet", "reason": "state-unchanged", "comparator": ">=", "threshold": 99.9, "readingValue": 99.72, "readingAsOf": "2026-11-15T23:59:00+00:00", "readingId": "1a1f9f0c518-67355d20af9a", "comparison": false} · `read_events` → 2 events
- **evidence it cited / signals:** goalState='unmet' status='live'; judgement cites reading=99.72 vs >= 99.9 → unmet; NOT-SERVED: uptime_goal (1a1f9f0b83c-d2dabe4555a1)
- **verdict reason:** The judgement states unmet on the basis of the November reading 99.72 compared with the >= 99.9 target (comparison false), i.e. measured, not declared by a status word.
- **what a wrong answer means:** If the answer cites a status rather than the reading, achievement is being declared instead of measured.

### Q5 — What was planned and never happened? → **right**

- **what the product answered:** `search('what was planned and never happened', object)` → 2 rows [related-to-question: 0/2]: *Q4 uptime* (kind=core:goal, status=live, rrf=0.504496507420787); *Uptime target — 99.9% or better* (kind=core:target, status=live, rrf=0.503371211551832) · `search(None, object, ended_scope)` → 2 rows: *Old onboarding flow* (kind=process, status=deprecated, rrf=None) [no linked event]; *Thursday partner call — 6 November* (kind=core:plan, status=None, rrf=None) [stage=terminal/withdrawn, no linked event, endedAt=2026-11-20T17:11:04.697420+00:00] · `search('thursday partner call', object)` → 2 rows [related-to-question: 0/2]: *Q4 uptime* (kind=core:goal, status=live, rrf=0.536121731203842); *Uptime target — 99.9% or better* (kind=core:target, status=live, rrf=0.523403448172588) · `read_entity` → {"isClosed": true, "name": "Thursday partner call \u2014 6 November", "objectKind": "core:plan", "pipelineOutcome": "withdrawn", "pipelineSlot": "terminal", "pipelineStageId": "cancelled"}
- **evidence it cited / signals:** search: read_status='available'; search: read_status='available'; search: read_status='available'; by-id read_entity: stage='cancelled' slot='terminal' isClosed=True; found: partner_call (1a1f9f0b2b3-0f36550185d9); NO-RELATION-TO-QUESTION: all 2 rows served for 'what was planned and never happened' share no word with it — a degraded read ranked the corpus rather than answering; NO-RELATION-TO-QUESTION: all 2 rows served for 'thursday partner call' share no word with it — a degraded read ranked the corpus rather than answering
- **verdict reason:** The ended-scope read plus the by-id read serve the Thursday partner call on 6 November as stage 'cancelled', slot 'terminal', isClosed true, ending 'withdrawn' with no occurrence, so the cancelled plan is found and not reported as upcoming.
- **what a wrong answer means:** If the cancelled partner call cannot be found, the product cannot answer the one question a mutable-event model loses.

### Q6 — Has anything we relied on been withdrawn? → **right**

- **what the product answered:** `search('has anything we relied on been withdrawn', point)` → 2 rows [related-to-question: 0/2]: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=None) [no linked event, endedAt=2026-11-20T17:11:13.596738+00:00]; *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=None) [successor=1a1f9f09af9-d7fe6d17be05, no linked event, endedAt=2026-11-20T17:10:57.529685+00:00] · `search(None, point, ended_scope)` → 2 rows: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=None) [no linked event, endedAt=2026-11-20T17:11:13.596738+00:00]; *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=None) [successor=1a1f9f09af9-d7fe6d17be05, no linked event, endedAt=2026-11-20T17:10:57.529685+00:00] · `search('beach club confirmed booking', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.488511323928833); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.47858142852783203) · `search('beach club confirmed booking', point, include_terminal)` → 4 rows [related-to-question: 1/4]: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=0.04098360655737705); *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=0.024193548387096774); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.023809523809523808); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0234375) · `read_entity` → {"content": "The beach club confirmed our 12 December booking.", "status": "retracted"}
- **evidence it cited / signals:** search: read_status='available'; search: read_status='available'; search: read_status='available'; search: read_status='available'; claim status='retracted' (a by-id read_entity); the ended-scope read served 2 ended record(s) without the id being supplied — each row states how it ended: 1a1f9f0d524-3dfdb25f00fe: retracted; 1a1f9f01766-aa41b0f6c8b3: superseded; found: beach_club_claim (1a1f9f0d524-3dfdb25f00fe); NO-RELATION-TO-QUESTION: all 2 rows served for 'has anything we relied on been withdrawn' share no word with it — a degraded read ranked the corpus rather than answering; NO-RELATION-TO-QUESTION: all 2 rows served for 'beach club confirmed booking' share no word with it — a degraded read ranked the corpus rather than answering
- **verdict reason:** The beach-club confirmation is served as status 'retracted' with an endedAt (and is absent from the live current-commitment rows), so it is stopped being counted and the withdrawal is stated.
- **what a wrong answer means:** If the withdrawn claim still counts, belief is contaminated by retired evidence — the agent will act on something we retracted.

## Ingestion (the narrative through the product's write doors)

| op | ref | ok | detail |
|---|---|---|---|
| `declare_pipeline` | core:plan | yes | {"pipeline": "core:plan", "default": "scheduled", "source": "graph-override", "stages": ["scheduled", "held", "missed", "cancelled"]} |
| `object` | launch_plan | yes | core:plan → 1a1f9f00f66-8ecdb948ecbf |
| `advance_stage` | launch_plan | yes | {"applied": true, "reason": "", "stage": "scheduled", "slot": "entry", "outcome": null} |
| `point` | q3_pricing | yes | decision → 1a1f9f01766-aa41b0f6c8b3 |
| `point` | q4_pricing | yes | decision → 1a1f9f09af9-d7fe6d17be05 |
| `supersede_point` | q3_pricing | yes | superseded by 1a1f9f09af9-d7fe6d17be05 |
| `object` | old_onboarding | yes | process → 1a1f9f0ab35-d5f2407597ab |
| `deprecate_object` | old_onboarding | yes | status=deprecated |
| `object` | partner_call | yes | core:plan → 1a1f9f0b2b3-0f36550185d9 |
| `advance_stage` | partner_call | yes | {"applied": true, "reason": "", "stage": "cancelled", "slot": "terminal", "outcome": "withdrawn"} |
| `object` | uptime_goal | yes | core:goal → 1a1f9f0b83c-d2dabe4555a1 |
| `object` | uptime_target | yes | core:target → 1a1f9f0bb35-617f7a017924 |
| `reading` | uptime_oct | yes | 99.95percent asOf 2026-10-31T23:59:00+00:00 |
| `reading` | uptime_nov | yes | 99.72percent asOf 2026-11-15T23:59:00+00:00 |
| `judge_goal` | uptime_goal | yes | state=unmet value=99.72 reading=1a1f9f0c518-67355d20af9a |
| `point` | beach_club_claim | yes | statement → 1a1f9f0d524-3dfdb25f00fe |
| `retract_point` | beach_club_claim | yes | status=retracted |

## Target and honesty notes

- session capture — the SDK recorder was retired (#4282); only the hosted POST /v1/sessions captures sessions, and it needs an extractor model. This run therefore writes the state layer through the entity doors, which is the surface the state model governs.

### Retrieval posture of the target

| leg | reads | degraded | reasons |
|---|---|---|---|
| `fts` | 9 | 0 | ok, empty_results |
| `vector` | 9 | 0 | ok |
| `structural` | 9 | 0 | empty_results |
| `valid_time` | 5 | 0 | no-stale-entries, no-prefer-latest-intent |

### Judge

- mode: `fresh` · cmd: `pi -p --no-session --no-tools` · 84.0s
