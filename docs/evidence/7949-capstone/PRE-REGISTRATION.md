---
title: "Pre-registration — extractor v4 capstone: corpus, split, scoring and thresholds for E1–E5 (#9075)"
type: evidence
domain: platform
doc_status: live
created: 2026-10-18
subjects.team: epistemic-team
issue: "#9075"
aboutSubjects: epistemic-team
aboutObjects: "#9075, epic #7949, docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md §1/§7/§10, tests/eval/write_path, tortoise/definite_description_backfill.py, tortoise/value_gate.py, tortoise/extractor_v2.py, tools/capstone_preregistration_check.py"
---

# Pre-registration — extractor v4 capstone (E1–E5)

**What this is.** The epic (`#7949`) states the method constraint verbatim: *"Pre-register before
running — corpus, split, scoring — the discipline that made `#3011` usable."* This file is that
registration for the capstone's five criteria.

**Read §0 first.** The clean sequencing the issue assumed did not survive contact with history: the
E1/E2 measurement landed *before* this file (§0). §0 states exactly what that costs, what it does
not cost, and which part of this registration is still genuinely prior to its run. A pre-registration
that hid that would be worth less than none.

**Why it exists.** Without it, an E1 run is scored against whatever threshold the gate happens to
hit — the exact failure the epic's closing rule exists to prevent. Nothing here may be decided after
the first **scored** evidence log lands (§7, machine-checked).

**Authority.** The thresholds below are **argued in this document against measured in-repo facts**,
not adopted from any comparable or industry default (`EXTRACTOR-V4-ARCHITECTURE.md` §12's
contradiction test). Where a number is bounded by what the shipped detector can do, the bound is
named. Where a number is a judgement, the judgement is stated so it can be attacked.

**Re-measured at `3f2f4893` (2026-10-21).** Every quantity in §1 was re-derived from the tree at
this commit, not carried from the issue body or from an earlier draft. Three claims were
**corrected** in the process — see §1.3, §1.4 and §1.5.

---

## 0. Disclosure — what predates this file, and why it is still a pre-registration

**The fact.** `docs/evidence/7949-capstone/e1-e2.log` was added by `bc547406f` (2026-10-19, `#9110`),
which is **earlier** than this file's add-commit. The issue's premise (*"before any evidence log
exists"*) is therefore false at this commit and cannot be made true by any commit — history is not
editable, and `#9110` was right to land the measurement rather than sit on it.

**What `#9110` did with it, which is why this is survivable.** That log is explicitly **`measured,
not scored`**: its own line 5 records `prereg=UNPREREGISTERED`, every criterion row reads
`verdict=UNPREREGISTERED`, `result=UNPREREGISTERED`, `exit=3`. It asserts **no** pass and **no** fail.
No number in it was ever compared to a bar. `tools/e1_e2_census_check.py` (V9/V10) pins that: an
`UNPREREGISTERED` log is *refused* if it carries a scored criterion verdict or exits non-3.

One flag in that log reads `scored=yes` on the `after-vet-shipped` arm, and it should not be read as
a verdict: `scored=` marks which arm the run *reports as its scored arm* (the "what-if" arm the doc
above describes), not that anything was scored against a threshold. Every `verdict=` and `result=` in
the file is `UNPREREGISTERED` and the file exits 3 — which is what §7's whitelist checks, and why
`scored=` is not in that whitelist.

**⇒ The consequence, stated plainly and not softened: the author of §3.2 had seen the 60.0% before
writing `after_share <= 0.50 x before_share`.** That is a real discount on E1's threshold and a
reader should apply it. What can be offered against it is not innocence but **the derivation**: the
bar is computed from the shipped detector's ceiling (§1.3: 9 of 15 shape-in-scope names positively
classified) and the backfill's measured ≈48% delete precision, **not** from 60.0% — a threshold
fitted to 60.0% would have been *above* it, and 0.50 is below. A reader who does not accept that
argument should treat E1's bar as **unconfirmed**, which is exactly how `#9110` already reports it.

**What is genuinely prior to its run, and is the part this issue can still deliver:**

| criterion | is its measurement visible to the author? | what this registration is |
|---|---|---|
| **E1**, **E1b** | **yes** — `e1-e2.log` | a threshold for the **re-run**, disclosed as post-visible (§0 above) |
| **E2a**, **E2b** | **yes** — `e1-e2.log` | same |
| **E3**, **E4**, **E5** | **no** — no measurement exists anywhere | a genuine pre-registration, prior to its first run |

**What it forbids, mechanically.** The pre-existing log can **never** be scored against these
thresholds: §7 grants the exemption only to a log that declares itself `UNPREREGISTERED`, carries no
scored verdict, and exits 3. Turning `e1-e2.log` into a scored run requires editing the log, which
the exemption refuses. E1/E2 must therefore be **re-run**, and the re-run's log must postdate this
file.

---

## 1. The frozen corpus

### 1.1 Pins

| pin | value | how it is re-derived |
|---|---|---|
| corpus root | `tests/eval/write_path` | — |
| `fixtures_hash` | `sha256:dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc` | `corpus.compute_fixtures_hash()` over every fixture + gold |
| sessions | **7** (`wp01`…`wp07`) | `corpus.session_ids()` |
| seed / schema | `42` / `1` | **read from `_manifest.json`, not re-derived by the gate** — `compute_fixtures_hash()` hashes only the fixture and gold files, so a seed/schema edit is NOT caught by §7. They pin determinism-within-a-run, not the measurement, and are stated here so a re-run is comparable |
| planted units | **90** | gold `planted_units` summed |
| value-bearing units (§3, E2a) | **14** | digit-bearing anchors (16) minus the 2 exclusions named in §3 |
| planted operator edges | **15** (`wp01` 2, `wp02` 1, `wp03` 3, `wp04` 3, `wp05` 2, `wp06` 1, `wp07` 3) | `corpus.planted_operator_count()` |
| hedge/scope/stance sentences (§3, E5) | **29** of 343 raw sentences | the lexeme set in §3, over the fixtures |
| salience labels | **25 rows / 15 in scope / 9 positively classified / 6 deferred / 1 detected class** | `tests/test_salience_labels_4917.py` |

The corpus is a **fixture generator** — hermetic, deterministic seeds, sealed gold, committed
`_manifest.json`. It is pinned **by fixture revision, not by a graph dump**: a graph dump would carry
a store's state, its 10,000-row read cap, and a date, and none of those are reproducible on a
re-run. `fixtures_hash` is the pin, and `tools/capstone_preregistration_check.py` re-derives it
(§7) so a corpus change cannot pass silently.

### 1.2 The before-figure, and the mismatch this section exists to prevent

`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md:85` states **62.3% of Objects are `the <X>`**. Its
method is reconstructable from the tree, and it is a **different denominator** from the one E1 will
use — which is precisely the defect `#9075` exists to prevent:

- **Source:** live graph `org_3326a01ea34ae595d84de5d8f9`, census of **2026-09-25**
  (`tortoise/definite_description_backfill.py` module docstring: *"`7,870` Objects, `27,324`
  reference edges … `the` alone: **4,901**, of which 2,570 are load-bearing"*).
- **Arithmetic:** 4,901 / 7,870 = **62.27%** → the document's 62.3%. Same quantity, same shape
  predicate (`^the`-prefixed name), one audited production graph.
- **What that denominator is NOT:** it is not the corpus. A corpus run writes its *own* Objects,
  with its own total, and a pipeline that extracts fewer real facts also writes fewer Objects — so
  a **production-graph 62.3% compared against a corpus-run share is a numerator/denominator
  mismatch** and is hereby **refused as evidence** for E1.

**⇒ The 62.3% is MOTIVATION only.** The pass/fail comparison is **control-arm vs treatment-arm on
the same frozen corpus** (§3, E1). Any evidence log that compares a corpus share against 62.3% is
scored against the wrong denominator and is void.

### 1.3 Correction 1 — the salience labels are a DETECTION spec, not a drop list (`#9075` body)

The issue body calls `salience_labels.jsonl` *"the denominator the pre-registration must state"* and
is right that it must be stated. It is **wrong if read as a drop list**, and the tree says so:

- All **25** rows are `verdict: not_an_entity` — but `tests/eval/write_path/README.md` records that
  **4 of the 25 names are ruled `keep-object` (must-keep) in the later owner artifact
  `docs/engineering/1026-calibration-set.jsonl` (2026-09-26)**: *"a name can be a non-entity and
  still be a must-keep. Anything reading these rows as discard targets would delete must-keeps."*
- **Re-measured overlap (this commit):** the 4 are `guard 2`, `_TOOL_BY_NAME`,
  `daniel-ospina/agent-infra`, `_capture_cost_props` — all four sit in the `symbol_name` /
  `repo_slug` / `branch_name` classes, i.e. **outside the `definite_description` class entirely**,
  so the two artifacts do not contradict each other on the 15 definite descriptions. They do
  contradict a *delete* rule, which is why this document scores **shape coverage**, never a drop.
- The file is a **fixed prod-audit sample, not a random draw from the rejects** — so 9 of 25 is a
  **coverage** figure and *never* a population precision. Any evidence log reporting "precision"
  against these 25 is misreporting the artefact. The correct reading — 15 in scope by shape
  (`is_bare_definite_description`), **9 positively classified** (`is_positively_bare_definite_description`),
  **6 deferred**, and `definite_description` the only class with a detector — is the **ceiling on
  what a name-based gate can decide**, and it is the bound on E1's threshold in §3.

### 1.4 Correction 2 — the E4 denominator is 15 edges, across all 7 sessions, never 4

`tests/eval/write_path/test_write_path_operator_lane.py` records the reason: *"the operator gold grew
4 → 15 edges (a 4-edge denominator swung 0, 1, 1, 1, 2 of 4 on IDENTICAL code, so it could not
separate a fix from LLM variance)"*. The gold is **15 edges spread across all seven sessions**, and
`wp07`'s cross-session `SUPERSEDE` resolves its target against **`wp06`**'s graph (the lane's capture
order: `wp06` before `wp07`).

**⇒ Two consequences, both binding:**

1. **E4's denominator is 15 and no rate may be reported against 4** (§6 forbids the phrasing; §7's
   check fails on it).
2. **E4 is scored on the full 7-session replay only, never on a session subset.** A held-out-only
   replay cannot resolve `wp07`'s cross-session edge, and a per-session rate would put `wp02` and
   `wp06` on a **one-edge** denominator — the 4-edge defect again, one order of magnitude worse.

### 1.5 Correction 3 — the corpus pin is re-derived at THIS commit, and the earlier draft's was stale

This registration was drafted on a branch cut before `#9110` landed. Re-deriving `fixtures_hash` at
`3f2f4893` gives
`sha256:dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc`, and
`_manifest.json` carries that same value — so the hash is not merely *a* hash, it is the manifest's
own self-description, and the two agreeing is what makes it a pin rather than a copy.

**The earlier draft pinned `sha256:943a99ec…`, which no longer describes the tree.** Landing it
unchecked would have meant every threshold in §3 was argued against a corpus that is not the corpus
being scored — the exact numerator/denominator mismatch §1.2 exists to refuse, one level up. §7's
check re-derives the hash for this reason, and it **fails** on the stale value (that failure is how
this correction was found, not a review catch).

The other pins survived re-derivation unchanged at this commit: 90 planted units, 15 operator edges,
14 value-bearing units, 29 marker sentences of 343, and the 25/15/9/6 salience measurement. Only the
hash had moved.

---

## 2. The split

### 2.1 The two halves

| half | sessions | units | operator edges | value-bearing units | hedge/scope sentences |
|---|---|---|---|---|---|
| **TUNE** | `wp01`, `wp02`, `wp03`, `wp06` | 53 / 90 | 7 / 15 | 5 / 14 | 21 / 29 |
| **HELD-OUT** | `wp04`, `wp05`, `wp07` | 37 / 90 | 8 / 15 | 9 / 14 | 8 / 29 |

The split is chosen, not arbitrary: the held-out half must carry **the majority of the hardest
class** (it holds 9 of the 14 value-bearing units, 64%) while **both halves still exercise every
class** — TUNE keeps 5 value units, 7 operator edges and 21 hedge sentences, so prompt/threshold
iteration is exercised on the same kinds of content it will be judged on. A split that put *all* the
values in the held-out half would make the treatment untuned on values and prove nothing about the
design; a split that put *none* there would flatter it.

Held-out is **not** the larger half by units (37 of 90) and deliberately so: its job is to be
**untuned**, not to be big. It is large enough that no single unit decides a rate, and every failure
in it is reportable by id.

### 2.2 What "held out" means — a READ discipline over ONE replay

**Both halves are always replayed, in the lane's order, in one run** (§1.4: the corpus's operator
gold is cross-session, so the replay cannot be partitioned without voiding E4). The split therefore
governs **which half's results may inform tuning**, not which graph is written:

- **Before the scored run:** iteration may read **TUNE's** per-unit outcomes only. The held-out half's
  per-unit outcomes are **not read** — not aggregated, not inspected.
- **The scored run:** the held-out half is read **once**. A second held-out run must be justified
  *in the evidence log* with the reason the first is not the measurement (e.g. a harness crash), and
  a second held-out run that changes configuration is a **re-cut**, which requires a new pin here
  and a `## Deviations` entry (§8).

**The evidence that the held-out half was not tuned on** is procedural+temporal, and is stated as
such rather than pretended to be cryptographic:

1. this file's **add-commit timestamp strictly precedes every scored evidence log** (§7,
   machine-checked) — the one log that predates it is exempt only because it declares itself
   `UNPREREGISTERED` and carries no scored verdict (§0, §7); and
2. the scored run's receipt carries `resolved_config`, which must be **identical between TUNE and
   HELD-OUT except the session list** — a config delta that appears only for the held-out half is
   the tuning trace, and the reviewer looks for it there; and
3. the held-out read is **first-read** in the evidence log, dated.

### 2.3 The split never applies to E4

Restated because it is the trap: **E4's 15-edge denominator is a property of the full corpus.** E4 is
reported once, on the full 7-session replay. It has no TUNE/HELD-OUT reading and no per-session rate.

---

## 3. Scoring rules

**Arms.** Every criterion is scored on **one corpus, one commit, two arms**, and the arms' complete
environments are recorded in the evidence log:

- **CONTROL (before):** the selection flags unset — `TORTOISE_VET`, `TORTOISE_VALUE_GATE`,
  `TORTOISE_CLASSIFY_LATER` and `TORTOISE_S2_DECISION_SEAM` all absent (their default is OFF,
  `tortoise/extractor_v2.py:88-89`; the fourth is #5087's typed-decision seam, which arms S2.2
  unconditionally because it IS the arbiter).
- **TREATMENT (after):** the selection arm as the capstone run configures it. Whatever seam the epic
  lands before the run, the two arms' environment dicts **may differ only in keys matching
  `TORTOISE_(VET|VALUE_GATE|CLASSIFY_LATER|S2_DECISION_SEAM|SALIENCE.*|DD_.*|SELECT.*)`**; a
  difference outside that
  set means the comparison is not of the selection change and the run is **refused**.

**Counts, not rates, when the denominator is small.** A denominator below its stated floor makes a
criterion **REFUSED — not scorable**, which is neither a pass nor a fail. This is the 4-edge lesson
generalised: a rate on a denominator too small to separate signal from variance is not evidence, and
reporting one is the defect, not the small number.

### 3.1 The criteria table

| id | metric (numerator) | denominator | threshold |
|---|---|---|---|
| **E1** | `:Object` nodes written by the run whose name satisfies `is_bare_definite_description` (the `^the <X>` shape) | **all** `:Object` nodes written by the run, same arm, same commit (floor ≥ 30 Objects) | `after_share <= 0.50 × before_share` |
| **E1b** | anti-gaming control: planted units that **strictly** survive (`grading.strict_survival_counts`) on the deterministic `m2` lane (seed 42, judge pin `w2-write-path-mechanical-v2`) | the **90** planted units | `>= 90 of 90` |
| **E2a** | value-bearing planted units that **strictly** survive, each named by id if lost | the **14** value-bearing units (§3.3) | `>= 13 of 14` |
| **E2b** | surviving planted units whose matched `Point` **also** matches at least one **other** distinct surviving unit (a fused claim) | the planted units that survived (floor ≥ 30) | `<= 0.05` |
| **E3** | derived nodes written by the run (`Point` + `Object` + `Event`) carrying a version anchor | **all** derived nodes written by the run (floor ≥ 30 nodes) | `= 1.000` (zero unanchored) |
| **E4** | planted operator edges **wired AND persisted** with endpoint refs remapped | the **15** planted operator edges of the full 7-session corpus | `= 15 of 15` |
| **E5** | hedge/scope/stance-bearing claims recovered as claims in the **narrative** arm | the same claims recovered in the **raw** arm of the same run, both over the **29** marker-bearing sentences (the ratio's denominator); the raw arm's absolute recovery is reported too | `>= 0.90` ratio `and >= 0.80` raw absolute |

⚠️ The **step set** these rows are measured over is not all-unconditional: §10 records which of §4.1's
eight steps are required and which is conditional on the pipeline having work for it (`#10215`).

### 3.2 E1 — volume falls, and the bar is bounded by the shipped detector

**Why `<= 0.50 × before_share` and not a round number.** The only measured evidence about how much of
the `the <X>` class can be *decided* is in the tree, and it bounds the claim in both directions:

- the shape class is where the volume is: **62.27%** of the production graph's Objects (§1.2);
- but the shipped **positive** predicate decides only **9 of the 15** shape-in-scope labelled names
  (60%), and the backfill lane measured **≈48% precision on an irreversible delete** of the `the`
  class (`tortoise/definite_description_backfill.py:20`: 4,901 rows, 2,570 load-bearing).

**⇒ A promise to remove *most* of the class would be a promise the detector cannot keep.** Half is
the largest reduction that is still inside what a name-based gate can decide (`0.50` of the class is
below the measured 62.3%, and the retained half comfortably carries the load-bearing rows the same
measurement found). The bar is **relative**, so it transfers to the corpus's own base share without
assuming the corpus looks like the production graph — and it is paired with an absolute refusal: if
the **control arm's** share is `< 0.20`, the corpus cannot measure E1 (a share metric needs a
non-trivial base) and the criterion is **REFUSED**.

**Anti-gaming.** E1 alone is gameable by extracting less: writing fewer Objects, or fewer *real*
facts, lowers the share. **E1b is the control that closes it** — the deterministic `m2` lane currently
survives **90 of 90** (`tests/eval/write_path/baselines/m2.json` at this `fixtures_hash`:
`salient_unit_survival_macro`/`_strict` = 1.0), so "must not fall" is a bar with **no noise budget to
hide in**. A treatment that lowers the share while dropping a single planted fact **fails E1**, not
just E1b.

### 3.3 E2 — correctness is preserved

**E2a, the value-bearing class (14 units).** The gold has no `state_value` kind, so the class is
**derived by rule, not by hand**: a planted unit is value-bearing iff its `verbatim_anchor` contains a
digit (**16** units), **minus two exclusions**, each argued against `STATE_VALUE_CARVE_OUT`'s own text
(`tortoise/extractor_v2.py:278`, which keeps *"measurements, deadlines/freezes, thresholds/TTLs,
versions, and counts"* and drops only *"ids, hashes, and ephemeral counters"*):

| excluded unit id | anchor | why it is not a state value |
|---|---|---|
| `wp02_lumen_refactor_u_08` | *"Fail closed with a 401"* | a **status code in a policy rule**, not a measurement the decision turns on |
| `wp04_aurora_perf_u_04` | *"The N+1 embedding calls are what blew up the p95"* | `N+1` is a **query-pattern name**, not a quantity; the quantity in that claim (*p95*) is carried by `wp04_aurora_perf_u_06` |

16 − 2 = **14**. The rule and the exclusions are both re-derived by §7's check, so the denominator
cannot drift and cannot be quietly re-picked.

**Why 13 of 14 and not 14 of 14.** 100% would turn a single legitimate reformatting into a capstone
failure; **two** losses is a filter eating the carve-out. One loss must be **named by id** — a
pooled rate is not accepted for a 14-unit denominator (§3, "counts, not rates"). **Strict** survival
is required (not macro): the carve-out's requirement is the **verbatim** value
(`grading.strict_survival_counts`), and macro would accept a paraphrase leg that drops the number.

**E2b, atomicity — the anti-fusion control.** `EXTRACTOR-V4-ARCHITECTURE.md` §7 makes atomicity a
**requirement, not an optimisation** (*"operators connect only epistemic targets … a fused claim gives
one confidence number to several distinct beliefs"*) and forbids **aggregation**. The degenerate way
to satisfy E1 and E2a at once is to **fuse** many claims into few big `Point`s: volume falls, values
"survive" inside a blob, and every downstream operator inherits a many-claim target. E2b measures
exactly that: a surviving unit is **fused** if the `Point` that carries it also matches another
surviving unit's anchor (`grading.survival_match`, the shipped matcher). Bar: `<= 0.05` of surviving
units, with the denominator floored at 30 so a single fused claim cannot *be* 5% of a 20-unit run.

### 3.4 E3 — provenance is anchored

The epic requires **every derived node** to carry its version anchor — `:Object`/`:Event` included,
not just `Point`s. The corpus lane already computes `provenance_accuracy` over **`Point`s only**; that
is this row's **sub-denominator and first reporter, not its denominator**. E3's denominator is every
derived node the run writes, and the threshold is **exactly 1.000 with zero tolerance**: an
unanchored derivation is a structural defect, not a rate to be traded, so there is no percentage
budget to spend. Floor 30 nodes, so a three-node run cannot certify itself.

### 3.5 E4 — operator edges wired and persisted

**15 of 15**, numerator = edges wired *and* persisted with endpoint refs remapped (a `WIRE` result is
never evidence the behavioural half of `#2552` is fixed — the lane's own caveat). Denominator 15,
full 7-session corpus, one capture order, `wp06` before `wp07` (§1.4). No subset, no per-session rate.

### 3.6 E5 — the premise, measured

**Metric.** The falsifier (`EXTRACTOR-V4-ARCHITECTURE.md` §10, `#7947`): *does the summary lose the
hedging / scope / stance information that a claim's confidence depends on?* Scored as the **ratio of
marker-bearing claims recovered by the narrative arm to those recovered by the raw arm**, on OUR
corpus, one run, both arms.

**Denominator: 29 sentences** of the fixtures' **343** raw sentences carry a hedge/scope/stance
marker under this pinned lexeme set (case-insensitive, word-bounded): `probably`, `maybe`, `might`,
`likely`, `possibly`, `seem(s)`, `appear(s)`, `estimate(s|d)`, `roughly`, `approximately`,
`at least`, `up to`, `no more than`, `unless`, `except`, `only`, `until`, `tentative`, `pending`,
`assuming`, `depending on`, `should`, `must`, `need to`. The set is **declared here, before the run**,
because choosing it after seeing which markers survived is the whole failure mode. 29 is a usable
denominator (the 4-edge disaster was 4); §7's check re-derives it, so a corpus change cannot move it
silently.

**Two thresholds, both required.** `>= 0.90` **ratio** *and* `>= 0.80` **raw absolute**. The ratio alone
is degenerate — two arms that both recover 20% give a ratio of 1.0 and would read as a pass while the
premise is falsified in the other direction. The raw arm is the reference measurement, so it must
clear a floor of its own.

**Per-half reporting.** The split's counts are reported per half, but a half's rate is reported **only
if that half's marker denominator ≥ 10** — TUNE has 21 (rate reportable), HELD-OUT has 8 (counts
only). This is §3's refusal rule applied to E5.

---

## 4. The tie-break — which criterion wins when they disagree

The epic: *"E2 and E1 pull against each other. E5 is the measurement that says which wins — so a green
run that has not run `#7947` is not green."* Made explicit, and it is a **decision table, applied in
this order**:

| E1 (+E1b) | E2 (a+b) | E5 | verdict |
|---|---|---|---|
| pass | pass | any | **GREEN on E1/E2.** E5 still must be run and reported for the capstone to close. |
| pass | fail | `>= 0.90` ratio, `>= 0.80` raw | **E1 wins the trade, conditionally**: only if the E2 loss is **confined to non-marker, non-value units** (E2a holds and E1b holds). A **value-bearing** loss is never traded — E2a failing is a **RED**, whatever E5 says. |
| pass | fail | `< 0.90` ratio or `< 0.80` raw | **E1 is VOID.** The premise is falsified: the narrative loses the information confidence depends on, so a volume win bought through narrative distillation is not a win. **Re-score E1 on a raw-sourced arm**; do not ship the distillation. |
| fail | pass | any | **RED, not destructive.** Nothing fell; there is no volume win to bank and no trade to make. |
| fail | fail | any | **RED.** |
| any | any | **not run** | **NOT GREEN.** The epic's own rule: a green run that has not run `#7947` is not green. |

**E5 outranks E1, never E2a.** The ordering is deliberate: volume is the thing v4 claims, correctness
is the thing it may not spend. E5 is asked *which volume mechanism is legitimate*, not *whether
losing a value is acceptable*.

---

## 5. What this document does NOT decide, and what it forbids

- **It does not adopt a threshold from a comparable.** No industry default, no vendor number, no
  competitor's rate appears in §3 — §12's contradiction test applies to every one of them.
- **It does not authorise a delete.** The salience labels are a **detection** spec (§1.3); 4 of the
  25 are owner-ruled must-keeps, and `DESTRUCTIVE_DISPOSITIONS` is empty by construction. E1 moves a
  **share of what is written**, never a row that already exists.
- **It does not decide the v4 design.** §10's Option 4 stays provisional until E5 reports; this file
  scores it, it does not settle it.
- **It does not authorise a diagnostic run on the held-out half** (§2.2).
- **It does not permit a rate on a refused denominator**: below a floor, the answer is REFUSED.

---

## 6. Phrasings this document forbids

A rate reported against a **four-edge** operator denominator is the defect `#2552` fixed, and it is
the one number in this area that has already produced a false reading on identical code. This
document therefore never writes such a rate, and §7's check fails if one appears anywhere in it —
including in a historical aside. The history is stated in words (§1.4) for exactly this reason.

---

## 7. How this registration is enforced

```bash
uv run python tools/capstone_preregistration_check.py --dir docs/evidence/7949-capstone
```

**Exit codes.** `0` only when every rule below holds; `1` when any rule fails; `2` when the check
cannot read its subject at all (no document, an unparseable or wrong-typed pins/record block) — a
check that cannot read what it audits never reports success. `--no-git` and `--no-corpus` exist for
authoring, **or for a half that runs elsewhere** (CI is the standing case: `--no-corpus` above):
they SKIP a half, and the report says so (`OK (halves NOT checked) — … ordering NOT checked (…)`),
so neither can be mistaken for a verified run. Do not drop `--no-corpus` from the `changes` job to
"tighten" it — that job does not install the package, and the corpus half already runs in `test`. The acceptance command above runs both
halves; **CI runs the ordering half in the `changes` job** (the one job that checks out at
`fetch-depth: 0`) with `--no-corpus`, and the corpus half in the `test` job via
`tests/test_capstone_preregistration_9075.py`, which owns the package dependencies that leg needs.
Neither half is left to a human.

**The ordering half has a third outcome.** CI test jobs check out at `fetch-depth: 1`, and git grafts
a depth-1 clone at its tip, so both sides of an epoch comparison collapse onto one number and
"violated" is indistinguishable from "unprovable". The check says `UNPROVABLE — shallow clone` rather
than inventing a verdict. **The `prereg=UNPREREGISTERED` / verdict / exit whitelist below still runs
in that case**, because it reads the log's own lines and needs no history at all — so the shallow
path weakens the *date* comparison, never the protection against a scored number. The two bounds that
do need history (the log's add-commit, and its last-touch epoch) run in the `changes` job above,
which is why that job keeps full history.

**Scope of the log set.** The ordering half orders each `*.log` **in this directory**; the glob is
non-recursive on purpose, so a log written beside the probes (`probes/*.log`) is not silently ordered
as if it were an entry in this table. Adding a log there is a deliberate act with its own review.

Exit 0 only when **all** of these hold:

1. **The five rows carry all three parts.** Every criteria row (E1–E5) names a metric, a denominator
   and a **numeric** threshold. A row with a placeholder — *"to be decided"*, *"TBD"*, an em dash — or
   with any of the three missing **fails**.
2. **The registration precedes every scored evidence log, and every log it does not precede must
   prove it was never scored.** The commit that **adds** this file is **strictly older** than the
   commit that adds each `*.log` in this directory (`git log --diff-filter=A --format=%ct`). An
   evidence log at the same or an earlier second fails — *"same second" is not "before"*.

   The single documented exception is `e1-e2.log` (§0), and it is bounded on five sides so it
   cannot launder a scored number:

   - it must **predate** this file (a postdating log needs no exemption);
   - its **content** must not have been modified at or after this file's add-commit — the check
     reads the last commit that *touched* the log, not just the one that added it, so an in-place
     edit cannot put a measurement number into a grandfathered log after the thresholds were
     written;
   - it must declare `prereg=UNPREREGISTERED` **on its own line** — the check reads the log, never
     this paragraph;
   - **every** `verdict=`/`result=` line in it must read `UNPREREGISTERED`, and every `exit=` must
     be `3` — a whitelist, not a list of known-bad values, so the next scored vocabulary cannot
     slip through;
   - an exemption naming a log that does not exist **fails**, so the exempt set cannot grow.

   The property enforced is the one the epic wants: **no number is scored against a threshold whose
   author could already see it**. `e1-e2.log` stays unscored forever; the E1/E2 **re-run** must
   postdate this file.
3. **The machine record agrees with the argument.** `preregistration.json` — the path the evidence
   probe already names on its `prereg=` line — must parse, declare `capstone-preregistration/v1`,
   assert `written_before_any_scored_run` **with its basis stated** (no SCORED run exists; the
   `UNPREREGISTERED` log that predates it is named). The unqualified `written_before_any_run` is
   **refused**: it is false whenever any log exists, and a machine consumer keys on the field name
   rather than on prose, and it is **trusted, not derived** — it is only cross-checked against the
   `*.log` scan when the git ordering runs, so `--no-git` leaves it an assertion (the report says so).
   It must also agree with the pins block on
   `fixtures_hash`, on **every criterion's metric, denominator and threshold**, and on the exempt-log
   list; and it must carry `document_sha256`, the digest of THIS file. The digest is what makes a
   re-edited argument visible without git: the ordering half takes the *oldest* add-commit, so a
   delete-and-re-add rewrite keeps the old epoch, and a shallow checkout has no epochs at all. A
   document edit must therefore move the record with it, and the record is the artefact the run
   probe freezes.
4. **E4's denominator is 15.** The E4 row's denominator is `15`, and **no four-edge `N / 4` rate
   appears** (§6). The refusal is of the *rate*: §1.4 states the history as a count (`2 of 4`), which
   is not a fraction anyone can quote as a result.
5. **The pins still describe the corpus.** `fixtures_hash`, sessions, 90 planted units, 15 operator
   edges, 14 value-bearing units, 29 marker sentences, and the 25/15/9/6 salience measurement are
   **re-derived** from the tree and compared with the pins block; the operator-edge total comes from
   `corpus.planted_operator_count()` (§1.1), the corpus package's own single source for that
   denominator; both named value exclusions are re-derived (§3.3) — an exclusion naming an absent id,
   or a unit whose anchor carries no digit (so it excludes nothing), is a stale pin; and the split is
   a true partition of the 7 sessions. A corpus change therefore fails this check instead of silently
   re-basing the thresholds.
6. **The step set is a partition, and it is the one the record declares.** The `steps:` block
   (§10) must split §4.1's eight ids into `unconditional` and `conditional` with **no id missing, none
   in both, none invented**, and `preregistration.json` must restate it byte-identically. Every
   conditional entry must carry a `condition`, the `idle_evidence` a run has to show, and the
   `evidence_issue` that measured it — an entry without them is a waiver with no content. This is the
   rule that stops a step being quietly dropped from the capstone's gate (`#10215`).

The pins block below is the machine-readable copy; the prose in §1–§3 is the argument. Both are
checked, so neither can rot alone.

```yaml prereregistration-pins
corpus:
  root: tests/eval/write_path
  fixtures_hash: "sha256:dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc"
  sessions: 7
  seed: 42
  schema_version: 1
  planted_units: 90
  value_bearing_units: 14
  planted_operator_edges: 15
  hedge_scope_sentences: 29
  hedge_scope_sentences_total: 343
  hedge_scope_lexemes:
    - probably
    - maybe
    - might
    - likely
    - possibly
    - seem
    - appear
    - estimate
    - roughly
    - approximately
    - at least
    - up to
    - no more than
    - unless
    - except
    - only
    - until
    - tentative
    - pending
    - assuming
    - depending on
    - should
    - must
    - need to
  value_excluded_unit_ids:
    - wp02_lumen_refactor_u_08
    - wp04_aurora_perf_u_04
salience_labels:
  rows: 25
  in_scope: 15
  positively_classified: 9
  deferred: 6
  detected_classes:
    - definite_description
split:
  tune: [wp01_quarry_debug, wp02_lumen_refactor, wp03_ember_design, wp06_quarry_rollout]
  held_out: [wp04_aurora_perf, wp05_retro_writeup, wp07_bluepeak_followup]
  tune_counts: {units: 53, operator_edges: 7, value_bearing_units: 5, hedge_scope_sentences: 21}
  held_out_counts: {units: 37, operator_edges: 8, value_bearing_units: 9, hedge_scope_sentences: 8}
criteria: [E1, E1b, E2a, E2b, E3, E4, E5]
required_criteria_rows: [E1, E2, E3, E4, E5]
steps:
  source: "EXTRACTOR-V4-ARCHITECTURE.md §4.1 — the eight top-level steps"
  unconditional: [S0, S1, S2.1, S2.2, S3, S5, S6]
  conditional:
    - step: S2.3
      condition: >-
        the S2 map left at least one candidate's kind missing or `unclassified`, so
        `_collect_classify_items` (tortoise/extractor_v2.py:8666, `def _collect_classify_items`) had an item to hand the classifier;
        when the map types every candidate the pass has no work and neither classify pass runs
      idle_evidence: >-
        the run's own classify surface reads enabled=true with empty s2/union, the receipt's
        error/warning surfaces carry no `classify(S2) failed` / `classify(union) failed` /
        `classify-later init failed` — the fail-open path leaves the SAME empty counters, so the
        absence of a failure marker is what separates 'nothing to do' from 'crashed' — AND the
        receipt's `step_witness` carries a POSITIVE candidate count for the map: an absent or zero
        count is the shape an EMPTY item set leaves when it skips the pass's `try`, so it cannot
        support an idle claim (#10215 review r2 P2)
      evidence_issue: 10215
e4_denominator: 15
denominator_floors:
  e1_objects: 30
  e1_min_before_share: 0.20
  e2b_surviving_units: 30
  e3_derived_nodes: 30
  e5_rate_min_sentences: 10
pre_existing_unscored_logs:
  e1-e2.log:
    added_at: bc547406f
    issue: "#9110"
    verdict: UNPREREGISTERED
    why: >-
      Measured on 2026-10-19, before this pre-registration existed, and landed by #9110 as
      measured-not-scored: the log declares prereg=UNPREREGISTERED, every criterion row carries
      verdict=UNPREREGISTERED, result=UNPREREGISTERED, exit=3. It can never be scored against these
      thresholds; E1/E2 must be re-run, and the re-run must postdate this file.
```

---

## 8. Change control

**Every edit carries a re-cut.** `preregistration.json` pins this file's `document_sha256`, and the
gate checks it in BOTH halves (§7 item 3) — so a commit that touches this file **anywhere**, including
a typo or a disclosure clarification in §0, must re-cut the digest or the check reds. That is the
point (a pre-registration is the artefact that must not be silently editable), but it is easy to trip
over, so the command is here:

```bash
# re-cut after ANY edit to this file, then commit both together
uv run python - <<'EOF'
import hashlib, json, pathlib
doc = pathlib.Path("docs/evidence/7949-capstone/PRE-REGISTRATION.md").read_bytes()
rec_p = pathlib.Path("docs/evidence/7949-capstone/preregistration.json")
rec = json.loads(rec_p.read_text())
rec["document_sha256"] = "sha256:" + hashlib.sha256(doc).hexdigest()
rec_p.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
print(rec["document_sha256"])
EOF
```

**Before the first scored evidence log,** this file may be revised by commit, and a revision that
changes a pin or a threshold must state the reason in its message.

**After the first scored evidence log,** any change to a metric, a denominator or a threshold is a
**protocol deviation**. It is recorded in a `## Deviations` section appended below — with the date,
the original value, the new value, and the reason — and the *original* rows are struck through rather
than edited. A silently edited threshold after an evidence log is the failure this whole artefact
exists to make impossible, and it is visible to review because the file's blame shows an edit after a
log's add-commit.

**`e1-e2.log` is not a "first scored evidence log"** for this purpose: it is `UNPREREGISTERED` and
scores nothing (§0, §7), so this file's first version is still pre-scored. The point at which that
stops being true is visible as a log that does not carry `UNPREREGISTERED`.

## 9. Provenance of every number in this file

| number | re-derived at `3f2f4893` from |
|---|---|
| 62.27% (4,901 / 7,870) | `tortoise/definite_description_backfill.py` docstring; `EXTRACTOR-V4-ARCHITECTURE.md:85` |
| ≈48% delete precision | `tortoise/definite_description_backfill.py:20` |
| 25 / 15 / 9 / 6 / 1 | `tests/eval/write_path/salience_labels.jsonl` + `tests/test_salience_labels_4917.py` predicated run |
| 4 must-keep overlaps | `docs/engineering/1026-calibration-set.jsonl` ∩ `salience_labels.jsonl` |
| 90 units, 15 operator edges, 14 value units | `tests/eval/write_path/gold/*.gold.json` |
| 29 hedge sentences of 343 | `tests/eval/write_path/fixtures/*.json` |
| 90 of 90 m2 baseline | `tests/eval/write_path/baselines/m2.json` at this `fixtures_hash` |
| 53/37, 7/8, 5/9, 21/8 split counts | the per-session sums above, partitioned by §2.1 |
| 9 of 15 shape coverage (60%) | §1.3, `is_positively_bare_definite_description` |
| 7 unconditional / 1 conditional step (§10) | `tortoise/extractor_v2.py:8666-8690` (`_collect_classify_items`, the collector's item set), `:9209` and `:9341` (the two `if items:` gates, one per classify pass) |

## 10. The step set V5 gates over — seven unconditional, one conditional (`#10215`)

**Why this section exists.** `tools/capstone_verdict_check.py`'s V5 and `probes/capstone_run.py`'s G5
both required **all eight** steps of §4.1 in one pass. One of the eight cannot always run, so that
requirement was **false by construction** — no run of any corpus could satisfy it — and a gate that
can never pass is not a gate. This section records which steps are unconditional, which is not, and
the condition; the gate reads the declaration below instead of asserting eight.

### 10.1 The finding, and what it is not

`_collect_classify_items` (`tortoise/extractor_v2.py:8666-8690`) collects only candidates whose kind
is **missing or the `unclassified` sentinel**. Both classify passes are gated on that list being
non-empty — `if items:` at `:9209` (the S2 pass) and `:9341` (the post-S4 pass). So S2.3 has work **iff the S2 map left
something untyped** — and that is a property of the candidate set, not of the pipeline's health. A
corpus whose map assigns a concrete kind to every candidate legitimately idles the pass.

**Measured** (`#10215`, at `a2e80adb2` — PR `#10214`'s head, i.e. with the `#9116` witness surface
present): one hand-written conversation and three pre-registered fixtures (`wp01`, `wp03`, `wp05`) all
reported **7/8**, S2.3 missing, with the classify block reading
`{"enabled": true, "s2": {}, "union": {}}`. `wp05` reported 6/8 for a second, unrelated reason
(S5). That measurement is **not re-taken here** — it needs a provider key and a live lane graph, and it
is an observation about a model output; the **structural** half above is what makes the conclusion
independent of it, and it is re-derivable from the two code sites cited.

**What this is not.** It is not `#9116` (the surface is present and says what it saw), not `#9823`
(the flag-on/no-activity distinction, which this section *uses*), and not a proposal to change S2.3's
gating (`#1695` — deliberate, and out of scope here).

### 10.2 The declaration

| step | kind | why |
|---|---|---|
| `S0`, `S1`, `S2.1`, `S2.2`, `S3`, `S5`, `S6` | **unconditional** | each has an input-independent trigger: the reader segments, the first LLM stage is called, the map emits candidates, the VET gate reports a pass, a query is spent, the merge accounts items, a payload is produced. A run in which one of these does not happen is a failing run. |
| `S2.3` | **conditional** | its trigger is `_collect_classify_items(...) != []`, i.e. a candidate the map could not type. Zero candidates ⇒ the pass has nothing to do, and §4.1's own framing (*"assign each SURVIVING candidate its pack kind"*) is satisfied vacuously. |

The machine-readable form is the `steps:` block in the pins fence above, restated byte-identically in
`preregistration.json`, and `tools/capstone_preregistration_check.py` fails if the two disagree, if the
split is not a strict partition of the eight, or if the conditional entry loses its condition, its idle
evidence or its evidence issue.

### 10.3 The restated requirement — and why it is not a relaxation

**V5 / G5 now require every one of the eight steps to be ACCOUNTED FOR, not all eight to be observed:**

1. every **unconditional** step is observed in the one pass (unchanged);
2. every **conditional** step is either observed, or declared **idle** with its basis, where *idle* is a
   **witnessed** state: the pass's flag was **ON**, its `s2`/`union` counters are **empty**, the
   receipt's own error/warning surfaces carry **no failure marker** for it, and the receipt's
   `step_witness` carries a **positive candidate count** for the map (an absent or zero count is the
   shape an EMPTY item set leaves when it skips the pass's `try`, so it cannot support an idle claim);
3. a step may not leave the gate by being omitted from both lists — required ∪ conditional must be
   exactly the eight (§4.1), and the two must be disjoint;
4. the conditional **set** is read from the record **at the pre-registration commit**, never from the
   run log, so making a step conditional is a reviewed edit to a hash-pinned artefact rather than a
   lane's local choice. A log whose declaration the frozen record does not confirm is **refused**.

**Why (2) is a witness and not a loophole.** The extractor's fail-open `except` (`:9223`, `classify(S2) failed` / `:9353`, `classify(union) failed`) leaves
the *same* empty `s2`/`union` block as an idle pass — `#9823` measured that. Accepting a bare empty
block would therefore hand a **crashed** classify pass the same verdict as a pass with nothing to do,
which is the fail-open shape a gate may not have. The failure is visible: the extractor appends
`classify(S2) failed: …` / `classify(union) failed: …` to `errors` and the raw list to the receipt's
`diagnostics` (`tortoise/sdk.py`'s `_capture_resp_error_split`), plus the `classify_error` census. So
"enabled, no activity, no failure marker" is a positive state and a crash cannot impersonate it.

⚠️ **What this section does NOT do.** It does not move any E-row, does not weaken E1–E5's thresholds,
and does not make the epic closeable: a run still has to produce five measured rows. It removes one
**impossible** requirement from the step gate so that the gate can answer the question it was built
to answer.

## Deviations

A protocol deviation is a change made **after** the first scored evidence log (§8). Each entry
carries the date, the original value, the new value and the reason; the original wording is struck
through rather than edited. This section was missing until `#10215`'s review caught it — which is
itself the entry below.

### D1 — 2026-11-01 — the step set: "all eight observed" became "seven required + S2.3 conditional"

- **Original rule** (§4.1 as V5/G5 read it): *"ALL eight steps observed in one pass"* —
  `tools/capstone_verdict_check.py`'s V5 and `probes/capstone_run.py`'s G5 both required the eight
  step ids in `steps_observed`, with no state other than observed.
- **New rule** (§10 above, `steps` in `preregistration.json`, the `steps:` pins block): seven steps
  are **unconditional** and must be observed; `S2.3` is **conditional on the candidate set** and must
  be either observed or witnessed **idle** with a basis. The conditional set is read from this record
  at the pre-registration commit; a run log may not declare it.
- **Why it is not a relaxation**: `S2.3` structurally cannot run on a corpus whose S2 map types every
  candidate — `_collect_classify_items` (`tortoise/extractor_v2.py:8666-8690`) collects only
  kind-missing/`unclassified` candidates and both passes sit behind `if items:` (`:9209`, `:9341`).
  The old rule therefore could not be satisfied by **any** legitimate run of the registered corpus,
  so the gate could not return a verdict at all (§10.2's four ordered refusals). Every one of the
  eight still has to be accounted for, and an idle claim needs the run's own four facts — flag ON,
  no activity, **no fail-open marker**, and a positive candidate count.
- **Why it is honest to change it now**: the numbers are unaffected. No E1–E5 threshold, denominator
  or evidence row moves; `capstone.log` keeps its `GREEN=0, RED=0, BLOCKED=3, UNPREREGISTERED=2`
  census, and the epic is still not closeable until five measured rows exist.
- **The claim this entry corrects**: `written_before_any_scored_run: true` describes this record's
  ORIGINAL freeze, and it remains true of it — but the `steps` block is **not** original. It was
  authored while `capstone-measured-e0fc940b.log` (a COMPLETED run carrying E3/E4/E5 rows, added on
  `main` by `260903907`, 2026-10-23) already existed. A blanket pre-scored claim cannot cover content
  added after the number was seen, so the record now names the log it postdates in
  `steps_pinned_after_scored_log` and `tools/capstone_preregistration_check.py` **refuses** a `steps`
  block that does not.
