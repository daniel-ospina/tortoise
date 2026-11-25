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

Reproduce with (steps 3–5 read the graph step 1 wrote, so they must run in the SAME sitting — see
§10):

```bash
export TORTOISE_DB_URI='docker://:@127.0.0.1:16467/tortoise_test_matrix'   # this lane's container
export DEEPSEEK_API_KEY='…'                  # the extraction LLM credential (never read from a file)
# optional: TORTOISE_VENV=/path/to/a/built/venv   (default: <worktree>/.venv)

./docs/evidence/14592-operating-model-validation/run.sh \
    docs/evidence/14592-operating-model-validation/run_ingest.py            # 1. write
./docs/evidence/14592-operating-model-validation/run.sh \
    docs/evidence/14592-operating-model-validation/run_readback.py          # 2. read back
./docs/evidence/14592-operating-model-validation/run.sh \
    docs/evidence/14592-operating-model-validation/run_census.py            # 3. structural census
./docs/evidence/14592-operating-model-validation/run.sh \
    docs/evidence/14592-operating-model-validation/run_census2.py           # 4. claims / confidence / structure
./docs/evidence/14592-operating-model-validation/run.sh \
    docs/evidence/14592-operating-model-validation/run_census3.py           # 5. expansions / sources
```

`run.sh` takes both of the 2026-11-25 run's measured deviations from the **caller**
(`TORTOISE_VENV`, `DEEPSEEK_API_KEY`) and fails loudly when either is absent — it carries no absolute
path and reads no credential store (§9). Every artifact writer names itself and its sha256 in a
`provenance` block, so a reader can tell which code produced the numbers.

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

```text
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
| **Point** | 120 | 96 Points behind the `claim` door (62 `statement` + 34 `event` — the corpus's 34 turns ARE the 34 `event` Points) plus the 24 operator Points (`structure.operators: 24`, `120 - 96`) |

`tortoise_list_knowledge(kind="source")` returns verbatim:

```json
[{"url":"session:doc-02","sourceKind":"agentSession","points":13},
 {"url":"session:doc-01","sourceKind":"agentSession","points":13},
 {"url":"session:doc-05","sourceKind":"agentSession","points":12},
 {"url":"session:doc-04","sourceKind":"agentSession","points":11},
 {"url":"session:doc-03","sourceKind":"agentSession","points":8}]
```

**The layer reads the brief asked for do not all work** — `list_knowledge` rejects `object`/`subject`/
`event`, and whether another door enumerates them is **unmeasured** in this run: see gaps **G5**, **G6**.

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
delivery partner" — returns **8 incoming `IMPL` rows** (6 operators, 7 distinct contents; the
duplicate is called out below), each traceable to the corpus:

```json
{"mechanism":"IMPL","related_content":"ColdPath Logistics offers a 9pm cutoff that meets the morning restaurant slots","direction":"incoming"}
{"mechanism":"IMPL","related_content":"ColdPath Logistics holds a validated cold-chain certificate","direction":"incoming"}
{"mechanism":"IMPL","related_content":"NordicFreight's 6pm order cutoff cannot make our morning restaurant slots","direction":"incoming"}
{"mechanism":"IMPL","related_content":"Dev Patel ruled the capital spend for our own refrigerated van not approvable this quarter","direction":"incoming"}
{"mechanism":"IMPL","related_content":"The own-van option needs 48,000 pounds of capital spending, which is more than the current cash buffer allows","direction":"incoming"}
{"mechanism":"IMPL","related_content":"[MITIGATION] The cash buffer is 31,000 pounds and is already committed to the March supplier payments","direction":"incoming"}
{"mechanism":"IMPL","related_content":"Buying our own refrigerated van needs 48,000 pounds of capital spending","direction":"incoming"}
{"mechanism":"IMPL","related_content":"[MITIGATION] The cash buffer is 31,000 pounds and is already committed to the March supplier payments","direction":"incoming"}
```

Three options, all three reasons, and the affordability argument carries an extracted **MITIGATION**
node. This is the part of the answer that is genuinely good.

⚠️ **Rows 6 and 8 carry the same text.** The reply holds **8 relationship rows across 6
operators**: two of them (`1a21036fe8d…`, `1a21036ff42…`) are MITIGATES-bearing `IMPL` operators
returned once per endpoint — the claim (`pt_a9d9a2d58…` / `pt_a7c9c3cbc…`) and a **MITIGATION
operator** (`1a210370019…` / `1a21036ffa1…`), and those two mitigation operators carry identical
text, so `[MITIGATION] The cash buffer is 31,000 pounds…` appears twice. 8 rows, 6 distinct
`operator_id`s, 7 distinct `related_content`. The count here is the receipt's
(`out/census3.json` `.expand_doc01_decision.reply.result`), and the duplicate is itself a product
observation — the same duplication §4 flags on the goal probe — not a transcription error to tidy
away.

### NAND operators exist

Search returned NAND families over the "viable delivery partner" belief
(`"mechanism":"NAND"`, `family_size: 2`, both endpoints `contested: true`, conf 0.3294).

### Required relation classes — present / absent

| required class | present? | evidence |
|---|---|---|
| decision → option `IMPL` | ✅ | 8 incoming `IMPL` rows — 6 operators, 7 distinct contents (doc-01 decision, above) |
| option ↔ option `NAND` | ✅ | NAND operator families on the delivery-partner belief (`family_size: 2`) |
| goal → constraint | ✅ (as text + `IMPL`) | "The growth goal depends on the delivery partner keeping morning slots" → `IMPL` → "Every new restaurant account requires a morning delivery slot"; "Cold-chain rules forbid adding an account unless the cold chain can be held below 4 degrees" |
| source → claim `extractedFrom` | ✅ (structural) | 5 `agentSession` Sources, each with 8–13 Points; `relation_write.edges` shows `related`/`managedBy` written as `structural` |
| **role → object `performs` / `owns`** | ❌ **ABSENT** | The corpus fact is extracted as a *statement* ("Marta Reyes owns the cold-chain temperature log" — subject `Marta Reyes`) and **no `performs`/`owns` edge to an Object exists.** Ownership is prose, not structure. See **G2** |
| cross-document contradiction | ❌ **ABSENT** | See **G1** |

### Were any relations or entities invented?

**No.** Every edge endpoint inspected traces to a sentence in the corpus, and every Object/Subject
name is a surface form that appears in the corpus. The corpus contains no currency symbol and the
product correctly refused to type the amounts —

```text
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
| 1 | **Decision** "we choose ColdPath Logistics" | ✅ | ✅ | ✅ `sessionId: doc-01`, `quote` present | Point exists, 8 `IMPL` rows (6 operators) attached |
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

```text
"We choose ColdPath Logistics as our delivery partner"   status: live   conf 0.5
"We revert to NordicFreight as our delivery partner…"    status: live   conf 0.4622
"ColdPath Logistics holds a validated cold-chain certificate"  status: live  conf 0.5
```

`tortoise_expand_relationships` on **both** decision points returns only edges **inside their own
document**. Doc-01's decision has 8 incoming `IMPL` rows — 6 operators, 7 distinct contents (§5).
Doc-05's
revert has 4 incoming `IMPL` (all from doc-05). `tortoise_explore_connections(doc-01 decision, hops=3)`
returns **8 relationship rows** (the same 8 `operator_id`s the expand returned, duplicates included —
not 8 *nodes*), all from doc-01: it never reaches doc-05.

**The mechanism is NOT "the extractor never tried".** It tried, on the exact cross-document pair, and
the edge was lost at the S3 identity gate. From `out/ingest-receipt.json`, doc-05
(`documents[4].result`):

* `stats.held: 3`, `held_reasons: {"partial": 6}`, `stats.supersessions: 0` — those two hold counters
  are the finding, and the previous revision of this report named neither;
* six `operator endpoint HELD not minted (#10997 S3 HOLD)` warnings, the first naming **doc-01's
  claim** as the endpoint it could not resolve: *"NAND dst named 'ColdPath Logistics holds a validated
  cold-chain certificate' but the identity lookup was partial, so whether this endpoint already
  exists was never established — held with its edge, NOT persisted (no hold store was supplied)"*;
* the run-level summary: *"S3 HOLD (#10997): 3 candidate(s) held — identity UNRESOLVED (partial=6);
  run lookup was partial — not minted (I1) and not dropped (I2); NOT carried forward — no hold store
  was supplied"*;
* three `operator dropped: src/dst did not resolve to an emitted point/event` warnings, two of them
  the cross-document pair: `'The cold-chain audit failed ColdPath Logistics last week'` → **doc-01's
  certificate claim**, and the same source → doc-01's 9pm-cutoff reason;
* `S3 identity partial: the existing-item lookup did not run for 11 of 25 candidate(s)` (doc-05). Every
  document reports the same class of partial lookup: 6 of 21 (doc-01), 9 of 24 (doc-02), 2 of 17
  (doc-03), 13 of 27 (doc-04).

The NAND family that *did* persist is **intra-doc-05**, and it attacks doc-05's own belief point, not
doc-01's claim: doc-05's `relation_write.edges` holds two `NAND` rows with `stated_predicate:
"refutes"` and `write_path: "direct"`, both aimed at `We believed ColdPath Logistics was a viable
delivery partner…` (`pt_dffe3b5e72…`) — the point doc-05 itself asserts. So the graph does represent
"we believed X, then stopped" *inside* one document, and loses the cross-document edge that would make
the reversal a conflict between documents.

Two corrections to the previous revision's account of the cause:

* `search_degraded: false` grades the **search** leg, not the identity lookup. The identity counter is
  the `S3 identity partial` warning, and it says the lookup was partial for 6 of 21 candidates in
  doc-01 — the very document whose claim the refutation had to join.
* `supersessions: 0` is a **symptom** of two separate losses, not evidence that S3 produced nothing.
  The operators were constructed and then (a) HELD because the endpoint lookup was partial — and the
  hold was not persisted, because `hold_store` is `None` on the path that ships
  (`tortoise/extractor_v2.py:3614`, *"the path that ships (`hold_store=None`)"*; no caller in
  `tortoise/sdk.py`, `tortoise/mcp_server.py` or `tortoise/tool_registry.py` passes one), so there is
  nothing for a later run's `#10997` drain to pick up — and (b) dropped on the arm that requires both
  endpoints to resolve to an emitted point/event **within the same capture**, which a cross-document
  edge can never satisfy when the other document's point was never in that capture's payload.

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

### G5 — the layer reads PARTLY work: `list_knowledge` rejects three kinds, and the Object/Subject/Event enumeration is **unmeasured**

The brief's step 4 asks for "entity/object/subject/source reads". Over MCP:

* **Established, verbatim** (`out/readback.json` `.entities`): `tortoise_list_knowledge(kind="object")`
  → `{"error":"list_knowledge: unknown kind 'object'. Valid kinds: claim, source,
  source_reliability, batch, namespace, topic, diary"}` — same for `subject`, `event`, `point`. The
  door has no `object`/`subject`/`event` kind. It DOES have claims and sources:
  `list_knowledge(kind="source")` returns the 5 Sources, and `list_knowledge(kind="claim")` returns
  the whole claim population — `total: 96`, `hasMore: false`, 62 `statement` + 34 `event`
  (`out/census2.json` `.claims`, args `{"kind": "claim", "limit": 200}`).
* **Withdrawn — the probe could not have returned rows.** The previous revision reported
  `tortoise_search(entity_type="object")` → 0 rows as evidence of "no door". That call passed
  neither `query` nor `kind`; full-scan mode is `is_full_scan = (query is None and kind is not None)`
  (`tortoise/sdk.py:47018`), so the structural leg is never submitted and the retrieval legs have no
  query — the arm is empty for **every** `entity_type`, which is why the same call also returned 0
  rows for `source` (`out/census.json` `.fullscan_source`) while `list_knowledge` reads sources fine.
  A malformed probe, not a product gap. The tool's own description, quoted two lines away in the
  bulletin, says *"omit query, **set kind** → all Points of kind"* (`tortoise/tool_registry.py:517`).
* **Withdrawn — `tortoise_query(kind="operator")`.** `kind` on `tortoise_query` is a **pointKind**
  (`tortoise/tool_registry.py:303`: *"Query points by pointKind and/or property filters"*); the 24
  operators are `is_operator` Points, which that argument does not address, so its 0 rows says nothing
  about the operator population.
* **Not measured, and therefore not claimed:** whether `tortoise_search(entity_type="object",
  kind=<objectKind>)` — or `entity_type="operator"` — enumerates the 23 Objects / 24 operators. The
  code path exists (`kind_field` = `objectKind` / `op_type`), but no call in this run exercised it.
  **"No MCP door enumerates Object/Subject/Event" is withdrawn**: re-probe with `kind` named before
  the gap is filed (UNVERIFIED item 2).

The layers *were* readable through `tortoise_graph_overview` (counts) and `tortoise_recall` (content).
`check_structure` returned `{"result": []}` on a graph holding two mutually exclusive live decisions
— the structural validator sees no problem, which is consistent with G1.

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

### G9 — `check_confidence(topic=…)` returned `[]` for every probe — against a contract that says it is a belief-chain window

`tortoise_check_confidence(topic=<any of the six probes>)` returned `[]` for every probe, while the
same tool with `claim_id=""` returns rows carrying EP (`out/census2.json` `.conf_one`). The
observation is real.

**What the previous revision got wrong is the expected contract.** At `51f444fd088`,
`tortoise/sdk.py:31187` documents `topic=` as **"the dated belief chain for a topic, oldest window
first"** — a belief-chain-window read, not a filter over the population. A graph whose 120 Points
were all written inside one 11-minute capture (00:56–01:07 UTC) has no earlier window for any topic,
so `[]` is what that arm should return; it is **not evidence the arm is inert**, and "make it filter"
would change a documented contract. The work item is to *name* what a caller should get here before
any change is proposed.

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

1. **Carry the cross-document conflict the pipeline already builds — supply a hold store AND drop the
   same-capture resolution requirement.** The emitter is not the gap: doc-05's capture constructed the
   counter-claim `NAND` against doc-01's certificate claim and lost it at the S3 identity gate
   (`held: 3`, `held_reasons: {"partial": 6}`, six `#10997 S3 HOLD` warnings, plus two
   `operator dropped: src/dst did not resolve to an emitted point/event` warnings naming that pair).
   The designed path out already exists — `identity_hold.open_store` and the `#10997` drain mint
   exactly once on a later run whose lookup resolved — but nothing on the shipping path supplies a
   store (`hold_store=None`, `tortoise/extractor_v2.py:3614`), so the held edge is carried nowhere,
   and the drop arm requires the far side to be in *this* capture's payload, which a cross-document
   edge never is. `supersessions: 0` is the symptom; this stage is the cause. *Evidence: G1, G3; §5
   relation table; `expand` on both decision points.*
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
7. **Close the door gaps that are actually established before the next validation run.** (a) G5's
   established half: `list_knowledge` has no `object`/`subject`/`event` kind (verbatim in
   `out/readback.json`). ⚠️ Its "no door enumerates them" half is **withdrawn pending the re-probe in
   G5** — the first cut's `tortoise_search` probe passed neither `query` nor `kind`, so it never
   entered full-scan mode and returned `[]` for `source` too, which `list_knowledge` reads fine;
   re-probe with `kind` named, then restate the gap before filing it. (b) list `tortoise_recall` in
   `tools/list` (G8) — established, and it hides the one tool that returns Objects with EP and
   degree. (c) G9 is a **contract question, not a bug**: `check_confidence(topic=…)` is documented as
   "the dated belief chain for a topic, oldest window first" (`tortoise/sdk.py:31187`), so `[]` on a
   graph written inside one window is what that arm should return — name the expected behaviour before
   proposing any change, and do not "make it filter" without an owner decision on the contract.
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
   (`sdk._session_llm_provider` → `_PROVIDERS`), so the operator exported the box's existing deepseek
   credential from pi's auth store before invoking `run.sh`. One provider was exercised
   (`deepseek-direct`, `deepseek-v4-flash`); the openrouter/openai/gemini/venice routes were not.
   ⚠️ **The consequence, stated: the script does not reproduce on another box unless the caller
   supplies that key** — `run.sh` now REQUIRES it (`${DEEPSEEK_API_KEY:?…}`) rather than scraping a
   store, and requires `TORTOISE_VENV` or a built `<worktree>/.venv`. The committed receipts are the
   durable evidence; re-running needs both inputs.
3. **One corpus, one run, one model.** Five documents, one capture each, no re-run for
   idempotency (the product reports `dedup_hits`/`skipped` machinery for that; not exercised here).
4. **`tortoise_graph_overview` reported the container also holds `tortoise_test_matrix`** — a
   separate graph in the same FalkorDB instance, empty for this run. `xv14592_operating_model` is its
   own graph and the only one written.

---

## 10. Artifacts

| path | contents | producer |
|---|---|---|
| `corpus/corpus.json` | the corpus (5 transcripts, ~1,160 tokens) | authored for this run |
| `run_ingest.py` | step 1 — the five captures, SDK-write only | — |
| `run_readback.py` | step 2 — 6 search probes, layer/entity reads, recall, confidence, decision expansion (MCP-read only); carries `decision_probe_findings` | — |
| `run_census.py` | step 3 — object/subject/event full-scan attempts, operator census, decision/reversal expansion, recall state, confidence-by-topic | writes `out/census.json` |
| `run_census2.py` | step 4 — `list_knowledge(kind="claim")`, `check_confidence(claim_id="")`, `check_structure()` | writes `out/census2.json` |
| `run_census3.py` | step 5 — decision/reversal expansions (ids **discovered by content**, never pasted) + source list | writes `out/census3.json` |
| `run.sh` | the invocation; both inputs caller-supplied, nothing box-bound; the LLM credential is required **only** for `run_ingest.py` — the four read-only phases call no model | — |
| `stamp_provenance.py` | grades each receipt against its producer's own `WRITTEN_KEYS` and writes the `provenance` block; `--check` reds a missing or stale one | — |
| `out/ingest-receipt.json` | per-document full product result envelope, `warnings`, `stats.held`/`held_reasons`, `stats.llm` token table | `run_ingest.py` |
| `out/readback.json` | verbatim MCP replies for the 6 search probes, layer reads, entity reads, recall, confidence, decision expansion | `run_readback.py` |
| `out/census.json` | full-scan attempts per layer, operator-query census, decision/reversal expansion, recall:state, confidence-by-topic | `run_census.py` |
| `out/census2.json` | the claim-door population (`total: 96`), the unfiltered confidence read, `check_structure` | `run_census2.py` |
| `out/census3.json` | doc-01 decision expansion (8 rows / 6 operators / 7 distinct contents), the certificate claim's expansion, `explore_connections(hops=3)`, the source list | `run_census3.py` |
| `out/tool-surface.json` | the live MCP surface: 39 tools with each tool's own `inputSchema` | `run_readback.py` |
| `out/events.jsonl` (**not committed**) | the SDK event log — 391 KB of raw JSONL, **scratch**, excluded by this directory's `.gitignore`; reproducible with `run.sh` | `run_ingest.py` |

⚠️ **Two known defects in the committed receipts themselves, recorded rather than tidied away.**

* `out/readback.json`'s decision arm is a **FAILED read**: `decision_point: "doc-05_t5"` is an
  episodic turn id, and `expand.reply = {"result": []}` / `explore.reply = []` are recorded beside
  `ok: true`. The producer now refuses that shape (`pick_decision_point` + `decision_probe_findings`,
  driven in `tests/test_14592_operating_model_evidence.py`), and §5's relation evidence comes from
  `out/census3.json`, whose expansion DID run (8 rows).
* `out/census.json`'s decision arm did not run at all (`decision_point_id: null`) for the same reason
  in the first revision of `run_census.py`; the driver now searches with `limit=8`, picks by content
  and records the finding instead of a null.

⚠️ **Provenance, stated rather than implied — and MEASURED.** Every receipt in `out/` carries a
`provenance` block, and **all four say `produced_by_committed_producer: false`**. They were produced
by the 2026-11-25 run **before** their producers were committed, so they are not that code's output,
and they cannot be regenerated: the lane container is gone. `stamp_provenance.py` writes the block by
measuring, not asserting — it reads each producer's own `WRITTEN_KEYS` and records the keys a re-run
would write and this receipt lacks (`census3.json`: `graph`, `provenance`, `ok`, `resolved`) plus any
`point_id` passed as a call ARGUMENT that appears nowhere in the producer's source (`census3.json`:
the two `pt_…` ids the old ad-hoc run pasted in). It refuses to overwrite a producer-written block, and
`--check` reds a stale one — so a re-run that regenerates a receipt cannot leave a stamp behind
claiming it did not. The producers were written from the receipts' own recorded `tool`/`args` pairs —
that is what pins them to the same calls. Any re-run needs steps 1–5 in one sitting and OVERWRITES
these receipts.

⚠️ **`census.json` carried a key *named* `provenance` that was really a call receipt.**
`tortoise_check_confidence(include_provenance=True)` was stored under `provenance`, so the receipt
looked self-described while holding no provenance block at all. The receipt is kept under
`confidence_provenance_read` (the name the fixed producer writes) and the collision is recorded in the
block rather than deleted.

⚠️ **What the new `ok` verdicts guarantee, and what they do not.** Every phase now returns non-zero
and writes `ok: false` when its target did not resolve or its relation read came back empty, so the
*script* can no longer report success over a failed read. No exit code can stop an operator from
committing a receipt a failing run wrote; the residual is that a human `git add` after a non-zero exit
is still possible, which is why `ok` travels inside the receipt where the contradiction is visible.

### What this bundle does not establish

* **Object/Subject/Event enumeration.** Withdrawn (G5); the re-probe with `kind` named is owed.
* **A reproduction of these numbers.** The receipts are the evidence; a re-run needs a built venv, an
  LLM key and the container, so "reproducible" here means "the call set is committed and complete",
  never "run it anywhere and get 96".

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
