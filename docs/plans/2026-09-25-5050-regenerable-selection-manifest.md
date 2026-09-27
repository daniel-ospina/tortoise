<!-- research-path: docs/plans/2026-09-25-5050-regenerable-selection-manifest.md -->

# Regenerable, Value-Validated Selection Manifest — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Replace the hand-maintained CI selection manifest with a generated one — the `durations` map and the leg partition derived from measured junit artifacts and the repo's own guard/path graph — so that a wrong value, a missing row, a dead key, or an unselected guard is a CI failure at the moment it lands, never a silent omission.

**Team:** organisation-design-team (issue label `team:unknown`; base-template fallback)

**Architecture:** A new in-repo generator/validator, `tools/ci_manifest.py`, owns the derived half of `config/ci-surfaces.yml`. The measurement record (`config/ci-durations-source.json`) is the single source of truth for per-file durations; the human-facing `durations:` block is strict-projected from it. `tools/ci_selection.py --integrity` stays the ONE contract and calls the validator, so there is no parallel gate. Legs are asserted as a **partition** (direct set arithmetic over the classified universe), not as an emergent property of the LPT pack. The generator, not a new guard, closes the known failure classes: strict presence kills the unweighted files; a derived tool→guard reachability check kills the unselectable tools; a SOURCE_PATTERNS reality check kills the dead `ep` entry; declared guard inputs close the #4658 docs hole; an explicit `unmeasured` record row closes the bootstrap trap.

**Tech stack:** Python 3.12 stdlib (`argparse`, `json`, `xml.etree.ElementTree`, `ast`), PyYAML (already an in-repo dependency consumed by `ci_selection.py`), pytest.

> **Scoping note (process).** #5050 carries `complexity:complex` but no `<!-- issue-scoping: -->` signature. The owner's dispatch prompt supplies the scoping record for this increment: the root sentence, the refactor direction, the 11-child table, the explicit bounded deliverable, and the non-negotiable owner frame ("fix the root, not symptoms; no new guard/exception/allow-list/regex per missing row; keep CI lean; **a file that is not selected must be a CI failure at the moment it lands**"). The plan is authored against that artifact; it does not re-scope the root.

### Pattern Research

> Gate skipped: zero third-party dependencies introduced — the change is stdlib + the in-repo PyYAML wrapper `ci_selection.load_manifest()`, which already exists and is used 2+ times. No library version/API-surface, idiomatic-usage, or pitfalls bucket applies.

**Library docs (preflight)** — none; stdlib `xml.etree.ElementTree` + `json` only.

### Integration Surface Map

| Surface | Boundary | Test layer | Failure modes |
|---|---|---|---|
| `config/ci-surfaces.yml` `durations:` block | generator writes, `--integrity` reads | unit (`tests/test_ci_manifest.py`) | value ≠ record; missing row; dead (non-packable) key; block-rewrite corrupts the preserved header or a neighbouring top-level key |
| `config/ci-durations-source.json` | sweep writes, `--integrity` reads | unit | malformed/absent record; row with no samples and no `unmeasured`; `unmeasured` marker stale (a source run measured it) |
| junit artifacts (`pytest-log-test-a/-b/-slow/-carve-out`) | `sweep --junit-dir` reads | unit (fixture XML) | partial artifact set (leg cut by the 55 m watchdog); duplicate artifact name (the two `test-slow` legs) → a file measured in neither must be retained, never dropped |
| `.github/workflows/python-ci.yml` legs | `push_legs()` consumes the manifest | unit | partition violation (a classified file in two legs, or none) |
| `tools/ci_selection.py --integrity` | the single contract | unit + CLI | a validator failure that does not fail the exit code; a parallel gate introduced |
| `tools/*.py` guards | derived reachability | unit + CLI | a tool that owns a registered guard test selects no surface (#3362/#4115 class) |
| guarded docs (`ARCH_DOCS`) | `SOURCE_PATTERNS` | unit + CLI | a docs-only PR edits a guarded doc and the guard does not run (#4658) |

### Journey Test Map

### Journey: a lane adds a new test file
1. **Step:** author adds `tests/test_new.py` → **Acceptance:** `--register` classifies it AND records it as `unmeasured` with a provisional weight → **Test:** `test_register_leaves_integrity_green_for_a_new_file`
2. **Step:** CI runs the new file → **Acceptance:** the next `sweep` replaces the provisional value with the measured one and clears the marker → **Test:** `test_sweep_clears_a_stale_unmeasured_marker`
3. **Step:** a reviewer edits a `durations` value by hand → **Acceptance:** `--integrity` fails, naming the row and the recorded value → **Test:** `test_value_mismatch_fails`

### Journey: total selection
1. **Step:** a change lands to a file a guard reads → **Acceptance:** `select()` is non-empty (a surface runs) or the change fails closed to the full matrix → **Test:** `test_every_tool_guard_is_selectable`, `test_every_declared_guard_input_is_selectable`
2. **Step:** a change lands to an untracked path → **Acceptance:** fail-closed full matrix (unchanged) → **Test:** `test_unknown_path_fails_closed`

### Failure Modes
- A leg/artifact is missing from the sweep sources → the value must be RETAINED with an `# unmeasured` marker, never dropped to the flat default → `test_partial_artifact_set_retains_rather_than_drops`
- A hand-edit raises or lowers a row → `--integrity` red → `test_value_mismatch_fails`
- A newly registered file is not in the record → `--integrity` red unless explicitly `unmeasured` → `test_missing_row_fails`
- A carve-out key is added to `durations` → `--integrity` red → `test_carve_out_key_fails`

### Verification Plan
Unit + CLI only (Python tooling, no DB/UI). Surface map layers: unit for the generator/validator; the CLI is exercised via `subprocess`/`main()` calls against a scratch manifest copy. `test-routing` domain = code/config; UX depth = none.

---

## Full substrate — design (all children of #5050; only T1–T7 are this increment)

The root's four properties (regenerable / value-validated / total / hook-independent) map onto one artifact and three derivations:

1. **`config/ci-durations-source.json` — the measurement record.** Generated by `tools/ci_manifest.py sweep` from junit artifacts. Holds: the source run ids and their heads; the carrying-leg rule; per-file per-run samples; the derived `value`; the explicit `unmeasured` set; the pinned-equality set; the retained set. The human-facing `durations:` block is a strict projection of this record. **Nothing else may author a weight.**
2. **The leg partition.** `tools/ci_manifest.py partition_issues()` computes the classified universe (`surfaces ∪ tier1 ∪ slow`) and asserts each file lies in exactly one leg (`half_a`, `half_b`, `slow−carve_out`, `carve_out`, `env_broken`). It is direct set arithmetic over the derivation, not a reading of the LPT output — the #4528/#4615/#4754 class (an assertion on an emergent packing property) is retired by construction. The LPT pack remains as the *balance* mechanism; the *coverage* claim no longer depends on it.
3. **Guard reachability (derived).** `guard_reachability_issues()` derives, from the repo's own files: (a) every `SOURCE_PATTERNS` entry must name a real path/prefix (kills `ep → tortoise/decide.py`, #4165); (b) every `tests/**/test_<stem>.py` with a registered guard and a `tools/<stem>.py` sibling means that tool must be selectable (kills #3362/#4115); (c) every declared guard input must select the surface that owns the guard that reads it (closes #4658). A guard input is *declared* (`manifest['guard_inputs']`) because #2938 established that "which files a guard exists to protect" is intent, not derivable — but the declaration is validated in both directions, so an undeclared/rotten entry is a failure, not a silent hole.
4. **Hook-independent enforcement.** The completeness ratchet lives in `--integrity` (a required CI check), not in `.husky/pre-commit` (#4832). The commit hook stays as a convenience; CI is the net.
5. **The adjacent member #4547** (base resolved from the stale `pull_request.base.sha`) is the same root one level up — the selector's *input* is wrong, not its data. It is designed here (T10) and attached to #5050; it is NOT in this increment.

Children disposition for the full substrate: #4766 (regeneration path) = T2/T3; #4783 (value guard) = T4/T6; #4817+#4348+#4364 (bootstrap trap) = T5; #3463 (13 unweighted) = T3; #4835 (`fast_files_absent_from_halves` ignores `ENV_BROKEN_FILES`) = T6; #4528+#4615+#4754 (emergent LPT assertion / `push_extra` / split stability) = T6/T7; #4186+#4658 (reverse-direction ratchet, docs-only) = T7; #3362+#4115 (unselectable tool guards) = T7; #4165 (`ep` dead path) = T4; #4486 (conftest product modules) = T8; #4832 (husky) = T9 in this increment's framing (enforcement moved to CI); #5023 (drift-gate clause) = T11; #4547 = T10; #4034 (pglite gate) + #3977 (two registries) = deferred, distinct enough to stay children.

---

## This increment (T1–T7) — bounded deliverable

### Task 1: The measurement record + generator core

**Intent:** One command derives the `durations` map from real junit, and no value can exist without a recorded measurement behind it (#4766).
**Acceptance:** `tools/ci_manifest.py sweep --junit-dir <dir>...` emits a `durations` block and a record; the record carries every source run id and per-file samples; `--write` updates `config/ci-durations-source.json` and rewrites only the `durations:` block of `config/ci-surfaces.yml`, preserving the prose header above the first row and every top-level key before `durations:` byte-for-byte.
**Files:**
- Create: `tools/ci_manifest.py`
- Modify: `config/ci-surfaces.yml` (durations block only)
- Create: `config/ci-durations-source.json`
- Test: `tests/test_ci_manifest.py`

### Task 2: The carrying-leg rule and partial-artifact handling

**Intent:** Reproduce the documented sweep rule (leg that carries the file; larger value across runs; floor 0.1 s; 1 dp; `track-b`/`d14` excluded) and refuse to silently move weights down when a leg is missing (#4766, #3395).
**Acceptance:** a file in `carve_out` is measured from `pytest-log-test-carve-out`; a fast file from `test-a`/`test-b`; a slow-only file from `test-slow`; an unmeasurable row is RETAINED with an `# unmeasured` marker, never defaulted; the pinned pair and the retained row are honored.
**Files:** `tools/ci_manifest.py`; Test: `tests/test_ci_manifest.py`

### Task 3: Regenerate the map from the five documented source runs

**Intent:** Fill the unweighted fast-pool files by measurement, not by hand (#3463).
**Acceptance:** `--integrity`'s strict presence check passes with zero unweighted fast files; a dry `sweep` over the five documented source runs reproduces the committed record exactly (zero drift). The five runs are a UNIFORM basis — every listed run contributes to the per-file max, so the map is a pure function of the record's `sources` and the base-commit run is not fill-only. Rule evidence: the four header runs alone already reproduce ~610 of the pre-existing 687 rows (the residue is ±0.1 rounding on rows whose prior was `# unmeasured`), which pins the carrying-leg/max rule; the base-commit run then raises 176 rows, the direction-safe consequence of adding a legitimate sample of the same manifest (the map is a watchdog bound, so the larger value is the safe side). Committing to a five-run uniform basis rather than a four-run-plus-fill rule keeps ONE aggregation semantics — a fill-only role for one run would be a second, special-case basis.
**Files:** `config/ci-surfaces.yml`, `config/ci-durations-source.json`

### Task 4: Value validation — wrong value, dead key

**Intent:** A value that disagrees with the record, or a key that is not packable, is an error (#4783).
**Acceptance:** mutating a row to `0.1` or `1880` fails `--integrity` naming the file and the recorded value; adding a carve-out key fails; `SOURCE_PATTERNS` entries are checked to name something real (#4165).
**Files:** `tools/ci_manifest.py`, `tools/ci_selection.py`; Test: `tests/test_ci_manifest.py`

### Task 5: Bootstrap trap — atomic registration

**Intent:** A newly registered file cannot have a measurement before CI runs it, and must not pack at a silent default (#4348/#4364/#4817).
**Acceptance:** `--register` writes the surface row AND an explicit `unmeasured` record row/`durations` entry; `--integrity` stays green; the next sweep clears the marker when the file is measured.
**Files:** `tools/ci_manifest.py`, `tools/ci_selection.py`; Test: `tests/test_ci_manifest.py`

### Task 6: The partition invariant + #4835

**Intent:** assert coverage structurally, not as an emergent LPT property.
**Acceptance:** `partition_issues()` reports a file in two legs or none; it subtracts `ENV_BROKEN_FILES` (#4835); `--integrity` fails on a violation.
**Files:** `tools/ci_manifest.py`, `tools/ci_selection.py`; Test: `tests/test_ci_manifest.py`

### Task 7: Guard reachability — tools, docs, and the declared guard inputs

**Intent:** a change to a file a guard reads must select the guard's surface (#3362/#4115/#4186/#4658).
**Acceptance:** `surface_manifest.py` selects `core`; `docs/auth-architecture.md` + `website/website_architecture.md` select `onboarding`; a declared `guard_inputs` entry that selects no surface fails `--integrity`; a `tools/<stem>.py` with a registered `test_<stem>.py` guard that selects nothing fails `--integrity`.
**Files:** `tools/ci_manifest.py`, `tools/ci_selection.py`, `config/ci-surfaces.yml`; Test: `tests/test_ci_manifest.py`, `tests/test_ci_selection.py`

### Task 8 (deferred, designed): conftest product modules (#4486)
Decision needed per module (cross-cutting → full matrix, or deliberately `core`-only, recorded). Not this increment — it is a CI-cost trade and a recorded-direction question (the F4 audit recommends *shrinking* entries).

### Task 9 (deferred, designed): husky / hook independence (#4832)
The #1429 auto-registration hook must stop being load-bearing. This increment moves the *duration* half into `--register` + CI. The remaining work is a CI job that runs `--register --dry-run` and fails on drift (already `--integrity`) plus a bootstrap note. Not this increment.

### Task 10 (deferred, designed): #4547 — base-resolution self-check
`changes` must resolve its base from the checked-out merge (`git rev-parse HEAD^1` or `git merge-base`) and fail loudly when the base is not an ancestor of HEAD.

### Task 11 (deferred, designed): #5023 — exercise `python-ci-gate`'s `surface-guard` clause
The drift-gate test must actually execute the clause its docstring names.

---

## Definition of done for this increment

- `tools/ci_selection.py --integrity` is green AND strict (zero unweighted fast files, zero value disagreements, zero dead keys, partition clean).
- `tools/ci_manifest.py sweep` re-derives the committed map from the documented junit sources and reports zero drift.
- `tests/test_ci_manifest.py` + `tests/test_ci_selection.py` green.
- PR against `main`; the `durations` map is generated, not hand-edited.

## Learnings

- The four header runs alone reproduce ~610/687 of the pre-existing committed rows (the residue is ±0.1 rounding on rows whose prior was `# unmeasured`), which is strong evidence the carrying-leg/max rule is faithfully encoded. The committed map itself uses the UNIFORM five-run basis (the base-commit run raises 176 rows); that is the documented max aggregation, not a fill-only fill — see Task 3's acceptance above.
- The `pytest-log-test-slow` artifact name collides across the two slow legs, so a full-pool slow sweep is impossible from these artifacts — the record must carry a retained marker for slow rows the sweep cannot see (`eval/retrieval/test_integration.py` et al.).
