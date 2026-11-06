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
plausibly answered by three PRs landed the same evening (`#11213`, `#11212`, `#11211`). Each verdict below
is `ALREADY-DONE` or `STILL-MISSING`, with the evidence that decides it.

**Measured at:** `origin/main` = `80c912244f8fd5bf2fa36ec89aa341356d4113d9` (2026-11-06T21:33:54Z — the
`#11212` landing). The lane began at `af864a42a`; **`#11212` merged while it was being written**, and §0
records what that changed and what it did not. `git cat-file -e`/`git show origin/main:…` are the reads
below, never a bare `ls` of the checkout.

**Provenance:** every number read out of the tree, the scored artifacts, or the tracker is reproduced by a
command in §5. The two figures that are **quoted from research records rather than re-derived** — the
62.3 % definite-description share and `#4889`'s 13-type hosted census — are cited to their own sources
and are marked as such in §5.

**Verdict summary: 0 of 3 discharged — and the one blockage that *looked* cleared during the lane was
only half cleared, which is the finding worth reading (§2).**

| Child | Assertion | Verdict | Decisive evidence |
|---|---|---|---|
| `#10984` | A1 — declared part-whole relations become graph edges, **answering a rollup** | **STILL-MISSING** (its *blocker* is closed) | the declaration now reads and a writer exists, but nothing production calls it and no rollup query exists anywhere (§1) |
| `#10985` | A2 — S3 attaches instead of minting; E1 `after_share <= 0.50 × before_share` | **STILL-MISSING** | the HOLD seam it names **landed** (`#11212`, 21:33:54Z) and the share still moves `0.600000 → 0.600000` — but that HOLD covers **operator endpoints**, not the Object mint A2's population needs (§2) |
| `#10987` | A5 — `Subject` count above 26, four edge families non-zero | **STILL-MISSING** | the census reads `Subject: 19` and three of the four families are absent from a **complete** edge-type enumeration (§3) |

## 0. The world moved during this lane — and the verdicts did not

The lane's brief described `#11213`, `#11212` and `#11211` as merged. At the base the lane measured
(`af864a42a`), **two were merged and one was not** — `#11212` was an OPEN PR whose commits were reachable
only through its branch. It merged **2 m 49 s after** this record's previous head commit. Both facts are
recorded, because each one on its own is a trap:

| PR | closes | state | merge commit | on `origin/main`? |
|---|---|---|---|---|
| `#11213` | `#11001` | **MERGED** 2026-11-06T19:13:23Z | `e918f3d6d` | ✅ `merge-base --is-ancestor` → 0 |
| `#11211` | (part of `#11021` — **not** a PR close) | **MERGED** 2026-11-06T20:27:05Z | `aa414b4d5` | ✅ `merge-base --is-ancestor` → 0 |
| `#11212` | `#10997` | **MERGED** 2026-11-06T21:33:54Z | `80c912244` | ✅ **`80c912244` IS the `origin/main` tip** — but ❌ `merge-base --is-ancestor 78e8abae6` still exits **1**, because the landing was **squashed** |

⛔ **`git merge-base --is-ancestor` is a ONE-SIDED landing test: exit `1` is not a landing test, and
`0` is.** Exit **0** is sound — the commit is in `origin/main`'s history, so it landed, and that is what
the two ✅ rows above rest on (`e918f3d6d` and `aa414b4d5` both answer 0). Exit **1** proves nothing: at
`af864a42a` the branch commit `78e8abae6` was genuinely not an ancestor (the PR was open) and the check
answered correctly by accident; after the squash merge it *still* answers **1 for a PR that HAS landed**,
because the branch commit is not in the squashed merge commit's history at all. So a lane reading `1` as
"not landed" would park `#10985` on a seam that is already in the tree — and that is the trap, one
direction only. **The load-bearing read is the tree**: `git cat-file -e
origin/main:tortoise/identity_hold.py` → **PRESENT** (it was absent at `af864a42a`, hence the earlier
text), and `gh issue view 10997 --json state` → `CLOSED 2026-11-06T21:33:55Z`. `git log --all
--grep='#10997'` finds the commits either way — `--all` walks every ref, so it cannot distinguish a
landing from a branch. **So: an ancestry `1` and a `--all` grep both fail to answer "did it land"; a
`0` does answer it, and only a read of the tree answers "is its content here".**

⚠️ **`#11211` did not close `#11021`** — `gh pr view 11211 --json closingIssuesReferences` is empty
and its own body says `Part of #11021 — not Closes`, routing the remaining row to `#11239` (OPEN).
⛔ **And `#11021` does not read OPEN either**: `gh issue view 11021 --json state,closedAt` → `CLOSED`,
`closedAt 2026-11-06T20:27:06Z` — **one second after** the merge, i.e. a **manual close by the owner**,
not a PR close. So the rule stands and the fact is narrower than it looks: **neither** an OPEN issue with
a merged PR **nor** a CLOSED one is evidence the behaviour shipped — you have to read the behaviour. That
is why every verdict below cites the tree, never an issue's state.

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
- `tortoise pack validate` reports both lists. **Reproduced here** at `80c912244`, `packs/dev`, exit 0:

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
   index row — the count here is the pre-diff one, which is the one that decides the claim).
   `plan_hierarchy_edges` → **9 hits, all in the same test file**, plus four references in
   `tortoise/pack_hierarchy.py` (`:59` the module docstring, `:78` `__all__`, `:353` the definition,
   `:447` one internal call) — **no caller anywhere else in the tree**, `__main__.py` included (its
   `pack validate` reads `declared_hierarchy`/`hierarchy_diagnostics`, not the planner). The producer is
   a **planner/oracle**; `#11213`'s own body says so and routes instance pairing to `#10999`.
2. **`extractor_v2.py` calls none of the three edge-creating verbs**, so it cannot be the producer of any
   Object→Object edge, including the declared `hasPart` pairs: `grep -c -E
   'create_edge|create_about_edge|create_operator' tortoise/extractor_v2.py` → **0**. This is `#10984`'s
   own third bullet, still true after the merge.
   ⚠️ **Scoped on purpose.** The narrower claim is the one the command proves. "The extraction lane emits
   no structural edge of ANY kind" would be **false** — `references` is a valid structural predicate
   (`docs/ONTOLOGY.md` §3.9), and the measured run writes **7** of them (`(Source)-[:references]->…`,
   produced by `_EdgeHandlers.link_source_to_event` — `probes/capstone_measure.py:275-283`, reached from
   the capture path at `tortoise/sdk.py:17472`). (The probe's own docstring cites `sdk.py:17472` too —
   `#11212` corrected a stale `:17465` there while this lane ran, so there is nothing to fix at the
   probe.) What is missing is the *declared part-whole* edge, not every structural
   edge — and saying otherwise would send a lane off to build a writer the graph already has (the "second
   producer" the epic forbids).
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

## 2. A2 / `#10985` — STILL-MISSING, and its blocker **landed without moving the number**

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
- A2's mechanism is exactly the definite-description population: **62.3 % of Objects are `^(the )<X>`**
  (**4,900 / 7,859**, enumerated **2026-09-24** —
  `docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md:192,314`). ⛔ **`#4894` is the
  *mechanism* audit (50 real instances), NOT the ratio** — the epic's own research brief says so
  explicitly (`docs/epics/2026-11-05-5087-extractor-v4/02-research-brief.md:45`) and records attributing
  the ratio to `#4894` as a DEFECT it fixed in review round 1. `#11211` does not touch one of those
  Objects. ⚠️ The ratio is carried from a 7,859-Object population; today's is ~5,314 and has not been
  re-enumerated (`#10995`).
- `#11211` also ships **flag OFF** (`TORTOISE_REFERENTIALITY`), because plan R1 says measure before
  arming.

**What the shipped selection path does, measured — re-run AFTER `#11212` landed.** `probes/e1_e2_probe.py`
on the committed 25-candidate corpus, on a tree that contains `tortoise/identity_hold.py` (exit 3 =
`UNPREREGISTERED`, by design — a verdict needs a frozen threshold). **Selected lines** — the §5 command
prints all twelve arm lines; the six quoted here (plus the `e1` summary) are the ones that decide the
reading:

```text
e1 before_share=0.600000 after_share=0.600000 reduction=-0.000000 threshold=n/a observed=NO_REDUCTION verdict=UNPREREGISTERED
arm=before metric=objects total=25 numerator=15 denominator=25 share=0.600000
arm=after-vet-shipped metric=objects total=25 numerator=15 denominator=25 share=0.600000
arm=after-vet-arbiter metric=objects total=25 numerator=15 denominator=25 share=0.600000
arm=after-vet-shipped metric=vet-downgrades count=6
arm=before metric=real_facts count=90
arm=after-vet-shipped metric=real_facts count=90
```

Against the registered bar (`PRE-REGISTRATION.md:266-267`, `after_share <= 0.50 × before_share`,
`>= 90 of 90`):

- **E1: `0.600000 <= 0.300000` → FALSE.** The share does not move at all (`reduction=-0.000000` is
  `-0.0`, i.e. exactly zero). The semantic-arbiter arm moves it just as little.
- **E1b: `90 of 90` holds — vacuously.** Nothing is removed, so nothing is lost. A2's own issue says the
  two must move together; a control that holds only because the mechanism is absent is not a win.
- Why: `apply_vet` Rule 2 downgrades **all 3** mechanical discards back to `KEEP` (`vet-downgrades
  count=6`, 3 per pass), because each discarded entity is referenced by a surviving candidate — Layer-1
  referential integrity. That is structural, not a threshold.
- **And the 0.0 pp is a property of the CORPUS, not of S2.2 — the probe's own control shows it.** Strip
  the 9 `about_entities` references and the *same* arbiter arm then removes all 9
  (`control variant=unreferenced-definite-descriptions arm=after-vet-arbiter stripped_objects=9
  stripped_references=9 objects=16 share=0.375000 real_facts=90 removed=9`; the probe's own note: *"the
  0.0 pp is a corpus property (every Object wired to its statement), not a property of S2.2"*). So nothing
  is removed here because on this corpus every Object is defended by a reference — which is the same fact
  that makes E1b's `90 of 90` hold **vacuously**.
- And the mechanism that *would* move it is foreclosed: `#5333` (OPEN) records that a pass may not
  remove a committed row, and E1's share is moved by **no non-destructive mechanism**.

⛔ **The blocker `#10985` names by id has LANDED — and it did not move the number.** Its body: *"⛔ The id
is `#10997` (S3 — an unresolved identity is HELD, never minted)"*. `#10997` is **CLOSED
2026-11-06T21:33:55Z**; `tortoise/identity_hold.py` is **PRESENT** on `origin/main`; the share above was
re-measured on a tree containing it (`tests/test_10997_identity_hold.py`, 33 tests, green — and the
four-suite run below is 254).

⛔ **But the HOLD that landed is not the gate A2 needs, and an earlier revision of this record read it as
if it were.** The HOLD has exactly **one** kind (`identity_hold.py:125` `HOLD_ENDPOINT =
"operator_endpoint"`, and `KINDS = (HOLD_ENDPOINT,)`) and exactly **one** call site —
(`extractor_v2.py:9617`), inside `_mint_endpoint`, which fabricates a `statement` **Point** from an
operator ref. A2's population is the **Object** mint: every emitted entity is appended unfiltered at `extractor_v2.py:8880`
(`payload_entities.append({"name": name, "kind": kind, …})`) and minted at `sdk.py:16414-16428`. Nothing
filters it, and the tree's own registry says so in `#10997`'s own words —
`config/v4-mechanisms.yml`'s `failure_policy` row is still **`state: not-implemented`**, its note reads
*"the gate covers the MINTED candidate only"*, and it still carries the pin built to red the day a create
gate lands (`tests/test_5095_s3_identity_policy.py::TestTheRemainingAbsence::test_an_unresolved_lookup_still_mints`).
So `#10985`'s own premise — *"the **MINT** half does not — 'nothing suppresses a create'"* — is **still
true for Objects**, and the 0.0 pp above is explained by the arm that *did* run (Rule 2 re-KEEPs every
referenced entity), not by a HOLD the probe's arms never reach. Its other premise blocker, `#10996`, is
CLOSED, and S2.2's
mechanical half now defaults ON — the composition is `extractor_v2.py:11731-11732`
(`vet_enabled = (vet_arbiter is not None or _vet_enabled() or _value_gate_enabled())`), and the
default-ON itself lives at `tortoise/value_gate.py:81` (`is_truthy(os.environ.get(ENV_FLAG, "1"))`).
`extractor_v2.py:1154` is the **`TORTOISE_VET` toggle**, still OFF by default — cite it for that and
nothing else. ⚠️ Both cites are line numbers **at `80c912244`**: the forward merge that brought `#11212`
in inserted ~381 lines above them, and the two cites moved by **different** amounts —
`:11351-11352` → `:11731-11732` (**+380**) and `:1153` → `:1154` (**+1**) — so a bare "re-anchor on merge"
is not one arithmetic. That is the class of defect §0 exists to catch: a number true at one base and false
at the base the record claims. Which is why the 0.0 pp above is the **armed** result, not an unarmed one.

**Exact remaining gap for `#10985`:** (a) the attach rate must be a reported census number, per candidate,
with HOLD as a third bucket; (b) E1/E1b must be scored against the registered arithmetic rather than
reported as `NO_REDUCTION`; (c) it must state which non-destructive path moves the share, given `#5333`;
and **(d) the Object-mint HOLD/attach gate does not exist** — `#10997` gated operator endpoints only, so
the child's *"consume the seam or declare itself blocked by id"* is **still open** and has to be re-decided
against `#10997`'s actual scope (its own body names `_mint_endpoint`). (a)–(b) are the measurement half;
(c) and (d) are the hard ones, because the only arm that moves the share on this corpus is
`counterfactual-mint-skip` (`0.600000 → 0.375000`), a mechanism that does not exist.

## 3. A5 / `#10987` — STILL-MISSING, and already measured *below* its target

**The child's acceptance, quoted (`#10987` → Targets):**

> `Subject` count above 26 on a corpus the fleet actually captured, with the four edge families non-zero.

None of `#11213`, `#11212`, `#11211` touches the Subject path. Nothing at all in **the three changesets**
names `subject_binding`, `aboutSubject`, `memberOf`, `ownedBy` or `managedBy`. And the measurement the
child asks for **already exists**, and reads below the bar — on the C1 pre-registered corpus
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
production graph** (**2026-09-23**; complete 13-type inventory: `CONTAINS` 35,046 … `COMPLETED_STEP` 2 —
no `aboutSubject`/`memberOf`/`ownedBy`/`managedBy`; `:Subject` = **1**) and closed on a diagnosis that was
accurate **then**: the capture spine wrote **every** emitted entity through one label, so the extractor
named a Subject and the spine relabelled it an Object.

⛔ **That mechanism has since been FIXED, and it must not be relayed in the present tense** — the four ids
`#4889` routed it to are all **CLOSED**, and the fix landed in PR `#5230` (`cec2e0e957`, merged
2026-09-26T12:35:31Z, issue `#1370`) — `#4934` (CLOSED 2026-09-29T23:50:40Z, *"the capture entity spine
mints SUBJECT-kind entities as Objects"*) is the **report** whose own closure says **"No code change
remains"** and names `#5230` as the landing, so cite it for the diagnosis, not for the code. At the head
this record is measured on,
the write seam routes by kind (`tortoise/sdk.py:16414-16428`): `_ekind = _payload_entity_kind(e)` →
`_is_subject = is_subject_kind(_ekind or "")` → `create_entity("subject", …)` **or**
`create_entity("object", …)`. `#4938` (CLOSED 2026-09-28T15:02:50Z), `#1370` (CLOSED
2026-09-26T12:35:33Z) and `#1509` (CLOSED 2026-10-10T12:36:56Z) are closed too. So the relabelling is
**historical**, and a reader must not file it as a live defect.

The capstone's **19** is a *different graph* — a fresh, disposable, pre-registered corpus — so 1-vs-19 is
not a contradiction. Two explanations are live and this record does **not** choose between them:
`#10987`'s own **(3)** — *"the surface the owner measured is a different graph/deployment than the one
production serves"* — and a fourth this record adds, that **the kind→label routing landed between the two
measurements** (2026-09-26, PR `#5230` — not `#4934`, which is the report — vs the capstone run stamped
`9152448d7`, 2026-11-02), which would move
`:Subject` from ~1 to 19 with no change to the corpus. That is a **reading this record offers, not an
adjudication** — `#10987` owns the adjudication and has not made it; weighing (3) against the routing is
part of what it owes.

**Exact remaining gap for `#10987`:** (a) the binder's attach rate **per point** (resolved / no bindable
slot / probe unavailable), so explanation (1) — the fail-open `subject_binding_available` probe
degrading — stays distinguishable from (2) — the corpus has no bindable slots; (b) a corpus on which the
four families can be non-zero, or the finding that none exists (a **C1 corpus** finding, not a licence to
fabricate one); (c) the two customer-visible readers (search `subject` field, `get_org_structure`)
measured non-empty; (d) the explicit `#4889` adjudication — confirmed with a reason, or reopened.

## 4. What this changes for the epic

- **`#10984` is dispatchable.** Its `blocked-by #11001` is CLOSED; nothing else blocks it. It has no
  **implementation** lane and no implementation PR — ⚠️ the only open thing under `#10984` is **this
  record's own docs PR `#11265`**, which builds nothing. `tools/who-is-on.sh 10984 --repo .` names only
  this record's own surfaces — on a clean checkout the open PR and
  `refs/remotes/origin/reconcile/10984`, with **no local branch and no worktree** (a checkout that holds
  this record's own branch or worktree names those too: the same self-matching hazard this document warns
  about for its own `git grep`, in the tracker surface). ⛔ **And its exit status is not a stable verdict**:
  by contract it exits **0** for a complete answer and **2** — never 1 — when a surface could not be
  queried (`AGENTS.md`, the `who-is-on` verb), so the same command answers differently as the fleet
  board's readability changes. Both states were measured on this box while this record was written:
  **exit 0 / `2 holder(s) across 2 surface(s)`** (the open PR and the remote branch), reported on this PR
  at 22:07Z, and **exit 2 / holder surface UNQUERIED** at 22:36Z, from *both* the hub checkout and this
  worktree. So it never certifies that no lane holds `#10984`. An unmeasured
  surface is not a free one, which is the read-vs-verdict rule §0/§7 exist for. Treat `#10984` as
  dispatchable **subject to a clean `collision_preflight.py 10984` run** at dispatch time. The sequence is: census leg (§1(b)) → rollup query + ground truth
  (§1(c)) → re-run (d). ⛔ **Its `config/v4-mechanisms.yml` row does not exist today** — the file carries
  **13** mechanism ids (`vet`, `source_dedup`, `exact_dedup`, `near_dup_detect`, `near_dup_merge`,
  `against_priors_consolidation`, `span_link`, `embed_gate`, `sufficiency_router`, `fanout_cap`,
  `state_value_carve_out`, `atomic_point_write`, `failure_policy`) and **none for the declared-hierarchy
  mechanism**, despite `#10984`'s body asserting that `#11001` ships the `implemented` row. `#11001` is
  CLOSED and shipped no such row, so A1 is a mechanism that is now **implemented but undeclared** — the
  inverse of the `#5064` failure (*"a declared mechanism read exactly like a shipped one"*). ⛔ **`#10984`
  cannot simply take it:** its own body forecloses that (*"this child does not ship an `implemented` row and
  must not duplicate one"* — the row belongs to the producer, `#11001`). `#11001` is CLOSED without it, so
  the row is **orphaned** and needs its own tracker decision (reopen `#11001`, or file the declaration row
  against the mechanism registry), not a coin flip between two owners who both decline it.
- **`#10985` still has a seam gap, and it is narrower than "no seam blocker" would suggest.** `#10997`
  landed (21:33:55Z) and `#10996` is closed, so the *measurement* is unblocked and (a)–(b) of §2 can start
  today — but the HOLD it landed is operator-endpoint-only (`identity_hold.HOLD_ENDPOINT`, called from
  `_mint_endpoint`), so **(d) still needs an owner**: the Object-mint gate, or an explicit re-scope that
  says the attach rate is measured against a gate somebody else ships. Dispatching the measurement does not
  need `#11212` re-reviewed or re-landed — that PR is `main`.
- **`#10987` needs no producer work to be adjudicated; it needs a measurement and a decision.** Its
  census leg partly exists already; what is missing is the per-point attach rate and the `#4889` call.
- **None of the three is closed by tonight's merges.** Closing any of them on the strength of
  `#11213`/`#11212`/`#11211` would be the "a child closing is a claim" failure the epic's own rule exists
  to prevent — and `#11212` is the proof: it landed, `#10997` closed, and **not one number moved**.

## 5. Re-derive every number above

```bash
# §0 — the merge state, and the ancestry trap. The TREE is the load-bearing read, not history.
gh pr view 11213 --json state,mergeCommit,mergedAt
gh pr view 11211 --json state,mergeCommit,mergedAt,closingIssuesReferences
gh pr view 11212 --json state,mergeCommit,mergedAt        # MERGED 21:33:54Z, mergeCommit 80c912244
git rev-parse origin/main                                 # 80c912244 — the squash commit IS the tip
git fetch origin 78e8abae6b2cabc67d2cba75c53bfe7757c232e6   # BY SHA: the branch moved on, so
                                                          # without this the next line exits 128
                                                          # "Not a valid object name" — a sha read is
                                                          # reproducible only while the branch ref lives
git merge-base --is-ancestor 78e8abae6 origin/main; echo $?   # 1 EVEN THOUGH IT LANDED (the landing
                                                          # was squashed). 0 WOULD be conclusive; 1 and
                                                          # 128 ("cannot even ask") are both no-verdict
git cat-file -e origin/main:tortoise/identity_hold.py && echo PRESENT   # not `ls` — that reads the checkout
gh issue view 10997 --json state,closedAt                 # CLOSED 21:33:55Z
gh issue view 11021 --json state,closedAt                 # CLOSED, one second after its merge

# A1 (§1) — the declaration reads, and nothing production writes it
.venv/bin/python3 -m tortoise pack validate packs/dev      # 6 declared hierarchies, exit 0
git grep -n apply_hierarchy_edges                          # 9 in the test file + __all__ + def
git grep -n plan_hierarchy_edges                           # 9 in the test file + :59/:78/:353/:447
grep -c -E 'create_edge|create_about_edge|create_operator' tortoise/extractor_v2.py   # 0
grep -c '^  - id:' config/v4-mechanisms.yml                 # 13 — and none for the hierarchy mechanism
tools/who-is-on.sh 10984 --repo .                           # exit 0 = complete answer (2 holders on a
                                                          # clean checkout); exit 2 = a surface could not
                                                          # be queried — never read either as "nobody"
uv run python tools/collision_preflight.py 10984 --repo .    # the dispatch-time gate (exit 0 = clear)
grep -n -i -E 'hierarch|hasPart|part-whole|11001|10984' config/v4-mechanisms.yml      # no output
# …and the structural edges that DO exist on the capture path, which is why the claim above is scoped:
sed -n '275,283p' docs/evidence/7949-capstone/probes/capstone_measure.py   # `references`, via link_source_to_event
sed -n '17472p' tortoise/sdk.py                             # the call (`references`'s writer)
git grep -in 'rollup' -- '*.py' '*.md'                      # no graph rollup query anywhere
# ⚠️ self-matching once this record lands — exclude it, then the answer is 0:
git grep -rn -E 'hasPart|hierarchies|decomposesInto' -- 'docs/evidence/7949-capstone/' \
    ':!docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md'    # exit 1 = no output

# A2 (§2) — the shipped arm moves the share by exactly zero, on a tree that HAS #10997
.venv/bin/python3 docs/evidence/7949-capstone/probes/e1_e2_probe.py --json-out /tmp/e1e2.json \
    > /tmp/e1e2.log 2>&1                                   # exits 3 = UNPREREGISTERED, by design
grep -E '^e1 |metric=objects|vet-downgrades|metric=real_facts' /tmp/e1e2.log
grep -n -E 'after_share <= 0.50|>= 90 of 90' docs/evidence/7949-capstone/PRE-REGISTRATION.md
sed -n '11731,11732p' tortoise/extractor_v2.py; sed -n '81p' tortoise/value_gate.py   # the default-ON composition
sed -n '1154p' tortoise/extractor_v2.py                      # the `TORTOISE_VET` toggle (OFF by default)
# the 62.3 % share is QUOTED from a research record, re-read (not re-derived):
sed -n '192p;314p' docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md
sed -n '45p' docs/epics/2026-11-05-5087-extractor-v4/02-research-brief.md   # "#4894 is the mechanism audit"

# A5 (§3) — Subject 19, three of four families at zero
grep -n -A 12 '"label_counts"' docs/evidence/7949-capstone/capstone-measured-9152448d.log
grep -n -A 12 '"relationship_counts"' docs/evidence/7949-capstone/capstone-measured-9152448d.log
sed -n '16414,16428p' tortoise/sdk.py        # the kind→label routing at the write seam (#5230/#1370's fix)
for n in 4934 4938 1370 1509; do gh issue view $n --json state,closedAt -q '.state+" "+(.closedAt//"-")'; done
# #4889's 13-type hosted census is QUOTED from its scoping comment, not re-derived here:
gh issue view 4889 --json comments -q '.comments[].body' | grep -n -A 3 'CONTAINS` 35,046'
```

Suites run at `80c912244`, through `tools/test_lane.py run` (private lane container), all green:

| suite | command | result |
|---|---|---|
| four suites together | `tests/test_pack_hierarchy_11001.py tests/test_referentiality_11021.py tests/test_e1_e2_measurement.py tests/test_10997_identity_hold.py -q` | **254 passed**, 36.23 s, exit 0 |
| the four measurement pins | the two `test_referentiality_11021` corpus rows + `test_e1_share_is_the_pinned_number_with_both_counts` + `test_the_shipped_arm_moves_the_share_by_exactly_zero`, `-q -s` | **4 passed**, 6.25 s, exit 0 |

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
  **And the hypothetical in the previous revision of this bullet has now HAPPENED, which is why it stays
  a rule:** it read *"a new test asserting `tortoise/identity_hold.py` is absent would red `main` the day
  `#11212` merges"* — and `#11212` merged **2 m 49 s** after that revision's head commit. A pin on the
  *absence* of another lane's work reds the moment the work lands, which is the opposite of a guard. The
  record is reproducible instead: §5 re-derives every line.
- **It does not adjudicate `#4889`.** §3 offers the reading that reconciles the two measurements and says
  plainly it is a reading; `#10987` owns the adjudication.
- **It does not re-open `#5333`.** `#5333`'s ruling (a pass may not remove a committed row) is recorded
  and quoted, not challenged — which is why A2's remaining path must be non-destructive.

## 7. The transferable lesson (why §0 exists)

This record was **one merge away from shipping the wrong premise**, and nothing in it was wrong when
measured. Three reads disagreed and each was individually defensible:

| read | answered at `af864a42a` | answers now | why it is not the test |
|---|---|---|---|
| `git log --all --grep='#10997'` | "found" | "found" | walks every ref; cannot tell a branch from a landing |
| `git merge-base --is-ancestor 78e8abae6 origin/main` | 1 | **1** | a **squash** merge leaves the branch commit out of the merged history — it says "not landed" for a landed PR |
| `git cat-file -e origin/main:<the file the PR adds>` | absent | **present** | — the one that answers whether the *content* is here, and the only answer when ancestry returns 1 |

**So: an ancestry `1` answers nothing** (a squash landing reads as 1) — when a history read returns 1,
read the tree at `origin/main`; **a `0` is already conclusive**, which is why the two earlier rows above
can rest on it. And a measurement is a
*point-in-time* fact: this lane's own record had to be re-measured mid-flight, and the correction is in
§0–§2 rather than in a footnote. The verdicts survived the correction unchanged — which is the test that
they were about the **behaviour** and not about the merge.
