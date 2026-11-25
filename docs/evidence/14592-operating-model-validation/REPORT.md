# #14592 Phase D — does extractor v4 build an OPERATING MODEL from a fresh corpus?

**Answer: partly. It extracts the operating model's *content* well and wires the
*reasoning* around a decision correctly. It does not represent a contradiction between two
documents, does not emit the role→object `performs`/`owns` relation, and its search door returns
the *stale* side of the contradiction as its best answer. Those are gaps to fix, not inventions —
nothing in the extracted graph was unsupported by the corpus.**

| | |
|---|---|
| Lane | `chore/extractor-operating-model-validation` |
| PR | #14606 |
| Graph under test | `xv14592_operating_model` — its own graph on this lane's private FalkorDB (`fdb-lane-4258be895b`, `127.0.0.1:16467`) |
| Corpus | `docs/evidence/14592-operating-model-validation/corpus/corpus.json` |
| Code under test | this worktree at `origin/main` = `51f444fd088` (verified: `git rev-parse HEAD origin/main` match) |
| Date of run | 2026-11-25, 00:56–01:07 UTC |
| Extractor revision | `extractor_v2.py` @ `51f444fd088`; pipeline `extraction_mode: "llm:deepseek-direct"`, model `deepseek-v4-flash` |

---

## 0. The method rule, and how this run obeyed it

Every read and write went through the **Tortoise SDK** or the **Tortoise MCP server**. Nothing in
`run_ingest.py`, `run_readback.py`, `run_census.py` opens a socket, builds a Cypher string, or
touches the record store.

| Step | Door used | Why that is the product's own door |
|---|---|---|
| Write | `TortoiseSDK.mine_knowledge_from_session(conversation=…, session_id=…)` | `tool_registry` maps `tortoise_mine_knowledge_from_session` onto exactly this method (`sdk_method="mine_knowledge_from_session"`). It routes to `_capture_session` → `_extract_session_v2` → `extractor_v2.extract_session_v2` (S1 story → S2 map → S3 real-backend search → S4 gap review → S5 embed). |
| Read (search) | `tortoise_search`, `tortoise_search_knowledge`, `tortoise_suggest_entry_points` over **MCP stdio** (`python -m tortoise.mcp_server`) | This is literally the door the owner named. The client is `fastmcp.Client` against the real server process — not an in-process import of the same function. |
| Read (structure) | `tortoise_graph_overview`, `tortoise_check_structure`, `tortoise_list_knowledge`, `tortoise_expand_relationships`, `tortoise_explore_connections`, `tortoise_recall`, `tortoise_check_confidence` — all over MCP stdio | Same. |
| Isolation proof | `tortoise_graph_overview` **before** any write | Returns `Point 0, Event 0, Subject 0, Object 0, Source 0` — recorded below. |

Reproduce with:

```bash
export TORTOISE_DB_URI='docker://:@127.0.0.1:16467/tortoise_test_matrix'   # this lane's container
./docs/evidence/14592-operating-model-validation/run.sh \
    docs/evidence/14592-operating-model-validation/run_ingest.py            # 1. write
./docs/evidence/14592-operating-model-validation/run.sh \
    docs/evidence/14592-operating-model-validation/run_readback.py          # 2. read back
./docs/evidence/14592-operating-model-validation/run.sh \
    docs/evidence/14592-operating-model-validation/run_census.py            # 3. structural census
```

`run.sh` documents the two (measured) reasons it does not use `uv run` on this box — see §7.

---

## 1. The corpus

Five short **meeting transcripts** about one small business (Harbour Lane Provisions, a specialty
grocery wholesaler). Written for this test and never derived from any existing graph.

| doc | date | carries | content |
|---|---|---|---|
| `doc-01` | 2026-02-03 | **decision with 3 options + stated reason** | Stay with NordicFreight (6pm cutoff misses morning slots) / switch to ColdPath Logistics (9pm cutoff, validated certificate) / buy own refrigerated van (48,000 capital, cash buffer is 31,000 and committed) → **choose ColdPath** |
| `doc-02` | 2026-02-10 | **constraint** | cold chain below 4 °C at all times; a retained temperature log per delivery; licence forbids subcontracting chilled goods to a partner without a validated cold-chain certificate |
| `doc-03` | 2026-02-17 | **goal** | 40 restaurant accounts by December (18 today); delivery capacity / morning slots are the binding limit |
| `doc-04` | 2026-02-24 | **two roles/people and who does what** | Marta Reyes owns delivery scheduling + the cold-chain log; Dev Patel owns supplier payments + capex approvals; Priya Nair owns account onboarding + pipeline |
| `doc-05` | 2026-03-05 | **contradicts doc-01** | ColdPath failed its cold-chain audit → **revert to NordicFreight**; the 40-account goal stands but NordicFreight's 6pm cutoff still binds |

**Size: 4,642 characters of content ≈ 1,160 tokens** (5 documents, 34 turns). Small enough that the
extraction arithmetic below is checkable by hand.

**Scoping note (a product observation, not a workaround):** the corpus is delivered as meeting
transcripts because **the extractor v4 has exactly one door — session capture**. There is no v4 door
that takes a directory of documents: `mine_knowledge_from_directory` runs the older M2
`LLMExtractor` (`mining.ConversationMiner._make_extractor` → `LLMExtractor`), and
`index_sources_from_directory` extracts nothing at all. If the intended customer story is "point v4
at a document corpus", that door does not exist yet. See **gap G7**.

---

## 2. Ingest — what the product reported

### Isolation confirmed before the first write

`tortoise_graph_overview` on `xv14592_operating_model`, before any write:

```json
"status": {"connected": true,
           "counts": {"Point": 0, "Event": 0, "Subject": 0, "Object": 0, "Source": 0},
           "total_entities": 0}
```

This graph has never been written by an agent, by a fixture, or by any earlier lane. It is not
`live-v5` and not the owner's dogfood graph (recorded decision (b)).

### After five captures

```json
"taxonomy": {"Point": 120, "Event": 13, "Subject": 10, "Object": 23, "Source": 5},
"structure": {"operators": 24, "total": 96},
"pointkinds": {"total": 96, "by_kind": {"statement": 62, "event": 34}},
"sources": {"total": 5, "with_points": 5, "by_kind": {"agentSession": 5}},
"health": {"status": "ok", "falkordb": "connected", "graph_size": 171, "errors": 0,
           "journal_write_failures": 0}
```

Per document, the FULL product receipt is in `out/ingest-receipt.json`. Headline:

| doc | ack (s) | LLM calls | prompt tok | completion tok | points | operators | relations | entities | events | search queries | degraded | warnings |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| doc-01 | 22.0 | 3 | 25,107 | 3,193 | 13 | 11 | 3 | 6 | 2 | 15 | false | 15 |
| doc-02 | 25.2 | 4 | 27,035 | 4,153 | 13 | 4 | 4 | 10 | 1 | 15 | false | 15 |
| doc-03 | 24.3 | 4 | 25,800 | 2,382 | 8 | 5 | 3 | 7 | 2 | 15 | false | 8 |
| doc-04 | 23.0 | 4 | 26,655 | 3,194 | 12 | 3 | 10 | 14 | 1 | 15 | false | 4 |
| doc-05 | 29.6 | 4 | 27,268 | 4,052 | 12 | 7 | 6 | 11 | 2 | 15 | false | 21 |
| **total** | **24.8 avg** | **19** | **131,865** | **16,974** | | | | | | | | |

All five documents succeeded (`ok: true`, `errors: []`), `extraction_mode: "llm:deepseek-direct"`.

### Extraction tokens spent (the "one token budget")

| stage | calls | prompt | completion |
|---|---|---|---|
| `resolve` | 4 | 4,127 | 296 |
| `s1` (story) | 5 | 9,075 | 1,162 |
| `s2` (map) | 5 | 50,119 | 14,089 |
| `s4` (gap review) | 5 | 68,544 | 1,427 |
| **total** | **19** | **131,865** | **16,974** |

**148,839 tokens for 1,160 tokens of corpus — a 128× amplification.** For scale: the owner's real
deployment census was 98,430 Points across 122 graphs; at this ratio each 1k tokens of input costs
~128k tokens of extraction.

**Cost is NOT reported.** `stats.llm.cost_usd = 0.0` with `calls_without_cost: 19` on every call —
`deepseek-direct` returns no `usage.cost`, so the product reports tokens but cannot price them.
S3's real-backend search ran (`search_queries: 15`, `search_degraded: false`) and is token-free.

Graph work per document is also large: doc-01 recorded `graph_ops.total = 1048` (862 reads, 186
writes), of which the **extraction** phase alone was 617 reads.

### What the product said was wrong with its own extraction

Recorded verbatim from `stats`/`warnings` (doc-01):

```
"point 'The delivery partner question was believ' has no resolvable source turn (no quote match)"
"value-bearing point 'ColdPath Logistics offers a 9pm cutoff that meets the mornin' resolved to turn 3
 by the token-overlap band, not a verbatim anchor — it carries no source span (the value cannot be
 re-read verbatim)"
"point when '2026-01' is not a valid ISO date — dropped"
"S3 identity partial: the existing-item lookup did not run for 6 of 21 candidate(s)"
"value-normalizer: 6 point(s) carried an amount that was not typed — no_currency_anchor ×6
 (prose only — each value is a drop, never a guess)"
```

S4 corrected nothing (`s4_merge: corrected_by_s4 0, verbatim_reemissions 0, kept_from_s2 27,
added_by_s4 4`) — the gap-review stage added 4 items and confirmed all 27 of S2's.

---

## 3. Read-back, MCP only — the five ontology layers

All five layers exist and each carries corpus facts:

| layer | count | corpus facts found (verbatim from `tortoise_recall` / `tortoise_graph_overview`) |
|---|---|---|
| **Object** | 23 | `core:goal` "restaurant account growth goal"; `core:standard` "wholesale licence"; `core:decision` "the revert to NordicFreight"; `agent-ops:rule` "cold-chain temperature floor rule", "per-delivery temperature log rule"; "our own refrigerated van" |
| **Subject** | 10 | "Marta Reyes", "Dev Patel", "ColdPath Logistics", "NordicFreight", "food safety auditor", "delivery partner" |
| **Event** | 13 | 5 `sessionCaptured` + decision/friction/milestone events |
| **Source** | 5 | `session:doc-01` … `session:doc-05`, each `sourceKind: agentSession`, with `points` 8–13 |
| **Point** | 120 | 96 non-episodic (62 `statement`, 34 `event`) + episodic turns |

`tortoise_list_knowledge(kind="source")` returns verbatim:

```json
[{"url":"session:doc-02","sourceKind":"agentSession","points":13},
 {"url":"session:doc-01","sourceKind":"agentSession","points":13},
 {"url":"session:doc-05","sourceKind":"agentSession","points":12},
 {"url":"session:doc-04","sourceKind":"agentSession","points":11},
 {"url":"session:doc-03","sourceKind":"agentSession","points":8}]
```

**But three layer reads have no MCP door at all** — see gaps **G5**, **G6**.

---

## 4. Read-back — "is search retrieving the proper things"

`tortoise_search` (the owner's door), `limit=8`. Full replies in `out/readback.json`.

| probe | corpus fact a correct graph must answer with | what search actually returned (rank order) | verdict |
|---|---|---|---|
| **decision** — "Which delivery partner did we choose, and why?" | ColdPath, because 9pm cutoff makes the morning slots + validated certificate | 1. "We believed ColdPath Logistics was a viable delivery partner…" (doc-05, conf 0.5)<br>2. "The growth goal depends on the delivery partner keeping morning slots" (doc-03)<br>3. "We revert to NordicFreight…" (doc-05, conf 0.4622)<br>4. "The delivery partner question was believed to be a simple incumbent-versus-alternative tradeoff" (conf 0.3294)<br>5. raw turn `[assistant] Decision: we revert to NordicFreight…` | ⚠️ **partial** — the actual chosen decision ("We choose ColdPath Logistics as our delivery partner") is **not in the top 5**; a weaker belief sentence outranks it, and the *reversal* ranks above the decision it reverses |
| **constraint** — "What must the cold chain stay below, what does the auditor require?" | < 4 °C at all times; a retained temperature log per delivery | 1. raw turn `[user] And the auditor?`<br>2. raw turn `[assistant] Decision: we revert to NordicFreight…`<br>3. raw turn `[assistant] Marta Reyes (Operations Lead): ColdPath failed its cold-chain audit…`<br>4. "The cold-chain log and capital-spend clauses carry explicit accountability rules…"<br>5. "The wholesale licence forbids subcontracting chilled-goods delivery to any partner that does not hold…" | ❌ **miss** — the 4 °C floor and the twelve-month log retention are **not in the top 5**; 3 of 5 slots are raw episodic turns |
| **goal** — "What is our goal for restaurant accounts, what limits it?" | 40 accounts by December; capacity limits it | 1. "The year's target is 40 restaurant accounts by December, against a current base of 18 — 22 more…"<br>2–3. `[MITIGATION] NordicFreight's 6pm cutoff still cannot make our morning restaurant slots` (×2, duplicate text)<br>4. "The growth goal of 40 restaurant accounts by December stands"<br>5. "Priya Nair owns the restaurant account pipeline" | ✅ **correct** — the goal and its binding limit both surface; ⚠️ the same mitigation is returned **twice** |
| **role** — "Who owns the cold-chain temperature log?" | Marta Reyes | 1. "Marta Reyes owns the cold-chain temperature log"<br>2. "The food safety auditor requires a temperature log for every single delivery, retained for twelve months"<br>3. "Logging should be treated as part of the delivery flow itself" | ✅ **correct** — rank 1 is exactly the corpus fact |
| **contradiction** — "Did ColdPath fail its cold-chain audit, and what did we do?" | yes; reverted to NordicFreight | 1. **"ColdPath Logistics holds a validated cold-chain certificate"** (doc-01, `status: live`, conf 0.5)<br>2. "The cold-chain log and capital-spend clauses carry explicit accountability rules…"<br>3. "Cold-chain rules forbid adding an account unless the cold chain can be held below 4 degrees…"<br>4. "The wholesale licence forbids subcontracting chilled-goods delivery to any partner…"<br>5. "Marta Reyes owns the cold-chain temperature log" | ❌ **wrong answer** — the top hit is the claim doc-05 **refutes**, still `live` at conf 0.5. The failure ("The cold-chain audit failed ColdPath Logistics last week", conf 0.3294) and the revert are **not in the top 5** |
| **naming** — "What companies and people are involved?" | NordicFreight, ColdPath Logistics, Marta Reyes, Dev Patel, Priya Nair | 1–5. raw turns only — `[user] What are the numbers and what argues against each?`, `[user] Priya, what is the number we are chasing this year?`, … — every `scores.fts` **null** | ❌ **miss** — an entity question returns five user sentences and zero entities |

**Two findings fall straight out of this table.**

1. **Raw episodic turns compete with extracted claims in the same ranking.** `[user] And the auditor?`
   and `[assistant] Decision: …` are stored as Points and are returned by `tortoise_search` as if
   they were knowledge. On two of six probes they occupy 3 of the top 5. A caller cannot tell a
   verified claim from a transcript fragment except by the `[user]`/`[assistant]` prefix in the text.
2. **A refuted claim is not demoted and the refutation is not promoted.** This is the same defect as
   **gap G1** seen from the read side: nothing in the graph connects doc-05's refutation to doc-01's
   certificate claim, so search has no signal with which to rank one above the other.

### Retrieval is FTS-only in this run

Every search result carries `"scores": {"fts": <n>, "vector": null, "structural": null, "rrf": <n>}`.
The dense leg contributed nothing, because `sentence_transformers`/`torch` are not installed and
cannot be installed on this box (see §7). **This is an environment limitation, not a product defect:
the numbers above are a FLOOR, not the ceiling.** They do, however, measure what a self-hosted user
gets when the `embeddings` extra is absent — which `AGENTS.md` says is a keyword-only product.

---

## 5. Read-back — relations, and were any invented?

Relation classes are written as **reified operator Points** (`IMPL`/`NAND` as `mechanism`), plus a
small number of structural edges. 24 operators total.

### The decision's reasoning is captured correctly

`tortoise_expand_relationships(point_id="pt_f0a2b96…")` — "We choose ColdPath Logistics as our
delivery partner" — returns 7 incoming `IMPL` edges, each traceable to the corpus:

```json
{"mechanism":"IMPL","related_content":"ColdPath Logistics offers a 9pm cutoff that meets the morning restaurant slots","direction":"incoming"}
{"mechanism":"IMPL","related_content":"ColdPath Logistics holds a validated cold-chain certificate","direction":"incoming"}
{"mechanism":"IMPL","related_content":"NordicFreight's 6pm order cutoff cannot make our morning restaurant slots","direction":"incoming"}
{"mechanism":"IMPL","related_content":"Dev Patel ruled the capital spend for our own refrigerated van not approvable this quarter","direction":"incoming"}
{"mechanism":"IMPL","related_content":"The own-van option needs 48,000 pounds of capital spending, which is more than the current cash buffer allows","direction":"incoming"}
{"mechanism":"IMPL","related_content":"[MITIGATION] The cash buffer is 31,000 pounds and is already committed to the March supplier payments","direction":"incoming"}
{"mechanism":"IMPL","related_content":"Buying our own refrigerated van needs 48,000 pounds of capital spending","direction":"incoming"}
```

Three options, all three reasons, and the affordability argument carries an extracted **MITIGATION**
node. This is the part of the answer that is genuinely good.

### NAND operators exist

Search returned NAND families over the "viable delivery partner" belief
(`"mechanism":"NAND"`, `family_size: 2`, both endpoints `contested: true`, conf 0.3294).

### Required relation classes — present / absent

| required class | present? | evidence |
|---|---|---|
| decision → option `IMPL` | ✅ | 7 incoming `IMPL` edges on the doc-01 decision (above) |
| option ↔ option `NAND` | ✅ | NAND operator families on the delivery-partner belief (`family_size: 2`) |
| goal → constraint | ✅ (as text + `IMPL`) | "The growth goal depends on the delivery partner keeping morning slots" → `IMPL` → "Every new restaurant account requires a morning delivery slot"; "Cold-chain rules forbid adding an account unless the cold chain can be held below 4 degrees" |
| source → claim `extractedFrom` | ✅ (structural) | 5 `agentSession` Sources, each with 8–13 Points; `relation_write.edges` shows `related`/`managedBy` written as `structural` |
| **role → object `performs` / `owns`** | ❌ **ABSENT** | The corpus fact is extracted as a *statement* ("Marta Reyes owns the cold-chain temperature log" — subject `Marta Reyes`) and **no `performs`/`owns` edge to an Object exists.** Ownership is prose, not structure. See **G2** |
| cross-document contradiction | ❌ **ABSENT** | See **G1** |

### Were any relations or entities invented?

**No.** Every edge endpoint inspected traces to a sentence in the corpus, and every Object/Subject
name is a surface form that appears in the corpus. The corpus contains no currency symbol and the
product correctly refused to type the amounts —

```
value-normalizer: 6 point(s) carried an amount that was not typed — no_currency_anchor ×6
(prose only — each value is a drop, never a guess)
```

— which is the recorded decision **(a)** ("the extractor may not invent") holding under a corpus that
invites a guess (£48,000 / 31,000 / 40 accounts … 4 °C). One `point when '2026-01' is not a valid ISO
date — dropped` is a *drop*, not a fabrication.

---

## 6. The element-by-element table the owner asked for

*element of the operating model → present? → correct? → traceable to the corpus?*

| # | element (corpus fact) | present? | correct? | traceable? | evidence |
|---|---|---|---|---|---|
| 1 | **Decision** "we choose ColdPath Logistics" | ✅ | ✅ | ✅ `sessionId: doc-01`, `quote` present | Point exists, 7 `IMPL` reasons attached |
| 2 | **Option A** stay with NordicFreight | ✅ | ✅ | ✅ | "NordicFreight's 6pm order cutoff cannot make our morning restaurant slots" |
| 3 | **Option B** switch to ColdPath | ✅ | ✅ | ✅ | "ColdPath Logistics offers a 9pm cutoff that meets the morning restaurant slots" |
| 4 | **Option C** buy own refrigerated van | ✅ | ✅ | ✅ | Object "our own refrigerated van"; "needs 48,000 pounds of capital spending" |
| 5 | **Stated reason** for the choice | ✅ | ✅ | ✅ | 9pm cutoff + validated certificate, both `IMPL`-ed into the decision |
| 6 | **Constraint** cold chain < 4 °C | ✅ | ✅ | ⚠️ present as Point; **not retrievable** for the constraint question | "Cold-chain rules forbid adding an account unless the cold chain can be held below 4 degrees" — absent from the top 5 for its own probe |
| 7 | **Constraint** temperature log per delivery, retained 12 months | ✅ | ✅ | ✅ `quote` present | "The food safety auditor requires a temperature log for every single delivery, retained for twelve months" |
| 8 | **Constraint** licence forbids unvalidated subcontractors | ✅ | ✅ | ✅ `quote` present | `core:standard` "wholesale licence" + the statement |
| 9 | **Goal** 40 restaurant accounts by December | ✅ | ✅ | ✅ | `core:goal` "restaurant account growth goal" + "The year's target is 40 restaurant accounts by December… 22 more" — **rank 1** for its probe |
| 10 | **Goal limit** delivery capacity / morning slots | ✅ | ✅ | ✅ | "Growth is gated by delivery capacity, not sales effort"; "Every new restaurant account requires a morning delivery slot" |
| 11 | **Role** Marta Reyes — owns delivery scheduling + cold-chain log | ✅ as text | ✅ | ✅ | "Marta Reyes owns the cold-chain temperature log" — **rank 1** for its probe |
| 12 | **Role** Dev Patel — owns supplier payments + capex approvals | ✅ as text | ✅ | ✅ | "No capital spend is approved unless Dev Patel approves it" |
| 13 | **Role** Priya Nair — owns account onboarding + pipeline | ✅ as text | ✅ | ✅ | "Priya Nair owns the restaurant account pipeline" |
| 14 | **Role → Object edge** (`owns`/`performs`) | ❌ **absent** | — | — | No `owns`/`performs` edge; ownership only as statement text. **G2** |
| 15 | **Contradiction** ColdPath failed the audit | ✅ | ✅ | ✅ (`quote` present) | "The cold-chain audit failed ColdPath Logistics last week" (conf 0.3294) |
| 16 | **Contradiction resolved** revert to NordicFreight | ✅ as a decision Point | ✅ | ✅ | `core:decision` "the revert to NordicFreight"; "We revert to NordicFreight as our delivery partner…" |
| 17 | **Contradiction as a graph conflict** | ❌ **absent** | — | — | No `NAND`/supersede edge between doc-01's choose-ColdPath and doc-05's revert. Both `status: live` (conf 0.5 and 0.4622). **G1** |
| 18 | **EP per object** | ✅ | ⚠️ flat | ✅ | `ep_state: measured` on every statement; `confidence_mean` 0.5 for almost all, 0.3294/0.4622 on contested ones; `variance` ≈ 0.083. `contested: true` on **every** statement including uncontested ones — the flag does not discriminate |

**Score: 14 of 18 elements fully correct and traceable. Two structural classes missing (14, 17), one
retrieval failure (6), one confidence field that does not discriminate (18).**

---

## 7. Gaps — each with its evidence

### G1 — A contradiction between two documents is not represented as a conflict (the biggest gap)

The corpus's whole point is that doc-05 refutes doc-01. The graph holds **both as true**:

```
"We choose ColdPath Logistics as our delivery partner"   status: live   conf 0.5
"We revert to NordicFreight as our delivery partner…"    status: live   conf 0.4622
"ColdPath Logistics holds a validated cold-chain certificate"  status: live  conf 0.5
```

`tortoise_expand_relationships` on **both** decision points returns only edges **inside their own
document**. Doc-01's decision has 7 incoming `IMPL` (all from doc-01). Doc-05's revert has 4 incoming
`IMPL` (all from doc-05). `tortoise_explore_connections(doc-01 decision, hops=3)` returns **8 nodes,
all from doc-01** — it never reaches doc-05.

The extractor *did* extract the refuting facts (the failed audit, the licence clause). What it did not
do is relate them to the claim they refute. `stats.supersessions: 0` on all five documents, and
`sdk._capture_session`'s own docstring says supersession records form **only** when S3's search
resolves them — S3 ran (`search_queries: 15`, `search_degraded: false`) and produced none.

This is the difference between "an operating model" and "a pile of true sentences about the
business": the model must know that the current partner is NordicFreight *because* ColdPath was
rejected, not hold both.

### G2 — role → object `performs`/`owns` is never emitted

Doc-04 is entirely "who owns what", and the required relation class from the brief does not appear.
Ownership survives only as statement text with a `subject`; there is no Object endpoint and no
`owns`/`performs`/`managedBy` edge from a person to the thing they own. (Three structural `managedBy`
edges do exist — on the extractor's own internal entities, not on the corpus's ownership facts:
`relation_write.write_paths.structural = 3`.)

The corpus is unambiguous ("Marta Reyes owns the cold-chain temperature log") and the extractor read
it correctly. It has no output section for it.

### G3 — search returns the refuted claim as the best answer

Probe "contradiction" → rank 1 is the claim doc-05 refutes, `status: live`, conf 0.5. The refutation
is not in the top 5. A downstream agent asking "did ColdPath fail the audit?" is told, first, that
ColdPath holds a validated certificate. This is **G1 seen from the read side** — the two are one root.

### G4 — raw episodic turns are returned as knowledge, at claim rank

`tortoise_search` returns `[user] And the auditor?` and `[assistant] Decision: we revert to
NordicFreight…` in the same ranked list as extracted claims, with `scores.fts` comparable. On the
constraint probe they take 3 of the top 5; on the naming probe they take **all 5**. There is no
`point_kind`/`entity_type` distinction in the reply that a caller can filter on without knowing the
convention in advance.

### G5 — no MCP door to enumerate Object / Subject / Event layers

The brief's step 4 asks for "entity/object/subject/source reads". Over MCP:

* `tortoise_list_knowledge(kind="object")` → `{"error":"list_knowledge: unknown kind 'object'. Valid
  kinds: claim, source, source_reliability, batch, namespace, topic, diary"}`
  (same for `subject`, `event`, `point`)
* `tortoise_search(entity_type="object")` — the tool's own description says *"Full-scan mode: omit
  query, set kind → all Points of kind"* — returns **0 rows** for `object`, `subject`, `event`,
  `source`
* `tortoise_query(kind="operator")` returns **0 rows**, though the graph has 24 operators
* `tortoise_list_knowledge(kind="claim")` returns **0 rows**, though the graph has 62 statement Points

The layers were readable only through `tortoise_graph_overview` (counts) and `tortoise_recall`
(content). Those two doors work; the direct layer reads do not. **`check_structure` returned
`{"result": []}`** on a graph that is holding two mutually exclusive live decisions — the structural
validator sees no problem, which is consistent with G1.

### G6 — the source layer cannot be listed over stdio MCP

`tortoise_manage_sources(action="list")` → `{"error":"No team context (HTTP mode required)"}`.
`tortoise_list_knowledge(kind="source")` works, so this is a second door disagreeing with the first,
not a hard block.

### G7 — extractor v4 has no document-corpus door

`mine_knowledge_from_directory` (the batch door) runs M2's `LLMExtractor`, not v4:
`ConversationMiner._make_extractor` → `LLMExtractor` when a model is passed, `MockExtractor`
otherwise. There is no `file_type` value, and no argument on `index_sources_from_directory`, that
reaches `extract_session_v2`. So "run v4 over my document corpus" is not expressible through the
product — the only v4 door is one session at a time.

### G8 — the surface the brief names is out of date, and one tool is invisible

The brief says "27 tools". The live surface is **39** (`out/tool-surface.json`). And
`tortoise_recall` **answers** over MCP but is **absent from `tools/list`** — an agent that discovers
capability the normal MCP way will never find the one tool that returns Objects with EP and degree.
(`Tool tortoise_recall not listed by server, cannot validate any structured content` — emitted while
the same call returned a full result set.)

### G9 — `check_confidence(topic=…)` is inert, and disagreeing doors

`tortoise_check_confidence(topic=<any of the six probes>)` returned `[]` for every probe, while
`tortoise_check_confidence()` with no filter returns the whole population **and** the same call with
`claim_id=""` returns rows with EP. Two doors disagree about the same graph, and the topic filter
answers "nothing" about a topic the graph is full of.

### G10 — token amplification is 128×

148,839 tokens to model 1,160 tokens of corpus, with **cost unreported** (`calls_without_cost: 19`).
S4 (gap review) alone reads 68,544 prompt tokens to add 4 items and confirm 27. If the intended
customer story is "ingest your meeting corpus", this ratio is the product's unit economics.

### G11 — traceability is incomplete at the point of extraction

Doc-01 emitted 15 warnings including `has no resolvable source turn (no quote match)` for two Points,
and five value-bearing Points resolved only "by the token-overlap band, not a verbatim anchor — it
carries no source span (the value cannot be re-read verbatim)". So a subset of Points cannot be
re-read verbatim against the corpus. The `quote` field **is** present on most search hits, so the
mechanism works — it is the silent subset without a span that matters, and the product counts it in
`warnings` rather than exposing it as a per-Point flag.

---

## 8. Improvements — what to change in extraction, each with the evidence

1. **Emit a cross-session conflict relation when S3/S4 finds that a new claim refutes a prior one.**
   Today `supersessions: 0` and both sides stay `live`. The corpus gives the extractor exactly the
   evidence it needs (doc-05 says the certificate is invalid, doc-01's decision cites it). A
   `NAND`/`supersede` between the two decisions — or at minimum a `refutes` edge — is the single
   change that turns this graph from a pile of sentences into an operating model. *Evidence: G1, G3;
   §5 relation table; `expand` on both decision points.*
2. **Add an output section for ownership.** `role → object performs/owns` is in the brief's required
   list and in `ONTOLOGY.md`'s predicate set (`ownedBy`, `managedBy`, `performs`), and the extractor
   reads the sentence correctly. It has nowhere to put it. *Evidence: G2; element 14.*
3. **Rank extracted claims above raw episodic turns, or separate the two lists.** `tortoise_search`
   returning `[user] And the auditor?` beside a verified claim is a precision failure a caller cannot
   correct without knowing the `[user]`/`[assistant]` convention. *Evidence: G4; the constraint and
   naming probes.*
4. **Give the dense leg an offline fallback.** This run is FTS-only (`vector: null` everywhere)
   because `sentence_transformers`/`torch` cannot be installed on a box without PyPI. A
   self-hosted user without the `embeddings` extra gets exactly these rankings — and the `naming`
   probe's total miss is the shape of that failure. *Evidence: §4 closing note; `AGENTS.md`'s
   "keyword-only product" warning.*
5. **Make `contested` discriminate.** It is `true` on every statement in the graph, including
   facts nothing contests ("The year's target is 40 restaurant accounts"). A flag that is always on
   carries no signal, and it is exactly the flag a consumer would use to find conflicts when G1 is
   fixed. *Evidence: element 18; every `recall` row.*
6. **Report cost, or report that cost is unavailable, per call.** `cost_usd: 0.0` beside
   `calls_without_cost: 19` reads as "free" rather than "unknown". *Evidence: G10; §2.*
7. **Close the door/validator gaps before the next validation run.** Enumerate the Object/Subject/
   Event layers over MCP (G5); list `tortoise_recall` in `tools/list` (G8); make
   `check_confidence(topic=…)` filter rather than return `[]` (G9). Each is a case where the product
   holds the data and its own tool cannot return it — which is how this validation had to reach for
   `graph_overview` + `recall` to answer what the brief asked the layer reads directly.
8. **Give v4 a document-corpus door.** Otherwise "validate the extractor on a corpus" is only
   expressible as N session captures, and no customer has a corpus in that shape. *Evidence: G7;
   §1 scoping note.*

---

## 9. Constraints of this run — stated so the numbers are not over-read

1. **Retrieval is FTS-only.** `sentence_transformers`/`torch` are absent from this box's venv, and
   `uv sync --extra embeddings` cannot complete: PyPI DNS resolution fails
   (`failed to lookup address information: Temporary failure in name resolution` for
   `files.pythonhosted.org`, retried over ~1 h across three attempts, while `api.github.com` and
   `api.deepseek.com` both resolve and respond). The worktree's own venv therefore cannot be built;
   the run uses the main checkout's venv with `PYTHONPATH` pinned at this worktree, which resolves
   `import tortoise` to the worktree's code (verified). **The code under test is this worktree at
   `origin/main = 51f444fd088`.**
2. **No LLM key is exported in the shell.** The product's v4 path reads `DEEPSEEK_API_KEY`
   (`sdk._session_llm_provider` → `_PROVIDERS`), so `run.sh` exports the box's existing deepseek
   credential from pi's auth store. One provider was exercised (`deepseek-direct`,
   `deepseek-v4-flash`); the openrouter/openai/gemini/venice routes were not.
3. **One corpus, one run, one model.** Five documents, one capture each, no re-run for
   idempotency (the product reports `dedup_hits`/`skipped` machinery for that; not exercised here).
4. **`tortoise_graph_overview` reported the container also holds `tortoise_test_matrix`** — a
   separate graph in the same FalkorDB instance, empty for this run. `xv14592_operating_model` is its
   own graph and the only one written.

---

## 10. Artifacts

| path | contents |
|---|---|
| `corpus/corpus.json` | the corpus (5 transcripts, ~1,160 tokens) |
| `run_ingest.py` / `run_readback.py` / `run_census.py` | the three phases, SDK-write + MCP-read only |
| `run.sh` | the one invocation, with the two measured deviations documented |
| `out/ingest-receipt.json` | per-document full product result envelope, warnings, `stats.llm` token table |
| `out/readback.json` | verbatim MCP replies for the 6 search probes, layer reads, entity reads, recall, confidence |
| `out/census.json`, `out/census2.json`, `out/census3.json` | object/subject/event full-scans, operator census, decision/reversal expansion, source list |
| `out/tool-surface.json` | the live MCP surface: 39 tools with each tool's own `inputSchema` |
| `out/events.jsonl` | the SDK event log for the run |

---

## 11. Mandate-preflight disclosure

The dispatch brief opened with a `[mandate-preflight]` block naming two files this lane was ordered
to read and **cannot**:

* `/home/cmux/.pi/agent/state/LANE-BRIEF-PREAMBLE.md` — MISSING
* `/home/cmux/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` — MISSING

Both are absent on this box and there is no provisioning path for them (the block names #1193 as
owning their content and #3377 as owning this report). **I did not read them and am not claiming to
have.** The brief's own text was treated as the authority, per the block's instruction. The
consequence is concrete: `tortoise`'s `AGENTS.md` skill table orders a lane to read
`TORTOISE-GRAPH-BRIEF.md` **before it derives**, and that instruction could not be obeyed here — the
research-first reads named in the brief (`sdk.py`, `tool_registry.py`, `extractor_v2.py` §4379/§4607,
`ONTOLOGY.md`, `packs/`) were done directly from the source instead.
