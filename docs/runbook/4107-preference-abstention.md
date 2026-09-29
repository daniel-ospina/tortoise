---
title: "4107 — a preference/advice question abstains with the answer-bearing turn in context"
type: operations
domain: operations
doc_status: live
created: 2026-09-23
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# #4107 — a preference/advice question abstains with the answer-bearing turn in context

**Status:** characterization complete — **no prompt change landed** (deliberate; see *Scope decision*).
**Instrument:** `tools/ask_shape_rate.py` (the D3 answer-shape instrument, #4064) — **unmodified**.
**Diagnostic:** `docs/runbook/4107_preference_abstention_diagnostic.py`
**Receipts:** `docs/runbook/4107-preference-abstention.json` (diagnostic) ·
`docs/runbook/ask-shape-rate-2026-09-23.json` (instrument, `--mode live`, this tree)
**Measured tree:** `ada2706413158543eae07da68f38aeba19adb9a9` (`origin/main`), 2026-09-23
**Reader pin:** `deepseek-direct` / `deepseek/deepseek-v4-flash`, temperature 0, max_tokens 500 —
the diagnostic and the instrument both **assert** the built reader is the production
`RoutingModel` on exactly `deepseek-direct` (`assert_reader_pin`), and the instrument records
the resolved provider per question.

This is the W2A read-path root cause on `d6233ab6` — a preference question whose answer-bearing
turn is present in the reader's context yet which the pinned reader abstains on. #4107's discipline
is *characterise before changing the prompt*; this document is that characterisation.

⚠️ **Scope of the claim.** The issue calls `d6233ab6` "the one L1 failure that is not a retrieval or
assembly miss", on the basis of its 2026-09-18 STEP-0 rank (7) contrasted with `1d4e3b97`'s (84).
On the 2026-09-23 dense-leg receipt **five** questions fail L1 (`1d4e3b97`, `d6233ab6`, `e4e14d04`,
`gpt4_7a0daae1`, `gpt4_8279ba02`) and all five carry `ctx_recall: true`; this document verifies the
answer-bearing **turn's** presence for `d6233ab6` only. The other four L1 failures are **not**
characterised here — three of them (`e4e14d04`, `gpt4_8279ba02`, `gpt4_7a0daae1`) are the temporal/derived class and
`1d4e3b97` is the retrieval-window class the issue files separately; the exclusivity claim should not
be read as measured on this tree.

---

## 1. Presence — established, not assumed

Question `d6233ab6` (`single-session-preference`):
> "I've been feeling nostalgic lately. Do you think it would be a good idea to attend my high school reunion?"

The answer-bearing turn is `answer_b0fac439_t2` (`has_answer: true`; the fixture witness is
asserted by the diagnostic and by `tests/test_reader_4107_advice_shape.py`, not trusted from a
label).

**Fused rank (2026-09-23, dense leg active, pool 200): rank 4.** Recorded in
`4107-preference-abstention.json` → `pool.gold_turn_fused_rank`.

- Issue #4107's STEP-0 measurement records **rank 7** for the same turn. That number is on the
  2026-09-18 **FTS-only** lane and is not carried by the committed 2026-09-18 receipt, which has no
  per-turn rank field — it is the issue's own measurement, cited here as such. The 2026-09-23 run
  (dense leg active, `retrieval_degraded` false on 21/21) ranks the turn 4. The number moves with
  the lane's retrieval configuration; **presence does not** — both ranks are inside the assembled
  reader window.
- Pool neighbours (`pool.top_12`): `ultrachat_329160_t8`, `…_t9`, `…_t10`, **`answer_b0fac439_t2`**,
  `94bc18df_3_t10`, `ultrachat_125013_t4`, `f916c63a_2_t0`, `32f28c7b_1_t10`, …

**Rendered block** — verbatim from `presence.gold_turn_rendered_block` (the full block; it is one
turn, and the reader window's 16 000-token budget does not split turns):

```
[session answer_b0fac439] (session date 2023-05-23) [user] I still remember the happy high school
experiences such as being part of the debate team and taking advanced placement courses in
economics. Now, I am surprised that I will become a Economics major so soon. Speaking of future,
what are the average salaries for data scientists in different industries, and are there any
specific industries that are more in demand for data scientists?
```

**Verbatim overlap with the fixture's gold answer:** 16 contiguous words —
`high school experiences such as being part of the debate team and taking advanced placement courses`
(`presence.gold_answer_span`).

**What the instrument records for this question** (`ask-shape-rate-2026-09-23.json`):

| leg | result |
|---|---|
| L1 abstention | **FAIL** — `abstained: true`, expected `false` |
| L2 provenance | PASS — gold session `answer_b0fac439` in `retrieved_session_ids`; both shipping handlers carry seeded ids |
| `ctx_recall` | PASS — the gold session's turns are covered in the assembled evidence |
| `gold_answer_span_words` | 16 — the fixture's gold answer is verbatim-ish in the evidence |
| L3 grounding | **not a presence signal here** — see below |

⚠️ **L3 is not evidence that the answer is grounded, on this question.** L3 is a lexical floor
between the evidence and the **committed answer**; when the committed answer is an abstention, the
floor measures the abstention's wording, not the gold. The 2026-09-23 run scores `l3_grounding:
true` only because the abstention's own phrase "so I don't have information" happens to share the
4-word span `i don t have` with the evidence. The four committed FTS-only receipts (2026-09-18, the
plain 2026-09-19 run, and the two 2026-09-19 `-dense-leg-run{1,2}` runs — all four carry the
instrument's own inert-dense-leg caveat) score `l3_grounding: false` for this same question. So the honest reading is: **presence rests on
`ctx_recall`, the 16-word gold span, and the rank + rendered block above — not on L3.** (This is why
the failure is characterised as "the reader abstained with the answer-bearing turn in context": an
L1 failure, with retrieval and provenance green.)

## 2. The abstention, verbatim

| receipt | lane | verbatim abstention |
|---|---|---|
| `ask-shape-rate-2026-09-18.json` (generated 2026-09-19 01:57Z) | FTS-only | "The context does not contain information about whether attending a high school reunion would be a good idea. It does mention the user's happy high school memori…" (160-char head) |
| `ask-shape-rate-2026-09-19.json` (05:29Z) | FTS-only | same 160-char head wording |
| `ask-shape-rate-2026-09-19-dense-leg-run1.json` (18:18Z) | FTS-only (file name is legacy) | "The context does not contain information about whether attending a high school reunion would be a good idea." |
| `ask-shape-rate-2026-09-19-dense-leg-run2.json` (19:01Z) | FTS-only (file name is legacy) | "The context does not contain information about whether attending a high school reunion would be a good idea." |
| **`ask-shape-rate-2026-09-23.json` (this tree)** | **dense leg active** | "The context does not mention a high school reunion or any plans to attend one, so I don't have information to answer that." |

All five are abstentions. One names the reunion as **unmentioned** (2026-09-23 — the Phase-2
`asked subject absent` shape); the two FTS-only forms above it share the 2026-09-18 form's first
sentence ("does not contain information about whether attending a high school reunion would be a good
idea") — no information about the *evaluation* — differing from the 2026-09-18 wording only in that
they omit the memory citation. Each "FTS-only" receipt carries the instrument's own caveat that the
dense leg was inert (`no_embeddings`), so those four rows are sparse-lane (FTS+RRF) measurements;
only the 2026-09-23 row ran with the dense leg active. The written text and the `abstained` predicate
agree in every run — there is no discarded answer.

## 3. The prompt that was actually emitted

`run_ask_lane` resolves `question_type` with `detect_question_type(question)`. For `d6233ab6`:

```
detect_question_type(...) -> None
```

So the emitted system prompt is `system_prompt_for(None)` = `_SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT`.
The diagnostic does not re-derive this: it **captures the wire `(system, user)`** through a stub
reader and asserts the captured system equals `system_prompt_for(resolved question_type)` before
recording it. **`_PREFERENCE_FRAGMENT` is NOT emitted** — its marker `"PREFERENCE INSTRUCTIONS"` is
absent, and its opening premise ("this question asks which option the user prefers") does not fit a
question with no option set.

⚠️ **This corrects a premise in the issue.** The issue states the prompt "already covers this class
twice", citing `_PREFERENCE_FRAGMENT` at `tortoise/reader.py:95-102`. On the measured path it covers
it **once**: the detector returns `None` for this question, so only the universal clause applies.
(The detector returns `None` for **20 of the 21** fixture questions; the exception is
`gpt4_7a0daae1`, which matches a `_TR_PATTERNS` temporal rule. On 2026-08-30 the same 20/21 figure
was recorded in `docs/runbook/1987-ask-abstention-check.md`.)

## 4. Root cause — a tension inside the universal clause

`_ABSTRACTION_FRAGMENT` carries two rules that pull in opposite directions on an **advice-shaped**
question whose asked *event* is absent but whose relevant *experiences* are present:

1. **The synthesis license.** "When the question asks what the user prefers, thinks, or would like,
   and the context states their relevant preferences, experiences, or prior choices, answer by
   drawing on them — **do not abstain because the answer must be synthesized rather than quoted**."
2. **The asked-subject scoping guard.** "For a derived or synthesized answer, still check the asked
   subject: **commit only when the events or facts the question asks about are actually in the
   context; if they are absent, abstain (Phase 2)**." Phase 2 then reads: "abstain ONLY when no turn
   in the context mentions the asked subject or event at all."

On `d6233ab6` the two collide: **no** turn mentions the user's high school reunion — the only
`reunion` string in the assembled evidence is an unrelated generic bullet in a photo-organizing plan
in `f916c63a_2` ("Family Gatherings (holidays, reunions, etc.)"), and the `e419b7c3_4_t5`
"Friends reunion" reference is haystack-only and never rendered — while the debate-team /
AP-economics memories are present *and are the gold answer's basis*. Phase 2's condition is
therefore met on the asked event, and the pinned reader resolves the tension **against** the
synthesis license: it abstains, correctly noting the asked event is unmentioned.

The abstention is **truthful under a literal reading of Phase 2** and **wrong under the synthesis
license**. The
prompt does not say which rule wins, and the model picks the guard.

**This is not reader variance.** All five committed runs above abstain, at temperature 0,
under the asserted pin; the 2026-09-18 and 2026-09-23 wordings differ but the decision does not.

### Prior art — this is not the #2027 cause

`tortoise/reader.py` documents the `#1366 → #1546 → #1762 → #1775 → #2027` oscillation, and
`#2027` already names `d6233ab6` as its canonical generic-baseline false-abstention case. There is
a green regression test for the same shape
(`tests/test_reader_abstention_calibration.py::test_preference_synthesis_commits_on_generic_baseline`,
fixture 2). The two are **not** in conflict:

- #2027's cause was that, with no type fragment engaged, "the reader treated 'no category matched' as
  'abstain'" (`tortoise/reader.py:196`). The fix added the
  category-independent presence-commit rule. Its regression test is a **wiring pin driven by a
  compliant-model fake** — the fake mechanically executes the pinned rule, so it verifies that the
  rule reaches the model, not that the real model follows it (the test's own docstring says so).
- This cause is distinct: the clause's *own* asked-subject scoping guard licenses the abstention
  because the asked **event** is absent. The real pinned model follows the guard over the license.

A candidate sentence (filed as #4837) would amend the #1775/#2027 ordered clause, which is exactly why
it needs a battery before it lands.

## 5. Reachability — before/after on the SAME frozen context

`--replay` holds the rendered evidence fixed and replays it through the same pinned reader and call
shape, changing only the system prompt (`replay.note` records the contract):

| variant | abstained | reader output |
|---|---|---|
| **shipped** `system_prompt_for(None)` | **true** | "The context does not mention a high school reunion or any plans to attend one, so I don't have information to answer that." |
| `system_prompt_for("single-session-preference")` | **true** | "The context does not mention a high school reunion or the user's thoughts on attending one, so I don't know whether it would be a good idea." |
| shipped + targeted **advice sentence** | **false** | "Based on your memories, yes — it sounds like a good idea. You've spoken fondly of your happy high school experiences, like being part of the debate team and taking advanced placement economics courses, and you've stayed connected to that time by thinking about old high school friends. Reconnecting could be a meaningful way to revisit those positive memories." |

Each row is the committed receipt's `replay.variants[*].answer` verbatim. The receipt also records
per-variant `reader_calls` / `escalated` (all `1` / `false` — no #2280 budget escalation, so the
comparison is a prompt change at a constant call shape) and the answer↔evidence verbatim span.

Two conclusions, both load-bearing:

1. **Routing to `_PREFERENCE_FRAGMENT` does not change the outcome.** That one candidate — the
   fragment the issue believed already covered this class — is refuted. (Other fragment designs
   were not tested; see Rejected Alternatives in the scoping comment.)
2. **The gap IS prompt-reachable.** A sentence that explicitly resolves the §4 tension for the
   advice shape flips the outcome. The receipt records the advice answer's shared verbatim span with
the evidence as `being part of the debate team and taking advanced placement` (**10 words**), so it
would clear the L3 floor as well as L1.

⚠️ Reader wording varies run-to-run at temperature 0 (the 2026-09-19 and 2026-09-23 forms differ);
the **decision** (abstain / commit) does not vary across any recorded run of the shipped prompt
(§2). Quote the receipt for the exact wording.

The candidate sentence (recorded in the diagnostic as `ADVICE_SENTENCE`, **not shipped**) is:

> ADVICE QUESTIONS: when the question asks for advice or an opinion about the user's own life, plans,
> or decisions (for example 'would it be a good idea to…' or 'should I…'), and the context states the
> user's relevant experiences, preferences, or interests, answer with advice grounded in those
> memories. The specific event or plan the question names need not be mentioned in the context; do
> not abstain, and do not say the context lacks information, merely because that event or plan is
> not mentioned.

## 6. Scope decision

**It is a real, reproducible read-path gap — not single-sample wobble** (five abstentions, §2), and
its mechanism is the §4 rule tension, not retrieval (rank 4, `ctx_recall` green) and not the
`_PREFERENCE_FRAGMENT` route (§5.1).

**No prompt change is landed, deliberately.** The candidate is a *universal* prompt-contract change
justified by **one** advice-shaped sample. #4107's own Research Needed §1 says distinguishing a
systematic advice-question gap from a one-off "would need a small battery of comparable advice
questions"; the frozen fixture is SHA-pinned (*a changed fixture is a new instrument*), so that
battery cannot be added to this set. An unmeasured universal prompt edit on one sample is exactly
what the issue forbids, and the reader is **eval-only** (#3849) — a prompt tweak here moves a
measurement, not a shipped surface.

Filed as **#4837** — *the advice-question battery + the §5 candidate + the false-commit guard* — to
land the change only where the battery shows the class (not the sample), with the guard
(the three `_abs` questions, plus advice-shaped near-miss controls) measured before/after.

## 7. Reproduce

```bash
# deterministic (free): presence, emitted prompt, rank
uv run python docs/runbook/4107_preference_abstention_diagnostic.py

# + the paid before/after reader replay on the frozen context
uv run python docs/runbook/4107_preference_abstention_diagnostic.py --replay \
    --out docs/runbook/4107-preference-abstention.json
```

Requires the embeddings extra (`uv sync --extra embeddings --extra parity`). Without it the
diagnostic **aborts** at `assert_embedder` (fail-closed, #2985) — it does not silently fall back to
an FTS-only rank.

Instrument cross-check (`--mode live` re-measures the per-question legs; the movement control and
known-GREEN legs are the instrument's own validation and were not run here):

```bash
uv run python tools/ask_shape_rate.py --pin-sha <tree HEAD> --mode live \
    --receipt docs/runbook/ask-shape-rate-2026-09-23.json
```
