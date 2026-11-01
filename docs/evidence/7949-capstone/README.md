---
title: "Capstone evidence — #7949 / #9080 extractor-v4 E1–E5 verdict table"
type: evidence
domain: platform
doc_status: final
created: 2026-10-19
subjects.team: epistemic-team
aboutSubjects: Tortoise extractor v4
aboutObjects: "#7949, #9080, #9075, #9076, #9077, #9073, #9116, #5199, #5333, #7947, #8503, #9596, #9736, #9737, #9738, #9775, EXTRACTOR-V4-ARCHITECTURE.md"
---

# Capstone evidence — `#7949` / `#9080` (extractor-v4, E1–E5)

**Measured at:** `027c8c1ab` on branch `feat/9080-tm` (2026-10-20, after merging `origin/main` at `78ef670d3`) — that
is the stamp of the shipped REFUSAL log (`capstone.log`), and it is kept because the file is
kept. ⚠️ **`027c8c1ab` is branch-only, and the honest statement of what that costs is narrower than
"unrecoverable".** It is an ancestor of `feat/9080-tm` (the branch it names) — and, measured, of
`refs/pull/9089/head` (`7c9c34d84`) as well — while `git merge-base --is-ancestor 027c8c1ab
origin/main` exits 1. So which refs still carry it is a fact about the remote at lookup time, not a
property of this document: a shallow or single-branch clone may not reach it, and no ref here is
promised to outlive the branch deletions that follow a merge. That is why it is a STAMP — a
convenience, not a durable pointer — and why the RECORD does not depend on it: `capstone.log` is
committed in this tree and is read as a file. The SCORED run below is stamped separately (**Scored at** `e0fc940b5`,
2026-10-23T23:16:55Z), because it is a different artifact from a different program
(`probes/capstone_measure.py`).

**Blast radius — the files this branch touches** (`git diff --name-only origin/main...HEAD`; the
COUNT is deliberately not restated here — it changed twice while this was open, and a stale
integer in the one paragraph a reviewer bounds the PR by is worse than no integer):
the bundle itself (`docs/evidence/7949-capstone/**`, `tools/capstone_verdict_check.py`,
`tests/test_7949_capstone_verdict_check.py`, `tests/test_7949_capstone_measure.py`) **and the
ordering gate that judges it** (`tools/capstone_preregistration_check.py` +
`tests/test_capstone_preregistration_9075.py`, added in the `#9089` review round: the gate
exempts this bundle's REFUSAL log on its CONTENT, so a reviewer who bounds this PR by its own
reading must see it); its registration (`config/ci-surfaces.yml` `core`/`tier1`/`durations`
arms, `docs/00_index.md`); the selection edits `tools/ci_selection.py` +
`tests/test_ci_selection.py` **remove** a `CORE_ALSO` row this branch had added and could not
justify (the bundle prefix `docs/evidence/7949-capstone/`, landed by #9076, already selects
`core` for a PROBE-ONLY diff — measured, not assumed), and **add** one for the gate source
(`tools/capstone_verdict_check.py`), because a GATE-ONLY diff selected no surface at all while
the bundle prefix covers only the probe/evidence paths (pinned by
`tests/test_ci_selection.py::test_the_capstone_change_selects_core_not_tier1`) — and add a pin
that no `CORE_ALSO` row is shadowed by a parent prefix; the surface re-cut the new caller forces
(`config/surface-manifest.yml`, `docs/product/mcp-sdk-surface.md`); and two test-side edits —
`tests/test_ci_check_repair.py` (the `gh` launcher stub) and
`tests/test_uri_env_mutations_declared.py` (this suite's `TORTOISE_DB_URI` env-control
declaration, plus the removal of a dead duplicate row).
**Nothing under `tortoise/` changes behaviour and nothing instruments the extractor** — this
bundle installs no wrapper, spy or step-composition anywhere in the measured path, so the E4
row's "no monkeypatch" clause holds on the fact that matters. The `sdk.py`/`extractor_v2.py`
lines this bundle cites are read, not changed.

**Decision being proved:** the epic's own closing rule — *"Close this epic only when
E1–E5 all have evidence attached to this issue — not when the children close. A child
closing is a claim; this epic is where the claim is checked against the pipeline
running end to end."*

> **Verdict: 0/5 rows green. THREE rows are RED with measured numbers, TWO are BLOCKED
> because the frozen metric cannot be scored on the artifact that exists. The epic is NOT
> closeable, and this bundle says so mechanically:**
>
> | row | frozen bar | measured | verdict |
> |---|---|---|---|
> | **E1** | `after_share <= 0.50 × before_share`, over all `:Object` nodes, floor **30** | before **0.600000**, after **0.600000** over **25** Objects → below the floor, so **REFUSED** (the arithmetic would fail: the bound is 0.300000) | ⛔ **BLOCKED** |
> | **E2** | E2a `>= 13 of 14` value-bearing units; E2b fused share `<= 0.05` | E2a: the frozen **14-unit** denominator is carried by **no committed artifact** (the artifact counts 42 value literals, before = after); E2b: **1/114 = 0.008772** | ⛔ **BLOCKED** (E2a unmeasured) |
> | **E3** | `= 1.000` (zero unanchored), floor **30** derived nodes | **148 / 433 = 0.3418** — `Point` 141/338 anchored, `:Object` **0/50**, `:Event` **7/45** | ⛔ **RED** |
> | **E4** | `= 15 of 15` planted operator edges wired AND persisted | **2 / 15** | ⛔ **RED** |
> | **E5** | ratio `>= 0.90` **AND** raw absolute `>= 0.80` | ratio **1.0833** (passes) but raw absolute **0.4800** (fails), n = 25 | ⛔ **RED** |
>
> E1's and E2's proxies are printed rather than dropped (E1b 90/90 real facts; E2b 1/114) — and
> neither proxy is allowed to become the verdict. A refused measurement is **neither a pass nor
> a fail**; the pre-registration says so in its own words (`below_floor`), and the table's
> `BLOCKED` is that word. Writing RED there would report a verdict the population is not
> entitled to; writing GREEN would be worse.

---

## The measured run

```bash
export TORTOISE_DB_URI="$(uv run python tools/test_lane.py uri | sed -n "s/^export TORTOISE_DB_URI=//p" | tr -d "'")"
uv run python docs/evidence/7949-capstone/probes/capstone_measure.py \
    --prereg "$(git rev-parse HEAD)"
# G0 pre-registration e0fc940b5… READ (schema capstone-preregistration/v1)
# G1 corpus sha256:dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc MATCHES the frozen pin
# arm TORTOISE_VET=1 TORTOISE_CLASSIFY_LATER=1 TORTOISE_VALUE_GATE=<unset> TORTOISE_S2_DECISION_SEAM=<unset> (read back from os.environ)
# → docs/evidence/7949-capstone/capstone-measured-e0fc940b.log
```

⛔ **The arm needs no exported flags, and that is a required property, not a convenience.**
`probes/capstone_measure.py` FORCES `TORTOISE_VET=1` and `TORTOISE_CLASSIFY_LATER=1` in-process
(restoring the previous values in a `finally`, exactly as `e1_e2_probe.run_arm` does), pins
`TORTOISE_VALUE_GATE` **and** `TORTOISE_S2_DECISION_SEAM` unset — both also switch S2.2 on (the
latter is #5087's arbiter, so `_vet_enabled()` is True and a billable call would run inside an arm
declared OFF) — so an ambient value would silently change
the arm — and then **reads the values back out of `os.environ`**. The log's `arm:` line is that
read-back, never a typed literal, and a flag that does not read back as set REFUSES the run. The
earlier stamp of this bundle printed `arm: TORTOISE_VET=1 TORTOISE_CLASSIFY_LATER=1` while setting
neither: a re-run following a recipe that exported only `TORTOISE_DB_URI` measured the **OFF**
population under a log declaring both ON. Pinned by
`tests/test_7949_capstone_measure.py::test_the_measured_arm_is_forced_in_process_and_restored`,
`::test_a_flag_that_does_not_read_back_refuses_the_run` and `::test_the_run_happens_inside_the_forced_arm`.

`probes/capstone_measure.py` is the instrument this row needed and PR #9089 did not have.
`capstone_run.py` drives the real entry point and then reads its step witnesses off the receipt it
gets back — `stats["step_witness"]`, the `#9116` seam (see "The eight steps" below). Before that
seam the receipt carried `stats` alone, so the probe read four witnesses off a surface that was
not there, refused at G5 for **every** input and its table was 0/5 `BLOCKED` with no numbers
(#9737). It now refuses on what the stage's own block says — or, on a receipt with no
`step_witness` at all (a replay, a keyless capture, the M2 lane), as the **INSTRUMENT** gap, never
as a pipeline that skipped the work. The measurement probe keeps that refusal contract intact and
measures the other way round: G0 reads the frozen record out of the **commit**
(`git show <sha>:docs/evidence/7949-capstone/preregistration.json`), G1 compares this checkout's
`fixtures_hash` with the frozen pin (the freeze the record actually made — `capstone_run.py`'s
G1 wants a corpus file's sha, which the record does not freeze, so it refuses one gate later),
every bar is **parsed out of the record** by `parse_bars` (none is typed in the probe), and then
one pass over the seven pre-registered sessions runs through
`TortoiseSDK(graph_name=<fresh disposable graph>).capture_session(turns, session_id=<sid>)`.

⚠️ **On the stamp.** The log's `# head:` is `e0fc940b5` — the commit the run was stamped at and the
commit G0 read the frozen record out of — while the probe BYTES that produced it are this
worktree's, i.e. **the tree was dirty at run time** (the fixes in this round are exactly that diff).
That is disclosed rather than smoothed: `git diff e0fc940b5 -- docs/evidence/7949-capstone/probes/`
is the difference between the measuring program and the stamp. Re-running once these changes are
committed reproduces the same row SET from a clean tree, under a `<new-head8>` filename — a row
moves only that way, and the file it replaces is removed (the supersede chain is below). (The census
NUMBERS are LLM-dependent and move between passes; the verdict does not — see E3 below.) The probe's
own delta AFTER this run is two unreached strings — the `_pass_ran` docstring clause naming the
classify pass, and the wording of the present-but-unobserved refusal branch — and neither renders a
line of this log: both surfaces were ABSENT on this run, so the refusal branch never fired (S2.2/S2.3
below carry the absent-surface cause). Disclosed rather than smoothed, like the dirty-tree delta
itself.

⚠️ **A later probe delta DOES render, and the log is not edited for it.** #9553's insertion into
`tortoise/sdk.py` (+86 lines) moved the symbols this bundle cites, so the `file:line` numbers in the
probe's `E3_RULE` and in the S2.2/S2.3 cause strings were re-derived — and those strings RENDER into
a log. The frozen `capstone-measured-e0fc940b.log` therefore still carries the cites **as-of its own
stamp**, and those are the numbers listed here — the ones the log itself renders, exactly as it
renders them: `sdk.py:12401` (the meta fold), `sdk.py:12682` (the
references-edge writer), `sdk.py:2367` (`_TURN_WRITE_CYPHER`), `edges.py:467`
(`_DERIVATION_REFERENCES_LABELS`), `edges.py:494-503` (`_anchor_on_create` / `_DERIVATION_ANCHOR_SET`,
re-derived by this bundle to `774`/`782`), `edges.py:2168-2192` (`link_source_to_event`),
`tortoise/extractor_v2.py:8278-8285` (the **classify-later** surface, as the S2.3 cause string cites
it), and `tortoise/extractor_v2.py:8288-8292` (the **VET** surface, as the S2.2 cause string cites
it). **They no longer resolve at this head** — they are the stamp's, and a fresh `+N` bump of them
would name nothing at all (the mechanical-bump defect this paragraph exists to prevent). The LIVE
positions are the ones the step tables below print, and every one of those is re-derived and
checked against the tree by the cite sweep.
The `8278-8285` END was already one
line long at that stamp (`8284` closes the block; `8285` opens the next one) and is corrected to
the `result["classify_later"]` block at `:9217-9223` here, not in the log. The `8288-8292` cite is
the `result["vet"]` block, and it is corrected to `:9227-9231` here — **not** to the `:8534-8538`
this bundle's issue (#9919) predicted, because the tree moved again between that reading and this
head; that is precisely the drift this paragraph exists to stop a reader from re-deriving by
number. (The SYMBOL is what makes
this line checkable: a bare number in this paragraph is read by no pin — the paragraph is
`_BUNDLE_HISTORICAL`, deliberately — so a reader re-deriving it must land on the block the sentence
names, not on the number alone.) That is deliberate: a scored log is a measurement record and the bundle's rule is
that a row moves only by RE-RUNNING the probe and removing the file it replaces, never by editing a
number in place. Read this README (and the probes it pins) for CURRENT cites; read the log for the
numbers measured at `e0fc940b5`. A re-run under a new `<new-head8>` is the supersede path.

⚠️ **One clause of this log was corrected IN PLACE, and here is which.** The `row E3 note` (human and
machine copies) and the `e3 point composition` line described the *superseded* reading by splicing
THIS run's counts into it with a typed `= 1.000000` — so the record stated a historical fact that
never happened (`141/141` rather than that run's `132/132`), and would have printed
`140/141 = 1.000000` on a run that lost a link. The prose now states the circularity as a
CONSTRUCTION (a denominator defined by the link it reports can only read `1.000000`) with no live
count in the historical claim, and the composition line now names where the `132/132` figure can be
retrieved (`git show 6020cbd5f:docs/evidence/7949-capstone/capstone-measured-8a8bbcba.log`):
superseding DELETES the tree entry, not the history — what it does cost is citability as a row's
`evidence`, which V4 resolves against the tree.

⚠️ **That retrieval is REF-QUALIFIED, not durable — and the earlier wording implied otherwise.**
`6020cbd5f` is a **branch-only** commit: `git for-each-ref --contains 6020cbd5f` names this branch
and its remote, never `origin/main` (`git merge-base --is-ancestor 6020cbd5f origin/main` exits 1).
This repo squash-merges, so a branch-only commit is unresolvable **by design** the moment that ref is
deleted — not by accident, and V11 in `tools/capstone_verdict_check.py` records exactly that ("a
squash-merged branch sha NEVER reaches main"). While the branch exists the command above works;
after the merge the route is the **PR ref** — `git fetch origin refs/pull/9823/head`, whose history
carries the commit, then the same `git show <sha>:<path>` (verified for both `6020cbd5f` and
`8a8bbcba2`). The figure is also stated in this README, so it does not depend on the retrieval at
all.

**No measured number moved** — neither corrected span carries a figure belonging to this run; only
that explanatory clause is different, and the probe at this head (now `e3_row_note`) emits the
corrected form.

| session | ok | mode | extracted | edus | points | operators | search |
|---|---|---|---|---|---|---|---|
| `wp01_quarry_debug` | ✅ | `llm:deepseek-direct` | 26 | 26 | 26 | 5 | 15 queries, not degraded |
| `wp02_lumen_refactor` | ✅ | `llm:deepseek-direct` | 22 | 22 | 22 | 4 | 15, not degraded |
| `wp03_ember_design` | ✅ | `llm:deepseek-direct` | 18 | 24 | 18 | 7 | 15, not degraded |
| `wp04_aurora_perf` | ✅ | `llm:deepseek-direct` | 17 | 20 | 17 | 4 | 15, not degraded |
| `wp05_retro_writeup` | ✅ | `llm:deepseek-direct` | 19 | 23 | 19 | 5 | 15, not degraded |
| `wp06_quarry_rollout` | ✅ | `llm:deepseek-direct` | 23 | 22 | 23 | 9 | 15, not degraded |
| `wp07_bluepeak_followup` | ✅ | `llm:deepseek-direct` | 16 | 22 | 16 | 9 | 15, not degraded |

Seven of seven sessions returned `ok=True` with **zero errors**; 159 episode units, **141 claims** and
**433 derived nodes** were written to one fresh graph. The arm runs with `TORTOISE_VET=1` and
`TORTOISE_CLASSIFY_LATER=1` **forced ON in-process and read back** (the deployed config leaves both
OFF), the same convention #9076's E1/E2 arms used, so S2.2 and S2.3 are on-path for the pass even
though the receipt cannot witness them.

## The eight steps — 6 witnessed, 2 not, and why

| step | witnessed? | witness |
|---|---|---|
| S0 READ | ✅ | `stats.edus=159`, `stats.chunks=7` across the seven sessions |
| S1 NARRATE | ✅ | `stats.llm.by_stage.s1` calls = 7 |
| S2.1 EXTRACT | ✅ | `stats.llm.by_stage.s2` calls = 7; `entities=77 events=38 points=141` |
| S2.2 VET | ❌ **#9116** | measured at `e0fc940b5`, BEFORE the seam: the pass was ENABLED (`TORTOISE_VET=1`, forced in-process and read back), and its surface **exists** — `extract_session_v2` emits `result["vet"]` UNCONDITIONALLY at `tortoise/extractor_v2.py:9227-9231` — but the meta fold then forwarded `stats` alone, so whether it DID any work was not observable through the receipt (measured: 142 stats keys and 18 receipt keys inspected, `vet` in neither). The fold now COPIES the stage's own counts as `stats["step_witness"]["vet"]` (`tortoise/sdk.py:15010`), so this ❌ is the frozen run's answer, not the seam's reachability |
| S2.3 CLASSIFY | ❌ **#9116** | same layer, same run: `result["classify_later"]` is built at `extractor_v2.py:9217-9223` and was dropped at the same fold, which now copies it as `stats["step_witness"]["classify_later"]` (`tortoise/sdk.py:15010`) |
| S3 RESOLVE | ✅ | `stats.search_queries=105 > 0` and `stats.search_degraded=false` on every session (a degraded search is not S3) |
| S5 CONNECT | ✅ | the graph write: 433 derived nodes (`Point` 338, `Object` 50, `Event` 45); `stats.operator_endpoints_minted=0`; operator-shaped relationships `IMPL=57 INPUT=58 NAND=10` |
| S6 COMMIT | ✅ | 338 `:Point` nodes persisted, 170 carrying the commit's `eventId` stamp, 358 `:GraphEvent`s |

**6 of 8.** The two that are missing are missing from the INSTRUMENT: the arm was forced and read back, so both passes were ENABLED on this run — but whether either did work was NOT observable through this surface, which is exactly why the surface was missing (a flag-on block that idles, or a fail-open pass, leaves the same empty stats). So the honest statement is "enabled and not witnessed", not "ran and was hidden" — and the receipt did not say so either way. Working around that by reading a wrapper's state is
exactly the instrumentation E4's row forbids, so the honest output is `UNOBSERVED` with its cause
and file:line — and any table the probe emits records `steps_observed` **and**
`steps_unobserved` so the count cannot be read as 8. ⚠️ **This is the `e0fc940b5` run's count.**
`#9116`'s seam has since landed, so a re-run at a post-seam head can witness **S2.2**; **S2.3**
moves only once its classify pass carries a non-empty `s2`/`union` — on the measured corpus that
pass is IDLE (`enabled: true`, both counters empty), so a re-run there still reports S2.3
unobserved and G5 still refuses. The two scored rows above keep the count the run measured.

⛔ **The count is a measurement, not a constant.** `observe_steps` looks for a `vet` /
`classify_later` surface in what the run actually holds (the receipt's top-level keys and its
`stats` block), and reports the step OBSERVED only when that surface **shows its pass DID work** —
a non-empty `s2` or `union`, with `enabled` not `false`. The FLAG alone is never enough (see
below). When it is not a witness, the cause reports the number of keys it really inspected instead
of quoting a remembered one. A step is never *assigned* unwitnessed, and every step ends up in
exactly one of the two sets (a hole or an overlap refuses the run). So the epic's own GREEN gate —
V5, all eight steps observed — stays reachable: `#9116`'s seam makes both rows OBSERVABLE (each
step's own block is on the receipt), and S2.2 then flips on a re-run at a post-seam head (S2.3
flips only once its pass does work, which the measured corpus' idle classify pass does not). It
did **not** come for free and this bundle does not claim it did: the seam landed with this probe's
observer rewritten (`observe_steps` reads `stats['step_witness']` instead of guessing at a surface
the receipt never carried) and with the pin that certified the old unreachability replaced by the
one that pins the wiring.

⛔ **And PRESENT is not RAN.** `extract_session_v2` emits both keys UNCONDITIONALLY — on the
flag-OFF arm and on its early-return path the block is `{"enabled": False, "s2": {}, "union":
{}}` (`extractor_v2.py:8864-8868`, `:9217-9231`). An acceptance test of "a non-empty dict"
therefore takes an OFF block as the witness for *the pass ran* and prints `step S2.2 WITNESSED …
enabled=false, s2={}`: the moment `#9116` copies the sibling key — exactly what this bundle
instructs — both steps would be WITNESSED on **every** run, including one measured with the
flags OFF. That is the "OFF population under an ON label" failure one layer up, and V5 is the
only closing gate for the step count. A block that contradicts itself (`enabled: false` with
non-empty S2 stats) fails CLOSED.

⛔ **And the FLAG is not ACTIVITY** — the sharper half, and the one this rule got wrong first. The
first tightened rule accepted `enabled: true` on its own, reading it as "the extractor's OWN
statement that the gate was open". It is not that: `enabled` is the FLAG, computed BEFORE the pass
is reached. `vet_enabled` is computed at `extractor_v2.py:8848`, i.e. BEFORE the
`if not edus:` early return (`:8852`), which emits literally
`"vet": {"enabled": vet_enabled, "s2": {}, "union": {}}` (`:8868`), and
each VET pass is gated on the LIST — `if vet_enabled and embed_list:` (`:8936`), `if vet_enabled and
complete_list:` (`:9058`). So a session with the flag ON and an empty list emits `enabled: True`
with empty counters and no pass ran (a FAILED vet pass leaves the same shape: `_run_vet_pass` returns
`{}` on its fail-open path, `:8727-8730`). Because `_witness_block` returns the first positive
ANYWHERE on the run, ONE such session out of seven made S2.2/S2.3 WITNESSED (6/8 → 8/8) — and V5
would then have passed on a step nobody observed. The rule now requires the flag AND positive
activity, and a flag-on/zero-activity block gets its OWN cause ("flag on, no pass activity") that is
distinct from both the OFF cause and `#9116`'s absent-surface cause: three negatives, three places
to look.

⛔ **And that cause cites each surface's OWN code — the CLASSIFY pass is not the VET pass.** It was
first written once and templated for the surface NAME, so the S2.3 line argued from `vet_enabled`,
the `if vet_enabled and …` gates and `_run_vet_pass` — code the classify pass never reaches. The
classify pass has its own flag (`classify_later`, `:8817`), its own early-return block
(`:8864-8867`), its own gates — `if classify_later and embed_list:` (`:8948`) around `if items:`
(`:8951`), and `if classify_later and complete_list:` (`:9073`) around `if items:` (`:9083`) — and its
own fail-open `except` (`:8964` / `:9094`), so an idle classify block comes from no embed list **or
no classify items**, not from a vet gate. The cites now live in ONE table keyed by the surface
(`_IDLE_CITES`), and a surface added without its own entry fails loudly rather than borrowing
another pass's code. Pinned by `test_the_flag_on_cause_is_per_surface_so_a_swap_cannot_pass` and by
per-step, mutually exclusive assertions in `test_the_flag_alone_is_not_a_witness_and_has_its_own_cause`
— swapping the two causes fails.

⛔ **And "the flag was set" is not "the pass ran" IN THE SHIPPED LOG EITHER.** The absent-surface
cause used to read "the arm forced `TORTOISE_VET=1` … **so the pass RAN**", supported only by the
`arm:` read-back — the equation `_pass_ran` itself rejects two sections up. A forced flag read back
proves the flag; a classify or vet block can legitimately carry `enabled: True, s2: {}` (an empty
list, no classify items, or a fail-open `except`) while doing nothing, and that shape is
indistinguishable through a surface that is missing. The cause now says the pass was **ENABLED** and
that whether it did work is not observable here; the `#9116` attribution — the surface is emitted
unconditionally at `:9217`/`:9227` and now reaches the receipt through the seam at the meta fold
`sdk.py:15010` (`"stats": _capture_stats_with_step_witness(out, len(extracted))`) — is updated
accordingly, and the "where the fix must target" line with it.

Pinned by `test_a_witnessed_vet_surface_makes_s2_2_observed` (8/8 witnessed when the surface is
there WITH activity), `test_a_stats_home_for_the_classify_surface_also_counts`,
`test_a_surface_that_says_the_pass_did_not_run_is_not_a_witness` (the OFF block stays
UNOBSERVED and the count drops to 6/8),
`test_the_witness_rule_requires_the_flag_and_a_pass_that_did_work`,
`test_the_flag_alone_is_not_a_witness_and_has_its_own_cause` (`{"enabled": True, "s2": {},
"union": {}}` stays UNOBSERVED, 6/8, with the distinct cause and NOT the `#9116` one),
`test_the_flag_on_cause_is_per_surface_so_a_swap_cannot_pass` (the per-surface cite table, with
mutual exclusion — a swapped pair fails),
`test_the_unobserved_cause_counts_the_keys_it_actually_inspected` and
`test_every_step_is_witnessed_or_given_a_cause`. (The shipped-log test still asserts THIS
artifact's 6/8 — that pins the record, not the probe's capability.)

## E3 — the census, and the contradiction it measures

The anchor is a property of the **derivation link**, not of the node
(`tortoise/projection/edges.py:774-782` `_anchor_on_create` / `_DERIVATION_ANCHOR_SET`), and the
derivation target labels are `_DERIVATION_REFERENCES_LABELS` (`edges.py:747`: `Event`,
`Document`). ⛔ **The two directions are not interchangeable**, and the first stamp of this bundle
got one of them backwards: `extractedFrom` points FROM the derived node
(`(Point)-[:extractedFrom]->(GraphEvent|Event)`), while `references` points AT it
(`(Source)-[:references]->(Event|Document)` — `link_source_to_event`, `edges.py:2832-2857`,
reached from the capture path at `sdk.py:15291` (`proj.link_source_to_event(source_key,
event_id)`)). So the rule now applied — printed in the log,
applied by one Cypher query per label — is:

> a derived node carries a version anchor **iff** it is the **source** of at least one
> `extractedFrom` relationship whose `sourceVersion` is a non-empty string, **or** the **target**
> of at least one `references` relationship whose `sourceVersion` is a non-empty string.

The old single-direction query printed `references=7/7` anchored beside `:Event 0/41` — two claims
that cannot both be true, because those 7 anchored edges ARE the 7 anchored Events. The correct
count is `:Event` **7/45** (`anchored_in=7`, `anchored_out=0`); the census prints both directions
per label so the pair can never disagree again
(`test_the_e3_census_counts_references_inbound_and_extractedfrom_outbound`).

| label | nodes written | anchored | unanchored | of which, inbound `references` |
|---|---|---|---|---|
| `Point` | 338 | 141 | 197 | 0 (all 141 via outbound `extractedFrom`) |
| `Object` | 50 | **0** | 50 | 0 |
| `Event` | 45 | **7** | 38 | **7** |
| **total** | **433** | **148** | **285** | 7 |

Relationship-level: `extractedFrom` **141/141** anchored, `references` **7/7**, and
`aboutObject` **0/211**, `mitigated_by` 0/9, `CONTAINS` 0/300 (those are not derivation links).

⇒ **E3 = 148/433 = 0.3418, RED — and the RED is the decision, not a defect.** `:Object` mention
links are identity links that carry no `sourceVersion` **by design** (owner-approved option A,
2026-09-25, #5199), so the frozen denominator — which counts `:Object` — makes `= 1.000`
unreachable **as registered**. #9736 owns the choice: re-register the denominator as the classes
the model treats as derivations, or reopen the model. A lane cannot pick, and this bundle does not.

⛔ **E3's Point denominator mixes classes that CANNOT derive, and the figure is taken over the
writer class — not over the link.** Of the 338 `Point` nodes: **159 mechanical turn rows**
(`_write_capture_turns`, `_TURN_WRITE_CYPHER` at `sdk.py:2418`, wired only by `CONTAINS`),
**141 extraction claims**, **29 operator Points** and **9 other Points** — each class read off the
property its **writer** stamps (`is_episodic`, `is_operator`, an `extractedFrom` edge), none
hand-typed, and each printed with its own anchored count.

Two of those classes are **structurally incapable** of an `extractedFrom` link: turn rows carry
none BY CONSTRUCTION, and operator Points are written by `create_operator` (the IMPL/NAND/INPUT
operator writer, `tortoise/sdk.py`), which never writes one — measured here as 0 of 29 anchored.
The first correction excluded them and then took the figure over **`extraction_claims` alone** —
which was CIRCULAR: that class is DEFINED by `deriv > 0`, i.e. by the very link the figure reports,
so it could not say anything but `1.000000` and it could not move when a link was LOST (a node that
drops `extractedFrom` leaves the class and joins `other`). Reproduced on the pre-fix probe:
`derivation_only` returned `1.0` for `other_points` of 0, 1, 9 and 40, and dropping one link moved
`132/132 → 131/131`, still `1.000000` — the node-level restatement of the relationship line
`extractedFrom=132/132`.

So the figure is now **`anchored / expected-to-derive`** over the WRITER class: non-episodic,
non-operator Points = extraction claims **+ `other`**. `other` is not a footnote any more — it is
the class where a **lost derivation link lands** (Points with no `extractedFrom` that are neither
turn rows nor operator Points) — so it sits INSIDE the denominator and an unanchored node in it is
visible in the figure. On this run: **`141/150 = 0.940000`**, and dropping one of the 141 links
moves it to `140/150`. That is also why it is not the `141/179 = 0.7877` a denominator summing
operator Points reports (a shortfall nothing could close): the incapable classes are named with
their own anchored counts instead of being folded in, and the frozen denominator is **not** changed
unilaterally — the "all derived nodes" reading (148/433, and the 285 unanchored nodes — nearly all
turn rows, `:Object` mention links and operator Points, none of them derivations) stays printed
beside it. Without that, those 285 read as "extraction claims lost their provenance" when most are
not derivations at all. What the figure cannot say by itself is whether a given unanchored `other`
Point LOST a link or was minted by a writer that never derives — that attribution is #9736's open
question, and the line prints the composition so it is argued from numbers. Pinned by
`test_the_derivation_figure_divides_by_the_writer_class_not_the_link` and
`test_the_derivation_figure_moves_when_a_derivation_link_is_lost`.

The floor is met (433 ≥ 30), so this is a scored measurement and not a refusal. The *numbers* move
with LLM non-determinism; the *verdict* does not — every stamp of this probe has been RED, because
`:Object` mention links never carry an anchor.

## E4 — the committed operator receipt, read verbatim

`tests/eval/write_path/receipts/w2b-operator-2026-10-19.json`:

```text
operator_audit: planted=15  edge_correct=2  content_ok=8  operators_total=27  operators_provenanced=27
run_id=w2b-cef50160a1e2  date=2026-10-19T01:39:26Z  commit=6164c683c
corpus_hash=sha256:943a99ec619b4c67b8e31fefb90316ea023bbaddd1905d835b9d930243518324
judge_pin=w2-write-path-mechanical-v2  verdict=regression  failure_origin=gate_regression
```

⇒ **E4 = 2/15, RED.** Two provenance facts a reader needs, both stated in the log rather than
smoothed over: the FILE postdates the pre-registration (added 2026-10-22) but the MEASUREMENT
does not (`date=2026-10-19T01:39:26Z`), and its `corpus_hash` is **not** the frozen
`fixtures_hash`. The row is therefore a non-green measurement on **its own** corpus revision,
and the gate (V7) requires that digest to appear in the cited artifact — a declaration the
artifact does not record is a finding. The mechanism is #8503 (the planted edges are not formed
at all); the receipt's retrievability rate is unmeasured (#9596).

## E5 — the falsifier, re-scored against this commit's gold

```bash
uv run python -m tests.eval.write_path.score_qualifiers \
    --sessions wp01_quarry_debug wp02_lumen_refactor wp03_ember_design wp04_aurora_perf \
               wp05_retro_writeup wp06_quarry_rollout wp07_bluepeak_followup \
    --arms-dir tests/eval/write_path/receipts/7947-arms-2026-10-18 \
    --out docs/evidence/7949-capstone/e5-rescore.json
```

```text
raw        loss 13/25 = 0.520000  recovery 0.480000  recall 71/90 = 78.9%
narrative  loss 12/25 = 0.480000  recovery 0.520000  recall 73/90 = 81.1%
ratio 0.520000/0.480000 = 1.0833   (bar >= 0.90 — PASSES)
raw absolute             0.4800    (bar >= 0.80 — FAILS)
⛔ INCOMPLETE RECEIPTS IN THE HOLDOUT: wp07_bluepeak_followup:raw  partial=True
```

⇒ **E5 = RED**, on the record's own second bar — and this is exactly why the record required
two: the ratio alone reads GREEN here while the narrative arm keeps half the qualifiers the raw
arm loses. The re-score grades the 2026-10-18 arm receipts against **this** commit's gold (the
gold is inside `fixtures_hash`); the receipts themselves are older, which is disclosed in the
row's note. #9738 is the row's blocker and carries the rest: §10's prior measurement was visible
when the record said none was, and the 25-bound (this instrument) vs 29-marker-sentence (the
record) populations must be reconciled before either number is called final.

## E1 / E2 — why the two rows are BLOCKED, not RED

`e1-e2.log` is the only E1/E2 artifact, and it says what it is:

```text
e1 before_share=0.600000 after_share=0.600000 reduction=-0.000000 threshold=n/a observed=NO_REDUCTION verdict=UNPREREGISTERED
e1-control before_real_facts=90 after_real_facts=90
e2-state before=42 after=42
e2-atomic before=114 after=114 fused_after=1
prereg=UNPREREGISTERED corpus=e1e2_corpus.json corpus_sha256=e78d306c…
result=UNPREREGISTERED
```

Three independent reasons the E1 number cannot become a verdict:

1. **Its denominator is 25 Objects**, under the frozen floor of **30** (`arm=before
   metric=objects total=25`). The record's own rule for that is `below_floor: "REFUSED, which is
   neither a pass nor a fail"`. The arithmetic would fail (0.600000 > the 0.300000 bound), and
   the bundle prints exactly that — but a refused measurement is not a scored one.
2. **Its population is not the criterion's.** The 25 names are the owner-labelled audit set
   (`tests/eval/write_path/salience_labels.jsonl`), not "all `:Object` nodes written by the run".
3. **Its corpus is not the frozen corpus** (`e78d306c…` ≠ `dd7e85…`), and the artifact declares
   itself `UNPREREGISTERED` — the record's `interface_note` says a run handed a different corpus
   must be refused, never scored.

E2's composite is E2a (14 value-bearing planted units strictly surviving) **and** E2b (fused
share). E2b measures **1/114 = 0.008772**, comfortably inside `<= 0.05` — printed, and NOT used
to green the row, because the artifact counts atomic *Points*, not surviving planted units. E2a's
frozen denominator is the **14 value-bearing units** derived by §3.3's rule ("`verbatim_anchor`
contains a digit" minus two named exclusions) — and **no committed artifact counts it**. The
artifact's no-loss control (42/42 value literals, 90/90 real facts) is consistent with E2a but
does not compute it. #9775's render half — the state-value carve-out on every prompt-building path —
is closed (PR #10382); what still blocks E2 is `#10360`: nothing enforces retention, and the 1-of-14
value loss sits upstream of the filter.

**What E1 would take, precisely.** The measurement run DOES produce a pre-registered-corpus
population large enough to score E1 — **50 `:Object` nodes**, and this pass's bare-definite share
is printed in the log as the `e1-aux` line (6/50 = 0.12, against the E1b proxy 90/90). What it does not
produce is a **before** arm: E1's criterion is a
*ratio between two arms*, and two independent captures would confound the gate with LLM
non-determinism — which is exactly why the bare-definite share is not quoted as a trend: the same
probe on the same corpus has produced double-digit bare counts on one pass and zero on the next
(6/50 in the shipped log; **0/59** at the superseded stamp `7c9c34d84`, whose log is not in the tree
— retrievable with `git show 8a8bbcba2:docs/evidence/7949-capstone/capstone-measured-7c9c34d8.log`,
because superseding DELETES the tree entry and not the history; what it costs is citability as a
row's `evidence`, which V4 resolves against the tree. **That route is ref-qualified like the one
above:** `8a8bbcba2` is branch-only, so after the squash-merge the same `git fetch origin
refs/pull/9823/head` + `git show` applies; and the `0/59` figure is stated here, so it does not
depend on the retrieval).
So E1 needs the controlled design #9076 built — one extraction, both arms applied to the SAME
candidate population (`assert_one_gate_population`) — pointed at `tests/eval/write_path` and made
to persist. That is work, and it is recorded here rather than approximated by a ratio of two runs
that nobody controlled. The mechanism that must move the share is an owner ruling on
removal/relocation (**#5333**).

## Two artifacts, and why `capstone.log` is not overwritten

| file | what it is |
|---|---|
| `capstone.log` | the shipped REFUSAL record (`capstone_run.py`, stamped `027c8c1ab`): G0 refused before any model call, 5 rows, no numbers |
| `capstone-measured-e0fc940b.log` | the SCORED run (`capstone_measure.py`, stamped `e0fc940b5`, generated 2026-10-23T23:16:55Z): 3 RED + 2 BLOCKED, every row carrying its artifact |
| `e5-rescore.json` | the E5 re-score artifact the E5 row cites (written by the probe) |

⛔ **Each earlier stamp is SUPERSEDED and its file is gone, deliberately.** `cf98e15f1` was written
by a probe with two defects this bundle has since fixed — it printed an arm it never set (so it
measured the default-OFF population under a log declaring both passes ON) and it counted the E3
anchor in one direction (so its `:Event 0/41` contradicted its own `references=7/7`). `7c9c34d84`
fixed both, but its envelope was stale: every `tortoise/sdk.py` line it cited (`:12196` for the meta
fold, `:12477` for `link_source_to_event`, `:2306-2331` for `_TURN_WRITE_CYPHER`) had moved under
it when `main` advanced, so the one instruction it carried for #9116 — "the fix targets
`sdk.py:12196`" — pointed into unrelated code. `8a8bbcba2` fixed
the OFF-block witness hazard, but its rule accepted `enabled: true` on its own — and `enabled` is the
FLAG, not activity (see "The eight steps" above), so a single flag-on/empty-list session could have
made V5 pass on a step nobody observed; its derivation figure was also circular (`132/132`, a class
defined by the link it measures). `6020cbd5f` (the stamped run THIS one replaces) fixed that rule but
left two prose defects this round repairs: its flag-on/no-activity cause was templated for the surface
NAME while every citation in it was the VET pass's, so the S2.3 line sent a reader to code the
classify pass never runs (`classify_later` at `:8817`, its `if classify_later and …` / `if items:`
gates at `:8948`/`:8951`/`:9073`/`:9083`), and its absent-surface cause asserted "so the pass RAN"
from the forced-and-read-back flag alone — the very equation `_pass_ran` rejects — so an unobserved
step read as a pass that ran and was merely hidden. Keeping any of them alongside the corrected run
would leave two artifacts claiming to be THE measurement. **Superseding is how a row moves: re-run the probe, DELETE
the file it replaces, disclose the chain — never edit a number.** The "exactly one" rule is not a
convention: `tests/test_7949_capstone_measure.py::test_the_shipped_measurement_log_is_structurally_clean`
asserts EXACTLY ONE scored log (and validates the newest by its `# generated:` stamp, not by filename
order — `capstone-measured-6020cbd5.log` sorted BEFORE the `8a8bbcba` file it replaced), and
`tools/capstone_preregistration_check.py::check_single_measured_log` refuses a second one in the
required CI gate with a message that names the files and the repair.

⛔ **A scored table must NOT be written into `capstone.log`**, and this is mechanical, not a
preference: `tools/capstone_preregistration_check.py::_log_declares_no_measurement` exempts a log
from the ordering rule only while it carries **no** `numerator`/`denominator` **and** its
`run.status != "COMPLETED"`. `capstone.log`'s add-commit predates the pre-registration, so the
moment it carries a number it fails the ordering comparison — permanently, because the rule reads
the file's OLDEST add-commit and that never moves. The new name is a new path, added after the
record, and it passes. (Verified: `tools/capstone_preregistration_check.py --dir
docs/evidence/7949-capstone` → `OK — … the ordering holds`, three evidence logs.)

## The gate: two questions, two exits

```bash
uv run python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone-measured-e0fc940b.log
# → capstone-verdict-check: … GREEN=0, RED=3, BLOCKED=2, UNPREREGISTERED=0
# → ✅ verdict table is structurally sound  (exit 0)

uv run python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone-measured-e0fc940b.log --require-green
# → [V9] epic #7949: --require-green: 5/5 rows are not green  (exit 1)
```

*Is the table honest?* yes, 0 findings. *Can the epic close?* no, V9. A single exit code would
force the second answer to be read as the first — which is the exact inference the epic exists to
stop.

**One gate change shipped with this run, and it only ever ADDS a check.** V7 used to require every
measured row's `corpus_sha256` to equal the log's corpus. This table cannot satisfy that honestly:
E1/E2 cite #9076's arm run and E4 cites an operator receipt, and both measured *other* corpus
revisions — their records say so. Refusing those rows would hide the only numbers they have;
accepting them on the row's word would let any red row name any corpus. So a **non-green** row may
declare `artifact_corpus_sha256`, and V7 then requires (a) it equals the row's own
`corpus_sha256`, (b) that digest **appears in the cited artifact's text**, and (c) the row's note
says so. A **GREEN** row never takes that path: it must still equal the log's corpus exactly. The
arms are pinned in `tests/test_7949_capstone_verdict_check.py`
(`test_a_non_green_row_on_another_corpus_must_declare_that_revision`,
`test_a_declared_corpus_the_artifact_never_records_is_refused`,
`test_the_declaration_arm_never_relaxes_GREEN`).

## Why the steps are observed, never wrapped

E4's row *"fails if … the extractor was monkeypatched"*. A wrapper installed to watch the
pipeline is the same evidence-destroying move the row is guarding against, so both probes read the
pipeline's OWN returned surface. The consequence is stated rather than hidden: a step the surface
cannot witness is reported **unobserved** (with its cause and file:line), and the count of
witnessed steps is printed — an honest finding for `#7949`, never a guess.

## What this bundle does NOT establish

- **No E1 or E2a number.** Their frozen populations (all Objects with a before arm; the 14
  value-bearing units) are not carried by any committed artifact, and E1's only artifact is below
  its floor on a different corpus. Both rows are `BLOCKED` with their proxies printed.
- **No evidence that E3's `= 1.000` bar is reachable as registered.** It is not: `:Object`
  mention links are unanchored by design (#5199 → #9736 owns the choice).
- **No E4 edge denominator of our own.** E4's number is the committed operator receipt's
  (`2/15`), whose corpus revision is not the frozen one; the run's own operator-shaped
  relationships (`IMPL=57 INPUT=58 NAND=10` — the `S5` row above and the log's `step S5`
  line carry the same three numbers) are a *step witness*, not the 15-edge gold.
- **Nothing about E5's population reconciliation.** The instrument's `n` is 25 bound qualifiers;
  the record's denominator is 29 marker-bearing sentences. #9738 owns that, and this bundle says
  so instead of calling the numbers final.
- **The gate cannot detect a fabricated evidence file** with a plausible sha. Its stated limit: it
  closes *inference* (green rows with no artifact), not forgery.
- **Nothing about the five dependencies beyond their state** — the states above are a fresh read
  (2026-10-23), but *why* each is open is the issue body's decomposition, not re-derived.
- **The two unwitnessed steps stay unwitnessed for THIS run.** The scored log was measured before
  `#9116`'s seam, when `capture_session`'s receipt carried no step surface, so S2.2/S2.3 are
  `UNOBSERVED` there for every input. The seam has since LANDED — `stats["step_witness"]` names
  every stage's own facts — and the probe's `observe_steps` was rewritten to read it (the observer
  is part of this change, not something a re-run gets for free). A re-run at a post-seam head moves
  **S2.2**; **S2.3 does not flip**, because its classify pass is IDLE on this corpus and the witness
  says so (`enabled: true`, empty `s2`/`union`), so G5 still refuses there (see "The eight steps"
  above).

## Reproduce

```bash
# the gate's own tests + the measurement probe's pure contract (DB-free, pure):
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
    tests/test_7949_capstone_verdict_check.py tests/test_7949_capstone_measure.py \
    -q -p no:cacheprovider

# THE MEASURED RUN — a provider key and a searchable backend are required. #9116's
# seam has LANDED: the receipt carries stats["step_witness"], and the probe reads
# S2.2/S2.3 there (it still recovers S3/S6 from the live graph). What still bounds a
# re-run is the CORPUS, not the receipt — the classify pass is IDLE on it, so S2.3
# is unobserved and G5 still refuses. It writes its own file — never over capstone.log.
# ⛔ AND THE FILE IT SUPERSEDES IS DELETED: exactly ONE scored log may exist, and
# both the test and the pre-registration gate refuse a second one.
# NO ARM EXPORTS: the probe forces TORTOISE_VET/TORTOISE_CLASSIFY_LATER itself,
# pins TORTOISE_VALUE_GATE and TORTOISE_S2_DECISION_SEAM unset, and prints the
# os.environ READ-BACK as its `arm:` line — check that line in the log it writes,
# not your shell.
export TORTOISE_DB_URI="$(uv run python tools/test_lane.py uri | sed -n "s/^export TORTOISE_DB_URI=//p" | tr -d "'")"
uv run python docs/evidence/7949-capstone/probes/capstone_measure.py \
    --prereg "$(git rev-parse HEAD)"
uv run python tools/capstone_verdict_check.py \
    docs/evidence/7949-capstone/capstone-measured-<head8>.log --require-green

# the REFUSAL probe (unchanged contract, its own gate): G0 refuses before any
# model call at a commit that carries no pre-registration record.
python docs/evidence/7949-capstone/probes/capstone_run.py \
    --prereg <pre-registration commit> --corpus <pre-registered corpus> \
    --out docs/evidence/7949-capstone/capstone-<sha>.log
python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone-<sha>.log
```

⛔ **Not over `capstone.log`**: that file is the pre-landing REFUSAL record and its add-commit
predates the pre-registration, so a scored run written into it reds the ordering check
permanently (see the section above for the mechanism). `capstone_run.py`'s rows cite the path it
was actually told to write (`--out`), so a refusal written elsewhere cites that file.

`--head <sha>` on `capstone_run.py` (optional) **re-stamps** the run at another commit of the SAME
TREE — a re-run against a tag, or the same tree checked out elsewhere. It is validated, not
trusted: 7-40 lowercase hex digits that resolve to a commit in this repo, AND whose `^{tree}` is
byte-identical to the worktree's `HEAD^{tree}`; refused before G0 otherwise. A commit with a
DIFFERENT tree is a different program, so measuring it is not an override — check that revision
out and run there. Every row is stamped with the value and the gate resolves the
pre-registration's ancestry against it, so an unusable value cannot reach a shipped log.
`capstone_measure.py` has no `--head` on purpose: it re-measures the current tree, and the head it
stamps is `git rev-parse HEAD`.

A checkout with no resolvable `HEAD` commit (unborn HEAD, or not a git repo) is refused the same
way: a table whose rows carry the literal string `HEAD` is red by this repo's own `V3` rule.

## The entry point the run drives (and the receipt the #9116 seam opened)

The pipeline's OWN path is `TortoiseSDK.capture_session` →
`sdk._extract_session_v2` (`sdk.py:14182`; called at `:13219`) →
`extractor_v2.extract_session_v2` (`extractor_v2.py:8733`), which calls, in one pass.

📌 **Cite convention for this bundle's own cites.** Every `file:line` THIS bundle adds is
re-derived from the tree at the head it is stamped at and is paired with the symbol it names —
plus a short quote for the `sdk.py` cites — so a number that stops resolving is self-detecting:
**the symbol is authoritative and the line is a convenience**. Where a cite cannot be pinned
without drift it is symbol-only on purpose. (`tests/test_7949_capstone_measure.py` pins the cite
each cause string and the composition line carry, so one cannot be dropped silently; the
number's resolution is checked against the tree when it is re-derived.)

⚠️ **What that sweep can and cannot read.** `test_the_bundles_cites_resolve_where_the_bundle_says_
they_do` resolves a cite that NAMES ITS FILE (`extractor_v2.py` plus a line number), because only a
prefixed cite
says which tree to look in. A bare `` `:NNNN` `` / `` `:NNNN-NNNN` `` continuation — the shape the
step table and the S2.2/S2.3 paragraphs use for the second and later cites into a file they have
already named — names no file, so that sweep does not match it and CANNOT resolve it. The per-
surface numbers are still checked, from the other side: `_EXTRACTOR_SITES` derives each of them
from the tree and `test_the_per_surface_prose_cites_are_the_probe_tables_own_numbers` requires
this document and the `docs/00_index.md` row to print exactly those numbers. Every OTHER bare
continuation is navigational only and owes a hand re-derivation whenever a line moves above it —
which is why the number, never the symbol, is the half that rots: `#9675`'s index row records
the same lesson (cite the symbol, not the line).

| step | call site | witness the probe reads | on the receipt? |
|---|---|---|---|
| S0 READ | `_edus_from_conversation` `:8851` | `stats["edus"]`, `stats["chunks"]` | ✅ |
| S1 NARRATE | `run_s1` `:8884` | `stats["llm"]["by_stage"]["s1"]`, `story_arc` | ✅ (`story_arc` is off-receipt; `by_stage` carries it) |
| S2.1 EXTRACT | `run_s2` `:8908` | `by_stage["s2"]`, item counts | ✅ (`embed_list` is off-receipt; `by_stage` carries it) |
| S2.2 VET | `_run_vet_pass` `:8937` | `result["vet"]` — built at `extractor_v2.py:9227-9231`, copied onto the receipt at the meta fold `sdk.py:15010` as `stats["step_witness"]["vet"]` | ✅ reachable (#9116 seam) — the shipped log predates it |
| S2.3 CLASSIFY | `_apply_classify_kinds` `:8953` | `result["classify_later"]` — built at `extractor_v2.py:9217-9223`, copied at the same fold as `stats["step_witness"]["classify_later"]` | ✅ reachable (#9116 seam) — ⚠️ and on the measured corpus the pass is IDLE (`enabled: true`, empty `s2`/`union`), so it is honestly NOT witnessed |
| S3 RESOLVE | `search_graph` `:8969` | `result["search"]`, copied as `stats["step_witness"]["search"]` (mode/degraded/queries_run/hits); `stats["search_queries"]`/`stats["search_degraded"]` were already on the receipt | ✅ |
| S5 CONNECT | `execute_embed` `:9189` (+ `run_s4` `:8990` gap filler) | `stats["s4_merge"]` | ✅ (gap-filler counters) — `capstone_measure.py` uses the graph write + `stats["operator_endpoints_minted"]` |
| S6 COMMIT | `execute_embed` payload | `result["payload"]`, copied as `stats["step_witness"]["commit"]` (payload_produced + the count the write loop RESOLVED — created or folded onto an existing canonical, so a fully-folded capture reports the folded count, never 0) | ✅ — `capstone_measure.py` additionally reads the committed `:Point` nodes and their `eventId` stamps off the live graph |

`capstone_run.py` binds `result = captured` and hands `observe_steps` the receipt itself — the
object the shipped caller can hold. Before #9116 that object carried none of `result["vet"]`,
`result["classify_later"]`, `result["search"]`, `result["payload"]`, so the probe reported those
steps unobserved and refused at G5 for EVERY input. That was a property of the caller's receipt,
not of §4.4 or of the pipeline: the steps' surfaces are BUILT unconditionally, and the surface the
probe was handed did not say so. The drop was one layer ABOVE the extractor — it builds
`result["vet"]` and `result["classify_later"]`, and the SDK's meta fold
(`tortoise/sdk.py:15010`, `"stats": _capture_stats_with_step_witness(out, len(extracted))` inside
`_extract_session_v2`) forwarded `stats` and `error_census` only.

**#9116 LANDED that seam** — `_capture_step_witness` (``tortoise/sdk.py``) copies each stage's own
facts onto the existing ``stats`` key as ``stats["step_witness"]``: a sub-key of a key the receipt
already carried, so no new receipt key and no change to the SDK/MCP surface. Both probes read it:
`capstone_run.py`'s G5 **can** witness all eight from one pass — it is no longer structurally
barred from four of them, and the run's own witness values decide each row — and
`capstone_measure.py` looks for
`vet`/`classify_later` in `stats['step_witness']` after the extractor's own top-level keys. The
SCORED log in this bundle stays what it is — `capstone-measured-e0fc940b.log` was measured at
`e0fc940b`, BEFORE the seam, and reports 6/8 with S2.2/S2.3 unobserved. A re-run at a post-seam
head moves **S2.2**; **S2.3 moves only once its classify pass carries a non-empty `s2`/`union`** —
on the measured corpus that pass is IDLE (`enabled: true`, empty counters), and
`observe_steps`/`_witness_block` both refuse to count it, so a re-run there still reports S2.3
unobserved and G5 still refuses. Nothing here relabels a historical measurement.

⛔ The distinction the gate keeps, and why it is load-bearing: a receipt with **no**
`step_witness` at all (a replay, a keyless capture, the M2 lane) is refused as the **INSTRUMENT**
gap, never as "the pipeline skipped VET/classify/search/commit". A receipt that **has** the seam and
still does not witness a step is the run's own answer, quoted from the witness's values — a flag
gated the pass off, or the stage produced nothing.

## Dependency state (fresh read, 2026-10-23; the `#9116` row re-read at this PR's head)

| issue | state |
|---|---|
| `#9075` pre-register the corpus/split/scoring | **CLOSED COMPLETED** — landed `a6085af84` (#9507); the record is in this directory and G0 reads it |
| `#9076` E1+E2 on one run | **CLOSED COMPLETED** — its artifact is `e1-e2.log`, and the rows above show why it is not scorable against the frozen bars |
| `#9077` E4 on the real extraction leg | **CLOSED COMPLETED** — its receipt is cited by E4 (`2/15`) |
| `#5199` the version anchor stops at Points | **CLOSED COMPLETED** — the anchor is on the derivation link by design; #9736 owns the metric contradiction that leaves behind |
| `#7947` the raw-vs-narrative measurement | **CLOSED COMPLETED** — E5 re-scores its arms |
| `#9116` the capture receipt's step surface | **LANDED** — this PR's seam copies each stage's own facts onto `stats["step_witness"]`, so `capture_session`'s receipt now names all eight; `S2.2` is witnessed by any post-seam re-run, `S2.3` only once its classify pass does work (IDLE on this corpus), and V5's 8/8 scoping is the owner's open decision on the issue |
| `#9736` E3's denominator vs the owner-approved model | **OPEN** — E3's blocker |
| `#9738` E5's disclosure and population reconciliation | **OPEN** — E5's blocker |
| `#5333` may any pass remove/relocate an Object (owner ruling) | **OPEN** — E1's blocker (the mechanism that must move the share) |
| `#10360` nothing enforces the state-value guarantee on the write path | **OPEN** — E2's blocker (the render half is closed: PR `#10382` on `#9775`) |
| `#8503` the planted operator edges are not formed | **OPEN** — E4's blocker |
| `#9596` the operator receipt's retrievability is unmeasured | **OPEN** — the E4 artifact's own caveat |

A **scored** row moves to a different verdict only by RE-RUNNING the probe, never by reading an
issue's state — which is why E1/E2 stay `BLOCKED` even though #9076 closed, and why E3/E4/E5 are
`RED` rather than "blocked by a closed child". (The table above is the dependencies' own state,
not a scoreboard — `#9116`'s landing is recorded there because the seam shipped, and the S2.2/S2.3
rows above keep the count the frozen run measured.)
