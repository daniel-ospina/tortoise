# Capstone #13038 — the state model measured by customer value

**Run:** 2026-11-17T22:17:13Z → 2026-11-17T22:18:52Z (target `local`, 99.8s)
**Product:** tortoise-sdk @ bf3a7b8e77bfa02ed7b381cc52408afe35ddb6ee
**Store:** `docker://<redacted>@127.0.0.1:16415/tortoise_test_matrix` · graph `capstone13038_20261117t221713z`
**Scenario:** `docs/evidence/13038-capstone/narrative.json` (frozen 2026-11-16)

## Scoreboard: 3 right · 3 wrong · 0 blocked · 0 unjudged

| # | question | verdict | what the product answered | why |
|---|---|---|---|---|
| **Q1** | What are we currently committed to? | **wrong** | search('what are we currently committed to — pricing, onboarding, launch and uptime') → 2 rows 2/2 on-topic · search('current commitments') → 0 rows · search('onboarding flow') → 0 rows | The served set is only two of the four current commitments — Q4 pricing ($39) and the Q4 uptime result — with the 12 December launch plan missing entirely, so a customer asking what they are committed to is not told about the launch. |
| **Q2** | Did we ever plan a launch event, and did it happen? | **right** | search('launch event') → 1 rows 1/1 on-topic · entity → {"pipelineStageId": "scheduled", "pipelineSlot": "entry", "isClosed": false} · events → 2 | The plan is served as 'Launch event — 12 December at the beach club' with stage scheduled, slot entry and isClosed=false, so planned and happened are correctly distinguished. |
| **Q3** | Why did the pricing decision change, and what replaced it? | **right** | search('pricing decision Q3 Q4') → 2 rows 1/2 on-topic · search('pricing decision Q3 Q4', +terminal) → 3 rows 2/3 on-topic · entity → {"status": "superseded"} · entity → {"status": "live"} | It names Q4 pricing ($39/seat) as the live successor, shows Q3 ($29/seat) as superseded with its supersedes link, and invents no rationale. |
| **Q4** | Is the uptime goal met, and on what basis? | **right** | entity → {"status": "live", "goalState": "unmet"} · judge → unmet (99.72 vs >= 99.9) · events → 2 | The judgement compares the November reading of 99.72% against the >= 99.9 target and returns unmet with the reading cited, not a bare status word. |
| **Q5** | What was planned and never happened? | **wrong** | search('what was planned and never happened') → 0 rows · search('thursday partner call') → 0 rows · entity → {"pipelineStageId": "cancelled", "pipelineSlot": "terminal", "isClosed": true} | Both searches for 'what was planned and never happened' and 'thursday partner call' returned zero rows; the cancelled 6 November call was only reachable by already knowing its id, so the customer is not given it. |
| **Q6** | Has anything we relied on been withdrawn? | **wrong** | search('has anything we relied on been withdrawn') → 2 rows 0/2 on-topic · search('beach club confirmed booking') → 2 rows 0/2 on-topic · search('beach club confirmed booking', +terminal) → 1 rows 1/1 on-topic · entity → {"status": "retracted"} | The default-scope reads served two unrelated rows (Q4 pricing and the uptime statement, zero query overlap) instead of the retracted claim, which appeared only when terminal scope was forced — so the surface cannot say the claim was withdrawn. |

## Per-question detail

### Q1 — What are we currently committed to? → **wrong**

- **what the product answered:** `search('what are we currently committed to — pricing, onboarding, launch and uptime', point)` → 2 rows [related-to-question: 2/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=1.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.666666666666667) · `search('current commitments', object)` → 0 rows · `search('onboarding flow', object)` → 0 rows
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; search: read_status='degraded'; found: q4_pricing (1a1eb96ab04-5b418d94c2a3); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The served set is only two of the four current commitments — Q4 pricing ($39) and the Q4 uptime result — with the 12 December launch plan missing entirely, so a customer asking what they are committed to is not told about the launch.
- **cause:** S0/S7 plans vocabulary + currency predicate: the object leg for 'current commitments' and 'onboarding flow' returned 0 rows, so the plan commitment never entered the answer (vector leg also down, no_embedder).
- **what a wrong answer means:** If the replaced Q3 pricing decision or the retired onboarding flow appears, the product serves retired things as current — the trust failure this model exists to remove.

### Q2 — Did we ever plan a launch event, and did it happen? → **right**

- **what the product answered:** `search('launch event', object)` → 1 rows [related-to-question: 1/1]: *Launch event — 12 December at the beach club* (kind=core:plan, status=live, rrf=4.0) · `read_entity` → {"isClosed": false, "name": "Launch event \u2014 12 December at the beach club", "objectKind": "core:plan", "pipelineOutcome": "", "pipelineSlot": "entry", "pipelineStageId": "scheduled"} · `read_events` → 2 events
- **evidence it cited / signals:** search: read_status='degraded'; stage='scheduled' slot='entry' isClosed=False status=None; found: launch_plan (1a1eb966b87-fc6ad3a325d9); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The plan is served as 'Launch event — 12 December at the beach club' with stage scheduled, slot entry and isClosed=false, so planned and happened are correctly distinguished.
- **what a wrong answer means:** If 'planned' and 'happened' cannot be told apart, plans are being stored as facts — the answer would let an agent claim an event occurred.

### Q3 — Why did the pricing decision change, and what replaced it? → **right**

- **what the product answered:** `search('pricing decision Q3 Q4', point)` → 2 rows [related-to-question: 1/2]: *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `search('pricing decision Q3 Q4', point, include_terminal)` → 3 rows [related-to-question: 2/3]: *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=3.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `read_entity` → {"content": "Q3 pricing: the entry tier is $29 per seat.", "status": "superseded", "validTo": "2026-11-17T22:18:02.369776+00:00"} · `read_entity` → {"content": "Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 decision.", "status": "live"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; replaced node status='superseded', names-its-successor=False; found: q4_pricing (1a1eb96ab04-5b418d94c2a3); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** It names Q4 pricing ($39/seat) as the live successor, shows Q3 ($29/seat) as superseded with its supersedes link, and invents no rationale.
- **what a wrong answer means:** If the successor cannot be named, change is illegible — the agent cannot explain a change to a human.

### Q4 — Is the uptime goal met, and on what basis? → **right**

- **what the product answered:** `read_entity` → {"goalState": "unmet", "name": "Q4 uptime", "objectKind": "core:goal", "status": "live"} · `judge` → {"state": "unmet", "reason": "state-unchanged", "comparator": ">=", "threshold": 99.9, "readingValue": 99.72, "readingAsOf": "2026-11-15T23:59:00+00:00", "readingId": "1a1eb96d7af-a096f464429b", "comparison": false} · `read_events` → 2 events
- **evidence it cited / signals:** goalState='unmet' status='live'; judgement cites reading=99.72 vs >= 99.9 → unmet; NOT-SERVED: uptime_goal (1a1eb96ca6c-8e068130327a)
- **verdict reason:** The judgement compares the November reading of 99.72% against the >= 99.9 target and returns unmet with the reading cited, not a bare status word.
- **what a wrong answer means:** If the answer cites a status rather than the reading, achievement is being declared instead of measured.

### Q5 — What was planned and never happened? → **wrong**

- **what the product answered:** `search('what was planned and never happened', object)` → 0 rows · `search('thursday partner call', object)` → 0 rows · `read_entity` → {"isClosed": true, "name": "Thursday partner call \u2014 6 November", "objectKind": "core:plan", "pipelineOutcome": "withdrawn", "pipelineSlot": "terminal", "pipelineStageId": "cancelled"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; by-id read_entity: stage='cancelled' slot='terminal' isClosed=True; NOT-SERVED: partner_call (1a1eb96c297-f3b16b2ea271); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** Both searches for 'what was planned and never happened' and 'thursday partner call' returned zero rows; the cancelled 6 November call was only reachable by already knowing its id, so the customer is not given it.
- **cause:** Terminal exclusion (include_terminal=false) plus no plan vocabulary in search: cancelled plans sit in terminal scope and the default read cannot reach them.
- **what a wrong answer means:** If the cancelled partner call cannot be found, the product cannot answer the one question a mutable-event model loses.

### Q6 — Has anything we relied on been withdrawn? → **wrong**

- **what the product answered:** `search('has anything we relied on been withdrawn', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search('beach club confirmed booking', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search('beach club confirmed booking', point, include_terminal)` → 1 rows [related-to-question: 1/1]: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=0.857142857142857) · `read_entity` → {"content": "The beach club confirmed our 12 December booking.", "status": "retracted"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; search: read_status='degraded'; claim status='retracted' (a by-id read_entity); found: beach_club_claim (1a1eb96e740-03a938649752); NO-RELATION-TO-QUESTION: all 2 rows served for 'has anything we relied on been withdrawn' share no word with it — a degraded read ranked the corpus rather than answering; NO-RELATION-TO-QUESTION: all 2 rows served for 'beach club confirmed booking' share no word with it — a degraded read ranked the corpus rather than answering; DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The default-scope reads served two unrelated rows (Q4 pricing and the uptime statement, zero query overlap) instead of the retracted claim, which appeared only when terminal scope was forced — so the surface cannot say the claim was withdrawn.
- **cause:** Currency predicate (#13034) treats 'retracted' as non-current and hides it from default reads, with the tfidf fallback ranking unrelated live points instead.
- **what a wrong answer means:** If the withdrawn claim still counts, belief is contaminated by retired evidence — the agent will act on something we retracted.

## Ingestion (the narrative through the product's write doors)

| op | ref | ok | detail |
|---|---|---|---|
| `declare_pipeline` | core:plan | yes | {"pipeline": "core:plan", "default": "scheduled", "source": "graph-override", "stages": ["scheduled", "held", "missed", "cancelled"]} |
| `object` | launch_plan | yes | core:plan → 1a1eb966b87-fc6ad3a325d9 |
| `advance_stage` | launch_plan | yes | {"applied": true, "reason": "", "stage": "scheduled", "slot": "entry", "outcome": null} |
| `point` | q3_pricing | yes | decision → 1a1eb967dfa-7a59aa61fa15 |
| `point` | q4_pricing | yes | decision → 1a1eb96ab04-5b418d94c2a3 |
| `supersede_point` | q3_pricing | yes | superseded by 1a1eb96ab04-5b418d94c2a3 |
| `object` | old_onboarding | yes | process → 1a1eb96b797-0582ea0e7d0b |
| `deprecate_object` | old_onboarding | yes | status=deprecated |
| `object` | partner_call | yes | core:plan → 1a1eb96c297-f3b16b2ea271 |
| `advance_stage` | partner_call | yes | {"applied": true, "reason": "", "stage": "cancelled", "slot": "terminal", "outcome": "withdrawn"} |
| `object` | uptime_goal | yes | core:goal → 1a1eb96ca6c-8e068130327a |
| `object` | uptime_target | yes | core:target → 1a1eb96ccda-2f1b8b29bac8 |
| `reading` | uptime_oct | yes | 99.95percent asOf 2026-10-31T23:59:00+00:00 |
| `reading` | uptime_nov | yes | 99.72percent asOf 2026-11-15T23:59:00+00:00 |
| `judge_goal` | uptime_goal | yes | state=unmet value=99.72 reading=1a1eb96d7af-a096f464429b |
| `point` | beach_club_claim | yes | statement → 1a1eb96e740-03a938649752 |
| `retract_point` | beach_club_claim | yes | status=retracted |

## Target and honesty notes

- retrieval leg `vector` degraded in 11 of 11 reads (reasons: no_embedder)
- retrieval leg `fallback` degraded in 6 of 6 reads (reasons: no_fallback_applicable, tfidf_snapshot)
- the state-model verdicts in this scoreboard are read against that posture: a row that says a thing was wrong because unrelated rows came back is the read path, not the lifecycle model
- session capture — the SDK recorder was retired (#4282); only the hosted POST /v1/sessions captures sessions, and it needs an extractor model. This run therefore writes the state layer through the entity doors, which is the surface the state model governs.
- no fresh-reader verdicts: the fresh reader returned no parseable verdict JSON (exit 76): h --out /tmp/capstone-13302-before --verdicts-out /tmp) [pi-gate] refusal #5, recorded durably in /Users/danielospina/.pi/agent/state/spawn-gate.tsv — nothing was consumed: no model call, no session [pi-gate] remedy: re-dispatch when the 1-min load falls below 100; operator rescue is the real pi by…

### Retrieval posture of the target

| leg | reads | degraded | reasons |
|---|---|---|---|
| `vector` | 11 | 11 | no_embedder |
| `fts` | 11 | 0 | ok, empty_results |
| `structural` | 11 | 0 | empty_results |
| `valid_time` | 4 | 0 | no-stale-entries, no-prefer-latest-intent |
| `fallback` | 6 | 6 | no_fallback_applicable, tfidf_snapshot |

### Judge

- mode: `fresh` · cmd: `pi -p --no-session --no-tools` · 81.0s
