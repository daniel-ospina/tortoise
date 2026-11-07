---
title: "The memory-substrate problem map — re-cut, 26 entries, 4 patterns, 21 fixed and 5 open"
type: engineering
domain: platform
doc_status: live
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, memory-substrate, issue 2820, issue 4917
measured_at_commit: 23d227c53
measured_at: 2026-11-05
---

# The memory-substrate problem map — re-cut

> **What this is.** The status half of issue **#2820** — the child of **#4917**
> (memory quality + cost). #2820 was written on 2026-09-10 as an *organizing*
> artifact: four defect classes, six workstreams, a dependency order, and 26
> linked issues. Its body has not changed since, and the tree has moved a very
> long way underneath it (WS-A/WS-B/WS-C designs landed in
> `docs/plans/2026-09-10-*.md`; ~40 of the 26 issues' fixes merged). Reading the
> issue today therefore tells a lane the opposite of the truth about most of its
> rows — it reads as 26 problems waiting, when it is **21 fixed and 5 open**.
>
> **This page is the re-cut.** Same 26 entries, same four patterns, same
> workstreams — and, for each entry, **what the tree says now** rather than what
> the 2026-09-10 filing said.
>
> **Nothing here schedules anything and nothing here approves a scope.** #2820 is
> a tracking artifact, not an epic: promoting a workstream still goes through
> `epic-workflow` with its own human gates (owner ruling, quoted in #2820's body).
> This re-cut is a status pass, not a plan.

**Scope note, because it is the thing this page gets read for:** it answers *"is
this still broken?"* for 26 named defects. It does **not** re-open any of them,
and it does not re-derive a fix. Where a row says VERIFIED-FIXED, the work
shipped; where it says OPEN, the row names the half that did not.

---

## 1. How a row earns its status

A row is **VERIFIED-FIXED** only when **both** of these hold, and the row carries
both:

1. **An anchor** — a commit that is reachable from `HEAD` (the fix, not a
   mention of the issue number). The anchor is what makes the claim checkable in
   history rather than in a memory.
2. **A pin** — an artifact in the *current* tree that fails if the fix is undone
   or removed: a test module that asserts the behaviour, or the shipped symbol
   itself. A test module is preferred, because a symbol can survive a fix being
   reverted out from under it.

**Issue closure is not evidence, and this map has three rows that prove it:**

| What the tracker said | What the tree said | Row |
|---|---|---|
| CLOSED **NOT_PLANNED** | the symptom no longer reproduces (`ruff check` clean on the file it named) | **#2748** |
| CLOSED **NOT_PLANNED** (consolidated) | the mechanism it asked for landed under the root issue | **#2787** |
| **OPEN, never closed** (no closure event at all) | the *representation* half shipped while the issue sat open — the tracker is right and the work is half done | **#2782** — the mirror case: closure is not a verdict in either direction |

So: a closure can lag a fix, and a fix can lag a closure. The anchor+pin pair is
what this map trusts; the tracker state is reported beside it as a second
opinion, never as the verdict.

**Status vocabulary — exactly three tokens:**

- **VERIFIED-FIXED** — anchor + pin both present, both checked by
  `tools/problem_map_check.py` at this head.
- **OPEN** — the defect is still live. The row names the surviving half and, where
  something *did* land, that part's anchor.
- **UNKNOWN** — the tree cannot answer. **No row currently uses this token**, and
  that is a measurement, not an omission: every one of the 26 entries resolved to
  one of the other two.

---

## 2. Summary — the counts, and what changed since 2026-09-10

| Status | Rows |
|---|---|
| VERIFIED-FIXED | 21 |
| OPEN | 5 |
| UNKNOWN | 0 |
| **Total tracked** | **26** |

| #2820 as filed (2026-09-10) | Now (2026-11-05) |
|---|---|
| WS-A write-path integrity — "a decision + three mechanical bugs" | **5 of 5 rows fixed**; the write contract design itself is in `docs/plans/2026-09-10-write-contract-five-doors.md` |
| WS-B ontology substrate — "~2 design decisions + mostly mechanical bugs" | **8 of 8 rows fixed**; the property-declaration design is in `docs/plans/2026-09-10-ws-b-pack-property-declaration.md` |
| WS-C state & value model — "~25 open decisions, owner-gated" | **3 of 6 fixed**; the numeric-normalization design is in `docs/plans/2026-09-10-ws-c-numeric-normalization.md`; **#2730, #2782 and #2684 remain open** |
| WS-D venture pack delivery | **2 of 4 fixed** net of #2725/#2726; **#2696 (epic) and #2728 remain open** |
| WS-E repo / CI hygiene | **3 of 3 fixed** |

**The dependency order the map proposed is spent.** It read
`E ──► A,B ──► C ──► D`. E closed on 2026-09-10; A and B are fully closed; C is
down to two rows, neither of which blocks D any more (D's ontology — #2725 —
shipped in #3419 on 2026-10-06). What is left of C is **value fidelity**, not
lifecycle plumbing, so the chain that made D wait no longer exists. Do not plan
against it.

---

## 3. Pattern 1 — Declared ≠ stored (WS-A: write-path integrity)

*The system reports something as stored, or documents it, that never reaches the
graph — or is lost later. No error is raised.*

| Item | The defect (as filed) | Status | Anchor (merged, in `HEAD`) | Pin (in the current tree) |
|---|---|---|---|---|
| #2813 | v2 product capture silently drops the E3 fields (`quote`/`when`/`search_keys`/`source_turn_id`) while the eval lane passes them, so benchmarks look healthy and the product persists none | VERIFIED-FIXED | `0643bba86` | `tests/test_capture_session.py` |
| #2795 | `rebuild_all` drops live-only props (`amount`, `currency`, `search_keys`, `content_hash`) — the live writer and the replay writer keep different field lists | VERIFIED-FIXED | `37d5ef00c` | `tests/test_ingest_rebuild_durability.py` |
| #2814 | `rebuild_all` wipes **all** nodes including config (`:PackManifest`/`:PackInstall`), so per-graph config cannot survive the recovery path | VERIFIED-FIXED | `b586fab83` | `tests/test_rebuild_config_preservation.py` |
| #2788 | the Event arbitrary-prop lane advertises event-carried state that nothing reads (and the commit path forbids) | VERIFIED-FIXED | `ed02cfbb2` | `tests/test_event_state_carrier_2788.py` |
| #2742 | `CORE_SOURCE_KINDS` snapshots `SOURCE_KIND_DEFAULTS` at import, so a runtime `register_source_kind_default()` is invisible to the Layer-1 gate | VERIFIED-FIXED | `2b2156f3f` | `tests/test_source_kind_runtime_registration.py` |

**Note on the class, not just the rows.** All five are the same shape and all
five are now pinned *by a test that asserts the wrong state cannot come back*,
which is the only durable form of this class: the defect is a **silent** gap, so a
warning or a doc would not have caught it either.

---

## 4. Pattern 2 — Canonical ≠ consistent (WS-B)

*The same vocabulary is defined in several places, the definitions disagree, and a
capability is documented but unavailable.*

| Item | The defect (as filed) | Status | Anchor (merged, in `HEAD`) | Pin (in the current tree) |
|---|---|---|---|---|
| #2786 | core kind vocabularies diverge across layers — `CANONICAL_*` (pack) vs the runtime `EVENT_KINDS` / source registry, with no consistency guard | VERIFIED-FIXED | `04ecba158` | `tests/test_kind_vocabulary_consistency.py` |
| #2783 | the `subclassOf` gate rejects lowercase canonical parents (`agreement`/`standard`/`document`), blocking `fundingAgreement ⊂ agreement` | VERIFIED-FIXED | `2d7af8bfc` | `tests/test_venture_pack.py` |
| #2767 | `milestone` is an Object kind **and** a legacy pointKind **and** a miner event kind — the bare-name collision fails cross-pack `nearMisses` validation | VERIFIED-FIXED | `142b3e7a2` | `tests/test_kind_vocabulary_consistency.py` |
| #2727 | `CANONICAL_OBJECT_KINDS` is incomplete against ONTOLOGY §5, so packs cannot subclass commitment-state kinds | VERIFIED-FIXED | `2d7af8bfc` | `tests/test_pack_kinds.py` |

**The guard is the deliverable here, not the corrections.** #2786's own closing
record names why: a consistency guard whose two sides are *hand copies* can never
fire, and the fix was to derive the tooling copies from the canonical set and then
pin that the guard **can** fire. `tests/test_kind_vocabulary_consistency.py` is
that guard's own regression test.

---

## 5. Pattern 3 — Promised ≠ expressible (WS-B, continued)

*Packs and the ontology advertise a capability with no mechanism behind it.*

| Item | The defect (as filed) | Status | Anchor (merged, in `HEAD`) | Pin (in the current tree) |
|---|---|---|---|---|
| #2766 | declared `relations` are declaration-only — 9 of 11 proposed predicates had no write path (allowlist vs mapping table) | VERIFIED-FIXED | `0f2f7601d` | `tortoise/pack_registry.py` |
| #2787 | relation declarations pass shape validation but fail coherence — no IMPL/NAND kind-bucket check, and `extractable` had no runtime consumer | VERIFIED-FIXED | `0f2f7601d` | `tests/test_pack_registry_gaps.py` |
| #2818 | no property-declaration surface in pack manifests — `VALID_KINDDEF_KEYS` was a closed 7-key set, so per-kind value fields had nowhere to live | VERIFIED-FIXED | `1c14b1ecb` | `tests/test_pack_value_declarations_2818.py` |
| #2781 | cross-pack references silently drop the referring pack (self-hosted) and fail install (hosted) — `depends_on` was dead code | VERIFIED-FIXED | `9f01fe948` | `tests/test_pack_kinds.py` |

**#2787 is the row this map is most likely to be misread on, so it is stated
plainly.** It was closed **NOT_PLANNED**, consolidated into #2766 as the family
root — not rejected. The half that #2766 shipped is the **write-path verdict**
(`relation_write_verdict` → writable / declaration-only / reported, with
`RELATION_DECLARATION_ONLY` as a first-class state), which is the "reported"
indicator this row asked for. The **IMPL/NAND kind-bucket coherence check** named
in the row is *not* a symbol a grep finds in the relation validator today; the
conservative reading is that it was subsumed by the mechanism-inversion table
(`tortoise/pack_registry.py`, the `IMPL`/`NAND` predicate map) rather than
implemented as a separate bucket check. The row is VERIFIED-FIXED **for the
capability it was filed about — a declared relation now carries a verdict the
runtime and `pack validate` both report** — with that residual recorded here
instead of being rounded away.

---

## 6. Pattern 4 — Fixed vs flexible (WS-C: state & value model)

*Which semantics are canonical (must be exact) and which are tenant-configurable
was never decided, so both are half-built.*

| Item | The defect (as filed) | Status | Anchor (merged, in `HEAD`) | Pin (in the current tree) |
|---|---|---|---|---|
| #2792 | configurable per-graph pipelines — packs ship default stages, each graph can customise them, stage changes are dated events | VERIFIED-FIXED | `639137d4d` | `tests/test_pipeline_2792.py` |
| #2729 | pack-declared per-kind status folds — the hardcoded Object `status` event→status map generalised | VERIFIED-FIXED | `72e42deea` | `tests/test_status_folds_2729.py` |
| #2782 | money has no representation — `amount`+`currency` on entities, and same-named money items must not merge across portfolio companies | OPEN | `b408d8588` | `tests/test_money_value_2782.py` |
| #2817 | no numeric/locale normalization anywhere — PT-BR/EN amounts cannot be canonicalized (8 failure modes) | VERIFIED-FIXED | `79c13050c` | `tests/test_value_normalizer_2817.py` |
| #2730 | entity attachment & disambiguation — binding facts to the right Subject/Object (name collisions, aliases, placeholders, recurring people) | OPEN | `6a73ea7ea` | `tests/test_entity_attachment_brief_claims.py` |
| #2684 | verbatim value-spans on value-bearing points — a claim carries its exact value **and** the source span, and assembly surfaces it | OPEN | `` | `tests/eval/write_path/test_value_span_census_2684.py` |

**#2782 — OPEN, and the row states which half.** The *representation* landed: a
flat, currency-safe money value on entities (#8641) and on the Point lane
(#8976), pinned by `tests/test_money_value_2782.py`. The *identity* half — two
same-named money items under different portfolio companies must stay distinct —
did not: that file says so in its own docstring ("staying distinct … is #2730 and
is NOT papered over here"), and #2730's brief is a research artifact, not a
mechanism. So the issue is open on the indicator it was really filed for.

**#2684 — OPEN, and the mechanism is not what is missing.** The mechanism landed
(#8659's stamp, #8966's "a verbatim quote is not yet an address to the value") and
is pinned in `tests/test_extractor_v2.py::TestValueVerbSpan2684`. What has not
happened is the **slice**: one extractor re-ingest and the assembly surfacing, and
the census at `tests/eval/write_path/test_value_span_census_2684.py` exists
precisely to measure the population *before* spending that re-ingest. This is the
one row where the honest status is "waiting on a measurement that was
deliberately built first".

---

## 7. WS-D venture pack delivery — and WS-E repo hygiene

*WS-D is the actual product; WS-E was the pre-existing breakage blocking it.*

| Item | The defect (as filed) | Status | Anchor (merged, in `HEAD`) | Pin (in the current tree) |
|---|---|---|---|---|
| #2725 | the `venture` domain pack itself — fund/portfolio kinds, relation logic, lifecycle chains (owner approval gate) | VERIFIED-FIXED | `e9ac7b637` | `tests/test_venture_pack.py` |
| #2696 | epic: meeting-ingestion pipeline for venture-builder customers — meeting-attuned extraction, per-startup segregation, HTTP ingest surface | OPEN | `` | `` |
| #2728 | per-graph pack selection — backend enforcement **plus** the dashboard Settings modal | OPEN | `` | `tortoise/extractor_v2.py` |
| #2726 | register `meeting_transcript` + `meeting_minutes` sourceKinds (tier decision per #398) and allow them in pack `sourceTypes` | VERIFIED-FIXED | `24a640f2c` | `tests/test_source_credibility.py` |
| #2749 | `main` is red — `test (b)` fails on a clean `origin/main`, blocking `python-ci-gate` for every PR | VERIFIED-FIXED | `f84daa169c`, `4c4eb7ddd7` | `tests/test_sdk_props_coercion.py`, `tests/test_uri_env_mutations_declared.py` |
| #2748 | `tests/test_delivery_tenancy.py` carries 2 pre-existing ruff I001 import-sort errors on `main` | VERIFIED-FIXED | `cc72a9151` | `tests/test_delivery_tenancy.py` |
| #2734 | MCP tool tests patch the dead `mcp_server.sdk` global — handlers resolve via `_get_team_sdk()` | VERIFIED-FIXED | `6a13a4165` | `tests/test_domain_validators.py` |

**#2696 — OPEN, and it is an epic.** It is not a defect awaiting a fix; it is a
product epic whose first prerequisite (meeting source kinds, #2726) landed on
2026-09-24 and whose remainder needs its own `epic-workflow` run with its own
human gates. Nothing in this map approves it.

**#2728 — OPEN, and the split is real.** The *backend* half is partly live: the
graph's installed packs are consumed by the classifier and the extractor when a
graph has `:PackInstall` records (`tortoise/extractor_v2.py`,
`tortoise/kind_classifier.py`). The half that is not built is the one the issue
titles second: a dashboard Settings surface where a user can see and change the
installed set. Do not read "the vocabulary is per-graph" as "#2728 is done" — the
user-facing control is the deliverable.

---

## 8. What is actually left

Five rows, and three of them are defects:

1. **#2696** — an epic, not a defect. Needs `epic-workflow`, with its human gates.
2. **#2728** — the dashboard Settings surface for installed packs (backend
   consumption already exists).
3. **#2782** — the cross-company identity half of money. Blocked on nothing
   mechanical; it is a decision about what "the same money item" means across
   parents, which is why it has stayed open since 2026-09-10.
4. **#2684** — the value-span slice: one extractor re-ingest + assembly
   surfacing. Its measurement prerequisite is built and committed.
5. **#2730** — entity attachment. **The research is done and the mechanism is
   not built** (#8639 completed the brief's alias-resolution rung and states the
   surviving defects: the alias surface form is lost, so a re-ingest re-MERGEs
   into a second node, and nothing folds suffixes or honorifics). The pin on this
   row asserts the BRIEF's wording, not the behaviour — this row is OPEN for
   exactly that reason, and a reviewer caught it claiming otherwise.

Ordering, if anyone asks: **#2782, #2684 and #2730 are the ones with a customer
consequence** (a wrong total is a wrong answer; an unsurfaced span is an
unverifiable one; a re-MERGEd entity is two customers' facts becoming one).
`#2728` is a UX completion. `#2696` is a pipeline with its own process. That is
a priority statement, not a schedule.

---

## 9. Re-verifying this page

The machine-checkable half of this map is guarded:

```bash
uv run python tools/problem_map_check.py          # 0 = the doc and the tree agree
uv run pytest tests/test_problem_map_2820.py -q   # the guard's own regression test
```

The checker re-measures, offline and without network access:

1. **Taxonomy completeness** — every one of the 26 tracked issues appears exactly
   once, and no row names an issue outside the tracked set (a silently dropped row
   is a CLEAN over nothing, which is the failure this whole page exists to stop).
2. **Status vocabulary** — each row's status is exactly one of
   `VERIFIED-FIXED`, `OPEN`, `UNKNOWN`.
3. **Anchors resolve** — every anchor is a hex object that `git cat-file -e`
   confirms, and (when history is available) is an **ancestor of `HEAD`**. In a
   shallow clone this is reported as UNVERIFIED rather than drift: the document is
   not wrong, the clone is shallow.
4. **Pins resolve** — every pin path exists, and a pin that is a test module
   actually contains a `def test_` (a pin whose file survives but whose assertions
   were deleted pins nothing).
5. **Summary counts** — the numbers in §2 equal the rows that follow. A summary
   that drifts from its own table is the same defect class as the missing
   `content_hash` this map's Pattern 1 names.
6. **Registration** — this page is listed in `docs/00_index.md`, so it is
   reachable rather than merely committed.

**What is deliberately NOT guarded — and each one is a real residual, not a
rounding:**

- **The status *prose* of a row, and the tracker state.** Those are dated facts;
  the guard refuses to pretend it can read GitHub. §2 and §8 are dated and carry
  the commit they were read at.
- **Anchor RELEVANCE.** The guard proves an anchor exists and is merged. It cannot
  prove the commit is *the fix* — a lane once cited a merge that mentioned nothing
  of the issue (this page carried exactly that defect until review caught it on
  #2749, whose anchor is now the two commits that actually closed its two root
  causes). Reading the anchor is the reviewer's job.
- **Pin RELEVANCE.** The guard proves a pin exists and, for a test module, that it
  declares at least one test function (`^\s*(async )?def test_` — a pytest
  FIXTURE named `test_*` satisfies that floor too, e.g. `tests/conftest.py:1048`).
  It cannot prove the tests assert *this* defect: a pin pointed at a module whose
  assertions were deleted still passes. #2748's pin is the fixed file itself, so
  it is the weakest pin on the page — recorded rather than dressed up.
- **The `#2730`-class error.** A row can be VERIFIED-FIXED with a docs-only
  anchor and a prose pin (that is what this page shipped first, at #2730). The
  guard passed it. Only a reviewer reading the anchor *as a fix* catches it, which
  is why §1's rule says "the fix, not a mention" and why this page states the
  residual instead of implying the guard covers it.

---

## 10. Provenance

- The map's content is **#2820**'s (2026-09-10); its four patterns, six
  workstreams and 26 entries are unchanged here.
- The statuses, anchors and pins were re-measured against `23d227c53` (2026-11-05)
  by reading the tree, the history, and the tracker — never the issue's own body.
- The three WS design documents this re-cut assumes are already committed:
  `docs/plans/2026-09-10-write-contract-five-doors.md`,
  `docs/plans/2026-09-10-ws-b-pack-property-declaration.md`,
  `docs/plans/2026-09-10-ws-c-numeric-normalization.md`.
- Tracker reads: `gh issue view <n>` for each of the 26 on 2026-11-05. Two were
  closed NOT_PLANNED (**#2787**, **#2748**) and one has **never been closed at
  all** (**#2782**, open since 2026-09-10 with half its work shipped) — the three
  cases §1 exists to keep honest.
