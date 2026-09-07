<!-- research-path: docs/epics/1402-eval-battery/02-research-brief.md + #2292 issue body & scoping v5.1 (authoritative) + #2284 merged plan docs/plans/2026-09-05-2284-battery-measurement-path.md (Tasks 0-7 MERGED via PR #2341) -->

# #2292 — Rubric/model/budget feasibility for the real run: arm-neutral R2 rubric + judge-validation record, model pin, measured-cost + determinism re-scope (sibling of #2284)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Resolve the battery's rubric/model/budget feasibility decisions for the real run — author + validate the arm-neutral R2 coverage rubric (the only gated-judge field in R1–R5), pin the model under test, and re-baseline cost/determinism constants from measured data with [cal]-discipline re-locks — so #2284 exposure part 1 (Task 8) consumes closed deliverables (rubric + ValidationRecord + pin + provisional budget + hashed machinery) and judge-gated scoring + real-run economics are honest.

**Team:** epistemic-team
**Architecture:** Four coupled feasibility decisions resolved in dependency order on measured data, riding the #2284 Phase-1 machinery (MERGED) rather than inventing parallel seams:
1. **R2 rubric + declarative seam** (D1): the spec's graph-only anchors ("mitigations filed per support edge") are replaced by an **arm-neutral itemized rubric JSON** — decomposed anchored yes/no items judged on tool-stripped evidence renders (envelope scalars only; NO tool names / edge counts / arm identity). The existing pairwise `better/worse/tie` judge/gate vocabulary (`battery/judge/client.py build_abba_prompts` + `gate.py` stress/IRT binarization) is structurally inapplicable to a single-construct anchored yes/no judge — the gate's verdict vocabulary + stress items + AB+BA prompt shape get a **declarative parameterization seam** (yes=1/no=0, same κ/IRT math, exact-binomial AB+BA at small n), with the Cohen's-κ inter-judge leg RETAINED unchanged (vocabulary-agnostic). Rubric file form (decision (d)): **itemized rubric JSON + canonical rendered judge prompt** — recommendation recorded, non-blocking.
2. **Validation run PRE-exposure** (D2): ONE validation run over REAL deliberation text produced by a **#2292-owned minimal real-model probe** (3–5 scenarios, candidate-pinned model temp 0, budget-guarded under the existing dollar cap, judge spend metered) → persisted ValidationRecord by rubric id (AB+BA p<0.05, κ≥0.70, IRT infit [0.7,1.3], stress, gold anchors over per-ANCHOR item-judgment units). Scoring stays blocked (JudgeGateBlocked) until the record exists. **The probe IS the pre-exposure validation run #2284 Task 8's precondition references** (coordination n7) and simultaneously measures per-phase + judge-leg tokens.
3. **Model pin** (D3): arms.yaml `model_pin`/`temperature` (schema landed in #2284 Task 6 — MERGED) re-locked to a concrete measured-adequate pin: **candidate `deepseek/deepseek-v4-flash` UNCAPPED temp 0** (repo MODELS registry `max_tokens=None`; decision (a) — plan-time owner recommendation, recorded non-blocking), same model across arms, provider recorded in the artifact `model` block; real-run pre-flight refuses an unpinned run AND the class-level `model_id="fixed"` sentinel.
4. **Measured budget + cal re-locks** (D4/D5/D6/D7): probe-measured per-phase tokens (95th-pct + judge-leg accounting) → provisional arms.yaml `expected_tokens_per_episode` re-lock **through a reviewable hashed change** (never silent); thresholds.yaml re-locks (R1 post-I-1 rows, flip-flop-rate + fp-rate rows added, R2 coverage 1.43×/1.5× reconciled to ONE canonical gate form ratio ≥1.5× WITH a0=0 floor, coverage metric KEY canonicalized, determinism measured re-lock over the #2284 Task-7 seed + hash round-trip — merge-order conditional documented, Task 7 MERGED so the seed is present; the real-path model-text/judged tolerance FINALIZATION is reserved for #2284 Task 8 exposure-measured numbers per merged 04-plan E2E-7.1 — this issue re-locks only what the seed + probe legitimately support, provisional rows under a thresholds.yaml once-per-phase pin); parity methodology hash becomes an end-to-end triple with the `report.py` producer emitting `protocol_hash` (additive + back-compat, locked migration test, #1144 cross-dependency recorded); 04-plan/spec/05-decompose doc amendments.

**Scope boundary (owner-checked, from scoping Amend-3 §1 + notes):** the Amendment-2 write/surfacing-hop rubric ITEM authoring ships in Task 1, but the **surfacing-leg operational definition is an OWNER-CHECKED checkpoint** — the plan presents the proposed operational definition and implementation of that leg's scoring semantics PAUSES for an explicit owner check before its code lands (Task 1 Step 1.7). Model-pin decision (a) and rubric-file-form decision (d) are plan-time owner recommendations, recorded WITH recommendations in this plan and in the issue comment — non-blocking.

### Pattern Research

> **Findings date:** 2026-09-06
> Gate skipped: plan touches zero NEW third-party dependencies — real model calls ride the in-repo `tortoise/model_adapters.py` MODELS registry (`OpenRouterModel` UNCAPPED `max_tokens=None`, temp 0, per-call usage capture `last_*` + #2185 usage-sink seam); the judge rides the in-repo `tools/longmem_eval/judge.py` model conventions — bare OpenRouter slug `openai/gpt-4o-2024-08-06`, temp 0; the `openrouter:` provider-prefix convention + `TORTOISE_LME_JUDGE_MODEL` official default live ONLY in `tools/longmem_eval/judge.py` — through `battery/judge/client.py` JudgeClient, whose `_real_call` posts `_model_id` VERBATIM to OpenRouter (no prefix convention) and which has NO model default: absent `BATTERY_JUDGE_MODEL`/`LLM_MODEL` ⇒ mock by design (verified in client.py), so every real judge run must SET the env to the bare slug; embedded FalkorDBLite in-repo. PRIOR_RESEARCH: #2292 issue-scoping v5.1 Phase 1.5 `### Axis Research` (rubric reliability: task/tool rubrics low-reliability arXiv 2606.29920 → arm-neutral anchored-yes decomposition; binary criteria strongest judge agreement — Autorubric arXiv 2603.00077; judge adequacy is NEVER assumed — κ gate is the proof seam; budget: dollar hard-stop not output caps — gbrain `CAT35_HARD_STOP_USD=40` precedent, #2134/#1509 truncation confound → UNCAPPED model output; determinism: temp-0 ≠ bit-deterministic arXiv 2606.26185/2602.14349 — deduplicated against #2284 scoping) + `### Integration Docs` (all in-repo surfaces verified against code this session: judge/client.py + gate.py, RubricRegistry, arms.py ArmConfig with model_pin/temperature, thresholds.py cal_table_hash + determinism fold-in, probe_scorer expected-coverage seam, parity runner 3-tuple protocol hash, report.py baseline producer at 2256 + `_match` at 2774, model_adapters MODELS registry) + the #2284 plan-review changelogs (rubric/judge/token ownership split already negotiated across Tasks 5/8/9 — this plan consumes those seams, never forks them).

### Integration Surface Map

Derived from test-design #1404 (S3/S5/S6/S8) + scoping wiring; surfaces touched by THIS plan (surfaces owned by #2284 Task 8/9 or sibling A/B are listed as intake boundaries, not re-built):

| Surface | Boundary | Test layer | Where | Bug-pattern flags |
|---|---|---|---|---|
| S6 LLM-as-judge rubric surface | battery/judge (client/gate) + rubrics files | unit (seam) + integration (real validation run) | Tasks 1-2 `tests/test_battery_rubric_authoring.py` + `test_battery_judge_declarative.py`; Task 4 | pairwise-vocab judge on anchored-yes items = structurally inapplicable (silent wrong leg); unvalidated rubric scores = JudgeGateBlocked (fail-closed, keep) |
| S6 evidence surface (rubric item judging) | tool-stripped evidence renders from deliberation text | unit neutrality lint + integration | Task 1 lint + Task 3 probe bundle | tool names / edge counts / arm id in evidence = verb-availability artifact + arm-identity leak |
| S3 agent LLM runtime (model under test) | model_adapters MODELS registry → probe driver / real-mode caller bridge | unit pin resolution + integration (real, budget-guarded, temp 0, UNCAPPED) | Tasks 3/5 `tests/test_battery_model_pin.py` | unpinned run = unreproducible; silent fallback to a default model; class-level "fixed" sentinel |
| S8 harness config (arms.yaml tokens/pin, thresholds.yaml, budget.yaml) | config loaders + provenance hash | unit | Tasks 5-7 `tests/test_battery_token_relock.py` + `test_battery_thresholds_relock.py` | cost constants outside the reviewable hash = silent budget re-lock; test-local constants (forbidden) |
| S5 parity methodology hash | battery/parity runner (3-tuple, MERGED) + tools/longmem_eval/report.py producer | unit migration + locked emission | Task 8 `tests/test_battery_parity_migration.py` | protocol change invisible to parity (2-tuple hole — closed in #2284 Task 6; producer must emit or the real unchanged-check never sees protocol deltas) |
| Judge spend metering | JudgeClient real call + probe accumulator | unit + integration | Task 3 | unmetered judge spend = budget lie (judge leg crowds model spend) |

### Verification Plan

test-routing (domain-aware; carve-out lane `TORTOISE_TEST_CARVE_OUT=1` for embedded-only surfaces): code domain, complexity standard → unit + integration; the real-model probe + validation run are **network-gated integration** (marked `@pytest.mark.slow` + env-key guard, budget-guarded, spend pre-authorized within the existing `budget.yaml` dollar cap under a probe sub-cap) — hermetic fallbacks (mock judge + recorded evidence fixtures) keep CI green with zero model spend; UX RATING = low, zero UI files → no UX checks; config domain rows verified via config tests (thresholds/arms re-locks assert hash round-trips + gate-form consistency — no test-local constants); doc amendments verified by stale-grep sweeps. Sibling B (#2284 Task 8 cycle-3 #17) owns any judge-spend reserve RESIZING beyond this plan's default reserve line — recorded here, not invented (the default `judge_leg_reserve_usd` line + its `--evidence`-path hard stop ship in Tasks 3/4).

### UX Design Decisions

| # | Decision Type | User Choice | Rationale |
|---|---|---|---|
| — | UX gate skipped | n/a | Zero UI files; pure judge/config/provenance/measurement-path work. |

### Plan-time owner decisions (recorded WITH recommendations — non-blocking)

| # | Decision (scoping) | Recommendation | Recorded |
|---|---|---|---|
| (a) | Concrete pinned model id | `deepseek/deepseek-v4-flash` UNCAPPED (`model_adapters` registry `deepseek-flash` → `OpenRouterModel('deepseek/deepseek-v4-flash', max_tokens=None, temperature=0.0)`), same model across arms, temp 0, provider `openrouter` recorded in the artifact `model` block; a thinking-capable variant only if the 3–5× headroom is sized for it (probe measures). | Task 5 |
| (d) | Rubric file form | **Itemized rubric JSON** (machine-parseable per-anchor items: id, arm-neutral phrasing, decision rule) + **canonical rendered judge prompt** (stable derived string — the `rubric_text` the gate checksums); loader extended JSON-first with `.md` fallback; existing `battery validate-judge` CLI kept. | Task 1 |
| (b) | Surfacing-leg operational definition (Amend 3 §1) | **OWNER-CHECKED CHECKPOINT** — see "Scope boundary" above and Task 1 Step 1.7; proposed definition presented in this plan, code paused on owner check. | Task 1 |
| (c) | Judge-spend metering | Metered against the SAME probe dollar sub-cap with a separate accumulator line + a `judge_leg_reserve_usd` reserve line (default $1.50) HARD-ENFORCED in Task 4's `--evidence` path (judge spend never silently crowds out model-under-test spend); reserve RESIZING beyond the default stays sibling-B-owned (#2284 Task 8 cycle-3 #17) — recorded, referenced, not invented. | Task 3/4 |
| (e) | R2 gate canonical form | Ratio ≥ 1.5× (AC-R2 wording, load-bearing for STRONG) WITH the a0=0 floor (control == 0 ⇒ gate passes iff treatment > 0); the config expectation assert and the runtime gate agree at the boundary; cal row re-locked consistent — one form, no tolerated 1.43×/1.5× mismatch. | Task 7 |

### Execution notes (lanes + sequencing + coordination pins)

**10 tasks (Task 0..Task 9) > 8 → parallel-session execution handoff** (writing-plans rule: new session pastes the executing-plans prompt). Hard edges: T1 → T2 (spec shape feeds the seam) → T3 (seam + rubric needed by the probe) → T4 (validation consumes probe bundle) → T5 (pin pre-flight) → T6 (measured re-lock consumes probe token tables) → T7 (consumes measured rows) → T8 (independent of T3–T7 — parity migration can land first) → T9 (doc amendments LAST, after rows settle). Spend-gated steps (Task 3 Step 3.7 + Task 4 Step 4.2 + Task 7 Step 7.4's two-run determinism probe) are pre-authorized within the existing dollar cap under the probe sub-cap (one shared accumulator; judge spend additionally capped by the `judge_leg_reserve_usd` reserve line inside it) — recorded, never silently re-run. Zero model spend until Task 3. Merge-order pins (coordination notes): **n1** arms.yaml double-edit hazard — #2292 owns FORMULA + hashed machinery + PROVISIONAL values (Task 6); #2284 Task 8 finalizes over the SAME machinery; arms.yaml is edited once per phase, never in parallel (single commit on this branch; handoff note tells Task 8 to "finalize", not "re-lock"). **n2** determinism block: #2284 Task 7 MERGED the seed + hash fold-in (verified on origin/main this session) → this plan performs the measured re-lock OVER the seed + asserts the hash round-trip; the merge-order conditional (Task 7 unmerged ⇒ do block+hash+re-lock in one reviewable change) is documented in Task 7 for the record. **n3** report.py producer edit is additive/back-compatible (Task 8) — cross-note #1144 (file owner) via a comment; never edit the issue body. **n4** issue-body indicator-4 wording is unsatisfiable as written — plan + a comment recommend the wording change to the owner (comment only — do NOT edit the issue body). **n5** #2080 W2 reuse boundary — hop rubric files reuse #2080 W2 mechanics with battery QA on top; fork nothing (Task 1 note). **n6** R2 1.43× vs 1.5× reconciliation is deliverable 5 (Task 7). **n7** DEPENDS ON #2284 Task 6 (MERGED — schema present; verified: arms.yaml carries `model_pin`/`temperature` + `ArmConfig` parses them; the parity protocol leg + placeholder-pin honesty are live) — no inline schema edit needed; the probe IS the pre-exposure validation run Task 8 references. **n8** κ leg retained vocabulary-agnostic, canonical coverage metric key at the loader (Task 7) — in the REAL run the κ pass measures TWO real judge configs (Task 4), never one model at temp 0 (κ≡1.0 by construction is not reliability); the declarative AB+BA slot is the judge RETEST-consistency leg (identical renders = byte-identical prompts — position bias is unmeasurable in the single-construct vocabulary; round-2 relabel), and the gold-anchor block carries ≥ 1 expected-`no` render (no-share > 20%) so a degenerate all-yes judge is caught by the anchor leg, never masked by a self-consistent judge. **n9** thresholds.yaml concurrent-edit hazard — #2291 owns the [cal] `ep-variance` row (its calibration re-lock; referenced in provenance here, NEVER invented or edited by this issue — Task 7 Step 7.3) and #2284 Task 8 finalizes measured rows: #2292 Task 7 lands seed/mechanics + PROVISIONAL rows only; thresholds.yaml is edited once per phase — no parallel rows across #2291/#2284 Task 8/#2292 (04-plan E2E-7.1 coordination; the Task 9 handoff tells #2284 Task 8 to "finalize", never "re-lock" in parallel). **n10** run.py co-edit hazard — #2284 Task 9 (executor v1, gated) owns the real-model runner plumbing/run_mode/emission seam in run.py; Task 5's pin-pre-flight edit lands FIRST, additive INSIDE the existing real-executor pre-flight gate block (PR #2341 rounds 2+3, verified present), and #2284 Task 9 merges later over the same block consuming the pinned values ("sibling B pin" — its acceptance) — no parallel silent edits to the pre-flight region; Task 5 posts the cross-note on #2284. Execution mode: subagent-driven per task on this branch; ONE PR + full code-review at the end.

---
### Task 0: Baseline verification (worktree already created)

**Intent:** Edit base off origin/main tip (which INCLUDES merged PR #2341 — #2284 Phase-1 Tasks 0–7) with a green battery carve-out suite so RED flips in Tasks 1/2/5/7/8 are attributable to the change.
**Acceptance:** worktree `feat/2292-rubric-model-budget` exists (given), base verified, battery carve-out suite green; the #2284 Task-6 schema (arms.yaml `model_pin`/`temperature` + `ArmConfig`) and Task-7 tolerance seed are CONFIRMED present (they are — verified at plan time on origin/main).
**Files:** none (environment)

**Step 0.1** — `git merge-base HEAD origin/main` → plan-commit base; plan-doc commit rides the branch tip into the PR.
**Step 0.2** — Baseline: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_judge.py tests/test_battery_config.py tests/test_battery_determinism.py tests/test_battery_parity_hash.py tests/test_battery_schema_v11.py tests/test_battery_report_writers.py tests/test_battery_probes.py -q` → PASS.
**Step 0.3** — Grep-confirm the merged seam surface: `grep -n model_pin battery/config/arms.yaml` → **9 hits** (7 arm rows + 2 header-comment mentions — expect exactly 9; drift = a schema/convention change), `grep -n tolerances battery/config/thresholds.yaml`, `grep -n "def protocol_hash" battery/parity/runner.py`, `grep -rn "protocol_hash" tools/longmem_eval/report.py` → **EMPTY** (the producer emission is this plan's Task 8).

### Task 1: R2 arm-neutral itemized rubric JSON + neutrality lint + hop rubric files + loader extension (decision (d); surfacing-leg checkpoint (b))

**Intent:** The R2 rubric is un-authorable-as-written: the spec anchors reward graph-only behaviors (edge counts, tool names, arm identity) — the R2 delta would be a verb-availability artifact AND the task/tool-rubric shape scores low judge reliability (arXiv 2606.29920). Author the rubric as **arm-neutral decomposed anchored yes/no items** (Autorubric binary-criteria evidence, arXiv 2603.00077) judged on tool-stripped evidence renders (envelope scalars only), delivered as **itemized rubric JSON + canonical rendered judge prompt** (decision (d) recommendation), with a test-locked neutrality lint. Also author the Amendment-2 hop-item rubric files (#2080 W2 mechanics reuse, never fork — n5); the surfacing-leg operational definition is presented and CHECKPOINTED for an explicit owner check (b).
**Acceptance:** `battery/config/rubrics/r2-coverage.json` exists — itemized anchored yes/no items (each: `id`, arm-neutral `text`, `decision_rule`) whose phrasing cannot reference tools/edges/arm identity; item count ≥ 4 and each item maps to a coverage construct (counter-arguments considered, what-could-be-wrong specificity, support-vs-oppose weighing, revision after counter-evidence — per spec R2 + scoping, minus graph-only anchors); a gold-anchor block (≥ 3 hand-authored evidence renders + expected yes/no decided from scenario semantics — the supplementary gold-anchor agreement leg's fixture — **including ≥ 1 expected-`no` render with a no-share > 20% of the block, so a degenerate all-yes judge scores < the 0.8 agreement bar and is caught (round-2, n8)**); `battery/judge/rubric.py` ships `RubricSpec` + `load_rubric_spec(config_dir, rubric_id)` (JSON-first, `.md` fallback — existing `<id>.md` rubrics keep loading) + `render_rubric_prompt(spec)` (canonical stable string; sorted items) + the **neutrality lint** `lint_evidence_neutral(evidence_text)` + `lint_rubric_items(items)` rejecting tool names / edge counts / arm ids in ITEM phrasing and EVIDENCE renders (banned-token list: product verb names from `emit._SUBTYPE_OK["tool_event"]` + "a0/a1/a2/a4/control/graph arm" identity tokens + numeric edge-count patterns); `_load_rubric_text` (cli.py:140) extended JSON-first via the loader (existing `validate-judge` CLI kept). Amendment-2 hop files: `battery/config/rubrics/am2-write-hop.json` authored (itemized; #2080 W2 mechanics reference — cross-note, no fork); surfacing-leg operational definition RECORDED in the plan section below + the file carries a `status: owner-check-pending` marker.
**Files:**
- Create: `battery/config/rubrics/r2-coverage.json`, `battery/config/rubrics/am2-write-hop.json`, `battery/judge/rubric.py`, `tests/test_battery_rubric_authoring.py`
- Modify: `battery/cli.py` (`_load_rubric_text` → rubric.py loader)

**Step 1.1** — Write the failing tests:
```python
# tests/test_battery_rubric_authoring.py
"""#2292 Task 1 — arm-neutral itemized rubric JSON + loader + neutrality lint."""
from __future__ import annotations
import json, pytest
from pathlib import Path
from battery.judge.rubric import (load_rubric_spec, render_rubric_prompt,
                                   lint_evidence_neutral, lint_rubric_items)

CONFIG = Path(__file__).resolve().parents[1] / "battery/config"

def test_r2_rubric_exists_and_itemized():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    assert len(spec.items) >= 4
    for it in spec.items:
        assert it["id"] and it["text"] and it["decision_rule"]
        assert it["decision_rule"] in ("yes", "no")  # anchored yes/no, binary

def test_r2_items_are_arm_neutral():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    for it in spec.items:
        lint_rubric_items([it])   # raises on banned tokens (verb names, arm ids)
    # banned-token lint covers graph-only phrasing MECHANICALLY (verb names /
    # arm ids / edge counts). The old "not X or Y" check was vacuous (the or
    # branch was true whenever the phrase was absent) — absence alone is not
    # enough: the item texts must POSITIVELY name the deliberation constructs
    # they grade (next test), so a rubric that merely omits graph language but
    # grades nothing cannot pass.

def test_r2_items_anchor_deliberation_constructs_positively():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    texts = " ".join(it["text"] for it in spec.items).lower()
    # Positive semantic anchors: at least one item names each coverage
    # construct family from the acceptance. Author r2-coverage.json items so
    # the wording hits these families (Step 1.3's example item does:
    # "counter-argument").
    anchors = {
        "counter-argument weighing": ("counter-argument", "counterargument",
                                      "opposing", "objection"),
        "what-could-be-wrong specificity": ("what-could-be-wrong",
                                             "what could be wrong", "risk",
                                             "downside", "pitfall"),
        "support-vs-oppose weighing": ("support", "oppose"),
        "revision after counter-evidence": ("revis", "revised",
                                             "revisiting", "update"),
    }
    for construct, words in anchors.items():
        assert any(w in texts for w in words), \
            f"no item names the {construct} construct: {texts}"
    assert all(it["decision_rule"] in ("yes", "no") for it in spec.items)

def test_rubric_rendered_prompt_canonical_stable():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    p1 = render_rubric_prompt(spec); p2 = render_rubric_prompt(spec)
    assert p1 == p2 and "yes" in p1.lower() and "no" in p1.lower()

def test_evidence_neutrality_lint_rejects_leaks():
    good = ("The agent weighed the risk of vendor lock-in against the cost of "
            "migration and revised its earlier position.")
    lint_evidence_neutral(good)                      # no raise
    for leak in ("filed a mitigation against the support edge",
                 "create_point create_operator file_nand register_conflict",
                 "arm a4 retrieved 3 memories with 12 edges",
                 "the graph arm surfaced the contradiction at turn 6"):
        with pytest.raises(ValueError):
            lint_evidence_neutral(leak)              # banned tokens / arm id / edge count

def test_loader_json_first_md_fallback(tmp_path):
    rub = tmp_path / "rubrics"; rub.mkdir()
    (rub / "legacy.md").write_text("legacy rubric: judge coverage.", encoding="utf-8")
    spec = load_rubric_spec(tmp_path, "legacy")      # .md fallback kept
    assert "coverage" in render_rubric_prompt(spec)
```
**Step 1.2** — Run → FAIL (`battery.judge.rubric` missing; no rubric JSON; `_load_rubric_text` md-only).
**Step 1.3** — Create `battery/config/rubrics/r2-coverage.json`: itemized items (≥ 4 anchored yes/no, arm-neutral phrasing — e.g. "Did the agent's deliberation consider at least one counter-argument to its adopted position, and weigh it explicitly?" …), each with a decision rule and the gold-anchor block. Banned-token discipline documented in a header comment (evidence renders are tool-stripped; the lint is the mechanical guard).
**Step 1.4** — Create `battery/judge/rubric.py`: `RubricSpec` (rubric_id, items, gold_anchors, rendered prompt cache), `load_rubric_spec` (JSON-first `<id>.json` → md fallback → ConfigError), `render_rubric_prompt` (canonical: fixed preamble + sorted items + answer-vocabulary line "Answer YES or NO for each anchored item…"), `lint_rubric_items`/`lint_evidence_neutral` (banned tokens from `emit._SUBTYPE_OK["tool_event"]` verb names + arm-identity tokens + edge-count patterns). Loader wired into `cli._load_rubric_text` (JSON-first) so `battery validate-judge --rubric r2-coverage` renders the prompt from the JSON — note the CLI's gate loop itself stays the #1410 pairwise battery (hardcoded `n_items=4`, `better/worse/tie`, 5 default probe pairs — cli.py:128) until Task 2 wires the declarative vocabulary + spec item count (ordering note in Step 1.6); Task 1 does NOT claim a semantically valid r2-coverage gate run.
**Step 1.5** — Author `battery/config/rubrics/am2-write-hop.json` — Amendment-2 write-hop rubric items (deliberation content/evidence constructs per #2080 W2 write-path mechanics; battery QA on top; `status: owner-check-pending` on the surfacing-derived items; cross-note #2080 — reuse, never fork).
**Step 1.6** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_rubric_authoring.py tests/test_battery_judge.py tests/test_battery_cli.py -v` → PASS (existing judge/CLI tests keep passing with the JSON-first loader). **Ordering note (Task 1 vs Task 2):** the live `_cmd_validate_judge` hardcodes `n_items=4` + the pairwise vocabulary + 5 default probe pairs (cli.py:128) — a Task-1 `validate-judge --rubric r2-coverage` gate run would exercise the structurally-wrong pairwise leg on a yes/no rubric. Step 1.6's green gate is therefore ONLY the loader back-compat (md-rubric CLI tests keep passing; the JSON renders through the loader) — NOT a semantically complete r2-coverage gate run. `_cmd_validate_judge` handling of the JSON rubric (spec item count + declarative vocabulary + evidence renders) ships in Task 2 (Task 2 Files → cli.py); the CLI stays byte-identical in Task 1 so `test_battery_cli.py`'s existing mock-gate exit-0/2 contract cannot move.
**Step 1.7** — **OWNER-CHECKED CHECKPOINT (b)** — Present the surfacing-leg operational definition (proposal: an anchored-yes item family judged on the write/surfacing HOP evidence — the deliberation turn that first names the contradiction + its product action ref — with the arm-neutral evidence render excluding arm identity and tool names; scoring semantics consume #2291's read-surface + #2080 W2 in #2284 executor v2 Task 10 — THIS issue only authors the files). **Implementation of that leg's code pauses here** until the owner checks the definition on the issue. Files authored in Step 1.5 stay `owner-check-pending` — the pause is recorded in the commit message + a checkpoint comment is posted.
**Step 1.8** — Commit via `commit-workflow` (`feat(rubric): arm-neutral itemized R2 rubric JSON + neutrality lint + hop files; loader JSON-first (#2292)`).

### Task 2: Declarative validation-protocol seam — judge/gate verdict-vocabulary + stress parameterization, exact-binomial AB+BA, per-item IRT

**Intent:** The gate's verdict vocabulary is pairwise `better/worse/tie` (`build_abba_prompts` "which response is better", stress requires `tie` on all-identical, IRT binarizes `better=1`) — structurally inapplicable to a single-construct anchored yes/no judge. Parameterize the vocabulary + stress expectations for declarative labels (yes=1/no=0, same κ/IRT math), RELABEL AB+BA as judge retest-consistency on evidence renders (a single-construct yes/no prompt has no A/B frame — byte-identical renders give byte-identical prompts and a temp-0 judge agrees by construction, so "position bias" is unmeasurable in the anchored vocabulary; round-2 relabel), add within-item stochastic-stability + gold-anchor agreement legs and exact-binomial retest agreement at small n. **No new judging framework** — the same `validate_rubric` battery with a vocabulary protocol object. The Cohen's-κ inter-judge leg stays RETAINED UNCHANGED (vocabulary-agnostic, two independent judge runs over the same item set — n8); the REAL run's inter-judge semantics (two judge models at temp 0) ship in Task 4. The IRT leg must judge REAL anchored item renders — the pairwise loop hardcodes contentless `probe {i}` prompts (verified in gate.py today), so declarative mode takes an `irt_renders` param fed from the evidence bundle.
**Acceptance:** `validate_rubric` accepts a `vocabulary` protocol (labels tuple, irt_yes_label, stress expectations, prompt templates) with a shipped `DECLARATIVE_VOCAB = ("yes", "no")`; declarative mode: the pairwise AB+BA leg is RELABELED **judge retest-consistency** — the SAME anchored evidence render judged in TWO independent calls must return the SAME yes/no verdict ("Judge this evidence against the anchored item. Answer YES or NO."); identical renders in "swapped" positions are byte-identical prompts, so position bias is unmeasurable in the single-construct vocabulary and the pairwise label would be a lie (round-2 relabel; a real position variable would require a multi-render prompt frame that the per-anchor design deliberately does not have); stress probes REWORDED for the yes/no vocabulary — `all_identical` = two IDENTICAL anchored renders judged twice must return the SAME yes/no verdict (consistency replaces the pairwise all-identical⇒tie expectation — no tie concept in the anchored vocabulary), `label_flip` = a negated-label instruction ("answer YES where the item is NOT satisfied") must still yield a non-degenerate verdict consistent with the flipped semantics (never empty/collapsed), verbosity_bias/stochastic_stability adapted to yes/no; IRT infit per ANCHORED ITEM (n_items = len(spec.items), not the pairwise default 4) judged over the caller-supplied `irt_renders` (real anchored item renders from the evidence bundle) — the IRT loop NEVER judges the hardcoded contentless `probe {i}` prompts (gate.py:147, pairwise mode only), and `irt_renders` must supply **≥ 3 × n_items renders** because the live loop issues `n_items × 3` judgments (`for i in range(n_items * 3)`, verified gate.py:147-148) — an under-fed list spec'd at "length ≥ n_items" IndexErrors on a ≥ 4-item rubric (round-2 finding); the AB+BA/retest p-value uses the **exact binomial** at small n (math.comb CDF — no new deps) with the continuity-corrected normal approximation kept for large n; gold-anchor agreement leg (judge verdicts vs the rubric JSON's expected gold labels, agreement ≥ 0.8) runs in declarative mode — the Task-1 gold block carries **≥ 1 expected-`no` render with a no-share > 20%** of the block, so a degenerate all-yes judge scores < 0.8 and FAILS the leg (round-2 finding: an all-`yes` gold set can never detect a judge that says yes to everything); κ leg unchanged (vocabulary-agnostic two-run Cohen's-κ over the same item set — the REAL run's two runs use two real judge configs, Task 4); an unvalidated rubric still raises JudgeGateBlocked (regression kept).
**Files:**
- Create: `tests/test_battery_judge_declarative.py`
- Modify: `battery/judge/gate.py` (vocabulary protocol + exact-binomial + per-item IRT over caller-supplied `irt_renders` + gold-anchor leg), `battery/judge/client.py` (`build_abba_prompts` declarative branch or a `build_declarative_prompts`; `_mock_judge` yes/no-aware when a vocabulary is passed), `battery/cli.py` (`_cmd_validate_judge`: pass the spec's item count + declarative vocabulary + evidence-bundle `irt_renders` when the rubric is a JSON rubric)

**Step 2.1** — Write failing tests (deterministic yes/no mock judges mirroring the #1410 `_GoodJudge`/`_NoisyJudge` pattern):
```python
# tests/test_battery_judge_declarative.py
"""#2292 Task 2 — declarative anchored yes/no validation protocol seam."""
from __future__ import annotations
import pytest
from battery.judge.client import JudgeCall, JudgeClient
from battery.judge.gate import validate_rubric, DECLARATIVE_VOCAB
from battery.judge.rubric import (
    load_rubric_spec, render_rubric_prompt as render_prompt_text)
from pathlib import Path
CONFIG = Path(__file__).resolve().parents[1] / "battery/config"

class _YesJudge(JudgeClient):
    """Deterministic declarative judge: consistent 'yes' unless the item
    render is contradictory (then 'no') — passes every leg."""
    def judge(self, rubric_id, item_id, prompt, temperature=0.0):
        low = prompt.lower()
        verdict = "no" if ("contradict" in low and "opposite" in low) else "yes"
        return JudgeCall(rubric_id, item_id, verdict, 0.9)

class _FlipJudge(JudgeClient):
    """Judge that flips yes/no on the retest call slot (item_id abba-*ba) —
    fails the declarative judge retest-consistency leg."""
    def judge(self, rubric_id, item_id, prompt, temperature=0.0):
        flip = item_id.endswith("-ba")
        return JudgeCall(rubric_id, item_id, "no" if flip else "yes", 0.8)

def test_declarative_good_rubric_passes_on_spec_items():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    n = len(spec.items)
    pairs = [(f"evidence-{i}-a", f"evidence-{i}-b") for i in range(6)]
    rec = validate_rubric("r2-coverage", render_prompt_text(spec), _YesJudge(),
                          pairs, ["yes"]*n + ["no"], ["yes"]*n + ["no"],
                          n_items=n, vocabulary=DECLARATIVE_VOCAB)
    assert rec.passed
    assert len(rec.irt_infit) == n            # per-ANCHOR item infit

def test_declarative_retest_inconsistency_blocks():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    pairs = [(f"evidence-{i}-a", f"evidence-{i}-b") for i in range(6)]
    # round-2 relabel: "position-bias" was vacuous in declarative mode —
    # byte-identical renders in "swapped" positions give byte-identical
    # prompts, so a temp-0 judge always agrees and the binomial passes by
    # construction. The leg is judge RETEST-consistency (the same anchored
    # render judged twice must agree). _FlipJudge flips on the retest call
    # slot (item_id abba-*ba) → retest-inconsistent → the leg must block.
    rec = validate_rubric("r2-coverage", render_prompt_text(spec), _FlipJudge(),
                          pairs, [], [], n_items=len(spec.items),
                          vocabulary=DECLARATIVE_VOCAB)
    assert not rec.passed and "retest" in rec.blocked_reason

def test_declarative_stress_all_identical_consistent():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    pairs = [(f"evidence-{i}-a", f"evidence-{i}-b") for i in range(6)]
    # declarative: the stress probe over two IDENTICAL renders must return
    # the SAME yes/no verdict twice (stochastic-stability), and a verdict
    # must be produced (non-degenerate) — there is no 'tie' label in the
    # anchored vocabulary, so the pairwise all-identical->tie expectation
    # is replaced by a consistency expectation (vocabulary-parameterized).
    # This is asserted through a full validate_rubric run: the declarative
    # stress leg passes for the deterministic _YesJudge and fails for a
    # judge that flips on repetition (inject below via a FlakyJudge).
    rec = validate_rubric("r2-coverage", render_prompt_text(spec), _YesJudge(),
                          pairs, [], [], n_items=len(spec.items),
                          vocabulary=DECLARATIVE_VOCAB)
    assert all(rec.stress.values()), rec.blocked_reason

def test_gold_anchor_block_catches_all_yes_judge():
    # round-2: the gold-anchor set must include >= 1 expected-'no' render
    # (no-share > 20% of the block) — a degenerate all-yes judge then scores
    # below the 0.8 agreement bar and FAILS the anchor leg. Without an
    # expected-'no' anchor, a judge that says yes to every render is
    # indistinguishable from a good one (byte-identical-render retests and
    # a self-consistent judge both pass — the anchor leg is the only catch).
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    labels = [g["expected"] for g in spec.gold_anchors]   # Task-1 JSON shape
    no_share = labels.count("no") / len(labels)
    assert labels.count("no") >= 1 and no_share > 0.2

    class _AllYes(_YesJudge):          # degenerate: never says 'no'
        def judge(self, rubric_id, item_id, prompt, temperature=0.0):
            return JudgeCall(rubric_id, item_id, "yes", 0.9)

    rec = validate_rubric("r2-coverage", render_prompt_text(spec), _AllYes(),
                          [], [], [], n_items=len(spec.items),
                          vocabulary=DECLARATIVE_VOCAB)
    assert not rec.passed and "gold-anchor" in rec.blocked_reason

def test_exact_binomial_small_n():
    from battery.judge.gate import exact_binomial_p
    assert exact_binomial_p(agree=5, n=5) < 0.05      # 0.5^5 = 0.03125
    assert exact_binomial_p(agree=4, n=4) >= 0.05     # 0.0625 exact — blocks

def test_irt_leg_judges_anchored_renders_not_bare_probes():
    # RED for plan-review P1+P2: the pairwise IRT loop hardcodes contentless
    # `probe {i}` prompts (gate.py:147) AND iterates n_items * 3 judgments
    # (`for i in range(n_items * 3)`) — an irt_renders list spec'd at length
    # ">= n_items" would IndexError on a >=4-item rubric. Declarative mode
    # must judge the REAL anchored renders fed via irt_renders (>= 3 *
    # n_items — the true loop bound); assert the irt-* prompts carry anchored
    # content and that the loop fed exactly the bound, never bare probes.
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    n_items = len(spec.items)
    renders = [f"evidence render {i}: the agent weighed a counter-argument "
               f"against its position and revised it."
               for i in range(3 * n_items)]     # >= 3*n_items: loop bound
    seen: list[str] = []

    class _Recording(_YesJudge):
        def judge(self, rubric_id, item_id, prompt, temperature=0.0):
            if item_id.startswith("irt-"):
                seen.append(prompt)
            return super().judge(rubric_id, item_id, prompt, temperature)

    pairs = [(f"evidence-{i}-a", f"evidence-{i}-b") for i in range(6)]
    validate_rubric("r2-coverage", render_prompt_text(spec), _Recording(),
                    pairs, ["yes"] * n_items, ["yes"] * n_items,
                    n_items=n_items, irt_renders=renders,
                    vocabulary=DECLARATIVE_VOCAB)
    assert len(seen) == 3 * n_items, \
        f"IRT leg issued {len(seen)} judgments, expected {3 * n_items}"
    assert all("counter-argument" in p for p in seen), \
        "IRT prompts are contentless bare probes, not anchored item renders"
```
**Step 2.2** — Run → FAIL (vocabulary hardcoded; IRT at n=4 over contentless `probe {i}` prompts — `irt_renders` param missing and no ≥ 3×n_items bound; declarative AB+BA still spec'd as pairwise position-bias over byte-identical renders (vacuous — round-2); gold-anchor leg absent (no degenerate all-yes catch); all_identical/label_flip stress texts pairwise-tie-shaped; normal-approx binomial).
**Step 2.3** — Implement: `DECLARATIVE_VOCAB` protocol (labels `("yes","no")`, irt yes label, stress spec, declarative prompt builders); `validate_rubric(..., vocabulary=..., n_items=None, irt_renders=None)` — pairwise mode keeps current behavior byte-identical (default vocabulary `better/worse/tie`, `irt_renders=None` ⇒ the legacy `probe {i}` loop — existing #1410 tests must not move); declarative mode runs the same four legs with vocabulary-parameterized prompts/stress + the gold-anchor agreement leg, with the AB+BA slot RELABELED judge retest-consistency (the SAME anchored render judged twice in two independent calls must agree; the declarative blocked_reason names the retest leg, never the pairwise `position-bias` label — gate.py `_blocked_reason` appends `position-bias` today, so the declarative branch parameterizes that string too), and its IRT loop judges the anchored item renders from `irt_renders` (evidence-bundle renders injected by the CLI — `irt_renders[i]` replaces the `probe {i}` string for `i in range(n_items * 3)`, the LIVE loop bound at gate.py:147, so the list must be **≥ 3 × n_items** long — never a bare "length ≥ n_items", which IndexErrors on a ≥ 4-item rubric; round-2 finding); declarative stress probes reworded for yes/no (all_identical = same-verdict-twice consistency on identical renders; label_flip = negated-label instruction still yields a non-degenerate verdict); `exact_binomial_p` (math.comb) for n ≤ 32 with the normal-approx fallback above it; IRT over anchored items (`_rasch_infit(verdicts, n_items)` with yes=1); gold-anchor agreement = judge verdicts vs the JSON gold labels (agreement ≥ 0.8; the Task-1 block's ≥ 1 expected-`no` with no-share > 20% makes a degenerate all-yes judge score < 0.8 and fail — the leg's blocked_reason names `gold-anchor`). JudgeClient `_mock_judge` yields yes/no deterministically in declarative mode (so hermetic gate tests stay reproducible); `_real_call` unchanged in this task (usage capture = Task 3).
**Step 2.4** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_judge_declarative.py tests/test_battery_judge.py tests/test_battery_rubric_authoring.py -v` → PASS (existing pairwise tests byte-identical).
**Step 2.5** — Commit via `commit-workflow` (`feat(judge): declarative anchored-yes/no validation protocol seam + exact-binomial AB+BA (#2292)`).

### Task 3: #2292-owned real-model probe — driver + usage capture + measured-token tables + budget sub-cap; bounded REAL run (pre-authorized spend)

**Intent:** The validation run needs REAL deliberation text, and the budget needs MEASURED tokens — both from ONE minimal probe that runs the candidate-pinned model under test (decision (a): `deepseek/deepseek-v4-flash` UNCAPPED temp 0) on 3–5 scenarios, before the #2284 executor exists (Task 9 is gated on this issue). The probe is #2292-OWNED, standalone (it must NOT depend on run.py's real-executor seam, which refuses real mode until Task 9's emission seam is active — verified in run.py), budget-guarded under a probe sub-cap, with judge spend metered (decision (c)). It produces: (a) real deliberation text per scenario × arm for the validation anchors, (b) measured per-phase tokens (model under test + judge leg) → the token tables Task 6 re-locks from, (c) a persisted evidence bundle for Task 4's validation run.
**Acceptance:** `battery probe` CLI subcommand (`--scenarios <ids> --arms a0,a4 --seed N --config --out`) drives the pinned model via `model_adapters` registry (`deepseek-flash` → `OpenRouterModel('deepseek/deepseek-v4-flash', max_tokens=None, temperature=0.0)`) wrapped in `OutcomeRecordingCaller`; per-call usage captured (model_adapters `last_prompt_tokens`/`last_completion_tokens` + the #2185 usage-sink pattern) into a per-phase accumulator (deliberation-turn phase vs envelope phase vs judge-leg phase); judge calls go through JudgeClient REAL mode — the run MUST set `BATTERY_JUDGE_MODEL=openai/gpt-4o-2024-08-06` (bare slug; JudgeClient has NO model default — absent env ⇒ mock by design, and `_real_call` posts `_model_id` verbatim with no `openrouter:` prefix — verified in client.py) with per-call usage captured (Task 3 extends `_real_call` to record usage on the JudgeCall) and metered against the SAME probe sub-cap; probe spend sub-cap in `budget.yaml` (`probe_cap_usd`, default $3.00 — pre-authorized within the existing $50 dollar cap; judge leg accumulates separately but counts toward the sub-cap with its own reserve line `judge_leg_reserve_usd`, default $1.50 — decision (c); the reserve is HARD-ENFORCED in Task 4's `--evidence` path); the probe refuses to start when OPENROUTER_API_KEY is absent (fail-closed, exit 1) or the sub-cap would be exceeded; output: `attempt-*/probe_manifest.json` (scenarios × arms × model/temp/provider + usage rows), per-episode deliberation transcripts (real turn content — zero fabricated turns: every turn non-empty and matching the recorded model-call outcome), `probe_tokens.json` (per-phase 95th-pct + mean + judge-leg accounting), and `probe_validation_bundle.json` (rubric id → per-anchor evidence renders, tool-stripped + lint-passed, for Task 4).
**Files:**
- Create: `battery/probes/probe_runner.py` (probe driver: scenario render → deliberation scaffold turns → envelope fields; usage accumulator; budget sub-cap check; bundle writer), `tests/test_battery_probe_measured.py` (hermetic: mock caller with scripted usage; NO network)
- Modify: `battery/cli.py` (`probe` subcommand), `battery/config/budget.py` + `budget.yaml` (`probe_cap_usd`), `battery/judge/client.py` (`_real_call` usage capture → JudgeCall fields), `battery/config/arms.py` (no change — pin read in Task 5)

**Step 3.1** — Write hermetic RED tests (no network, scripted usage):
```python
# tests/test_battery_probe_measured.py
"""#2292 Task 3 — probe driver (hermetic): usage capture, sub-cap refusal,
95th-pct token tables, per-phase + judge-leg accounting, real-content guard."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from battery.exceptions import ConfigError          # real import path: battery/exceptions.py
from battery.probes.probe_runner import run_probe, ProbeBudget, token_tables

CONFIG = Path(__file__).resolve().parents[1] / "battery/config"

class _ScriptedCaller:
    """Scripted deliberation caller. Exposes the model_adapters usage
    contract the probe accumulator reads (model_adapters.py sets
    last_prompt_tokens/last_completion_tokens per real call) — the
    scripted (10, 20) tokens per call make the per-phase sums deterministic
    WITHOUT any network. (Round-2: the earlier version only CLAIMED (10, 20)
    usage capture in a comment while implementing none of it — doc drift;
    the capture fields below are the real seam the accumulator reads.)"""
    model_id = "deepseek/deepseek-v4-flash"; temperature = 0.0
    last_prompt_tokens = 0; last_completion_tokens = 0
    def __init__(self): self.calls = []
    def call(self, *, prompt: str) -> str:
        self.calls.append(prompt)
        self.last_prompt_tokens = 10      # scripted usage capture: (10, 20)
        self.last_completion_tokens = 20  # tokens per call — accumulated per phase
        return "The agent weighs the counter-argument and revises its position."  # noqa: E501

def test_probe_accumulates_usage_per_phase(tmp_path):
    # scripted caller whose usage capture returns (10, 20) tokens per call
    # → per-phase accumulator rows sum per-call prompt+completion tokens
    out = run_probe(config=CONFIG, arms=["a0", "a4"], scenario_ids=["S1", "S2"],
                    caller=_ScriptedCaller(), out_dir=tmp_path)
    tok = json.loads((tmp_path / "probe_tokens.json").read_text())
    assert "deliberation" in tok and "judge" in tok     # per-phase tables
    assert tok["judge"]["calls"] >= 0                   # judge-leg accounted

def test_probe_real_content_guard(tmp_path):
    # zero fabricated turns: a caller returning EMPTY text fails the run
    class _Empty(_ScriptedCaller):
        def call(self, *, prompt): return ""
    with pytest.raises(ValueError):
        run_probe(config=CONFIG, arms=["a0"], scenario_ids=["S1"],
                  caller=_Empty(), out_dir=tmp_path)

def test_probe_subcap_refusal(tmp_path):
    budget = ProbeBudget(cap_usd=0.001)
    with pytest.raises(ConfigError):
        run_probe(config=CONFIG, arms=["a0", "a4"], scenario_ids=["S1", "S2"],
                  budget=budget, out_dir=tmp_path)       # refuses before spend

def test_token_tables_95th_pct():
    rows = {"deliberation": [100, 110, 120, 200, 500], "judge": [30, 30, 30]}
    t = token_tables(rows)
    assert t["deliberation"]["p95"] == 200 and t["judge"]["p95"] == 30
```
**Step 3.2** — Run → FAIL (module missing; no `probe` subcommand; no sub-cap).
**Step 3.3** — Implement `battery/probes/probe_runner.py` (scenario selection: 3–5 authored scenario ids — one decision-family (R2/R4), one contradiction (R1/R2-render), one calibration (R3) minimum; per scenario × arm: render `render_reader_prompt(scenario.to_render_dict())` + a minimal harness deliberation scaffold (position → challenge → deepen → revise envelope) executed by the pinned model; every turn content recorded; model-call usage via the caller's last_* fields or a bound usage sink; envelope scalars assembled; per-phase token accounting; budget refusal BEFORE the first call when sub-cap exceeded and mid-run stop when the cap is hit (never silent continuation); `probe_manifest.json` carries the model block `{model_id, provider: "openrouter", temperature: 0.0}`).
**Step 3.4** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_probe_measured.py tests/test_battery_cli.py -v` → PASS.
**Step 3.5** — `budget.yaml` gains `probe_cap_usd: 3.0` (+ `budget.py` loader + refusal) and the judge-leg reserve line `judge_leg_reserve_usd: 1.5` (decision (c) — recorded here; the HARD STOP that enforces it ships with Task 4's `--evidence` path). JudgeClient `_real_call` records usage (parse the OpenRouter `usage` block → JudgeCall.prompt_tokens/completion_tokens/cost) so judge spend is metered, additive/back-compatible.
**Step 3.6** — Commit via `commit-workflow` (`feat(probe): #2292-owned real-model probe driver + measured-token tables + sub-cap (#2292)`).
**Step 3.7** — **REAL PROBE RUN (pre-authorized spend; executing-plans runs this once)**: `uv run python -m battery probe --scenarios <3-5 ids> --arms a0,a4 --seed 7 --out battery-out` with `OPENROUTER_API_KEY` from `.env` (present). Spend ceiling: probe sub-cap $3.00 (pre-authorized within the existing `budget.yaml` $50.00 cap — recorded in the run manifest). Expected: manifest + per-episode real transcripts + `probe_tokens.json` + `probe_validation_bundle.json`. If the run fails mid-way on a terminal provider error, re-run ONCE after a documented retry; a second failure → STOP and surface to the owner (the probe is spend-gated, never silently re-run).

### Task 4: ONE validation run PRE-exposure on the real probe text → persisted ValidationRecord

**Intent:** Indicator (2): ONE validation run on real deliberation text lands BEFORE #2284 exposure part 1 — the only honest proof the R2 anchors discriminate on the real rendering surface (scoping adversarial scenario 1: anchors drawn from the SAME rendering surface as exposure). The record is persisted by rubric id so R2 scoring is unblocked and rubric-id final pinning may defer.
**Acceptance:** `battery validate-judge --rubric r2-coverage --evidence <probe_validation_bundle.json>` (real judge, declarative vocabulary, per-anchor items, gold anchors) runs the full battery over the probe's REAL text → `ValidationRecord` persisted at `<out>/judge/records.json` with `passed=True`: AB+BA **judge retest-consistency** exact-binomial p<0.05 over **≥ 8 identical-render retest pairs** (the SAME per-anchor render from the bundle judged twice in two independent calls; p<0.05 needs ≥5/5 agree at n=5, ≥7/8 at n=8, ≥9/10 at n=10 — renders are byte-identical, so "swapped prompt positions" is meaningless in the single-construct vocabulary: a temp-0 judge over byte-identical prompts agrees by construction and the leg's honest meaning is judge self-consistency, NEVER position bias (round-2 relabel); different-text pairs measure nothing and never pad the count); **κ ≥ 0.70 measured between TWO real judge configs** — Judge A `openai/gpt-4o-2024-08-06` + Judge B a second real OpenRouter model (`BATTERY_JUDGE_MODEL_2`, e.g. `anthropic/claude-opus-5`), both temp 0, over the SAME per-anchor renders — a single judge model at temp 0 yields κ≡1.0 by construction and never counts as inter-judge reliability, so the `--evidence` path fails closed when the second config is absent; per-item IRT infit [0.7,1.3] (anchored renders; the bundle feeds ≥ 3 × n_items per-item renders — the live IRT loop bound, gate.py `for i in range(n_items * 3)`); declarative stress all-green; gold-anchor agreement ≥ 0.8 (the gold block carries ≥ 1 expected-`no` render with no-share > 20% — a degenerate all-yes judge fails below the bar, round-2 finding); the record's checksum matches the rubric's rendered prompt (drift re-blocks — E2E-5.2); scoring WITHOUT the record still raises JudgeGateBlocked (regression asserted); judge spend metered + recorded in the validation-run receipt with a **HARD STOP** enforced in the `--evidence` path against the judge-leg reserve line (`budget.yaml` `judge_leg_reserve_usd`, default $1.50 — Task 3 adds the line; the path refuses to start when the reserve would be exceeded and aborts mid-run on cap hit — never a silent overshoot).
**Files:**
- Create: `tests/test_battery_validation_real.py` (network-gated `@pytest.mark.slow`, env-key guarded, hermetic evidence-fixture fallback for CI), evidence-bundle reader in `battery/judge/rubric.py` or `battery/cli.py` (`_cmd_validate_judge --evidence`)
- Modify: `battery/cli.py` (`--evidence` flag: pair renders → item prompts; κ pass built from a SECOND JudgeClient — env `BATTERY_JUDGE_MODEL_2`, fail-closed when absent on the real path; judge-leg reserve HARD-STOP cap), `battery/judge/gate.py` (evidence-bundle entry: probe_pairs from bundle renders, judge_labels_a/b = two judge passes over the item set), `battery/config/budget.py` + `budget.yaml` (`judge_leg_reserve_usd` — the reserve line Task 3 added; cap enforcement lives in the `--evidence` path)

**Step 4.1** — RED (hermetic first): with a FIXTURE evidence bundle + the deterministic `_YesJudge`, the full declarative battery passes and persists a record; an unvalidated rubric still blocks (`RubricRegistry.require_validated` raises). Tests: `tests/test_battery_validation_real.py::test_evidence_bundle_persists_record` (mock judge, fixture bundle) + `::test_second_judge_absent_fails_closed` (no `BATTERY_JUDGE_MODEL_2` on the real path ⇒ ConfigError — no degenerate single-model κ≡1.0 pass) + `::test_judge_leg_cap_refusal` (reserve exceeded ⇒ refuse/abort, never overshoot) + `::test_gold_anchor_all_yes_judge_fails` (a degenerate all-yes judge — 'no' absent from its vocabulary — must fail the gold-anchor leg: the block's ≥ 1 expected-`no` render drops agreement below 0.8; a battery over renders alone, without the anchor leg, can never catch it — round-2) → implement `--evidence` path (two-judge κ pass + reserve cap) → GREEN.
**Step 4.2** — REAL validation run (spend-gated, one shot, INSIDE the Task-3 probe sub-cap): `OPENROUTER_API_KEY` + `BATTERY_JUDGE_MODEL=openai/gpt-4o-2024-08-06` (bare slug — JudgeClient has NO default and mocks by design when the env is absent, so the env MUST be set for a real judge) + `BATTERY_JUDGE_MODEL_2=anthropic/claude-opus-5` (the κ leg's second real judge config — absent ⇒ fail-closed, see acceptance) `uv run python -m battery validate-judge --rubric r2-coverage --evidence <bundle> --out battery-out`. Retest pairs = the SAME evidence render judged twice (≥ 8 identical-render retest pairs; byte-identical prompts make "position swap" vacuous in the single-construct vocabulary — the leg measures judge self-consistency, and different-text pairs never count toward the binomial; round-2 relabel); κ = inter-judge reliability between the two real judge configs; judge spend metered (Task 3 accumulator) against `judge_leg_reserve_usd` (default $1.50 — HARD STOP: the `--evidence` path refuses to start when the reserve is exceeded and aborts mid-run on cap hit; expected spend recorded in the receipt — never a note-only ceiling). Outcome gates: passed=True → record persisted → proceed to Task 5; passed=False → STOP: iterate rubric anchors ONCE against the blocked_reason legs (bounded authoring fix — the exact-binomial/κ/IRT legs localize failed anchors), re-run validation ONCE; second failure → surface to the owner (the issue's falsification case (1): anchors may not discriminate on probe text — the correct response is a documented owner decision, never a silent rubric weakening).
**Step 4.3** — Regression: `tests/test_battery_judge.py::test_registry_fail_closed` still green; commit via `commit-workflow` (`feat(judge): pre-exposure validation record on real probe text (r2-coverage) (#2292)`).

### Task 5: Model pin re-lock + read path + real-run pre-flight (decision (a); coordination n10)

**Intent:** Indicator (3): the model under test is pinned in arms.yaml (same model across arms, temp 0, provider recorded in the artifact `model` block) and a real run refuses without a pinned concrete model. #2284 Task 6 MERGED the `model_pin`/`temperature` schema (ArmConfig parses them; parity protocol leg live) — this task re-locks the VALUES and wires the read path + refusal (the "mirror-inversion of the judge's absent-model⇒mock rule").
**Acceptance:** arms.yaml real-capable arms carry `model_pin: deepseek/deepseek-v4-flash` (decision (a)) + `temperature: 0.0` (identical across arms; mock's placeholder-pin row is exempted and stays a lane cap — mock is never real-scored); a `resolve_pinned_model(arm_cfg)` helper maps the pin → the model_adapters registry factory and raises `ConfigError` on the placeholder sentinel / an unknown model id; the real-run pre-flight in `run.py` (alongside the existing real-executor pre-flight) refuses when: any requested real arm's pin is the `flash-class-placeholder` sentinel OR an arm class still hardcodes the `model_id = "fixed"` sentinel (battery/arms/*.py — verified present today) OR temperatures differ across requested real arms OR the pin cannot resolve; the refusal fires BEFORE the attempt dir (zero orphaned artifacts); the probe/real artifact `model` block records `{model_id, provider: "openrouter", temperature}` (build_run_artifact already carries `model` — populate it from the pinned arm config).
**Files:**
- Create: `tests/test_battery_model_pin.py`
- Modify: `battery/config/arms.yaml` (pin values), `battery/runner/run.py` (pin pre-flight + model block population), `battery/config/arms.py` or `battery/probes/probe_runner.py` (`resolve_pinned_model` — in `battery/config/arms.py` so both run.py and the probe import one home), `tests/test_battery_run.py` (existing real-mode refusal tests keep passing)

**Step 5.1** — RED tests:
```python
# tests/test_battery_model_pin.py
"""#2292 Task 5 — model pin re-lock + read path + real-run pre-flight."""
from __future__ import annotations
import pytest
from pathlib import Path
from battery.config.arms import load_arms, resolve_pinned_model
from battery.exceptions import ConfigError
CONFIG = Path(__file__).resolve().parents[1] / "battery/config"

def test_arms_pin_relocked_concrete_same_across_arms():
    arms = load_arms(CONFIG / "arms.yaml")
    real = [a for aid, a in arms.items() if aid != "mock"]
    assert {a.model_pin for a in real} == {"deepseek/deepseek-v4-flash"}
    assert {a.temperature for a in real} == {0.0}

def test_placeholder_pin_refused():
    with pytest.raises(ConfigError):
        resolve_pinned_model("flash-class-placeholder")   # sentinel never runs

def test_unknown_pin_refused():
    with pytest.raises(ConfigError):
        resolve_pinned_model("no/such-model")

def _hermetic_cfg(tmp_path) -> Path:
    """Yaml-only config dir with a 2-scenario corpus + sized caps (the
    test_battery_run._config_dir pattern) — corpus.json absent so the
    freshness gate no-ops. arms.yaml is NOT copied from live CONFIG: it is
    written per branch below so the test is ORDER-INDEPENDENT — it must stay
    green both RED (live pins still placeholders) AND after Step 5.3
    re-locks the live arms.yaml to concrete pins."""
    import yaml, hashlib
    d = tmp_path / "cfg"; d.mkdir(parents=True, exist_ok=True)
    golds = tmp_path / "golds"; golds.mkdir(parents=True, exist_ok=True)
    gold = golds / "g.txt"; gold.write_text("gold", encoding="utf-8")
    sha = hashlib.sha256(b"gold").hexdigest()
    corpus = {"scenarios": [
        {"id": f"s{i}", "tier": "probe", "family": "f", "k": 1,
         "gold_ref": {"path": "g.txt", "sha256": sha}}
        for i in range(2)]}
    (d / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6}, "cal": {}}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d

def _write_pin(cfg_dir: Path, pin: str) -> None:
    import yaml
    (cfg_dir / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "mock", "adapter": "battery.arms.mock", "config": {},
         "model_pin": pin, "temperature": 0.0},
        {"arm_id": "a4", "adapter": "battery.arms.a4_tortoise", "config": {},
         "model_pin": pin, "temperature": 0.0}]}), encoding="utf-8")

def test_real_preflight_refuses_unpinned_or_fixed_sentinel(tmp_path, monkeypatch):
    # hermetic: stub the real emission seam active (run.py round-3 pattern —
    # hermetic tests activate the seam by stubbing run._episode_log), then a
    # real-executor request must ConfigError BEFORE attempt-dir creation on
    # EITHER refusal branch (zero orphaned artifacts).
    from battery.runner import run as run_mod
    run_mod._episode_log = lambda *a, **k: []        # seam "active"
    cfg_dir = _hermetic_cfg(tmp_path)
    out = tmp_path / "out"

    # Branch (i): placeholder-pin arms.yaml fixture -> the pin gate refuses.
    _write_pin(cfg_dir, "flash-class-placeholder")
    with pytest.raises(ConfigError):                 # placeholder pin refused
        run_mod.run_battery(run_mod.RunConfig(config_dir=cfg_dir, arms=["a4"],
                                              executor="real", out_dir=out),
                            stdout=lambda s: None)
    assert not out.exists() or not [p for p in out.iterdir()]  # no orphaned dir

    # Branch (ii): CONCRETE pin but the arm class still hardcodes the
    # model_id = "fixed" class sentinel (battery/arms/a4_tortoise.py etc. —
    # verified present) -> the class-sentinel gate refuses. Asserted
    # explicitly here (no trailing "..."): it clears only when Task 9's
    # executor parameterizes the arm classes off the fixed sentinel.
    _write_pin(cfg_dir, "deepseek/deepseek-v4-flash")
    with pytest.raises(ConfigError):                 # class-sentinel refused
        run_mod.run_battery(run_mod.RunConfig(config_dir=cfg_dir, arms=["a4"],
                                              executor="real", out_dir=out),
                            stdout=lambda s: None)
    assert not out.exists() or not [p for p in out.iterdir()]  # no orphaned dir
```
**Step 5.2** — Run → FAIL (pin values are placeholders; no resolve helper; no pre-flight refusal).
**Step 5.3** — Re-lock arms.yaml pins (mock row exempted with a comment: lane cap, never real-scored); add `resolve_pinned_model` to `battery/config/arms.py` (pin → `model_adapters.MODELS` factory lookup by pin-derived registry key, raising ConfigError on sentinel/unknown); wire the real-mode pin pre-flight into `run.py`'s existing real-executor gate block (refuse placeholder pin / "fixed" class sentinel / temp mismatch / unresolvable pin; before attempt-dir creation); populate the artifact `model` block from the pinned arm config on real runs. **Cross-note (n10):** run.py is ALSO co-edited by #2284 Task 9 (executor v1 — real-model runner plumbing + run_mode + emission seam; gated on this issue). Task 5's pin-pre-flight edit lands FIRST and stays additive INSIDE the existing real-executor pre-flight gate block (PR #2341 rounds 2+3, verified present in run.py); #2284 Task 9 merges later over the same block and consumes the pinned values ("sibling B pin" — its acceptance). Never touch Task 9's emission-seam surface; post the cross-note comment on #2284 with Task 5's commit.
**Step 5.4** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_model_pin.py tests/test_battery_run.py tests/test_battery_config.py tests/test_battery_parity_hash.py -v` → PASS (parity hash tests still green — a pin change trips the protocol hash by design; the parity tests derive from the authored arms.yaml so they re-lock in this task).
**Step 5.5** — Commit via `commit-workflow` (`feat(pin): measured model pin re-lock + resolve helper + real-run pre-flight (#2292)`).

### Task 6: Measured token re-lock + hashed reviewable-change machinery (coordination n1)

**Intent:** Indicators (4): arms.yaml `expected_tokens_per_episode` (800 tok/ep guess on a4) replaced by MEASURED per-phase tokens from the probe (95th-pct + judge-leg accounting) riding a REVIEWABLE HASHED CHANGE — never silent. Coordination n1: this issue owns the FORMULA + machinery + PROVISIONAL values; #2284 Task 8's smoke finalizes over the SAME machinery (arms.yaml edited once per phase, never in parallel).
**Acceptance:** a `token_table_hash` (canonical serialization of arms.yaml token fields + the per-phase measured rows that produced them) joins the provenance surface (artifact provenance `config_files` + the hash printed by `battery calibrate --print` alongside the cal hash — [cal]-discipline print-don't-tune extension to cost constants); arms.yaml `expected_tokens_per_episode` re-locked to `ceil(p95_measured_per_episode × headroom)` with the 3–5× thinking headroom factor applied (default 3×) **for the probe-MEASURED arms ONLY — a0 and a4** (the Task 3 probe runs `--arms a0,a4`; mock's 64 stays the exempt lane cap; measured rows exist for exactly a0/a4) + judge-leg per-episode tokens added as a separate accounting line in the manifest/provenance (the arms row stays model-under-test tokens; judge spend lives in the metered line — decision (c)); arms WITHOUT probe-measured rows — a1/a2/a2b/a3 (600/700/700/400 guesses) — are NOT re-locked and NEVER annotated as measured: they keep their authored rows annotated `TBD(EXPOSURE): finalize from #2284 Task 8 exposure measurements over the same token_table_hash machinery` (stale guesses never masquerade as measured rows); the guess provenance is preserved (`# measured_after_exposure — provisional: probe-derived 95th-pct × 3× headroom; finalization by #2284 Task 8 smoke over the same token_table_hash machinery` — n1 handoff); the re-lock test asserts the old-800/new-measured delta surfaces in the hash (never silent) + `calibrate --print` prints the token hash.
**Files:**
- Create: `tests/test_battery_token_relock.py`
- Modify: `battery/config/arms.py` (token-table canonical serialization helper `token_table_hash(arms)`), `battery/config/arms.yaml` (measured re-lock rows for the probe-measured arms a0/a4; a1/a2/a2b/a3 keep their authored guesses + `TBD(EXPOSURE)` annotations — never stale-guess-as-measured), `battery/report/calibrate.py` + `battery/cli.py` (`calibrate --print` prints the token hash — print-only), `battery/runner/artifacts.py`/`run.py` (provenance gains the token hash)

**Step 6.1** — RED test (provisional fixtures): a re-lock from fixture measured rows (a) computes `ceil(p95 × 3)` for the MEASURED arms (fixture keys arms a0/a4 only — arms without measured rows keep their authored rows + `TBD(EXPOSURE)` annotation; the re-lock helper never fabricates a measured row from a stale guess), (b) drifts `token_table_hash` vs the authored 800-tok guess table (assert hashes differ), (c) `calibrate --print` output carries the token hash.
**Step 6.2** — Run → FAIL (no machinery).
**Step 6.3** — Implement `token_table_hash` in `arms.py` (canonical `"<arm>|<tokens>"` lines + the measured-source rows header — a re-lock changes the hash; folded into provenance + the `calibrate --print` surface).
**Step 6.4** — Re-lock arms.yaml rows from `probe_tokens.json` (Task 3 output): re-lock `expected_tokens_per_episode = ceil(p95_deliberation_and_envelope × headroom)` for the PROBE-MEASURED arms ONLY (a0, a4 — the Task 3 probe ran `--arms a0,a4`, so measured rows exist for exactly these two real arms + mock's exempt lane cap); arms a1/a2/a2b/a3 keep their authored rows and get a `TBD(EXPOSURE): finalize from #2284 Task 8 exposure measurements over the same token_table_hash machinery` annotation — never annotate the stale 600/700/700/400 guesses as measured; judge-leg tokens recorded as the separate metered line in the probe manifest (never folded into the model-under-test row); `measured_after_exposure` annotations updated to name the probe source + 3–5× factor.
**Step 6.5** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_token_relock.py tests/test_battery_config.py tests/test_battery_cli.py -v` → PASS; commit via `commit-workflow` (`feat(budget): measured token re-lock + token_table_hash reviewable-change machinery (#2292)`).

### Task 7: thresholds re-locks — R1 post-I-1 rows + flip-flop/fp rows + R2 1.5× reconciliation + metric-key canonicalization + determinism measured re-lock over the seed (coordination n2/n6/n8/n9)

**Intent:** Indicators (5): stale [cal] rows would silently mis-classify deltas under the post-I-1 semantics. Re-lock R1 rows corrected post-I-1, add the missing flip-flop-rate + false-positive-rate rows, reconcile the R2 coverage 1.43×/1.5× inconsistency to ONE canonical gate form (decision (e): ratio ≥ 1.5× with the a0=0 floor) + pin the coverage metric SEMANTIC to one form + canonicalize the coverage metric KEY (n8: single spelling across thresholds.yaml key / probe metric / report metric / cal rows — the hyphen/underscore split unified at the loader boundary; the schema-v1.1 event-log field name `coverage_subscore` is LOG-internal and stays), and perform the determinism measured re-lock over the #2284 Task-7 seed — PROVISIONAL: the probe-EMITTABLE usage/token rows (`total_tokens` + per-phase tokens) re-confirmed on the probe path with honest measured tolerances; the DERIVED/OBJECTIVE rows (`n_tool_calls`, `n_turns`, `outcome_*`, `re_derivations`) stay at their mock-lane seed — the #2292 scaffold probe (4-turn envelope, no event log, no outcome enum) cannot emit them (round-2 finding); real-path model-text/judged tolerance finalization DEFERRED to #2284 Task 8 exposure-measured numbers (merged 04-plan E2E-7.1) — + assert the cal_table_hash round-trip (n2: Task 7 MERGED the seed + hash fold-in; if it had not merged, this task does the block + hash + re-lock in one reviewable change — merge-order conditional documented below; the n9 double-edit pin applies to every thresholds.yaml row).
**Acceptance:** thresholds.yaml: R1 `surfaced-rate` row corrected to the post-I-1 seed-mode semantics (measured basis from the mock lane + probe evidence where surfacing legs exist; provenance comment names the basis; the row stays consistent with the E2E-1.1 surfaced ≥ 90% gate target); NEW rows `flip-flop-rate` + `false-positive-rate` added as **placeholder-locked PROVISIONAL rows** — their measurement path (bct control-verdict emission + the flip/fp rate pools; `false_positive` verdicts on bct benign twins) is #2284 Task 9 EXECUTOR-owned, so the rows carry a placeholder-lock provenance annotation naming their STATED basis (the AC nominal upper-bound caps — flip-flop ≤ 10%, fp ≤ 5%, E2E-1.1 — as placeholders, plus the fp denominator = bct benign twins pooled across arms × runs, #2284 Task 3 corpus) and are NEVER presented as measured or stale-guess-as-locked; the annotation extends the thresholds.yaml deferral note (PR #2341 round 4 — "the FP gate path consumes profile.json control_records once Task 9 emits bct control verdicts") to the flip-flop row (round-2 finding); R2 `coverage-subscore` row re-locked so the a4-expectation ≥ 1.5× the a0-expectation holds (config expectation assert — no tolerated 1.43× mismatch) with the a0=0 floor stated (control == 0 ⇒ gate passes iff treatment > 0 — the expectation assert and the runtime gate agree at the boundary); `r2_coverage.delta_vs_control` updated to the canonical ratio form with the floor (treatment/control if control > 0 else pass-if-treatment>0); the coverage metric key canonicalized (probe `metric`/`cal_metric` unified to ONE spelling — `coverage-subscore`; the underscore spelling retired from report-visible surfaces; tests assert `calibrate --print` resolves the row, no "NOT IN CAL TABLE" for the measured family payload); determinism: the real-path re-confirm is scoped to the rows the probe can actually EMIT — the usage/token family (`total_tokens` per episode + the per-phase token rows the probe tables): two-run probe on 2 scenarios, same seed → per-usage-metric |Δ| ≤ a tolerance re-locked from the MEASURED two-run usage spread (provisional real-path rows — real usage is NOT transcript-locked, so the mock-lane 1e-6 seed never applies to them); the DERIVED/OBJECTIVE tolerance rows (`n_tool_calls`, `n_turns`, `outcome_*`, `re_derivations`) CANNOT be emitted by the #2292 scaffold probe (4-turn envelope, no event log, no outcome enum — they are harness `EpisodeResult`/`HarnessScorer`-derived, `HARNESS_METRIC_IDS` in battery/runner/scorers.py) → they STAY at their mock-lane seed values (transcript-locked 1e-6) with the two-run probe recording them as a SANITY PRINT only — never a re-lock basis (round-2 finding); their real-path confirmation is #2284 Task 8 exposure over the event log; model-text/judged rows STAY at their #2284 Task-7 seeded values — merged 04-plan E2E-7.1 reserves the real-path model-text/judged tolerance re-lock to exposure-measured numbers (#2284 Task 8), so those rows keep their `TBD(EXPOSURE)` markers + the run-level nondeterminism fingerprint (never bit-compared, never re-locked from a 2-run probe) + `cal_table_hash` round-trip asserted (a tolerance re-lock drifts the hash — reviewable) + `calibrate --print` hash route green; thresholds.yaml once-per-phase double-edit pin holds (n9): this issue lands seed/mechanics + PROVISIONAL rows, #2284 Task 8 performs the measured finalization over the same rows.
**Files:**
- Create: `tests/test_battery_thresholds_relock.py`
- Modify: `battery/config/thresholds.yaml`, `battery/probes/r2_coverage.py` (canonical semantic + delta_vs_control floor), `battery/runner/probe_scorer.py` (metric-key canonicalization at the record boundary — if the hyphen/underscore split survives there; verify + unify), `battery/report/calibrate.py`/`cli.py` if the loader boundary needs the alias, `tests/test_battery_config.py`, `tests/test_battery_determinism.py` (re-lock assert), `battery/config/arms.yaml` comment n/a

**Step 7.1** — RED tests:
```python
# tests/test_battery_thresholds_relock.py
"""#2292 Task 7 — thresholds re-locks + R2 gate-form + metric-key + determinism."""
from __future__ import annotations
import yaml, pytest
from pathlib import Path
from battery.config.thresholds import ThresholdsConfig, load_thresholds
CONFIG = Path(__file__).resolve().parents[1] / "battery/config"

def _rows(cfg) -> dict: return {(m, a): v for m, a, v in cfg.cal_rows}

def test_r2_gate_form_consistent_ratio_ge_1_5():
    cfg = load_thresholds(CONFIG / "thresholds.yaml")
    rows = _rows(cfg)
    a4 = rows[("coverage-subscore", "a4")]; a0 = rows[("coverage-subscore", "a0")]
    if a0 == 0.0:
        assert a4 > 0.0                      # a0=0 floor: pass iff treatment > 0
    else:
        assert a4 >= 1.5 * a0                # canonical form — no 1.43x tolerated

def test_flip_flop_and_fp_rows_present_placeholder_locked():
    cfg = load_thresholds(CONFIG / "thresholds.yaml")
    rows = _rows(cfg)
    metrics = {m for m, _ in rows}
    assert {"flip-flop-rate", "false-positive-rate"} <= metrics
    # round-2: both rows are authored BEFORE their measurement path exists
    # (bct control-verdict emission + the rate pools are #2284 Task 9
    # executor-owned) — they must be PLACEHOLDER-LOCKED with a stated basis
    # (the AC nominal upper-bound caps as placeholders + the bct-pooled fp
    # denominator), never a stale-guess-as-locked measured claim. The row
    # VALUES trace to that basis (the caps), and the lock annotation lives
    # in the thresholds.yaml provenance comments next to the rows — read the
    # raw text (load_thresholds strips comments) and assert the marker:
    assert rows[("flip-flop-rate", "a4")] == 0.10       # AC cap placeholder
    assert rows[("false-positive-rate", "a4")] == 0.05 # AC cap placeholder
    raw = (CONFIG / "thresholds.yaml").read_text(encoding="utf-8")
    assert raw.count("placeholder-locked") >= 2

def test_surfaced_rate_row_post_i1_semantics():
    cfg = load_thresholds(CONFIG / "thresholds.yaml")
    rows = _rows(cfg)
    a4 = rows[("surfaced-rate", "a4")]; a0 = rows[("surfaced-rate", "a0")]
    # round-2: the OLD assert (a0 == 0.0 only) was VACUOUS — it already held
    # against the pre-fix contradiction row {a4: 0.90, a0: 0.00} (the
    # no-store floor never moves), so it could not DETECT the post-I-1
    # correction. The discriminating meaning is the A4 expectation's
    # delta-direction/decidability: post-I-1 seed-mode semantics the row is
    # the mock-lane MEASURED basis (04-plan fixture matrix A4 0.92 — the
    # planted-population surfaced rate under the seed-mode split), strictly
    # ABOVE the no-store comparator AND strictly above the verbatim E2E-1.1
    # gate nominal — a row copied verbatim from the 90% AC number is an
    # undecidable boundary lock (any real-path variance crosses it silently).
    assert a0 == 0.0        # no-store arm comparator intact (floor)
    assert a4 > a0          # delta-direction: treatment > control
    assert a4 > 0.90        # seed-mode measured basis above the 90% AC
                            # nominal with margin — never the verbatim copy

def test_coverage_metric_key_canonical_no_not_in_cal():
    # a family payload stamped metric "coverage-subscore" resolves in
    # calibrate --print (no "NOT IN CAL TABLE") — loader-boundary unify
    ...

def test_determinism_real_relock_hash_roundtrip():
    from battery.report.calibrate import cal_table_hash
    t1 = load_thresholds(CONFIG / "thresholds.yaml")
    assert t1.cal_table_hash() == cal_table_hash(t1.cal_rows, t1.determinism_tolerances)
    dropped = ThresholdsConfig(cal_rows=t1.cal_rows)
    assert dropped.cal_table_hash() != t1.cal_table_hash()   # tolerance rows folded
```
**Step 7.2** — Run → FAIL (1.43× coverage row; surfaced-rate row still the verbatim 0.90 AC nominal — the a4 > 0.90 delta-direction assert fires; fp/flip rows absent or not placeholder-locked; probe metric split; hash round-trip unasserted for the re-lock).
**Step 7.3** — Re-lock rows (probe-measured basis where the probe measures them; post-I-1 semantic basis named in provenance comments otherwise — behavioral R1 surfacing legs are #2284 Task 9 executor-owned, recorded as TBD(EXPOSURE)-finalize at Task 8 like arms.yaml); the [cal] `ep-variance` row is #2291-OWNED (its calibration re-lock — referenced in provenance comments, NEVER invented or edited by this issue; n9); add the flip-flop-rate + false-positive-rate rows as PLACEHOLDER-LOCKED provisional rows (stated basis = the AC nominal caps ≤ 10%/≤ 5% as placeholders + the bct-pooled fp denominator; each row's provenance annotation names the Task-9 control-verdict measurement path it awaits — never stale-guess-as-locked; extends the PR #2341 round-4 deferral note); re-lock `coverage-subscore` consistent with ≥ 1.5× + a0 floor; re-lock the `surfaced-rate` a4 expectation to the post-I-1 seed-mode MEASURED basis (mock-lane planted-population surfaced rate — 0.92 per the 04-plan fixture matrix — above the 90% AC nominal, with a provenance comment naming the basis; the row stays consistent with the E2E-1.1 ≥ 90% gate target); update `r2_coverage` semantic + `delta_vs_control` floor; canonicalize the coverage metric key at the record/loader boundary (verify where the underscore spelling surfaces — probe `metric` attr, ProbeResult, calibrate measured keys — unify to `coverage-subscore`; the emit-registry log field stays underscore, documented as a log-internal namespace).
**Step 7.4** — Determinism measured re-lock (spend-gated, one shot): run the probe TWICE on 2 scenarios (same seed 7, real model) → per-metric |Δ| over the probe-emittable determinism metric set. Re-lock scope is exactly what the seed + probe legitimately support: **the usage/token rows the probe can actually emit (`total_tokens` per episode + the per-phase token rows) are re-confirmed ≤ a tolerance re-locked from the MEASURED two-run usage spread — a provisional real-path row, never the mock-lane 1e-6 transcript lock (real usage is not transcript-locked, so the seeded 1e-6 would fail honestly measured two-run usage deltas)**; the DERIVED/OBJECTIVE rows (`n_tool_calls`, `n_turns`, `outcome_*`, `re_derivations`) cannot be emitted by the #2292 scaffold probe AT ALL — 4-turn envelope, no event log, no outcome enum, no tool-event surface (they are harness `EpisodeResult`/`HarnessScorer`-derived; `HARNESS_METRIC_IDS` battery/runner/scorers.py) — so they STAY at their mock-lane seed values and the two-run probe records them as a SANITY PRINT only (never a re-lock basis; their real-path confirmation is #2284 Task 8 exposure over the event log; round-2 finding); the model-text/judged tolerance rows are NOT re-locked here — merged 04-plan E2E-7.1 reserves real-path model-text/judged re-lock to exposure-measured numbers (#2284 Task 8; the rows keep their `TBD(EXPOSURE)` markers; a 2-scenario×2-run probe cannot re-lock them), so they remain at the seeded values with the run-level nondeterminism fingerprint recorded, never bit-compared. These determinism runs are REAL spend and sit INSIDE the probe sub-cap (Task 3 `probe_cap_usd` — pre-authorized with Task 3 Step 3.7 + Task 4 Step 4.2 under the SAME sub-cap accumulator; one-shot discipline: never silently re-run, a second failure → STOP and surface to the owner). Tolerance rows folded into `cal_table_hash`; assert the hash round-trip (Step 7.1 test green). **Merge-order conditional (n2/n9, recorded):** #2284 Task 7 IS merged (verified at plan time) → this task re-locks OVER the seed. Had Task 7 not merged, this task would have landed the tolerance block + hash fold-in + re-lock as ONE reviewable change and #2284 Task 7 would have been edited to seed-only — recorded so executing-plans can detect drift. thresholds.yaml double-edit pin (n9): this issue lands seed/mechanics + PROVISIONAL rows; #2284 Task 8 performs the measured finalization over the SAME rows — edited once per phase, never a parallel edit (04-plan E2E-7.1 coordination, mirroring n1's arms.yaml pattern).
**Step 7.5** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_thresholds_relock.py tests/test_battery_config.py tests/test_battery_determinism.py tests/test_battery_probes.py tests/test_battery_report.py -v` → PASS; commit via `commit-workflow` (`chore(cal): thresholds re-locks — post-I-1 rows, fp/flip rows, R2 1.5× canonical, metric-key unify, determinism measured re-lock (#2292)`).

### Task 8: Parity protocol-hash producer migration — report.py emits `protocol_hash` + locked migration test + #1144 cross-dependency (coordination n3)

**Intent:** Indicator (6): #2284 Task 6 (MERGED) made `methodology_hashes` a 3-tuple + the parity CLI derive the protocol hash from the pinned arm — but the #1144 baseline-record PRODUCER (`tools/longmem_eval/report.py`, methodology block at 2256) still emits only the 2-tuple, so the real unchanged-check never SEES protocol deltas. This task ships the producer emission (additive + back-compatible), the locked migration test, and records the #1144 cross-dependency.
**Acceptance:** `tools/longmem_eval/report.py` methodology block gains `protocol_hash` (derived from the same protocol inputs the parity CLI uses: seed, reader-model pin + temperature, schema/event version, tool-surface ids — additive key, absent on old reports by construction); old 2-tuple baselines still compare on the reader-prompt + rubric hashes with the existing warn path (back-compat — existing `_match` at 2774 untouched for the 2-tuple); a locked migration test asserts (a) a 2-tuple baseline record matches with `protocol_unknown=True` (back-compat), (b) a protocol delta (model pin change, schema bump) trips `methodology_matched=False` on a 3-tuple baseline, (c) the producer emits the key (emission lock); cross-note comment posted on #1144 (file owner) so the baseline re-record lands with the producer already emitting (n3 — additive/back-compat, never an issue-body edit).
**Files:**
- Create: `tests/test_battery_parity_migration.py`
- Modify: `tools/longmem_eval/report.py` (methodology block producer emission ~2256; a small protocol-input resolution helper next to the existing hash computation), `battery/cli.py` (no change — CLI already derives + backfills; verify)

**Step 8.1** — RED migration tests:
```python
# tests/test_battery_parity_migration.py
"""#2292 Task 8 — report.py producer emits protocol_hash; migration locks."""
from __future__ import annotations
from pathlib import Path
import pytest
from battery.parity.runner import run_parity, protocol_hash, methodology_hashes

def test_two_tuple_baseline_backcompat_protocol_unknown():
    # run_parity derives the compared hashes from the REAL texts via _sha256
    # (a 16-hex digest of the string) — a literal "a"*16 baseline can never
    # equal sha256("a"*16)[:16], so base_matched=False forever and this RED
    # fails for the wrong reason. Derive the 2-tuple from the real hash,
    # exactly like test_protocol_delta_trips_three_tuple does with _sha16().
    rp = "reader-prompt-text"; jr = "rubric-id"
    bl = {"reader_prompt_hash": _sha16(rp), "judge_rubric_id_hash": _sha16(jr)}
    res = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                     rp, jr, bl, protocol="p"*64)
    assert res.methodology_matched and res.protocol_unknown

def test_protocol_delta_trips_three_tuple():
    rp = "reader-prompt-text"; jr = "rubric-id"
    base_proto = protocol_hash(seed=0, model={"model_id": "deepseek/deepseek-v4-flash",
                                              "temperature": 0.0},
                               event_schema="1.1", tool_surface=("file_nand",))
    new_proto  = protocol_hash(seed=0, model={"model_id": "deepseek/deepseek-v4-flash",
                                              "temperature": 0.7},   # temp change
                               event_schema="1.1", tool_surface=("file_nand",))
    _, _, ph_base = methodology_hashes(rp, jr, protocol=base_proto)
    bl = {"reader_prompt_hash": _sha16(rp), "judge_rubric_id_hash": _sha16(jr),
          "protocol_hash": base_proto}
    ok = run_parity("locomo", "locomo-v1", "a4", rp, jr, bl, protocol=base_proto)
    assert ok.methodology_matched and not ok.protocol_unknown
    trip = run_parity("locomo", "locomo-v1", "a4", rp, jr, bl, protocol=new_proto)
    assert not trip.methodology_matched        # protocol delta trips — the point

def test_report_producer_emits_protocol_hash():
    # invoke the report producer's methodology builder on a fixture run
    # dict — assert the emitted methodology dict carries protocol_hash, and
    # that an old-format report (no key) still loads + compares (back-compat).
    from tools.longmem_eval.report import build_methodology  # producer seam
    m = build_methodology(seed=0, reader_model="deepseek/deepseek-v4-flash",
                          temperature=0.0, event_schema="1.1",
                          reader_prompt_hash="a"*16,
                          judge_rubric_id_hash="b"*16)
    assert m["protocol_hash"] and len(m["protocol_hash"]) == 64
    # back-compat: reports without the key keep comparing on the 2-tuple
    old = {"reader_prompt_hash": "a"*16, "judge_rubric_id_hash": "b"*16}
    assert "protocol_hash" not in old


def _sha16(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
```
**Step 8.2** — Run → FAIL (producer 2-tuple only).
**Step 8.3** — Implement the additive producer emission in `report.py` (resolve protocol inputs from the same config surface the parity CLI uses — model pin/temp + event-schema + tool-surface; additive methodology key; back-compat preserved for absent keys).
**Step 8.4** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery_parity_migration.py tests/test_battery_parity_hash.py tests/test_battery_parity.py -v` → PASS.
**Step 8.5** — Post the #1144 cross-note comment (producer now emitting `protocol_hash`; baseline re-record should land with the key). Commit via `commit-workflow` (`feat(parity): report.py producer emits protocol_hash; locked migration test (#2292)`).

### Task 9: Doc amendments + full verification + PR + handoff

**Intent:** Indicators (7) + issue-body/coordination reconciliation: 04-plan §3 fixture matrix R2 row + E2E-1.1/§7 wording coherence + spec §R1–R5 anchor edits (docs/agent-reasoning-eval-battery.md R2 — de-graph-ify "what it measures" + pin the judged-subscore-only 1.5× gate with the a0=0 floor) + 05-decompose sibling rows amended; then full-suite verification + ONE PR + code review + handoff.
**Acceptance:** doc edits landed with no stale anchors: 04-plan.md line-77 fixture matrix R2 row re-labeled (mock-era "A4 0.51 | A2 0.50 | A0 0.50 → PARITY" is stale — a rule-consistent row shows the ratio-gate form or an amendment marker) + E2E-1.1 line-171 R2 AC wording states the canonical ratio ≥ 1.5× + a0=0 floor + judged-subscore-only; docs/agent-reasoning-eval-battery.md R2 spec anchors reflect the arm-neutral judged subscore (no "mitigations filed per support edge" graph-only language in the graded construct; mechanism-gate clause already excluded from Tier-3); 05-decompose.md gains/amends the sibling row for #2292 (decompose-gap) + #2284 rows consistent with this issue's ownership; stale-grep sweeps clean (no surviving 1.43×/800-guess claims without an amendment marker); full battery carve-out suite green; PR opened via commit-workflow (code-review gate auto); plan doc carries the clean `<!-- plan-review:` signature; issue #2292 → `planned`.
**Files:**
- Modify: `docs/epics/1402-eval-battery/04-plan.md` (§3 fixture matrix R2 row ~line 77; E2E-1.1 R2 wording ~line 171; E2E-7.1 assert wording ~line 271 if the determinism re-lock changed a value), `docs/agent-reasoning-eval-battery.md` (R2 spec block + AC-R2 row), `docs/epics/1402-eval-battery/05-decompose.md` (sibling rows)

**Step 9.1** — Doc edits per acceptance (each edit names the row it replaces + the amendment marker; no silent prose rewrites).
**Step 9.2** — Full suite: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_battery*.py -q` → PASS.
**Step 9.3** — Doc sweep: `grep -rn "1\.43\|800 tok/ep" docs/epics/1402-eval-battery/ docs/agent-reasoning-eval-battery.md` → only amendment-marked hits; stale §R1–R5 anchor greps clean.
**Step 9.4** — PR + plan-review signature; apply `planned` label to #2292 (per writing-plans handoff).
**Step 9.5** — Post the handoff note on #2284: closed deliverables (rubric JSON + ValidationRecord + pin + provisional measured budget + token_table_hash machinery) consumed at the Task-8 gate; Task 8's smoke FINALIZES the arms.yaml numbers over the SAME machinery — no parallel edit, no re-validation (the probe IS the pre-exposure validation run Task 8's precondition references); #1416 deps unchanged (its Blocks chain via #2284 v1+v2 + siblings). Post the indicator-4 wording recommendation on #2292 (comment only — never edit the issue body; coordination n4). Post the model-pin (a) + rubric-form (d) decision comments on #2292 (non-blocking, per the mandate).

---

## Review Changelog

(plan-review cycles logged here as they run — see below)

<!-- plan-review: cycles=0, status=running, version=2.3.0 -->
