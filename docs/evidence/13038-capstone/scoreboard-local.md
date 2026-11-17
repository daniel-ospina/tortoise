# Capstone #13038 — the state model measured by customer value

**Run:** 2026-11-17T18:42:34Z → 2026-11-17T18:46:07Z (target `local`, 213.0s)
**Product:** tortoise-sdk @ 446bd09a097680e2edadc4d37cd23075b60a8715
**Store:** `docker://:@127.0.0.1:16409/tortoise_test_matrix` · graph `capstone13038_20261117t184234z`
**Scenario:** `docs/evidence/13038-capstone/narrative.json` (frozen 2026-11-16)

## Scoreboard: 3 right · 3 wrong · 0 blocked · 0 unjudged

| # | question | verdict | what the product answered | why |
|---|---|---|---|---|
| **Q1** | What are we currently committed to? | **wrong** | search('what are we currently committed to — pricing, onboarding, launch and uptime') → 2 rows 2/2 on-topic · search('current commitments') → 0 rows · search('onboarding flow') → 0 rows | The answer serves Q4 pricing ($39/seat, superseding Q3) and the unmet Q4 uptime goal, correctly excluding the replaced Q3 price and the retired onboarding flow, but it omits the current 12 December launch plan — a third live commitment the narrative says exists — so the commitment set is incomplete. |
| **Q2** | Did we ever plan a launch event, and did it happen? | **right** | search('launch event') → 1 rows 1/1 on-topic · entity → {"pipelineStageId": "scheduled", "pipelineSlot": "entry", "isClosed": false} · events → 2 | By-id read returns the 12 December beach club launch as core:plan, stage 'scheduled', slot 'entry', isClosed false with no occurrence event anywhere in read_events, so the customer gets 'planned, not yet held'. |
| **Q3** | Why did the pricing decision change, and what replaced it? | **right** | search('pricing decision Q3 Q4') → 2 rows 1/2 on-topic · search('pricing decision Q3 Q4', +terminal) → 3 rows 2/3 on-topic · entity → {"status": "superseded"} · entity → {"status": "live"} | The Q3 decision is served as status 'superseded' (expired, validTo set) and the live Q4 decision both states 'replacing the Q3 decision' and carries an explicit supersedes edge to the Q3 id, naming the successor with no invented rationale. |
| **Q4** | Is the uptime goal met, and on what basis? | **right** | entity → {"status": "live", "goalState": "unmet"} · judge → unmet (99.72 vs >= 99.9) · events → 2 | The judge surface returns state 'unmet' with comparator >=, threshold 99.9, readingValue 99.72 as of 2026-11-15 and comparison false — the basis is the reading against the target, not a status word. |
| **Q5** | What was planned and never happened? | **wrong** | search('what was planned and never happened') → 0 rows · search('thursday partner call') → 1 rows 1/1 on-topic · entity → {"pipelineStageId": "cancelled", "pipelineSlot": "terminal", "isClosed": true} | The question-shaped query returned 0 rows, and the only row served for the cancelled call carried status 'live' as a current result — the customer is told a withdrawn call never happened nowhere, and is shown the cancelled plan as live where it does appear (only the by-id read reveals stage 'cancelled'/slot 'terminal'/isClosed true). |
| **Q6** | Has anything we relied on been withdrawn? | **wrong** | search('has anything we relied on been withdrawn') → 2 rows 0/2 on-topic · search('beach club confirmed booking') → 2 rows 0/2 on-topic · search('beach club confirmed booking', +terminal) → 1 rows 1/1 on-topic · entity → {"status": "retracted"} | For 'has anything we relied on been withdrawn' the default scope served two rows sharing no word with the question (Q4 pricing, Q4 uptime) at score 0.0; the retracted beach-club claim only appears when terminal objects are force-included, so the customer's question is answered with unrelated content. |

## Per-question detail

### Q1 — What are we currently committed to? → **wrong**

- **what the product answered:** `search('what are we currently committed to — pricing, onboarding, launch and uptime', point)` → 2 rows [related-to-question: 2/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=1.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.666666666666667) · `search('current commitments', object)` → 0 rows · `search('onboarding flow', object)` → 0 rows
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; search: read_status='degraded'; found: q4_pricing (1a1ead1b945-35956a12440e); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The answer serves Q4 pricing ($39/seat, superseding Q3) and the unmet Q4 uptime goal, correctly excluding the replaced Q3 price and the retired onboarding flow, but it omits the current 12 December launch plan — a third live commitment the narrative says exists — so the commitment set is incomplete.
- **cause:** The plan node is unreachable from the commitment query: the object search 'current commitments' returned 0 rows and the point search surfaced only pricing/uptime, with the valid_time leg reporting state_read_incomplete and the vector leg d…
- **what a wrong answer means:** If the replaced Q3 pricing decision or the retired onboarding flow appears, the product serves retired things as current — the trust failure this model exists to remove.

### Q2 — Did we ever plan a launch event, and did it happen? → **right**

- **what the product answered:** `search('launch event', object)` → 1 rows [related-to-question: 1/1]: *Launch event — 12 December at the beach club* (kind=core:plan, status=live, rrf=4.0) · `read_entity` → {"isClosed": false, "name": "Launch event \u2014 12 December at the beach club", "objectKind": "core:plan", "pipelineOutcome": "", "pipelineSlot": "entry", "pipelineStageId": "scheduled"} · `read_events` → 2 events
- **evidence it cited / signals:** search: read_status='degraded'; stage='scheduled' slot='entry' isClosed=False status=None; found: launch_plan (1a1ead1a106-732ea57b4059); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** By-id read returns the 12 December beach club launch as core:plan, stage 'scheduled', slot 'entry', isClosed false with no occurrence event anywhere in read_events, so the customer gets 'planned, not yet held'.
- **what a wrong answer means:** If 'planned' and 'happened' cannot be told apart, plans are being stored as facts — the answer would let an agent claim an event occurred.

### Q3 — Why did the pricing decision change, and what replaced it? → **right**

- **what the product answered:** `search('pricing decision Q3 Q4', point)` → 2 rows [related-to-question: 1/2]: *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `search('pricing decision Q3 Q4', point, include_terminal)` → 3 rows [related-to-question: 2/3]: *Q3 pricing: the entry tier is $29 per seat.* (kind=decision, status=superseded, rrf=3.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.333333333333333); *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.222222222222222) · `read_entity` → {"content": "Q3 pricing: the entry tier is $29 per seat.", "status": "superseded", "validTo": "2026-11-17T18:42:55.428975+00:00"} · `read_entity` → {"content": "Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 decision.", "status": "live"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; replaced node status='superseded', names-its-successor=False; found: q4_pricing (1a1ead1b945-35956a12440e); DEGRADED-READ: the product reported the read degraded (legs: vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The Q3 decision is served as status 'superseded' (expired, validTo set) and the live Q4 decision both states 'replacing the Q3 decision' and carries an explicit supersedes edge to the Q3 id, naming the successor with no invented rationale.
- **what a wrong answer means:** If the successor cannot be named, change is illegible — the agent cannot explain a change to a human.

### Q4 — Is the uptime goal met, and on what basis? → **right**

- **what the product answered:** `read_entity` → {"goalState": "unmet", "name": "Q4 uptime", "objectKind": "core:goal", "status": "live"} · `judge` → {"state": "unmet", "reason": "state-unchanged", "comparator": ">=", "threshold": 99.9, "readingValue": 99.72, "readingAsOf": "2026-11-15T23:59:00+00:00", "readingId": "1a1ead1e2b1-7640fec2913e", "comparison": false} · `read_events` → 2 events
- **evidence it cited / signals:** goalState='unmet' status='live'; judgement cites reading=99.72 vs >= 99.9 → unmet; found: uptime_goal (1a1ead1d4a2-7e3ac820c7a4)
- **verdict reason:** The judge surface returns state 'unmet' with comparator >=, threshold 99.9, readingValue 99.72 as of 2026-11-15 and comparison false — the basis is the reading against the target, not a status word.
- **what a wrong answer means:** If the answer cites a status rather than the reading, achievement is being declared instead of measured.

### Q5 — What was planned and never happened? → **wrong**

- **what the product answered:** `search('what was planned and never happened', object)` → 0 rows · `search('thursday partner call', object)` → 1 rows [related-to-question: 1/1]: *Thursday partner call — 6 November* (kind=core:plan, status=live, rrf=6.0) · `read_entity` → {"isClosed": true, "name": "Thursday partner call \u2014 6 November", "objectKind": "core:plan", "pipelineOutcome": "withdrawn", "pipelineSlot": "terminal", "pipelineStageId": "cancelled"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; by-id read_entity: stage='cancelled' slot='terminal' isClosed=True; SEARCH STILL SERVES THE CANCELLED PLAN: it came back from the plain default-scope search as a current row (status='live') — the S1 gap (#13034's predicate reads neither the terminal slot nor isClosed); found: partner_call (1a1ead1cf82-161bd0308f67); DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** The question-shaped query returned 0 rows, and the only row served for the cancelled call carried status 'live' as a current result — the customer is told a withdrawn call never happened nowhere, and is shown the cancelled plan as live where it does appear (only the by-id read reveals stage 'cancelled'/slot 'terminal'/isClosed true).
- **cause:** #13034 currency predicate reads neither the terminal slot nor isClosed, so default-scope search serves the cancelled plan as current, while the question itself matches nothing (default scope excludes terminal objects).
- **what a wrong answer means:** If the cancelled partner call cannot be found, the product cannot answer the one question a mutable-event model loses.

### Q6 — Has anything we relied on been withdrawn? → **wrong**

- **what the product answered:** `search('has anything we relied on been withdrawn', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search('beach club confirmed booking', point)` → 2 rows [related-to-question: 0/2]: *Q4 pricing: the entry tier becomes $39 per seat, replacing the Q3 dec…* (kind=decision, status=live, rrf=0.0); *Q4 uptime: unmet — latest reading 99.72 percent against >= 99.9 (dwel…* (kind=statement, status=live, rrf=0.0) · `search('beach club confirmed booking', point, include_terminal)` → 1 rows [related-to-question: 1/1]: *The beach club confirmed our 12 December booking.* (kind=statement, status=retracted, rrf=0.857142857142857) · `read_entity` → {"content": "The beach club confirmed our 12 December booking.", "status": "retracted"}
- **evidence it cited / signals:** search: read_status='degraded'; search: read_status='degraded'; search: read_status='degraded'; claim status='retracted' (a by-id read_entity); found: beach_club_claim (1a1ead20aee-ff688ecd407c); NO-RELATION-TO-QUESTION: all 2 rows served for 'has anything we relied on been withdrawn' share no word with it — a degraded read ranked the corpus rather than answering; NO-RELATION-TO-QUESTION: all 2 rows served for 'beach club confirmed booking' share no word with it — a degraded read ranked the corpus rather than answering; DEGRADED-READ: the product reported the read degraded (legs: fallback, vector) — rows may be the whole corpus ranked, not a match set
- **verdict reason:** For 'has anything we relied on been withdrawn' the default scope served two rows sharing no word with the question (Q4 pricing, Q4 uptime) at score 0.0; the retracted beach-club claim only appears when terminal objects are force-included, so the customer's question is answered with unrelated content.
- **cause:** Retracted/withdrawn evidence is filtered out of the default scope and the tfidf_snapshot fallback ranks the corpus instead of matching (2/2 zero-overlap rows); the withdrawn claim is reachable only via include_terminal.
- **what a wrong answer means:** If the withdrawn claim still counts, belief is contaminated by retired evidence — the agent will act on something we retracted.

## Ingestion (the narrative through the product's write doors)

| op | ref | ok | detail |
|---|---|---|---|
| `declare_pipeline` | core:plan | yes | {"pipeline": "core:plan", "default": "scheduled", "source": "graph-override", "stages": ["scheduled", "held", "missed", "cancelled"]} |
| `object` | launch_plan | yes | core:plan → 1a1ead1a106-732ea57b4059 |
| `advance_stage` | launch_plan | yes | {"applied": true, "reason": "", "stage": "scheduled", "slot": "entry", "outcome": null} |
| `point` | q3_pricing | yes | decision → 1a1ead1a796-1afcb06625a7 |
| `point` | q4_pricing | yes | decision → 1a1ead1b945-35956a12440e |
| `supersede_point` | q3_pricing | yes | superseded by 1a1ead1b945-35956a12440e |
| `object` | old_onboarding | yes | process → 1a1ead1c6e5-a8314335ba3c |
| `deprecate_object` | old_onboarding | yes | status=deprecated |
| `object` | partner_call | yes | core:plan → 1a1ead1cf82-161bd0308f67 |
| `advance_stage` | partner_call | yes | {"applied": true, "reason": "", "stage": "cancelled", "slot": "terminal", "outcome": "withdrawn"} |
| `object` | uptime_goal | yes | core:goal → 1a1ead1d4a2-7e3ac820c7a4 |
| `object` | uptime_target | yes | core:target → 1a1ead1d508-47dc0e0c8b1c |
| `reading` | uptime_oct | yes | 99.95percent asOf 2026-10-31T23:59:00+00:00 |
| `reading` | uptime_nov | yes | 99.72percent asOf 2026-11-15T23:59:00+00:00 |
| `judge_goal` | uptime_goal | yes | state=unmet value=99.72 reading=1a1ead1e2b1-7640fec2913e |
| `point` | beach_club_claim | yes | statement → 1a1ead20aee-ff688ecd407c |
| `retract_point` | beach_club_claim | yes | status=retracted |

## Target and honesty notes

- retrieval leg `vector` degraded in 11 of 11 reads (reasons: no_embedder)
- retrieval leg `fallback` degraded in 5 of 5 reads (reasons: no_fallback_applicable, tfidf_snapshot)
- the state-model verdicts in this scoreboard are read against that posture: a row that says a thing was wrong because unrelated rows came back is the read path, not the lifecycle model
- session capture — the SDK recorder was retired (#4282); only the hosted POST /v1/sessions captures sessions, and it needs an extractor model. This run therefore writes the state layer through the entity doors, which is the surface the state model governs.

### Retrieval posture of the target

| leg | reads | degraded | reasons |
|---|---|---|---|
| `vector` | 11 | 11 | no_embedder |
| `fts` | 11 | 0 | ok, empty_results |
| `structural` | 11 | 0 | empty_results |
| `valid_time` | 4 | 0 | state_read_incomplete, no-prefer-latest-intent |
| `fallback` | 5 | 5 | no_fallback_applicable, tfidf_snapshot |

### Judge

- mode: `fresh` · cmd: `pi -p --no-session` · 150.2s
