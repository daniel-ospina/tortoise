# Capstone #13038 — the state model measured by customer value

**Run:** 2026-11-18T00:33:51Z → 2026-11-18T00:35:41Z (target `local`, 110.2s)
**Product:** tortoise-sdk @ c8ecf9b37e6f0147769ada8ae7b0699376fbea26
**Store:** `docker://<redacted>@127.0.0.1:16415/tortoise_test_matrix` · graph `capstone13038_20261118t003351z`
**Scenario:** `docs/evidence/13038-capstone/narrative.json` (frozen 2026-11-16)

## Scoreboard: 5 right · 1 wrong · 0 blocked · 0 unjudged

| # | question | verdict | what the product answered | why |
|---|---|---|---|---|
| **Q1** | What are we currently committed to? | **wrong** | search('what are we currently committed to — pricing, onboarding, launch and uptime') → 2 rows 2/2 on-topic · search('current commitments') → 0 rows · search('onboarding flow') → 0 rows | It serves Q4 pricing ($39) and the uptime target but omits the pending 12 December launch plan, which the narrative counts as a current commitment, so the 'what we are committed to' set is incomplete. |
| **Q2** | Did we ever plan a launch event, and did it happen? | **right** | search('launch event') → 1 rows 1/1 on-topic · entity → {"pipelineStageId": "scheduled", "pipelineSlot": "entry", "isClosed": false} · events → 2 | It serves the launch as a core:plan node with stage 'scheduled', isClosed=false and no occurrence event anywhere in the graph, so plan and fact are told apart. |
| **Q3** | Why did the pricing decision change, and what replaced it? | **right** | search('pricing decision Q3 Q4') → 2 rows 1/2 on-topic · search('pricing decision Q3 Q4', +terminal) → 3 rows 2/3 on-topic · entity → {"status": "superseded"} · entity → {"status": "live"} | The served rows name Q4 pricing ($39) as the replacement, carry supersedes=[Q3 id] and a live successor node, and invent no rationale. |
| **Q4** | Is the uptime goal met, and on what basis? | **right** | entity → {"status": "live", "goalState": "unmet"} · judge → unmet (99.72 vs >= 99.9) · events → 2 | It reports unmet with the basis given as the 99.72% November reading measured against the >=99.9 target (comparison false), not a bare status word. |
| **Q5** | What was planned and never happened? | **right** | search('what was planned and never happened') → 0 rows · search(None) → 2 rows · search('thursday partner call') → 0 rows · entity → {"pipelineStageId": "cancelled", "pipelineSlot": "terminal", "isClosed": true} | The ended scope serves the Thursday partner call — 6 November as a closed plan with terminal stage 'cancelled', while the still-pending December launch is not reported as never happened. |
| **Q6** | Has anything we relied on been withdrawn? | **right** | search('has anything we relied on been withdrawn') → 2 rows 0/2 on-topic · search(None) → 2 rows · search('beach club confirmed booking') → 2 rows 0/2 on-topic · search('beach club confirmed booking', +terminal) → 1 rows 1/1 on-topic · entity → {"status": "retracted"} | The retracted beach-club confirmation is excluded from default scope and served with status 'retracted' when terminal scope is included, so it both stops counting and can be reported as withdrawn. |

## Per-question detail

### Q1 — What are we currently committed to? → **wrong**

- **what the product answered:** `search('what are we currently committed to — pricing, onboarding, launch and uptime', point)` → 2 rows [related-to-question: 2/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=1.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.666666666666667) · `search('current commitments', object)` → 0 rows · `search('onboarding flow', object)` → 0 rows
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; search: read_status='degraded'; found: q4_pricing (1a1ec132f4a-197413b54f44); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** It serves Q4 pricing ($39) and the uptime target but omits the pending 12 December launch plan, which the narrative counts as a current commitment, so the 'what we are committed to' set is incomplete.
- **cause:** Plans vocabulary / S0-S7 slice: the plan object (core:plan) is not returned by an object-scope 'current commitments' search (0 rows, vector leg no_embedder, fts empty), so the only served items are points.
- **what a wrong answer means:** If the replaced Q3 pricing decision or the retired onboarding flow appears, the product serves retired things as current — the trust failure this model exists to remove.

### Q2 — Did we ever plan a launch event, and did it happen? → **right**

- **what the product answered:** `search('launch event', object)` → 1 rows [related-to-question: 1/1]: *Launch event — 12 December at the beach club* (kind=core:plan, status=live, rrf=4.0) · `read_entity` → {"isClosed": false, "name": "Launch event \u2014 12 December at the beach club", "objectKind": "core:plan", "pipelineOutcome": "", "pipelineSlot": "entry", "pipelineStageId": "scheduled"} · `read_events` → 2 events
- **evidence it cited / signals:** search: read_status='degraded'; stage='scheduled' slot='entry' isClosed=False status=None; found: launch_plan (1a1ec132801-088dd205af0b); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** It serves the launch as a core:plan node with stage 'scheduled', isClosed=false and no occurrence event anywhere in the graph, so plan and fact are told apart.
- **what a wrong answer means:** If 'planned' and 'happened' cannot be told apart, plans are being stored as facts — the answer would let an agent claim an event occurred.

### Q3 — Why did the pricing decision change, and what replaced it? → **right**

- **what the product answered:** `search('pricing decision Q3 Q4', point)` → 2 rows [related-to-question: 1/2]: *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `search('pricing decision Q3 Q4', point, include_terminal)` → 3 rows [related-to-question: 2/3]: *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=3.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `read_entity` → {"content": "Q3 pricing: the entry tier is $29 per seat.", "status": "superseded", "validTo": "2026-11-18T00:34:02.698636+00:00"} · `read_entity` → {"content": "Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 decision.", "status": "live"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; replaced node status='superseded', names-its-successor=False; found: q4_pricing (1a1ec132f4a-197413b54f44); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The served rows name Q4 pricing ($39) as the replacement, carry supersedes=[Q3 id] and a live successor node, and invent no rationale.
- **what a wrong answer means:** If the successor cannot be named, change is illegible — the agent cannot explain a change to a human.

### Q4 — Is the uptime goal met, and on what basis? → **right**

- **what the product answered:** `read_entity` → {"goalState": "unmet", "name": "Q4 uptime", "objectKind": "core:goal", "status": "live"} · `judge` → {"state": "unmet", "reason": "state-unchanged", "comparator": ">=", "threshold": 99.9, "readingValue": 99.72, "readingAsOf": "2026-11-15T23:59:00+00:00", "readingId": "1a1ec133692-fec0ad360937", "comparison": false} · `read_events` → 2 events
- **evidence it cited / signals:** goalState='unmet' status='live'; judgement cites reading=99.72 vs >= 99.9 → unmet; NOT-SERVED: uptime_goal (1a1ec133371-de7dd1c1d98a)
- **verdict reason:** It reports unmet with the basis given as the 99.72% November reading measured against the >=99.9 target (comparison false), not a bare status word.
- **what a wrong answer means:** If the answer cites a status rather than the reading, achievement is being declared instead of measured.

### Q5 — What was planned and never happened? → **right**

- **what the product answered:** `search('what was planned and never happened', object)` → 0 rows · `search(None, object)` → 2 rows: *Old onboarding flow* (kind=process, status=deprecated, rrf=None); *Thursday partner call — 6 November* (kind=core:plan, status=None, rrf=None) · `search('thursday partner call', object)` → 0 rows · `read_entity` → {"isClosed": true, "name": "Thursday partner call \u2014 6 November", "objectKind": "core:plan", "pipelineOutcome": "withdrawn", "pipelineSlot": "terminal", "pipelineStageId": "cancelled"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='available'; search: read_status='degraded'; by-id read_entity: stage='cancelled' slot='terminal' isClosed=True; found: partner_call (1a1ec133272-64582e57791c); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The ended scope serves the Thursday partner call — 6 November as a closed plan with terminal stage 'cancelled', while the still-pending December launch is not reported as never happened.
- **what a wrong answer means:** If the cancelled partner call cannot be found, the product cannot answer the one question a mutable-event model loses.

### Q6 — Has anything we relied on been withdrawn? → **right**

- **what the product answered:** `search('has anything we relied on been withdrawn', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search(None, point)` → 2 rows: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=None); *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=None) · `search('beach club confirmed booking', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search('beach club confirmed booking', point, include_terminal)` → 1 rows [related-to-question: 1/1]: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=0.857142857142857) · `read_entity` → {"content": "The beach club confirmed our 12 December booking.", "status": "retracted"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='available'; search: read_status='degraded'; search: read_status='degraded'; claim status='retracted' (a by-id read_entity); the ended-scope read served 2 withdrawn record(s) without the id being supplied: ['1a1ec134353-a233c156731f', '1a1ec132aa6-07bdbef2b880']; found: beach_club_claim (1a1ec134353-a233c156731f); NO-RELATION-TO-QUESTION: all 2 rows served for 'has anything we relied on been withdrawn' share no word with it — a degraded read ranked the corpus rather than answering; NO-RELATION-TO-QUESTION: all 2 rows served for 'beach club confirmed booking' share no word with it — a degraded read ranked the corpus rather than answering; DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The retracted beach-club confirmation is excluded from default scope and served with status 'retracted' when terminal scope is included, so it both stops counting and can be reported as withdrawn.
- **what a wrong answer means:** If the withdrawn claim still counts, belief is contaminated by retired evidence — the agent will act on something we retracted.

## Ingestion (the narrative through the product's write doors)

| op | ref | ok | detail |
|---|---|---|---|
| `declare_pipeline` | core:plan | yes | {"pipeline": "core:plan", "default": "scheduled", "source": "graph-override", "stages": ["scheduled", "held", "missed", "cancelled"]} |
| `object` | launch_plan | yes | core:plan → 1a1ec132801-088dd205af0b |
| `advance_stage` | launch_plan | yes | {"applied": true, "reason": "", "stage": "scheduled", "slot": "entry", "outcome": null} |
| `point` | q3_pricing | yes | decision → 1a1ec132aa6-07bdbef2b880 |
| `point` | q4_pricing | yes | decision → 1a1ec132f4a-197413b54f44 |
| `supersede_point` | q3_pricing | yes | superseded by 1a1ec132f4a-197413b54f44 |
| `object` | old_onboarding | yes | process → 1a1ec133130-eaeee588aab3 |
| `deprecate_object` | old_onboarding | yes | status=deprecated |
| `object` | partner_call | yes | core:plan → 1a1ec133272-64582e57791c |
| `advance_stage` | partner_call | yes | {"applied": true, "reason": "", "stage": "cancelled", "slot": "terminal", "outcome": "withdrawn"} |
| `object` | uptime_goal | yes | core:goal → 1a1ec133371-de7dd1c1d98a |
| `object` | uptime_target | yes | core:target → 1a1ec1333d5-fcfef6fdbf22 |
| `reading` | uptime_oct | yes | 99.95percent asOf 2026-10-31T23:59:00+00:00 |
| `reading` | uptime_nov | yes | 99.72percent asOf 2026-11-15T23:59:00+00:00 |
| `judge_goal` | uptime_goal | yes | state=unmet value=99.72 reading=1a1ec133692-fec0ad360937 |
| `point` | beach_club_claim | yes | statement → 1a1ec134353-a233c156731f |
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

- mode: `fresh` · cmd: `pi -p --no-session --no-tools` · 84.5s
