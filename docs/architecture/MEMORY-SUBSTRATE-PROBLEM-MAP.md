---
title: "The memory-substrate problem map — re-cut against the tree, 26 entries, 4 patterns"
type: engineering
domain: platform
doc_status: live
created: 2026-11-06
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, memory-substrate, issue 2820, issue 4917
measured_at_commit: f70d884b7f
measured_at: 2026-11-11
---

# The memory-substrate problem map — re-cut

> **What this is.** The status half of issue **#2820** — the child of **#4917**
> (memory quality + cost). #2820 was written on 2026-09-10 as an *organizing*
> artifact: four defect classes, five workstreams, a dependency order, and 26
> linked issues. Its body has not changed since, and the tree has moved a very
> long way underneath it (WS-A/WS-B/WS-C designs landed in
> `docs/plans/2026-09-10-*.md`; 22 of the 26 rows' fixes merged, across ≈45
> separate changes). Reading the issue today therefore tells a lane the opposite of
> the truth about most of its rows — it reads as 26 problems waiting, when it is
> **22 fixed and 4 open**.
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

**Issue closure is not evidence, and this map has four rows where the tracker's
state (or its closure reason) and the row's status disagree:**

| What the tracker said | What the tree said | Row |
|---|---|---|
| CLOSED **NOT_PLANNED** | the symptom no longer reproduces (`ruff check` clean on the file it named) | **#2748** |
| CLOSED **NOT_PLANNED** (consolidated) | ONE of its two indicators landed under the root issue; the other did not | **#2787** |
| CLOSED **COMPLETED** | the mechanism is unbuilt and the pin asserts the brief's wording, not the behaviour — the strongest instance on this page | **#2730** |
| **OPEN**, never closed | both halves shipped — `08a6b0288` (2026-10-18) added the Settings home and the per-graph modal, pinned in the tree — so the TRACKER is the stale half: the issue never closed over a finished job | **#2728** |

So: a closure can lag a fix, and a fix can lag a closure. The anchor+pin pair is
what this map trusts.

The other direction is not a tracker error and is deliberately NOT in that table:
`#2684` is **OPEN** in the tracker and **OPEN** here, and that verdict is right
in both places — what an OPEN does not tell you is how much landed. `#2684`'s
mechanism landed (`#8659`'s stamp, `#8966`'s address, pinned by
`tests/test_extractor_v2.py::TestValueVerbSpan2684`) while the slice its title
names has not run. So an OPEN is a claim that needs its own anchor+pin, exactly
like a fix — which is why the row carries one. `#2782` sat in this sentence until
2026-11-11, when the tracker closed it `COMPLETED` and the row was re-measured
into VERIFIED-FIXED (§6, §10). Tracker state is **not** a column
in the row tables: it is recorded only where it is the point (§1, §10), because
a state column would invite exactly the reading this page exists to stop — that a
closed issue is a fixed defect, or an open one an unfixed one.

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
| VERIFIED-FIXED | 22 |
| OPEN | 4 |
| UNKNOWN | 0 |
| **Total tracked** | **26** |

| #2820 as filed (2026-09-10) | Now (2026-11-11) |
|---|---|
| WS-A write-path integrity — "a decision + three mechanical bugs" | **5 of 5 rows fixed**; the write contract design itself is in `docs/plans/2026-09-10-write-contract-five-doors.md` |
| WS-B ontology substrate — "~2 design decisions + mostly mechanical bugs" | **7 of 8 rows fixed** (#2787 OPEN: the kind-bucket rule is not implemented); the property-declaration design is in `docs/plans/2026-09-10-ws-b-pack-property-declaration.md` |
| WS-C state & value model — "~25 open decisions, owner-gated" | **4 of 6 fixed**; the numeric-normalization design is in `docs/plans/2026-09-10-ws-c-numeric-normalization.md`; **#2730 and #2684 remain open** |
| WS-D venture pack delivery | **3 of 4 fixed** net of #2725/#2726/#2728; **#2696 (epic) remains open** |
| WS-E repo / CI hygiene | **3 of 3 fixed** |

**The dependency order the map proposed is spent.** It read
`E ──► A,B ──► C ──► D`. E's three rows closed on 2026-09-10 (#2749, #2748) and
2026-09-27 (#2734); A is fully closed; B is closed but for #2787's bucket rule; C
is down to two rows (#2730, #2684), **none** of which blocks D (D's
ontology — #2725 — shipped in #3419 on 2026-10-06). What is left of C is **value
fidelity and entity binding**, not lifecycle plumbing, so the chain that made D
wait no longer exists. Do not plan against it.

---

## 3. Pattern 1 — Declared ≠ stored (WS-A: write-path integrity)

*The system reports something as stored, or documents it, that never reaches the
graph — or is lost later. No error is raised.*

| Item | The defect (as filed) | Status | Anchor (merged, in `HEAD`) | Pin (in the current tree) |
|---|---|---|---|---|
| #2813 | v2 product capture silently drops the E3 fields (`quote`/`when`/`search_keys`/`source_turn_id`) while the eval lane passes them, so benchmarks look healthy and the product persists none | VERIFIED-FIXED | `0643bba86` | `tests/test_capture_session.py` |
| #2795 | `rebuild_all` drops live-only props (`amount`, `currency`, `search_keys`, `content_hash`) — the live writer and the replay writer keep different field lists | VERIFIED-FIXED | `37d5ef00c` | `tests/test_projection.py` |
| #2814 | `rebuild_all` wipes **all** nodes including config (`:PackManifest`/`:PackInstall`), so per-graph config cannot survive the recovery path | VERIFIED-FIXED | `b586fab83` | `tests/test_rebuild_config_preservation.py` |
| #2788 | the Event arbitrary-prop lane advertises event-carried state (as filed: "that nothing reads"; the tree's answer, recorded in the anchor, is that `name` has **one** reader — `sdk.backfill_about_entities` — while `eventStatus`/`createdAt` have none) and the commit path forbids the keys | VERIFIED-FIXED | `ed02cfbb2` | `tests/test_event_state_carrier_2788.py` |
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
| #2786 | core kind vocabularies diverge across layers — `CANONICAL_*` (pack) vs the runtime `EVENT_KINDS` / source registry, with no consistency guard | VERIFIED-FIXED | `04ecba158`, `b88a7bf87` | `tests/test_kind_vocabulary_consistency.py` |
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
| #2787 | relation declarations pass shape validation but fail coherence — no IMPL/NAND kind-bucket check, and `extractable` had no runtime consumer | OPEN | `0f2f7601d` | `tests/test_pack_registry_gaps.py` |
| #2818 | no property-declaration surface in pack manifests — `VALID_KINDDEF_KEYS` was a closed 7-key set, so per-kind value fields had nowhere to live | VERIFIED-FIXED | `1c14b1ecb` | `tests/test_pack_value_declarations_2818.py` |
| #2781 | cross-pack references silently drop the referring pack (self-hosted) and fail install (hosted) — `depends_on` was dead code | VERIFIED-FIXED | `9f01fe948` | `tests/test_pack_kinds.py` |

**#2787 is OPEN, and this row is the page's own cautionary tale about itself.**
It was closed **NOT_PLANNED**, consolidated into #2766 as the family root — not
rejected. What #2766 shipped is the **write-path verdict** (`relation_write_verdict`
→ writable / declaration-only / reported, with `RELATION_DECLARATION_ONLY` as a
first-class state) and the runtime handling of `extractable`. What it did **not**
ship is the issue's indicator 1: *declaring `mechanism: IMPL`/`NAND` between two
Object kinds that violate the bucket rule is a validation error naming the rule*.
The relation validator carries the **shape** check only (`mechanism must be IMPL or
NAND`); no kind-bucket coherence check exists — `grep -n 'bucket'
tortoise/pack_registry.py` returns the `storeAs` stream comment, the #2781
reference buckets, and the `LEGACY_KIND_ALIASES` expansion buckets (in
`_build_kind_expansions`), and never a relation arm.

This row shipped as **VERIFIED-FIXED** on the first landing, on an inference
("conservatively … subsumed by the mechanism-inversion table") rather than a
measurement, while the row's own note admitted the check was absent. A fresh
reviewer read it against #2787's indicator list and demoted it. Both halves are
now named, which is the row's whole value: a reader can act on the shipping half
and the missing half separately.

---

## 6. Pattern 4 — Fixed vs flexible (WS-C: state & value model)

*Which semantics are canonical (must be exact) and which are tenant-configurable
was never decided, so both are half-built.*

| Item | The defect (as filed) | Status | Anchor (merged, in `HEAD`) | Pin (in the current tree) |
|---|---|---|---|---|
| #2792 | configurable per-graph pipelines — packs ship default stages, each graph can customise them, stage changes are dated events | VERIFIED-FIXED | `639137d4d` | `tests/test_pipeline_2792.py` |
| #2729 | pack-declared per-kind status folds — the hardcoded Object `status` event→status map generalised | VERIFIED-FIXED | `72e42deea` | `tests/test_status_folds_2729.py` |
| #2782 | money has no representation — `amount`+`currency` on entities, and same-named money items must not merge across portfolio companies | VERIFIED-FIXED | `b408d8588`, `ef405bd3e71`, `ccf5c4036c09` | `tests/test_money_value_2782.py` |
| #2817 | no numeric/locale normalization anywhere — PT-BR/EN amounts cannot be canonicalized (8 failure modes) | VERIFIED-FIXED | `79c13050c`, `29cc564e1` | `tests/test_value_normalizer_2817.py` |
| #2730 | entity attachment & disambiguation — binding facts to the right Subject/Object (name collisions, aliases, placeholders, recurring people) | OPEN | `6a73ea7ea` | `tests/test_entity_attachment_brief_claims.py` |
| #2684 | verbatim value-spans on value-bearing points — a claim carries its exact value **and** the source span, and assembly surfaces it | OPEN | `2312845b6`, `fda19cf93` | `tests/eval/write_path/test_value_span_census_2684.py` |

**#2782 — VERIFIED-FIXED, and the row records which clause moved.** Re-measured
on 2026-11-11, after the issue closed `COMPLETED` (`#11836`, merge
`ccf5c4036c09`; its update-door residual `#11863` closed the same hour): the
*representation* landed earlier — a flat, currency-safe money value on entities
(#8641) and on the Point lane (#8976) — and the **write boundary**, which is what
"money has no representation" was really about, refuses the malformed shapes it
can see at the SDK/MCP props doors and grades an *update* on the state it leaves
(`money_view_after_update`) rather than on the bag. Pinned by
`tests/test_money_value_2782.py`, whose `TestWriteBoundary` is the arm that fails
if the gate is removed.

The row's second clause — two same-named money items under different portfolio
companies must stay distinct — is **not money's**: the closing measurement put it
on the **identity** defect this page already tracks as **#2730** (OPEN, below),
whose brief is a research artifact rather than a mechanism. So the row moves
without claiming money identity shipped: a reader asking "is money representable,
validated at the door, and summable?" gets yes with a pin, and a reader asking
for cross-parent money identity is sent to #2730 by name. The one bound the fix
states about itself — a CREATE door grades the caller's props bag because a
create predicate cannot see the target it will MERGE onto, so a re-create through
the `create_object` ON MATCH arm, or one through `EventAPI.add_point(id=…)`
against an existing point, can still install a lone exponent — is its own OPEN
ticket, **#11868**, which this page does not
track.

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
| #2728 | per-graph pack selection — backend enforcement **plus** the dashboard Settings modal | VERIFIED-FIXED | `08a6b0288` | `website/apps/dashboard/src/packs.test.js`, `website/apps/dashboard/src/packsModalTripwire.test.js`, `tests/test_pack_selection_by_graph.py` |
| #2726 | register `meeting_transcript` + `meeting_minutes` sourceKinds (tier decision per #398) and allow them in pack `sourceTypes` | VERIFIED-FIXED | `24a640f2c` | `tests/test_source_credibility.py` |
| #2749 | `main` is red — `test (b)` fails on a clean `origin/main`, blocking `python-ci-gate` for every PR | VERIFIED-FIXED | `f84daa169c`, `4c4eb7ddd7` | `tests/test_sdk_props_coercion.py`, `tests/test_uri_env_mutations_declared.py` |
| #2748 | `tests/test_delivery_tenancy.py` carries 2 pre-existing ruff I001 import-sort errors on `main` | VERIFIED-FIXED | `cc72a9151` | `tests/test_delivery_tenancy.py` |
| #2734 | MCP tool tests patch the dead `mcp_server.sdk` global — handlers resolve via `_get_team_sdk()` | VERIFIED-FIXED | `6a13a4165` | `tests/test_domain_validators.py` |

**#2696 — OPEN, and it is an epic.** It is not a defect awaiting a fix; it is a
product epic whose first prerequisite (meeting source kinds, #2726) landed on
2026-09-24 and whose remainder needs its own `epic-workflow` run with its own
human gates. Nothing in this map approves it.

**#2728 — VERIFIED-FIXED, and this row was the page's second wrong status until a
reviewer re-measured it.** Both halves the issue titles exist, and so do all four
of its own indicators:

1. **Backend enforcement.** `pack_state.graph_installed_namespaces` returns the
   graph's approval set (`#2714` layer 1) and the compile path narrows to it:
   `extractor_v2._build_master_from_brief(installed_namespaces=…)` gates the
   master list's `pack_kinds`, and `KindClassifier(installed_namespaces=…)`
   (`tortoise/kind_classifier.py`) gates the candidate index by the same set —
   "the same `compile_value_brief`/`compile_vocab` gate, so the classifier can
   never assign a kind the graph does not install".
2. **The dashboard surface, the half this row said was unbuilt.** `08a6b0288`
   (2026-10-18, *"feat(packs): the Configured-packs surface — Settings home +
   per-graph modal (#2728)"*) added the Settings home and the per-graph modal —
   read/toggle/save over the FULL desired set, the honest empty/loading/failed
   states, and the consequence copy — in `website/apps/dashboard/src/packs.js` +
   `main.jsx`, pinned by the cases in `packs.test.js`, the modal tripwire file and
   the server state model in `tests/test_pack_selection_by_graph.py`.
3. **Per-graph, not team-wide.** `test_changes_are_isolated_to_their_graph` and
   `test_cross_graph_isolation_through_the_api` pin it; a disabled namespace keeps
   its kinds writable from data while new mints stop
   (`test_data_union_keeps_a_disabled_namespaces_kinds_writable`).
4. **Self-hosted parity** — the one indicator no single test names, and the reason
   this paragraph is longer than the row. It holds *structurally*: both readers
   behind the view go to the SAME on-disk registry, `domain_loader._get_registry()`,
   read at call time — `pack_state._resolve_catalog()` for the graph's own rows and
   `hosted_api._pack_catalog_rows()` for the add-list — so there is no
   hosted-only catalogue path and a pack dropped in the directory appears in both
   deployments' views. `test_get_offers_catalog_packs_the_graph_does_not_have`
   and the degraded-catalog arms (`test_a_degraded_catalog_is_reported_not_papered_over`,
   `test_an_unavailable_registry_is_not_reported_as_a_readable_catalog`) pin the
   behaviour that matters here: the view reports what the registry actually holds,
   and an unreadable one is reported as unread (`catalog_ok`), never as "nothing
   left to add".

This row said OPEN for eighteen days *after* that merge, on the reasoning that
"the user-facing control is the deliverable" — it is, and it shipped. The lesson
is the page's own: **an OPEN is a claim that has to be re-measured exactly like a
fix**, and nobody had re-read this one against the tree.

---

## 8. What is actually left

Four rows, and three of them are defects:

1. **#2696** — an epic, not a defect. Needs `epic-workflow`, with its human gates.
2. **#2684** — the value-span slice: one extractor re-ingest + assembly
   surfacing. Its measurement prerequisite is built and committed.
3. **#2787** — relation coherence: the write-path verdict shipped, the
   **IMPL/NAND kind-bucket validation** did not (see §5). It is a *validator*
   gap, not a runtime one, so it cannot corrupt data — it can let a pack declare
   an incoherent relation.
4. **#2730** — entity attachment. **The research is done and the mechanism is
   not built** (#8639 completed the brief's alias-resolution rung and states the
   surviving defects: the alias surface form is lost, so a re-ingest re-MERGEs
   into a second node; what HAS closed is the mint of an *unresolved* identity
   (`#11693` HOLDs it instead of minting it) — the surface-form arm above is
   still open — and nothing folds suffixes or honorifics). The pin on this
   row asserts the BRIEF's wording, not the behaviour — this row is OPEN for
   exactly that reason, and a reviewer caught it claiming otherwise.

Ordering, if anyone asks: **#2684 and #2730 are the ones with a customer
consequence** (an unsurfaced span is an unverifiable one; a re-MERGEd entity is
two customers' facts becoming one). The money write gate's own stated create-door
bound is **#11868**, its own ticket, which this page does not track. `#2696` is a
pipeline with its own process. That is a priority statement, not a schedule.

---

## 9. Re-verifying this page

The machine-checkable half of this map is guarded:

```bash
uv run python tools/problem_map_check.py                          # 0 = doc and tree agree
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_problem_map_2820.py -q  # the guard's own test
```

The `TORTOISE_TEST_CARVE_OUT=1` is not decoration: this suite is DB-free, and on
a box with no database the default conftest refuses the run outright
(`default pytest requires TORTOISE_DB_URI (epic #1647 P4)` — one refusal per
collected test, 59 at this head), which reads as the guard being broken rather
than the target being absent. That number is a property of the RUN, and nothing
reads it (see the residuals below): it is re-derived with `--collect-only`
rather than read off the page's `measured_at_commit`.

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
4. **Pins resolve** — every pin path exists, resolves to a **file** (a directory
   satisfies `exists()` and pins nothing), and a pin that is a `tests/*.py`
   module actually contains a `def test_` (a pin whose file survives but whose
   assertions were deleted pins nothing). A pin outside that shape — a JS test
   module, a shipped symbol, a document — is checked for existence, NOT content.
   A pin the checker **reads** — a `tests/*.py` module — that exists as a file
   but cannot be read (`chmod 000`) is reported as NO ANSWER (a state error,
   exit 2), never as drift: the guard has not measured it, so "the map disagrees
   with the tree" would be the wrong diagnosis on the code a reader is told means
   the document is wrong. A pin outside that shape is existence-checked only, so
   an unreadable *file* of that shape is not a state error — nothing would have
   parsed it. But a pin the guard cannot even **stat** (an unreadable parent
   directory) is a state error for **every** shape, because existence itself is
   then unmeasured. A **directory** at a pin path is the third case and stays
   DRIFT (the case above): it pins nothing, and the guard CAN tell — calling that
   unreadable would excuse a row that fails no assertion.
5. **Summary counts** — §2's three `| STATUS | n |` rows, its `Total tracked`
   row, its five per-workstream `**N of M fixed**` cells (each bound to its
   workstream BY LABEL, so a mislabelled row cannot pass), and the page's opening
   abstract (`**N fixed and N open**`, read from the text above `## 1.` — the
   frontmatter and the opening blockquote alike, so a headline placed in either
   IS read; the frontmatter carries `measured_at_commit`/`measured_at` and their
   siblings, not the headline) all equal the rows below. A summary that drifts from its own table is the same defect class as
   the missing `content_hash` this map's Pattern 1 names. The READ SET is
   exactly those counts plus the index row's headline (`check_registration`);
   every other count the page prints is listed in the residuals below.
6. **Registration** — this page is listed in `docs/00_index.md`, so it is
   reachable rather than merely committed — **and that entry's own
   `**N fixed · N open · N unknown**` headline must equal §2's**, read from the
   ONE index row whose LAST cell is this file's path (a mention of the filename
   elsewhere is a cross-reference, not a registration). The index is the half
   most readers meet first, so a stale headline there is the same defect as a
   stale count here. Like a pin, an index that cannot be
   **read** (a directory at that path, an unreadable file) is NO ANSWER (a state
   error, exit 2) rather
   than drift, because registration was never checked.

**What is deliberately NOT guarded — and each one is a real residual, not a
rounding:**

- **The status *prose* of a row, and the tracker state.** Those are dated facts;
  the guard refuses to pretend it can read GitHub. §2 carries the DATE it was read
  and §8 carries neither date nor commit — the commit for the whole page is the
  frontmatter's `measured_at_commit`, restated in §10. Do not read §2 or §8 as
  self-dating.
- **A status token SWAPPED between two rows — CLOSED by the by-issue pin; what
  survives is the pin's own provenance.** Before the head that added the pin arm,
  one row demoted to `OPEN` while another was promoted held §2's tally, the
  abstract, the index row's headline and the WS-C cell — measured: reverting
  `#2782` to `OPEN` **with** `#2684` promoted to `VERIFIED-FIXED` read CLEAN
  under `tools/problem_map_check.py --check` and passed the whole suite before
  review caught it. The re-cut row and §8's four survivors are therefore pinned
  **by issue**
  (`tests/test_problem_map_2820.py::test_the_recut_row_and_survivors_hold_their_status_by_ISSUE`),
  which closes that class: §2's `OPEN` count is `4` and the pin names those four,
  so any swap that holds the tally has to promote one of them and reds the pin.
  (The §8 prose that also names them is NOT compared — see the prose-counts
  residual below — so the guarantee rests on the `OPEN` count plus the pin, not
  on §8's sentence.)
  Those expected values — the four statuses in that arm and the `22`/`4` literals
  the count arm asserts — are a second copy of the same hand measurement, not an
  independent source, so a re-cut that moves the page and the pin together is not
  caught by anything here. The tracker records whether the *ticket* was closed,
  and three of §1's four rows show that says nothing about the row in either
  direction — two are CLOSED in the tracker over a defect this page calls `OPEN`,
  one is `OPEN` over a fixed defect. `tools/problem_map_check.py` deliberately
  refuses to read GitHub and re-derives none of this from the tree, so re-derive
  those literals from the tree on every re-cut; a re-cut that only makes the
  suite green has proven nothing about the statuses. The two halves are not
  equally dark, though: a compensating **swap** IS caught by the pin as a status
  finding (§2's `OPEN` count plus the pin's four by-issue assertions), while a
  **unilateral** move whose counts are all corrected is invisible to the guard —
  `--check` reads CLEAN — and reds only as breakage: the fixture premises that
  name that row's cells, and the arms whose fixtures encode the shipped counts.
  Detected as breakage, never confirmed.
- **Anchor RELEVANCE.** The guard proves an anchor exists and is merged. It cannot
  prove the commit is *the fix* — a lane once cited a merge that mentioned nothing
  of the issue (this page carried exactly that defect until review caught it on
  #2749, whose anchor is now the two commits that actually closed its two root
  causes). Reading the anchor is the reviewer's job.
- **Pin RELEVANCE.** The guard proves a pin exists as a FILE and, for a test module, that it
  declares at least one test function (`^\s*(async )?def test_` — a pytest
  FIXTURE named `test_*` satisfies that floor too, e.g. `tests/conftest.py:1048`).
  It cannot prove the tests assert *this* defect: a pin pointed at a module whose
  assertions were deleted still passes. #2748's pin is the fixed file itself, so
  it is the weakest pin on the page — recorded rather than dressed up.
- **A BROKEN object store can still read as drift.** `git cat-file -e` exits
  **128** both for its real negatives ("no such object", "not a commit") and for a
  store it cannot read (a corrupt loose object: `error: inflate: data stream
  error`, `error: unable to unpack … header`, `fatal: loose object … is
  corrupt` — measured on git 2.43.0). The guard separates the two by
  matching git's own failure wording, and reports the second as UNVERIFIED — but
  that arm is **TEXT matching**, so a git release that rewords those messages
  turns it back into `False`, i.e. into exactly the false drift the tri-state
  exists to stop. Pinned by a real corrupted object in
  `test_a_corrupt_object_store_reads_as_no_answer_not_as_drift`, so a reword is
  caught by that control rather than by a reader.
- **The ENUMERATED and prose counts — §1's census sentence, §2's cell text, §8,
  §10, and §9's own refusal count.** The counts §2 states in its three status
  rows, its total, its five per-workstream cells and the abstract ARE compared
  (plus the index row's headline, in `check_registration`). These are not: §1's
  census sentence; §2's `(#2787 OPEN: …)`, `**#2730 and #2684 remain open**` and
  `**#2696 (epic) remains open**`; §8's "Four rows, and three of them are
  defects"; §10's census prose; and §9's own refusal count above. Deciding
  "which rows does this
  sentence call open" means reading prose — and §8's second clause additionally
  needs to know which row is an epic, the tracker half this guard refuses to
  read — so a guard that parsed those sentences would be a spelling trap rather
  than a check. Measured at this head: drop `#2684` from §2's narrated
  `**#2730 and #2684 remain open**` without moving a row, and `--check` reports
  **no drift** while the page calls an OPEN row fixed in prose and the suite
  reports **59 passed**. Move the ROW instead and it reds, because `#2684` is one
  of the four survivors the pin reads by issue — the pin reads statuses, not the
  prose around them. That is the residual, named rather than dressed up. §9's own
  refusal count is the same class: a property of the RUN rather than of the
  document — it is the suite's collected count, so it moves with every control
  added, and nothing in the checker reads it.
- **The `#2730`-class error.** A row can be VERIFIED-FIXED with a docs-only
  anchor and a prose pin (that is what this page shipped first, at #2730). The
  guard passed it. Only a reviewer reading the anchor *as a fix* catches it, which
  is why §1's rule says "the fix, not a mention" and why this page states the
  residual instead of implying the guard covers it.

---

## 10. Provenance

- The map's content is **#2820**'s (2026-09-10); its four patterns, five
  workstreams and 26 entries are unchanged here.
- The statuses, anchors and pins were re-measured against `23d227c53` (2026-11-06)
  by reading the tree, the history, and the tracker — never the issue's own body.
  A review pass then re-measured the two rows this page had got wrong in the
  *other* direction: `#2728` (marked OPEN although its dashboard surface merged on
  2026-10-18) and `#2795` (whose pin asserted only the `content_hash` half of the
  row's defect — the prop-drop indicators the row names were unpinned, because the
  regressions for them are in `tests/test_projection.py`, not in the A10 suite the
  row pointed at). Every fact those two corrections rest on is present at
  `23d227c53` — `git cat-file -e 23d227c53:website/apps/dashboard/src/packs.js`,
  and `#2795`'s own regression tests in `tests/test_projection.py` — so that pass
  did not move the page's `measured_at_commit`; that is the point of an OPEN being
  a claim with evidence behind it too.
- The three WS design documents this re-cut assumes are already committed:
  `docs/plans/2026-09-10-write-contract-five-doors.md`,
  `docs/plans/2026-09-10-ws-b-pack-property-declaration.md`,
  `docs/plans/2026-09-10-ws-c-numeric-normalization.md`.
- Tracker reads: `gh issue view <n>` for each of the 26 on 2026-11-06. Four are
  not what their state (or its closure reason) suggests, and those are exactly the
  four where that reading and the row's status disagree: two closed NOT_PLANNED
  (**#2787** with one indicator landed, **#2748** fixed in effect — its STATE says
  closed, its *reason* says abandoned, and the file it named is lint-clean), one
  closed COMPLETED with the mechanism unbuilt (**#2730**), and one **OPEN** whose
  fix outran it (**#2728** — both halves landed in `08a6b0288` on 2026-10-18 and
  are pinned in the tree) — the four cases §1 exists to keep honest. `#2684` is
  OPEN in both places and is therefore NOT in that census: its state is right, it
  is just silent about how much landed, which §1's paragraph after the table
  records.
- **Re-measured again on 2026-11-11 at `f70d884b7f`**, because the tracker moved
  after this page was cut: `#2782` closed `COMPLETED` on 2026-11-11
  (`#11836`, merge `ccf5c4036c09`; its update-door residual `#11863` closed the
  same hour) while the row still said OPEN. The row is now VERIFIED-FIXED on §1's
  criterion — anchors `b408d8588`, `ef405bd3e71`, `ccf5c4036c09`; pin
  `tests/test_money_value_2782.py` — and its distinctness clause is recorded as
  **#2730**'s, which is OPEN here on its own evidence. §2's counts, the WS-C cell,
  the abstract and the `docs/00_index.md` headline moved with it (21/5 → 22/4).
  The other four survivors were re-measured in the same pass and **none changed
  status**: `#2787` (`grep -n 'bucket' tortoise/pack_registry.py` still returns
  no relation arm; aside from this map's own re-cut, no commit since 2026-11-06
  mentions it), `#2730` (the tracker says CLOSED **COMPLETED** and this page keeps
  it OPEN because the mechanism is unbuilt — §1's table; the arm §8 records as
  closed is the *unresolved-identity* one, measured at this head: `556e581ceb`'s
  `test_the_graph_node_count_does_not_increase` passes, so an unresolved identity
  is HELD rather than minted, and what stays unbuilt is surface-form retention
  plus the honorific/name-suffix folding (`grep -ci honorific
  tortoise/extractor_v2.py` → 0)),
  and `#2684` / `#2696`
  OPEN in the tracker.

  ⛔ **The guard cannot see this class of drift at all**, and that is why it is
  recorded by hand: an OPEN row carries an anchor and a pin the guard validates,
  but nothing validates *"the issue this row calls open is now closed, and what it
  was really waiting for has been measured onto another row"* — the tracker is
  exactly the source `tools/problem_map_check.py` refuses to read. A CLOSED row's
  fix can therefore ship and leave the page saying OPEN (this one did, for four and
  a half hours of lane time — `#2782` closed at 14:17Z, the row moved at 18:42Z),
  and only a re-measurement pass catches it.
