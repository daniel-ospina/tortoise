---
title: "#13732 — claim recall under an armed dense leg: the pre-registration, and why there is no run yet"
type: evidence
domain: platform
doc_status: live
created: 2026-11-19
subjects.team: epistemic-team
aboutSubjects: claim recall, dense leg, record-served retrieval, paired measurement
aboutObjects: "#13732, #12186, #12349, #13210, #13345, Record PDF, evidence/12186-m2-windowed-quality"
---

# #13732 — claim recall under an armed dense leg

**This bundle carries a PRE-REGISTRATION, not a measurement.** `#12186` M2 measured the claim
(`:Point`) population at **recall@3 15.7 %** / **found@deep 71.3 %** with
`retrieval.mode = keyword_only` — the dense leg did not run. `#12349` (M3) measured the text
scorer and closed it. `#13345` and `#13210` have since landed, so the record can now serve a dense
leg. The question this lane was dispatched with is *"does serving that leg from the record lift
claim recall?"* — and it is **unanswered here**, because this box cannot run the instrument:

| ingredient | state on this box (`cmux`, 2026-11-19) |
|---|---|
| capture corpus (`~/.tortoise/session-events/*.jsonl`) | **absent under the lane's `HOME=/tmp/cell-home`** — 0 files. ⚠️ NOT absent on the box itself: under `HOME=/home/cmux` the same path holds **25** dated `*.jsonl` captures (measured 2026-11-19). The row is a statement about the lane's home, not about `cmux`, and the run on the real home gets PAST `require_corpus` and refuses `no_embedder` instead |
| embedder (`fastembed`) | **absent**; `https://huggingface.co` → `000`, so it cannot be installed (`pyproject` extras are `embeddings`) |
| record driver (`psycopg2`) | **absent**; `https://pypi.org` is unreachable, so it cannot be installed — every record read in this tree goes through it |
| pgvector image | **present** (`pgvector/pgvector:pg16`), so the *store* is not the blocker |
| FalkorDB | **present** (lane containers) |

⛔ The lane's rule is explicit — *"if a step needs [the dataset], say so and hand back rather than
fabricating a fixture"*. A synthetic corpus plus a stub embedder would have produced a recall
number that was a statement about the fixture, and it would have been quoted as a claim-population
figure. So: no rate is published here.

What IS published is everything a later run must **not** be free to choose after it sees the
numbers.

---

## 1. The pre-registered statistic

`measurement.n` = **786** — M2's built claim population (`--points 900` → 786 built; the funnel is
in that record). Primary k = **3**, the production capture prior's width (`extractor_v2._fts_rows`).
Test: **exact two-sided McNemar / binomial on the discordant pairs only**, imported from
`tools/m3_fts_leg_recall.py::_binom_two_sided` so the tree keeps one paired statistic.

**The detectable effect, stated before any verdict** —
`Δ_min = (z_{0.975} + z_{0.80}) · sqrt(p_d / n)`:

| discordance `p_d` | detectable Δ at n = 786 |
|---|---|
| 0.05 | **2.23 pp** |
| 0.10 | **3.16 pp** |
| 0.20 | **4.47 pp** |
| 0.30 | **5.47 pp** |

So on this population the test resolves a **≈ 3–5.5 point** move; anything smaller is
`UNRESOLVED`, not a small win. This table is machine-generated into
[`preregistration.json`](./preregistration.json) by `--preregister`, and
`tests/test_claim_dense_leg_13732.py` pins the committed block equal to the instrument's own
output — a composition check, so an edit to one that stales the other reds.

## 2. The arms, and what each must prove about itself

Every query is asked in **all three** arms, in one run, over one graph, on one population.

| arm | dense leg | the trace MUST show | why it exists |
|---|---|---|---|
| `lexical` | not submitted (`EmbeddingModel.get()` → `None`, the documented `no_embedder` path) | vector entry `ran=False` | the M2 posture — the arm whose 15.7 % is being re-measured |
| `graph_dense` | armed OFF (`record_vector.ARMING_ENV` explicit falsy) | `mechanism ∈ {index, scan, scan_fallback}` | the control that isolates **where** the leg came from |
| `record_dense` | armed ON, coverage verified | `mechanism = pgvector`, on every query | the arm the lane's question is about |

⛔ **A fallback is not a figure.** A `record_dense` arm reporting `scan_fallback` is REFUSED
(exit 3) rather than published: that is the graph answering wearing the record's name, and it
would make any delta a statement about *having* a dense leg rather than about the record. The same
refusal covers a MIXED arm, a dense arm whose leg did not run, a **dense arm whose leg ran and
DEGRADED** (`dense_arm_degraded_read` — the engine's own shape for a failed query is
`ran=True, degraded=True, reason="query_failed"` *with its mechanism still set*,
`search_engine.py:4153`/`:4742`, so `ran` alone cannot see it and the queries would score as
guaranteed misses), and a `lexical` arm in which the dense leg actually ran.

**`found@deep` rides beside every delta.** A gain at k=3 that lowers the depth-50 read is labelled
`TRADE`, never `GAIN`: that is a better order bought with coverage, and the two must not be
published as one number (M2's Finding 2 is the same distinction).

## 3. The finding that made the instrument necessary

**M2's claim population carried no vectors — and the reason is the EMBEDDER, not the payload.**
`window_quality_m2.write` writes each claim through
`proj._upsert_point_props({"id", "content"})` (`window_quality_m2.py:850` — `build_point_population`
only *builds* the dicts and writes nothing) — **no `embedding` key** — and on M2's box no node
carried a vector. ⛔ But "no `embedding` key" is not why: `_upsert_point_props` **derives** one from
`content` whenever the payload owns no `embedding` key —
`tortoise/projection/entities.py:2968-2976`, `elif not op and p.get("content"):
encode_for_row(p.get("content", ""), self.required_embedding_dim)` — and the tree says so about
itself at `tortoise/record_writer.py:1809`: *"the graph fold derives it and writes it straight onto
the node, #7845"*. On a box **with** an installable embedder M2's exact payload would therefore have
carried vectors. It did not because that box had none, `encode_for_row` raised, and the surrounding
`except Exception: pass` swallowed it. A first cut of this bundle stated the opposite ("could not have
found anything even on a box that had one"); that sentence was **wrong** and is corrected here.

What follows from it: the population must be embedded and written **before** a dense arm can be
measured, and this instrument's seeding step is that prerequisite — not because M2 wrote its payload
"wrong" (it wrote the production one), but because the seeding step buys a vector written
**unconditionally, by this run, under this run's embedder**, rather than inherited from a
best-effort branch whose behaviour depends on the EMBED gate, the adapter, and the node's prior
state. ⛔ And the seed composes its text through the write path's OWN seam
(`embeddings.compute_embeddings` — 512-word truncation, then `vec.tolist()`), not through a bare
`model.encode(content)`: an untruncated text's vector, or a list of `numpy.float32` scalars, is a
value production never writes, and this step exists to make the seeded vector the production one. Consequence 1 of M2's box: with no embedder there is no `query_vec`, so the vector strategy
is never submitted (the declared `keyword_only`). Consequence 2, the quiet one: no node carries an
`embedding` to be near — a fact about the box.

⛔ **And the coverage gate does not catch it.** `record_vector.coverage` decides
`record_vectors >= graph_vectors`, and **`0 >= 0` is VERIFIED**. An unseeded population therefore
*passes* the gate and then serves an empty cohort as `mechanism=pgvector, reason=ok` — which would
have read as "the dense leg has no recall" instead of "the population has no vectors".
`require_seeded` refuses two zeros for exactly this reason, and
`test_seeded_guard_refuses_the_zero_zero_coverage_trap` pins it.

That is **half** of this lane's code, and the other half is the correction `#13734`'s review forced.

⛔ **AND THE COUNT IS NOT ENOUGH — THE IDS ARE (#13734 review, finding 1).**
`coverage` answers a **label-wide** question on purpose ("does the record hold as many vectors as
the graph serves?", `#11395` step 2), so on any tenant holding enough embedded `Point` rows it
answers VERIFIED for a population whose **own** ids are in neither store. The `record_dense` arm
would then rank none of them, report `recall@3 = 0.0`, and publish *"the record's dense leg
collapses claim recall"* with a valid McNemar p — a figure about the seeding step wearing the leg's
name. The instrument therefore asks a second, **id-scoped** question before any arm runs:
`record_population_vectors` counts how many of the population's own ids the record holds a vector
for (the same predicates as the label-wide count — `org_id`/`graph_id` from the one tenant prologue,
`embedding_vec IS NOT NULL`, since that is the column the serve slices), and
`require_population_in_record` REFUSES the `record_dense` ARM when that is not all of them (the
`lexical` and `graph_dense` arms need no record cohort and still publish):
`record_population_absent` (or `record_population_unmeasurable` when the count could not be taken —
unmeasured is never zero). `coverage` itself is unchanged: widening it would reverse a documented
decision and change every other label's reader, and the id-set half belongs in the instrument, which
is the only place the population is known.

So the truth about this instrument, stated plainly: **it embeds and writes the population to the
GRAPH, and it REQUIRES — never writes — the record's matching cohort.** `#13210` is the backfill's
tool, and the record is a journal fold with no side door; a second writer for its population is the
one thing this file promised not to be.

⛔ **The record's own preconditions gate the ARM too, not just the cohort.** `no_record_dsn`,
`no_record_driver`, `record_leg_disarmed` and `no_tenant` are resolved BEFORE the graph is seeded
(so a refused run leaves no seeded graph) and are applied to `record_dense` alone — `lexical` and
`graph_dense` publish regardless. The artifact carries a `cleanup` block naming the graph a
completed run leaves behind, because nothing drops it.

⛔ **And this population's ids cannot reach the record by any backfill.** `_seed_graph` writes
through `proj._upsert_point_props`, which is the apply-FOLD — it emits no journal record — while the
record is a journal fold (`record_runner`). So `#13210` folding a journal cannot deliver a cohort
that never entered the journal. The guard therefore gates the **`record_dense` arm**, not the run:
the `lexical` and `graph_dense` arms need no record cohort and still publish, the `record_dense` arm
is recorded as `BLOCKED[record_population_absent]` by name, and the run exits **3** so the artifact
cannot be mistaken for the paired answer. A run that refused before its first arm published nothing
while looking like a strict gate — that was this file's first cut of the guard, and it is the reason
the scoping exists. Getting a `record_dense` number requires a population that IS in the record: one
created through the journaled SDK door and folded with `record_runner`.

## 4. Falsifiers, and where each one stands

| falsifier the brief names | status |
|---|---|
| paired Δ on recall@3 **and** `found@deep` before/after | **not measured** — no corpus/embedder/record driver on this box; the instrument computes both and labels `TRADE` |
| `leg_trace` showing `mechanism: pgvector` from the record | **not measured**; asserted structurally (`check_arm`) so a run that cannot show it refuses — including a leg that RAN and **DEGRADED** (`ran=True, degraded=True`, the engine's shape for a failed query), which `ran` alone cannot see |
| detectable effect for n = 786 stated before the verdict | ✅ **done** — §1, committed and pinned |
| a signal's own isolated delta, never bundled | ✅ by construction — the three arms are the isolation; the pre-registration forbids adding a signal to an arm |
| `object`/`subject` still withheld (run the pinning test) | ⛔ **the named test does not exist in this tree.** `test_object_and_subject_still_have_no_record_served_dense_leg` was not found by `grep -rn` anywhere under `tests/`. The posture it names was **reversed by landed work**: `record_vector.LABEL_SERVED` carries `object` and `subject` since `#13422`/PR #13466, and `MIRROR_RETIRED_LABELS = {"object", "subject"}` is the set whose graph copy is retired. That is the owner's 2026-11-17 answer on `#13220` — *"if indexing improves search for Object/Subject/Event of course we want that … in Postgres and the search aligned with that"*. The brief's instruction is stale, and following it would have reversed a landed decision inside a measurement tool. What holds instead: **no graph vector index is created on any label** (V1, PR #11481), pinned structurally by `TestContradictionTest` |
| `dod_5090_probe.py` still 3 MET / 1 PARTIAL | ✅ re-run at this head (see the PR body) |

## 5. What the next lane does

```bash
# 1. a box with the corpus, the embedder and a record:
uv sync --extra embeddings --extra parity
export TORTOISE_RECORD_DSN=... TORTOISE_RECORD_SINGLE_TENANT=1
export TORTOISE_RECORD_ORG_ID=<org> TORTOISE_RECORD_GRAPH_ID=<graph>   # the PAIR is required
export TORTOISE_RECORD_JOURNAL=/path/to/journal
python -m tortoise record-reconcile --journal "$TORTOISE_RECORD_JOURNAL"   # exits 3 while the record lags

# 2. the pre-registration, unchanged (it is what makes the run a measurement):
uv run python tools/claim_dense_leg_13732.py --preregister --population 786

# 3. the paired run:
#    `record_dense` refuses by name unless the record holds THIS population's ids AND the
#    record's DSN / driver / arming / tenant all resolve; the `lexical`/`graph_dense` arms
#    publish regardless (those four gates are applied to the record arm alone), and the run
#    exits 3, so the artifact is never mistaken for the paired answer the pre-registration
#    prices.
uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
uv run python tools/claim_dense_leg_13732.py --points 900 --deep 50 \
    --out /tmp/claim-dense-13732.json
```

⛔ **`TORTOISE_RECORD_SINGLE_TENANT=1` ALONE RESOLVES NO TENANT.** `record_writer.record_tenant()`
returns the env **pair** `TORTOISE_RECORD_ORG_ID`/`TORTOISE_RECORD_GRAPH_ID` and refuses a
half-configured one; the declaration only permits the pair to be consulted (#9192). Measured
(2026-11-19, no store needed): with the DSN and the declaration exported and no pair,
`record_tenant()` → `None`, so `_resolve_scope()` → `None` and the run refuses `no_tenant` **before
any arm**. A first cut of this runbook exported only the declaration — following it was a guaranteed
refusal.

⛔ **STEP 3 CANNOT SUCCEED ON THIS TREE TODAY, AND THE MISSING STEP IS NAMED HERE RATHER THAN
DISCOVERED AT THE END OF A WINDOW.** The instrument embeds and writes the population to the
**GRAPH** only (`_seed_graph` → `_upsert_point_props`, a direct projection write that emits **no**
journal record), while the record is fed **exclusively** by a journal fold —
`tortoise/record_vector.py:247`: *"the record is a journal fold with NO backfill"* — folded by
`tools/record_runner.py apply`. The population's ids are minted by the M2 harness
(`pt-12186…`), and nothing else in the tree writes them. So `record_population_vectors` returns `0`
on **every** tenant and step 3 refuses `record_population_absent`. For the `record_dense` arm to be
measurable at all, the population must first be written through a **journaled** door and folded into
the record — which this instrument deliberately does not do, and must not: the guard is the `#5090`
Q2 false PASS this bundle exists to prevent.

⛔ Do **not** write over `preregistration.json`; it is the record of what was declared before the
numbers, and a re-run that edits it has no pre-registration at all.

⛔ And a refusal is a result: `REFUSED[record_population_absent]` means the record does not hold
this population's ids, so the `record_dense` arm would have measured the seed step. Do not relax the
guard to get a number — that is the false PASS `#13734`'s review closed.
