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
| **A1**, **A3**, **A4**, **A5** | **no at registration** — the field asks whether a measurement of the row was visible to the registration's author **at `a6085af84`** (§10.3, the same anchor E3 uses), and every reading of these metrics postdates it. ⛔ **The ground is NOT "no measurement exists anywhere"**: the owner's 2026-11-04 `live-v4` hand reading is a prior of A1/A3/A4/A5 (it is quoted as each row's `threshold_basis`), and `capstone-measured-9152448d.log` — committed `31f996f91`, 2026-11-04, **in this directory** — carries A1's object population and a COMPLETE edge-type enumeration on the pinned corpus, plus A5's `Subject` count. Both postdate `a6085af84`, so `false` holds; what a reader must apply is the discount that the bar's author HAD seen those figures (§10.11) | a threshold for the **scoring run** (`#10984`–`#10988`), disclosed as post-visible |
| **A2** | **no** — no S3 RESOLVE attach/mint census has ever been taken on any graph, committed or not | a genuine pre-registration, prior to its first run |
| **E6** | **yes** — two priors predate this row: the owner's 2026-11-05 baseline (`#7949` comment `6664402530`: 32 of 36 declared relations writable, 4 unbound) and `docs/evidence/11083/pack-relation-census.json`, committed `f5c69f7478` on 2026-11-08 by `#11543` (§10.21) | a threshold for the **scoring run**, disclosed as post-visible — and this is the row whose disclosure costs least, because its bar is a zero-tolerance COUNT (`= 0`): there is no measured value it could have been fitted to. The count it registers has MOVED since that baseline, which §10.21 states rather than absorbs |

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

`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md:108` states **62.3% of Objects are `the <X>`**. Its
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

### 1.6 Amendment — the DOOR, and which corpus carries which row (`#10983` / C2)

Added by `#10983`. Nothing above changes: no metric, no denominator, no threshold. What this section
adds is the two facts a reader needs to know **what a number in §3 is a number ABOUT**, and it is
written here — into the registration — because a measurement contract that lives in prose is one
a reader can re-decide.

#### 1.6.1 The door

**The acceptance runs the fleet's v2 extraction lane:**

```text
tortoise/hosted_api.py::_capture_lane()            → "v2" (default), "m2" iff TORTOISE_SESSION_EXTRACTOR == "m2"
  sdk.capture_session → sdk._extract_session_v2    → tortoise/extractor_v2.py::extract_session_v2
```

The hosted endpoint and the SDK path **select the lane from the same variable with the same
comparison**, so the "hosted endpoint or SDK path" question is not a choice between two doors — it is
one door with an optional HTTP boundary in front of it. The probe drives `sdk.capture_session`, i.e.
the SDK side of that one door. The `m2` lane (`_extract_session_llm` → `tortoise/extractor.py`) is the
**opt-in, non-default** extractor and is *not* the door.

Every citation above is resolved from the tree by `tests/test_7949_c2_door.py`, not typed — a renamed
or relocated function reds that suite.

#### 1.6.2 The seam set, as the CODE resolves it at this head

| seam | resolver | default at this head | behaviour seam? |
|---|---|---|---|
| `TORTOISE_VET` | `extractor_v2._vet_enabled` | **off** | yes |
| `TORTOISE_CLASSIFY_LATER` | `extractor_v2._classify_later_enabled` | **off** | yes |
| `TORTOISE_S2_DECISION_SEAM` | `s2_decision_seam.seam_enabled` | **off** | yes |
| `TORTOISE_VALUE_GATE` | `value_gate.value_gate_enabled` | **ON** ⚠️ | yes |
| `TORTOISE_S4_DELTA` | `extractor_v2._s4_delta_enabled` | **ON** | yes |
| `TORTOISE_LABEL_ORDER` | `extractor_v2._label_order_rng` | off (render diagnostic) | **no** |

⚠️ **`TORTOISE_VALUE_GATE` is default-ON at this head** — `#10996` made the mechanical predicate run
unless the flag says otherwise (`is_truthy(os.environ.get(ENV_FLAG, "1"))`), and `extractor_v2` ORs it
into `vet_enabled`. **The S2.2 VET pass is therefore armed on the fleet's default write path even
with `TORTOISE_VET` unset**, so "VET is off by default" is true of the flag and false of the pass.
`#10983`'s own ledger and the epic's "the fleet's write path performs zero selection" both predate
that change; this table is the record at this head, and the pin suite resolves every row against the
modules so it cannot rot quietly.

**What the acceptance run arms:** `TORTOISE_VET=1`, `TORTOISE_CLASSIFY_LATER=1`,
`TORTOISE_VALUE_GATE=0`, `TORTOISE_S2_DECISION_SEAM=0`; the lane pinned to (unset ⇒) `v2`;
`TORTOISE_S4_DELTA` pinned to `1`; `TORTOISE_LABEL_ORDER` pinned unset; `TORTOISE_EXTRACTOR` pinned
unset; and the three measurement switches an inherited shell must not supply —
`TORTOISE_SESSION_LLM_MOCK` (the offline model seam, which swaps the model for `_V2SessionMock`
**and** short-circuits the provider-key check, so an ambient `=1` would score canned output under a
real key), `TORTOISE_EXTRACTOR_PROMPT` (a third render switch, not byte-identical to the default
since `#9733`), `TORTOISE_EXTRACT_MODEL` (selects the model — NOT the output bound: every stage
passes its own `max_tokens=_stage_cap(…)` per call, which beats the adapter default), and the
truncation budgets `TORTOISE_EXTRACTOR_MAX_TOKENS` (`_stage_cap`, read at call time: unset ⇒ the
shipped 1500/16000 stage defaults) and `TORTOISE_EXTRACTOR_ESCALATION_TOKENS` (the
length-truncation retry budget), `TORTOISE_REFERENTIALITY` (the S2.2 mention arm, reached
through `referentiality.ENV_FLAG`), `TORTOISE_CHANGE_FEED` (an ambient `=1` adds a `:GraphEvent`
to the graph the run writes), and the two confidence gates `TORTOISE_SUBJECT_TAU_HI` /
`TORTOISE_SUBJECT_TAU_LO` (read through a parameter by `subject_binding`, and they decide whether
the `:aboutSubject`/`:aboutObject` edge is written at all), plus `TORTOISE_LABEL_ORDER_SEED` (inert while
`TORTOISE_LABEL_ORDER` is off, pinned so the pair cannot be split), `TORTOISE_CONTEXT_ENRICH` (the per-turn REWRITE of the text each turn embeds, gated in `context_enrich.enrich_enabled` and reached from `sdk.capture_session` as `enrich_fn=_capture_enriched_texts`; fail-safe OFF) — all pinned **unset** — and `TORTOISE_JSON_MODE`, pinned to **`1`** because its absent state is already ON and it is read PER CALL to flip the request's `response_format` (`model_adapters`), and `TORTOISE_IDENTIFIER_TOKENS`, pinned to **`1`** because its absent state is already ON (`sparse.py:226`, `env_flag(IDENTIFIER_ENV, True)`) and it rewrites `search_keys` on every `:Point` the run writes (`projection/entities.py:1478`, reached from `create_point`); the registered corpus yields no aliases either way, measured — and
`TORTOISE_STATE_VALUE_VERBATIM`, pinned to **`1`** because its absent state is already ON
(`extractor_v2._state_value_verbatim_enforced`, `env_flag(…, True)`) and it is read PER POINT at
the emit site, where it decides whether a REWRITTEN value is WITHHELD (`=0` writes what the shipped
default withholds). The cycle-7 to
cycle-11 review findings (`TORTOISE_CONTEXT_ENRICH`/`TORTOISE_JSON_MODE`, then `TORTOISE_CHANGE_FEED`,
then `TORTOISE_IDENTIFIER_TOKENS`, then `TORTOISE_SUBJECT_TAU_HI`/`_LO`, then
`TORTOISE_STATE_VALUE_VERBATIM`) each sits on the acceptance
path and was in no pin, no ledger row and no closure. The authoritative list is
`capstone_measure.DOOR_PINNED_OFF` / `DOOR_PINNED_ON`, which `tests/test_7949_c2_door.py` resolves
against the tree — this sentence is the reader's map, not the control. The set
is CLOSED over the **measurement modules the path renders with**, and what
`tests/test_7949_c2_door.py` enforces **mechanically is the CENSUS over that
list, not the list's membership**: `path_modules` is a hand-maintained tuple, and every
`TORTOISE_*` read inside those modules must be classified (an arm seam, a pin, a declared input, or
an acknowledged non-seam with a stated reason) or the suite reds. Membership is hand-kept, so a read
added to a module *outside* that tuple is **not** caught by it — stated plainly rather than left to
be discovered, and tracked as **#11410** (the `sdk`/`hosted_api` helpers those two hops call are the
measured gap). The same call chain also reads *infrastructure* variables (record DSN,
packs dir, embedding gates, store connection): those are inputs a run needs in order to happen, not
settings that move the measured population, and they are deliberately outside the door's partition —
stated because an earlier revision of this section called the whole path "closed", which the review
measured false (`TORTOISE_REFERENTIALITY` was read on every v2 capture and accounted for by
nothing). The closure is derived over the modules the test names in `path_modules` — a POINTER rather than a
count, because a count in this paragraph went stale three rounds running (six, then seven, then eight) while
the derivation grew, which is itself the defect this section is about. Cycle 10 also made the derivation
read the `TORTOISE_*` **literals** in those modules, not only the two read shapes its resolver understands:
`subject_binding.py` holds both of its spellings and passes one to a helper as an argument, so it resolved
to the EMPTY SET while sitting on the write path (`bind_point_subjects`, inside
`_extract_session_v2`) and gating whether the `:aboutSubject`/`:aboutObject` edge is written at all
(measured: `decide_binding(0.90)` is `bound` at the defaults and `suspected` with
`TORTOISE_SUBJECT_TAU_HI=0.99`) — so the arm seams, the mention arm, the per-turn embed
rewrite, the request's `response_format`, the change-feed writer, the identifier-token writer and the
confidence-gated subject binder are inside the derived
surface, not hand-listed beside it. That list grew one review round at a time — named POSITIONALLY,
because a count in this sentence went stale every time it was written: `TORTOISE_CONTEXT_ENRICH` /
`TORTOISE_JSON_MODE` (cycle 7), then `TORTOISE_CHANGE_FEED` (cycle 8), then `TORTOISE_IDENTIFIER_TOKENS`
(cycle 9), then `TORTOISE_SUBJECT_TAU_HI`/`_LO` (cycle 10), then `TORTOISE_STATE_VALUE_VERBATIM`
(cycle 11) — and the DISCOVERY ROUTES are not the same, so neither is generalised: cycles 7–10 were
each found by a measured mutation that left the previous derivation green, while cycle 11 was found by
the **CENSUS** (the switch landed inside `path_modules` and redded this closure for every PR until it
was classified), which is the closure doing its own job rather than a mutation finding it. That is the
honest reason to state
it as a CLOSURE over the reads INSIDE those modules rather than as a list somebody curated — not
a closure over everything the path reaches, which is the gap #11410 records. Every one is written in-process, **read back**, and a run whose read-back
disagrees is **refused** (`probes/capstone_measure.py`); the measurement switches were
unpinned until the `#10983` reviews (rounds 1 and 2), which is the same declaration-versus-control gap one layer below
the lane. The arm pins `TORTOISE_VALUE_GATE=0` on purpose: with the
default-ON predicate the arm would otherwise measure the predicate's population while declaring an
arm that does not include it.

#### 1.6.3 Which corpus carries which row

**The registered corpus (`tests/eval/write_path`) is a FIXTURE**, pinned by `fixtures_hash`, and one
of its arms (`E1b`) is defined as a run on the **`m2`** lane. So the registered E1/E2 rows are
statements about *this fixture on this lane* — they are **not** a proof about the graph the fleet
builds. Writing that down does not weaken any row; it stops a green row from being read as a claim it
never made.

| row | corpus that carries it | the claim it supports | it is NOT a proof of |
|---|---|---|---|
| **E1** | `tests/eval/write_path` (fixture, this hash) | `^the <X>` share falls ≥50% **on this corpus**, both arms, same commit | that the fleet's graph volume falls |
| **E1b** | `tests/eval/write_path` **on the `m2` lane** (`baselines/m2.json`, posture `m2`: `salient_unit_survival_strict` 1.0 = 90/90 at this `fixtures_hash`). ⚠️ The figure the probe **publishes** is read from `docs/evidence/9889/e1-e2-scores.json` → `e1b.real_facts` ← `on-arm.log`, which is a no-loss **CONTROL on the scored (v2) arm** and says so itself ("no committed artifact carries `grading.strict_survival_counts` over the m2 lane"), and the `m2` baseline's own history records its metrics as **carried forward** from `943a99ec6`, not re-measured at this hash | the anti-gaming control: planted units survive the deterministic lane (90/90) | anything about the v2 door: the `m2` lane is a *different extractor*, so unit survival there says nothing about what the v2 lane writes — **and the published 90/90 was not measured on it either** |
| **E2a / E2b** | `tests/eval/write_path` (fixture) | value-bearing units survive; claims are not fused | the fleet's correctness, which needs a capture through the door |
| **E3** | the run's **own door-written graph** (shape, not content) | every derived node carries a version anchor | a content claim: it is a structural property of the writer; nor a fleet-population claim, since the graph it counts is whichever graph the run wrote |
| **E4** | `tests/eval/write_path`, graded **through the v2 door** (the receipt the probe selects by `corpus_hash`) | 5 of 15 planted operator edges are wired and persisted against the registered bar (15) | the fleet's graph: this is the fixture corpus. ⚠️ It is **not** an `m2` row — the selected receipt (`w2b-operator-2026-10-24-8503-b.json`) records `extract_session_v2` |
| **E5** | `tests/eval/write_path` (fixture, §10) | the raw-vs-narrative premise, measured on this corpus | a fleet-corpus premise: a different corpus is a different measurement |
| **any fleet-graph claim** | **`fleet-capture`** — a real capture through the door | what the fleet's write path actually produces | nothing yet: this registration pins no fleet-capture corpus, and one must be registered **before** such a number is scored |

⚠️ **E4's figure, reconciled.** §10.3 and the record's `criteria.E4` `post_registration_measurement`
state **2 of 15**, measured on receipt `w2b-operator-2026-10-19.json` — whose `corpus_hash`
(`943a99ec…`) is **not** the frozen `fixtures_hash` (`dd7e8520…`), so that figure measures a different
corpus revision. The probe now SELECTS E4's receipt by `corpus_hash` against the frozen pin, and the
receipt that matches (`w2b-operator-2026-10-24-8503-b.json`) reads **5 of 15** on
`extract_session_v2`. **5 of 15 is this row's figure at this head**; 2 of 15 is kept above as the
historical disclosure it is, not as the row's current number. The denominator (**15**) and the
threshold (`= 15 of 15`) are untouched by either figure, so the row is RED either way.

#### 1.6.4 The negative control, MEASURED

Same corpus session (`wp01_quarry_debug`), same offline model seam, both lanes, one embedded
graph each:

| lane | `:Object` | `:Point` | `:Event` | `:Session` | `:Operator` | `:Object` names |
|---|---|---|---|---|---|---|
| **v2 — the door** | **1** | **27** | **2** | 1 | 0 | `['the strategy']` |
| **m2 — the other lane** | **0** | **149** | **1** | 1 | 0 | `[]` |

The difference is measured, and it is **structural IN KIND, not a matter of degree**: the `m2` lane mints
**no `:Object` at all** on this session, and it also writes **5.5×** the `:Point`s — the ratio is the
*size* of a difference whose nature (a different extractor, not a different seam of the same one) is
what makes a number measured on one lane not a number about the other.

⇒ **A number measured on one lane is not a number about the other.** That is exactly why the `m2` column in §1.6.3 (E1b) must not be read as a fleet fact, and why the door is pinned structurally (§1.6.1)
as well as measured here — a control measures the two lanes *as they are today*, while the pin is
what stops the selector moving tomorrow.

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
| **A1** | `object→object` edges written by the run, by **declared relation type** and by **endpoint kind**, plus the number of **rollup queries** the run asks that answer (return at least one row) | every `:Object` node written by the run, same arm, same commit (floor of at least 30 Objects); and the rollup queries asked (floor of at least 1 query) | `>= 1` edge for **each** relation family pinned in `a1_relation_families` (`hasPart` — the ONE relationship label the pack's two declared hierarchy predicates `decomposesInto`/`hasPart` are both **written** as; a pin on the declared spelling names a label no writer stamps, which makes the bar unsatisfiable), and **every** rollup query answers (`>= 1` row); zero object→object edges on a population that clears the floor is **RED**, never REFUSED |
| **A2** | S3 RESOLVE candidates **attached** to an existing `:Object` versus **minted**, plus candidates **held** and each held candidate named with its reason | the **decided** S3 RESOLVE candidates in the scored run (floor of at least 30 decided candidates — `a2_decided_candidates`; the all-candidate count is reported beside it, never used as the floor) | `attach_share >= 0.50` of **decided** candidates, and every `held` candidate named with its reason — a run that holds candidates without naming them is **RED** |
| **A3** | statements that **join a chain** under the definition registered here — carrying at least one incident `IMPL`/`NAND` edge **and** at least one such edge crossing a batch boundary (a different `Event` root, with an `eventId` on **both** ends) — with the CHEAP rate (any incident `IMPL`/`NAND`, no cross-batch requirement) reported beside it and explicitly NOT the bar | the statements written by the run that carry an `eventId` — `Point` nodes whose `pointKind` is `statement` **and** whose `eventId` is present (floor of at least 30 statements AND an event-root coverage of at least 0.50); the unstamped statements and the `NULL` `pointKind` population are reported separately, never folded in | `cross_batch_rate >= 0.50` over the stamped statements; the cheap rate and the coverage are reported and never compared to this bar |
| **A4** | typed claims — **claim** Points (neither a truthy `is_episodic` nor a truthy `is_operator`) whose `pointKind` is present, not blank and not `statement` — reported by kind, with the `statement` and `NULL`/blank populations reported beside them | all **claim** `Point` nodes written by the run (floor of at least 30 typed statements); the excluded turn-row and operator-Point populations are counted and reported beside it, never folded in | UNMEASURED(the bar is owner-gated on the D3 fork — `#10988` and `#1022` decide whether A4 overrides D3; until that is ruled the row is REPORTED and compared to no bar, above the floor of `>= 30` typed statements) |
| **A5** | `:Subject` nodes written by the run, and the **four subject-edge families** (`aboutSubject`, `memberOf`, `ownedBy`, `managedBy`) counted per family (`count(DISTINCT r)`), plus the statements carrying at least one of them | the distinct actors the A-corpus names — `a5_actors`, floor of at least 30 named actors — with the `:Subject` count and the `Point` population reported beside them, never used as the actor denominator | `>= 1` `:Subject` per distinct actor named in the A-corpus and `>= 1` statement carrying a subject edge; a family that is zero is named with its reason rather than averaged away |
| **E6** | declared relation rows that are **unbound and unexplained** — every `relations[]` / `hierarchies[]` row whose `relation_write_verdict` is `predicate-not-in-allowlist` and which is **not** marked `declarationOnly: true` with a reason, **plus** every row that binds a write path but reports no `effective_direction` | **every** declared row across the installed packs, counted from the registry at the run's head — `population.declared` in `docs/evidence/11083/pack-relation-census.json`, **42** at the `#11083` stamp (36 relations + 6 hierarchy paths); a pack set that resolves to fewer than `e6_declared_rows` rows is REFUSED, never scored on an empty population | `unbound_and_unexplained = 0` — and the census's `written` column (`direct` / `mediated` / `none`) is reported beside it, **never** the bar: `A1` already measures what the **written** graph answers |

⚠️ The **step set** these rows are measured over is not all-unconditional: §11 records which of §4.1's
eight steps are required and which is conditional on the pipeline having work for it (`#10215`).

⚠️ **The five `A`-rows were added on 2026-11-06 by `#10982` (§10.11) and are NOT required rows.**
`pins.required_criteria_rows` is now `[E1, E2, E3, E4, E5, E6]` — the sixth entry is `E6`, added by
`#11673` (§10.21) on the owner's 2026-11-05 ruling, **not** an `A`-row: the `A`-rows name criteria
whose scoring runs `#10984`–`#10988` own, and a registration may not turn a row into a gate before
anyone has measured it. A verdict table that omits them is not refused; a table that carries one is
held to the same rules as `E`-row (`V12` refuses a row whose `blocked_by` has CLOSED).

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
(`tortoise/extractor_v2.py:325-341`, the constant's own extent, which keeps *"measurements,
deadlines/freezes, thresholds/TTLs, versions, and counts"* and drops only *"ids, hashes, and
ephemeral counters"*):

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

### 3.7 The `A`-rows — the object layer the epic's assertions name (`#10982`)

`#7949` was extended on 2026-11-04 with five owner assertions (`A1`–`A5`) about the **object layer**,
measured on the fleet's hosted `live-v4` graph (`#7949` comment `6645275147`). `E1`–`E5` name none of
them: they cover volume, retention, anchoring, operator edges and narrative fidelity, so the object
layer had **no acceptance criterion at all**. These five rows close that gap — **registered here,
before any `A`-row run is scored**, so `#10984`–`#10988` report against a fixed bar rather than a bar
written after their numbers landed.

**The corpus, and the refusal that guards it.** `A1`–`A5` score on the **same** corpus the `E`-rows pin
(`tests/eval/write_path`, `fixtures_hash` above), because the record's corpus pin is a single
`fixtures_hash` and two corpora would make the two row families incomparable on one run.

**That pin was written `PROVISIONAL`, and `#10983` (C2) has since LANDED** (`#11298`), so the qualifier
is retired rather than left standing. C2 did not move any `A`-row value; it settled the question the
provisional line was waiting on, in §1.6.3: **the registered corpus is a FIXTURE**, and a claim about
the graph the fleet actually builds belongs to `corpus_rows.fleet-claim`, which pins `fleet-capture`
and **may not be scored until that corpus is registered**. The `A`-rows inherit that rule verbatim, so
every one of them is a **fixture-corpus** statement — **none is a fleet measurement**, and in particular
none of the four `A`-rows whose `threshold_basis` quotes a 2026-11-04 `live-v4` figure (`A1`, `A3`,
`A4`, `A5` — `A2`'s basis is tree-derivable) measures that figure. The machine
record states this as `a_corpus.not_a_proof_of`.

What C2 did **not** do is discharge the corpus's *content* requirement: `A1` still needs a pack that
declares a part-whole hierarchy and `A5` still needs statements carrying bindable subject slots. That
requirement sits on the corpus, not on a pending landing, so it survives C2's close unreduced — a row
whose population is absent is **REFUSED** with the absent population named, never scored on a
population of three objects. A run handed a **different** corpus is refused by the `G1` rule the record
already applies, not scored.

**One instrument, one record.** The census is emitted by the existing
`probes/capstone_measure.py` — one `run.a_row_census` block per graph (the exact key the machine
record carries; the human log renders it as the `## A-row census` section) — there is no second
census, no second registration file and no new gate. `TAGGED` is emitted **with its "no carrier" annotation**, never as
a target: it is written only by `_sync_tags` from an explicit `tags` value and no extraction path sets
one (`#7949` §2 R7).

**Per row: metric, denominator, floor, and where the bar comes from.**

- **A1 — pack business logic is load-bearing.** *Metric:* `object→object` edges **by declared relation
  type and by endpoint kind**, plus rollup queries answered. *Denominator:* `:Object` nodes in the
  scored graph (floor **30**), and the rollup queries asked (floor **1**). *Bar:* at least one edge per
  relation family pinned in `a1_relation_families` **and** every rollup query answering. The set is PINNED rather
  than "whatever the pack declares", because an unpinned family list makes the bar unfalsifiable — a
  scorer could read one, two, nine or thirty-six edges as the requirement — and `#10983` may widen it
  only as a reviewed edit to this record. ⛔ **A pinned family is a WRITTEN label, not a declared
  predicate** (`#10982` review round 2): the dev pack declares two hierarchy entries — `Theme → Epic`
  and `Epic → Issue`, `packs/dev/manifest.yaml:192-196` — and BOTH are written `type: hasPart`; the
  hierarchy seam writes the entry's `type` (`tortoise/pack_hierarchy.py:215-224`: *"the edge this
  function would write carries `decomposesInto`, which `create_edge` refuses"*) and every writer folds
  `composedOf`/`decomposesInto`/`contains`/`wraps` onto `hasPart`
  (`tortoise/projection/edges.py:1513`). `hasPart` is in `create_edge`'s own predicate set;
  `decomposesInto` is not (`edges.py:621-632`). So the row pins the ONE written family and names the
  declared spellings that fold onto it (`a1_relation_family_fold`) — a pin on the declared spelling
  would name a label that occurs zero times on every graph this repository can write, i.e. an
  unsatisfiable bar. ⚠️ **What the family half does NOT prove:** `hasPart` is also in the generic
  structural writer's predicate set and `create_edge` validates the RELATION, not the endpoint kinds,
  so ONE `hasPart` edge between any two `:Object` nodes satisfies it — a green `A1` family half is
  NOT by itself evidence that the pack's declared `Theme → Epic` / `Epic → Issue` hierarchy is wired.
  `by_endpoint_kind` is emitted beside the count so a reader can tell the two apart; `#10984` (C3)
  must read it, and tightening the bar to the pack's declared endpoint kinds is a `#10984` decision,
  not something this registration asserts. *Basis:* the 2026-11-04
  reading measured `object→object edges []` — zero of every relation type — against 5,314 Objects,
  while the declared vocabulary already exists (`packs/dev/manifest.yaml:142` `decomposesInto`,
  `:148` `hasPart`). **Not `E6`** (`#11083`): `E6` measures the declared→writable→written chain per
  pack across all 36 declared relations; `A1` measures what the **written** graph can answer.
- **A2 — S3 attaches instead of minting.** *Metric:* candidates **attached** vs **minted**, plus
  **held** (each named with its reason). *Denominator:* **decided** candidates, floor **30**
  (`a2_decided_candidates`) — the floor sits under the RATE's own denominator, because a floor on all
  candidates lets a run hold 29 of 30, decide one, and score `1.000` on a single decision. The
  all-candidate count is reported beside it and is never the floor. *Bar:* `attach_share >= 0.50` of
  decided candidates, and no unnamed hold. *Basis:* it is a two-way decision, so the bar is its
  half-way point. The measured context it exists to reverse is the backfill's own census
  (`tortoise/definite_description_backfill.py:15-23`): of a drop-shaped population of **6,533** Objects,
  **2,614 are single-use** and 402 unused, on **7,870** Objects and 27,324 reference edges — i.e. 40%
  of that class is single-use. ⚠️ The 62.3% figure in §1.2 is a **different** measurement (the
  `the`-prefixed rows: 4,901 of 7,870, of which 2,570 are load-bearing) and it is **not** a
  single-reference share — a bar must not be justified by a class it does not describe. No `A2` run
  exists, so nothing is fitted to one.
- **A3 — S5 emits the batch's story *and* the cross-batch link.** *Metric:* chain-join rate under the
  **registered** definition — an incident `IMPL`/`NAND` **and** at least one such edge crossing a batch
  boundary, with an `eventId` on **both** ends (a `coalesce(eventId,'')` comparison counted an
  unanchored row as "crossing", which is a fail-open, not a measurement). *Denominator:* the
  **stamped** statements (`pointKind = statement` **and** an `eventId`) — floor **30** statements and
  an event-root coverage of at least **0.50**; below either floor the row is REFUSED. The rate is over
  the stamped population, not over every statement: an unstamped statement can never be counted as
  crossing, so dividing by all of them reports coverage as if it were chain-joining. The unstamped
  count and the `NULL` `pointKind` population are reported separately. *Bar:* `cross_batch_rate >= 0.50`
  (the registered name of the field the census emits; an earlier draft called it `chain_join_rate`,
  which no artifact carries); the **cheap** rate (any incident `IMPL`/`NAND`)
  is reported beside it and is **not** the bar. *Basis:* 9,910 of 15,534 statements (63.8%) carried
  **neither** edge in the 2026-11-04 reading, so 36.2% join cheaply — half is a reversal of the
  measured majority. The cross-batch requirement is what makes the *claim* the measured thing: intra-
  session `CONTAINS`/`extractedFrom`/`INPUT` already exist and must not count as the link.
  (⚠️ "member of a connected component of ≥ 2" is **not** a third definition — any incident edge
  already makes a component of two, so that phrasing collapses into the cheap one and is rejected for
  exactly the reason it looks attractive.)
- **A4 — typing is applied.** *Metric:* typed claims by kind, with `statement` and `NULL`/blank beside
  them, over the **claim** class — Points with neither a truthy `is_episodic` nor a truthy
  `is_operator` (TRUTHINESS, because `create_point(is_episodic=False)` really writes the property).
  *Denominator:* **claim** `Point` nodes written by the run (floor **30** typed statements); the
  excluded turn-row and operator-Point populations are counted and reported beside it, because they
  are capture rows and not claims — 159 of the committed run's 354 Points are turn rows, so counting
  them read ≈45% "typed" against the owner's 0.6% ceiling. *Bar:*
  **`UNMEASURED`** — deliberately, and this is a registration of that fact rather than an omission.
  The owner's *"0.6% typed is the ceiling the capstone must beat"* points **opposite** to `D3`'s
  "statement only"; `#10988` (C7) is blocked on that reopen and `#1022` carries the decision, so a bar
  invented here would settle an owner decision this registration has no authority over. The row is
  **reported** above its floor and compared to nothing.
- **A5 — Subjects are populated.** *Metric:* `:Subject` nodes, the **four** subject-edge families
  (`aboutSubject`, `memberOf`, `ownedBy`, `managedBy`) per family, and statements carrying one.
  *Denominator:* the distinct actors the corpus names — `a5_actors`, floor **30** named actors, which
  `#10983` (C2) registers as the corpus's actor list; the `:Subject` count is **not** that denominator
  (`:Subject` nodes are what the run WRITES, so using them would let the bar satisfy itself). Until the
  actor list exists the count is `UNMEASURED` and the row is REFUSED, never scored on the Subject
  count. *Bar:* at least one `:Subject` per distinct actor named, and at least one **statement**
  carrying a subject edge; a family that is zero is **named with its reason**. *Basis:* 26 Subjects against 19,300 points
  in the 2026-11-04 reading, while `#4889` is CLOSED — the bar is the corpus's own actor count, not the
  number **26**, so it cannot be satisfied by writing one Subject per run. `#10987` (C6) owns the
  scoring and the `#4889` verdict.

**Floors, in one place.** `pins.denominator_floors` carries **fourteen** keys, in two
families. **Five are the `E`-rows'**: `e1_objects: 30`, `e1_min_before_share: 0.20`,
`e2b_surviving_units: 30`, `e3_derived_nodes: 30`, `e5_rate_min_sentences: 10`. **Nine are the
`A`-rows'**, and they are not all the same kind of thing. Seven are population count floors —
`a1_objects`, `a2_candidates`, `a2_decided_candidates`, `a3_statements`, `a4_typed_statements`,
`a5_points` and `a5_actors`, all **30** — the `E1` object floor applied uniformly, because a per-row
floor chosen after seeing a population is a floor fitted to its own result. ⚠️ Two of them
(`a2_decided_candidates`, `a5_actors`) are floors under the **rate's** own denominator, not merely
under the population it is drawn from: a floor that guards a population the bar does not divide by
is a floor a one-of-one decision clears. The other two are **not population counts**, which is why
they are not `30`: `a1_rollup_queries: 1` and `a3_event_root_coverage: 0.5` are the second refusal
boundary each of those rows registers, and `#10984`/`#10986` must clear them before their bars are
meaningful (§10.11). They live in the same block on purpose — they were prose-only until `#10982`
round 2, and a child reading the pins missed them, scoring `A1` with zero rollup queries and `A3`
at 0.05 coverage: rows the record REFUSES. Below a floor a row is **REFUSED**, which §3 defines as
neither a pass nor a fail.

**A zero denominator is REFUSED, and it is never `0/0`.** Two bars divide by a population the floor
does not itself guarantee: `A2`'s is **decided** candidates (a run that holds all 30 candidates clears
`a2_candidates` and decides none), and `A5`'s is the **distinct actors the corpus names** (a run can
clear `a5_points` on points that name no actor). Those two denominators are therefore guarded by the
same rule in both directions: a decided-candidate count of zero, or a named-actor count of zero, makes
the row **REFUSED** and the instrument emits `UNMEASURED(<the absent population>)` — never a rate.
The probe already applies exactly this to `A3`'s statement denominator and `A4`'s claim-point
denominator, for the same reason; registering the rule here is what makes it the row's and not the
instrument's private convention.

### 3.8 `E6` — the packs' declared business logic is constructed (`#11673`)

The owner's ruling of 2026-11-05 (`#7949` comment `6664402530`) added a sixth capstone row in these
words: *"the point is that the business logic of the packs should be followed so we're constructing
the packs' business logic of connections as ontologies"*. The row is **per-pack: declared → writable
→ written**. `#11083` built the census for it and closed without registering it, which is the defect
`#11673` fixes: an acceptance criterion the epic names and the registration does not carry has no bar,
so no run could ever be refused against it.

**What it measures.** Two defects in the DECLARATION, both countable and both zero-tolerance:

1. **A declared row with no write path and no explanation.** `pack_registry.relation_write_verdict`
   is the ONE resolver (`#2766`), so a row it reports as `predicate-not-in-allowlist` binds no write
   path at all. That is not automatically a defect — `pm`'s `onBoard` is deliberately retired with
   `declarationOnly: true` and its reason recorded in the pack (a board is a VIEW over cards, not a
   container of them) — so the metric counts a row that is unbound **and** unexplained. Deleting a
   declaration is therefore not a way to satisfy this bar, which is the property `#11083` was asked
   for: the count must not fall by removing the word.
2. **A row that binds a write path but states no direction.** `effective_direction` is where the
   writer learns which way confidence runs (`#2766`, and `#11083`'s `produces` correction: a label can
   carry no direction, a meaning can). A row whose sense is neither declared nor resolved from its
   semantics falls back to a default nobody registered, so an INVERTED meaning reads as plausible.

**What it does NOT measure, and the boundary against `A1`.** `A1` (§3.7) measures what the **written**
graph can answer — `object→object` edges by written relation family, plus rollup queries answered. `E6`
measures the **declaration → write chain** across every declared row in every installed pack. They are
different questions and this row must not restate `A1`: the `written` column of the census is
**reported beside** `E6` and is never its bar, because a bar on it would duplicate `A1` and let one
defect red two rows. `A1`'s own §3.7 bullet states the same boundary from its side (*"**Not `E6`**
(`#11083`)"*). Neither row is evidence for the other: `E6` can be GREEN while `A1` is RED (every
relation writable, no edge ever written by the pipeline), and `A1` can be GREEN while `E6` is RED (the
one written family satisfied by a caller while a declared row binds no path).

**The bar's basis, and why a count and not a ratio.** The **issued baseline** is 32 of 36 declared
relations writable with four unbound (`#7949` comment `6664402530`, 2026-11-05) — all four in
`packs/project-management/`, declared in the legacy `from`/`to` shape with no `mechanism`. ⛔ **What
resolved them is `#11083`'s OWN landing** (`f5c69f7478`, PR `#11543`), not a later writer: that commit
edits `packs/project-management/manifest.yaml` directly — `blocks` / `partOfSprint` / `assignedTo` gain
`mechanism` + `semantics`, `onBoard` gains `declarationOnly: true` with its reason — and
`relation_write_verdict` is purely DECLARATIVE (`tortoise/pack_registry.py`), so `writable` is decided
by that edit alone. `#11237`/`#11548` is the runtime relations **WRITER** (the `written` column, a day
later), which is exactly the column this row forbids as its bar. ⇒ the census reads **42 declared rows,
41 `writable`, 1 `declaration-only` (with its reason), 0 `predicate-not-in-allowlist`** **at
`f5c69f7478` and at this head**, with an `effective_direction` on every row that has a write path. So `E6` is **GREEN on both halves at registration**, and that is stated rather than dressed as a
near miss: the bar is not fitted to a gap, it is the invariant the row exists to keep true — that no
NEW declaration lands unbound or direction-less. A rate over 42 rows would be a second way to report
the same count and would let 41/42 read as a pass.

**The floor, and the false PASS it refuses.** `denominator_floors.e6_declared_rows: 30`. Without a
floor the row is scorable on an empty population: a registry that failed to load its packs reports
`population.declared: 0` and therefore `unbound_and_unexplained: 0` — a GREEN at the top of a row whose
whole subject is missing. The value is `30` because this record applies that floor uniformly (E1's
object floor, and every §3.7 `A`-row floor), not because 30 was fitted to today's 42.

**One instrument, and it already exists.** The census is `tools/pack_relation_census.py`, whose
committed artifact is `docs/evidence/11083/pack-relation-census.json`; it emits every cell this metric
reads per row (`verdict`, `declaration_only`, `declaration_only_reason`, `effective_direction`,
`written`) and derives `population.declared`. So `E6` is a REGISTRATION over an existing instrument,
not a new measurement, and no second census is introduced. ⚠️ The artifact's `verdicts` aggregate is
over `relations[]` only; the metric is over the ROWS of both surfaces, which is why it reads the
per-row cells and the population rather than that aggregate.

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

1. **The six rows carry all three parts.** Every criteria row (E1–E6) names a metric, a denominator
   and a **numeric** threshold. A row with a placeholder — *"to be decided"*, *"TBD"*, an em dash — or
   with any of the three missing **fails**.

   Two additions to that arm, both forced by `#10982` and both stated rather than assumed:

   - **It is no longer E1–E6.** The table carries thirteen rows (§3.1); the arm applies to every row in
     it, not only the six the record requires.
   - **A REGISTERED non-bar is complete, and a floor is not a bar.** A threshold that declares
     `UNMEASURED(<ground>)` is complete **only** when its ground names the issue that gates the row
     (A4 names `#10988`/`#1022`) — and the floor clause beside it (`` … above the floor of
     `>= 30` … ``) may never be what satisfies the numeric arm. A floor is a REFUSAL boundary
     (§3.7), so a row that has no bar must not pass the "has a bar" check on the floor's number.
     `tools/capstone_preregistration_check.py` enforces both halves: it strips the comparator THE
     FLOOR OWNS and refuses any comparator left outside it, and it requires a `#NNNN` reference.
     ⛔ **The exemption is the row's PINNED value AND its own floor unit, and the clause must END
     the cell at that unit** (§10.13): `` floor of `>= 30` typed statements `` is `A4`'s floor,
     while a rate spelled after the same unit (`typed statements per claim`, `…, share`, `… ratio`)
     re-scopes the number and is a BAR, refused exactly as `>= 30 % typed` is. Only closing
     punctuation may follow the unit, to the end of the cell — an allowlist rather than a denylist
     of rate spellings, because a denylist is bypassed by the spelling it does not list.
     ⛔ **The exemption is a CLOSED register, and registering a new bar-less row is a TOOL change.**
     The gate tests membership in `REGISTERED_NON_BAR_ROWS` (a pinned `frozenset({"A4"})`, asserted
     disjoint from the required `E`-rows), not the mere presence of `UNMEASURED(`. So adding a
     sixth bar-less row to §3.1 is **not** a record-only edit under §8: the row must also be added
     to that constant in the same PR, or the gate refuses it — which is deliberate, because the
     branch skips both numeric arms and an unscoped version let a required row declare itself
     bar-less and still read `OK` (`#10982` review round 5). The reason is disclosure, not
     secrecy: a reader who finds the register named here does not have to discover it by failing.
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
   denominator — **each cell on its own, not their union** — to name `Point`, `Event`, and the
   document-derivation class. That last class may be spelled **either** `Document` (the writer's
   retained relation spelling) **or** `Source` (the node label the document is stored on once the
   `Document` label retires), and accepting both is deliberate: **which spelling is honest is
   #9736's owner-gated question, not this rule's**, so the rule refuses a cell that names NEITHER
   and settles nothing. ⛔ **What this rule cannot do is check the instrument.** Nothing in
   `tools/capstone_preregistration_check.py` reads `probes/capstone_measure.py`, and the probe's set
   is derived from the same writer constant (`("Point", *sorted(_derivation_label_classes()))`), so
   it tracks the **writer**, not this record: no sentence written here can move it. Spelling the
   class `Source` is therefore an obligation **on the amendment** — move the probe in the same
   change — and not something §7 item 6 enforces, so reading it as enforcement would be reading a
   check that does not exist. The writer's `Document` spelling is what the record uses today (§10.1),
   so the registration and the probe agree by construction right now. What the gate *does* do is
   **declare** the divergence it can see: a row spelled `Source` carries a `registry arm NOT
   applied`-class declaration naming the probe, so a registration and its instrument can never part
   company silently.
   Today's cost of the gap is zero nodes, and it is disclosed rather than hidden: the third class
   contributes **0 to E3 in either spelling** — no `Document` label is written (post-D10 a document
   is a `:Source`, §10.1), and the probe enumerates no `Source` — so a run that wrote
   `(Source)-[:references]->(Source)` would have its document derivations counted **nowhere** in
   E3. Closing that is part of #9736's ruling; this record does not pre-empt it.
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

7. **The step set is a partition, and it is the one the record declares.** The `steps:` block
   (§11) must split §4.1's eight ids into `unconditional` and `conditional` with **no id missing, none
   in both, none invented**, and `preregistration.json` must restate it byte-identically. Every
   conditional entry must carry a `condition`, the `idle_evidence` a run has to show, and the
   `evidence_issue` that measured it — an entry without them is a waiver with no content. This is the
   rule that stops a step being quietly dropped from the capstone's gate (`#10215`).

8. **A registered class the run measured ZERO nodes of cannot satisfy `= 1.000` (#9736).** E3's row
   is scored by SUMMING its classes, so a class with no nodes contributes `0/0` to BOTH halves and
   vanishes: the row then reports `share = 1.000` over a population NARROWER than the one registered.
   That is reachable on every post-D10 graph, because `:Document` is a retired label (the writer
   remaps it onto `:Source`) and §10.1 records that the run IT re-derives wrote none at all (the
   scored log agrees: `capstone-measured-9152448d.log` carries `e3 label=Document nodes=0`) — a leg
   that `0/0` makes permanently unable to fail. So the probe derives the empty legs from the census
   (`e3_unmeasured_legs`, over `DERIVED_LABELS` and never `:Object`) and a row naming one **cannot
   PASS** — it is `FAIL`, or `REFUSED` where the node floor applies (a too-small population is not a
   verdict either way) — with the leg disclosed in its bar, note and log line. **This decides no
   spelling and moves no threshold**: a row that already failed stays failed (the scored run shipped
   in this bundle, `capstone-measured-9152448d.log`, reads E3 `157/397` and is RED before and after —
   and it is itself the empty-leg shape, carrying `e3 label=Document nodes=0`), a row whose every
   registered class carried nodes is scored exactly as
   before, and whether the honest document class is `Document` or `Source`-as-document — and whether
   that anchor belongs in the denominator at all — remains `#9736`'s owner-gated ruling.
   ⛔ **This item is NOT one the gate can exit 0 on.** `tools/capstone_preregistration_check.py`
   never reads `probes/capstone_measure.py` (it lints registrations, not runs), so a gate exit 0
   says nothing about it. The rule lives in the probe and is pinned where it runs:
   `tests/test_7949_capstone_measure.py`'s empty-leg arms (the pure rule, the census-derived log
   line and row note, and an AST pin that `main` feeds the legs in).

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
criteria: [E1, E1b, E2a, E2b, E3, E4, E5, A1, A2, A3, A4, A5, E6]
required_criteria_rows: [E1, E2, E3, E4, E5, E6]
steps:
  source: "EXTRACTOR-V4-ARCHITECTURE.md §4.1 — the eight top-level steps"
  unconditional: [S0, S1, S2.1, S2.2, S3, S5, S6]
  conditional:
    - step: S2.3
      condition: >-
        the S2 map left at least one candidate's kind missing or `unclassified`, so
        `_collect_classify_items` (tortoise/extractor_v2.py:11803, `def _collect_classify_items`) had an item to hand the classifier;
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
prior_measurements:
  E1: bc547406f     # e1-e2.log, 2026-10-19 — measured, never scored (§0)
  E1b: bc547406f
  E2a: bc547406f
  E2b: bc547406f
  E5: ecc7bc8ca     # EXTRACTOR-V4-ARCHITECTURE.md §10, 2026-10-19 — PREDATES this record (§10.2)
  E6: f5c69f7478    # docs/evidence/11083/pack-relation-census.json — committed 2026-11-08 (#11543), PREDATES
                    # this row (§10.21); the owner's 2026-11-05 baseline (#7949 comment 6664402530) is
                    # quoted as its threshold_basis
e4_denominator: 15
denominator_floors:
  e1_objects: 30
  e1_min_before_share: 0.20
  e2b_surviving_units: 30
  e3_derived_nodes: 30
  e5_rate_min_sentences: 10
  # #10982: the A-rows' floors, all 30 — the E1 object floor applied uniformly, because a per-row
  # floor chosen after seeing its population is a floor fitted to its own result (§3.7).
  a1_objects: 30
  # ⛔ The two A-rows whose cell registers a SECOND refusal boundary that is not a population
  # count. Before this, both lived only in §3.1's prose, so a child reading the pins scored A1 with
  # ZERO rollup queries and A3 at 0.05 event-root coverage — rows the record REFUSES (#10982 review
  # round 2). Every refusal boundary the rows register is pinned here, not only the 30s.
  a1_rollup_queries: 1
  a3_event_root_coverage: 0.5
  a2_candidates: 30
  a2_decided_candidates: 30
  a3_statements: 30
  a4_typed_statements: 30
  a5_points: 30
  a5_actors: 30
  # #11673: E6's floor is a POPULATION floor like the rest, and it is the one that refuses the
  # false PASS at the top of the row — a registry that failed to load its packs reports zero
  # declared rows, and `unbound_and_unexplained = 0` would then read GREEN over an absent subject.
  e6_declared_rows: 30
a1_relation_families:
  - hasPart
a1_relation_family_fold:
  # ⛔ WRITTEN label -> the pack-declared predicates that fold onto it. Both of the dev pack's
  # hierarchy entries (Theme -> Epic, Epic -> Issue) are written `hasPart`; `decomposesInto`,
  # `composedOf`, `contains` and `wraps` are DECLARED spellings the writers fold onto that one
  # label (projection/edges.py:1513, pack_hierarchy.py:215-224). Pinning a declared spelling names
  # a label that occurs zero times on any graph this repository writes (#10982 review round 2).
  hasPart: [hasPart, decomposesInto, composedOf, contains, wraps]
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

**Two provenances are not the same thing, and this table distinguishes them.** A row that names a
file in this tree is **re-derivable**: run the gate and it re-checks the figure. A row that names an
issue comment is a **RECORDED READING** — the basis a threshold was set against, deliberately not
presented as re-derivable here (§0, §10.3). The A-row bases added by `#10982` are the second kind
except where noted; the alternative was to omit the provenance and let a reader assume the first.

| number | re-derived at `3f2f4893` from |
|---|---|
| 62.27% (4,901 / 7,870) | `tortoise/definite_description_backfill.py` docstring; `EXTRACTOR-V4-ARCHITECTURE.md:108` |
| ≈48% delete precision | `tortoise/definite_description_backfill.py:20` |
| 25 / 15 / 9 / 6 / 1 | `tests/eval/write_path/salience_labels.jsonl` + `tests/test_salience_labels_4917.py` predicated run |
| 4 must-keep overlaps | `docs/engineering/1026-calibration-set.jsonl` ∩ `salience_labels.jsonl` |
| 90 units, 15 operator edges, 14 value units | `tests/eval/write_path/gold/*.gold.json` |
| 29 hedge sentences of 343 | `tests/eval/write_path/fixtures/*.json` |
| 90 of 90 m2 baseline | `tests/eval/write_path/baselines/m2.json` at this `fixtures_hash` |
| 53/37, 7/8, 5/9, 21/8 split counts | the per-session sums above, partitioned by §2.1 |
| 9 of 15 shape coverage (60%) | §1.3, `is_positively_bare_definite_description` |
| 7 unconditional / 1 conditional step (§11) | `tortoise/extractor_v2.py:11803-11825` (`_collect_classify_items`, the collector's item set), `:14222` and `:14375` (the two `if items:` gates, one per classify pass) |
| 148 / 433 (0.34180), 148 / 383 (0.38642) | `capstone-measured-e0fc940b.log`'s `E3 census` block: `e3 label=Point nodes=338 anchored=141`, `Object 50/0`, `Event 45/7`, no `Document` written (§10.1) |
| 141/150 (0.940000), 159 turn rows, 29 operator Points | the same census block's `point composition` line — reported, NOT adopted as E3's denominator (§10.1) |
| 5/25 (20%) vs 13/25 (52%) recovered; 0.4800 raw absolute on n=25 | §10 @ `ecc7bc8ca` (run 1 losses 80.0%/48.0%) and `capstone-measured-e0fc940b.log`'s E5 row (§10.2) |
| **5,314 Objects** and `object→object edges []` (A1 basis) | ⚠️ **RECORDED READING** — the owner's 2026-11-04 `live-v4` reading at `#7949` comment `6645275147`; the tree-derivable half is only the vocabulary (`packs/dev/manifest.yaml:142` `decomposesInto`, `:148` `hasPart`) |
| **9,910 / 15,534** statements (63.8%) carrying neither `IMPL` nor `NAND`; 36.2% cheap (A3 basis) | ⚠️ **RECORDED READING** — the same comment |
| **typed 109 (0.6 %)** · `NULL` **3,658 (19 %)** · `statement` **15,534 (80 %)** (A4 basis) | ⚠️ **RECORDED READING** — the same comment's `pointKind` census. This is the figure A4's threshold turns on (`0.6 % typed is the ceiling the capstone must beat`), so a reader auditing that threshold must be able to find its provenance here |
| **26 Subjects** against **19,300 points** (A5 basis) | ⚠️ **RECORDED READING** — the same comment. The PINNED corpus measures a different population: `"Subject": 19` at `capstone-measured-9152448d.log:275`, which §10.11 reports beside it rather than substituting |
| **6,533 / 2,614 / 402** on **7,870** Objects and **27,324** reference edges (A2 basis) | `tortoise/definite_description_backfill.py:15-23` — re-derivable |
| **64** Objects / **19** Subjects / **21** `aboutSubject` (the A-row figures §10.11 discloses) | `capstone-measured-9152448d.log:271` (`"Object": 64`), `:275`, `:299-309` — re-derivable |
| the **nine** A-row floors (§3.7) | `pins.denominator_floors` in this file, cross-checked against `preregistration.json` **both ways** by `tools/capstone_preregistration_check.py` |
| **42** declared rows · **41** `writable` · **1** `declaration-only` · **0** `predicate-not-in-allowlist` · **41 of 42** carrying an `effective_direction` (E6's population and its state at this head) | `docs/evidence/11083/pack-relation-census.json` — re-derivable with `uv run python tools/pack_relation_census.py --write-probe --json`; the `written` family is a GRAPH fact and needs the probe, the declaration half is hermetic. ⚠️ The issued baseline for this row was **32 of 36 writable, 4 unbound** (`#7949` comment `6664402530`, 2026-11-05); **`#11083`'s own landing** (`f5c69f7478`, PR `#11543`) bound those four by editing `packs/project-management/manifest.yaml` — `relation_write_verdict` is declarative — so the figures here are re-measured at the census commit itself, not inherited and not attributable to `#11237` (that PR owns the runtime WRITER, `written`) (§10.21) |
| §1.6.4's censuses — v2 `Object 1 / Point 27 / Event 2 / Session 1 / Operator 0`, m2 `0 / 149 / 1 / 1 / 0` — and the `5.5×` ratio | `tests/test_7949_c2_door.py` (`test_the_negative_control_is_measured_and_the_two_lanes_DISAGREE`), measured on `wp01_quarry_debug` under `TORTOISE_SESSION_LLM_MOCK=1`, one embedded graph per arm |
| 5 of 15 operator edges (§1.6.3, E4) | `tests/eval/write_path/receipts/w2b-operator-2026-10-24-8503-b.json` — the receipt `select_e4_receipt()` selects at the frozen `fixtures_hash` `dd7e8520…` (`operator_audit.edge_correct = 5`, extractor `tortoise.extractor_v2::extract_session_v2`) |

---

## 10. Deviations — the amendments recorded after this registration was written

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

**Ordering, stated once and binding on every entry below.** This registration was added by `a6085af84`
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
construction — `_TURN_WRITE_CYPHER` in `tortoise/sdk.py`) and 29 are **operator Points** (written by
`create_operator`, which never writes `extractedFrom`); 188 of 338 `Point`s are therefore *structurally
incapable* of carrying the anchor this row demands, and no denominator can make `= 1.000` reachable
while they are counted as derivations. The same log computes the writer-class figure — anchored / the
class that is *expected to* derive — at **141/150 = 0.940000**. **This amendment does NOT adopt that
denominator**: `Point` is in the class set by the issue's own resolution, and switching to a
"expected-to-derive" subset would be exactly the re-definition §7 item 6 refuses (it requires every
cell to name **every** anchored class — `Point`, `Event`, and the document class in either
spelling — so a subset fails the rule rather than satisfying it). It is recorded as the
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
replaced by four rows — `E3` (no measurement at registration: the census postdates this record),
`E4` (measured `2026-10-19T01:39:26Z`, its artefact only landed `d50217273` on 2026-10-22, so nothing
was *visible* to this record's author — §10.4), `E5` (measured, post-visible) and — added later by
`#11673` (§10.21), which is why this sentence names four rather than the three this entry shipped
with — `E6` (post-visible: the owner's 2026-11-05 baseline and the committed `#11083` census both
predate the row).

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
than trusted); and E3's metric and denominator — **each cell on its own, not their union** — must name
`Point`, `Event`, and the document-derivation class. That last class may be spelled **either**
`Document` or `Source`, because **which spelling is honest is #9736's owner-gated question and not
this rule's**: the rule refuses a cell that names NEITHER and settles nothing (§7 item 6 carries the
same sentence, deliberately — a reader who arrives here instead of there must not be told a
`Source`-spelled row is refused when the gate accepts it). Mutation-checked in
`tests/test_capstone_preregistration_9075.py`, including the node `#9738` names — flip E5's field back
to `false` and the gate goes red — and the `false`-without-a-ground arm, where deleting E4's basis (or
blanking it to whitespace) reds the same way.

### 10.3 The post-registration measurements — disclosed, NOT re-scored

`capstone-measured-e0fc940b.log` (head `e0fc940b`, generated **2026-10-23T23:16:55Z**, landed by
`260903907`) is the first **scored** run against this registration, and it measures **more than the two
criteria amended above**. Its verdict table reads: `E1 BLOCKED`, `E2 BLOCKED`, `E3 RED 148/433`,
`E4 RED 2/15`, `E5 RED 12/25`. That is disclosed here so no reader concludes from §0's
"no at registration" and "no measurement was *visible* at registration" rows that E3 and E4 remain
unmeasured today — they do not, and the distinction between *"unmeasured when registered"* and
*"unmeasured"* is the whole subject of this section.

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

### 10.5 — the step set: "all eight observed" became "seven required + S2.3 conditional" (2026-11-01, `#10215`)

- **Original rule** (§4.1 as V5/G5 read it): *"ALL eight steps observed in one pass"* —
  `tools/capstone_verdict_check.py`'s V5 and `probes/capstone_run.py`'s G5 both required the eight
  step ids in `steps_observed`, with no state other than observed.
- **New rule** (§11 below, `steps` in `preregistration.json`, the `steps:` pins block): seven steps
  are **unconditional** and must be observed; `S2.3` is **conditional on the candidate set** and must
  be either observed or witnessed **idle** with a basis. The conditional set is read from this record
  at the pre-registration commit; a run log may not declare it.
- **Why it is not a relaxation**: `S2.3` structurally cannot run on a corpus whose S2 map types every
  candidate — `_collect_classify_items` (`tortoise/extractor_v2.py:11803-11825`) collects only
  kind-missing/`unclassified` candidates and both passes sit behind `if items:` (`:14205`, `:14358`).
  The old rule therefore could not be satisfied by **any** legitimate run of the registered corpus,
  so the gate could not return a verdict at all (§11.2's four ordered refusals). Every one of the
  eight still has to be accounted for, and an idle claim needs the run's own four facts — flag ON,
  no activity, **no fail-open marker**, and a positive candidate count.
- **Why it is honest to change it now**: the numbers are unaffected. No E1–E5 threshold, denominator
  or evidence row moves; `capstone.log` keeps its `GREEN=0, RED=0, BLOCKED=3, UNPREREGISTERED=2`
  census, and the epic is still not closeable until five measured rows exist.
- **The claim this entry corrects**: `written_before_any_scored_run: true` describes this record's
  ORIGINAL freeze, and it remains true of it — but the `steps` block is **not** original. It was
  authored while `capstone-measured-e0fc940b.log` (a COMPLETED run carrying E3/E4/E5 rows, added on
  `main` by `260903907`, 2026-10-23) already existed. A blanket pre-scored claim cannot cover content
  added after the number was seen — so the deviation is recorded **here**, and
  `tools/capstone_preregistration_check.py` now closes it mechanically on AUTHORING ORDER rather than
  on an attestation: it dates the commit that first carried the record's CURRENT `steps` block
  (content-compared, because a key that is merely present tells you nothing about when the rule was
  written, and an already-pinned block EDITED after a scored log never moves a key's first
  appearance) and refuses when that commit postdates a scored log while this section is absent or
  empty. Where the checkout is too shallow to establish the order it returns no verdict at all
  rather than falling back to asking the record to assert it.

### 10.6 E3 — a registered class the run measured ZERO nodes of can no longer satisfy `= 1.000` (2026-11-03, `#9736`)

- **Original rule** (§3.1's E3 row as §10.1 left it): the row is scored by **summing** its registered
  classes against `= 1.000`, and the document said nothing about a registered class the run wrote
  **no** nodes of. Such a class contributed `0/0` to both halves, so it vanished from the ratio and
  the row reported `1.000` over a population NARROWER than the one registered.
- **New rule** (§7 item 8): a registered class the run measured **zero** nodes of makes the row
  unable to PASS — `FAIL`, or `REFUSED` where the node floor applies — with the leg named in the
  row's bar, in its note and in the log's `e3 unmeasured_legs=` line. The probe derives the legs from
  its own census (`e3_unmeasured_legs`, over `DERIVED_LABELS` and never `:Object`).
- **Why it is not a re-cut of a bar**: no threshold, no denominator and no class set moves. The
  scored run reads E3 `157/397` and is RED before and after; a row whose every registered class
  carried nodes is scored bit-identically; the change can only add refusals, never remove one. §7's
  hazard is a denominator moved to fit a number, and nothing here moves toward a pass.
- **Why it is honest to record it now**: the false green was MEASURED on the pre-fix probe — a census
  with `Document` and `Event` at zero nodes and `Point` 30/30 anchored reported `PASS` (GREEN,
  `1.000000`) — and the leg it protects is structurally empty on every post-D10 graph, `:Document`
  being a retired label the writer remaps onto `:Source`. `#9736`'s own decision card names this as
  `NOT-A-DECISION(9736-gate-empty-leg, …)`: *"the mechanical fix that would have caught the `Document`
  spelling on its own, and it should land regardless of what you answer."*
- **What it deliberately does NOT decide**: which spelling is the honest document class (`Document`
  vs `Source`-as-document), and whether the document anchor belongs in E3's denominator at all. Both
  stay `#9736`'s **owner-gated** ruling, exactly as §10.1 left them.
- **Where it is enforced, and why not where the card named it**: in the probe, not in
  `tools/capstone_preregistration_check.py`. That tool lints registrations and never runs the probe,
  so it has no path to a measured leg at all; §7 item 8 states this rather than implying a gate
  covers it, and the rule is pinned by the probe's own cases.

### 10.7 — the §11 cite block re-pointed after a 167-line insert above it (2026-11-04, `#10360`)

- **Original value (as this record was last cut):** `_collect_classify_items` at
  `tortoise/extractor_v2.py:9987`, its extent `:9987-10009`, the two `if items:` gates at `:10530`
  and `:11724`, and the two fail-open `except` arms at `:10543`/`:10673`.
- **New value:** the same symbols at `:10020`, `:10169-10191`, `:10736`/`:10868` and
  `:10749`/`:11973`.
- **Why:** these are HEAD-relative cites, and `#10360`'s two changes together insert **167** lines
  into `extractor_v2.py` above every one of them — `21` in `_UNIT_EQUIV` and `12` in `_UNIT_WORDS`
  from the duration-family widen, then `134` more from the fusion-time composition block
  (`_MAGNITUDE_TAIL`/`_NUMERIC_HEAD`/`_compose_magnitude_and_unit`) and the `percent`-non-fusion
  note in `_UNIT_WORDS` — so every cite past them moves by exactly +167 while the symbols are
  unchanged (`10310`→`10477`, `10442`→`10609`, `10323`→`10490`, `10453`→`10620`, `9767`→`9934`).
  ⚠️ The earlier cut of this entry said `+33` and quoted only the two table hunks: that was the
  WIDEN-only arithmetic, measured before the composition half landed in the same tree, and it
  understated the shift by 134 — stated here rather than silently corrected, because a resident
  offset is how this cite block went stale the first time. The numbers themselves were verified by
  CONTENT at the merged head (`_collect_classify_items` at `:10169`, its `return items` at `:10191`,
  the two `if items:` gates at `:10736`/`:10868`, the two fail-open `except` arms at
  `:10749`/`:11973`), never by adding an offset.
- **What moved: nothing else.** No metric, denominator, threshold, class set, corpus pin or `steps`
  block is touched; the numbers this record exists to freeze are byte-identical. `document_sha256`
  is re-cut in this same commit, per §8's rule for *any* edit to this file.

### 10.8 — the §11 cite block re-pointed again after `#10997`'s hold/reclaim insert (2026-11-06, `#10997`)

- **Original value (as §10.7 left it):** `_collect_classify_items` at
  `tortoise/extractor_v2.py:10193`, its extent `:10193-10215`, the two `if items:` gates at
  `:10736`/`:10868`, and the two fail-open `except` arms at `:10749`/`:11973`.
- **New value:** the same symbols at `:10397`, `:10397-10419`, `:11849`/`:11998` and
  `:11862`/`:12009`.
- **Why:** `#10997` inserts **36** lines into `extractor_v2.py` above every one of them — the
  hold/population comment in `execute_embed`, the drain's reclaim sweep before it reads
  `pending()`, and the committed-vs-attempted census on the return. The symbols are unchanged.
  Each number was verified by CONTENT at this head (the `def`, the `return items` that ends it,
  the two `if items:` gates, the two `except` arms), never by adding an offset — the failure this
  section has already recorded twice.
- **What moved: nothing else.** No metric, denominator, threshold, class set, corpus pin or `steps`
  block is touched; the numbers this record exists to freeze are byte-identical. `document_sha256`
  is re-cut in this same commit, per §8's rule for *any* edit to this file.

### 10.9 — the §11 cite block re-pointed after `main`'s S5 inserts moved `extractor_v2.py` (2026-11-07, `#11205`/`#11242`/`#11288` then `#11264`/`#11361`)

- **Original value (as §10.8 left it):** `_collect_classify_items` at
  `tortoise/extractor_v2.py:10397`, its extent `:10397-10419`, the two `if items:` gates at
  `:11849`/`:11998`, and the two fail-open `except` arms at `:11862`/`:12009`.
- **New value:** the same symbols at `:11098`, `:11098-11120`, `:12908`/`:13057` and
  `:12921`/`:13068`.
- **Why:** `main` landed five changes to `extractor_v2.py` while this branch was open, and they
  moved these cites in **two windows** — only the second of which is what this entry's **749**-line
  figure measures. The first window: **`#11205`** (S5 Job 1, the entity↔entity channel; `606`/`4`
  lines), **`#11242`** (`4`/`4`) and **`#11288`** (`22`/`22`) all landed **after §10.8's own commit**
  (`80c912244f`) and are **ancestors of** `04deeaacda`, so they carry the collector from `:10397`
  (as recorded above) to `:10969` at `04deeaacda` — **+572**. The second window: **`#11264`**
  (`482`/`81`) and **`#11361`** (`149`/`37`) are the only commits in `04deeaacda..origin/main` that
  touch `extractor_v2.py`, and they carry it the rest of the way, `:10969` → `:11098` — **+129**.
  Their **631** insertions and **118** deletions are exactly the **749** changed lines quoted here,
  because `04deeaacda..origin/main` is the only range they are measured over.
  ⚠️ The first cut of this entry attributed the 631/118/749 to all five changes ("inserting **631**
  lines and re-cutting **118** above every one of these cites"), which is false for three of them:
  those three are ancestors of `04deeaacda` and appear nowhere in the measured range — they moved
  the cites in the window *before* it, not inside it. Corrected here rather than silently
  restated, in this section's own idiom (§10.7 likewise records an understated shift instead of
  quietly fixing it). ⚠️ Also carried, not silently reconciled: §10.8's recorded values are high by
  a **per-symbol** amount at its own commit `80c912244f` — the `def` and its extent by one line
  (`:10397`/`:10397-10419` against a measured `:10398`/`:10398-10420`), and the four gate/arm cites
  by **seven** (`:11849`/`:11998`/`:11862`/`:12009` against a measured
  `:11856`/`:12005`/`:11869`/`:12016`). Both routes therefore reach the same LINE, `:11098`, at two
  different deltas: +572 then +129 from the recorded `:10397` is **701**, while +571 then +129 from
  the measured `:10398` is **700**. Re-deriving the next move must anchor on the measured `:10398`,
  never on the recorded value plus 700. The symbols are unchanged: the collector
  grew no item and lost none, and both passes are still gated on `if items:`. Each number was
  verified by CONTENT at this head (the `def`, the `return items` that ends it, the two `if items:`
  gates, the two `except` arms), never by adding an offset — the failure this section has already
  recorded twice, and this round it was the whole bundle's cites (README, both probes, the E-row
  audit, `docs/00_index.md` and the architecture doc) that had to be re-derived the same way.
- **What moved: nothing else.** No metric, denominator, threshold, class set, corpus pin or `steps`
  block is touched; the numbers this record exists to freeze are byte-identical. `document_sha256`
  is re-cut in this same commit, per §8's rule for *any* edit to this file.

### 10.10 — the §11 cite block re-pointed after `#11368`'s mention-builder consolidation (2026-11-07, `#11368`)

- **Original value (as §10.9 left it):** `_collect_classify_items` at `:11098`, its extent
  `:11098-11120`, the two `if items:` gates at `:12908`/`:13057`, and the two fail-open `except`
  arms at `:12921`/`:13068`.
- **New value:** the same symbols at `:11098`, `:11098-11120`, `:12982`/`:13131` and
  `:12995`/`:13142`.
- **Why:** `#11368` gave the declared-span verdict ONE home (`_verified_span_source`) and routed
  both mention builders through it, a net **+74** lines (`126` inserted / `52` deleted) confined to
  the mention-builder region (`:12128`–`:12628` — the last hunk is `+12607,22`, so `:12607` is its
  START, and the bound is `:12628`; it must contain BOTH calls the section claims, `:12273` in
  `_mention_record` and `:12624` in `_clamped_mention`). Every cite **below** that region therefore moves
  by exactly `+74` — `:12908`→`:12982`, `:13057`→`:13131`, `:12921`→`:12995`, `:13068`→`:13142` —
  while `_collect_classify_items` (`:11098`), which sits **above** it, does not move at all. The
  `+0` on the collector and the `+74` on the four cites below it are one measurement, and quoting
  only one of them is how the next re-derivation anchors on the wrong number.
- **What moved: nothing else.** No metric, denominator, threshold, class set, corpus pin or `steps`
  block is touched; the numbers this record exists to freeze are byte-identical. `document_sha256`
  is re-cut in this same commit, per §8's rule for *any* edit to this file.

⚠️ §10.8's own **New value** is deliberately left at `:10397`/`:11849`/`:11862` — the state
`#10997` left — because it is the ANCHOR §10.9 re-derives from ("must anchor on the measured
`:10398`, never on the recorded value plus 700"). Rewriting it to a later head's numbers leaves
§10.9 citing an Original value its predecessor never stated, and contradicts §10.8's own stated
insert size. Each §10.N entry freezes ONE move; the moves after it are §10.N+1's to record.
⛔ NOT corrected here, and deliberately so: §10.7's New value gives `_collect_classify_items` as
`:10020` with its extent as `:10169-10191`, while §10.8's Original states `:10193`/`:10193-10215`
— three values for one symbol, and §10.8's "inserts **36** lines" reconciles with none of them
(`:10193`→`:10397` is `+204`). Both entries are `main`'s BYTES, unmodified by this commit; fixing
them in place is exactly what this note forbids, and the correction is tracked as **#11425**
rather than smuggled into a cite re-derivation.

### 10.11 — five `A`-rows added to the criteria table, none of them required (`#10982`, 2026-11-06)

**Old value (verbatim).** The record registered **seven** rows and two places said so:

- the pins fence: `criteria: [E1, E1b, E2a, E2b, E3, E4, E5]`
- §3.1's criteria table: its last row was E5, and the table ran `E1, E1b, E2a, E2b, E3, E4, E5`.

The pins fence also carried **no** `a1_relation_families` and **no** A-row floor of any kind.

**New value.** Both now name **twelve**: the same seven, plus `A1, A2, A3, A4, A5` — appended as
five §3.1 rows and as five `criteria[]` entries in `preregistration.json`, with their metric,
denominator, threshold and threshold basis set out in prose in the new **§3.7**. **Nine** floors are
pinned, and the two that are not population counts are called out because they are the ones a reader
of a list of `30`s would miss: `denominator_floors.a1_objects` / `a2_candidates` /
`a2_decided_candidates` / `a3_statements` / `a4_typed_statements` / `a5_points` / `a5_actors`, all
**30**, plus `a1_rollup_queries: 1` and `a3_event_root_coverage: 0.50` — the two REFUSAL boundaries
the `A1` and `A3` cells register that are not counts of a population. Of the seven `30`s,
`a2_decided_candidates` and `a5_actors` sit under the **rate's own denominator**, not merely under
the population it is drawn from (§3.7), which is why they are listed here by name.

**`required_criteria_rows` is UNCHANGED at `[E1, E2, E3, E4, E5]`, and that is the load-bearing half
of this entry.** Adding a row to a registration is not the same act as adding a row to the gate: a
required row cannot be inferred from the rest, so making `A1`–`A5` required would red **every existing
verdict table**, including the committed `capstone-measured-9152448d.log`, for rows nobody has run yet.
The `A`-rows are therefore **registered, reported and scored by their own children**
(`#10984` `A1`, `#10985` `A2`, `#10986` `A3`, `#10988` `A4`, `#10987` `A5`), and the gate's V2 accepts
a table that carries them without demanding them — **and treats a duplicated one exactly as it treats
a duplicated `E`-row** (V2's "decided once, not voted on" now runs over `RECOGNISED_CRITERIA`, so an
`A`-row cannot be decided twice while the rule silently applied only to `E`).

**Why an amendment and not a second registration.** The record's corpus pin is a single
`fixtures_hash`, its checker parses one criteria table and one machine record, and its
`measurement_visible_to_author` disclosure is per-row. A second registration would duplicate the corpus
pin, let two rows disagree about the same number, and split the gate — the exact failure the decompose
gate caught in this child's first version (§ `#10982` body). §3.7 is therefore an **extension** of the
pinned record.

**Why it is honest to record it now, and what it does NOT claim.** This is the first amendment in this
section that adds rows **after a scored log exists** (`capstone-measured-9152448d.log`, 2026-11-04). It
is filed under §8's protocol-deviation rule for that reason — and it is safe to file under it because
**no existing row moves**: no `E`-row's metric, denominator, threshold, class set or floor changes, and
no committed verdict is re-scored.

**⛔ The disclosure, corrected (`#10982` review round 2).** An earlier draft of this section claimed
`measurement_visible_to_author` was `false` for all five "on the only ground that field recognises —
no `A`-row has been measured, so there is nothing in `prior_measurements` that could contradict it".
That ground was the **gate's blind spot, not a fact about history**, and it is the shape §10.3/§10.4
exist to refuse. The field is a claim about **visibility at the registration commit `a6085af84`**
(§10.3), and on that anchor `false` is correct for all five — every reading of these metrics postdates
`a6085af84`, exactly as E3's stays `false` even though the amending author had seen its FAIL. But the
number WAS in front of the bar's author, and pretending otherwise is what the field is for:

- the owner's 2026-11-04 `live-v4` hand reading is quoted as the **threshold basis** of `A1`, `A3`,
  `A4` and `A5` in this very record; and
- on the **pinned corpus**, `capstone-measured-9152448d.log` — committed `31f996f91` on 2026-11-04,
  **in this directory**, two days BEFORE the rows were written — carries A1's object population
  (`"Object": 64`), a **complete** edge-type enumeration (`MATCH ()-[r]->() RETURN type(r)`, so an
  absent type is 0 rather than uncounted) and A5's `"Subject": 19` beside `"aboutSubject": 21`.

So §0's disclosure table now carries the `A`-rows, and the four rows whose bars were written with a
figure in view are stated as **post-visible** with that discount applied — not as "no measurement
exists". A2 is the one genuine absence: no S3 RESOLVE attach/mint census has ever been taken.

**⛔ A1's pinned relation families, corrected (`#10982` review round 2).** The first draft pinned
`a1_relation_families: [decomposesInto, hasPart]` — the pack's two **declared hierarchy predicates**.
`decomposesInto` is never a relationship **label**: both hierarchy entries are written `type: hasPart`,
and the OPERATOR writers fold `composedOf`/`decomposesInto`/`contains`/`wraps` onto that same label
(`tortoise/projection/edges.py:1513`, `tortoise/record_projection.py:4771-4779`) while the hierarchy seam writes the
entry's `type` and REFUSES a `decomposesInto` entry
(`tortoise/pack_hierarchy.py:215-224`: *"the edge this function would write carries `decomposesInto`,
which `create_edge` refuses"*); `create_edge`'s own predicate set carries `hasPart` and not
`decomposesInto` (`edges.py:621-632`). A pin on the declared
spelling therefore named a label that occurs zero times on every graph this repository can write, and
the registered bar — *">= 1 edge for EACH of the two families"* — was **unsatisfiable**: `#10984` (C3)
would have scored `A1` RED forever on a correct product. The pin is now the ONE written family
(`hasPart`), with `a1_relation_family_fold` naming the declared spellings that fold onto it, and the
`>= 1` bar and the rest of the row are unchanged.

**What enforces it.** `tools/capstone_preregistration_check.py` — the amended record must pass it, which
means the five new §3.1 cells and the five `criteria[]` entries agree character for character, the
floors agree between pins and record in both directions, a threshold that declares `UNMEASURED(...)`
names an issue (`#NNNN`) gating the row rather than borrowing a number from its own floor clause
(§7 item 1 — the check is "names an issue", it is **not** "names the registered decision" that
decides the row), the `UNMEASURED(...)` exemption is a **closed register** in the tool
(`REGISTERED_NON_BAR_ROWS`), so registering a further bar-less row is a tool change and not a
record-only edit (§7 item 1), and `document_sha256` is re-cut in this same commit (§8). `tools/capstone_verdict_check.py` — a table that
carries an `A`-row is checked by the same `V1`–`V12` rules as an `E`-row, including `V12`'s refusal of
a row whose `blocked_by` has **CLOSED**.

### 10.12 — the `A`-corpus qualifier retired, because `#10983` (C2) landed (`#11331`, 2026-11-07)

**Old value (verbatim).** `preregistration.json`'s `a_corpus.status` read
`"PROVISIONAL — #10983 (C2) owns the reconciliation"`, `a_corpus.requires` ended *"Until `#10983`
lands a session exercising both, a row whose population is absent is REFUSED…"*, and §3.7's corpus
paragraph said *"That pin is **provisional** … `#10983` (C2) owns the reconciliation, and until it
lands a row whose population is absent is **REFUSED**"*. `a_corpus` carried **no** `not_a_proof_of`.

**New value.** `status` reads **`RECONCILED by #10983 (C2), landed as #11298`**; the `#10983`-pending
conditional is replaced by the requirement itself, which is a statement about the **corpus's content**
and survives C2's close unreduced; `a_corpus` gains a **`not_a_proof_of`** naming the 2026-11-04
`live-v4` figures the four `A`-rows' `threshold_basis` quotes (`A1`, `A3`, `A4`, `A5`; `A2`'s basis is
`tortoise/definite_description_backfill.py:15-23`, tree-derivable). §3.7's paragraph is rewritten to match.

**Why (the reason this is not a cosmetic edit).** `#11298` landed C2 while this record was being
amended, so the two halves of the merged record each carried a claim about the other: the `A`-rows said
C2 owned an **open** reconciliation, and C2's **§1.6.3** said *"**`fleet-capture`** — a real capture
through the door … this registration pins no fleet-capture corpus, and one must be registered
**before** such a number is scored"*. A record still pointing at C2 would be **false on its face**,
and worse it left the `A`-rows silently exempt from the boundary C2 had just drawn: every `A`-row's bar
is justified by a **fleet** reading (`object→object edges []` against 5,314 Objects; 9,910 of 15,534
statements; 0.6 % typed; 26 Subjects against 19,300 points) while the corpus that will score them is
the **fixture**. That is exactly the shape §1.6.3 exists to make unreadable — a green `A`-row cited as
if it were a measurement of the fleet graph it was written to reverse.

⛔ **No value moves.** No metric, denominator, threshold, relation family, floor or `UNMEASURED(...)`
register changes; `required_criteria_rows` stays `[E1, E2, E3, E4, E5]`; no committed verdict is
re-scored. `document_sha256` is re-cut from the resolved document (§8), never picked from a side.

### 10.13 — the `A4` floor clause is exempted as a WHOLE unit phrase that ENDS the cell (`#11331`, 2026-11-07)

**Old value (verbatim).** §3.1's `A4` threshold cell (and its `criteria[A4].threshold` restatement in
`preregistration.json`, which agrees character for character) ended:

> until that is ruled the row is REPORTED above the floor of `` `>= 30` `` typed statements and compared to no bar)

**New value.** The same sentence, with the floor clause **moved to the end of the cell** so nothing
follows its unit but the closing parenthesis:

> until that is ruled the row is REPORTED and compared to no bar, above the floor of `` `>= 30` `` typed statements)

**Why (the reason this is not a cosmetic edit).** The gate exempts a floor clause from §7 item 1's
"a registered non-bar cannot also be a bar" arm, and round 10 bound that exemption to the row's
**pinned value AND its floor unit** — but on a **prefix** of the unit. So a RATE spelled *after* the
unit walked through the other side of the same hole that `>= 30 % typed` walked through before it:
measured at `#11331` review round 11, `` floor of `>= 30` typed statements per claim, and compared to
no bar `` — and the same cell with `share` or `ratio` in place of `per claim` — left the gate at
**`exit=0` / `OK`** with `criteria[A4].floor` untouched, while the identical cell with `%` after the
number was refused. `typed statements per claim` is the ordinary way to write a rate whose denominator
is a claim, and it is exactly the `A4`-vs-`D3` bar `#10988`/`#1022` have **not** ruled on, so the
exemption was settling an owner-gated decision by prose.

The tool now requires the clause to **END** the cell at the row's own floor unit: after that unit only
closing punctuation is accepted. That is an **allowlist**, not a denylist of rate spellings, because a
denylist is bypassed by the spelling it does not list — the same reason OWASP's input validation
control names allowlisting as the required minimum — and because a delimiter is not a licence either:
`typed statements, per claim` is refused exactly as `typed statements per claim` is. It is also the
fail-**closed** direction: over-blocking costs a re-worded cell (what this entry records), while
failing open settles an owner-gated decision. The `A4` cell is re-worded accordingly — floor clause
last — so the pinned artifact stays green under the stricter rule.

⛔ **No value moves.** No metric, denominator, threshold NUMBER, relation family, floor or
`UNMEASURED(...)` register changes: the cell is re-ordered and nothing else, `document_sha256` is
re-cut from the resolved document (§8), and no committed verdict is re-scored.

### 10.14 — the §11 cite block re-pointed after `#11449`'s state-value insert moved `extractor_v2.py` (2026-11-08, `#11449`)

- **Original value (as §10.10 left it):** `_collect_classify_items` at `:11098`, its extent
  `:11098-11120`, the two `if items:` gates at `:12982`/`:13131`, and the two fail-open `except`
  arms at `:12995`/`:13142`.
- **New value:** the same symbols at `:11174`, `:11174-11196`, `:13058`/`:13207` and
  `:13071`/`:13218`.
- **Why:** between `5c866513c` (the head §10.10 was written at) and `origin/main` `5ef5805f0`, the
  ONLY commit touching `extractor_v2.py` is `91dbc9a88` (`#11449`, **+80/−4**, net **+76**), whose
  hunks START at `:5190`, `:5245`, `:5275`, `:5285`, `:9923` and `:9943` (the first group's added
  lines run to `:5288`). The gross is the DEFAULT-CONTEXT view (`git show --numstat`); a `-U0`
  recount reports `+79/−3`, because the one `"""` line this commit RELOCATES (its bytes are
  unchanged) is rendered as a delete+add pair only when context is included. Same net `+76`, and the 68/76 split below is
  identical under either view.
  ⛔ **Two deltas from one commit, and the table states both** — this is why a blanket `+76` is a
  wrong re-derivation for part of the block: the first four hunks contribute **68** NET lines
  (`+54`, `+2−1`, `+9`, `+4` — the second is a two-for-one replacement) above `:5286`, so the #2684 `_carries_value` pair moves `:5286-5287` → `:5354-5355`
  (**+68**), while everything at or below the collector sees all six hunks and moves **+76**:
  `:11098`→`:11174`, `:11098-11120`→`:11174-11196` (the extent read out of `ast`, `def` through
  `return items`), `:12982`→`:13058`, `:13131`→`:13207`, `:12995`→`:13071`, `:13142`→`:13218`.
  Every one of those is verified against the LINE it names, by content, not by arithmetic.
- **Also re-derived here, never moved by a §10 entry before:** the step table's `search_graph`
  (`:13000` → `:13076`) and `run_s4` (`:13021` → `:13097`) cites, and the `_IDLE_CITES`
  `classify_later` cause's `classify_later = False` site (`:12847` → `:12923`). The first two named
  lines that are not the code their sentence claims (`:13000` is `run_s2`'s call; `:13021` is a
  comment at the call site inside `extract_session_v2` — `_run_vet_pass` itself is `:12774-12811`);
  the third was the only cause line the +76 had not reached.
- **The `steps:` block's S2.3 `condition` carries this same cite, and it is re-pointed with it.**
  That block is operative text, so its CONTENT EPOCH (`_steps_block_epoch`) moves with this commit;
  what moves is a line NUMBER in the condition's provenance, not the condition, the step set, a
  metric, a denominator or a threshold — the operative rule is unchanged. It is recorded as a §10.14
  ENTRY in §10's own deviations list (a new numbered entry, not a struck-through edit to the operative
  rows). The gate's arm is DOCUMENT-level, not entry-level (`check_steps_block_is_pre_scored` refuses a
  `steps` block that post-dates the EARLIEST scored log — the gate takes `min(log_epochs)` — and only
  when `_document_has_deviations(directory)` is false), and §10.1–§10.13 already clear it — so what this entry does is record THIS move, not
  single-handedly keep the epoch legible. Stating it the stronger way would have been an
  over-attribution: measured with §10.14 itself removed from the text, the arm still returns no
  verdict.
- **The union also pins one switch the base was red on.** `91dbc9a88` introduced
  `TORTOISE_STATE_VALUE_VERBATIM`, resolved `env_flag(SWITCH, True)` in
  `extractor_v2._state_value_verbatim_enforced` — the ONE module that reads it; the nine
  modules the door's closure test names are the capture path it SCANNED, not the readers.
  Its ABSENT state is not the neutral one to inherit (an ambient `=0` disarms the write
  path's verbatim-value refusal), so it is pinned to the shipped default in the probe's
  `DOOR_PINNED_ON` and in this record's `door.acceptance_arm.pinned_to` — the door's C2 closure
  otherwise refuses the run (`test_every_env_var_the_capture_path_reads_is_accounted_for`).
- **§10.5 is deliberately LEFT FROZEN, and unchanged by this commit.** Its prose still cites
  `:11098-11120` and the gates `:12982`/`:13131` — the values it recorded for its own move. Each
  §10.N freezes ONE move (see §10.10's tail), so re-pointing it here would make one entry describe
  two heads; the fact that no pin reads a §10 entry is a convenience, not a licence. Where a §10
  entry is BELIEVED wrong about an earlier move, the correction belongs to **#11425**'s set, not to
  this re-derivation.
- **What moved: nothing else.** No metric, denominator, threshold, class set or corpus pin is
  touched: the values this record freezes are unchanged and only line citations move.
  `document_sha256` is re-cut in this same commit, per §8's rule for *any* edit to this file.

### 10.15 — the §11 cite block re-pointed for `#11002` (2026-11-08, `#11002`)

- **Baseline — measured at THIS branch's base, `f76b74c3e`:** `_collect_classify_items` at
  `:11174`, its extent `:11174-11196`, the two `if items:` gates at `:13058`/`:13207`, and the two
  fail-open `except` arms at `:13071`/`:13218`.
- **New value — measured at this head:** the same symbols at `:11402`, `:11402-11424`,
  `:13101`/`:13250` and `:13114`/`:13261`.
- **Why the baseline is NOT §10.10's `:11098`.** §10.9 and §10.10 record the moves `#11264`/`#11361`
  and `#11368` made, and they are left **byte-identical to `main`** here — they are history, and
  editing their "New value" for a later change is what the note below forbids. The drift between
  them and this branch's base is `main`'s own (`:11098` → `:11174`, **+76**), and it is owned by
  `main`'s own §10.14 (`#11449`'s state-value insert) rather than by this entry — each §10.N freezes
  ONE move, so §10.15 states the baseline it actually landed on (`:11174`) instead of re-describing
  a move another entry already owns under a second number.
- **The deltas are measured, per symbol, and there are two of them.** `#11002`'s insertions are not
  all below the collector — its G5 record at the module head, its `run_s1` `emphasis` seam and its
  review-round repairs all sit **above** it — so the collector moves **+15** while the four cites
  below it move **+43** each (`:13058`→`:13101`, `:13207`→`:13250`, `:13071`→`:13114`,
  `:13218`→`:13261`). The four are equal because they all sit below every hunk; the difference from
  the collector's +15 is the pipeline hunks between them. The collector's extent is unchanged in
  size (23 lines, `def` to `return items`).
- **Verified by content, not by arithmetic.** Each head line was opened and checked: `:11402` is
  `def _collect_classify_items`, `:13101`/`:13250` are the two `if items:` gates (one per classify
  pass), `:13114`/`:13261` are the two fail-open `except` arms whose messages name
  `classify(S2) failed` / `classify(union) failed`. §11.1 and §11.3 carry the same four gates/arms
  and were cross-checked against them, so this record and the §11 block no longer disagree.
- **What moved: nothing else.** No metric, denominator, threshold, class set, corpus pin or `steps`
  block is touched; the numbers this record exists to freeze are byte-identical. `document_sha256`
  is re-cut in this same commit, per §8's rule for *any* edit to this file.

### 10.16 — the §11 cite block re-pointed again for `#11002`'s review fix (2026-11-08, `#11002`)

- **Baseline — measured at this branch's head before the fix (`ec7b897d`, the `origin/main` merge):**
  `_collect_classify_items` at `:11402`, its extent `:11402-11424`, the two `if items:` gates at
  `:13101`/`:13250`, and the two fail-open `except` arms at `:13114`/`:13261`.
- **New value — measured at this head:** the same symbols at `:11402`, `:11402-11424`,
  `:13706`/`:13859` and `:13719`/`:13870`.
- **Why this is NOT §10.15's +43, and why the collector does not move.** The review fix behind this
  entry (keeping a first-arm DISCARD's verdict on the retried path) inserts **six** lines ABOVE the
  two classify gates — a `decisions` key and its comment inside `renarrate_stats`, in
  `extract_session_v2`'s retry block — so every cite BELOW that insert moves **+6**: the two
  `if items:` gates and both fail-open `except` arms. The collector sits ABOVE the insert, so its
  `:11402` is unchanged and §10.15's +15 reading of it still stands. Two deltas from two commits is
  the same shape §10.15 records for its own move.
- **Verified by content, not by arithmetic.** `:13706`/`:13859` are the two `if items:` gates (one per
  classify pass) and `:13719`/`:13870` are the two fail-open `except` arms whose messages name
  `classify(S2) failed` / `classify(union) failed`. §11.1 and §11.3 carry the same four and were
  cross-checked against them.
- **What moved: nothing else.** No metric, denominator, threshold, class set or corpus pin is
  touched; the values this record freezes are byte-identical. `document_sha256` is re-cut in this
  same commit, per §8's rule for *any* edit to this file.

### 10.17 — the §11 cite block re-pointed for `#11569`'s fail-open guard (2026-11-08, `#11569`)

- **Baseline — measured at this branch's head before the guard:** the two `if items:` gates at
  `:13107`/`:13256`, the two fail-open `except` arms at `:13120`/`:13267`, the two result blocks at
  `:13515`/`:13525`, and `_run_vet_pass`'s call at `:15542`.
- **New value — measured at this head:** `:13177`/`:13326`, `:13190`/`:13337`, `:13515`/`:13525`,
  and `:15612`.
- **Why every number moves by the SAME delta.** The change wraps `extract_session_v2`'s S2.2
  re-narrate block in a fail-open `try/except`: the `try:` line above the block, the block's own
  62 lines re-indented (a column change, not a content change — all 62 pair 1:1 with new
  lines of identical stripped text, measured), and the handler below it. The block therefore grows
  by exactly **+70** lines, so every cite BELOW it moves +70 and every cite
  ABOVE it (`_collect_classify_items` at `:11189`, its extent `:11189-11211`) is untouched — so
  §10.15's and §10.16's readings of the collector still stand.
- **The delta is a property of the final diff, not a constant.** The first cut of this guard was
  39 lines shorter and the review that followed added a pre-bound `emphasis`, a
  `vet_warnings` truncation mark, a per-arm `chunks` block and an `errors`/census record — which is
  31 more lines. Every cite re-derived against the shorter guard was stale by exactly
  that difference, which is why this entry states the delta it was measured at rather than a rule.
- **Verified by content, not by arithmetic.** Each re-pointed number was read out of the tree and
  compared with the symbol the document names: `:13177`/`:13326` are the two `if items:` classify
  gates, `:13190`/`:13337` the two fail-open `except` arms, `:13515` `result["classify_later"] = {`,
  `:13525` `result["vet"] = {`, `:15612` the `= _run_vet_pass(` call.
- **What moved: nothing else.** No metric, denominator, threshold, class set or corpus pin is
  touched. `document_sha256` is re-cut in this same commit, per §8's rule for *any* edit to this
  file — which is why a cite move changes the digest even though no frozen value changed.
- ⛔ **Superseded for the merged tree by §10.18.** The numbers above are `#11569`'s own head;
  `#11534`'s merge moved every cite below that block again.

### 10.18 — the §11 cite block re-pointed for `#11534`'s S2.2b-ADD merge (2026-11-09, `#11534`)

- **Baseline — measured on `origin/main` before the merge (`7f0b3bc84`):** the two `if items:` gates
  at `:13177`/`:13326`, the two fail-open `except` arms at `:13190`/`:13337`, the two result blocks at
  `:13515`/`:13525`, and `_run_vet_pass`'s S2.2 call at `:15612` — exactly the "new value" §10.17
  measured for `#11569`.
- **New value — measured at this head (the `#11534` merge):** the same symbols at `:13706`/`:13859`
  (the two `if items:` gates), `:13719`/`:13870` (the two fail-open `except` arms), the two result
  blocks at `:14048` (closing `:14054`) and `:14058` (closing `:14062`), and `_run_vet_pass`'s S2.2
  call at `:16229` (the post-S4 union call at `:13835`). `_collect_classify_items` and its extent do
  not move: `:11402`, `:11402-11424`.
- **Why the delta is NOT constant.** `#11534` inserts at several distinct points below §10.17's
  numbers — the S2.2b-ADD gap-fill stage between S2.2 and classify(S2), the
  `_s4_malformed_sections`/`_unknown_delta_sections` guards on its merged output, and the stage's own
  module-level helpers — so the two gates move +529 and +533 while `_run_vet_pass`'s S2.2 call moves
  +617. Every number in this entry was re-derived by content, not by adding a delta to §10.17.
- **Verified by content, not by arithmetic.** `:13706`/`:13859` are the two `if items:` gates (one
  per classify pass), `:13719`/`:13870` are the two fail-open `except` arms whose messages name
  `classify(S2) failed` / `classify(union) failed`, `:14048`/`:14058` open `result["classify_later"]`
  and `result["vet"]` and close at `:14054`/`:14062`, and `:16229` is
  `embed_list, vet_s2_stats, _w, vet_s2_pool = _run_vet_pass(`.
- **What moved: nothing else.** No metric, denominator, threshold, class set or corpus pin is
  touched. `document_sha256` is re-cut in this same commit, per §8's rule for *any* edit to this file.
- ⛔ **Superseded for the merged tree by §10.19.** The numbers above are the `#11534` merge's head
  BEFORE `_gap_fill_collisions` landed; that counter inserts 57 lines above every one of them, so
  this entry's "measured at this head" is a claim about a tree two commits behind the shipped one.

### 10.19 — the §11 cite block re-pointed for `#11534`'s `_gap_fill_collisions` (2026-11-09, `#11534`)

- **Baseline — measured at `25e1c71173cb`, this branch's head before the collision counter:** the two
  `if items:` gates at `:13706`/`:13859`, the two fail-open `except` arms at `:13719`/`:13870`, the
  two result blocks at `:14048` (closing `:14054`) and `:14058` (closing `:14062`), and
  `_run_vet_pass`'s S2.2 call at `:16229` (the post-S4 union call at `:13835`) — exactly the "new
  value" §10.18 measured.
- **New value — measured at this head:** the same symbols at `:13763`/`:13916` (the two `if items:`
  gates), `:13776`/`:13927` (the two fail-open `except` arms), the two result blocks at `:14105`
  (closing `:14111`) and `:14115` (closing `:14119`), and `_run_vet_pass`'s S2.2 call at `:16286`
  (the post-S4 union call at `:13892`). `_collect_classify_items` and its extent do not move:
  `:11402`, `:11402-11424`.
- **Why this delta IS a constant, and why a constant is the shape that rots unnoticed.** The
  net-vs-gross counter (`_gap_fill_collisions` and its call site) is ONE pure insertion of **57**
  lines — 44 after `:13147`, 3 after `:13644`, 10 after `:13663` — every one of them ABOVE this
  block, so all ten numbers move by the same +57 while the collector above the insert stands.
  §10.15 and §10.17 both warn that the delta is a property of the diff and not a rule; the warning
  is what was violated here, because a uniform +57 is exactly the arithmetic a re-derivation is
  tempted to trust. It is used below only after the insertion was verified as a PURE SHIFT
  (`old[L] == new[L + 57]` for all 17,621 pre-insert lines, 0 mismatches) and only as a check on
  numbers re-read out of the tree.
- **Why §10.18 needed a successor when §10.1–§10.17 did not.** Since §10.15 this section's rule has
  been that an entry records numbers measured at the head it names, and that re-numbering a history
  entry would be a false record of what was measured. §10.18 is the one entry that rule does not
  reach: its heading and its first bullet claim the SHIPPED tree ("re-pointed for `#11534`'s S2.2b-ADD
  merge", "measured at this head (the `#11534` merge)"), so leaving its numbers two commits behind
  does not preserve a measurement — it publishes a claim about this tree that this tree refutes, in
  the same document whose §11 and whose `README.md` carry the correct ones. The settled handling of
  exactly this move is the one §10.17→§10.18 used: a new dated entry, and the old one marked
  superseded. (The field convention this mirrors — a registration is frozen at submission and a later
  change is a separate dated amendment, never a silent edit — is what `document_sha256` exists to
  enforce.)
- **Verified by content, not by arithmetic.** `:13763`/`:13916` are the two `if items:` gates (one per
  classify pass), `:13776`/`:13927` are the two fail-open `except` arms whose messages name
  `classify(S2) failed` / `classify(union) failed`, `:14105`/`:14115` open
  `result["classify_later"]` and `result["vet"]` and close at `:14111`/`:14119`, and `:16286` is
  `embed_list, vet_s2_stats, _w, vet_s2_pool = _run_vet_pass(`. A pin now reads the NEWEST entry
  against the tree rather than only the live prose, so the next insertion cannot leave this block
  behind silently (`tests/test_7949_capstone_measure.py`,
  `test_the_newest_change_control_entry_describes_the_shipped_tree`).
- **What moved: nothing else.** No metric, denominator, threshold, class set or corpus pin is
  touched. `document_sha256` is re-cut in this same commit, per §8's rule for *any* edit to this file.
- ⛔ **Superseded for the merged tree by §10.24.** The numbers above are the head this entry
  names, and they are byte-identical to `main`'s copy of that entry — a history entry is not
  re-pointed in place (this entry's own warning, and the handling §10.17→§10.18 used). The inserts
  above every one of them total **+401** lines by this head — `_collect_classify_items` measures
  `:11402` at the merge base, `:11510` on `main` (`#11654`'s +108) and `:11803` here — so the live
  values are §10.24's (`§10.23`'s own block is a record of the `#11654` forward-merge head, and
  `§10.24` is the entry that claims THIS tree).

⛔ **10.20 is superseded for the shipped tree by §10.23** (the live values for what §10.20 pins — the `:10508` read-back — which §10.23 re-derived; §10.22 records the `#11654` head it measured).

### 10.20 — the merge of `#11523`, and the door-read pin re-pointed for THIS tree (2026-11-09, `#11534`)

- **Baseline — measured at `origin/main`'s head when it merged `#11523` (`8212c41eff`):** the
  switch's read-back that the door's acceptance arm quotes, `_state_value_verbatim_enforced`
  assigned to `_value_fidelity_judged`, at `extractor_v2.py:9944`.
- **New value — measured at this head (the `#11523` merge):** the same assignment at `:10157`.
- **Why the delta is NOT main's.** `#11523` moved no line of `extractor_v2.py`; the +213 is this
  branch's own inserts above that line (the `#11002` G5 record at the module head and the
  `run_s1` emphasis seam — the same inserts §10.15 records as **+15** for the collector, which
  sits below them and therefore moves by the smaller delta). The number is re-derived HERE rather
  than inherited from main's record, which is correct on main and wrong on this branch.
- **Also fixed in this same merge, because the merge re-created it.** `config/ci-surfaces.yml`
  is merged with `merge=union` (`.gitattributes`), so the `#11523` merge produced TWO
  `durations` rows for `tests/test_7949_capstone_measure.py` — the same silent-duplicate shape
  `#11523` had just fixed on main. Collapsed to ONE row: main's CI-measured weight `4.1` with its
  `2026-11-09` attribution, carrying this tree's case count, **231** — the merge base's 229 plus
  `#11523`'s cross-file cite pin plus this branch's change-control pin — which is what
  `grep -c '^def test_'` reads here. Both `tools/ci_selection.py --integrity` and
  `tools/registry_integrity.py` refuse a duplicate key, and both are required CI steps.
- **What moved: nothing else.** No metric, denominator, threshold, class set or corpus pin is
  touched; `document_sha256` is re-cut in this same merge, per §8's rule for *any* edit to this
  file.

- ⛔ **Superseded for the merged tree by §10.23.** The numbers above are the `#11523` merge's
  head. At this head, the switch's read-back this entry pins —
  `_state_value_verbatim_enforced` assigned to `_value_fidelity_judged` — sits at `:10508`,
  which `preregistration.json`'s `read_back` already carries.

### 10.21 — the sixth row: `E6` is registered as a REQUIRED capstone criterion (`#11673`, 2026-11-10)

**What was added, and on what authority.** The owner ruled on 2026-11-05 (`#7949` comment
`6664402530`) that the capstone gains a sixth row — *"we can add a sixth row, the point is that the
business logic of the packs should be followed so we're constructing the packs' business logic of
connections as ontologies"* — defined as per-pack **declared → writable → written**. `#11083` built
the census for it and closed **without registering the row**, so the epic's own acceptance criterion
had no bar: any run could report whatever it liked about the packs' declared logic and nothing could
refuse it. `#11673` registers it here, as a **re-registration in the `#9736` class** (a new id added
in the open, with its own before-arm and its own disclosure), never as an edit to a registered row.

**The superseded pins, quoted rather than deleted.**

```yaml
criteria: [E1, E1b, E2a, E2b, E3, E4, E5, A1, A2, A3, A4, A5]
required_criteria_rows: [E1, E2, E3, E4, E5]
```

…and this entry's replacement, which is what the pins fence at §7 now carries:

```yaml
criteria: [E1, E1b, E2a, E2b, E3, E4, E5, A1, A2, A3, A4, A5, E6]
required_criteria_rows: [E1, E2, E3, E4, E5, E6]
```

**No existing row moves.** `E1`–`E5`, `E1b`, `E2a`, `E2b` and `A1`–`A5` keep their metrics,
denominators, thresholds, class sets and floors untouched; the corpus pin, the split, the salience
pins and the step set are unchanged; no committed verdict is re-scored and no `*.log` is added or
touched. `E6` is therefore the only new row and the only new floor.

**Why it is REQUIRED, and what that does not change.** A required row is a row the RECORD may not be
silent about: `tools/capstone_preregistration_check.py`'s `REQUIRED_CRITERIA` gains `E6` in this same
change, so a registration without it is refused. That is the whole of what "required" means here, and
the boundary is worth stating because the two neighbouring sets are deliberately different:

- `REGISTERED_NON_BAR_ROWS` stays `frozenset({"A4"})`. `E6` is **not** bar-less — it carries
  `unbound_and_unexplained = 0` — so adding it there would have been a second deliberate tool change
  with its own disclosure argument, and it is not needed (§7 item 1).
- `tools/capstone_verdict_check.py` gains `E6` to `RECOGNISED_CRITERIA` — a verdict table carrying
  an `E6` row is no longer refused as *"not an E1–E5 or A1–A5 criterion"*, so this row's own §8
  precondition (its add-commit strictly earlier than any `E6` scored log) can be exercised at all.
  It enters NEITHER `CRITERIA` (the rows every TABLE must structurally carry, `V2`) NOR the epic's
  closing set (`V9`/`--require-green`, which reads `CRITERIA`). Both absences are deliberate, and
  the correction below says why.

  ⛔ **CORRECTED — and the SECOND correction is the one that stands (`#11673` review r1 + r4).**
  This bullet originally read *"gains `E6` to `RECOGNISED_CRITERIA` **only**"*, and the sentence
  that followed it claimed `CRITERIA` is "the epic's CLOSING rule" with `E6` therefore kept out of
  it. The first review called that false and this entry briefly grew a
  `CLOSING_CRITERIA = (*CRITERIA, "E6")`, so `V9` closed the epic over six rows. **The second review
  reverted its own edit to `#7949` and recorded why: the recorded close condition is the FIVE-row
  sentence** — *"Close this epic only when **E1–E5** all have evidence attached to this issue — not
  when the children close"* — and `E6` follows the precedent §3.7 records for the five `A`-rows
  (*"registered here, before any `A`-row run is scored … registered, measured, reported, **not
  closing**"*). So the tool is back to one closing set, `CRITERIA`, and the truthful reading of this
  bullet is: the RECORD must carry `E6` (`REQUIRED_CRITERIA`, `tools/capstone_preregistration_check.py`),
  the scorer accepts and checks it (`RECOGNISED_CRITERIA`), and the epic closes on `E1`–`E5`.

  **Consequence, stated rather than implied: this epic CAN close 5-of-5 with `E6` red.** That is the
  recorded rule, not an oversight. Promoting any row into the closing set — `E6` included — is a
  RE-REGISTRATION: `V2`'s row set, `V9`'s `5/5` and §3.1's table all change, so it is the owner's
  call and not a lane's. What `CRITERIA`'s five-row shape is NOT is a demand on every historical
  table — `CRITERIA` is the rows every TABLE must structurally carry *because the epic closes on
  them*, and the tables that predate the sixth row are FROZEN evidence whose bytes §7 item 2 forbids
  revising in place, so demanding `E6` of the shipped `capstone.log` and
  `capstone-measured-9152448d.log` would red this bundle permanently on artifacts nobody may edit.

**The bar, and the `A1` boundary in both directions.** `E6`'s bar is a COUNT of declared rows that are
**unbound and unexplained**, and it is not the census's `written` column. `A1` (§3.7) already measures
what the **written** graph answers; a bar on `written` would duplicate `A1` and let one defect red two
rows. `A1`'s bullet carries the same boundary from its side (*"**Not `E6`** (`#11083`)"*), and §3.8 is
this row's own statement of what it measures and does not.

**The disclosure, CORRECTED — this row IS post-visible.** `#11673`'s own checklist proposed the
opposite (*"`E6` is **not** post-visible: it had no prior measurement"*), and that is **false at this
head**: two priors predate the row and are cited in `prior_measurements.E6`. The owner's 2026-11-05
baseline reads 32 of 36 declared relations writable with four unbound; and
`docs/evidence/11083/pack-relation-census.json` was committed `f5c69f7478` on **2026-11-08**, two days
before this entry — the row's population, its verdicts and its direction cells are all readable from a
committed artefact. So `measurement_visible_to_author` is `true`, and the discount a reader must apply
is stated rather than denied. It is affordable here for a specific reason: the bar is `= 0`, a
zero-tolerance count, so there is no measured value it could have been fitted to — a bar fitted to the
measured 4 would have read `<= 4`.

**The measured state at this head, re-derived and not remembered.** `#11083`'s body quoted 32 of 36
writable with four unbound; that figure is the owner's **2026-11-05** reading (`#7949` comment
`6664402530`) and it is **already superseded by `#11083`'s own landing** — `f5c69f7478` (PR `#11543`)
edits `packs/project-management/manifest.yaml` to give the three legacy `from`/`to` rows a `mechanism`
and `semantics` and to retire `onBoard` as `declarationOnly` with its reason, and
`relation_write_verdict` is declarative, so the census committed IN that commit already reads **42
declared rows — 41 `writable`, 1 `declaration-only`, 0 `predicate-not-in-allowlist`** — with an
`effective_direction` on all 41 rows that bind a write path. (`#11237`/`#11548` owns the runtime
WRITER, i.e. `written`, and landed 2026-11-09 — it is not what bound these rows.) ⚠️ Note: the
`verdicts` aggregate in that artifact sums over `relations[]` only; the 41 is the per-row count across
both surfaces, verified 2026-11-10: 35 `relations[]` cells + 6 per-pack hierarchy cells.
The one `declaration-only` row is `pm`'s `onBoard`, retired **with** its reason recorded in the pack
(a board is a VIEW over cards), which is exactly the shape the metric excludes rather than the defect
it counts. ⇒ **`E6` is GREEN on both halves at registration.** That is disclosed, not dressed as a
near miss: the bar is the invariant the row exists to keep true — no NEW declaration lands unbound or
direction-less — and a zero-tolerance count that is currently met is what a rule looks like before it
is broken, not evidence that it is unnecessary.

**The floor, and the false PASS it refuses.** `denominator_floors.e6_declared_rows: 30` is new in this
entry. Without it, the row is scorable on an EMPTY population: a registry that failed to load its
packs reports `population.declared: 0`, so `unbound_and_unexplained` is also `0` and the row reads
GREEN over a subject that is not there. `30` is the value because this record applies that floor
uniformly (E1's object floor and every `A`-row floor, §3.7) rather than fitting one to today's 42.

**The instrument is named, not rebuilt.** `tools/pack_relation_census.py` and its committed artifact
`docs/evidence/11083/pack-relation-census.json` already emit every cell the metric reads per row
(`verdict`, `declaration_only`, `declaration_only_reason`, `effective_direction`, `written`) and derive
`population.declared`. `E6` is therefore a registration over an existing measurement: no second
census, no second record, no new gate. ⚠️ The artifact's `verdicts` aggregate covers `relations[]`
only; the metric spans the ROWS of both surfaces, which is why §3.1's row names the per-row cells and
the population rather than that aggregate.

⛔ **What is NOT yet wired, and is named rather than left to be discovered: the verdict ROW
(`#11695`).** The census emits the metric's CELLS; the scored-log producer does not yet emit its ROW.
`probes/capstone_measure.py` declares `CRITERIA = ("E1", …, "E5")` and its table builder writes five
rows, so a full re-run produces a five-row table — which is exactly the table `V9` closes the epic on,
because the recorded close condition is the FIVE-row sentence. `E6` is therefore
**registered and reported, not scored and not closing**: `V2` does not demand it of a frozen table,
`V9` does not read it, and **`#7949` CAN close 5-of-5 with `E6` red — by rule, not by oversight**.
`#11695` still owns teaching the probe to emit the row (per-row cells of BOTH surfaces under the
`e6_declared_rows` floor, with `written` reported beside and never as the bar), because a registered
row nobody can score is a bar nobody can check. Registering it AHEAD of its producer is deliberate:
waiting for the producer would leave the epic's own acceptance criterion with no bar at all, which is
the defect `#11673` exists to fix.

**Why an amendment and not a second registration.** Same ground as §10.11: the record's corpus pin is a
single `fixtures_hash`, its checker parses one criteria table and one machine record, and
`measurement_visible_to_author` is per-row. A second registration would duplicate the corpus pin and
split the gate, which is the failure shape `#10982` already refused for the `A`-rows.

## 11. The step set V5 gates over — seven unconditional, one conditional (`#10215`)

**Why this section exists.** `tools/capstone_verdict_check.py`'s V5 and `probes/capstone_run.py`'s G5
both required **all eight** steps of §4.1 in one pass. One of the eight cannot always run, so that
requirement was **false by construction** — no run of any corpus could satisfy it — and a gate that
can never pass is not a gate. This section records which steps are unconditional, which is not, and
the condition; the gate reads the declaration below instead of asserting eight.

### 11.1 The finding, and what it is not

`_collect_classify_items` (`tortoise/extractor_v2.py:11803-11825`) collects only candidates whose kind
is **missing or the `unclassified` sentinel**. Both classify passes are gated on that list being
non-empty — `if items:` at `:14222` (the S2 pass) and `:14375` (the post-S4 pass). So S2.3 has work **iff the S2 map left
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

### 11.2 The declaration

| step | kind | why |
|---|---|---|
| `S0`, `S1`, `S2.1`, `S2.2`, `S3`, `S5`, `S6` | **unconditional** | each has an input-independent trigger: the reader segments, the first LLM stage is called, the map emits candidates, the VET gate reports a pass, a query is spent, the merge accounts items, a payload is produced. A run in which one of these does not happen is a failing run. |
| `S2.3` | **conditional** | its trigger is `_collect_classify_items(...) != []`, i.e. a candidate the map could not type. Zero candidates ⇒ the pass has nothing to do, and §4.1's own framing (*"assign each SURVIVING candidate its pack kind"*) is satisfied vacuously. |

The machine-readable form is the `steps:` block in the pins fence above, restated byte-identically in
`preregistration.json`, and `tools/capstone_preregistration_check.py` fails if the two disagree, if the
split is not a strict partition of the eight, or if the conditional entry loses its condition, its idle
evidence or its evidence issue.

### 11.3 The restated requirement — and why it is not a relaxation

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

**Why (2) is a witness and not a loophole.** The extractor's fail-open `except` (`:14235`, `classify(S2) failed` / `:14386`, `classify(union) failed`) leaves
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

### 10.22 — the §11 cite block re-pointed for `#11654`'s S3 narrative (+108 / +112) (2026-11-10, `#11654`)

> ⛔ **Renumbered from §10.21 in the `#11673` forward merge.** `#11673`'s E6 section claimed §10.21 at
> 01:21Z on 2026-11-10; this `#11654` section landed on `main` at 12:35Z the same day and claimed the
> same number, so two sections claimed one (§8's own failure mode — an ambiguous pointer everywhere).
> The LATER section yields: `#11673`'s §10.21 keeps its number and its seven pointers (`:445`, `:1092`,
> `:1328`, `preregistration.json`, the check tool, two test files); this one moves to §10.22, which is
> also the order the sections appear in.

- **Baseline — measured at `a577d0e25cb3`, this branch's BASE (the merge-base with `main`, before the S3 narrative was threaded):**
  the two `if items:` gates at `:13763`/`:13916`, the two fail-open `except` arms at
  `:13776`/`:13927`, the two result blocks at `:14105` (closing `:14111`) and `:14115`
  (closing `:14119`), and `_run_vet_pass`'s S2.2 call at `:16286` (the post-S4 union call at
  `:13892`) — exactly the "new value" §10.19 measured.
- **New value — measured at this head:** the same symbols at `:13871`/`:14024` (the two `if items:`
  gates), `:13884`/`:14035` (the two fail-open `except` arms), the two result blocks at `:14217`
  (closing `:14223`) and `:14227` (closing `:14231`), and `_run_vet_pass`'s S2.2 call at `:16398`
  (the post-S4 union call at `:14000`). §10.19's "`_collect_classify_items` and its extent do not
  move" was true of `#11534`, whose insertions all sat BELOW the collector; `#11654` inserts ABOVE
  it, so the collector moves too: `:11510`, `:11510-11532`.
- **Why the delta is NOT one constant.** `#11654` inserts at four distinct points — +99 at
  `_resolution_prompt`/`_bounded_narrative` (below `:5794`), +8 at `:5825`, +1 at `:5851` (so **+108**
  for every line between `:5851` and `:13949`), and a further +4 at the `extract_session_v2` resolve
  seam that hands the narrative to `resolve_entities` (so **+112** at and below `:13950`). Every
  number in this entry was re-derived by content, not by adding a delta to §10.19.
- **Verified by content, not by arithmetic.** `:13871`/`:14024` are the two `if items:` gates (one
  per classify pass), `:13884`/`:14035` are the two fail-open `except` arms whose messages name
  `classify(S2) failed` / `classify(union) failed`, `:14217`/`:14227` open
  `result["classify_later"]` and `result["vet"]` and close at `:14223`/`:14231`, `:16398` is
  `embed_list, vet_s2_stats, _w, vet_s2_pool = _run_vet_pass(`, and `:14000` is
  `complete_list, vet_union_stats, _w, _ = _run_vet_pass(`.
- **What moved: nothing else.** No metric, denominator, threshold, class set or corpus pin is
  touched. The `#10360` switch's read-back is re-derived from `:10157` to `:10265` (the +108) in
  `preregistration.json`'s live prose. `document_sha256` is re-cut in this same commit, per §8.

### 10.23 — the §11 cite block re-derived at the `#11654` forward merge, and the in-place re-pointings reverted (2026-11-10, `#11653`)

> ⛔ **Superseded for the merged tree by §10.24** — the §11 cite-block numbers below are a faithful
> record of the head this entry names, and §10.24 carries the live values for those cites in this
> tree. That includes the last sentence of the note below ("it is the newest entry and its numbers
> are this tree's"): true when this entry was written, not true of this tree.
>
> **One number below is still live, and it is not §10.24's.** This entry's `#10360` read-back bullet
> re-derives that switch's read-back to `extractor_v2.py:10508`; `#11239`'s `+17` inserts sit at
> `extractor_v2.py:12112-12157`, **below** that assignment, so it did not move. That is why §10.20's
> pointer still names THIS entry for the `:10508` value, and why `preregistration.json`'s `read_back`
> prose carries it: §10.24 re-derived the block's lower cites and never restates `:10508`.
>
> ⛔ **Renumbered from §10.21 to §10.23 in the `#11654` forward merge**, by the rule §10.22 states for
> `#11673`'s collision: this section and `#11673`'s both claimed §10.21 on 2026-11-10, the later one
> yields, and §10.22 holds the number the other one moved to in the same merge. The section is
> appended rather than interleaved because it is the newest entry and its numbers are this tree's.

- **Why this entry exists rather than an edit to an earlier one.** This branch's S3-hold fix
  (`#11653`, `d26a6020c2` and the `_strip_reference_sets`/`_prune_held_operators` work after it)
  inserts **+293** net lines into `tortoise/extractor_v2.py` above every cite in §11's block, and
  `main`'s `#11654` inserts a further **+108** above the same cites (+112 at and below its own
  resolve seam, which §10.22 measured) — so the collector sits at `:11803` here against `:11402` at
  the merge base both entries name (**+401** = `#11653`'s +293 plus `#11654`'s +108). `main`'s
  `#11660` moved `tortoise/sdk.py` (+44) and `projection/__init__.py`. A forward merge of `main`
  then had to re-derive the block, and an earlier revision of that merge re-pointed §10.15,
  §10.16 and §10.19 IN PLACE. That was reverted: §10.19's own text forbids it — *"an entry
  records numbers measured at the head it names, and … re-numbering a history entry would be a
  false record of what was measured … The settled handling of exactly this move is the one
  §10.17→§10.18 used: a new dated entry"* — and it did not even hold arithmetically, because
  §10.19's prose still asserted that *all ten numbers move by the same +57* while the numbers
  written into it had moved by +391, +409 and +422. Those three entries are **byte-identical to
  `main`**, each a faithful record of the head it names, and the live values are here.
- **New value — measured at this head:** `_collect_classify_items` at `:11803`, its
  extent `:11803-11825` (23 lines, `def` to `return items` — unchanged in size, so the extent is
  still the 23 lines §10.19 measured), the two `if items:` gates at `:14205`/`:14358`
  (the S2 pass and the post-S4 pass), the two fail-open `except` arms at `:14218`/`:14369`,
  the two result blocks at `:14569` (closing `:14575`) and `:14579` (closing `:14583`), and
  `_run_vet_pass`'s S2.2 call at `:16763` and its post-S4 union call at `:14334`.
- **Verified by content, not by arithmetic.** Each line was opened at this head, not shifted:
  `:11803` is `def _collect_classify_items(embed_list: dict) -> list[dict]:` and `:11825` is
  `return items`; `:14205`/`:14358` are the two `if items:` gates; `:14218`/`:14369` are the
  two fail-open `except` arms whose warnings name `classify(S2) failed` / `classify(union)
  failed`; `:14569` opens `result["classify_later"]` and `:14579` opens `result["vet"]`;
  `:16763` is `embed_list, vet_s2_stats, _w, vet_s2_pool = _run_vet_pass(` and `:14334` is
  `complete_list, vet_union_stats, _w, _ = _run_vet_pass(`. The constants differ between the
  sides (`+293` for this branch's own inserts above the collector, `+108` for `#11654`, `+44` for
  `#11660`'s `sdk.py` move), which is exactly why a constant could not be trusted here.
- **The `#10360` switch's read-back is re-derived by content too**, to `:10508` (the
  `_value_fidelity_judged = _state_value_verbatim_enforced()` assignment), and
  `preregistration.json`'s live `read_back` prose carries that number.
- **What moved: nothing else.** No metric, denominator, threshold, class set or corpus pin is
  touched — every §1–§9 value and the E4 denominator are the ones this record froze.
  `document_sha256` is re-cut in this same merge, per §8's rule for *any* edit to this file.

### 10.24 — the §11 cite block re-derived for `#11239`'s +17 (2026-11-11, `#11795`)

> **A successor entry, not an edit to §10.23.** §10.23's numbers are a faithful record of the
> `#11654` forward-merge head it names, and §10.19's rule is that re-numbering a history entry in
> place would be a false record of what was measured. The tree moved again under it, so the
> handling is the one §10.17→§10.18 used: a new dated entry.

- **Why this entry exists.** `#11795` (`3794677ee3`) inserted a net **+17** lines into
  `tortoise/extractor_v2.py` at `:12112-12157` (hunk `@@ -12112,29 +12112,46 @@`), which is **above
  every cite in §11's block that sits BELOW `:12112`** — so each of those moved by exactly `+17`
  and nothing reddened the document. The block's collector cite (`:11803-11825`,
  `def _collect_classify_items` to its `return items`) sits ABOVE the insert, so it did NOT move
  and is carried unchanged: the `+17` is the block's LOWER half, not the whole block, and applying
  it to the collector would manufacture the stale cite this record exists to prevent. The same
  commit, together with `#11805`, moved `tortoise/sdk.py` twice: **+115**
  above the `_extract_session_v2` region and a further **+18** above the `step_witness` meta fold,
  so its cites are re-derived individually rather than by a constant.
- **Three further files moved in the same pass**, and they are named here rather than folded into
  "nothing else", because a reader auditing this hash-pinned artefact has to be able to tell which
  files moved. What is given is each file's TOTAL movement (a line-count delta — stable, and
  checkable with `wc -l`) plus, for each cite this entry actually re-points, that cite's own measured
  `was`/`now`. ⛔ **No per-band offset table is published here, deliberately, and no number below
  may be used to derive a cite you have not opened.** `#11795` inserts at several points in each of
  these files, and inside a hunk the last few pre-image lines still carry the PREVIOUS offset — so a
  table of "from line N it is +K" is wrong by 1-13 at every boundary it names, which manufactures
  exactly the stale cite this record exists to prevent. A cite is re-derived by opening it in the
  tree; that is how every number under *New value* was obtained. (Earlier revisions of this bullet
  did publish such tables, and were corrected for it four times.)
  - `tortoise/projection/entities.py` — `#11795` alone: `7091` → `7355` (`+264`). The cite this pass
    re-points, `§1.6.2`'s alias gate, is `1239` → `1478` — `+239`, from that commit's first hunk
    (`@@ -1150,6 +1150,245 @@`), NOT `+264`, because three later hunks sit below it. ⚠️ An earlier
    revision called that correction "pre-existing staleness": it had measured the base `b50e3293`,
    which already CONTAINED `#11795`, so the test was circular. The same `:1239` → `:1478` re-point
    is carried into `preregistration.json`'s live `read_back` prose.
  - `tortoise/projection/__init__.py` — `#11795` alone: `21840` → `21973` (`+133`) in thirteen
    hunks. The cite this pass re-points, `13738-13740` → `:13804-13806`, carries `+66` because it
    sits above the fourth hunk (`@@ -14549,6 +14615,8 @@` — `+66` is that hunk's START offset and
    `8` its line count). So this cite's offset is half the file's, which is precisely why the two
    must not be interchanged — and why `+66` must not be turned into "the next band is `+68`" either:
    the two lines inserted inside that hunk still carry `+66`, so the next offset begins after them,
    not at new `:14615`.
  - `tortoise/hosted_api.py` — `+217` overall (`23249` → `23466`), and it takes TWO commits:
    `#11814` (`69cc13a565`) `+173` (`23249` → `23422`), then `#11795` a further `+44` (`48310` →
    `48354`; its four hunks at `:21428`/`:21437`/`:21451`/`:21605` all sit above the pivot).
    `#11805` does not touch the file. The pivot was re-measured with
    `tools/hosted_api_cite_census.py --refresh`, which also re-stated that note's above/below split,
    `14`/`441` → `12`/`443`.
  The `read_back` re-point is disclosed the same way §10.22 and §10.23 disclose theirs.
- **New value — measured at this head:** the two `if items:` gates at `:14222`/`:14375` (the S2
  pass and the post-S4 pass), the two fail-open `except` arms at `:14235`/`:14386`, the two result
  blocks at `:14586` (closing `:14592`) and `:14596` (closing `:14600`), and `_run_vet_pass`'s
  S2.2 call at `:16780` and its post-S4 union call at `:14351`. In `tortoise/sdk.py`,
  `_extract_session_v2` is now declared at `:16599` (was `16484`) and called at `:15625` (was
  `15510`); the `step_witness` meta fold is at `:17484` (was `17351`), and
  `proj.link_source_to_event` at `:17882` (was `17749`). Also in `tortoise/sdk.py`, and re-derived
  by content like the rest: the `relation_write` seam at `:17429`/`:17441`/`:17442` (was
  `17296`/`17308`/`17309`) and `_TURN_WRITE_CYPHER` at `:2530` (was `2528`). That last `+2` IS from
  the two commits named here and is not a mystery insert: `#11795` adds one import line
  (`@@ -168,6 +168,7 @@`, which `git show 3794677ee3 -- tortoise/sdk.py` prints) and `#11805` one
  blank line (`@@ -368,6 +368,7 @@`, which `git show 0bdb08dbdb -- tortoise/sdk.py` prints — each
  header is quoted from ITS OWN commit's diff, because the combined diff renumbers the second) —
  so `_TURN_WRITE_CYPHER` carries exactly `+2`. That `+2` is NEITHER the file's movement (`sdk.py`
  grows `49122` → `49213`, `+91`, across `#11795` alone) NOR a band value: because the two inserts
  sit at `:168` and `:368`, a cite anywhere in `:168`-`:2528` carries `0`, `+1` or `+2` depending on
  which side of those two lines it sits. Offsets stated in this entry are the offsets AT the cite
  named; applying one to a different line is the error the note above forbids, because they are not
  uniform across these files nor even within a single hunk.
- **Verified by content, not by arithmetic.** Each line was opened at this head:
  `:14222`/`:14375` are the two `if items:` gates; `:14235`/`:14386` are the two fail-open
  `except` arms whose warnings name `classify(S2) failed` / `classify(union) failed`; `:14586`
  opens `result["classify_later"]` and `:14596` opens `result["vet"]`; `:16780` is
  `embed_list, vet_s2_stats, _w, vet_s2_pool = _run_vet_pass(` and `:14351` is
  `complete_list, vet_union_stats, _w, _ = _run_vet_pass(`. The `#11795` growth is not uniform
  across `sdk.py`, which is exactly why a single constant could not be trusted there either.
- **What moved: the citations, and nothing that is a protocol value.** No metric, denominator,
  threshold, class set or corpus pin is changed — every §1–§9 criterion VALUE and the E4
  denominator are the ones this record froze. What §1.6.2 and §11 carry changed is the `file:line`
  each one points at, and `document_sha256` is re-cut in this same commit, per §8's rule for *any*
  edit to this file.

### 10.25 — the §548 blank-`pointKind` cite re-pointed for `#9752`'s own insert (2026-11-11, `#9752`)

> **A successor entry, not an edit to §10.24.** §10.24's numbers are a faithful record of the
> `#11795` head it names, and §10.19's rule — quoted by §10.23 and by §10.24 above — is that
> re-numbering a history entry in place would be a false record of what was measured. The tree moved
> again under §10.24, and the handling is the same one: a new dated entry.

- **Why this entry exists.** §10.24 is the newest change-control entry, which makes it the entry a
  reader checks against the SHIPPED tree. Its `tortoise/projection/__init__.py` cite — the `#548`
  rebuild's blank-`pointKind` synthesis, re-pointed there as `13738-13740` → `:13804-13806` — is
  **false at this head**: `#9752` inserts into that same file, above the anchor, so `:13804-13806`
  is now the `:PackSelection` restore's CONFLICT warning, i.e. lines this very change added. A
  reader following it lands on unrelated code, which is the class of stale pointer §10.7-§10.24
  exist to keep closed.
- **New value — measured at this head, by opening the lines:** `tortoise/projection/__init__.py:14225-14227`
  — `14225` is `# Ensure pointKind is present (operators often lack it)`, `14226` is
  `if "pointKind" not in clean:` and `14227` is `clean["pointKind"] = ""`, the same three-line
  window §10.24 re-pointed to at its own head.
- **The offset is the cite's, never the file's.** At this cite the movement is `+421`
  (`13804` → `14225`). `#9752` moves the file by `+748`/`-27` (net `+721`) across 38 hunks, and the
  hunks BELOW this anchor carry the rest — so `+721` applied here would land 300 lines late. This is
  §10.24's own rule applied to a third file, and the reason every number in this entry is a
  before/after at a line that was opened rather than a file total.
- **Only this cite moved.** `#9752` touches `tortoise/projection/__init__.py` and no other file
  §10.24 cites: `tortoise/extractor_v2.py`, `tortoise/sdk.py`, `tortoise/hosted_api.py` and
  `tortoise/projection/entities.py` are untouched by it, so §10.24's `:14222`/`:14375`,
  `:14235`/`:14386`, `:14586`/`:14596`, `:16780`/`:14351`, `:1478`, `:16599`/`:15625`,
  `:17484`, `:17882`, `:17429`/`:17441`/`:17442`, `:2530` and the `hosted_api.py` pivot `:23466`.
  Of those, the `extractor_v2.py` group, `entities.py:1478` and `hosted_api.py:23466` still
  resolve at this head (each re-opened; `tools/capstone_preregistration_check.py` exits 0). ⛔ **The
  `sdk.py` numbers do NOT** — they were re-opened at `9ba2da35`, and `main`'s `ccf5c4036c`
  (`#11836`) then landed on this branch and moved that file by `+248` at every one of those sites
  (`+9` for `:2530`): measured at `45c065cc79`, `:16599` is `# secret to a third-party provider`
  and `def _extract_session_v2` is at `:16847`. They are HISTORICAL here, not re-asserted.
- **The bundle's probe cite agrees, and did not before.** `probes/capstone_measure.py:2989` carried
  `:14213-14215` — derived at an intermediate head and then moved by this change's own later
  commits — and `tests/test_7949_capstone_measure.py::test_the_bundles_cites_resolve_where_the_bundle_says_they_do`
  redded on it (`:14213 reads "# new gate's guarantee is false."`). It now reads `:14225-14227`, the
  same window this entry records, so the document and the probe cannot disagree about this anchor.
- **What moved: one citation, and nothing that is a protocol value.** No metric, denominator,
  threshold, class set or corpus pin is changed — every §1-§9 criterion VALUE and the E4
  denominator are the ones this record froze. `document_sha256` is re-cut in this same commit, per
  §8's rule for *any* edit to this file.
- ⛔ **Superseded for the merged tree by §10.26.** The numbers above are the `9ba2da35` head this
  entry measured — `:14225-14227` really was the window there. A later commit on this same branch
  (`93dafca241`) inserted 20 lines above the anchor and did not re-derive the cite, so the live
  numbers are §10.26's.

### 10.26 — the same §548 cite re-pointed again, for this branch's own `+20` (2026-11-11, `#9752`)

> **A successor entry, not an edit to §10.25.** §10.25's numbers are a faithful record of the
> `9ba2da35` head it measured, where `:14225-14227` really was the window. The tree moved again
> under it on this same branch, and the handling is §10.24→§10.25's: a new dated entry.

- **Why this entry exists.** `93dafca241` ("bound the replay success log to the marker it actually
  restores") added a **+20**-line comment block to `tortoise/projection/__init__.py` ABOVE the anchor
  §10.25 cites, and did not re-derive the cite. Nothing reddened on the document's side, because
  §10.25 is stripped from every live-cite gate by `_without_change_history` and
  `tools/capstone_preregistration_check.py` folds this file to a digest and resolves no line
  number. The cite's OTHER half lives in code, and that half redded:
  `probes/capstone_measure.py:2989` carried the same `:14225-14227`, so
  `tests/test_7949_capstone_measure.py::test_the_bundles_cites_resolve_where_the_bundle_says_they_do`
  reported `projection_init.py:14225 reads 'if isinstance(er[0], str)]'`, which carries none of the
  three lines the cite names.
- **New value — measured at this head, by opening the lines:**
  `tortoise/projection/__init__.py:14245-14247` — `14245` is
  `# Ensure pointKind is present (operators often lack it)`, `14246` is
  `if "pointKind" not in clean:` and `14247` is `clean["pointKind"] = ""`, the same three-line
  window §10.25 re-pointed to at its own head.
- **The offset is the cite's, never the file's, and the two offsets here differ by their base.**
  Against §10.24's `13804` the movement is `+441` (`13804` → `14245`). Against §10.25's `14225` it
  is `+20` (`14225` → `14245`), and that `+20` is `93dafca241`'s own insert at this site — not a
  file total, and not a value to apply to another line.
- **Only this cite moved in this file's §10.24 block.** §10.24 also re-points cites in
  `extractor_v2.py`, `sdk.py`, `projection/entities.py` and `hosted_api.py` — this change touches
  none of those four files, so §10.24's values there are exactly the ones it recorded at
  `9ba2da35`. ⛔ They are not all still live: `main`'s `ccf5c4036c` (`#11836`) moved `sdk.py` by
  `+248` at every cite that entry carries there (see §10.25's measurement), so the `sdk.py`
  numbers are historical at this head. The other three files' cites still resolve (each
  re-opened here). Beyond its own entries, nothing outside `tortoise/projection/__init__.py` was
  re-pointed by this entry.
- **What moved: one citation, and nothing that is a protocol value.** No metric, denominator,
  threshold, class set or corpus pin is changed — every §1-§9 criterion VALUE and the E4
  denominator are the ones this record froze. `document_sha256` is re-cut in this same commit, per
  §8's rule for *any* edit to this file. The probe's cite and this document's cite are re-pointed
  TOGETHER so they cannot disagree about this anchor, which is the failure §10.25 was written to
  stop and this entry closes.
