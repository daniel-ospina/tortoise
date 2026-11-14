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
| `#10984` | A1 — declared part-whole relations become graph edges, **answering a rollup** | **STILL-MISSING** (its *blocker* is closed) | as measured, the declaration read and a writer existed with no production caller; ⛔ **the caller landed after this record's base (`#11548`, `relation_write.py`)**, so the basis is now "the writer landed, the corpus was not re-measured" — see the fifth-landing amendment in §0, and no rollup query exists anywhere (§1) |
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
| `#11212` | `#10997` | **MERGED** 2026-11-06T21:33:54Z | `80c912244` | ✅ **`80c912244` is an ANCESTOR of `origin/main`** (`merge-base --is-ancestor 80c912244 origin/main` → 0) — it was the tip when this record was measured, and it is no longer (a commit count here would be stale the day after it was written), so the DURABLE test is the ancestor one, never tip-equality. And ❌ `merge-base --is-ancestor 78e8abae6` still exits **1**, because the landing was **squashed** |
| `#11205` | `#10999` (closed by its own merge commit — `Closes #10999` rode `316a572ce2`'s message; ⛔ `closingIssuesReferences` is empty because the keyword rode the commit, **not** the PR body, so the empty field is **not** evidence of a manual close) | **MERGED** 2026-11-06T22:40:32Z — **after the base, before this record merged** | `316a572ce2` | ✅ `merge-base --is-ancestor` → 0; and it is the landing that moved one of §5's own counts (below) |

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

⚠️ **A fourth landing arrived between the base and this record's merge — and it is why one recipe below
no longer prints its base number.** `#11205` (S5 Job 1, `316a572ce2`, 2026-11-06T22:40:32Z) added an
**entity↔entity `relations` channel** to the extractor contract, whose predicate allowlist includes
`hasPart` (`tortoise/extractor_v2.py:1773`). It does **not** discharge A1: it is **transport only** —
nothing in `tortoise/` consumes `result["relations"]` (`extractor_v2.py:12397`, and
`git grep -n 'result\["relations"\]' -- tortoise/` prints that comment and no consumer at all), and
materialising them as Object→Object edges is **`#11237`** (OPEN). ⛔ **FALSE in this record's merged
tree — see the fifth-landing amendment below:** the consumer landed as **`#11548`**, and that grep now
prints three lines. The paragraph is kept as what was measured at this record's base. ⚠️ **The scope is `tortoise/`, and it is
load-bearing:** tree-wide the channel HAS a reader — `tests/test_10999_batch_story_relations.py` names
`result["relations"]` on **56 lines** (**59 occurrences**, three lines naming it twice) at `origin/main`
(`62248db88`), pinning the contract
against model output — so "no consumer at all" would be false of the tree. What is missing is a producer,
not a reader; the two greps in §5 print both sides. What it *does* change is this record's
evidence surface: the bare token count of the three edge-creating verbs prints **4** on `origin/main`
and **0** at the base, because #11205's own explanation names `sdk.create_operator` in four comments —
`git grep -n 'create_operator' -- tortoise/extractor_v2.py` prints exactly four, and none of them is a
call — but ⚠️ **those four line numbers are `origin/main`'s, not this record's base's**: they are `:8938`,
`:8956`, `:8963` and `:9343` at `origin/main` (`62248db88`; `#11361`, `7491da3fe`, moved them), while the
same grep prints **0** at this record's base `80c912244`, and four at `:8892`, `:8910`, `:8917` and
`:9231` at `de72cfd45` (the last commit to touch THIS record, `#11326`). The conclusion that matters —
**zero calls** — holds at every one of those bases (the call-shaped grep prints nothing at all three); the
mention count and its line numbers do not, which is this section's own lesson. §1's second reason therefore
stands unchanged, and both §1 and §5 carry the call-shaped command, which a comment cannot satisfy. That
is §7's lesson one merge later: **a count is only as good as the base it was taken at.**

⚠️ **A fifth landing arrived between the base and this record's merge, and it reaches the seam this
record said nothing called.** `#11237`'s write path landed as **`#11548`** (`e49788706a`, *feat(extractor):
S5 Job 1 remainder — the relations channel gets a write path (`#11237`)*): a new module
`tortoise/relation_write.py` is the consumer `#10999` did not have, called from
`TortoiseSDK._extract_session_v2` at `tortoise/sdk.py:19012` (`relation_write = apply_relation_edges(` —
the `from tortoise.relation_write import apply_relation_edges` is the line above it, `:19011`, and the
binding below is the result), fed **only** by
the typed record (`out["relations"]`) and never by the story text (`#10000`). Two measurements, both
taken on this merge:

- `git grep -n 'result\["relations"\]' -- tortoise/` → **3** lines (`extractor_v2.py:12397`,
  `relation_write.py:7`, `sdk.py:18999`), against the *"that comment and no consumer at all"* this
  record was written with.
- `git grep -n 'plan_hierarchy_edges' -- tortoise/` → **8** lines, four of them in `relation_write.py`
  and one of them the **call** (`:700`): a declared hierarchy over a pair the story *states* is routed
  through `plan_hierarchy_edges` and written as the **declared** structural predicate through
  `sdk.link_entities` → `create_edge` — the seam `#11001` documented and nothing called, now called.
  (`tests/` gains two hits as well, `test_relation_write_11237.py`, so *"all in the same test file"* is
  no longer true either.)

⛔ **This does NOT move A1.** The leg fires only when a loaded pack DECLARES a hierarchy over the two
kinds AND the story STATES that pair, and only its `structural` arm writes: the `operator` arm is refused
here because these endpoints are **Objects, not Points** (`relation_write.py`'s own docstring). Whether
the capstone's corpus produces such a pair is **unmeasured** — no probe was re-run for this merge, and a
row moves only by re-running one. What changed is narrower and still material: **A1 can no longer be
argued from "nothing production calls the planner."** The row keeps the value it was measured at, and its
basis is now *the writer landed, the corpus was not re-measured* — `#11237` stays OPEN for the remainder.

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
  (`pack_hierarchy.py:431`) writes them through `sdk.link_entities` → `create_edge`.
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
   `tortoise/pack_hierarchy.py` (`:59` the module docstring, `:78` `__all__`, `:365` the definition,
   `:459` one internal call) — **no caller anywhere else in the tree**, `__main__.py` included (its
   `pack validate` reads `declared_hierarchy`/`hierarchy_diagnostics`, not the planner).
   ⛔ **Superseded in the merged tree — but only for the PLANNER: `#11548` added a production caller
   of `plan_hierarchy_edges`**, at `relation_write.py:700` (`planned = plan_hierarchy_edges(…)`) — see
   the fifth-landing amendment in §0. It did **not** add a caller of the WRITER: `apply_hierarchy_edges`
   still has zero production callers (`git grep apply_hierarchy_edges -- tortoise/` → **two** hits,
   the `__all__` entry and the `def`, i.e. no call at all), so this item's
   heading stands and only the planner half of it is superseded. The producer is
   a **planner/oracle**; `#11213`'s own body says so and routes instance pairing to `#10999` (now
   **CLOSED**; its remainder is **`#11237`**, OPEN).
2. **`extractor_v2.py` calls none of the three edge-creating verbs**, so it cannot be the producer of any
   Object→Object edge, including the declared `hasPart` pairs:
   `git grep -nE '(^|[^[:alnum:]_])(create_edge|create_about_edge|create_operator)[[:space:]]*\(' tortoise/extractor_v2.py` → **no output**, exit 1. This is `#10984`'s own third bullet, still true
   after the merge. ⚠️ **This spelling is the one printed here on purpose**, and it must stay on ONE
   line: the earlier `'\.(create_edge|…)\('` form exits 1 on an UNQUALIFIED
   `return create_operator(payload)`, while a `[^[:alnum:]_.]` class (which EXCLUDES the dot) exits 1
   on the receiver-qualified `sdk.create_edge(payload)` — each is blind to one half (both measured), and
   a wrap across two lines drops the pathspec and greps the whole tree. The tokenized pin in
   `tests/test_7949_capstone_measure.py` is the complete check; this command is its
   hand-reproducible form.
   ⛔ **Do not use the bare token count.** `grep -c -E 'create_edge|create_about_edge|create_operator'`
   printed **0** at this record's base and prints **4** on `origin/main`, because `#11205`'s explanation
   names `sdk.create_operator` in four comments — a count of *mentions*, not of calls. The call-shaped
   command above cannot be satisfied by prose, which is why it is the one recorded here.
   ⚠️ **Scoped on purpose.** The narrower claim is the one the command proves. "The extraction lane emits
   no structural edge of ANY kind" would be **false** — `references` is a valid structural predicate
   (`docs/ONTOLOGY.md` §3.9), and the measured run writes **7** of them (`(Source)-[:references]->…`,
   produced by `_EdgeHandlers.link_source_to_event` — `probes/capstone_measure.py:275-283`, reached from
   the capture path at `tortoise/sdk.py:19452`). (The probe's own docstring cites it too and was
   re-derived with it — `#11212` had already corrected a stale `:17465` there, and `#11459`'s +102
   insertion then moved both, and `#11835`'s seam stamp moved them once more; both now read
   `:19452`.)  <!-- #10226 removed 26 SDK methods above this writer: the prose cite and the `sed` were re-derived with it, this bare parenthetical was not — re-derived here off `grep -n '^ *proj\.link_source_to_event' tortoise/sdk.py`. --> What is missing is the *declared part-whole* edge, not every structural
   edge — and saying otherwise would send a lane off to build a writer the graph already has (the "second
   producer" the epic forbids).
3. **The census cannot observe the edge even if it existed.** `probes/capstone_measure.py:2747-2749`
   enumerates **every** relationship type (`MATCH ()-[r]->() RETURN type(r) …`), and the registered
   scored census (`capstone-measured-9152448d.log:299-309`) lists 9 types — `CONTAINS` 309, `IMPL` 65,
   `INPUT` 68, `NAND` 14, `aboutObject` 248, `aboutSubject` 21, `extractedFrom` 150, `mitigated_by` 11,
   `references` 7 — with **`hasPart` absent**. Because the query is complete, absence from that list is
   `hasPart = 0`, not "not counted". ⚠️ The bare `git grep -rn -E
   'hasPart|hierarchies|decomposesInto' -- 'docs/evidence/7949-capstone/'` is **self-matching** — at
   `4e6a3ce612bd^` every one of its hits is this file's own lines — so the operand has to name the
   bundle alone, with this file excluded:
   `git grep -rn -E 'hasPart|hierarchies|decomposesInto' -- 'docs/evidence/7949-capstone/' \
   ':!docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md'` → **all 42 hits are the bundle's own registration**
   (#10982 landed it after this record was written) — the bundle CITES the predicate, it does not
   produce it; the producer claim is the call-shaped grep above, which is still empty. The **0 hits**
   the `exit 1` in earlier revisions of this record was taken at is that EXCLUDED command in the tree
   *before* `#10982` landed (`4e6a3ce612bd^`, and at this record's own base `80c912244`; measured at
   each → 0, exit 1): A1's leg was not in the census at all when this record was written. A bare
   "pre-diff" would be false here for the record's own reason: `#10982` landed after BOTH of those
   bases, so its registration is already inside the CURRENT tree — only the commit name is a stable
   operand, never "today's tree minus a diff".
   The per-file split is stated once, in the §5 note below; the TOTAL is stated in both places, and
   the pin binds both to the same DERIVED value rather than to two typed numbers.
4. **The rollup query does not exist.** `git grep -in 'rollup' -- '*.py' '*.md'` over the tree is
   **self-matching** — this record names the word — and beyond that returns only unrelated uses:
   extractor `_rollup_llm`/`_rollup_recovery` stage counters, a CI per-name rollup discussion, the
   pinned bundle's own probe strings, and several plan docs (the set grows; this record types no
   total, because a count nothing re-derives is this record's recurring defect — the §5 reproduction
   block carries the commands, and item 3 above is what that costs when a number hangs on the wrong
   operand). There is **no** query of the graph shape "which work items serve this goal,
   and what evidence supports them", no ground truth, and no scoring — which is the half `#10984` says
   `#11001` does **not** own and is "the half most likely to be dropped".

**Exact remaining gap for `#10984`** (states its own scope, writes no producer):
(a) invoke the `#11213` planner at a real call site, or declare that `#11083`/`#11237` own the invocation
and re-scope to measurement-only — ⚠️ **not `#10999`**, which this record named before that issue closed:
`#10999` is **CLOSED** (2026-11-06T22:40:34Z, `closedByPullRequestsReferences` empty — the issue field, not
`closingIssuesReferences`, which is a PR field and cannot be read on an issue) and its remainder, the
Object→Object **write** path, is **`#11237`** (OPEN). Naming a closed issue as an owner is the same
orphaning this section warns about for the `v4-mechanisms.yml` row;
(b) add the declared-edge leg to the census so `0 → N` is observable at
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

Against the registered bar (`PRE-REGISTRATION.md` §3.1, the E1/E1b rows; `after_share <= 0.50 × before_share`,
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
  stripped_references=9 objects=16 the_prefix=6 share=0.375000 real_facts=90 removed=9`; the probe's
  own note: *"the 0.0 pp is a corpus property (every Object wired to its statement), not a property of
  S2.2"*). So nothing
  is removed here because on this corpus every Object is defended by a reference — which is the same fact
  that makes E1b's `90 of 90` hold **vacuously**.
- And the mechanism that *would* move it is foreclosed: the **design decision recorded for `#5333`,**
  in `#10069` (merged) and in `EXTRACTOR-V4-ARCHITECTURE.md` §*G5's destructive half*, says a pass may
  not remove a committed row — and **no shipped** non-destructive mechanism moves E1's share. The one
  hypothetical arm that does (`counterfactual-mint-skip`, `0.600000 → 0.375000` on the 16-Object
  stripped corpus — the arm §2's gap (c) below names) is a mechanism that does not exist **and would
  still miss the registered `0.300000` bound if it did** — so "nothing non-destructive moves it" is
  true of the tree, false of the arithmetic. ⚠️ `#5333` is still **OPEN** because that is the *design*
  answer only: the **authorisation** (its task 1, *whether a relocation pass may ever RUN*) remains the
  owner's, and nothing here assumes it.

⛔ **The blocker `#10985` names by id has LANDED — and it did not move the number.** Its body: *"⛔ The id
is `#10997` (S3 — an unresolved identity is HELD, never minted)"*. `#10997` is **CLOSED
2026-11-06T21:33:55Z**; `tortoise/identity_hold.py` is **PRESENT** on `origin/main`; the share above was
re-measured on a tree containing it (`tests/test_10997_identity_hold.py`, 33 tests, green — and the
four-suite run below is 254).

⛔ **But the HOLD that landed is not the gate A2 needs, and an earlier revision of this record read it as
if it were.** The HOLD has exactly **one** kind (`identity_hold.py:125` `HOLD_ENDPOINT =
"operator_endpoint"`, and `KINDS = (HOLD_ENDPOINT,)`) and exactly **one** call site —
(`extractor_v2.py:9617` at `80c912244`; **`:10172` at `d69faaf38`**, the sole `_hold_endpoint(...)` call —
inside `def _mint_endpoint`, `:10052` at `d69faaf38` / `:9497` at `80c912244`, while `_hold_endpoint`'s OWN
def is `:3104` at `d69faaf38`; `git grep -n '_hold_endpoint(' -- tortoise/extractor_v2.py` prints that
def and the one call), which fabricates a `statement` **Point** from an operator ref. A2's population is
the **Object** mint: every emitted entity is appended unfiltered at the
`payload_entities.append({` site (`extractor_v2.py:8880` at `80c912244`; **`:9433` at `d69faaf38`**)
(`payload_entities.append({"name": name, "kind": kind, …})`) and minted at `sdk.py:18206-18230`. Nothing
filters it, and the tree's own registry says so in `#10997`'s own words —
`config/v4-mechanisms.yml`'s `failure_policy` row is still **`state: not-implemented`**, its note reads
*"the gate covers the MINTED candidate only"*, and it still carries the pin built to red the day a create
gate lands (`tests/test_5095_s3_identity_policy.py::TestTheRemainingAbsence::test_an_unresolved_lookup_still_mints`).
So `#10985`'s own premise — *"the **MINT** half does not — 'nothing suppresses a create'"* — is **still
true for Objects**, and the 0.0 pp above is explained by the arm that *did* run (Rule 2 re-KEEPs every
referenced entity), not by a HOLD the probe's arms never reach. Its other premise blocker, `#10996`, is
CLOSED, and S2.2's
mechanical half now defaults ON — the composition is the `vet_enabled = (vet_arbiter is not None or
_vet_enabled() or _value_gate_enabled())` assignment (`extractor_v2.py:11731-11732` at `80c912244`;
**`:12302-12303` at `d69faaf38`**), and the default-ON itself lives at `tortoise/value_gate.py:81`
(`is_truthy(os.environ.get(ENV_FLAG, "1"))`).
`extractor_v2.py:1185` is the **`TORTOISE_VET` toggle** on this tree
(`return is_truthy(os.environ.get("TORTOISE_VET")) or _s2_seam_armed()`, `def _vet_enabled` at
`:1177`), still OFF by default — cite it for that and nothing else. ⚠️ It was `:1183` only on the
2026-11-09→11-13 commits — through `576524d0c0`, its last mainline carrier, and `#12348`'s
`42409813a7` is the commit that moved it to `:1185` — and a blank line on every sha this paragraph
names below — which is the
trap this very sentence is about: an unbased toggle number is safe at one head and wrong at the next. ⚠️ **A line cite is pinned to a base, and cites drift SITE BY SITE.** Three points, each
named by the sha it is true at, so every delta has a reproducible operand: `:11351-11352` / `:1153` at
**`aa414b4d5`** (the `#11211` landing — ⚠️ the immediate parent of the next point, `80c912244^`, holds
`:11358`, i.e. **+373**, so the delta depends on which pre-merge base you pick) → `:11731-11732` / `:1154`
at **`80c912244`** (**+380** / **+1**) → `:12302-12303` / `:1154` at **`d69faaf38`** (**+571** / **+0**).
So a bare "re-anchor on merge" is not
one arithmetic, and an arithmetic that is right for one base is already wrong for the next: **cite the
symbol and let the line number be a convenience printed beside it** (`git grep -n 'vet_enabled = '
-- tortoise/extractor_v2.py`, `git grep -n 'payload_entities.append' -- tortoise/extractor_v2.py` —
the path matters: unscoped, both patterns also match THIS record and its quoted lines, which is the
count-of-mentions trap §0 documents). Every number this paragraph pins to one of §5's four shas is
re-derived at that sha by the
`for s in aa414b4d5 8e49ae909 80c912244 d69faaf38` sweep in §5 — a live grep cannot do it (scoped,
`git grep -n 'vet_enabled = ' -- tortoise/extractor_v2.py` prints
one line, and its number is a convenience printed BESIDE the symbol rather than a
`HEAD`-relative claim: `:12929` at `a1dff7cec8`, `:12779` at `62248db88` — so the sweep
covers **none** of those two — the numbers it re-derives are pinned to the SHAS, never to a
`HEAD`-relative delta, which is the same rot in a new coat), which is the whole point. That is the class of defect §0 exists to catch: a number true at
one base and false at the base the record claims. Which is why the 0.0 pp above is the **armed** result,
not an unarmed one.

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
the write seam routes by kind (`tortoise/sdk.py:18206-18230`): `_ekind = _payload_entity_kind(e)` →
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
  (§1(c)) → re-run (d). ⛔ **And the write path it must consume is `#11237`'s, not one it may build.**
  `#10999` — the owner candidate §1(a) named — is **CLOSED**, and its remainder (the Object→Object write
  path) is **`#11237`** (OPEN). A `#10984` lane consumes that transport; writing a second `create_edge`
  producer is the "second producer" the epic forbids. ⛔ **Its `config/v4-mechanisms.yml` row does not exist today** — the file carries
  **13** mechanism ids (`vet`, `source_dedup`, `exact_dedup`, `near_dup_detect`, `near_dup_merge`,
  `against_priors_consolidation`, `span_link`, `embed_gate`, `sufficiency_router`, `fanout_cap`,
  `state_value_carve_out`, `atomic_point_write`, `failure_policy`) and **none for the declared-hierarchy
  mechanism**, despite `#10984`'s body asserting that `#11001` ships the `implemented` row. `#11001` is
  CLOSED and shipped no such row, so A1 is a mechanism that is now **implemented but undeclared** — the
  inverse of the `#5064` failure (*"a declared mechanism reads exactly like a shipped one"*). ⛔ **`#10984`
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
gh pr view 11205 --json state,mergeCommit,mergedAt,closingIssuesReferences   # MERGED 22:40:32Z, 316a572ce2
gh issue view 11237 --json state,closedAt                # the Object→Object WRITE path — this read
                                                          # prints its state; do not quote the state
# The arms' own evidence — the two PRs whose merge sha arm 1 can inherit, and arm 2's commit message.
# Without these three lines the loop prints a sha but nothing says WHAT it must equal, so a reader
# cannot tell arm 1 (a NON-EMPTY closedByPullRequestsReferences, whose commit_id may be NULL or that
# PR's own merge sha) from arm 2 (empty refs + the keyword in the COMMIT message):
gh pr view 11105 --json state,mergeCommit,mergedAt        # MERGED 11:18:49Z, mergeCommit 24d2dbcb
gh pr view 10738 --json state,mergeCommit,mergedAt        # MERGED 16:14:21Z, mergeCommit fd6558c5
git log -1 --format=%B 316a572ce2 | grep '^Closes #10999' # arm 2's keyword, in the COMMIT message
# arm 1's OWN discriminator — an auto-detected keyword vs a PR linked BY HAND. `closedByPullRequestsReferences`
# carries MANUALLY-linked closing references too: `userLinkedOnly` and its inverse `excludeUserLinked` both
# default to `false`, and `addCloseIssueReferences` is the mutation that creates the manual kind. So a
# NON-EMPTY field on its own does NOT prove a body/title keyword — a hand close carrying a linked PR reads
# the same. `gh issue view` has no flag for that filter, so this is the GraphQL read that separates them.
# Those names are read from the SCHEMA here, not remembered: the defaults are what let a `[]` mean
# "auto-detected", and the mutation pair is what creates and removes the other kind. No count is quoted.
# ⛔ The filter is NOT just the "Linked" pair — `includeClosedPrs` (default `false`) is the third switch,
# and it is the one that decides whether a PR CLOSED WITHOUT MERGING still names the issue here. ⛔ `false`
# does NOT mean "merged only": OPEN references are in the set too — that follows from the three argument
# DEFAULTS read just below (all `false`), which is why they are read and not remembered — so the default
# is "every reference except a PR closed without merging" — narrower than "any PR named it", not the same
# as "only merged ones". #11337 is in the loop below, so whichever row holds the OPEN pole is
# RE-DERIVED on every run rather than named here.
# ⛔ No row's state is used as the BASIS of a classification here, on purpose: a row's state DRIFTS, and
# an earlier draft cited #10848/11392 that way — it merged and closed within the day, leaving the sentence
# re-deriving to its own opposite, beside the command that disproves it. The states quoted further down
# sit beside the command that PRINTS them, which is the opposite contract: they are that loop's own
# output, re-derived on every run, never relied on.
gh api graphql -f query='{__type(name:"Issue"){fields{name args{name defaultValue}}}}' \
  --jq '.data.__type.fields[]|select(.name=="closedByPullRequestsReferences")|.args[]|select(.name|test("Linked|ClosedPrs"))'
                                                          # all three default FALSE — the inverse Linked
                                                          # pair, and includeClosedPrs. The first two are why
                                                          # an unfiltered read and a `userLinkedOnly` read can
                                                          # differ at all (the discriminator this block rests
                                                          # on); the third is why the default read UNDER-reports
                                                          # the reference set — see arm 2 and the read below
                                                          # the loop.
gh api graphql -f query='{__schema{mutationType{fields{name}}}}' \
  --jq '.data.__schema.mutationType.fields[].name|select(test("CloseIssueReferences"))'
                                                          # addCloseIssueReferences /
                                                          # removeCloseIssueReferences — how the manual kind
                                                          # is created and removed, i.e. the arm a
                                                          # keyword read cannot separate on its own
for n in 11001 10997 10684 10698; do                    # arm 1's four rows; `[]` = auto-detected, not linked
  gh api graphql -f query="{repository(owner:\"daniel-ospina\",name:\"tortoise\"){issue(number:$n){closedByPullRequestsReferences(first:10,userLinkedOnly:true,includeClosedPrs:true){nodes{number}}}}}" \
    --jq '.data.repository.issue.closedByPullRequestsReferences.nodes'
done
                                                          # ⛔ BOTH filters, not just `userLinkedOnly`:
                                                          # a hand link to a PR closed WITHOUT merging is
                                                          # invisible to the narrow read, so the narrow one
                                                          # cannot rule out "linked" on its own. With both
                                                          # set the four rows still print `[]` — which is
                                                          # what makes the parenthetical below a measurement
                                                          # rather than a claim.
# How an issue was actually CLOSED — THREE arms, and the PRIMARY field is closedByPullRequestsReferences,
# NOT commit_id alone. Read it first, and read it WITH `state` on each node: that is the GraphQL read in
# the loop below. (`gh issue view <n> --json state,closedByPullRequestsReferences` returns the same set
# with no `state` on its nodes, so it cannot separate a MERGED reference from an OPEN one — arm 1 needs
# that separation, arms 2 and 3 do not.)
#   1. NON-EMPTY **on a MERGED node** => closed by that PR's keyword (either auto-detected from its
#      body/title, which is what the four rows below are — `userLinkedOnly,includeClosedPrs` printed `[]`
#      for all four when measured — or a MANUAL closing link, which that read rules out). ⛔ "MERGED" is
#      load-bearing here for the SAME reason as in arms 2 and 3, one layer down: the default set carries
#      OPEN references too, so a `Closes #n` on a STILL-OPEN PR reads NON-EMPTY for an issue closed by
#      something else. ⛔ STILL-OPEN, not "never merged" — a CLOSED-unmerged PR's reference does NOT
#      appear there (#10850 says `Closes #10812.` and #10812 still reads `[]`); that one is what
#      `includeClosedPrs:true` reveals. So this arm guards the OPEN pole and that read guards the CLOSED
#      one, and neither substitutes for the other. ⛔ AND THE STATE HAS TO BE PRINTED TO SEE IT — `gh issue view <n> --json
#      closedByPullRequestsReferences` returns these nodes with NO `state` key at all, so the read that
#      classifies is the GraphQL one in the loop below, not that line. ⛔ Its `commit_id` is NOT fixed: it reads
#      NULL for #11001 ([11213]) and #10997 ([11212]), and that PR's own merge sha for #10684 ([11105])
#      and #10698 ([10738]) — the same shape, both outcomes. A non-null sha here does NOT demote it to
#      arm 2, which is exactly why the field above is read FIRST and commit_id cannot classify alone.
#   2. empty + commit_id = SHA => the keyword rode the COMMIT message, and no MERGED PR named it
#      (#10999 -> 316a572ce2, whose message carries `Closes #10999`). ⛔ "MERGED" is load-bearing, not
#      decoration: the default read carries no reference from a PR closed WITHOUT merging, so #10812
#      reads `[]` + a sha (c4a369b011) here while PR **10850** — CLOSED, `mergedAt: null` — opens with
#      `Closes #10812.`. Read `includeClosedPrs:true` (the block below the loop) before calling a row
#      arm 2, or the recipe re-creates the very mistake it exists to prevent, one arm over.
#   3. empty + commit_id = null => no MERGED PR named it, and nothing in the DEFAULT set attributes the
#      close (#11021: [] and null). ⛔ "in the DEFAULT set" is exact, not hedging: #8447 reads `[]` here
#      AND is named in the tree (next sentence), so "nothing attributes this close" would be false of
#      arm 3's own row. ⛔ "MERGED" is load-bearing HERE TOO, for arm 2's reason: #8447 reads
#      `[]` + null + a human actor, yet PR **10562** — CLOSED, `mergedAt: null` — opens with
#      `Closes #8447`, so a PR DOES name that close and only the default read hides it. Read
#      `includeClosedPrs:true` here as well before writing the word "manual". ⚠️ "manual" is
#      a partial label — a bot or a `not_planned` close reads the same [] + null — so the loop below prints
#      the closed event's `actor` beside the sha: #11021's is a human (daniel-ospina), one second after
#      #11211 merged, which is what makes the word a checked fact rather than an assumption.
# ⛔ So `commit_id: null` ALONE is NOT "manual" — arm 1 reads null in two of its four examples. And an
#   empty closingIssuesReferences on the MERGED PR is not evidence either: that PR-body field stays empty
#   in arms 2 and 3 while the PR merged (#11205 -> []). ⛔ It is NOT empty on a CLOSED-UNMERGED PR —
#   #10850 reads [10812] and #10562 reads [8447] — so there it corroborates nothing either way; the field
#   that stays empty is the ISSUE-side one above, and that is the one this section reads.
# The loop BELOW re-derives every row above — arm 1's four named issues, one arm 2, one arm 3, and the
# two counter-examples that NARROW arms 2 and 3 (#10812 and #8447: each reads `[]` here, and each is
# named by a PR that never merged, which is why both tests say "MERGED" rather than "no PR"), plus
# #11337, which is in the loop to re-derive the OPEN pole — the row that shows the default set is not
# "merged only" whenever it reads OPEN. ⛔ The loop PRINTS every row's `state`; the prose must not
# quote one, because a quoted state drifts: #10848/#11392 was this row until PR 11392 merged (#10848
# closed two seconds later), and the quote then contradicted the command printed beside it.
# Everything below is read, never recalled.
# Every issue AND every PR the arms name is read here, and so are the facts that are
# neither an issue nor a PR — arm 2's commit message, and arm 1's user-linked filter with the schema
# names it rests on — so no row, and no equality a row rests on, is asserted without a command:
# ⛔ the first read is GraphQL, not `gh issue view`, precisely because it has to print `state`: the
# `gh issue view <n> --json closedByPullRequestsReferences` line returns the SAME set with no `state`
# on its nodes, so it can show a row is non-empty but never whether the referring PR merged.
for n in 11001 10997 10684 10698 10999 11021 10812 8447 10848 11337; do
  echo "#$n"
  gh api graphql -f query="{repository(owner:\"daniel-ospina\",name:\"tortoise\"){issue(number:$n){state closedByPullRequestsReferences(first:10){nodes{number state}}}}}" \
    --jq '.data.repository.issue | "issue=\(.state) refs=\([.closedByPullRequestsReferences.nodes[] | "\(.number):\(.state)"] | join(","))"'
  gh api "repos/daniel-ospina/tortoise/issues/$n/timeline?per_page=100" --paginate \
    --jq '.[] | select(.event=="closed") | {created_at, commit_id, actor: .actor.login}'
done
                                                          # The POSITION the note below rests on is a
                                                          # read too, not a claim — the SAME timeline with
                                                          # NO --paginate, reporting the closed event's INDEX
                                                          # within the first page:
gh api "repos/daniel-ospina/tortoise/issues/11021/timeline?per_page=100" \
  --jq 'to_entries|map(select(.value.event=="closed"))|.[].key'
                                                          # 27 — an index INSIDE page 1, which is why the
                                                          # paginated read above prints for this issue; push
                                                          # that same close past the page size and the
                                                          # unpaginated read prints nothing at all
                                                          # ⛔ --paginate is MANDATORY: without it gh
                                                          # returns only the FIRST 100 events, and the
                                                          # closed event is one line among many — a close
                                                          # that happened late is not on page 1, so the
                                                          # command prints NOTHING and "nothing" reads as
                                                          # "no closed event". The failure is POSITIONAL,
                                                          # not "timeline over 100 events": #11021's close
                                                          # sits inside page 1, so the command prints — push
                                                          # that same close past event 100 and it would print
                                                          # nothing. No count is quoted here on purpose: a
                                                          # count drifts, and quoting one is how this note
                                                          # got it wrong twice. A truncated check is worse
                                                          # than none.
                                                          # ⛔ the `closedByPullRequestsReferences` read
                                                          # above is the DEFAULT set: OPEN + MERGED
                                                          # references; only a PR closed WITHOUT merging is
                                                          # omitted. The same field with `includeClosedPrs:true`
                                                          # is the full set — #10997 gains 11281 (CLOSED,
                                                          # never merged) beside [11212], #10812 goes from
                                                          # `[]` to [10850] (CLOSED), and #8447 is the same
                                                          # shape one arm over: it reads `[]` + null, which
                                                          # is arm 3's test, yet 10562 (CLOSED, never merged)
                                                          # is in the set. The two PRs' first body lines are
                                                          # the keywords that read cannot see, and their
                                                          # PR-side closingIssuesReferences is the field the
                                                          # paragraph above says is NOT empty — measured in
                                                          # the same command. ⛔ The loop reads BOTH poles:
                                                          # the three rows that GAIN an entry (10997, 10812,
                                                          # 8447) and the two arm examples that must NOT
                                                          # (#10999 is arm 2, #11021 is arm 3). Both arms
                                                          # tell the reader to run this read BEFORE naming
                                                          # the arm, so the negative result for their own
                                                          # rows has to be printed here — arm 2's and arm
                                                          # 3's classifications rest on it:
for n in 10997 10812 8447 10999 11021; do
  echo "#$n — includeClosedPrs:true"
  gh api graphql -f query="{repository(owner:\"daniel-ospina\",name:\"tortoise\"){issue(number:$n){closedByPullRequestsReferences(first:10,includeClosedPrs:true){nodes{number state}}}}}" \
    --jq '.data.repository.issue.closedByPullRequestsReferences.nodes'
done
gh pr view 10850 --json state,mergedAt,closingIssuesReferences,body \
  --jq '{state, mergedAt, closing: [.closingIssuesReferences[].number], firstLine: (.body|split("\n")[0])}'   # CLOSED, null, [10812], "Closes #10812."
gh pr view 10562 --json state,mergedAt,closingIssuesReferences,body \
  --jq '{state, mergedAt, closing: [.closingIssuesReferences[].number], firstLine: (.body|split("\n")[0])}'   # CLOSED, null, [8447], "Closes #8447"
git rev-parse origin/main                                 # NOT 80c912244 any more — origin/main moves.
                                                          # The durable test is the ancestor one:
                                                          # git merge-base --is-ancestor 80c912244 origin/main → 0
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

# §0/§2 — the sha-pinned cites, each re-derived AT THE SHA IT NAMES (the one read that decides them;
# a live grep cannot: `git grep -n 'vet_enabled = ' -- tortoise/extractor_v2.py` prints :12929 at a1dff7cec8, :12779 at 62248db88, neither of which is one of these numbers)
for s in aa414b4d5 8e49ae909 80c912244 d69faaf38; do echo "== $s"; \
  git show "$s":tortoise/extractor_v2.py | grep -n 'vet_enabled = (\|os.environ.get("TORTOISE_VET")\|def _hold_endpoint\|_hold_endpoint(hold_store\|def _mint_endpoint\|payload_entities.append({'; done
# aa414b4d5 → 1153, 8784, 9401, 11351 · 8e49ae909 → 1153, 8785, 9402, 11358
# 80c912244 → 1154, 3103, 8880, 9497, 9617, 11731 · d69faaf38 → 1154, 3104, 9433, 10052, 10172, 12302
#   (no `_hold_endpoint` before 80c912244 — #11212 is what landed it)
# deltas: 11731−11351=380 · 11731−11358=373 · 12302−11731=571 · 1154−1154=0 · 1154−1153=1
# §0's four `create_operator` MENTIONS are base-dependent too — the CALL count is 0 either way:
git show 80c912244:tortoise/extractor_v2.py | grep -c 'create_operator'   # 0
git show de72cfd45:tortoise/extractor_v2.py | grep -n 'create_operator'  # :8892 :8910 :8917 :9231
# the previous base printed :8938 :8956 :8963 :9343 (`62248db88`); `91dbc9a888` (#11449) moved all four +68
git grep -n 'create_operator' -- tortoise/extractor_v2.py                 # :10360 :10378 :10385 :10765

# A1 (§1) — the declaration reads, and a production caller outside its own module now writes it
# ⛔ This header read "…and nothing production writes it" until #11548/#11237 landed
# `relation_write.py` — `plan_hierarchy_edges` IS called from production at
# `tortoise/relation_write.py:700` (`pack_hierarchy.py:459` is the same module's own internal
# call, which is why it is not a second consumer). §0 above records the change; do not argue A1
# from the retired premise (that is the "second producer the epic forbids" argument).
.venv/bin/python3 -m tortoise pack validate packs/dev      # 6 declared hierarchies, exit 0
git grep -n apply_hierarchy_edges -- tortoise/ tests/   # 12 lines on this merge: 9 in
                                                          # tests/test_pack_hierarchy_11001.py, 1 in
                                                          # tests/test_7949_capstone_measure.py, plus
                                                          # `__all__` and `def` in pack_hierarchy.py
                                                          # (§1 above counts 11 because it says "all in"
                                                          # the one test file and misses the 7949 hit.)
                                                          # ⚠️ THE SCOPE IS THE CLAIM: unscoped this
                                                          # command returns 22 across 8 files, including
                                                          # this record's own citations,
                                                          # its index row and tools/pack_relation_census.py
git grep -n plan_hierarchy_edges -- tortoise/ tests/      # the ENUMERATION is the claim, not a total:
                                                          # 9 + 2 + 3 across the other three test
                                                          # files, `:59/:78/:365/:459` in
                                                          # pack_hierarchy.py, and — the part this line
                                                          # used to omit — 4 in relation_write.py
                                                          # (`:53 :658 :700 :705`), `:700` being the CALL.
                                                          # The remaining hits are this PIN test itself,
                                                          # which quotes the call AND these very anchors,
                                                          # so its share grows every time a row is added —
                                                          # the self-match trap, one layer up.
                                                          # ⛔ NO TYPED TOTAL, on purpose: this record used
                                                          # to state one and it went stale the moment this
                                                          # record's own block rows landed (adding rows moved
                                                          # both the scoped and the unscoped figure), which
                                                          # is the lesson the pin itself records for
                                                          # `RECONCILE`'s other citation lists — a typed
                                                          # count is this bundle's recurring defect.
                                                          # ⚠️ Unscoped, the same command also matches this
                                                          # record's own citations and
                                                          # `config/v4-mechanisms.yml`.
git grep -nE '(^|[^[:alnum:]_])(create_edge|create_about_edge|create_operator)[[:space:]]*\(' \
  tortoise/extractor_v2.py                                 # no output, exit 1 — the class must NOT
                                                          # exclude `.`: a dot-EXCLUDING one exits 1 on
                                                          # `sdk.create_edge(payload)`, a dot-ANCHORED
                                                          # one on the bare call (both measured)
                                                          # ⛔ NOT the bare token count: that is 4 on
                                                          # origin/main since #11205 (four COMMENTS naming
                                                          # sdk.create_operator) and 0 at this base — a
                                                          # count is only as good as the base it was taken at
# ⛔ THREE hits in this record's merged tree since #11548 landed the consumer (#11237):
# extractor_v2.py:12397 (the comment that WAS stale, corrected in place by #11001's lane),
# relation_write.py:7, sdk.py:18999.
# ⛔ AND THE CONCLUSION THIS ROW WAS WRITTEN WITH IS NOW FALSE — `relation_write.py` IS that
# consumer. Its own docstring says so ("#10999 built the `relations` channel as transport only …
# and nothing consumed it. … This module is that consumer"), and `_extract_session_v2` calls it at
# sdk.py:19012 (`_capture_session` reaches it at `sdk.py:16914`). Of the three hits only ONE was still prose-about-absence (the stale comment at
# `extractor_v2.py:12397`); the other two are the consumer announcing itself. Read §0's amendment
# above, never "nothing consumes it".
# ⛔ AND AS OF `#11001`'s LANE NONE OF THE THREE IS: the comment at `extractor_v2.py:12397` was
# corrected IN PLACE — same line, and its block's line count was held at 13 so every cite in this
# record keeps its number — to state the consumer (`apply_relation_edges`, called from
# `_extract_session_v2`) and to leave only the EMISSION leg unmeasured. Its number then was
# `:11524`; this tree carries the same comment at `extractor_v2.py:12397`. So the A1 paragraph's quote
# is now history only, and the ONE prose-about-absence hit this row counted is gone.
# ⛔ Both sides of the #11534 × #8290 forward-merge printed a different number, and neither matched
# the other: the branch printed 10906 / :7 / 17181 at its own tip, main printed 11119 / :7 / 17188
# at its own. The MERGED tree's three are exactly main's three — which is precisely why this was
# re-measured rather than picked: the branch's two numbers no longer describe this tree, and the
# merged ones coincide with main's instead of being derived from them. (The pre-merge text's "at
# this record's base: ONE hit" was itself stale — that base predates #11205, whose comment is the
# hit.) Re-measured at the merge.
git grep -n 'result\["relations"\]' -- tortoise/        # at this record's merged tree: THREE hits —
                                                          # :12397 (was the stale "nothing
                                                          # consumes it" comment; CORRECTED in
                                                          # place — see the note above),
                                                          # relation_write.py:7 (the
                                                          # CONSUMER's docstring) and sdk.py:18999 (the
                                                          # #10999 transport-only note). ⛔ NOT "no
                                                          # consumer": the second hit IS the consumer
                                                          # (§0 above). (⚠️ the `-- tortoise/` scope is
                                                          # the claim: unscoped it also matches the
                                                          # reader below)
git grep -c 'result\["relations"\]' -- tests/          # tree-wide MATCHES — THREE files on this merge,
                                                          # 62 LINES (`-c` counts lines per file; re-measured
                                                          # at `ba7f759`, where the two finder lines below
                                                          # add their own matches):
                                                          # tests/test_10999_batch_story_relations.py —
                                                          # 56 — assertions on the channel; tests/eval/
                                                          # write_path/test_write_path_relation_lane.py
                                                          # — 3 — and tests/test_7949_capstone_measure.py
                                                          # — 3 — SOURCE-TEXT scans, not consumers: one is
                                                          # the `extractor_v2.py` scan (`:14356`) that derives
                                                          # the doc's cite, and the two the finder itself needs
                                                          # to re-derive that number (#12569)
# ⛔⛔ THIS GREP COUNTS LINES, NOT READERS — do not read the per-file numbers as "who consumes the
# channel". Two of the three files are NOT what their matched lines look like:
#   • `test_write_path_relation_lane.py`: its 3 matched lines are all DOCSTRING prose (`:6`, `:22`,
#     `:424`), but the FILE is the strongest reader in the tree — it rebinds the result
#     (`folded = ev2.execute_embed(...)`, `:439`) and asserts on `folded["relations"]` at `:441`,
#     then drives it through the REAL commit leg (`_capture`, `:443`) and asserts
#     `cap["relation_write"]["written"] == 2` plus both edges present in the graph (`:444-448`,
#     the file's last line).
#     A literal `result\["relations"\]` grep is blind to a REBIND — that is this block's own
#     lesson (see the `-- tortoise/` scope note above), applied to itself.
#   • `test_7949_capstone_measure.py`: the hit is a scan of `extractor_v2.py`'s source lines used to
#     DERIVE the cite — a citation pin, not a consumer.
# So: 1 file reads the channel through the literal name (`test_10999`), 1 reads it through a rebind
# (`write_path_relation_lane`), and 1 does not read it at all (the pin). `"relations"` is a
# RESULT-level key (`extractor_v2.py:12649`, `"relations": payload_relations`), which is why both
# readers can see it at all.
# ⚠️ The third file was added after this record was written, so the earlier "TWO files … 59"
# was true of the head this block was first measured on (`e49788706a`, which reads 2 files /
# 59 lines / 62 occurrences) and is re-measured here rather than left stale.
git grep -o 'result\["relations"\]' -- tests/ | wc -l    # 65 OCCURRENCES on this merge (62 lines + the 3
                                                          # doubly-named ones below). ⛔ A count is only
                                                          # as good as the BASE it was taken at, and the
                                                          # earlier text's pair belonged to a different
                                                          # base than the sentence it sat on claimed: its
                                                          # "59" was a LINE count and its occurrence count
                                                          # was "62", BOTH true at `e49788706a`. At
                                                          # `62248db88` — the base §0 names — `-c` reads
                                                          # ONE file / 56 lines and `-o | wc -l` reads 59
                                                          # OCCURRENCES, a different quantity from 62
git grep -n -o 'result\["relations"\]' -- tests/ \
  | cut -d: -f2 | sort | uniq -d                          # the 3 extra: the doubly-named :1551, :1579, :161
grep -c '^  - id:' config/v4-mechanisms.yml                 # 15 — and ONE for the hierarchy mechanism
                                                          # (DM5, `:1695`, landed by `#11001` AFTER this
                                                          # record was written; `13`/"none" was true of
                                                          # the head it measured). Read it before adding
                                                          # a second row: `declared_hierarchy` is already
                                                          # registered there
tools/who-is-on.sh 10984 --repo .                           # exit 0 = complete answer; exit 2 = a
                                                          # surface could not be queried — never read
                                                          # either as "nobody"
uv run python tools/collision_preflight.py 10984 --repo .    # the dispatch-time gate (exit 0 = clear)
grep -n -i -E 'hierarch|hasPart|part-whole|11001|10984' config/v4-mechanisms.yml      # 13 lines on this
                                                          # merge — DM5 at `:1695` names
                                                          # `pack_hierarchy.py`, `relation_write.py`,
                                                          # `declared_hierarchy`, `apply_relation_edges`
                                                          # and `tests/test_pack_hierarchy_11001.py`; it
                                                          # was empty when this record was written
# ⛔ So the registry DOES declare the hierarchies surface **and** registers its producer: DM5's
# `code:` is `tortoise/pack_hierarchy.py` + `tortoise/relation_write.py`, its `symbols:` include
# `apply_relation_edges`, and its note says that symbol is "the PRODUCTION consumer, called from
# `sdk.py`'s capture commit path on `out["relations"]`" (`config/v4-mechanisms.yml:1700-1701,`
# `:1709, :1722-1724`). ⛔ Do NOT re-argue A1 from "nothing production calls the planner" — §0's
# fifth-landing amendment already retired that basis. A1's remaining basis is the one §0 states:
# *the writer landed, the corpus was not re-measured* (and no rollup query exists, §1).
# …and the structural edges that DO exist on the capture path, which is why the claim above is scoped:
sed -n '275,283p' docs/evidence/7949-capstone/probes/capstone_measure.py   # `references`, via link_source_to_event
sed -n '19452p' tortoise/sdk.py                             # the call (`references`'s writer)
git grep -in 'rollup' -- '*.py' '*.md'                      # no graph rollup query anywhere
# ⚠️ self-matching, and now no longer empty: exclude this record, and the hits are the
# BUNDLE's OWN registration of the A1 row (#10982 landed it AFTER this record was written),
# not a producer — the bundle CITES the predicate, it does not produce it. The producer
# claim is the call-shaped grep above, which is still empty:
git grep -rn -E 'hasPart|hierarchies|decomposesInto' -- 'docs/evidence/7949-capstone/' \
    ':!docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md'    # 42 lines, exit 0 — all in the bundle
                                                            # (PRE-REGISTRATION.md 23,
                                                            # preregistration.json 9,
                                                            # probes/capstone_measure.py 10)

# A2 (§2) — the shipped arm moves the share by exactly zero, on a tree that HAS #10997
.venv/bin/python3 docs/evidence/7949-capstone/probes/e1_e2_probe.py --json-out /tmp/e1e2.json \
    > /tmp/e1e2.log 2>&1                                   # exits 3 = UNPREREGISTERED, by design
grep -E '^e1 |metric=objects|vet-downgrades|metric=real_facts' /tmp/e1e2.log
grep -n -E 'after_share <= 0.50|>= 90 of 90' docs/evidence/7949-capstone/PRE-REGISTRATION.md
git grep -n 'vet_enabled = ' -- tortoise/extractor_v2.py   # the default-ON composition (symbol, not a line)
git grep -n 'ENV_FLAG, "1"' -- tortoise/value_gate.py      # the default-ON itself (value_gate.py:81)
git grep -n 'os.environ.get("TORTOISE_VET")' -- tortoise/extractor_v2.py   # the toggle (OFF by default)
# the 62.3 % share is QUOTED from a research record, re-read (not re-derived):
sed -n '192p;314p' docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md
sed -n '45p' docs/epics/2026-11-05-5087-extractor-v4/02-research-brief.md   # "#4894 is the mechanism audit"

# A5 (§3) — Subject 19, three of four families at zero
grep -n -A 12 '"label_counts"' docs/evidence/7949-capstone/capstone-measured-9152448d.log
grep -n -A 12 '"relationship_counts"' docs/evidence/7949-capstone/capstone-measured-9152448d.log
git grep -n -A 24 '_ekind = _payload_entity_kind(e)' -- tortoise/sdk.py   # the kind→label routing at the
                                                          # write seam (#5230/#1370's fix); symbol, not a
                                                          # line — the -A 24 reproduces sdk.py:18206-18230
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
- **It does not re-open `#5333`.** The **design** decision is recorded in `#10069` and in
  `EXTRACTOR-V4-ARCHITECTURE.md` §*G5's destructive half*, and this record does not challenge it: no
  pass may remove, archive or withdraw a committed row except through the one journaled lifecycle fold,
  or through the D7 `ObjectSuperseded` arm where a visible successor exists. `#5333` is **OPEN** because that records the design half
  only — the **authorisation** (task 1) is still the owner's, so no reading here treats it as given.
  That is why A2's remaining path must be non-destructive.

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
