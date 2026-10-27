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
| **E2a** the carve-out reaches the prompt | `tests/test_extractor_v2.py::TestMasterList::test_operational_value_carve_out_surfaces`; `tests/test_value_gate_4899.py::test_2453_decision_relevant_identifier_survives` | green | the operational/state value carve-out is rendered into the **real** S1 slot and the **real** S2/S4 master list (verbose *and* core-only), and the mechanical predicate cannot fire on a decision-relevant value | ✔ measured: deleting the carve-out append in the verbose render reds it (`OPERATIONAL-VALUE CARVE-OUT` absent from the rendered master list) |
| **E2b** values *survive* a capture | **none** — and the property does not hold: 1 of 14 value-bearing units survives (bar: 13 of 14) | red | nothing. The loss is **upstream of the filter** (OFF arm 2/14 vs ON 1/14, gate drops 0), i.e. an extraction/render loss, not a selection loss — `#9775` | — (no test can prove a property the pipeline does not have) |
| **E3** every derivation carries its anchor | `tests/test_8501_capture_path_anchor.py::test_capture_anchors_every_extractedfrom_link`; `tests/test_graph_census_7949.py::test_the_session_source_to_event_reference_anchor_is_counted` | green | on a **real `capture_session`**: every `(Point)-[:extractedFrom]->` link records the version it was read from, and `references` anchored == total | ✔ measured: making the anchor never stamp a version reds it with the unanchored rows printed |
| **E3** `:Object` included | `tests/test_graph_census_7949.py::test_an_object_reached_only_by_aboutObject_has_no_derivation_link` | green (as a **reported gap**) | the capture's `:Object` carries **no derivation link at all** (`with_derivation_link == 0`), so E3's literal "every derived node" bar is unreachable **as registered** — an owner metric decision (`#9736`), not a defect a lane may repair | n/a — the pin exists so a change in this fact is noticed, not to green the row |
| **E4** planted operator edges wired + persisted | **none on the unpatched leg.** `tests/eval/write_path/test_write_path_operator_lane.py` monkeypatches `extract_session_v2` and says so in its own docstring | red (5 of 15) | nothing about the product: a patched extractor cannot say whether the extractor *forms* the operators | — (the criterion is not implemented; no proof is possible) |
| **E5** the falsifier is run on OUR corpus | `tests/eval/write_path/test_score_qualifiers.py::test_the_committed_score_artifact_regenerates_byte_identically` | green | a fresh run of the registered scorer over the committed arm receipts reproduces the committed score artefact **byte for byte** | (pre-existing; its own docstring names the mutation class — an edit to `MARKERS`, the 0.6 threshold or `classify()`) |
| **E5** …and **written back into §10** | **ADDED HERE** — `tests/test_7949_e5_section10_writeback.py::test_section10_publishes_the_numbers_the_registered_falsifier_produces` | green | §10's replicated-run table publishes exactly the figures a fresh falsifier run produces — the write-back half of E5's criterion, which nothing in the tree checked | ✔ measured ×3: `52.0%` → `53.0%` in §10 reds; deleting the replicated subsection reds (heading named); moving the scorer's claim threshold `0.6 → 0.9` reds (instrument drift with no write-back) |

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
precision, claims emitted) and the holdout decision-input row (n, both losses, Δloss).
It pins **no prose** and not the run-1 block, whose receipts were never retained
(`#9738`).

---

## What this audit does NOT add, and why

* **No new instrument, probe, gate or fixture.** The tree already carries a
  measurement probe, a refusal probe, a verdict gate and their pins; a further
  counter would be machinery guarding machinery.
* **No edit to `tools/capstone_verdict_check.py`, the shipped log, or any frozen
  number.** A row moves by re-running the probe and deleting the file it replaces —
  never by editing a number. The new test is a *pin on the write-back*, not a
  verdict change.
* **No `docs/00_index.md` row.** This file lives inside the already-registered
  bundle path and its pins are reached through it, so the bundle's existing row is
  the index's unit. (An earlier draft justified this by `_index_row`'s "exactly
  one" rule — that was wrong: `_index_row` (`tests/test_e1_e2_measurement.py:1839`)
  selects rows containing the literal cell `docs/evidence/7949-capstone/E1-E2.md`,
  so a second row naming this file would not collide. The conclusion stands on the
  bundle-unit convention alone.)
* **No E1/E2/E4 proof.** Their mechanisms are missing, not their pins (below).

---

## Why no row can be GREEN — the structural cap, re-verified here

`tools/capstone_verdict_check.py:110` declares `STEPS = ("S0", "S1", "S2.1", "S2.2",
"S2.3", "S3", "S5", "S6")`, and `:498` refuses a GREEN row whose run left any step
out (`missing = [s for s in STEPS if s not in observed]`, message at `:500`).

The only entry point the capstone may drive is `TortoiseSDK.capture_session`. Its
receipt folds the extractor's `stats` and `error_census` and nothing else —
`tortoise/sdk.py:13217` is the fold (`"stats": out.get("stats") or {}`) — while the
extractor builds `result["classify_later"]` (`tortoise/extractor_v2.py:8873`) and
`result["vet"]` (`:8883`) on a surface the receipt never carries. `#9116` is open and
still true at this head.

⇒ **S2.2 and S2.3 are unobservable through the capstone's own entry point**, so any
table `probes/capstone_measure.py` can emit today reports 6 of 8 steps observed and
**no row it contains can be certified GREEN**, whatever the underlying numbers do.
This is a limit of the instrument, not of the verdicts — and it is the reason the
epic's closing rule is unsatisfiable until `#9116` lands, ahead of every row's own
blocker.

---

## Honest closure verdict

**`#7949` is not closeable, and nothing this lane can land changes that.** Ranked by
what would have to move:

| to make this row closable | what has to land first | state at `3fbca18ed` |
|---|---|---|
| **every row** (GREEN is unreachable) | `#9116` — copy `vet` / `classify_later` (at least) onto the capture receipt, so S2.2/S2.3 become observable | **OPEN** |
| E1 | `#5333` — the owner ruling on whether any pass may **remove or relocate** an Object. Without it there is no mechanism to prove: the gate drops 0 of 34 `^the <X>` and `value_gate`'s own docstring says a definite description is *"left to the arbiter, and is reported, not dropped"* | **OPEN** |
| E2 | `#9775` — the carve-out is absent on the compact render path; and the surviving-value loss (1 of 14) is upstream of the filter, so the row needs the extraction loss fixed, not the gate | **OPEN** |
| E3 | `#9736` — the registered denominator counts `:Object` mention links, which carry no anchor **by design** (owner option A, `#5199`). Either the metric is re-registered or the model reopened: an owner decision, not a lane's | **OPEN** |
| E4 | the planted edges are not formed: 5 of 15 wired-and-persisted on the frozen corpus (`#8503` closed at 5/14 on `#9873`; the remaining misses are content-level as well as connect-level), and the receipt's retrievability rate is still unmeasured (`#9596`) | `#8503` CLOSED / `#9596` **OPEN** |
| E5 | the epic's own wording is **met** (falsifier run + written back; both halves now test-pinned). The **pre-registered** outcome floor is not: raw recovery 0.4800 against a `>= 0.80` bar, on an n of 25 that must be reconciled with the record's 29 marker sentences (`#9738`). Reporting that as GREEN is the one thing this bundle exists to refuse | `#9738` **OPEN** |

### One measured correction to the scoreboard's E4 row, not applied to it

The E4 row cites `tests/eval/write_path/receipts/w2b-operator-2026-10-19.json`
(`edge_correct` **2** of 15), whose own `corpus_hash` is `sha256:943a99ec…` — **not**
the frozen pin. Two later receipts landed with `#9873` (`2cba48d3c`) on the **frozen**
corpus (`sha256:dd7e8520…cb035dc`, re-derived from this tree and matched against
`preregistration.json`):

| receipt | date | planted | `edge_correct` | corpus |
|---|---|---|---|---|
| `w2b-operator-2026-10-24-8503-a.json` | 2026-10-24T06:03:47Z | 15 | **5** | frozen pin |
| `w2b-operator-2026-10-24-8503-b.json` | 2026-10-24T06:10:47Z | 15 | **5** | frozen pin |
| `w2b-operator-2026-10-19.json` (cited by the row) | 2026-10-19T01:39:26Z | 15 | 2 | `943a99ec…` |

E4 is therefore **5 of 15**, not 2 of 15, and the fresher figure is measured on the
pre-registered corpus. The verdict does not move — the bar is 15 of 15 — and the
number is **not** edited into the shipped log: a row moves by re-running the probe
and removing the file it replaces. This note is the correction that rule permits.

---

## Reproduce

```bash
# the new proof, and the three mutants behind its red (apply one, run, revert)
uv run pytest tests/test_7949_e5_section10_writeback.py -q -p no:cacheprovider

# the pre-existing proofs cited above
uv run pytest tests/test_e1_e2_measurement.py::test_the_shipped_arm_moves_the_share_by_exactly_zero \
              tests/test_extractor_v2.py::TestMasterList::test_operational_value_carve_out_surfaces \
              tests/test_8501_capture_path_anchor.py \
              tests/test_graph_census_7949.py \
              tests/eval/write_path/test_score_qualifiers.py -q -p no:cacheprovider
```

Measured at `3fbca18ed`: the four pre-existing capstone/measurement files
(`tests/test_7949_capstone_verdict_check.py`, `tests/test_7949_capstone_measure.py`,
`tests/test_e1_e2_measurement.py`, `tests/eval/write_path/test_score_qualifiers.py`)
→ **308 passed** in 575 s; the new file → **1 passed** in 2.4 s.
