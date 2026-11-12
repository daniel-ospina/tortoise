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
committed in this tree and is read as a file. The SCORED run below is stamped separately (**Scored at** `9152448d7`,
2026-11-02T12:16:06Z), because it is a different artifact from a different program
(`probes/capstone_measure.py`).

**Blast radius — the files this branch touches** (`git diff --name-only origin/main...HEAD`; the
COUNT is deliberately not restated here — it changed twice while this was open, and a stale
integer in the one paragraph a reviewer bounds the PR by is worse than no integer):
the bundle itself — `docs/evidence/7949-capstone/**` (this `README.md`, `E1-E5-PROOFS.md`, the
scored log `capstone-measured-9152448d.log` — `#10601`'s re-measurement, which replaced a log that
`main` had already deleted before this branch's base, so that file is named nowhere here as a
touched one — and `probes/capstone_measure.py`) and
`tools/capstone_verdict_check.py` with `tests/test_7949_capstone_verdict_check.py` (the V12 arm and
its pins) plus `tests/test_7949_capstone_measure.py` (the bundle's own claim pins); its registration
(`config/ci-surfaces.yml` `core`/`tier1`/`durations` arms, `docs/00_index.md`).
⛔ `blocker-states.json` is deliberately NOT listed: it landed on `origin/main` in `#10601` and this
branch only READS it. Two commands say so and one does not — `git diff --name-only origin/main...HEAD
-- docs/evidence/7949-capstone/blocker-states.json` and `git log --no-merges origin/main..HEAD -- …`
are both empty, while a plain `git log origin/main..HEAD -- …` is NOT: it lists the forward merge
commit, whose second parent carries the file, so a reader who runs the obvious form sees a hit and
concludes the claim is false. Named with that distinction because this paragraph has now been
re-derived twice for this class.
⛔ A previous revision of this paragraph named eight files this branch does **not** touch —
`tools/capstone_preregistration_check.py`, `tests/test_capstone_preregistration_9075.py`,
`tools/ci_selection.py`, `tests/test_ci_selection.py`, `config/surface-manifest.yml`,
`docs/product/mcp-sdk-surface.md`, `tests/test_ci_check_repair.py`,
`tests/test_uri_env_mutations_declared.py` — from an earlier cut whose selection and surface edits
were dropped. It is corrected here rather than carried, because it claimed the PR re-cut the
**MCP/SDK surface** (`config/surface-manifest.yml`, `docs/product/mcp-sdk-surface.md`), which this
branch does not do: no surface tool was added, removed or renamed.
**Nothing under `tortoise/` changes behaviour and nothing instruments the extractor** — this
bundle installs no wrapper, spy or step-composition anywhere in the measured path, so the E4
row's "no monkeypatch" clause holds on the fact that matters. The `sdk.py`/`extractor_v2.py`
lines this bundle cites are read, not changed.

**Decision being proved:** the epic's own closing rule — *"Close this epic only when
E1–E5 all have evidence attached to this issue — not when the children close. A child
closing is a claim; this epic is where the claim is checked against the pipeline
running end to end."*

⛔ **The rule reads E1–E5, and `tools/capstone_verdict_check.py` agrees with it.** `V9`
(`--require-green`) refuses the close while ANY of `E1`–`E5` is not green. `E6` — the sixth row the
owner ruled on 2026-11-05 (§3.8) — is **registered and reported, NOT closing**: it is accepted by
the scorer (`RECOGNISED_CRITERIA`, so a table may carry it and the registration's §8 precondition is
exercisable at all) and it is outside `CRITERIA`, which is both V2's completeness set and V9's
closing set. That follows the precedent §3.7 records for the five `A`-rows — *"registered here,
before any `A`-row run is scored … registered, measured, reported, **not closing**"*. **Consequence,
stated rather than implied: this epic CAN close 5-of-5 with `E6` red.** Promoting a row into the
closing set is a re-registration (`V2`'s row set, `V9`'s `5/5` and §3.1's table all change), so it
is the owner's call and not a lane's (`#11673` review, relayed to `#7949`).

⛔ **`E6` cannot be SCORED yet, and that residual is named rather than claimed away:** the scored-log
producer (`probes/capstone_measure.py`) still emits the five `E1`–`E5` rows, so no run can populate
an `E6` row. Growing the probe is `#11695` — registering the row ahead of its producer is
deliberate, because the alternative leaves the epic's acceptance criterion with no bar at all.

> **Verdict: 0/5 rows green. THREE rows are RED with measured numbers, TWO are BLOCKED
> because the frozen metric cannot be scored on the artifact that exists. The epic is NOT
> closeable, and this bundle says so mechanically — at `9152448d7`, re-measured 2026-11-02:**
>
> | row | frozen bar | measured | verdict |
> |---|---|---|---|
> | **E1** | `after_share <= 0.50 × before_share`, over all `:Object` nodes, floor **30** | this run has no BEFORE arm (one capture cannot decide `after <= 0.5 × before` without confounding the gate with LLM non-determinism); its AFTER arm read **64 `:Object` nodes**, clearing the frozen floor of 30, with a bare-definite share of 14/64 = 0.21875. `#9889` has since produced a before arm on the same frozen corpus, **twice** — **78 Objects / 0.3590** and **69 Objects / 0.5652** against `#9889`'s own after arm of **61 Objects / 0.2131** (`docs/evidence/9889/e1-e2-scores.json`) — so the ratio now EXISTS and the two pairings **disagree** (`0.2131 <= 0.1795` FAILS, `0.2131 <= 0.2826` PASSES; the two OFF runs of the SAME arm are 20.6 pp apart). A verdict that flips with which before run you pair against is not decided, so the row stays BLOCKED until **n >= 2 per arm** — the reason is now the pairing, not the floor | ⛔ **BLOCKED** (`#5333`) |
> | **E2** | E2a `>= 13 of 14` value-bearing units; E2b fused share `<= 0.05` | E2a: the frozen **14-unit** denominator had **no READER** in `probes/capstone_measure.py` when this log's table was emitted — the census itself was already committed (`docs/evidence/9889/e2a-value-units.json`, added 2026-10-30, `value_units: 14, value_survived: 8`), so this is a missing consumer, not a missing artifact — and the row was emitted BLOCKED and not scored. The current builder scores E1/E2 from `docs/evidence/9889/e1-e2-scores.json` (`E1_E2_SCORES`; the superseded `e1-e2.log` parse path is removed), so a RE-RUN moves this row to **RED 8/14** — its falsifier reaches **14/14** over the same population, so the deficit is retention (`#10360`), not the denominator. That number is deliberately NOT written as this row's verdict: a row moves only by re-running the probe, and that run has not been taken at this head. E2b: **1/114 = 0.008772** | ⛔ **BLOCKED** (`#10360`) |
> | **E3** | `= 1.000` (zero unanchored), floor **30** derived nodes | **157 / 397 = 0.395466** as registered (this run's own graph, `capstone_measure_9152448d_…`). The `Source->Object` identity link carries no `sourceVersion` BY DESIGN (owner option A, `#5199`), so `:Object mention links` can never anchor and the frozen `= 1.000` is unreachable as registered — the metric needs re-registration (`#9736`, OPEN, owner-gated) | ⛔ **RED** |
> | **E4** | `= 15 of 15` planted operator edges wired AND persisted | **5 / 15** on the **frozen** pin — `select_e4_receipt` chooses by the receipt's own `corpus_hash`, so the `2 / 15` this table shipped until 2026-11-02 (a receipt on `943a99ec…`, a NON-frozen revision) can no longer be selected; the receipt's retrievability rate is UNMEASURED (`#9596`) | ⛔ **RED** |
> | **E5** | ratio `>= 0.90` **AND** raw absolute `>= 0.80` | ratio **1.0833** (passes) but raw absolute **0.4800** (fails), n = 25 | ⛔ **RED** (S3 selection recovery, `#5087`) |
>
> E1's and E2's proxies are printed rather than dropped (E1b 90/90 real facts; E2b 1/114) — and
> neither proxy is allowed to become the verdict. A refused measurement is **neither a pass nor
> a fail**; the pre-registration says so in its own words (`below_floor`), and the table's
> `BLOCKED` is that word. Writing RED there would report a verdict the population is not
> entitled to; writing GREEN would be worse.

---

## ⚠️ Which run this document describes — the `e0fc940b5` stamp is SUPERSEDED

The scored log is **`capstone-measured-9152448d.log`** (stamped `9152448d7`, re-measured
2026-11-02, all 8 steps witnessed). **Every table and number below that describes THE RUN** — the session table, the eight-step table,
the E3 census and its derivative figures, the "6 of 8" step count, the reproduce transcript, the "On
the stamp" note, and any operator/entity/Point count quoted as this run's — is the **SUPERSEDED** run's,
measured at `e0fc940b5`, and is kept verbatim as the record of what it measured and of why it was
replaced (#10537). The CURRENT run's numbers are in the verdict table at the top of this document and
in `capstone-measured-9152448d.log` (E3 `157/397`, `Point` 354/150 anchored, `:Event` 7/43,
`:Object` 64, composition `159 + 150 + 34 + 11`, derivation figure `150/161 = 0.931677`,
`IMPL=65 INPUT=68 NAND=14`, 397 derived nodes, **8 of 8 steps** witnessed). Do not read the sections
below as current; where the two disagree, the log at `9152448d7` wins. **The one exception is a
passage that NAMES the shipped log while quoting it** — the E1-aux paragraph ("**64 `:Object`
nodes** … 14/64 = 0.21875 … the `e1-aux` line") and the operator-triple note in "What this bundle
does NOT establish" — which state what `9152448d7` measured and are checked against it by
`test_no_document_quotes_the_superseded_runs_e1_aux_numbers_as_current` and
`test_the_not_established_section_quotes_the_s5_operator_counts_the_log_measured`. A re-derivation
pass must NOT rewrite those two to the dead run's numbers: they are already the current run's. The supersede chain is the bundle's own rule:
a row moves only by RE-RUNNING the probe and deleting the file it replaces, so the old log is gone
from the tree while the numbers it produced stay citable from history. Where the two disagree, the
log at `9152448d7` wins.

⚠️ **The pre-registration still names the deleted file, and this PR does not edit it.**
`PRE-REGISTRATION.md` (§8's "**A scored evidence log already exists**: `capstone-measured-e0fc940b.log`"
and its `E3 census` / `E5` cites) and `preregistration.json`'s three `post_registration_measurement`
fields all name `capstone-measured-e0fc940b.log`, which this PR deletes. That is expected, not a
broken reference: the record names the log **as of its own stamp**, and re-pointing it at the
supersede would be a rewrite of the record rather than a correction of it. Read every path it names
as **as-of its own stamp**, and use the durable route for the content: `git show
2609039079717e445a19a0b0308e4302b02f0a8b:docs/evidence/7949-capstone/capstone-measured-e0fc940b.log`.
⚠️ That commit is the file's ADD commit on `origin/main` — `e0fc940b5` itself does **not** contain the
log (it is the head the run was stamped at, written by a later commit), which is the trap a reader
hits first if they try `git show e0fc940b5:…`. `tools/capstone_preregistration_check.py --dir
docs/evidence/7949-capstone` exits 0 against the bytes in the tree, which is why this is disclosed
HERE instead of "fixed" there.

⛔ **AND THAT RECORD WAS AMENDED SIX MINUTES AFTER THIS RUN — quoting its current hash alone says
the opposite.** The scored run at `9152448d7` (`# generated: 2026-11-02T12:16:06Z`) read the record
whose `document_sha256` was
`sha256:5ff05906cf383f50a873dafeee12216d5eafc80b89ae9676c78a109f16714fbe` — what
`PRE-REGISTRATION.md` hashed to *at that commit*. The record in this tree hashes to
`sha256:10d3ed3766e9662e1bd98fb58d03f33b3fc6f852506e514ef0ef7b9d5dd1d9cd`, and **do not trust
this line without re-deriving it** (`sha256sum docs/evidence/7949-capstone/PRE-REGISTRATION.md`):
§8's change-control rule re-cuts the digest on *every* edit to the document, so a present-tense
digest here is only ever as fresh as the last commit that touched it. `8bc2fbed1`
(`#10215` / PR `#10509`, merged 2026-11-02T12:22:19Z) amended it: the step set is restated as **all
eight ACCOUNTED** — seven observed plus the one the record declares conditional. That amendment is
the same DISCLOSED class of revision the record's §8 change-control protocol covers, but it is
**post-run**, so the document this run was measured against is `5ff05906…` and **not** the
post-amendment digest (`e8b83872…` when this paragraph was written).
**The artifact does not STATE this, and the gate CANNOT see it.** The shipped log's machine block
names the commit it read the record from (`read_from: git show
9152448d7:docs/evidence/7949-capstone/preregistration.json`), so the difference is one `git show`
away — but the log asserts no hash of its own, and the gate compares the tree's bytes with the
tree's record, so each version passes in its own tree. That is why it is stated here instead of
being left to be inferred.

---

## The measured run

```bash
export TORTOISE_DB_URI="$(uv run python tools/test_lane.py uri | sed -n "s/^export TORTOISE_DB_URI=//p" | tr -d "'")"
uv run python docs/evidence/7949-capstone/probes/capstone_measure.py \
    --prereg "$(git rev-parse HEAD)"
# G0 pre-registration e0fc940b5… READ (schema capstone-preregistration/v1)
# G1 corpus sha256:dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc MATCHES the frozen pin
# arm TORTOISE_VET=1 TORTOISE_CLASSIFY_LATER=1 TORTOISE_VALUE_GATE=0 TORTOISE_S2_DECISION_SEAM=0 (read back from os.environ)
# → docs/evidence/7949-capstone/capstone-measured-e0fc940b.log
```

⛔ **The arm needs no exported flags, and that is a required property, not a convenience.**
`probes/capstone_measure.py` FORCES `TORTOISE_VET=1` and `TORTOISE_CLASSIFY_LATER=1` in-process
(restoring the previous values in a `finally`, exactly as `e1_e2_probe.run_arm` does), pins
`TORTOISE_VALUE_GATE` **and** `TORTOISE_S2_DECISION_SEAM` to an EXPLICIT `"0"` — both also switch
S2.2 on (the latter is #5087's arbiter, so `_vet_enabled()` is True and a billable call would run
inside an arm declared OFF) — so an ambient value would silently change
the arm — and then **reads the values back out of `os.environ`**. The log's `arm:` line is that
read-back, never a typed literal, and a flag that does not read back as set REFUSES the run.
⛔ **`"0"`, not `<unset>`** (`#10996`): the value gate DEFAULTS ON, so an absent value pins nothing —
absence IS the ON arm. The committed log in this bundle was taken at a PRE-flip head, where absence
genuinely was OFF; its `<unset>` spelling is **history, and no licence follows from it here** — `e1_e2_scores.py`
is scoped to **9889** (`ON_ARM_LOG = "on-arm.log"`) and never reads this bundle's log, so the `value_gate_pin` in
`docs/evidence/9889/e1-e2-scores.json` describes that bundle's arms only (`#11201`). The
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
a log. ⚠️ **The SHIPPED log's own cites are as-of ITS stamp too, and `main` has since moved
them.** That log renders, for the capture path's `proj.link_source_to_event` call, the number
`tortoise/sdk.py` line 15506, and for `_TURN_WRITE_CYPHER` the number `tortoise/sdk.py` line 2408
— written here WITHOUT the `file:line` form on purpose, because these are not live cites and must
not be read as such. At the base it ships from (`9152448d7`) those lines are exactly those symbols;
at THIS head neither number resolves: measured, `15506` is the closing `)` of an unrelated
`_logger.warning(...)` call and `2408` is a `#:` comment line (the one that records *"Do NOT
reintroduce a literal tuple at this site"*). `sdk.py` has moved many times since — `git log
9152448d7..HEAD -- tortoise/sdk.py` lists every mover at this head, `main`'s and this PR's alike, and
this paragraph deliberately quotes NO count of them: a number here is one more thing that goes stale
— so the symbols no longer sit at the numbers the log renders. **§E3 — the census** below is where
this head's numbers for both of them live; this paragraph exists only to say which numbers in the
LOG belong to the log. ⛔ **Not `#10215`:** its `8bc2fbed1` touches no `tortoise/` file at all,
which is why the movers are `main`'s plus this PR's. Neither number is a defect and the log is not
edited for it — a log is the record of what the run rendered — but do NOT follow them as pointers
at this head.

The **SUPERSEDED** `capstone-measured-e0fc940b.log` carried the cites **as-of its own
stamp**, and those are the numbers listed here — the ones that log rendered, exactly as it
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
the `result["classify_later"]` block at `:14640-14646` here, not in the log. The `8288-8292` cite is
the `result["vet"]` block, and it is corrected to `:14650-14654` here — **not** to the `:8534-8538`
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

⛔ **One correction THIS log carries, and it is a different kind: two cells, a note tail and the
E4 note's `#8503` pointer are this lane's DECISION, not the run's output.** `capstone-measured-9152448d.log` was produced at
`9152448d7`, where `probes/capstone_measure.py` still emitted `#8503` (E4) and `#9738` (E5) as the
rows' `blocked_by` and its E5 note stopped at *"…before either number is called final"*. After the
run, this lane re-pointed those two attributions (`#8503 → #9596`, `#9738 → #5087`, both verified
**OPEN** in `blocker-states.json` at `2026-11-02T14:22:24Z`) and appended the E5 note sentence that
explains the move. **No numerator, denominator or verdict was touched — the numbers are the run's.**
The third is prose in the same class: the E4 note said the mechanism *is* `#8503`, which CLOSED
2026-10-24 — so the note now states that the closure records E4's DISPROOF (the row's measurement
record, not a live blocker) and says the planted-edge residual is UNOWNED — `#9596`,
which is the row's live blocker pointer and the receipt's unmeasured retrievability
rate, owns a DIFFERENT gap, and a later correction to the note says so. The log's own
`POST-RUN CORRECTION` block states all three AND the second correction to the third, and the
committed probe now EMITS both cells, the E4
note sentence and the E5 tail. ⛔ It does **NOT** follow that a re-run reproduces this file: since
`#7949`'s selector the committed probe reads the receipt ON the frozen pin, so a re-run at this tree
scores E4 at **5/15** from the NEWEST on-pin receipt — `w2b-relations-2026-11-08-11237.json` as of `#11237`, formerly `w2b-operator-2026-10-24-8503-b.json`; same `planted` 15, same `edge_correct` 5 — and writes a NEW
`capstone-measured-<head>.log` — it SUPERSEDES this one, which is the bundle's documented rule. Two
pins in
`tests/test_7949_capstone_measure.py` (`…blocker_cells_are_the_emitters_own`,
`…discloses_what_the_run_did_not_emit`) RED if the artifact and its emitter diverge again or if the
disclosure disappears. This was found in review and is recorded rather than absorbed: a cell edited
silently would be indistinguishable from emitter output, which is the same stale-pointer class V12
exists to refuse — and a reader who took `#8503`/`#9738` from a log claiming to be the run's output
would be reading exactly the closed pointers this PR removes.

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
| S2.2 VET | ❌ **#9116** | measured at `e0fc940b5`, BEFORE the seam: the pass was ENABLED (`TORTOISE_VET=1`, forced in-process and read back), and its surface **exists** — `extract_session_v2` emits `result["vet"]` UNCONDITIONALLY at `tortoise/extractor_v2.py:14650-14654` — but the meta fold then forwarded `stats` alone, so whether it DID any work was not observable through the receipt (measured: 142 stats keys and 18 receipt keys inspected, `vet` in neither). The fold now COPIES the stage's own counts as `stats["step_witness"]["vet"]` (`tortoise/sdk.py:18363`), so this ❌ is the frozen run's answer, not the seam's reachability |
| S2.3 CLASSIFY | ❌ **#9116** | same layer, same run: `result["classify_later"]` is built at `extractor_v2.py:14640-14646` and was dropped at the same fold, which now copies it as `stats["step_witness"]["classify_later"]` (`tortoise/sdk.py:18363`) |
| S3 RESOLVE | ✅ | `stats.search_queries=105 > 0` and `stats.search_degraded=false` on every session (a degraded search is not S3) |
| S5 CONNECT | ✅ | the graph write: 433 derived nodes (`Point` 338, `Object` 50, `Event` 45); `stats.operator_endpoints_minted=0`; operator-shaped relationships `IMPL=57 INPUT=58 NAND=10` |
| S6 COMMIT | ✅ | 338 `:Point` nodes persisted, 170 carrying the commit's `eventId` stamp, 358 `:GraphEvent`s |

**6 of 8.** The two that are missing are missing from the INSTRUMENT: the arm was forced and read back, so both passes were ENABLED on this run — but whether either did work was NOT observable through this surface, which is exactly why the surface was missing (a flag-on block that idles, or a fail-open pass, leaves the same empty stats). So the honest statement is "enabled and not witnessed", not "ran and was hidden" — and the receipt did not say so either way. Working around that by reading a wrapper's state is
exactly the instrumentation E4's row forbids, so the honest output is `UNOBSERVED` with its cause
and file:line — and any table the probe emits records `steps_observed` **and**
`steps_unobserved` so the count cannot be read as 8. ⚠️ **This is the `e0fc940b5` run's count.**
`#9116`'s seam has since landed, so a re-run at a post-seam head can witness **S2.2**; a re-run also
witnesses **S2.3** once its classify pass carries a non-empty `s2`/`union` — and on this corpus it
does: the committed re-run at `9152448d7` reports `enabled: true` with
`s2 = {"adjudication_calls": 1, "assigned_llm": 7, …}` (session `wp06_quarry_rollout`), so the
scored log observes **all eight** and its `steps_unobserved` is empty. (On the SUPERSEDED
`e0fc940b5` run that pass was idle, which is why the row above reads 6 of 8 — that count is the
history, not this bundle's current state.) Had it stayed idle, `#10215` makes that a **witnessed
idle**, not a refusal: `observe_idle` accounts it from the receipt's own facts, this probe emits
that accounting itself (`run.steps_required` / `run.steps_conditional`, read from the SAME frozen
record G5 reads), and G5/V5 accept it because the pre-registration declares the step conditional.
The two scored rows above keep the count the run measured.

⛔ **The count is a measurement, not a constant.** `observe_steps` looks for a `vet` /
`classify_later` surface in what the run actually holds (the receipt's top-level keys and its
`stats` block), and reports the step OBSERVED only when that surface **shows its pass DID work** —
a non-empty `s2` or `union`, with `enabled` not `false`. The FLAG alone is never enough (see
below). When it is not a witness, the cause reports the number of keys it really inspected instead
of quoting a remembered one. A step is never *assigned* unwitnessed, and every step ends up in
exactly one of the two sets (a hole or an overlap refuses the run). So the epic's own GREEN gate —
V5, all eight steps **accounted for** — stays reachable: `#9116`'s seam makes both rows OBSERVABLE
(each step's own block is on the receipt), and S2.2 then flips on a re-run at a post-seam head
(S2.3 flips once its pass does work — and it DID on the committed post-seam re-run, which is why
this bundle's recorded idle prediction is falsified by its own artifact; `#10215` restated the
requirement as seven observed + S2.3 observed-or-witnessed-idle from the frozen record, so an idle
pass is also ACCOUNTED rather than refused; see `PRE-REGISTRATION.md` §11 and §11.3). It
did **not** come for free and this bundle does not claim it did: the seam landed with this probe's
observer rewritten (`observe_steps` reads `stats['step_witness']` instead of guessing at a surface
the receipt never carried) and with the pin that certified the old unreachability replaced by the
one that pins the wiring.

⛔ **And PRESENT is not RAN.** `extract_session_v2` emits both keys UNCONDITIONALLY — on the
flag-OFF arm and on its early-return path the block is `{"enabled": False, "s2": {}, "union":
{}}` (`extractor_v2.py:13851-13855` — the classify block starts at `:13851`, the vet surface at `:13855` — and `:14640-14646` / `:14650-14654`, the two result blocks). An acceptance test of "a non-empty dict"
therefore takes an OFF block as the witness for *the pass ran* and prints `step S2.2 WITNESSED …
enabled=false, s2={}`: the moment `#9116` copies the sibling key — exactly what this bundle
instructs — both steps would be WITNESSED on **every** run, including one measured with the
flags OFF. That is the "OFF population under an ON label" failure one layer up, and V5 is the
only closing gate for the step count. A block that contradicts itself (`enabled: false` with
non-empty S2 stats) fails CLOSED.

⛔ **And the FLAG is not ACTIVITY** — the sharper half, and the one this rule got wrong first. The
first tightened rule accepted `enabled: true` on its own, reading it as "the extractor's OWN
statement that the gate was open". It is not that: `enabled` is the FLAG, computed BEFORE the pass
is reached. `vet_enabled` is computed at `extractor_v2.py:13820`, i.e. BEFORE the
`if not edus:` early return (`:13824`), which emits literally
`"vet": {"enabled": vet_enabled, "s2": {}, "union": {}}` (`:13855`), and
each VET pass is gated on the LIST — `if vet_enabled and embed_list:` (`:16833`), `if vet_enabled and
complete_list:` (`:14404`). So a session with the flag ON and an empty list emits `enabled: True`
with empty counters and no pass ran (a FAILED vet pass leaves the same shape: `_run_vet_pass` returns
`{}` on its fail-open path, `:13549-13552`). Because `_witness_block` returns the first positive
ANYWHERE on the run, ONE such session out of seven made S2.2/S2.3 WITNESSED (6/8 → 8/8) — and V5
would then have passed on a step nobody observed. The rule now requires the flag AND positive
activity, and a flag-on/zero-activity block gets its OWN cause ("flag on, no pass activity") that is
distinct from both the OFF cause and `#9116`'s absent-surface cause: three negatives, three places
to look.

⛔ **And that cause cites each surface's OWN code — the CLASSIFY pass is not the VET pass.** It was
first written once and templated for the surface NAME, so the S2.3 line argued from `vet_enabled`,
the `if vet_enabled and …` gates and `_run_vet_pass` — code the classify pass never reaches. The
classify pass has its own flag (`classify_later`, `:13789`), its own early-return block
(`:13851-13854`), its own gates — `if classify_later and embed_list:` (`:14273`) around `if items:`
(`:14276`), and `if classify_later and complete_list:` (`:14419`) around `if items:` (`:14429`) — and its
own fail-open `except` (`:14289` / `:14440`), so an idle classify block comes from no embed list **or
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
unconditionally at `:14640`/`:14650` and now reaches the receipt through the seam at the meta fold
`sdk.py:18363` (`"stats": _capture_stats_with_step_witness(out, len(extracted))`) — is updated
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
`test_every_step_is_witnessed_or_given_a_cause`. (The 6/8 counts above are the PROBE's capability
fixtures — synthetic receipts — not this bundle's record: the shipped-log test asserts THIS
artifact's **eight of eight**, with `steps_unobserved` empty.)

## E3 — the census, and the contradiction it measures

The anchor is a property of the **derivation link**, not of the node
(`tortoise/projection/edges.py:870-878` `_anchor_on_create` / `_DERIVATION_ANCHOR_SET`), and the
derivation target labels are `_DERIVATION_REFERENCES_LABELS` (`edges.py:843`: `Event`,
`Document`). ⛔ **The two directions are not interchangeable**, and the first stamp of this bundle
got one of them backwards: `extractedFrom` points FROM the derived node
(`(Point)-[:extractedFrom]->(GraphEvent|Event)`), while `references` points AT it
(`(Source)-[:references]->(Event|Document)` — `link_source_to_event`, `edges.py:3032-3057`,
reached from the capture path at `sdk.py:18761` (`proj.link_source_to_event(source_key,
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

⇒ **E3 = 148/433 = 0.3418 as registered on 2026-10-21 — RED, and the RED is the decision, not a
defect.** `:Object` mention
links are identity links that carry no `sourceVersion` **by design** (owner-approved option A,
2026-09-25, #5199), so the original denominator — which counts `:Object` — makes `= 1.000`
unreachable **as registered**. ~~#9736 owns the choice: re-register the denominator as the classes
the model treats as derivations, or reopen the model. A lane cannot pick, and this bundle does not.~~

**AMENDED 2026-11-01 — `PRE-REGISTRATION.md` §10.1 (#9736).** The registration in this bundle now
names the amended class set (`Point` + `Event` + `Document`), §10.1 records old → new with the
authority and the date, and the probe derives the same set from `_DERIVATION_REFERENCES_LABELS`
`probes/capstone_measure.py` instead of hardcoding it — so the next run prints its own
`anchored / derived` and not this row's number. That is **following** a recorded decision, not picking between the two
options struck above: #5199 option A already ruled the anchor sits on the **derivation link**, so
counting `:Object` mentions in E3's denominator was the registration lagging the decision rather
than a bar. **No threshold moved and the narrowing does not rescue the bar** — the residual (188 of
338 `Point`s are turn rows or operator Points and can never carry an `extractedFrom` link, §10.1)
stays #9736's to attribute and is disclosed there, not absorbed. **#9736 remains OPEN and
owner-gated**: this amendment is the reopen with the evidence in front of the owner, `Refs` and not
`Closes`, and both readings are stated in §10.1 (and in §9's provenance table) (`148/433` as registered, `148/383` under
the amended class set). The probe's own composition line reports the REGISTERED set it scores — it
does not re-emit the pre-amendment figure as if it were this run's — and it says so.

⛔ **E3's Point denominator mixes classes that CANNOT derive, and the figure is taken over the
writer class — not over the link.** Of the 338 `Point` nodes: **159 mechanical turn rows**
(`_write_capture_turns`, `_TURN_WRITE_CYPHER` at `sdk.py:2554`, wired only by `CONTAINS`),
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
by this probe or unilaterally — it moves only through the disclosed amendment in
`PRE-REGISTRATION.md` §10.1 — so the "all derived nodes" reading (148/433, and the 285 unanchored
nodes — nearly all
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

⇒ **E4 = 2/15 on `943a99ec…`** — the receipt above is kept as the SHIPPED LOG's record, and it
was NOT measured on the frozen corpus.

⛔ **SUPERSEDED as the row's artifact (2026-11-02).** This receipt's `corpus_hash` is not the
frozen `fixtures_hash`, so the row above was a measurement of a different corpus revision — on
code that predates `#8503`'s own measurement work. ⛔ Not "before `#8503`'s fix" — there is no such
fix: `#8503` CLOSED 2026-10-24 as a **DISPROOF**, and its own body closes with *"No open issue owns
it."* `probes/capstone_measure.py` now selects E4's receipt **by its
own `corpus_hash`** (`select_e4_receipt`): the newest receipt on the frozen pin wins, and no match
REFUSES instead of falling back. Three receipts on the frozen pin (`w2b-operator-2026-10-24-8503-{preleg,a,b}.json`
— `#8503`'s OWN measurement receipts, dated `2026-10-24T05:46:51Z`/`06:03:47Z`/`06:10:47Z`, i.e.
**14.66–15.06 h** BEFORE that issue's `closedAt` of `2026-10-24T20:50:13Z` (re-derived from the
receipts' own `date` fields against the tracker), so they are not "after its fix" either) read
**5/15**, with `content_ok` 11–13 and `operators_total` 29–33 across the
three (the row reads `b`: `content_ok=11`, `operators_total=33`, commit `a999be41e`).
So **E4 is 5/15, still RED** against the 15/15 bar. The verdict did not move; the number did, and
it is now a measurement of the pre-registered corpus. The scored log below keeps its 2/15 because
a superseded run is replaced by RE-RUNNING the probe, not by editing the log it wrote.

Two provenance facts a reader needs, both stated in the log rather than
smoothed over: the FILE postdates the pre-registration (added 2026-10-22) but the MEASUREMENT
does not (`date=2026-10-19T01:39:26Z`), and its `corpus_hash` is **not** the frozen
`fixtures_hash`. **The shipped log's row** is therefore a non-green measurement on **its own**
corpus revision (`943a99ec…`), and the gate (V7) requires that digest to appear in the cited
artifact the row declares — which is why *that* row carries `artifact_corpus_sha256`. The
**current** row does not take that arm: it is selected BY the frozen pin, so its
`corpus_sha256` IS the log's corpus and V7's short-circuit applies to it (§*The gate: two
questions, two exits* states which arm each row takes). The mechanism is #8503 — the planted
edges are largely unformed: **2 of 15** on the shipped log's `943a99ec…` receipt and **5 of 15**
on the current row's frozen-pin one, which is why both are RED against the 15/15 bar; the
receipt's retrievability rate is unmeasured (#9596).

⚠️ **The frozen record still publishes the pre-fix figure for E4, and is deliberately NOT
edited.** `PRE-REGISTRATION.md` §10.3 says *"E4 is not amended"*, scores it at **2 of 15**, and
closes with *"its row are unchanged"*; `preregistration.json`'s
`criteria[E4].post_registration_measurement` still hands a machine consumer `edge_correct=2 of 15
… so it measures its own corpus revision`. Both sentences are scoped to the PRE-FIX artifact —
the `w2b-operator-2026-10-19.json` receipt on corpus `943a99ec…` and the log that read it — so
neither is a false statement about the thing it names. (The log itself is gone: `#10601`
re-measured the table and replaced `capstone-measured-e0fc940b.log` with
`capstone-measured-9152448d.log`, which still renders E4 as 2/15 because it was written before
this selector existed.) What is not true of it is the conclusion a reader draws: the record is the
one file a consumer reads without a human in the loop, and it now disagrees with the row this
bundle publishes while carrying no pointer to the selector. It is **not** silently edited:
`preregistration.json` pins the document by BYTES (`document_sha256`), and the `#10463` ruling
recorded further down this bundle says an amendment is a **NEW linked record, never an edit to
the frozen one**. This section is that changes record — read the record's E4 figure as *that
log's*, read the row's as **5/15 via `select_e4_receipt`**, and treat the §10.3 sentence about
"its row" as true of the log it names and not of the row the probe now builds.

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
row's note. **`#5087` is the row's blocker and carries the rest** (`#9738`, which the row cited
until 2026-11-02, CLOSED that morning on its own §10 *record* correction — that closure is not a
measurement of this number): `EXTRACTOR-V4-ARCHITECTURE.md` §10's
prior measurement was visible when the record said none was, and the 25-bound (this instrument) vs
29-marker-sentence (the record) populations must be reconciled before either number is called final.

## C9 (`#10990`) — the two stale bars re-derived at this head, and E5's population answered

**Measured at:** `af864a42a6` (`origin/main`, 2026-11-06) — every command and number below was run
there, so a later head that disagrees is a regression rather than an argument. Both rows were
re-derived, not re-implemented; nothing here builds a second instrument.

### E4 — `#2552` / `#4716` / `#8503` re-derived: **VERIFIED-RED at 5 of 15**, unpatched

```bash
# 1. the receipt is chosen by its OWN corpus_hash against the frozen pin, not by filename
cd docs/evidence/7949-capstone && uv run python -c 'import json,sys
sys.path.insert(0,"probes"); sys.path.insert(0,"../../..")
import capstone_measure as m
prereg = json.load(open("preregistration.json"))
rel, rec = m.select_e4_receipt(frozen_pin=m.check_corpus(prereg))
print(rel, rec["operator_audit"]["edge_correct"], rec["operator_audit"]["planted"])'
#    frozen pin: sha256:dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc
#    selected:   tests/eval/write_path/receipts/w2b-operator-2026-10-24-8503-b.json
#    edge_correct: 5 of 15   commit a999be41e   date 2026-10-24T06:10:47Z
#    extractor real: True    posture: llm

# 2. the receipt's own evidence rules (the checker that grades an E4 artifact)
uv run python tools/operator_receipt_check.py \
    tests/eval/write_path/receipts/w2b-operator-2026-10-24-8503-b.json
#    [PASS] E4-EXTRACTOR: real extractor (tortoise.extractor_v2.extract_session_v2)
#    [PASS] E4-LANE: product lane (extractor_posture=llm)
#    [PASS] E4-DENOM: denominator 15 == gold
#    [PASS] E4-NUMERATOR: edge_correct 5 == the per-edge rows   (content_ok 11)
#    [PASS] E4-CAUSE: 10 miss(es) individually named
#    VERDICT: EVIDENCE VALID   (exit 0)

# 3. the shipped verdict table gates it
uv run python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone-measured-9152448d.log
#    GREEN=0, RED=3, BLOCKED=2 — 5 row(s) carry an evidence artifact + sha, all 5 rows V2
#    requires are present (exit 0)
#    (the five rows V2 requires ARE the epic's close — #7949's close condition is the five-row
#     sentence; `E6` is registered and reported, not closing — see V9 below)
```

⇒ **E4 = 5/15 = RED** against `= 15 of 15`. The leg is **unpatched in the sense that matters**: the
receipt's own `extractor` block records `real: true` with `extractor_posture: llm`, so
`tortoise.extractor_v2.extract_session_v2` is the SHIPPED function and the number grades whether the
extractor *forms* the operators. The only *test* still cannot say that —
`tests/eval/write_path/test_write_path_operator_lane.py` monkeypatches `extract_session_v2` (`:212`)
and its docstring says so — but a **measurement** can and does, twice: `8503-a`
(`edge_correct 5`, `content_ok 11`, 06:03:47Z) and `8503-b` (same 5/11, 06:10:47Z) are two
independent runs seven minutes apart that agree on the numerator, plus `preleg` (5, `content_ok` 13).
So 5/15 REPRODUCES and is not one lucky draw.

**Both tree figures, named as the issues require** — `2/15` (shipped log, receipt
`w2b-operator-2026-10-19.json`, `corpus_hash 943a99ec…`, commit `6164c683c`) and `5/15` (frozen pin,
commit `a999be41e`). The `2/15` is not the bar and is not this row's artifact any more: it measures a
corpus revision that was never frozen.

**The gap, split two ways — and the two splits do not agree on WHICH half dominates.** At the
frozen pin (10 lost of 15): **6** `edge_missing` — both endpoints present, no qualifying operator,
the **operator-selection** half — and **4** endpoint-recall misses (`3 to_content_missing` +
`1 from_content_missing`, `wp02_lumen_refactor_op_01`). `#8503` reports the same numerator on a
**14**-edge population (wp02 excluded as unmeasured) as **4 selection / 5 recall** — so selection is
the larger half in the frozen reading only, and the older reading has recall larger.

⛔ **The split did NOT move because wp02 joined the population — that was this section's first
draft, and the receipts refute it.** Re-grading the frozen receipt over exactly the 14 edges `#8503`
scored gives **6 selection / 3 recall** (not 4/5), and `#8503`'s own four `edge_missing` edges grade
`edge_correct` (`wp03_ember_design_op_01`) and `to_content_missing` (`wp01_quarry_debug_op_02`) in
that receipt. Four of the frozen run's six `edge_missing` edges were not `edge_missing` in `#8503`.
So what changed is **per-edge attribution across runs**, not the denominator: LLM non-determinism
moves which edge is missed, while the **numerator is stable at 5**.

⚠️ **Which means the 5 is reproducible and the attribution is NOT.** `8503-a` and `8503-b` — seven
minutes apart on the same commit — agree on `edge_correct 5` but disagree on **five of fifteen**
individual verdicts (`wp01_op_01`, `wp03_op_01`, `wp03_op_02`, `wp05_op_02`, `wp06_op_01`), and
`preleg` splits its 10 misses **8 selection / 2 recall**. Read the 5 as the measurement and any split
as one run's draw: a single reading of "where the 10 go" is not a stable fact at this corpus.

**Blocker named, not assumed satisfied: `#9596`.** The row's retrievability dimension is UNMEASURED —
and this re-derivation found the reason is stronger than that issue says: `#9596` describes a fix (a
v2 receipt carrying `operators_seen` / `operators_unprovenanced`, plus an `E4-PERSIST-OPS` rule in
`tools/operator_receipt_check.py`), and **that fix is not in the tree**:
`git log --all -S'operators_unprovenanced'` and `-S'E4-PERSIST-OPS'` return NOTHING on any ref, all
four receipts are `receipt_version: 1`, and the checker's persistence rule still reads the
eventId-filtered list whose numerator and denominator come from the same query. So `N/N` here is
still the layer's SIZE, not a rate — `#9596` stays the row's blocker and its described repair has to
be re-landed.

### E5 — the raw arm re-derived: **VERIFIED-RED at raw 0.4800**, and the 25-vs-29 population answered

```bash
# 1. the registered scorer, re-run over the committed arm receipts
uv run python -m tests.eval.write_path.score_qualifiers \
    --sessions wp01_quarry_debug wp02_lumen_refactor wp03_ember_design wp04_aurora_perf \
               wp05_retro_writeup wp06_quarry_rollout wp07_bluepeak_followup \
    --arms-dir tests/eval/write_path/receipts/7947-arms-2026-10-18 --out /tmp/e5-rescore.json
#    raw        n=25  loss 13  recovery 0.480000   <- bar >= 0.80  FAILS
#    narrative  n=25  loss 12  recovery 0.520000
#    ratio      0.52/0.48 = 1.0833                 <- bar >= 0.90  PASSES
#    INCOMPLETE RECEIPTS IN THE HOLDOUT: wp07_bluepeak_followup:raw partial=True

# 2. the committed artifact is byte-identical to that run
python3 -c "print(open('/tmp/e5-rescore.json','rb').read() == \
    open('docs/evidence/7949-capstone/e5-rescore.json','rb').read())"     # True  (44 092 B)

# 3. the population question, answered mechanically
uv run python docs/evidence/7949-capstone/probes/e5_population_reconcile.py
```

⇒ **E5 = RED on the absolute floor** (0.4800 < 0.80) with the ratio passing (1.0833) — the asymmetry
the record required two bars to catch. Blocker **`#5087`** (S3 selection recovery), named by id and
still OPEN.

**The 25-vs-29 population is now answered, and the answer is that no arithmetic reconciliation
exists.** The two counts are different **instruments over different units**, not one population
counted twice:

| | what it counts | derived by |
|---|---|---|
| **29** | **sentences** in the corpus fixture matching the pre-registration's own 24 `hedge_scope_lexemes` (of 343 sentences) | `capstone_preregistration_check.derive_corpus_facts` |
| **25** | **planted qualifiers** in the sealed gold, which is what `score_qualifiers` classifies | `gold/<session>.gold.json → planted_qualifiers` |

| session | marker sentences | planted qualifiers |
|---|---|---|
| wp01_quarry_debug | 8 | 3 |
| wp02_lumen_refactor | 6 | 4 |
| wp03_ember_design | 3 | 4 |
| wp04_aurora_perf | 5 | 4 |
| **wp05_retro_writeup** | **0** | **3** |
| wp06_quarry_rollout | 4 | 4 |
| wp07_bluepeak_followup | 3 | 3 |
| **total** | **29** | **25** |

Markers **exceed** qualifiers in three sessions (wp01, wp02, wp04) and **fall short** in two (wp03,
wp05), so no monotone map exists and the 29 cannot be reached by adding rows to the 25. **wp05 is the
decisive case**: its three qualifiers (`about`, `they are both good`, `that was the decision after the
August incident`) are in the fixture text, but no word of theirs is in the corpus lexeme table — the
two vocabularies differ (13 of the 24 corpus lexemes appear nowhere in the scorer's 78 markers). The
registered *"29 marker-bearing sentences"* therefore binds **no gold**: it cannot be scored, and the
only population that can is the 25. Pinned by `tests/test_7949_e5_population_reconcile.py` (5 tests)
so the argument cannot rot silently.

⚠️ **This narrows the open reconciliation, it does not decide it.** Which count is *canonical* — i.e.
whether E5's registration should keep a denominator it cannot score — is carried by **`#5087`** (the
S3 epic), and this bundle says so instead of re-registering the row: `preregistration.json` still
carries `hedge_scope_sentences: 29` and E5's `post_registration_measurement` still names both
figures. ⚠️ `#9738` — which owned this until 2026-11-02 — **CLOSED that morning on its own §10
*record* correction, which is not a measurement of this number** (the re-attribution is the README's
dependency table, not this section's).

**Two questions, kept apart** — conflating them is what made "unreconciled" unreadable:

| question | state |
|---|---|
| *Are the 29 and the 25 one population counted twice?* — arithmetic | **ANSWERED**: no, different instruments over different units (the table above) |
| *Which count is E5's denominator?* — policy | **OPEN**, `#5087`'s to carry; `preregistration.json` still pins 29, so neither number is called final here |

> ⚠️ **What is NOT claimed.** E5's *other* two halves stay green and separate: the scorer regenerates
> the committed artefact byte-identically (verified above), and §10's write-back pin
> (`tests/test_7949_e5_section10_writeback.py`) still passes. Neither is E5's registered outcome, and
> reporting either as the verdict is the substitution this bundle exists to refuse.

## E1 / E2 — the `#9889` record: E1 still UNDECIDED (n=1 per arm), E2 would score 8 of 14 on a RE-RUN

**These two rows no longer read `e1-e2.log` — no row the current builder emits does.** Their `evidence` is
`docs/evidence/9889/e1-e2-scores.json`, produced in ONE invocation by
`probes/e1_e2_scores.py` over the three `#9889` probes' own outputs, and the old
log's parse path is deleted from `probes/capstone_measure.py` (`#9889` item 5).
The record's `corpus_sha256` IS the pre-registration's frozen `fixtures_hash`
(`dd7e8520…`), because `probes/e1_paired_arm.py` refuses to run on any other
corpus (G1) — so both rows now cite a measurement on the **frozen** corpus, which
the superseded log never was.

```text
E1 REFUSED decidable=False (before spread 20.6 pp)
   before 0.3590 -> bound 0.1795 vs after 0.2131 = FAIL (ratio 0.5937)
   before 0.5652 -> bound 0.2826 vs after 0.2131 = PASS (ratio 0.3770)
E2 FAIL — E2a 8/14 (bar >= 13), E2b fused 1/114 = 0.008772
```

- **E1 = REFUSED, and the refusal is now a MEASUREMENT.** Both before arms clear
  E1's own floors (30 Objects; `e1_min_before_share` 0.20 — measured 78 and 69
  Objects, 0.3590 and 0.5652), so the shares are real. What they cannot do is
  decide the bar: one after realization paired against two independent before
  realizations gives **both** verdicts, so at n=1 per arm the verdict would
  depend on which before run is chosen. The two OFF runs of the same arm differ
  by **20.6 pp** — wider than the 14.6 pp the ON arm moved away from OFF 1. The
  next requirement is **n >= 2 per arm**, not a second arm.
- **E2 would score RED 8/14 on a re-run, and the denominator that was missing for eleven days is now carried.**
  E2a is **8 of 14** value-bearing planted units strictly surviving on the run's
  own graph, against `>= 13 of 14`. The denominator is §3.3's rule read off the
  pre-registration and computed from the gold by `probes/e2a_value_units.py`; the
  **anchor-injection falsifier reaches 14/14**, so the counter is shown to move
  and 8/14 is a measurement rather than an inert instrument. E2b is 1/114 =
  0.008772 (inside `<= 0.05`); the composite needs both halves, so the short half
  decides the row. `#10360` still records that nothing enforces retention.

The mechanism half is measured too (`probes/e1_gate_drops.py`): the v4 value
predicate drops **0 of 61** Objects and **0 of 13** `^the <X>` with its positive
controls alive, so the selection pass is not what moves E1's share — the
removal/relocation ruling is (`#5333`).

### What the superseded log said (kept as history, cited by no row the current builder emits)

`e1-e2.log` was the only E1/E2 artifact for eleven days, and it says what it is:

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

> ℹ️ The three reasons above, and the E2a sentence below, describe the state **before `#9889`
> landed**. Both are superseded by the section above: E1's before arm exists (twice, on the frozen
> corpus) and E2a's 14-unit denominator is carried. Read on only for what the log itself says.

E2's composite is E2a (14 value-bearing planted units strictly surviving) **and** E2b (fused
share). E2b measures **1/114 = 0.008772**, comfortably inside `<= 0.05` — printed, and NOT used
to green the row, because the artifact counts atomic *Points*, not surviving planted units. E2a's
frozen denominator is the **14 value-bearing units** derived by §3.3's rule ("`verbatim_anchor`
contains a digit" minus two named exclusions) — and **no committed artifact counts it**. The
artifact's no-loss control (42/42 value literals, 90/90 real facts) is consistent with E2a but
does not compute it. #9775's render half — the state-value carve-out on every prompt-building path —
is closed (PR #10382); what still blocks E2 is `#10360`: nothing enforces retention, and the 1-of-14
value loss sits upstream of the filter.

**What E1 took, precisely — and why it is still REFUSED.** That paragraph was
written when the run had one arm. `#9889` has since produced the before arm on the
frozen corpus, twice: **78 Objects / 0.3590** and **69 Objects / 0.5652**, against
the same run's after arm of **61 Objects / 0.2131**
(`docs/evidence/9889/e1-e2-scores.json`). Both before arms clear E1's own floors, so
the ratio exists and IS the criterion: `0.2131 <= 0.5 x 0.3590 = 0.1795` **fails**,
while `0.2131 <= 0.5 x 0.5652 = 0.2826` **passes**. The residual problem is the one
the paragraph above already names from the other side — across-run LLM
non-determinism — and it is now measured rather than asserted: the two OFF runs of
the SAME arm are **20.6 pp** apart on the same corpus. A verdict that flips with
which before run you pair against is not decided, so E1 stays `BLOCKED` until
**n >= 2 per arm**. That is cheaper than the controlled single-extraction design
`#9076` built (`assert_one_gate_population`), which remains the stronger instrument
and is the next step if n >= 2 keeps disagreeing. (The `0/59` / `6/50` figures
below are the superseded log's own bare-definite counts, quoted as history.)
— retrievable with `git show 8a8bbcba2:docs/evidence/7949-capstone/capstone-measured-7c9c34d8.log`,
because superseding DELETES the tree entry and not the history; what it costs is citability as a
row's `evidence`, which V4 resolves against the tree. **That route is ref-qualified like the one
above:** `8a8bbcba2` is branch-only, so after the squash-merge the same `git fetch origin
refs/pull/9823/head` + `git show` applies; and the `0/59` figure is stated here, so it does not
depend on the retrieval).
The mechanism that must move the share is an owner ruling on
removal/relocation (**#5333**).

## Two artifacts, and why `capstone.log` is not overwritten

| file | what it is |
|---|---|
| `capstone.log` | the shipped REFUSAL record (`capstone_run.py`, stamped `027c8c1ab`): G0 refused before any model call, 5 rows, no numbers |
| `capstone-measured-9152448d.log` | the SCORED run (`capstone_measure.py`, stamped `9152448d7`, generated 2026-11-02T12:16:06Z): 3 RED + 2 BLOCKED, every row carrying its artifact, all 8 steps witnessed. ⛔ **Its E1/E2 rows PREDATE the `#9889` re-point.** The file is frozen at its own stamp and a row moves only by RE-RUNNING the probe (§"Two artifacts"), so it still carries both rows on the hard-coded `docs/evidence/7949-capstone/e1-e2.log` — E2 `BLOCKED` (`#10360`) — and its E1/E2 section header still reads "parsed out of the landed e1-e2.log", where the current builder emits "read out of the #9889 measurement record". Those two rows are what that run measured, NOT the current routing: the rows the builder emits today cite `docs/evidence/9889/e1-e2-scores.json` (§"E1 / E2 — the `#9889` record") |
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
classify pass never runs (`classify_later` at `:10631`, its `if classify_later and …` / `if items:`
gates at `:10769`/`:10772`/`:10894`/`:10904`), and its absent-surface cause asserted "so the pass RAN"
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

### The ordering rule this bundle's registration states is SUPERSEDED

`PRE-REGISTRATION.md`'s exit list, item 2, tells an author that the commit that **adds** the
registration must be **strictly older** than the commit that adds each `*.log` in this directory
(`git log --diff-filter=A --format=%ct`) — a comparison of two sibling commits **inside one
contribution**. That predicate is what the gate no longer enforces, for the reason `#10463` shows:
a squash merge collapses the pair into ONE commit, so the same tree passed its PR checks and redded
`main` minutes later (`#10468`). The ruled repair (`#10463` comment 6564634454, item 4) is **base
ancestry**: the registration's earliest add-commit reachable from the **merge base** must strictly
precede the log's add-commit, which means a change that lands the registration and its log **in one
PR** is refused (`SAME_PR_REFUSAL` in the gate: *"it must land FIRST, in its own PR"*). The gate
prints the ref it actually compared on its `ordering measured against:` line — read that line, not
this note and not the registration's item 2.

⛔ **The registration is NOT edited to say this, and its own terms are why.** `preregistration.json`
pins the document by BYTES (`document_sha256`), asserted for **this** bundle by
`tests/test_capstone_preregistration_9075.py::test_the_real_record_pins_the_real_document` (its
`PREREG_DIR` is `docs/evidence/7949-capstone`, so an edited registration reds there at any checkout
depth — the test reads no history), and enforced in the required CI step by the gate's own
`DOCUMENT_SHA_PIN` arm in `tools/capstone_preregistration_check.py::check_machine_record`.
⛔ **Scoped, because the record's own change control is narrower than "never edit":** §8 permits a
post-log change, but only as a RECORDED one — a `## Deviations` entry carrying the date, the original
value, the new value and the reason, with `document_sha256` re-cut in the SAME commit. That is not
hypothetical: this record has been amended in place three times after its first cut (`eb9fd644…` →
`e8a74aaf…` → `5ff05906…` → `e8b83872…`, the last being `8bc2fbed1`), which is what the pin makes
REVIEWABLE rather than what it prevents. A NEW linked record is the other permitted route (the
`#10463` ruling this paragraph follows). Either way, the failure the gate exists to catch is the
SILENT edit. Leaving item 2 as the only
statement of the rule is the other failure: a lane that reads the authoritative protocol, orders its
two commits inside one PR exactly as criterion 2 asks, and lands is refused by a requirement the
registration never states. **This section is the changes record that closes that gap** — item 2 is
what the registering author wrote, base ancestry is what the gate enforces today, and
`tests/test_capstone_preregistration_9075.py::test_the_bundle_records_the_superseded_ordering_rule_its_registration_states`
keeps this record here (and the registration unedited).

⚠️ **This sentence must cite a test that pins THIS bundle's bytes, not merely a test that exists.**
An earlier revision of it cited
`tests/test_s3_prior_ablation.py`'s `test_the_frozen_pre_registration_is_still_the_document_its_record_pins`
(named, not addressed — it is a historical mention, not a command to run)
— a real name in a real file, which pins the **5087** document (`tools/s3_prior_ablation.py`'s
`PREREG_REL`) and therefore stays GREEN on an edited 7949 registration. That is worse than citing
nothing: it turns "unchecked" into "checked and green" for exactly the lane this paragraph is
warning. `test_every_test_name_the_evidence_bundles_cite_exists` resolves a citation's *existence*
and structurally cannot see its *subject*, so this one is held by
`tests/test_capstone_preregistration_9075.py::test_the_supersession_record_cites_the_pin_that_guards_this_bundles_bytes`
instead — and reproduce it by hand by appending a byte to `PRE-REGISTRATION.md` and running the cited
test (it must FAIL) before changing this line.

## The gate: two questions, two exits

```bash
uv run python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone-measured-9152448d.log
# → capstone-verdict-check: … GREEN=0, RED=3, BLOCKED=2, UNPREREGISTERED=0
# → ✅ verdict table is structurally sound … blocker states: every cited blocker
#   verified 2026-11-02T14:22:24Z (11 on record)   (exit 0)

uv run python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone-measured-9152448d.log --require-green
# → [V9] epic #7949: --require-green: 5/5 rows are not green  (exit 1)
#   (E1–E5 close the epic, #7949's verbatim close condition; `E6` is recognised and reported but
#    is NOT a closing row, so its absence here is not what refuses this close)
```

*Is the table honest?* yes, 0 findings. *Can the epic close?* no, V9. A single exit code would
force the second answer to be read as the first — which is the exact inference the epic exists to
stop.

**One gate change shipped with this run, and it only ever ADDS a check.** V7 used to require every
measured row's `corpus_sha256` to equal the log's corpus. The shipped table cannot satisfy that
honestly: E1/E2 cited `#9076`'s arm run, which measured a *different* corpus revision, and E4 cited
an operator receipt measured on another revision too. **Both now measure the FROZEN corpus** —
E1/E2 because `#9889` re-scored them there (their records say so), E4 because `select_e4_receipt`
chooses its receipt BY the frozen pin, so its row carries the run's own corpus. So the table a run
builds TODAY takes the declaration on no row — while the SHIPPED log's E1/E2/E4 rows still carry
it, which is exactly what keeps the arm live: a red row on a genuinely other revision must still
be able to say so. Refusing those rows would hide the only numbers they have; accepting them on
the row's word would let any red row name any corpus. So a **non-green** row may
declare `artifact_corpus_sha256`, and V7 then requires (a) it equals the row's own
`corpus_sha256`, (b) that digest **appears in the cited artifact's text**, and (c) the row's note
says so. A **GREEN** row never takes that path: it must still equal the log's corpus exactly. The
arms are pinned in `tests/test_7949_capstone_verdict_check.py`
(`test_a_non_green_row_on_another_corpus_must_declare_that_revision`,
`test_a_declared_corpus_the_artifact_never_records_is_refused`,
`test_the_declaration_arm_never_relaxes_GREEN`).

## V12 — the gate that used to print `OK` over closed blockers (#10537)

**The defect, measured.** The table shipped at `e0fc940b` named, as its blockers, three issues that
had already CLOSED: E2←`#9775` (closed 2026-10-31T14:44:43Z), E4←`#8503` (closed
2026-10-24T20:50:13Z), E5←`#9738` (closed 2026-11-02T08:53:06Z) — and
`tools/capstone_verdict_check.py` printed `✅ verdict table is structurally sound` over all three.
V6 checked the POINTER's form (`#NNN`) and never the thing it pointed at, so a row could go on
saying *"blocked by work that is finished"* indefinitely. Nothing here is a judgement call: a
pointer at closed work is stale evidence **by construction**, and looking the state up is the whole
check.

**The fix — V12, and why the states are a committed file.** A non-green row whose `blocked_by` the
`docs/evidence/7949-capstone/blocker-states.json` record reports **CLOSED** is a finding (exit 1,
naming the row, the issue and its `closedAt`); so is a cited blocker the record does not cover, so a
row cannot point at work nobody looked up. The record is COMMITTED rather than fetched so the check
stays pure, offline and deterministic in CI — and so the state that decides a row is reviewed in the
same diff as the row. A record that is present but malformed is a **usage error (exit 2), never a
silent disarm**; the OK line now STATES which V12 arm ran, so "structurally sound" cannot be read as
"the blockers are live".

```bash
uv run python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone-measured-9152448d.log
# ✅ … blocker states: every cited blocker verified 2026-11-02T14:22:24Z (11 on record)   exit 0
# …re-point E2 at #9775 / E4 at #8503 / E5 at #9738 in a COPY and the same command gives:
# ❌ 3 finding(s):
#   [V12] rows[E2]: blocked_by #9775 is CLOSED (2026-10-31T14:44:43Z) — a pointer at closed work…
#   [V12] rows[E4]: blocked_by #8503 is CLOSED (2026-10-24T20:50:13Z) — …
#   [V12] rows[E5]: blocked_by #9738 is CLOSED (2026-11-02T08:53:06Z) — …        exit 1
```

**One exemption, by semantics:** a row of a **REFUSED** run that measured nothing (`capstone.log`,
five rows, no numbers) records the *historical* cause of the refusal; its blocker closing is what the
refusal was FOR, so demanding an OPEN blocker there would make the bundle rewrite history by
re-running a refusal that can no longer happen. A row that carries a NUMBER, or a `BLOCKED` row of a
completed run, asserts a live cause — that is what V12 checks.

**Two ATTRIBUTIONS were corrected, and no number was touched.** Re-measuring moves the numbers; it
does not re-point a blocker, so the two rows whose cited issue had closed were re-attributed to the
only OPEN issue their artifact still has — a blocker POINTER, not an ownership claim on the residual
(which is unowned: `#8503` says so itself, and E4's note now says so too). E4 `#8503` → **`#9596`**
(the receipt's retrievability rate is UNMEASURED, which E4's own note already named), E5 `#9738` →
**`#5087`** (raw recovery 0.4800 against
the 0.80 floor is S3 selection recovery; `#9738` closed on its own §10 *record* correction, which is
not a measurement of this number). `probes/capstone_measure.py` carries the same two constants so the
next run emits them. Every NUMBER in the table below is the run's own, at `9152448d7`.

## Why the steps are observed, never wrapped

E4's row *"fails if … the extractor was monkeypatched"*. A wrapper installed to watch the
pipeline is the same evidence-destroying move the row is guarding against, so both probes read the
pipeline's OWN returned surface. The consequence is stated rather than hidden: a step the surface
cannot witness is reported **unobserved** (with its cause and file:line), and the count of
witnessed steps is printed — an honest finding for `#7949`, never a guess.

## What this bundle does NOT establish

- **No E1 verdict** — its shares ARE carried (see the bullet's own numbers below); its DECISION is
  not, and no green E2: `#9889` now carries BOTH E1 populations on the FROZEN corpus
  (before `0.3590` / 78 Objects and `0.5652` / 69 Objects, after `0.2131` / 61 Objects, in
  `docs/evidence/9889/e1-e2-scores.json`), so E1 is no longer refused for want of a population:
  it is `BLOCKED` because the two pairings disagree, and a verdict that changes with which before
  run you pick has not been decided (n >= 2 per arm is the next requirement). E2a's 14-unit
  denominator IS carried and scores **8/14**, so a RE-RUN of this probe makes E2 **RED**, not blocked. (The superseded
  `e1-e2.log` — 25 Objects, one arm, a corpus that is not the frozen one — is kept as history and
  cited by no row the current builder emits.)
- **No evidence that E3's `= 1.000` bar is reachable as registered.** It is not: `:Object`
  mention links are unanchored by design (#5199). The registered denominator of 2026-10-21 counted
  them; the amendment in `PRE-REGISTRATION.md` §10.1 re-registers it to the classes #5199 says the
  model anchors, and the bar is **still** not reached (`148/383 = 0.38642`, §10.1) — #9736 remains
  the owner-gated attribution of the residual.
- **No E4 edge denominator of our own.** E4's number is the committed operator receipt's
  (`5/15` on the frozen pin since the artifact is selected by its `corpus_hash`; `2/15` was the
  pre-`#8503` receipt on another revision); the run's own operator-shaped
  relationships (`IMPL=65 INPUT=68 NAND=14` — the log's `step S5`
  line carry the same three numbers) are a *step witness*, not the 15-edge gold.
- **E5's population is reconciled as "two different populations", not as one denominator.** The
  instrument's `n` is 25 planted qualifiers; the record's denominator is 29 marker-bearing
  sentences; the C9 section above measures why those cannot be reconciled arithmetically (markers
  exceed qualifiers in 3 sessions and fall short in 2; wp05 has 3 qualifiers and 0 marker sentences)
  and pins it in `tests/test_7949_e5_population_reconcile.py`. The ARITHMETIC question is answered;
  which count is CANONICAL — i.e. what E5's denominator should be — is `#5087`'s to carry (`#9738`,
  which owned it until 2026-11-02, CLOSED that morning on its own §10 *record* correction — which is
  not a measurement of this number), so neither number is called final here.
- **The gate cannot detect a fabricated evidence file** with a plausible sha. Its stated limit: it
  closes *inference* (green rows with no artifact), not forgery.
- **Nothing about the five dependencies beyond their state** — the states above are the tracker
  states recorded in `blocker-states.json` at its `checked_at` (`2026-11-02T14:22:24Z`, re-confirmed
  by running the gate), but *why* each is open is the issue body's decomposition, not re-derived.
- **The two unwitnessed steps stay unwitnessed for the SUPERSEDED run — not for this bundle's.**
  The 6-of-8 count belongs to `capstone-measured-e0fc940b.log`, measured before `#9116`'s seam,
  when `capture_session`'s receipt carried no step surface, so S2.2/S2.3 are `UNOBSERVED` there for
  every input. The seam has since LANDED — `stats["step_witness"]` names every stage's own facts —
  and the probe's `observe_steps` was rewritten to read it (the observer is part of this change,
  not something a re-run gets for free). **The post-seam re-run is committed in this bundle**, and
  it moves BOTH rows: `capstone-measured-9152448d.log` (`9152448d7`) observes all eight, and S2.3's
  witness is non-empty (`enabled: true`, `s2 = {"adjudication_calls": 1, "assigned_llm": 7, …}`,
  session `wp06_quarry_rollout`) — so the classify pass was NOT idle on this corpus after all, and
  this bundle's recorded prediction that S2.3 stays idle is falsified by its own artifact. `#10215`
  still matters for the general rule: an idle classify pass is accounted as a **witnessed idle**
  (flag ON, no activity, no fail-open marker, a positive candidate count) rather than refused, and
  the probe emits that split itself (`run.steps_required` / `run.steps_conditional`, read from the
  frozen record) so G5/V5 accept it where the record declares the step conditional. ⚠️ The log
  committed in THIS bundle predates that emitter (it was generated `2026-11-02T12:16:06Z`, and
  `#10215` landed `8bc2fbed1a` at 12:22:19Z), so its run block carries `steps_observed` /
  `steps_unobserved` — all eight observed, which V5 accepts either way — and NOT the conditional
  split. A consumer must not read that log as carrying one (`capstone_run.py`'s
  `required = run.get("steps_required") or list(STEPS)` falls back to the all-eight list exactly as
  it would for any pre-#10215 log); every FUTURE run writes it.

## Reproduce

```bash
# the gate's own tests + the measurement probe's pure contract (DB-free, pure):
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
    tests/test_7949_capstone_verdict_check.py tests/test_7949_capstone_measure.py \
    -q -p no:cacheprovider

# THE MEASURED RUN — a provider key and a searchable backend are required. #9116's
# seam has LANDED: the receipt carries stats["step_witness"], and the probe reads
# S2.2/S2.3 there (it still recovers S3/S6 from the live graph). What still bounds a
# re-run is the CORPUS, not the receipt. On the SUPERSEDED run the classify pass was idle;
# on the committed post-seam re-run it does work (the shipped log's `step S2.3 WITNESSED`
# carries `assigned_llm: 7`), so S2.3 is OBSERVED there — and where a future corpus DOES leave
# the pass idle, #10215 has the gate require it ACCOUNTED (witnessed idle), not observed, with
# the conditional set read from the frozen record.
# It writes its own file — never over capstone.log.
# ⛔ AND THE FILE IT SUPERSEDES IS DELETED: exactly ONE scored log may exist, and
# both the test and the pre-registration gate refuse a second one.
# NO ARM EXPORTS: the probe forces TORTOISE_VET/TORTOISE_CLASSIFY_LATER itself,
# pins TORTOISE_VALUE_GATE and TORTOISE_S2_DECISION_SEAM to an explicit "0" (not
# unset — since #10996 an absent value gate is the ON arm), and prints the
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

The pipeline's OWN path WAS `TortoiseSDK.capture_session` (**DELETED by #4282**, 2026-10-27 —
this bundle's probes now drive the private `sdk._capture_session`) →
`sdk._extract_session_v2` (`sdk.py:17444`; called at `:16452`) →
`extractor_v2.extract_session_v2` (`extractor_v2.py:13705`), which calls, in one pass.

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
| S0 READ | `_edus_from_conversation` `:13823` | `stats["edus"]`, `stats["chunks"]` | ✅ |
| S1 NARRATE | `run_s1` `:16748` | `stats["llm"]["by_stage"]["s1"]`, `story_arc` | ✅ (`story_arc` is off-receipt; `by_stage` carries it) |
| S2.1 EXTRACT | `run_s2` `:16780` | `by_stage["s2"]`, item counts | ✅ (`embed_list` is off-receipt; `by_stage` carries it) |
| S2.2 VET | `_run_vet_pass` `:16834` | `result["vet"]` — built at `extractor_v2.py:14650-14654`, copied onto the receipt at the meta fold `sdk.py:18363` as `stats["step_witness"]["vet"]` | ✅ reachable (#9116 seam) — and WITNESSED by the shipped post-seam run: `stats["step_witness"]["vet"]` `enabled=true`, `s2={"candidates": 40, "by_outcome": {"KEEP": 40}}` (the SUPERSEDED `e0fc940b5` log is the one that predates the seam) |
| S2.3 CLASSIFY | `_apply_classify_kinds` `:14278` | `result["classify_later"]` — built at `extractor_v2.py:14640-14646`, copied at the same fold as `stats["step_witness"]["classify_later"]` | ✅ reachable (#9116 seam) — and WITNESSED by the shipped post-seam run: `enabled: true`, `s2 = {"adjudication_calls": 1, "assigned_llm": 7, …}` (`assigned_llm` is the evidence it did work, so this is not an idle pass) |
| S3 RESOLVE | `search_graph` `:14294` | `result["search"]`, copied as `stats["step_witness"]["search"]` (mode/degraded/queries_run/hits); `stats["search_queries"]`/`stats["search_degraded"]` were already on the receipt | ✅ |
| S5 CONNECT | `execute_embed` `:14554` (+ `run_s4` `:14315` gap filler) | `stats["s4_merge"]` | ✅ (gap-filler counters) — `capstone_measure.py` uses the graph write + `stats["operator_endpoints_minted"]` |
| S6 COMMIT | `execute_embed` payload | `result["payload"]`, copied as `stats["step_witness"]["commit"]` (payload_produced + the count the write loop RESOLVED — created or folded onto an existing canonical, so a fully-folded capture reports the folded count, never 0) | ✅ — `capstone_measure.py` additionally reads the committed `:Point` nodes and their `eventId` stamps off the live graph |

`capstone_run.py` binds `result = captured` and hands `observe_steps` the receipt itself — the
object the shipped caller can hold. Before #9116 that object carried none of `result["vet"]`,
`result["classify_later"]`, `result["search"]`, `result["payload"]`, so the probe reported those
steps unobserved and refused at G5 for EVERY input. That was a property of the caller's receipt,
not of §4.4 or of the pipeline: the steps' surfaces are BUILT unconditionally, and the surface the
probe was handed did not say so. The drop was one layer ABOVE the extractor — it builds
`result["vet"]` and `result["classify_later"]`, and the SDK's meta fold
(`tortoise/sdk.py:18363`, `"stats": _capture_stats_with_step_witness(out, len(extracted))` inside
`_extract_session_v2`) forwarded `stats` and `error_census` only.

**#9116 LANDED that seam** — `_capture_step_witness` (``tortoise/sdk.py``) copies each stage's own
facts onto the existing ``stats`` key as ``stats["step_witness"]``: a sub-key of a key the receipt
already carried, so no new receipt key and no change to the SDK/MCP surface. Both probes read it:
`capstone_run.py`'s G5 **can** witness all eight from one pass — it is no longer structurally
barred from four of them, and the run's own witness values decide each row — and
`capstone_measure.py` looks for
`vet`/`classify_later` in `stats['step_witness']` after the extractor's own top-level keys. The
SUPERSEDED log (`capstone-measured-e0fc940b.log`) was measured at `e0fc940b`, BEFORE the seam, and
reports 6/8 with S2.2/S2.3 unobserved — that is history, and the supersede chain deleted it from
the tree while keeping its numbers citable. **This bundle's scored log is the post-seam one**:
`capstone-measured-9152448d.log` (`9152448d7`) observes all eight, so a re-run at a post-seam
head moves **S2.2** — and on this corpus **S2.3 moved too**, because its classify pass was not
idle after all (`enabled: true`, `s2 = {"adjudication_calls": 1, "assigned_llm": 7, …}`, session
`wp06_quarry_rollout`), which falsifies this bundle's recorded prediction that it stays IDLE. Had
it been idle, `#10215` makes that a **witnessed idle**, not a refusal: `observe_idle` accounts S2.3
from the receipt's own facts (flag ON, empty counters, NO fail-open
marker, a positive candidate count), this probe writes that accounting into its own log
(`run.steps_required` / `run.steps_conditional`, from the frozen record — the split the gate reads
and cannot infer), and G5/V5 accept it because the pre-registration declares the step conditional.
Nothing here relabels a historical measurement.

⛔ The distinction the gate keeps, and why it is load-bearing: a receipt with **no**
`step_witness` at all (a replay, a keyless capture, the M2 lane) is refused as the **INSTRUMENT**
gap, never as "the pipeline skipped VET/classify/search/commit". A receipt that **has** the seam and
still does not witness a step is the run's own answer, quoted from the witness's values — a flag
gated the pass off, or the stage produced nothing.

## Dependency state (the `#9116` row re-read at this PR's head; the other rows carry the tracker states recorded in `blocker-states.json` at its `checked_at` `2026-11-02T14:22:24Z` — that record covers 9 of these 12 issues, so `#9076` and `#7947` carry their own closure evidence in the cell, not a stamp)

| issue | state |
|---|---|
| `#9075` pre-register the corpus/split/scoring | **CLOSED COMPLETED** — landed `a6085af84` (#9507); the record is in this directory and G0 reads it |
| `#9076` E1+E2 on one run | **CLOSED COMPLETED** — its artifact is `e1-e2.log`, and the rows above show why it is not scorable against the frozen bars |
| `#9077` E4 on the real extraction leg | **CLOSED COMPLETED** — it built the real-extraction leg, and the receipt E4 used to cite (`w2b-operator-2026-10-19.json`, `2/15`) is ITS artifact, on a different corpus revision. The frozen-pin receipts E4 now selects (`5/15`) are **not** `#9077`'s: they landed with `#8503`/`#9873` (`2cba48d3c`), which is the provenance §E4 above and `E1-E5-PROOFS.md` both state |
| `#5199` the version anchor stops at Points | **CLOSED COMPLETED** — the anchor is on the derivation link by design; #9736 owns the metric contradiction that leaves behind |
| `#7947` the raw-vs-narrative measurement | **CLOSED COMPLETED** — E5 re-scores its arms |
| `#9116` the capture receipt's step surface | **LANDED** — this PR's seam copies each stage's own facts onto `stats["step_witness"]`, so `capture_session`'s receipt now names all eight; `S2.2` is witnessed by any post-seam re-run, and `S2.3` is WITNESSED by this one — the shipped log's `step S2.3 WITNESSED` line carries `assigned_llm: 7`, so the classify pass is NOT idle on this corpus (`#10215` restated V5/G5 as all-eight-accounted, and this run is 8/8 witnessed) |
| `#9736` E3's denominator vs the owner-approved model | **OPEN — owner-gated**; `PRE-REGISTRATION.md` §10.1 carries the amended class set (`Point`+`Event`+`Document`, re-derived `148/383 = 0.38642`, still RED) as the reopen awaiting the ruling |
| `#9738` E5's disclosure and population reconciliation | **CLOSED** `2026-11-02T08:53:06Z` — it closed on its own §10 *record* correction (PR `#10525`), which is not a measurement of E5's number. The residual is S3 selection recovery, owned by **`#5087`** (OPEN): raw absolute `0.4800` against the `0.80` floor |
| `#5333` may any pass remove/relocate an Object (owner ruling) | **OPEN** — E1's blocker (the mechanism that must move the share) |
| `#10360` nothing enforces the state-value guarantee on the write path | **OPEN** — E2's blocker (the render half is closed: PR `#10382` on `#9775`) |
| `#8503` the planted operator edges are not formed | **CLOSED** `2026-10-24T20:50:13Z` — it was E4's `blocked_by` and closed nine days before this bundle noticed. Its residual is the UNFORMED PLANTED EDGES, and **no open issue owns it** (`#8503` says so itself) — the receipt's unmeasured retrievability is a DIFFERENT gap, `#9596`, and is E4's blocker POINTER rather than this residual's owner |
| `#9596` the operator receipt's retrievability is unmeasured | **OPEN — E4's blocker** (re-attributed from the closed `#8503`, `2026-11-02`): the receipt's retrievability rate is UNMEASURED, which E4's own note already named |

A **scored** row moves to a different verdict only by RE-RUNNING the probe, never by reading an
issue's state — which is why E1 stays `BLOCKED` (its two before realizations disagree, so it is not
decided at n=1 per arm) and E3/E4/E5 are `RED` rather than "blocked by a closed child". E2 becomes `RED`
on a re-run, because `#9889`'s record carried the 14-unit denominator (8 of 14); the shipped table above is the
dependencies' own state, not a scoreboard — `#9116`'s landing is recorded there because the seam
shipped, and the S2.2/S2.3 rows above keep the count the frozen run measured.)
