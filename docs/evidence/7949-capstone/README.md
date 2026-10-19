---
title: "Capstone evidence — #7949 / #9080 extractor-v4 E1–E5 verdict table"
type: evidence
domain: platform
doc_status: final
created: 2026-10-19
subjects.team: epistemic-team
aboutSubjects: Tortoise extractor v4
aboutObjects: "#7949, #9080, #9075, #9076, #9077, #5199, #7947, EXTRACTOR-V4-ARCHITECTURE.md"
---

# Capstone evidence — `#7949` / `#9080` (extractor-v4, E1–E5)

**Measured at:** `717355e67` on branch `feat/9080-tm` (2026-10-19). Worktree dirty:
only the files this bundle adds (`docs/evidence/7949-capstone/**`,
`tools/capstone_verdict_check.py`, `tests/test_7949_capstone_verdict_check.py`,
`config/ci-surfaces.yml`); **no product source under `tortoise/` is modified**, which
is what makes the E4 row's "no monkeypatch" clause possible at all.

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
| **E5** | the falsifier (§10) was run on OUR corpus | ⛔ **BLOCKED** | `capstone.log` | **`#7947`** |

`UNPREREGISTERED` and `BLOCKED` are deliberately different verdicts: E1/E2's mechanism
exists and what is missing is the **frozen thresholds** (`#9075`); E3/E4/E5 are missing
their **mechanism or measurement** and `#9075` landing would not change that. Collapsing
them into one word would let the next lane re-run and still learn nothing.

## What the run did

The acceptance command from the issue, executed verbatim at `717355e67`:

```bash
python docs/evidence/7949-capstone/probes/capstone_run.py \
    --prereg "$(git rev-parse HEAD)" --corpus battery/config/corpus.json \
    | tee docs/evidence/7949-capstone/capstone.log
```

**G0 refused, before any model call** (§4.4: *S0 is FAIL-CLOSED — end the run before any
model call or write*):

```text
run: REFUSED — commit 717355e67… does not carry
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

   | step | call site | witness the probe reads |
   |---|---|---|
   | S0 READ | `_edus_from_conversation` `:7719` | `stats["edus"]`, `stats["chunks"]` |
   | S1 NARRATE | `run_s1` `:7752` | `stats["llm"]["by_stage"]["s1"]`, `story_arc` |
   | S2.1 EXTRACT | `run_s2` `:7776` | `by_stage["s2"]`, item counts |
   | S2.2 VET | `_run_vet_pass` `:7805` | `result["vet"]` |
   | S2.3 CLASSIFY | `_apply_classify_kinds` `:7821` | `result["classify_later"]` |
   | S3 RESOLVE | `search_graph` `:7837` | `result["search"]` |
   | S5 CONNECT | `execute_embed` `:8057` (+ `run_s4` `:7858` gap filler) | `stats["s4_merge"]` |
   | S6 COMMIT | `execute_embed` payload | `result["payload"]` |

   So the capstone drives the **real entry point**, which is what the epic's phrase *"the
   pipeline running end to end"* demands — it does not compose the step functions, the
   construction the issue forbids.
4. **Dependency status at this head — all five re-queried, all OPEN**
   (first read was rate-limited mid-lane, `gh` HTTP 403 / `gh-rest: gh-limit … FAILED`;
   re-read after the reset, before the PR):

   | issue | title (truncated) | state |
   |---|---|---|
   | `#9075` | pre-register the capstone corpus, split and scoring BEFORE any E1–E5 run | **OPEN** |
   | `#9076` | E1+E2 on one run — volume falls, correctness preserved | **OPEN** |
   | `#9077` | E4 on the real extraction leg — wired AND persisted, no monkeypatch | **OPEN** |
   | `#5199` | the version anchor stops at Points — `:Object`/`:Event` not version-scoped | **OPEN** |
   | `#7947` | the raw-vs-narrative measurement — D3's falsifier | **OPEN** |

   `#9075` is a precondition for the whole table (it is G0). ⚠️ A row moves to
   `RED`/`GREEN` only by RE-RUNNING the probe and producing the artifact — never by
   reading an issue's state, even when that state is CLOSED.

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
  an operator to pass any: the artifacts' shapes are owned by `#9076` (E1/E2 on one run)
  and `#7947` (the §10 falsifier), so the flags land with them rather than ahead of them.
- **The gate cannot detect a fabricated evidence file** with a plausible sha. Its stated
  limit: it closes *inference* (green rows with no artifact), not forgery.
- **Nothing about the five dependencies beyond their `state`** — that they are OPEN is a
  fresh read, but *why* each is open is the issue body's decomposition, not re-derived.

## Reproduce

```bash
# the gate's own tests (DB-free, pure):
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
    tests/test_7949_capstone_verdict_check.py -q -p no:cacheprovider

# the real run, once #9075 lands and a provider key + a searchable backend exist:
python docs/evidence/7949-capstone/probes/capstone_run.py \
    --prereg <pre-registration commit> --corpus <pre-registered corpus> \
    | tee docs/evidence/7949-capstone/capstone.log
python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone.log --require-green
```
