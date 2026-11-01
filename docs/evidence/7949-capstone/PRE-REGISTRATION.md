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
| ~~**E3**, **E4**, **E5**~~ | ~~**no** — no measurement exists anywhere~~ | ~~a genuine pre-registration, prior to its first run~~ |
| **E3** | **no at registration** — no measurement existed when this record was written (§10.1, §10.3: a POST-registration run measures it) | a genuine pre-registration, prior to its first run; E3's class set re-registered to follow #5199 (§10.1) |
| **E4** | **no measurement was *visible* at registration** — ⛔ not "no measurement existed": the operator audit **ran** `2026-10-19T01:39:26Z`, two days BEFORE this record, and its **artefact** landed after it (`d50217273`, 2026-10-22), so nothing it measured was in the tree this row's author wrote (§10.3, §10.4) | a genuine pre-registration, prior to its first run — and the visibility claim states WHICH of the two facts makes it `false` |
| **E5** | **yes** — `#7947`: `EXTRACTOR-V4-ARCHITECTURE.md` §10, committed `ecc7bc8ca` (2026-10-19) | a threshold for the **re-run**, disclosed as post-visible (§10.2): the measurement predates this record and sits BELOW the bar |

**⇒ §0's table was wrong about E5, and had been since this file was written.** The struck row above
is the original, preserved rather than edited; it claimed no measurement existed anywhere for E5,
while `#7947`'s measurement had landed two days *before* this registration. §10.2 records the
correction, the sentence that was false, the figures, and the ordering — the correction is an
amendment appended here, never a quiet rewrite. An artefact whose whole value is that it hides
nothing cannot be the one artefact that hides a prior measurement.

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
| **E3** | derived nodes written by the run (`Point` + `Event` + `Document`) carrying a version anchor | **all** derived nodes written by the run — `Point` + `Event` + `Document` (the classes the model anchors: `Point` on the node, `Event`/`Document` on their derivation link; floor ≥ 30 nodes) | `= 1.000` (zero unanchored) |
| **E4** | planted operator edges **wired AND persisted** with endpoint refs remapped | the **15** planted operator edges of the full 7-session corpus | `= 15 of 15` |
| **E5** | hedge/scope/stance-bearing claims recovered as claims in the **narrative** arm | the same claims recovered in the **raw** arm of the same run, both over the **29** marker-bearing sentences (the ratio's denominator); the raw arm's absolute recovery is reported too | `>= 0.90` ratio `and >= 0.80` raw absolute |

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

The epic requires **every derived node** to carry its version anchor — ~~`:Object`/`:Event` included,
not just `Point`s~~ **(amended, §10.1: `:Event`/`:Document` included, `:Object` NOT — the anchor sits
on the derivation link, and `:Object` reaches its source through a **mention** link that carries none
by design)**. The corpus lane already computes `provenance_accuracy` over **`Point`s only**; that
is this row's **sub-denominator and first reporter, not its denominator**. E3's denominator is every
derived node the run writes, and the threshold is **exactly 1.000 with zero tolerance**: an
unanchored derivation is a structural defect, not a rate to be traded, so there is no percentage
budget to spend. Floor 30 nodes, so a three-node run cannot certify itself.

**Mentions are out of scope, by decision, not by convenience (§10.1, #9736).** `#5199`'s owner-approved
option A (2026-09-25) put the version anchor on the **derivation** link, and the writer's own constant
names those classes: `_DERIVATION_REFERENCES_LABELS = frozenset({"Event", "Document"})`
(`tortoise/projection/edges.py:747`) — `:Object` links are identity/mention links and are pinned
property-free by `tests/test_source_version_references_5199.py::test_identity_link_stays_property_free`
(`tortoise/projection/edges.py:721-723`, `docs/architecture/STORAGE-ARCHITECTURE.md:763`). Counting `:Object` mentions in
E3's denominator therefore made `= 1.000` **unreachable as registered**: every mention link is
unanchored by rule, so a run would have reported the decision as a defect. `:Point` carries the anchor
on the node itself (`#5038`), which is why the derivation set is those two link labels **plus**
`Point`. §7 item 6 re-derives that set from the constant on every run, so this row cannot drift from
the model it follows.

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
- **It does not decide the v4 design.** `EXTRACTOR-V4-ARCHITECTURE.md` §10's Option 4 stays
  provisional until E5 reports; this file scores it, it does not settle it.
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

6. **No criterion claims to be unmeasured while a measurement for it is cited.** The record's
   `measurement_visible_to_author` field is a claim about **history**, and a history claim is the one
   kind a registry can get wrong without any number changing — which is exactly what happened to E5
   (§10.2): the field read `false` for a metric whose measurement had landed two days before this
   file. So the record must carry `prior_measurements` (criterion id → `{artifact, commit, date}`),
   and the two are held to each other in **both** directions: an id in `prior_measurements` whose
   criterion says `false` **fails** (the falsified claim), and a criterion saying `true` with no
   entry **fails** too (a visibility claim no artefact backs is uncheckable, not harmless). The
   pins block restates the id→commit mapping and the two must agree, so the argument and the record
   cannot drift apart.

   **E3's class set is re-derived, not read from this prose.** The same rule parses
   `tortoise/projection/edges.py`'s `_DERIVATION_REFERENCES_LABELS` and requires E3's metric and
   denominator — **each cell on its own, not their union** — to name **exactly** `{Point} ∪ that set`.
   A denominator naming a class the model
   leaves unanchored by design (`:Object`, #5199 option A) **fails** — that is an unreachable
   `= 1.000`, and it would report the decision as a defect (§10.1) — and a denominator that *omits*
   a class the model does anchor fails too, because it would score with a numerator that cannot reach
   its own denominator. The rule reads the constant with `ast`, so it runs in every job including the
   one that does not install the package; a constant it cannot find is exit `2`, never a pass.

   **Both arms are asked only of a bundle that makes the claim.** The step that runs this gate
   checks TWO bundles in one invocation (`--dir docs/evidence/7949-capstone --dir
   docs/evidence/5087-hypothesis`, #10468), and these two arms were written for this one. Applied
   unconditionally they red-ed the sibling for seven things it never said (measured at the merged
   head, #10525): its record predates `measurement_visible_to_author` entirely — no field on any
   criterion and no `prior_measurements` block — so it makes no visibility claim to contradict, and
   its E3 is a session-arm receipt check that names no graph class at all, which the anchor model
   never scored. So the visibility arm is asked of a record that carries the schema (and such a
   record still owes the field on **every** criterion, so deleting one does not opt out), the
   class-set arm of an E3 row that names at least one graph class, and an arm that is not applied is
   **printed** (`registry arm NOT applied — …`) and carried in the JSON as `registry_scopes`. An
   undeclared skip would be the silent-green shape this very section exists to refuse.

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
prior_measurements:
  E1: bc547406f     # e1-e2.log, 2026-10-19 — measured, never scored (§0)
  E1b: bc547406f
  E2a: bc547406f
  E2b: bc547406f
  E5: ecc7bc8ca     # EXTRACTOR-V4-ARCHITECTURE.md §10, 2026-10-19 — PREDATES this record (§10.2)
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
than edited. **One exception, because the alternative is worse:** the **criteria table in §3.1 is
machine-parsed** (§7 item 6 reads E3's two cells to check them against the writer's constant), and a
`~~struck~~` cell would either red that check or require the parser to un-strike text it is judging.
Those rows are therefore **amended in place** — the original value and the reason live in the
`## Deviations` entry (§10.1), which is where a reader and the blame trail both find them. Struck
prose outside that table (§3.4) still carries its own inline `(amended, §10.1: …)` marker, so an
amendment is never invisible either way. A silently edited threshold after an evidence log is the failure this whole artefact
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
| 148 / 433 (0.34180), 148 / 383 (0.38642) | `capstone-measured-e0fc940b.log`'s `E3 census` block: `e3 label=Point nodes=338 anchored=141`, `Object 50/0`, `Event 45/7`, no `Document` written (§10.1) |
| 141/150 (0.940000), 159 turn rows, 29 operator Points | the same census block's `point composition` line — reported, NOT adopted as E3's denominator (§10.1) |
| 5/25 (20%) vs 13/25 (52%) recovered; 0.4800 raw absolute on n=25 | §10 @ `ecc7bc8ca` (run 1 losses 80.0%/48.0%) and `capstone-measured-e0fc940b.log`'s E5 row (§10.2) |

---

## 10. Deviations — the two amendments recorded after this registration was written

**What this section is.** §8's own convention: a change to this registration is recorded **here**,
with the date, the original value, the new value and the reason, and **the original text is preserved**
(the criteria rows this section amends are shown struck where they are still readable as prose; the
criteria table in §3.1 carries the amended cells, and this section quotes what they used to say,
verbatim, because a table cell cannot hold two versions and still be parsed by the gate).

**Why an amendment and not a new registration.** The convergent practice — PROSPERO (*"the published
version is not deleted, while amendments are submitted as new versions"*), CONSORT/SPIRIT 2025
(disclose every post-registration change **with its timing and reason**, alongside the registration
date), the Center for Open Science (*"if a measurement already exists before registration, it should
not be presented as newly preregistered … create a transparent-changes document and clearly disclose
what was known and when"*), and the registered-reports norm that later decisions be **timestamped
rather than obscured** — is the same shape: **do not rewrite the claim; append a dated, reasoned
disclosure that names what was known and when.** A fresh registration would have abandoned the four
untouched criteria's frozen bars to correct one class set and one history claim, which buys nothing a
preserved original plus this section does not already buy, and loses the E1–E4 provenance.

**Ordering, stated once and binding on all three entries.** This registration was added by `a6085af84`
(**2026-10-21**, #9507). **A scored evidence log already exists**: `capstone-measured-e0fc940b.log`
(head `e0fc940b`, generated **2026-10-23T23:16:55Z**, landed by `260903907`) — so §8's **protocol
deviation** rule is the one that governs this section, not the pre-scored revision rule. That is why
the original values are quoted and struck rather than overwritten, and why the dates below run
*registration (10-21) → measurement (10-23) → amendment (11-01)*. **§10.4 is the exception to that
chain and says so in its own first sentence:** it corrects a DISCLOSURE about E4 rather than a value,
no bar moves, and its ordering runs *measurement (10-19) → registration (10-21) → artefact (10-22) →
disclosure corrected (11-01)*.

**⇒ And the disclosure that ordering forces, stated plainly and not softened: the author of these
two amendments had SEEN both numbers before writing them.** The E3 census (`148/433`, RED) and the E5
re-score (`raw 0.4800`, RED) were in the tree, from a committed scored run, nine days before this
commit. A reader should apply that discount exactly as §0 asks them to apply E1's — with the concrete
counter-evidence that neither amendment rescues its criterion: **E3 under the amended denominator is
re-derived below at `148/383 = 0.38642`, still RED**, and E5's threshold is untouched and its cited
re-score still FAILS. A change made after seeing the number, that leaves the criterion red, is a
correction of a defect in the registration and not a re-cut of a bar.

### 10.1 E3 — the denominator counted a class the model leaves unanchored BY DESIGN (#9736)

**Old value (verbatim, as landed by `a6085af84`):**

- metric: `derived nodes written by the run (Point + Object + Event) carrying a version anchor`
- denominator: `**all** derived nodes written by the run (floor ≥ 30 nodes)`

**New value (§3.1, both cells, because the class set IS the definition):**

- metric: `derived nodes written by the run (Point + Event + Document) carrying a version anchor`
- denominator: `**all** derived nodes written by the run — Point + Event + Document (the classes the model anchors: Point on the node, Event/Document on their derivation link; floor ≥ 30 nodes)`

**Threshold: `= 1.000` — UNCHANGED.** No threshold moved for any measurement; nothing here was
fitted to a number (the one measurement that exists is reported below, and it does not reach the bar
under either class set).

**The measurement / decision this cites, and its date.** The **recorded decision**, not a measurement:
**#5199's owner-approved option A (2026-09-25)**, CLOSED 2026-10-21, comment `2026-10-21T02:27Z` — the
version anchor sits on the **derivation link**, and `:Object` reaches its source through an
identity/mention link that carries **no** `sourceVersion` **by design**. Pinned in the tree, not in
prose: `_DERIVATION_REFERENCES_LABELS = frozenset({"Event", "Document"})`
(`tortoise/projection/edges.py:747`), `edges.py:721-723`'s statement of the rule, and
`tests/test_source_version_references_5199.py::test_identity_link_stays_property_free`.

**Why the old denominator was a defect and not a bar.** Counted over `Point` + `Object` + `Event`, E3's
numerator could never equal its denominator: every `:Object` mention link is unanchored *by rule*, so
`= 1.000` was **unreachable as registered**, and a run reporting E3 red would have been reporting the
decision, not a defect. The narrowing **follows** #5199 — it is the pre-registration catching up to a
recorded decision, which is the only direction a denominator may move without fitting a number.

**Finding — the number, re-derived on the AMENDED denominator, from the committed census (no new
run, no re-scoring).** `capstone-measured-e0fc940b.log`'s `E3 census` block reports, per label:
`Point` **338** nodes (141 anchored), `Object` **50** (0 anchored), `Event` **45** (7 anchored), and
**no `Document` label was written** (post-D10 a document is a `:Source`; the run wrote none). So:

- **as registered on 2026-10-21** (Point+Object+Event): `(141 + 0 + 7) / (338 + 50 + 45)` =
  **148/433 = 0.34180** → RED (the log's own figure);
- **under the denominator this amendment registers** (Point+Event+Document):
  `(141 + 7 + 0) / (338 + 45 + 0)` = **148/383 = 0.38642** → **still RED**, and NOT close to `= 1.000`.

**⇒ The narrowing does not rescue the bar, and that is the honest result — reported, not adjusted.**
The residual is a *second* contradiction inside `Point`, already measured in the same log and **not**
resolved by #9736: of the 338 `Point`s, 159 are mechanical **turn rows** (no `extractedFrom` link by
construction — `_TURN_WRITE_CYPHER`, `tortoise/sdk.py:2408`) and 29 are **operator Points** (written by
`create_operator`, which never writes `extractedFrom`); 188 of 338 `Point`s are therefore *structurally
incapable* of carrying the anchor this row demands, and no denominator can make `= 1.000` reachable
while they are counted as derivations. The same log computes the writer-class figure — anchored / the
class that is *expected to* derive — at **141/150 = 0.940000**. **This amendment does NOT adopt that
denominator**: `Point` is in the class set by the issue's own resolution, and switching to a
"expected-to-derive" subset would be exactly the re-definition §7 item 6 refuses (it requires E3's
cells to name *exactly* `{Point} ∪ _DERIVATION_REFERENCES_LABELS`, so a subset fails the rule rather
than satisfying it). It is recorded as the
finding that #9736's remaining attribution — *lost link, or a writer that never derives?* — still owns.
`measurement_visible_to_author` for E3 therefore stays **`false`**: the field asks whether the
measurement was visible to the **registration's** author, and this one postdates the registration by two
days. Its existence is disclosed here and in the record's `post_registration_measurement`, which is
what a reader needs and what the field name alone cannot carry.

### 10.2 E5 — the record said "no measurement exists anywhere" for a metric measured two days EARLIER (#9738)

**Old value (verbatim, as landed by `a6085af84`):**

- `preregistration.json`: `"measurement_visible_to_author": false` for `E5`.
- §0's table: `| **E3**, **E4**, **E5** | **no** — no measurement exists anywhere | a genuine pre-registration, prior to its first run |`

**New value:** `"measurement_visible_to_author": true` for `E5`, with a
`measurement_visible_to_author_basis` naming the artefact; §0's row is **struck, not deleted**, and
replaced by two rows — `E3, E4` (still unmeasured) and `E5` (measured, post-visible).

The measurement: `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §10 — *the
raw-vs-narrative measurement* — landed in **`ecc7bc8ca`, 2026-10-19** ("… (#7947) (#9071)"; #7947 CLOSED
COMPLETED 2026-10-19T12:54Z). Its receipt, run 1: **raw loss 80.0% (20/25)** vs **narrative loss 48.0%
(12/25; 7 absent, 5 flattened)** of bound qualifiers — i.e. **recovered** 5/25 vs 13/25. (The
`8 absent, 4 flattened` figure quoted elsewhere is the 2026-10-18 **replication**'s narrative row, whose
raw arm is 52.0% = 13/25; the two runs are cited apart so neither is paired with the other's receipt.)

**Ordering — the direction that makes the original text false.** The measurement (2026-10-19)
**PRECEDES** this registration (`a6085af84`, 2026-10-21) by two days. So this is not a metric that
became measurable after registration: it was measurable **before**, and the record said the opposite.
The amendment is **2026-11-01**, i.e. after **both**, and it corrects a claim about history rather
than a value — no threshold moved.

**Why the prior is alarming and the bar is NOT retro-fitted — and which figure is which.** E5 registers
the ratio of **recovered** claims (narrative over raw), not the loss. §10's receipt states **loss**:
narrative loses **48.0%** (12/25) and raw loses **80.0%** (20/25), so the recovered figures are
**13/25 = 52%** and **5/25 = 20%** — a ratio of **2.6**, which PASSES `>= 0.90` with margin. What the
prior falsifies is the **raw absolute floor**: `0.20 < 0.80`. (An earlier draft of this section
divided the losses and called it a `0.60` ratio — the arithmetic was right and the quantity was wrong,
and it is corrected here rather than left to flatter the record in the alarming direction.)

**And the populations still do not match, so §10 is NOT a verdict on E5.** §10's denominator is **25
*bound* qualifiers**, while E5 is registered over the **29 marker-bearing sentences**
(`hedge_scope_sentences: 29`, pins). Mixing them would be the numerator/denominator mismatch §1.2
refuses. **⇒ E5 must be re-measured on its own 29-sentence population — but the raw absolute failure
on the prior population is now alarming rather than neutral, and the record stops telling readers it
had no prior at all.** Recorded as a finding; the threshold is untouched.

**And the post-registration re-score, disclosed here rather than only in a log.**
`capstone-measured-e0fc940b.log` (2026-10-23) re-scored the #7947 arm receipts against **this corpus's**
gold: `ratio=1.0833` (passes `>= 0.90`) with `raw_absolute=0.4800` (FAILS `>= 0.80`), `n=25` sentences,
and a disclosed incomplete raw receipt (`wp07_bluepeak_followup:raw partial=True`). So E5 is **RED on the
registered bar** as of that run — on **25** sentences, not the registered **29**. Both numbers are
quoted because neither may be called final until the populations are reconciled, and the reconciliation
is not a reason to move the bar.

**Not a duplicate of any earlier amendment.** `git log -S'measurement_visible_to_author'` over
`preregistration.json` returns **exactly one hit *before* this amendment — `a6085af84`, the
original**; the second hit is this commit, which introduces `measurement_visible_to_author_basis` —
a string that carries the search term, so any count written as a bare total moves the moment the
amendment lands. The claim is scoped to the history it is about; and the two files'
history holds **no earlier `## Deviations` entry at all**: before this one, the only change to either
file after its add-commit was `ef231735b` (2026-10-29, #5087 / PR #10072), which touched §3's
CONTROL/TREATMENT arm paragraph and re-cut `document_sha256` **once** — a re-cut of the digest, not an
amendment of a criteria row, a denominator, or a disclosure claim. (`#10215` is an unrelated OPEN
finding about S2.3 idling on the pre-registered corpus; it is not a revision of this file.)

**What enforces both, mechanically.** `tools/capstone_preregistration_check.py` → §7 item 6: a
`measurement_visible_to_author: false` contradicted by a cited `prior_measurements` entry **fails**; a
`true` with nothing cited **fails**; a `false` that states **no** `measurement_visible_to_author_basis`
**fails** (§10.4: the ground a `false` rests on cannot be checked, so an unstated one is refused rather
than trusted); and E3's metric/denominator must name exactly the classes derived
from `_DERIVATION_REFERENCES_LABELS` plus `Point`. Mutation-checked in
`tests/test_capstone_preregistration_9075.py`, including the node `#9738` names — flip E5's field back
to `false` and the gate goes red — and the `false`-without-a-ground arm, where deleting E4's basis (or
blanking it to whitespace) reds the same way.

### 10.3 The post-registration measurements — disclosed, NOT re-scored

`capstone-measured-e0fc940b.log` (head `e0fc940b`, generated **2026-10-23T23:16:55Z**, landed by
`260903907`) is the first **scored** run against this registration, and it measures **more than the two
criteria amended above**. Its verdict table reads: `E1 BLOCKED`, `E2 BLOCKED`, `E3 RED 148/433`,
`E4 RED 2/15`, `E5 RED 12/25`. That is disclosed here so no reader concludes from §0's "no measurement
existed at registration" row that E3 and E4 remain unmeasured today — they do not, and the distinction
between *"unmeasured when registered"* and *"unmeasured"* is the whole subject of this section.

**E4 is not amended, and this section says why.** The run scores E4 at **2 of 15** — `edge_correct=2`
on receipt `tests/eval/write_path/receipts/w2b-operator-2026-10-19.json` — and the log's own E4 note
records the caveat that matters: the *file* postdates this registration but the *measurement* does not,
and its `corpus_hash` (`sha256:943a99ec…`) is **not** the frozen `fixtures_hash`
(`sha256:dd7e8520…`), so it is scored on **its own corpus revision**. E4's denominator (**15**), its
threshold (`= 15 of 15`) and its row are unchanged: nothing in #9736 or #9738 asks to move them, and a
bar that fails on a non-frozen corpus is a reason to re-run, never a reason to re-cut the bar. The
figure is recorded in the record's `post_registration_measurement` for E4 so a machine consumer meets it
too.

**Neither `measurement_visible_to_author` field changes for this**, but they stay `false` for **two
DIFFERENT reasons**, and conflating them is the defect §10.4 fixes. The field asks whether the
measurement was visible to the **registration's** author at `a6085af84` (2026-10-21). **The 2026-10-23
run** postdates it by two days, so the existence of a later measurement is stated here and in the
record, and neither field is retroactively rewritten. But that run is not what makes E4's field
`false`: **E4's measurement ran on 2026-10-19**, before the record — its *artefact* landed after it — so
E4's `false` rests on the artefact's absence from the tree at `a6085af84`, not on the run's date. §7
item 6 enforces that separation in both directions: it refuses a `false` contradicted by a
**pre-registration** citation, and it now also refuses a `false` that states **no ground at all**,
because a bare `false` cannot be told apart from a stale one.

### 10.4 E4 — the `false` was right and its ground was stated NOWHERE (#10525 review)

**What was wrong, stated as the contradiction it was.** §0's replacement row read
`**E3**, **E4** | **no at registration** — no measurement existed when this record was written`, and
§10.3 — ten lines after quoting the log's own caveat — kept E4 `false` on the ground that *"this run
postdates it by two days"*. Both cannot be true of E4: its **measurement** (`operator_audit` on
`tests/eval/write_path/receipts/w2b-operator-2026-10-19.json`: `edge_correct=2`, `planted=15`) carries
the receipt's own `date=2026-10-19T01:39:26Z`, **two days BEFORE** `a6085af84` (2026-10-21T21:49:11Z).
What is true is narrower and was stated nowhere: the **artefact** landed at **`d50217273`
(2026-10-22T05:34Z)**, *after* the registration, so no number from that row was in the tree its author
wrote. `false` is therefore correct — as a claim about **visibility**, which is what the field is
named — and it was unfalsifiable, because `prior_measurements` is the only table the gate reads and E4
was not cited in it.

**What changed, and what did NOT.** *Changed:* E4's `false` now carries a
`measurement_visible_to_author_basis` naming all three facts (when the measurement ran, when its
artefact landed, when this record was added), and §0's row was corrected from *"no measurement
existed"* to *"no measurement was **visible**"* — the sentence, not the verdict. *Not changed:* E4's
metric, its **15**-edge denominator, its `= 15 of 15` threshold, its `post_registration_measurement`,
and the field itself. **No bar moved and no measurement is re-scored**; the amendment is to the
DISCLOSURE of a claim that was already there, which is why it is a correction and not a re-registration.

**⇒ The general rule this leaves behind.** §7 item 6 now refuses *any* `false` that states no ground.
The reason is measurable rather than stylistic: E4's field was **right**, and it took a fresh-context
reviewer reading three files to establish that — with the reason unstated, no reader and no arm could
tell it from a `false` nobody had revalidated. A claim about history has to carry its evidence in the
place a machine reads it.
