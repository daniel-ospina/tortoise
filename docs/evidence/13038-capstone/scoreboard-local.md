# Capstone #13038 — the state model measured by customer value

**Run:** 2026-11-17T20:25:19Z → 2026-11-17T20:25:55Z (target `local`, 35.5s)
**Product:** tortoise-sdk @ 6f162b9efc1dab12b8ecf01df68f92f85edaf577
**Store:** `docker://<redacted>@127.0.0.1:16409/tortoise_test_matrix` · graph `capstone13038_20261117t202519z`
**Scenario:** `docs/evidence/13038-capstone/narrative.json` (frozen 2026-11-16)

## Scoreboard: 3 right · 3 wrong · 0 blocked · 0 unjudged

| # | question | verdict | what the product answered | why |
|---|---|---|---|---|
| **Q1** | What are we currently committed to? | **wrong** | search('what are we currently committed to — pricing, onboarding, launch and uptime') → 2 rows 2/2 on-topic · search('current commitments') → 0 rows · search('onboarding flow') → 0 rows | The answer served only two rows — Q4 pricing ($39, superseding Q3) and the Q4 uptime-unmet statement — and the `committed_objects` read returned 0 rows, so the 12 December launch plan, which the narrative calls a current commitment, is absent from the answer entirely. It is true that Q3 ($29) and the retired onboarding flow were correctly not served as current (the `retired_flow` search returned 0 rows), but an answer missing one of the commitments the key names, where the plan object demonstrably exists in the graph, does not satisfy the question. |
| **Q2** | Did we ever plan a launch event, and did it happen? | **right** | search('launch event') → 1 rows 1/1 on-topic · entity → {"pipelineStageId": "scheduled", "pipelineSlot": "entry", "isClosed": false} · events → 2 | The `search` returned the object "Launch event — 12 December at the beach club" as kind core:plan, and the by-id `plan_node` read shows pipelineStageId='scheduled', pipelineSlot='entry', isClosed=false with status null — a plan that has not been held. The `events` call returned only the two uptime measurement events and no launch occurrence, so 'planned but not happened' is supported rather than asserted. |
| **Q3** | Why did the pricing decision change, and what replaced it? | **right** | search('pricing decision Q3 Q4') → 2 rows 1/2 on-topic · search('pricing decision Q3 Q4', +terminal) → 3 rows 2/3 on-topic · entity → {"status": "superseded"} · entity → {"status": "live"} | The served rows name the successor: the Q4 pricing row reads "the entry tier becomes $39 per seat, replacing the Q3 decision" and carries `supersedes` naming the Q3 point, and the `include_terminal` search plus the `replaced_node` by-id read show Q3 at status='superseded' with its validity window ended and outdated=true. That answers what replaced it (Q4, $39) and the ending of Q3 without inventing any rationale the narrative does not record. |
| **Q4** | Is the uptime goal met, and on what basis? | **right** | entity → {"status": "live", "goalState": "unmet"} · judge → unmet (99.72 vs >= 99.9) · events → 2 | The `judgement` call returned state='unmet' with comparator '>=', threshold 99.9, readingValue 99.72, readingAsOf 2026-11-15 and comparison=false, and the `readings` call served both the October (99.95) and November (99.72) measurements. So the answer is 'not met' on the basis of the latest reading measured against the target, not a bare status word, and the `goal_node` read confirms goalState='unmet'. |
| **Q5** | What was planned and never happened? | **wrong** | search('what was planned and never happened') → 0 rows · search('thursday partner call') → 0 rows · entity → {"pipelineStageId": "cancelled", "pipelineSlot": "terminal", "isClosed": true} | Both searches for this question returned nothing usable: the `question` search ("what was planned and never happened") and the `by_name` search ("thursday partner call") each returned 0 rows, so the customer's question got an empty result even though the cancelled plan exists. The cancelled plan was reachable only by its id in the `cancelled_plan` read_entity, which shows stage='cancelled', slot='terminal', pipelineOutcome='withdrawn', isClosed=true — evidence the product held but did not serve to the question. |
| **Q6** | Has anything we relied on been withdrawn? | **wrong** | search('has anything we relied on been withdrawn') → 2 rows 0/2 on-topic · search('beach club confirmed booking') → 2 rows 0/2 on-topic · search('beach club confirmed booking', +terminal) → 1 rows 1/1 on-topic · entity → {"status": "retracted"} | The `question` search ("has anything we relied on been withdrawn") served two rows — Q4 pricing and the Q4 uptime statement — both live and both with zero word overlap with the question, and it says nothing about a withdrawal; the `by_name` search ("beach club confirmed booking") returned those same two unrelated rows from the degraded fallback. Only the separately-requested `include_terminal` search and the `claim_node` by-id read show the beach club claim at status='retracted', so the answer actually delivered to the question does not tell the customer that anything was withdrawn. |

## Per-question detail

### Q1 — What are we currently committed to? → **wrong**

- **what the product answered:** `search('what are we currently committed to — pricing, onboarding, launch and uptime', point)` → 2 rows [related-to-question: 2/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=1.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.666666666666667) · `search('current commitments', object)` → 0 rows · `search('onboarding flow', object)` → 0 rows
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; search: read_status='degraded'; found: q4_pricing (1a1eb2face3-a77c3bcd3aa1); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The answer served only two rows — Q4 pricing ($39, superseding Q3) and the Q4 uptime-unmet statement — and the `committed_objects` read returned 0 rows, so the 12 December launch plan, which the narrative calls a current commitment, is absent from the answer entirely. It is true that Q3 ($29) and the retired onboarding flow were correctly not served as current (the `retired_flow` search returned 0 rows), but an answer missing one of the commitments the key names, where the plan object demonstrably exists in the graph, does not satisfy the question.
- **cause:** The object-side search (`committed_objects`, entity_type=object, query "current commitments") returned 0 rows with leg fts='empty_results' while the vector leg was off ('no_embedder') and fallback reported 'no_fallback_applicable'; the only rows served came from the points query, which ran entity_type=point and could never return the launch plan object.
- **what a wrong answer means:** If the replaced Q3 pricing decision or the retired onboarding flow appears, the product serves retired things as current — the trust failure this model exists to remove.

### Q2 — Did we ever plan a launch event, and did it happen? → **right**

- **what the product answered:** `search('launch event', object)` → 1 rows [related-to-question: 1/1]: *Launch event — 12 December at the beach club* (kind=core:plan, status=live, rrf=4.0) · `read_entity` → {"isClosed": false, "name": "Launch event \u2014 12 December at the beach club", "objectKind": "core:plan", "pipelineOutcome": "", "pipelineSlot": "entry", "pipelineStageId": "scheduled"} · `read_events` → 2 events
- **evidence it cited / signals:** search: read_status='degraded'; stage='scheduled' slot='entry' isClosed=False status=None; found: launch_plan (1a1eb2f9ed0-0351ccdc5d86); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The `search` returned the object "Launch event — 12 December at the beach club" as kind core:plan, and the by-id `plan_node` read shows pipelineStageId='scheduled', pipelineSlot='entry', isClosed=false with status null — a plan that has not been held. The `events` call returned only the two uptime measurement events and no launch occurrence, so 'planned but not happened' is supported rather than asserted.
- **what a wrong answer means:** If 'planned' and 'happened' cannot be told apart, plans are being stored as facts — the answer would let an agent claim an event occurred.

### Q3 — Why did the pricing decision change, and what replaced it? → **right**

- **what the product answered:** `search('pricing decision Q3 Q4', point)` → 2 rows [related-to-question: 1/2]: *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `search('pricing decision Q3 Q4', point, include_terminal)` → 3 rows [related-to-question: 2/3]: *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=3.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `read_entity` → {"content": "Q3 pricing: the entry tier is $29 per seat.", "status": "superseded", "validTo": "2026-11-17T20:25:32.643684+00:00"} · `read_entity` → {"content": "Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 decision.", "status": "live"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; replaced node status='superseded', names-its-successor=False; found: q4_pricing (1a1eb2face3-a77c3bcd3aa1); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The served rows name the successor: the Q4 pricing row reads "the entry tier becomes $39 per seat, replacing the Q3 decision" and carries `supersedes` naming the Q3 point, and the `include_terminal` search plus the `replaced_node` by-id read show Q3 at status='superseded' with its validity window ended and outdated=true. That answers what replaced it (Q4, $39) and the ending of Q3 without inventing any rationale the narrative does not record.
- **what a wrong answer means:** If the successor cannot be named, change is illegible — the agent cannot explain a change to a human.

### Q4 — Is the uptime goal met, and on what basis? → **right**

- **what the product answered:** `read_entity` → {"goalState": "unmet", "name": "Q4 uptime", "objectKind": "core:goal", "status": "live"} · `judge` → {"state": "unmet", "reason": "state-unchanged", "comparator": ">=", "threshold": 99.9, "readingValue": 99.72, "readingAsOf": "2026-11-15T23:59:00+00:00", "readingId": "1a1eb2fbf46-31c5146de2d5", "comparison": false} · `read_events` → 2 events
- **evidence it cited / signals:** goalState='unmet' status='live'; judgement cites reading=99.72 vs >= 99.9 → unmet; NOT-SERVED: uptime_goal (1a1eb2fb989-f41a951a2d62)
- **verdict reason:** The `judgement` call returned state='unmet' with comparator '>=', threshold 99.9, readingValue 99.72, readingAsOf 2026-11-15 and comparison=false, and the `readings` call served both the October (99.95) and November (99.72) measurements. So the answer is 'not met' on the basis of the latest reading measured against the target, not a bare status word, and the `goal_node` read confirms goalState='unmet'.
- **what a wrong answer means:** If the answer cites a status rather than the reading, achievement is being declared instead of measured.

### Q5 — What was planned and never happened? → **wrong**

- **what the product answered:** `search('what was planned and never happened', object)` → 0 rows · `search('thursday partner call', object)` → 0 rows · `read_entity` → {"isClosed": true, "name": "Thursday partner call \u2014 6 November", "objectKind": "core:plan", "pipelineOutcome": "withdrawn", "pipelineSlot": "terminal", "pipelineStageId": "cancelled"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; by-id read_entity: stage='cancelled' slot='terminal' isClosed=True; NOT-SERVED: partner_call (1a1eb2fb671-f7389b15bd02); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** Both searches for this question returned nothing usable: the `question` search ("what was planned and never happened") and the `by_name` search ("thursday partner call") each returned 0 rows, so the customer's question got an empty result even though the cancelled plan exists. The cancelled plan was reachable only by its id in the `cancelled_plan` read_entity, which shows stage='cancelled', slot='terminal', pipelineOutcome='withdrawn', isClosed=true — evidence the product held but did not serve to the question.
- **cause:** Both object searches returned 0 rows with leg fts='empty_results' (the query words share no token with "Thursday partner call — 6 November") while the vector leg was off ('no_embedder') and fallback reported 'no_fallback_applicable', and the plan sits at slot='terminal' so the default include_terminal=false scope excludes it with no surface that enumerates terminal plans.
- **what a wrong answer means:** If the cancelled partner call cannot be found, the product cannot answer the one question a mutable-event model loses.

### Q6 — Has anything we relied on been withdrawn? → **wrong**

- **what the product answered:** `search('has anything we relied on been withdrawn', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search('beach club confirmed booking', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search('beach club confirmed booking', point, include_terminal)` → 1 rows [related-to-question: 1/1]: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=0.857142857142857) · `read_entity` → {"content": "The beach club confirmed our 12 December booking.", "status": "retracted"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; search: read_status='degraded'; claim status='retracted' (a by-id read_entity); found: beach_club_claim (1a1eb2fcfdc-fbc555f8bba5); NO-RELATION-TO-QUESTION: all 2 rows served for 'has anything we relied on been withdrawn' share no word with it — a degraded read ranked the corpus rather than answering; NO-RELATION-TO-QUESTION: all 2 rows served for 'beach club confirmed booking' share no word with it — a degraded read ranked the corpus rather than answering; DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The `question` search ("has anything we relied on been withdrawn") served two rows — Q4 pricing and the Q4 uptime statement — both live and both with zero word overlap with the question, and it says nothing about a withdrawal; the `by_name` search ("beach club confirmed booking") returned those same two unrelated rows from the degraded fallback. Only the separately-requested `include_terminal` search and the `claim_node` by-id read show the beach club claim at status='retracted', so the answer actually delivered to the question does not tell the customer that anything was withdrawn.
- **cause:** The default-scope search (include_terminal=false) dropped the retracted claim and the degraded fallback leg ('tfidf_snapshot') ranked the corpus's live points at score 0.0; the retracted status was observable only via the include_terminal=true search and the by-id read_entity.
- **what a wrong answer means:** If the withdrawn claim still counts, belief is contaminated by retired evidence — the agent will act on something we retracted.

## Ingestion (the narrative through the product's write doors)

| op | ref | ok | detail |
|---|---|---|---|
| `declare_pipeline` | core:plan | yes | {"pipeline": "core:plan", "default": "scheduled", "source": "graph-override", "stages": ["scheduled", "held", "missed", "cancelled"]} |
| `object` | launch_plan | yes | core:plan → 1a1eb2f9ed0-0351ccdc5d86 |
| `advance_stage` | launch_plan | yes | {"applied": true, "reason": "", "stage": "scheduled", "slot": "entry", "outcome": null} |
| `point` | q3_pricing | yes | decision → 1a1eb2fa3d1-fa91304657b0 |
| `point` | q4_pricing | yes | decision → 1a1eb2face3-a77c3bcd3aa1 |
| `supersede_point` | q3_pricing | yes | superseded by 1a1eb2face3-a77c3bcd3aa1 |
| `object` | old_onboarding | yes | process → 1a1eb2fb60b-041b15a3e140 |
| `deprecate_object` | old_onboarding | yes | status=deprecated |
| `object` | partner_call | yes | core:plan → 1a1eb2fb671-f7389b15bd02 |
| `advance_stage` | partner_call | yes | {"applied": true, "reason": "", "stage": "cancelled", "slot": "terminal", "outcome": "withdrawn"} |
| `object` | uptime_goal | yes | core:goal → 1a1eb2fb989-f41a951a2d62 |
| `object` | uptime_target | yes | core:target → 1a1eb2fba7e-fb5189270089 |
| `reading` | uptime_oct | yes | 99.95percent asOf 2026-10-31T23:59:00+00:00 |
| `reading` | uptime_nov | yes | 99.72percent asOf 2026-11-15T23:59:00+00:00 |
| `judge_goal` | uptime_goal | yes | state=unmet value=99.72 reading=1a1eb2fbf46-31c5146de2d5 |
| `point` | beach_club_claim | yes | statement → 1a1eb2fcfdc-fbc555f8bba5 |
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

- mode: `file`
