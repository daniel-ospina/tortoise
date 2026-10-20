---
title: "Capstone evidence — #7949 / #9080 extractor-v4 E1–E5 verdict table"
type: evidence
domain: platform
doc_status: final
created: 2026-10-19
subjects.team: epistemic-team
aboutSubjects: Tortoise extractor v4
aboutObjects: "#7949, #9080, #9075, #9076, #9077, #9116, #5199, #7947, EXTRACTOR-V4-ARCHITECTURE.md"
---

# Capstone evidence — `#7949` / `#9080` (extractor-v4, E1–E5)

**Measured at:** `472599b6a` on branch `feat/9080-tm` (2026-10-20). **Blast
radius — the 13 files this branch touches** (`git diff --name-only origin/main...HEAD`):
the bundle itself (`docs/evidence/7949-capstone/**`, `tools/capstone_verdict_check.py`,
`tests/test_7949_capstone_verdict_check.py`); its registration (`config/ci-surfaces.yml`
`core`/`tier1`/`durations` arms, `docs/00_index.md`); the selection change that makes
a PROBE-ONLY diff select this suite (`tools/ci_selection.py` + `tests/test_ci_selection.py`,
the `CORE_ALSO` sibling pattern); the surface re-cut the new caller forces
(`config/surface-manifest.yml`, `docs/product/mcp-sdk-surface.md`); and two test-side
edits that are NOT this bundle's subject — `tests/test_ci_check_repair.py` (the `gh`
launcher stub) and `tests/test_uri_env_mutations_declared.py` (the declaration for
`#9122`'s indexer `TORTOISE_DB_URI` setenv, a `main` red every lane inherited).
**Nothing under `tortoise/` changes behaviour and nothing instruments the
extractor** — this bundle installs no wrapper, spy or step-composition anywhere
in the measured path, so the E4 row's "no monkeypatch" clause holds on the fact
that matters. The one-line `tortoise/sdk.py` receipt construction this bundle
reasons about is read, not changed. (An earlier draft of this paragraph also
claimed a citation remap in `docs/engineering/auth/consent-identity-bridge.md`
and two comment rewrites under `tortoise/`; `main` carries equivalent fixes for
all three, so they are NOT part of this bundle and are no longer claimed here.)

**Decision being proved:** the epic's own closing rule — *"Close this epic only when
E1–E5 all have evidence attached to this issue — not when the children close. A child
closing is a claim; this epic is where the claim is checked against the pipeline
running end to end."*

> **Verdict: 0/5 rows green. The epic is NOT closeable, and this bundle says so
> mechanically.** What ships is the instrument the rule needs — the run
> (`probes/capstone_run.py`) and its gate (`tools/capstone_verdict_check.py`) — plus an
> honest table in which every row names the issue that blocks it. That is the outcome
> the issue asks for: *"it must report a red row and name the blocking issue rather than
> infer green from a closed child."*

---

## Verdict table

| # | criterion | verdict | evidence | blocked by |
|---|---|---|---|---|
| **E1** | volume falls, correctness preserved (before-figure 62.3%) | ⛔ **UNPREREGISTERED** | `capstone.log` (refusal record) | **`#9075`** → then **`#9076`** |
| **E2** | state values preserved verbatim under the volume drop | ⛔ **UNPREREGISTERED** | `capstone.log` | **`#9075`** → then **`#9076`** |
| **E3** | every derived node carries its version anchor (`:Object`/`:Event` included) | ⛔ **BLOCKED** | `capstone.log` | **`#5199`** |
| **E4** | the extraction leg merges all 15 edges on the REAL path, unpatched | ⛔ **BLOCKED** | `capstone.log` | **`#9077`** |
| **E5** | the falsifier (§10) was run on OUR corpus | ⛔ **BLOCKED** | `capstone.log` | **`#9075`** (re-derive in a pre-registered pass) — `#7947` **LANDED** |

`UNPREREGISTERED` and `BLOCKED` are deliberately different verdicts: E1/E2's mechanism
exists and what is missing is the **frozen thresholds** (`#9075`); E3/E4 are missing
their **mechanism** and `#9075` landing would not change that. Collapsing them into one
word would let the next lane re-run and still learn nothing.

⚠️ **E5 moved off `#7947` on 2026-10-20, and the move is the row's own history.** `#7947`
(the raw-vs-narrative falsifier) is **CLOSED COMPLETED** (`2026-10-19T12:54:38Z`, PR
`#9071` merged): the measurement ran twice, `EXTRACTOR-V4-ARCHITECTURE.md` §10 carries
the result and D3 stays provisional. Citing it sent every reader of this table to a
landed lane, and two commits here re-touched this README *after* that closure while
still printing `OPEN` — the same misattribution `ALSO_BLOCKED_BY` exists to prevent.
So the row now names what E5 actually lacks: a **pre-registered** capstone pass to
re-derive the measurement in, i.e. G0 (`#9075`) — plus the run-level `#9116`. Per this
README's own rule, a row moves only by RE-RUNNING the probe, never by reading an
issue's state, so E5 stays `BLOCKED` even though its measurement exists.

## What the run did

The acceptance command from the issue, executed verbatim at `472599b6a`:

```bash
python docs/evidence/7949-capstone/probes/capstone_run.py \
    --prereg "$(git rev-parse HEAD)" --corpus battery/config/corpus.json \
    | tee docs/evidence/7949-capstone/capstone.log
```

**G0 refused, before any model call** (§4.4: *S0 is FAIL-CLOSED — end the run before any
model call or write*):

```text
run: REFUSED — commit 472599b6a… does not carry
     docs/evidence/7949-capstone/preregistration.json
steps observed: (none)   required: S0 S1 S2.1 S2.2 S2.3 S3 S5 S6
```

`--prereg` names the commit whose tree must CARRY the pre-registration record — the
runner reads it with `git show <sha>:<artifact>`, never from the worktree, so a threshold
edited after the numbers are in cannot be served. No commit carries it: **#9075 is open.**
The corpus argument is a *candidate* (`battery/config/corpus.json`), not a pre-registered
corpus — none exists for the same reason — and it is labelled as such in the log.

Then the gate, over the log it just produced:

```bash
python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone.log
# → capstone-verdict-check: — GREEN=0, RED=0, BLOCKED=3, UNPREREGISTERED=2
# → ✅ verdict table is structurally sound  (exit 0)

python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone.log --require-green
# → [V9] epic #7949: --require-green: 5/5 rows are not green  (exit 1)
```

Two exits, two questions: *is the table honest?* (yes, 0 findings) and *can the epic
close?* (no, V9). A single exit code would force the second answer to be read as the
first — which is the exact inference the epic exists to stop.

## The re-measurement (the issue body is provenance-stale)

The body's figures are pinned to `9037c7a7b`. Re-measured at `717355e67`:

1. **`docs/evidence/7949-capstone/` did not exist** — still true; this bundle is it.
2. **"No end-to-end v4 runner exists"** — still true, and now false in the specific
   sense that matters: the *pipeline* entry point does exist (below), and the missing
   runner is now this probe rather than nothing.
3. **⚠️ CORRECTED — a single entry point CAN drive all eight steps.** The body asks this
   as an open question. It resolves YES: `TortoiseSDK.capture_session` →
   `sdk._extract_session_v2` (`sdk.py:9698`) → `extractor_v2.extract_session_v2`
   (`extractor_v2.py:7611`), which calls, in one pass:

   | step | call site | witness the probe reads | on the receipt? |
   |---|---|---|---|
   | S0 READ | `_edus_from_conversation` `:7719` | `stats["edus"]`, `stats["chunks"]` | ✅ |
   | S1 NARRATE | `run_s1` `:7752` | `stats["llm"]["by_stage"]["s1"]`, `story_arc` | ✅ (`story_arc` is off-receipt; `by_stage` carries it) |
   | S2.1 EXTRACT | `run_s2` `:7776` | `by_stage["s2"]`, item counts | ✅ (`embed_list` is off-receipt; `by_stage` carries it) |
   | S2.2 VET | `_run_vet_pass` `:7805` | `result["vet"]` | ❌ **#9116** |
   | S2.3 CLASSIFY | `_apply_classify_kinds` `:7821` | `result["classify_later"]` | ❌ **#9116** |
   | S3 RESOLVE | `search_graph` `:7837` | `result["search"]` | ❌ **#9116** |
   | S5 CONNECT | `execute_embed` `:8057` (+ `run_s4` `:7858` gap filler) | `stats["s4_merge"]` | ✅ (gap-filler counters — see the labelling note below) |
   | S6 COMMIT | `execute_embed` payload | `result["payload"]` | ❌ **#9116** |

   So the capstone drives the **real entry point**, which is what the epic's phrase *"the
   pipeline running end to end"* demands — it does not compose the step functions, the
   construction the issue forbids. The pipeline RUNS all eight; the four `❌` rows are
   steps the probe cannot WITNESS **through that entry point**, because
   `capture_session`'s receipt carries `stats` and nothing else (#9116). The probe reads
   those four keys anyway so the observation is complete the day the seam lands; today
   they are reported unobserved and G5 refuses, attributed to the instrument.

   ⚠️ S5's witness is the **S4 gap-filler's** counters (`stats["s4_merge"]`, populated by
   `_rollup_llm(..., "s4")`), not a counter emitted by S5 itself, so a pass whose
   `execute_embed` did not run can still report S5 observed. That is a labelling
   limitation of the surface, recorded here rather than presented as a measurement.
4. **Dependency status at this head — the five re-read again on 2026-10-20, and one of
   them has MOVED** (the earlier read printed `OPEN` for all of them; `#7947` closed at
   `2026-10-19T12:54:38Z`, which this row and the log's E5 note now say out loud):

   | issue | title (truncated) | state |
   |---|---|---|
   | `#9075` | pre-register the capstone corpus, split and scoring BEFORE any E1–E5 run | **OPEN** |
   | `#9076` | E1+E2 on one run — volume falls, correctness preserved | **OPEN** |
   | `#9077` | E4 on the real extraction leg — wired AND persisted, no monkeypatch | **OPEN** |
   | `#5199` | the version anchor stops at Points — `:Object`/`:Event` not version-scoped | **OPEN** |
   | `#7947` | the raw-vs-narrative measurement — D3's falsifier | **CLOSED COMPLETED** (2026-10-19) |
   | `#9116` | the run's G5 cannot observe 4 of its 8 steps — `capture_session`'s receipt carries no step surface | **OPEN** |

   `#9075` is a precondition for the whole table (it is G0). ⚠️ A row moves to
   `RED`/`GREEN` only by RE-RUNNING the probe and producing the artifact — never by
   reading an issue's state, even when that state is CLOSED. That is why E5 did not turn
   green when `#7947` closed: the falsifier's numbers are in §10, but no pre-registered
   capstone pass has re-derived them, and the E5 row's owner is now the G0 gate that would
   allow one.

   `#9116` is not a row's blocker, it is the **run's**: the five rows above cannot be
   measured at all until the probe can WITNESS a step (see *What this bundle does NOT
   establish* and the Reproduce note). A table is only as reachable as the run that
   emits it.

## Why the steps are observed, never wrapped

E4's row *"fails if … the extractor was monkeypatched"*. A wrapper installed to watch the
pipeline is the same evidence-destroying move the row is guarding against, so the probe
reads the pipeline's OWN returned surface. The consequence is stated rather than hidden:
a step the surface cannot witness is reported **unobserved** (the run then fails G5 and
refuses) — an honest finding for `#7949`, never a guess.

## What this bundle does NOT establish

- **No E1–E5 number.** No row carries a numerator or denominator, because no measurement
  was performed. The table's honesty is structural, not numerical.
- **No E3 census instrument.** The probe carries none: E3's census reader over the real
  `:Point`/`:Object`/`:Event` nodes belongs to `#5199` (the lane that gives `:Object` /
  `:Event` their anchor at all). Shipping one here would mean a Cypher filter on a
  property that does not exist yet — it would return zero rows and read as a working
  instrument reporting `(0, 0)`.
- **No E1/E2/E5 evidence inputs.** The probe takes none, and this README no longer tells
  an operator to pass any: the artifacts' shapes were owned by `#9076` (E1/E2 on one run)
  and `#7947` (the §10 falsifier), so the flags land with them rather than ahead of them.
  `#7947` **LANDED** its measurement in its own harness
  (`tests/eval/write_path/`, PR `#9071`) — *not* as a capstone flag — which is the other
  half of why E5's blocker is now the G0 pre-registration rather than the falsifier.
- **The gate cannot detect a fabricated evidence file** with a plausible sha. Its stated
  limit: it closes *inference* (green rows with no artifact), not forgery.
- **Nothing about the five dependencies beyond their `state`** — the states above are a
  fresh read (2026-10-20, `#7947` CLOSED COMPLETED and the rest OPEN), but *why* each is
  open is the issue body's decomposition, not re-derived.
- **Nothing reachable by a run.** The eight-step observation is **currently unreachable**
  on `capture_session`'s receipt: `tortoise/sdk.py` assembles the receipt from `meta` +
  `extracted` (no `extraction` key), so the probe binds `result = captured` and hands
  `observe_steps` the receipt itself, which carries none of `result["vet"]`,
  `result["classify_later"]`, `result["search"]`, `result["payload"]`. Those four steps are
  therefore reported **unobserved** and the run refuses at G5 for EVERY input, once G0
  lands. A COMPLETED run — hence any row that could ever be measured — waits on `#9116`
  as well as `#9075`. This is a property of the caller's receipt, not of §4.4 or of the
  pipeline: the steps RUN, the surface the probe is handed does not say so.

## Reproduce

```bash
# the gate's own tests (DB-free, pure):
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
    tests/test_7949_capstone_verdict_check.py -q -p no:cacheprovider

# the real run, once #9075 lands AND #9116 lands AND a provider key + a searchable
# backend exist. #9116 is a second precondition, not a detail: the observation
# surface the probe reads is not on `capture_session`'s receipt yet, so a run today
# refuses at G5 with S2.2/S2.3/S3/S6 unobserved (see "What this bundle does NOT
# establish"). Re-running before it lands reproduces the REFUSED table, not a verdict.
python docs/evidence/7949-capstone/probes/capstone_run.py \
    --prereg <pre-registration commit> --corpus <pre-registered corpus> \
    | tee docs/evidence/7949-capstone/capstone.log
python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone.log --require-green
```

`--head <sha>` (optional) measures at a commit other than the worktree's current `HEAD`
— a re-run against a tagged commit. It is validated, not trusted: 7-40 lowercase hex
digits that resolve to a commit in this repo, refused before G0 otherwise. Every row is
stamped with it and the gate resolves the pre-registration's ancestry against it, so an
unusable value cannot reach a shipped log.
