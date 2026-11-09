---
title: "Extractor v4 (#5087) — epic decomposition: children, doors, and proofs"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-04
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#5087, #7949, EXTRACTOR-V4-ARCHITECTURE.md, config/v4-mechanisms.yml, SUBSTRATE-REBUILD-MAP.md, packs/dev/manifest.yaml"
---

# Extractor v4 (#5087) — the epic decomposition

**What this is.** The decomposition of epic **#5087** into children: for each, the **step** it lives in (S0–S6), the write **door** it changes, and the artifact that proves it. It lands no code.

**What it is not.** Not a scope approval, not a schedule, not a redesign. The design of record is `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md`; the declared-vs-shipped ledger is `config/v4-mechanisms.yml`; the acceptance rows are **#7949** — **0 of 6 green** (E6 was added by owner ruling on 2026-11-05 and is red by construction: nothing writes a declared pack relation yet), and its verdicts are not this document's to change.

**Read §1 before trusting anything else**, including the epic body and this lane's own brief: three premises they carry are stale or false as measured on 2026-11-04.

---

## 1. What the tracker and the tree actually say

| the claim carried by the epic body or the lane brief | measured 2026-11-04 | consequence |
|---|---|---|
| **"#4894 is OPEN — the design home … supplies align/scope"** | **CLOSED** (2026-10-14) | the align/scope artifacts this epic was to inherit do not exist as an open issue. They survive as **the architecture document** and the owner's 2026-11-04 brief; this decomposition treats those as the inherited align/scope and says so rather than pointing at a closed issue as a gate. |
| **"every fleet point arrives through the agent-capture door running the legacy rule extractor (`tortoise/extractor.py::_classify_point_kind`, #4432)"** | **FALSE.** The fleet path is pi hook → `POST /v1/sessions` → `hosted_api.capture_session` → `sdk._extract_session_v2` → `extractor_v2.extract_session_v2`. `_classify_point_kind` is reachable only from the **documents** door (`ingest.py`), and the substrate map records that the owner moved `#4432` out of every lane. | the brief's constraint is right in direction — *the fleet's arrow is the only arrow that counts* — and wrong in fact. A child that improved `extractor.py` would improve a path the fleet does not use, so **no child here touches it**. ⚠️ **One second branch does run `extractor.py` on the same door** — `TORTOISE_SESSION_EXTRACTOR=m2` → `sdk._extract_session_llm` → `extractor.LLMExtractor` (`hosted_api.py:17938`) — and it is a **test-only configuration**, not the fleet's default. |
| the 12-row "The issues" table | **10 of 12 CLOSED** (#5006 #5007 #5008 #4899 #2453 #2730 #2552 #4716 #1026 #2714) | a decomposition read off that table re-files shipped work. |
| **"Part of: #5086"** | **CLOSED** | children are filed against **#5087**. |
| DoD: *"the EMBED gate is declared in the pack, not enforced"* | `embed_gate` is **`implemented`** (`tortoise/embed_gate.py`, flag-gated); `#5008` closed, residual is a *rate measurement* | the DoD bullet is a measurement question, not a build. |
| the brief's `NULL pointKind 3,658 (19%)` read as a classify failure | **not a classify failure.** `pointKind` is `statement` by design for extraction Points; a NULL or empty `pointKind` marks a **non-extraction** Point — operator Points are written with `pointKind = ""` (`projection/__init__.py`), and the point fold is `coalesce($pk, n.pointKind)` | the "typed 0.6%" figure needs a definition before it can be a bar; the census child #10995 defines it and does not adopt the misreading. |

**The epic's own acceptance rows** (`#7949`, `docs/evidence/7949-capstone/`): **0 of 6 green** — E1–E5 as registered, plus **E6** (the pack-logic row) added by owner ruling 2026-11-05.

- **E1 `BLOCKED`** — the two before-arms on the frozen corpus are 0.3590 (78 Objects) and 0.5652 (69); the after-arm is 0.2131 (61). One pairing passes its bound, one fails, and the two before-runs differ by **20.6 pp**, wider than the 14.6 pp the arm moved. The next requirement is **n ≥ 2 per arm**, not a fix.
- **E2 `BLOCKED`/RED** — `E2a` 8 of 14 against a 13-of-14 bar; the loss is upstream of the filter (`#10360`).
- **E3 `RED`** — 157/397 anchored, and the registered `= 1.000` is unreachable as registered because `:Object` **mention** links are unanchored **by design** (`#5199`; metric re-registration `#9736`, now closed).
- **E4 `RED`** — 5 of 15 planted operator edges on the frozen corpus (the receipt-based **5-of-14** reading excludes `wp02` per `#9873`). `#8503`'s own title splits **nine** of the ten misses into **4 operator-selection** and **5 endpoint-recall**; the tenth (`wp02`, content-level) has **no owner here**, so **E4's 15-of-15 bar is unreachable on this decomposition even if both E4-owning children land** — see §8.
- **E5 `RED`** — ratio 1.0833 passes, raw absolute 0.4800 fails, n = 25, on a population (25 bound qualifiers) that is not the registered one (29 marker sentences).

**The pre-registered metric set had no row for what the owner asked for.** E1–E5 measure volume, retention, anchoring, operator edges and narrative fidelity; **none of them measured a hierarchy**. ✅ **Resolved 2026-11-05:** the owner added a **sixth row** and defined it as *per-pack declared → writable → written* (not a generic hierarchy count), recorded on `#7949` (comment `6664402530`) and implemented by `#11083` — see §8.2, which is now a decided section rather than an open question.

---

## 2. The three axes

1. **The step** (S0 · S1 · S2.1 · S2.2 · S2.3 · S3 · S5 · S6) — which decision the child changes. §4.0's *purpose test* is the authority: a child must state its step's purpose in one clause no other step shares.
2. **The door** — which write path it changes: hosted `/v1/sessions`, SDK `capture_session`, `commit_session`, documents/`ingest`, MCP + SDK primitives. A child that does not touch the fleet's capture door must say why it is still in scope — the brief's constraint, kept.
3. **The proof** — the artifact that reds if the behaviour is removed. For most children that is a capstone row (`#7949`) or a registry pin (`tests/test_v4_declared_mechanisms.py`); where a row is owner-gated or foreclosed, the child's proof is its own pin and **the row is not claimed**.

---

## 3. The children

| key | issue | step | door | the proof that reds | file family |
|---|---|---|---|---|---|
| **A** | #10994 | — (gate artifact) | all five, enumerated | the map itself, registered in the docs index | `docs/epics/` |
| **B** | #10995 | S6 (measures) | all five | a committed census artifact + a re-derivation pin on the frozen corpus; an unmeasured door reads `UNMEASURED` | `tools/`, `docs/evidence/` |
| **C** | #10996 | S2.2 | fleet capture | default-arm DISCARD with its `rule_id` (a test that reds if the default flips back, and reds if the flip lands anywhere but the leaf resolver); ⛔ the reference question's precision and "zero classified-and-deleted" are **NOT** this issue's — the question is `#11021`'s `ref-v1` (this issue supplies the arming), and the prune has no reachable target (`classify_consolidation` cannot return `DELETE`, D5) | `value_gate.py`, `extractor_v2.py` (comments), `vet_gate.py` (comments), `config/v4-mechanisms.yml` |
| **D1** | #10997 | S3 (+ S5 pre-pass, S6) | fleet capture | `TestTheRemainingAbsence::test_an_unresolved_lookup_still_mints` reds; a partial run leaves the journal unchanged; the held set round-trips | `extractor_v2.py`, `sdk.py`, `failure_policy.py` |
| **D2** | #10998 | S5 Job 2 | fleet capture | the Job-2 hold pin; held/committed/discarded as three counts; `failure_policy` flips only with both halves | `extractor_v2.py`, `failure_policy.py` |
| **E** | #10999 | S2.1 + S5 Job 1 | fleet capture | E4 read from a receipt whose `extractor.real` is true (the receipt field; `extractor_identity` is the *function* that builds it, not a field); the `relations` channel's schema test; zero mints | `extractor_v2.py`, `commit_schema.py`, `tests/eval/write_path/` |
| **F** | #11000 | S2.3 (the vocabulary) | fleet capture, indirectly | `expand_kind("dev:issue")` includes `WorkItem`; the index spec carries parent kinds | `pack_registry.py`, `value_extractor.py` |
| **G** | #11001 | S5 → S6 | fleet capture + ingest | three declared pairs > 0 against a before-arm of **0**; `live == rebuild` on the new edge class; the fan-out cap holds | `pack_registry.py`, `projection/edges.py`, `extractor_v2.py` |
| **H** | #11002 | S1 → S2.2 → S5 | fleet capture | the retry fires exactly once; a tie keeps the first narrative; `run_s4` is gone **with** an ADD-capable stage present | `extractor_v2.py`, `vet_gate.py` |
| **I** | **#11083** | S0 (the declaration) + S6 (what is written) | fleet capture, indirectly | a committed per-pack census (`declared → writable → written`, over **both** `relations` and `hierarchies`) + the **sixth capstone row** on `#7949`; the four `pm` relations named or resolved; direction reported per row; `funds`/`produces` restored per the owner ruling | `packs/*/manifest.yaml`, `pack_registry.py`, `docs/evidence/` |
| **J** | **#11021** | S2.1 + S2.2 | fleet capture | the mention channel in `OUTPUT_CONTRACT` (a contract pin), and **zero mints** for a judged non-referential phrase while a must-keep row still mints (T1/T2); the arbiter scored on the 25 owner-labelled rows **plus the 12 most-referenced rows a name-pattern rule would have deleted** | `extractor_v2.py`, `commit_schema.py`, `s2_decision_seam.py` |

### The children that do not touch S3 or S5, and why they are still in scope

The brief requires this justification, and two of the answers are findings rather than concessions.

- **B (the census) is the instrument, not a change.** Without it no child can say *which door it improved*, and the brief's own premise about the fleet's door turned out to be false when measured. A per-door census is how that class of error stops recurring.
- **C does not touch S3/S5 and cannot fix the flat catalogue alone — and that is the finding.** `vet_gate.apply_vet`'s Rule 2 is *"a referenced entity is never removed"*, and every extracted Object is named by the claim that produced it, so **VET alone can never remove one**: the shipped arm moves the `^the <X>` share by **exactly 0.0**, which is what `tests/test_e1_e2_measurement.py` pins. C exists because without it **the gate does not run at all** on the fleet door, and because the reference *question* is what the owner's labelled set can measure cheaply (§8).
- **F is one bug in the kind vocabulary.** S3's exact-match discriminator (`_find_existing_entity` folds `kind`) and the declared hierarchy both key on kinds, so a missing `subclassOf` widening quietly degrades resolution and enforcement at once. It is a precondition for **G**, not for the flat catalogue.
- **H completes DoD bullet 1** — *"the pipeline is the eight steps, in that order"* — which is unmet while `run_s4` runs under `if story:` with no S4-specific switch. No open issue covers it: `#8500` closed with a design only.

---

## 4. Coverage — every epic scope item and its owner

| scope item | owner after this decomposition | step | note |
|---|---|---|---|
| the eight steps in order, VET → CLASSIFY → RESOLVE | **C** (S2.2/S2.3), **H** (S4's removal + the bounded retry) | S2.2 · S2.3 · S1 | DoD bullet 1 |
| S3 is the single authority on new-vs-existing | **D1** | S3 | the mint half of `failure_policy` |
| the per-step failure policy (I1/I2/I3) | **D1** + **D2** | S3 · S5 | both halves, then `config/v4-mechanisms.yml` flips |
| S5 Job 1, the batch's own story | **E** | S2.1 · S5 | adds the entity↔entity channel the contract lacks |
| S5 Job 2, the batch against the graph | **D2** | S5 | fail-closed, per §4.4 |
| the owner's map of the business | **G** (+ **F**, **E**) | S5 · S6 | declared `relations` become edges |
| the verbatim span layer (DoD bullet 2) | **`#2684`** (open) — *not a child* | S6 | the value-bearing half is measured 101/112; the every-point widening is `#2684`'s and `config/v4-mechanisms.yml` says so |
| the EMBED gate (DoD bullet 3) | **`#5008`** (closed) — *not a child* | S5 | `implemented`; the residual is a rate measurement |
| the bounded fan-out / sufficiency (DoD bullet 4) | **`#5009`** (open, the last item of lane L3) + `fanout_cap` (`implemented`) | read path | *not a child* |
| near-duplicate MERGE, exact dedup, source dedup, `against_priors_consolidation` | `#5006`, `#5012`/`#5007` — **all closed / `implemented`** | S2.2a · S3 | the registry is the ledger |
| the atomic point write | **`#9879`** (open) | S2.2a | a precondition of the against-store exact dedup, **not** of D1's HOLD |
| the narrative + raw persistence (G1) — whose **declared home in the architecture is `#5064`** | **`#10000`** + **`#10394`** (open) | S1 · S6 | *not a child*; the architecture names `#5064` for G1 and the two implementation issues are the corrected pointer — recorded rather than silently substituted |
| the seam's request shape | **`#10176`** (open) | S2.2 | a stated precondition of the first *armed* scored run |
| `link_before_create` contract with no consumer | **`#10448`** (open) | S3 | reads the same receipt `#10533`/`#10449` argue about |
| the S3 receipt's `search` block | **`#10533`** + **`#10449`** (open, the same defect twice) | S3 | **B** declares which reader it trusts and does not add a third |
| E2 retention | **`#10360`** + **`#9775`** (closed render half) | S2.1 | upstream of selection |
| E3's metric | **`#9736`** (closed) | — | the row needs a re-run, not work |
| E4's receipt retrievability | **`#9596`** (open) | — | a different gap from E4's unformed edges |
| the relocation / destructive half | **`#5333`** + **`#5059`** (open, owner-gated) | — | **no pass may remove a committed row**; see §8 |
| the claim-class write path (L8: `#9417`, `#9727`, `#9819`) | **all three CLOSED** — re-measured 2026-11-04 (`#9417` 2026-11-01, `#9727` 2026-11-02, `#9819` 2026-11-01) | supersede · ingest | ⚠️ `SUBSTRATE-REBUILD-MAP.md` still reads `DECLARED, no issue yet`, which is **stale by measurement** — the same failure §1 describes. The three fixes landed, so **no sequencing edge is claimed here** |
| vector reaping / text-worth-a-vector | **`#4997`** — CLOSED 2026-10-05 · **`#4998`** — a MERGED PR (2026-09-24) | — | re-measured: both are done, so this row is history, not an open dependency |

---

## 5. Order, file families, and the honest parallelism

**Seven of the eleven children edit `tortoise/extractor_v2.py`.** `config/ci-surfaces.yml` is a *serialization point* rather than a file — every PR that adds a test edits it — so "eleven children, eleven lanes" would be false. The honest number is **one extractor lane**, with four genuinely parallel side lanes.

```text
A (docs) ────────────────────────────────────────────────────► no dependency, gates the checklists
B (tools/ + docs/evidence/) ─────────────┐
F (pack_registry + value_extractor) ─────┤ parallel
I (packs/ + pack_registry) ──────────────┘
                                         ▼
J ──► C ──► D1 ──► D2 ──► E ──► H ──► G     the extractor_v2 lane — strictly serial
```

- **J first** in the extractor lane: the mention channel is the substrate every later selection step reads (`#11021` adds `mentions[]` to `OUTPUT_CONTRACT`; `C` then judges them).

- **A first**: it is the checklist source for every other child.
- **C before D1**: the gate must run before its disambiguation can be calibrated.
- **H before G** and **G last**: the align gate's verdict (below) — the hierarchy moves **none** of the five acceptance rows, so it must not front-run the claim/edge work. **Amended 2026-11-05:** a **sixth** row now exists (owner ruling, `#7949` comment `6664402530`), so the hierarchy's outcome *is* watched — but the ordering does not change, because **I (#11083) depends on G**: the census cannot show a `written` delta before the mechanism that writes it lands.
- **Max parallelism 4** (B, F, I, and exactly one extractor child); **honest schedule: 1** for the extractor family.

### The align gate's verdict, recorded

A fresh-context adversarial align review returned: *"the goal is right; the decomposition is not proportionate — it is sequenced after the constraint it depends on"*, on the evidence that **9,910 of 15,534 statement points (64%) carry neither `IMPL` nor `NAND`** — ⚠️ **an owner-supplied measurement from the 2026-11-04 lane brief, taken on the hosted `live-v4` graph; it has no committed artifact in this repository and is not re-derivable from §9's commands.** **Applied**: the claim/edge children (C, D1, D2, E, H) precede G, and G is scoped to not delay them. Its other two points were checked rather than adopted:

- *"the relations writer is near-zero code — `create_direct_edge` already admits `:Object` endpoints"* — **true and useful** (`sdk.py`'s `create_direct_edge`, owner rulings `#7813`/`#7902`), so G is instructed to try the one-call operator path before adding a producer.
- *"pointless if the zero is deliberate — `#9736` says `:Object` mention links are unanchored by design"* — **a conflation, and refuted**: `#9736` is about E3's denominator counting `Point→Object` **mention** links; it says nothing about **Object→Object** edges, which ONTOLOGY §3.7 declares (`hasPart`, `wasDerivedFrom`) and which no producer writes. The zero is a gap, not a design outcome. Recorded so the next reader does not have to re-derive it.

---

## 6. Deliberately out, with the owner

Every row of §4's last block is out for a reason that is **not** capacity: it has an owner, it is closed, or it is a decision this lane may not take. The three that matter most:

- **`#5333` / `#5059` (relocation, owner-gated)** — *"a pass may add; after COMMIT no pass may remove a committed row except through the journaled `restatus` fold where no visible successor exists, or the D7 `ObjectSuperseded` arm where one does; a physical delete is foreclosed to every pass."* No child here writes to an existing Object.
- **`#2684` (the every-point span)** — the value-bearing half is measured at 101 of 112; the widening is a different mechanism with its own registry row and tracking issue. A child here that claimed "the span layer" would recreate exactly the declared-implies-present defect `#5064` exists to catch.
- **L8 (`#9417`, `#9727`, `#9819`)** — **all three are CLOSED** (re-measured 2026-11-04). The substrate map's `DECLARED, no issue yet` row — whose §3.1 says only that *"they need their own lane issue declaring the `sdk.py` family"* — predates their landing, so **the "L8 before E1's payoff" edge is no longer a live precondition** and is not claimed by this document.

**Also out, and named so it is not rediscovered:** `#5009` (the sufficiency router — the only remaining item of the one open lane), `#9879` (the atomic write — a precondition of the exact-dedup arm, deliberately *not* a `preconditions:` edge on it), `#10000`/`#10394` (narrative/raw persistence), `#10176` (the seam's request shape), `#10448`, `#10533`/`#10449`, `#9596`, `#10360`, `#9736`, `#8503`, `#8500`, `#4997`/`#4998` (both CLOSED, §4), `#1936`, `#5202`/`#8325`/`#9412`.

---

## 7. The review record

Three fresh-context rounds, and what was done with each finding. **A finding is a claim; it was verified against its cited artifact before being applied.**

| round | findings | applied | rejected (with the evidence) |
|---|---|---|---|
| **per-child** (8 children, pre-filing) | the mention channel + the referentiality question (`#11083`'s sibling); **7 of 8 children carried at least one issue, and the test-design child came back clean** | 2 detailed cases (T1, T2), **1 of them a must-keep positive control**; merged C3+C4 into **C** (the arbiter seam is already reachable — `_run_vet_pass` calls `_s2_seam_arbiter`, so no `sdk.py` change); dropped the structured-source S0 child (GitHub→Object mapping already ships: `github_map.issue_to_object`, `connectors/github.py`); scoped **E** to a `real: true` receipt instead of deleting the eval lane's patch; scoped **D1** to unresolved *identity* so it does not touch the value-fidelity refusal; scoped **G** to consume `pack_registry.relation_write_paths()`; added the `OVERRIDES:` guard to **C** | *"S2.3's two passes should become one"* — **refused**: the S2 pass must precede S3 so the lookup sees final kinds, and the union pass re-classifies the S4 superset with `_restamp_s2_kinds` |
| **MECE** | 6 findings | split D into **D1**/**D2** (only the pair flips `failure_policy`); added **H** (the re-narrate + S4 removal, which no open issue owned); added the edges B→C, D→E, F-after-C; corrected C's and F's file families; scoped **C** to carry an `OVERRIDES:` line and to flip the default in `extractor_v2`, never in the `value_gate` leaf; scoped **B** to declare `#10449`/`#10533` rather than add a third reader | *"B and E both claim E4 is observable"* — **accepted**: E owns the E4 read, B publishes doors only |
| **align** | 1 verdict, 3 findings | reordered the claim/edge children ahead of **G**; instructed G to try the one-call operator path first | *"the zero Object→Object count is by design (`#9736`)"* — **refused**, see §5 |
| **PR review** (fresh context, at the first head) | 2 P1 · 5 P2 | **all applied in the second commit**: E4's 10th miss has no owner (§1/§8, and the two E4-owning issues say so); L8's three issues are CLOSED, not a live family (§4/§6); the collision pre-flight's stall is attributed correctly (§7); the align review's 64% figure is marked owner-supplied and non-re-derivable (§5); the per-child count corrected to 7 of 8 (§7); the `m2` branch named as test-only (§1); `#5064` restored as G1's declared home (§4); and the arbiter probe's claim narrowed from precision-and-recall to specificity (§8) | — |
| **PR review, round 2** (fresh context, at the second head) | 3 P2 | **all applied in the third commit**: §4's L8 close dates corrected (two were transposed) and §9's re-check now emits `closedAt`; §6's L8 quotation replaced with the map's actual words; §8's `wp02` closure route stated the right way round (**excluded** from the 15-edge row, not re-included). An independent citation sweep run alongside found two more stale states — `#4997` CLOSED 2026-10-05 and `#4998` a **merged PR** — both corrected in §4/§6 | — |

**Two process facts, recorded rather than smoothed over:** `tools/collision_preflight.py 5087 --repo .` **reached no verdict** in this worktree — the invocation was still running after ~9 minutes and was abandoned. Its per-command seam has a `DEFAULT_TIMEOUT = 60` s and a timed-out command returns `rc=124` with an `INCOMPLETE` verdict (exit 2), so the stall was **not** a single hung subprocess; the likeliest cause is the tool's own multi-stage pass over a very large open-issue population. It is recorded as **no verdict**, which is not a clean one — so the mandated pre-dispatch collision check is *declared not run*, not satisfied; and the epic-workflow sequence-enforcer's stage gates blocked the parent session's shell, so the mechanical half of this lane (issue creation, this document, the PR) was executed by child sessions while the judgment stayed with the parent.

---

## 8. The decisions this decomposition puts to the owner

### 8.1 — E1: is the volume bar reachable, retirable, or stuck?

**The position, measured.** E1 is the epic's volume bar: the `^the <X>` share of `:Object` nodes must at least halve, over a floor of 30 Objects. Measured on the frozen corpus, the two before-arms are **0.3590** and **0.5652**, the after-arm **0.2131**; one pairing passes and one fails, and the two before-runs differ by **20.6 pp** — wider than the 14.6 pp the arm moved. So E1 is `BLOCKED` on **n ≥ 2 per arm**, not on a mechanism.

**But the mechanism question is answered, and the answer is that there is not one.** `docs/evidence/9889/on-arm.log` states it: *"the mechanism that must move the share is an owner ruling on removal/relocation (`#5333`)"*. Three measured facts make that true:

- the mechanical predicate is **identifier-only by design** and drops **0 of 61** Objects (**0 of 13** bare-definite) — its positive controls fire, so the zero is a measurement, not a dead predicate;
- `apply_vet`'s **Rule 2** (*"a referenced entity is never removed"*) means VET cannot remove an entity a surviving claim names, and every extracted Object is named by the claim that produced it — so VET alone moves the share by **exactly 0.0**, which `tests/test_e1_e2_measurement.py` pins;
- mint-time **name-pattern** drops are foreclosed: `#1026` ruled *"no name patterns; nothing is dropped at mint"*, and `packs/_template/manifest.yaml` refuses those keys at validation.

**The options, and what each costs.**

| option | what it buys | what it costs | who can take it |
|---|---|---|---|
| **1 · authorise the relocation pass** (`#5333` task 1) | the only mechanism that can move the **existing** population's share | the design defers it *"until recall cost is measured"*; every read keeps the status predicate; the ≈48% figure is the **name-pattern** method's precision, **not the arbiter's** | **owner** |
| **2 · retire E1 as a closing bar, keep E1b** | the epic can close on the rows that measure the pipeline (E1b 90/90 no-loss; E2–E5) | the volume claim is dropped and the 62.3% class stays | owner (a metric decision, the same class as `#9736`) |
| **3 · leave E1 as it is** | nothing | an epic that cannot close, whose only lever is option 1 | — |

**Recommendation: measure before ruling, and the measurement is cheap.** The owner-labelled audit set already exists — `tests/eval/write_path/salience_labels.jsonl`, **25 names, owner-confirmed 2026-09-23**, with a `verdict` column — and the seam's arbiter has **never been scored against it**. One probe over those 25 rows (plus the 15 shape-in-scope ones) measures the arbiter's **agreement on the 25 `not_an_entity` rows** — a **specificity** reading, because the labelled set carries **no positive example** (all 25 verdicts are `not_an_entity`); recall on real entities must come from the corpus's planted units, not from this set. That specificity number is what option 1 needs and what option 2 would retire. **The ≈48% is the name-pattern's precision; using it to refuse the arbiter is refusing a method nobody has measured.** Run that probe — an hour, and scorable by hand if the vendor key is unavailable — then take option 1 or option 2 with a number in hand.

---

### 8.2 — The owner's goal now has an acceptance row: **DECIDED 2026-11-05 (option 1)**

**Status: decided.** The owner took **option 1** — add a sixth row — and redefined what the row measures, which is the part that mattered: *"the business logic of the packs should be followed so we're constructing the packs' business logic of connections as ontologies."* The row is therefore **per-pack declared → writable → written**, not a generic "is there a hierarchy" count. Recorded on the capstone (`#7949` comment `6664402530`) and on the epic (`#5087` comment `6664404259`); the implementing child is **`#11083`**, and the pre-registration amendment lands there, not by hand.

**What the capstone measured before this decision:** **five** rows — `E1` volume, `E2` retention, `E3` anchoring, `E4` operator edges, `E5` narrative fidelity (`docs/evidence/7949-capstone/README.md`). `E1b` (no-loss) and `E2b` are **proxies printed inside** E1/E2, not rows of their own — so "no row measures a hierarchy" was true of the row set, and the proxies did not change that.

**What the audit the owner asked for actually found** — run with the registry's own `relation_write_verdict`, not by reading manifests, over all 7 packs (**36 declared relations, 0 load errors**):

| finding | measured | consequence for the row |
|---|---|---|
| **32 of 36 relations are `writable`** | 4 are `predicate-not-in-allowlist`, **all four in `packs/project-management`** (`blocks`, `partOfSprint`, `assignedTo`, `onBoard` — legacy `from`/`to` shape, no `mechanism`) | a pack's business logic can be **declared and unwritable at once**; a per-pack row shows it, a total hides it |
| **2 packs declare a RETIRED semantics value** (4 declarations — ONTOLOGY §8 / `#7852`): `opposes`, `funds`, `produces`, `holds` | warn-not-block, so they survive load | alignment to the core vocabulary is part of "proper logic" |
| **No declared relation starts from a Subject** — companies/people are only *targets* (`venture:holds → Subject`) | verified across every relation | the graph can hold a company and **cannot reach from it to its goals or work** |
| **`goal` is a core kind with no edges** — **0 of 36** declared relations names `core:goal` as an endpoint; and **`initiative` is a synonym** of `dev:theme` (`packs/dev/manifest.yaml:61`) and of `venture:program` (`:134`), with no kind behind it | verified across all 7 manifests | the layer exists and is **unreachable** — so the fix is *declarations*, not ontology edits: **`#11000`** declares `dev:initiative` (a **pack** kind; **no `ONTOLOGY §5` change and no approval needed**), **`#11001`** materialises the four-level path and the three goal connections, and **`#11083`** measures them |

**Baseline for the row: 0 written Object→Object edges** — the same before-arm `#11001` names. The row starts red by construction rather than by accident.

⚠️ **The row's population is `Object→Object`, and the instrument that reports it counts one wider** (#11548 review, context note): `graph_census.aggregate.object_edges_total` sums every edge the write leg RECORDED and the graph verified, which includes the Subject-targeted `ownedBy`/`managedBy` edges (measured on `w2b-relations-2026-11-08-11237.json`: 35 recorded = 24 Object→Object + 11 Subject-targeted). The row is NOT moved by saying so — the after-arm must be compared like-for-like against this baseline, and the split needs no new write: each per-edge row of `relation_write.edges[]` carries its `predicate` and both ids.

**What this decision did *not* authorise, stated so it is not inferred later:** `ONTOLOGY §5` is untouched; `E1`/`E1b`/`E2–E5` keep their definitions, populations and bars; and the `wp02` (E4) and E5 population questions below stay open.

⚠️ **The sequencing cost the recommendation warned about is now live and is `#11083`'s to manage:** the row cannot register a delta until `#11001` exists, so the census ships its *declared vs writable* half first and its *written* half when the mechanism lands. That is a two-step row, and it is recorded rather than discovered at the capstone.

---

**A third, smaller hole in the same class: E5's population reconciliation is unowned.** `#9738` closed on its own *record* correction, not on a measurement; E5 is registered over **29 marker-bearing sentences** and measured over **25 bound qualifiers**, and `preregistration.json` says *"neither is final until the populations are reconciled"*. No issue owns that reconciliation today. It is a measurement, so it may belong to `#7949` rather than to this epic — but someone must own it, or E5 stays RED by attrition.

**A fourth hole, and it caps E4: its tenth miss has no owner.** `#8503`'s nine-loss split (4 selection + 5 endpoint recall) covers 9 of the 10 misses on the frozen **15**-edge denominator; the tenth is `wp02`, which `#9873` excluded from the criterion's 14-edge reading and which is a **content-level** miss, not a connect-level one. So **E4 cannot reach 15 of 15 on this decomposition even if both of its E4-owning children land** — closing it needs either `wp02` **excluded** from the 15-edge row by re-registration (making the row 14-of-14), or a content-level extraction fix that no issue owns. Recorded here rather than discovered at the capstone.

---

## 9. Reproduce

```bash
# the tracker state this document asserts (do not trust the epic body — re-run this)
for n in 4894 5086 5006 5007 5008 4899 2453 2730 2552 4716 1026 2714 2684 5009 9879 5333 5059 \
         8500 8503 9596 9736 9738 9775 10360 10000 10394 10176 10448 10449 10533 4997 4998; do
  gh issue view $n --json number,state,title --jq '"\(.number)\t\(.state)\t\(.title)"'
done

# the fleet door (the brief's claim, falsified)
grep -n "_classify_point_kind" tortoise/extractor.py tortoise/ingest.py
grep -n "_extract_session_v2\|extract_session_v2" tortoise/sdk.py tortoise/hosted_api.py | head

# the two absent channels
grep -n "OUTPUT_CONTRACT = " -A 20 tortoise/extractor_v2.py      # no entity↔entity array
grep -n "STRUCTURAL_REL_LABELS" -A 12 tortoise/projection/edges.py   # no hasPart / wasDerivedFrom

# the subclassOf bug (the loop's `parent` is unused)
grep -n "kind_subclasses.items" -A 4 tortoise/pack_registry.py

# the flag defaults
grep -n "def _vet_enabled\|def _value_gate_enabled\|def _classify_later_enabled" -A 6 tortoise/extractor_v2.py

# the L8 family's real state (the substrate map is stale on it)
gh issue view 9417 --json number,state,closedAt --jq '"\(.number)\t\(.state)\t\(.closedAt)"'
gh issue view 9727 --json number,state,closedAt --jq '"\(.number)\t\(.state)\t\(.closedAt)"'
gh issue view 9819 --json number,state,closedAt --jq '"\(.number)\t\(.state)\t\(.closedAt)"'

# the acceptance rows (re-run the probe; never edit a number)
uv run python docs/evidence/7949-capstone/probes/e1_e2_scores.py --out /tmp/e1-e2-scores.json
uv run python tools/capstone_verdict_check.py \
    docs/evidence/7949-capstone/capstone-measured-9152448d.log
```

**Revising this document.** A number here that a reader cannot re-derive from the commands above is a defect in the document, not a fact about the system. Re-measure before quoting; the epic body and the lane brief were both wrong in the same way.
