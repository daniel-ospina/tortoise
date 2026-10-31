---
title: "E1–E5 — which criterion has an EXECUTABLE proof (audit, #7949)"
type: evidence
domain: platform
doc_status: final
created: 2026-10-26
subjects.team: epistemic-team
aboutSubjects: Tortoise extractor v4
aboutObjects: "#7949, #9080, #9116, #9736, #9738, #9775, #5333, #8503, #9596, #7947, #5199"
---

# E1–E5 — which criterion is PROVEN by an executable test, and which is not

**Question this file answers.** The scoreboard beside it
(`capstone-measured-e0fc940b.log`, and the `#7949` comments that carry later stamps)
says what was **measured**. This file answers a different, stricter question, which
the epic's closing rule actually needs:

> For each of E1–E5, is there a test that **executes the property end to end** —
> drives the production path, not a helper restating it — passes today, and **reds
> when the property is removed**?

A measurement recorded in prose is evidence. A criterion with no test behind it is a
claim nothing re-checks: it can rot silently, and the epic cannot be re-verified
without re-reading a comment. That distinction is the whole content of this file.

**Audited at `3fbca18ed`** (`origin/main` at the time of the audit). Every "green"
below was run at that head; every mutation named in the last column was **applied,
run, and reverted** (`git status` clean after each), so the discrimination claim is
measured rather than asserted.

---

## The table

`none` in column 2 means no test executes the property — not "the number is
unknown". The blockers are named with the artefact that has to move first.

| item | end-to-end test at `3fbca18ed` | green / red | what it proves | mutation that reds it |
|---|---|---|---|---|
| **E1** volume falls | **none** — the mechanism does not exist. Nearest pin: `tests/test_e1_e2_measurement.py::test_the_shipped_arm_moves_the_share_by_exactly_zero` | green | the shipped S2.2 selection path moves the `^the <X>` share by **exactly 0.0** through the real pipeline. It pins the **absence** of E1's mechanism, which is why E1 is `REFUTED` and not "unmeasured" | ✔ measured: making `value_gate.mechanical_verdict` discard definite descriptions moves the share `0.600000 → 0.333333` and the pin reds |
| **E2** *mechanism* — the carve-out reaches the prompt | `tests/test_extractor_v2.py::TestMasterList::test_operational_value_carve_out_surfaces`; `tests/test_value_gate_4899.py::test_2453_decision_relevant_identifier_survives` | green | the operational/state value carve-out is rendered into the **real** S1 slot and the **real** S2/S4 master list (verbose *and* core-only), and the mechanical predicate cannot fire on a decision-relevant value. This is **not** a registered criterion — `E2a` is the survival count below — it is the mechanism the survival criterion depends on | ✔ measured: deleting the carve-out append in the verbose render reds it (`OPERATIONAL-VALUE CARVE-OUT` absent from the rendered master list) |
| **E2a** values *strictly survive* a capture (bar `>= 13 of 14`) | **none** — and the property does not hold: **1/14** value-bearing units survives (`#9775`) | red | nothing. The loss is **upstream of the filter** (OFF arm 2/14 vs ON 1/14, gate drops 0), i.e. an extraction/render loss, not a selection loss — `#9775`. ⚠️ Two different claims, kept apart: `#9775` **reports** 1/14, while the bundle's README marks the row **BLOCKED** because the frozen **14-unit denominator is carried by no committed artifact** (`README.md:65`) — a reported count is not a certified one | — (no test can prove a property the pipeline does not have) |
| **E2b** the *fusion* control (bar `<= 0.05`) | **none** — no test executes it; the figure exists only as an artifact reading | not RED | the registered anti-gaming control: surviving planted units whose matched `Point` also matches another distinct surviving unit. The artifact figure is **1/114 = 0.008772** (`README.md:65`) — **a proxy, not the criterion**: `README.md:430-435` records that 1/114 is *"printed, and NOT used to green the row, because the artifact counts atomic Points, not surviving planted units"*, so it does **not** certify the registered denominator, and `README.md:70-71` states plainly that neither E1's nor E2's proxy may become the verdict. The row is BLOCKED with `E2a`, and for the same reason: no committed artifact carries the registered denominators | — (a criterion that must stay *low*; what it needs is an artifact on its registered denominator, not a proof) |
| **E3** every derivation carries its anchor | `tests/test_8501_capture_path_anchor.py::test_capture_anchors_every_extractedfrom_link`; `tests/test_graph_census_7949.py::test_the_session_source_to_event_reference_anchor_is_counted` | green | on a **real `capture_session`**: every `(Point)-[:extractedFrom]->` link records the version it was read from, and `references` anchored == total | ✔ measured: making the anchor never stamp a version reds it with the unanchored rows printed |
| **E3** `:Object` included | `tests/test_graph_census_7949.py::test_an_object_reached_only_by_aboutObject_has_no_derivation_link` | green (as a **reported gap**) | the capture's `:Object` carries **no derivation link at all** (`with_derivation_link == 0`), so E3's literal "every derived node" bar is unreachable **as registered** — an owner metric decision (`#9736`), not a defect a lane may repair | n/a — the pin exists so a change in this fact is noticed, not to green the row |
| **E4** planted operator edges wired + persisted | **none on the unpatched leg.** `tests/eval/write_path/test_write_path_operator_lane.py` monkeypatches `extract_session_v2` and says so in its own docstring | red (5 of 15) | nothing about the product: a patched extractor cannot say whether the extractor *forms* the operators | — (the criterion is not implemented; no proof is possible) |
| **E5** the falsifier is run on OUR corpus | `tests/eval/write_path/test_score_qualifiers.py::test_the_committed_score_artifact_regenerates_byte_identically` | green | a fresh run of the registered scorer over the committed arm receipts reproduces the committed score artefact **byte for byte** | (pre-existing; its own docstring names the mutation class — an edit to `MARKERS`, the 0.6 threshold or `classify()`) |
| **E5** …and **written back into §10** | **ADDED HERE** — `tests/test_7949_e5_section10_writeback.py::test_section10_publishes_the_numbers_the_registered_falsifier_produces` | green | §10's replicated-run table publishes exactly the figures a fresh falsifier run produces — the write-back half of E5's criterion, which nothing in the tree checked | ✔ measured ×8: `52.0%` → `53.0%` in §10 reds; deleting the replicated subsection reds (heading named); moving the scorer's claim threshold `0.6 → 0.9` reds (instrument drift with no write-back); swapping the arms table's `recall` / `precision` HEADER labels reds (the cells are read by position, so without this the table could publish the wrong figure green); swapping the SPLIT table's `raw loss` / `narrative loss` header labels reds (before the fix it left the decision input published inverted, GREEN); `holdout \| bound` `Δ +16.7 pp` → `+99.9 pp` reds (the figure §10 calls *past branch 1's trigger*); a `tuning` split row's rate or loss count moved reds; `Δloss +10.0 pp` → `+11.0 pp` reds while `+10 pp`, `+10.00 pp`, `+16.67 pp` and `0.0 pp` stay green (the Δ cells are parsed on the document's printed precision, not text-compared) |

### Why the E5 write-back was the one hole that could be closed now

E5 is the only criterion whose **property already holds** and whose **proof was
missing**. Its two halves were done by hand on 2026-10-18/19: the falsifier ran, and
§10 was edited to carry the result. The run half was pinned by a test. The
**write-back** half had no test at all — §10's table could be retyped, edited by one
digit, or deleted, and every gate in the repo stayed green while `#7949` and `#7947`
went on citing numbers the design document no longer recorded. That is exactly the
failure the epic's closing rule exists to catch, and it was unpinned.

One test, one behaviour. It re-runs the registered scorer (stdlib only, no DB, no
network, no model, ~2.4 s) and compares §10's published cells against what that run
produced: the two pooled arm rows (loss/absent/flattened, distortion, recall,
precision, claims emitted), both table HEADERS (the cells are compared by position,
so a relabelled column would otherwise publish the wrong figure green), all six rows
of the split table (n, both loss counts, the rates the document prints there, and Δ
— `holdout` /
`tuning` × `all qualifiers` / `bound` / `unbound`), and the holdout decision-input
row's Δprecision/Δrecall legs, which the registered rule also reads. It pins **no
prose**, not the split table's `other` column, and not the run-1 block, whose
receipts were never retained (`#9738`).

⚠️ **The Δprecision/Δrecall legs were unpinned in the first draft of this pin**, and
the gap is worth recording because it is this document's own failure mode: the pin
compared the holdout row up to Δloss and stopped, so `Δprecision +0.8` could be
retyped `+8.0` and stay **GREEN** — publishing a branch-1 FIRING the run does not
have, in the one cell §10's prose immediately below reads (*"the holdout's (+0.8) is
inside it"*, sc. the registered ±5 tolerance; `+8.0` is outside it). The registered
rule consumes all three legs (`tests/eval/write_path/score_qualifiers.py:760-761`
`evaluate_branches(hold["delta_loss_pp"], hold["delta_precision_pp"],
hold["delta_recall_pp"], …)`), so an unpinned leg was an unpinned verdict. Found by
the `#10108` review, measured both ways (`+8.0` and `Δrecall +9.0` now red); the cell
is pinned by `_DELTA_CELL`.

⚠️ **Three more gaps of the same shape were found by the two review rounds that
followed**, and each was measured before being closed — recorded because every one is
a *pin that stops one step short*, which is exactly what this file exists to name:

1. **The arms table's column HEADER was unpinned.** Every arm cell is compared by
   POSITION (`got[2]` is recall, `got[3]` is precision), so a header edit that swaps
   two labels publishes the wrong figure under the right number and stays GREEN —
   measured: swapping `recall` / `precision`, deleting the header row, and renaming
   `qualifier loss` → `distortion` all left the pin GREEN. Now pinned by
   `_ARM_HEADER` / `_header_cells`; measured red, naming the header it read.
2. **The holdout Δloss was a TEXT compare**, contradicting this pin's own doctrine one
   cell over: `got_holdout[4] == f"+{…:.1f} pp"` reds a re-publication that is
   numerically CORRECT in the document's own format (§10's `unbound` row already
   renders `0 pp`, not `0.0 pp`), and renders a negative Δ as `+-12.3 pp`, which can
   never match. Now parsed by `_pp`; measured: `+10 pp` and `+10.00 pp` are GREEN,
   `+11.0 pp` reds.
3. **The SPLIT table — the decision input's own table — was read only on its
   `all qualifiers` row, and its HEADER was unpinned.** Measured GREEN before the
   fix: swapping `raw loss` / `narrative loss` in the header (after which §10
   publishes the holdout decision input INVERTED — narrative `6 (60%)` vs raw
   `7 (70%)`, a Δ of −10 pp — next to its own unchanged `**+10.0 pp**` cell); and
   `holdout | bound`'s `**+16.7 pp**` → `**+99.9 pp**`, which is the one figure §10
   names as *"past branch 1's trigger"*, i.e. the single place the registered
   falsifier's trigger is crossed. Now pinned wholesale by `_SPLIT_HEADER` /
   `_split_table` / `_assert_split_table` — all six rows, every numeric cell — with
   a row-length check so a cell added or dropped cannot silently shift the columns.
   Measured after: each of those reds, while a correct re-rendering (`+16.67 pp` for
   `+16.7 pp`, `0.0 pp` for `0 pp`) stays GREEN. ⚠️ The FIRST version of that fix
   was weaker than its own claim, and the measurement that showed it is kept here
   because it is the same failure mode one level up: the rows were keyed in a dict,
   so a **duplicated** row collapsed onto its key and a corrupted row hid behind a
   correct twin (measured GREEN — and the same content graded RED with the rows in
   the other order, i.e. the verdict depended on merge order); a printed rate could
   be **deleted** and stay GREEN; and a second `| split |` table placed above the
   real one would be the only one read. All three are now pinned — physical row
   count, a unique-table assertion, and `_RATE_ROWS` for the four rows that print a
   rate today — and each is measured red, while a row written `||` (a valid empty
   final cell) and a rate ADDED to a row that prints none both stay GREEN.

---

## What this audit does NOT add, and why

* **No new instrument, probe, gate or fixture.** The tree already carries a
  measurement probe, a refusal probe, a verdict gate and their pins; a further
  counter would be machinery guarding machinery.
* **No edit to `tools/capstone_verdict_check.py`, the shipped log, or any frozen
  number.** A row moves by re-running the probe and deleting the file it replaces —
  never by editing a number. The new test is a *pin on the write-back*, not a
  verdict change.
* **One `docs/00_index.md` row — added in this PR, not "none".** The bundle carries
  one row PER ARTEFACT (the `#7949` / `#9080` gate/probes/logs row, the
  PRE-REGISTRATION row, and the E1+E2 row), cited **by title, never by line**: a line
  number ROTS. ⚠️ Corrected by the `#10108` review: the first draft of this bullet
  said those numbers *rotted* (`:140`/`:146`/`:147`/`:148` → `:141`/`:148`/`:149`/`:150`).
  Measured with `git show <ref>:docs/00_index.md` over every commit that touched the
  bundle's rows, they read `:141`/`:148`/`:149` at the pre-PR head `59cd1101` and
  `:141`/`:148`/`:149`/`:150` at the commit that wrote that sentence (`b65c79080`) —
  i.e. those four numbers were **wrong when written, not stale** at `b65c79080`. The
  rot claim is the same kind of unmeasured assertion this audit exists to catch, so it
  is recorded rather than deleted. ⚠️ Then it happened to this bullet's OWN numbers,
  which is why they are gone: at `b2027e46b` the draft's `:140` / `:146` / `:147` were
  the Objective-7 exit row, the hosted-prerequisites row and the read-path row, and
  the merge of `origin/main` in `75d9ef846` shifted every one of them (`+1` / `+2` /
  `+2`). A reader is therefore given **titles, never line numbers**: the three rows
  those numbers now land on belong to other epics (the `#7846` graph-key `RESTORE`
  row, the `#10183` AX-principles row and the `#7950` capstone-evidence row), so three
  of the four would have routed a reader into someone else's epic. And
  the README does not name this file — so "reachable through the bundle's rows" was
  false and a reader routing by the documented entry point could not reach it. (An
  earlier draft justified adding nothing by `_index_row`'s "exactly one" rule; that was
  wrong — `_index_row` (`tests/test_e1_e2_measurement.py:1839`) selects rows
  containing the literal cell `docs/evidence/7949-capstone/E1-E2.md`, so a second row
  cannot collide. Both the premise and the conclusion were wrong, caught by the
  `#10108` review.)
* **No E1/E2/E4 proof.** Their mechanisms are missing, not their pins (below).

---

## Why no row can be GREEN — the structural cap, re-verified here

`tools/capstone_verdict_check.py:110` declares `STEPS = ("S0", "S1", "S2.1", "S2.2",
"S2.3", "S3", "S5", "S6")`, and `:498` refuses a GREEN row whose run left any step
out (`missing = [s for s in STEPS if s not in observed]`, message at `:500`).

⚠️ **Those three cites are re-checked BY HAND at the audit head, not by the bundle
sweep.** `tests/test_7949_capstone_measure.py`'s `_BUNDLE_CITE` matches only the three
sources this bundle prints numbers for (`sdk.py`, `edges.py`, `extractor_v2.py`), so a
`tools/*.py` cite and the bare `:N` continuation below are invisible to it — stated
here so "swept" is never read into a number nothing re-checks.

The only entry point the capstone may drive is `TortoiseSDK.capture_session`. Its
receipt folds the extractor's `stats` and `error_census` and nothing else —
`tortoise/sdk.py:14725` is the meta fold — while the extractor builds
`result["classify_later"]` (`tortoise/extractor_v2.py:9129`) and `result["vet"]`
(`tortoise/extractor_v2.py:9139`) on the extractor's OWN return.

⛔ **`#9116`'s seam has since LANDED, and this paragraph is the pre-seam state
it was written in.** The fold no longer forwards `stats` alone — it calls
`_capture_stats_with_step_witness`, which copies each stage's own counts onto
`stats["step_witness"]` (a sub-key of the `stats` key the receipt already
carried, so no new receipt key and no SDK/MCP verb). S2.2 and S2.3 are
therefore REACHABLE through the capstone's own entry point at this head; what is
still true is that they were **absent on the `e0fc940b` run**, which is what the
scored log and the 6-of-8 row set record.

⇒ **A table measured BEFORE the seam reports 6 of 8 steps observed** and no row
the already-scored log contains can be certified GREEN, whatever the underlying
numbers do — that is a limit of the measurement's stamp, not of the verdicts.
The seam does not by itself move a row: a row moves only by RE-RUNNING
`probes/capstone_measure.py` at a post-seam head, which is what makes those two
rows **witnessable** — S2.2 moves on such a re-run, while S2.3 moves only once its
classify pass carries a non-empty `s2`/`union` (on the measured corpus it is IDLE,
so a re-run still reports it unobserved). Until such a re-run is committed, the epic's closing rule
stays unsatisfiable on the committed evidence, ahead of every row's own blocker.

---

## Honest closure verdict

**`#7949` is not closeable, and nothing this lane can land changes that.** Ranked by
what would have to move:

| to make this row closable | what has to land first | state (the audit is at `3fbca18ed`; every row re-read at `6986dcd4d`, 2026-10-27; the `#9116` row re-read at this PR's head) |
|---|---|---|
| **every row** (GREEN is unreachable) | `#9116`'s seam has **LANDED** (this PR): `stats["step_witness"]` carries `vet` / `classify_later` (and the other six) on the capture receipt, so S2.2/S2.3 are now observable through the capstone's own entry point. What still has to land is a **committed post-seam re-run** — and on the measured corpus that re-run still reports S2.3 unobserved (its classify pass is IDLE), so it refuses at G5 | `#9116` **LANDED** (this PR); what it leaves open is V5's 8/8 scoping — the owner's explicit decision, on that issue |
| E1 | `#5333` — **its design half is no longer pending; it was DECIDED on 2026-10-26** (`EXTRACTOR-V4-ARCHITECTURE.md:869`, commit `6986dcd4d`, PR `#10069`): a pass may **add**, may execute an authorised `MERGE`, and after COMMIT may not remove a committed row except through **the journaled `restatus` fold where no visible successor exists, or the D7 `ObjectSuperseded` arm where one does** (`ONLY` is scoped to the successorless case and never denies the D7 arm); a physical delete is **foreclosed to every pass**; the journaled `restatus` fold — the only permitted form of removal-from-recall where no visible successor exists — is **deferred until recall cost is measured** and writes nothing until a relocation pass is authorised, while the D7 `ObjectSuperseded` arm is **permitted now** where a visible successor exists (that is pre-existing supersession, not the deferred relocation). The same paragraph records the measurement that settles this row's shape: **"E1's share is moved by no non-destructive mechanism"** (the shipped gate drops 0 of 34 `^the <X>`; `value_gate`'s own docstring says a definite description is *"left to the arbiter, and is reported, not dropped"*). So the ruling did not hand E1 a mechanism — it foreclosed the one that would move the number. What stays open in `#5333` is **task 1, the owner's authorisation to ever RUN a relocation**, and even that buys only a deferred fold, never a delete | `#5333` **OPEN** (design half **decided**) |
| E2 | `#10360` — the **render half is fixed** (PR `#10382` on `#9775`: every render path carries the carve-out, the compact one included, pinned by `tests/test_v4_declared_mechanisms.py::test_every_render_path_carries_the_carve_out_the_guarantee_requires` and `::test_the_dispatcher_prompts_carry_the_carve_out_once_and_compactly`), so what remains is the OTHER half: nothing in the write path *enforces* retention (`#10360`), and the surviving-value loss (1 of 14) is upstream of the filter, so the row needs the extraction loss fixed, not the gate | `#9775` render half **fixed** (PR `#10382`) / `#10360` **OPEN** |
| E3 | `#9736` — the registered denominator counts `:Object` mention links, which carry no anchor **by design** (owner option A, `#5199`). Either the metric is re-registered or the model reopened: an owner decision, not a lane's | **OPEN** |
| E4 | the planted edges are not formed: 5 of 15 wired-and-persisted on the frozen corpus (`#8503`'s own criterion reports 5/14 with `wp02` excluded as the pre-registered exclusion, `#9873`; the frozen denominator is **15** and every receipt reads `planted=15` — the remaining misses are content-level as well as connect-level), and the receipt's retrievability rate is still unmeasured (`#9596`) | `#8503` CLOSED / `#9596` **OPEN** |
| E5 | the epic's own wording is **met** (falsifier run + written back; both halves now test-pinned). The **pre-registered** outcome floor is not: raw recovery 0.4800 against a `>= 0.80` bar, on an n of 25 that must be reconciled with the record's 29 marker sentences (`#9738`). Reporting that as GREEN is the one thing this bundle exists to refuse | `#9738` **OPEN** |

### One measured correction to the scoreboard's E4 row, not applied to it

The E4 row cites `tests/eval/write_path/receipts/w2b-operator-2026-10-19.json`
(`edge_correct` **2** of 15), whose own `corpus_hash` is `sha256:943a99ec…` — **not**
the frozen pin. **Three** later receipts landed with `#9873` (`2cba48d3c`) on the **frozen**
corpus (`sha256:dd7e8520…cb035dc`, re-derived from this tree and matched against
`preregistration.json`), and all three agree:

| receipt | date | planted | `edge_correct` | corpus |
|---|---|---|---|---|
| `w2b-operator-2026-10-24-8503-preleg.json` | 2026-10-24T05:46:51Z | 15 | **5** | frozen pin |
| `w2b-operator-2026-10-24-8503-a.json` | 2026-10-24T06:03:47Z | 15 | **5** | frozen pin |
| `w2b-operator-2026-10-24-8503-b.json` | 2026-10-24T06:10:47Z | 15 | **5** | frozen pin |
| `w2b-operator-2026-10-19.json` (cited by the row) | 2026-10-19T01:39:26Z | 15 | 2 | `943a99ec…` |

The figure does not depend on which frozen receipt is read.

E4 is therefore **5 of 15**, not 2 of 15, and the fresher figure is measured on the
pre-registered corpus. The verdict does not move — the bar is 15 of 15 — and the
number is **not** edited into the shipped log: a row moves by re-running the probe
and removing the file it replaces. This note is the correction that rule permits.

---

## Reproduce

```bash
# the new proof, and the three mutants behind its red (apply one, run, revert).
# ⚠️ The carve-out is REQUIRED: `tests/_embedded.py` refuses a URI-less run by
# default (epic #1647 P4), so the bare command errors before this test is even set
# up. The pin itself is hermetic (no DB, no network, no model) — the carve-out is
# the suite gate, not a dependency of this test.
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_7949_e5_section10_writeback.py -q -p no:cacheprovider

# the pre-existing proofs cited above
TORTOISE_TEST_CARVE_OUT=1 \
uv run pytest tests/test_e1_e2_measurement.py::test_the_shipped_arm_moves_the_share_by_exactly_zero \
              tests/test_extractor_v2.py::TestMasterList::test_operational_value_carve_out_surfaces \
              tests/test_8501_capture_path_anchor.py \
              tests/test_graph_census_7949.py \
              tests/eval/write_path/test_score_qualifiers.py -q -p no:cacheprovider
```

Measured on two heads, because the new pin does not exist on the first: at
`3fbca18ed` (the pre-PR baseline) the four pre-existing capstone/measurement files
(`tests/test_7949_capstone_verdict_check.py`, `tests/test_7949_capstone_measure.py`,
`tests/test_e1_e2_measurement.py`, `tests/eval/write_path/test_score_qualifiers.py`)
→ **308 passed** in 575 s; the new file, run in this PR's worktree at a head that
carries it → **1 passed** in 2.4 s (the harness calls the test body directly, since
`pytest` is not installed in that worktree — `git cat-file -e
3fbca18ed:tests/test_7949_e5_section10_writeback.py` fails, the file is *added* by
this PR, so the pin's own run cannot be attributed to that head).
