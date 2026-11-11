---
title: "The memory-substrate findings ledger — re-cut: the corrections log and what is left to lose"
type: engineering
domain: platform
doc_status: live
created: 2026-11-12
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, memory-substrate, issue 2852, issue 2820, issue 4917
measured_at_commit: f70d884b7
measured_at: 2026-11-12
---

# The memory-substrate findings ledger — re-cut

> **What this is.** The durable half of issue **#2852**, the findings ledger of the
> 2026-09-09/10 memory-substrate review. Its sibling **#2820** already has an
> in-repo re-cut (`MEMORY-SUBSTRATE-PROBLEM-MAP.md`), and that page is the
> **status half** — *is defect N still broken?* This page is the other half, and it
> answers a different question: **which of the review's findings were falsified, and
> where does its evidence live now?**
>
> **Why it needed a re-cut, in one measurement.** The ledger's own §7 exists so
> nobody re-learns a falsified claim the hard way. Re-measured at `f70d884b7`,
> **seven of its twelve rows no longer read as written** — four of them overtaken by
> the very fixes they motivated, three by a number or a tracker state that moved —
> so a lane that trusted §7 today would believe the Point fold still
> drops props (#2795 closed that), and would go looking for a defect that is gone.
> Meanwhile **three of the seven research briefs §3 indexes have no in-repo mirror at
> all**, and the machine they lived on no longer carries them (§5).
>
> **What this page does NOT do.** It does not re-open a defect, propose a fix, or
> schedule anything. It does not restate the 26-entry status table — read the
> problem map for that. It records what the review concluded, what has since been
> disproved, and what of its evidence is still reachable.

**Read §3 first if you are about to fix something.** It is the part that changes a
lane's behaviour. Read §5 if you are about to cite the 2026-09-09 research.

---

## 1. What a row means, and how to check one

Every row in this page carries a verdict from a **closed vocabulary**, because the
failure this page exists to prevent is a reader treating a *dated finding* as a
*current fact*:

| Verdict | Means | What a lane should do |
|---|---|---|
| **STILL-TRUE** | Re-measured at `f70d884b7` and the claim holds as written | Rely on it; the evidence line is current |
| **SUPERSEDED-BY-FIX** | The defect the corrections log described no longer exists | Do **not** re-derive it; the row names the fix and its pin |
| **ADOPTED** | The correction was a recommendation, and the recommendation has landed | Treat the new behaviour as the norm, not as a proposal |
| **DRIFTED** | The claim's *shape* survives but a number, path or state in it moved | Use the re-measured value on the page, never the 2026-09-10 one |
| **MOOT-BY-LOSS** | The claim cannot be re-measured because its subject no longer exists | Do not assert it either way |

Every evidence cell names a `path:line`, an issue, a command, or an in-page section.
A row whose evidence cannot be opened is not evidence; it is a memory. `tools/findings_ledger_check.py`
refuses the page if an evidence cell names none of those, or if a code citation stops resolving (§8).

---

## 2. The four defect classes — unchanged, and why they survived

The review's organizing insight was that ~20 seemingly unrelated defects collapse
into four patterns. Nothing in the four months since has put a fifth pattern on the
page, and the problem map still sorts its 26 entries by exactly these four — so
they are carried **unchanged** here, as the shared vocabulary rather than as a
finding:

| Pattern | What it means | Where it showed up |
|---|---|---|
| **1. Declared ≠ stored** | The system reports something as stored that never reaches the graph, or is lost later. No error is raised. | #2813 (capture drops fields), #2795 (rebuild drops props), #2814 (rebuild wipes config), #2788, #2742 |
| **2. Canonical ≠ consistent** | The same vocabulary is defined in several places and the definitions disagree, so a capability is documented but unavailable. | #2786, #2783, #2767, #2727 |
| **3. Promised ≠ expressible** | Packs/ontology advertise a capability with no mechanism behind it. | #2766, #2787, #2818, #2781 |
| **4. Fixed vs flexible, unresolved** | Not decided which semantics are canonical and which are tenant-configurable, so both are half-built. | #2792, #2729, #2782/#2817, #2730, #2684 |

Pattern 4 is the one the review got **most wrong when it was written**, and the
correction is worth keeping visible: the review's initial instinct was to treat
"fixed vs flexible" as an unresolved *argument*. It was instead an unresolved
*design*, and the two-tier answer (canonical meanings in core, tenant-owned
labels/order in the graph) is what shipped — `tortoise/pipeline.py:16` is that
decision in code. See correction **(e)**.

---

## 3. The corrections log, re-measured at `f70d884b7`

**This is the load-bearing section.** Each row is a claim the review made and then
falsified or narrowed. The review kept them so nobody re-learns them the hard way;
this re-cut keeps them *and* records which ones the tree has since overtaken.

| # | The retracted claim | The correction (2026-09-10) | Verdict at `f70d884b7` | Evidence now |
|---|---|---|---|---|
| **a** | The commit `extra="forbid"` gate is the binding gate for extraction | **False.** Extraction writes through the SDK doors (`_extract_session_v2`), not the Layer-1 commit contract. The gate binds *external* callers only. | **STILL-TRUE** | `tortoise/sdk.py:16962` — its own docstring says the payload is written "entities via create_entity, points via create_point … events via create_event"; the call is `tortoise/sdk.py:17361` |
| **b** | `CommitEvent` has 8 fields; `Point` has 15; `Entity` has 3 | Counts corrected to 10 / 15 / 3 — the prep #1418/#1533 additions were missed | **DRIFTED** — `Entity` 3 ✓, `CommitEvent` 10 ✓, **`Point` is now 18** (+`id`, `mentions`, `span_start`/`span_end` moved on) | `.venv/bin/python -c "from tortoise.commit_schema import Entity, Point, CommitEvent; print([len(c.model_fields) for c in (Entity, Point, CommitEvent)])"` → `[3, 18, 10]`; classes at `tortoise/commit_schema.py:395`, `:792`, `:1002` (the old `:429-438` cite now lands in `ParticipantSlots`) |
| **c** | "Props are not durable by construction" | **Too broad.** The journal carries them; only the **Point** fold dropped them. Subject/Object/Event/Document/Source extras survived rebuild. | **SUPERSEDED-BY-FIX** — the Point fold now persists and restores extras, so *no* label drops them | `tortoise/projection/entities.py:2632` (`_upsert_point_props`) calls `_persist_extra_props` at `:3115` and returns `persisted_extra_keys`; #2795 CLOSED |
| **d** | Route all doors through `sdk.ingest` and the loss class cannot recur | **`sdk.ingest` itself lost Point extras on replay** — the canonical bundle contract inherited #2795 | **SUPERSEDED-BY-FIX** — replay restores Point extras; `content_hash` is now recomputed from replayed content, so a rebuild is self-healing | `tortoise/sdk.py:36042-36046` states the rebuild case is "NOT … any more — `projection/entities.py::_upsert_point_props` (#2795 D2) now RECOMPUTES `content_hash`"; pin `tests/test_ingest_rebuild_durability.py` |
| **e** | The terminal outcome set `{won, lost, withdrawn, completed}` is fine | **Narrowed** — split the **outcome axis** from the **completion axis** (#2826 C9/C14) | **ADOPTED** — both axes are shipped properties, and the code names the separation | `tortoise/pipeline.py:16` carries the props (`pipelineStageId` / `pipelineSlot` / `pipelineOutcome`), and `:27` is where the separation #2826's C14 recommends is stated; `:66` holds both axes in `STAGE_PROPS`; #2792 CLOSED COMPLETED 2026-10-15 |
| **f** | `tortoise/semantic_extractor.py` exists | **False** — verified absent | **STILL-TRUE** | `ls tortoise/semantic_extractor.py` → *No such file or directory* at `f70d884b7` |
| **g** | #2795's prop loss is a **journal** gap | **False.** The journal carries the full snapshot; the loss was the **replay fold** (`_upsert_point_props` never called `_persist_extra_props`). Fix = one code path. | **SUPERSEDED-BY-FIX** — that one code path was added | same evidence as **(c)**: `tortoise/projection/entities.py:3115` |
| **h** | The write-path benchmark exercises the **eval** lane, so it cannot see this class | **False.** It runs the **product** lane (`sdk.capture_session` → `_extract_session_v2`); it was blind because its graded vocabulary never asserted those props exist | **STILL-TRUE** (citation relocated — the 2026-09-10 `runner.py` line citation no longer resolves at this head) | `tests/eval/write_path/test_write_path_relation_lane.py:9` ("**capture commit** (`sdk.capture_session` → `_extract_session_v2`)") |
| **i** | The pack-fit brief forward-referenced filing M1 as `#2697` | **Wrong.** #2697 is a merged executor/envelope PR; M1 was filed as **#2714** | **STILL-TRUE**, and now spent — `#2714` CLOSED COMPLETED | `gh issue view 2714` → `CLOSED`; `gh pr view 2697` → `MERGED` "fix(executor): #1416 — robust envelope extraction" |
| **j** | The pilot corpus is "sanitized" | **Unverified / inconsistent** — it carried customer-identifying names and a work email in provenance metadata. Treat as private regardless of the label. | **STILL-TRUE as a rule; MOOT-BY-LOSS as an object** — the corpus is no longer on this machine (§5), so nothing here can be re-measured. **The rule outlives the object: never commit corpus material to this public repo.** | `ls -d ~/.swarm` → *No such file or directory* |
| **k** | #2820 estimated "~40 decisions scattered" | **Undercount.** #2826's verified count was **50 open** (17 owner-required, 33 engineering) | **DRIFTED** — the count is now a closed-book figure: #2826 was closed `NOT_PLANNED` 2026-09-17, so the register is a *snapshot*, not a live queue | `gh issue view 2826` → `CLOSED NOT_PLANNED @ 2026-09-17T20:54:28Z` |
| **l** | A list of issues the ledger recorded as OPEN | Corrected 2026-09-10: #2748, #1695, #1418, #1535, #2313, #2083 are **closed** | **DRIFTED, and much further than one step** — of the 40 issues §4 indexed, **4 remain open**: `#2696`, `#2728`, `#2684`, `#2811` | §6 below, measured per issue |

**The one thing to take away:** **seven of the twelve rows no longer read as
written** — **(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, **(e)** is
**ADOPTED**, and **(b)**, **(k)** and **(l)** are **DRIFTED**. Only **(a)**, **(f)**,
**(h)**, **(i)** and **(j)** still read as written. (Four of the seven were
*overtaken by the very fixes they motivated* — (c), (d), (e) and (g); the other
three moved for a different reason, a number or a tracker state, which is why the
count is stated both ways.) The review's central correction — *"the Point fold
drops props"* — is the exact defect #2795 D2 fixed. A lane that reads the
2026-09-10 ledger and goes hunting for it will find the fix and call it a regression.

---

## 4. The artefact index, re-measured — where each artefact actually lives

The ledger's §3 indexed 15 artefacts by *what they concluded* and *whether they were
mirrored*. That second column is the one that decayed: "MIRRORED (condensed) on an
issue" is a real durable home, but it is a **different document** from the brief, and
in several cases the *design* it argued for became an in-repo plan doc or shipped
code while the brief itself never did.

| Artefact (2026-09-09/10) | Best home reachable at `f70d884b7` | What survives where |
|---|---|---|
| `research/2026-09-09-write-path-contract.md` (464 l) | **In-repo plan doc** — `docs/plans/2026-09-10-write-contract-five-doors.md` (WS-A design v5) + condensed comment on #2820 | The **argument** survives as an in-repo design; the 33-row source table and §9/§11/§13 do not |
| `research/2026-09-09-entity-attachment.md` (258 l) | **In-repo successor** — `docs/research/2026-09-24-2730-entity-attachment/research-brief.md`, which explicitly *reconciles* the predecessor (PR #8639) | The ladder and the code facts were re-derived in-repo; the **out-of-repo predecessor's** external precedent list (X1–X10) survives only condensed on #2730 |
| `research/2026-09-09-numeric-storage-audit.md` (286 l) | **Cited by** `docs/plans/2026-09-10-ws-c-numeric-normalization.md:558-559` + condensed comment on #2782 | The per-layer storage table and the rebuild transcript are lost; the conclusions are baked into the WS-C design |
| `research/2026-09-09-typed-values-and-lifecycles.md` (298 l) | **Cited by** the same WS-C plan, `:558-559` + condensed comment on #2792 | Same shape — conclusions baked in, evidence lost |
| `research/2026-09-09-ontology-naming-review.md` (200 l) | Condensed comment on #2725 only | **No copy of its content in-repo** — its only `docs/` hit is this page's own row |
| `research/2026-09-09-ontology-packs-venture-builder.md` (47 l) | Condensed in #2696 §5 (issue body, not a comment) | **No copy of its content in-repo** — its only `docs/` hit is this page's own row |
| `research/2026-09-09-pack-fit-selection.md` (33 l) | Condensed in #2714 body | **No copy of its content in-repo** — its only `docs/` hit is this page's own row (and #2714 is now CLOSED — its body is the only copy) |
| `design/2026-09-09-lifecycle-pipelines-design.md` (556 l) | **Shipped as code** — `tortoise/pipeline.py` (#2792) + near-full comment on #2792 | The design is a *product*, not a document: the behaviour is pinned by tests, the rationale only by the issue comment |
| `design/2026-09-10-decision-briefs-workstream-C.md` (167 l) | Near-full comment on #2826 | #2826 is CLOSED `NOT_PLANNED` — still readable, no longer a queue |
| `design/2026-09-10-a1-a2-comment.md` (103 l) | Near-full comment on #2826 (A1/A2 briefs) | Same |
| `design/2026-09-10-a1-a2-decision-briefs.md` (71 l) | **Gone** — never posted, superseded by the A1 comment by design | Nothing; flagged local-only when written |
| `<corpus>/2026-09-09-ontology-evidence.md` (431 l) | **MIRRORED (condensed)** — comment on #2725 | The corpus-grounding dossier: all 8 meeting docs read and grepped against the proposed 10-kind pack, verdict **8/10 kinds STRONG**, `milestone` WEAK, several lifecycle states design extrapolations. This is the **only** corpus artefact with a surviving mirror — the payload it was derived from does not survive (§6.3) |
| `<corpus>/README.md` + 8 `.docx` + 8 `plaintext/*.txt` (~976 KB) | **Gone from this machine** — and **must never** enter this public repo (#2852 §6.3) | Nothing reachable; originals were also in `~/Downloads`, unverified |
| `~/.swarm/issue-triage-2026-09-09.md` (~5 KB) | **Gone** — local-only by design | Nothing; its outcome (33 closures + a 10-item shortlist) is in the tracker, the reasoning is not |

**The pattern worth naming:** every artefact whose conclusion *changed the code* survived
in a better form than a document — a plan doc, a shipped module, a test. Every artefact
that was *only analysis* either survives as a condensed issue comment or does not survive.
That is a stronger durability answer than the 2026-09-10 ledger could give, and it is the
argument for recording a finding as a change or as an issue comment rather than as a brief.

---

## 5. The at-risk inventory, resolved: it is already lost

The 2026-09-10 ledger named §6 its most important section — "the part that is actually
lose-able" — because all of `~/.swarm` (1.3 MB, **not a git repository**, no backup, no
remote) sat on one machine, and GitHub held only condensed versions.

**Re-measured 2026-11-12 on this host:**

```console
$ ls -d ~/.swarm
ls: cannot access '/home/cmux/.swarm': No such file or directory
$ ls -d /home/*/.swarm
ls: cannot access '/home/*/.swarm': No such file or directory
```

The directory is **gone**. The ~2,000 lines of research that existed only there are not
recoverable from this box, and nothing in this repository ever carried them. This is the
ledger's own predicted failure, realised — and it is the reason the *condensing ratio*
mattered: the 5–10× detail that only the briefs held is the 5–10× that no longer exists.

Three consequences, and they are the actionable part of this page:

1. **A citation into `~/.swarm` is now a broken citation.** If you find one in-repo, it is
   not "at risk" — it is dangling. `docs/plans/2026-09-10-ws-c-numeric-normalization.md:558-559`
   names two `~/.swarm/research/…` files that cannot be opened. Treat the WS-C design's
   *conclusions* as the record and its *source briefs* as lost; do not claim to have read them.
2. **Do not attempt to re-mirror what is gone.** The ledger's §6.4 proposed three candidate
   durable homes and committed nothing, deliberately: *"Do not commit anything without the
   owner's choice."* That choice is now partly moot for the briefs and entirely live for the
   corpus — the corpus is customer material and is barred from this repo by §6.3 regardless
   of who decides.
3. **The lesson is the process one, not the content one.** A brief that changes nothing is
   not durable, and a brief that changes something does not need to be. Record the finding
   where the work is gated (§4 above shows this already happened by accident, and better).

---

## 6. The issue index, re-measured: 4 of 40 open

The ledger indexed **40 issues and 2 PRs** across §4.1/§4.2, with states read on 2026-09-10.
Re-read per issue on 2026-11-12 — 42 rows, and they reconcile:

| State (`stateReason`) | Count | Which |
|---|---|---|
| **OPEN** | **4** | `#2696` (an epic, not a defect) · `#2728` (per-graph pack selection) · `#2684` (value-span slice) · `#2811` (answer normalizer) |
| CLOSED COMPLETED | 32 | incl. `#2714`, `#2725`, `#2726`, `#2727`, `#2729`, `#2730`, `#2766`, `#2767`, `#2781`, `#2782`, `#2783`, `#2786`, `#2788`, `#2792`, `#2795`, `#2813`, `#2814`, `#2817`, `#2818`, `#2749`, `#2734`, `#2742`, `#2083`, `#2105`, `#1695`, `#1370`, `#1418`, `#1535`, `#2313`, `#2296`, `#2820`, `#2790` |
| CLOSED other | 4 | `#2748` (`NOT_PLANNED`, fixed in effect) · `#2826` (`NOT_PLANNED`) · `#1509` (`NOT_PLANNED`) · `#2787` (`NOT_PLANNED`) |
| MERGED (both were PRs) | 2 | PR `#2747` · `#2697` — both are PRs, not issues; see correction **(i)** |

**⚠️ of the four survivors, the two survivor bullets below are already re-measured elsewhere and must
not be re-derived — and one CLOSED issue must not be read as fixed:**

- **`#2728`** was re-measured **out** of the problem map's survivor set — its Settings home and
  modal merged 2026-10-18 in `08a6b0288`, with the add-list read from the on-disk pack registry so
  self-hosted parity holds by construction. The tracker still says OPEN.
- **`#2730`** is CLOSED, but the problem map deliberately **excludes** it from the fixed set: the
  research is done and the *mechanism* is unbuilt. **Issue closure is not evidence of a fix.**
- **`#2696`** is an epic with human gates, not a defect. Count it as scope, never as a bug.

**For the status question — *is defect N still broken?* — read
`docs/architecture/MEMORY-SUBSTRATE-PROBLEM-MAP.md`, not this page and not the issue bodies.**
It re-cut all 26 entries with a commit anchor plus an in-tree pin per fixed row, and it records
that its own proposed `E ──► A,B ──► C ──► D` dependency order is **spent**.

---

## 7. What this changes for a lane reading #2852 today

1. **Do not re-derive corrections (c), (d), (e), (g), (k).** They are answers, and the first four
   are answers the *code now embodies*. A "the Point fold drops props" finding is a false positive
   against the current tree.
2. **Do not trust a `~/.swarm/…` citation.** It resolves on no machine in the fleet (§5).
3. **Do not read the ledger's §4 state labels.** 36 of 40 have moved; two of the four survivors are
   already re-measured.
4. **Do carry forward the one thing that is still entirely true:** the review's §10 verification
   discipline — *runtime probes beat reading the diff*. It is why two of the loudest claims in this
   very ledger were falsified before they could be built on.

---

## 8. What is machine-checked, and what is deliberately not

**Checked** (`tools/findings_ledger_check.py`, run by the `docs` job beside its #2820/#5086/#11084
siblings, and pinned by `tests/test_findings_ledger_2852.py`):

- every **verdict** in §3 is a member of the closed vocabulary in §1 — an invented verdict is a
  claim class with no defined handling, which is how a superseded correction gets read as live;
- every **`path:line` citation** in §3 and §5 resolves in the tree, and the line number is in range —
  a citation that points at nothing is the doc's own "declared ≠ stored" defect, and it looks exactly
  like evidence. That includes the **shorthand `:LINE`** form used inside a cell — in row (b),
  `tortoise/commit_schema.py:395` is followed by `:792` and `:1002`. A shorthand resolves against the
  nearest preceding full-path citation, and one with nothing to anchor it is refused rather than
  guessed; without that rule, the load-bearing half of rows (b), (c) and (e) was unchecked while this
  section said it was checked. **No citation shape is exempt:** a backticked token that ends in a line
  number but is not a recognised `path:line` is **refused**, not skipped. Skipping it was a hole with
  three doors — a `.txt` path, an extension-less path, and a leading-dot path like
  `.github/workflows/ci.yml:892` matched nothing, so reformatting a citation was enough to defuse a
  real red;
- every **§3 evidence cell** names a `path:line`, an issue, a command or a §N (§1's own rule). Without
  this, the whole evidence column can be **emptied** while every verdict still passes — a red defused
  by deleting what it pointed at, which is the one move this page exists to make impossible;
- the **§3 population is pinned** to twelve rows, and the **§6 arithmetic** (open + closed + merged =
  the indexed population) is re-derived from the table rather than trusted — deleting a row and every
  mention of it otherwise passes every referential check. Because a pin selects rows *by shape*, an
  **unrecognised row shape is refused** on both tables: a thirteenth correction row, or a fifth §6
  state bucket, used to be silently invisible (measured — appending one left the guard CLEAN);
- the **§6 survivor sentence's two numbers** are derived as well: its survivor TOTAL must equal the
  OPEN row's own count, and its "already re-measured" count must equal the number of **bullets naming
  an OPEN issue** — the `#2730` bullet is CLOSED and is printed because *issue closure is not evidence
  of a fix*, not because it survives. §6 must also carry **exactly one** sentence matching that
  pattern: the check finds its subject by pattern, so a second one earlier in §6 takes the match and the
  guard reports OK over the sentence a lane actually reads — measured, one added decoy sentence was
  enough. Unguarded, this sentence carried a false `three` over two
  survivor bullets while §7 said `two`, and the guard reported OK over it: the page's own *declared ≠
  stored* defect, inside the correction that exists to stop it — and the reason two reviewers could
  each "prove" the other side was the stale one.

The self-test that proves those rules still fire has its own **population floor**
(`MIN_SELF_TEST_MUTATIONS`). An emptied arm set used to print `PASS (0 failing arm(s))` and exit 0 —
a gate reporting OK when it cannot run — so a shrunk population now reds instead.

Exactly one arm needs `git` **history**: the frontmatter's `measured_at_commit` must be an ancestor of
`HEAD`. The other seven read this checkout's tree and nothing else, and the tool keeps that split
(`check_document()` / `check_all()`) because the two jobs that run it differ: the REQUIRED `docs` job
checks out at `fetch-depth: 0` and runs `--check` (all eight arms), while the `test` jobs check out at
the default depth 1, cannot resolve `f70d884b7` at all, and are where this page's own test module runs.
In a depth-1 clone `--check` therefore reports **exit 2 UNVERIFIED** — never `0`, and never a verdict
against the document it could not read — and `--self-test`, which drives only the tree-only arms,
still runs.

⛔ **Do not read the skipped ancestry test as coverage.** The `docs` job runs no pytest, so a test that
skips in a depth-1 checkout is inert everywhere; the live ancestry claim is proved by that job's
`--check` (which *is* `check_all`), and the **not-an-ancestor branch** is pinned in every pytest job by
a stubbed-`git` case that needs no history. Two mechanisms, stated separately, because "the other job
covers it" was true of the tool and false of the test.

**Deliberately not checked:** the issue states in §6, the durability column in §4, and every
`~/.swarm` claim in §5. They are **dated readings of things outside this repository**, and a guard
that asserted them would either fail on someone else's tracker or, worse, pass by not looking. This
follows `SUBSTRATE-REBUILD-MAP.md`'s ruling: *the tracker half is deliberately not guarded — it is
dated and its command is recorded.* The command for §6 is `gh issue view <N> --json state`, and it
was run per issue, not sampled.

---

*Re-cut 2026-11-12 from `f70d884b7`, which was `origin/main` at the time of the cut — it is **behind
`origin/main` at this page's merge head** (`bf7401e4` brought later main in; the guard requires
*ancestry*, not equality). Cut against the original ledger posted on #2852 and re-measured claim by
claim. Every code citation on this page was opened at that commit; every issue state was read
individually. Where a 2026-09-10 claim could no longer be re-measured, it says so rather than
repeating it.*
