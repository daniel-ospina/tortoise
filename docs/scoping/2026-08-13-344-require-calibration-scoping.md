---
title: "docs/scoping/2026-08-13-344-require-calibration-scoping.md"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---


<!-- issue-scoping: v5.1 double diamond + verify -->

# Issue #344 — Flip `require_calibration` default to True + migrate existing graphs

> **Run mode:** STREAMLINED (retry of a stalled run) — diamond phases inline (no sub-agent dispatch),
> verification gates via 2 parallel fresh-context verifiers (`task`), coherence check via substitute
> reviewer (qwen3.8-max 401-blocked). REST-only gh API.
> **Infra note:** `parallel_work_check` / `checkout_guard` tooling absent from agent-infra scripts —
> C1/C2 checkpoint skipped with note per run instructions.

## Confirmed Problem

`require_calibration=False` defaults on **both** the SDK surface (`tortoise/sdk.py:3288`) and the MCP
surface (`tortoise/mcp_server.py:917`) make #7478's EP calibration gate **opt-in** — uncalibrated EP
still runs silently on topology alone, violating #7478's stated target ("0 bare-EP runs. Every EP call
is calibrated or blocked"). The flip to `True` was **always the planned v2.0 breaking change**
(eldato#7478, planned-change #15: "Flip `require_calibration` default to `True` — v2.0 breaking
change") — issue #344 is that deferred follow-up. Shipping it requires three coupled deliverables:

1. **Flip both surfaces** (SDK + MCP) so the target is met for all explicit EP consumers, and
   **rewrite** the fail-open test (`tests/test_calibration.py:107`). ⚠️ Gate-verified correction: that
   test's fixture creates the point with `credibility="medium"` → `baseline_set=true` → the graph is
   ALREADY calibrated, so it passes unchanged under the flip and the plan must change the FIXTURE, not
   just the assertion: drop `credibility="medium"` (genuinely uncalibrated graph → flipped default
   raises `CalibrationError`), plus a companion assertion that a calibrated graph succeeds under the
   default. Also update the MCP `ToolDefinition` description + docstrings (default semantics + the
   `require_calibration=False` escape hatch visible to LLM consumers). Hardening (1 line, absorbed —
   it sits in the touched gate code and becomes user-facing under the flip): exclude `retracted`
   points from the gate's uncalibrated list (mirror `_hydrate_evidence`, `sdk.py:3381`).
2. **Make every internal EP caller calibration-safe.** The flip converts today's *silent uncalibrated
   runs* into tomorrow's *silent non-runs* where `CalibrationError` is swallowed:
   `session_continuity.py:54` (`except: pass` — fully silent), CLI decide (`__main__.py:2730`,
   try/except print), `graph-scripts/{decide,file_pricing_decision,decide_licensing}.py`
   (try/except print), and the SDK's own `approve_points` path (`sdk.py:2606`). Per the
   fail-noisily principle (ezyang 2026), internal surfaces must either calibrate, consciously
   opt out with `require_calibration=False`, or surface the error — never swallow it.
3. **Explicit, source-aware, idempotent migration for legacy uncalibrated graphs.** `_apply_source_
   inheritance` (#398, `sdk.py:3443`) already auto-calibrates **sourced** points at EP time, but
   **unsourced** legacy evidence points (e.g. the `tortoise-gtm-decision` graph's 28 points) have no
   lazy path — they block forever without a deterministic migration. The migration must backfill a
   **neutral Beta(1,1)** prior with provenance marker `baseline_source='legacy'`, **not** the issue
   body's stale "T4 = Beta(2,4)" (superseded by #398's recalibration — live T4 is `(1.1, 1.0)`,
   `tortoise/source_credibility.py:40`). **Sourced/unsourced split (pinned, gate-verified):** the
   `'legacy'` marker is applied ONLY to unsourced uncalibrated evidence points; sourced uncalibrated
   points are never touched by the migration — they stay inheritance-eligible and self-calibrate via
   `_apply_source_inheritance` at the next EP run. This is mandatory because any `baseline_set=true`
   non-`inherited` point is permanently frozen out of inheritance (`sdk.py:3461, 3522`) — a blanket
   migration would regress the #398 relief.

   **Target boundary (rescoped, gate-verified):** "0 silent uncalibrated EP runs" is scoped to the
   **explicit `compute_confidence` surface** (SDK + MCP + internal callers). Three additional
   un-gated EP surfaces exist and are NOT fixed by this issue (filed as follow-up): `dream`
   (`tortoise/dream.py:89-96`), `get_confidence` (`sdk.py:3433`), and `ingest.py:101/564` (direct
   `ep.run` with priors synthesized from stored `coalesce(n.confidence, 0.5)` — the exact silent-
   uncalibrated pattern the target seeks, unaffected by the default flip since it never calls
   `compute_confidence`).

### Why This Framing (vs alternatives)

- **Framing 1 (original, "flip + CLI")** — correct direction but incomplete: silently assumes internal
  callers survive the flip (they degrade silently) and assumes the issue's T4/Beta(2,4) migration
  semantics (stale post-#398; and bulk-asserting "unverified" on points where the user expressed
  nothing manufactures skepticism — the honest mapping is neutral, per #7478's own "omit credibility →
  neutral Beta(1,1)" design).
- **Framing 2 (root-cause: gate is a per-call flag, not a system invariant)** — confirmed as a real gap:
  `dream` (`tortoise/dream.py:89-96`) and `get_confidence` (`sdk.py:3433`) run EP **without** the gate
  and still write `n.confidence`. The flip alone cannot literally hit "0 silent uncalibrated EP runs"
  on those surfaces. Resolution: the target is scoped to the explicit `compute_confidence` surface
  (public API + MCP + internal callers); gating `dream` is out of scope (it only runs on dirty roots
  after writes and its evidence contract mirrors the SDK's — see Boundary) and is filed as a
  follow-up consideration, not absorbed.
- **Framing 3 (flag-rollout with opt-out window, dbt 3-phase)** — rejected for this stage (see
  Rejected Alternatives): the consumer base is small and internal; the flip was already announced in
  #7478; a flag adds invisible state + cleanup debt. "When it would have been better" documented.

### Assumptions

| Assumption | Status | Evidence / Falsification |
|---|---|---|
| Gate checks `baseline_set=true` on evidence kinds only (statement/observation/hypothesis); operators/assessments/diary excluded | validated | `sdk.py:3319-3333`, `test_non_evidence_kinds_ignored_by_gate` |
| Live T4 prior is Beta(1.1, 1.0), not the issue body's Beta(2,4) | validated | `tortoise/source_credibility.py:40`; #398 scoping doc priors `{(10,1),(5,1),(3,1),(2,1),(1.1,1)}` |
| `_apply_source_inheritance` auto-calibrates sourced points at EP time | validated | `sdk.py:3443+`; `test_source_inheritance` sets `baseline_set=true`, `baseline_source='inherited'` |
| Consumer base is small and internal (justifies big-bang flip without a separate opt-out release) | unverified | No consumer inventory run (hosted_api/auth/MCP consumers exist); flip is the pre-announced v2.0 change (#7478 item 15) and consumers are the same epistemic team — deprecation-window precedent (PEP 387/skpro) waived with this documented rationale |
| Points with no baseline are ALWAYS inheritance-eligible; `baseline_set=true` non-`inherited` (incl. legacy) are frozen | validated | `sdk.py:3461-3464` (2x2 mapping), `_apply_source_inheritance` WHERE clause `sdk.py:3522` |
| `_hydrate_evidence` only loads rows with `ep_alpha IS NOT NULL` — a migration must set alpha/beta, not just the flag | validated | `sdk.py:3381` |
| Internal callers swallow CalibrationError | validated | `session_continuity.py:54` (`except: pass`); try/except in `__main__.py:2730`, `graph-scripts/*.py` |
| #7478 explicitly deferred the flip as v2.0 breaking change | validated | eldato#7478 planned-change #15 |
| GTM graph's 28 evidence points have no `extractedFrom`→Source edges | unverified | no live graph access locally (repo `tortoise.db` empty; graph is hosted) — migration must be generic either way |
| MCP default must flip in lockstep with SDK | validated | target is "0 silent uncalibrated EP runs" — MCP is the agent-facing surface (`tool_registry.py:194`) |

### Boundary & Stakeholders

- **Out of scope:** gating the `dream`/`get_confidence` internal EP surfaces; schema changes
  (none needed — reuses `baseline_set`, `ep_alpha`, `ep_beta`, `baseline_source`, `credibilityTier`);
  four annotation dims on operators (#7478 deferred item); `tortoise-decide` skill edits
  (calibrate gate already exists per #7478); auditing live hosted graphs.
- **Affected but unmentioned:** MCP/agent consumers (hosted API + tool_registry "memory" team);
  `session_continuity` session-end confidence; the CLI `decide` command; `graph-scripts/`
  decision scripts; ~15 test files calling `compute_confidence()` with no calibration.

### Falsification Check

The confirmed problem is wrong if: (a) the #7478 target was never intended to cover the MCP
surface (contradicted by this issue's own indicator-1/2 wording listing `mcp_server.py:917` and by
eldato#7478's target "Every EP call is calibrated or blocked"); (b) existing graphs are already fully
calibrated so no migration is needed (contradicted by issue evidence — GTM graph `baseline_set=false`
— and the absence of any migration tooling); (c) flipping the default breaks zero internal callers
(contradicted by the caller scan above). (Gate note: conditions (b)/(c) were already settled by
diverge evidence — genuinely falsifying conditions would need to target an undiscovered surface, e.g.
a hole in the evidence-kinds gate filter.)

### Confidence (0-100)

**82** — all codebase claims code-verified with line refs; residual uncertainty: live-graph composition
(GTM sourced-ness), MCP consumer behavior on hosted graphs, exact test-fixture breakage count.

---

## Phase 1.5 — External Research

### Axis Research

**Axis ratings:** Architecture = medium (default flip + backfill tooling pattern);
Ontology = low (no schema change — but the backfill-prior semantics are ontology-adjacent, covered
under pitfalls); UX = low (no UI); Library-deps = none (no new third-party deps).

**Codebase-first precedent scan (per axis):** backfill/migration precedents exist in-repo —
`backfill_v25()` (`sdk.py:7286`, dry-run + report dict), `backfill_about_entities()` (`sdk.py:2694`,
idempotent MERGE), `_cmd_backfill` CLI (`__main__.py:1733`), `migrate-db` subparser
(`__main__.py:3145`), MCP admin wrapper `tortoise_backfill_v25` (`mcp_server.py:1837`), migration
doc pattern (`docs/migrations/id-normalization-plan.md`). 3+ precedents → queries can be light.

**Queries (4, exa MCP) — per-framing provenance:**

| # | Query (axis) | Framing informed | Finding | Provenance (canonical / competitor-precedent / pitfalls) |
|---|---|---|---|---|
| 1 | Breaking-default flips | F1 (original) + F2 (root cause) | A default change is a breaking change: requires deprecation notice, version-appropriate release, changelog; MONAI `deprecated_arg_default` pattern skips warning when the arg is passed explicitly. Pitfall: silent breaks must be turned noisy — "endeavor to fail noisily and as quickly as possible" — with a quick-revert circuit-breaker/flag. | canonical: PEP 387, Google OSS Library Breaking Change Policy, skpro developer guide deprecation policy; pitfalls: ezyang "Silent BC Breaking Changes" (2026-03) |
| 2 | Idempotent backfill on RedisGraph/FalkorDB | F3 (legacy semantics) | RedisGraph has **no unique constraints**; idempotency via `MERGE ON CREATE` or WHERE-guarded `SET`; bulk loads commit incrementally → must be resumable/idempotent. Our migration uses WHERE-guarded `SET` (idempotent by construction) + dry-run + count report — matches `backfill_v25` precedent. | pitfalls: RedisGraph docs / redisgraph-bulk-loader README, SO 61949043 |
| 3 | Calibration priors in KG/EP | F3 (legacy semantics) | Beta(1,1) uniform is the standard noninformative conjugate prior; "there is no such thing as an uninformative prior" — the default prior always carries information; Beta(1/3,1/3) "neutral" preferred for rare events. Pitfall: assigning a skeptical prior where the user expressed nothing manufactures skepticism (grounding-doc anti-pattern: "Boosting single-source concepts by source type would manufacture certainty"). | canonical: Kerman 2011 (10.1214/11-ejs648); pitfalls: knowledge-graph-system grounding doc, stats.stackexchange 297901 |
| 4 | Rollout strategy for breaking behavior changes | F4 (flag rollout, rejected) | dbt behavior-change flags: Introduced (off default) → Mature (on default, opt-out + deprecation warnings) → Removed; feature-flag guidance: every flag needs a default, kill-switch, expiry — "flags are a rollout tool, not a permanent architecture." | competitor-precedent: dbt behavior-changes docs; pitfalls: how2.sh "How to Implement Feature Flags Safely in Production" (2026-02) |

**Findings feed the solution:** neutral-prior backfill (q3), fail-noisily internal-caller discipline
(q1), idempotent WHERE-guarded migration + dry-run (q2), big-bang flip justified for small internal
consumer base with documented deprecation waiver (q4).

---

## Verification Gates

### problem-verify: 1 cycle, PASS (2 verifiers, NO P0/P1; P2s incorporated)
- Verifier A: P0=0, P1=0, P2=4 (test blast radius un-inventoried; sourced-freeze tension unpinned;
  rescoped target not restated; framing-4 rejection on untagged assumption), P3=2, P4=1.
- Verifier B: P0=0, P1=0, P2=2 (ingest.py un-gated EP surface omitted; sourced-freeze tension
  unpinned), P3=2, P4=1.
- Controller action: no P0/P1 → no re-dispatch. Incorporated all P2s: pinned sourced/unsourced
  migration split; restated target boundary + fixed falsification (a); added test-call-site
  inventory to plan; tagged consumer-base assumption [unverified] + deprecation waiver; added
  `ingest.py` surface + `tortoise_dream` MCP tool to wiring table; added confidence-preserving
  migration to rejected alternatives; restructured Axis Research with per-framing provenance.

### solution-verify: 2 cycles, PASS (2 verifiers × 2 rounds; P1 fixed in round 2; max re-dispatch cap respected)
- **Round 1:** A: P0=0, P1=1 (test fixture already calibrated — `credibility="medium"` → `baseline_set=true`;
  "invert assertion" alone would create a failing test), P3=1, P4=1. B: P0=0, P1=0, P3=3
  (retracted exclusion; AC scoping for untiered-sourced; stale `context=ctx` TypeError), P4=1.
  Controller: FIXED P1 (fixture rewrite + companion assertion + ToolDefinition description +
  retracted hardening); incorporated P3s/P4; re-dispatched.
- **Round 2:** A: P0=0, P1=1 (fix content correct but landed in narrative sections, not the executable
  Proposed-solution Step 1; wiring-table tool_registry row contradicted fix), P2=1, P3=2, P4=1.
  B: P0=0, P1=0, P3=2 (`calibrate_summary` must RETURN `n.status` for retracted filter; migration
  should `_mark_dirty`), P4=2. Gate passes (B clean; A's P1 = placement defect, content verified
  correct by B).
- Controller (max re-dispatch cap): applied deterministic fixes — Proposed-solution Step 1 rewritten;
  tool_registry wiring row updated; `n.status` RETURN dependency noted; `_mark_dirty` added;
  `untiered_sourced` report bucket; Extras section added. Documented per streamlined-mode escape hatch.
  Full detail in Review Cycle Log.

---

## Solution Diamond

**Diverge — 3 distinct approaches:**

1. **Approach A — Big-bang flip + bulk T4 CLI** (issue's Option D as written): flip both surfaces,
   `tortoise migrate-calibration` bulk-sets `baseline_set=true` + T4 prior on ALL uncalibrated
   evidence points. Risks: stale prior semantics (T4=(1.1,1.0), not the issue's (2,4)); manufactures
   "unverified" on points where the user expressed nothing; blanket marking freezes sourced points
   out of inheritance (#398 regression).
2. **Approach B — Source-aware, provenance-marked neutral migration** (chosen): flip both surfaces;
   `migrate_calibration()` SDK method + CLI; 'legacy' marker ONLY on unsourced uncalibrated evidence
   points; sourced points untouched (self-calibrate via inheritance); WHERE-guarded idempotency;
   dry-run report; `calibrate_summary` provenance note; internal callers fail-noisily.
3. **Approach C — Flag-rollout with opt-out window** (dbt 3-phase): keep default False one release,
   add `TORTOISE_REQUIRE_CALIBRATION` flag defaulting True next release, deprecation warnings.
   Risks: invisible state + cleanup debt; delayed target; disproportionate for internal base.

**Converge:** Approach B chosen — quality over convenience: it handles the inheritance-freeze edge
case, the stale-prior correction, and silent-degradation — the outcomes that matter — at the cost of
more moving parts than A. C documented as "when it would have been better" (external consumers).

## Plan

### Problem statement

Make EP calibration **fail-closed by default** on all explicit `compute_confidence` surfaces
(SDK + MCP), keep every internal EP consumer from silently degrading, and give legacy uncalibrated
graphs a deterministic, source-aware, idempotent migration path.

### Proposed solution

1. **Flip defaults.** `sdk.py:3288` and `mcp_server.py:917`: `require_calibration: bool = True`.
   Update docstrings + MCP tool docstring + `tool_registry.py:194` ToolDefinition description
   (default semantics + `require_calibration=False` escape hatch visible to LLM consumers).
   **Rewrite** `test_require_calibration_default` (`tests/test_calibration.py:107`) — the fixture is
   changed, not just the assertion: drop `credibility="medium"` so the graph is genuinely
   uncalibrated (`baseline_set=false`) and the flipped default raises `CalibrationError`; companion
   assertion that a calibrated graph (`credibility="gold"`) succeeds under the default.
   Absorbed hardening: gate's uncalibrated list excludes `status='retracted'` — requires adding
   `n.status` to `calibrate_summary`'s RETURN (`sdk.py:3647`, additive — public MCP surface
   `tortoise_calibrate_summary` is backward-compatible), then filter on it (mirrors
   `_hydrate_evidence` `sdk.py:3381`).
2. **`migrate_calibration()` SDK method + `tortoise migrate-calibration` CLI.** Mirrors
   `backfill_v25` (`sdk.py:7286`): `migrate_calibration(dry_run: bool = False) -> dict` report
   `{dry_run, actions, uncalibrated, migrated, skipped_sourced, already_calibrated}`.
   Semantics (source-aware, idempotent):
   - **Skip** points already calibrated (`baseline_set = true`).
   - **Leave sourced points eligible for inheritance:** uncalibrated points with
     `extractedFrom`→Source where the source has an effective tier are counted in
     `skipped_sourced` and NOT backfilled (they self-calibrate via `_apply_source_inheritance`
     at the next EP run; backfilling would freeze them out of inheritance — `sdk.py:3461`).
   - **Backfill** remaining uncalibrated **evidence** kinds (statement/observation/hypothesis;
     exclude operators/assessments — mirror gate filter `sdk.py:3322`; exclude `status='retracted'`
     — mirror `_hydrate_evidence` `sdk.py:3381`): `ep_alpha=1, ep_beta=1,
     baseline_set=true, baseline_source='legacy'` (new provenance value; `calibrate_summary`
     gains a note for `baseline_source='legacy'` so "calibrated" ≠ "evidence-backed" is visible), and
     **clear stale `posterior_alpha/posterior_beta`** (pre-flip degenerate EP runs wrote posteriors
     on uncalibrated points; mirror `set_point_baseline`'s posterior-clear `sdk.py:3405` — keeps
     "neutral prior" honest on every read path incl. the follow-up surfaces).
     WHERE-guarded (`baseline_set IS NOT true`) → idempotent; `dry_run` reports counts only.
     After backfill, `_mark_dirty(migrated_ids)` (batch variant — mirrors `set_point_baseline`
     `sdk.py:3413`) so lazy-consistency read paths recompute rather than serve stale `n.confidence`.
   - **Report** gains an `untiered_sourced` bucket (uncalibrated sourced points whose sources have no
     effective tier — not backfilled, inherit once tiered; expected fail-noisy residual per AC-7,
     with pointer to `calibrate_summary`'s `set_source_tier` guidance).
   - CLI: `tortoise migrate-calibration --db <uri> [--dry-run]` — mirrors `_cmd_backfill`
     (`__main__.py:1733`); requires `--db`. MCP admin tool wrapper optional (follow
     `tortoise_backfill_v25` pattern) — low priority.
3. **Internal-caller discipline (fail noisily, never swallow).** Each internal caller either
   calibrates or passes `require_calibration=False` **explicitly with a comment/rationale**:
   - `session_continuity.py:54`: replace `except: pass` — pass `require_calibration=False`
     explicitly (session findings are uncalibrated by design; confidence update is best-effort)
     or catch and print; never silent.
   - `__main__.py:2730/2732` (decide CLI) + `graph-scripts/{decide,file_pricing_decision,
     decide_licensing}.py`: explicit `require_calibration=False` with comment, OR run
     `calibrate_summary` first; keep the try/except but make the message actionable. ⚠️ Adjacent-bug
     note: `decide.py:230` and `decide_licensing.py:155` call `compute_confidence(context=ctx)` —
     no `context` param exists (`sdk.py:3286-3290`), so these raise TypeError today (swallowed by
     the surrounding try/except). The internal-caller edit must ALSO remove the stale `context=ctx`
     kwarg; filed as separate issue (see Extras).
   - `sdk.py:2606` (approve_points internal EP): pass `require_calibration=False` explicitly —
     its strong evidence prior `{decision_id: (10,1)}` is the calibration for that subgraph;
     comment documents the choice.
4. **Test migration (explicit inventory).** Inventory of default `compute_confidence()` call sites in
   tests + dev tooling (file set verified via rg; counts are grep -c incl. non-SDK mentions —
   SDK-surface call sites enumerated precisely at implementation): test_sdk_ep 14, test_decide 11 (zero
   calibration setup — all call sites raise under the flip), test_projection 11 (mostly
   `proj._compute_confidence`, different surface — check each), ep_e2e_patterns 9,
   test_ep_sources 7 (calibrated via sources — likely survives), test_ep_selector 6, test_dream 5,
   test_ep_draft_filter 5, test_event_provenance 3, test_ep_calibration 3, ep_diagnostic 5,
   test_extractor_priors 3, test_recall_state 1, test_directional_impl 1,
   test_source_inheritance_own 1. **Dev tooling (coherence-flagged):** `validation/` has 24 calls
   across 6 files (validate_tortoise_ep 12, svbp_gate3 7, test_docker_ep 2, svbp_gate2 1,
   svbp_gate4 1, compare_algorithms 1) — fail-noisy under the flip is arguably correct for dev
   tooling, but inventory them and decide per file (calibrate fixtures or document the raise as
   intended) so "full suite green" has a defined boundary. Remediation per call site: add
   `credibility=` to fixtures or call
   `set_point_baseline` (preferred — keeps the gate active), OR pass `require_calibration=False`
   explicitly where the test intentionally exercises uncalibrated EP. New tests for
   `migrate_calibration`:
   - dry-run mutates nothing, reports counts;
   - idempotent (2nd run → 0 migrated);
   - source-aware (sourced T1 point → not backfilled, inherits after EP run);
   - provenance (`baseline_source='legacy'` + `calibrate_summary` note);
   - unsourced legacy point → Beta(1,1) + gate passes after migration.
5. **Docs.** `docs/migrations/` plan doc (id-normalization-plan.md pattern: status, backup,
   audit, dry-run) + release note; ONTOLOGY/`baseline_source` vocabulary note for `'legacy'` value.

### Acceptance criteria

- [ ] `compute_confidence()` (no args) on an uncalibrated graph raises `CalibrationError` (SDK + MCP default flipped)
- [ ] `tortoise_compute_confidence` MCP tool without `require_calibration` raises on uncalibrated graph
- [ ] `test_require_calibration_default` rewritten: fixture WITHOUT `credibility=` (genuinely uncalibrated) expects `CalibrationError`; companion assertion that a calibrated graph succeeds under the default
- [ ] `migrate_calibration(dry_run=True)` reports counts, mutates nothing
- [ ] `migrate_calibration()` marks unsourced uncalibrated evidence points Beta(1,1) + `baseline_set=true` + `baseline_source='legacy'`, clears stale posteriors, skips `retracted`; second run reports 0; already-calibrated points untouched
- [ ] Sourced uncalibrated points are NOT frozen (still inheritance-eligible; inherit after EP run)
- [ ] Post-migration gate passes on graphs whose sourced points have tiered sources; untiered-sourced points raise with actionable `calibrate_summary` guidance (expected fail-noisy state, not an AC violation)
- [ ] Full suite green (no test relies on silent fail-open)
- [ ] No internal caller silently swallows `CalibrationError` (`session_continuity` included); stale `context=ctx` kwarg removed from graph-scripts
- [ ] `calibrate_summary` surfaces `baseline_source='legacy'` provenance note
- [ ] Docs: migration plan + release note; `baseline_source` vocabulary updated

### Runtime prerequisites

- FalkorDB write access on target graph (`--db`); backup before real migration
  (backup tool precedent: `tortoise backup`).
- No new third-party dependencies.

---

## Clarifications

No clarifying-questions pass run (streamlined mode; issue O/I/T unambiguous). Open questions for the
human (recommendations applied per research-before-ask, evidence >80%):
1. **Backfill prior** — neutral Beta(1,1) + `'legacy'` marker recommended (evidence: #7478's own
   omitted→neutral design; Kerman 2011; manufacturing-skepticism pitfall). The issue body's Beta(2,4)
   is stale.
2. **MCP flip in lockstep** — yes (target requires it). Note: agent MCP consumers on legacy graphs
   will start raising until they migrate — intended fail-closed behavior.
3. **Internal callers** — conscious explicit `require_calibration=False` + no silent swallow.

---

## Rejected Alternatives

| Approach | Why rejected | When it WOULD have been better |
|---|---|---|
| **Option A — bulk T4 on ALL uncalibrated points** (issue body) | Stale prior semantics (T4=(1.1,1.0) not (2,4)); manufactures "unverified" on points where user expressed nothing; freezes sourced points out of inheritance; silent shift in EP results | If #398 had never recalibrated priors AND every legacy point was genuinely untiered AND sourced graphs didn't exist — then a single bulk assertion would be simpler |
| **Option C — no migration, error + manual calibrate** | Unsourced legacy graphs (GTM's 28 points) block forever; per-point manual work for whole graphs; the issue's own indicator-2 asks for migration | If graphs were small/hand-maintained and calibration was rare — the error message + `calibrate_summary` guidance is already the manual path |
| **Option D-hybrid CLI (issue recommendation)** | Adopted in spirit (CLI + explicit migration) but corrected: source-aware skip + neutral prior + provenance marker instead of blind T4 bulk | — (this is the chosen approach's baseline) |
| **Confidence-preserving migration** (map each point's stored `n.confidence` → Beta via `confidence_to_prior`, `ep.py:895`) | Rejected by omission-corrected: stored confidence was written by the very uncalibrated EP the flip targets — backfilling it bakes degenerate connectedness-counter results into baselines, freezing them as explicit; neutral Beta(1,1) + 'legacy' marker preserves no false signal. Noted for graphs where stored confidence IS user-authored (then `set_point_baseline` is the correct tool, not migration) | If a legacy graph's stored `n.confidence` values were intentionally curated rather than EP-written — then confidence-preserving migration would be the honest choice |
| **Flag-rollout with opt-out window (dbt 3-phase)** | Small internal consumer base; flip was already announced (#7478); flags = invisible state + cleanup debt (how2.sh: default + kill-switch + expiry required); big-bang with migration is proportionate | If there were many external/hosted consumers on live graphs where silent breakage would cause P0 — then a Mature-phase opt-out window before the flip would de-risk |
| **Gate the dream/get_confidence surfaces too** | Out of scope: dream runs only on dirty roots post-write and shares the SDK evidence contract; gating it would break lazy-consistency (#85) semantics; filed as follow-up consideration | If the target "0 silent uncalibrated EP runs" is later interpreted literally across all EP surfaces, not just explicit compute calls |

---

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| `sdk.py:3288` `compute_confidence` default | SDK API | Plan step 1 + AC-1 | ✅ |
| `mcp_server.py:917` `tortoise_compute_confidence` default | MCP API | Plan step 1 + AC-2 | ✅ |
| `tool_registry.py:194` tool wiring | MCP registry | Update ToolDefinition description (default semantics + escape hatch) — plan step 1 | ✅ |
| `tests/test_calibration.py:107` fail-open test | Test | Plan step 1 (invert) + AC-6 | ✅ |
| ~15 test files calling `compute_confidence()` w/o calibration | Test | Plan step 4 + AC-6 | ✅ |
| `session_continuity.py:54` silent swallow | Internal caller | Plan step 3 + AC-8 | ✅ |
| `__main__.py:2730` decide CLI | Internal caller | Plan step 3 | ✅ |
| `graph-scripts/{decide,file_pricing_decision,decide_licensing}.py` | Internal caller | Plan step 3 | ✅ |
| `sdk.py:2606` approve_points internal EP | Internal caller | Plan step 3 | ✅ |
| Migration method + CLI (`migrate_calibration`, `__main__.py`) | Data migration | Plan step 2 + AC-3/4/5 | ✅ |
| `_apply_source_inheritance` interplay (freeze risk) | Cross-cutting | Plan step 2 (skip sourced) + AC-5 | ✅ |
| `calibrate_summary` provenance note (`baseline_source='legacy'`) | SDK surface | Plan step 2 | ✅ |
| `docs/migrations/` + release note + ONTOLOGY vocab | Docs | Plan step 5 + AC-10 | ✅ |
| `dream` (`dream.py:89-96`) + `get_confidence` (`sdk.py:3433`) uncalibrated EP | Cross-cutting | Out of scope — follow-up consideration (filed) | ⚠️ documented |
| `ingest.py:101/564` direct `ep.run` (priors from stored confidence) | Cross-cutting | Out of scope — never calls `compute_confidence`, unaffected by flip; follow-up consideration (filed) | ⚠️ documented |
| `tortoise_dream` MCP tool (`mcp_server.py:956`) ungated EP write | MCP surface | Out of scope (dream surface); note: gate runs BEFORE dream in `compute_confidence` (`sdk.py:3319` before `:3347`) so no uncalibrated confidence write precedes a raise on the gated path | ⚠️ documented |

**<HARD-GATE>** — All touch points covered; the only ⚠️ is a documented scope boundary (dream surface),
not a gap in this issue's delivery.

---

## Review Cycle Log

### problem-verify — Cycle 1 (PASS, no re-dispatch)
- Verifier A: P0=0, P1=0, P2=4, P3=2, P4=1 — verified every code citation against working tree;
  confirmed T4=(1.1,1.0) correction and inheritance-freeze concern; flagged test blast radius,
  sourced-freeze tension, target not restated, untagged consumer-base assumption.
- Verifier B: P0=0, P1=0, P2=2, P3=2, P4=1 — independently verified all line refs; found
  `ingest.py:101/564` third un-gated EP surface; flagged sourced-freeze tension; suggested
  confidence-preserving migration as omitted alternative.
- Controller: incorporated all P2s (see gate section). Gate passes.

### solution-verify: 2 cycles, PASS (2 verifiers × 2 rounds; P1 fixed in round 2; max re-dispatch used)
- **Round 1:** Verifier A: P0=0, P1=1 (test fixture is already calibrated — `credibility="medium"` →
  `baseline_set=true`; "invert assertion" would create a failing test), P3=1, P4=1. Verifier B:
  P0=0, P1=0, P3=3 (retracted exclusion; AC scoping for untiered-sourced; stale `context=ctx`
  TypeError), P4=1. Controller: FIXED the P1 (fixture rewrite + companion assertion + ToolDefinition
  description + retracted hardening); incorporated all P3s (retracted WHERE in migration; posterior
  clearing; AC refinement; context kwarg removal note) + P4. Re-dispatched both.
- **Round 2:** Verifier A: P0=0, P1=1 (fix content correct but landed in Confirmed Problem + AC-3,
  not the executable Proposed-solution Step 1; wiring table tool_registry row contradicted fix),
  P2=1, P3=2, P4=1. Verifier B: P0=0, P1=0, P3=2 (`calibrate_summary` must RETURN `n.status` for
  retracted filter to work; migration should `_mark_dirty`), P4=2. Gate passes (B clean); A's P1 is
  a section-placement defect with content independently verified correct by B.
- Controller (max re-dispatch cap reached): applied deterministic fixes — Proposed-solution Step 1
  rewritten to match AC-3; wiring table tool_registry row updated; `calibrate_summary` `n.status`
  RETURN dependency noted in Step 1; `_mark_dirty(migrated_ids)` added to backfill; report
  `untiered_sourced` bucket added; test-inventory counting method noted; Extras section added with
  filed issue numbers. Both verifiers confirmed the underlying fix content is correct → no third
  dispatch; documented per streamlined-mode escape hatch.
### [QWEN-GATE] coherence — substitute reviewer (qwen3.8-max 401-blocked → deepseek-v4-flash)
P0=0, P1=0, P2=5 — coherence HOLDS. P2s incorporated: live-graph dry-run validation gate (plan
step 2); #1156 scope widening comment; `validation/` dev-tooling inventory (plan step 4);
sourced-self-calibration wording corrected in Confirmed Problem; `approve_points` opt-out rationale
corrected (gate is graph-wide). No re-run per QWEN-GATE P2 policy. Full detail in Review Cycle Log.

---

## Extras (filed during scoping — not absorbed)

- **#1156** — stale `context=ctx` kwargs in graph-scripts (`decide.py:230`,
  `decide_licensing.py:155` on `compute_confidence`, `decide_licensing.py:146` on
  `create_operator`) — API has no `context` param → TypeError swallowed by try/except → EP
  silently never runs / operators silently never created (pre-existing). Adjacent bug; the
  internal-caller step touches these lines and must remove the stale kwarg (coupled edit, tracked
  separately).
- **#1157** — un-gated EP surfaces: `dream` (`dream.py:89-96`), `get_confidence`
  (`sdk.py:3433`), `ingest.py:101/564` — run EP without the calibration gate; #7478 target
  "0 silent uncalibrated EP runs" formally unmet on these surfaces; out of scope for #344
  (target boundary = explicit `compute_confidence` surface).

---

## Complexity

| Domain | Rating | Rationale |
|---|---|---|
| Engineering | standard | 1-line ×2 default flip + migration method (~40 lines) + CLI (~15) + internal-caller updates + ~15 test files — small code, broad test surface |
| Ontology | low | No schema change; one new vocabulary value (`baseline_source='legacy'`) + doc note |
| Architecture | medium | Default-flip interacts with inheritance 2x2 mapping, MCP surface, and internal EP consumers — wiring-sensitive |
| UX | low | No UI; error-message wording is the only user-facing surface |
| Library-deps | none | No new third-party deps |
| Research | low | Capped 4-query external pass (done); codebase-first precedents strong |
| Capability | low | No skill changes needed (calibrate gate already in tortoise-decide per #7478) |
