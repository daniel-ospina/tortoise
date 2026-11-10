---
title: "Epic #7949 — extractor v4 CAPSTONE: the plan and the decomposition"
type: engineering
domain: platform
doc_status: superseded
created: 2026-11-04
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: extractor v4, expansion packs, object layer, ontology map
issue: daniel-ospina/tortoise#7949
---

# Epic #7949 — extractor v4 CAPSTONE

> ⛔ **SUPERSEDED — document level, not section level.** This is the epic's **first cut**, written
> before the research pass. **`04-plan.md` supersedes it** (its own `**Supersedes:**` line) and is the
> authoritative plan: §7a is E6's home, §8.4 lists the amendments, and `05-verify.md` is the audit.
> **Nothing below directs work** — §3, §4, §6 and C7 are retained as history only, because comments
> reference them. Concretely: the retired A4 framing (*"attribute the typed population"*,
> *"Target: 100 % attribution"*, *"⛔ BLOCKED on the D3 reopen"*) is **not** current. The owner
> answered the D3 question on **2026-11-09** (*classification is on by default; the declared kinds are
> the point*), so A4 is unblocked and carries **no percentage target** — `02-scope.md` item 5 and
> `04-plan.md` §8.4's **AM7** carry its current wording.

**This document WAS the epic's plan and decomposition.** It is the artifact the first round of child
issues derived from, kept because comments reference it. **The current plan is `04-plan.md`.**
Owner direction: `#7949` comment `6645275147`
(2026-11-04) and this lane's brief (`.lane-brief.md`, 2026-11-04).

**Head at authoring:** `641e5f1b37`. Every `file:line` below was read at that head. ⚠️ **The symbol is
authoritative and the line number is a convenience** — this repo carries a citation-rot guard
(`tests/test_extractor_v4_citation_rot.py`) for exactly that reason.

---

## 0. The question, and what this lane had to answer

The owner's question (2026-11-04):

> *Can v4 use the business logic declared in the ontology expansion packs, and otherwise connect
> objects to each other — so the ontology becomes a map of the business?*

Measured on the hosted fleet graph `live-v4` that night, it does neither:

```text
object→object edges:      []              ← ZERO, of every relation type
objects:                  5,314           (dev:code 1,030 · core:tool 646 · core:workflow 237 …)
claims attached:          23,814 aboutObject edges   ← the last hop works
pointKind:                statement 15,534 (80 %) · NULL 3,658 (19 %) · typed 109 (0.6 %)
logic edges:              IMPL 6,132 · NAND 1,810 — 9,910 of 15,534 statements carry NEITHER
Subjects:                 26              (against 19,300 points)
TAGGED edges:             0
```

⇒ **A flat set of objects, each carrying claims, with nothing above or between them.** That is a
catalogue of things, not a map of the business.

**This lane's deliverable was not the implementation.** It was the decomposition that makes the
capstone *provable*: child issues that each produce measured evidence on a real corpus.

---

## 1. Align — strategy alignment decision (reviewed, rev 5)

**Decision: PROCEED** — as a proof + decomposition epic. Its GREEN bar widens from the epic's
E1–E5 (v4 *selection*) to **E1–E5 plus the owner's five assertions A1–A5** (§3).

**Alternatives considered, and why each was rejected:**

1. **Skip the capstone; implement the missing wiring directly.** Rejected — and *not* because "we don't
   know what is missing", because after research we do (§2 R2/R3). It is rejected because the whole
   E1–E5 instrument already exists, is committed, and has produced a **verdict that is mostly RED**
   (§2 R11). Implementing without a verdict row that can turn green re-runs the failure the instrument
   was built to catch.
2. **Narrow the epic to E4 (operator edges).** Rejected twice over: E4 is one *Point-layer* edge family;
   and its true registered bar is **15 of 15**, not the epic body's stale "4/4" (§2 R11).
3. **Split the owner's five assertions into a new epic.** Rejected: same question, same pipeline.
   Splitting would let v4 close "green" while the graph stays a catalogue.
4. **Pre-capstone probe: replay a handful of real sessions by hand + one rollup query** (§14 calls this
   "the cheap test… small-sample-first"). Adopted as an **addition**, not an alternative: it becomes
   the baseline capture for A1's rollup — but a hand-replay proves a *human* can connect objects, never
   that S3/S5 do.
5. **Treat "build a capstone harness" as the work.** Rejected — **it is already built** (§2 R11). A lane
   that rebuilt it would mint a second census beside a pinned, gate-checked record.
   ⚠️ **This alternative was nearly taken:** an earlier revision of this decision chartered C1 to
   "produce one script, one pre-registration and one baseline", because research had enumerated the
   epic's *issue* children and missed the apparatus those issues built. The fresh-context reviewer
   caught it. The lesson is in R11's last line.

**Profit impact (causal chain):** the graph *is* the product. If objects never connect, the promised
rollups ("which work items serve this goal, on what evidence") cannot be answered from the graph, so
the differentiation that justifies the price does not exist. Order: it gates whether the graph can be
sold as a map at all — not a feature increment.

**Eisenhower:** IMPORTANT + URGENT (do now). P0-blocker, dated owner direction, measured baseline, and
every downstream lane assuming the object layer works is building on a catalogue.

| # | assumption | confidence | basis |
|---|---|---|---|
| A-a | The `live-v4` baseline in §0 is accurate | **high** | owner-measured 2026-11-04, per-kind breakdowns; the existing census (§2 R11) can re-derive it once the A-rows are added |
| A-b | The packs *do* declare part-whole object relations; the schema and writers admit them | **high** | §2 R1/R2 |
| A-c | The gap is the **declaration → consumer** seam, not a missing writer | **high** | §2 R2/R3 |
| A-d | A real corpus exists, reachable through the door the fleet writes through | **medium** | ⚠️ the *registered* corpus is a **fixture** and one arm runs the **`m2`** lane (§2 R11) — reconciling that is C2's job, and it is a genuine open decision, not a formality |
| A-e | `TAGGED edges: 0` is a gap | **rejected** | zero is *expected* — no extraction path writes tags (§2 R7). Not a baseline to beat; no child moves it |
| A-f | E1–E5 need a fresh instrument | **rejected** | built, pinned and gated (§2 R11) |

### 1.1 Review record — align gate (3 cycles) and decompose gate

| gate / cycle | verdict | what it changed |
|---|---|---|
| align 1 | `ISSUES` — 2×P1, 3×P2 | P1: the decision rejected the obvious alternative on a **false premise** (that no pack declares part-whole relations; three do). P1: A4 (typing) silently reverses a **recorded decision** (D3 `statement`-only). P2s: A3's mechanism is an open question, not a decision; `TAGGED 0` is not a gap; the cheap hand-replay probe was missing. |
| align 2 | `ISSUES` — 1×P1, 4×P2 | P1: the A4 fork was **malformed** (its second reading claimed the graph should show `statement` and does not — the baseline refutes it: 80 % already do). P2s: three citation errors (object-layer writer cited as the point-layer operator path; `hierarchies` mislabelled "declaration-only"; the D3 vs §13.8 Q1 warrants conflated). |
| align 3 | folded into rev 4 | all five fixed in §2 and §6 |
| decompose 1 | `ISSUES` — 4×P1, 3×P2 | **P1: the whole decomposition duplicated an existing, committed, gate-checked apparatus (`#9075`/`#9080`/`#9089`/`#9110`/`#10601`)** — see §2 R11. P1: E4's bar was stale (4/4 vs the registered **15 of 15**). P1: E2's bar was the wrong population (C8 promised `#10360`'s target while claiming it did not). P1: E3's 100 % anchor target on new edges is **impossible and would reverse an owner-approved decision** (option A, `#5199`). P2s: E3's existing population had no owner; E1 had a registered threshold and two claimants; the seam ledger listed a default-**ON** seam as default-off and a render diagnostic as a behaviour seam, and several indicators had no targets. |
| decompose 2 | recorded with this doc | all seven fixed in §3/§4 and in the child issues |

⛔ **The decompose-1 finding is the most valuable thing this lane produced**, and it is a process
lesson worth carrying: research enumerated the epic's *child ISSUES* and concluded the harness did not
exist. **Issues are not artifacts.** Ask what the closed issues *built* — here, a directory of committed
evidence, two checker tools and a pinned registration.

---

## 2. Research — the measured facts the decomposition rests on

**R1 · The packs already declare it.** `packs/dev/manifest.yaml` declares `decomposesInto`
(`dev:epic → dev:issue`, `:142`) with `semantics: hasPart`, `hasPart` (`dev:theme → dev:epic`, `:148`),
and `hierarchies: Theme→Epic / Epic→Issue (type: hasPart)` (`:193`). Also
`packs/product-strategy/manifest.yaml:144,187`, `packs/venture/manifest.yaml:365` (+ `hierarchies`
`:97,:99`), `packs/marketing/manifest.yaml:57`.

**R2 · The schema admits it, and the writer exists.**
`hasPart` is on the structural write allowlist (`tortoise/projection/edges.py:621-630`) and
`docs/ONTOLOGY.md` §3.7 types it **Object → Object**, N-ary, `dcterms:hasPart`, with inverse traversal
covering "part of" (**no separate `partOf` edge**). The writer is `TortoiseSDK.create_edge` →
`projection.edges.create_edge` (`edges.py:2899`).
⚠️ **`decomposesInto` — the predicate the packs actually declare — is NOT in that set.** Its route to a
write is its `semantics: hasPart`, and **nothing derives the writable predicate from the declaration on
the extraction path**. That is the seam.
⚠️ **`create_edge` takes no property parameter and no `**kwargs`** (`edges.py:2903-2908`): a derivation
edge minted there carries **no `sourceVersion` anchor and nothing can supply one**. That is §9.6's
boundary, pinned by `tests/test_references_boundary_5213.py`, and `#5213`'s resolution was **option 3 —
document, do not re-route**. See R11/E3 for why that constrains C3.

**R3 · The extraction lane writes no structural edge at all.** `tortoise/extractor_v2.py` contains zero
`create_edge` / `create_about_edge` / `create_operator` calls (the only occurrences of the structural
vocabulary are comments). A1 cannot be satisfied by "enabling something"; something has to emit.

**R4 · The declarations have two legs, and only one is unread.** `relation_write_paths` /
`relation_write_verdict` (`tortoise/pack_registry.py:534-585`) return **`writable`** for the relations
above (they declare `mechanism: IMPL`, so the `operator` path binds; the predicate is in the structural
set). The **`mechanism`** leg *is* read at the write door by `declared_mechanisms`
(`pack_registry.py:598-820`, matching at `:804`), consumed by `create_operator`. Unread: (i) the
**`semantics`** leg — surfaced at `:2965`, and nothing derives an op_type or direction from it; and (ii)
**`hierarchies`** — validated only (`pack_registry.py:2341-2355`). ⚠️ The operator writers (`commit_ops.py:426-429`,
`mcp_server.py:4322`) are the **Point-layer** mechanism; they are not evidence about Object→Object.

**R5 · The declared-mechanism registry is a contract, not a list.** `config/v4-mechanisms.yml` — 13 rows,
guarded by `tests/test_v4_declared_mechanisms.py`. `implemented` requires non-empty `code:` + `tests:` +
`symbols:` with every symbol *defined* at module level; `not-implemented` requires a `tracking_issue:`
**and** a `pins:` node that reds the day the absence closes; `preconditions:` orders rows and fails
closed on a cycle or an `implemented` row on an unbuilt precondition. Four rows are `not-implemented`:
`span_link` (#2684), `sufficiency_router` (#5009), `atomic_point_write` (#9879), `failure_policy`
(#5087). ⇒ **none of the owner's five assertions has a row.** That is the gap stated as an artifact.

**R6 · The DOOR is the v2 lane, and that corrects the brief.** `hosted_api._capture_lane()`
(`tortoise/hosted_api.py:16975`) returns `"v2"` **unless** `TORTOISE_SESSION_EXTRACTOR == "m2"`, so
fleet captures land in `_extract_session_v2` → `extract_session_v2` (`tortoise/extractor_v2.py`) — the
**LLM v2 lane**, not `extractor.py`'s rule extractor (that is the opt-in `m2` lane,
`_extract_session_llm`). ⚠️ **The brief's premise — "today the fleet writes through the legacy rule
extractor" — is false at this head.**
**Seam ledger, corrected** (the first version listed a default-**ON** seam as default-off):

- genuinely **default-off**: `TORTOISE_VET`, `TORTOISE_CLASSIFY_LATER`, `TORTOISE_S2_DECISION_SEAM`,
  `TORTOISE_VALUE_GATE`;
- ⚠️ **default-ON**: `TORTOISE_S4_DELTA` (`extractor_v2.py:1141` is
  `env_flag("TORTOISE_S4_DELTA", True)`, and the module says *"GARBAGE → the default (True) … the
  INVERTED default"*). It selects S4's OUTPUT contract, not whether S4 runs;
- ⚠️ **not a behaviour seam**: `TORTOISE_LABEL_ORDER` is a render diagnostic.

**R7 · `TAGGED edges: 0` is not a gap.** `TAGGED` is written only by `_sync_tags` from an explicit `tags`
value (`tortoise/projection/edges.py`), and no extraction path sets one
(`rg '"tags"' tortoise/extractor*.py` → nothing); it is documented as the family with no carrier
(`edges.py:1213`). Recorded with that annotation, never as a target.

**R8 · Two owner assertions sit on a recorded decision or an open question — ROUTED, not decided
(§6).** A4 vs **D3** (`docs/ONTOLOGY.md:975` *"extraction emits `statement` only"*, option B
2026-08-12, `#1022` MERGED). A3 vs **D6** — *"Two distinct connection sets — within-batch and to-graph,
both **proposed by the model**"* (`EXTRACTOR-V4-ARCHITECTURE.md:1400` — the **D6** row; `:1095` — "D6 as ruled"),
with §13.8 (`:2085`, under the heading at `:2057`) recording the to-graph LLM-vs-mechanical question as **OPEN** and `:2024` recording
the mechanical alternative as *"a direct challenge to D6 — D6 is the decision that stands; the challenge
is evidence for the owner, **not** a change we may make."*

**R9 · The epic body's child table is stale.** CLOSED: `#7947` `#7948` `#4894` `#2817` `#5010` `#2552`
`#4716` `#5199` `#5213` `#5214` `#2453` `#1534` `#2730` `#4240` `#4889`. OPEN: `#5087` `#5064` `#9879`
`#5009` `#2684` `#10360` `#4432` `#10976` `#5090` `#5333` `#9596`.
⚠️ **`#9736` is NOT in that list** — it CLOSED 2026-11-04T15:37Z (§6), and `blocker-states.json` still
saying OPEN is the stale pointer §6 names. ⇒ the capstone's remaining
work is **new children**, and several closed ones are the *"closed while the symptom persists"* shape
that A5 and E4 ask the capstone to adjudicate.

**R10 · The code's stage names are not v4's.** `extractor_v2.py`'s header states its stages are
`[S1] STORY SUMMARY … [S5] EMBED`, and that v4's eight-step list is the **target state**; `run_s4` is
the GAP REVIEWER (E4, "S4 merges, not replaces"), not v4's removed S4. "S5 CONNECT ran" must be
evidenced by **a named call site**, never by the string `S5` in a log.

**R11 · ⛔ THE E1–E5 APPARATUS ALREADY EXISTS AND HAS PRODUCED A MOSTLY-RED VERDICT.** *(This section
was added after a decompose-gate review caught the lane about to duplicate all of it.)*

- **The registration** — `#9075` CLOSED. `docs/evidence/7949-capstone/PRE-REGISTRATION.md` +
  `preregistration.json` (`schema: capstone-preregistration/v1`, `document_sha256`, `issue: 9075`,
  `epic: 7949`; `written_before_any_scored_run: true` with its basis stated). Pinned by
  `tools/capstone_preregistration_check.py` + `tests/test_capstone_preregistration_9075.py`.
- **The corpus** — `tests/eval/write_path`, `pinned_by: "fixture revision (fixtures_hash), not a graph
  dump"`; 7 sessions, seed 42, **90 planted units**, **15 planted operator edges**, 14 value-bearing,
  343 hedge/scope sentences of which 29 bear.
- **The criterion set, with real thresholds** — E1 `after_share <= 0.50 × before_share` (floor ≥ 30
  Objects) · **E1b** anti-gaming `>= 90 of 90` on the deterministic **`m2`** lane · E2a `>= 13 of 14` ·
  E2b fusion `<= 0.05` · E3 `= 1.000` over derived nodes (`Point` + `Event` + `Document`) · E4
  `= 15 of 15` (`e4_denominator: 15`) · E5 `>= 0.90` ratio **and** `>= 0.80` raw.
- **The instrument** — `docs/evidence/7949-capstone/probes/capstone_measure.py`, with committed run
  artefacts (`capstone-measured-<sha>.log`) and a scoring pass.
- **The verdict, with an anti-staleness guard** — `tools/capstone_verdict_check.py` (V12: a row's
  `blocked_by` is a provenance POINTER, and a pointer at CLOSED work is stale evidence by
  construction; `#10537`/`#10601` re-measured the table), reading the committed
  `docs/evidence/7949-capstone/blocker-states.json` (re-read 2026-11-02: `#5333` `#9736` `#10360`
  `#9596` `#5087` OPEN).
- ⛔ **The measured verdict, read from the SHIPPED table — not from a summary of it.**
  `docs/evidence/7949-capstone/capstone-measured-9152448d.log:105-111`:

  ```text
  E1 | BLOCKED | unmeasured | e1-e2.log          | #5333
  E2 | BLOCKED | unmeasured | e1-e2.log          | #10360
  E3 | RED     | 157/397    | capstone-measured-9152448d.log | #9736
  E4 | RED     | 2/15       | receipts/w2b-operator-2026-10-19.json | #9596
  E5 | RED     | 12/25      | e5-rescore.json    | #5087
  ```

  **`E1-E5-PROOFS.md`** (audited at `3fbca18ed`, every green run, every mutation applied-run-reverted)
  answers a *different* question — *"is there a test that executes the property end to end?"* — and the
  two must not be confused. Read together, they say:
  - **E1** — the *mechanism* is **REFUTED** (the shipped S2.2 path moves the `^the <X>` share by **exactly
    0.0**; a mutation that makes `value_gate.mechanical_verdict` discard definite descriptions moves it
    `0.600000 → 0.333333` and reds the pin), while the *row* is **BLOCKED/unmeasured** below its
    30-Object floor on **`#5333`** (the owner's destructive-half ruling). Both are true; neither alone is.
  - **E2** — **BLOCKED/unmeasured** on **`#10360`**: no committed artifact carries the registered
    denominators. The **1/14** (`#9775`) is a *reported count, not a certified one*; the loss is
    **upstream of the filter** (OFF arm 2/14 vs ON 1/14, gate drops 0), and E2b's `1/114 = 0.008772` is
    explicitly *"a proxy, NOT the criterion"*.
  - **E3** — **RED at 157/397 (= 0.395)**, and the composition explains it: **159 turn-row Points + 34
    operator Points are structurally unable to carry an anchor** (193 of 354), per the log's own
    `e3 point composition`. `:Object` mention links carry **no `sourceVersion` BY DESIGN**
    (owner-approved option A, `#5199`). ⇒ `= 1.000` is unreachable **as registered**; the re-registration
    landed (`#9736`, amendment `8c1151426`) and the bar is **still** unreachable, so the remaining item is
    a **denominator decision**, not a lane's to take.
  - **E4** — **RED at 2/15** in the shipped table; `E1-E5-PROOFS.md` **corrects it to 5/15** and says the
    correction was *applied to the row, not edited into the shipped log*. ⚠️ **Both figures are in the
    tree and both must be named** — 5/15 is the reading to beat; the log's 2/15 cell is
    superseded-not-edited. And the only operator test **monkeypatches `extract_session_v2`**, so *"a
    patched extractor cannot say whether the extractor forms the operators"*: no proof is possible today.
  - **E5** — **RED at 12/25**: `ratio 1.0833` passes the `≥ 0.90` bar while **`raw recovery 0.4800` FAILS
    the `≥ 0.80` absolute floor** — *"the ratio alone is degenerate, which is why the absolute floor
    exists"* — and the `25`-bound vs `29`-marker populations are **unreconciled**, so neither number is
    final. Blocker `#5087` (S3 selection recovery). ✅ The two halves that *are* green, as tests, are the
    falsifier's **scorer** (regenerates the committed artefact byte-identically) and **§10's write-back**;
    that is a different claim from E5's registered outcome, and it is why the plan must not call the row
    green.

⇒ **Three consequences the decomposition must honour:**

1. **Do not rebuild the instrument.** Extend it. (C1)
2. **Use the registered thresholds, not the epic body's prose.** `4/4` is stale; E4 is `15 of 15`. (C9)
3. **The registered corpus is a fixture, and one arm runs `m2`** — the exact path R6 says proves nothing
   about the fleet's graph. That is the sharpest form of the brief's door question, and it is a **real
   open decision**, not a formality. (C2)

---

## 3. Scope — the widened GREEN bar

Run the **whole** pipeline on a real corpus through the fleet's door (§2 R6) and show **all ten** rows,
each against its **registered** threshold where one exists.

| # | criterion | registered bar | fails if |
|---|---|---|---|
| **E1** | volume falls | `after_share ≤ 0.50 × before_share` (floor ≥ 30 Objects) + **E1b** `≥ 90/90` | ⚠️ row **BLOCKED** (`#5333`, below its floor); the *mechanism* is **REFUTED** — the shipped path moves the share by exactly 0.0 (§2 R11) |
| **E2** | correctness preserved | **E2a** `≥ 13 of 14` · **E2b** fusion `≤ 0.05` | ⚠️ row **BLOCKED/unmeasured** on `#10360` — no committed artifact carries the registered denominators (§2 R11) |
| **E3** | provenance anchored | `= 1.000` over `Point` + `Event` + `Document` | ⚠️ **RED at 157/397**; 193 of 354 Points are structurally unable to carry an anchor, and `:Object` links are unanchored **by design** (`#5199` option A). The re-registration landed (`#9736`, `8c1151426`) and the bar is **still** unreachable — the remaining item is a **denominator decision** (§6) |
| **E4** | operator edges wired + persisted | **`= 15 of 15`** with endpoint refs remapped | ⚠️ **RED**: 2/15 shipped, corrected to **5/15**; the only test monkeypatches the extractor, so no proof exists (§2 R11) |
| **E5** | the premise is measured | `≥ 0.90` ratio **and** `≥ 0.80` raw | ⚠️ **RED at 12/25**: ratio 1.0833 passes, **raw 0.4800 fails the 0.80 floor**; the 25-vs-29-marker populations are unreconciled (§2 R11). Only the scorer + §10 write-back halves are green |
| **A1** | **the pack's business logic is load-bearing** | *(unregistered — C1 registers it)* object→object edges of the pack's declared types + a rollup that answers | a pass still yields zero object→object edges |
| **A2** | **S3 attaches instead of minting** | *(unregistered — C1 registers it)* the attach rate, attached vs minted | the rate is not measured, or rises by dropping real subjects |
| **A3** | **S5 emits the batch's story AND the cross-batch link** | *(unregistered)* chain-join rate (today 9,910 of 15,534 = 64 % unlinked) | statements still never join |
| **A4** | **things and events carry the labels the ontology declares for them, reported per layer** — ⚠️ *rewritten by the owner's 2026-11-09 ruling; the "typing is applied / typed population attribution" framing is retired (`02-scope.md` item 5)* | appears + labelled, **per layer**, floor **≥ 1 labelled node**, the percentage **reported, not chased** | the claim layer alone is counted again (the one layer deliberately uniform — it can never fail), or a percentage target is set |
| **A5** | **Subjects are populated** | *(unregistered)* `Subject` count + the four subject-edge families + the search `subject` field | 26 Subjects for a fleet of agents plus one human |

⛔ **E1 and E2 pull against each other, and A1 can be faked by minting edges to satisfy a count.** The
pre-registered decision rule is what makes a pass meaningful — which is why the A-rows must be
**registered before any A-run**, through the same pinned record (`#9075`'s schema), not in a new
apparatus. **A green run that has not run the falsifier is not green** — and that half is already green
(E5).

---

## 4. Decomposition — MECE map

**The covering argument.** Every row of §3 is either an **object-layer assertion** (A1, A2, A3, A5 — the
owner's question and the only genuinely unbuilt part), an **instrument extension** (C1) or **door
contract** (C2) that serves all rows, or a **guard** that stops the object work from being bought with
lost facts (C8) or from resting on a stale claim (C9). A4 has **no child that chases a number** (C7 — and the "until the D3 reopen is answered" clause this sentence used to carry is retired: the owner answered D3 on 2026-11-09, see `02-scope.md` item 5). **E1–E5's instrument is not a child (§2 R11); their remaining work is
their blockers — E1 via C4, E4+E5 via C9, E2 via C8's arm and `#10360`, E3 as a reported figure plus a routed
denominator decision.**

| row | owner | why it is the only home |
|---|---|---|
| **A1** | **C3** — the EVIDENCE that declarations became structural edges **+ the rollup answer** (producer: `#11001`) | E3's *new-edge* obligation cannot be folded in any more (§2 R2: `create_edge` cannot carry an anchor; `#5213` option 3 is a documented boundary) — it is **routed to `#9736`**, not carried. `#11001` owns edge production but **not** A1's rollup clause (its body has no `query`/`rollup`/`answers`) |
| **A2 + E1** | **C4** attach instead of mint | E1's registered arithmetic is *what attach-instead-of-mint produces*; a second child would double-count. **C4 owns E1's movement** |
| **A3** | **C5** — the evidence that both S5 jobs reached the graph (producers: `#10999` Job 1 · `#10998` Job 2) | one stage, two jobs (§S5); splitting would let "job 1 done" report as progress on a question that needs job 2 |
| **A5** | **C6** Subjects | the S1–S5 spine; adjudicates the `#4889` closed-vs-symptom contradiction |
| **A4** | **C7** — the read-only per-layer label census (§8.4 **AM7**) | the "blocked on the D3 reopen" wording here is retired: the owner answered D3 on 2026-11-09, and the child may not set a percentage target |
| **E2 (and E1 non-regression)** | **C8** the anti-"extract less" guard | the only child that can falsify C3/C4/C5, gated on the **registered** E2a/E2b |
| **E4 + E5** | **C9** re-derive both stale bars | E4's registered denominator is 15 and its only test patches the extractor; E5 is **RED** on the raw floor with unreconciled populations — neither is a "confirmation" |
| **E3** | **C1** (reported) + **§6 routed** | E3's bar is unreachable as registered and its blocker **CLOSED without making it reachable**; the remaining item is a denominator decision (`#9736`'s two owed owner-gated lines), so the only lane-side duty is to **report** the figure and the composition |
| all rows (instrument) | **C1** | registers the A-rows into the **existing** pinned record and extends the **existing** census |
| all rows (contract) | **C2** | the fixture-corpus / `m2`-arm reconciliation the registered record leaves open |

### C1 · Extend the existing instrument; register the A-rows

**Serves:** all ten rows (the instrument). **⛔ Not a new census, not a new pre-registration.**
**Produces:** (a) the **A-row criteria** added to the pinned registration's schema (`#9075`'s
`capstone-preregistration/v1` — a versioned amendment with its own `document_sha256`, the way `#9736`
re-registered E3, never a silent overwrite); (b) the corresponding census rows in
`docs/evidence/7949-capstone/probes/capstone_measure.py` — object→object edges **by type and by endpoint
kind**, `pointKind` histogram (typed / `statement` / **`NULL` separately**), the four subject-edge
families + `Subject` count, and the **chain-join rate**; (c) **anchor coverage over the whole graph**
(the existing E3 population), reported and *not* re-criterionised — `#9736` owns the metric decision.
**Proof:** the extended instrument, run against `live-v4`, reproduces the owner's §0 table (including
`object→object edges []` and `Subjects 26`); the amended registration passes
`tools/capstone_preregistration_check.py`; `tools/capstone_verdict_check.py` accepts the new rows and
refuses a row whose `blocked_by` has CLOSED (V12).
**Not this child:** a second census, a second registration, or a new gate.

### C2 · The door, and the fixture-corpus reconciliation

**Serves:** all ten rows (the contract the brief calls out). **Produces:** the door named in code
(`_capture_lane()` → `_extract_session_v2`, §2 R6), the corrected seam ledger (R6), and — the sharp part
— **a decision about the registered corpus**: it is a **fixture** (`tests/eval/write_path`, pinned by
`fixtures_hash`, *"not a graph dump"*) and **E1b runs the `m2` lane**, so the registered E1/E2 verdict is
**not** a proof about the fleet's graph. Options to be argued in Scope: run the A-rows on a
fleet-captured corpus and register *that* as the A-corpus; or re-register the E-rows on the fleet's
door; or keep both and state precisely which claim each corpus supports. **Plus a negative control:**
the same corpus through a fixture path (or `m2`) must be *shown* to disagree, so "proved on a fixture"
cannot again be mistaken for proved.
**Proof:** one capture through the door, with the seam set recorded in the run metadata, that the
extended census can read; the negative control's measured difference; and the corpus decision recorded
in the registration, not in prose.

### C3 · A1 — the EVIDENCE that the pack's declared relations became edges, and that a rollup answers *(mechanism owned by `#11001`; E3's anchor obligation ROUTED to `#9736`, not carried)*

**Corpus:** one whose content exercises a **named** pack's hierarchy (dev's `Theme→Epic→Issue`, whose
predicates are `decomposesInto`/`hasPart`).
**Produces:** the **evidence** half — the registered A1 criterion (C1), the before/after census through the
fleet's door (C2), and the **rollup query** that answers from the graph, which `#11001` does **not** own
(grep its body for `query|rollup|answers` → nothing). The **edge producer itself is `#11001`'s** — this
child is `blocked-by #11001` and does not write a second one. Plus a `config/v4-mechanisms.yml` row for the
*evidence* seam only if the registry's contract can express it; the mechanism row belongs with `#11001`.
**Proof (before/after on the registered corpus, through C1's census):** `0 → N` edges of the declared
types with endpoints of the declared kinds; the rollup returns a **non-empty and correct** answer against
a hand-labelled ground truth (an edge count alone is gameable); **E1's registered arithmetic and E2a/E2b
unaffected** (C8's arm).
⛔ **Anchor obligation, corrected.** The first version of this child demanded **100 % anchor coverage on
the new edges** and told the implementer to use `create_edge`. Both are wrong: `create_edge` *cannot*
carry a `sourceVersion` (`edges.py:2903-2908`, pinned by `tests/test_references_boundary_5213.py`), and
`#5213`'s recorded resolution is **option 3 — document, do not re-route**. `:Object` mention links are
unanchored **by design** (owner-approved option A, `#5199`). ⇒ **E3's obligation for these edges is
ROUTED to `#9736`** (the metric's owner). The child must **state** which writer it uses and, if it wants
an anchored edge, name the writer that can carry one — it may not demand an anchor from a verb documented
not to take one.
**Not this child:** inventing a relation vocabulary, or making `decomposesInto` writable as itself
(ONTOLOGY §3.7: inverse traversal covers "part of").

### C4 · A2 + E1 — S3 attaches instead of minting

**Corpus:** the definite-description population (`the <X>`, 62.3 % of Objects; 37.7 % referenced by
exactly one point — `#4894`'s audit).
**Produces:** the **attach rate** (attached / minted / **held**, three buckets) as a first-class reported
number, and the resolution pass that moves it. **Owns E1's movement**, measured with the **registered**
arithmetic (`after_share ≤ 0.50 × before_share`, floor ≥ 30) **and E1b** (`≥ 90 of 90`) — a directional
"it fell" is not the criterion.
**Proof:** E1 + E1b on the registered corpus, attach/mint/held per candidate recorded, and C8's arm
unchanged (a fact lost to buy the rate voids the win).
⚠️ **State the reconciliation, because a lane holding the architecture line will read this as a
challenge to `#5333`:** E1's registered denominator is *"**all** `:Object` nodes written by the run"*, so
C4 moves the **run's** written share — not-minting removes candidates from numerator *and* denominator,
the existing `the <X>` population stays in recall, and **no relocation or delete is implied**. `#5333`'s
design ruling is not touched.
**Precondition:** `#5087`'s HOLD gate is the *mint* half (`failure_policy`, `not-implemented`). This
child consumes that seam or is **blocked by id**; it does not build a second mint gate, and `#5095`'s
lesson stands — `execute_embed({})` must not be the suppression (it is the sole carrier for retractions,
supersessions and chain notes).

### C5 · A3 — the EVIDENCE that both S5 jobs reached the graph *(mechanism owned by `#10998`/`#10999`)*

**Produces:** the **measurement** of both jobs — chain-join rate before/after, each job reported
separately, the cross-batch link observed in the graph and a dependent rollup answered — plus the registered
A3 criterion. ⛔ **The jobs themselves are `#10999` (Job 1) and `#10998` (Job 2)**; this child implements
neither. D6's division and `PER_ENTITY_FANOUT_CAP` are constraints its measurement reports compliance with,
not things it builds.
**Proof:** the chain-join rate falls on the capstone corpus; **both jobs reported separately** (a
job-1-only result is not progress); the cross-batch link is observable and a dependent rollup answers;
fan-out respected; C8 holds.
**Failure policies are asymmetric and must be respected:** job 1 **FAIL-OPEN** (nodes commit;
`connections_incomplete` recorded), job 2 **FAIL-CLOSED on the write of the affected item** (`HOLD`).

### C6 · A5 — Subjects are populated

**The contradiction to adjudicate:** `#4889` ("the Subject layer is unpopulated in production") is
**CLOSED**, and the owner measured **26 Subjects against 19,300 points**. One of those is false.
**Produces:** the binder's attach rate (`bind_point_subjects`, `tortoise/subject_binding.py:341`, called
from `sdk.py:16079`), the four subject-edge family counts, and the **customer-visible** consequence
(search `subject`, `get_org_structure`). The three candidate explanations — the binder never runs (note
`subject_binding_available` is a **fail-OPEN** probe, defined at `tortoise/search_engine.py:4552` and
consumed at `sdk.py:8766/8819` — ⚠️ `sdk.py:8952` is a *different* probe's docstring), the slots are absent in the
corpus, or the graph served is not the graph measured — are decided by evidence.
**Proof:** Subjects `26 → N` on a real corpus, four families non-zero, both customer readers non-empty —
or the honest finding that the corpus has no bindable subjects, recorded as such (**not** a pass).

### C7 · A4 — the declared labels appear on the right nodes (⚠️ superseded heading, kept as history)

> ⛔ **The rest of this section is superseded text.** The D3 question was answered by the owner on
> **2026-11-09** (*classification is on by default; the declared kinds are the point*), so this child
> is **not blocked** and carries **no percentage target**. The current wording is `02-scope.md` item 5
> and `04-plan.md` §8.4 **AM7**: **things and events carry the labels the ontology declares for them,
> reported per layer**, bar = **appears + labelled**, floor **≥ 1 labelled node**, percentage
> **reported, not chased**. The provenance of today's **109 typed points** (which writer —
> `extractor.py::_classify_point_kind` vs the v2 lane) and the typed/`statement`/**NULL** shares
> survive as read-only census inputs, not as the criterion.
>
> Superseded text, retained verbatim: *"Filed because the measurement is legitimate; no target may be
> set. Produces: the provenance of today's 109 typed points … Decides nothing. Target: 100 %
> attribution."*

### C8 · E2 (and E1 non-regression) — the guard

**The only child that can falsify C3/C4/C5.** **Operates on the REGISTERED criteria** — **E2a `≥ 13 of
14`**, **E2b fusion `≤ 0.05`** — and must state that the row is currently **BLOCKED/unmeasured**
(`#10360`: no committed artifact carries the registered denominators), reporting the **1/14** (flags-on;
2/14 OFF) as a *reported count, not a certified one*, with the finding that the loss is **upstream of the
filter** (`#9775`), plus the per-claim discard audit and atomicity.
⛔ **Two corrections from review:** (i) the `38/77 = 49.4 %` vs `49/90 = 54.4 %` strict-verbatim pair is
`#10360`'s population **and** `#10360`'s target — this child may cite it as a non-blocking note and must
not present it as its own bar; (ii) **E1's movement belongs to C4**, so this child's E1 content is the
**non-regression** arm only.

### C9 · E4 + E5 — re-derive both stale bars

**E4's registered bar is `= 15 of 15`** (`e4_denominator: 15`), not the epic body's `4/4` — that figure
is the `#2552`-era comparable (`0/4 → 4/4`), reported as history, never as the threshold. ⚠️ **Two E4
figures are in the tree and both are named:** `2/15` in the shipped verdict table and **`5/15`** in
`E1-E5-PROOFS.md`, which states the correction was *applied to the row, not edited into the shipped log*;
5/15 is the reading to beat. **E5 is RED at 12/25** (raw 0.4800 vs the `≥ 0.80` floor; ratio 1.0833
passes) with an **unreconciled 25-vs-29** population and blocker `#5087` — so this child owns E5's raw
arm too, rather than confirming a green it never had.
**Produces:** the planted-operator measurement at this head **on an unpatched leg** (the only existing
operator test monkeypatches `extract_session_v2`, so *no proof exists today*), the N/15 result with
endpoint refs read back from the graph, and a confirmation that E5's two halves are still green (the
scorer reproduces byte-identically; §10 publishes the falsifier's own figures).
**Proof:** `15 of 15` or the measured N/15 with the gap attributed (`#8503` recorded 5/14 edge-correct:
4 lost to operator selection, 5 to endpoint recall — that is the place to start).
**Root-cause discipline:** a regression **reopens** the closed issue (`#2552`/`#4716`/`#8503`), never a
peer.

---

## 5. Integration surfaces (the Test-Design Gate's output, inline)

> ⚠️ **SUPERSEDED by the Test-Design Gate's own artifact:** `03-test-design.md`, filed as **#11454** —
> 18 rows (1–17 plus `10a`, E6) each with a test layer **and a run tier** (`CI` · `Tier E` · `milestone`), a measured CI-cost
> baseline (64.3 s of 12,734.8 s), and an existing-test audit. The table below is retained only as the
> historical inline version; **children cite #11454**, not this section.

The workflow's Test-Design Gate files a *separate* test-design issue. **Here its output is this table
plus each child's own verification checklist** — the gate exists so child issues *carry* their
verification, and they do (§4). An extra issue holding a table every child cites is machinery around
behaviour.

| surface | layer | what must be true |
|---|---|---|
| the graph a real capture writes (`_extract_session_v2` → S6) | **integration, on a real graph** | C2's door is the one used; a fixture/`m2` path is *shown* to disagree |
| the pinned registration (`preregistration.json` + its checker) | contract | the A-rows enter via an **amended, versioned** record with its own `document_sha256`; no silent overwrite; the checker passes |
| the verdict gate (`tools/capstone_verdict_check.py`, V12) | contract | new rows accepted; a row whose `blocked_by` has CLOSED is **refused**; `blocker-states.json` refreshed in the same diff as the measurement |
| pack declaration → write path (`semantics`/`hierarchies` → `create_edge`) | **contract** | a declared type produces a write of the **derived** predicate; a second pack's different predicate maps without a code change |
| `relation_write_verdict` / pack load | unit | the verdict stays `writable`; a new consumer does not change it |
| `config/v4-mechanisms.yml` + its guard | config/contract | a new row passes `test_v4_declared_mechanisms` in whatever state it ships |
| version anchoring | integration, **documented boundary** | `create_edge` mints an **unanchored** derivation edge by design (`#5213` option 3); `:Object` mention links are unanchored by design (`#5199` option A); the E3 metric question is `#9736`'s |
| verbatim value survival | integration (registered arm) | **E2a ≥ 13 of 14**, **E2b ≤ 0.05**, on the registered corpus |
| Subjects → search `subject` / `get_org_structure` | e2e (customer-visible) | non-empty answers, not structurally-empty ones |
| chain join (IMPL/NAND presence per statement) | integration | the unlinked share falls; both S5 jobs reported separately |
| planted operator edges | integration, **unpatched leg** | **15 of 15**, endpoints read back from the graph |

---

## 6. Routed to the owner — two items, two different routes

> ✅ **The D3 item below was ANSWERED by the owner on 2026-11-09** (*classification is on by default; the
> declared kinds are the point*), and the answer is recorded where a lane reads it: `02-scope.md` item 5,
> `04-plan.md` §8.4 **AM7**, `05-verify.md` item 3. The fork below is kept verbatim as the history of the
> question — **do not read it as open**, and do not set the percentage target fork (a) proposed.

**REOPEN — D3 `statement`-only (`docs/ONTOLOGY.md:975`, option B 2026-08-12, `#1022` MERGED).**
A4 ("0.6 % typed is the ceiling the capstone must beat") points **opposite** to D3. A later owner ruling
may reverse an earlier one (§16.3/O4) — but the reversal must be recorded where the next lane reads it.
**The fork:**

- **(a)** A4 **overrides** D3 — typed claims must rise above 0.6 %. Then the decision home (`#1022`) and
  `ONTOLOGY.md` §5 each carry an `OVERRIDES:` line, and C7 gains a numeric target.
- **(b)** A4 was aimed at `#4432`'s docs-vs-code drift (`ONTOLOGY.md` says statement-only while
  `tortoise/extractor.py::_classify_point_kind` still classifies decision/vision/plan/goal/observation/
  hypothesis and is live). Then C7 measures where the 109 came from and implements whichever of `#4432`'s
  own two options the owner picks.

⚠️ A third reading was rejected in review and must not be reintroduced: *"the graph should show
`statement` and does not"* — the baseline refutes it (80 % already are).

**ASK — §13.8 Q1, the to-graph connection mechanism.** Recorded **OPEN**
(`EXTRACTOR-V4-ARCHITECTURE.md:2085`, `:2024`) while **D6 stands** (`:1400`, `:977`). **C5 measures conformance to D6** — the two connection sets are `#10999`/`#10998`; C5 builds neither
and reports both jobs; the answer is routed as evidence, never adopted quietly. (Distinct from `#7948`,
which answered Q2 — `aboutObject` is a **STORED** edge — and is CLOSED.)

**Third item — ⚠️ ROUTED, but its home CLOSED without making the bar reachable:** **`#9736` CLOSED
2026-11-04T15:37Z** (i.e. **before** this head), its amendment landed (`8c1151426`), and E3 is **still RED
at 157/397** because 193 of 354 Points are structurally unable to carry an anchor. `#9736`'s own closure
leaves **two owner-gated lines owed** (`#9736-spelling`, `#9736-anchor`) that *"a lane must not take"*.
⚠️ And `blocker-states.json` still lists `#9736: OPEN` (`checked_at 2026-11-02`) — **a stale pointer of
exactly the kind this apparatus exists to refuse (V12)**, so refreshing it must ride in the same diff as
the next measurement. The route is therefore: a comment on `#7949` naming the two owed lines, and a
successor home if the owner wants one. C3 may **not** satisfy E3 for its new edges by inventing an anchor.

All three are relayed by a comment on `#7949` naming the decision home each answer must land in
(DECISION RELAY), so an answer cannot survive only in a conversation.

---

## 7. Deliberate deviations from the workflow, with reasons

| workflow step | what this lane did | why |
|---|---|---|
| scope + plan human gates | one plan doc holding both, reviewed by fresh-context reviewers | the epic's direction is owner-given (two comments); the gates' purpose is that the *plan* is reviewed, and it is (§1.1) |
| Test-Design Gate files an issue | output inlined (§5) | the gate exists so children carry verification; they do (§4) |
| Capstone Verification Gate files a child | **not filed** | ⛔ circular: **this epic IS the capstone** for v4. Its closing rule (E1–E5 + A1–A5 evidence attached to the epic) is what that gate exists to guarantee — and it is already **enforced in code** by `tools/capstone_verdict_check.py` (#9089/#10601) |
| open a PR for this doc | ⚠️ **Outdated — a PR now exists: `#11685`** (`docs/7949-capstone-artifacts`). At this document's head the lane worked to PR-ready only | `INTAKE-FROZEN` applied then: **25 open PRs** at that head (bar ≥ 10). The intake unfroze and the bundle was filed as `#11685` |

---

## 8. Review record — decompose gate, cycle 2

⚠️ **Outdated.** See §1.1. Cycle 1's seven findings were folded into §3/§4 and into the child issues,
but **cycle 2 never completed for this document**: the supersession by `04-plan.md` moved the remaining
review cycles to that artifact, whose record is `05-verify.md` Step 4.

---

## 9. Explicitly NOT in scope

- **The MCP-side nudge** — telling agents to *use* the graph so they connect objects rather than dumping
  claims. Nothing implements it; a different surface from extraction. Recorded so this epic is never read
  as "the agents are now nudged". Related: `#5087`, `#10976`, `#5090`.
- **A volume target.** §16.3 **O2**: no volume target, no recall floor, no node budget. E1's registered
  `≤ 0.50 ×` is a pre-registered measurement, not an OKR.
- **Rebuilding the E1–E5 instrument** (§2 R11).
- **The v3 fidelity programme.** C8 runs the registered arm as a guard; it does not extend it.
- **`#2817`** (numeric/locale canonicalisation, CLOSED) — the value layer's locale question stays with
  `value_normalizer`'s own rulings.
