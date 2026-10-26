---
title: "The S2.2 / S2.3 typed-decision seam"
type: engineering
domain: platform
doc_status: live
created: 2026-10-26
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: EXTRACTOR-V4-ARCHITECTURE.md, extractor pipeline, S2.2 VET, S2.3 CLASSIFY
---

# The S2.2 / S2.3 typed-decision seam (issue #5087)

> **What this is.** The design record of the seam that
> `EXTRACTOR-V4-ARCHITECTURE.md` §4.2 described as **missing** — *"swapping Jev in
> requires an adapter, a prompt, and a parser … it is a small piece of work, but
> it is work, not a config flag."* It now exists, internal and **off by
> default**. This page states what it does, what it deliberately does not do,
> what it costs, and how to arm it.

**Code:** `tortoise/s2_decision_seam.py` (internal — not a `tool_registry` tool, no
`TortoiseSDK` method; nothing about the public surface changes).
**Tests:** `tests/test_s2_decision_seam_5087.py`.
**Consumers:** `vet_gate.vet_candidates(..., arbiter=…)` (S2.2) and
`kind_classifier.KindClassifier(..., tail_decider=…)` (S2.3) — both seams that
already existed and had no production caller.

---

## 1. The three parts, one line each

| part | what it is |
|---|---|
| **adapter** | `vet_verdicts` / `classify_tail` turn a candidate batch into **one round trip**: `state` (the narrative, plus one element per candidate) and a **map** of typed `questions`, answered in one response. The shape is copied from a working caller where one existed — see §2 for the two parts that are not. |
| **prompt** | two versioned question sets, `vet-v1` (a `noul` *validity* question + a `score` *atomicity* question per candidate) and `classify-v1` (a `choice` per candidate over the kinds the kNN just offered). The version rides in the seam's record (`result["vet"][*]["batch"]["seam"]["prompt"]` for VET, `stats["seam"]["prompt"]` for the tail) and in every degraded record, so an artifact names the wording that decided. |
| **parser** | `_noul` / `_score` / `_choice` validate the answer *against the question that was asked*: the echoed `type`, a finite probability in `[0, 1]`, a score on the declared scale, a choice inside the offered set. A violation raises `SchemaViolation` **carrying the question name**; nothing downstream coerces a bad answer into a label. |

## 2. The request shape is copied from a caller, not from the documentation

Three shapes 400 if you write them from the docs' prose (issue `#7866`):

- `questions` must be a **map** (name → question). The documented array form returns
  ``400 `questions` must be a map of at least one question.``
- every question needs **`instructions`** (``400 'Question "t" needs instructions.'``);
- a **`score`** question must carry `criteria`: an **ordered array of 2–10 level
  descriptions, low → high**. `vet-v1` sends four levels
  (`ATOMICITY_LEVELS`), so a score is on `0..3` — and it is **fractional in
  practice** (the vendor's own example returns `2.97`), which is why the band
  compares against a threshold instead of demanding an integer.

The envelope is validated before any answer is read: `model` must equal the pin
(`jev-1.13.0`) and `answers` must be a map. An answer attributed to an unverified
model is not an answer.

**What is NOT previously-exercised.** Two shapes here are taken from the vendor's
own reference page (fetched 2026-10-26), not from a live in-repo caller: the
`choice` question's `criteria` **map** (kind → description) and the `model`-echo
check in the envelope. The in-repo caller this module's construction was copied
from (`tools/collision_preflight.py`, since deleted by `#8175`) used `noul` only.
If either shape is wrong, the first armed call returns 400 → `Unavailable` → the
declared fallback (nothing is dropped, nothing is minted) and that call is what
settles it; the counters name it. Do not read this page as "verified live".

## 3. What it does

- **S2.2 VET** — per candidate: *is this a durable, self-contained item?* (`noul`,
  adversarial) and *how atomic is it?* (`score`). Two questions, because §6 fixes
  VET's shape as **Noul + Score**, both answered in one call.
- **S2.3 CLASSIFY** — per tail candidate: *which of these kinds is it?* (`choice`)
  over exactly the kNN's candidate list, so the answer cannot leave the pack
  vocabulary by construction. The kNN fast path is untouched: this replaces only
  the hardcoded prompt + JSON parser that used to answer the low-margin tail.
- **The verdict is the caller's.** The model returns a number; `vet_verdict` reads
  it through `Bands` — written down, argued in `#4899`/`#2453`, overridable per
  call and recorded in `stats["bands"]`:

  | band | default | meaning |
  |---|---|---|
  | `keep_min` | `0.70` | `noul >= 0.70` ⇒ `KEEP` |
  | `discard_max` | `0.30` | `noul < 0.30` ⇒ *eligible* for `DISCARD` |
  | `atomic_min` | `2.0` | atomicity below this **vetoes** the discard (`NOOP`) |

  `NOOP` is the honest word for the in-between band: a decision *was* attempted
  and it changes nothing (`vet_gate`'s own definition). Nothing in the module can
  emit `DISCARD` without a parsed, in-schema, in-band answer. On a **degraded**
  path it emits **no verdict at all** — see §5.

## 4. What it deliberately does NOT do

Each is a **recorded decision**, not an omission:

- **No `SPLIT`.** It is absent from O4's vocabulary (§16.3), so atomicity cannot
  ask for a split — it can only **veto a drop** (`atomic_min`). That is the only
  action the vocabulary can express, and guessing the rest would be a second
  vocabulary.
- **No `RENARRATE`, no coverage question (Level-1 q3).** That is a **batch**
  verdict, and §S2.2 bounds the loop it triggers (*"once, then accept"*).
- **No `MERGE`.** Near-duplicate detection is `#5006`'s mechanical ladder; the
  arbiter that decides a *flagged pair* is a different question with a different
  evidence bar (§16.4).
- **Not S3 and not S5.** *"Is this the same entity as that?"* and the lifecycle
  `Choice` are other steps with other inputs; a seam that answered them from
  S2.2's evidence would be deciding new-vs-existing before S3 — the circular
  dependency §S2.2 removes.
- **No retry ladder, no cache.** The steps are fail-open and the run records the
  failure; a cached probability would be a probability read under a stale prompt
  version. A caller that needs backoff owns it.
- **No per-mention links, no span, no `sourceKind`.** It never re-derives a source
  type (S0 knows it mechanically and for free) and never collapses the
  `sourceKind`/`documentKind` axes: `classify-v1` offers **genres and pack kinds**,
  which is what S2.3 assigns (Q2).
- **It does not touch the deployed pipeline** until armed (§6).

## 5. Degradation is explicit, and its direction is the step's (§4.4)

| step | capability unavailable (no key, 429, timeout, 5xx, pin mismatch) | answer unparseable / out of schema |
|---|---|---|
| **S2.2** | **no verdict** for the candidate — it keeps whatever the gate already decided (the mechanical `#4899` `DISCARD` included); the failure is counted in `result["vet"][*]["batch"]["seam"]` (`unavailable`, `degraded`) | **no verdict**, and the **failing question is named** in `batch["seam"]["degraded"][item]` / `["schema_rejects"]` |
| **S2.3** | no assignment → the caller's **kNN top-1** fallback (the design's named mechanical fast path), counted in `stats["seam"]["unavailable"]` | same; the kind is never minted, `stats["seam"]["rejected"]` names the question |

One line of code: a failed chunk produces **no verdict** / no assignment — never a
label. One line of doc: the table above. **A Jev outage can never become a discard**,
and a rejected answer can never become a kind.

**Why "no verdict" rather than a `NOOP` verdict — this was wrong once, and review
caught it.** `vet_gate.vet_candidates` applies arbiter verdicts *over* the
mechanical pass (`decisions[iid].update(verdict)`), so a degraded `NOOP` **rescinds
the `#4899` identifier-only `DISCARD`**: an outage would silently keep what a
working predicate had dropped, and erase the rule id that explains it. Omitting the
verdict leaves every candidate exactly where the gate put it, and the failure is
recorded once, by name. The composition is pinned by an assertion in
the fail-closed test (armed seam + a transport raising `Unavailable` + an
identifier-only candidate ⇒ still `DISCARD` / `value.identifier_only.hash`).

## 6. Arming it

```
TORTOISE_S2_DECISION_SEAM=1   # unset/0 = off (the production state)
JEV_API_KEY=...               # $JEV_API_KEY, else the repo-root .env
```

Off is the default because the seam puts a **network call and a spend on the write
path** — the deployed pipeline keeps exactly today's behaviour until an operator
arms it:

- VET off → the mechanical identifier-only predicate only, every candidate kept.
- classify tail → the caller's hardcoded prompt, or the kNN fallback when no model
  is injected.
- **Arming the flag arms S2.2 unconditionally** — `_vet_enabled()` gains
  `TORTOISE_S2_DECISION_SEAM` as a second contributor, so the flag **overrides an
explicit `TORTOISE_VET=0`**. That is deliberate: the seam *is* the arbiter, and an
  arbiter that runs while the gate is off is an inert enforcer.
- **It does not by itself arm S2.3.** The tail decider only exists where a
  `KindClassifier` is constructed — i.e. under `TORTOISE_CLASSIFY_LATER=1` or an
  injected classifier. There is **no classify-only arming**; both halves are one
  flag, and each half's record names which one answered.

## 7. Cost per call — measured

**No live call was made for this record: there is no `JEV_API_KEY` on the machine
that wrote it.** What *is* measured is the request this seam actually builds, and
the price per input token, which is derived twice and agrees with itself.

| shape | request (measured, this module) | tokens (cl100k proxy) | cost |
|---|---|---|---|
| `vet-v1`, **40 items** / 80 questions, 500-word narrative | 39,484 B | 9,265 | **$0.0039** → **$0.000097/item** |
| `classify-v1`, **40 tail items** / 40 questions, top-5 kinds offered | 23,292 B | 6,112 | **$0.0026** → **$0.000064/item** |

Derivation, so it can be re-checked rather than believed:

- **The price.** The vendor's documented example bills **$0.000026 for 62 input
  tokens** ⇒ `4.19e-7 $/token`. Independently, a 100-row usage ledger retained from
  this machine's 2026-09-24 consolidation pass fits `cost = 4.20e-7 · input_tokens`
  with a **maximum absolute error of $0.000001** across all 100 rows (output tokens
  contribute ≈ 0 — the fit's coefficient is `-2.6e-10`, i.e. within noise). Both
  agree at **≈ $0.42 per 1M input tokens**, input-only.
- **The tokens.** `cl100k_base` over the exact serialized payload. The upstream
  tokenizer is not published, so read the token counts as **±15%**, and the
  dollars as an estimate of the same order — not a receipt. The first real call is
  what produces a receipt, and the seam records it on **both** halves:
  `usage.cost_usd` accumulates into the VET record at
  `result["vet"][*]["batch"]["seam"]["cost_usd"]` (with `calls`, `bands`,
  `prompt`, `unavailable`, `degraded`) and into `stats["seam"]["cost_usd"]` on the
  classify stats. So the number becomes measured rather than derived the moment it
  is armed — on both halves, from the same artifact a reader already opens.
  ⚠️ It reaches `result["vet"]`, **not** `stats["llm"]`, so the A/B cost gate
  (`stats["llm"]["cost_usd"]`) does not add it to the LLM tail's spend: the two
  providers are counted separately, on purpose, and an armed run's true tail cost
  is their sum.
- **Two questions per item is not free.** The `score` question's four-level
  `criteria` is repeated **per item**; that is the dominant term in `vet-v1`'s
  request, and it is why this row costs more per item than the architecture doc's
  recorded `$0.000144/item at 1 question`.
- **A month, by arithmetic** (§4.2's own projection: 25k items, ~20% tail):
  classify's 5,000 tail items = 125 round trips ≈ **$0.32/month** — against the
  doc's `$0.72/month` estimate for the same tail, on the same order.

## 8. The two tests, and the mutants that redden them

`tests/test_s2_decision_seam_5087.py` — two tests, because the seam prevents one
defect class and a hundred shape-pinning assertions would not strengthen it.

| test | what it proves |
|---|---|
| `test_a_valid_typed_answer_produces_the_expected_verdict` | a real `noul`+`score` pair decides `KEEP`/`DISCARD` **through `vet_gate`**, and a real `choice` decides the kind **through `KindClassifier`**. The stub answers the kNN's **second** candidate, so the assertion is the model's answer and not the fast path's. |
| `test_an_out_of_schema_answer_fails_closed_and_names_the_question` | a wrong primitive, a score off the declared scale, and a choice outside the offered set all produce **no verdict** — and the record names the question. The seam rejects the out-of-set choice **before** the classifier's own closed-vocab guard sees it (`closed_vocab_rejects == 0`), which is the difference between a validating seam and one that trusts whatever comes back. It also holds the **composition floor**: an armed seam whose transport raises leaves the mechanical `#4899` `DISCARD` standing (§5). |

Mutation-checked (each restores byte-identically — verified with `diff`):

| mutant | red |
|---|---|
| `_noul` coerces `float(answer.get("noul") or 0.0)` | ✅ a `choice` answer becomes a confident **DISCARD** |
| `_score` drops its `0..hi` range check | ✅ `9.0` on a `0..3` scale licenses the discard |
| `_choice` returns `answer.get("choice")` unvalidated | ✅ a minted `core:invented` reaches the classifier |
| a degraded item emits a `NOOP` **verdict** instead of no verdict | ✅ the armed-seam composition assertion reads `KEEP`/`vet.fail_open` where `DISCARD`/`value.identifier_only.hash` is required |
| `vet_arbiter` drops its stats again (`"batch": {}`) | ✅ **both**: the record assertions find no `batch["seam"]` |
| the classify record loses its `prompt` | ✅ the prompt-version assertion reads `None` |
| `classify_tail` assigns the offered top-1 instead of the model's choice | ✅ **both**: the kind assertion reads `core:plan` instead of `core:workflow`, and the out-of-set answer stops being rejected because nothing is validated |

## 9. What this does NOT close

- **`#7949`'s capstone is not green because the seam exists.** The capstone's
  arms are gated runs; this removes one named blocker (*"the Jev seam does not
  exist"*, §4.3 G5) and nothing more.
- **The pre-gate `S2.2a-β` site** (near-duplicate detection before the gate,
  `#5087`'s other half) is untouched.
- **No production arm is armed by this change**, so no rate effect is measurable
  yet — arming it is an operator's act with its own measurement.
