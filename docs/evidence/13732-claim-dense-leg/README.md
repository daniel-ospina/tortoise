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

| ingredient | state on this box (`cmux`, measured 2026-11-19; command given) |
|---|---|
| capture corpus (`~/.tortoise/session-events/*.jsonl`) | **absent under the lane's `HOME=/tmp/cell-home`** — `ls … \| wc -l` → 0. ⚠️ NOT absent on the box itself: under `HOME=/home/cmux` the same path holds dated `*.jsonl` captures (`ls ~/.tortoise/session-events/*.jsonl \| wc -l` → 24 at 2026-11-19, a rolling daily series, so any fixed integer here stales within a day). The row is a statement about the lane's home, not about `cmux`; a run on the real home gets PAST `require_corpus` |
| embedder (`fastembed`) | **absent** — `importlib.util.find_spec("fastembed")` → `None`. ⚠️ **NOT because the network is down**: `curl -m 10 -o /dev/null -w '%{http_code}' https://huggingface.co` → **200** and `https://pypi.org` → **200** (measured 2026-11-19). The earlier `000` reading in this row was a **transient DNS failure** — in the same session `uv run` aborted with `failed to lookup address information: Temporary failure in name resolution` and then succeeded later — so network state is INTERMITTENT here and a single probe is not a durable claim. The blocker is that the extras were never installed, not that they cannot be |
| record driver (`psycopg2`) | **absent** — `find_spec("psycopg2")` → `None`; every record read in this tree goes through it. Same network note as the row above: absence of the PACKAGE, not of the index |
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

`populations.point.nodes` = **786** — M2's built claim population (`--points 900` → 786 built; the funnel is
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
itself at `tortoise/record_writer.py:1814-1815`: *"the graph fold derives it and writes it straight onto
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
*passes* the gate and is then served by a leg whose trace never names the two-zero state —
`empty_results` from the record where the contract owns the label, `no_embeddings`/`degraded=True`
from the graph everywhere else (this instrument reads `point`, so the second shape is the one it
sees) — which would have read as "the dense leg has no recall" instead of "the population has no
vectors".
The zero-zero state is refused from **both** sides — `require_record_measured` for the record,
`require_graph_population` for the graph — and
`test_seeded_guard_refuses_the_zero_zero_coverage_trap` pins it. One function asserting both counts
was what let a GRAPH fact (a zero-vector graph) refuse the RECORD arm out of the record block's own
`try/except`; each fact now refuses where it lives.

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

⛔ **THE GRAPH HALF NEEDS THE SAME QUESTION, ASKED OF THE ID *AND THE TEXT*, ON THE `graph_dense` ARM**
(`#13734` review, round 5). The gate beside the seed is **prefix-wide** —
`MATCH (n:Point) WHERE n.id STARTS WITH '<prefix>' AND n.embedding IS NOT NULL` — so it answers
*"does this graph hold ANY embedded claim?"*, which stays above zero while the population is only
**partly** seeded. ⛔ **The reachable mechanisms, and only these** (`#13745` round 6, finding 3): the
instrument's seed payload ALWAYS carries an explicit `embedding`, so the fold takes its
`owns_embedding` branch and never reaches the `encode_for_row` derive — an operator point, an
EMBED-gate refusal (`#5008`) and a swallowed encode failure are **unreachable on this write path**
(and a `None` from `compute_embeddings` is refused by name as `seed_vectors_missing` before a single
write). What IS reachable: an embedding-**width mismatch** (the fold warns and leaves the node's
vector alone rather than write one the index cannot hold, `#5004`); and a vector the fold **refuses as
unusable** (`_writable_journalled_vector` answers `None` for an empty, over-long, or
non-finite/non-numeric value, so nothing is written). ⛔ **Nothing else:** the seed appends and folds
every population row before the guard runs and raises on failure, so `matched < n` means the VECTOR is
missing, never the row — the `--graph` case included, because the seed's own `MERGE` writes this run's
text at every deterministic id it scores.

`graph_population_vectors` therefore asks the **id-and-text** question — one `IN $ids` round trip
returning `(id, content)`, paired in Python against the text this run asked for — and
`require_graph_population` refuses `graph_population_absent` (or `graph_population_unmeasurable`,
because unmeasured is never zero). ⛔ **What the text half buys is narrow, and stated exactly:** it
catches a row this run did NOT write — a node the seed never created, or one a LATER write moved (the
population's ids are deterministic, `f"{ID_PREFIX['point']}{index:06d}"`, so on a reused `--graph`
every id collides and the seed's own `MERGE` rewrites each one — which is why a reused graph alone
does **not** put a row in this category). It does **not** catch a preserved VECTOR: the fold rewrites
`n.content` unconditionally, so on a reused graph the node carries THIS run's text beside the OLD
vector and the pair matches anyway — an id-only `IS NOT NULL` count would agree with it.
Residual, stated rather than papered over: the pairing compares TEXT only, so on a re-run whose
population text is unchanged a preserved vector is indistinguishable **whatever its width or
embedder** — the text test catches only a node whose text CHANGED, and this guard reads no
vector's VALUE or identity (it filters on `n.embedding IS NOT NULL` and returns `(id, content)`).
That residual is closable without touching the store, which is worth saying because the earlier
text of this paragraph implied it was not: `_upsert_point_props` already RETURNS whether the row's
vector was written (`(embedding is not None, …)`, `projection/entities.py:3339`) and the fold path
the seed now uses discards that value — capturing it per row makes the count exact, where a re-read
cannot. Until
then this guard asserts the population **was written**, not that every vector was replaced.

The guard gates the **`graph_dense` arm, not the run** — the same scope, for the same reason, as the
record gate above (`#13745` review round 1, finding 2). A partly seeded graph does **not** degrade
`lexical`: that arm forces `EmbeddingModel.get()` to `None` and the full-text index it reads is built
from `n.content`, which the fold writes **unconditionally** — only the vector is held back. Nor does
it degrade `record_dense`, which reads Postgres. (The content write is unconditional, and the seed
appends/folds every row before this guard and raises on failure, so a row this run did not write is a
defensive case rather than an ordinary one — the text half of the pairing exists to catch it, and it
is why a `graph_population_absent` is still worth reading against the population's own rows.) So the
graph cohort is a precondition of exactly one arm, and refusing the run over it would throw away
`record_dense_vs_lexical` — the lane's own headline question — while looking like a strict gate. When
the graph cannot hold the cohort, the arm is recorded `BLOCKED[graph_population_absent]` by name, the
other two arms measure, the graph comparisons are `UNMEASURED`, and the run exits **3**.

That replaces the `graph_vectors <= 0` branch that used to refuse the whole run when the graph held
no vector at all: on a box that could still publish the lexical baseline and the record comparison,
it published nothing. The zero case is now just the extreme of a partly seeded population. The
artifact carries both counts — `graph_vectors` (prefix-wide, now the **coverage denominator** only)
and `graph_population_vectors` (id-and-text scoped) — because the first alone cannot tell a full seed
from a partial one.

So the truth about this instrument, stated plainly — **as of #13830**: it embeds the population and
writes it through **one journaled door** (`tortoise/live_write.py::write`: append the journal record
→ project it into the record → fold the graph), so BOTH stores are fed from the same records by the
same path. Before #13830 the seed was a direct projection write (`proj._upsert_point_props`) that
emitted **no journal record**, so the population could reach the graph and never the record — the
gap the next two notes are about, and the one #13830 closes without adding a second writer.

⛔ **The record's own preconditions gate the ARM too, not just the cohort.** `no_record_dsn`,
`no_record_driver`, `record_leg_disarmed` and `no_tenant` are resolved BEFORE the graph is seeded
(so a refused run leaves no seeded graph) and are applied to `record_dense` alone — `lexical` and
`graph_dense` publish regardless. The artifact carries a `cleanup` block naming the graph a
completed run leaves behind, because nothing drops it.

⛔ **The seed is now the journaled door, and the guard is still the guard (#13830).**
`_seed_population` writes each row with `live_write.write` — append, then record projection, then
graph fold — so the population the `lexical`/`graph_dense` arms rank is the population the record
holds. The **id-scoped guard is unchanged**: `record_population_vectors` counts the population's own
ids in the record *before* the arm, so a write that was ATTEMPTED and a write that LANDED are still
different facts. When they are not all there, the `record_dense` arm is recorded as
`BLOCKED[record_population_absent]` by name and the run exits **3**, so the artifact cannot be
mistaken for the paired answer. What changed is the DIAGNOSIS: a shortfall is now a fold/ordering
fact (a refused projection, a row filed under another tenant, an enumerated replay refused on a
cut-over graph, or an `embedding` the store's converter turns into a NULL `embedding_vec`), and the
repair is the record's own fold — `record_runner plan --from-checkpoint`, then
`record_runner apply --acknowledge-plan <N>` — which the artifact emits as `seed.fold`, tenants
filled in, whenever a write came back unprojected. ⛔ **One command would not do:** `apply`
requires `--org-id`/`--graph-id` on every verb and refuses a writing run without the count `plan`
prints, so a bare `apply --journal <path>` is argparse-rejected.

## 4. Falsifiers, and where each one stands

| falsifier the brief names | status |
|---|---|
| paired Δ on recall@3 **and** `found@deep` before/after | **not measured** — no corpus/embedder/record driver on this box; the instrument computes both and labels `TRADE` |
| `leg_trace` showing `mechanism: pgvector` from the record | **not measured**; asserted structurally (`check_arm`) so a run that cannot show it refuses — including a leg that RAN and **DEGRADED** (`ran=True, degraded=True`, the engine's shape for a failed query), which `ran` alone cannot see. ⛔ **The cohort it needs is now produced by the run** (#13830): the seed writes through the journaled door, so `record_dense` is measurable — the arm being measurable is NOT the arm having been measured |
| detectable effect for n = 786 stated before the verdict | ✅ **done** — §1, committed and pinned |
| a signal's own isolated delta, never bundled | ✅ by construction — the three arms are the isolation; the pre-registration forbids adding a signal to an arm |
| `object`/`subject` still withheld (run the pinning test) | ⛔ **the named test does not exist in this tree.** `test_object_and_subject_still_have_no_record_served_dense_leg` was not found by `grep -rn` anywhere under `tests/`. The posture it names was **reversed by landed work**: `record_vector.LABEL_SERVED` carries `object` and `subject` since `#13422`/PR #13466, and `MIRROR_RETIRED_LABELS = {"object", "subject", "event", "document", "source"}` is the set whose graph copy is retired. That is the owner's 2026-11-17 answer on `#13220` — *"if indexing improves search for Object/Subject/Event of course we want that … in Postgres and the search aligned with that"*. The brief's instruction is stale, and following it would have reversed a landed decision inside a measurement tool. What holds instead: **no graph vector index is created on any label** (V1, PR #11481), pinned structurally by `TestContradictionTest` |
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

⛔ **STEP 3 NOW SEEDS THE RECORD ITSELF, THROUGH THE JOURNAL (#13830).** The instrument writes every
population row with one call of the `#13345` chokepoint (`live_write.write`): the journal record is
appended, then projected into the record on that write's own transaction, then folded into the
graph. So the `record_dense` arm's cohort is produced by this run, through the ONE write path, and
not seeded a second way — a parallel record seed would reintroduce exactly the dual-write defect the
chokepoint exists to remove.

⛔ **The journal is therefore a REQUIRED declaration** — `--journal` or
`TORTOISE_RECORD_JOURNAL`, refused as `no_journal` when neither is set, refused as `journal_conflict`
when the two name different paths. That path is what `record_vector.journal_ready` compares the
tenant's projected positions against, so the journal this run writes has to be the journal the
record's readiness gate measures. Point it at a **measurement tenant**: a run appends `n` journal
records and writes `n` record rows, and the ids are deterministic, so a re-run upserts the rows and
only grows the journal.

⛔ **`--dsn` ARMS THE WRITE, NOT ONLY THE READS (#13839).** The write path's arming predicate reads
`TORTOISE_RECORD_DSN` from the **environment** (`record_writer._live_write_verdict`, no argument), so
a run whose DSN came only from the `--dsn` flag used to arm every read and leave `live_write.write`
unarmed: the seed wrote **nothing**, no fold was emitted, and the artifact published
`record_armed: false` for a record the same run could query. `run_measurement` now declares the
resolved value into `TORTOISE_RECORD_DSN` for the length of the run (`main()` restores the caller's),
and if a run holding a DSN ever sees the seam answer *unarmed* again the `record_dense` arm is
blocked by name — `record_arm_undeclared` — instead of being reported as a fold shortfall. **Export
`TORTOISE_RECORD_DSN` (or pass `--dsn`) before pasting the fold commands**: they carry
`--dsn "$TORTOISE_RECORD_DSN"` so the fold writes into the store this run measured; with the
variable unset that is a named refusal, and with it naming a *different* record it used to be a
silent fold into a store nobody measured.

⛔ **If a write comes back unprojected, the artifact says so and names the repair.** `seed` in the
artifact carries `record_armed` (three-state: `true`/`false`/`null`, so `record_applied: 0` on an
unarmed deployment is not read as a refusal), `record_applied`, `record_unprojected`,
`record_declined`, `record_deferred`, `record_deferred_failures` and
`record_deferred_undrained`, plus `positions_unproven`; when
a fold is needed it also carries `seed.fold` — the record's own fold over the journal this run wrote,
as the TWO commands `record_runner` actually requires:

⛔ **A DRAIN THAT DID NOT FINISH ALSO NAMES THE FOLD** (#14222 review, finding 6). `record_deferred`
counts writes whose fold was QUEUED, so on its own it says nothing about whether the queue reached
them — a converged drain leaves it non-zero and correctly emits NO `seed.fold`. Two things DO emit
one, and they are different failures:

* `record_deferred_undrained` — the positions the worker still held when its drain timed out, read
  from the loop's own `pending_positions` (`deferred` is the fallback when that read fails, which can
  only over-count). The queue never reached them.
* ⛔ `record_deferred_failures` — the folds that RAN and wrote no row: the loop's own failure counter
  (`projection_raised`, `record_no_outcome`, `projection_refused`), DIFFERENCED around the seed
  because it is process-cumulative. ⛔ **A CONVERGED DRAIN IS NOT A WRITTEN RECORD** (#14222 review
  round 16, P2): the `circuit_open` outage refuses every deferred fold and therefore drains
  immediately, leaving `deferred=N, applied=0, undrained=0` — byte-identical to a healthy seed. Before
  this field the artifact said "no fold needed" for a record short by every queued row, and the
  refusal message pointed the operator at a `seed.fold` that did not exist. Verifying a drain is not
  verifying a write.

The note names the cause that fired, so a repair is never emitted with two zero counts and no reason.

⛔ **`record_declined` IS THE FOURTH ANSWER, AND IT IS NOT A FOLD** (#13839 review). A write the seam
DECLINED (`live_write`'s `no_durable_journal`: it answered, refused and never attempted the row)
reports `applied: 0, unprojected: 0` — the same shape an UNARMED deployment has — so it used to read
as a clean seed. It now carries its own count, a `declined` note and a stderr warning, and it
deliberately emits **no** `seed.fold`: a decline means there is no durable journal line for a fold to
replay, so a repair command there could not work. If `record_declined` is non-zero, the journal is not
a real journal.

⛔ **And `record_dsn_fingerprint` says WHICH store.** `--dsn "$TORTOISE_RECORD_DSN"` names the
variable, not the value, so the artifact publishes the DSN's 8-hex fingerprint
(`record_writer.dsn_fingerprint`, #2903/#2796 — a secret must never enter an artifact) beside the
commands. Compare it against the
fingerprint of the DSN in your shell before pasting the fold: a shell that exports a *different*
record folds this population into a store nobody measured, silently.

```bash
# 1. plan: read the number, writes nothing
uv run python tools/record_runner.py plan --from-checkpoint \
    --journal "$TORTOISE_RECORD_JOURNAL" --org-id "$TORTOISE_RECORD_ORG_ID" --graph-id "$TORTOISE_RECORD_GRAPH_ID" \
    --dsn "$TORTOISE_RECORD_DSN"
# 2. apply that number (N = what the plan reported)
uv run python tools/record_runner.py apply \
    --journal "$TORTOISE_RECORD_JOURNAL" --org-id "$TORTOISE_RECORD_ORG_ID" --graph-id "$TORTOISE_RECORD_GRAPH_ID" \
    --dsn "$TORTOISE_RECORD_DSN" --acknowledge-plan <N>
```

(The artifact fills the tenant in from the run itself — the block above is the same two commands with
shell variables.) `apply` refuses a writing run without `--acknowledge-plan` (#11469's
report-before-drop guarantee, made mechanical), which is why the pair is emitted rather than one
line. ⚠️ A CUT-OVER graph (one holding a `record_cursor` row) refuses an ENUMERATED replay whatever
the acknowledgement; the repair there is the allocator's operator path, not this fold.

The run still measures the other two arms and still exits 3 with `record_dense` blocked by name; a
re-run after the fold measures all three.

⛔ Do **not** write over `preregistration.json`; it is the record of what was declared before the
numbers, and a re-run that edits it has no pre-registration at all.

⛔ And a refusal is a result: `REFUSED[record_population_absent]` means the record does not hold
this population's ids, so the `record_dense` arm would have measured the seed step. Do not relax the
guard to get a number — that is the false PASS `#13734`'s review closed.
