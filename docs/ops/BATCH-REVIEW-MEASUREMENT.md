---
title: "Batch review — measured, and why it is not the queue fix"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise, review-gate, batch-review
aboutObjects: tortoise, "#9790"
created: 2026-10-23
updated: 2026-10-23
---

# Batch review — measured, and why it is not the queue fix (2026-10-23)

**Status:** measurement + a tested harness (`tools/batch_review.py`), **not wired into
any lane**. The motivating premise is falsified (§1); the safety question was still
answered empirically, and the answer is *do not adopt* (§4–§6).

**Question asked:** can one fresh-context reviewer review N PRs, so a landing lane
stops paying a full reviewer child per PR?

**Answer:** the cost that motivated batching — spawn and cold context — is **0.92 %**
of reviewer wall time. The wall is the review itself: in four of the five sampled
children model generation was **75–92 %** (tool latency 5–23 %); the fifth was
tool-bound at 60 % under fleet load. A batch
does not shorten that reasoning; it can only skip it — and when it did, it missed a
real P1 (§4). **Largest N demonstrated safe: 1.** The queue's actual lever is
**parallel per-PR dispatch** (§6.1), and the owner's "don't re-review when the merge
touched unrelated files" ask is **already shipped** as the diff-bound carry (§6.2) —
this experiment caught it working on a live PR.

---

## 1. The premise: "900 s to emit its FIRST message" is a threshold, not a latency

The quoted dispatch-log lines are emitted by the task watchdog for the **load-scaled
first-message bound**, latched at spawn:

```text
[task] first-message bound 300s → 600s (load1=13.48)   # 2x band (load 8–15)
[task] cut-gap bound 75s -> 113s   (load1=17.06)
[task] first-message bound 300s → 900s (load1=17.06)   # 3x band (load >= 16)
```

`DEFAULT_FIRST_MESSAGE_MS = 300_000` (`extensions/builtin-tools/index.ts`) is
multiplied by the load band (1x below 8, 2x at 8–15, 3x at >= 16) and **latched
monotonic per dispatch** (`loadScaledBound` in `extensions/builtin-tools/index.ts`; the load-band
latch is pinned by test cases E15/E15b in `builtin-tools.test.ts`). At load1=17.06 the kill threshold is 900 s. The log says
*how long the child is allowed to take*, not how long it took. Read as latency, it
makes spawn look like a 15-minute wall; measured, it is seconds.

## 2. The split, from the child transcripts

Method: `t0` = first transcript event (process up, prompt issued); `t_first_text` =
first assistant message carrying text (the event a dispatcher's
`latest_submitted_message` observes); `t_end` = last event; tool time = the sum of
each `toolCall → toolResult` gap. Reader (one-off, kept out of the repo):
`/tmp/reviewbatch/measure_sessions.py`.

Real reviewer dispatches, 2026-10-23:

| child | PR | to first msg | tool time | model time | total | pre share |
|---|---|---|---|---|---|---|
| `2d8cce1f` | agent-infra #2414 | 4.8 s | 8.8 s (5.1 %) | 157.5 s (92.1 %) | 171.1 s | 2.8 % |
| `5158602e` | agent-infra #2440 | 4.3 s | 21.3 s (6.7 %) | 293.5 s (92.0 %) | 319.1 s | 1.3 % |
| `888e4735` | agent-infra #2399 | 6.7 s | 18.6 s (6.5 %) | 261.5 s (91.2 %) | 286.8 s | 2.3 % |
| `57d91421` | agent-infra #2396 | 6.1 s | 71.8 s (23.0 %) | 234.8 s (75.1 %) | 312.7 s | 2.0 % |
| `0e729ee7` | agent-infra #2398 | 8.3 s | 693.6 s (59.9 %) | 455.2 s (39.3 %) | 1157.1 s | 0.7 % |

**N = 318** reviewer children whose transcript timestamp is before
`2026-10-23T14:00Z` (a frozen snapshot — the population grows through the day), same
method:

| metric | median | p90 | max |
|---|---|---|---|
| to first message | **4.9 s** | 9.3 s | 194.2 s |
| total wall | 445.3 s | — | — |
| billed input / cacheRead / output | 64,254 / 3,734,272 / 36,388 | 109,045 / 7,987,942 / 54,890 | — |
| tool calls (per child) | 50 | 78 | — |

**Only 1 of 318 children took >60 s to emit its first message; none took >300 s.**
Aggregate share of wall time before the first message: **0.92 %** (63.6 h of child
wall time, 35 min of it before the first message).

**Conclusion:** spawn + cold context is **0.92 %** of the wall. The other 99 % is
the review: a median 445 s per PR, essentially all of it model generation and tool
calls, with tool latency swinging from 5 % to 60 % under fleet load (the 0e729ee7
outlier had one 389 s tool call). Batching N diffs does not reduce the generation, and it does not reduce the
per-PR verification calls — so it cannot shorten the dominant term.

## 3. What a batch *can* amortise, and how little it is

First API call of a reviewer child = system prompt + brief (the shareable fixed cost):

| child | first-call input | first-call cacheRead |
|---|---|---|
| `888e4735`, `5158602e`, `2d8cce1f` | ~1.1 k | 38.0 k |
| `57d91421` | 1.6 k | 38.0 k |

So for N=4 the batch saves `3 × ~5 s ≈ 15 s` of spawn and `3 × 39 k ≈ 117 k`
cacheRead of prompt — against the measured 445.3 s and 3.73 M cacheRead per single
review. That is **~3 % of both** a review's wall time and its cacheRead (or ~0.8 % per
PR once amortised). Neither justifies changing how review happens.

## 4. Agreement test — the batch was weaker, and the miss is disqualifying

Setup: the same 4 PRs re-reviewed through the batch path at the same heads, against
single-PR verdicts already established today. Dispatched twice — run 1 with a
bounded read budget (my instruction, a possible confound), run 2 with **no read
cap** and an explicit instruction to follow callers.

| PR | single-PR verdict (source) | batch run 1 | batch run 2 | agree? |
|---|---|---|---|---|
| #2399 @ `aebcb037` | **`issues`** — 1 P1 + 1 P2 + 4 follow-ups (`888e4735`) | **`clean`** (3×P2) | **`clean`** (3×P2) | **NO — disqualifying** |
| #2414 @ `9bfd58e7` | `clean` — `NO ISSUES FOUND` (`2d8cce1f`) | `clean` (1×P2) | `clean` (0×) | yes |
| #2428 @ `61567482` | `clean` — record `diff=5021b171…` | `clean` | `clean` (2×P2) | yes |
| #2355 @ `aebae4bd` | `clean` — record `diff=1ec46580…` (carried from `e9b18d3b`; chain `3ea4b966 → e9b18d3b → aebae4bd`) | `clean` | `clean` (0×) | yes |

### The disagreement, quoted

The single-PR reviewer (`888e4735`), which read all three callers of the changed
function, returned:

> ```text
> ISSUE:
>   check_type: infra-safety
>   severity: P1
>   file: scripts/cloud-cell-bringup.sh:963
>   description: The new step gives every `--check` invocation real, unredirected
>     root side effects (`/swapfile`, `/etc/fstab`, `/etc/sysctl.d`,
>     `vm.swappiness`), and only `cloud-cell-bringup.test.sh` was given the
>     `SWAP_*`/`SWAP_SUDO` seams. `scripts/orchestrator-harness/selftest.sh` drives
>     the same script with a stub toolchain that makes the provider probe PASS and
>     stubs only `gh curl pi timeout docker uv tmux` — `sudo`, `df`, `fallocate`,
>     `mkswap`, `swapon`, `sysctl` are the HOST's.
>   outcome: on a host where `sudo -n` succeeds … `bash
>     scripts/orchestrator-harness/selftest.sh` reaches `check_swap` and creates a
>     multi-GiB `/swapfile`, `mkswap`+`swapon`s it, appends … to the real
>     `/etc/fstab` … from a test run.
> ```

The batch reviewer's verdict on that PR was **`clean`**, and its summary asserted the
opposite of the finding:

> "Every side effect is behind a documented env seam, and the suite redirects all of
> them (SWAP_SUDO emptied, sysctl/fstab/proc-swaps fixtures, tool shims) **so no host
> swap or /etc edit is possible**."

It had read `cloud-cell-bringup.sh` **and the PR's own test file** at the head — it
did not follow the function to the other two suites that drive `--check`. This is
**not** a lost-in-the-middle position effect (the PR was the *first* diff in the
prompt). It is depth: with four diffs in one context the reviewer stopped at the
PR's own tests; with one diff it walked the caller chain. Both got the same rubric
("PASS 2 … check callers").

Per the brief's own criterion — *"Any PR where the batch says clean and the single
review found a real issue is a disqualifying defect"* — **this is that defect**, and
the P1 is a host-mutating side effect (the silent-destruction class), not a nit.

**Run 2 is the control for the read-budget confound, and the miss REPRODUCES.** Without
any read cap — 62 tool calls, 8.86 M cacheRead, 117 k billed input, **3033 s** for the
same four PRs — the batch still returned **`clean`** on #2399 and never mentions
`selftest.sh`. It found a *different* set of three P2s there (including a real ordering
risk: `check_swap`'s `die` runs before `check_disk_reaper` installs the remedy), so the
batch is not uniformly blind — it is **non-deterministically covered**: each run walks
a different set of threads, and neither run walked the caller chain to the third suite.

**Result: 3 of 4 agreed; 1 disagreed in the unsafe direction, at both settings.** For a
merge gate, coverage that varies between runs is the defect regardless of how many
findings a run happens to produce: a clean verdict may not mean "nothing blocking
found", only "the blocking thing was not on the threads this run walked".

## 5. Measured win — the "win" was the skipped depth

Run 1 (bounded reads), against today's single-review medians (N=318):

| | wall / PR | cacheRead / PR | billed input / PR | tool calls / PR |
|---|---|---|---|---|
| single review (median, N=318) | 445.3 s | 3,734,272 | 64,254 | 50 |
| batch run 1 (bounded reads; 4 PRs in 487.7 s) | **121.9 s** | **735,040** | 16,503 | 7.75 |
| batch run 2 (no read cap; 4 PRs in 3033 s) | 758.3 s | 2,215,424 | 29,328 | 15.5 |

Run 1 is **3.7× faster and 5.1× cheaper per PR** — and it is exactly why the P1 was
missed: it spent ~8 tool calls per PR where a single review spends ~50, **about a sixth
of the verification**. The saving and the defect are the same fact, so the saving is
not a win.

Run 2 spent 1.7× the single-review wall time *per PR* (758 s vs 445 s) and still
missed the P1 — so the defect is not a read budget, and there is no setting where the
batch becomes both as deep as a single review and cheaper than it. One extra cost also
appears at this size: a batch child is a **single failure domain**. Run 2 spent 10+
minutes with one `gh api` call hung under fleet contention; four children would have
lost one PR, one child loses all four.

**Honest number: the batch is only faster when it is shallower. At equal depth it is
slower, and it is less reliable (shared failure).**

## 6. What the numbers actually recommend

### 6.1 The queue fix is parallel per-PR dispatch, not batching

The landing lane dispatched its reviewers **serially** — measured start times
`12:24:15 → 12:45:48 → 12:51:54 → 12:59:21` with durations 171 s / 319 s / 287 s /
1157 s. Serial child time was 32.2 min; the same four in parallel would have taken
`max(171, 319, 287, 1157) = 19.3 min` — **1.7× less wall, and every PR keeps its own
fresh context, its own full verification depth, and an independent failure.** The
lane's own span was longer still (12:24 → 13:18, 54 min) because it also did rail
work between dispatches.

That is the change worth making: dispatch N reviewers as N **background** tasks and
collect them, rather than one child with N diffs. It needs no change to the review
rubric, no schema, and no new failure mode.

### 6.2 "Don't re-review when the merge only touched unrelated files" is already shipped

The diff-bound carry (`record-review.sh`'s `diff=<64-hex>` marker, #2982/#6072) is
exactly that: a head that moves without changing the reviewed artifact re-binds the
existing verdict instead of paying a new review. It fired on a live PR during this
experiment: **#2440's head moved `022d99da → 4219e2ba` with an unchanged diff
(`fda0ee0b…`)** and landed today on the carried record — no second review. #2355 is
the same shape (`3ea4b966 → e9b18d3b → aebae4bd`, `diff=1ec46580…`).

So the owner's ask has two answers already: the carry for no-op push churn, and
parallelism for throughput. Batching adds a third path whose measured benefit is
negative at equal review depth.

### 6.3 If batching is ever revisited

The harness stays as the tested artifact, with its limits stated: the schema forces a
per-PR head and diff binding (no batch verdict can be recorded), the caps are
attention guards with a conservative token ESTIMATE (3.5 chars/token, deliberately
erring high — the code does not claim a calibration this document does not carry),
and a malformed/aborted PR falls out to a single review. What it
**cannot** enforce is verification depth — and that is what the disagreement was.
A future attempt would have to make depth mechanical (e.g. a required evidence list
of the files read per PR, cross-checked against the caller graph) before the
agreement test could pass at any N > 1. Until then: **N = 1**.

**A second, larger limit: batching widens prompt injection from one PR to N.** Every
structural defence in `validate_answer`/`plan_records` echoes data the prompt itself
supplied — `===== BEGIN PR {pr} @ {head_sha} diff={diff_sha256} nonce=…` in
`build_prompt` — and the answer schema asks the model to copy those values back. The
nonce stops a diff from *forging a delimiter*; it does not stop a diff from
*instructing* the model. So a single attacker-controlled diff in the batch can ask for
`clean` for its siblings, and the per-PR head/diff echo will **validate**, because the
values it is checked against are the ones that same header handed it. Blast radius
under a single-PR review is one attestation; under a batch it is N. The only
mechanical mitigation is not a schema check but isolation — one child per PR, i.e.
exactly §6.1 — which is why the recommendation below is N = 1 on safety grounds as
well as on depth.

## 7. The tool (kept, not wired)

`tools/batch_review.py` — `prepare` (one diff read per PR, diffs cached, digest from
the shared normalizer), `verify` (per-PR schema validation, per-PR abort), `post`
(one evidence comment per PR), `record` (`record-review.sh` per PR at its own head
with `--expect-head`). Its docstring carries the rule:

> **The batch is a TRANSPORT OPTIMISATION for the reviewer's context, not a
> relaxation of the review: a batch verdict can never stand in for a per-PR
> verdict.**

Enforced in code, not prose: a top-level `{"verdict": "clean"}` makes **every** PR
invalid; a verdict echoed from another PR's head is refused; a `clean*` contradicted
by a P0/P1 is coerced to `issues`; one malformed result fails only its own PR; and no
record is minted for a PR that fell out, for `issues`, or without its own evidence
comment. No tail step reports a partial batch as a success: `verify` exits 3 on any
fallout, `post` exits 3 when a queued PR is left with no evidence comment (and
persists each comment id as it is minted, so an abort mid-loop keeps the ids of the
comments that already exist on GitHub and a re-run does not post them twice), and
`record` exits 3 when a recordable PR was left with no record. The evidence map
carries its own `__meta__` (batch id + the head each comment was minted at), so a map
from an earlier batch, or a comment minted at another head, is refused rather than
bound as this run's artifact. `diff_sha256` is computed with
agent-infra's one shared normalizer, and two
of the four prepared digests match digests `record-review.sh` itself minted today
(#2428 `5021b171…`, #2355 `1ec46580…`) — verified against the real recorder, not
assumed.

```bash
uv run python tools/batch_review.py prepare --repo owner/name --pr 1 --pr 2 --out .batch-review
# dispatch ONE fresh-context reviewer with .batch-review/prompt.txt
uv run python tools/batch_review.py verify  --manifest .batch-review/manifest.json \
    --answer answer.json --recheck-heads --out .batch-review/verified.json
uv run python tools/batch_review.py post    --manifest .batch-review/manifest.json \
    --answer .batch-review/verified.json
uv run python tools/batch_review.py record  --manifest .batch-review/manifest.json \
    --answer .batch-review/verified.json
```

Tests: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_batch_review.py -q`
(53 cases; the load-bearing ones: a top-level verdict invalidates every PR, one bad
result never invalidates a sibling, a record is never minted for a PR that fell
out or whose digest is not the manifest's, neither `post` nor `record` reports a
partial batch as a success, a comment is never re-posted for a PR already covered at
that head, a comment minted at another head is not accepted as evidence, and a PR
whose head moves between `prepare`'s meta read and its diff read is dropped rather
than bound). The record path's EXECUTOR is covered too:
`record` is run against a stub `record-review.sh`, so the planned argv (and the
fail-closed rc when the recorder refuses a PR) is asserted rather than assumed. A
finding's file anchor is MEASURED against the cached diff it claims to come from — a
finding naming a path that diff does not touch fails its PR — so the evidence
comment's "anchored to files in this PR's diff" is a checked sentence, and the
footer says the anchor was NOT checked when a run carries no readable cached diff.
The two digest cases are host-free by
construction: the real normalizer is host state (the CI runners carry no
`scripts/lib/`), so one case INJECTS a stub normalizer to assert the `index` line is
not content, and the other pins the raw-sha256 fallback that a runner takes.

## 8. Honest limits of this measurement

- The parent reviewer children come from one machine on one day at load 13–35; the
  spawn figure is robust (318 samples, p90 9.3 s) but tool latency is not (§2's 5 % to
  60 % swing is load, not code).
- The agreement test is **N = 1 defect in 4 PRs**, not a rate. One disqualifying miss
  is enough to refuse the change; it is not an estimate of the batch's error rate.
- Token figures are the provider's own `usage` counters; "cost" was not compared
  across the two paths because run 1's cost is not a valid price for a review that
  missed a P1.
- External research was attempted and **unavailable on this machine**: no
  `PERPLEXITY_API_KEY` (rung 2), no `EXA_API_KEY` (rung 3), `seo-intelligence` MCP
  absent (rung 1). The one primary source fetched is Liu et al., *Lost in the
  Middle* (arXiv 2307.03172 / TACL 2023): multi-document QA degrades when relevant
  information sits in the middle of a long context. It predicts the *positional*
  failure mode; the failure actually measured here was **depth**, not position, so
  the paper is cited as the reason to keep N small and test position explicitly — not
  as the explanation of this result.
