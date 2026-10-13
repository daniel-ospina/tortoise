---
title: "4837 — the advice-shaped question battery: the class decision"
type: operations
domain: operations
doc_status: live
created: 2026-10-13
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# #4837 — the advice-shaped question battery: the class decision

**Verdict: REJECT — no reader-prompt change lands.**
The candidate #4107 sentence is **safe** (0 false commits across all 7 guard
controls, under every variant) but it is **not a class fix**: on a
pre-registered 6-item advice battery the shipped prompt already commits on
4, and the two items that abstain are fixed by the candidate. `C=2/6` on the
pre-registered metric, `C=4/6` under a corrected abstention label — both below
the pre-registered `LAND` bar of `C≥5`. The battery also surfaced a second,
independent defect in the product's own abstention label (filed as **#8406**).

**Instrument:** `docs/runbook/4837_advice_battery.py` + fixture
`docs/runbook/4837-advice-battery.json` (**SHA-pinned = the
pre-registration**). This is a *separate harness*, not the D3 instrument: the
D3 fixture is SHA-pinned and **a changed fixture is a new instrument** (#4064),
so the questions that decide a universal prompt-contract change are measured on
their own fixture against the same frozen reader pin.
**Receipt:** `docs/runbook/4837-advice-battery-receipt.json` ·
**Sensitivity:** `docs/runbook/4837-advice-battery-sensitivity.json`
**Measured tree:** `a91179c505f2779bcfd548c3ba6f0ed6ca99d533` (`origin/main`), 2026-10-13
**Reader pin:** `deepseek-direct` / `deepseek/deepseek-v4-flash`, temperature 0,
max_tokens 500 — `RoutingModel`, asserted by `ask_shape_rate.assert_reader_pin()`
and recorded in the receipt; the embedder is asserted too (#2985), so the dense
leg is live and this is not a keyword-only run. **39 paid completions**, none
escalated (`any_variant_escalated_output_budget: false` on all 13 items — every
variant ran the same call shape, so a variant difference is a prompt difference).

---

## 1. What #4107 left open, and what this decides

Issue #4107 characterised ONE advice-shaped L1 failure, `d6233ab6` ("…would it be a
good idea to attend my high school reunion?"): the answer-bearing turn is
rendered in the reader's context (16 verbatim gold words) and the pinned reader
abstains anyway, because the universal `_ABSTRACTION_FRAGMENT` holds a
**synthesis licence** and an **asked-subject scoping guard** in tension and the
model follows the guard. A candidate sentence flips that one sample
(`ADVICE_SENTENCE`, byte-identical here to the value in the #4107 diagnostic and
to the system-prompt hash in the #4107 receipt — asserted by
`tests/test_reader_4837_advice_battery.py`). #4107 deliberately did not land it:
**one sample is not a class**, and the candidate is a universal append.

This battery decides the class question. **Pre-registered rule** (frozen in the
harness before the run, with the fixture SHA pinned so the questions could not
move after the answers were seen):

| arm | n | premise | expected |
|---|---|---|---|
| `class` | 6 | advice-shaped question about the user's life; the asked **event** is absent, the user's relevant **experiences** are present | abstain before, commit after |
| `near_miss` | 4 | advice-shaped; the asked subject is absent **and** no relevant experience / preference / interest exists anywhere in the context | abstain **before and after** |
| `d3_abs` | 3 | the frozen D3 fixture's three `_abs` questions, **read** from the fixture (never copied) | abstain before and after |

`C` = class items where V1 abstains and V2 commits **and the commit is grounded**
(≥4 contiguous verbatim words shared with the evidence — the D3 instrument's own
L3 lexical floor). `G2`/`G3` = guard items (near_miss + d3_abs) where the variant
does **not** abstain. **LAND** iff `C≥5 and G2==0`; **NARROW** iff `C≥5 and G2>0`
and the gated variant is clean; **VOID** if >1 class item fails its own presence
precondition (the denominator would have moved).

Variants, replayed on each item's ONE frozen rendered evidence:

* **V1 `v1_shipped`** — `system_prompt_for(resolved question_type)`, captured
  from the lane's own wire and asserted equal to the re-derivation (the emitted
  prompt is evidence, not a parallel computation);
* **V2 `v2_universal_advice`** — V1 + the #4107 `ADVICE_SENTENCE`;
* **V3 `v3_gated_fragment`** — a **detector-gated bidirectional** advice fragment
  (the "narrow" candidate): emitted only where `is_advice_shaped()` fires, and it
  names **both** directions (commit when the context states the relevant
  material; abstain when it does not). The gate fires on all 10 battery items and
  on **none** of the three D3 `_abs` questions, so V3 is a genuinely narrower
  contract than V2 rather than the same append behind a flag.

## 2. Results

Arm table (V1 → V2 → V3), `**abstain**` = the product's label, `ABSTAIN*` = a
substantive Phase-2 refusal the product label **missed** (§3), `commit (Nw)` =
committed with an N-word verbatim span shared with the evidence:

| item | arm | V1 shipped | V2 + advice sentence | V3 gated bidirectional |
|---|---|---|---|---|
| `c1_college` | class | **abstain** (4w) | commit (8w) | commit (9w) |
| `c2_culinary` | class | ABSTAIN* (4w) | commit (8w) | commit (8w) |
| `c3_piano` | class | **abstain** (0w) | commit (6w) | commit (4w) |
| `c4_dog` | class | ABSTAIN* (5w) | commit (9w) | commit (0w) |
| `c5_solo_trip` | class | commit (11w) | commit (11w) | commit (8w) |
| `c6_novel` | class | commit (5w) | commit (9w) | commit (8w) |
| `n1_triathlon` | near_miss | **abstain** | **abstain** | **abstain** |
| `n2_sabbatical` | near_miss | **abstain** | **abstain** | **abstain** |
| `n3_sailing` | near_miss | **abstain** | **abstain** | **abstain** |
| `n4_veg_garden` | near_miss | **abstain** | **abstain** | **abstain** |
| `ba358f49_abs` | d3_abs | **abstain** | **abstain** | **abstain** |
| `f4f1d8a4_abs` | d3_abs | **abstain** | **abstain** | **abstain** |
| `gpt4_93159ced_abs` | d3_abs | **abstain** | **abstain** | **abstain** |

* **Class, product label: `C=2/6`** (`c1_college`, `c3_piano`). The #4107
  mechanism reproduces on 2 of 6 items **and on no others**: on `c5`/`c6` the
  shipped reader already answers from the memories without consulting the asked
  event, so there is nothing for the candidate to fix. This is the measurement
  #4107 could not make from one sample.
* **Class, corrected label: `C=4/6`.** `c2_culinary` and `c4_dog` answer with a
  verbatim Phase-2 refusal — *"The context doesn't say whether you'd like to
  enrol in a culinary course. It does show you love cooking on weekends…"* — and
  the product's `abstained` field scores both as **commits** (§3).
* **Guard: `G1=G2=G3=0/7`.** All four near-miss controls and all three `_abs`
  questions abstain under **every** variant, in flat-refusal wording
  (*"I don't know — the memory context doesn't mention any triathlon, endurance
  training, or related fitness interests."*). The advice sentence does not break
  the false-commit guard — **the risk #4837 said was real and unmeasured is
  measured, and it did not materialise.**
* **Grounding.** Every V2 commitment on the class arm shares 6–11 verbatim
  words with the evidence. `c4_dog`'s V3 answer commits but shares 0 words
  (it paraphrases), so it does not count as an N3 flip — the gated variant is
  *weaker* on grounding, not just narrower.
* **D3 abstain leg.** The three `_abs` questions abstain in all three variants,
  so nothing here regresses the D3 abstain leg.

## 3. Sensitivity: the label this battery caught — and why the verdict survives it

Two class items answer with the prompt's own Phase-2 template executed with a
refusal **verb**, and `_looks_abstained` has no verb family. Measured on this
tree, deterministically:

| answer | `_looks_abstained` | should be |
|---|---|---|
| `"The context doesn't say whether you'd like to enrol in a culinary course. It does show you love cooking on weekends."` | **False** | True |
| `"The context doesn't say whether you should adopt a rescue dog next month. It does mention you grew up with dogs."` | **False** | True |
| `"The context does not state whether you enjoy this."` | **False** | True |
| `"The memory says nothing about a reunion."` | **False** | True |
| `"…though I do not know if it changed."` (#2027 committed hedge) | False | False ✓ |

This is a **product** field, not just sugar: `ask_lane.run_ask_lane` returns
`abstained` from `_looks_abstained`, and the D3 instrument's L1 leg reads that
field — so a genuine refusal in this wording scores **L1 GREEN** where it should
be RED. Filed as **#8406** (no committed `ask-shape-rate-*.json` receipt
currently carries the form, so the gap is latent rather than already-bitten).

⚠️ **The verdict is not an artefact of that label.** `--relabel` re-applies the
**same pre-registered rule** to the committed receipt with the refusal verbs
recognised (it does not re-run the reader and does not touch the recorded
decision):

| metric | C (V2) | N3 (V3) | G1 | G2 | G3 | verdict |
|---|---|---|---|---|---|---|
| product label (**pre-registered**) | 2 | 2 | 0 | 0 | 0 | **REJECT** |
| refusal-verb label (`--relabel`) | 4 | 3 | 0 | 0 | 0 | **REJECT** |

Two independent readings, both below the pre-registered `C≥5`, and the guard is
clean in both.

## 4. Why REJECT, and what would change it

The candidate is **safe** and **effective on every item that actually abstains**
(4/4 under the corrected label). It is rejected at the bar this issue
pre-registered, for three reasons that the measurement — not taste — supplies:

1. **The class effect is a split, not a class.** 2 of 6 items reproduce the
   #4107 abstention on the product label (4 of 6 under the corrected label); the
   other items commit already. A **universal** prompt-contract append was
   justified by the assumption that the advice class systematically abstains.
   That assumption is not supported: `d6233ab6` is the *tail* of the class, not
   its representative.
2. **The change is a universal append on a reader with a documented layer
   history.** `tortoise/reader.py` records the `#1366 → #1546 → #1762 → #1775 →
   #2027` oscillation; the issue itself names "a universal append repeats that
   pattern of layers". Paying that cost for a minority of the class — on an
   eval-only reader (#3849), so a benchmark movement rather than a user outcome —
   is the easy path, not the good one.
3. **The gated alternative is weaker, not safer.** V3 (detector-gated,
   bidirectional) does not out-perform V2 where it matters: `N3=2`/`3` vs `C=2`/`4`,
   and on `c4_dog` it commits *without* any verbatim grounding. So `NARROW` is
   not available as a measured fallback either — the pre-registered rule's
   `NARROW` branch requires `N3≥5`, and it is not close.

**Rejected alternatives, recorded:**

| alternative | why not |
|---|---|
| Land V2 universally (the #4107 candidate) | `C=2/6` (4/6 corrected) — below the pre-registered bar, on a universal append |
| Land V3 (detector-gated bidirectional fragment) | `N3=2/6` (3/6 corrected) and it loses grounding on one item — worse than V2, not narrower-but-equal |
| Route advice questions to `_PREFERENCE_FRAGMENT` | already refuted by #4107 §5.1 (same abstention), re-confirmed by the fragment never being emitted (`detect_question_type` returns `None` for all 10 battery items) |
| Extend `detect_question_type` with the #2009 advice cues | the #2009 cross-tab measured `+0.050` mapped agreement and was not added; this battery adds no reason — the gate would not change ANY of the 13 outcomes' prompt on the `_abs` arm and would not fix the 4 items that already commit |
| Treat the label gap as the cause and re-decide | the sensitivity table above shows the verdict is unchanged under the corrected label |

**What would justify a future landing** (recorded so the measurement is
reusable): a battery at realistic context scale (the #4837 arm items render a
4-session context; the D3 contexts render ~90 kB over 26–48 sessions), larger
`N`, with the refusal-verb label in place (#8406), showing `C≥5` with the guard
still clean. The `C=4/6` corrected reading means a re-run could reach the bar
without the mechanism changing — so the honest statement is **"not justified at
the pre-registered bar on this battery"**, not "no effect exists".

## 5. Limitations, stated

* **Context scale.** Each battery item's haystack is 4 short sessions, all
  rendered (asserted: `haystack_all_sessions_rendered: true`, and the class
  items' `relevant_needle` present in the evidence). The D3 contexts are ~90 kB.
  Scale is the main threat to the `C=2/6` reading: a large noisy context may make
  the asked-event absence more salient and raise the abstention rate. Not
  measured here — `c1`/`c3` abstain in the *clean* context too, which is why the
  mechanism is confirmed rather than assumed, but the *rate* is context-scale
  sensitive and this battery does not bound it.
* **`N=6`.** The pre-registered bar was 5/6; the study has no power to resolve
  4-vs-5-of-6. That is the reason the verdict is recorded as "not justified at
  the bar" with the corrected reading beside it, rather than as a refutation of
  the mechanism.
* **Fixture authorship.** The battery's questions are hand-written for this
  issue (recorded in the fixture's `authorship` field) — authored so that each
  arm's premise is a structural, machine-checked property (the asked event is
  never named in the haystack; the class arm's needle is a USER turn). They are
  not sampled from a corpus, so they bound the *mechanism*, and the D3 `_abs` arm
  plus #4107's `d6233ab6` are what tie it to the production fixture.
* **The reader's served model.** The provider answers 200 for
  `deepseek-v4-flash` and serves `deepseek-flash` (the #4129 substitution
  warning fires). That is the pinned model id the D3 instrument and #4107 both
  used, so the comparison is like-for-like, but the served model is not literally
  the requested id.

## 6. Second defect filed

**#8406** — `_looks_abstained` has no Phase-2 refusal **verbs**, so
"doesn't say whether X" scores as a COMMIT and the D3 instrument's L1 leg is
blind to it. Filed with the four measured forms, the #2027 hedge that must NOT
be swallowed, and the note that fixing it moves L1 labels and therefore needs
its own before/after.

## 7. Reproduce

```bash
# deterministic + free: per-item evidence, emitted prompt, presence preconditions
uv run python docs/runbook/4837_advice_battery.py --out /tmp/4837-free.json

# PAID: the variant comparison (39 pinned-reader completions)
uv run python docs/runbook/4837_advice_battery.py --replay \
    --out docs/runbook/4837-advice-battery-receipt.json

# sensitivity: re-apply the SAME pre-registered rule with the refusal-verb label
uv run python docs/runbook/4837_advice_battery.py --relabel \
    docs/runbook/4837-advice-battery-receipt.json

# hermetic pins (pre-registration, arm premises, prompt hashes, decision rule)
uv run pytest tests/test_reader_4837_advice_battery.py -q
```

The `--replay` run requires the embeddings extra (`uv sync --extra embeddings
--extra parity`) and a `DEEPSEEK_API_KEY`; without the key the run **fails
closed** at `assert_reader_pin` (#1530) rather than measuring a different reader.

## 8. References

* #4107 — the characterisation this battery decides · `docs/runbook/4107-preference-abstention.md`
* #4837 — this issue (battery + class decision)
* #8406 — the `_looks_abstained` refusal-verb gap (filed from §3)
* `tools/ask_shape_rate.py` — the D3 answer-shape instrument (the ruler)
* `tests/test_reader_4837_advice_battery.py` — hermetic drift alarm for this instrument
