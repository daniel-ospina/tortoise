<!-- research-path: the issue comment carrying `<!-- research: v1 -->` on #11689 -->
<!-- plan-review: cycles=3, status=convergence-capped, version=2.3.0 -->

# #11689 — the E1/E2 probe refuses every run on floors owned by other criteria

**Issue:** [#11689](https://github.com/daniel-ospina/tortoise/issues/11689) · `complexity:standard`, `P1-severe`, `team:epistemic-team`
**Branch:** `fix/11689-e1e2-owned-floors` · **Base:** `main` @ `a577d0e25c`
**Artifact class:** code (`gate-enforcement` questions apply to the V15 half)

## 1. Confirmed problem

`docs/evidence/7949-capstone/probes/e1_e2_probe.py` is the only instrument that re-runs
E1/E2 on one corpus in one process. Its verdict is gated on
`prereg_binding(...) == "MATCHED"`, because a verdict without a frozen (threshold,
population) pair is not evidence. At `a577d0e25c` that function **cannot** return
`MATCHED` on the record the probe exists to score: `CONSUMED_FLOORS` was a one-key
set (`e1_objects`), so every other key in `preregistration.json`'s 14-key
`denominator_floors` block was "unconsumed" and refused the run. Durable,
re-derivable transcript — the measurement is at the BASE revision and is
reproduced by reading the base file and the committed record (HEAD deliberately
reproduces the opposite, `floor_unconsumed=n/a` on the binding line):

```text
$ git show a577d0e25c:docs/evidence/7949-capstone/probes/e1_e2_probe.py | grep -n 'CONSUMED_FLOORS ='
621:CONSUMED_FLOORS = frozenset({"e1_objects"})

$ python3 -c "import json; d=json.load(open('docs/evidence/7949-capstone/preregistration.json'))['denominator_floors']; print(len(d)); print(sorted(k for k in d if k!='e1_objects'))"
14
['a1_objects', 'a1_rollup_queries', 'a2_candidates', 'a2_decided_candidates', 'a3_event_root_coverage', 'a3_statements', 'a4_typed_statements', 'a5_actors', 'a5_points', 'e1_min_before_share', 'e2b_surviving_units', 'e3_derived_nodes', 'e5_rate_min_sentences']

# ⇒ 14 declared - 1 consumed = 13 unconsumed ⇒ `floor-unconsumed` ⇒ MISMATCH
# (`:758` is the MISMATCH arm; `:756` is BELOW_FLOOR, which is why a mixed reason
#  set takes 758 and not 756) ⇒ `threshold = None` at `:1017`, the binding-gated
#  assignment — `:1012` is the malformed-artifact arm and `:1013` is the `if` that
#  guards `:1017` ⇒ every criterion UNPREREGISTERED.
$ git show a577d0e25c:docs/evidence/7949-capstone/probes/e1_e2_probe.py | grep -n 'status = "MISMATCH"\|status = "BELOW_FLOOR"\|if bind\["status"\] != "MATCHED"\|^                threshold = None'
756:        status = "BELOW_FLOOR"
758:        status = "MISMATCH"
1012:                threshold = None
1013:            if bind["status"] != "MATCHED":
1017:                threshold = None
```

Nine of the thirteen are the `A1`–`A5` rows' floors (added to the same JSON block by
`#10982` §10.11); `e3_derived_nodes`/`e5_rate_min_sentences` belong to E3/E5. The
remaining two — `e1_min_before_share` (E1's own §3.2 refusal boundary) and
`e2b_surviving_units` (E2b's population floor) — are rows **this** probe scores, so
the refusal both voids the run and never checks the floor it cites.

**Root cause, one sentence:** the probe has one bucket for floor keys ("consumed"),
so a floor owned by another instrument's row is treated as this binding's unmet
precondition, and every run is voided before it can score.

**What the fix does and does NOT change, stated precisely (cycle 1 finding).** After
this change the committed record STILL refuses a re-run over `e1e2_corpus.json` — for
`corpus-path` and `below-floor`, which are genuine: the record pins
`tests/eval/write_path` (a 30-Object floor) while this probe measures a 25-Object
corpus, and reconciling the two corpora is an explicitly separate follow-up. What
this lane delivers is (i) the refusal is scoped to reasons about THIS run, and
(ii) the two E1/E2b floors that were cited-but-never-read are now compared. The
issue's "non-vacuity pin" is therefore delivered as a **per-key accounting
assertion derived from the committed record** (every declared key is owned or
declared-skipped; `floor_unconsumed=n/a`; no `floor-unconsumed` reason) plus a
positive binding test on a record whose corpus/denominator the run meets — not as a
`MATCHED` verdict for a corpus the record does not pin.

**Falsification check:** the fix is wrong if a `denominator_floors` key that no row
classifies can be dropped without refusing the run — i.e. if `floor-unconsumed`
stops firing for an unknown key. Test:
`tests/test_e1_e2_measurement.py::test_a_floor_no_row_owns_is_refused_not_dropped`.

## 2. Alternatives considered

| Option | Why not |
|---|---|
| **A. One bucket → three (owned / declared-skip / unconsumed)** — chosen | Keeps #9513's fail-closed half (an unclassified key still refuses and names itself) while removing the over-scope. Every declared key is accounted for, so nothing is silently dropped *and* nothing foreign voids the run. |
| B. Delete the `floor-unconsumed` refusal entirely | Reverses #9513 review P2: a record's new floor would be dropped silently while the bar is stamped `MATCHED`. That is the fail-open the finding closed. |
| C. Keep the blanket refusal and edit the record to drop the foreign keys | The record is one artifact for 14 rows; deleting A-row floors to satisfy one probe would remove the A rows' own anti-gaming floors. Also does not fix the `e1_min_before_share`/`e2b_surviving_units` half (they are ours and unread). |
| D. Return a partial/unscored result with an `incomplete` marker instead of scoping | The binding's verdict is boolean by contract (`MATCHED` or not); a third state would leak into `prereg=`/V15 and make "this corpus cannot measure E1" indistinguishable from "the instrument gave up". Scoping satisfies the precondition instead of degrading around it. |

## 3. Complexity + domains

`standard`. Domains: `engineering` (measurement instrument + its gate), `data` (the
pre-registration record's floor block is read, not changed); team `epistemic-team`.

## 4. Integration Surface Map

| Surface | What consumes it | Test layer |
|---|---|---|
| `prereg_binding()` signature | `e1_e2_probe.main()` (one call site) | unit + integration (`_run_with_prereg` runs the real `main`) |
| log line `prereg-binding …` | `tools/e1_e2_census_check.py` V15 (log parser), `binding_line()` | integration (checker against a real run's log) |
| `census()` dict | `main()` (E-criteria + binding), `--json-out`, tests | unit (`census` is pure) |
| committed `e1-e2.log` | `test_the_committed_log_is_frozen_and_stays_unscored` | **frozen** — grandfathered by `PRE-REGISTRATION.md` §0, so NO re-take (see §9 correction) |
| `denominator_floors` in `preregistration.json` | read-only; **not modified** | record pin unchanged |

## 5. Research

One `<!-- research: v1 -->` comment on #11689 (Tortoise graph ×2 queries, internal
codebase read, one rung-3 primary source). Decisions it supports: (a) the refusal's
scope, not its existence, is the defect; (b) the owned set is exactly the rows whose
numbers this probe prints; (c) the fail-closed half must survive as a third bucket.

## 6. Wiring

Every call site / consumer of each thing this change touches:

| Touch point | Type | What it does there | Status |
|---|---|---|---|
| `docs/evidence/7949-capstone/probes/e1_e2_probe.py` `OWNED_FLOORS` | definition | replaces `CONSUMED_FLOORS` (one key → three); `_declared_floor` and `_floor_keys` read it | covered |
| `…/e1_e2_probe.py` `SKIPPED_FLOORS` | definition | key → owning criterion (`A1`–`A5`, `E3`, `E5`, `E6`); printed on the binding line | covered |
| `…/e1_e2_probe.py` `_floor_keys()` | helper | partitions declared keys into owned / skipped / unconsumed | covered (unit-derived from the real record) |
| `…/e1_e2_probe.py` `_declared_metric_floor()` | helper | validates `e1_min_before_share` as a share in [0,1] and `e2b_surviving_units` as an int; raises so the caller refuses (`floor-unreadable`) | covered |
| `…/e1_e2_probe.py` `prereg_binding()` | public helper | adds two keyword-*required* params (`before_share`, `surviving_units`) and a third `below-floor` cause; returns `floors_owned`/`floor_skipped`/`declared_before_floor`/`before_share`/`declared_surviving_floor`/`surviving_units` | covered (one call site, in `main`) |
| `…/e1_e2_probe.py` `binding_line()` | log formatter | prints the new fields and the declared skips with owners | covered (V15 parses them) |
| `…/e1_e2_probe.py` `census()` | measurement | unchanged shape — the E2b population is the arm's existing `atomic_points`, the quantity the row's own floor tests (a third population was cut in code review: it diverged by the surviving fused points, so a run the E2b row refuses could bind here) | covered (V15 anchors on the arm line) |
| `…/e1_e2_probe.py` main's binding call | call site | passes `before_c["share"]` and `after_c["atomic_points"]` | covered |
| `tools/e1_e2_census_check.py` `_is_number()` | helper | finite-number test for the new measured/declared pairs | covered |
| `tools/e1_e2_census_check.py` V15 floor accounting | gate | disjointness of the three buckets, skip must name an owner, a compared floor must be claimed as owned, scored logs must carry the accounting, and the two new measured sides are anchored to the ARM lines | covered (8 new pins) |
| `tools/e1_e2_census_check.py` V15 BELOW_FLOOR | gate | generalised from `objects`/`declared_floor` to the floor that actually fired | covered |
| `docs/evidence/7949-capstone/E1-E2.md` limit 7 + Re-running | docs | states the owned/skipped/unconsumed partition and the two newly consumed floors | covered |
| `docs/evidence/7949-capstone/e1-e2.log` | evidence artifact | **NOT touched** — `origin/main`'s bytes; frozen by `PRE-REGISTRATION.md` §0 (see §9) | sha256 constant digest (`test_the_committed_log_is_frozen_and_stays_unscored`) |
| `preregistration.json` | input record | **unchanged** — this lane must not edit the record it reads | n/a |

## 7. Implementation steps (each with the test that proves it)

1. Add `OWNED_FLOORS` (3) + `SKIPPED_FLOORS` (12 declared) + `_floor_keys()`; `_declared_floor`
   returns only unclassified keys as unconsumed → `test_the_real_pre_registration_schema_names_why_it_cannot_score_this_run`,
   `test_a_floor_no_row_owns_is_refused_not_dropped`.
2. Validate + check the two newly owned floors (`_declared_metric_floor`, the arm's
   `atomic_points`, required kwargs). Code review added a positivity rule to all three owned
   floors: a floor of `0` or less binds nothing, and the sibling reader refuses it by name → `test_the_owned_floors_are_checked_and_a_run_that_meets_them_binds`,
   `test_an_owned_floor_the_run_is_under_is_refused_not_scored` (both floors, both directions),
   `test_an_unreadable_owned_share_floor_is_refused_not_read_as_absent`.
3. Print the accounting beside the decision; skip must carry its owner →
   `test_a_floor_declared_for_another_row_does_not_void_the_run` (not-over-widening).
4. V15: three-bucket accounting, both-directions arithmetic for the new pairs, accounting
   required on a SCORED log, and the two new measured sides ANCHORED to the arm lines
   (cycle 1 P1) → seven `test_checker_refuses_*` pins added beside
   `test_checker_refuses_a_binding_that_names_floors_it_did_not_evaluate`
   (`…_denies_owning_a_floor_it_checked`, `…_a_skip_that_names_no_owning_criterion`,
   `…_one_floor_in_two_buckets`, `…_a_matched_binding_under_an_owned_share_floor`,
   `…_a_scored_log_without_the_floor_accounting`,
   `…_a_below_floor_binding_with_no_under_floor_pair`,
   `…_a_binding_whose_measured_side_is_unanchored`).
5. Docs (`E1-E2.md`) → the probe's printed text is pinned by the note-state tests below.
   ⛔ **`e1-e2.log` is NOT re-taken** — see §9.

## 8. Rounds

**Rounds:** 3 (verifiers: 1 per round, class: gate-enforcement)

**Cycle log — the exit is CONVERGENCE at the standard cap (3 cycles), not a clean pass:**

- **Cycle 1** (verifier 1): 6 issues. P0 the evidence log had not been re-taken; P1 the two new
  owned floors were only self-consistent on the binding line (a hand-edited log could defeat
  them); P1 the plan header attested `status=clean` before any verifier returned; P2 ×3 (the
  §1 reproduction cited an untracked `/tmp` script, §1 overstated the outcome, "six" pins where
  five existed). All six fixed: the measured sides are now anchored to the ARM lines
  (`metric=atomic_points atomic=` on the scored arm, the control arm's `share=`) and pinned by
  `test_checker_refuses_a_binding_whose_measured_side_is_unanchored`; the log was re-taken; the
  header claim was withdrawn.
- **Cycle 2** (verifier 2, fresh): 4 issues, all P2, all plan-doc accuracy — a stale §1
  reproduction line, three wiring locators shifted by cycle 1's inserted code, T4's residual
  mis-naming its catcher, and a log note asserting a check its own file does not contain. The
  last was a REAL defect and was fixed in the probe (the E2b clause is now conditional on a
  binding existing) with a fresh log re-take.
- **Cycle 3** (verifier 3, fresh): 2 issues remain, both P2 plan-doc accuracy (two quoted
  base-revision line numbers wrong by one function arm; the residual under-claiming an existing
  value pin at `tests/test_capstone_a_rows_10982.py::test_no_existing_bar_moved_the_probe_reads_the_same_seven`).
  Both are FIXED in this revision, and **no re-check was spent on them**: cycle 3's set is a
  strict subset of cycle 2's and both were documentation accuracy in this doc, which is the
  convergence exit at the standard tier's 3-cycle cap. Recorded as capped, **not clean** — no
  verifier returned `NO ISSUES FOUND` on this plan.

Cycle 3 is where the PLAN REVIEW exited; it is not where the change stopped. Code changed
**after** cycle 2 — the log-note defect the cycle-2 paragraph calls the one *"REAL defect"* was
fixed in the probe (`837c8d34d20`), so "no code changed after cycle 2's P1 fix" was never true —
and it kept changing through the code-review rounds that followed `12dbdddfe4`: measured against
that head, the probe, the checker and the tests had moved **+783/−102** at `7f9a22b2b8`
(`git diff --shortstat 12dbdddfe4 7f9a22b2b8 -- docs/evidence/7949-capstone/probes/e1_e2_probe.py
 tools/e1_e2_census_check.py tests/test_e1_e2_measurement.py`; the figure is pinned to that head
because it keeps moving — at this PR's head the same diff is larger). The committed
probe/checker/tests are therefore **NOT** the
ones verifier 3 measured at `12dbdddfe4`; the coverage claim for what ships rests on the **code
review** below, not on cycle 3.

**Code review** (fresh context per round, each bound to one head — a record for an earlier head
never covers a later push, so the rounds after the one recorded here are on the PR's review
comments and supersede it):

- Cycle 2's REAL defect above, plus 5 more rounds of findings — the `surviving_planted`
  denominator (P1, resolved by cutting the third population), the share-only pin that could not
  fail on the regression it named, the `+dirty` re-take, the `atomic_points` residual (filed at
  #10989), the E2b note keyed on the binding's status instead of the declared key, `floors_owned=`
  trusted word-for-word, a declared `null` collapsing into `n/a`, five unpinned V15 rules, a
  refusal that could shed an owned floor via `floor_skipped=`, and a declared floor value rendered
  with unescaped spacing. Every one is fixed in the code with a pin that reds without the fix;
  each round's head, measurement and remaining list is on the PR.
- The two plan-doc items cycle 3 left (stale base-revision locators, the residual under-claiming an
  existing value pin) are fixed in this doc.

## Review Scope Contract

**Class:** gate-enforcement

**Risk surface** — what this change can break, each with the check that would catch it:

- **R1** A floor that belongs to another row starts voiding the run again (over-refusal returns) →
  `test_a_floor_declared_for_another_row_does_not_void_the_run` (declares floors 10,000× the run
  and the run still SCORES) + the committed record's own accounting assertion in
  `test_the_real_pre_registration_schema_names_why_it_cannot_score_this_run` (`floor_unconsumed=n/a`).
- **R2** An unclassified floor is silently dropped while the bar is stamped `MATCHED` (the #9513
  fail-open returns) → `test_a_floor_no_row_owns_is_refused_not_dropped` (2 unclassified shapes,
  both exit 3 naming the key) + V15's bucket-disjointness rule.
- **R3** An owned floor is read but never compared (a floor that enforces nothing) →
  `test_an_owned_floor_the_run_is_under_is_refused_not_scored` for **both** new floors, plus V15's
  "checked but not claimed as owned" rule, the two-sided arithmetic on `before_share`/
  `surviving_units`, and the refusal-side pin
  `test_checker_refuses_a_below_floor_binding_with_no_under_floor_pair` (a `BELOW_FLOOR`
  whose every pair is MET is a relabel, not a refusal).
- **R4** A declared-but-unreadable owned floor is read as "no floor stated" →
  `test_an_unreadable_owned_share_floor_is_refused_not_read_as_absent` (`"0.2"`, `1.5`, `None`,
  `[0.2]` → `UNREADABLE`).
- **R5** The gate stops accepting a log it used to accept, i.e. the checker's own V15 rules now
  contradict a legitimate binding → the full `tests/test_e1_e2_measurement.py` suite (172 tests
  collected, all green at the final head) and the committed log's structural check
  (`test_the_committed_log_passes_the_structural_check`).
- **R6** The evidence log drifts from the probe's real output (a probe improved without a re-take) →
  **CORRECTED (§9):** not this. `e1-e2.log` is grandfathered by `PRE-REGISTRATION.md` §0 and
  cannot be re-taken at all; the pin that demanded it (`test_the_committed_log_is_what_the_probe_prints`)
  was replaced by `test_the_committed_log_is_frozen_and_stays_unscored`. The probe's printed text
  is pinned against its own output by the note-state tests instead.

**Threat classes** (gate-enforcement):

- `T1` **unclassified-key drop** — a floor in neither bucket is dropped and the run scores →
  `test_a_floor_no_row_owns_is_refused_not_dropped` + V15 disjointness.
- `T2` **owned-floor bypass** — an owned floor declared in a form the probe cannot evaluate reads
  as absent → `test_an_unreadable_owned_share_floor_is_refused_not_read_as_absent`.
- `T3` **accounting/decision divergence** — the printed buckets disagree with the refusal reason
  (a key skipped AND refused, a compared floor not claimed, a skip with no owner) → the three
  V15 accounting pins + `test_checker_refuses_a_scored_log_without_the_floor_accounting`.
- `T4` **denominator substitution** — the E2b floor (and E1's control-arm share) compared against
  a quantity the run did not produce. Cycle 1 found the first cut was only self-consistent
  (both sides printed on the binding line), so the measured side is now anchored to the ARM
  lines: `arm=<name> metric=atomic_points atomic=` is printed per arm and V15 reconciles
  the binding's `surviving_units=` against the scored arm's atomic-Point count, and
  `before_share=` against the control arm's `share=` — the same cross-line anchor `objects=`
  has always had. Pinned by
  `test_checker_refuses_a_binding_whose_measured_side_is_unanchored` (inflated on the binding
  line, and anchor-deleted).
  **Declared residual** (restated at cycle 3 — the cycle-2 wording under-claimed the
  gate): a RECORD-ONLY edit that deletes or weakens an owned floor is caught twice —
  by `tools/e1_e2_census_check.py`'s `check_machine_record`, which reconciles
  `denominator_floors` against the document's own pins block
  (`test_capstone_preregistration_9075.py::test_a_record_denominator_floor_that_disagrees_with_the_pins_fails`
  — "two copies of a denominator that can disagree"), AND by a VALUE pin that already
  exists: `test_capstone_a_rows_10982.py::test_no_existing_bar_moved_the_probe_reads_the_same_seven`
  asserts `bars["floors"] == {"e1_objects": 30, "e1_min_before_share": 0.2,
  "e2b_surviving_units": 30, …}`, where `parse_bars` reads those numbers straight out of
  `preregistration.json`. So even a **coordinated** record + PRE-REGISTRATION-document
  edit that LOWERS either owned floor value reds that test on the record alone.
  **One log-only residual, and it is OPEN — nothing in the tree closes it:** V15 now
  requires all six floor sides on a scored binding line (both bucket names plus the
  declared/measured pair for each owned floor), so the line must be COMPLETE and
  internally consistent. But `floors_owned=` is still the probe's own statement, and a
  log carries no copy of `denominator_floors`: a hand-edited log that claims it owned
  fewer floors, blanks the two declared sides and keeps `objects=` yields ZERO V15
  findings (measured against this suite's own `_scored()` fixture). No code reconciles
  `floors_owned=` against the record — `check_machine_record` lives in
  `tools/capstone_preregistration_check.py` and compares the RECORD with `pins.denominator_floors`;
  it never opens a log, and nothing outside `tools/e1_e2_census_check.py` reads
  `floors_owned=` for reconciliation (the probe WRITES it, by design). So this is a
  known, stated gap, not a covered one — pinned as such by
  `test_a_blanked_pair_is_the_stated_open_residual`.
  Beyond that item, **no residual remains on this plan's declared surface**; the
  copy-consistency of
  record ↔ pins ↔ document is pre-existing coverage this lane relies on and does not
  re-implement.

**Out of scope — must NOT be raised** (note, file, do not chase):

- pre-existing defects outside the diff (e.g. `probes/capstone_measure.py` dropping
  `e1_min_before_share` — that is #10347, a *different* probe);
- the record's `tests/eval/write_path` vs `e1e2_corpus.json` corpus mismatch (a known, documented
  refusal; reconciling the two corpora is a follow-up, explicitly not this issue);
- the record's missing `e1.min_share_reduction` (`prereg-error=TypeError: e1 is absent`) — that is
  the record's threshold shape, not its floor block;
- style/prose preference with no behaviour change.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared surface.

## 9. Correction — the evidence log is FROZEN, and the re-take is withdrawn

**What was planned.** Steps 4–5 and the surface-map row above had this lane re-take
`docs/evidence/7949-capstone/e1-e2.log` byte-for-byte against the current probe's output, because
`tests/test_e1_e2_measurement.py::test_the_committed_log_is_what_the_probe_prints` compared the two.

**Why it cannot be done.** `e1-e2.log` **predates** the pre-registration (`#9507`), which
grandfathers it on five bounds — one of them being that its **content must not have been modified at
or after** the registration's add-commit, because an in-place edit can put a measurement number into
a grandfathered log after the thresholds were written and the add-commit cannot see that
(`docs/evidence/7949-capstone/PRE-REGISTRATION.md` §0 and §7 item 2, enforced by
`tools/capstone_preregistration_check.py` in the `changes` job). §0 states the consequence verbatim:
*"`e1-e2.log` stays unscored forever; the E1/E2 **re-run** must postdate this file."*

Measured, not argued — the re-take made the required check fail:

```text
$ uv run python tools/capstone_preregistration_check.py --dir docs/evidence/7949-capstone \
      --no-corpus --base-ref origin/main
FAIL — 1 problem(s):
  - e1-e2.log: exempted, but it was MODIFIED at 1794290611, which is not strictly before the
    pre-registration's appearance on origin/main at 1792619351 — an in-place edit can put a
    measurement number into a grandfathered log after the thresholds were written, and the
    add-commit cannot see that
```

(`1794290611` = this branch's `daab38f`; the registration's add-commit is `a6085af84` at
`1792619351`.) `main` passes the same command; only the re-take reds it.

**The root cause was the pin, not the gate.** A pin whose only remedy is forbidden is unsatisfiable:
any future change to what the probe prints would re-demand an in-place edit of a frozen evidence
artefact. So:

- `e1-e2.log` is restored to `origin/main`'s bytes — it is not touched by this PR at all;
- `test_the_committed_log_is_what_the_probe_prints` is replaced by
  `test_the_committed_log_is_frozen_and_stays_unscored`, which asserts the load-bearing facts (the
  log is in the pins' `pre_existing_unscored_logs` exemption, and is still the `UNPREREGISTERED`,
  exit-3 artefact that exemption is granted to) and keeps **no second copy** of the "was it edited
  after the registration?" predicate — that has exactly one owner, the ordering gate;
- the probe's reworded E2b note and floor accounting are unchanged, and are pinned against the
  probe's **own** output by the note-state tests, never against the historical log;
- `E1-E2.md`'s `Measured at:` returns to `c2544f54f14` (2026-10-19) and its "Re-running" block now
  writes a NEW log, so it no longer documents an edit the pre-registration refuses.

**What this costs, stated plainly.** `probes/e1_e2_probe.py` no longer has a **reproduction** pin on
*this bundle's committed log*, and cannot have one: reproducing it would mean re-running the probe
over it, which the freeze forbids. The property that pin was reaching for — "a probe improved without
a re-take" — is not enforceable here at all, because the re-take it names is prohibited.

What it does NOT cost is the **anti-tamper** pin, and the difference is the fix rather than a
quibble: a reproduction pin is a moving target, a **content** pin is a constant. The log may never
change again, so `sha256(e1-e2.log)` is one hex string forever, and
`test_the_committed_log_is_frozen_and_stays_unscored` asserts it (`_FROZEN_LOG_SHA256`). That is what
catches an edit the census checker **cannot** see: the checker reconciles the log with *itself*, so it
only notices an edit that makes two of the log's own lines disagree. Editing one of the log's
free-text/`note=` spans is invisible to it — MEASURED on `split-implemented=no note=…`, where the
checker still reports "structurally sound" — and the digest pin reds on exactly that.
(An edit that DOES break the log's arithmetic, such as `real_facts=90` → `91` on both arms, is caught
by the checker's own V4/V5 rules instead; that is a different, already-covered shape.)
Remaining E1/E2 coverage is that
digest, the note-state tests (one per branch), the census check's V-rules over the committed log, and
the ordering gate's content bound.

**Scope note.** Whether the E1/E2 re-run should land as `e1-e2-rerun.log` (the name the doc now
uses) is a decision for the follow-up that reconciles the two corpora — it is named as an example,
not created by this PR.
