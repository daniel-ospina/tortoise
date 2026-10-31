---
title: "The substrate rebuild — epics, lane boundaries by file family, and the interface map"
type: engineering
domain: platform
doc_status: live
created: 2026-10-30
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, STORAGE-ARCHITECTURE.md, EXTRACTOR-V4-ARCHITECTURE.md, issue 5086
measured_at_commit: 4f68a2aa73be4572c3ea88367981ca874ca0e54b
measured_at: 2026-10-30
---

# The substrate rebuild — the map, re-cut

> **What this is.** The organizing artifact for the substrate rebuild: which epic
> each issue belongs to, which issues can run in parallel (**by *file family*, not by
> topic**), what changes hands between components, and what must be sequenced.
>
> **It is a MOVE, not a new map.** Issue #5086 has carried this map since
> 2026-09-24 and it is the authority on *which issue belongs where*. This page is
> the same map **re-measured against `4f68a2aa7`** (2026-10-30) and given a
> versioned home, because the issue-only form has now gone stale **three times**
> — each time costing a full re-derivation before anyone noticed (§9).
>
> **Nothing here schedules anything, and nothing here approves a scope.** An epic
> runs its own `epic-workflow` pipeline with its own human gates; a lane issue
> carries its own collision constraint. Filing an epic is not approving it.

**Scope, restated because it is the thing lanes get wrong:** the contention in
this workstream has never been about *topics*. It is about **files**. Three
epics' worth of work landed in `projection/entities.py` in one week. So the lane
table (§3) is derived from file families and the epic table (§2) is derived from
capability, and the two are deliberately not the same division.

---

## 1. The measurement this re-cut is built on

Everything below that carries a state token was read, not remembered.

```bash
# the tracker half — every number #5086 cites, resolved in THIS repo
gh issue view 5086 --repo daniel-ospina/tortoise --json body,comments \
  --jq '.body + "\n" + ([.comments[].body]|join("\n"))' > /tmp/i5086.txt
grep -oP '(?<![0-9])#\K[0-9]{1,5}' /tmp/i5086.txt | sort -un   # -> 125 numbers (incl. #5086)
# then each number resolved through `issueOrPullRequest` (GraphQL) at 2026-10-30

# the code half
git rev-parse HEAD            # 4f68a2aa73be4572c3ea88367981ca874ca0e54b
.venv/bin/python tools/substrate_map_check.py --check
```

| measurement | value |
|---|---|
| distinct issue/PR numbers cited across #5086's body + **10** comments | **124** (excluding #5086 itself) |
| of those, resolvable in `daniel-ospina/tortoise` | **123** — **76 CLOSED** issues, **33 OPEN** issues, **14 MERGED** PRs |
| not resolvable here | **#4393** — correctly so: it reproduces upstream in `@earendil-works/pi-coding-agent` and has no lane in this repo |
| epics still open | **#5087** (E1) · **#5088** (E3) · **#5090** (E4) · **#7879** (the Postgres record) |
| epic closed | **#5089** (E2) · **#5091** (E5) |
| file-family lanes still open | **1 of 7** — `#5095` (L3) |
| lane issues closed | **6 of 7** — `#5093` `#5094` `#5096` `#5097` `#5149` `#5150` |

**The load-bearing consequence:** the map a lane would read today (the #5086
body plus ten comments) describes **seven live lanes, five live epics and two
named holes**. Measured, it is **one live lane, four live epics, and one live
hole of the two it names** — WS-B is closed outright, while WS-D keeps two of
its three issues open and only its approval gate (#2725) is closed (§8, D5). A
lane that trusted the issue body would plan against a workstream that is ~60 %
shipped.

---

## 2. The epics — re-cut

Grouped by **capability** (what the user gets), not by file. State is the
measured tracker state at `4f68a2aa7`'s date, not a copy of the earlier table.

| # | epic | state | its children, measured |
|---|---|---|---|
| **E1** | **#5087** — Extractor v4 · the selective write path | **OPEN** | OPEN: **#5009** (sufficiency router unbuilt) · **#2684** (verbatim value-spans) · **#1844** (object-only ingestion). **#4432** is OPEN but **excluded from every lane** — the owner dispatched it elsewhere. CLOSED: #5005 #5006 #5007 #5008 #4899 #2453 #2552 #4716 |
| **E2** | **#5089** — Durability · `derived = replay(journal)` | **CLOSED** | every child closed — #5011 #3895 #3998 #4240 #5024 #2897 #3813. ⚠️ **The epic is closed while the code half of its invariant is not** — see §8, delta **D4** |
| **E3** | **#5088** — The D10 fold · a document is a `:Source` | **OPEN** | **zero open children** — #5013 #5026 #5025 #5038 #5012 #1726 all CLOSED. Gated on its own mandated review artifacts: **#5100** (test-design) and **#5101** (capstone), both OPEN |
| **E4** | **#5090** — The vector layer | **OPEN** | **zero open children** — #4997 #4999 both CLOSED. **No test-design issue and no capstone exist for E4** |
| **E5** | **#5091** — Capture-path integrity | **CLOSED** | every child closed — #4897 #4911 #4614 #4714 |
| **E6** | **#7879** — The Postgres record | **OPEN** | the hybrid ruling's pre-customer deliverable. Not one of the five capability epics (#5086's 2026-10-09 amendment attached it here); **unowned** |

**Two states here are findings, not bookkeeping** (both are §9 deltas):

- **E3 and E4 have no open children, yet both are open.** Their capability claim is
  not retired — E3's gate is its own two review artifacts (#5100 capstone,
  #5101 test-design) and E4 has neither. An epic with no open child and no
  capstone is not "in progress"; it is **unverified**, and the map must say which.
- **E2 is CLOSED with an interpreter-level invariant still stated as its goal.**
  `derived = replay(journal)` being true is a claim about the **cache**, and the
  2026-10-08 hybrid ruling made the record authoritative (`#7879`). The epic closed on
  its own children; the *claim* did not become smaller, it changed owner.

---

## 3. The lanes — re-cut, plus the one lane this map was missing

A lane is **not** an epic. Lanes group by **file family**, because that is what
decides whether two workstreams can run at once. A lane spans epics, so no epic
owns it — each lane has its own issue carrying its collision constraint.

| lane | issue | state | file family | what is left |
|---|---|---|---|---|
| **L1** projection-keys | #5093 | **CLOSED** | `tortoise/projection/edges.py` · `tortoise/projection/entities.py` · `tortoise/quota.py` | landed — #5026 → #5025 → #5024 → #5038 all closed |
| **L2** projection-index | #5094 | **CLOSED** | `tortoise/projection/__init__.py` · `tortoise/api.py` · `tortoise/embeddings.py` | landed — #4997 · #4240 closed |
| **L3** extractor | **#5095** | **OPEN** | `tortoise/extractor_v2.py` · `tortoise/commit_schema.py` · `tortoise/kind_classifier.py` | **#5009** (the sufficiency router / escalation write-back: entirely unbuilt) |
| **L4** retrieval-traversal | #5096 | **CLOSED** | `tortoise/assembly.py` · `tortoise/subgraph.py` · `tortoise/fanout.py` · `tortoise/search_engine.py` | landed on #4999; **#2513** (multi-session evidence surface) is still OPEN and is this lane's file family |
| **L5** capture-doors | #5097 | **CLOSED** | `tortoise/hosted_api.py` · `tortoise/capture_spool.py` · `tools/` | landed — #4911 · #4897 · #4614 closed. **#1936** (pack export/import) is still OPEN and is this lane's file family |
| **L6** durability-core | #5149 | **CLOSED** | `tortoise/consistency.py` · `tortoise/backup.py` · the provenance read paths *(prose — not measured)* | landed — #5011 · #3895 · #3998 closed |
| **L7** pack compile | #5150 | **CLOSED** | `tortoise/extractor_v2.py` (`build_master_list`) · `tortoise/commit_schema.py` (`compile_vocab`) · `tortoise/value_extractor.py` (`compile_value_brief`) · pack templates *(prose — not measured)* | landed on #2714 · #1026. **#5163** (three callers still compile the catalog union) is OPEN, and is this family's residual |
| **L8** claim-class write path | **this document** | **DECLARED, no issue yet** | `tortoise/sdk.py` (`supersede_point`, the ingest/content-dedup fold) | **#9417 · #9727 · #9819** — and their record-layer siblings #9836 · #9974 · #10063 belong to **#7879** |

### L8 — the family the map's own last pass admitted it had missed

The 2026-10-26 pass on #5086 recorded three write-path defects and stated them as
*"a single interface concern, not three separate placements, and it is not in the
E1–E5 table."* That is still exactly true — and it is still the only substantive
open **code** family in this workstream with no owner:

| # | the defect | door |
|---|---|---|
| **#9417** | `supersede_point` transfers the **structural** set (`SUPERSEDE_STRUCTURAL_RELS` — the `about*` edges) with a bare `MERGE`, so the binder's confidence is **dropped, not moved** | supersede |
| **#9727** | the same method re-points the predecessor's operator edges onto the successor with a raw edge `MERGE` that never consults the **targets'** claim classes, minting `derived -[IMPL]-> asserted` | supersede |
| **#9819** | an `ingest` can **mutate a TERMINAL (retracted/superseded) point** through the content dedup fold — `create_point(dedup=True)` resolves a content match unconditionally | ingest |

**Why they are one lane and not three issues in three epics:** one file
(`tortoise/sdk.py`), one theme (*a terminal fact can be moved or stamped and
nothing on the way checks the claim class*), and one collision surface — the
file every lane in this rebuild already contends on. Placing them in E1 would
put them behind the extractor's lane; placing them in E2 would put them behind a
**closed** epic. They need their own lane issue declaring the `sdk.py` family,
for the same reason L6 and L7 were opened in 2026-09-25: an unowned family is how
an issue sits open for months with nobody deciding it is not being worked.

**External practice — evidence, not an adoption.** The concrete mechanism here
is *what happens to a superseded fact*. Two comparables were read as primary
sources (rung 2 `web_search model=sonar` was **unavailable on this box** —
`PERPLEXITY_API_KEY not set` — so rung 3 `web_fetch` on the projects' own READMEs
was used instead):

- **Zep / Graphiti** — a fact *is* the edge, and it carries a validity window: the
  README's own framing is *"each fact in a context graph has a validity window:
  when it became true, and when (if ever) it was superseded"*, with retrieval
  reading *"what's true now and what was true before"*. Invalidation **closes the
  old fact**; it does not transplant the old fact's connections onto the new one.
- **mem0** — its current algorithm moved *away* from the ADD/UPDATE/DELETE/NOOP
  decision to **"single-pass ADD-only extraction — one LLM call, no
  UPDATE/DELETE. Memories accumulate; nothing is overwritten."**

So both converge on **do not mutate a superseded fact in place** — which is
precisely the shape #9417, #9727 and #9819 each describe as our defect. This is
recorded as **evidence that the family is a real, recognised interface concern**,
not as an adoption: the fix belongs to #9417/#9727/#9819 and any change of
semantics is theirs to argue, in their own homes, against the recorded claim-class
decision (`#2351`). **The contradiction test is answered: no recorded decision
reaches this placement** — nothing has ever ruled *where* the family lives — and
this document adopts nothing.

---

## 4. The hotspots — re-measured

| file | measured size | why it is a hotspot | what wants it |
|---|---|---|---|
| `tortoise/sdk.py` | **42,535** lines | the public surface — nearly every issue touches it | almost all, incl. **L8** |
| `tortoise/hosted_api.py` | **42,343** lines | the hosted capture/commit doors | L5's residuals (#1936) |
| `tortoise/projection/__init__.py` | **20,411** lines | the aggregator — imports `entities`, `edges`, `grounding`, `propagation`; owns `_ensure_indexes` + the replay entrypoint | E2's residues, E4 |
| `tortoise/extractor_v2.py` | **10,669** lines | the pipeline body | L3, L7's `build_master_list` |
| `tortoise/projection/entities.py` | **6,216** lines | the entity/source writers + `_SOURCE_HANDLED` (the property allowlist) | E3's residues |
| `tortoise/search_engine.py` | **4,895** lines | the read path | L4's residual (#2513) |
| `tortoise/projection/edges.py` | **2,611** lines | `STRUCTURAL_REL_LABELS` + `stub_key` — **the replay keys** | L1 (landed) |
| **`config/ci-surfaces.yml`** | **9,446** lines | ⚠️ **a serialization point, not a file** | **everything** |

**`config/ci-surfaces.yml` is still the plan's only true serialization point.**
It is hand-maintained, and `tools/ci_selection.py --integrity` fails when a
`tests/*.py` file is absent from it — an unregistered test is *never selected on
the relevant source diff*, which is a silent coverage hole rather than an error.
So **every PR that adds a test edits it**, and no two such PRs merge concurrently
without a rebase. Any lane plan that assumes N fully-independent PRs is wrong;
budget for the rebase rather than discover it.

---

## 5. The interface map — what changes hands

Taken from the import graph, not from prose.

| component | depends on | the interface it exposes | issues that change it |
|---|---|---|---|
| `projection/__init__.py` | `projection.entities` · `projection.edges` · `projection.grounding` · `projection.propagation` · `config` · `ids` · `live` | **the replay entrypoint + `_ensure_indexes`** | #5026 · #4997 · #4240 · #5004 (all closed) |
| `projection/entities.py` | `ids` · `live` · `source_identity` | the source/entity writers; `_SOURCE_HANDLED` (`entities.py:1485`) | #5026 · #5025 · #5024 · #5038 (all closed) |
| `projection/edges.py` | `source_identity` | `STRUCTURAL_REL_LABELS` (`:55`) + `stub_key` (`:84`) — **the replay keys** | #5026 · #5025 (closed) |
| `source_identity.py` | *leaf* | canonical source identity (the dedup key) | #5038 |
| `fanout.py` | *leaf* | the fan-out cap | #4999 |
| `vet_gate.py` | `value_gate` | the S2.2 VET decision | E1's remaining steps |
| `embeddings.py` | `env_truthy` · `ids` | embed/serialise — the journal payload | #5004 |
| `consistency.py` | `projection` | divergence detection | #5011 |
| `assembly.py` · `subgraph.py` | `fanout` | traversal + assembly | #5010 |
| `quota.py` | *leaf* | the capped resources — `documents` re-pointed at `:Source` | #5026 · #1726 |

**The "clean LEAF" claim, re-measured** (files under `tortoise/` importing
each module, at `4f68a2aa7`): `fanout` **2** · `source_identity` **4** ·
`embeddings` **14** · `vet_gate` **1** — its one importer is
`extractor_v2.py:157` (`from . import vet_gate as _vet_gate`, a module-level
import beside `value_gate`), which is what reaches
`vet_gate.vet_candidates` at `extractor_v2.py:8464`. That importer is L3's
declared family (§3), so the single inbound edge is inside one lane rather than
across the dependency graph: a `vet_gate` interface change is L3's business.
The leaves created by the merged lanes are still leaves. **The contention
remains entirely in the pre-existing aggregators.**

What a *leaf* cell means in that table, stated because it is narrower than
"dependency-free": **no import of anything under `tortoise/` that EXECUTES at
module level** — the property the contention argument rests on, since a
module-level cycle is what serializes two lanes. "Module level" is read as
"runs at import time", not "sits at column 0": a `try:`/`if:`/`with:`/`for:`/
`while:`/`match:`/`class` at module scope runs too, so an internal import
wrapped in one counts, while the BODY of an `if TYPE_CHECKING:` block (which
does not run) and an in-function import do not. `fanout`,
`source_identity` and `quota` hold on that reading (verified at `4f68a2aa7`: no
internal import of theirs executes at module level). Two of
them are **not** dependency-free in the wider sense, and the difference is
deliberate rather than stale: `source_identity.py:271` and eleven sites in
`quota.py` (`:78`, `:476`, `:518`, `:556`, `:604`, `:647`, `:724`, `:883`,
`:1035`, `:1249`, `:1648`) import an internal module **inside functions**,
lazily, to break import cycles — ten as `tortoise.*` and one (`:1035`)
relatively, `from .security import redact_error`. A lane editing `projection`'s
private helpers still has those call sites to consider.

`vet_gate.py` is consequently **not** a `depends on` leaf: it carries one
internal dependency of its own — `value_gate`, the stdlib-only identifier
predicate that both it and `extractor_v2` import (`vet_gate.py:149`).
`tools/substrate_map_check.py`'s `check_leaf_rows` re-measures exactly this: a
row whose `depends on` cell says `*leaf*` must import nothing under `tortoise/`
at module level, and a row that does is a drift (§10).

### The one interface that must not be broken

`stub_key` and `STRUCTURAL_REL_LABELS` are **the replay keys**: a rebuilt edge
finds its target by *stable name* (`url`, `name`, `coalesce(title, name)`),
**never by `target.id`**. Nothing else in this workstream has that failure mode —
everything else changes behaviour, while these two change *identity*. A wrong
value there does not raise: it silently attaches an edge to the wrong node, or to
none.

**D10 landed:** the `:Document` **node label** is retired and the
`aboutDocument` **edge** is retargeted at `:Source` — that asymmetry is the
whole fold, and it is why L1 and L2 could close. The retirement mechanism is a
symbol pair this guard does check: the writers' retired-key allowlist
`_DOC_RETIRED_KEYS` (`projection/entities.py:1584`) and the graph migration
`relabel_legacy_documents` (`migrate_kinds.py:167`).

⚠️ **The claim "`:Document` is not minted" is deliberately NOT regex-checked**,
and the reason is measured: `:Document` occurs on **55** lines across **17**
files under `tortoise/` at `4f68a2aa7` (`git grep -n ':Document'` → 55 lines,
`git grep -l ':Document'` → 17 files — the file count is not the line count).
`tests/test_substrate_map.py` re-derives both numbers over the **same**
population this sentence is stated in — every *tracked* file under `tortoise/`,
any suffix, not a `.py` walk — so the figure cannot go stale silently. Every one
of those lines is prose that *names the retired label* —
a docstring, a comment, a printed message, or an RST literal in
`migrate_kinds.py:63` describing the pre-D10 shape. A pattern loose enough to
catch a mint also catches those,
and one narrow enough to skip them (`\(\w*:Document`) still matches two RST
literals. A guard carrying a hand-maintained allowlist of docstrings is a guard
nobody can trust, so the label claim stays evidence recorded here rather than a
check that would have to be argued with.

---

## 6. Dependency order — what survives

```text
L1 (#5026) ──┬──► #5025   ── landed ──► the replay keys are consistent
             └──► #5024
E2's invariant ────────► E1 rides on it: the pipeline cannot be trusted
                          to write selectively if a replay is not faithful
E3 (#5038) ────────────► E1's S6 (version-scoped extraction records `sourceVersion`)
#7879 (the record) ────► everything: the cache is derived, the record is truth
L8 (#9417 · #9727 · #9819) ──► E1's payoff: a selective writer on a write path
                          that can still move a terminal fact is not selective
```

**Two hard edges, restated:**

1. **#5026 → #5025 must never parallel** — same function, same resolver, and the
   failure is *silence*. Both are closed, so this edge is now historical; it is
   kept because the same rule applies to any future edit of the pair.
2. **The record before the cache's payoff.** `derived = replay(journal)` makes the
   *cache* faithful; `#7879` makes the *record* the truth. E1 on an unfaithful
   journal just produces less, reproducibly-wrong data.

**New edge, declared here:** **L8 before E1's payoff.** E1 makes the *writer*
selective; #9819 says a terminal point can still be stamped through the ingest
door and #9727 says a superseded fact's edges can still be transplanted. A writer
that selects better into a path that can still move a terminal fact is selective
about the wrong thing.

---

## 7. The review-artifact debt — re-measured

`epic-workflow` mandates two artifacts per epic, and the map has tracked their
absence since 2026-09-24. Measured today:

| artifact | E1 #5087 | E3 #5088 | E4 #5090 | #7879 |
|---|---|---|---|---|
| **test-design / integration-surface map** | ❌ none | ✅ **#5100** | ❌ none | ✅ in `docs/epics/2026-10-10-7879-postgres-record/01-test-design.md` |
| **capstone: clickthrough verification** | ❌ none | ✅ **#5101** | ❌ none | ❌ none |
| **lane review gate** | n/a (L3 open) | n/a | n/a | n/a |

**Why they cannot be written early, restated because it is the reason this table
is allowed to have holes:** a capstone's verification checklist *IS* the
test-design surface map plus the epic's E2E tests. A capstone filed before those
exist is a placeholder, and a placeholder that says "verify end-to-end" is how a
gate becomes ceremonial. Each epic produces its test-design on entering Scope and
its capstone on completing Decompose.

**Lane review vs epic capstone — different questions, both needed.** A lane
review asks *"is this file family still internally consistent?"*, whose sharpest
form is **"does a replay still reproduce the same graph?"** — because
individually-correct edits to a shared writer can still make a replay unfaithful.
An epic capstone asks *"does the capability work end-to-end for a user?"*

---

## 8. The named holes — re-cut

The issue body names two holes and says **"neither has an owner today."** One of
them no longer exists:

| hole | state, measured | what changed |
|---|---|---|
| **WS-B · ontology substrate** — *"the same vocabulary is defined in several places and the definitions disagree"* | **CLOSED** — all seven issues closed (#2766 #2767 #2783 #2786 #2787 #2781 #2818) | the hole was filled by closure, not by a lane. **The standing claim "WS-B has now been open as a hole since 2026-09-25" is retired** |
| **WS-D · venture pack delivery** | **2 of 3 OPEN** — #2696 (meeting-ingestion epic) and #2728 (per-graph pack selection) open; **#2725 — the issue the map names as "the approval gate" — is CLOSED** | the map's *"needs an owner ruling (#2725 is the approval gate)"* is answered in the tracker. `#5163` is the residual that gates #2728's closure |

**Also retired by measurement:** **#4894** (the extractor-v4 design home) is
**CLOSED** — the recommendation the owner was asked for on 2026-09-24 (*"close
it"*) was taken. **#5014** (a review finding must be verified before it is
applied) is **CLOSED**, so the cross-cutting "process, not code" row no longer
needs a home here. **#2820** (the predecessor problem map) remains **OPEN**; this
map promotes its workstreams, it does not close it.

---

## 9. What this re-cut changed — the delta that justifies the file

Read this as the answer to *"why is the map a document now"*. Three completeness
passes have been run on #5086 from the issue body (2026-09-25, two on 2026-09-25
and 2026-10-26 by the owner's own count). **Each pass found the map had silently
drifted, and each pass's correction lived only in a comment** — a surface nothing
reads. This pass measured the drift rather than finding one instance of it.

| # | where the map was | measured at `4f68a2aa7` |
|---|---|---|
| **D1** | *"five epics"*, all live | **two closed** (#5089, #5091), **four open** (#5087 #5088 #5090 #7879) |
| **D2** | *"seven lanes"*, all live, with *"may run with"* cells | **six closed**; only **L3 #5095** open. The *"may run with"* column is moot for closed lanes and must not be read as a current constraint |
| **D3** | the **L7** file family — `build_master_list` *"separate from `extractor_v2.py`"* | **false, and the line numbers drifted too**: `build_master_list` is `extractor_v2.py:774` (the map said `:361`), `compile_vocab` is `commit_schema.py:169` (`:143`), and `compile_value_brief` is `value_extractor.py:89` — not in `extractor_v2.py` at all. L7 shares files with **L3** |
| **D4** | E2's goal stated as `derived = replay(journal)` *for the graph* | E2 is **CLOSED**; the claim moved owner to **#7879** (the record is truth, the cache is derived). *"The graph is faithfully rebuildable"* and *"the graph is not the record"* are not the same claim |
| **D5** | **WS-B** and **WS-D** "no owner today" | WS-B is **closed outright**; WS-D's approval gate **#2725 is closed**. The hole count went 2 → 1 |
| **D6** | *"no epic has a test-design map or a capstone"* | **E3 has both** (#5100, #5101). E1 and E4 have neither, and E4 has no open child at all — so E4's remaining work is *the missing instrument*, not missing code |
| **D7** | the write-path claim-class family *"is not in the E1–E5 table"* | still true, and now **placed**: lane **L8** (§3), with the external-practice evidence recorded and the fixes left where they belong |
| **D8** | *"every PR is blocked by a pre-existing main-red lint check"* (two I001 errors) | **#5120** and **#5130** remain OPEN but are **locally green at `4f68a2aa7`**: `.venv/bin/python -m ruff check tortoise/__main__.py tests/test_document_source_gold.py` → `All checks passed!`. Verify the CI check-run before closing them; do not quote them as a blocker |
| **D9** | the map lived only in an issue body + 10 comments, 66 KB | it is this file, checked by `tools/substrate_map_check.py`, and registered in `docs/00_index.md` |

**What did NOT change** — stated plainly, because a stale map invites
over-correction: the five capability epics' claims are all still real work;
`stub_key` + `STRUCTURAL_REL_LABELS` are still the replay keys; the D10 code half
landed and `:Document` is retired; `config/ci-surfaces.yml` is still a
serialization point; and the review-artifact rule is unchanged.

---

## 10. How this document stops going stale

`tools/substrate_map_check.py` re-measures the **machine-checkable** half of the
map — the half that drifted every previous time — and fails when the document and
the tree disagree:

```bash
.venv/bin/python tools/substrate_map_check.py --check     # 0 clean · 1 drifted · 2 unverified
.venv/bin/python tools/substrate_map_check.py --json      # the measurements it read
```

`2` is a **state error, not a pass**: this checkout could not read the document,
or could not *answer* one of the claims — the case that matters here is the
ancestry claim in a shallow clone, which is reported as `UNVERIFIED` with the
cause named, never as drift (the document is not wrong; the clone is shallow).
A **pre-3.12 interpreter** is that same case and exits `2` as well — the guard
prints its refusal and raises `SystemExit(2)` rather than `SystemExit(<message>)`,
because the latter exits `1`, which this tool reserves for `drifted` and would
report *"the document and the tree disagree"* about a document never read.

The ancestry probe is **bounded at 10 s**, and the bound is classified with the
status arm rather than against it: a locally-present sha answers in 7 ms, an
absent one costs ~24 s on a partial clone while git attempts a lazy fetch, so the
bound exists to stop a CI gate hanging on a typo. A probe that hits it is
therefore *"could not answer"* — the same absent-object condition as git's
error status, arriving slowly — and is reported as `UNVERIFIED`, exit `2`. Both
unverified arms carry the same report token (*"… cannot be verified here"*), so a
caller reading the output or the tests reading `measure()` cannot mistake one arm
for a different verdict. It is
never `1`: `--is-ancestor` exits `0` (ancestor) / `1` (not an ancestor) / a
non-zero status that is not `1` (error), and a timeout is not a git status at
all, so scoring it as drift would hand an operator *"the document and the tree
disagree"* about a correct document.

### Where the guard runs, and which half runs where

Dual-registered on purpose, because the two halves need different checkouts:

| home | what it proves | why there |
|---|---|---|
| `core` + `tier1` (`config/ci-surfaces.yml`) → the `test` shards | everything except the ancestry claim | a **docs-only** edit of this file selects no surface at all and runs `tier1` only, so a `core`-only row would never fire on the diff that actually staled three times |
| the REQUIRED **`docs`** job (`.github/workflows/ci.yml`, next to the `#4454` generated-artifact drift step) | all five claims, including **`measured_at_commit` is an ancestor of `HEAD`** | `docs` checks out at **`fetch-depth: 0`** and has no `paths:` filter, so it cannot inherit the docs-PR skip **and** it has the history the ancestry probe needs (the `test` job's checkout is depth 1, where the object is absent — or present-and-unreachable under the graft, which git answers as "not an ancestor" about a real ancestor, so neither status is drift there — #10356) |
| `CORE_ALSO` (`tools/ci_selection.py`) | the same `test`-shard set, on a **tool-only** edit | `tools/` is a flat non-python prefix and matches no `SOURCE_PATTERNS` row, so without that entry an edit to the tool itself selected nothing |

What it checks, and deliberately does not:

| checked | not checked |
|---|---|
| every **file family** a lane declares resolves on disk — and a §3 lane row that declares its family only in **prose** must say so with `*(prose — not measured)*`, or it is reported: a silently unmeasured row is a CLEAN over nothing (D3 is why L7 now names `extractor_v2.py` / `commit_schema.py` / `value_extractor.py` instead of *"the three compile seams"*) | whether a lane is *live* (that is the tracker's job — §1's command) |
| every **symbol** the interface map places is still **declared** in the file it names (`STRUCTURAL_REL_LABELS`, `stub_key`, `_SOURCE_HANDLED`, `_DOC_RETIRED_KEYS`, `relabel_legacy_documents`, `build_master_list`, `compile_vocab`, `compile_value_brief`) — *declared*, not merely mentioned: a comment or a reader does not satisfy it — and that the document still *names* it | whether the symbol still *means* what the map says |
| every §5 row whose `depends on` cell says `*leaf*` really imports nothing under `tortoise/` that executes at **module level** — a module-scope `try:`/`if:`/`with:`/`for:`/`while:`/`match:`/`class` body counts (it runs at import time), the BODY of an `if TYPE_CHECKING:` block and a lazy in-function import do not (that is how `source_identity` and `quota` break their cycles) | whether a row's dependency list is *complete*: the non-leaf rows name the interfaces that matter to the contention argument, not every import |
| | whether `:Document` is minted anywhere — **not regex-checkable**, and §5 records the measurement showing why |
| | the tracker state of any issue — **the 76/33 split above is a point-in-time measurement, and it is dated in the frontmatter** |
| this document is registered in `docs/00_index.md` | the correctness of the placement judgements |
| `measured_at_commit` is an ancestor of `HEAD` (the map names the tree it was cut from) — proved by the required `docs` job, which has the history | |

**The tracker half cannot be machine-checked, and pretending otherwise would be
the exact failure this document exists to prevent.** So §1 records the *command*,
and §9 D1/D2/D5/D6 record the *result with a date*. Re-running §1's command is the
standing obligation of whoever edits the epic or lane table.

---

## What this document does not do

- **It schedules nothing.** Order within a lane is not a commitment to start.
- **It does not approve any epic's or lane's scope.**
- **It does not reopen a decided question.** D1–D13, D30 and the 2026-10-08 hybrid
  ruling are the owner's; the one decision the map used to call open (V1, *"should
  the vector index live in FalkorDB at all?"*) was **answered by the ruling** —
  vectors leave the graph, so V1 is a scheduled migration and not an open question.
- **It does not supersede #2820** — it promotes its workstreams, and both remain
  readable.
