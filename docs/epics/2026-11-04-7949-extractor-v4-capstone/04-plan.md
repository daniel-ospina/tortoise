---
title: "Epic #7949 — Implementation Plan (8 substeps)"
type: engineering
domain: platform
doc_status: live
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: extractor v4, object layer, capstone evidence, CI tiers
issue: daniel-ospina/tortoise#7949
stage: epic-plan
---

# Epic Plan — #7949 extractor v4 CAPSTONE

**Inputs:** `00-align.md` (PROCEED; GREEN bar = E1–E5 + A1–A5) · `01-research-brief.md` · `02-scope.md` (9
in-scope items, E2E-1…8, D-S1…D-S5) · `03-test-design.md` / **#11454** (17 surfaces + the CI tier rule).
**Supersedes:** `00-plan.md`, whose §1–§4 were written before research and whose §5 is now a pointer to #11454.

> **Gate grouping, disclosed.** `epic-plan` specifies one review gate **per substep**. This lane runs them as
> **four gates over adjacent substeps** — (1–4 design), (5 architecture + `duplication-architecture`), (6–7
> contracts + detailed E2E), (8 coherence + the second `duplication-architecture` re-check) — because substeps 1–4
> are deliberately thin here (this epic has no UI, no schema migration and no new user journey), and a gate whose
> artifact is a half-page section cannot catch more than a grouped gate can. **The purpose the skill states —
> "quality is caught at each step, not at a single terminal coherence review" — is preserved:** four gates, not
> one. Recorded so this is not mistaken for a skip.

---

## 1 · User Journeys

Four personas. Each is a *complete flow with entry/exit states*, and each names its edge cases — including the
ones this epic has already hit for real.

### J1 — The decider: *is v4 working, at this head?*

| | |
|---|---|
| **Entry** | owner/tech-lead asks "does the graph behave like a map yet?" |
| **Flow** | reads the ten rows on `#7949` → each row states `GREEN/RED/BLOCKED/REFUSED` + its number + the artifact it came from → follows one artifact link to the committed run → sees corpus, head sha, and the tier that produced it |
| **Exit** | a ship/no-ship call, with no number taken on trust |
| **Edge — empty** | no run at this head → the row says `UNMEASURED(<what would measure it>)`, never `0` |
| **Edge — error** | the run was refused (402 quota / G1 corpus mismatch) → the row says `BLOCKED — <reason>`, never `0` and never PASS |
| **Edge — partial** | the run scored the deterministic quantities but the model-proposed arms disagreed → both reported with the disagreement, per the repeat policy |

### J2 — The agent answering a rollup

| | |
|---|---|
| **Entry** | an agent is asked "which work items serve this goal, and what evidence supports them?" |
| **Flow** | queries the graph over the pack's declared hierarchy → gets objects + the claims attached → the answer states whether it walked **direct** or **transitive** part-whole |
| **Exit** | an answer with its evidence; or an honest "the graph has no such edges yet" |
| **Edge — empty** | zero edges ⇒ **today's** state: the correct answer is "no path", and the journey must not fabricate one |
| **Edge — cycle** | a `hasPart` cycle would make a transitive walk non-terminating or double-counting → the query carries an explicit cycle guard |
| **Edge — inherited-vs-direct confusion** | a work item two hops away must not be reported as a direct part |
| **Produced by** | W2b's scoring step (the rollup is executed against gold there); the *runtime* consumer query is an existing graph reader constrained by C3's declared reading — **no new service is built for J2** |

### J3 — The lane running the evidence (Tier E)

| | |
|---|---|
| **Entry** | a milestone completes, or a maintainer dispatches the eval lane |
| **Flow** | dispatch `evals-on-demand.yml` with the A-corpus + head → capture through `POST /v1/sessions` → census → rows → verdict gate → numbers attached to `#7949` |
| **Exit** | a verdict table at a named head, or a refusal that names why |
| **Edge — quota** | 402 at 25,000 points (`#10976`) → `BLOCKED — capture refused 402`; the run does not proceed to scoring and does not report zeros |
| **Edge — wrong corpus** | the posted payload set's hash ≠ the row's pin → **refuse** (no row scored) |
| **Edge — non-determinism** | the composer is model-proposed (D6) → deterministic quantities reported as identical-or-not, model-proposed arms reported with their disagreement |
| **Edge — empty** | the run completes and finds 0 candidates / 0 statements / 0 slots (the most likely outcome on a fresh graph) → `REFUSED (below floor: n of N)`, **never `0` reported as a result** |

### J4 — The maintainer changing a declaration (PR time)

| | |
|---|---|
| **Entry** | a PR edits `semantics`/`hierarchies` in a pack, or a criterion in the record |
| **Flow** | CI runs the **contract pins only** (sub-second, offline) → a drift reds the PR |
| **Exit** | a green PR that says nothing about whether a row moved — **by design** |
| **Edge — schema drift** | a new criterion id the verdict enum refuses → red in CI, not at a milestone |
| **Edge — the tempting mistake** | adding a merge-gating test that needs a graph → **refused by the tier rule** (#11454) |

---

## 2 · Workflows

System-level flows, not user journeys. Handoffs and failure modes named.

### W1 — PR-time contract workflow (CI tier)

```text
PR diff → ci_selection (surfaces) → CI shard → contract/unit pins only
        → pass ⇒ merge allowed        → fail ⇒ the drift is named
```

- **Handoff:** none — no human step, no budget spent.
- **Failure modes:** a pin that needs a graph (refused by the tier rule); a `durations` row missing for a new file
  (`tools/ci_selection.py --integrity` reds); a duplicate `durations` key (pinned).

### W2a — Tier E: the A-lane (hosted door, curated corpus)

```text
dispatch(evals-on-demand, corpus=a-corpus, head)
  → G0 preregistration readable            (refuse if not)   <- NEW on this lane (OWED, C2)
  → G1 posted-payload hash == the row's pin (refuse if not)   <- NEW on this lane (OWED, C2)
  → capture via POST /v1/sessions           (402 ⇒ BLOCKED, stop)
  → census (deterministic: edges, kinds, subjects, pointKind, attribution)
  → scoring (A1 vs sealed gold; A2/A3/A5 vs thresholds)
  → repeat run for the model-proposed arms  ⇒ report disagreement vs policy
  → rows → verdict gate (V1…V12) → table attached to #7949
```

- **Handoff:** the verdict table is the deliverable. The lane is `workflow_dispatch` only *once C2 ships the
  job* (§5.2a) - until then it is a credentialed operator run with the same gates.
- **Failure modes:** quota (402); corpus mismatch; gold sealed *after* the run (refuse); a model-proposed arm
  disagreeing beyond policy (**failing run**, not a footnote); a row scored below its floor (`REFUSED`).

### W2b — Tier E: the fixture arm (committed, deterministic where it can be)

```text
dispatch → capstone_run.py --prereg preregistration.json --corpus tests/eval/write_path
  → E1/E2a/E2b on the committed arm · E1b on the deterministic m2 lane
  → E4/E5 on the UNPATCHED extractor leg (the current test monkeypatches it, so it cannot certify)
  → rows → verdict gate → #7949
```

- **Why separate from W2a:** these rows' denominators (**14** value-bearing, **90** planted, **15** operator edges)
  exist **only** in the fixture — a curated payload corpus has no planted units. Two pins, two derivations, one
  record (§4.2).
- **Handoff:** the fixture author owns the corpus; the lane owns the run.
- **Failure modes:** E1b's `m2` control unproduced; the received log's corpus ≠ the frozen `fixtures_hash` (refuse);
  the patched leg being reported as the product.

### W2c — Tier E: the read-only fleet-graph census (A4)

```text
dispatch → census over live-v4 at head (READ-ONLY, no capture, no spend)
  → typed / statement / NULL shares · per-writer attribution of the typed population
  → floor n > 0 (100 % of 0 is not attribution) → row → #7949
```

- **Why its own workflow:** it captures nothing and spends nothing, so it can run when the quota is exhausted — the
  one A-row that survives `#10976`. Conflating it with W2a is what the first version did.
- **Handoff:** none; read-only.
- **Failure modes:** the graph at C1's head is not the graph measured (name the head); `NULL` folded into
  `statement`; a typed share quoted without its denominator.

### W3 — Milestone workflow

```text
roadmap milestone completes → dispatch full sweep (perf/cost + W2a + W2b + W2c in sequence)
  → performance/cost numbers recorded (never a merge gate — precedent: the perf file is already on_demand)
```

⚠️ **Corrected:** the first version described W3 as where E1/E2 and E4/E5 "happen", which left in-scope items 5, 6
and 7 with no workflow of their own. W3 **composes** W2a–W2c; it does not replace them.

- **Handoff:** **owner named - C1** (the instrument owner). **Trigger named:** a roadmap milestone completion,
  fired by **C2's dispatch job** (§5.2a) with the full phase list; recorded on `#7949` as a milestone entry.
- Corrected: the first version's handoff was "milestone owner", a role in no component list - and it narrowed the
  tier to perf/cost while `03-test-design.md` §5 assigns the milestone **both** perf/cost and the full sweep.
- **Failure mode:** a p95 on a shared runner read as a regression (the repo already names this as a flake class).

### W4 — The D3 reopen workflow (the only human-blocked flow)

```text
owner answers on #1022 (a) overrides D3 | (b) it was aimed at #4432's drift
  → relay to the decision home + ONTOLOGY §5 (OVERRIDES line if (a))
  → C7 (#10988) gains its target (or re-scopes to attribution only)
  → E2E-8 unchanged (asserts attribution, never a count)
```

- **Manual intervention trigger:** the owner's answer. **Automation:** none, deliberately.
- **Failure mode:** a lane picking a typed-claim target before the answer — the forbidden adoption.

---

## 3 · Prototype (markdown diagram — non-GUI feature)

```text
                      ┌──────────────── CI (merge-gating, offline, <1s/file) ────────────────┐
   PR diff ─────────► │ registration pins · verdict engine · corpus-pin map · semantics       │
                      │ derivation · anchor boundary · registry guard · gold integrity        │
                      └──────────────────────────────┬──────────────────────────────────────┘
                                                     │ never runs here
                      ┌──────────────────────────────▼──── Tier E (dispatch only) ───────────┐
   dispatch ─────────►│ G0 prereg → G1 payload-hash == pin → capture POST /v1/sessions        │
                      │      └─ 402 quota ⇒ BLOCKED (stop)                                    │
                      │ → census (deterministic) → scoring (A1 gold / A2 A3 A5 thresholds)    │
                      │ → repeat for model-proposed arms ⇒ disagreement vs policy             │
                      │ → rows → verdict gate V1…V12 → table on #7949                         │
                      └──────────────────────────────┬──────────────────────────────────────┘
                                                     │
                      ┌──────────────────────────────▼──── milestone ──────────────────────────┐
                      │ full sweep: perf + cost + all rows re-derived                          │
                      └───────────────────────────────────────────────────────────────────────┘

  Two pins, one record:   A-corpus (curated, hash-pinned)  +  fixture tests/eval/write_path
  Sealed before the run:  gold/{direct,transitive}.json  +  labeller identity
```

**All states represented** — ⚠️ *corrected: the first diagram drew only two of the five.* Each branch is now an
explicit exit on the Tier-E box:

| state | when | where in the diagram |
|---|---|---|
| **GREEN / RED** | scored, above floor, test passed/failed | the rows → verdict gate arrow |
| **`BLOCKED(402)`** | quota at the door | the capture step's exit |
| **`BLOCKED(corpus mismatch)`** | G1: posted-payload hash ≠ the row's pin | the G1 gate's exit — *no row scored* |
| **`REFUSED(below floor: n of N)`** | the population is under the row's floor | the census step's exit |
| **`UNMEASURED(<what>)`** | no run at this head | the rows box's exit |
| **composer** | the model-proposed arm disagreed beyond policy | the repeat step's exit — a **failing run** |

**Diagram scope, stated:** this is the **measurement pipeline**. The governance flows (the D3 reopen, §2 W4) are not
drawn; they produce no graph state.

---

## 4 · Data Model

The epic's "schema" is **the pinned registration** and **the verdict table** — not a database migration.

### 4.1 The registration gains A-rows (amendment, not a new file)

`criteria[]` gains five entries. ⚠️ **Corrected after review: "mirrors the E-rows" is the whole requirement, and the
first version dropped two required fields.** Every existing criterion carries **`threshold_basis`** and
**`measurement_visible_to_author`** (with a `_basis` when false) — the checker treats a missing one as a hard
failure, not a skip — so the A-rows carry them too, and §6 I1 freezes the *complete* set:

| field | A1 | A2 | A3 | A4 | A5 |
|---|---|---|---|---|---|
| `id` | `A1` | `A2` | `A3` | `A4` | `A5` |
| `metric` | edges of declared types + rollup precision | attach/mint/held distribution | unlinked share + both S5 jobs | attribution of typed population | subjects + 4 families + 2 readers |
| `denominator` | transitive label set | definite-description candidates | statements written by the run | typed population of the named graph | bindable subject slots |
| `floor` (+ `kind`) | ≥30 pairs · *sample sufficiency* | ≥30 candidates · *sample sufficiency* | ≥30 statements · *sample sufficiency* | n>0 · *non-vacuity* | ≥5 slots · *constructibility* |
| `threshold` | precision ≥ 0.80 **and** direct-set reachability | attach > mint, 100 % classified | share lower **and** component arm positive | 100 % attributed | families non-zero **and** readers non-empty |
| `corpus_id` | `a-corpus` | `a-corpus` | `a-corpus` | `fleet-graph` | `a-corpus` |
| floor number (read from `denominator_floors`) | `a1_labelled_pairs: 30` | `a2_candidates: 30` | `a3_statements: 30` | `a4_typed_points: 1` | `a5_slots: 5` |
| `threshold_basis` | the ~10 pp resolution argument (`EXTRACTOR-V4-ARCHITECTURE.md:1197`) | same | the owner's own 9,910/15,534 baseline | "100 % of 0 is not attribution" | presence question, not a statistic |
| `max_realizations` | 2 | 2 | 2 | 1 (read-only) | 2 |
| `measurement_visible_to_author` | **`false`** — no prior A-row measurement exists (the honest answer) + the basis that says so | `false` + basis | `false` + basis | `false` + basis | `false` + basis |

⛔ **P1 from the substep 6–7 gate: `pinned_by` was about to recreate the P0 it just fixed.** The **retained**
`corpus` block carries `pinned_by` as **free prose** (*"fixture revision (fixtures_hash), not a graph dump"*), while
`corpora.fixture.pinned_by` is a **machine token** — and `CROSS_CHECKED_CORPUS_KEYS` does **not** include
`pinned_by`, so the two can never be reconciled and G1 dispatching on the token would match no kind on the retained
block. ⇒ **three requirements, all in the same commit:** (i) `pinned_by` is a **closed 3-value enum** (I2);
(ii) the retained `corpus.pinned_by` is **re-cut to the token**; (iii) **`pinned_by` joins
`CROSS_CHECKED_CORPUS_KEYS`** in the same commit, so the token itself is cross-checked rather than trusted.
⚠️ **One decision, not two.** An earlier revision of this subsection recommended "dispatch on the presence of the
hash key, never on `pinned_by`" while §4.2 **decided** to dispatch on `pinned_by` — opposite instructions to the same
owner. **The decision is §4.2's: dispatch on the token**, and (iii) is its companion requirement, not an
alternative to it.

**Where a floor lives, and who reads it.** ⚠️ Today the only machine-readable floor home is the record's root
**`denominator_floors`** map, cross-checked key-for-key against `pins.denominator_floors` — *"every REFUSED boundary
the run probe enforces comes from it"*. A per-row `floor` field would be **prose that nothing enforces**, which is
precisely the risk D-S4 exists to close. So the A-floors are added **to that map**, with named keys:

```json
"denominator_floors": { "a1_labelled_pairs": 30, "a2_candidates": 30, "a3_statements": 30,
                        "a4_typed_points": 1, "a5_slots": 5 }
```

and **one spelling**: `floor_kind` (never `kind`) — the field names the KIND of floor (*sample sufficiency* /
*non-vacuity* / *constructibility*) and carries no number.

⛔ **P1 from the substep-8 gate: the five keys must land in BOTH homes or C1's first commit is a hard red.** The
checker fails closed on any record `denominator_floors` key with **no** counterpart in
`PRE-REGISTRATION.md`'s fenced `preregistration-pins` block — *"the record may not invent a REFUSED boundary the
document does not state"* — and that fence holds exactly the five E-keys today. So the A-floors go into **the
record's `denominator_floors` AND the document's `denominator_floors:` block, with `document_sha256` re-cut, in the
same commit**. Without that clause, D-S4's purpose silently inverts: the A-floors are dropped to make the gate green
and *"an A-row with no floor can pass on three objects"* returns.

⛔ **P1 from the substep 6–7 gate: a SECOND bundle shares `capstone-preregistration/v1`, and the checker runs on
both in ONE CI step.** `python-ci.yml:512` invokes the checker with `--dir docs/evidence/7949-capstone … --dir
docs/evidence/5087-hypothesis`, and `5087-hypothesis/preregistration.json`'s criteria carry **only**
`{id, metric, denominator, threshold}` — no `threshold_basis`, no `measurement_visible_to_author`, and its `corpus`
has **no `pinned_by` at all**. So §4.1's "every existing criterion carries them — a missing one is a hard failure"
is true **only because the checker's arms are bundle-scoped**; written into I1 as a bare requirement, **six sibling
criteria red**. ⇒ **I1's field set is BUNDLE-SCOPED**, `5087-hypothesis` is named as a **consumer** of the schema in
I1/I2, and the plan states explicitly that the A-row fields are **not** required of it. This is also the real
versioning answer: "only a breaking change bumps it" must first ask **who else reads the schema** — and a live second
bundle is the answer.

**`corpus_id` on the E-rows too.** I1/I2 make it the per-row key, so all **twelve** rows carry it
(`fixture` for the seven E-rows, as the D-S1 matrix says) — otherwise the frozen contract would fail the seven rows
it was written to protect.

### 4.2 Two corpus pins in ONE record

```json
"corpus":  { "path": "tests/eval/write_path", "pinned_by": "fixtures_hash", "fixtures_hash": "…" },   // UNCHANGED
"corpora": {
  "fixture":  { "path": "tests/eval/write_path", "pinned_by": "fixtures_hash", "fixtures_hash": "…",
                "derived": ["sessions","seed","planted_units","planted_operator_edges"] },
  "a-corpus": { "path": "docs/evidence/7949-capstone/a-corpus", "pinned_by": "payload_sha256",
                "payload_sha256": "…", "supplied_by": "POST /v1/sessions",
                "derived": ["sessions","statements"],
                "point_budget": 0, "control_point_budget": 0 },
  "fleet-graph": { "pinned_by": "graph_head", "org": "…", "graph": "live-v4", "head": "<sha>",
                   "derived": [] }
}
```

Corrected after review: A4 cites `corpus_id: fleet-graph` while the map defined only two entries - a direct
violation of I2's own breaking-change rule - and the map's file shape could not express a live graph at all. It can
now: `pinned_by: graph_head` is the third pin **kind**, and I2 admits a non-file pin. A4 is **not** exempt from the
map; it is pinned by `(org, graph, head)` and its `BLOCKED` reason names the head.

⛔⛔ **P0, found by the `duplication-architecture` review: the fixture lane's G1 REFUSES the committed record
TODAY, and §4.2's earlier claim that "`corpus` stays ⇒ the six fixture rows are safe" is false.** The record's
`corpus` block carries **no `sha256` and no `items`**, while `probes/capstone_run.py:1184` fail-closes with *"the
pre-registration freezes no corpus.sha256 — G1 would be a no-op"*; the checker's own note records the same
(*"refuses at G1 (the registration freezes no corpus sha)"*). So **all six fixture rows** (E1, E1b, E2a, E2b, E4,
E5) **cannot be produced at this record** — W2b was built on a pin that is already refusing.
**Decision (owner: C1 for the record, C2 for the probe):** **teach G1 to dispatch on `pinned_by`** — `fixtures_hash`
for the fixture kind, `posted_payload_sha256` for the A-corpus, `graph_head` for the fleet graph — rather than
adding a `sha256`/`items` pair that duplicates `fixtures_hash`. One pin contract, one derivation per kind, and the
probe stops hardcoding one key. ⚠️ The A-corpus key is renamed **`posted_payload_sha256`** because `payload_sha256`
already means something else in this repo (`tortoise/export.py:132`, the encrypted export envelope's digest) —
a name collision in a pin is how the wrong digest gets compared.

⛔ **The first version also RETYPED `corpus` into `corpora`, which would have failed the
majority of the epic.** `capstone_preregistration_check.py` reads `record["corpus"]` and cross-checks nine
`CROSS_CHECKED_CORPUS_KEYS` against `pins.corpus`; and `capstone_run.py:1184` **fail-closes every run at G1** with
*"the pre-registration freezes no corpus.sha256"* — so removing `corpus` refuses all six fixture rows
(E1, E1b, E2a, E2b, E4, E5). ⇒ **`corpus` STAYS**, `corpora` is **purely additive**, and **one CI pin asserts
`corpora.fixture.{path,fixtures_hash} == corpus.{path,fixtures_hash}`** — otherwise the fixture is described twice
with no reconciliation, which is the R6 stale-copy class this plan itself rates *high*. A schema bump
(`capstone-preregistration/v2`) is only needed if `corpus` is ever removed; it is not, here.
`derive_corpus_facts` gains a **named second derivation** — it may not branch on shape.

### 4.3 The gold block (sealed before any rollup)

```json
"gold": { "direct":     {"path":"…/gold/direct.json","sha256":"…","labeller":"<agent or person>"},
          "transitive": {"path":"…/gold/transitive.json","sha256":"…","labeller":"…"},
          "rollup_author": "<who wrote C3's rollup query>",
          "sealed_before_any_scored_run": true,
          "sealed_before_any_scored_run_basis": "<what makes that true — mirroring written_before_any_scored_run_basis>" }
```

⛔ **Corrected after review: `labeller` alone cannot make "labeller ≠ author" checkable**, because **the rollup's
author was recorded nowhere** — so the block now carries `rollup_author` and **one pin asserts the two differ** (or
resolves the rollup file's git author and asserts inequality). A hash proves *when*; a pair of recorded names plus a
pin proves *who*. And `sealed_before_any_scored_run` carries its own `_basis`, mirroring the flag it duplicates.

### 4.4 The verdict enum must be DERIVED, not extended by hand

`capstone_verdict_check.py`'s `CRITERIA` is a **closed** tuple that refuses unknown ids.

⛔ **Corrected after review — "record ids == enum minus declared sub-rows" is FALSE today.** The record's ids are
`E1, E1b, E2a, E2b, E3, E4, E5` (7) while the enum is `E1…E5` (5), and the check's own `SUB_ROW_CRITERIA` is
`("E2",)` — which answers a **different** question ("may stand in for E2"), not "is not a verdict row". So the
subtraction set is declared **literally, in one place**:

```python
NON_VERDICT_ROWS  = {"E1b", "E2a", "E2b"}   # ride inside E1/E2, never table rows
SUB_ROW_PARENT    = {"E1b": "E1", "E2a": "E2", "E2b": "E2"}   # which row carries them
```

⛔ **P0 from the `duplication-architecture` review: the invariant as first written is FALSE.** The record's ids are
`E1, E1b, E2a, E2b, E3, E4, E5` — **there is no plain `E2` row** — so `record ids − NON_VERDICT_ROWS` = **4**, not 5;
and after the amendment 12 − 3 = **9**, not 10. The correct invariant adds the **parent** of every sub-row back:

```text
(record ids − NON_VERDICT_ROWS) ∪ SUB_ROW_PARENT.values() == enum ids
```

⛔ **And three hardcoded copies silently DROP the A-rows**: `capstone_run.py:681` (`refused_rows`) and `:748`
(`completed_rows`) both iterate `("E1","E2","E3","E4","E5")`, and `capstone_measure.py:242`/`:455` carries the same
literal — so an A-row is **never emitted**, while V2's "the table is 5 rows" still passes. **All three are in scope
for C1's change**, or the A-rows are registered and never scored.
⛔ **And the A-rows must be authored in `PRE-REGISTRATION.md` too**: the checker compares the record's criteria
against the document's table character-for-character and refuses a record criterion with no doc row — and that
document is hash-pinned by the record's `document_sha256`, so **the A-row table and the re-cut hash land in the same
commit**. §4.1 did not say that.

**Cardinality is a decision, and it moves five hardcoded numbers.** If the A-rows become verdict rows (and §1 J1's
"ten rows" says they do), then the table is **10**, and these move **in the same change**: the "table is 5 rows"
assertion (`:803`), `{len(red)}/5` (`:1350`), the `5/5` strings (`:1369`, `:1536`), and
`pins.required_criteria_rows` / `REQUIRED_CRITERIA` (the prereg gate asserts it is exactly `[E1..E5]`). **And
`blocker-states.json` must move too** — a non-green A-row reds V12 unless every blocker it cites is covered there,
which the first version of this section omitted.

### 4.5 What does NOT change

E-rows' metrics, denominators, thresholds, E1b's `m2` arm, E3's class set, and the `REFUSED`-is-neither-pass-nor-fail
vocabulary. **The A-rows are additive.**

---

## 5 · Architecture

### 5.1 The two axes

| axis | decision | why |
|---|---|---|
| **where evidence runs** | CI = contract/unit only; **Tier E = all A-row measurement** (A4 is the read-only exception: **no model, no spend**, hence its own run W2c and the one row that survives `#10976`); **milestone = perf/cost *and* a full A-row sweep** (matching `03-test-design.md` §5) | a measurement needs a graph and (mostly) a model and a budget; none can gate a merge (and `#10976` proves the door refuses) |
| **which corpus carries which row** | A-corpus for A1/A2/A3/A5; fleet graph for A4; fixture for E1/E2a/E2b/E1b/E4/E5 | the E-denominators (14 value-bearing, 90 planted) exist only in the fixture; a curated corpus has no planted units |

### 5.2 Boundaries and their failure modes

| boundary | failure mode | handled by |
|---|---|---|
| **G0** — the record is readable, on the **A-lane** | `prereg` appears **zero times** in `hosted_api.py`; the chain lives only in `capstone_run.py` | **OWED — new** · owner **C2** · refusal under I6 |
| **G1** — posted-payload hash equals the row's pin, on the **A-lane** | the check does not exist: `payload_sha256` occurs only as an export/import envelope key, and `docs/evidence/7949-capstone/a-corpus/` does not exist | **OWED — new** · owner **C2**; the record-side map is C1's (seam split: C1 = map + CI pin, C2 = run-side hash + refusal) |
| capture door (`POST /v1/sessions`) | **402 at 25,000 points** (`hosted_api.py:17368` the condition / `:17379` the raise) | `BLOCKED — capture refused 402`; never a zero, never a pass |
| registration → verdict gate | a new id the enum refuses | derive the enum (4.4) + a CI pin |
| corpus pin | a row scored against another row's corpus | per-row `corpus_id`; refuse on mismatch |
| `create_edge` (the A1 writer's boundary) | it **cannot** carry an anchor (`#5213` option 3) | documented, not fixed; E3's metric is `#9736`'s |
| model-proposed composer (D6) | non-determinism | repeat policy; disagreement above policy = failing run |
| `#11001`/`#10997`/`#10998`/`#10999` | the mechanism is not shipped yet | `blocked-by` by id; the capstone measures, it does not build (except C4's argued attach half) |
| quota root `#10976` | the ceiling holds | a blocking precondition, named in the scope boundary |

### 5.2a The Tier-E execution surface - it does not exist yet, and the plan must say so

The architecture above declares Tier E the home of all measurement. **No existing workflow can run it**, verified:

| option | why it cannot host the A-lane |
|---|---|
| `.github/workflows/evals-on-demand.yml` | **one input (`files`)**, **no `secrets.` reference anywhere**, deliberately **embedded** (`TORTOISE_TEST_CARVE_OUT: '1'`, `TORTOISE_DB_URI` unset on purpose), and it runs **pytest over test files** - while `capstone_run.py` is a script, not a test. Its own G2/G3 gates then refuse that environment (non-searchable backend; no routing LLM key). |
| `.github/workflows/eval-suite-manual.yml` | one input (`subset`), a docker FalkorDB URI - **no hosted door credentials**, so it cannot reach `POST /v1/sessions` |
| the hosted A-lane needs | the hosted API key + org/graph + a **point budget** - held by no workflow |

⚠️ **And the new job must join the register, or it is a fourth unrecorded runner.** The repo's non-gating-runner
contract is a **lane membership** statement plus a **runner register** whose comment is *"the only record that a
runner exists"* (`config/ci-surfaces.yml:10339-10357`; the lane→runner table is
`docs/ci/affected-test-selection.md:134-138`). The A-lane runs a **script** (`capstone_run.py`), not a test file, so
it **cannot be an `on_demand:` member** and `leg_coverage_issues()` **cannot see it**.
⇒ **"Tier E" names three unreconciled populations** — `on_demand:` test files (the only one any check sees),
script-driven run lanes, and this new job — and `03-test-design.md` §5 must be amended to name all three and say
which the coverage check can see. C2 owns both the job **and** its register entry.

=> **Decision (owner: C2):** a **new `workflow_dispatch` job** carrying the hosted secret and `corpus` / `head` /
`budget` inputs is **a C2 deliverable**, and **until it exists a Tier-E run is a credentialed operator lane with
exactly the same gates** - not "CI". W2a's "the lane is `workflow_dispatch` only" is corrected there: it is
`workflow_dispatch` **once C2 ships the job**, and an operator run before that.

### 5.3 Component boundaries

- **C1** owns the record + the census extension; **C2** owns the door + the A-corpus artefact; **C3–C6** own the
  four object-layer rows; **C7** attribution only; **C8** the guard; **C9** the re-derivation.
- ⛔ **The boundary, corrected after the owner's 2026-11-05 ruling: this epic is a CAPSTONE PLUS ONE IMPLEMENTATION
  COMPONENT (C10).** The earlier "three named exceptions" is false as of the ruling — C10 derives the structural
  write vocabulary **in the product write path**, which is production code. The exception list is **four**:
  **C4's attach/resolve half** (the argued capability exception) · **C2's capture-path checks** (the A-lane's G0/G1 —
  OWED, new) · **C10's derivation** (`#11484`) · **and nothing else**. C10 is therefore added to the component table
  with its boundary and its failure mode (the **live-vs-replay divergence**: a predicate admitted live but absent
  from `DERIVABLE_STRUCTURAL_RELS`/`structural_edge_carrier` is written and **dropped by `rebuild_all`**), and gets
  the contract it needs — **I10** below. **Do not leave the epic's heaviest component appearing only in R11.**
  **C3's rollup is EVIDENCE-ONLY** - a committed query the scorer runs against sealed gold, not a shipped reader -
  which is why it is not a fourth exception.
- **The declared-mechanism registry (I8):** ⛔ **the `duplication-architecture` review found that `failure_policy`
  ALREADY declares this mechanism** - its note names *"the single S3-attach/S5-connect/S6-commit transaction (I3)"*
  among its three declared parts - so a **new `s3_attach` row would split one declared mechanism across two rows**.
  **Decision: one row - `failure_policy`.** C4 records the **shipped half** in its optional `code:`/`tests:`/
  `symbols:` (the registry's own header calls those fields *"how a row records the half that IS built"*) while the
  row stays `not-implemented`, and C4's acceptance names the **reddening pin** -
  `tests/test_5095_s3_identity_policy.py::test_an_unresolved_lookup_still_mints`, whose own docstring says *"the day
  a create gate lands ... this reds, and the row must flip to `implemented`"* - plus the flip condition. **Do not**
  use `preconditions:` for composition: the file defines it as **ordering**, not "part of".
  The two planning docs disagreed (C3 vs C4); **C4 is the owner**, because `symbols:` can only be satisfied by the
  producer.

**Research check (substep 5's D11 clause).** Corrected: the first version fired no hook and inherited a skip that
does not reach it. `02-scope.md`'s Architecture skip is about *"an internal transport choice"* - it says nothing
about retry/backoff/idempotency, and `01-research-brief.md` has no coverage of it. **Honest disposition: skip,
named.** The knowledge governing §5.4 is not external - it is the repo's own capture semantics (`hosted_api.py`'s
replay path, `#3129`'s abandoned-marker rule), a stronger source than any blog post and cited in §5.4. A future lane
wanting external corroboration can fire the hook on "idempotent retry of a quota-metered capture door"; this plan
does not need it, and saying so is the skip.

### 5.3a THE OBJECT-LAYER RELATION VOCABULARY — three vocabularies, and the product claim depends on which one

**Owner direction (2026-11-05):** *"increasingly I see the product as an ontology mapper system — the business is
mapped logically."* That reframes A1, and the check found a gap that A1 as scoped does **not** close.

**Three vocabularies exist. They do not line up.**

| vocabulary | what it holds | who reads it |
|---|---|---|
| **V1 — the ontology's declared Object↔Object section** | `docs/ONTOLOGY.md` **§3.7**: exactly **two** predicates — `wasDerivedFrom` (provenance) and `hasPart` (composition, inverse traversal covers "part of") | humans; the design |
| **V2 — the ontology's enforced write vocabulary** | **§3.9** *"Valid Predicate Vocabulary (code)"*: **16 generic** predicates — `performs, produces, uses, authoredBy, ownedBy, managedBy, hasMember, holdsRole, memberOf, reportsTo, participatesIn, hasPart, related, dependsOn, references, wasDerivedFrom` — **plus the `about*` family's 6**, which `create_edge` also accepts (`_VALID_EDGE_PREDICATES`), while `create_about_edge`'s own `_VALID_ABOUT_EDGE_PREDICATES` holds **5** — it adds `aboutPoint` and drops `aboutAction`/`aboutSource` | `create_edge` and `create_about_edge` |
| **V3 — what the packs declare** | `dev` (9): `decomposesInto`(→hasPart), `hasPart`(→hasPart), `implements`(→addresses), `deploysTo`(→addresses), **`dependsOn`**(→addresses), `causes`(→supports), `fixes`(→addresses), `addresses`×2(→addresses) · `venture` (7): **`funds`**(→funds), `gatedBy`(→dependsOn), `protects`(→related), `producedBy`(→produces), `assignedTo`(→managedBy), `partOf`(→hasPart), `holds`(→holds) · `product-strategy` (8): `contains`(→hasPart), `addresses`×4(→addresses), `targets`(→addresses), `manifests`(→supports), `competesWith`(→opposes) · **every one `mechanism: IMPL` except `competesWith` (`NAND`)** — ⚠️ the `→` is the row's `semantics:`, **not** a declaration: `produces`/`related`/`managedBy` are not predicates any pack declares | `list_relations()`, the classifier's kind index, the write-path verdict |

**The three mismatches, each load-bearing:**

1. **V2 is a superset of V1 with no declared Object-level semantics.** `dependsOn`, `related`, `produces`,
   `managedBy`, `memberOf`, `reportsTo` are all **writable** between Objects, and **§3.7 declares none of them** as
   Object→Object. So the writer can express relations the ontology does not say are valid at that layer.
2. **§3.9 itself records three of them as having ZERO producers**: *"Vocabulary-only edges (valid predicates with zero
   producers): `reportsTo` …, `dependsOn` (pack-declared — dev:api dependsOn dev:database; used by
   `list_relations()` for kind expansion), and `related`"*. **The gap is self-documented** — the vocabulary exists,
   nothing emits it.
3. ⛔ **Every business predicate in V3 routes to the OPERATOR path** — and, except for the two core structural words the
   packs re-declare (`hasPart`, `dependsOn`, whose `relation_write_paths()` is `('operator','structural')`), not to the
   structural one — because each carries a **core `mechanism`** (`IMPL`, or `NAND` for `competesWith`), and
   `relation_write_paths()` binds the `operator` path on `mechanism ∈ CORE_PREDICATES`. So
   `funds`, `producedBy`, `holds`, `dependsOn`, `addresses`, `competesWith` are consumed as **epistemic IMPL/NAND edges**
   (belief propagation, EP confidence) with the pack's word as a **label** — not as structural business facts.

**And that third point is a deliberate design decision, not an accident.** §3.9 states it: *"Where relevance is
expressed: not here. Relevance belongs on the operator — by attaching an `IMPL`/`NAND` to the operator's node"*,
and `related` "carries no epistemic weight, by decision (#5025, owner ruling 2026-09-27)". So the ontology's design
is: **structure carries structure** (part-whole, provenance, org, procedural), **meaning carries the operator**
(carry/supports/opposes with confidence).

### ⛔ THE OWNER'S RULING (2026-11-05) — this settles the question §5.3a opened

> *"the core ontology is expanded by the expansion packs, so they're just an expansion. And the enforced write
> vocabulary needs to be matched to the others **automatically**, because the ontology **core+packs is the
> canonical source**. Note the packs can be toggled on/off **per graph**. The idea is extractor v4 does that."*

**This is a decision, and it removes the (A)/(B) framing below** — that framing was mine, and it was wrong in one
place: it treated the packs as a *third* vocabulary competing with §3.7, when the packs are **an expansion of the
core**. So:

- **The canonical source is `core ∪ packs`** — and because packs toggle per graph, the canonical ontology is
  **per-graph**: `effective_ontology(graph) = core ∪ installed_packs(graph)`.
- **The enforced write vocabulary must be DERIVED from it, not hand-maintained.** A static allowlist beside a
  declarative ontology is drift by construction — it is the same defect class as
  *"the design documents a substrate the code does not have"* (`#5064`), one layer up.
- **That derivation IS extractor v4's job** — the reason v4 exists, not an add-on to it.

**The drift this ruling targets, measured exactly (`§5.3a` above):**

| path | derivation | where |
|---|---|---|
| **operator / label path** | ✅ **already derived** from the packs: `declared \|= {r.get("predicate") for r in reg.list_relations()}` | `tortoise/pack_registry.py:635`, in `declared_relation_predicates()`, which `create_operator` calls at `tortoise/sdk.py:23188` |
| **structural path** | ⛔ **a static literal** — and it is **1 of FIVE declarations in one file**, and **not the one the verdict reads**: `relation_write_paths` reads **`STRUCTURAL_WRITE_PREDICATES`** (the union), while `create_edge` gates on `_VALID_EDGE_PREDICATES` | `projection/edges.py:621` (`_VALID_EDGE_PREDICATES`) · `:639` (`_VALID_ABOUT_EDGE_PREDICATES`) · `:655` (`STRUCTURAL_WRITE_PREDICATES`) · `:54` (`DERIVABLE_STRUCTURAL_RELS`) · `:1129` (`structural_edge_carrier`) · the reader, `relation_write_paths`, at `pack_registry.py:701` |
| **per-graph gating** | ✅ **the resolver exists and is THREE-VALUED** — `pack_state.graph_installed_namespaces(sdk, *, graph_name=None)`: `None` ⇒ no `:PackInstall` records ⇒ **catalog union**; a `frozenset` ⇒ recorded ∪ already-in-data; an **empty** frozenset ⇒ a configured graph whose set is deliberately empty (`#2728`), where the union fallback **must not** apply. The SDK v2 master's write gate stays ungated (`#5202`) | `tortoise/pack_state.py:1170`; caveat in `docs/EXPANSION_PACKS.md` (`#2714`/`#2728`) |

⛔⛔ **P0 from the substep-8 `duplication-architecture` pass: a derivation on `_VALID_EDGE_PREDICATES` alone would be
wrong three ways.**

1. **It is not the set the verdict reads.** `relation_write_paths` reads **`STRUCTURAL_WRITE_PREDICATES`** — the
   union — and its own comment says a predicate added or removed *"THERE moves this verdict with it"*. Deriving
   the literal instead leaves **writer accepts / verdict refuses**, silently (the verdict never raises).
   ⚠️ This exact defect was already fixed once at another layer: `pack_registry.py:719-722` records that reading
   *"only `_VALID_EDGE_PREDICATES`"* under-reported `aboutPoint`/`extractedFrom` as unwritable — **#8285 review P1**.
2. **A derived predicate must carry THREE facts, not one name**: (i) is it admitted by `core ∪ installed_packs`;
   (ii) **which verb** accepts it (`create_edge` / `create_about_edge` / `ingest`'s `extractedFrom` exemption);
   (iii) is it **record-carried** (`structural_edge_carrier` + `DERIVABLE_STRUCTURAL_RELS`) — because a predicate
   admitted live but absent from the durability axis is **written and then dropped by `rebuild_all`**, with no
   warning. That is the failure mode R11 must name, and C10's acceptance must test.
3. **The count is stated three different ways.** The plan said 15, ONTOLOGY §3.9 lists 16, the code holds **22**
   (16 generic + 6 `about*`). The fix lands **one** number, with the `about*` family called out separately.

⇒ **The derivation's home is `STRUCTURAL_WRITE_PREDICATES`** (the union the verdict already reads), extended from a
static frozenset to a **per-graph function returning the three facts per predicate** — and both readers
(`relation_write_paths`/`relation_write_verdict` and `list_relations`) consume that one function.

⇒ **A pack-declared object relation (e.g. `funds`) is rejected by the structural writer and accepted only as an
IMPL operator label.** The consequence is that the *structural* object layer can never be expanded by a pack, which
is precisely the "map of the business" gap — and the fix is not a new vocabulary, it is **making the structural
allowlist a function of the graph**: `allowed_predicates(graph) = CORE ∪ {rel.predicate for rel in
installed_packs(graph)}`, with `relation_write_verdict`'s existing `writable` computation reading it.

**A1's scope, corrected under this ruling:** *"object→object edges of the types declared by the graph's
**effective ontology** (core ∪ installed packs)"* — of which `Theme→Epic→Issue` is the **first instance, not the
whole claim**. That is what makes the result a *map* rather than a hierarchy.

**⇒ What this means for A1, stated plainly:**

- **A1 as scoped proves the HIERARCHY half only** — `hasPart` edges of a pack's declared types (`Theme→Epic→Issue`).
  That is a real and necessary result, and it is what C3 measures.
- **It does NOT prove "the ontology becomes a map of the business."** A map needs typed business relations
  (`funds`, `owns`, `dependsOn`, `blocks`), and those live in the **operator** channel, which **no extraction path
  emits** (§5.3: `extractor_v2.py` has zero `create_edge`/`create_operator` calls).
- **Two coherent routes exist, and they are mutually exclusive as a product promise:**
  - **(A) Operator route** — keep §3.7 as-is; emit business relations as **IMPL operators with the pack's
    `semantics` as label, over Object endpoints** (admitted since `#7902`). Businesses become *argued* relations:
    traversable, confidence-weighted, and honest about being claims. **Cost:** a query over the map must walk
    IMPL edges and know that a `funds` label is a business fact, not support — and §3.9's `related` ruling shows the
    ontology already worries about exactly that confusion.
  - **(B) Structural route** — extend **§3.7** with a declared Object↔Object business vocabulary (promote
    `dependsOn`, `produces`, `managedBy` from V2 into §3.7 with directions/cardinalities, and decide `funds`/`holds`/
    `addresses`/`opposes`). Then the map is structural, queryable without belief semantics, and §3.9's
    vocabulary-only note disappears. **Cost:** it is an **ontology-vocabulary decision** — ONTOLOGY §5 is canonical,
    so this needs the owner, and §3.7's two-predicate shape was itself a decision.

⛔ **This plan does not choose between (A) and (B), and must not.** It records the gap, keeps A1 scoped to the
hierarchy (which both routes need), and routes the choice to the owner — because the answer changes what "the map"
*is*, and the packs' `mechanism: IMPL` declaration currently implies (A) while the product claim reads like (B).

### 5.4 Retries / circuit breakers

Corrected after review: "retry once on 5xx" would have manufactured the silent zero this epic exists to
prevent. A re-POST of an **existing** `session_id` takes the **replay path** - it skips extraction and returns a
200 *success-shaped* receipt (`hosted_api.py:18629-18635`, receipt fields at `:18697`; the abandoned-marker code says "same-session retry would
legacy-replay (#3129)"). So a "retry" after a 5xx is not a retry of the measurement. **The contract is on the
receipt, not on the status code:**

| case | rule |
|---|---|
| retryable | `502` `503` `504`, **and `409`/`429` after the door's own `Retry-After`** - retried **once**, by re-POSTing the **SAME `session_id`** |
| terminal | `402` (quota - a refusal, not a failure), `422` (malformed payload). **`409` is NOT terminal** - the door publishes `Retry-After: 30` on it |
| replay guard | read the receipt: `meta.mode == "replayed"` **and** a `capture_ok` that is **not** false => `REFUSED(replayed, no extraction)`, **no row scored**. A re-POST is a **TRUE retry** exactly when the stored `capture_ok` is `false` - which is what `#3129`'s abandoned marker exists to write |
| breaker | **two consecutive refusals at one head stop dispatching that run** |
| census | **not** retried (a partial census is a refused run, not a best-effort one); W2c's read-only census inherits this and needs no breaker |
| model-proposed arms | re-run **once** for the repeat policy. "A third run is a policy violation" was narration - nothing counts runs. It is frozen as a **record field** (`max_realizations`) with a **CI pin over the committed run artefacts**; absent that, it is a reporting convention and the plan says so instead of calling it a violation |

⛔⛔ **P1 from the `duplication-architecture` review: the first version INVERTED this contract.** It said "retry with
a **fresh** `session_id`" - which **discards the door's own convergence machinery and mints a second session's
points**, double-minting silently. The door already owns the retry semantics: a same-session re-POST on a convergent
lane is the TRUE retry (`hosted_api.py:18111-18114`, `retry_failed_capture`, blocked for `m2`); a **replay** fires
only when `capture_ok` is `True`/legacy-`None` and returns `mode: "replayed"` (`hosted_api.py:18674-18701`; the field at `:18697`); and `#3129`'s abandoned
marker writes `capture_ok=false` **precisely so that** a same-session retry is a true retry. §5.4's citation
(`:17212-17217`) was also wrong — that is the `session_id` `max_length` comment, while the **422
empty-conversation** branch is `:18026-18030`, not the replay path. **The retry
contract is the door's; the plan declares it, it does not restate a different one.**

---

## 6 · Interfaces (contract-first)

**Nine contracts** to freeze before implementation (I1, I2, I3, I4a, I4b, I5, I6, I7, I8 — **I9** below is the
tenth and closes the rollup's answer shape). Each names its consumers, so a change is visibly breaking. ⚠️
*Corrected: the first version said "six" over a nine-row table and left I6 after I8.*

| # | contract | shape | consumers | breaking change |
|---|---|---|---|---|
| **I1** | **criterion row v2** | `{id, metric, denominator, floor_kind, threshold, corpus_id, threshold_basis, measurement_visible_to_author(+_basis), max_realizations}` - **no `floor` field**: the NUMBER lives in `denominator_floors` (the one map the run probe enforces) and `floor_kind` is the row-side label. Two enforcement sites for one number is the defect D-S4 exists to close. | `capstone_measure.py`, `capstone_verdict_check.py`, `capstone_preregistration_check.py`, the README | removing/renaming a field, or an `id` outside the derived enum |
| **I2** | **corpora map** — and `pinned_by` is a **closed enum**, not prose | `corpora{<id>: {path\|org+graph, pinned_by, <hash>, derived[]}}`; **`pinned_by ∈ {fixtures_hash, posted_payload_sha256, graph_head}`** | the same three tools + `derive_corpus_facts` | a row citing a `corpus_id` absent from the map; **a `pinned_by` outside the enum** (the retained `corpus` block's prose spelling would match no kind and refuse every fixture run) |
| **I3** | **gold block** | `{direct, transitive}` each `{path, sha256, labeller}` + **`rollup_author`** + `sealed_before_any_scored_run` + **`_basis`** | C3's scoring; the sealing pin; **R2's labeller≠author pin** | gold present without `labeller`; **a missing `rollup_author`** (then "labeller ≠ author" has nothing to compare) |
| **I4a** | **row verdict vocabulary - the gate's OWN, verbatim** | `GREEN · RED · BLOCKED · UNPREREGISTERED` (exactly `capstone_verdict_check.VERDICTS`, enforced by V3: "unknown is not a verdict"). `BLOCKED`'s reason is carried by the existing **`blocked_by`** field, **never inside the verdict string** | `capstone_verdict_check.py` (V3) | adding a fifth word (`REFUSED`, `UNMEASURED`) to this set - V3 refuses it |
| **I4b** | **display vocabulary for the `#7949` table + README - a MAPPING onto I4a** | `GREEN`->GREEN - `RED`->RED - `BLOCKED(<reason>)`->`BLOCKED` + `blocked_by` - `REFUSED(below floor: n of N)`-> a **run status on the artifact** (the record's own `below_floor`: "REFUSED, which is neither a pass nor a fail"), displayed as `REFUSED`, with the row it belongs to `BLOCKED` - `UNMEASURED(<what>)`->`BLOCKED` + `blocked_by` naming what would measure it | the README, the `#7949` table | displaying a state that maps onto no I4a verdict - i.e. a fifth *verdict* |
| **I5** | **test-tier declaration — it must UNIFY onto the two declarations that already exist, not add a third** | ⛔ **file-level ⇒ `config/ci-surfaces.yml`'s `on_demand:`** (read by `tools/ci_selection.py::leg_coverage_issues`, and the runner-register comment stays the one record of what runs where); **per-test, for a mixed file ⇒ a REGISTERED pytest marker** (`tier_e`, added to `pyproject.toml`'s `markers` and to each leg's `-m` filter — the mechanism `slow`/`live`/`integration` already use at five legs). The `Tier:` docstring survives as **human annotation only** | the **existing** enforcement: `on_demand:` + the `-m` filters. The one NEW pin is the **consistency assertion** — every `Tier: E` file is an `on_demand:` member (or carries `tier_e` on every live test), and every `on_demand:` member is `Tier: E`-annotated | ⚠️ **corrected: "none exists today" was false** — two declarations are live and enforced. A third, readerless marker would add a population no check can see (the F5 gap again) |
| **I7** | **point budget - a POINTER, not a snapshot** | the door already enforces `count + est > max_points` and already publishes the readers: `org_info` -> `nodes_used` / `max_nodes` (`hosted_api.py:12837-12850`, from `quota.count_org_usage`), limits from `quota.resolve_org_limits`, and the 402 carries `used`/`limit`/`estimate` (`quota_refusal_payload`). C2's field is **`graph_headroom_at`** (when it was read) plus the A-corpus's own consumption, **doubled** for the control | C2 declares; W2a refuses when the estimated budget exceeds the read headroom | declaring a *snapshot* that drifts from the door's live readers |
| **I8** | **declared-mechanism row** | `config/v4-mechanisms.yml`: **`id`**, **`name`**, **`declared_in`**, `state`, `code:`, `tests:`, `symbols:`, `pins:`, `preconditions:`, `tracking_issue:` **plus the marker-agreement rule** — every `declared_in:` document must carry `<!-- v4-mechanism: <id> state: … -->` and agree with the row - guarded by `tests/test_v4_declared_mechanisms.py` | C4 (the one component that builds a write-path mechanism) | a mechanism shipped with no row (**the `#5064` defect inverted**); a row missing `id`/`name`/`declared_in`; a doc marker disagreeing with its row |
| **I9** | **rollup answer** (C3's deliverable — frozen by no other contract) | `{rows[], reading: "direct"\|"transitive", intermediates[], evidence[]}` — `rows[]` are the objects the query returns, `reading` names which walk produced them, `intermediates[]` proves each inherited row is reachable through **declared** edges | C3 (author), the scorer, the census/verdict tooling, **J2's reader** | an answer whose shape two lanes can disagree on while E2E-1/E2E-3 still "pass" |
| **I10** | **`allowed_predicates(graph)` — the derived vocabulary's shape** | an ordered mapping `predicate -> {admitted_by, verb, record_carried}`: `admitted_by ∈ {core, <pack namespace>}`, `verb ∈ {create_edge, create_about_edge, ingest}`, `record_carried: bool` (durable across `rebuild_all`) | **all three writers** (`create_edge`, `create_about_edge`, `ingest`), `relation_write_paths`/`relation_write_verdict`, `list_relations`, and **C10's acceptance** | adding a predicate with no `verb` or no `record_carried` answer — a live-only edge dropped by a rebuild, silently |
| **I6** | **refusal text rule** | a refusal **names the key/row/corpus it refused** in its leading clause (the repo's existing convention) | every guard | a generic "invalid input" |

**Error responses** are the `I4a`/`I4b` pair — ⚠️ **corrected after review: the first version asserted one
vocabulary was "checked, not narrated", and the tree checks a different one.** The gate's closed set is
`GREEN/RED/BLOCKED/UNPREREGISTERED` (V3-enforced); `REFUSED` is a **run status** (`record.below_floor`), and
`UNMEASURED` exists in no tool. So: **I4a is what the gate accepts and refuses** (V3 enforces it); **I4b is what a
human reads**, and every I4b state must **map onto** an I4a verdict. A child that emits a fifth *verdict* is refused
by V3; a child that displays a state with no mapping is refused by the new I4b pin.

**Versioning:** the registration carries a schema string. The A-rows land as an **amendment** inside the current
schema (additive), and only a breaking change (retyping `corpus`) bumps it — quoting the superseded text, per
`#9736`.

---

## 7 · Detailed E2E test cases

Each is implementable as an automated test, names its **tier**, and its assertions are behavioural.

### E2E-1 · declared hierarchy reaches the graph, and a rollup answers — **Tier E**

- **Given** the A-corpus exercises dev's `Theme→Epic→Issue`, and the before-census shows 0 object→object edges
- **When** the pipeline runs through `POST /v1/sessions` and `dev` is installed on the graph
- **Then** edges of the **declared types** exist with endpoints of the **declared kinds**
- **And** the rollup returns precision **≥ 0.80** against the **transitive** gold, with every claimed inherited part
  reachable through declared intermediates (**direct** gold)
- **And** the answer names its reading (direct/transitive) and contains **no inherited part reported as direct**
- **And** a cycle in `hasPart` is refused by the guard rather than inflating the answer

### E2E-2 · attaching instead of minting, facts intact — **Tier E**

- **Given** a corpus whose statements reference things by definite description
- **When** the pipeline runs
- **Then** every candidate is recorded **attached / minted / held**, and attached+minted+held = 100 % classified
- **And** the `the <X>` share falls **and** the durable-fact count does not
- **And** a **false merge** (two distinct entities attached) is audit-visible — the reversibility argument is why
  `held` exists

### E2E-3 · a claim joins a chain and crosses a batch boundary — **Tier E**

- **Given** two sessions where the second refers to something the first recorded
- **When** both run against the same graph
- **Then** the second session's statement carries an IMPL or NAND
- **And** the **connected-component** arm is positive (the anti-gameable check), not only the cheap metric
- **And** **job 1 and job 2 are reported separately**, and each edge records **which of D6's two sets** produced it
  (derived from `batch_id`, not persisted)

### E2E-4 · the subject layer answers, or the reason is named — **Tier E**

- **Given** a corpus with agent/team activity
- **When** the pipeline runs
- **Then** `Subject` nodes exist and ≥1 of the four families is non-zero, and both customer readers are non-empty
- **And** if the run's graph is **not** the graph named in the row's pin, it is **REFUSED** (no row scored; the
  refusal names both graphs) — *"wrong graph" is not a passing outcome of the row, it is a refused run*
- **Or** for the two genuine reasons — **binder did not run** / **no bindable slots** — A5 reports the finding and
  the row is **BLOCKED** with that reason
- ⚠️ **Corrected after review:** the first version offered "wrong graph" as a *third passing branch* and then
  asserted an **issue state** (`#4889` confirmed or reopened). A test cannot assert issue state, and every outcome
  satisfied one branch — the case was unfalsifiable. `#4889`'s adjudication is **row metadata**, outside the
  assertion.
- **And** `#4889` is confirmed or **reopened** — "neither" is not an outcome

### E2E-5 · reproducible from the door, with a control — **Tier E**

- **Given** the extended registration and the A-corpus
- **When** the census runs twice at one head
- **Then** the **deterministic** quantities are identical across runs
- **And** the **model-proposed** arms are reported with their disagreement against the stated repeat policy
- **And** a run handed a corpus other than **that row's** pin is **refused**
- **And** the same payloads replayed through `TortoiseSDK.capture_session` **measurably disagree** (the control)

### E2E-6 · the volume win cannot be bought with lost facts — **Tier E**

- **Given** the A1/A2/A3 mechanisms shipped by their owners and armed
- **When** the registered volume + guard criteria are scored on the run named by D-S1's matrix
- **Then** the `^the <X>` share falls (or E1 is honestly `BLOCKED` below its floor)
- **And** E2a `≥ 13 of 14` and E2b `≤ 0.05`, with a per-claim discard audit
- **And** an E2a failure **blocks the epic** even though every other number improved

### E2E-7 · the two closed claims re-derive — **Tier E**

- **Given** the planted-operator corpus and the **unpatched** extractor
- **When** the operator measurement runs at this head
- **Then** **`15 of 15` (GREEN)** or **`N/15` reported RED** with the gap attributed — attribution is diagnosis, never a pass
- **And** endpoint refs are read **back from the graph**, and **both** tree figures (`2/15`, `5/15`) are named
- **And** E5 is scored on its **registered** bar — **`≥ 0.90` ratio AND `≥ 0.80` raw absolute** — on the reconciled
population, or blocked-by `#5087` by id. ⚠️ *Corrected: the first version quoted only `≥ 0.80`, and D-S4 forbids
re-deriving a registered threshold; surface 11's named failure mode is exactly "ratio passes while the absolute floor
fails".*

### E2E-8 · the typed population is attributable — **Tier E**

- **Given** today's typed `pointKind` population
- **When** attribution runs
- **Then** **100 %** of typed points name their writer, and typed / `statement` / **`NULL`** are reported separately
- **And** **no numeric target** is asserted (the count is the D3 reopen's)

### E2E-9 · a replayed receipt REFUSES, and a true retry is one session — **Tier E** (its contract pin is CI)

- **Given** a capture response whose `meta.mode == "replayed"` and whose stored `capture_ok` is not `false`
- **When** the run reads the receipt
- **Then** the run is `REFUSED(replayed, no extraction)` and **no row is scored** from it
- **And** a re-POST whose stored `capture_ok` **is** `false` is accepted as a **TRUE retry** — one session, no
  double-mint (this is the double-mint §5.4 exists to prevent; a wrong rule here spends real money and corrupts the
  counts)

### E2E-10 · the door at its cap reports BLOCKED, never zero — **Tier E** (its contract pin is CI)

- **Given** the graph at its point cap (the state `#10976` says will fire at acceptance)
- **When** the A-lane captures
- **Then** the run reports **`BLOCKED — capture refused 402`**, with the door's `used`/`limit`/`estimate`
- **And** **no row is scored**, no `0` is reported, and no row reads PASS

**CI-eligible counterparts (must exist, must stay sub-second):** for each Tier-E case, the **contract** half is a CI
pin — E2E-1→I1/I3/I9 (criteria + gold present and sealed + the answer's shape), E2E-2/3/4→I4 (state vocabulary),
E2E-5→I2 (per-row corpus map), **E2E-6→the E1/E2a/E2b threshold-verbatim pin** (test-design surface 1),
E2E-7→the stale-figure pin, E2E-8→the `NULL`-denominator pin, **E2E-9→the retry/replay contract pin** and
**E2E-10→the `BLOCKED(402)` state pin** (both offline over canned receipts/responses — CI-tier, needing no graph).
**A Tier-E case without its CI contract pin is not done** — that pairing is the whole point of #11454.
⚠️ *Corrected: E2E-6 had no twin in the very paragraph that makes the twin mandatory, and the two new cases
(E2E-9/E2E-10) had none either.*

---

## 7a · E6 — the sixth criterion, registered by `#11083` (added 2026-11-07)

⚠️ **This plan was built on E1–E5 + A1–A5 and MISSED a sixth acceptance row that another lane registered on the epic.**
`#11083` (*"the declared connection logic is the ontology we construct — per-pack census + the sixth acceptance row"*)
defines **E6** and asks `#7949` to carry it:

> *for each installed pack, every declared relation whose endpoints both occur in the corpus is materialised, and the
> census reports declared vs written, with the delta named.* **Before-arm: 0 written on the Object→Object class.**

**Why it matters here:** E6 is the row that measures the very gap this plan found independently in §5.3a — the
**wiring residual** (`pack_hierarchy.py` ships the applier; `extractor_v2.py` calls it **zero** times) and the
**static allowlist** (`_VALID_EDGE_PREDICATES` is not derived from core ∪ packs, so a pack predicate outside the
core's two is rejected by `create_edge`). Two lanes reached the same conclusion from different directions, which is
the strongest evidence either is right.

**Where E6 lands in this plan:**

| aspect | home |
|---|---|
| the criterion + its before-arm | registered on `#7949` by **`#11083`** (re-registration amendment, per the `#9736` model) |
| the census that reports `declared → writable → written` | **`#11083`** (`docs/evidence/<issue>/pack-relation-census.json`) |
| **`writable`** — the middle column | **`#11484`** (C10): the derivation is what makes a pack predicate writable at all |
| **`written`** — the right column | the **wiring residual**: `extractor_v2` → the applier (nearest owner **`#11237`**) |
| the row's verdict state | **`#10984`** (C3) and **`#11496`** (the closing walk) must report E6 alongside A1 |

⛔ **E6 is `RED` by construction today** (0 written), and it will stay red until the wiring lands — *not* because of
pack content. `#11083` says so explicitly and this plan adopts that reading.

---

## 8 · Coherence Review + Risk Analysis

### 8.1 Cross-substep drift check

| pair | drift risk | resolution |
|---|---|---|
| J1 ↔ §7 | J1 promises "no number taken on trust"; a case that reports a bare number breaks it | every case names its artifact + state (`I4`) |
| W2 ↔ §5.4 | the workflow retries capture; the architecture forbids retry on 402 | §5.4 wins; W2 says "402 ⇒ stop" |
| §4.4 ↔ §6 I1 | the enum is derived, but I1 freezes the row shape | both: derive from `criteria[].id`, freeze the field set |
| §3 ↔ #11454 | the prototype draws three tiers; the map lists 17 surfaces | the diagram is the *tier* view; the map is the *surface* view — one cross-reference, no second list |
| §7 ↔ 02-scope | ⛔ **the claim "1:1, no new case invented" is FALSE** — §7 has **ten** cases, the scope **eight**; §7 added **E2E-9** (F3's retry/replay contract, a money-spending one) and **E2E-10** (the 402 state) with **no scope parent** | **02-scope gains E2E-9/E2E-10**, or §7 marks them derived additions that **C2's child must carry** — either way they get an owner, or decomposition drops them |
| **§6 I1 ↔ the seven E-rows** | I1 freezes `floor_kind`, `max_realizations`, `corpus_id`; the seven E-rows carry **none of the three** today | state that `floor_kind`/`max_realizations` are **A-row-only** (or add them to all twelve) — §4.1 resolves only `corpus_id` |
| **§6 I9 ↔ §5.3 / J2** | I9 names "J2's reader" and "the census/verdict tooling" as consumers, while §5.3 says C3's rollup is **EVIDENCE-ONLY** and J2 says **no new service is built** | narrow I9's consumers to **C3 + the scorer** (the reader is the existing graph path, not a new one) |
| **§4.1 floors ↔ the checker's pin↔record contract** | the A-floors in the record alone are a **hard red** (no doc counterpart) | both homes + `document_sha256`, same commit (§4.1) |
| §2 W4 ↔ 02-scope D3 | W4 is a workflow, not a decision | W4 routes; it does not adopt |
| **§5.1 ↔ §2 W2c** | the first version's axis-1 rationale ("a measurement needs a graph, a model and a budget") is false for A4, which needs **no model and no spend** | §5.1 names A4 as the read-only exception and W2c as its own run |
| **§5.2a ↔ §2 W2a** | W2a called the lane `workflow_dispatch` while no workflow can host it | §5.2a names the missing job, its owner (C2) and the operator-run fallback |

### 8.2 Risk register

| # | risk | likelihood | impact | mitigation |
|---|---|---|---|---|
| R1 | **quota** (`#10976`) blocks every A-row | **high** (already at cap) | epic cannot close | `BLOCKED(402)` state; blocking precondition named; the epic reports rather than fakes |
| R2 | **gold fitted to the query** by the same lane | medium | A1's headline claim is void | sealed + hash-pinned + **labeller ≠ rollup author**, checked (I3) |
| R3 | **non-determinism** (D6) makes "green" unreproducible | high | a green run that cannot be re-derived | repeat policy + disagreement-vs-policy = failing run |
| R4 | **enum drift** — an A-row the verdict gate refuses *(the LOUD half; the silent half is R8)* — ⚠️ **and a SECOND bundle, `5087-hypothesis`, shares `capstone-preregistration/v1` and is invoked in the same CI step**, so a schema rule written unbundled reds six sibling criteria | medium | the row cannot land at all | derive the enum (4.4) + CI pin; **I1's field set is bundle-scoped and `5087-hypothesis` is named as a consumer** |
| R5 | **CI bloat** — nine children each adding a merge-gating test | medium | the suite the owner already called expensive | #11454's tier rule; **Tier-E marker required**; ~20 s recovered |
| R6 | **stale copies** — a claim fixed in a summary, stale in its copies | **high** (hit 3× in this lane alone) | a green claim beside a refuted copy | one vocabulary (`I4`); the repo's existing double-copy pins |
| R7 | **mechanism dependency** — `#11001`/`#10997`/`#10998`/`#10999` slip | medium | A1/A2/A3 unmeasurable | `blocked-by` by id, stated per child; the epic never builds them (except C4's argued half) |
| **R8** | ⛔ **A-rows registered but NEVER EMITTED** — the silent P0 this plan's own §4.4/§8.5-F2 found, and which R4 (loud enum refusal) does not cover: `capstone_run.py:685`/`:748` and `capstone_measure.py:242` iterate a literal `("E1"…"E5")`, so an A-row is never emitted **while V2's "table is 5 rows" still passes** | **high** | **the epic closes 5/5 having measured no A-row** — the exact failure class it exists to prevent | derive all three row lists from `criteria[].id` **plus a sub-second source pin: "no literal E-row tuple under `docs/evidence/7949-capstone/probes/`"** — the three CI pins C1 was granted do **not** assert this |
| **R9** | the fixture lane's **G1 refuses the committed record** (F1) | high | six E-rows unproducible | §4.2's token dispatch + the `CROSS_CHECKED_CORPUS_KEYS` companion (C1/C2) |
| **R10** | the new Tier-E job **joins no runner register** (F5) | medium | a non-gating runner nothing can see; "Tier E" names three populations | C2 owns the register entry; `03-test-design.md` §5 amended |
| **R11** | ⛔ **the structural allowlist is a static literal** (`_VALID_EDGE_PREDICATES`), so a pack-declared object relation is rejected by `create_edge` and can only ride as an IMPL label — the object layer therefore cannot be expanded by a pack, which is the "map of the business" gap | **certain** (measured) | the epic goes green on hierarchy and the map claim stays unproven | **owner ruling recorded (§5.3a): the write vocabulary is DERIVED per graph from `core ∪ installed packs`** — **C10** owns the derivation, on the existing `list_relations()` + `:PackInstall` machinery, not a new one |

### 8.3 Improvement opportunities (advisory, recorded not adopted)

- **IO1** — the three "capstone probe" test files (`#7949`/`#7950`/`#9889`) could share one bundle-pin harness; split
  its cost off #11454's audit rather than deciding here.
- **IO2** — `capstone_measure.py`'s 159 prose pins are the repo's most effective anti-staleness device; **extracting
  the pattern** into a reusable doc-claim linter would serve every epic. Noted, not scoped.
- **IO3** — the same question `duplication-architecture` asks at scope is asked again here at coherence; the second
  run adds the *finished* plan's surfaces (data model + interfaces + detailed E2E), which the first could not see.

### 8.5 `duplication-architecture` review — verdicts recorded (substep 5, advisory)

Ran as a **separate dispatch** after the substep-5 gate, per the skill. Result: **`ISSUES`** — 2×P0, 2×P1, 1×P2
(+1 suppressed), **DEGRADED** (the Tortoise registry unreachable: `mcp_load tortoise` → `401`, the same empty-key
expansion the scope gate recorded; repo search carried every finding). Recorded per the skill's disposition table;
**no fix loop entered**.

| # | finding | verdict | disposition — folded into |
|---|---|---|---|
| **F1** | ⛔ **P0 — the fixture lane's G1 REFUSES the committed record today** (`capstone_run.py:1184` needs `corpus.sha256`; the record has none), so **all six fixture rows cannot be produced**; and the plan added two more pin kinds beside the one key the probe reads | `unify-contract-keep-drivers` | **§4.2**: G1 dispatches on `pinned_by`; the A-corpus key renamed **`posted_payload_sha256`** (the old spelling collides with `tortoise/export.py`'s envelope digest); owners C1 (record) / C2 (probe) |
| **F2** | ⛔ **P0 — the enum invariant was arithmetically FALSE** (record ids − sub-rows = 4, not 5: there is **no plain `E2` row**); and **three hardcoded literals silently drop the A-rows** (`capstone_run.py:681`, `:748`, `capstone_measure.py:242`) while V2's "5 rows" still passes | `unify-contract-keep-drivers` | **§4.4**: the invariant gains `SUB_ROW_PARENT`; the three literals are in C1's scope; **and `PRE-REGISTRATION.md`'s table + `document_sha256` are re-cut in the same commit** |
| **F3** | ⛔ **P1 — §5.4 INVERTED the door's retry contract**: "fresh `session_id`" double-mints, while the door's TRUE retry is a **same-session** re-POST with `capture_ok=false`, and `409` carries `Retry-After: 30` (not terminal); the citation `:17212-17217` was the `session_id` `max_length` comment, not the replay path (the 422 branch is `:18026-18030`) | `unify-contract-keep-drivers` | **§5.4**: the contract is the door's (`meta.mode`, `capture_ok`, `Retry-After`), cited at `hosted_api.py:18111-18114` / `:18674-18701` (`"mode": "replayed"` at `:18697`) |
| **F4** | **P1 — a new `s3_attach` row would split a mechanism `failure_policy` already declares**, whose absence pin reds the day C4's create gate lands; `preconditions:` is ordering, not composition | `unify` | **§5.3 / I8**: **one row — `failure_policy`**; record the shipped half in `code:`/`tests:`/`symbols:`; name the reddening pin + the flip condition |
| **F5** | **P2 — §5.2a's new job is a fourth non-gating runner joining no register**, and "Tier E" then names three unreconciled populations (`on_demand:` test files · script-driven runs · the new job) that `leg_coverage_issues()` cannot all see | `unify-contract-keep-drivers` | **§5.2a**: C2 owns the **register entry** (`config/ci-surfaces.yml` `on_demand:` + `docs/ci/affected-test-selection.md`); **`03-test-design.md` §5 must name all three populations** |
| **F6** *(suppressed)* | I7 declared a budget **snapshot** that duplicates the door's live readers | `unify-contract-keep-drivers` | **I7** now points at `org_info` / `quota.count_org_usage` / the 402 payload, and declares only **when it was read** |

**Refused a place:** none — all six were adopted or unified.

**The reviewer's own line, worth carrying:** *"every P0 above lives on the boundary between the plan and the live
tree, not inside the plan … Coherence Review cannot find these because it never reads `hosted_api.py`,
`capstone_run.py:748`, or the `on_demand:` register. The incoherence is concentrated in **what the plan assumes
already works**."*

---

### 8.4 Readiness

Every in-scope item (1–9) has a §7 case or a §4/§6 contract; the tier rule is written; the four routed decisions
(D3, §13.8 Q1, `#9736`, quota) are untouched. **Ready for decomposition.**

**The amendments decomposition must apply — enumerated, because the earlier "nine amendments" resolved to nothing**
(superseded wording; the scope's review table has 5 findings, §8.5 has 6, and neither list was a decomposition input):

| # | amendment | owner |
|---|---|---|
| A1 | C3 re-scoped to **evidence**; `blocked-by #11001`; the **rollup** stays C3's (with **I9** as its shape) | C3 |
| A2 | C4 names **`#10997`** by id; remains the argued attach/resolve exception | C4 |
| A3 | C5 re-scoped to **evidence**; `blocked-by #10998 + #10999`; D-S5's two arms | C5 |
| A4 | C6 follows D-S3: measure here, **file a real mechanism gap under `#5087`** | C6 |
| A5 | C1: the **A-row criteria** (+ `threshold_basis`, `measurement_visible_to_author`, `max_realizations`), the **A-floors in BOTH homes**, the **per-row corpus map**, the **enum derived** with `SUB_ROW_PARENT`, and the **three literal row lists replaced** | C1 |
| A6 | C2: the **A-corpus artefact**, the A-lane's **G0/G1**, the **point-budget pointer**, and the **runner-register entry** | C2 |
| A7 | C7: A4 = **read-only fleet census** at a named head, `n>0`, no target | C7 |
| A8 | C8/C9: **`BLOCKED(402)` as an acceptance branch**; C9 reports **`N/15` as RED** with attribution, and E5's **full** bar | C8, C9 |
| A9 | every A-row child cites **#11454** for its tier, and carries a **CI contract twin** (E2E-9/E2E-10 included) | all |

⚠️ **And one correction to the twin rule:** E2E-2/3/4 were paired to **I4**, which freezes the state *words* and
**cannot fail** for what those cases test. They are re-paired: **E2E-2 → I1's A2 row; E2E-3 → I1's A3 row + the
`batch_id`-derivation contract (F4); E2E-4 → the `subject_binding_available` fail-open selector pin**
(`03-test-design.md` §3 already requires that selector pin). E2E-9/E2E-10 → the retry/replay and `BLOCKED(402)`
pins.
