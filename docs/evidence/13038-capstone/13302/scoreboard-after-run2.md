# Capstone #13038 — the state model measured by customer value

**Run:** 2026-11-18T03:00:10Z → 2026-11-18T03:02:39Z (target `local`, 148.8s)
**Product:** tortoise-sdk @ 353c225703affc3d8b46972216bf998da8781478
**Store:** `docker://<redacted>@127.0.0.1:16417/tortoise_test_matrix` · graph `capstone13038_20261118t030010z`
**Scenario:** `docs/evidence/13038-capstone/narrative.json` (frozen 2026-11-16)

## Scoreboard: 6 right · 0 wrong · 0 blocked · 0 unjudged

| # | question | verdict | what the product answered | why |
|---|---|---|---|---|
| **Q1** | What are we currently committed to? | **right** | search('what are we currently committed to — pricing, onboarding, launch and uptime', point) → 2 rows 2/2 on-topic · search('current commitments', object) → 0 rows · search('onboarding flow', object) → 0 rows | The current-commitment read served Q4 pricing ($39/seat, live, supersedes the Q3 id) and the Q4 uptime target/judgement as live, and the retired onboarding flow and superseded Q3 pricing were excluded from the current scope, so nothing retired is served as current. |
| **Q2** | Did we ever plan a launch event, and did it happen? | **right** | search('launch event', object) → 1 rows 1/1 on-topic · entity → {"pipelineStageId": "scheduled", "pipelineSlot": "entry", "isClosed": false} · events → 2 | The launch event node is a core:plan with stage 'scheduled', slot 'entry', isClosed=false, and the only graph events are uptime measurements — no occurrence of the launch exists, so it is shown as planned, not as having happened. |
| **Q3** | Why did the pricing decision change, and what replaced it? | **right** | search('pricing decision Q3 Q4', point) → 2 rows 1/2 on-topic · search('pricing decision Q3 Q4', point, include_terminal) → 3 rows 2/3 on-topic · entity → {"status": "superseded"} · entity → {"status": "live"} | The Q3 node is served as superseded and the live Q4 node is served with content 'the entry tier becomes $39 per seat, replacing the Q3 decision' plus a supersedes link to the Q3 id, naming the successor without inventing any rationale. |
| **Q4** | Is the uptime goal met, and on what basis? | **right** | entity → {"status": "live", "goalState": "unmet"} · judge → unmet (99.72 vs >= 99.9) · events → 2 | The judgement returns state=unmet with comparator '>=', threshold 99.9, reading 99.72 as of 2026-11-15, comparison=false, so the verdict rests on the reading versus the target rather than a status word. |
| **Q5** | What was planned and never happened? | **right** | search('what was planned and never happened', object) → 0 rows · search(None, object, ended_scope) → 2 rows · search('thursday partner call', object) → 0 rows · entity → {"pipelineStageId": "cancelled", "pipelineSlot": "terminal", "isClosed": true} | The Thursday partner call (6 November) is served as a plan in stage 'cancelled', slot 'terminal', outcome 'withdrawn', isClosed=true, while the 12 December launch remains scheduled rather than being reported as never having happened. |
| **Q6** | Has anything we relied on been withdrawn? | **right** | search('has anything we relied on been withdrawn', point) → 2 rows 0/2 on-topic · search(None, point, ended_scope) → 2 rows · search('beach club confirmed booking', point) → 2 rows 0/2 on-topic · search('beach club confirmed booking', point, include_terminal) → 1 rows 1/1 on-topic · entity → {"status": "retracted"} | The beach-club confirmation claim is served with status 'retracted' and an endedAt timestamp, and it is excluded from the default (current) scope, so the product both stops counting it and can say it was withdrawn. |

## Per-question detail

### Q1 — What are we currently committed to? → **right**

- **what the product answered:** `search('what are we currently committed to — pricing, onboarding, launch and uptime', point)` → 2 rows [related-to-question: 2/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=1.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.666666666666667) · `search('current commitments', object)` → 0 rows · `search('onboarding flow', object)` → 0 rows
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; search: read_status='degraded'; found: q4_pricing (1a1ec992a2c-bfc03985f6da); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The current-commitment read served Q4 pricing ($39/seat, live, supersedes the Q3 id) and the Q4 uptime target/judgement as live, and the retired onboarding flow and superseded Q3 pricing were excluded from the current scope, so nothing retired is served as current.
- **what a wrong answer means:** If the replaced Q3 pricing decision or the retired onboarding flow appears, the product serves retired things as current — the trust failure this model exists to remove.

### Q2 — Did we ever plan a launch event, and did it happen? → **right**

- **what the product answered:** `search('launch event', object)` → 1 rows [related-to-question: 1/1]: *Launch event — 12 December at the beach club* (kind=core:plan, status=live, rrf=4.0) · `read_entity` → {"isClosed": false, "name": "Launch event \u2014 12 December at the beach club", "objectKind": "core:plan", "pipelineOutcome": "", "pipelineSlot": "entry", "pipelineStageId": "scheduled"} · `read_events` → 2 events
- **evidence it cited / signals:** search: read_status='degraded'; stage='scheduled' slot='entry' isClosed=False status=None; found: launch_plan (1a1ec9918d7-963e8fec3888); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The launch event node is a core:plan with stage 'scheduled', slot 'entry', isClosed=false, and the only graph events are uptime measurements — no occurrence of the launch exists, so it is shown as planned, not as having happened.
- **what a wrong answer means:** If 'planned' and 'happened' cannot be told apart, plans are being stored as facts — the answer would let an agent claim an event occurred.

### Q3 — Why did the pricing decision change, and what replaced it? → **right**

- **what the product answered:** `search('pricing decision Q3 Q4', point)` → 2 rows [related-to-question: 1/2]: *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `search('pricing decision Q3 Q4', point, include_terminal)` → 3 rows [related-to-question: 2/3]: *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=3.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `read_entity` → {"content": "Q3 pricing: the entry tier is $29 per seat.", "status": "superseded", "validTo": "2026-11-18T03:00:23.212217+00:00"} · `read_entity` → {"content": "Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 decision.", "status": "live"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; replaced node status='superseded', names-its-successor=False; found: q4_pricing (1a1ec992a2c-bfc03985f6da); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The Q3 node is served as superseded and the live Q4 node is served with content 'the entry tier becomes $39 per seat, replacing the Q3 decision' plus a supersedes link to the Q3 id, naming the successor without inventing any rationale.
- **what a wrong answer means:** If the successor cannot be named, change is illegible — the agent cannot explain a change to a human.

### Q4 — Is the uptime goal met, and on what basis? → **right**

- **what the product answered:** `read_entity` → {"goalState": "unmet", "name": "Q4 uptime", "objectKind": "core:goal", "status": "live"} · `judge` → {"state": "unmet", "reason": "state-unchanged", "comparator": ">=", "threshold": 99.9, "readingValue": 99.72, "readingAsOf": "2026-11-15T23:59:00+00:00", "readingId": "1a1ec993978-6d7081ad49d0", "comparison": false} · `read_events` → 2 events
- **evidence it cited / signals:** goalState='unmet' status='live'; judgement cites reading=99.72 vs >= 99.9 → unmet; NOT-SERVED: uptime_goal (1a1ec993536-34095b5aeee7)
- **verdict reason:** The judgement returns state=unmet with comparator '>=', threshold 99.9, reading 99.72 as of 2026-11-15, comparison=false, so the verdict rests on the reading versus the target rather than a status word.
- **what a wrong answer means:** If the answer cites a status rather than the reading, achievement is being declared instead of measured.

### Q5 — What was planned and never happened? → **right**

- **what the product answered:** `search('what was planned and never happened', object)` → 0 rows · `search(None, object, ended_scope)` → 2 rows: *Old onboarding flow* (kind=process, status=deprecated, rrf=None) [no linked event]; *Thursday partner call — 6 November* (kind=core:plan, status=None, rrf=None) [stage=terminal/withdrawn, no linked event, endedAt=2026-11-18T03:00:25.716898+00:00] · `search('thursday partner call', object)` → 0 rows · `read_entity` → {"isClosed": true, "name": "Thursday partner call \u2014 6 November", "objectKind": "core:plan", "pipelineOutcome": "withdrawn", "pipelineSlot": "terminal", "pipelineStageId": "cancelled"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='available'; search: read_status='degraded'; by-id read_entity: stage='cancelled' slot='terminal' isClosed=True; found: partner_call (1a1ec9933d4-5b48b424a801); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The Thursday partner call (6 November) is served as a plan in stage 'cancelled', slot 'terminal', outcome 'withdrawn', isClosed=true, while the 12 December launch remains scheduled rather than being reported as never having happened.
- **what a wrong answer means:** If the cancelled partner call cannot be found, the product cannot answer the one question a mutable-event model loses.

### Q6 — Has anything we relied on been withdrawn? → **right**

- **what the product answered:** `search('has anything we relied on been withdrawn', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search(None, point, ended_scope)` → 2 rows: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=None) [no linked event, endedAt=2026-11-18T03:00:31.376809+00:00]; *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=None) [successor=1a1ec992a2c-bfc03985f6da, no linked event, endedAt=2026-11-18T03:00:23.212217+00:00] · `search('beach club confirmed booking', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search('beach club confirmed booking', point, include_terminal)` → 1 rows [related-to-question: 1/1]: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=0.857142857142857) · `read_entity` → {"content": "The beach club confirmed our 12 December booking.", "status": "retracted"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='available'; search: read_status='degraded'; search: read_status='degraded'; claim status='retracted' (a by-id read_entity); the ended-scope read served 2 ended record(s) without the id being supplied — each row states how it ended: 1a1ec994867-abc6bd3e06c8: retracted; 1a1ec991eb7-78e10ce6a234: superseded; found: beach_club_claim (1a1ec994867-abc6bd3e06c8); NO-RELATION-TO-QUESTION: all 2 rows served for 'has anything we relied on been withdrawn' share no word with it — a degraded read ranked the corpus rather than answering; NO-RELATION-TO-QUESTION: all 2 rows served for 'beach club confirmed booking' share no word with it — a degraded read ranked the corpus rather than answering; DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The beach-club confirmation claim is served with status 'retracted' and an endedAt timestamp, and it is excluded from the default (current) scope, so the product both stops counting it and can say it was withdrawn.
- **what a wrong answer means:** If the withdrawn claim still counts, belief is contaminated by retired evidence — the agent will act on something we retracted.

## Ingestion (the narrative through the product's write doors)

| op | ref | ok | detail |
|---|---|---|---|
| `declare_pipeline` | core:plan | yes | {"pipeline": "core:plan", "default": "scheduled", "source": "graph-override", "stages": ["scheduled", "held", "missed", "cancelled"]} |
| `object` | launch_plan | yes | core:plan → 1a1ec9918d7-963e8fec3888 |
| `advance_stage` | launch_plan | yes | {"applied": true, "reason": "", "stage": "scheduled", "slot": "entry", "outcome": null} |
| `point` | q3_pricing | yes | decision → 1a1ec991eb7-78e10ce6a234 |
| `point` | q4_pricing | yes | decision → 1a1ec992a2c-bfc03985f6da |
| `supersede_point` | q3_pricing | yes | superseded by 1a1ec992a2c-bfc03985f6da |
| `object` | old_onboarding | yes | process → 1a1ec99302b-0d92977153b2 |
| `deprecate_object` | old_onboarding | yes | status=deprecated |
| `object` | partner_call | yes | core:plan → 1a1ec9933d4-5b48b424a801 |
| `advance_stage` | partner_call | yes | {"applied": true, "reason": "", "stage": "cancelled", "slot": "terminal", "outcome": "withdrawn"} |
| `object` | uptime_goal | yes | core:goal → 1a1ec993536-34095b5aeee7 |
| `object` | uptime_target | yes | core:target → 1a1ec993636-666e53953a9a |
| `reading` | uptime_oct | yes | 99.95percent asOf 2026-10-31T23:59:00+00:00 |
| `reading` | uptime_nov | yes | 99.72percent asOf 2026-11-15T23:59:00+00:00 |
| `judge_goal` | uptime_goal | yes | state=unmet value=99.72 reading=1a1ec993978-6d7081ad49d0 |
| `point` | beach_club_claim | yes | statement → 1a1ec994867-abc6bd3e06c8 |
| `retract_point` | beach_club_claim | yes | status=retracted |

## Target and honesty notes

- retrieval leg `vector` degraded in 11 of 11 reads (reasons: no_embedder)
- retrieval leg `fallback` degraded in 6 of 6 reads (reasons: no_fallback_applicable, tfidf_snapshot)
- the state-model verdicts in this scoreboard are read against that posture: a row that says a thing was wrong because unrelated rows came back is the read path, not the lifecycle model
- session capture — the SDK recorder was retired (#4282); only the hosted POST /v1/sessions captures sessions, and it needs an extractor model. This run therefore writes the state layer through the entity doors, which is the surface the state model governs.

### Retrieval posture of the target

| leg | reads | degraded | reasons |
|---|---|---|---|
| `vector` | 11 | 11 | no_embedder |
| `fts` | 11 | 0 | ok, empty_results |
| `structural` | 11 | 0 | empty_results |
| `valid_time` | 4 | 0 | no-stale-entries, no-prefer-latest-intent |
| `fallback` | 6 | 6 | no_fallback_applicable, tfidf_snapshot |

### Judge

- mode: `fresh` · cmd: `pi -p --no-session --no-tools` · 97.2s
