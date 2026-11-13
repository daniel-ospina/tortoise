---
title: "Epic #7949 — Strategy Alignment Decision"
type: engineering
domain: platform
doc_status: live
created: 2026-11-04
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: extractor v4, expansion packs, object layer, epic capstone
issue: daniel-ospina/tortoise#7949
stage: epic-align
---

# Strategy Alignment Decision — epic #7949 (extractor v4 CAPSTONE)

**Status:** PROCEED → hands off to `epic-research`.
**Produced by:** `epic-align` (read in full). **Reviewed by:** fresh-context reviewer, cycles in §6.

> **Provenance.** This is **rev 9**. **What is unchanged is the DECISION — PROCEED, with the GREEN bar
> widened to E1–E5 + A1–A5.** Everything else has moved: rev 6 re-cut the ownership boundary to
> **mechanism vs evidence** and corrected an anti-post-rationalisation answer that was false against the
> tracker; rev 7 re-cut the operative sentences; rev 8 named the two mechanism exceptions and fixed a route
> identifier; rev 9 corrected this artifact's own review record (the closing line said rev 8 and totalled
> fewer cycles than §6's table holds). §6 records each. ⚠️ An earlier version of this note said *"the substance is unchanged"*,
> which was false — a decision that says that while its boundary, its magnitude and its exceptions all
> changed is exactly the kind of note this artifact exists to prevent. Revs 1–3 were produced before the research pass and
> were corrected by its findings; those corrections are preserved in §3 and §4 rather than rewritten
> away, because a decision that hides what it got wrong cannot be audited.

---

## Feature

Epic **#7949** — the CAPSTONE. Not an implementation epic: it owns the **end-to-end proof** that extractor
v4 works, and the child issues that produce the evidence. Its GREEN bar is E1–E5 (v4 *selection*) **plus**
the owner's five assertions A1–A5 (2026-11-04, `#7949` comment `6645275147`) — the assertions are the
actual question and are treated as primary.

**The question (owner, verbatim):** *"Can v4 use the business logic declared in the ontology expansion
packs, and otherwise connect objects to each other — so the ontology becomes a map of the business?"*

**Measured on the fleet's hosted graph `live-v4`, 2026-11-04:**

```text
object→object edges:      []              ← ZERO, of every relation type
objects:                  5,314           (dev:code 1,030 · core:tool 646 · core:workflow 237 …)
claims attached:          23,814 aboutObject edges
pointKind:                statement 15,534 (80 %) · NULL 3,658 (19 %) · typed 109 (0.6 %)
logic edges:              IMPL 6,132 · NAND 1,810 — 9,910 of 15,534 statements carry NEITHER
Subjects:                 26 (against 19,300 points)
TAGGED edges:             0
```

⇒ **a flat set of objects, each carrying claims, with nothing above or between them.** A catalogue of
things, not a map of the business.

---

## Decision

**PROCEED** — as a proof + decomposition epic, with the GREEN bar widened to E1–E5 + A1–A5.

---

## Alternatives considered

1. **Skip the capstone; implement the missing wiring directly.** Rejected — **not** on "we don't know what
   is missing" (after research, we do: §3 R2/R3), but because the whole E1–E5 instrument already exists,
   is committed, and has produced a verdict that is **mostly RED** (§3 R11). Implementing without a row
   that can turn green re-runs the failure the instrument was built to catch.
2. **Narrow the epic to E4 (operator edges).** Rejected twice: E4 is one *Point-layer* edge family, and its
   registered bar is **15 of 15**, not the epic body's stale `4/4`.
3. **Split the owner's five assertions into a new epic.** Rejected — same question, same pipeline; splitting
   lets v4 close green while the graph stays a catalogue. ⚠️ **Corrected:** the *first* version rejected this
   on the premise that no other issue owned the assertions, which the tracker refutes — `#5087`'s children
   own A1/A2/A3's **mechanism** (see the boundary below). The rejection now rests only on what survives:
   the **evidence** half of A1–A5 has no other owner, and a proof claim cannot be satisfied by an
   implementation issue closing.
4. **Pre-capstone probe: replay a few real sessions by hand + one rollup query** (§14's *"cheap test…
   small-sample-first"*). Adopted as an **addition**, not an alternative: it establishes the gap and seeds
   the baseline capture, but a hand-replay proves a *human* can connect objects, never that S3/S5 do.
5. **Treat "build a capstone harness" as the work.** Rejected — **it is already built** (§3 R11). ⚠️ This
   alternative was *nearly taken*: an earlier revision chartered a second census, second registration and
   second baseline because research had enumerated the epic's **child issues** and missed the **artifacts**
   those issues built. Caught by a decompose-gate review. **Issues are not artifacts.**

### Anti-post-rationalisation — the strongest argument against proceeding

*"Six fresh-context reviews already found and fixed four P1s; the epic's own rows are RED on bars that
belong to other owners (`#5333`, `#10360`, `#5087`, `#9596`, `#9736`). A capstone that keeps producing
findings about **other** issues' unfixed work adds process, not product."*

**Answer (corrected — the first version of it was false against the tracker).**

⛔ **What the first version claimed:** *"the A1–A5 rows have no home anywhere in the live family."*
**That is refuted by the tracker at this head.** The sibling epic's lane (`#5087`) filed a batch at
2026-11-05T00:30–00:31Z that owns A1/A2/A3's **mechanism**:

| issue | filed | owns |
|---|---|---|
| **#11001** | 00:31:11 | **A1's EDGE-PRODUCTION clause only** — *"the pack's declared relations become replayable Object→Object edges … against a before-arm of 0 Object→Object edges of any type"*. ⚠️ It does **not** own A1's second clause: a grep of its body for `query`, `rollup` or `answers` returns nothing, so the **rollup answer** stays this epic's cut in C3 |
| **#10997** | 00:30:40 | **A2's HOLD half** — *"S3 — an unresolved identity is HELD, never minted"* (the `failure_policy` mint half) and the run transaction. ⚠️ **Not** the attach/resolve half, which is unowned and lands as C4 (below) |
| **#10998 / #10999** | 00:30:47 / 00:30:55 | **A3's two jobs** — S5 Job 2 (fail-closed `HOLD`) and Job 1 (the batch's own story reaches the graph) |
| **#11000 / #11002 / #10995** | 00:31 | pack `subclassOf` widening; the eight steps + S4's gap-filling; the per-door write census |

**Timing, stated precisely** so neither lane over-claims: **this lane's children were created at
00:05:01–00:05:36Z** (`#10982`–`#10990`); **the sibling's batch at 00:30–00:31Z**. So this lane did not
duplicate a live filing — the sibling's batch arrived 25 minutes later, after this lane's decomposition was
already reviewed and posted. That does **not** make this lane's version canonical: #5087 is the epic that
owns the **implementation**, and its lane is the one that will build the mechanism.

**The boundary this forces, and it is the decision this artifact records:**

- **#5087's children own the MECHANISM** (A1's edge producer, A2's HOLD gate, A3's two S5 jobs).
- **#7949 owns the EVIDENCE** — the pre-registered criterion, the before/after on a real corpus through the
  fleet's door, and the proof object. A capstone that implements is not a capstone; it is a second
  implementation with its own opinion.
- ⇒ **C3 / C4 / C5 are re-scoped to the evidence half**, each carrying a `blocked-by` on the sibling issue
  that builds its mechanism: **C3 → `#11001`**, **C4 → `#10997`** (plus `#5087`'s mint half and `#10996`),
  **C5 → `#10998` + `#10999`**. ⚠️ **State of that re-cut at this revision, stated per child because a blanket claim was false here once:**
  **C3** (`#10984`) and **C5** (`#10986`) are re-scoped to the evidence half in the same change that ships
  this revision; **C4** (`#10985`) is the **second mechanism exception**, alongside C6/A5 — see below.
  A1–A5's **proof** still has no home elsewhere; **that** is this epic's cut.
- **The two-branch rule these exceptions follow** (stated once so neither reads as convenience):
  *the capstone implements nothing a sibling is chartered for, and never measures against a mechanism
  nobody was told to build.* Where the absence is **known, unowned and needed now**, the capstone **builds
  it** (C4). Where the absence is **disputed** — the code may already work and the symptom may be a corpus
  artefact — the capstone **measures first and files the mechanism under `#5087`'s epic only if the gap is
  real** (C6/A5). That is the difference between the two, and it is the reason they are not one case.
- **C4 (A2) — the SECOND mechanism exception, and it is genuinely unowned.** `#10997` owns **only the HOLD
  half** (*"an unresolved identity is HELD, never minted"*). A2 as the owner wrote it is the opposite
  direction — *attach instead of mint*, i.e. an S3 **resolve/attach** decision — and **no sibling issue is
  chartered for it.** So a lane reading a blanket *"the mechanism is #5087's"* would measure A2 against a
  mechanism nobody was told to build. C4 therefore **builds the attach/resolve half** and consumes `#10997`
  for the HOLD half. If the sibling lane later charters it, C4 defers and keeps only the evidence.
- **A5 (Subjects) — the exception, argued rather than assumed.** The principle *"a capstone does not
  implement"* is scoped to the **extraction-path mechanism a sibling already owns**. For the subject layer
  there is no sibling issue **and possibly no mechanism gap**: `bind_point_subjects`
  (`subject_binding.py:344`) exists and is called from the commit path — but ⚠️ **`#4889`'s own investigation
  records the opposite of "shipped":** *"the Subject half was never implemented, at any commit … 'shipped'
  covered the `aboutObject` half only. That is a closure that overstated scope"*, and its scope comment says
  the layer has *"no reachable producer"*. So **C6 decides which is true now** — the point is that the answer
  is genuinely in dispute, not that it is settled in either direction, so filing a mechanism issue in
  advance would presuppose C6's finding. If C6 finds the binder genuinely broken, the mechanism issue is filed **under `#5087`'s
  epic** and C6 keeps only the evidence.
- ⛔ **Sample-size note: the rollup half of A1 is the row most likely to be dropped in a hand-off**, because
  the sibling issue's title reads as A1 in full. C3's title and checklists now name the rollup explicitly.

**And a process finding worth keeping:** this artifact's cited source (comment `6646645605`) enumerated that
batch as "2 of 9" (#10994, #10996). Both enumerations were **derived from a list rather than re-queried from
the tracker**. The rule that follows: **before any ownership claim, re-query `gh issue list` at the moment
of the claim** — a list in a document is a cache of a past read.

---

## Profit impact (causal chain, testable)

The graph **is** the product. If objects never connect, the promised rollups — *"which work items serve
this goal, and what evidence supports them"* — cannot be answered from the graph, so the differentiation
that justifies a paid tier does not exist.

⚠️ **Magnitude, corrected.** An earlier revision said *"$$1000s/month class"*. The only **recorded** pricing
is `product/pricing.json` (`status: current`, `owner_confirmed: 2026-08-07`): free **$0** · solo **$9** ·
pro **$25** · team **$149**/mo, with extraction overage at **$5 per 10k** write-ops. A thousands-class
contract is **one to two orders above** the highest recorded tier and is cited nowhere, so:
**the claim is a HYPOTHESIS, not a finding** — *"the map capability is what moves a customer from the
$149 team tier to a contract an order above it."* It is testable with the same instrument this section
already names, and it is **not** tested here.

**Causal hop that could fail:** customers might buy the claim-catalogue value alone — if so, A1's rollup is
nice-to-have rather than load-bearing. Also untested here.

---

## Eisenhower placement

**IMPORTANT + URGENT (do now).** P0-blocker label; dated owner direction; a measurable, reproducible
baseline; and every downstream lane that assumes the object layer works is building on a catalogue. The
cost of the wrong assumption compounds per lane.

---

## Key assumptions

| # | assumption | confidence | source | validation plan |
|---|---|---|---|---|
| A-a | the `live-v4` baseline in §Feature is accurate | **high** | owner-measured 2026-11-04, per-kind breakdowns | C1's extended census re-derives it |
| A-b | the packs *do* declare part-whole object relations; schema + writers admit them | **high** | tree: `packs/dev/manifest.yaml:142,148,239`; `projection/edges.py:717-726`; ONTOLOGY §3.7 | C3 |
| A-c | the gap is the **declaration → consumer** seam, not a missing writer | **high** | tree: zero `create_edge` in `extractor_v2.py`; `hierarchies` validated-only (`pack_registry.py:2489-2503`) | C3 |
| A-d | a corpus reachable **through the door the fleet writes through** exists or is capturable | **medium** | ⚠️ **Transports, not lanes.** The committed arm *is* the **v2 lane** — `llm:deepseek-direct` is what `sdk.py:16346-16347` states as *"the v2 path reports `extraction_mode: llm:<route>`"* (keyed by symbol: `extraction_mode`), and `probes/capstone_measure.py` drives `TortoiseSDK.capture_session`. What **no committed capstone arm** exercises is the **hosted transport** the fleet's sessions actually use — `POST /v1/sessions` (`tortoise/pi-hooks/tortoise-capture.ts:948`, the plain `postCapture`; `:938` is the `install-probe` sibling, `hosted_api.py:20345`), with its quota/auth gates, and the registered corpus is a **fixture** (`tests/eval/write_path`, `pinned_by: fixtures_hash`). Also: **E1b's deterministic `m2` control has no committed artifact**. | C1 + C2 (the open decision is *which transport*, not *which lane*) |
| A-e | `TAGGED edges: 0` is a gap | **rejected** | no extraction path writes `tags`; `_sync_tags` is the only writer | no child; recorded so it never becomes a target |
| A-f | E1–E5 need a fresh instrument | **rejected** | `#9075` registration + `probes/capstone_measure.py` + verdict gate, all committed | C1 extends it |

---

## §3 · Research findings that corrected this decision (condensed)

Written here because a decision that cites only its conclusion is unauditable; the full pass is
`01-research-brief.md`.

- **R1/R2 — the packs already declare it** (`decomposesInto`, `hasPart`, `hierarchies`), the schema admits
  it (`hasPart` on the structural allowlist; Object→Object per ONTOLOGY §3.7) and the writer exists
  (`edges.py:3100`). ⚠️ `decomposesInto` is **not itself writable** — its route is `semantics: hasPart`,
  and **nothing derives the writable predicate from the declaration**.
- **R3 — the extraction lane writes no structural edge at all.**
- **R6 — the door is the v2 lane** (`_capture_lane()` → `"v2"` unless `TORTOISE_SESSION_EXTRACTOR=m2`).
  The lane brief's "the fleet writes through the legacy rule extractor" is **false** at this head.
- **R11 — the E1–E5 instrument already exists and is mostly RED** (shipped table: E1 BLOCKED, E2 BLOCKED,
  E3 RED 157/397, E4 RED 2/15 corrected to 5/15, E5 RED 12/25).

**Corrections this produced in the decision itself:** rev 1 rejected alternative 1 on a **false premise**
(that no pack declares part-whole relations); rev 2 put a malformed question to the owner about D3; rev 4
demanded an anchor `create_edge` cannot carry, reversing `#5213` option 3; rev 5 called E5 green when the
shipped table says RED.

---

## §4 · Routed, not decided (owner territory)

Two recorded decisions and one closed-but-unsettled metric sit under the owner's assertions. This epic
**routes** them; it does not decide them.

1. **REOPEN — D3 `statement`-only** (`docs/ONTOLOGY.md:975-984`, option B 2026-08-12, `#1022` MERGED) vs A4
   ("0.6 % typed is the ceiling the capstone must beat"). Fork: (a) A4 overrides D3 → `OVERRIDES:` on
   `#1022` + `ONTOLOGY.md` §5 and C7 gains a target; (b) A4 was aimed at `#4432`'s docs-vs-code drift → C7
   attributes the 109 and follows whichever of `#4432`'s options the owner picks.

   > ⚠️ **RESOLVED AFTER THIS STAGE — do not act on the fork above.** The owner ruling of 2026-11-09 replaced A4
   > with *"things and events carry the labels the ontology declares for them, reported per layer"*, bar =
   > appears + labelled, percentage **reported, not chased**. Both arms (a) and (b) are therefore closed:
   > **C7 does not gain a comparator, and does not follow `#4432`.** See `02-scope.md` *In Scope* item 5 for the A4 half (the ruling's effect, and the `D3`
   > question it closes) and `04-plan.md` §W4 for the `C7`/`#4432` half — the comparator reopen
   > test and the `OWNER_DECISION` pin that refuses a registered non-bar live in §4.4. The ruling's home is
   > `#4432` comment `6751932659`; this file recorded the ruling, the later stage resolves the fork. This file is the **record of the align stage** and is left as
   > written — a later stage resolving a fork this stage opened is the pipeline working, not a contradiction;
   > read the ruling, not the fork.
2. **ASK — §13.8 Q1**, to-graph connection by model or mechanically: recorded **OPEN**
   (`EXTRACTOR-V4-ARCHITECTURE.md:2084` — under §13.8's heading at `:2075` — and `:2086`) while **D6 stands** (`:1418`, `:1113`). **C5 measures conformance to D6** — the two connection sets are `#10999` (Job 1) and `#10998` (Job 2); C5 builds neither.
3. **`#9736`** — E3's denominator. **CLOSED 2026-11-04T15:37Z** *before* this head; its amendment landed
   (`8c1151426`) and E3 is still RED at 157/397 because 193 of 354 Points cannot carry an anchor. Its
   closure leaves two owner-gated lines owed. `blocker-states.json` still says OPEN — a stale pointer of
   exactly the class the apparatus refuses (V12).

---

## §5 · Recommendation

**PROCEED to research.** Research's first jobs: (a) a *gated* research brief — this stage's substitute was
repo facts, which is not the `epic-research` artifact; (b) confirm the unconsumed `semantics`/`hierarchies`
seam against the tree; (c) reconcile "the registered corpus is a fixture" with "the acceptance must run
through the fleet's door"; (d) put the D3 reopen in front of the owner.

---

## §6 · Review record (this artifact)

| cycle | verdict | findings | disposition |
|---|---|---|---|
| 1 | `ISSUES` 2×P1, 3×P2 | false premise rejecting alternative 1; unrecorded reversal of D3; A3 mechanism treated as a decision; `TAGGED` treated as a gap; cheap probe missing | all fixed (rev 2) |
| 2 | `ISSUES` 1×P1, 4×P2 | malformed D3 fork (its "second reading" refuted by the baseline); 3 citation errors (object-layer writer vs point-layer operator path; `hierarchies` mislabelled declaration-only); D3 vs §13.8 Q1 warrants conflated | all fixed (rev 3) |
| 3 | **not run** — the sequence-enforcer advanced past `align` mid-dispatch and the cycle was never re-issued; the lane then bypassed the `scope`/`plan` human gates with a session escape | — | **owed**; this rev exists to pay it, and the bypass is recorded in the pipeline audit |
| 4 (rev 4–5 fixes) | **caught by the `decompose` gate, not by a cycle of this artifact** | demanded an anchor `create_edge` structurally cannot carry (reversing `#5213` option 3); called E5 green when the shipped table says RED; used a stale E4 denominator; gated E2 on `#10360`'s population | fixed in rev 4–5 — the credit belongs to the decompose reviewer |
| 5 (rev 6, this artifact) | `ISSUES` 1×P1, 2×P2 | P1: the anti-post-rationalisation answer was **false against the tracker** (the A1/A2/A3 mechanisms are owned by `#11001`/`#10997`/`#10998`/`#10999`); P2: an uncited `$$1000s` pricing magnitude; P2: this table asserted a cycle with no verdict and hid the decompose-gate reviews | all three fixed in rev 6: the boundary is re-cut to **evidence vs mechanism**, the magnitude is restated against `pricing.json` and marked a hypothesis, and this table now names the gate that caught each correction |
| 6 (rev 7) | `ISSUES` 3×P1 + 4×P2 | P1s: the re-cut reached #10984's Objective but not its Targets/checklist/Research/Components (an implementer would still build `#11001`'s producer); A-d's replacement clause was refuted by its own citation (`llm:deepseek-direct` **is** the v2 lane — the real gap is the *transport*); C4 named `#10997` only by description. P2s: A5's `#4889` fact inverted; `00-plan.md` §4's MECE A1 row + C5 Produces + #10986's title still chartered the mechanism; §6 trailed the content; `:1840` vs `:1842`. | all seven fixed in rev 7 — the pattern worth naming: **a decision restated in its summary sentence and not in its operative sentences reads as resolved while still directing the old work** |
| 7 (rev 8) | `ISSUES` 4×P2, **0×P1** | a non-existent route (`POST /capture` → `/v1/sessions`); the blanket *"C4 already carried it"* was false **and** A2's attach half is genuinely unowned; #10984's **Indicator 4** still shipped the `implemented` registry row its own Targets gave away; the provenance note said *"substance unchanged"* while three revisions changed it | all four fixed in rev 8, and **three of the four were deletions**, not additions — the convergence signal: severity fell to zero P1 for the first time |
| 8 (rev 9) | `ISSUES (cosmetic only)` — **convergence** | three surviving *implementation* verbs in sentences the re-cut had not touched (C5 "implements D6" ×2; the A3 MECE row; the `#10997` row still claiming A2's *whole* mechanism; an unqualified "what nothing exercises" the repo's 27 hosted-capture test files refute) | fixed by **grep**, not by re-reading — the reviewer's own prescription. Reviewer's trend read: *"two consecutive cycles at zero P1, three of rev 8's four fixes were deletions"* |
| 9 (rev 9) | ✅ **`NO ISSUES FOUND`** — `CONTRADICTION TEST: clean` | — | **GATE CLOSED.** **Nine rows above, six of them cycles that returned findings.** The **five P-counted cycles (1, 2, 5, 6, 7) total 7×P1 + 17×P2**, every one fixed; cycle 8 was cosmetic-only and fixed by grep; cycle 3 was never run and cycle 4's corrections belong to the decompose gate — both recorded above rather than smoothed away. The two lessons worth carrying: (i) *a decision restated in its summary sentence and not in its operative sentences reads as resolved while still directing the old work* — it recurred in cycles 3, 4 and 5; (ii) **grep the moved verb, do not re-read the section you edited** |
