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
> Meanwhile **three of the seven research briefs §4 indexes have no in-repo mirror at
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
| **a** | The commit `extra="forbid"` gate is the binding gate for extraction | **False.** Extraction writes through the SDK doors (`_extract_session_v2`), not the Layer-1 commit contract. The gate binds *external* callers only. | **STILL-TRUE** | `tortoise/sdk.py:17109` — its own docstring says the payload is written "entities via create_entity, points via create_point … events via create_event"; the call is `tortoise/sdk.py:17530` |
| **b** | `CommitEvent` has 8 fields; `Point` has 15; `Entity` has 3 | Counts corrected to 10 / 15 / 3 — the prep #1418/#1533 additions were missed | **DRIFTED** — `Entity` 3 ✓, `CommitEvent` 10 ✓, **`Point` is now 18** (+`id`, `mentions`, `span_start`/`span_end` moved on) | `.venv/bin/python -c "from tortoise.commit_schema import Entity, Point, CommitEvent; print([len(c.model_fields) for c in (Entity, Point, CommitEvent)])"` → `[3, 18, 10]`; classes at `tortoise/commit_schema.py:395`, `:792`, `:1002` (the old `:429-438` cite now lands in `ParticipantSlots`) |
| **c** | "Props are not durable by construction" | **Too broad.** The journal carries them; only the **Point** fold dropped them. Subject/Object/Event/Document/Source extras survived rebuild. | **SUPERSEDED-BY-FIX** — the Point fold now persists and restores extras, so *no* label drops them | `tortoise/projection/entities.py:2679` (`_upsert_point_props`) calls `_persist_extra_props` at `:3162` and returns `persisted_extra_keys`; #2795 CLOSED |
| **d** | Route all doors through `sdk.ingest` and the loss class cannot recur | **`sdk.ingest` itself lost Point extras on replay** — the canonical bundle contract inherited #2795 | **SUPERSEDED-BY-FIX** — replay restores Point extras; `content_hash` is now recomputed from replayed content, so a rebuild is self-healing | `tortoise/sdk.py:36215-36218` states the rebuild case is "NOT … any more — `projection/entities.py::_upsert_point_props` (#2795 D2) now RECOMPUTES `content_hash`"; pin `tests/test_ingest_rebuild_durability.py` |
| **e** | The terminal outcome set `{won, lost, withdrawn, completed}` is fine | **Narrowed** — split the **outcome axis** from the **completion axis** (#2826 C9/C14) | **ADOPTED** — both axes are shipped properties, and the code names the separation | `tortoise/pipeline.py:16` carries the props (`pipelineStageId` / `pipelineSlot` / `pipelineOutcome`), and `:27` is where the separation #2826's C14 recommends is stated; `:66` holds both axes in `STAGE_PROPS`; #2792 CLOSED COMPLETED 2026-10-15 |
| **f** | `tortoise/semantic_extractor.py` exists | **False** — verified absent | **STILL-TRUE** | `ls tortoise/semantic_extractor.py` → *No such file or directory* at `f70d884b7` |
| **g** | #2795's prop loss is a **journal** gap | **False.** The journal carries the full snapshot; the loss was the **replay fold** (`_upsert_point_props` never called `_persist_extra_props`). Fix = one code path. | **SUPERSEDED-BY-FIX** — that one code path was added | same evidence as **(c)**: `tortoise/projection/entities.py:3162` |
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
| **OPEN** | **4** | `#2696` (an epic, not a defect) · `#2728` (per-graph pack selection) · `#2684` (value-span slice) · `#2811` (answer normalizer — ⚠️ it has **no row in `MEMORY-SUBSTRATE-PROBLEM-MAP.md`**, so the redirect below cannot answer for it) |
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

**⚠️ Two more CLOSED rows sit in the buckets above, and they resolved in OPPOSITE directions —
which is why tracker state answers neither question on its own.**

**`#2787`** (CLOSED `NOT_PLANNED` 2026-09-29) is still **OPEN as a defect** on the problem map: its
kind-bucket rule is unimplemented. A closed issue that is not a fixed defect.

**`#2782`** (CLOSED `COMPLETED` 2026-11-11) is **VERIFIED-FIXED** on the problem map, and this page
said the opposite until it was re-measured against the merge base. The representation landed
earlier — a flat, currency-safe money value on entities and on the Point lane — and the write
boundary, which is what *"money has no representation"* was really about, shipped in `#11836`
(merge `ccf5c4036c09`), pinned by `tests/test_money_value_2782.py::TestWriteBoundary`. Its
cross-company **identity** clause was **moved onto `#2730`, not dropped**, so `#2782` is not one of
the problem map's survivors and must not be counted as one. A closed issue that *is* a fixed
defect — the mirror of `#2787`, and the reason a survivor count is only ever read from the problem
map's own table.

Tracker closure is not the status question; this page buckets by the
tracker's own `stateReason` and says so, which is exactly why the bucket column must be read as a
*closure* fact and never as a *fix* fact.

**For the status question — *is defect N still broken?* — read
`docs/architecture/MEMORY-SUBSTRATE-PROBLEM-MAP.md`, not this page and not the issue bodies.**
It re-cut all 26 entries with a commit anchor plus an in-tree pin per fixed row, and it records
that its own proposed `E ──► A,B ──► C ──► D` dependency order is **spent**.

---

## 7. What this changes for a lane reading #2852 today

1. **Do not re-derive corrections (c), (d), (e), (g), (k).** They are answers, and the first four
   are answers the *code now embodies*. A "the Point fold drops props" finding is a false positive
   against the current tree.
2. **Do not trust a `~/.swarm/…` citation.** It resolves on none of the machines measured (§5) — a
   one-host reading, stated as one, not a fleet-wide census.
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
  claim class with no defined handling, which is how a superseded correction gets read as live. §1's
  table IS the authority, so its five rows must equal the enforced tuple — in order, which is why a
  pure reordering reds too and names both sequences; deleting one was measured to
  leave every arm green while §3 still used the verdict;
- every **`path:line` citation** in §3 and §5 resolves in the tree, and the line number is in range —
  a citation that points at nothing is the doc's own "declared ≠ stored" defect, and it looks exactly
  like evidence. ⛔ **In range is not ON TARGET**: this arm cannot tell a citation that lands on its
  symbol from one that lands on a comment above it once the file shifts. Measured on this page's path: rows
  (c) and (g) cited `_upsert_point_props` at line 2632 and its `_persist_extra_props` call at line
  3115 — correct at `f70d884b7`. `main` then added 16 lines to that file (`43db5c0f55`) and 13 more
  (`927c24e233`); at 2648 and 3131 the citations had slid onto a `#2958` comment and a `#2795`
  comment while `--check` stayed at exit 0. Re-measured by hand to lines 2661 and 3144 at that
  ask — and `main` moved the file a THIRD time before this page's merge head, carrying them to
  **2679** and **3162**, and `sdk.py` with it: row (a)'s docstring quote to `tortoise/sdk.py:17109`
  and its call to `tortoise/sdk.py:17530`, row (d)'s range to `tortoise/sdk.py:36215-36218`. Every one
  of those moves stayed IN RANGE with `--check` at exit 0: 2661 landed on a bare `continue`, 17045 on
  a `_rollup_llm(...)` call, 17478 on a `# deliberately EXCLUDES validFrom` comment. That is the arm's
  limit, measured three times on one page — and it will
  rot again the moment the file moves, which is why the symbol arm is the missing half. That third
  move is `main`'s `9958281e7c`, which shifted `sdk.py` by **52** lines net (17045 → 17097, `--numstat`
  53/1), taking `_extract_session_v2`, `create_point` and row (d)'s
  quoted range with it; `tests/test_substrate_map.py::test_every_symbol_citation_in_section5_lands_on_the_symbol_it_names`
  is the sibling that pins symbol identity, and no such arm exists here. That includes the
  **shorthand `:LINE`** form used inside a cell — in row (b),
  `tortoise/commit_schema.py:395` is followed by `:792` and `:1002`. A shorthand resolves against the
  nearest preceding full-path citation, and one with nothing to anchor it is refused rather than
  guessed; without that rule, the load-bearing half of rows (b), (c) and (e) was unchecked while this
  section said it was checked. **A `path:line`-shaped token that the resolver cannot read is
  refused**, not skipped — and THREE spellings are measured here: `path <LINE>` (a path followed by
  whitespace and a number), `path|LINE` (the same, with a stray pipe), and the **GitHub permalink**
  form (path, `#`, `L`, line number), because
  every wider delimiter set refused correct prose when tried
  against this repository's own `docs/`: `_` parses an identifier (`tests/test_findings_ledger_2852.py`),
  `-` parses a dated slug, `/` parses plain paths and dates (600+ spans across the whole `docs/`
  tree, re-derived rather than carried), `#`
  parses the `owner/repo#N` issue refs this repo writes everywhere, `@` parses commit refs, and `,`
  parses SQL and call prose. The pipe is refused because it was refused before the narrowing and
  dropping it silently was a red-to-green the carve-out list did not admit: no code span in the 1150
  tracked files under `docs/` (nor the 144 other tracked `*.md`) carries a `path|LINE` form, so
  refusing it cannot red this repository's prose (the `docs/` population is what moves — re-derive it
  with `git ls-files docs | wc -l`, never carry this number forward). The permalink form survives that objection and the
  rest do not: an
  issue ref carries no `L`, so `#` + `L` + digits is unambiguous, and it is what a human writes when
  copying a GitHub link — the spelling most likely to arrive looking verified while going unchecked
  (it did: both an out-of-range permalink and an in-range one left this page CLEAN before the arm
  existed). It is measured, not assumed: every code span in all 1150 tracked files under `docs/`,
  plus the 144 other tracked `*.md`, was swept with the shipped pattern — zero matches, so refusing
  it cannot red this repository's own prose. Rewriting a citation's delimiter is therefore **not**
  universally refused: `path#LINE` (the bare-number form, which IS what collides with issue refs),
  `path@LINE`, `path-LINE`, `path_LINE`, `path/LINE`, `path,LINE`,
  `path:L LINE` and `path:L16` (the `#L16` sibling of a permalink) are **named carve-outs**, exactly as the rest are, because a rule that refuses
  this page's own issue refs and paths is the false-accusation class one arm over — and so is
  **`path N --flag`** (a numeric ARGUMENT rather than a line, indistinguishable from a command that
  does not start with a recognised program name) and an **HTTP-status line**, in exactly the form the
  code measures: **any token ending `.html`/`.htm` followed by a status in 100–599**, METHOD prefix
  or not, with the literals left unspanned on purpose because this scan refuses them. Measured with
  `check_citation_shapes`, not assumed — the skipped forms, which may be written in backticks:
  `index.html 200`, `GET /index.html 200` (a METHOD prefix does **not** disqualify it, which an
  earlier revision of this section got backwards), `x.html 100`, `x.html 599`. And the refused ones,
  which must therefore stay OUT of backticks: page.html 600 and page.html 099 are refused because the
  band is 100–599, and so is any status after an upstream path or after a METHOD-and-path line whose
  path does not end in `.html`/`.htm` — error text is NOT a carve-out of its own, so a status inside
  running prose is refused wherever it appears.
  ⚠️ The exemption is a SUFFIX test on the token, so it also swallows `website/404.html 200` — a real
  118-line file, i.e. an out-of-range citation the arm skips. That is the carve-out's recorded cost,
  not an oversight: narrowing it to tracked files would make the exemption depend on the very tree it
  is meant to check independently. The rest of this
  paragraph is what IS measured. Skipping it was a hole with
  three doors — a `.txt` path, an extension-less path, and a leading-dot path like
  `.github/workflows/ci.yml:892` matched nothing, so reformatting a citation was enough to defuse a
  real red — and there was a fourth: **one inserted space**, in the form “tortoise/pipeline .py:16”
  or “sdk.py: 16962”, matched neither the resolver nor a refusal whose token class excluded
  whitespace, so it went entirely unchecked (measured, `--check` exited 0). The scan is the **whole
  page**, not just §3/§5: scoping it to the two sections named above left §2's own citation free to be
  mangled — those two strings are deliberately NOT in code spans, because the scan would (correctly)
  refuse them. A trailing character is refused the same way: a trailing space after the line number,
  a space after the shorthand's number, and a full stop after an out-of-range number all match no
  resolver, so they are refused rather than skipped (the examples are left unspanned on purpose — the
  scan would, correctly, refuse them). Two
  carve-outs are named rather than implied away: the delimiter shapes listed above, a
  **backticked command** (a legal evidence form whose trailing number is an argument —
  `uv run python tools/collision_preflight.py 2852 --repo .` must not red this context), and a
  **marker-less, extension-less filename**
  (a bare `Makefile` with a line number) is not treated as a path — a host:port or `key: value` in
  prose must not red this
  required context — and a row's tally class is its **leading** bold token, which is why row (j)'s
  `MOOT-BY-LOSS as an object` half does not move the count;
  A citation whose path **resolves outside the repository** (a `..` escape) is refused for the same
  reason: an existence check alone certified a `..`-escaping citation to a real file the repo does
  not contain — the "looks exactly like evidence" class this page exists to catch (that literal is
  left out of a code span on purpose: this very scan refuses it, which is the proof). An
  **absolute** path is not exempt either: it is resolved against the repository root and refused if
  it points outside it, because rewriting every citation to an absolute one used to leave the page
  CLEAN with nothing resolved. A citation **embedded in a longer span** is refused too — appending one
  word inside the backticks defused a legitimate out-of-range red (the example is unspanned for the
  same reason as the others above). A **`~`-rooted path is not a carve-out either**: it is refused as a
  dangling citation, in both spellings (`path:line` and the permalink form), because §5's own first
  consequence already says a `~/.swarm` citation is now a **broken** citation. This page carried a
  `~` carve-out until #11902's review measured it **fail-open**: the exemption skipped both spellings,
  so an out-of-range `~/`-rooted citation read CLEAN and the only floor under it was "nothing resolved
  at all" — and the carve-out's stated justification was itself false, since no revision of this page
  has ever carried a `~`-rooted `path:line` or permalink (measured over every commit that touches it
  — re-run `git rev-list --count HEAD -- docs/architecture/MEMORY-SUBSTRATE-FINDINGS-LEDGER.md`,
  which read 35 when this was written and grows with each edit to this page, so it is a command and
  not a constant: zero matches in either spelling, in every revision). The exemption protected nothing and defused a real red, so it is
  gone rather than narrowed;
- the **§3 headline tally** is derived too. The "N of the twelve rows no longer read as written"
  count, the letter list beside it, and the four-of-the-seven "overtaken" figure are all recomputed
  from the verdict cells and compared with the sentence a reader actually quotes. Unguarded, flipping
  one verdict cell to `STILL-TRUE` left the page asserting a superseded count with `--check` at
  **rc 0** — and that number is repeated in this repo's `ci.yml`, `config/ci-surfaces.yml`,
  `docs/00_index.md`, this module's docstring and the test module's, so a wrong one propagates well
  beyond the page. It must also be the ONLY such paragraph: a decoy carrying a correct-looking tally,
  inserted anywhere above §3, took the match while the real sentence was falsified (measured, rc 0),
  so more than one is a page defect — and the paragraph must likewise contain **exactly one sentence**
  carrying that phrase, because the arm locates its subject *by pattern*: it joined every matching
  sentence, so a second one in the same paragraph could supply the letters, the per-letter verdict
  attributions and the overtaken count while the sentence a lane quotes went stale (measured on the
  pre-fix tool: falsifying the real sentence beside a compliant decoy returned `[]`). ⛔ A sentence
  that only QUOTES a figure or a letter list asserts nothing — the page discusses both phrases — so
  quoted spans are removed before each phrase test and a quoted full stop does not end a sentence;
  counting them reded a clarifying edit (measured), the false-accusation class these arms must not
  create. This is the §6
  survivor arm's "keep exactly one" rule, one level down. ⛔ The SAME rule applies to the
  **complement** list ("still read as written"), which was joined across sentences and compared as a
  set-union: a second complement sentence could assert a STILL-TRUE letter the real sentence does not,
  so the page could say *seven of the twelve no longer read as written* beside *only `**(a)**` still
  read as written* — 7 + 1 against a table of twelve — at rc 0 (measured). ⛔ **THE SENTENCE IS
  SPLIT ON ITS OWN PHRASE, AND COMPARED BY LIST MEMBERSHIP.** The page's shape is `<still list>`,
  the phrase, then the complement its negation governs, so the PHRASE is the boundary. Reading
  membership from text adjacent to either end of that split was wrong in both directions, and each
  was measured: a marked letter in prose — this page writes `(i)` as a roman-numeral enumerator and
  `(a)`/`(c)`/`**(e)**`/`**(i)**` as cross-references — certified a member the list omits (fail-open)
  and reded a correct page that refers to a non-still row (false accusation); and peeling the
  governed group off the clause END stopped at the first word between the group and its verb (any
  adverb) while refusing a correct complement joined by `&`, `or`, `also`, `plus`, `as well as`,
  `followed by` or `namely`, all of which this section lists as separators. Membership is now the RUN
  the page wrote, on each side of the phrase, so no separator set and no prose can change it. ⚠️ The
  one-member case is the DECLARED OVER-REFUSAL that buys it: a lone PARENTHESISED letter is prose (the
  same rule that keeps a stray `(c)` out of candidacy), so a complement naming a single row must write
  it marked — `a` in code, bold or italic — rather than as a bare parenthetical. ⛔ **AND THE CLAUSE THE
  NEGATION GOVERNS IS VALIDATED, NOT DISCARDED.** It used to be sliced off unread, so the group could
  name any rows at all — including the STILL-TRUE ones, i.e. the sentence could contradict the table
  and itself at rc 0 (measured, fixed, pinned). It is the complement, so every letter in its run must
  be a row the table does NOT call STILL-TRUE; a lone MARKED letter there is only read when the span
  IS that item, because otherwise it is a cross-reference and reading it accuses a correct page
  (measured both ways). ⛔ **THE PHRASE — NOT THE WORD `no longer` — DECIDES WHICH GROUP A SENTENCE IS.**
  Excluding every sentence containing `no longer` made that governor unreachable, so the natural
  spelling of a STALE second sentence (`…, while the rest no longer do.`) rode out of the "exactly
  one" rule while the CORRECT complement written the same way red (both measured, both fixed).
  ⛔ **AND THE GROUP IS OFTEN ITS OWN SENTENCE.** The page's ellipsis closes the complement at the
  phrase and spells the governed group separately (`… still read as written. — **(c)** and **(d)** do
  not.`), sometimes wedged between the non-still tally and the still one. Such a fragment carries
  NEITHER tally phrase, so every phrase-driven arm looked past it: measured, a fragment naming
  STILL-TRUE rows read CLEAN in both positions while the identical text inside the sentence red. So
  every sentence in the paragraph is asked one question — is everything before its negation verb
  nothing but letter items and separators? Yes ⇒ governed group, validated; a real word in that span
  (`Rows (i) and (j) do not apply here.`) ⇒ prose, never accused. Two further bounds, both measured:
  the verb must govern READING — the text after it is the ellipsis (`do not.`), a form of `read`
  (`do not read`, `read as written any more`), or `do` again (`no longer do.`), never something
  else (`… do not need re-reading.` states exactly what the table says and was wrongly RED before
  that check); and `Only` — this page's own list label — may open the span, since
  `Only **(i)** and **(j)** do not.` otherwise read CLEAN; a LONE MARKED item is read when the
  fragment is nothing but that item, the same rule as the governed-span arm (`… — **(i)** do not.`
  read CLEAN while the identical claim inside the sentence red, measured). ⚠️ **STATED RESIDUAL:** the verb span
  after the negation must be nothing but a form of `read` / `do` and punctuation. So a negation of
  any OTHER verb is not read (`… no longer hold.`, `… do not overlap the fix.`), **and neither is a
  PUNCTUATED continuation** (`… do not, unlike the rest.` name rows too). Both CLEAN sides are the
  deliberate half of the trade, pinned in the test so they cannot drift: `… did not move.`,
  `… did not, however, move.` and `… do not, in fact, need re-reading.` are TRUE sentences of the
  IDENTICAL shape — punctuation does not separate them from the false ones, and allowing it made
  the verb requirement moot. A miss on this page is visible on the page it reds nowhere; a false
  accusation reds a REQUIRED job.
  ⚠️ **Residual, stated:** the
  "exactly one" rule is scoped to the tally's own paragraph, so a second complementary or "no longer"
  sentence **two newlines below** it is not counted. Widening it to the whole page is not available
  (this section discusses both phrases) and a section-scoped widening is not yet measured, so read it
  as a known hole rather than a guarantee (#12028).
  ⚠️ WHAT MAKES A SENTENCE A CANDIDATE IS WIDER THAN "a letter list of two or
  more", and the code says so: a sentence qualifies if EITHER the four-form gate matches — which
  admits a SINGLE marked letter (``Note `c` still read as written.`` is a candidate on one letter) — OR
  the extractor names two or more letters, in any spelling and with NO join required between them
  (`Note **(c)** is fine and **(d)** is fine, so both still read as written.` qualifies). Each letter
  may be written bare, bold, code or italic, every form bounded to the twelve letters §3's table can
  name. A page author who assumes "two comma-joined letters" UNDERSTATES this and gets a red they did
  not expect. ⛔ WHAT MAKES A BARE MEMBER A MEMBER IS THE
  LIST RUN, NOT THE CHARACTER AFTER IT: a list is a run of two or more items joined by separators
  (`,`, `;`, `&`, `and`, `or`, `also`, `plus`, `as well as`, followed by, namely) with nothing else
  between them — a word of any other kind ends the run, so prose appended after the phrase is a run
  of its own and never a member OF THE PRECEDING RUN (a two-item run of its own DOES
  contribute elsewhere in the sentence — the residual census below states that cost). The ending-rule shape this replaced was wrong in BOTH directions,
  measured: too loose, `, i.e. today.` supplied `i` and closed a stale complement list, and a
  connector branch with no ending rule at all let `, and I think so.` supply `i` and
  `, and a claim §1 supports.` supply `a`; too tight, it DROPPED a member a sentence writes —
  `Only a, f, h, i, j still read as written.` lost its trailing `j`, and a bare `c` inside
  `, c and **(j)**` vanished so the compared set silently equalled the table's. `a` and `i` are the
  only single letters that are also English words, so WHICH SIDE IS EVIDENCE DEPENDS ON
  WHERE THE LETTER SITS: an INTERIOR `a`/`i` is read only when the separator to its left is
  HOMOGENEOUS — exactly one punctuation mark (`,` `;` `&`) or exactly one bare connector
  (`and`, `or`, `also`, `plus`, as well as, followed by, namely), both of which §8 lists as
  separators, so `a, f, h, i, j` AND `a and f and h and i and j` must each read every member;
  a MIXTURE of the two is REFUSED, which is a declared over-refusal (`a, f, h, and i, j`
  loses its `i`) and it is deliberate: the mixture's bytes ARE prose's bytes, so the same
  bytes can either read a phantom (`…, and I, **(j)** …` closed a stale complement list) or
  drop a member, and a false PASS outranks the friction of an over-block; the TAIL is
  unaffected — it is decided by what FOLLOWS it, so the Oxford-comma spelling
  `a, f, h, and i.` does read its `i`;
  the HEAD of a list is
  read because a run's first item has no item before it, so there is no separator-side
  evidence to consult — a choice, not a proof (see the residuals below);
  and the END of a run is read only when the sentence's own ending follows — a full stop that
  does not begin an abbreviation, the end of the sentence, a closing parenthesis (`)`; the
  class is `[):—–]`, so `]` and `}` are not accepted), a colon or an em/en dash (a plain  hyphen is not accepted), or one of the list's own predicates as a whole word
  (`still read` yes, `still-unsolved` no). ⛔ **SCOPE OF THAT RULE, STATED BECAUSE THE PAGE ONCE
  OVERSTATED IT:** the ending test is consulted for `a` and `i` ONLY, since they are the only single
  letters that are also English words — every other bare member is decided by the run it sits in, so
  `b, c, d` followed by ordinary prose reads all three. A one-letter run is never a list, in either
  form. ⛔ A bare comma is NOT an ending for the last item:
  a comma there means the
  run did not end, so `Only a, f, h, i, and then some.` must not read `i` (measured — an
  ordering that tested the LEFT side first did read it). ⚠️ FIVE OVER-READINGS remain open and
  none is closable in code, because each is byte-identical to a spelling §8 accepts: a SECOND
  RUN later in the same sentence — membership is sentence-global, so `… still read as written;
  see h, i.` supplies `i` (and scoping the arm to the first run is refused, because a letter
  added in a later clause must still be compared — `test_a_letter_added_to_either_tally_list_is_compared_in_every_spelling`);
  an
  `a`/`i` inside a run beside a homogeneous separator (`…, a, …`, or `… and a and …`,
  indistinguishable from the bare and connector spellings); a connector-preceded TAIL with an accepted ending (`a, f, h, and i.` IS the
  Oxford-comma spelling, so it must read); a TAIL followed by one of the arm's own
  predicates (a verb phrase can start with one); and a prose `I`/`a` at a run's HEAD. One
  AMBIGUOUS OVER-REFUSAL is deliberate and is not closable either: an INTERIOR `a`/`i` joined by a
  MIXED separator (`a, f, h, and i, j`) loses that member (only `a`/`i` are subject to the rule —
  `a, f, h, and c, j` keeps its `c`), because those bytes are prose's bytes
  and a false PASS outranks the friction of an over-block; the TAIL is decided by what
  follows it, so the Oxford-comma spelling above still reads. ⛔ WHAT MAKES NONE CLOSABLE IS THE
  BYTE-IDENTITY, not any single cost: closing one means choosing the other side's, so for the
  first the price is a broken pin (its scope fix is refused for exactly that reason) and for the
  predicate-tailed one it is a wider predicate list, i.e. accepting the over-block. Markup
  around a member never decides which items a LIST has (`**(c)**` and `(c)` are the same
  member), so a `**` cannot change a run's membership; ⛔ but markup DOES decide CANDIDACY for
  a sentence naming ONE letter, because only the marked spellings match standalone: measured,
  `Note **(c)** still read as written.` appended to §3's takeaway paragraph counts as a second
  tally while the otherwise identical `Note (c) still read as written.` does not (a single
  parenthetical letter in prose is not a list, by design); for a list of two or more the
  extractor disjunct makes markup neutral. Code, italic and
  uppercase spellings are read too, and the letter EXTRACTOR reads every spelling the candidate
  test accepts, so a sentence cannot qualify on its markup and then contribute no letters.
  ⛔ THAT WAS TRUE OF THE EXTRACTOR AND FALSE OF THREE ARMS BESIDE IT — closed at #11902's
  round-4 review, and recorded here because it is the same defect class this bullet describes.
  The verdict-ATTRIBUTION scan, the MENTION scan and the negated-COMPLEMENT strip each carried
  their own `\*\*\(([a-z])\)\*\*`, i.e. `**(x)**` only, while the extractor and §8 accept four
  spellings. Measured on this page: `Note (d) is **ADOPTED**.` beside the `(d)` SUPERSEDED-BY-FIX
  row read CLEAN (a contradicting verdict nobody saw), the class-first spelling the checker's own
  comment blesses was REFUSED as a swap of all seven rows, and the documented
  negated complement (a `— <letters> do not.` clause, letters in code or italic) was
  COUNTED into the STILL-TRUE list. One constant
  (`_LETTER_ALT_MARKED`) now BUILDS the extractor's regex and is embedded in the complement
  pattern, and `test_the_span_readers_and_the_extractor_share_one_spelling_list` pins that it
  stays the single source — because a first attempt at this fix left a SECOND literal beside it,
  and a review measured the drift it permitted: a spelling added to one side alone re-opened the
  fail-open (or the false accusation) with a green suite.
  ⛔ ONE SPELLING IS DELIBERATELY NOT READ, and it is named here rather than left to a test
  docstring: the BARE form **outside** a list run. `Note d is **ADOPTED**.` beside the `(d)`
  row reads CLEAN, so markup still decides THAT case — `a` and `i` are English words, and reading
  every bare `[a-l]` token as a verdict claim would let ordinary prose assign one, which is the
  false-accusation direction this arm must not create either. A bare letter IS read where it is
  unambiguous: inside a list RUN (the §3 tally extractor), and in a clause-anchored complement
  (the `— <letters> do not.` form, where the group ends the clause). Pinned in both directions by
  `test_a_bare_letter_outside_a_list_run_assigns_no_verdict` and
  `test_the_complement_clause_is_read_in_every_spelling`.
  A single parenthetical letter in prose is **not** a list — `row (c) still read as written` is a
  sentence, not a tally — and a
  sentence that merely QUOTES the phrase has no list to disagree with the table, so counting it reded
  a clarifying edit (measured) — the false-accusation class the neighbouring citation arms exist to
  avoid. Requiring **markup** rather than a list was itself fail-open, measured: a de-bolded stale
  letter list beside one compliant bolded decoy read CLEAN while the parent refused it. The quote
  removal applies to the PHRASE only; a quoted letter still names that letter, and stripping the
  letters too let a quoted list ride out of the count (measured, rc 0);
- every **§3 evidence cell** names a `path:line`, an issue, a command or a §N (§1's own rule), and
  the §N must name a heading that EXISTS — accepting any `§<digit>` let all twelve cells be replaced
  with a reference to a section the page does not have (measured, rc 0). Without this, the whole
  evidence column can be **emptied** while every verdict still passes — a red defused by deleting
  what it pointed at, which is the one move this page exists to make impossible. The number is read
  **whole**: a single-digit read parsed `§10` as `§1`, so every two-digit reference to a section the
  page does not have was accepted as evidence (measured, rc 0);
- the **two §3 content columns** — the retracted claim and its correction — must each carry a letter
  or a digit a READER can read, and the correction must not be a copy of its claim. Without a floor on them a row
  could be reduced to `| **e** |  |  | **ADOPTED** | <citation> |`: a verdict with neither the claim
  that was retracted nor the correction that replaced it, while the citation beside it still opened
  and every other arm stayed green (measured, rc 0 — the citation was never the hole, which is why
  the evidence floor could not see it). ⛔ **The test is not `.strip()`**: a cell that is not blank
  can still RENDER blank, so the cell is read with tags and comments stripped, markdown links
  reduced to their label, and HTML entities decoded — and `&nbsp;`, `<br>`, `<!-- removed -->` and
  `[](https://x)` are all refused for carrying no word (each measured at `c5990249c0` leaving
  `--check` at rc 0 once the whitespace floor alone was in place). ⛔ **A code span is LITERAL**:
  `` `<br>` `` renders the text `<br>`, so nothing inside backticks is treated as a tag, a comment
  or an entity — and the cell is scanned LEFT TO RIGHT, so a comment wrapping a span still renders
  as nothing, while a span wrapping an entity shows that entity's SOURCE text literally (`` `&nbsp;` ``
  is the six characters `&nbsp;`, which is exactly why it counts). A run of N backticks closes
  only on a run of exactly N, and an **unmatched** run is literal text (CommonMark opens no span
  without a closer), so a stray backtick in front of `<br>` still carries a tag: treating it as
  code let a plain typo of the accepted spelling hide markup and pass as content (measured). A
  **backslash escape** renders
  its character, so `\<br>` is the literal text `<br>` (accepted) while a backslash in the SOURCE
  must not be swallowed by the row splitter — swallowing it rewrote the cell before every reader
  saw it, and one escaped backtick in front of a blank-rendering payload defeated all four families
  below (measured, end to end). The tag family includes **declarations and processing
  instructions** (`<!DOCTYPE html>`, `<![CDATA[x]]>`, `<?php ?>`), which also render as nothing.
  ⛔ **A tag is consumed WHOLE**, with the renderer's own grammar: a quoted attribute may contain
  `>`, a backtick or a backslash, so an element whose quoted title carries a backticked word
  is still ONE construct that renders as
  nothing. Stripping tags per scanned SEGMENT instead meant a boundary inside the tag ended the
  segment before its `>`, the tag never matched, and its own text was read as content while the
  cell rendered blank — 41 of 84 tag shapes, in all four columns (measured), including a blank
  VERDICT cell carrying a phantom bold token that satisfied both verdict arms. A **link is a unit
  too**, and its LABEL is what renders: reducing links only inside a scanned chunk left a link
  whose label carried a tag unreduced, so the DESTINATION's letters counted as content in 109 of
  156 measured fail-opens. The comment matcher is the renderer's as well: an empty comment may be
  closed ABRUPTLY (an opener followed immediately by a single `>` or by `->`), which the renderer
  treats as COMPLETE rather than unterminated; and a comment's text may not end with `-`, so
  `<!--a--->` is literal text and readable. So
  `<div title="a > b"></div>` is now refused rather than declared, because it really does render
  as nothing. A close tag carries no attributes, so `</a b>` is literal text. The **character
  reference** is the renderer's too: it needs its **semicolon** — `&nbsp` renders five visible
  characters and is accepted, while `&nbsp;` renders nothing. A **raw-text element's body** is not
  rendered at all, so `<script>**STILL-TRUE**</script>` renders blank and is refused — in every
  CASE (`<SCRIPT>`), and up to the close tag a browser ACTUALLY honours: `</name` then whitespace
  then `>`, so `</script foo=">**STILL-TRUE**">` closes nothing and its attribute text stays
  hidden, while a `` `</script>` `` inside a code span is escaped and closes nothing either
  (measured against a real browser; reading the attribute text as content was a fail-open in all
  four columns). `<script>` additionally has the tokenizer's ESCAPED and DOUBLE-escaped states, so
  `<!-- </script>AFTER -->` SHOWS `AFTER -->` (escaped: the close tag ends the element),
  `<!--<script></script>-->lorem` shows nothing (double-escaped: the inner close only returns to the
  escaped state), and `<!--<script>--></script>VISIBLE` SHOWS `VISIBLE`, because `-->` leaves BOTH
  dash-dash states for the data state. `<template>` is the mirror case — its content is PARSED as
  markup but never rendered — so there a `</template` inside a COMMENT, a TAG's quoted attribute, a
  code span, an escape or a recognised link closes nothing, the element NESTS (an inner
  `</template>` closes only the inner one), and a trailing `/` does not self-close it (`<template/>`
  OPENS one), which is why `<template><template>x</template>VISIBLE` renders blank and is refused. A browser SHOWS the text of `<textarea>`, `<xmp>` and
  `<plaintext>`, so those count — refusing `<plaintext>` was this guard's own false accusation
  until the tenth review measured a real render and found the words on screen.
  A cell whose every word is a **declared placeholder** (`n/a`, `to do`, `tbc`) is
  refused too — unless it also carries a digit (`to do 5`, `x = 2`), because a digit is content
  everywhere else here. A correction whose casefolded token sequence — letters AND digits —
  equals its claim's is refused as a **copy**: the row would restate the claim it exists to
  correct. ⛔ So the direction matters and is measured both ways: restating the claim with
  different MARKUP or CASE is refused (the words are equal), while DERIVING from it by changing a
  count, a line or a section number is not (the digits differ) — which is the shape rows (b), (k)
  and (l) take, and refusing it was a false accusation until the copy test read numbers.
  ⛔ **The rule is emptiness, not length.** A three-word floor was written first and measured false:
  the page's own shortest claim and shortest correction are BOTH three words, so it sat at the
  minimum and refused a legitimate compression (`#2748, #1695, #1418 **closed**`, one content word
  that names exactly what closed), and an ASCII-only word class counted a non-Latin correction as
  zero. A brief cell passes; a cell that records nothing does not. ⛔ **A DIGIT counts**, and that is
  measured too: a letters-only class refused a bare date (`2026-09-10`) and a bare issue list
  (`#2748, #1695, #1418`) while the message claimed the cell was empty — the false-accusation
  direction the neighbouring arms exist to avoid.
  ⚠️ **THREE DECLARED limits, not closed classes:** the placeholder denylist is a list (a placeholder
  outside it is not refused, pinned by `test_an_unlisted_placeholder_is_the_declared_limit`); the
  render-blank list is the five families above (a shape outside them — an element the browser
  hides with CSS, `<span hidden>lorem ipsum</span>` say — renders blank and is not refused, while a
  REFERENCE-style link `[][ref]` is accepted because with no reference definition it renders its
  literal text, and a raw `<img alt="lorem ipsum">` is refused although the browser lays its alt
  text out, because the tag family strips it); and the copy test is
  **equality**, so a NEAR-copy — the claim with one word added, dropped or swapped, including the
  word that inverts it — is not refused. Neither length nor truth is checked: a short true statement
  and a long false one both pass, because this arm asks whether a row records something, not whether
  it records the right thing. The VERDICT and EVIDENCE columns are read the same way — through the
  same rendered text — because until they were, a verdict and a `path:line` citation each wrapped in
  an HTML comment left `--check` at rc 0 (measured): a row that loses its verdict and its openable
  citation while §1 and §8 promise both;
- the **§3 population is pinned** to twelve rows, and the **§6 arithmetic** (open + closed + merged =
  the indexed population) is re-derived from the table rather than trusted — deleting a row and every
  mention of it otherwise passes every referential check. Because a pin selects rows *by shape*, an
  **unrecognised row shape is refused** on all three tables: a thirteenth correction row, a fifth §6
  state bucket, or a §1 row that is not a `**VERDICT**` row, used to be silently invisible (measured —
  appending one left the guard CLEAN). The §1 case is the one that matters most, because §1 is what
  this section names as the authority for the vocabulary §3 is checked against: a sixth claim class
  shaped any other way there is a verdict with **no handling rule**, which is the state §1 exists to
  prevent. §1's header is the **FIRST `|` row, exempted by SHAPE — its first cell reads
  `Verdict` — and nothing else**: the skip used to be a prefix test on the word `Verdict`, so any
  row whose first cell merely began with it (`| Verdicts | … |`) escaped the very arm beside it
  (measured, rc 0); exempting row 0 by *position* instead was a red→green of its own, because
  replacing the header with an unbolded invented class (`| FIXED-IT | … |`) then matched nothing at
  all (measured: 1 problem on the pre-fix tool, `[]` on the positional one); and a table with no
  header at all is refused too. A reworded header is therefore a **two-place edit** — the page and
  `check_vocabulary()` — rather than a silent exemption, which is the same discipline
  `MIN_SELF_TEST_MUTATIONS` imposes on the arm population. The **row-shape** refusal also counts the
  first table's ROWS and every other `|` line in the section, not only table separators, because a
  lone `|` row appended under a blank line brings no separator: measured, it carried the invented
  verdict `FIXED-IT` and left `--check` at rc 0;
- the **§6 survivor sentence's two numbers** are derived as well: its survivor TOTAL must equal the
  OPEN row's own count, and its "already re-measured" count must equal the number of **bullets naming
  an OPEN issue** — the `#2730` bullet is CLOSED and is printed because *issue closure is not evidence
  of a fix*, not because it survives. §6 must also carry **exactly one** sentence matching that
  pattern: the check finds its subject by pattern, so a second one **anywhere** in §6 makes it report
  that §6 carries two and check neither rather than picking one — keep exactly one. Its BULLET SET is
  pinned as well: both derived numbers are functions of the bullets naming an OPEN issue, so the CLOSED
  bullet could be deleted with both counts unchanged (measured, rc 0) — and that bullet is the one whose
  whole job is *issue closure is not evidence of a fix*, so its absence now reds. Before that was
  guarded it took the first match and reported OK over the other, and one added decoy sentence was
  enough (measured). Unguarded, this sentence also carried a false `three` over two
  survivor bullets while §7 said `two`, and the guard reported OK over it: the page's own *declared ≠
  stored* defect, inside the correction that exists to stop it — and the reason two reviewers could
  each "prove" the other side was the stale one.

The self-test that proves the arms its 62 mutations exercise still fire has its own **population
floor** (`MIN_SELF_TEST_MUTATIONS`). An emptied arm set used to print `PASS (0 failing arm(s))` and
exit 0 — a gate reporting OK when it cannot run — so a shrunk population now reds instead. ⛔ **The
population is 62 mutations of the PAGE, not a proof of the guard**, and every one of them edits the
page's text (a falsified tally, a deleted correction row, a second survivor sentence), so anything an
arm reads that is not the page's CONTENT is outside it. Of the arms `check_document()` calls, exactly
two are blind this way — `check_section_presence`, the FIRST one called, and `check_registration`,
which reads `docs/00_index.md` rather than the page — and so are the §3 letter-list rules above, which
decide which letters a sentence names and are internal to the tally arms rather than one of them.
Those rules are THREE and each is deletable — the list-RUN grouping, the `a`/`i` evidence test, and
its ending test. Measured: no-op'ing either arm, or deleting any one of the three letter rules, leaves
`--self-test` reporting `PASS` with exit 0 (measured, all five), while EVERY OTHER arm reds when
no-op'd. Their mechanism is the pytest arms instead: `test_every_section_is_required_not_just_the_bounded_ones`
for the structural arm, `test_unregistered_document_is_rejected` for the registration arm, and
`test_english_prose_cannot_supply_a_missing_member` — beside
`test_a_mixed_separator_interior_join_is_the_declared_over_refusal` — for the letter-rule deletions
(measured, one rule at a time), while `test_a_written_bare_member_is_still_read` and
`test_a_letter_added_to_either_tally_list_is_compared_in_every_spelling` pin the opposite direction —
that a spelling §8 accepts is still READ, so a guard cannot buy a green by refusing correct prose. All
of them run in the `test` jobs on both change classes this guard exists for (`core` for a `tools/`
edit, `tier1` for a `docs/` one).
Two mechanisms, stated separately, for the same reason the ancestry arm below states its own.

Exactly one arm needs `git` **history**: the frontmatter's `measured_at_commit` must be an ancestor of
`HEAD`. The other twelve read this checkout's tree and nothing else, and the tool keeps that split
(`check_document()` / `check_all()`) because the two jobs that run it differ: the REQUIRED `docs` job
checks out at `fetch-depth: 0` and runs `--check` (all thirteen arms), while the `test` jobs check out at
the default depth 1, cannot resolve `f70d884b7` at all, and are where this page's own test module runs.
In a depth-1 clone `--check` therefore reports **exit 2 UNVERIFIED** — never `0`, and never a verdict
against the document it could not read — and `--self-test`, which drives only the tree-only arms,
still runs.

⛔ **Do not read the skipped ancestry test as coverage.** The `docs` job runs no pytest, so a test that
skips in a depth-1 checkout is inert everywhere; the live ancestry claim is proved by that job's
`--check` (which *is* `check_all`), and the **not-an-ancestor branch** is pinned in every pytest job by
a stubbed-`git` case that needs no history. Two mechanisms, stated separately, because "the other job
covers it" was true of the tool and false of the test.

A **second** stubbed-`git` case pins the **grafted/shallow counterpart**, which is the arm's only
false-red direction: in a deepened shallow clone the pinned commit's object IS present (`rev-parse --verify`
succeeds) while the graft boundary cuts the walk, so `merge-base --is-ancestor` exits **1** for a real
ancestor — the same status an unrelated tree produces, and reading it as drift accuses a correct page of
a cut it did not make (#10356). So **only** a `rev-parse --is-shallow-repository` of `false` may certify
drift; `true` *and* an unanswerable probe both take **exit 2 UNVERIFIED**, never a verdict. That is
`tools/substrate_map_check.py`'s rule, copied rather than invented.

**Deliberately not checked:** the issue states in §6, the durability column in §4, and every
`~/.swarm` claim in §5. They are **dated readings of things outside this repository**, and a guard
that asserted them would either fail on someone else's tracker or, worse, pass by not looking. This
follows `SUBSTRATE-REBUILD-MAP.md`'s ruling: *the tracker half is deliberately not guarded — it is
dated and its command is recorded.* The command for §6 is `gh issue view <N> --json state`, and it
was run per issue, not sampled.

⚠️ **Ten more are unguarded, and none is out-of-repo — they are simply not armed yet (#11902, #11957).**
Every one is derivable from **this page or the page it points at**, so the ruling above does not reach
them and a guard for them could not pass by not looking:
**the two derived figures the OPENING restates** — `seven of its twelve rows no longer read as written`
and `three of the seven research briefs … have no in-repo mirror at all` — which are copies of §3's and
§4's counts and are re-derivable from the same tables: measured, rewriting the opening to
`six of its twelve` leaves `--check` at exit 0, because both figure arms require the literal
`of the <number>` and the opening says `of its twelve` (#11902 review, open through three rounds);
§4's row population (count its rows: 14) and its `**No copy of its content in-repo**` cells among the
`research/` rows (3); §6's own heading count (`4 of 40 open`); §7's `36 of 40 have moved` and
`two of the four survivors`; **§6's statement about the problem map's survivors and `#2782`'s status**,
which is a claim about `MEMORY-SUBSTRATE-PROBLEM-MAP.md` — a page `tools/problem_map_check.py` already
parses, so an arm could compare the two rather than trusting this prose (that claim WAS falsified by
`main`'s re-cut and nothing noticed); and **the copies of §3's tally outside this page**
(`docs/00_index.md`, `ci.yml`, `config/ci-surfaces.yml` and the two module docstrings), because
`check_registration()` is a SUBSTRING presence test — an index row whose counts are falsified still
passes, where the sibling `problem_map_check.check_registration` derives its headline instead; and
**the `26`-entry problem-map population quoted three times on this page**, which is derivable from
`MEMORY-SUBSTRATE-PROBLEM-MAP.md` exactly as item 6's claim is — `tools/problem_map_check.py` prints
`26 row(s) parsed` on every run and nothing re-derives 26 here, so adding a row to that map leaves
this page asserting 26 at exit 0. §4's two come straight out of its own table, the same way the §6
arithmetic is already re-derived rather than trusted. Until arms are added they are **hand-derived and
unmeasured**, and that was measured: deleting the `<corpus>/2026-09-09-ontology-evidence.md` row —
after which §4 has 13 rows and covers 14 of the 15 artefacts `#2852` §3 lists — reverting the
opening to *"six of the eight"*, restating §7 as `11 of 40 have moved`, and following this
section's own §6 re-read workflow (OPEN 4→3, one row to CLOSED COMPLETED 32→33, survivors
`four`→`three` so every CHECKED number reconciles) each leave `--check` at exit 0 and the test file
green — the last one while §7 still reads `36 of 40`, i.e. it goes stale silently on the update
this section prescribes. Re-derive all ten from their own tables before quoting them; `#2852` §3 is
the population source, not the derivation.

---

*Re-cut 2026-11-12 from `f70d884b7`, which was `origin/main` at the time of the cut — it is **behind
`origin/main` at this page's merge head** (the guard requires *ancestry*, not equality). Cut against
the original ledger posted on #2852 and re-measured claim by claim. Where a 2026-09-10 claim could no
longer be re-measured, it says so rather than repeating it.*

⛔ **The line numbers are NOT at `f70d884b7`, and this page used to say they were.** Every ISSUE state
here was read individually at that commit; every CODE citation was re-derived by hand at the merge
head, because `main` moved `entities.py` and `sdk.py` three times between the cut and it (the last,
`9958281e7c`, slid all four §3 citations off their symbols while `--check` stayed at exit 0 —
§8 records that measurement). The commit in the frontmatter is therefore the basis of the STATUS half,
not of the line numbers, and a reader who needs a number re-derived should ask the symbol, not this
page.
