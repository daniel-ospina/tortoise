---
title: "A/B/C context-assembly pre-registration — verbatim source vs epistemic subgraph vs union"
type: data
domain: data
status: live
created: 2026-09-11
updated: 2026-09-11
ownedBy: epistemic-team
subjects:
  team: epistemic-team
doc_status: live
aboutSubjects: epistemic-team
aboutObjects: Point, Operator, Session
---

# A/B/C context-assembly pre-registration

**Status:** ⛔ **Pre-registration — written BEFORE data collection.** Metrics, arms,
falsification criteria and the analysis plan below are frozen. Any change after the first
run is a new experiment revision, recorded as a dated amendment, never a silent edit.
**Created:** 2026-09-11
**Amended:** 2026-09-11 — review remediation before any run (see Amendments, below). No data
has been collected, so this is repair of the pre-registration, not a post-hoc revision.
**Related:** #2976 (oracle ceiling = 81%) · #2978 (content-less slots) · #2683 (`[evidence-assembly]` Slice A) · #2820 (memory-substrate map) · #2578 (the measurement)
**Workflow:** `experiment-workflow` (9 stages) — this doc covers stages 1–5; stage 6 is the validation gate.

---

## 1. Why — the question this settles

Two designs are in direct conflict and both have evidence:

| Position | Source | Claim |
|---|---|---|
| **Serve more, structured** | Owner's subgraph thesis; Kumiho/Atlas research | The graph's reasoning state (claims + typed relations + confidence) is *leaner and higher quality* than raw source |
| **Serve less, verbatim** | `[evidence-assembly]` wave §4 (#2080/#2683) | "Adding more relevant evidence to a fixed window DEGRADES answers"; rank-then-admit ≈3–6 items |
| **Verbatim beats derived** | arXiv 2601.00821v3 (controlled ablation) | Verbatim source beats extracted artifacts by **15.9 pts** (LoCoMo) / **22.0 pts** (LongMemEval-S); **union matches chunks; artifacts alone forfeit the gap** |

Our own data sits between them: extracted fragments score **0/52**, raw gold sessions score
**42/52 (81%)** (#2976). The evidence-assembly wave's flooding result was measured **while 61% of
admitted slots were empty operator nodes** (#2978) — so its "less is more" doctrine rests on
contaminated data.

**This experiment is the first measurement that separates the three.** It is also the experiment
nobody has published: no controlled A/B at matched context budgets (whitespace words; §5) of a *reasoned* subgraph
(claims + typed IMPL/NAND + propagated credence + supersession) versus raw passages.

## 2. Hypotheses (frozen)

| # | Hypothesis | Directional prediction |
|---|---|---|
| **H1** | **Union beats subgraph-alone** — the epistemic subgraph needs verbatim source to be answerable | C > B, ≥15 pts |
| **H2** | **Verbatim beats subgraph-alone** — the 2601.00821 result, reproduced on our stack | A > B, ≥10 pts |
| **H3** | **Union matches or beats verbatim alone at matched context budgets** — the union thesis | C ≥ A − 5 pts |
| **H4** | **Subgraph-alone is sufficient** — would falsify the union requirement | B ≥ A − 5 pts |
| **H5** | **The renderer/selection path is the binding constraint, not absent knowledge** — subgraph arm fails for a fixable reason | B < A − 15 pts **and** answer-bearing-claim presence (metric 5) ≥ 80% (§7 F6) |

H4 and H5 are the **falsification hypotheses** — the outcomes that would kill or reshape the
thesis. They are stated here so a null result is a finding, not a disappointment.

**Every hypothesis is subject to the §6 decision floor.** A directional prediction realised at a
magnitude below 15 points is recorded as *inconclusive* and cannot force a product decision;
H3/H4's 5-point margins are directional expectations only, never decision thresholds.

## 3. Arms (frozen)

All arms answer the **same 52 answerable questions** with the **same frozen reader** and the
**same judge**. Only the context package differs.

| Arm | What the reader receives | Built from |
|---|---|---|
| **A — verbatim** | The gold sessions rendered verbatim (`render_context`), turn text intact | `answer_session_ids` (gold) |
| **B — subgraph** | The epistemic subgraph relevant to the question: claims, rendered **with typed relations** (`C1 IMPLIES C3`, `C4 CONTRADICTS C1`), propagated confidence, entity links (`C2 is about Rovo`), and dates. **No raw turn text.** | Graph traversal from matched points |
| **C — union** | Arm B's subgraph **plus** the verbatim source turns each subgraph claim derives from, linked in place (`C1 ⟵ session 12, turn 4`) | B + provenance edges |
| **D — no-context control** | The question only, plus the `Current Date:` header. No retrieved evidence. Establishes the **prior-knowledge floor** (§4, §11.7–.8). | Nothing (question + date) |

**Arm A is already measured** (#2976: 42/52) and must be **re-run in the same session** as B, C and D
so that reader/judge/environment are identical. The prior number is a reference, not the
comparison baseline: it is the frozen `A_ref` that sets §7 F3's magnitude (42/52 under the official
or variant-aware judge, 25/52 under strict containment — §4), while every accuracy comparison in
§6/§11 uses this run's re-measured arm A.

**Labeling rule (hard):** a condition that leaks the gold annotation into the context (e.g. the
`has_answer` flag rendered) invalidates that arm. The subgraph must be built from graph content
and the question only — never from `answer_session_ids`.

**Leakage test (hard stop-condition, frozen; detects *use*, not just printing).** The rule above
catches a gold field that is *rendered*; it does not catch a selector that *ranks* by a gold
field — ordering candidates by `has_answer` emits clean text and still leaks. Two tests are
therefore mandatory before the run, and their pass/fail is recorded in the run manifest:

1. **Gold-field perturbation run.** The **scratch namespace set is a full per-question copy of
   all 52 eval graphs** — one scratch namespace per question, each a copy of that question's eval
   namespace (same points, same properties) — so the perturbation hits exactly the point set the
   run would render; the eval namespaces themselves are never mutated. In the scratch namespaces
   set `has_answer=false` on every Point and apply one fixed permutation to `lme_session_index`
   (permutation seed recorded). Re-render arms B and C for all 52 questions and assert the output
   is **byte-identical** to the unperturbed render. This is well-defined because the B/C render
   path derives its dates from the point's stored `createdAt` property (the ingest
   `point_created_at` local — the session's `haystack_dates` date, or the undated sentinel) and,
   where present, the extracted claim's stored `validFrom`, derives the rendered session number
   from the point's stored `session_id` (its 1-based position in the question's frozen
   `haystack_session_ids` list — §3 serializer template), derives the rendered turn ordinal from
   the turn id carried in the point's own `id` or its `source_turn_id`, and **never reads
   `lme_session_index`**; a shuffled index must change nothing. Any byte difference fails. The
   **gold-evidence claim list is never written into any graph namespace**, so it is not part of
   the perturbation set; if a future revision ever materialises it in a graph, it is added to
   this perturbation in the same revision.
2. **Static reference assertion.** A static check (grep/AST) asserts that the seed, traversal,
   ranking, and B/C render code paths never reference `has_answer`, `answer_session_ids`,
   `lme_session_index`, **or the gold-evidence claim list** — neither its loader symbol nor its
   artifact path
   (`docs/experiments/artifacts/2026-09-11-abc-context-assembly/gold-evidence-claims.json`).
   Any reference fails. The claim list is the newest gold artifact and the one metric 5 and the
   §10 reviewer consume: a selector that seeds or filters from it emits clean text and would
   evade the perturbation test, which is why it is named here rather than left to the
   `answer_session_ids` clause.

Either failure is a **hard stop**: fix the leaking code path and re-run the test — never run an
arm that can rank on gold.

### Arm B/C construction (frozen)

These parameters are fixed now and may not be tuned after seeing results. They are drawn from
`docs/research/2026-09-11-subgraph-retrieval-research.md` (design implications 1–5).

**Seed policy (frozen).** The seed function is **`vector_search(question, limit=64)`** in
`tools/longmem_eval/retrieve.py` — vector-only retrieval over the question's eval graph. The
width is deliberate: the frozen tie-break below must see the **full tied set**, and only 8
candidates ever reach the runner, so a `limit` of exactly 8 would hide the tied candidates the
tie-break exists to order. If the embedder is unavailable (`vector_search` raises
`ModelEncodeFailedError` on a graph with zero embedding-bearing points), the frozen fallback is
**BM25-only seeding** (the FTS leg of `tortoise_fts_query`, same `k=64`). The run manifest
records which was used (`seed_fn: vector | bm25`, plus the embedder model id) and the **eight
selected seed `id`s in rank order**, so a boundary difference between two runs is detectable; a
run must not mix the two seed functions across arms. Seeds are selected from the
**question text only** — **never** from `answer_session_ids`, `has_answer`, or any other gold
field (the labeling rule above). Seed count is capped at **8** per question: rank by raw seed
score, **dedupe by point `id` keeping the highest score**, then take the top 8. **Seed-truncation
tie-break (frozen):** when raw seed scores tie at the 8-seed boundary, order **every candidate
the query returned that carries the tied boundary score** by **ascending point `id`**
(byte-wise) and take the first — never by a gold field, never at random.
If zero seeds match, arms B and C render the **empty-context sentinel** — the literal
context slot `[no context retrieved]`, with the reader scaffolding (question text + `Current
Date:` header) unchanged — and the question is recorded under its own per-question **`zero_seed`
selection-failure flag**, reported per question and as a per-arm rate. `zero_seed` is **not**
metric 5 and is never back-filled from gold: zero seeds is a retrieval/selection event, whereas
metric 5 is a property of the graph that can be 1 while the seed set is empty.

**Score normalization (frozen).** `vector_search` and the BM25 fallback return different score
scales, and `seed_similarity` enters the ranking formula unscaled. `seed_similarity` is therefore
**min–max normalized to [0,1] across the seed set of each question** — the **8 selected seeds**,
not the wider 64-candidate fetch pool:
`s_norm = (s − min(seeds)) / (max(seeds) − min(seeds))`; if all seed scores are equal, every
`s_norm = 1.0`. The normalized value is what multiplies into the ranking formula below.

**Traversal.** Expand **exactly 1 hop** from each seed by default. A **second hop is permitted
ONLY through an `aboutObject` hub** (seed → entity → sibling claim); never a general 2-hop walk.

**Edge priority (admission order).** `aboutObject` (identity) → supersession/validity →
`NAND` (contradiction) → `IMPL`. A contradicted or superseded claim that is **admitted** always
renders its explicit marker — contradictions are never left implicit (research implication 5).
Because contradiction visibility is a stated design requirement, supersession/validity and `NAND`
endpoints are **reserved ahead of the 12-candidate cap** and placed at the head of their anchor's
rendered block, so cap admission and trailing-line truncation cannot silently drop them. *Their
anchor* is the anchor claim they were reached from during its traversal — the block carrying that
anchor's `C1: <claim text>` line; the reserved relation line leads that block's relation lines,
immediately beneath the claim line and above the anchor's other relation lines, and is never
appended after them or left to a trailing line. A reserved
endpoint that is itself admitted as an anchor renders the mirrored relation line at the head of
its own block as well, and a `(source, relation, target)` line appears at most once per block.
Reserved claims are **not** exempt from the §5 word budget — that remains the only global bound; a question
whose reserved claims alone exceed the budget is flagged `reserved_overflow` in the run manifest
with the count of dropped reserved lines.

**Candidate cap + dedupe (operation order frozen: cap → dedupe).** At most **12 candidates per
anchor** (the research doc's 10–15 band; we pin 12), **counted after** the reserved
supersession/validity and `NAND` endpoints above are admitted — reserved claims do not consume a
cap slot, and their per-question count is reported. Dedupe then runs **once, across all anchors,
by point `id`, keeping the single occurrence with the highest score**
(`seed_similarity × edge_priority_weight × hop_decay`, hub-damped where applicable). **Score
decides; edge priority is only the tie-break** — when two occurrences of the same point carry an
identical score, the occurrence admitted via the higher-priority edge is kept (the admission
tie-break, lower point `id` first, is unchanged). A higher-priority edge never displaces a
higher-scored occurrence. Because dedupe runs **after** the per-anchor cap, a point deduped out
of one anchor's block does not free a cap slot in any other anchor's block. **There is no
separate global candidate cap** — every candidate from the 8 anchors is admissible (bounded by
8 × 12 = 96, plus the reserved set, which is fixed by the graph rather than tuned), and the **§5
word budget is the only global bound**.

**Hub damping (Mem0-style).** For hub-mediated (2-hop) candidates, score is divided by
`1 + ln(1 + degree)`, where **`ln` is the natural logarithm (base `e`; fixed here)**, and
**`degree` is the hub entity's whole-graph degree** in the eval namespace at render time (count of
all incident edges, not degree within the admitted subgraph) — a high-degree entity (e.g. "the
user") cannot dominate the block. Direct 1-hop seeds are undamped.

**Ranking.** `seed_similarity × edge_priority_weight × hop_decay`, with
`edge_priority_weight = {aboutObject: 1.0, supersession: 0.9, NAND: 0.8, IMPL: 0.7}` and
`hop_decay = {1 hop: 1.0, 2 hops: 0.5}`. **In-network candidates (connected to a seed) are
admitted before out-of-network ones** (PPR-like reachability, not unweighted BFS). Final
admission is greedy by score until the §5 budget binds. **Admission tie-break (frozen):** when
two candidate scores are equal, admit the **lower point `id`** (byte-wise ascending) first — the
same key as the seed-truncation tie-break, never a gold field, never random.

**Serializer template (exact rendered forms).** One line per relation — labeled, directed,
dated:

```text
C1 is about Rovo
C1 IMPLIES C3
C4 CONTRADICTS C1
C1 came from session 12 (2023-05-06), turn 4
```

**Rendered session number (frozen source field).** The number in `session 12` is **not**
`lme_session_index`. It is the point's **stable source-session ordinal**: the 1-based position of
the point's stored `session_id` (the ingest `haystack_session_ids[si]` string, written on every
Point at ingest and mirrored on its Session node as `lme_source_session_id`) in the question's
frozen `haystack_session_ids` list. That list is a static ingest artifact, so permuting the
`lme_session_index` graph property cannot change the rendered number (§3 leakage test). A point
whose `session_id` is absent or not present in the frozen list renders `session ?`.

**Rendered turn ordinal (frozen source field).** The number in `turn 4` is the point's **stable
source-turn ordinal**, 1-based like the session number. It is derived from the turn node id
`lme:<qid>:s<si>:t<ti>`, whose `t<ti>` suffix is **0-based** in the ingest id (turn Points carry
it in their own `id`; extracted claim Points carry it in the stored `source_turn_id` property,
written at ingest as the resolved turn node id, `null` when the payload cited no in-range turn).
The rendered ordinal is **`ti + 1`**, so a session's first turn renders as `turn 1`. A point whose
turn id does not resolve to a `…:t<ti>` suffix renders `turn ?`.

**Rendered date (frozen source field).** The date in `(2023-05-06)` is the point's stored
`createdAt` property — the value the ingest wrote from its `point_created_at` local (the session's
`haystack_dates[si]` date, or the explicit undated sentinel for a session with no date). An
extracted claim may additionally store `validFrom` (written from the payload `when` slot): when
`validFrom` is present it is the rendered date, otherwise `createdAt` is. The undated sentinel, a
missing `createdAt`, or an unparseable value renders `(date unknown)`.

**Rendered confidence (frozen format).** `confidence:` carries the anchor claim's propagated
credence, formatted as **exactly two decimal places with half-even rounding** — Python's
`f"{c:.2f}"` — including trailing zeros (`0.80`, never `0.8`; `1.00`, never `1`).

An anchor renders its claim text on a `C1: <claim text>` line immediately before its relation
lines. A claim whose date is unknown renders `C1 came from session 12 (date unknown), turn 4`.
A superseded claim additionally renders `C1 [SUPERSEDED BY C2]`.

**Arm C provenance form.** Arm C appends the verbatim source turn **immediately beneath the
provenance line it comes from**, and therefore before the block's `confidence:` line — the
provenance turns are never moved below `confidence:`:

```text
C1 came from session 12 (2023-05-06), turn 4
  > user: I'm flying to Lisbon on the 6th of May for the offsite.
  > assistant: Got it — Lisbon, May 6.
confidence: 0.82
```

**Worked example (one complete block).** A single anchor renders, in this fixed order: its claim
line; its reserved supersession/validity and `NAND` relation lines immediately beneath the claim
line, at the head of the block's relation lines; its
remaining relation lines in admission-priority order (`aboutObject` → supersession/validity →
`NAND` → `IMPL`); its provenance line; then (arm C only) the verbatim source turns, each
immediately beneath the provenance line it comes from; and `confidence:` as the block's last
line.

*Arm B* — claim + typed relations + confidence, **no raw turn text**:

```text
C1: The user is flying to Lisbon on 6 May 2023 for the offsite.
C4 CONTRADICTS C1
C1 is about Rovo
C1 IMPLIES C3
C1 came from session 12 (2023-05-06), turn 4
confidence: 0.82
```

*Arm C* — the same block, with the verbatim source turn appended beneath the provenance line it
originates from:

```text
C1: The user is flying to Lisbon on 6 May 2023 for the offsite.
C4 CONTRADICTS C1
C1 is about Rovo
C1 IMPLIES C3
C1 came from session 12 (2023-05-06), turn 4
  > user: I'm flying to Lisbon on the 6th of May for the offsite.
  > assistant: Got it — Lisbon, May 6.
confidence: 0.82
```

Every anchor in the block follows this form; `confidence:` carries the anchor claim's propagated
credence. **Scope of the example (frozen):** the worked block validates the **render format
only** — it shows no seed score, edge weight, hop decay, hub damping, or admission cap, and
therefore validates **no** part of the ranking or admission rule.

**Frozen artifact.** The serializer module path and its **git sha** must be recorded in the run
manifest. Changing the serializer (template, weights, caps, damping) after the first run requires
a **dated amendment** to this document, and the run is re-labelled with the new sha. No silent
re-render.

## 4. Metrics (frozen)

**Primary:** *answerable-correct* — count of correct answers among the 52 non-abstention
questions, judged by the frozen judge. **Floor reference (arm D):** every primary conclusion is
stated *relative to D* (A−D, B−D, C−D); a bare accuracy count is not interpretable without the
no-context floor (§11.7).

**The gold-evidence claim list (frozen — derived once, here; §10 only consumes it).** The list is
the source-derived claim set metric 5 matches against and the §10 reviewer is handed. Its
derivation, granularity and storage are part of the pre-registration:

- **Source turns.** For each question `q`: every turn in `q`'s gold sessions
  (`answer_session_ids`) that carries the dataset's turn-level gold mark `has_answer: true`, plus
  `q`'s gold `answer` string. Nothing else — no extracted claim, no graph Point, no turn from a
  session outside the gold set — enters the list.
- **Granularity.** One claim `g` = one sentence-level span of a source turn: split the turn's
  `content` on `.`/`!`/`?`/newline, strip the role prefix, trim whitespace, drop empty spans. The
  gold `answer` string enters as one further claim.
- **Tokens.** `tokens(g)` is the single frozen tokenizer used by **both** the `MIN_GOLD_TOKENS`
  test and the metric-5 ratio: `g` lowercased → **every ASCII punctuation character deleted** (not
  replaced by a space, so `don't` → `dont` and `12,000` → `12000`, one token) → **the result split
  on whitespace** → empty strings dropped → frozen stopwords removed (the same list the metric-5
  rule below uses, appended to the run manifest before the first render). The split is on
  whitespace only, never on word boundaries, so `|tokens(g)|` is deterministic across
  implementations.
- **Minimum token count.** `MIN_GOLD_TOKENS = 3`. A span with `|tokens(g)| < 3` is written to the
  artifact with `"trivial": true` and is **never matched** — `match(p, g) = 0` by definition, so
  it cannot register an answer-bearing Point. This closes the 1–2-content-token case, where the
  ratio `|tokens(g) ∩ tokens(p)| / |tokens(g)|` takes only the values 0, 0.5 or 1.0, so the ≥0.80
  threshold degenerates into an exact-subset test: a 1-token claim is matched by any point
  containing that token (1.00), and a 2-token claim by any point containing both (1.00) — so a
  claim absent from the graph can register as present.
- **`|tokens(g)| = 0`.** The all-stopword / all-punctuation span is exactly the
  `|tokens(g)| < 3` case: flagged `"trivial": true`, never matched, and no division is performed
  (the ratio is defined as 0 rather than computed).
- **Non-emptiness (build prerequisite).** The artifact **does not exist yet** — it is a pre-run
  deliverable, not part of this frozen document. It must be built and validated **before the
  first render**; the pre-registration is already frozen without it, so it can no longer be
  built "before the freeze" and carries its own commit and sha256 (§9.5). Every question must
  contribute **≥1 non-trivial claim**. A question that cannot is
  a construction failure fixed before the run — never a reason to change the 52-question
  denominator of metric 5 or §9.4.
- **Storage.** One JSON artifact, **not yet created** —
  `docs/experiments/artifacts/2026-09-11-abc-context-assembly/gold-evidence-claims.json` (an
  object keyed by `qid`; each value a list of
  `{"claim": str, "source_turn_id": "lme:<qid>:s<si>:t<ti>" | "gold_answer", "tokens": int, "trivial": bool}`)
  — committed **before the first render**, as its own commit (the pre-registration is already
  frozen, so it cannot be committed in the freeze commit), with its **sha256 recorded in
  the run manifest** alongside the frozen seed, stopword and prompt artifacts. Until it exists
  and is validated, the run may not start (§9.5). It is materialised
  **outside the eval graph**: no namespace, eval or scratch, ever holds it, so the seed,
  traversal, ranking and B/C render paths have no access to it, and the §3 static-reference
  assertion names it explicitly.

**Secondary (all pre-registered):**
1. Reader refusal rate per arm (judge-independent signal).
2. Context **words** per arm — mean, median, IQR, max, distinct-value count (the measured unit is
   whitespace words, §5; the ~1.3× word→token conversion is noted only against external figures).
3. **Word-normalized accuracy: correct per 1,000 context words** — part of the primary
   interpretation (reported in §11.6), not an afterthought.
4. Per-class accuracy: interval (n=19), ordering/compare (n=32), and **`current-state` (n=1,
   reported for completeness, excluded from per-class inference)** = 52.
5. **Answer-bearing-claim presence (content-bearing; matching rule frozen).** Per question, a
   boolean: **1 iff at least one Point in the eval graph is *answer-bearing***, where Point `p` is
   answer-bearing for question `q` iff at least one claim `g` in `q`'s pre-registered gold-evidence
   claim list (**defined immediately above**) is **matched** by `p` under the frozen content rule:
   the frozen `tokens()` function defined under "The gold-evidence claim list" above (lowercase →
   delete ASCII punctuation → split on whitespace → drop the frozen stopword list, appended to the
   run manifest before the first render) is applied to both texts, and the resulting content-token
   sets are compared; `g` is matched by `p`
   iff `|tokens(g) ∩ tokens(p)| / |tokens(g)| ≥ 0.80`, and a claim flagged `trivial: true` in the
   list is never matched. The rate = the fraction of the 52 questions with
   ≥1 answer-bearing Point. **This is the metric F6/H5 use** — because it is content-bearing, it
   is 0 when the graph holds only unrelated points that merely share a gold session, which is
   exactly the §8.4 confound. It is a measurement input only: the list lives in the artifact
   defined above (committed before the first render, §4 Storage; §9.5) and is read **only** by
   this metric's scorer and the §10 reviewer — the
   seed/traversal/ranking/render code paths never open it, and it is never written into any graph
   namespace (§3 leakage test). It separates "the answer-bearing claim
   was never extracted" from "it was extracted but not retrieved". Reported per question.
6. Subgraph size: claims admitted, relations rendered, mean claims/question.
7. **No-context floor (arm D)** — answerable-correct for the question-only arm.
8. **Typed-relation presence (new; the §9.4 gate's second clause).** Per question, a boolean:
   **1 iff at least one typed relation (`IMPL`, `NAND`, or a supersession edge) has at least one
   endpoint that is an answer-bearing Point (metric 5) for that question.** The rate = the
   fraction of the 52 questions. Metric 5 asks only whether an answer-bearing claim exists;
   metric 8 asks whether the *relational* structure the thesis depends on exists. The **§9.4
   viability gate** is the per-question **conjunction (metric 5 ∧ metric 8) ≥ 80%** — it is *not*
   the same criterion as F6's metric-5-only 80% threshold.
9. **Gold-provenance presence (separate from metric 5).** Per question, a boolean: **1 iff at
   least one Point in the eval graph is *gold-provenant***, where a Point is gold-provenant iff
   its stored provenance resolves to a session id listed in that question's `answer_session_ids`.
   The rate = the fraction of the 52 questions with ≥1 gold-provenant Point. This is a
   **provenance-resolution** measure only: it is content-free by construction (an unrelated point
   from a gold session satisfies it), and it is therefore **never** used for F6/H5 or the §9.4
   gate. It is reported alongside metric 5 so that a missing answer-bearing claim (metric 5 = 0)
   is distinguishable from mere provenance presence (metric 9 = 1). Reported per question.

**Judge note (frozen):** the official `gpt-4o-2024-08-06` judge routes through the OpenRouter key,
which is currently exhausted (`limit_remaining=0`). If still exhausted at run time, use the
**deterministic containment** judge (#2976) applied **identically to all arms and both runs**, and
label every number as judge-substituted. **Strict containment is the primary deterministic
metric; variant-aware is the sensitivity analysis** — strict containment is a single frozen rule
with no variant-list construction choices, while gold-variant enumeration is itself a judgment
call. The treatment-dependence is acknowledged honestly: both judges are lexical and reward
reproducing the gold string, and arms A/C are handed the verbatim text while B is handed claims —
so **arm B's score is a conservative lower bound**, and any B advantage is robust to the judge
choice. Report strict and variant-aware side by side (the multi-variant gold format makes strict
an undercount: 25/52 vs 42/52 for arm A). Every question where B loses to A or C is subject to
the blind adjudication rule in §10 before the loss is attributed.

## 5. Controls (frozen)

| Control | Requirement |
|---|---|
| **Reader constancy** | One pinned reader for all arms and both runs; `assert_reader_constancy` must not abort |
| **Prompt constancy** | The reader **scaffolding** — system + instruction text, the question, and the `Current Date:` header — is **one pre-registered prompt template used byte-identically for all four arms**; only the `{context}` slot differs, and it holds that arm's rendered context (which necessarily differs: verbatim session blocks for A, typed relation lines for B, B plus its provenance turns for C, and **empty** for D — question + date only). The exact prompt file path and its **sha256 are recorded in the run manifest before the first reader call**. **Per-arm prompt adaptation is forbidden** — the scaffolding is not part of the treatment, and no run-time choice may change it. (The frozen prompt was authored for session-block rendering; arm B's typed relation lines go in through the same `{context}` slot, deliberately.) A future run that needs per-arm scaffolding is a new experiment revision under a dated amendment (see Amendments), never a run-time adaptation. |
| **Judge constancy** | One judge implementation for all arms (strict primary / variant-aware sensitivity, §4) |
| **Matched context budget** | All arms capped at the **same** `max_context_tokens = 8000`, **enforced in whitespace words** (`int(len(text.split()) * 1.1)`; `DEFAULT_CONTEXT_TOKEN_CAP = 8000` in `tortoise/retrieval.py`). The unit is **words** throughout this doc; the ~1.3× word→token conversion is noted only when comparing to external figures. Report per-arm word **distributions** (mean/median/IQR/max), not just means. **Pre-registered match rule:** if the arms' mean word counts differ by **>20%**, also report a budget-matched sub-analysis that truncates **all three evidence arms (A, B, C)** to one **common word cap = min(max_words(A), max_words(B), max_words(C))**, where `max_words(X)` is arm X's maximum per-question rendered word count, so no arm is ever handed more material than any other. Truncation drops whole trailing lines/blocks — never cuts mid-line. Truncating only arm A is invalid: arm C is arm B's content **plus** provenance turns, so C ≥ B by construction, and a one-sided A-truncation leaves C *more* volume-advantaged — the opposite of equalisation. Arm A's gold sessions average ≈5,068 words (#2976) while B is claims-only, so the common cap is short and the match cannot hold for every question. The matched sub-analysis validates the **C-vs-A accuracy comparison** (the union thesis); C-vs-B and B-vs-A are reported on the same equalised budgets as secondary reads. A failed match downgrades the token-efficiency claim (§4 metric 3), never the accuracy comparisons. |
| **Environment constancy** | Fresh graph namespace per question; same machine, sequential (never two memory-heavy evals at once) |
| **Independence** | No arm's context may be constructed using the gold annotation |
| **Frozen question set** | The #2578 55-Q subset (52 answerable) — not re-selected after results |

Metric 3 (correct per 1,000 context words) is part of the primary interpretation (§4, §11.6):
arms are compared both at matched budgets and at equal accuracy-per-word.

## 6. Power analysis (honest)

n = **52** answerable questions, **paired** (same questions across arms) → the correct test is
**McNemar** on discordant pairs, not a two-proportion test. Power therefore depends on the
**discordance rate** `d` (the fraction of questions where exactly one of the two arms is
correct), **not** on n alone — six criteria, five hypotheses and two class tests cannot be read
off n=52.

**Worked calculation (exact two-sided McNemar, n=52, α=0.05, 80% power).** For a paired accuracy
difference Δ, the discordant count is `D ≈ 52·d` and the asymmetry among discordant pairs is
`p = 0.5 + Δ/(2d)`. Solving for the smallest Δ that reaches 80% power:

| Assumed discordance rate `d` | Minimum detectable effect (80% power) |
|---|---|
| 0.20 | **17 pts** |
| 0.30 | **22 pts** |
| 0.40 | **25 pts** |
| 0.50 | **28 pts** |

`d` is **unknown before the run** — B's disagreement pattern with A has never been measured. The
old claim "≥25 pts adequately powered" is therefore **derived, not observed**: it holds only if
`d ≥ 0.40`. At `d = 0.30` the 80%-power MDE is ~22 pts and a true 15-pt effect has ~43% power; a
sub-15-pt effect is not detectable at any plausible `d`. (These figures are computed from the
exact binomial sign test, conditional on D; they are approximations to McNemar but the right
order of magnitude, and the honest summary is: **this run is an effect-size probe, not a powered
test for small effects.**) Report the observed discordance pairs (b, c, b+c) with every result so
the realised MDE can be computed after the fact.

**Pre-registered commitment (the decision floor).** A difference **smaller than 15 points** will
**not** be claimed as a result in either direction, and **no criterion in §7 triggered at a
magnitude below 15 points forces a §14 product decision** — it is recorded as *"triggered but
INCONCLUSIVE"*. This floor **governs over every other section**, including §7 and §11.3: a CI that
excludes zero at a magnitude <15 pts is still INCONCLUSIVE (§11.3), and §7 is subordinate to this
section.

**Multiplicity.** The three primary paired comparisons (B vs A, C vs A, C vs B) form one
confirmatory family, tested with **Holm–Bonferroni at family α = 0.05**. The reader-refusal
signal, the per-class tests, metric 3 and every other secondary metric are **descriptive** (CIs
reported, no confirmatory claim, no multiplicity adjustment). §7 criteria are evaluated by the
fixed precedence in §7, never by p-value shopping.

## 7. Falsification criteria (frozen — mandate per `experiment-workflow`)

**These criteria are subordinate to §6.** Any criterion triggered at a magnitude below 15 points
is recorded as **"triggered but INCONCLUSIVE"** and does **not** force a §14 product decision.
Only thresholds at ≥15 points can force a decision. Concretely, the decision-bearing forms are:
F3 (all three evidence arms ≤ 10/52, measured against arm A's **pre-registered reference count**
`A_ref` — so its magnitude is `A_ref − 10`, **32 points** official / **15 points** strict; see the
F3 row and paragraph), F5 (a 15-point separation), F1 at C ≤ A−15, and F4 at C ≥ A+15. F2's
5-point non-inferiority margin is a directional observation only, and **F6 is a diagnostic, never
decision-bearing**.

**Precedence (evaluate top-down for the *forced product decision*): F3 → F1 → F4 → F5 → F2.**
The first trigger wins and no lower-precedence criterion overrides it. **F6 (the renderer/selection
diagnostic) and metrics 5, 8 and 9 are not decisions:** they are **always computed and reported**,
even when a higher-precedence criterion fires — only the *product decision* is ranked. This order
is fixed now so the conclusion is never chosen post hoc.

| # | Condition | Magnitude | Conclusion forced |
|---|---|---|---|
| **F1** | C ≤ A − 15 pts | 15 pts | **Union thesis rejected.** Verbatim alone is better; the subgraph layer does not earn its words. |
| **F2** | B ≥ A − 5 pts **AND** C ≤ B + 5 pts **AND** C ≤ A + 5 pts | 5 pts | **Subgraph-alone sufficient** — only if the union adds nothing over B. Union is unnecessary complexity; simplify to B. (Below the §6 floor → INCONCLUSIVE in every case; F2 cannot force a decision.) |
| **F3** | All **evidence arms** (A, B and C) ≤ 10/52 — arm D excluded | `A_ref − 10` = **32 pts** official / **15 pts** strict | **Void for the assembly question.** The failure is upstream (retrieval/extraction) — the experiment says nothing about assembly. |
| **F4** | C ≥ A + 15 pts | 15 pts | **Union supported.** Proceed to scale on the 133-question census. |
| **F5** | C > B by ≥15 pts **and** C ≥ A | 15 pts | **H1 supported** — the union is the product shape. |
| **F6** | B < A − 15 pts **AND** answer-bearing-claim presence (metric 5) ≥ 80% | 15 pts | The graph contains the answer-bearing claims but B cannot use them → the defect is **query→subgraph SELECTION**, not extraction. **Diagnostic only — always computed and reported, licenses no product decision.** Each B-loss question's own metric-5 value is reported; a B-loss on a metric-5-negative question is attributed to extraction, never to selection. |

F2 is **mutually exclusive with F4 and F5** (but not with F1 or F3): its `C ≤ A + 5` clause cannot
co-fire with F4's `C ≥ A + 15`, and its `C ≤ B + 5` clause cannot co-fire with F5's `C > B` by
≥15 pts. **F1 and F2 CAN co-fire** — e.g. A=40, B=35, C=25 fires F1 (25 ≤ 40−15) *and* F2
(35 ≥ 35; 25 ≤ 40; 25 ≤ 45); the earlier "F1-only" argument assumed `C ≥ B − 5`, which is not
guaranteed. **F3 and F2 can also co-fire** (all three evidence arms collapsed and within 5 pts).
Co-firing is resolved by the fixed precedence order: **F1 outranks F2**. F6 is **no
longer gated on F3**, so the answer-bearing-claim diagnostic — the metric that separates a
*graph/extraction* defect from a *selection* defect in the most likely outcome (B loses) — is
always reachable.

**F3's magnitude is relative to arm A's pre-registered reference count, and both readings sit at
or above the §6 floor.** `A_ref` is **arm A's frozen reference count from §4** — the fixed
yardstick the collapsed arms are measured against, **not** this run's re-measured arm-A count
(§3: the prior number is a reference, not the comparison baseline). Under the official judge (and
the variant-aware deterministic reading) `A_ref = 42/52`; under strict containment — the primary
when the deterministic judge is substituted — `A_ref = 25/52` (§4). F3's magnitude is therefore
`A_ref − 10`: **32 points** official/variant-aware, or **15 points** strict containment. The
condition's own `A ≤ 10/52` clause states how low this run's **evidence arms** must fall; it never
enters the magnitude, so the collapsing arm is this run's arm A (≤10/52) while `A_ref` stays fixed
at 42/52 or 25/52. Both readings are ≥15 points, so F3 is decision-bearing whenever its condition
fires; the reading not primary for the run is §4's sensitivity analysis for F3's magnitude.

**F3's arm set is A, B and C only** — arm D is the no-context floor reference, not an evidence
arm, so a prior-knowledge floor above 10/52 cannot block void-detection: D = 11–14/52 removes no
F3 trigger, it only leaves the prior-knowledge discount switched off. When the evidence arms
collapse *and* arm D scores **≥15/52 (28.8%)**, §11.8's prior-knowledge discount applies to every
number — that trigger is defined in §11.8 and nowhere else; for D < 15/52 the discount does not
apply (F3 may still fire on its own terms).

## 8. Confounds & pre-mortem

**What could make this experiment lie — written before running it:**

1. **A weak renderer makes B lose for fixable reasons.** The subgraph serializer is new code; a
   poor format would reject the thesis wrongly. → *Mitigation:* the stage-6 validation gate
   includes a **blind, numeric** renderer check on up to 10 questions (§10). If the render is
   malformed, **fix it and restart** — never scale a broken design (the E013 anti-pattern).
2. **Budget creep.** Union naturally has more material; if it is not capped it "wins" by
   volume. → *Mitigation:* matched word cap + report actual per-arm word distributions (§5).
3. **Judge strictness masquerading as arm quality.** The strict-containment judge already
   under-counted once (25 vs 42). → *Mitigation:* hand-inspect every disagreement between arms
   on the same question; report strict + variant-aware side by side.
4. **Extraction loss read as assembly failure.** If the wedding claim was never extracted, B
   cannot win regardless of presentation. → *Mitigation:* metric 5 (**answer-bearing-claim**
   presence — a content match against the question's gold-evidence claims, not mere gold-session
   provenance) is measured per question and reported alongside. Metric 9 (gold-provenance
   presence) is reported separately and is never used for this mitigation.
5. **Prior-knowledge contamination.** LongMemEval questions can sometimes be answered without
   evidence (the benchmark's "Best Guess" baseline scores 18.8%). → *Mitigation:* a **no-context
   control arm** (arm D, question only) on the same 52 questions, reported as a full arm (§3). If
   the floor is high, all arms are inflated and the result must be discounted accordingly
   (§11.7–.8); every primary conclusion is stated relative to D.
6. **Prerequisite contamination (#2978).** Running before PR #3000 lands means every arm's
   window is ~61% empty slots. → **Hard prerequisite, see §9.**

## 9. Prerequisites (hard gates)

1. **#2978 must be fixed first** — PR **#3000** (`fix/2978-empty-slots`, open) skips content-less
   hits in `assemble_context`. Until it lands, all arms are measured through a window where
   ~61% of slots are empty operator nodes. **Do not run this experiment before it merges.**
2. Decide the OpenRouter budget question (official judge) or accept the labelled deterministic
   judge.
3. The subgraph serializer (arm B) and the union packer (arm C) must exist and pass the
   validation gate.
4. **Corpus-wide arm-B viability (the *viability gate*).** Before the run, measure metric 5
   (**answer-bearing-claim presence**) and metric 8 (typed-relation presence) for **all 52**
   questions and report claims and typed relations per question. **Gate (frozen):** the run
   proceeds only if **≥80% (42/52)** of questions satisfy **metric 5 ∧ metric 8** — an
   answer-bearing claim *and* a typed relation with an endpoint in that claim set, using the
   matching rules frozen in §4. This per-question conjunction is the viability gate; it is **not**
   the same criterion as F6's `metric 5 ≥ 80%` (answer-bearing-claim presence alone). If the gate
   fails, the run is **not** an assembly experiment: extraction is fixed first, and until then the
   run may be reported only as an **extraction measurement**, labelled as such — there is no
   run-time choice to proceed anyway. This prevents measuring an under-extracted graph while
   reporting on "the assembly thesis".
5. **The gold-evidence claim artifact (§4) must exist before the first render.** It is **not in
   the repo yet** (`docs/experiments/artifacts/` does not exist): it must be built, validated
   (§4 Non-emptiness), committed as its **own** commit — the pre-registration is already frozen,
   so there is no freeze commit left to join — and its **sha256 recorded in the run manifest**
   before any arm is rendered or scored. Metric 5 and the §10 reviewer are undefined until it
   exists, so this is a hard gate, not a formality.

## 10. Validation gate — stage 6 (1-question hard gate + leak test + blind H5 check, before scaling)

Run `gpt4_4929293a` (known oracle-correct, wedding question) through **all four** arms
(A/B/C/no-context). **STOP and fix if any of:**
- B or C produces an empty or content-less context **other than the frozen zero-seed sentinel** —
  a question that renders the literal `[no context retrieved]` sentinel is recorded under its
  `zero_seed` flag (§3) and does **not** by itself trigger this STOP,
- C's **word count** is not ≥ B's (union must contain B; words, not tokens),
- B's rendered context contains no typed relation line (`IMPLIES`/`CONTRADICTS`),
- C's rendered context contains no provenance link back to a source turn,
- any arm's context contains a leaked gold artifact (`has_answer`, `answer_session_ids`).

**Gold-field leakage test (hard stop-condition — detects *use*, not just printing; both parts must
pass on all 52 questions before the run).**
1. *Perturbation run.* The **scratch namespace set is a full per-question copy of all 52 eval
   graphs** — one scratch namespace per question, each a copy of that question's eval namespace
   (same points, same properties) — so the perturbation hits exactly the point set the run would
   render; the eval namespaces themselves are never mutated. In the scratch namespaces, set
   `has_answer=false` on every Point and apply a fixed permutation to `lme_session_index`
   (permutation seed recorded in the manifest). Re-render arms B and C and assert the output is
   **byte-identical** to the unperturbed render. This is well-defined because the B/C render path
   derives dates from the point's stored `createdAt` (the ingest `point_created_at` local — the
   session's `haystack_dates` date, or the undated sentinel) and, where present, the extracted
   claim's `validFrom`, derives the rendered session number from the point's stored
   `session_id` (its 1-based position in the question's frozen `haystack_session_ids` list — §3
   serializer template), derives the rendered turn ordinal from the turn id in the point's own
   `id` or its `source_turn_id`, and **never reads `lme_session_index`**; a shuffled index must
   change nothing. Any byte difference fails. The gold-evidence claim list is never written into
   any graph namespace and is therefore not part of the perturbation set.
2. *Static reference assertion.* A static check (grep/AST) asserts the seed, traversal, ranking and
   B/C render code paths never reference `has_answer`, `answer_session_ids`, `lme_session_index`,
   or the gold-evidence claim list — neither its loader symbol nor its artifact path
   (`docs/experiments/artifacts/2026-09-11-abc-context-assembly/gold-evidence-claims.json`).
   Any reference fails.

Record both pass/fail in the run manifest. Either failure is a hard stop: fix the leaking code path
before running — never run an arm that can rank on gold.

**H5 renderer check (numeric and blind — replaces "reads as knowledge").** Hand-inspection of B's
context alone is unblinded and subjective, so: (i) freeze the rule **before** the run — B fails
the renderer check on a question iff its rendered context contains **zero** typed relation lines,
**or** the question's pre-registered gold-evidence claim list (≥1 non-trivial claim per question
by construction, §4) holds no claim that can be read out of the block; (ii) the reviewer receives,
as a **fixed pre-registered input**, the gold-evidence claim list for each inspected question (the
artifact defined in §4, which must be committed as its own pre-render deliverable before any
render — §4 Storage, §9.5; the §4 metric-5 content-match rule is
what decides whether a graph Point carries each claim), so the second clause is judgeable while
the reviewer still does **not** see the arm label or the outcome (contexts are stripped of arm
identifiers and shuffled); (iii) **question selection is pre-registered** — the first **10**
questions in #2578 `qid` order, selected before the run and independent of any rendered output;
(iv) fixed decision rule — "renderer defect" iff the blind reviewer flags **≥1** question **and**
the corpus-wide answer-bearing-claim presence rate (metric 5, §4) is ≥80%. **One reviewer; on any flag a
second blind reviewer independently adjudicates, and any disagreement is a FAIL (flag-fail), never
a majority vote** (two reviewers cannot produce a majority). Only then scale to 52.

## 11. Analysis plan (frozen)

1. Paired McNemar per primary hypothesis (B vs A, C vs A, C vs B), exact test, two-sided, with
   **Holm–Bonferroni across the three** (family α = 0.05). Report the discordance pair counts
   (b, c, b+c) with every test so the realised MDE can be computed (§6).
2. Report absolute counts, not only percentages: `C 31/52` is a result; `59.6%` invites
   over-reading.
3. Report 95% CIs on every difference. If the CI crosses zero → **inconclusive**. **§6's
   15-point floor governs over any CI-excludes-zero result:** a difference under 15 points is
   INCONCLUSIVE even when the interval excludes zero, and forces no §14 decision.
4. Report the per-class breakdown for interval (n=19), ordering/compare (n=32) and
   **`current-state` (n=1, reported for completeness, excluded from per-class inference)** — the
   classes behave differently (#2578: interval 0/19 vs the oracle's 18/19) and an aggregate
   would hide it.
5. Report per-arm context **words**: mean, median, IQR and max, plus the matched-budget verdict
   from §5 — including the >20%-mean-difference truncation sub-analysis (all of A/B/C truncated to
   the common cap defined in §5) when it triggers.
6. Report **metric 3** (correct per 1,000 context words) as part of the primary interpretation,
   alongside the §5 budget verdict.
7. Report **arm D**'s score and state **every** primary conclusion relative to it (A−D, B−D,
   C−D). The no-context floor is the prior-knowledge baseline; a bare accuracy count is not
   interpretable without it.
8. **Prior-knowledge ceiling (pre-registered).** If arm D scores **≥15/52 (28.8%)** — meaningfully
   above LongMemEval's published 18.8% "Best Guess" baseline — the corpus is easier than the
   benchmark average; discount all absolute numbers as prior-knowledge-inflated and lead with the
   D-relative margins (§11.7) as the headline. The D-relative margins are the primary numbers
   regardless of whether this trigger fires.
9. Run the falsification criteria (§7) **before** writing any conclusion, in the fixed precedence
   order (F3 → F1 → F4 → F5 → F2); report the F6 and metric-5/8/9 diagnostics alongside, in every
   case. A hypothesis that fails is reported as failed.
10. Stage the runs: **52 → (if F4/F5) the 133-question census → (if confirmed) 500**. Never skip
    the intermediate step.

## 12. Limitations (written BEFORE analysis — mandate per `experiment-workflow`)

- **n=52 is small.** The 80%-power MDE is ~17–28 pts depending on the (unmeasured) discordance
  rate; the ≥25-pt figure is powered only at `d ≥ 0.40` (§6). This run is an effect-size probe,
  not a powered test for small effects.
- **Power/threshold mismatch is explicit.** F2's 5-point non-inferiority margin cannot force a
  conclusion at n=52; it is a directional observation (§6–§7). F1 and F4 exist only at ≥15 points
  — there is no sub-15 form of either.
- **Judge substitution** if OpenRouter stays exhausted: the deterministic judge is an
  approximation of the official one; absolute numbers are not comparable to published
  LongMemEval results.
- **Oracle-gold construction.** Arm A uses the *gold* sessions, which no real system has. A is a
  ceiling, not an achievable configuration — C vs A therefore understates C's real-world margin.
- **Single corpus, single domain.** LongMemEval-S conversational memory. Conclusions do not
  transfer to document QA without a second corpus.
- **The subgraph serializer is new code.** A first implementation may not represent the
  architecture's best form; a null result measures *this renderer*, not the thesis in principle.
  This is the single largest threat to validity and is why §8.1 exists.
- **Deterministic extraction.** The graph's claim quality is capped by the extractor; arm B
  cannot exceed the knowledge that was actually written.

## 13. Resource estimate

| Item | Cost |
|---|---|
| Reader calls | 52 questions × 4 arms = **208** (+ retries) |
| Judge calls | 208 (0 if deterministic judge) |
| Ingestion | **0** — reuses the #2578 probe graphs on the eval container (`falkordb-eval`, :6380) |
| Wall clock | ~30–60 min end-to-end for 52×4, sequential: reader calls are only ~7–17 min of it (208 × 2–5 s); the rest is judge calls (208, or 0 with the deterministic judge), context rendering/assembly, the 52 per-question scratch-namespace copies for the leakage test, and retries |
| Implementation | subgraph serializer + union packer + 4-arm runner (the real work) |

## 14. What each outcome means for the product

**Every row is subordinate to §6:** a criterion triggered below 15 points is INCONCLUSIVE and does
**not** license the decision below. Only F3 (decision-bearing by construction: `A_ref − 10` =
**32 pts** official / **15 pts** strict, §7), F5, and F1/F4 at ≥15 points are decision-bearing;
**F6 is diagnostic and licenses no product decision.** The arm-D floor always qualifies the margins.

| Outcome | Product decision |
|---|---|
| **F4/F5 (union wins, ≥15 pts)** | Context = subgraph (reasoning) + verbatim (evidence). Build the union packer as the assembly default. |
| **F2 (subgraph alone sufficient)** | **Triggered but INCONCLUSIVE — no product decision.** F2's 5-pt margin is always sub-floor (§6), so it cannot license simplifying to B; only F5 at ≥15 pts decides the union-vs-B choice. |
| **F1 (verbatim wins, ≥15 pts)** | The epistemic layer's value is not retrieval-time. Keep it for decisions/auditing, and serve verbatim source to readers. |
| **F3 (evidence arms A/B/C all ≤10/52 — `A_ref − 10` = 32 pts official / 15 pts strict)** | Nothing about assembly matters yet; go fix retrieval (#2976 trace, #2992 lane defects) first. |
| **F6 (answer-bearing claims present, not surfaced)** | Diagnostic only — no product decision. The defect is query→subgraph selection; the hypothesis to test next is that HippoRAG-style query→triple linking (+12.5 Recall@5 measured) addresses it. Do not adopt on this run. |
| **Arm D high (≥15/52)** | The benchmark is prior-knowledge-inflated — all absolute numbers discounted; D-relative margins are the headline (§11.8). |

---

## Amendments

| Date | Change | Reason |
|---|---|---|
| 2026-09-11 | Review remediation **round 1, before any run** (P0-1…P2-4): §2 H5 numeric; §3 arm D + frozen B/C construction; §4 arm-D floor, strict-primary judge, `current-state` class; §5 prompt-constancy + budget-match controls; §6 derived MDE + decision floor + multiplicity; §7 precedence, mutually exclusive F2/F4, detached F6; §8 confound fixes; §9.4 corpus-wide viability gate; §10 numeric blind H5; §11 analysis-plan completeness; §12 limitation update; token→word relabelling. | Three fresh-context reviews of the pre-registration, **before any data was collected**. No result has been seen, so this is repair of the pre-registration, not a post-hoc revision. |
| 2026-09-11 | Review remediation **round 2, before any run** (P1-1…P2-6): §3 seed function pinned to `vector_search` with the BM25 fallback recorded in the manifest, min–max normalization of `seed_similarity`, dedupe key and the "no separate global cap" rule, whole-graph degree for hub damping, and the complete worked arm-B/arm-C block; §4 metric 5's matching rule frozen with an explicit threshold and §9.4's gate registered as a separately named criterion; §5 budget-match sub-analysis truncating all of A/B/C to a common cap; §7 F6 classified diagnostic everywhere, F1/F4's sub-floor parentheticals deleted, F2 reduced to INCONCLUSIVE and its false F1-exclusivity claim corrected; §10 blind H5 check pre-committed (gold-evidence list as reviewer input, first 10 questions by `qid`, flag-fail on disagreement); §3/§10 gold-field perturbation + static-reference leakage test added. | A fresh-context review of the round-1 remediation, **before any data was collected**. Repair of the pre-registration, not a post-hoc revision. |
| 2026-09-11 | Review remediation **round 3, before any run** (P1-1…P2-8): §2 H5 metric renamed; §3 seed-truncation tie-break + concrete empty-context sentinel + `zero_seed` selection-failure flag, contradiction reservation ahead of the 12-cap, natural-log damping base, named rendered-session field + full scratch-namespace definition, admission tie-break, worked-example scope limited to render format; §4 metric 5 made content-bearing (answer-bearing-claim match) + new metric 9 (gold-provenance presence) split out, metric 8 re-pointed; §5 prompt scaffolding made single and adaptation forbidden; §7 F6 re-pointed to metric 5 + F2/F4/F5 exclusivity + F3 arm set (A/B/C) + evidence-collapse/D-does-not case; §8.4 mitigation re-pointed; §9.4 gate re-pointed and made deterministic; §10 leak-test + H5 references synced; §11.9 diagnostics list; §14 F2 row reduced to INCONCLUSIVE, F3/F6 rows re-labelled. | A fresh-context review of the round-2 remediation, **before any data was collected**. Repair of the pre-registration, not a post-hoc revision. |
| 2026-09-11 | Review remediation **round 4 (final), before any run** (P1-1…P2-7): §7's prior-knowledge trigger aligned to §11.8's single definition (D ≥ 15/52), removing the superseded lower D-trigger; §3/§4/§10 leakage guard extended to the gold-evidence claim list (static-reference assertion + never-in-a-graph-namespace rule); §4 gold-evidence claim list derived from the `has_answer` turns of the gold sessions plus the gold `answer`, sentence-span granularity, a JSON artifact built and committed as a **pre-render** deliverable + sha256, `MIN_GOLD_TOKENS = 3`, the zero-content-token claim defined as never-matched; §3 dedupe precedence fixed (score first, edge priority the tie-break) and operation order frozen (cap → dedupe); §3 seed fetch widened to `limit=64` so the boundary tie-break sees the full tied set, selected seed `id`s recorded in the manifest, min–max normalization scoped to the 8 selected seeds; §3 serializer turn ordinal (`source_turn_id`/`id`, 0-based `t<ti>` + 1), date (`createdAt`, else `validFrom`), confidence (`f"{c:.2f}"`) fields named; §3/worked-example render order corrected and the reserved-relation anchor disambiguated; §13 reader-call arithmetic aligned with the wall-clock total; §7/§14 F3 magnitude re-anchored to the primary arm-A count with the §6 15-pt floor; §10 zero-seed carve-out in the first STOP bullet. | A fresh-context review of the round-3 remediation, **before any data was collected**. Repair of the pre-registration, not a post-hoc revision. |
| 2026-09-11 | Review remediation **round 5, before any run** (FIX 1, P1 + FIX 2–4, P2): §7 F3's magnitude re-defined against arm A's **pre-registered reference count** `A_ref` (42/52 official/variant-aware, 25/52 strict containment) instead of this run's arm-A count — `A_ref − 10` = **32 pts** or **15 pts**, both ≥ the §6 floor — making the F3 row, the §7 preamble, the §7 paragraph, §3's arm-A note and §14 agree exactly and removing the unsatisfiable ≤10/52-derived magnitude; §4's `tokens()` given a frozen split rule (**ASCII punctuation deleted, not replaced by a space**, then split on whitespace) shared by `MIN_GOLD_TOKENS` and the metric-5 ratio, with metric 5 deferring to that one definition; §4's 1–2-content-token rationale corrected (the ratio takes only 0 / 0.5 / 1.0, so the ≥0.80 threshold degenerates into an exact-subset test); Amendments table completed — the round-1 label added and the round-2 remediation logged, so every round cited in a reason has its own entry. | A fresh-context final review of the round-4 remediation, **before any data was collected**; supersedes the round-4 "(final)" label. Repair of the pre-registration, not a post-hoc revision. |
| 2026-09-11 | Review remediation **round 6, before any run** (P1): the round-4 requirement that the gold-evidence claim artifact be *committed in the freeze commit* was not satisfiable — the pre-registration is already frozen and `docs/experiments/artifacts/` exists in neither the tree nor its history, so §4 Non-emptiness/Storage, §4 metric 5, §10 and the round-4 amendment row falsely asserted an already-committed artifact (the residual "pre-committed" wording at the three live list/input sites is re-worded "pre-registered"). The artifact is now stated as **not yet created**: a pre-run deliverable built, validated, committed on its own commit, and sha256-recorded in the manifest before the first render, with §9.5 added to gate the run on its existence. | A fresh-context review of the frozen pre-registration, **before any data was collected**: no artifact directory exists in the worktree, the base repo, or any commit history. Repair of the pre-registration, not a post-hoc revision. |

*Pre-registration frozen: 2026-09-11. Amendments after the first run must be dated and appended, never edited in place.*
