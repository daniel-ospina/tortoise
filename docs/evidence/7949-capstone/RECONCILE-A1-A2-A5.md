---
title: "RECONCILE — are A1/A2/A5 discharged by the 2026-11-06 merges? (#10984, epic #7949)"
type: evidence
domain: platform
doc_status: final
created: 2026-11-06
subjects.team: epistemic-team
aboutSubjects: Tortoise extractor v4
aboutObjects: "#7949, #10984, #10985, #10987, #11001, #11021, #10997, #10996, #11211, #11212, #11213, #5333, #4889, #11083, #10999, #9736"
---

# RECONCILE — are A1 / A2 / A5 discharged by the 2026-11-06 merges?

**This document is a de-duplication record, not an implementation.** Its whole job is to stop two lanes
building the same thing: three of epic `#7949`'s children — **`#10984` (A1 declared part-whole relations)**,
**`#10985` (A2 S3 attaches instead of minting)**, **`#10987` (A5 Subjects are populated)** — were
plausibly answered by three merges landed the same evening (`#11213`, `#11212`, `#11211`). Each verdict
below is `ALREADY-DONE` or `STILL-MISSING`, with the evidence that decides it.

**Measured at:** `origin/main` = `af864a42a626038c5f4c75d76ab145200570310e` (2026-11-06). Every number
below is reproduced by a command in §5; none is copied from a PR body.

**Verdict summary: 0 of 3 discharged. One of the three merges the premise names is not merged at all.**

| Child | Assertion | Verdict | Decisive evidence |
|---|---|---|---|
| `#10984` | A1 — declared part-whole relations become graph edges, **answering a rollup** | **STILL-MISSING** (its *blocker* is closed) | the declaration now reads and a writer exists, but nothing production calls it and no rollup query exists anywhere (§1) |
| `#10985` | A2 — S3 attaches instead of minting; E1 `after_share <= 0.50 × before_share` | **STILL-MISSING** | the shipped arm moves the share `0.600000 → 0.600000`; the HOLD seam it names is **unmerged** (§2) |
| `#10987` | A5 — `Subject` count above 26, four edge families non-zero | **STILL-MISSING** | the census reads `Subject: 19` and three of the four families are absent from a **complete** edge-type enumeration (§3) |

## 0. The premise correction — one of the three "merges" is an open PR

The lane brief describes `#11213`, `#11212` and `#11211` as "44 PRs merged tonight". Two are merged and
one is not, and the difference decides a verdict:

| PR | closes | state | merge commit | on `origin/main`? |
|---|---|---|---|---|
| `#11213` | `#11001` | **MERGED** 2026-11-06T19:13:23Z | `e918f3d6d` | ✅ `merge-base --is-ancestor` → 0 |
| `#11211` | (part of `#11021` — **not** a PR close) | **MERGED** 2026-11-06T20:27:05Z | `aa414b4d5` | ✅ `merge-base --is-ancestor` → 0 |
| `#11212` | `#10997` | ⛔ **OPEN** — `mergeCommit: null`, `mergedAt: null` | — | ❌ none of its commits are ancestors of `origin/main` |

The `#11212` branch does carry the work (`78e8abae6 fix(10997): an unresolved identity is HELD, never
minted`), and `git log --all --grep='#10997'` finds it — which is exactly the trap: `--all` walks every
ref, so a branch-only commit reads like a landing. `git merge-base --is-ancestor 78e8abae6 origin/main`
exits **1**, and `tortoise/identity_hold.py` — the module the PR adds — **does not exist** on
`origin/main`. A lane that measured with `--all` would have reported A2's blocker as closed.

⚠️ **`#11211` did not close `#11021`** — `gh pr view 11211 --json closingIssuesReferences` is empty
and its own body says `Part of #11021 — not Closes`, routing the remaining row to `#11239` (OPEN).
⛔ **And `#11021` does not read OPEN today either**: `gh issue view 11021 --json state,closedAt` →
`CLOSED`, `closedAt 2026-11-06T20:27:06Z` — **one second after** the merge, i.e. a **manual close by the
owner**, not a PR close. The merge did not discharge the issue; the owner closed it. So the rule stands
and the fact is narrower than it looks: **neither** an OPEN issue with a merged PR **nor** a CLOSED one
is evidence the behaviour shipped — you have to read the behaviour. That is why the verdicts below cite
the tree, never an issue's state.

## 1. A1 / `#10984` — STILL-MISSING, but **unblocked**

**The child's acceptance, quoted (`#10984` → Targets):**

> `N > 0` edges of the declared types, and the edge count alone is **not** the assertion — the rollup must be right.

**What `#11213` actually shipped** (it closes `#11001`, which `#10984` lists as `blocked-by`). This is a
real, substantial half — do not understate it:

- `PackManifest.hierarchies` now carries the declaration (`tortoise/pack_registry.py`).
- `tortoise/pack_hierarchy.py::declared_hierarchy()` resolves each entry to a namespaced
  `(from_kind, predicate, to_kind)`; `relation_write_paths()` binds it to a writer.
- `plan_hierarchy_edges()` filters candidate pairs to declared ones; `apply_hierarchy_edges()`
  (`pack_hierarchy.py:419`) writes them through `sdk.link_entities` → `create_edge`.
- `tortoise pack validate` reports both lists. **Reproduced here**, `packs/dev` at `af864a42a`, exit 0:

```text
  declared hierarchies (ontology.hierarchies, #11001):
    ✓ dev: dev:epic → dev:issue [hasPart via structural]
    ✓ dev: dev:theme → dev:epic [hasPart via structural]
    ✓ marketing: marketing:campaign → marketing:content [hasPart via structural]
    ✓ product-strategy: product-strategy:product → product-strategy:feature [hasPart via structural]
    ✓ venture: venture:investment → venture:program [hasPart via structural]
    ✓ venture: venture:program → venture:asset [hasPart via structural]
✓ Clean — pack(s) validate
```

So the declaration IS read (6 declared, each bound to `hasPart via structural`) — that half of `#10984`'s
gap is **closed**, and `#10984`'s blocker `#11001` is **CLOSED** (2026-11-06T19:13:25Z). Reporting
`#11001`'s merge as "A1 done" would be the wrong read, for four measured reasons:

1. **Nothing in production calls the writer.** `git grep -n apply_hierarchy_edges` → **9 hits, all in
   `tests/test_pack_hierarchy_11001.py`**, plus its own `__all__` entry and definition in
   `tortoise/pack_hierarchy.py` (re-running it after this record lands also matches this file and its
   index row — the count below is the pre-diff one, which is the one that decides the claim).
   `plan_hierarchy_edges` → **9 hits, all in the same test file**, plus four references in
   `tortoise/pack_hierarchy.py` (`:59` the module docstring, `:78` `__all__`, `:353` the definition,
   `:447` one internal call) — **no caller anywhere else in the tree**, `__main__.py` included (its
   `pack validate` reads `declared_hierarchy`/`hierarchy_diagnostics`, not the planner). The producer is
   a **planner/oracle**; `#11213`'s own body says so and routes instance pairing to `#10999`.
2. **The extraction lane still emits no structural edge of any kind.** `grep -c -E
   'create_edge|create_about_edge|create_operator' tortoise/extractor_v2.py` → **0**. This is `#10984`'s
   own third bullet, still true after the merge.
3. **The census cannot observe the edge even if it existed.** `probes/capstone_measure.py:2238-2243`
   enumerates **every** relationship type (`MATCH ()-[r]->() RETURN type(r) …`), and the registered
   scored census (`capstone-measured-9152448d.log:299-309`) lists 9 types — `CONTAINS` 309, `IMPL` 65,
   `INPUT` 68, `NAND` 14, `aboutObject` 248, `aboutSubject` 21, `extractedFrom` 150, `mitigated_by` 11,
   `references` 7 — with **`hasPart` absent**. Because the query is complete, absence from that list is
   `hasPart = 0`, not "not counted". `git grep -rn -E 'hasPart|hierarchies|decomposesInto'
   docs/evidence/7949-capstone/` → **0 hits on the pre-diff tree**: A1's leg is not in the census at
   all. ⚠️ That command is **self-matching** once this record lands — every hit is then this file's own
   lines — so re-derive it with the file excluded:
   `git grep -rn -E 'hasPart|hierarchies|decomposesInto' -- 'docs/evidence/7949-capstone/' \
   ':!docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md'` → **exit 1, no output**.
4. **The rollup query does not exist.** `git grep -in 'rollup' -- '*.py' '*.md'` over the tree returns
   only unrelated uses (extractor `_rollup_llm`/`_rollup_recovery` stage counters, a CI per-name rollup
   discussion, a plan doc). There is **no** query of the graph shape "which work items serve this goal,
   and what evidence supports them", no ground truth, and no scoring — which is the half `#10984` says
   `#11001` does **not** own and is "the half most likely to be dropped".

**Exact remaining gap for `#10984`** (states its own scope, writes no producer):
(a) invoke the `#11213` planner at a real call site, or declare that `#11083`/`#10999` own the invocation
and re-scope to measurement-only; (b) add the declared-edge leg to the census so `0 → N` is observable at
all; (c) define the rollup query, hand-label its ground truth, pre-state the scoring; (d) re-run on the
pre-registered corpus. **`#10984` is now dispatchable** — its named blocker closed.

## 2. A2 / `#10985` — STILL-MISSING, and its named blocker is **not merged**

**The child's acceptance, quoted (`#10985` → Indicators/Targets):**

> **E1 `after_share ≤ 0.50 × before_share` AND E1b `≥ 90 of 90`** — the registered thresholds, not a home-made one.

and

> The **attach rate** (attached-to-existing vs minted-new) is a reported number on the census, before vs after, on the same pre-registered corpus.

**What `#11211` shipped:** a referentiality cue (`tortoise/referentiality.py`) that records a **mention**
instead of minting, for phrases whose referent is not recoverable from the phrase — **pronouns,
deictics, generic heads**. Its scope is explicitly *not* A2's population:

- `#11211`'s own measurement, reproduced here by
  `tests/test_referentiality_11021.py::test_the_COMMITTED_capstone_corpus_is_not_touched`:
  `[#11021] capstone corpus: 25 surfaces, 0 held, 25 still mint` — the cue holds **zero** of the capstone
  corpus's surfaces.
- and the reason, from `test_the_25_row_label_set_is_the_ARBITERS_class_not_this_cue_s`: the owner's
  25-row `not_an_entity` set is **15 definite descriptions + 10 identifier/pointer rows, with no
  pronoun/deictic/generic row at all**, so this cue's hit count on it is **0 by construction**.
- A2's mechanism is exactly the definite-description population: `#4894`'s audit found **62.3 % of
  Objects are `^(the )<X>`**. `#11211` does not touch one of them.
- `#11211` also ships **flag OFF** (`TORTOISE_REFERENTIALITY`), because plan R1 says measure before
  arming.

**What the shipped selection path does, measured.** `docs/evidence/7949-capstone/probes/e1_e2_probe.py`
on the committed 25-candidate corpus, at `af864a42a` (exit 3 = `UNPREREGISTERED`, by design — a verdict
needs a frozen threshold):

```text
e1 before_share=0.600000 after_share=0.600000 reduction=-0.000000 threshold=n/a observed=NO_REDUCTION verdict=UNPREREGISTERED
arm=before metric=objects total=25 numerator=15 denominator=25 share=0.600000
arm=after-vet-shipped metric=objects total=25 numerator=15 denominator=25 share=0.600000
arm=after-vet-shipped metric=vet-downgrades count=6
arm=before metric=real_facts count=90
arm=after-vet-shipped metric=real_facts count=90
```

Against the registered bar (`PRE-REGISTRATION.md:266-267`, `after_share <= 0.50 × before_share`,
`>= 90 of 90`):

- **E1: `0.600000 <= 0.300000` → FALSE.** The share does not move at all (`reduction=-0.000000` is
  `-0.0`, i.e. exactly zero).
- **E1b: `90 of 90` holds — vacuously.** Nothing is removed, so nothing is lost. A2's own issue says the
  two must move together; a control that holds only because the mechanism is absent is not a win.
- Why: `apply_vet` Rule 2 downgrades **all 3** mechanical discards back to `KEEP` (`vet-downgrades
  count=6`, 3 per pass), because each discarded entity is referenced by a surviving candidate — Layer-1
  referential integrity. That is structural, not a threshold.
- And the mechanism that *would* move it is foreclosed: `#5333` (OPEN) records that a pass may not
  remove a committed row, and E1's share is moved by **no non-destructive mechanism**.

**The blocker `#10985` names by id is unmerged.** Its body: *"⛔ The id is `#10997` (S3 — an unresolved
identity is HELD, never minted)"* and *"this child consumes `#5087`'s seam or declares itself blocked by
id"*. `#10997`'s only implementation is PR **`#11212` — OPEN** (§0); `tortoise/identity_hold.py` is
**absent** from `origin/main`. Its other premise blocker, `#10996`, is CLOSED and S2.2's mechanical half
now defaults ON — the composition is `extractor_v2.py:11350-11352`
(`vet_enabled = (vet_arbiter is not None or _vet_enabled() or _value_gate_enabled())`), and the
default-ON itself lives at `tortoise/value_gate.py:81` (`is_truthy(os.environ.get(ENV_FLAG, "1"))`).
`extractor_v2.py:1153` is the **`TORTOISE_VET` toggle**, still OFF by default — cite it for that and
nothing else. Which is why the 0.0 pp above is the **armed** result, not an unarmed one.

**Exact remaining gap for `#10985`:** (a) `#10997`'s HOLD seam must land (it is a PR, not a decision);
(b) the attach rate must be a reported census number, per candidate, with HOLD as a third bucket;
(c) E1/E1b must be scored against the registered arithmetic rather than reported as `NO_REDUCTION`;
(d) it must state which non-destructive path moves the share, given `#5333`.

## 3. A5 / `#10987` — STILL-MISSING, and already measured *below* its target

**The child's acceptance, quoted (`#10987` → Targets):**

> `Subject` count above 26 on a corpus the fleet actually captured, with the four edge families non-zero.

None of `#11213`, `#11212`, `#11211` touches the Subject path. Nothing at all in the three merges names
`subject_binding`, `aboutSubject`, `memberOf`, `ownedBy` or `managedBy`. And the measurement the child
asks for **already exists**, and reads below the bar — on the C1 pre-registered corpus
(`capstone-measured-9152448d.log:265-311`, `9152448d7`):

```text
"label_counts": { … "Object": 64, "Point": 354, … "Subject": 19 }
"relationship_counts": {
  "CONTAINS": 309, "IMPL": 65, "INPUT": 68, "NAND": 14,
  "aboutObject": 248, "aboutSubject": 21, "extractedFrom": 150,
  "mitigated_by": 11, "references": 7 }
```

- `Subject` = **19**, which is **below** A5's `above 26`.
- `aboutSubject` = **21** (non-zero).
- `memberOf`, `ownedBy`, `managedBy` — **absent** from a *complete* edge-type enumeration, i.e. **0**.
  So **three of the four required families are at zero**, and the "count alone is not the indicator"
  clause in A5's own body is satisfied by a corpus that fails both halves of the target anyway.

**The `#4889` contradiction is reconcilable, and here is the reading.** `#4889` measured the **hosted
production graph** (complete 13-type inventory: `CONTAINS` 35,046 … `COMPLETED_STEP` 2 — no
`aboutSubject`/`memberOf`/`ownedBy`/`managedBy`; `:Subject` = **1**), and closed after establishing the
mechanism: the capture spine writes **every** emitted entity through one label —
`create_entity("object", …)` — so the extractor names a Subject and the spine relabels it an Object
(routed to `#4934`/`#4938`/`#1370`/`#1509` as non-goals). The capstone's **19** is a *different graph* —
a fresh, disposable, pre-registered corpus — so 1-vs-19 is not a contradiction, it is explanation (3) in
`#10987`'s own list: *"the surface the owner measured is a different graph/deployment than the one
production serves"*. That is a **reading this record offers, not an adjudication** — `#10987` owns the
adjudication and has not made it.

**Exact remaining gap for `#10987`:** (a) the binder's attach rate **per point** (resolved / no bindable
slot / probe unavailable), so explanation (1) — the fail-open `subject_binding_available` probe
degrading — stays distinguishable from (2) — the corpus has no bindable slots; (b) a corpus on which the
four families can be non-zero, or the finding that none exists (a **C1 corpus** finding, not a licence to
fabricate one); (c) the two customer-visible readers (search `subject` field, `get_org_structure`)
measured non-empty; (d) the explicit `#4889` adjudication — confirmed with a reason, or reopened.

## 4. What this changes for the epic

- **`#10984` is dispatchable now.** Its `blocked-by #11001` is CLOSED; nothing else blocks it. It has no
  implementation lane and no PR, so the sequence is: census leg (§1(b)) → rollup query + ground truth
  (§1(c)) → re-run (d). Its `config/v4-mechanisms.yml` row stays `#11001`'s: **`#11001` ships the
  `implemented` row** (it owns `code:`/`symbols:`); `#10984` ships at most `tracking_issue:` + `pins:` —
  one line, so the row cannot end up orphaned between two owners.
- **`#10985` cannot start its measurement premise until `#11212` merges.** Dispatching it now would have
  it "consume a seam" that is a branch. Review/land `#11212` first — it is a PR, not a decision.
- **`#10987` needs no producer work to be adjudicated; it needs a measurement and a decision.** Its
  census leg partly exists already; what is missing is the per-point attach rate and the `#4889` call.
- **None of the three is closed by tonight's merges.** Closing any of them on the strength of
  `#11213`/`#11212`/`#11211` would be the "a child closing is a claim" failure the epic's own rule exists
  to prevent.

## 5. Re-derive every number above

```bash
# the merge-state correction (§0) — a branch-only commit is not a landing
gh pr view 11213 --json state,mergeCommit,mergedAt
gh pr view 11211 --json state,mergeCommit,mergedAt,closingIssuesReferences
gh pr view 11212 --json state,mergeCommit,mergedAt          # OPEN, mergeCommit: null
gh issue view 11021 --json state,closedAt                   # CLOSED, one second after the merge
gh issue view 4889 --json state,closedAt,title
git merge-base --is-ancestor 78e8abae6 origin/main; echo $?  # 1
ls tortoise/identity_hold.py                                 # absent

# A1 (§1) — the declaration reads, and nothing production writes it
.venv/bin/python3 -m tortoise pack validate packs/dev         # 6 declared hierarchies, exit 0
git grep -n apply_hierarchy_edges                            # tests + definition only
grep -c -E 'create_edge|create_about_edge|create_operator' tortoise/extractor_v2.py   # 0
git grep -in 'rollup' -- '*.py' '*.md'                       # no graph rollup query anywhere
# ⚠️ self-matching once this record lands — exclude it, then the answer is 0:
git grep -rn -E 'hasPart|hierarchies|decomposesInto' -- 'docs/evidence/7949-capstone/' \
    ':!docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md'    # exit 1 = no output

# A2 (§2) — the shipped arm moves the share by exactly zero
.venv/bin/python3 docs/evidence/7949-capstone/probes/e1_e2_probe.py --json-out /tmp/e1e2.json \
    > /tmp/e1e2.log 2>&1                                  # exits 3 = UNPREREGISTERED, by design
grep -E '^e1 |metric=objects|vet-downgrades|metric=real_facts' /tmp/e1e2.log
grep -n -E 'after_share <= 0.50|>= 90 of 90' docs/evidence/7949-capstone/PRE-REGISTRATION.md
sed -n '11350,11352p' tortoise/extractor_v2.py; sed -n '81p' tortoise/value_gate.py   # the default-ON composition

# A5 (§3) — Subject 19, three of four families at zero
grep -n -A 12 '"label_counts"' docs/evidence/7949-capstone/capstone-measured-9152448d.log
grep -n -A 12 '"relationship_counts"' docs/evidence/7949-capstone/capstone-measured-9152448d.log
```

Suites run at `af864a42a`, through `tools/test_lane.py run` (private lane container), all green:

| suite | command | result |
|---|---|---|
| three suites together | `tests/test_pack_hierarchy_11001.py tests/test_referentiality_11021.py tests/test_e1_e2_measurement.py -q` | **219 passed**, 34.75 s, exit 0 |
| the three measurement pins | the two `test_referentiality_11021` corpus rows + `test_e1_share_is_the_pinned_number_with_both_counts` + `test_the_shipped_arm_moves_the_share_by_exactly_zero`, `-q -s` | **4 passed**, 6.25 s, exit 0 |

## 6. What this document does NOT do, and why it adds no gate

- **It implements nothing.** No producer, no attach step, no Subject writer, no rollup query — for the
  same reason the child says: writing a second producer is the failure the epic forbids.
- **It adds no test and no CI gate on purpose.** The facts it relies on are of **three** kinds, and
  each is pinned where it belongs — or is explicitly **not** a pin:
  1. **Pinned numbers** — the E1 shares by `tests/test_e1_e2_measurement.py`
     (`MEASURED_BEFORE_SHARE = 0.60`, `MEASURED_SHIPPED_REDUCTION = 0.0`) and the 0-of-25 corpus arm by
     `tests/test_referentiality_11021.py`.
  2. **A tracker fact** — the merge states, with the re-derivation command written out in §5.
  3. **⛔ A MEASUREMENT RECORD, not a pin** — the §3 census figures (`Subject: 19`, `aboutSubject: 21`,
     the 9 relationship types with no `hasPart`) are read out of a committed scored log
     (`capstone-measured-9152448d.log`), and this bundle's own rule is that a row moves only by
     **re-running the probe**, never by editing a document. Nothing asserts them; a re-run supersedes
     them. A5's verdict rests on that kind of number, and saying so is more useful than implying a gate
     guards it.
  A new test asserting "`tortoise/identity_hold.py` is absent" would **red `main` the day `#11212` — a PR
  this record says SHOULD land — merges**. That is a false block on correct work, and building machinery
  to police a document is the category-B drift the fleet's own rules exist to stop. The record is
  reproducible instead: §5 re-derives every line.
- **It does not adjudicate `#4889`.** §3 offers the reading that reconciles the two measurements and says
  plainly it is a reading; `#10987` owns the adjudication.
- **It does not re-open `#5333`.** `#5333`'s ruling (a pass may not remove a committed row) is recorded
  and quoted, not challenged — which is why A2's remaining path must be non-destructive.
