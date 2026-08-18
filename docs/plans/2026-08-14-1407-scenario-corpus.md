<!-- research-path: docs/epics/1402-eval-battery/02-research-brief.md + docs/plans/2026-08-14-1407-scope.md -->

# Scenario Corpus v1 — Implementation Plan (#1407)

> **For Pi:** implement this plan task-by-task. Scope (issue-scoping) passed 3 verification cycles: `docs/plans/2026-08-14-1407-scope.md`. Plan passed 2 verifier cycles; this is the final revision.

**Goal:** Ship the sealed, deterministic scenario corpus v1 — single owner of ALL scenario content for the Agent-Reasoning Eval Battery (epic #1402) — with 12 authored packs (134 scenarios), gold answers sealed in a gitignored store with a reader-path guard, and train/waves/held-out splits pinned at authoring time.

**Team:** epistemic-team
**Architecture:** `battery/config/corpus.yaml` (authored truth incl. golds) → `battery/config/validate.py` (shared validators — used by the builder AND per-pack authoring checkpoints) → `battery/config/build_corpus.py` (deterministic builder: validate-with-gold → recursive strip → post-strip proof → digest → emit committed reader-safe `corpus.json` + gitignored `.gold_store/golds.json`) → `battery/config/corpus_loader.py` (reader-safe `load_corpus`/`Corpus.filter`, fail-closed `GoldStore`, `verify_seal(corpus, store)`, `assert_no_gold`, `render_reader_prompt`). Zero new third-party deps (pyyaml already a project dependency). Schema enums are constants in `battery/config/schema.py` — referenced by name everywhere, never literal strings (epic 04-plan §6 discipline). **Invocation convention (pinned):** all battery entry points run as `uv run python -m battery.config.build_corpus` from the repo root (package imports, repo-root sys.path — the `-m` form; script-path invocation is NOT used).

### Pattern Research

> **Findings date:** 2026-08-14
> **Gate skipped: plan touches zero third-party deps** — builder/loader/validators use stdlib (`json`, `hashlib`, `pathlib`) + `pyyaml>=6.0` (already a project dependency, verified in pyproject.toml `dependencies`; CI installs it via `pip install -e '.[test,embeddings]'`). No new library, no API surface to verify. Design patterns (JSONL/JSON scenario records with evaluator-only golds; τ-bench-style end-state golds; split-at-creation-time contamination control; held-out ≥10–20%; canonical serialization for determinism) are documented with sources in the scope's `### Axis Research` and re-verified against in-repo precedent `tools/longmem_eval/` (reader sees context only, judge sees gold; `has_answer` evidence stamps; `judge.py` MockJudge matching: `answer.strip().lower() in hypothesis.lower()` — the normalization precedent for claim-placement bindings).

### Integration Surface Map

Per test-design #1404 (epic 03-scope.md S1–S8). Surfaces touched by THIS issue: **S5** (benchmark data — corpus.json pinned + sealed golds) and **S8** (config — scenario JSON schema). No DB, no external services, no auth.

| Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---|---|---|---|---|
| S5 — corpus.json (committed, pinned) | Config/data file | In | Unit + determinism | scenario JSON schema (plan §4); manifest `{corpus_version, content_sha256, golds_sha256, pack_counts, split_counts, family_coverage}`; version/sha recorded in run_artifact by downstream (#1406) | version drift; content drift (determinism test catches); gold leakage (guard tests) |
| S5 — sealed gold store `.gold_store/golds.json` (gitignored) | Data file | In | Unit | store dict digest == manifest `golds_sha256`; GoldStore fails closed (SealMissingError/SealMismatchError/StoreEntryMissingError); reader path never exposes golds | missing store on fresh clone; stale store after gold edit; gold in reader prompt (load+render guards) |
| S8 — corpus.yaml schema + content invariants | Config | In | Unit | enums from schema.py; k==5 pin; claim-placement bindings (normalized, first-appearance); gold no-substring rule; graph_script edge list; D4 sub-schema; PACK_SPLITS exact distribution; bijective controls; duplicate-key-free YAML | invalid enums; dup ids; claim not at injection turn; gold colliding with prompt text; split drift |

**Bug Pattern Flags:** gold leakage into reader (S5 — guarded at load AND render, recursive, exact-key equality); corpus drift (S7 — byte-identical rebuild test across processes); schema/enum drift (S8 — constants, not literals); silent authoring errors (content invariants machine-checked at build AND at per-pack checkpoints, not left to discipline).

### Verification Plan

Domain: code+config (S5/S8). Complexity: standard. Test layers: **unit only** — no integration surfaces outside the repo; no UX surface; content domain covered by schema/content tests. Verification = `uv run pytest tests/test_battery_corpus.py -v` + full `uv run pytest tests/ -v` regression. **CI wiring (this issue, required):** register `test_battery_corpus.py` in `config/ci-surfaces.yml` (new `battery` surface + `core` entry) AND `tools/ci_selection.py` `SOURCE_PATTERNS` (add `"battery/" → "battery"` — REQUIRED, no "full matrix fallback" hedge: an unmapped source silently runs the full matrix on every battery PR) AND `.github/workflows/python-ci.yml` matrix half-a `files += test_battery_corpus`. Verify `uv run python tools/ci_selection.py --integrity` exits 0. `build_corpus.py --check` (committed corpus.json vs fresh rebuild) wires into the same CI job when the battery CI workflow lands (#1406+).

**Digest conventions (pinned — file bytes are NEVER hashed):** `canonical_json(obj)` = `json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")` (sorted keys — no sets anywhere; every manifest aggregate is a sorted list). `golds_sha256 = sha256(canonical_json(store_dict))` where `store_dict = {scenario_id: gold}` sorted by id — IDENTICAL to `GoldStore.digest()` (same dict basis, same serializer). `content_sha256 = sha256(canonical_json(reader_safe_scenarios_array))` (sorted by id). The indented files on disk are display format only. Recipe documented in `battery/config/README.md`; downstream (#1406) records the same digests.

**Matching contract (pinned — used by claim-placement bindings, gold no-substring rule, hostile↔turns binding, render-guard tests):** `normalize(s) = casefold + collapse all whitespace`; matched as a **word-boundary substring of the full phrase** (never per-word, never raw substring — "sky" must not match "skydiving"). Precedent: `tools/longmem_eval/judge.py` MockJudge.

**Gold shape policy (pinned):** `gold.expected` is phrase-level (≥2 words), machine-checkable, and **agent-output-shaped wording deliberately distinct from prompt wording** (golds describe the agent's correct RESPONSE; prompts describe the situation). For **list-valued golds (R4 defeat conditions), the no-substring rule applies per element** — each element phrase-level, checked as a word-boundary substring individually. Short tokens only via `GOLD_ENUMS` (schema-derived allowlist — extend the constant, never inline). **No-substring content rule (builder-invariant):** normalized gold text must NOT be a word-boundary substring of the normalized rendered prompt (GOLD_ENUMS exact-token exemption). Collision = build fails loudly with the scenario id → author rewrites gold wording.

### Pack → Enum Summary (from scope — single source of truth)

task_type ∈ {decision, contradiction, calibration, retraction, loopy_contested, adversarial, family_rep, interdependent, wave_variant, cross_session_contradiction, decision_drift, feedback_loop} · family ∈ {R1,R2,R3,R4,R5,L1,L2,L3,L4,L5,L6,D3,D4} (D1/D2/L6 packless, documented) · tier ∈ {probe, stream, differential} · split ∈ {train, wave-1, wave-2, wave-3, held_out} · attack_type ∈ {poisoned, sybil, echo_chamber, flapping, anchoring} (only when task_type=adversarial) · source tier ∈ {T0,T1,T2,T3,T4} (D4 sources; T0 highest credibility — separate constant, see Task 1).

**PACK_SPLITS (exact per-pack split distribution — schema constant, builder-validated):** decision {train 14, wave-1 6} · contradiction {train 15} · calibration {train 12, wave-2 3} · retraction {train 10} · loopy_contested {train 8, wave-2 4} · adversarial {held_out 4, train 6} · family_rep {wave-1 5, wave-2 5, wave-3 5, held_out 3} · interdependent {wave-1 5, wave-2 5} · wave_variant {held_out 6} · cross_session_contradiction {wave-2 3, wave-3 3} · decision_drift {wave-2 3, wave-3 3} · feedback_loop {wave-3 6}. Totals: train 65, wave-1 16, wave-2 23, wave-3 17, held_out 13 = 134.

---

## Task 1: Package skeleton + schema constants + .gitignore FIRST

**Intent:** Establish the `battery` package and the single source of truth for schema enums/constants; gitignore the gold store BEFORE any build can stage it (a staged gold store is unpatchable git history).
**Acceptance:** `battery/config/schema.py` exports all enum sets + `PACK_COUNTS` + `PACK_SPLITS` + `GOLD_ENUMS` + `CORPUS_VERSION` + `CONTRADICTION_K` + `ATTACK_DISTRIBUTION`; `.gitignore` contains `battery/config/.gold_store/`; `git check-ignore battery/config/.gold_store/golds.json` exits 0; enum smoke test passes.
**Files:**
- Create: `battery/__init__.py`, `battery/config/__init__.py`, `battery/config/schema.py`
- Modify: `.gitignore` (MUST precede any build)
- Test: `tests/test_battery_corpus.py`

**Step 1:** Create `battery/__init__.py` (module docstring: eval-battery scenario corpus; epic #1402) and `battery/config/__init__.py`.
**Step 2:** Modify `.gitignore` — append `battery/config/.gold_store/`. Verify: `git check-ignore battery/config/.gold_store/golds.json` → exit 0.
**Step 3:** Create `battery/config/schema.py` (`from __future__ import annotations`):
- `TIERS`, `FAMILIES` (13 values; packless D1/D2/L6 documented in the docstring), `TASK_TYPES` (12), `ATTACK_TYPES` (5), `SPLITS` (5), `EVIDENCE_TIERS = ("T1","T2","T3","T4")`, `SOURCE_TIERS = ("T0","T1","T2","T3","T4")` (D4 adversarial source tiers — T0 highest credibility; **separate from EVIDENCE_TIERS** because T0 is a source-credibility tier, not an evidence tier), `VALENCES = ("supports", "undercuts")` (calibration evidence tiers + flapping flip valences), `REP_VALUES = (1, 2, 3)` (family_rep), `GOLD_ENUMS = ("undecided",)`, `PACK_COUNTS` (20/15/15/10/12/10/18/10/6/6/6/6), `PACK_SPLITS` (per the pack table — exact), `CORPUS_VERSION = "1.0"`, `CONTRADICTION_K = 5`, `ATTACK_DISTRIBUTION = {poisoned: 2, sybil: 2, echo_chamber: 2, flapping: 2, anchoring: 2}`, `FAMILY_REP_NAMES = ("incident-triage","customer-churn-review","vendor-selection","feature-priority","pricing-review","compliance-assessment")`, `HELD_OUT_FAMILY = "compliance-assessment"`.
**Step 4:** Write `test_enum_schema` (enum sets; `sum(PACK_COUNTS.values()) == 134`; `sum of PACK_SPLITS.values()` per pack == PACK_COUNTS) and `test_gold_store_gitignored` (subprocess `git check-ignore`, exit 0). Tests import via `sys.path.insert(0, repo_root)` (conftest convention). Run — must PASS.
**Step 5 (CI registration with file creation — drift gate green from the first commit):** register `test_battery_corpus.py` NOW: `config/ci-surfaces.yml` (new `battery:` surface + entry under `core:`), `tools/ci_selection.py` `SOURCE_PATTERNS["battery"] = ("battery/",)` (REQUIRED — unmapped = silent full-matrix bloat), `.github/workflows/python-ci.yml` matrix half-a `files += test_battery_corpus`. Verify `uv run python tools/ci_selection.py --integrity` exits 0. Run `uv run pytest tests/test_battery_corpus.py -v` — PASS.

## Task 2: Reader-safe loader core + GoldStore + shared validators

**Intent:** The reader path must never retrieve gold answers (S5 seal). Guards are recursive with exact-key equality, cover multi-session packs, and fail closed. The shared validators module is created here so Task 3's per-pack checkpoints can run them incrementally.
**Acceptance:** `corpus_loader.py` exposes `load_corpus` (fail-closed), `Corpus` (`.filter` with documented seed semantics), `assert_no_gold` (recursive, exact-key), `render_reader_prompt(scenario, session=None)`, `GoldStore` (fail-closed on missing/corrupt/unknown), `verify_seal(corpus, store)`; `validate.py` exposes the per-task_type validators; guard + fail-closed + validator tests pass on synthetic dicts.
**Files:**
- Create: `battery/config/corpus_loader.py`, `battery/config/validate.py`
- Test: `tests/test_battery_corpus.py`

**Step 1:** Write `battery/config/corpus_loader.py` (stdlib only):
- `GOLD_KEY = "gold"`, `GOLD_HASH_KEY = "gold_sha256"`
- Exceptions: `GoldLeakError`, `SealMissingError`, `SealMismatchError`, `StoreEntryMissingError`, `CorpusMissingError`, `CorpusCorruptError`
- `canonical_json(obj) -> bytes` (pinned serializer)
- `normalize(s) -> str` (casefold + whitespace collapse — the matching contract)
- `def assert_no_gold(obj) -> list[str]` — recursive walk; **exact key equality (`key == GOLD_KEY`)** — `gold_sha256` must NOT trip it; raises `GoldLeakError` on any found `gold` key at any depth; returns the paths list (empty when clean).
- `def render_reader_prompt(scenario: dict, session: int | None = None) -> str` — runs `assert_no_gold` first (fail-closed). Single-session packs (no `session_scripts`): renders system + turns + question. Multi-session packs: `session=None` renders the **accumulated full-history view** (system + all sessions' turns + questions in session order — a comparison/control surface, NOT the L4 delivery default); `session=N` renders system + session N's turns + question only. `attack_type`, `hostile`, `gold_sha256`, `matched_control_for`, `variant_of`, `graph_script` are scorer metadata and NEVER rendered. **Docstring: the rendered prompt is the ONLY agent-visible surface; adapters must never stringify scenario dicts.**
- `class Corpus` — `.scenarios`, `.manifest`, `.by_id(id)`; `.filter(tier=None, family=None, task_type=None, attack_type=None, split=None, seed=None)` — AND-combination, results sorted by id, deterministic; `seed` = determinism key for a downstream-supplied sample (the corpus itself returns the FULL filtered set; sampling with a size is the caller's contract — documented: same seed + same size → same subset, sorted order).
- `def load_corpus(path=None) -> Corpus` — **default path package-anchored** `Path(__file__).resolve().parent / "corpus.json"` (same basis as GoldStore; never CWD-anchored); **fail-closed:** missing file → `CorpusMissingError` (rebuild hint); corrupt JSON → `CorpusCorruptError`; runs `assert_no_gold` on every scenario.
- `class GoldStore` — `__init__(path=None)`: default path **package-anchored** `Path(__file__).resolve().parent / ".gold_store" / "golds.json"` (never CWD-anchored); missing → `SealMissingError` (rebuild hint); corrupt JSON → `SealMissingError` (message distinguishes corrupt); `gold(scenario_id)` — unknown → `StoreEntryMissingError`; `digest()` = `sha256(canonical_json(store_dict))` (sorted by id).
- `def verify_seal(corpus, store)` — `store.digest()` vs `corpus.manifest["golds_sha256"]`; `SealMismatchError` with both digests.
**Step 2:** Write `battery/config/validate.py` — the shared validators (imported by the builder AND Task 3 checkpoints): `load_yaml_dupreject(path)` (the duplicate-key-rejecting SafeLoader — 5-line subclass); `render_scan_text(scenario) -> str` — **the single render basis for build-time scans**: `render_reader_prompt` on a shallow copy of the scenario with the top-level `gold` key deleted (strip-then-render, exact-key; gold is always top-level in the authored schema) — byte-identical to the reader-path render, so the no-substring scan and the reader guard can never diverge (used by validate_scenario AND Task 5's render-guard test). **Nested-gold refusal mechanism (pinned):** a nested `gold` key inside `hostile`/turns is caught during validate-with-gold — `render_scan_text`'s copy still contains it, so `render_reader_prompt`'s `assert_no_gold` raises `GoldLeakError` (the builder's recursive strip + post-strip `assert_no_gold` are the belt-and-suspenders proof over the EMITTED artifact only); `validate_scenario(scenario, all_ids, all_ct_ids, pack_splits, ...) -> list[str]` returning error strings (empty = valid). `all_ids`/`all_ct_ids` **tolerate partial/empty sets** (authoring checkpoints pass what's accumulated so far): cross-id references are FORM-checked when the target set is incomplete (e.g., `matched_control_for` matches the `ct-` id pattern at batch 1) and RESOLUTION-checked when the target is present. Enforce ALL the per-task_type rules from Task 3/4 (required fields; k==5 + first-appearance + claim placement; calibration no-outcome-in-evidence (no evidence item equals gold.expected) + VALENCES; retraction k ≤ len(turns); loopy graph_script triangle + gold ∈ GOLD_ENUMS; D4 sub-schemas incl. sybil counts/tiers + hostile↔turns binding; family_rep family_name/rep + held-out pin; feedback ==5 iterations; gold no-substring via render_scan_text; per-scenario split ∈ SPLITS enum). Cross-scenario invariants (matched_control_for bijection/resolution, PACK_SPLITS exact totals, id-set completeness) are separate functions the builder calls after full accumulation — NOT run at partial checkpoints (Task 3 Step 1 defines "complete pack" semantics).
**Step 3:** Write tests: (a) loader/GoldStore/render: `assert_no_gold` raises on nested gold (incl. inside session_scripts turn, hostile, nested dict); **passes on a dict containing `gold_sha256`** (exact-key regression); `render_reader_prompt` excludes metadata; multi-session `session=None` vs `session=N` render differ and session=N contains only that session's content; `GoldStore` raises `SealMissingError` (missing + corrupt), `StoreEntryMissingError` (unknown id); `verify_seal` raises `SealMismatchError`; `load_corpus` raises `CorpusMissingError`/`CorpusCorruptError`; (b) **validators BEFORE Task 3 uses them** (test-before-use): `validate_scenario` unit tests on synthetic dicts — one valid scenario per task_type + one mutation per invariant (k=6; counter_claim pre-appearing in turns[0..3]; claim missing at expected turn; gold colliding with prompt text; loopy triangle invalid; sybil counts ≠ 100/1; echo ring < 3; flapping same-valence; feedback 4 iterations; valence invalid; anchoring_turn out of bounds; **retraction k = len(turns)+1; calibration evidence item == gold.expected; R4 empty defeat-condition list; L4 counter_claim placed in session 4; L4 counter_claim absent from session 5; `variant_of` → nonexistent id**) each returning a non-empty error list; `load_yaml_dupreject` rejects duplicate keys. Run — must PASS.

## Task 3: Authored corpus content (corpus.yaml — all 12 packs, exactly 134) with incremental checkpoints

**Intent:** Author the full v1 corpus with machine-checked content bindings; every pack checkpoint runs the shared validators so binding errors surface in the pack where they occur (bounded fix loop ~15 scenarios), not after all 134.
**Acceptance:** `battery/config/corpus.yaml` loads with the duplicate-key-rejecting loader; exactly 134 scenarios; **after each pack batch the shared validators run on the accumulated list with zero errors**; final full validation passes.
**Files:**
- Create: `battery/config/corpus.yaml`
- Test: `tests/test_battery_corpus.py` (YAML loads; per-task_type required-field + binding tests)

**Step 1:** Author `battery/config/corpus.yaml` **pack-by-pack; after each batch run the per-pack checkpoint** — `uv run python -c "...load via load_yaml_dupreject; run validate_scenario per scenario with the ACCUMULATED id sets; run accumulated-local checks (id uniqueness, dup-key rejection, enum/binding validity, splits for COMPLETE packs only)"`. ⛔ **Checkpoint scope (pinned):** per-pack checkpoints run ONLY per-scenario validators + accumulated-local checks. Cross-scenario invariants (`matched_control_for` bijection/resolution, `PACK_SPLITS` exact totals, id-set completeness) are DEFERRED to the final checkpoint (Step 2) and the builder (Task 4) — they cannot pass while later packs are absent, and the authoring loop must not deadlock on them. **"Complete pack" semantics (pinned):** a pack is complete iff accumulated count ≥ `PACK_COUNTS[task_type]`; count > target is an immediate checkpoint error. Zero errors before continuing to the next pack.
- Header: `meta: {corpus_version: "1.0", seed: 1407, threat_model: "reader isolation (internal tooling) — authored golds live here; the reader path never sees them"}`.
- Common: `id, tier, family, task_type, split, prompt: {system, turns: [{role, content}], question}, gold: {expected, rubric?}`.
- **decision (d-001..d-020, R2 ×14 / R4 ×6):** R4 gold.expected = non-empty list of defeat conditions; 15 (11 R2 + 4 R4) carry `matched_control_for: ct-XXX` (bijection; control = same decision shape, comparable turn count ±1).
- **contradiction (ct-001..ct-015, R1):** `planted_contradictions: [{claim, counter_claim, k: 5}]`; `len(turns) ≥ 5`; **bindings (normalized, word-boundary):** `claim` appears in turns[0..3]; `counter_claim` appears in turn 4 (k=5 → index 4); **first-appearance:** `counter_claim` does NOT appear in turns[0..3]. Gold.expected: surface + explicit resolution behavior.
- **calibration (cal-001..cal-015, R3):** `evidence_tiers: [{tier: T1..T4, claim, valence: supports|undercuts}]` (valence enum-validated); no-outcome-in-evidence (evidence items never state the known outcome; validated: no evidence item equals gold.expected); gold.expected = definite known outcome.
- **retraction (ret-001..ret-010, R5):** `retraction: {claim, supporting_evidence, retraction_event, k}` with `k ≤ len(turns)`; gold.expected = correct update direction (proportional, not full flip).
- **loopy_contested (lp-001..lp-012, R3):** `graph_script: {nodes: [{id, claim_or_turn_ref}×3], nand_edges: [[p,q],[q,r],[r,p]], contested_pair: {a, neg_a, a_ref, neg_a_ref}}` — 3 distinct node ids forming an odd NAND triangle; a/neg_a distinct from triangle nodes; refs resolve into `prompt.turns`; `gold.expected == "undecided"` (∈ GOLD_ENUMS).
- **adversarial (adv-001..adv-010, D4, exactly 2 per attack_type):** the hostile material is **woven into `prompt.turns`** (the agent experiences the attack through scenario text) — the `hostile:` block is the machine-readable mirror, never the reader-visible carrier. **Hostile↔turns binding (pinned enumeration — the SAME list is validated by validate.py AND tested by test_hostile_bound_to_turns):** poisoned → `injected_claim`; sybil → `injected_claim`; echo_chamber → **every** `echo_ring` variant claim; flapping → **every** flip entry's `claim`; anchoring → `stale_claim` AND `superseded_by`. Sub-schemas (validated): poisoned `{injected_claim, legit_claim, source_tier_injected: T4, legit_tier ≤ T1}`; sybil `{sybil_sources: [{source_id, tier: T4, count: 100}], legit_sources: [{source_id, tier: T0, count: 1}], injected_claim}` (the 100-vs-1 ordering); echo_chamber `{echo_ring: [≥3 pairwise-distinct claim variants], ring_sources, injected_claim}`; flapping `{flip: [≥2 entries {turn_idx, claim, new_valence}] with opposing valences (VALENCES), source_claim}`; anchoring `{stale_claim, superseded_by, supersession_turn, anchoring_turn, anchoring_source}` with **0-based bounds** `0 ≤ supersession_turn ≤ len(turns)−1`, `0 ≤ anchoring_turn ≤ len(turns)−1` (consistent with flapping `turn_idx` and the k=5→index-4 convention). Gold.expected: reject/abandon + expected high confidence.
- **family_rep (fam-*, L2, 18):** `family_name ∈ FAMILY_REP_NAMES`, `rep ∈ {1,2,3}`; rep 2 adds a reuse-opportunity turn; rep 3 adds a distractor turn; **the compliance-assessment family (3 reps) is split `held_out`** (builder-pinned: held_out family_rep scenarios are all `family_name == HELD_OUT_FAMILY` and all its reps are held_out).
- **interdependent (int-001..int-010, L1):** `session_scripts: [{session, turns, question}]` (2–4 sessions; strict causal ordering — later questions depend on earlier decisions).
- **wave_variant (wv-001..wv-006, L3, held_out):** `variant_of: <train decision id>` + `delta` (harder: extra counter-evidence turn); one-shot, held out, presented only at the wave-3/final checkpoint.
- **cross_session_contradiction (xs-001..xs-006, L4):** `session_scripts` (session 1 plants A; session 5 plants ¬A; session 6 query) + `planted_contradictions: [{claim, counter_claim, k: 5}]`; **bindings:** claim ∈ session 1 turns; counter_claim ∈ session 5 turns; **first-appearance:** counter_claim NOT in sessions 1–4.
- **decision_drift (drift-001..drift-006, L5):** `drift: {decision, offsets: ["7d", "21d"]}`; interleaved sessions between t0 and re-derivation are harness-generated filler (#1411 — noted, not authored).
- **feedback_loop (fb-001..fb-006, D3):** `feedback: {iterations: [{task, feedback} ×5]}` — exactly 5 authored entries, last is the hardest repeat (E2E-3.4 monotone improvement).
**Step 2:** Final checkpoint: exactly 134 scenarios; full validation (per-scenario + all cross-scenario invariants: bijection/resolution, PACK_SPLITS exact, id-set completeness, held-out family pin) with zero errors; write per-task_type required-field tests. Run — must PASS.

## Task 4: Deterministic builder (build_corpus.py)

**Intent:** Derive the sealed artifacts deterministically: validate (with golds) → **recursive strip (delete every `key == GOLD_KEY` at any depth)** → re-check the emitted form is gold-free → digest → emit; `--check` proves committed corpus.json matches a fresh build; no mutation of the authoring file.
**Acceptance:** `build_corpus()` emits `corpus.json` (gold-free, per-scenario `gold_sha256`, manifest `{corpus_version, content_sha256, golds_sha256, pack_counts, split_counts, family_coverage}`) + `.gold_store/golds.json`; `--check` byte-diffs (exit 0/1); two builds byte-identical **across processes with different PYTHONHASHSEED**.
**Files:**
- Create: `battery/config/build_corpus.py`
- Test: `tests/test_battery_corpus.py`

**Step 1:** Write `battery/config/build_corpus.py` (stdlib + yaml):
- Loads via `validate.load_yaml_dupreject`; runs the shared validators (per-scenario + cross-scenario: id uniqueness, bijection, PACK_SPLITS exact, meta.corpus_version == CORPUS_VERSION, corpus non-empty).
- **Strip phase:** recursive delete of every `key == GOLD_KEY` at any depth (the SAME walker as `assert_no_gold`, inverted); then `assert_no_gold` on the emitted scenarios (post-strip proof — a nested gold inside `hostile` is caught here).
- Emission: scenarios sorted by id; `gold_sha256 = sha256(canonical_json(gold))`; store `{scenario_id: gold}` sorted by id; manifest per the digest conventions (dict basis, never file bytes); files written `json.dumps(indent=2, sort_keys=True, ensure_ascii=False)` + trailing newline. No timestamps, no sets (every manifest aggregate a sorted list), no absolute paths.
- `main()` CLI: default in-place build; `--out <dir>`; `--check` rebuilds into a temp dir and byte-diffs corpus.json + store digest vs committed artifacts (exit 0/1).
**Step 2:** Run `uv run python -m battery.config.build_corpus` → corpus.json + gold store. Write `test_determinism` (**two builds in SUBPROCESSES with different `PYTHONHASHSEED`** — e.g. parent vs `PYTHONHASHSEED=0` subprocess — byte-identical; plus committed corpus.json == fresh build bytes) and `test_invariants` (mutation-driven refusals: dup id, k=6, split drift vs PACK_SPLITS, bijection break, gold missing, counter_claim at wrong turn, counter_claim pre-appearing in turns[0..3], gold colliding with prompt text, pack count 19, duplicate YAML key, attack_type distribution 3+1, sybil counts ≠ 100/1, poisoned legit_tier too high, echo_ring duplicates/<3, flapping same-valence flips, anchoring_turn out of bounds, loopy triangle invalid, **nested gold inside a hostile block refused at validation (GoldLeakError during validate-with-gold — NOT the post-strip phase, which proves gold-freeness of the emitted artifact)**). **Commit boundary (pinned):** commit `corpus.json` together with `test_determinism` at Task 4 completion so every later committed-artifact test (`test_load_corpus_reader_safe`, `test_sealing_no_gold_in_corpus_json`) is green in CI at every commit. Run — must PASS.

## Task 5: Full test suite (hermetic — green on fresh clone AND in CI)

**Intent:** Lock the corpus contract for downstream; store-dependent tests are hermetic (tmp-built fixture, never the local gitignored store); CI registration lands in Task 1 Step 5 with the test file creation (drift gate green from the first commit) — Task 5 verifies only.
**Acceptance:** All tests pass with a tmp-built store fixture; `uv run pytest tests/ -v` regression passes; `uv run python tools/ci_selection.py --integrity` exits 0 (registration already landed in Task 1 — verify only).
**Files:**
- Modify: `tests/test_battery_corpus.py`
- Verify (already modified in Task 1): `config/ci-surfaces.yml`, `tools/ci_selection.py`, `.github/workflows/python-ci.yml`

**Step 1:** Add `sealed_corpus` fixture: `build_corpus(source=<corpus.yaml>, out_dir=<tmp_path>)` → (Corpus loaded from the TMP-built corpus.json — hermetic, not the committed file, so the fixture stays green pre-commit; GoldStore on the tmp store). Fixture setup asserts `verify_seal` passes; on failure the message instructs rerunning `uv run python battery/config/build_corpus.py` (fixture validity requires Task 4's build to be current). Add tests:
- `test_pack_counts` (manifest == PACK_COUNTS exactly; totals == 134)
- `test_pack_splits` (per-task_type split counts == PACK_SPLITS; split totals match manifest.split_counts)
- `test_k_pin` + `test_claim_placement` (bindings incl. first-appearance) + `test_feedback_iterations` (==5) + `test_sybil_counts` (100/1, T4/T0 via SOURCE_TIERS) + `test_hostile_bound_to_turns` (each D4 scenario's key hostile text appears in the rendered prompt)
- `test_sealing_no_gold_in_corpus_json` (recursive walk of committed corpus.json; no `gold` key, no gold plaintext)
- `test_load_corpus_reader_safe` (e2e: load_corpus on the committed artifact; every scenario passes `assert_no_gold`; `Corpus.by_id` reader-safe; `verify_seal` passes via fixture)
- `test_gold_sha256_matches_store` (per-scenario hash == sha256 of store entry; store digest == manifest golds_sha256)
- `test_splits_partition` (exactly-one; held_out non-empty; ≥3 waves; family_rep held-out family pinned)
- `test_controls_bijection` (15 ↔ 15, each ct-id referenced exactly once)
- `test_zero_context_render` (full-prompt form self-containment for single-session packs; multi-session packs' accumulated view is expected to reference earlier sessions — scan the full form only; docstring documents heuristic limits)
- `test_render_guard_no_gold_substring` (every rendered prompt free of gold.expected — normalized, word-boundary; GOLD_ENUMS exact-token exemption)
- `test_attack_not_rendered` (adversarial renders exclude attack_type/hostile labels; the attack material itself arrives via turns — the agent experiences the attack without its label; hostile↔turns binding per the pinned enumeration)
- `test_corpus_filter` (subset by each param, AND-combination, empty result, id-sorted, seeded determinism with a fixed sample size)
- `test_load_corpus_fail_closed` (missing → CorpusMissingError; corrupt → CorpusCorruptError)
- `test_goldstore_fail_closed` (missing/corrupt/unknown-id — Task 2)
**Step 2:** Verify CI registration (already landed in Task 1): `uv run python tools/ci_selection.py --integrity` exits 0. Run `uv run pytest tests/test_battery_corpus.py -v` — all PASS.

## Task 6: Commit artifacts + full regression

**Intent:** Land the committed corpus.json (pinned reader artifact), README, CI registration; prove the whole suite passes.
**Acceptance:** `battery/config/corpus.json` committed; `battery/config/README.md` documents the full contract; `git status` shows NO `.gold_store/`; full `uv run pytest tests/ -v` green.
**Files:**
- Create: `battery/config/README.md`
- Modify: committed `battery/config/corpus.json` (Task 4 output)
- Test: full suite

**Step 1:** Write `battery/config/README.md`: what the corpus is (epic #1402/#1407); rebuild (`uv run python -m battery.config.build_corpus`); determinism (`--check`); the seal model (gitignored store, reader-guard load+render, verify_seal, fail-closed GoldStore, build-before-score); the digest recipe (canonical_json — dict basis, never file bytes); **reader-surface contract (only `render_reader_prompt` output is agent-visible; adapters must never stringify scenario dicts; the accumulated full-history view is a comparison/control surface — L4's cross-session accumulation requires per-session delivery + retrieval, owned by #1410);** file-read threat-model note (arm adapters must not grant the agent repo access to `battery/config`); downstream contract (**schema-level note: #1406 must extend the run_artifact schema with `corpus_version` + `content_sha256` (and the reader-prompt hash per E2E-4.1) — README prose alone is not the contract surface**); import/invocation convention (`uv run python -m battery.<mod>` from the repo root; `battery*` packaging into the wheel is a #1406 concern).
**Step 2:** Rebuild corpus.json deterministically (`uv run python -m battery.config.build_corpus`); confirm store still gitignored. Run full suite: `uv run pytest tests/ -v` — all pass.
**Step 3:** Commit via commit-workflow (VGATE → commit → PR → code-review gate → merge).

## Common Mistakes to Avoid

- Committing `.gold_store/` (unpatchable git history) — gitignored in Task 1 BEFORE the first build; verified with `git check-ignore` + `git status`.
- Gold leaking into turns/session_scripts/hostile/rubric — nested gold refused at validation (GoldLeakError via render_scan_text); recursive strip + post-strip `assert_no_gold` prove gold-freeness of the EMITTED artifact; `gold_sha256` never trips the walker (exact-key equality).
- Authoring claim at the wrong turn or pre-appearing (k pin decorative) — normalized word-boundary claim-placement bindings incl. first-appearance, validated at per-pack checkpoints AND build.
- Gold wording colliding with prompt text (render-guard false positive) — gold no-substring rule as a builder invariant with scenario-id errors; golds are agent-output-shaped.
- Non-deterministic output — canonical JSON (sorted keys, no sets, sorted manifest aggregates); cross-process PYTHONHASHSEED determinism test; never hash file bytes.
- Enum literals scattered — constants from `schema.py` only (incl. SOURCE_TIERS for D4 source tiers, VALENCES, REP_VALUES).
- Per-pack authoring checkpoints deadlocking on cross-scenario invariants (bijection/PACK_SPLITS need the full set) — checkpoints run per-scenario + accumulated-local checks only; cross-scenario invariants run at the final checkpoint + builder.
- Store-dependent tests reading the local gitignored store (green locally, red in CI) — hermetic tmp-built fixture.
- New test file unregistered (CI `--integrity` drift trap) — registered in Task 1 Step 5 with the test file creation; Task 5 verifies only.
- `battery/` unmapped in SOURCE_PATTERNS (silent full-matrix bloat on every battery PR) — REQUIRED mapping, no hedge.
- Committing corpus.json tests without corpus.json (CI-red at the commit boundary) — corpus.json committed at Task 4 completion with test_determinism.
- Script-path invocation (`uv run python battery/config/build_corpus.py`) breaking package imports — always `uv run python -m battery.config.build_corpus` (repo-root sys.path).

## E2E Alignment Notes (for downstream)

- **E2E-1.2 (R1):** claim-placement bindings (incl. first-appearance) make turn k the true injection point; the 15 matched controls give the FP gate its matched non-contradictory set.
- **E2E-1.3 (R3 honest-UNDEC):** loopy graph_script carries node prose + NAND edge list so #1409 can materialize the graph (create_point/create_operator); gold "undecided" ∈ GOLD_ENUMS.
- **E2E-2.1 (L2):** held-out compliance-assessment family (3 reps, held_out, builder-pinned); no prior-wave baseline exists by design — #1411 must define the held-out baseline (vs wave-3 in-wave SR or a fixed floor).
- **E2E-2.2 (L1):** interdependent session_scripts have strict causal ordering; later questions depend on earlier decisions.
- **E2E-2.3 (L3):** wave_variant scenarios are one-shot, held out, presented only at the wave-3/final checkpoint; wave composition is pinned by PACK_SPLITS.
- **E2E-2.4 (L4):** cross-session scenarios plant A (session 1) / ¬A (session 5) / query (session 6) with machine-checked placement; per-session delivery is the L4 default (accumulated view is a control surface, README-documented).
- **E2E-2.5 (L5):** decision_drift scenarios carry D at t0 + re-derivation at t+7d/t+21d; interleaved filler sessions are harness-generated (#1411).
- **E2E-3.4 (D3):** feedback_loop carries exactly 5 authored iterations, last hardest.
- **E2E-3.5 (D4):** per-attack sub-schemas carry the mechanics (sybil 100×T4 vs 1×T0, flip turn indices, supersession events) and the hostile material is bound into the rendered turns (machine-checked).
