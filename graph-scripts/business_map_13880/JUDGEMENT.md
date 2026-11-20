# The business map: what was kept, what was dropped, what stayed ambiguous

**Issue:** #13880 · **Target:** clean graph `live-v5` (org `3326a01ea34ae595d84de5d8f9`, graph `g_311d79c684de4d0e8953d189ed`)
**Sources judged:** the four legacy graphs, measured 2026-11-19.
**Law applied:** `docs/ONTOLOGY.md` v3.38 (core) + `packs/product-strategy` + `packs/venture` + `skills/how-to-use-tortoise`.

This is a **business-mapping exercise**, not a migration. Nothing below is a copied row: every item was
re-read, re-judged and re-stated. `business_map.json` holds the result; `file_map.py` writes it.

---

## 1. The judgement rule I applied (and where it came from)

The owner, 2026-11-19 (#13880):

> *"the key is not bloating our ontology — packs + core declare business logic, this is like a business
> mapping exercise. A bunch of notes about a PR going through review is noise when what I want is
> architecture, strategy, product, etc."*

So the test for every candidate was **"would an operator, an investor, or a new engineer need this to
understand what we are building, for whom, and why?"** — not *"is it written down?"*.

That rule has teeth in this corpus, because the corpus is mostly **agent-session capture**: the largest
legacy graph's 24,787 Points are claims extracted from 2,960 captured sessions, and its `aboutObject`
edges (21,431 of them) point overwhelmingly at *harness* objects — `agent-ops:rule` (693),
`dev:pullRequest` (603), `core:workflow` (401), `dev:architectureDoc` (203, nearly all about CI gates,
rails, reapers and PR bodies). Filing those would be filing the process, not the business.

---

## 2. Kept — by topic

| Topic | Kept | Kinds used |
|---|---|---|
| **Goals / commitments** | the beta cascade: `validate revenue` → `launch private beta` → two gates (`merge all P0 and P1 issues`, `announcement ready for the interested segment in Attio CRM`) → `reduce agent-harness bottlenecks` → C1-C5. 10 Objects | `goal` + `goalState` (`active`/`blocked`) |
| **Product & market** | `Tortoise` (product), `the agent-memory market` (market), five competitors (Graphiti, Neo4j Agent Memory, GraphRAG, Supermemory, Letta/Cognee) | `product-strategy:product` / `:market` / `:competitor` |
| **Funding strategy** | six programmes (a16z Speedrun SR008, YC W2027, ERA W'27, Techstars Spring 2027, EF The Bridge, HF0) + the deadline-cluster, priority-sequence, density and ruled-out findings | `venture:program` + `statement` Points |
| **Unit economics** | Supabase's MAU floor, Identity Platform's banded rate, the DeepSeek supplier price spread, the paid-vs-free overage asymmetry | `statement` Points about the product |
| **Product & architecture decisions** | 14 decisions as **Events** (`eventKind: decision`): the beta gate measurement, the "interested" population definition, unlimited graphs (Gate #2), the two per-account storage dimensions (#11758), nodes retired as the pricing unit (#4495/#5331), fleet memory = the hosted graph, the hosted deployment postponement, EXTRACTOR-V4 §S3 (phase 2 receives the S1 narrative), the v3.37 status model ("good > easy"), the graph-as-tool-argument reopen rule, generic measurement text, no capture caps (#4010), the Wave-1 exit gate (the "aha test" withdrawn), and the business-map law itself | `Event` + `aboutObject` / `produces` |
| **Standing constraints** | 4 `standard` Objects: no per-graph caps / no node-count pricing; the MCP+SDK surface needs owner approval; no capture caps; the record is truth and the graph is a derived cache | `standard` |
| **Architecture claims** | 12 `statement` Points: record-vs-graph, the journal is not knowledge, decision-is-an-Event, goal-is-an-Object, role-vs-member, document-is-a-Source, the 91→27 surface consolidation, retention doctrine | `statement` |
| **Competitive findings** | the single-write-door finding **plus the three findings that REFUTE it** (Graphiti's three APIs, Neo4j's batch/streaming shapes, GraphRAG's batch pipeline), Supermemory's missing as-of search | `statement` + `NAND` operators |
| **Who acts** | `Premise Labs` (organization), `Daniel Ospina` (naturalPerson), `epistemic-team` (team), `owner` (role — `holdsRole`, not `memberOf`) | `Subject` |
| **Where it came from** | six documents as `Source` nodes, each `authoredBy` its author and `references` what it governs | `Source` |

The map is **78 nodes / 101 edges** (4 subjects + 27 objects + 6 sources + 14 events + 27 points;
51 operator edges + 50 structural). Deliberate shape choices:

- **A decision is an Event, never a Point** — so a query for *why* walks the timeline, and the belief
  layer stays about states.
- **A goal is an Object with `goalState`** — `blocked` is information; a `Point` could not carry it.
- **Support and contradiction are operators** — including three `NAND`s *against our own* single-door
  finding, because the corpus's counter-findings are worth keeping. The skill's rule (relevance lives on
  the operator, truth on the point) is why the counter-findings NAND the claim rather than the claim's
  target. ⛔ The MECHANISM is what is filed: `tortoise_create_operator` exposes no `label` parameter, so
  a Layer-2 word travels as the write's `semantics` and influences only the DIRECTION it resolves to
  (the gate warns when it contradicts the mechanism, and `file_map.py` now treats that warning as a
  failure). **The word itself is not readable back** — the surface stores no operator `label` — so
  `--verify` checks the mechanism and the direction, never the Layer-2 term; a label-only correction
  would require deleting the existing operator first, which this filer never does. The map's five `refutes` edges are therefore `NAND`s, not `IMPL`s (three of them against the
  single-write-door finding, which is what §3 rests on) — an `IMPL` wire carrying
  a `refutes` meaning RAISES the target where the judgement says it must lower it.
- **Nothing is mitigated.** A mitigation is 0.10-0.50 and must be written one at a time; none of these
  edges needed dampening, and inventing one to look thorough would have dampened EP for no reason.

---

## 3. Dropped — by category

Each line is a *category*, with the size that made it a category rather than an exception.

| Category | Evidence in the corpus | Why it is not business knowledge |
|---|---|---|
| **The write-ahead journal** | `GraphEvent` × 76,561 in the largest graph (42% of all nodes): `PointAdded`, `PointPromoted`, `OperatorAdded`, `OperatorPromoted`, `PointSuperseded`, `PointRetracted`, `ObjectSuperseded` | It is the record's own change log, whose home is Postgres (§2.0). Re-filing it would duplicate the journal into the cache that is derived *from* it. **Never re-filed.** |
| **Capture bookkeeping** | `Session` × 3,221, `OnboardingState`/`OnboardingStep`, `Meta`, `EpMeta`, `PackInstall`, `GraphEventMeta` | Container/metadata nodes. They say a session happened, not what the business knows. |
| **PR-review and lane chatter** | ~9,000 of the 16,337 `statement` Points in the largest graph are session-turn claims about a PR, a review round, a failing check, a mutation-attribution run, or a lane's own process | This is the category the owner named explicitly ("a bunch of notes about a PR going through review is noise"). It is the reason `dev:pullRequest` (603) and `core:review` (133) exist as objects at all. |
| **CI / test minutiae** | the `dev:architectureDoc` (203) and `core:standard` (786) populations are dominated by guards, pins, carve-outs, shard selection, citation guards and flake triage | A CI guard is a *means*; the business map wants the ends. Kept only where a guard *is* the constraint (#4010, surface approval). |
| **Harness / fleet operations** | `agent-ops:rule` (693), the entire object→object edge population (716 `IMPL`, 460 `related`, 369 `hasPart`, 348 `dependsOn`) — `merge-one.sh`, `record-review.sh`, `pr-doctor.py`, `lane-merge.sh`, the reaper, the rail | The harness is how we build, not what we sell. It is also where 90%+ of the legacy edge density sits, which is exactly why the legacy graph was not a business map. |
| **Benchmark / synthetic corpora** | a Q3→Q4 pricing quiz ("the entry tier becomes $39 per seat, replacing the Q3 decision that the entry tier is $29 per seat"), "what was planned and never happened", "thursday partner call", the six customer questions, the capstone scoreboard | **Dropped hardest, because it is the most dangerous to keep.** It reads as product truth and it is not: the real pricing is `product/pricing.json` (free $0 · solo $19 · pro $25 · team $149; its `owner_confirmed: 2026-08-07` records the pricing *decision*, not every later value move — solo moved to $19 in #11981). Importing the synthetic Q3/Q4 rows would have put a *false* price list in the business map. |
| **Onboarding / tutorial content** | the wizard-step and "connect your agent" claims, the seed/demo points | Activation bookkeeping. |
| **Telemetry & deploy verification** | "all 323 tests pass", "byte-identical to the committed build artifact (sha256 …)", console-error sweeps at 1440×900 | A deploy check is evidence *for a PR*, not a durable belief. |
| **Duplicates** | the four graphs are overlapping snapshots; the goal cascade existed **twice** (11:40Z carriers and 15:43Z carriers, the later explicitly `superseded` by the earlier) | One carrier per commitment. The corpus's own rule — earlier carrier wins, the later one is superseded *naming the winner* — was kept as a claim (`pt:dedup-earlier-carrier`) instead of the duplicate nodes. |
| **Superseded duplicates inside one graph** | `GOAL-A: validate revenue` (`status: superseded`, `supersededBy: 1a19ceaed34`), `option`/`criterion`/`evidence`-style Point kinds, `diary` (2), `finding` (1) | A superseded duplicate carries no knowledge the winner does not. And `option`/`criterion`/`evidence`/`claim` **are not kinds** — the ontology says so, so they were not carried as points at all. |

---

## 4. Ambiguous — stated rather than guessed

1. **`core:strategy` (98) / `core:plan` (73) objects: mostly dropped, and I am not fully certain.**
   Read one by one, nearly all are *engineering* strategies and plans — "the two-part record write fix",
   "the merge-one 81 arm fix", "the operand-scan fix for `_is_last_path_token`". A minority are plainly
   business-shaped ("the 12 December launch plan", "goal-measurement-and-reasoning plan"). I kept none as
   `strategy`/`plan` Objects, because a plan doc's authority is the document itself (`docs/plans/…`),
   which the graph should point at as a `Source` rather than paraphrase. **If the owner wants the plan
   corpus in the map, the right move is a Source-per-plan sweep, not an object per plan** — that is a
   follow-up decision, not something I should have decided silently.
2. **The accelerator programmes: `venture:program` is a defensible kind, but it is a pack decision.**
   The venture pack models *our* programmes of work funded by an agreement. An accelerator cohort is an
   external programme we apply to. I used `venture:program` because the corpus treats each one as a dated
   programme with terms and a deadline, and the pack's own `related`/`fundingAgreement` vocabulary then
   fits. The alternative — `:Subject` (the accelerator as an organization) + a `:Source` per term sheet —
   is also defensible. **Not settled.**
3. **Infrastructure suppliers were filed as findings, not Objects.** Supabase, Identity Platform and the
   DeepSeek suppliers appear in the legacy corpus as `product-strategy:product` / `:competitor` objects.
   They are not *our* products and not our competitors — they are the cost of goods. No declared kind
   means "supplier of a component", so their facts live as `statement` Points about `Tortoise` instead of
   inventing a kind. Splitting `:Source`/`:Object` for suppliers is a **pack** question for the owner.
4. **`el Dato` (eldato repo) was not mapped.** The pack's `product` examples name both `Tortoise` and
   `El Dato`, and DMeer appears in the Apresto corpus. I mapped only the product this graph and this issue
   are about. If the business map is meant to span both products, that is a scope decision for #13880's
   parent, not something to infer.
5. **`Apresto Internal` (`team_7a3b5403935f865c27d3fb1751`) is the same business.** I checked before
   trusting it: its Subjects are "Apresto Internal Test" / "Test Apresto 2026", but its content names
   Premise Labs, DMeer, Tortoise, the same accelerator cluster and the same competitor set. Treated as one
   corpus. If that org is actually a **separate tenant**, the accelerator and competitor findings should
   not have been read across — flagging it rather than assuming quietly.
6. **The "identity-and-placement" and "capability placement" decisions** are real architecture decisions
   that I could not re-state faithfully from the claims alone without re-deriving the design. I left them
   as decisions attached to `Tortoise` only where the decision text itself was self-contained.

---

## 5. What a query should now be able to answer

- *What are we trying to achieve, and what is in the way?* → the goal cascade with `goalState` and IMPL edges.
- *Why was the beta held, and on what measurement?* → `ev:beta-gates-measured` + `pt:beta-gate-counts`.
- *What did the owner decide about pricing, and what may never be re-introduced?* → three decision Events producing `std:pricing-shape`, plus `pt:paid-tiers-buy-overage`.
- *What is the market, who is in it, and what do we claim about it?* → the market, five competitors, and the single-door claim with its three `NAND`s.
- *What funding paths are live, and what did the survey find out?* → six programmes, the deadline cluster, the priority sequence, and the single-source warning.
- *Which document governs what?* → six Sources, `authoredBy` + `references`.
