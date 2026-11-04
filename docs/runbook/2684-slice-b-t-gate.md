---
title: "#2684 (Slice B) — the value-span gate: the mechanism is LANDED, the per-question leg is MEASURED, the accuracy leg is VOID as written and re-homed"
type: runbook
domain: operations
doc_status: live
created: 2026-10-24
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# #2684 (Slice B) — settled as far as a lane can settle it

**Status.** The slice's **I** (extraction value-fidelity + a span-carrying
schema field + assembly surfacing) is **LANDED and green** — §1 re-measures it,
because the issue body is provenance-stale by construction. Its **T** has two
legs, and they are not in the same state:

| Leg | Statement (wave §6) | State |
|---|---|---|
| **T-1** per-question | *the value-bearing evidence is **verbatim-present** + **deduped** in the reader's package* | **verbatim half MEASURED** on the sealed corpus — 101 of 112 value-bearing points carry an address that re-reads the point's own words **and a value**; all 11 absences are a **reported** class — **0 silent**, each carrying a warning the census attributes to its class (the single `empty_quote` point via the resolver's *shared* fail-open "no resolvable source turn" — the same string a `no_turn` emits — so its class rides on the census's derivation from the quote, not on the warning text; §2.2), measured rather than asserted (§2.2) — **0 unclassified**. **dedupe half: the CONTENT-RATIO route is unreachable on this corpus** (in the guard's own per-arm universe, **0 of 433** value-bearing pairs — and **0 of 1,719** pairs over ALL extracted points — reach 0.9 content overlap), while the **same-source-turn** route is reachable and the quote-value guard is **decisive on exactly 1 pair** (a value-bearing point vs a non-value-bearing strict sub-quote, overlap 1.00) — so the corpus does carry evidence the guard prevents a value loss, while the leg's broad property still rests on Slice A's unit guards (§2.3) |
| **T-2** end-to-end | *aggregation-class accuracy **moves** on the same slice with the SAME reader* | **VOID AS WRITTEN** — there is no committed pre-span baseline, and the span mechanism has **no arm to flip** (it is unconditional in extraction since #8659), so "moves" is unfalsifiable rather than failed. Re-specified and re-homed to the end-to-end run **#8486** (§4) |

**No product behaviour changed by this lane.** The census reads committed
artefacts and asserts on the shipped functions; the only code-shaped additions
are a guard test and this record.

**Guard:** `tests/eval/write_path/test_value_span_census_2684.py` (registered on
`core` + `sdk` + `eval` in `config/ci-surfaces.yml` — the three surfaces its
three inputs select: `extractor_v2` -> `core`, `retrieval` -> `sdk`, the
committed receipts/fixtures -> `eval`). It re-derives the published **totals**
below from the committed receipts and **fails on drift**; §2.1's per-session
split is pinned the same way, so no published figure below is a bare reading.

---

## 0. What this measurement IS, and what it is NOT

| | |
|---|---|
| **What is measured** | whether a value-bearing point can be **re-read verbatim** from an address: the point carries `span_start`/`span_end`, the addressed text carries a value, and that text is the point's own quoted words |
| **On which code** | the **shipped** `tortoise.extractor_v2._value_verbatim_span` / `_carries_value` / `_verbatim_span_in_turn`, on the **clamped** inputs production hands them: the content trimmed to `_MAX_CONTENT` (`extractor_v2.py:7548`) and the quote to `_MAX_QUOTE` (`:7610`), while `_resolve_source_turn` deliberately receives the RAW point (`:7740`). The census applies the same two clamps (`_stamp_inputs`) — and it **imports both caps from production** (`v2._MAX_CONTENT` / `v2._MAX_QUOTE`) instead of re-typing either literal, so the addresses it reports are the ones production writes *by construction*: a change to either cap moves the census with it and reds the clamp pin below, rather than leaving the census green on addresses production never writes (measured: exactly 3 of the 217 points carry a >`_MAX_QUOTE`-char quote, 1 of them value-bearing **and** spanned, census `(0, 201)` vs production `(0, 200)`; no published count moves) |
| **Population** | the **7 sealed write-path sessions × 2 arms** (`receipts/7947-arms-2026-10-18/`), whose points are real `deepseek-v4-flash` S2 extractions — **not** the frozen 50-Q LongMemEval slice |
| **Paid calls** | **0**. No reader, no judge, no graph, no network |
| **Reader** | **none** — and none may be substituted: wave §3 freezes GPT-4o + the official judge for every product measurement |
| **Answer accuracy** | **NOT measured.** No accuracy number exists here and none may be inferred from it. The census counts whether a value is *addressable*, not whether a reader then *sums* it |
| **Derivation, not observation** | the receipts carry **no** span keys, so the census answers *"what does the shipped mechanism stamp on these committed points?"*. The reason is **structural, not chronological**: the measuring harness persists `ev2.run_s2(...)` — the S2 *model* output — and the span is stamped later, when `pt_entry` is built (`extractor_v2.py:7741-7748`, stamped at `:7756-7759`), so no re-ingest through that harness can put a span key in a receipt. (The receipts are **not** pre-#8659: they were committed `ecc7bc8ca8` on 2026-10-19, #8659 landed `2312845b6` on 2026-10-14.) Pinned by `test_the_census_is_a_rederivation_not_an_observation` so it is never published as an observed extraction rate |

---

## 1. Provenance, RE-MEASURED (the body is stale by construction)

| Body claim (verbatim only where quoted) | Measured at this worktree | Evidence |
|---|---|---|
| "extend the value-fidelity discipline … in extraction" | **landed** — `_value_verbatim_span` stamps only a VERBATIM anchor whose addressed text carries a value; the two absence routes are reported, not silent | `tortoise/extractor_v2.py:3676` (`_verbatim_span_in_turn`), `:3704` (`_carries_value`), `:3737` (`_value_verbatim_span`), call site `:7756-7759`; commits `2312845b6` (#8659), `fda19cf93` (#8966) |
| "a span-carrying schema field" | **landed** — `Point.span_start`/`span_end`, all-or-nothing, `end > start`, `>= 0`, validated on the model AND every write path | `tortoise/commit_schema.py:589-608` (fields + `_span_is_an_address`), `:445` (`validate_span`) — the field itself is #5007's (`2ff8c3c88`) |
| "the assembly surfaces the span with the point" | **landed on the read path** — the eval lane annotates the offsets onto the hit the package reads, and `package_evidence_pool` copies the kept anchor's keys; the product ask lane's hit carries `quote`/`source_turn_id`/`span_*` since #8658 | `tools/longmem_eval/retrieve.py:839-847`; `tortoise/sdk.py:30379` (the four columns are read — the `RETURN` continuation that appends `n.quote, n.source_turn_id, n.span_start, n.span_end`, six lines past the `rows = graph.query(` opener at `:30373`); `e70a5ff17` (#8658 / #9543) |
| "Requires ONE extractor re-ingest (~4 h) then cached cycles" | **true for T-2 only**; the per-question leg needs **no** re-ingest — the committed receipts + fixtures are the population (§2) | this runbook §2 |
| the `⛔` residual: the two DEGRADED fallback tiers keep lean projections | **confirmed, and it is a DECLARED boundary** — `fallback_snapshot`/`fallback_tfidf` return before the provenance fetch, so a degraded hit carries no `quote`/`span_*` and Slice A's own-source leg is inert there | `tortoise/sdk.py:30340-30356` (the `⛔ KNOWN LIMITATION` comment: it names #8658's four columns and the declared boundary) |

**Nothing in the ontology or the SDK/MCP surface is touched by this slice.**

---

## 2. T-1, the per-question leg — RUN (zero paid calls)

### 2.1 The census

112 value-bearing points across 7 sealed sessions × 2 arms; **101 (90.2%)**
carry an address satisfying all three conditions, and **11 absences** are
distributed over exactly four classes, **all four reported** — measured by
`test_every_absence_in_the_census_is_REPORTED` over this whole population, not
asserted here:

| Session | arm | value-bearing | spanned | absences |
|---|---|---|---|---|
| wp01_quarry_debug | raw | 9 | 8 | band 1 |
| wp01_quarry_debug | narrative | 10 | 9 | band 1 |
| wp02_lumen_refactor | raw | 6 | 6 | — |
| wp02_lumen_refactor | narrative | 7 | 5 | value-free quote 2 |
| wp03_ember_design | raw | 6 | 6 | — |
| wp03_ember_design | narrative | 5 | 5 | — |
| wp04_aurora_perf | raw | 9 | 9 | — |
| wp04_aurora_perf | narrative | 12 | 12 | — |
| wp05_retro_writeup | raw | 10 | 10 | — |
| wp05_retro_writeup | narrative | 11 | 10 | no turn 1 |
| wp06_quarry_rollout | raw | 10 | 10 | — |
| wp06_quarry_rollout | narrative | 5 | 1 | band 1 · no turn 3 |
| wp07_bluepeak_followup | raw | 4 | 3 | empty quote 1 |
| wp07_bluepeak_followup | narrative | 8 | 7 | value-free quote 1 |
| **total** | | **112** | **101** | **band 3 · value-free quote 3 · no turn 4 · empty quote 1** |

**Every row above is pinned, not just the totals.** `test_the_per_session_split_is_pinned`
re-derives this whole split from the committed population and fails if any single arm's
value-bearing / spanned / absence-class counts move. That matters because the totals alone are
not a sufficient guard: a corpus change can preserve `112`/`101` and all four class counts while
moving points *between* sessions, and this table is the only published figure that says **which**
session lost which class.

⛔ **The turn is the RESOLVED one, never the receipt's `source_turn_id`.**
The write path resolves the anchor from the quote first (`_resolve_source_turn`,
`extractor_v2.py:7740`) and stamps against THAT turn (`:7756`); the receipt's
field is the model's **advisory** index. Reading it straight through puts three
`wp06_quarry_rollout`/`narrative` points on turns production refuses
(quote-overlap < 0.6), which hides the resolver's E3 warning and reports them as
`band` where production reports `no_turn` — an earlier revision of this runbook
published exactly that (`band 6 · no turn 1`). Pinned by
`test_the_census_resolves_the_turn_the_way_the_write_path_does`.

### 2.2 The four absence classes — each is visible to a consumer

| class | n | who reports it |
|---|---|---|
| `band` | 3 | `_value_verbatim_span` — "resolved … by the token-overlap band, not a verbatim anchor". The band route is otherwise **silent**, and it misses by construction exactly when the value was REWRITTEN (#2817's measurement) |
| `valuefree_quote` | 3 | `_value_verbatim_span` — "the quoted text carries no value … a false address" (#8966's hole: the model quoted the context sentence) |
| `no_turn` | 4 | the **E3 anchor warning** at the resolution site (`_resolve_source_turn`), before the span branch runs |
| `empty quote` | 1 | the **resolver** (`_resolve_source_turn`), which the write path runs *before* the stamp — "unverified (empty quote) — using it" when the model index is in range, "no resolvable source turn" when it is not (this corpus's point carries no index and takes the second form, pinned in `test_extractor_v2.py`). `_value_verbatim_span` itself says nothing here — there is no verbatim text to address — but that is **one function being silent, not the class being unreported**: the absence is on the warnings a consumer already receives. Span shape pinned by `test_extractor_v2.py::test_empty_quote_model_index_path_carries_no_span` |

⚠️ **One shape is silent BY CONSTRUCTION and is not in this census**: with an
EMPTY `edus` list `_resolve_source_turn` returns `None` before any warning
(`extractor_v2.py:7034-7035`), so a value-bearing point extracted from no turns at all
would be a silent absence. Declared here rather than measured, because the
census has no such point — and the fix belongs in the resolver (warn there),
not in this record.

**What "reported" means, and what it does not.** It means a consumer of the
warnings sees the absence — every counted outcome surfaces in the warning
stream, none is a silent drop. For **two** of the four classes the warning also
names the class (`band` via "token-overlap band", `valuefree_quote` via
"carries no value"), and that per-class ownership is what
`test_every_absence_in_the_census_is_REPORTED` asserts — the test derives each
absence's class from **settled facts** (the resolved turn's own text against the
quote, and whether the addressed text carries a value), *then* requires the
warning to name that class, so the assertion cannot be satisfied by a classifier
reading the same string it checks. It does **not** mean
every warning identifies its class, nor that it is actionable in every case.
The string "no resolvable source turn" is emitted by the resolver's step-4
fail-open for **both** `no_turn` and `empty_quote`, so it names a *condition the
two classes share* — true in both (no verbatim match / no quote to resolve
against) but it identifies neither, and for the empty quote it also does not
name the *reason*. `_absence_class` never derives a class from that text:
`empty_quote` comes from the quote being empty, `no_turn` from
`turn_idx is None`. So for those two classes the identification is carried by
this table and by the census's own derivation — not by the warning text.

⚠️ **A SECOND population no census here can see, and the one emit site it hid:
MINTED OPERATOR ENDPOINTS.** A minted endpoint (`_mint_endpoint`, the #2552
mint-before-wire pre-pass) is a statement **Point**, so #2684's rule applies to
it — but the census reads the S2 receipts and minted endpoints are created in
**S5**, so none of the counts above ever spoke about them. Measured on the
shipped code before this was fixed: a value-bearing endpoint that WAS
verbatim-anchored (`src='cut latency by 20%'` against a turn holding that text)
carried `quote` + `source_turn_id` and **no** span, and its band route carried no
span **and said nothing** — the pre-pass spent its resolver call into a scratch
warnings list (deliberate: the generic "no resolvable source turn" text
describes an emitted point losing its E3 anchor, not a mint) and never called
`_value_verbatim_span`, whose own band warning needs a non-empty quote the band
route deliberately leaves empty. The mint site therefore now runs the **same**
gate and owns the absence report itself. Pinned by
`test_extractor_v2.py::TestValueVerbSpan2684::test_a_value_bearing_MINTED_endpoint_carries_the_span_or_says_it_cannot`,
not by a census row — a receipt-shaped census cannot reach an S5-created point,
and inventing a second census for it would be a parallel layer over one gate.

**The guards, and which one discriminates what.**

* `test_every_value_bearing_point_carries_a_span_or_is_a_REPORTED_class` — the
  **census**. It is the guard that catches a population/mechanism regression: the
  pinned counts above and ``unclassified == []``.
* `test_every_stamped_span_addresses_the_whole_quote_and_carries_a_value` — the
  **span invariant**, over all 101: in bounds and forward, the addressed text is
  the point's WHOLE quote (normalised **equality**, not containment), and an
  **independent** value detector in the guard (not `_carries_value`, which is
  the predicate under test) finds a value in it. Measured discriminating: a
  fragment-anchor implementation fails the equality leg, and a `_carries_value`
  that regressed to always-True fails the independent leg.
* `test_the_trimmed_anchor_relaxation_is_pinned_on_both_readings` — the
  **refused relaxation** (§3), which is what stops the `band` class from being
  "fixed" with a false address.
* `test_every_absence_in_the_census_is_REPORTED` — the **absence-report** guard,
  and the failing path behind this section's "0 silent" claim: every one of the
  11 absences must carry a warning within its own class's allowed set (a stray
  warning does not count — and `empty_quote`'s allowed set contains the
  `no_turn` string, which is why that warning proves *reported* but not *which
  class*).
  Measured discriminating: deleting the resolver's step-4 absence
  warning turns 5 of the 11 silent (the 4 `no_turn` + the empty quote) and the
  test fails naming them; deleting the band-route warning turns the 3 `band`
  points silent and it fails as well.

The census is not a policy guard: it is a pinned measurement, so a lane that
changes it reads "re-run the census", which is exactly what it should do.

### 2.3 The dedupe half — one decisive pair, and the content-ratio route unreachable

"Verbatim-present **+ deduped**" is two properties. The dedupe half is Slice A's
(#2683, merged): a point and its own source collapse to ≤ `1 + max_verbatim`
slots, and a differing number/currency/negation token refuses the collapse at
ANY content overlap.

**The universe matters, so it is stated and pinned.** The guard runs inside ONE
package = one ingest = **one arm of one session**. Over that universe
(value-bearing points only, pairwise within each of the 14 session-arms):
**433 pairs = Σ C(n,2)**, and **0** of them reach 0.9 content overlap — so no
pair *could* differ in a value-critical token **on the content-ratio leg**. ⚠️ That zero is a property of the **per-arm**
universe, not of the corpus: pooling the two arms (a pool **no package ever
holds** — they are two independent ingests of the same session) does produce
≥0.9 pairs, which the guard pins as measured context so the zero is never
restated as "this corpus has no near-duplicates".

⛔ **That zero is about ONE route — and the population it is measured over is the
other half of the same trap.** `_same_fact` has a second, content-overlap-
INDEPENDENT route (`retrieval.py:2405-2413`): same `source_turn_id` **and** quote
overlap ≥ 0.75, with `_pkg_differ_value_critical(qa, qb)` consulted on the
**quotes** first. Over the population dedupe actually sees — **every** extracted
point of a session-arm, not the value-bearing narrowing this census otherwise
uses, because a hit's partners are whatever the package holds — that route is
reachable **and decisive**:

| same-turn route, per-arm | consulted | value-flagged | **guard DECISIVE** | collapses |
|---|---|---|---|---|
| all extracted points (the population dedupe sees) | 41 | 24 | **1** | 2 |
| value-bearing pairs only (the census's span population) | 11 | 8 | 0 | 1 |

⛔ **The key is the RESOLVED turn, never the receipt's `source_turn_id`** —
§2.1's rule, one leg over. Production overwrites the model's advisory index with
the resolver's answer before the point is committed (`extractor_v2.py:7740` →
`:7747`), and `_same_fact` groups on the committed field
(`retrieval.py:2403-2405`). Grouping by the advisory index instead gives
`43 / 26 / 1 / 2` over all extracted points — a population no package holds, and
this section's third population error. That reading is **published here and
pinned** (`test_the_dedupe_leg_is_measured_on_BOTH_legs` asserts
`ADVISORY_KEY_DEDUPE == {consulted 43, value_flagged 26, value_guard_decisive 1,
collapsed 2}`), so the four numbers above are re-derived rather than quoted: a
lane that switches the grouping back to the receipt's field gets a red on the
counts themselves, not merely on the divergence.

⛔ **And both quotes are the COMMITTED ones, not the receipt's raw strings** —
§2.1's rule one field over. The write path rewrites a point's `quote` to
`[:_MAX_QUOTE]` at that point's own commit (`extractor_v2.py:7610`, carried into
`pt_entry` at `:7745`) and `_same_fact` compares the committed hit
(`retrieval.py:2407-2408`), so the census reads its quotes through
`_stamp_inputs` — the same clamp the span half applies. The population differs on
**3 of the 217 points** (exactly the three carrying a >200-char quote) and
**every published count is clamp-invariant on it**: the table above
(41/24/1/2 and 11/8/0/1) and the advisory reading (43/26/1/2) reproduce
byte-identically with the clamped quotes, so the four/seven numbers are
unaffected while the INPUTS are now the ones a package holds. That invariance is
why the pin is synthetic rather than corpus-shaped:
`test_the_dedupe_leg_reads_the_COMMITTED_quote_not_the_raw_receipt` drives one
constructed pair whose raw and committed readings DIVERGE (a value token in the
truncated tail), so a revert to the raw field reds instead of staying green on a
corpus that cannot tell the two apart.

**The one decisive pair is `wp07_bluepeak_followup`/narrative, turn 14**: a
value-bearing point paired with a strict SUB-quote that carries no value, at quote
overlap **1.00**. Delete `if _pkg_differ_value_critical(qa, qb): return False`
(`retrieval.py:2411-2412`) and the overlap gate collapses those two into one slot
— so the guard changes exactly one outcome on this corpus, and that outcome is a
value-bearing claim losing its own words. ⚠️ The value-bearing-only row is the
**weaker** reading and its zero is a property of the narrowing, not of the
corpus: it excludes precisely the pair the guard is decisive on, because that
pair's partner is not value-bearing. It was published as the leg's reach for one
round and that was wrong; it is kept here, labelled, so the two readings can never
be confused again.

So: the **content-ratio** route is unreachable here (0 of 433 value-bearing pairs,
and 0 of 1,719 over all extracted points, so the zero is not an artefact of the
filter either), and the **same-turn** route is reachable with the quote-value
guard decisive once — this corpus carries **one** piece of evidence that the guard
prevents a value loss, which is a unit-guard-scale observation, not a distribution.
Pinned by `test_the_dedupe_leg_is_measured_on_BOTH_legs` (`SAME_TURN_DEDUPE` for
the population dedupe sees, `SAME_TURN_DEDUPE_VALUE_BEARING` for the narrowing),
so neither "cannot be exercised" nor "decides nothing" can be written here again.

So the property, as a property, rests on Slice A's unit guards —
`tests/test_evidence_assembly.py` (300-vs-400 survives as its own slot; the
`_pkg_differ_value_critical` ratio guard): one decisive pair on real extractions
is corroboration of the guard, not a distribution, and it is not a licence to
read "deduped: PASS" off this census. Pinned here by
`test_the_dedupe_leg_is_measured_on_BOTH_legs`.

---

## 3. The relaxation a future lane will reach for — MEASURED, and refused

The `band` class is the visible cost of the strict anchor: a quote rewritten by
a word keeps no address even though the value IS verbatim in the turn —

```text
quote: "The cutoff stays September 30 as agreed"
turn:  "Cutoff stays September 30 as agreed, and the lease migration already landed …"
```

The obvious "improvement" is to trim the quote's leading/trailing tokens until a
verbatim run is found. That relaxation is **refused**, but the measurement has
two readings and only one of them is production — stated here so the refusal
rests on what was actually measured:

**On the SHIPPED population (resolver first), the 3-point `band` class yields 2
candidates, and BOTH are whole-assertion clean:**

| candidate | verdict |
|---|---|
| `"two workers grabbed the same batch twice from the ingest queue and each marked its own copy complete."` | **clean** — the whole assertion, verbatim |
| `"Cutoff stays September 30 as agreed"` | **clean** — the value, whole |

So this corpus does **not** make the relaxation look unsafe on the population
production produces. The refusal rests on (a) the fragment SHAPE below, which is
reachable and was measured, and (b) #8966's unit guards — **not** on a fragment
rate in this census. Saying otherwise would be the same population mistake
§2.1 corrects.

**On the ADVISORY-INDEX reading** (the receipt's `source_turn_id`, which
production never uses), the same relaxation produces 3 more candidates, and
**all 3 are fragments**:

| candidate | verdict |
|---|---|
| `"four shards"` | **fragment** — one of the point's two quantities; a consumer cannot tell which value it is summing |
| `"One flag"` | **fragment** — a number WORD that is a determiner, not a quantity |
| `"One"` | **fragment, wrong address** — the token search matches a shared word at the **turn's start**, in a sentence the point never quoted: **#8966's false-address class, reintroduced** |

Those three points are exactly the ones the shipped resolver reports as
**unresolvable** (they are 3 of the 4 `no_turn` absences), so a lane reaches the
fragments only by applying the relaxation to the raw model index instead of the
resolved turn — the population mistake §2.1 corrects, and the reason the
relaxation stays refused rather than adopted. Pinned by
`test_the_trimmed_anchor_relaxation_is_pinned_on_both_readings`, which carries
both measured candidate sets — a lane that "improves" the anchor hits a red test
that names the reason instead of silently shipping a false address.

---

## 4. T-2, the accuracy leg — VOID as written, re-specified, re-homed

**T-2 is not failed; it is unfalsifiable.** *"accuracy **moves**"* names a
delta, and a delta needs two arms under one frozen reader:

1. **There is no committed pre-span baseline.** The wave's §7 receipts
   (`lme-p50-*`) were never committed — `git ls-files` carries none, and the
   worktree that held them is gone. (This is the same defect that voided Slice
   A's T line; #8043 records it for Slice A.)
2. **There is no arm to flip.** Since #8659 the span is stamped
   **unconditionally** in extraction — no tri-state seam exists (unlike Slice
   A's `TORTOISE_LME_EVIDENCE_ASSEMBLY`), and `grep` finds no env read in the
   span path. A "with/without" comparison would need the pre-#8659 extractor
   reinstated, which is a product decision, not a lane's.

**Re-specified:** the leg becomes *aggregation-class accuracy on post-span
graphs, as the first committed number of that class, on the frozen reader* —
and it can only be reported as a delta once such a number exists. That run is
**#8486** (the LongMemEval-S end-to-end run, PENDING, open), which already owns
the frozen reader + official judge; the re-home is recorded on that issue.

⚠️ Anyone who reports an aggregation number must state the ingest mode next to
it (#8486's own rule): the `deterministic` ingest creates no
operators/NANDs, so it measures a raw-content baseline, not Tortoise.

---

## 5. Reproduce

```bash
# the census + the three invariants (hermetic; needs the carve-out because the
# suite's session gate requires a DB URI by default):
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
    tests/eval/write_path/test_value_span_census_2684.py -q -p no:cacheprovider
# 12 passed

# the mechanism's own unit pins:
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_extractor_v2.py \
    -q -p no:cacheprovider -k "VerbSpan or verbatim_span or carries_value"
# 15 passed

# the two selection legs this file must ride:
.venv/bin/python tools/ci_selection.py --integrity   # ✅ all test files classified
printf 'tortoise/extractor_v2.py\n' | .venv/bin/python tools/ci_selection.py --changed-files -
#   surfaces ['core']  → census file present
printf 'tests/eval/write_path/receipts/7947-arms-2026-10-18/wp01_quarry_debug.arms.json\n' \
  | .venv/bin/python tools/ci_selection.py --changed-files -
#   surfaces ['core', 'eval'] → census file present
```

---

## 6. What is settled here, and what is not

**Settled.** The mechanism exists, is validated at the commit boundary, rides
the read path to the package, and on real extractions stamps a truthful
address for **90.2%** of value-bearing points with **zero silent absences** —
each of the 11 carries a warning the census attributes to its class, pinned by
`test_every_absence_in_the_census_is_REPORTED` (with the §2.2 caveat: the
`no_turn` and `empty_quote` classes both report through the resolver's *shared*
"no resolvable source turn" string, so for those two the class is carried by the
census's derivation from `quote`/`turn_idx`, not by the warning text); the
obvious relaxation is measured on both readings and refused; the degraded-tier
boundary is declared (@ `sdk.py:30340-30356`), not an oversight.

**Not settled, and it needs a home.** The accuracy movement (T-2, re-homed to
**#8486**), the *observed* — rather than re-derived — stamping rate (needs an
end-to-end re-ingest, same issue), and the **11 absences this census measures**.

⚠️ **The 11 absences are NOT tracked by #5007 any more.** #5007 ("a span link on
every Point") is **CLOSED as COMPLETED (2026-09-25T01:23:27Z)** — but only its
**FIELD** landed (`span_start`/`span_end` + `validate_span`, `2ff8c3c88`, §1).
Its **widening — every committed Point carries a span, not just a value-bearing
one — is UNSHIPPED**: `_value_verbatim_span` returns `None` before any span work
unless `_carries_value(content, quote)` (`extractor_v2.py:3777-3778`), so no
value-free point can carry an address at this head (105 of the 217 committed
points are value-free — both figures pinned by
`test_the_census_is_a_rederivation_not_an_observation`'s `TOTAL_POINTS` /
`VALUE_FREE_POINTS`; 0 of them can have a span). Do **not** read the closed
issue as covering the residual. The residual — a span on every **value-bearing**
point: `band` 3 · value-free quote 3 · no turn 4 · empty quote 1 — is recorded on
the still-OPEN **#2684** (issue comment, 2026-10-25), and the every-Point
widening is neither shipped nor owned here.
