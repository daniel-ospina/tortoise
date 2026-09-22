<!-- research-path: none — standalone issue #3827 (no epic doc). Prior research = ~/.pi/agent/state/lane-reports/W0-3827-SCOPE-2026-09-17.md §Phase 1.5 (Axis Research) -->

# Embedded-lane evidence producer (#3827) Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** one command — `python3 tools/embedded_evidence.py run|red` — that runs a named, recorded file
selection N times in fresh subprocesses at a pinned CLEAN commit, classifies every run (never counts it),
demonstrates a RED for the same selection at a named pre-fix ref in the same lane, and **enforces**
`closes_issue` as an exit code — so the embedded family's exit evidence becomes a receipt a reviewer can
falsify.

**Team:** organisation-design-team
**Issue:** daniel-ospina/tortoise#3827 · **Tier:** standard · **Lane:** W0 (Wave-0 verification substrate)
**Worktree:** `.worktrees/w0-substrate` @ `37d5ef00c` · **doc_status:** draft (verdict revision 2.4.0 — 2026-09-18)
**Scoping record:** `~/.pi/agent/state/lane-reports/W0-3827-SCOPE-2026-09-17.md` (problem diamond, gate-passed)
**Solution record:** `~/.pi/agent/state/lane-reports/W0-3827-PLAN-2026-09-17.md` (F1–F30 binding constraints)

**Architecture:** a **composition, not a rebuild**. `tools/embedded_evidence.py` calls five existing
primitives — `tools/skip-guard.py` (selection manifest), `tools/ci_selection.py` (the selector, consumed
*verbatim*), `tools/testdb_canary_classify.py` (classification vocabulary + atomic write, **generalized with
a `lane=` argument** rather than bypassed), `tools/embedded_orphans.py` (per-run environment receipt), and
the `tests/test_tripwire.py` subprocess-session pattern — and adds exactly **three pieces of new code, two
fail-closed gates, one declaration table, and one shared lane-contract module** (`tools/lane_contract.py`, M24). It never re-derives a file list, never writes a second
file-set receipt, and never reports a verdict as clean when a conjunct is false.

> ⛔ **THIS PLAN IS THE RED HALF ONLY (PR #1).** The GREEN half (N consecutive green at the fixed commit)
> has **no referent today**: at HEAD `37d5ef00c` the family lane is RED (first `-x` failure is #3845's
> `GRAPH.COPY failed, could not fork`), `a59adeaa8` (#3813's fix) is **not** an ancestor of HEAD, and
> #3845/#3813 are **OPEN**. PR #1 ships the runner, the tripwire, the tests and the cold receipt; it
> **does not close #3827**. The GREEN half is a documented **no-new-code** handoff (see
> [§ GREEN half — deferred](#green-half--deferred-pr-1-ships-no-code-for-it)).
>
> ⛔ **CERTIFICATION RULES (verdict 2026-09-18 — see D23/D24).** Certification binds to the **shipping
> surface** (R1) and to the **reviewed head SHA** (R2); the required mutator is **statement deletion of the
> fix's own population/edit branch** (the #3888 class). **N=10 is DECLARED LOCAL** — no source makes any N
> canonical (D13).

**Naming-convention check (mandated).** `docs/plans/` is `YYYY-MM-DD-<issue>-<kebab-slug>.md`. Existing
files proving the match: `2026-09-12-2784-graphs-last-backup.md`, `2026-09-11-2977-object-retraction.md`,
`2026-09-11-3011-context-assembly-impl.md`, `2026-09-10-1701-chatgpt-harness-remaining.md`,
`2026-09-08-2165-connected-assembly.md`. This plan is
`docs/plans/2026-09-17-3827-embedded-lane-evidence-producer.md`.

---

## Pattern Research

> **Findings date:** 2026-09-17
> **Gate skipped:** zero new third-party dependencies. The plan adds **no** new dependency — stdlib
> (`argparse`, `json`, `hashlib`, `math`, `subprocess`, `pathlib`, `os`, `sys`, `tempfile`, `shutil`,
> `importlib.util`) plus **existing** `pytest>=8`, `pytest-timeout` (already used for `--timeout=300` and
> `-p no:cacheprovider`), and `pyyaml>=6.0` (`pyproject.toml:46`, consumed transitively via
> `tools/ci_selection.load_manifest()`). Sub-step B.0 (library docs) and Sub-step B.1 (multi-call
> Perplexity) are therefore skippable per `writing-plans` § Skip Rules.
> **Step A executed** (all tiers): prior research = the scoping lane report's `### Axis Research`
> (Architecture / test-infrastructure, **fired**, 7 external queries under the Standard cap ≤ 8,
> 1 HTTP-429 retry), whose findings are carried into `### Design Decisions` below as D5, D11, D13, D17, D19,
> D20. Its load-bearing findings: selection parity must be asserted from **one argument list** and the
> **resolved nodeid manifest** recorded; selection can shrink silently (`--deselect`, markers, discovery
> config) so the record must store nodeid **set + counts**, not just verdicts; "N consecutive green" is
> brittle as a *merge gate* but sound as *evidence attached to a claim*; redislite creates **exactly one**
> server per interpreter and cleans up only on deletion/exit, so per-iteration hermeticity must be explicit
> and recorded; the durable red is a **committed test**, and `XFAIL`/`skip` destroys its evidentiary value;
> no upstream project commits a machine-readable red/green record (so the schema derives from our own
> manifest format). **Prior-art correction (M62):** "upstream" is not the same as *in-repo* —
> `tools/ci_timing.py` already commits a machine-readable run record (`docs/ci-timing.json`, 43 bytes at
> HEAD) with `SCHEMA_VERSION` (written as `schema_version`), a `sampled_run{run_id, head_sha,
> created_at, conclusion, sample_time, selection}` block, an `outcome{passed, failed, error, skipped,
> xfailed, xpassed, killed}` block, nodeid `failed_tests`, a bounded `history`
> (`MAX_HISTORY_DEFAULT = 52`) and a derived `candidate_flakes` signal. That is a **second committed
> pass/fail record writer of the same kind**, so the envelope vocabulary is **shared**
> (`unify-contract-keep-drivers`): see D7's "Shared record-envelope vocabulary".
> **Domain detection:** Complicated (expert analysis works; existing precedent exists for every seam —
> parser, classifier, selector, census, subprocess tripwire). Not Complex.

---

## Integration Surface Map

Step C of the skill (`test-design`) was executed against the file list; the table is the deliverable. Every
boundary the implementation crosses has a layer and a named test.

| # | Touch point | Kind | Boundary crossed | Covered by |
|---|---|---|---|---|
| 1 | `tools/embedded_evidence.py` | **CREATE** | git (worktree add/remove, `status --porcelain=v2`), subprocess (pytest child), filesystem (ledger under `$TEMP`), the 5 primitives | `tests/test_embedded_evidence.py` (all tasks below) |
| 2 | `tests/test_embedded_evidence.py` | **CREATE** | itself | itself; MUST be registered (task 12) |
| 3 | `tests/test_embedded_save_tripwire.py` | **CREATE** | a **real embedded redislite server** (`CONFIG GET`), `.github/workflows/*.yml` + `docker-compose.yml` (static scan) | itself; MUST be registered (task 12) |
| 4 | `docs/ops/embedded-lane-evidence.md` | **CREATE** | docs index | `docs/00_index.md` row + `test_embedded_evidence.py::test_runbook_registered_in_docs_index` |
| 5 | `config/ci-surfaces.yml` — `embedded_family:` | MODIFY | the runner's selection source; `ci_selection.load_manifest()` | `test_ci_selection.py::test_embedded_family_declaration_is_inert_to_selection`, `::test_embedded_family_reproducers_exist_and_pin_the_mandatory_file` |
| 6 | `config/ci-surfaces.yml` — register the 2 new test files + `carve_out:` membership | MODIFY | `ci_selection.integrity()` (merge-blocking `--integrity` job) | `test_ci_selection.py::test_integrity_covers_all_test_files` (unchanged, must stay `[]`), `::test_new_embedded_tests_are_carve_out_and_core` |
| 7 | `tools/testdb_canary_classify.py` — required `lane=` + shared streak declaration | MODIFY | the classifier's population gate + the streak contract (two writers) | `tests/test_canary_classify.py` (existing 16 tests, all still green) + new lane/schema tests |
| 7b | `tools/testdb_canary_classify.py` — **the classifier CLI (`main()`) + the `canary-streak` job** | MODIFY / consumed by `.github/workflows/python-ci.yml:1751` (in `python-ci-gate`'s `needs:` `:1865`; `.github/**` NOT TOUCHED) | the `classify(...)` call at `:409` (the production caller; M6) | `test_classifier_cli_threads_lane`, `test_every_classify_call_site_passes_a_lane` |
| 7c | `tools/lane_contract.py` — ONE lane vocabulary + child-env registry (M24) | CREATE | `ask_recall_bench`, `BackendIdentity`, the classifier's `LANES`, the runner + its child env | `test_lane_vocabularies_agree`; the runner consumes `CHILD_LANE_VARS` (incl. `TORTOISE_TEST_CARVE_OUT`) |
| 8 | `tools/ci_selection.py` — `TOOL_CARVEOUTS` | MODIFY | `select()` reachability for `tools/**` | `test_ci_selection.py::test_embedded_evidence_tool_change_fails_closed_to_full`, `::test_testdb_canary_classify_tool_change_fails_closed_to_full`, `::test_lane_contract_tool_change_fails_closed_to_full` (**C14** — `tools/lane_contract.py` is the third carved-out path) |
| 9 | `tests/_embedded.py` — `TEST_NO_REDIRECT_STEMS` | MODIFY | the URI→server redirect (the tripwire must run **embedded**) | `tests/test_markers.py::test_no_redirect_stems_registry_exact`, `::test_no_redirect_stems_exist_as_modules` |
| 10 | `tests/test_markers.py` — the exact-set pin | MODIFY | same as #9 (the pin is a hardcoded `frozenset`) | itself |
| 11 | `tests/test_ci_selection.py` | MODIFY | config invariants | itself |
| 12 | `tests/test_canary_classify.py` | MODIFY | the 16 existing `classify(...)` call sites + the cross-writer schema assertion | itself |
| 13 | Ledger `${tempfile.gettempdir()}/pi-embedded-evidence/{selection}/{commit12}/{invocation_id}/` | ARTIFACT (out of repo) | filesystem | `test_embedded_evidence.py::test_ledger_path_is_under_tmpdir_and_not_the_repo`, `::test_tmpdir_resolved_in_python_not_shell`, `::test_concurrent_writers_do_not_collide` (**C13**: same-namespace case), `::test_prune_does_not_remove_a_live_run_file` |
| 14 | Streak `…/embedded-evidence-streak.json` | ARTIFACT (new name) | the docker streak file must never be touched | `::test_streak_name_is_not_the_ci_file` |
| 15 | `--record-out <path>` | ARTIFACT (caller-chosen, **outside the measured tree** — #4572 option (a); the closing receipt defaults to `$TMPDIR/pi-embedded-evidence/record.json` and is copied/uploaded afterwards) | filesystem | `::test_in_tree_record_out_is_refused` (the out-of-tree boundary — M5/**M45**, superseded by #4572), `::test_tracked_record_out_is_exit_2`, `::test_record_out_directory_is_exit_2`, `::test_unwritable_record_out_parent_is_exit_2` (the failure exits — M46) |
| 16 | CLI entry `python3 tools/embedded_evidence.py run\|red` | ENTRY | `sys.path` (F27) | `::test_cli_entry_is_direct_python`, `::test_module_imports_sibling_classifier_via_repo_root` |
| 17 | `.husky/pre-commit` — the `ci_selection --register` hook (**M43**) | WRITER (indirect) | `config/ci-surfaces.yml` is mutated + `git add`-ed whenever a staged `tests/**.py` change is unregistered | auto-registration IS the intended mechanism (the #1429 drift trap): task 4's commit auto-registers `tests/test_embedded_evidence.py` into `core`, task 11's registers the tripwire. Task 12 therefore only adds `carve_out:` + `TEST_NO_REDIRECT_STEMS` (+ the pin) and **verifies** the `core:` entries rather than re-adding them (`::test_new_embedded_tests_are_carve_out_and_core`, `::test_integrity_covers_all_test_files`); the manifest write is not a `tests/` path, so AC10's allowlist is affected **only** by the M24 shared read in `tests/test_tripwire.py` (**C11** — the seventh path, `::test_git_diff_allowlist`) |
| — | `.gitignore`, `.github/**`, `tortoise/**`, `graph-scripts/**`, `pyproject.toml`, `uv.lock` | **NOT TOUCHED** | — | AC10 |

**Auth surface: n/a (M46).** This is a **local CLI** — it reads git and the local filesystem and starts a
local redislite daemon; it makes **no network call and reads no credential**. There is no auth boundary to
test, and the absence is declared here rather than left implicit (the table above has no auth row for that
reason).

**Unmapped failure exits, now mapped (M46).** Three failures the map previously left undefined:

- **`red`'s `git worktree add` / ref resolution fails** (non-existent ref, ambiguous short SHA, pruned
  object) ⇒ **exit 2** (environment: the measurement cannot be taken), recorded, `mkdtemp` removed, no
  partial `.git/worktrees` registration — `test_nonexistent_ref_is_exit_2`,
  `test_ambiguous_short_sha_is_exit_2`, `test_worktree_add_failure_leaves_no_registration` (Task 8).
- **an unwritable `--record-out` parent** (or a directory / a tracked file) ⇒ **exit 2** —
  `test_unwritable_record_out_parent_is_exit_2`, `test_record_out_directory_is_exit_2`,
  `test_tracked_record_out_is_exit_2` (Task 7/9).
- **`--record-out` omitted** ⇒ **not an error**: it defaults to `LEDGER_ROOT/record.json` (D6, M46), recorded
  as `record_out_source: "default"` — `test_record_out_defaults_to_ledger_record_json` (Task 9).

**Bug-pattern flags (from `test-design`):**

- **Cross-writer contract drift** (#7) — two writers, one schema, nothing asserts agreement today → the
  cross-writer assertion is a task, not a nicety.
- **Silent-selection shrink** (#5/#6) — a marker/`--deselect`/discovery change removes nodeids with no
  trace in the pass/fail result → the record stores the resolved nodeid **set + counts**.
- **Env-read leak into a pure classifier** (#7) — `tests/test_canary_classify.py:302` pins
  `"os.environ" not in code`; the lane gate must be **parameter**-fed (F28).
- **Fail-open on an unverifiable measurement** (#13/#15) — a census that cannot run, a provenance that
  cannot be read, or a load band that does not overlap must be a **red**, never a pass (F24/F17).
- **Registration drift** (#2/#3) — an unregistered test file reds the merge-blocking `manifest-integrity`
  job; a carve-out file without a `TEST_NO_REDIRECT_STEMS` entry silently flips to the server lane (F22).

### Per-surface failure modes (≥2 each, with the pinning test — M44)

The table above names each boundary; this table names what **breaks** at it and which test pins the
behaviour, so no surface is covered only by a happy-path row (R2-10: `tools/embedded_evidence.py`,
`docs/00_index.md` and `config/ci-surfaces.yml` previously had no stated failure behaviour).

| # | Surface | Failure mode A → pinned by | Failure mode B → pinned by |
|---|---|---|---|
| 1 | `tools/embedded_evidence.py` | a conjunct passes vacuously with no work done → `test_empty_runs_is_not_closing`, `test_default_n_is_not_hardcoded` | a git/measurement error raises a traceback instead of exit 2 → `test_git_error_fails_closed` (M18) |
| 2 | `tests/test_embedded_evidence.py` | the new file is unregistered → `test_integrity_covers_all_test_files`, `test_new_embedded_tests_are_carve_out_and_core` | the injected `runner=` seam is bypassed, so a fixture test silently hits real git/subprocess → `test_run_once_is_injectable`, `test_tree_move_between_runs_sets_tree_moved` |
| 3 | `tests/test_embedded_save_tripwire.py` | a site the scanner does not read keeps a live `--save` → `test_every_declared_lane_site_disables_periodic_saves`, `test_declared_site_count_is_pinned` | the AOF axis passes by omission → `test_declared_aof_config_is_bounded` (M9) |
| 4 | `docs/ops/embedded-lane-evidence.md` | the runbook drifts from the tool's bucket/exit constants → `test_runbook_matches_tool_constants` (M32) | the runbook omits the non-closing role semantics → `test_runbook_states_record_role_and_honest_limit` (M31) |
| 4b | `docs/00_index.md` (the runbook's index row) | the row is missing so the runbook is unreachable → `test_runbook_registered_in_docs_index` | the row's link text drifts from the real path → the same test (it asserts the literal `docs/ops/embedded-lane-evidence.md` appears) |
| 5 | `config/ci-surfaces.yml` — `embedded_family:` | the family selection loses its mandatory reproducer → `test_family_requires_reproducer` | the family list silently omits a lane class → `test_family_reproducers_relation_is_asserted` (M23) |
| 6 | `config/ci-surfaces.yml` — registration + `carve_out:` | a new test file is unregistered → `test_integrity_covers_all_test_files` | a carve-out file lacks the redirect-exemption stem → `test_no_redirect_stems_registry_exact` |
| 7 | `tools/testdb_canary_classify.py` — lane + streak declaration | a caller omits `lane=` (silently ungated) → `test_every_classify_call_site_passes_a_lane` (M6) | the READ path bypasses `SCHEMA_FIELDS` → `test_load_prev_streak_records_validate` (M25) |
| 7b | classifier CLI + `canary-streak` job | the CLI stops threading `--lane` (docker CI behaviour changes) → `test_classifier_cli_threads_lane` | a new `classify(` call site is added without a lane → `test_every_classify_call_site_passes_a_lane` |
| 7c | `tools/lane_contract.py` | a second lane spelling diverges from the axis → `test_lane_vocabularies_agree` (M24) | the child env leaves `TORTOISE_TEST_CARVE_OUT` in place → `test_child_env_pops_lane_vars_and_sets_carve_out` |
| 8 | `tools/ci_selection.py` — `TOOL_CARVEOUTS` | a tool-only change drops to tier-1 smoke (the #3261 class) → `test_embedded_evidence_tool_change_fails_closed_to_full` | the classifier's own surface is not carved out → `test_testdb_canary_classify_tool_change_fails_closed_to_full` |
| 9 | `tests/_embedded.py` — `TEST_NO_REDIRECT_STEMS` | the tripwire is redirected to the server lane (vacuous `CONFIG GET`) → `test_no_redirect_stems_registry_exact` | a stem names a non-existent module → `test_no_redirect_stems_exist_as_modules` |
| 10 | `tests/test_markers.py` — the exact-set pin | the hardcoded `frozenset` drifts from the registry → `test_no_redirect_stems_registry_exact` | a member is added to the tuple but not the pin → same test |
| 11 | `tests/test_ci_selection.py` | an invariant test is weakened until `--integrity` passes → `test_integrity_covers_all_test_files` (must stay `[]`) | the registry relation lives only in a comment → `test_family_reproducers_relation_is_asserted` (M23) |
| 12 | `tests/test_canary_classify.py` | the 16 call-site assertions are weakened by the lane edit → `test_docker_lane_unchanged_at_all_16_call_sites` | the two reset-semantics homes drift → `test_bucket_reset_semantics_agree_across_modules` (M11) |
| 13 | Ledger dir | a relative `--ledger-root` writes inside the checkout → dirties the pin → `test_relative_ledger_root_inside_measured_root_is_exit_2` (M56) | `TMPDIR` unset writes to `/pi-embedded-evidence` → `test_tmpdir_resolved_in_python_not_shell` |
| 14 | Streak file | the tool overwrites the docker streak → `test_streak_name_is_not_the_ci_file` | a stale streak is read as authority → `test_streak_is_never_read_as_authority` (M14) |
| 15 | `--record-out` | an in-repo receipt dirties the pin → `test_in_tree_record_out_is_refused` (M5, superseded by #4572: exit 2, outside-the-tree required, no exclusion) | a tracked/directory/unwritable target → `test_tracked_record_out_is_exit_2`, `test_record_out_directory_is_exit_2`, `test_unwritable_record_out_parent_is_exit_2` |
| 16 | CLI entry | a `[project.scripts]` entry is added → `test_cli_entry_is_direct_python` | the sibling classifier import relies on cwd → `test_module_imports_sibling_classifier_via_repo_root` |
| 17 | `.husky/pre-commit` (M43) | auto-registration lands a file in the wrong surface → `test_new_embedded_tests_are_carve_out_and_core`, `test_integrity_covers_all_test_files` | the hook stages `config/ci-surfaces.yml` (a non-`tests/` path) → `test_git_diff_allowlist` (config is outside the seven-path allowlist) |

---

## Journey Test Map

No user-facing journeys (CLI + library). The one journey that matters is the **falsifier's**, because
nothing consumes `closes_issue` but a human (D18).

### Journey: a reviewer falsifies the receipt without trusting the tool

1. **Step:** **re-run the command and observe the real process exit code** (do **not** trust the
   receipt’s own `exit_code` field — it is self-declared and, per D20, content-bound not authenticated;
   treat it as advisory) → **Acceptance:** `closes_issue` is machine-readable and, when false, the observed
   exit code is non-zero → **Test:** `test_closes_issue_truth_table`, `test_exit_code_precedence`,
   `test_exit_code_equals_persisted_and_process_returncode` (M1/M33).
2. **Step:** re-run the recorded `reproduce` command → **Acceptance:** the same selection, the same commit
   (`--ref <pin.commit>` is IN the recorded string — M28), the same manifest digest (re-derived and
   compared) → **Test:** `test_reproduce_command_is_self_contained` (Task 9).
3. **Step:** check the pairing by hand → **Acceptance:** `red.ref` equals the declared
   `pin.pairing_ref` (an explicit `--pairing-ref`, a strict ancestor of `pin.commit`; C1) and
   `red.cause` is labelled from server-side `redis.log` evidence, not from the pytest message → **Test:**
   `test_red_cause_is_labelled_from_redis_log_not_the_pytest_message`,
   `test_pairing_ref_must_be_strict_ancestor`.
4. **Step:** check the environment claim → **Acceptance:** `environment`, `hermeticity` and `load` bands
   are present and the red/green bands overlap → **Test:** `test_load_bands_must_overlap`,
   `test_hermeticity_is_computed_not_declared`.

### Failure Modes

- Tool unreachable by CI selection (a `tools/`-only PR drops to tier-1 smoke) → **Expected:** fail-closed
  full matrix → **Test:** `test_embedded_evidence_tool_change_fails_closed_to_full`.
- Tripwire not actually embedded under the docker redirect → **Expected:** a listed
  `TEST_NO_REDIRECT_STEMS` stem + the exact-set pin → **Test:** `test_no_redirect_stems_registry_exact`.
- Record written but `closes_issue:false` and exit 0 (the false-PASS class) → **Expected:** exit 3
  `NOT-CLOSING` → **Test:** `test_exit_3_on_non_closing_clean_run`.

---

## Verification Plan

Step C.5 of the skill (`test-routing`) with the complexity ratings from the issue (`Config: standard`,
UX/Architecture/Ontology/Accessibility all `low`; no UI surface).

| Layer | Applies | Depth | Notes |
|---|---|---|---|
| unit | ✅ | full | `tests/test_canary_classify.py`, `tests/test_ci_selection.py` fixtures — bucket map, lane gate, schema subsets, exit precedence, truth table |
| integration | ✅ | full | `tests/test_embedded_evidence.py` — each claimed boundary is delivered by a **named** test, not just claimed (M21): real `git worktree add` (Task 8 `test_red_leaves_no_worktree_behind`), real subprocess pytest child (Task 6 `test_real_subprocess_child_import_provenance`), real embedded server (Task 11's live axis), real `embedded_orphans.census()` (Task 10 `test_real_census_invocation`). The loop/provenance/digest tests are fixture-driven via the injected `runner=` and are labelled as such |
| e2e smoke / full | ❌ | — | no user-facing journey, no browser surface |
| ux-verification | ❌ | — | `UX=low`, zero UI files |
| pgTAP / SQL | ❌ | — | no DB business logic changes; the tool only *reads* a census |
| docs | ✅ | light | `docs/00_index.md` row + the new runbook (F26) |
| non-code (content/research/config) | ⚠️ config | — | config domain = the `config/ci-surfaces.yml` guards + `ci_selection --integrity` |

Commands (repo conventions, `AGENTS.md`):

```bash
# docker-free lanes (classifier, selector, markers)
env -u TORTOISE_DB_URI uv run pytest tests/test_canary_classify.py tests/test_ci_selection.py tests/test_markers.py -v

# the embedded lanes (tripwire + the runner's own integration tests)
env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embedded_save_tripwire.py tests/test_embedded_evidence.py -v

# the merge-blocking registration gate
python3 tools/ci_selection.py --integrity
```

---

## Design Decisions

Every decision below is **re-derived from the scoping + solution records**; nothing here invents new
direction. Where the records left a gap, the decision is marked **[GAP]** and is repeated in
[§ Under-specified points](#under-specified-points--decisions-taken--highest-risk).

### D1 — Scope split: PR #1 = the RED half; the GREEN half carries no code

**Decision.** PR #1 delivers `tools/embedded_evidence.py` (`run` + `red`), the committed tripwire, the
tests, the config/module edits, and a **cold, non-closing** receipt for the pinned ref. The GREEN half
(`run --selection family --n 10` at a fixed commit) is a documented handoff with **no code in PR #1**.
#3827 is not closed by PR #1.

**Alternatives.** (a) Deliver both halves now — impossible, there is no green commit (SCOPE corrections
(i)); (b) deliver only the tripwire and defer the whole runner — rejected, it discards the two genuinely
new parts (commit pinning, paired red) that the problem diamond identified (SCOPE §Rejected alternatives F1).

**Why.** #3847(b) requires **both** halves; a green certificate against the 26-file `carve-out` selection —
which contains **none of the family's *mandatory* reproducer** (`test_dr_endpoints.py`; it does contain two
of the three family files, `test_hosted_backup.py` and `test_backup_e2e.py`, but **cannot demonstrate the
family's red** — C19) — would be a **vacuous green**: worse than none, because it gets quoted later as
evidence.

### D2 — `BUCKET_MAP`: field shape and the 11-bucket closed set

**Decision.** `BUCKET_MAP` is a `dict[str, dict]` in `tools/embedded_evidence.py`, one entry per local
bucket, nine fields per entry:

```python
BUCKET_MAP: dict[str, dict] = {
    "<local-bucket>": {
        "origin":      "carried" | "mapped" | "new",   # relation to the CI ladder
        "ci_bucket":   "<name in testdb_canary_classify's ladder>" | None,
        "resets_streak": bool,   # does this bucket break "consecutive"?
        "is_red":      bool,     # does it count as an observed failure?
        "exit":        0 | 1 | 2,  # the exit code a run with this bucket implies
        "closes_ok":   bool,     # may a run with this bucket be part of a closing receipt?
        "hermetic":    bool,     # does the run still tell us anything about the code?
        "severity":    "green" | "warn" | "red",
        "detail_key":  str,      # the record field carrying the evidence
    },
    ...
}
```

The **closed set is exactly 11** (SCOPE Step 0 + F29's `slow-run` split):

| # | bucket | origin | ci_bucket | resets_streak | is_red | exit | closes_ok |
|---|---|---|---|---|---|---|---|
| 1 | `green` | carried | `green` | no | no | 0 | yes |
| 2 | `guard-red` | carried | `guard-red` | yes | yes | 1 | no |
| 3 | `manifest-red` | carried | `manifest-red` | yes | yes | 1 | no |
| 4 | `unexpected-divergence` | carried | `unexpected-divergence` | yes | yes | 1 | no |
| 5 | `env-red` | mapped | `infra-flake` | yes | yes | **2** | no |
| 6 | `timeout-red` | mapped | `step-wall-gate` | yes | yes | 1 | no |
| 7 | `divergence` | mapped | `divergence` | no | no | 0 | **no** |
| 8 | `lane-red` | new | — | yes | yes | 1 | no |
| 9 | `selection-red` | new | — | yes | yes | 1 | no |
| 10 | `unexpected-bucket` | new | — | yes | yes | 1 | no |
| 11 | `slow-run` | new (F29) | — | **no** | **no** | 0 | **yes** |

**Alternatives.** (a) Reuse the CI vocabulary **wholesale** — a category error (SCOPE (g)): `step-wall-gate`
is the 3300 s CI job watchdog and `infra-flake` is docker-service-down; neither has a local analogue, and
`divergence` is the CI D1–D16 registry. (b) Fork a separate vocabulary — forbidden (no synonyms; SCOPE
(d′)). (c) Drop the un-mappable buckets — rejected, a dropped bucket means a genuine embedded infra
failure gets mislabelled a parity violation.

**Why.** `unexpected-bucket` (must stay `is_red`/`exit 1`) is what makes the mapping *closed*: a bucket
`classify()` returns that is not in the map is a **parity violation**, never a silent default. `slow-run`
is split from `timeout-red` (F29) because a run that **passed** but exceeded the recorded baseline is
evidence about the host, not a failure — it must not reset the streak, it **advances** it (M12: a passing
run counts, else D9 conjunct 1 contradicts `closes_ok: yes`), and it must not be hidden. `env-red` is
exit **2**, not 1 (M13): a census that cannot run is an *environment* error — the measurement is
impossible, not a violation of the code — and this matches D17's "the tool's exit 2 path" and GAP-8.
**C4: `env-red` is also the bucket for a census that runs but is not usable** — `inconclusive
(live_servers > 0 and unclassified == live_servers)` or `orphans > max_orphans`, the two keys the imported
`census()` does **not** return (they are added only in `embedded_orphans.main()`): an inconclusive census
attributed nothing, and filtering it to an empty delta made `hermeticity.status == "clean"` on a census
that learned nothing — the fail-open #3599 exists to refuse.
The whole set is also asserted against `BUCKET_RESETS_STREAK` (D4, M11).

**Carried-name pin.** `test_bucket_map_carried_names_exist_in_the_classifier_ladder` asserts each of the
four `origin == "carried"` entries' `ci_bucket` appears as a returned bucket value in
`tools/testdb_canary_classify.py`'s source, so the map cannot drift from the ladder it claims to carry.

**Ladder pin (M38 / C16).** The classifier's ladder names are a **declared constant** — and its **producer**
is where it lives: `CI_LADDER_BUCKETS` is declared in `tools/testdb_canary_classify.py` (the module that owns
the seven `bucket = "…"` literals at `:278–351`) and **imported by** `tools/embedded_evidence.py`. Declaring
it in the consumer made the cross-check a self-assertion — the runner was asserted consistent **with
itself**, so a rename of `infra-flake` / `step-wall-gate` / `divergence` **in the classifier** broke
nothing and M38's "a rename of `infra-flake` now breaks the assertion" was false. Two pins now cover it:
(i) `test_bucket_map_ci_bucket_column_equals_the_declared_ladder` asserts
`{spec["ci_bucket"] for spec in BUCKET_MAP.values() if spec["ci_bucket"]} == set(CI_LADDER_BUCKETS)`, and
(ii) the string-match pin is extended to **every** `ci_bucket` value — not `origin == "carried"` only —
so each of the four `carried` **and** the three `mapped` names must appear as `bucket = "…"` in the
producer's source. Separately,
`test_divergence_is_unreachable_without_a_divergence_log` pins F9's reachability claim — with
`divergence_log=None` the classifier can never settle the `divergence` bucket.

### D3 — the `lane=` parameter contract

**Decision.** `classify()` gains a **required keyword-only** `lane: str`:

```python
# tools/lane_contract.py — the ONE lane vocabulary + the ONE child-env registry (M24).
# A non-test-loaded module (NOT under tests/, so the tripwire's child never imports it as a test).
LANE_AXIS = ("embedded", "docker")                 # the canonical axis (ask_recall_bench's vocabulary)
LANE_ALIASES = {"embedded-local": "embedded",      # this plan's runner alias
                "docker-half-b": "docker",         # the CI classifier's alias
                "server": "docker"}                # tests/_embedded.py BackendIdentity's alias
def axis(lane: str) -> str: ...                     # unknown lane -> ValueError (never a default)
CHILD_LANE_VARS = ("TORTOISE_DB_URI", "TORTOISE_TEST_EXPECT_URI",
                   "TORTOISE_TEST_ALLOW_REMOTE", "TORTOISE_TEST_NO_REDIRECT",
                   "TORTOISE_TEST_JOURNAL_FILE", "TORTOISE_DB_PATH",
                   "TORTOISE_EMBEDDED_AOF", "TORTOISE_ALLOW_NONSTANDARD_PATH",
                   "TORTOISE_TEST_CARVE_OUT")       # M24: the 9th var — set by the runner AND
                                                    # recorded, so the old 8-name pop was partial

LANES = tuple(LANE_ALIASES)                         # READ from the contract, never redeclared

def classify(junitxml, manifest, step_wall, divergence_log, prev_streak, run_id,
             *,                                  # everything below is keyword-only
             lane: str,                          # REQUIRED — no default (F23)
             threshold: int | None = None,       # required for embedded-local (sentinel)
             step_wall_gate: int = STEP_WALL_GATE_SECONDS,
             producer_marker: str | None = None,
             run_provenance: dict | None = None,
             lane_shape: dict | None = None) -> dict:   # C18: runner-computed env lane shape
```

Contract:

1. **`lane` is required.** Omitting it is a `TypeError`; an unrecognised value is a `ValueError`. There is
   no "ungated" path.
2. **`lane="docker-half-b"`** — population gate is the **existing** marker gate
   (`half == 'b'` ∧ `full == 'true'`), semantics unchanged; `threshold` defaults to
   `CANARY_DROP_THRESHOLD`; behaviour byte-identical to HEAD for all 16 existing call sites.
3. **`lane="embedded-local"`** — the marker gate is **not used**. The population gate is derived from
two runner-computed, plain-data inputs (C18): the **`lane_shape`** the runner read from the shared
registry (`lane_contract`; `{uri_unset, carve_out, expect_uri}`) and the **`run_provenance` record**:
`lane_shape.uri_unset` ∧ `lane_shape.carve_out == "1"` ∧ ¬`lane_shape.expect_uri` ∧ `measured_root`
present ∧ `porcelain_digest` present. A wrong env lane settles **`lane-red`** (C18's single definition);
an out-of-root `import_provenance` is **not** this bucket — it is gate 1's `UNATTRIBUTABLE` (exit 1,
D8 / D9 conjunct 4). The classifier **computes** the predicate from the records; it never accepts a bare
`worktree_clean=True` boolean (F23) and never reads `os.environ` (F28) — the lane shape is passed in as
data.
4. **`threshold` for `embedded-local` is required, must not be `CANARY_DROP_THRESHOLD`, and must EQUAL
   `n.requested` (C5).** A `None` (omitted), a `5`, or any value `!= n.requested` is a `ValueError` — the
   embedded lane must state its own non-drop threshold explicitly (F13/F28) and `_settle` caps
   `consecutive_green` at `threshold`, so a threshold **above** the requested N made D9 conjunct 1
   unsatisfiable (`--n 10 --threshold 20` ⇒ `reached_n` false) with no exit code catching it. Pinning
   equality makes `reached_n` the requested-N flag.
5. The returned streak record carries `lane` (see D4).
6. **The CLI is the production caller, and it must be accounted for (M6).**
   `tools/testdb_canary_classify.py:409` (`main()`) calls `classify(...)` and is the **only CLI caller**
   (the merge-blocking `canary-streak` job, `.github/workflows/python-ci.yml:1751`, sits in
   `python-ci-gate`'s `needs:` at `:1865`). `main()` gains `--lane` **defaulting to `"docker-half-b"`**
   and threads `lane=args.lane` into the `:409` call. This is NOT a contradiction of F23/GAP-2: the
   *function* parameter stays required keyword-only; only the CLI parser supplies a default, because
   `.github/**` is NOT TOUCHED (AC10) and the docker CI job passes no flag. A CLI smoke test exercises
   the path; a predicate test scans every `classify(` call in the files that consume THIS classifier
   (scoped per C10 — a repo-wide sweep reds on `tests/test_analyze.py`'s unrelated
   `tortoise.analyze.classify`) so an un-updated caller cannot recur.
7. **ONE child-env registry (M24).** The runner's child-env set is
   `lane_contract.CHILD_LANE_VARS` — the same declaration the tripwire's `_CHILD_LANE_VARS` reads
   (`tests/test_tripwire.py:42–51` today; a *test* module that a `tools/` runner must not reach into).
   `TORTOISE_TEST_CARVE_OUT` is the **9th** entry: the runner sets it, the receipt records it, and the
   old 8-name pop left it in the child env. **The shared read is implemented by editing
   `tests/test_tripwire.py`** — which makes it the **seventh** `tests/` diff path (C11: AC10's allowlist,
   `test_git_diff_allowlist`, GAP-1 and the surface-map row that claimed the allowlist was unaffected are
   all corrected), pinned by a shared-read assertion in `tests/test_tripwire.py`.
8. **`threshold` must be `== n.requested` for `embedded-local` (C5; coherence with D9 conjunct 1).**
   `_settle` caps `consecutive_green` at `threshold`, so `threshold > n.requested` made conjunct 1
   unsatisfiable (`reached_n` can never be true) and `threshold < n.requested` made it trivially
   satisfiable. Equality is required; any inequality ⇒ exit 2.
9. **`step_wall` (a path) vs `step_wall_s` (seconds) are the SAME measurement (M53).** `classify()`'s
   third parameter is a **path** whose content is whole seconds (`_read_step_wall`, `:219`); the record's
   `runs[].step_wall_s` is the measured wall as float seconds. The runner writes the per-run measured wall
   to `<mkdtemp>/step_wall.txt` and passes that **path** as `step_wall`, passes
   `step_wall_gate=run_timeout` (the local lane's `DEFAULT_RUN_TIMEOUT_S = 900`, GAP-4; `--run-timeout >
   3300` ⇒ exit 2), and records the same number as `runs[].step_wall_s`. So `timeout-red` ←
   `step-wall-gate` has a declared local input: `wall >= step_wall_gate` (floored at the integer the file
   holds), and `subprocess.TimeoutExpired` maps to `timeout-red` with the run retained and the
   `mkdtemp` removed — never an abort (M19). `test_step_wall_path_and_seconds_agree` (Task 6) asserts the
   integer under the path equals `int(runs[].step_wall_s)`, and `test_timeout_expired_maps_to_timeout_red`
   pins the mapping.

**Lane vocabulary (M24).** One canonical axis (`embedded`/`docker`) with aliases, declared in
`tools/lane_contract.py` and read by **all four** consumers: `tools/ask_recall_bench.py:323`
(`choices=["embedded","docker"]`), `tests/_embedded.py:305–321 BackendIdentity.backend`
(`{"embedded","server"}`), the classifier's `LANES`, and the new runner. `test_lane_vocabularies_agree`
(Task 1) asserts the four collapse onto the same axis.

**Alternatives.** (a) `producer_marker=None` to defeat the gate — the shortcut the duplication review
called out (F2): "reusing a gated classifier by defeating its gate is a shortcut the next lane copies".
(b) A default `lane="docker-half-b"` (F14) — superseded by F23: a default makes the gate omittable, and an
omitted gate is a tautology. (c) Re-declare the 8 child-env names in the runner (the D4 duplicate) and
leave `TORTOISE_TEST_CARVE_OUT` out (M24) — rejected: the declaration is then incomplete for the new
consumer.

**Why.** `lane` required + keyword-only is the only shape where *forgetting the gate is a `TypeError`*
rather than a silently-ungated classification. **[GAP-2]** F14/F28's "backward-compatible default /
7th-positional" framing conflicts with F23; see § Under-specified points.

### D4 — the shared streak-schema declaration

**Decision.** One declaration in `tools/testdb_canary_classify.py`, consumed by **both** writers:

```python
SCHEMA_FIELDS: dict[str, dict] = {
    "run_id":            {"type": "int",   "lanes": ("docker-half-b", "embedded-local")},
    "lane":              {"type": "str",   "lanes": ("docker-half-b", "embedded-local")},
    "runs":              {"type": "list[int]", "lanes": ("docker-half-b", "embedded-local")},
    "consecutive_green": {"type": "int",   "lanes": ("docker-half-b", "embedded-local")},
    "last":              {"type": "dict(bucket,detail)", "lanes": ("docker-half-b", "embedded-local")},
    "canary_dropped":    {"type": "bool",  "lanes": ("docker-half-b",)},   # FORBIDDEN embedded
    "reached_n":         {"type": "bool",  "lanes": ("embedded-local",)},  # FORBIDDEN docker
    "resets":            {"type": "int",   "lanes": ("embedded-local",)},
    "drop_exemption":    {"type": "bool",  "lanes": ("embedded-local",)},
}

def streak_record(*, lane: str, run_id: int, runs: list[int], consecutive_green: int,
                  last_bucket: str, last_detail: str, **extras) -> dict:
    """The ONLY constructor for NEW streak records. Validates against
    SCHEMA_FIELDS[lane] and returns the record."""

def streak_from_prev(raw: dict | str | None, *, lane: str) -> dict:
    """The ONE constructor for the READ path (M25). `_load_prev_streak`
    routes through it so a dict read off disk cannot bypass SCHEMA_FIELDS.
    Returns a validated record; missing optional fields take declared defaults."""
```

Per-lane subsets:

| lane | required | forbidden |
|---|---|---|
| `docker-half-b` | `run_id`, `lane`, `runs`, `consecutive_green`, `last` | `reached_n`, `resets`, `drop_exemption`, `canary_dropped` (allowed, not required) |
| `embedded-local` | `run_id`, `lane`, `runs`, `consecutive_green`, `last`, `reached_n`, `resets`, `drop_exemption` | **`canary_dropped`** |

- `canary_dropped` is **docker-only** (AC7: `CANARY_DROP_THRESHOLD = 5` must appear nowhere as an embedded
  threshold; the embedded lane is drop-exempt by construction, `drop_exemption: false` is *recorded* to
  make the absence explicit).
- `reached_n` / `resets` / `drop_exemption` are **embedded-only**: `reached_n` is the same boolean
  `canary_dropped` means for docker (the streak reached the requested N), but it lives under its own name
  so a reader can never mistake an embedded record for a docker drop.
- **`reached_n` has exactly one producer (C5).** `_settle` (via `streak_record`) emits
  `reached_n = (consecutive_green >= threshold)`, and D3 rule 8 pins `threshold == n.requested` for the
  embedded lane — so `reached_n` IS "the requested N was reached". `_settle` grows the `requested`
  parameter it previously lacked (or `streak_record` receives it); either way the field is computed, never
  caller-supplied. `test_reached_n_is_true_for_a_full_n_green_run` (Task 2) is the **positive** test —
  Task 2's prior fixture only ever set it `False`.
- **`resets` vs `resets_streak` (M47).** The streak field `resets` is an **int counter** (embedded lane
  only). The bucket-level predicate is `BUCKET_MAP[*].resets_streak` (D2) — a **bool**, and the only thing
  `_settle`'s reset rule reads (`BUCKET_RESETS_STREAK`). The two are deliberately different names: the D2
  table column is headed `resets_streak` (never `resets`), so a reader cannot conflate the streak counter
  with the bucket predicate.
- `lane` is **added to the docker record too** (additive; `_load_prev_streak` reads only
  `runs` / `consecutive_green` / `canary_dropped`, so the committed `config/testdb-canary-streak.json`
  remains readable and the extra key is inert).

**In-invocation accumulation — the OWNER is stated (C6).** The runner owns the fold: for run `k > 1` it
passes `prev_streak` = the record built by `streak_record()` / `_settle()` from runs `1..k-1` — the
**in-invocation accumulator** — and run 1 alone gets `prev_streak=None`. `prev_streak=None` on the first
call is what keeps the on-disk `STREAK_FILE` out of the gate (M14: the file is written as an artifact and
never read as authority), **not** a claim that every run settles from nothing. The prior text's "the runner
passes `prev_streak=None` … so `consecutive_green` is derived only from this invocation's `runs[]`" was
self-contradicted: `_settle` is per call, so `None` on every call left each run at `consecutive_green == 1`
and the N-run value had **no owner**. `test_in_invocation_streak_accumulates` (Task 6) asserts a synthetic
3-green invocation records `consecutive_green == 3`.

**Reset semantics — ONE declaration (M11).** The streak-transition rule is declared **here, once**, and
`_settle` consumes it instead of the inline `reset_buckets` literal at
`tools/testdb_canary_classify.py:364` (which Task 2 does not otherwise touch) — and instead of the
runner's `BUCKET_MAP[*].resets_streak`:

```python
# Every name `classify()` can return MUST appear here; an unknown name is a `ValueError`
# (fail closed — never a silent preserve, the `else` branch that made `lane-red` non-resetting).
BUCKET_RESETS_STREAK: dict[str, bool] = {
    "green": False, "guard-red": True, "manifest-red": True,
    "unexpected-divergence": True, "infra-flake": True, "step-wall-gate": True,
    "divergence": False, "lane-red": True, "env-red": True, "selection-red": True,
    "unexpected-bucket": True, "timeout-red": True, "slow-run": False,
}
BUCKET_ADVANCES_STREAK: frozenset[str] = frozenset({"green", "slow-run"})   # M12
```

- The **embedded buckets are in the map**, so `lane-red` / `env-red` / `timeout-red` / `selection-red` /
  `unexpected-bucket` all zero `consecutive_green` — AC7 ("Red restarts the streak to 0, retained") now
  holds for the new buckets, which previously fell to `_settle`'s `else` (streak preserved).
- `slow-run` **advances** the streak (M12): it is a *passing* run that exceeded the recorded baseline
  (`is_red: no`, `exit: 0`, `closes_ok: yes` in D2), so `_settle` must count it exactly like `green` —
  otherwise a receipt containing any `slow-run` can never satisfy D9 conjunct 1, contradicting
  `closes_ok: yes`. It does **not** reset.
- `BUCKET_MAP[b]["resets_streak"] == BUCKET_RESETS_STREAK[b]` for every shared name — asserted by
  `test_bucket_reset_semantics_agree_across_modules` (Task 12), so the two modules cannot drift.
- `test_lane_red_zeroes_the_streak` asserts `consecutive_green == 0` after a `lane-red`; the `n=10` test
  `test_slow_run_advances_the_streak` pins `2×green + 1×slow-run + 7×green` (Task 2 Step 1).

**Read-path truthfulness (M25).** `streak_record()` is the only constructor **for NEW records** —
`tools/testdb_canary_classify.py:95–111 _load_prev_streak` also builds streak-shaped dicts directly
(`:97` and `:104–109`) and is the *read* path. It is migrated through `streak_from_prev(raw, lane=...)`
and Task 2 asserts every dict it returns validates against `SCHEMA_FIELDS` for its lane. Separately,
`tortoise/backup_sweep.py:1303–1370` (`graph_error_streaks`) is **not** a third writer of this contract:
it is a per-key **ops-state map** (not a run list), bounded at 500 keys, read/written by the sweep — a
deliberately different contract, recorded here so the third streak writer is acknowledged rather than
missed.

**Alternatives.** (a) Two schemas, one per writer — the drift the duplication review flagged (F6).
(b) A single flat field set with per-lane optionality documented in prose — no test can then falsify it.
(c) Put the embedded lane's fields in the *new* tool only — the cross-writer assertion becomes impossible.
(d) Leave the reset semantics in `_settle`'s literal and the runner's `BUCKET_MAP` — the two homes that
M11 found already disagreeing (embedded buckets absent from `reset_buckets`).

**Why.** Two writers touch one contract; today `_settle`'s return shape is implicit and nothing asserts the
writers agree. Declaring the field→lanes map, the reset semantics **and** a single constructor makes
agreement a test (`test_cross_writer_streak_schema_agrees`, `test_bucket_reset_semantics_agree_across_modules`).

### D5 — the selection contract: three named selections from ONE declaration

**Decision.** `--selection {family, carve-out, whole-suite}`, resolved from a single new declaration in
`config/ci-surfaces.yml`:

```yaml
embedded_family:
  # #3827: the ONE declaration site for the embedded-lane named selections.
  # Recognised by tools/embedded_evidence.py; INERT to tools/ci_selection.select()
  # (pinned by tests/test_ci_selection.py::test_embedded_family_declaration_is_inert_to_selection).
  family_reproducers:            # the family's evidence selection (the default)
  - test_dr_endpoints.py         # MANDATORY — the only selection that demonstrates the family's red
  - test_hosted_backup.py        # #3846 §4 exception: the POST /backups/restore route is product-relevant
  - test_backup_e2e.py
  diagnostic_only:
  - carve-out                    # the 26-file set: contains NONE of the family's MANDATORY reproducer
                                 # (test_dr_endpoints.py); it DOES contain test_hosted_backup.py and
                                 # test_backup_e2e.py (C19) but cannot demonstrate the family's red
  - whole-suite                  # supported, never the family's evidence
  expected_causes:               # CAUSE labels (F15); values are tools/embedded_evidence.CAUSE_CLASSES keys
                                 # DERIVED in code (EXPECTED_CAUSES), never a hand-written copy: a class added
                                 # to DETECT a cause must be a cause the record EXPECTS, or the class that exists
                                 # to detect it invalidates the record (`cause-not-expected`) the moment it fires.
  - save-child-slot              # (renamed from `expected_signatures` — M29: "signature" held two meanings)
  - aof-rewrite-fork
  - module-fork-hang
  - module-fork-eexist           # EEXIST refusal with no save/AOF discriminator (the #3845 sibling)
  lane_mix:                      # M23: the family selection deliberately mixes two lane classes
    redirect_exempt: [test_hosted_backup.py, test_backup_e2e.py]  # in TEST_NO_REDIRECT_STEMS
    redirect_non_exempt: [test_dr_endpoints.py]                   # api-surface; NOT redirect-exempt
    reason: >
      test_dr_endpoints.py is the mandatory reproducer but is absent from carve_out: and
      TEST_NO_REDIRECT_STEMS; under a URI lane it would run against the server. The family
      selection is therefore a LANE-PINNED selection: it must be invoked with
      TORTOISE_TEST_CARVE_OUT=1 and no URI, and the receipt's environment.lane records
      {uri_unset: true, carve_out: "1", expect_uri: false}. The lane assertion — not the
      stems list — is what makes the family embedded. That recorded shape is read by D9 conjunct 14
      (`environment-lane-wrong`, C18) and asserted in the family receipt test.
  declared_sets:                 # M23: the named sets are declared HERE, with an asserted relation
    carve_out_job: "ci_selection.carve_out_files(manifest)"       # the 26-file leg list
    redirect_exempt: "tests/_embedded.py:157 TEST_NO_REDIRECT_STEMS"
    family_reproducers: "this block"
    relation: >
      family_reproducers is asserted to be a SUBSET of (registered surfaces ∪ carve_out) by
      test_family_reproducers_relation_is_asserted (Task 3) — a real cross-key membership
      relation, checkable today. The cross-LANGUAGE relation
      (carve_out ↔ TEST_NO_REDIRECT_STEMS, equality modulo bench/ prefix + .py suffix) is
      assigned to #3862 ONLY (F14); it is NOT asserted by this plan. See the amended
      Alternative (a) — the "false invariant" the F7 review named was a COMMENT claiming a
      test existed; this block states which relation is asserted and which lives in #3862.
```

Rules:

1. **`family` is the default** and **must contain `tests/test_dr_endpoints.py`** — a hard membership
   assert (`test_family_requires_reproducer`), not a comment.
2. **`carve-out`** resolves to `ci_selection.carve_out_files(manifest)` (the 26-file leg list). It is
   **diagnostic-only**: `closes_issue` is `false` for it **regardless of colour**, exit 3.
3. **`whole-suite`** = all surface members; supported, never the family's evidence; same rule.
4. **No second file-set receipt.** The manifest is produced by the reused
   `skip_guard.emit_manifest(files, marker, output)`; the record stores its path + sha256 + count. **A
   duplicated nodeid is `selection-red` (exit 1) (M58):** `_read_manifest` returns a **set** while
   `manifest.count` is a **line count**, so a duplicate makes `count != len(set(nodeids))` — the tool
   requires the two to agree rather than silently accepting a set of size < count.
5. **The selector is consumed verbatim and recorded, with the exact path form (M22).** For each declared
   file the runner calls `ci_selection.select(["tests/<name>"], "pull_request", manifest)` and the same
   with `"push"` — the path is **repo-relative** (`tests/test_dr_endpoints.py`). Verified in-repo:
   `select(["test_dr_endpoints.py"], …)` → `full=True, test_files="ALL"` (unknown-path fail-closed) while
   `select(["tests/test_dr_endpoints.py"], …)` → `full=False, surfaces=['api','core']`. Recording
   `selector{fn_version, per_file, events{push,pull_request}}`, where `per_file["<tests/-relative name>"]`
   is keyed by the declared name but **holds the repo-relative call's result**. Parity is a comparison of
   two **records**, never a re-derived list; `test_selector_record_is_emitted` asserts **values**
   (`per_file["test_dr_endpoints.py"]["pull_request"]["full"] is False` with `'api' in surfaces`), so a
   vacuously full-matrix record cannot pass.
6. **`--marker`** defaults to `"not track_b and not live"` (the repo's run filter —
   `config/ci-surfaces.yml`-consistent, pinned by `test_run_marker_matches_manifest_marker`).

**Alternatives.** (a) A hand-maintained `embedded_family:` list whose relation is claimed **in a comment**,
with no assertion — the F7 "false invariant"; the cross-language `carve_out` ↔ `TEST_NO_REDIRECT_STEMS`
relation is **assigned to #3862 only** (F14), and this plan does not claim it. What this plan *does* declare
is the same ONE block plus the membership relation it can assert today
(`test_family_reproducers_relation_is_asserted`, M23) and the deliberate lane mix (`lane_mix`, M23) — so the
list is not a fourth hand-maintained set whose only pin proves it inert. (b) Re-derive the list inside the
runner — forfeits the one-argument-list parity the research names as canonical.

### D6 — ledger path, `TMPDIR` resolution, streak filename

**Decision.**

```python
LEDGER_ROOT = Path(tempfile.gettempdir()) / "pi-embedded-evidence"     # resolved IN PYTHON
LEDGER_DIR  = LEDGER_ROOT / selection / commit[:12]                    # NAMESPACED (M14)
INVOCATION_DIR = LEDGER_DIR / invocation_id                            # C13: per-invocation namespace
STREAK_FILE = LEDGER_DIR / "embedded-evidence-streak.json"             # shared per selection/commit
LOCK_FILE   = LEDGER_DIR / ".lock"                                     # C13: flock around streak + prune
RUN_FILE    = INVOCATION_DIR / f"run-{run_id:03d}.json"                # one per run (M15)
RECORD_FILE = INVOCATION_DIR / "record.json"                          # the final receipt (M15)
LEDGER_HISTORY_BOUND = 20                                              # M60: prune older retained outputs
                                                                      # (mirrors the docker streak's runs[:20])
```

`invocation_id` is a `uuid4().hex` recorded in `environment.invocation_id` (D7).

- **`TMPDIR` is resolved in Python**, never as shell syntax (F29): a `"${TMPDIR}/…"` literal in a Python
  string writes to `/pi-embedded-evidence` when `TMPDIR` is unset. `tempfile.gettempdir()` honours
  `TMPDIR`, then `TEMP`/`TMP`, then `/tmp`.
- The resolved root, the source (`tempfile.gettempdir()` vs `--ledger-root`) and pytest's tmpdir base are
  **recorded** in `environment{ledger_root, tmpdir, tmpdir_source, pytest_tmpdir_base}`.
- `--ledger-root <path>` overrides; the ledger dir is `mkdir(parents=True, exist_ok=True)`. A **relative**
  `--ledger-root` is resolved against cwd and refused (exit 2) if it lands inside the measured root
  (M56's relative-path edge; D8 exit-2 list). **Every filesystem-input edge is exit 2 with no traceback
  (M56):** an unwritable ledger root, `TMPDIR` empty (falls back via `tempfile.gettempdir()`) or set to a
  non-writable path, and a `--record-out` that is a **directory** (an unwritable parent is already in D8)
  — each leaves `git status --porcelain` empty. An **omitted** `--record-out` has a stated default:
  `LEDGER_ROOT / "record.json"` (M46), recorded as `record_out_source: "default"`.
- **Retention and cleanup (M60 / C13).** Every per-run `mkdtemp` is removed by the run's `try/finally` —
  after a full invocation no per-run root survives (D17 measures orphans; it does not reap, so the runner
  owns this). The ledger is bounded: at most `LEDGER_HISTORY_BOUND = 20` retained **invocation** directories
  per `LEDGER_DIR`, older ones pruned — the same bound the docker streak applies
  (`tools/testdb_canary_classify.py:362`, `runs = runs[:20]  # bounded history`) and the same shape of
  bound `ci_timing.MAX_HISTORY_DEFAULT = 52` applies to its `history` (M62). **The prune never removes a
  live invocation (C13):** it skips any invocation dir whose `LOCK_FILE` is held or whose `started_at` is
  newer than the newest retained entry, so a concurrent `mkstemp`/`os.replace` cannot raise
  `FileNotFoundError`; `test_prune_does_not_remove_a_live_run_file` (Task 9) pins it.
- The streak file name is **new and different** (`embedded-evidence-streak.json`) — never
  `config/testdb-canary-streak.json`, which is docker, post-merge, N=5, CI-written, and not in-tree at
  HEAD (SCOPE (d′)).
- **The embedded streak is namespaced and is never read as authority (M14 / C6).** `STREAK_FILE` lives
  under `LEDGER_DIR` = `{selection}/{commit12}/`, so a run under a different selection or commit starts a
  fresh chain; **and** the runner passes `prev_streak` = its own in-invocation accumulator (run 1:
  `None`) to `classify()`, so `streak.consecutive_green` is derived only from *this invocation's* `runs[]`
  — never inherited **and never accumulated from nothing** (C6: `prev_streak=None` is passed on the first
  call only, to keep the file out of the gate; the N-run fold is owned by the runner, D4). The file is
  written as an artifact (a second writer of the D4 contract, under the `LOCK_FILE` read-modify-write of
  C13) but is not a closing input. `test_streak_is_never_read_as_authority` runs two sequential `n=2`
  invocations with the shared file present and asserts the second verdict derives only from its own
  `runs[]`.
- **`_write_atomic` is writer-safe (M15 / C13).** The classifier's `_write_atomic`
  (`tools/testdb_canary_classify.py:384–390`) uses a **fixed** staging name (`path.name + ".tmp"`): two
  concurrent writers both write that one file, so A can publish B's record and one `os.replace` can raise
  `FileNotFoundError`. The tool's writer uses a **unique** staging file in the same directory and an atomic
  rename (mkstemp + `os.replace` — D6). **The per-invocation namespace closes the lost-update hole the
  unique staging name does not:** two concurrent `run` invocations of the SAME selection+commit write to
  different `INVOCATION_DIR`s (C13), and the one shared mutable file, `STREAK_FILE`, is read-modify-written
  under `fcntl.flock(LOCK_FILE)` (`O_CREAT`, exclusive), so a loser cannot overwrite the winner's receipt.
  `test_concurrent_writers_do_not_collide` runs two simultaneous writers of the same selection+commit and
  asserts neither raises, the shared artifacts parse, and **each invocation's record carries its own
  `run_id` and `invocation_id`** — rewritten to exercise the same-namespace case (the prior version
  asserted that property against one path, which could not hold).
- **`--record-out` must be OUTSIDE the measured tree (M5, option (a), REVERSED by #4572).** The earlier
  ruling allowed an in-repo receipt and excluded it from `porcelain_digest` / `tree_moved`. That exclusion
  was re-derived five times (substring; `Path.resolve()` following a symlink; a pathspec without `literal`
  globbing; `:(exclude)X` also matching every `X/…`; a lexical-vs-kernel `link/../out` divergence) and
  over-matched every time, each spelling a way for a genuinely dirty tree to read clean. #4572 removes the
  class instead of guarding it: a resolved `--record-out` that lands inside the measured tree is a **usage
  error (exit 2, no record written)**, and there is now **no path-based exclusion at all**. The receipt
  defaults to `$TMPDIR/pi-embedded-evidence/record.json` — outside — and the GREEN-half handoff copies it
  into the fix PR's `docs/evidence/3827-green.json` afterwards (Surface Map row 15). The pin no longer
  carries `record_out_excluded`. Test: `test_in_tree_record_out_is_refused` (superseding
  `test_record_out_path_is_excluded_from_the_pin`). An unreadable/unwritable `--record-out` parent is still
  exit 2.

### D7 — the record schema `embedded-evidence/1`, field by field

**Decision.** One JSON object, written atomically via a **collision-safe** writer (unique staging name via
`tempfile.mkstemp` in the same directory + `os.replace` — M15/D6; the classifier's `_write_atomic` is NOT
reused verbatim because its fixed `.tmp` name is not writer-safe):

```jsonc
{
  "schema": "embedded-evidence/1",
  "attestation": "self-declared",              // F/DA §5.4: content-bound, not authenticated
  "record_role": "historical-attestation",     // | "closing"    (F16) — `closing` requires an explicit
                                               // --pairing-ref AND a same-invocation paired red (C1/M52)
  "tool_version": "<git blob sha of tools/embedded_evidence.py>",   // F14
  "created_utc": "<iso8601 Z>",

  "selection": {
    "name": "family",                          // family | carve-out | whole-suite
    "files": ["test_dr_endpoints.py", "..."],  // the tests/-relative names actually run
    "source": "config/ci-surfaces.yml:embedded_family.family_reproducers",
    "expected_causes": ["module-fork-hang", "module-fork-eexist", "aof-rewrite-fork", "save-child-slot"]
    // renamed from `expected_signatures` — M29: a "signature" was both a CAUSE LABEL and a HASH
  },
  "selector": {
    "fn_version": "1.3.0",                     // ci_selection.SELECTION_FN_VERSION
    "per_file": { "test_dr_endpoints.py": {"pull_request": {...}, "push": {...}} },
    "events": { "push": {...}, "pull_request": {...} }
  },
  "manifest": { "path": "...", "digest": "sha256:...", "count": 412, "marker": "not track_b and not live" },

  "pin": {
    "commit": "<40 hex>", "tree_object": "<40 hex>",   // the MEASURED commit: `git rev-parse
                                                      // [<--ref>|HEAD]^{commit}` (C12)
    "head_sha": "<40 hex>",                     // R2/D24: the REVIEWED head SHA the certificate is bound
                                                // to — equals `commit` at write time; any later head change
                                                // invalidates the record and requires a re-run (conjunct 16)
    "head_sha_verified_at": "<iso8601 Z>",      // R2/D24: when the mutation/red evidence was taken
    "post_review_dirty": false,                 // R2/D24: true iff an edit landed after the review round
                                                // that produced the evidence ⇒ closes_issue:false, exit 1
    "requested_ref": "<40 hex | null>",        // M28/C12: `run --ref`; when supplied the measured
                                               // worktree is `git worktree add --detach <mkdtemp> <ref>`
    "pairing_ref": "<40 hex | null>",          // C1/D16: `run --pairing-ref` — the pre-fix ref the
                                               // closing role pairs against; a STRICT ancestor of `commit`
    "worktree_clean": true,
    "porcelain_digest": "sha256:<over `git status --porcelain=v2`>",   // NOT `git write-tree` (AC4)
    "measured_root": "<abs>",
    "environment_pinned": false,                 // D12/F1: the red runs the ref's SOURCE, current env
    "import_provenance": { "tortoise_file": "<abs>", "in_root": true, "per_run": [...] }
  },

  "n": {
    "requested": 10, "mode": "explicit",       // explicit | derived (M48: ONE derived-ness field,
                                                //   not `mode` + a duplicate `derived` bool)
    "confidence": null, "failure_probability": null,
    "observed_failure_rate": 0.0, "pilot_run": false, "max_runs": 50
  },

  "runs": [{
    "run_id": 1, "bucket": "green", "detail": "...", "step_wall_s": 71.2,
    "returncode": 0, "observed_nodeids": 412, "executed": 412, "skipped": 0,
    // M2: `executed`/`skipped` are recorded per run and conjunct 1b requires executed >= 1 —
    // an all-skipped junitxml can no longer settle "green" and close.
    "failure_digest": null,                     // M29: the STRUCTURED hash (nodeid+exception+message),
                                                // replacing `failure_signature` + `signature_digest`
    "import_provenance": {"tortoise_file": "<abs>", "in_root": true},
    "tree_moved": false, "tree_digest": "sha256:...",
    "load": {"load1": 41.2, "band": "L-C"},   // M4: 41.2 IS inside L-C (>=24) — the example no
                                                // longer labels a load above the ceiling as L-B
    "orphan_delta": {"before": 0, "after": 0},
    "slow_run": false, "redis_log_cause": null
  }],

  "streak": { /* exactly the SCHEMA_FIELDS["embedded-local"] subset (D4) */ },

  "buckets": { "closed_set": ["green", "..."], "histogram": {"green": 2, "lane-red": 1} },

  "environment": {
    "orphans": { "before": {...}, "after": {...} },
    "lane": { "uri_unset": true, "carve_out": "1", "expect_uri": false },  // read by conjunct 14 (C18)
    "invocation_id": "<uuid4 hex>",            // C13: the per-invocation ledger namespace key
    "ledger_root": "/var/folders/.../pi-embedded-evidence",
    "tmpdir": "/var/folders/...", "tmpdir_source": "tempfile.gettempdir()",
    "pytest_tmpdir_base": "/var/folders/.../pytest-of-<user>",
    "host_lock_pinned": false                  // F14: we cannot hold the host variable
  },
  "hermeticity": {
    "status": "clean",                          // clean | degraded          (F9)
    "per_run_orphan_delta": [{"run_id": 1, "before": 0, "after": 0, "in_root": 0, "foreign": 0}],
    "per_run_isolation_receipt": [{"run_id": 1, "tmpdir": "<abs>", "fresh": true}],
    "census_ok": true,
    // C4: the census's OWN fail-closed verdict — consumed verbatim from the CLI JSON or re-derived by the
    // runner (`inconclusive = live_servers > 0 and unclassified == live_servers`;
    // `within_budget = orphans <= max_orphans`). Either being false routes to `env-red` (exit 2), never
    // `clean`: the imported `census()` returns NEITHER key (they are added in `main()` — D17/GAP-8).
    "census_inconclusive": false, "census_within_budget": true, "max_orphans": 20
  },
  "load": { "bands": {"L-A": "x < 12.0", "L-B": "12.0 <= x < 24.0", "L-C": "x >= 24.0"},
            "ceiling": 60.0, "ceiling_source": "DEFAULT_LOAD_CEILING (operator --load-ceiling may lower it)",
            "declared_band": "L-C", "green_band": "L-C", "red_band": "L-C", "overlap": true },
  // M4: L-C is the band the red's regime actually occupies (host 38–49; B7's red at 80); the ceiling
  // (60.0) is derived to admit L-C and refuse the measured-red regime at 80. Bands are half-open and
  // therefore deterministic at 12.0 (L-B) and 24.0 (L-C).

  "red": {
    "ref": "<40 hex>", "ref_role": "pinned-head-pre-fix",   // | "last-before-first-family-fix" | "per-cause"
    "ref_tree_object": "<40 hex>",                         // M1: `git rev-parse <ref>^{tree}` in
                                                          // `_demonstrate_red` — conjunct 10's second input
    "cause": "save-child-slot",                            // CAUSE_CLASSES key | "unattributed"
    "cause_evidence": { "redis_log": "<path>", "lines": ["..."], "module_fork_exited_absent": true },
    "red_green_mix": {"red": 3, "green": 0},
    // M51: `refs_attempted` (a pre-D16 "search refs until one is red" vestige) and `causes`
    // (a redundant list of per-run labels) were REMOVED — the explicit pairing ref (D16) and
    // `runs[].redis_log_cause` already carry both; no conjunct/test read either.
    "record_digest": "sha256:<manifest.digest|red tree object|tool_version>",
    "at_fixed_commit": { "attempted": true, "appeared": false, "rate_change": true,
                         "mutation": null,
                         "mutation_operator": "statement-deletion:sdk.py:13239-13245",  // R2/D24:
                         // REQUIRED for the mutation disjunct — operator + target; the target must be the
                         // fix's own population/edit branch (never a test, never an unrelated helper)
                         "mutation_target_is_fix_branch": true,          // R2/D24
                         "mutation_red_returned": false,
                         "surface": "tortoise_search|tortoise_recall",   // R1/D23: the consumer shipping
                         // surface the mutation/rate-change proof is asserted against — never an internal
                         // helper; one of SHIPPING_SURFACES
                         "surface_assertion": "tests/…::test_…" },       // R1/D23: the resolving test-ID
                         // that pins that surface (conjunct 15)
    "same_file_list": true
  },

  "verdict": { "status": "RED-AT-PINNED-REF", "green_only": false, "attributable": true,
               // `attributable` is DERIVED from `red.cause` — true only when the cause is a real class
               // (not null, not `unattributed`); it is never a literal.
               "closes_issue": false, "violations": ["..."], "reasons": ["..."] },
  // M1: `violations` is the field D8's exit_code() branches on; `reasons` is appended per failing
  // conjunct by closes_issue() (never left empty on a false conjunct).
  "reproduce": "python3 tools/embedded_evidence.py run --selection family --n 3 --ref <pin.commit> --marker \"not track_b and not live\" --ledger-root <resolved root>",
  // M28: self-contained — carries the pinned commit and the marker, so re-running it later cannot
  // silently resolve a different HEAD; the manifest digest is re-derived and compared.
  // C2 — the CLOSING form (what the GREEN half records; `record_role` says `closing` only when the
  // pairing ref is declared and the paired red is re-run in the same invocation):
  // "python3 tools/embedded_evidence.py run --selection family --n 10 --ref <fix-commit>
  //    --pairing-ref <last-before-first-family-fix> --record-role closing
  //    --surface tortoise_search --surface-assertion <resolving-test-id>"
  // #4572: --record-out must be OUTSIDE the measured tree, so the closing form
  // OMITS it (default: $TMPDIR/pi-embedded-evidence/record.json) and copies the
  // receipt into docs/evidence/3827-green.json afterwards. An in-tree
  // --record-out is a usage error (exit 2, no record).
  "exit_code": 1
}
```

**Alternatives.** (a) A prose receipt — un-falsifiable (the whole point). (b) Reuse the docker streak
schema for the whole artifact — it has no run loop, no pin, no red, no load. (c) `git write-tree` for the
digest — **wrong**: it hashes the *index* and reports clean for an unstaged edit (AC4).

**Why.** Every field exists to make one conjunct of `closes_issue` (D9) or one threat row falsifiable; the
schema is derived from our own manifest format because no upstream project commits a red/green record
(SCOPE-Findings 6) — corrected by M62: the in-repo `tools/ci_timing.py`/`docs/ci-timing.json` writer is
prior art for the **envelope** (below), and the plan bounds `runs[]`/the ledger as that writer bounds its
`history` (M60).

**Shared record-envelope vocabulary (M62, verdict `unify-contract-keep-drivers`).** The embedded receipt
and `docs/ci-timing.json` are deliberately **distinct records with distinct roles** (a pinned-commit
red/green gate receipt vs a post-merge CI timing measurement — one reads git + the local daemon, the other
`gh api`; one is a closing input, the other never gates), but they share one **envelope** vocabulary, and
the shared part is asserted rather than assumed:

- **schema versioning** — a version field is present in both (`schema: "embedded-evidence/1"` /
  `ci_timing.SCHEMA_VERSION` → `schema_version: 1`); a version bump is a declared act.
- **run identity** — both name the measured run: `pin.commit`/`red.ref` vs `sampled_run.head_sha`; both
  record the tool's own version/schema so a later reader can tell who wrote it.
- **outcome vocabulary** — both map a run to a small closed outcome set (`BUCKET_MAP`'s 11 buckets vs
  `COUNT_KEYS` + `killed`); neither invents a synonym for the other's names.
- **bounded history** — both bound the retained run history (`runs[:20]` in the docker streak /
  `MAX_HISTORY_DEFAULT = 52` in `ci_timing` vs the ledger's `LEDGER_HISTORY_BOUND = 20`, M60).

**C17 — the parity test's subject is the GENERATOR, not the committed fixture.** `docs/ci-timing.json` is
**43 bytes** (`{"schema_version": 1, "history": []}`): it carries no `failed_tests`, no `outcome` and no
`sampled_run`, so a test that compared against the *file* could only ever assert `schema_version`. The test
therefore asserts against `tools/ci_timing.py`'s **own construction** (`SCHEMA_VERSION`, `COUNT_KEYS`,
`MAX_HISTORY_DEFAULT`, and the `failed_tests` field built by its record writer), not the committed file;
and the **"nodeid-keyed failing-test field" claim is DROPPED** — the receipt has no such field (failures
are a hash and the nodeid set lives in the manifest file), so the shared envelope is exactly **three**
items: a schema-version field, a closed outcome vocabulary, and a bounded history. The drift the fourth
item claimed to close was never closed by a 43-byte fixture.

**`record_role`'s producer (M52 / C1).** `record_role` is set by the CLI flag
`--record-role {historical-attestation,closing}` (default `historical-attestation`). `closing` is
**accepted only when `--pairing-ref <sha>` is supplied** (C1: without it there is no ref to pair against,
so the closing path was **unreachable** — the sole documented closing command passed neither the pairing
ref nor the role) **and** `red` was re-run at that pairing ref inside the same invocation that produced
the green streak (D16's green-time pair, F4b). `--record-role closing` without `--pairing-ref` — or with a
`--pairing-ref` that is not a strict ancestor of `pin.commit` — is a usage error ⇒ exit 2, and no
`closing` record is written. `ref_role` is **derived** by the tool from `(--pairing-ref, pin.commit,
ancestry)`, never a caller-declared label (C1: the old membership-only test made the label
unfalsifiable): absent `--pairing-ref` ⇒ `pinned-head-pre-fix` (non-closing); a strict ancestor ⇒
`last-before-first-family-fix` (or `per-cause` when `--cause` is also supplied); equal, descendant or
unrelated ⇒ exit 2. Task 9 pins both directions **and the derivation**:
`test_record_role_closing_requires_same_invocation_pair` (positive: `--pairing-ref` + a paired red in the
same invocation ⇒ `record_role == "closing"`; negative: `closing` without the pair ⇒ exit 2),
`test_ref_role_is_derived_not_declared`, and `test_pairing_ref_must_be_strict_ancestor` (same-as-measured
/ descendant / unrelated ⇒ exit 2). D9 conjunct 7 additionally requires `red.ref == pin.pairing_ref`, so a
record cannot label an arbitrary ref. D9 conjunct 12 is thus a check on a field the tool can actually be
told to produce, never on a default-only unreachable branch.

### D8 — exit-code precedence (0 / 1 / 2 / 3) — F11

**Decision.** One function, one precedence, pinned by a table test:

```python
def exit_code(verdict: dict, *, usage_error: bool = False, env_error: bool = False) -> int:
    # 2 — environment / usage error. The measurement is unusable for reasons that are
    #     NOT a property of the code under test.
    if usage_error or env_error:
        return 2
    # 1 — ANY violation. This includes every red run **in the `run` (green-half) loop**.
    #     The `red` SUBCOMMAND's demonstrated red is the requested artifact, not a violation:
    #     it is non-closing by role (F16) -> exit 3 (M16/M17's attribution failures are this case).
    if verdict["violations"]:          # UNPAIRED-GREEN-AT-REF, UNATTRIBUTABLE, unexpected-bucket,
        return 1                       # dirty/moved tree, vacuous selection, selection/lane-red,
                                       # load-band non-overlap
    # 3 — otherwise clean, but closes_issue is false for a NON-violation reason
    #     (green-only / no paired red / wrong selection for a closing claim).
    if not verdict["closes_issue"]:
        return 3
    return 0
```

Precedence **2 → 1 → 3 → 0**, asserted by `test_exit_code_precedence` with one case per branch and one
case where a run has *both* a violation and a non-closing reason (must be **1**, not 3).
`test_exit_code_equals_persisted_and_process_returncode` persists a verdict for each of the four branches
and asserts `exit_code(persisted_verdict) == persisted exit_code == subprocess returncode` (M1: the
receipt cannot claim an exit code the process did not return).

**`verdict["violations"]` is DERIVED, not ad-hoc (C7).** `closes_issue()` returns `reasons`; the runner
sets `violations = [r for r in reasons if r in VIOLATION_CONJUNCTS]`, where `VIOLATION_CONJUNCTS` is a
declared constant in `tools/embedded_evidence.py`:
`{"runs-empty", "non-green-bucket", "no-test-executed", "selection-not-family", "reproducer-absent",
"pin-not-airtight", "unattributable", "hermeticity-not-clean", "load-bands-do-not-overlap",
"environment-lane-wrong"}` — plus the **pre-loop hard gates** (`UNPAIRED-GREEN-AT-REF`, `UNATTRIBUTABLE`,
`unexpected-bucket`, `git-error`, `selection-red`/`lane-red`). The **non-violation** conjuncts —
`consecutive-green-not-reached`, `pairing-ref-undefined`, `cause-unattributed`, `cause-not-expected`,
`red-file-list-differs`, `record-digest-not-bound`, `no-rate-change`, `record-role-not-closing`,
`red-band-not-in-declared-band` — leave `violations` empty, which is what makes a clean-but-non-closing
run exit **3** rather than 1. Task 7 asserts one persisted record per conjunct, asserting exit 1 vs exit 3
from the declared set (not from prose).

Exit 2 cases include: `--n` **and** `--confidence`/`--failure-probability` both given; **explicit
`--n < 2`** (M3); derived `n < 2`; **`--confidence`/`--failure-probability` outside `0 < p < C < 1`**
(M20); `--run-timeout > 3300`; `--max-runs` exceeded by the derivation (explicit refusal, F29); **an
`--ref` that does not resolve (`run`'s pin — C12)**; **`--record-role closing` without `--pairing-ref`, or
a `--pairing-ref` that is not a strict ancestor of `pin.commit` (equal / descendant / unrelated — C1)**;
unknown
`lane`; unknown `--selection`; an unreadable/unwritable `--record-out` parent or a `--record-out` that
is a directory or a **tracked** file; a relative `--ledger-root` resolving inside the measured root;
`TMPDIR` unusable; **a census that cannot run, or that runs but is `inconclusive`/over `--max-orphans`**
(`env-red` — M13/C4: the measurement is *impossible* or *learned nothing*, not a
violation of the code); **load-ceiling refusal or an invalid `--load-ceiling` (non-numeric/`<= 0`)**
([GAP-7], M4); **`git status --porcelain=v2` exiting non-zero or `git` unavailable** (M18: the pin
digest cannot be computed, so the run cannot proceed as a clean measurement).

Exit 1 cases include: `--selection family` missing its reproducer (hard assert); a `<2`-nodeid or empty or
malformed manifest; collect rc ≠ 0; a dirty or moved tree; `import_provenance` out of root; a bucket
outside the closed set; a `red` ref that is green (`UNPAIRED-GREEN-AT-REF`); load bands that do not
overlap. (A census that cannot run is **no longer** in this list — M13.)

Exit 3 = `NOT-CLOSING`. Exit 0 = clean **and** closing.

**Alternatives.** (a) F5 as written — self-contradictory (an out-of-root provenance conjunct was "false ⇒
exit 3" while AC5 said exit 1). (b) Boolean collapse (0/1 only) — loses the distinction that a cold,
honest receipt is *not a failure of the code*.

**Why.** F11: only a clean-but-non-closing run is 3. Anything that makes the measurement invalid is 1;
anything that makes the measurement impossible is 2.

### D9 — `closes_issue`, the exact conjunction (F15 / F17 / F18 / F19)

**Decision.** `closes_issue` is **true iff every conjunct holds**, and it is a **field and an exit code**
(F5: `closes_issue == false` ⇒ exit 3 unless a violation ⇒ exit 1):

```python
def closes_issue(rec: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    def conj(name: str, value: bool) -> bool:
        if not value:
            reasons.append(name)          # M1: one reason per failing conjunct — never empty
        return value

    ok = True
    # 1  ALL runs green-or-slow, NON-EMPTY, and the requested N was reached (F13/D4).
    #    M3: an empty runs[] is an explicit violation — `all(...)` over zero runs is NOT a pass;
    #    M12: slow-run ADVANCES the streak (it is a pass that ran long), so it may appear here.
    ok &= conj("runs-empty", bool(rec["runs"]))
    # M48: `reached_n` is the embedded streak's own "N reached" flag — READ here (not a third
    # unread duplicate of this same condition).
    ok &= conj("consecutive-green-not-reached",
               rec["streak"]["consecutive_green"] == rec["n"]["requested"]
               and rec["streak"]["reached_n"])
    # C5: `reached_n` is PRODUCED by `_settle`/`streak_record` (D4) as
    # `consecutive_green >= threshold`, and D3 rule 8 pins `threshold == n.requested`, so this conjunct is
    # satisfiable (`_settle` caps `consecutive_green` at `threshold`; a threshold above the requested N
    # made it unsatisfiable, and there was no exit code catching it).
    ok &= conj("non-green-bucket",
               all(r["bucket"] in ("green", "slow-run") for r in rec["runs"]))
    # 1b EVERY run executed at least one test (M2). `_read_junitxml` adds every <testcase> (skip or not)
    #    to `observed`, and `skip_guard.is_falkor_reason_violation` exempts the intentional families
    #    (incl. embedded/redislite-unavailable) — so an all-skipped junitxml otherwise settles "green".
    ok &= conj("no-test-executed", all(r["executed"] >= 1 for r in rec["runs"]))
    # 2  the closing selection is `family` and it carried its mandatory reproducer (AC2).
    ok &= conj("selection-not-family", rec["selection"]["name"] == "family")
    ok &= conj("reproducer-absent", "test_dr_endpoints.py" in rec["selection"]["files"])
    # 3  the pin is airtight — every run, not just the first (AC4).
    ok &= conj("pin-not-airtight",
               rec["pin"]["worktree_clean"] and all(not r["tree_moved"] for r in rec["runs"]))
    # 4  attribution, enforced (AC5 / F5).
    ok &= conj("unattributable",
               all(r["import_provenance"]["in_root"] for r in rec["runs"]))
    # 5  hermeticity is computed, not declared (F9/F24), AND the census's OWN fail-closed verdict is
    #    consumed (C4: `inconclusive`/`within_budget` are added only in `embedded_orphans.main()` — the
    #    imported `census()` returns neither, so an all-unclassified census read `clean` on zero
    #    attributed orphans).
    ok &= conj("hermeticity-not-clean",
               rec["hermeticity"]["status"] == "clean" and rec["hermeticity"]["census_ok"]
               and not rec["hermeticity"]["census_inconclusive"]
               and rec["hermeticity"]["census_within_budget"])
    # 6  TWO LOAD BANDS, ONE BAND (F17): the red half and the green half overlap.
    ok &= conj("load-bands-do-not-overlap", rec["load"]["overlap"])
    # 7  the pairing ref is DEFINED and DECLARED (F19/C1): the last commit before the FIRST family fix
    #    landed, OR a per-cause record naming the cause — and the role is DERIVED from an explicit
    #    `--pairing-ref` (D16), so the conjunct also requires the red to have run AT that declared ref.
    #    A caller-declared label on an arbitrary ref is no longer enough.
    ok &= conj("pairing-ref-undefined",
               rec["red"]["ref_role"] in ("last-before-first-family-fix", "per-cause")
               and rec["pin"].get("pairing_ref") is not None
               and rec["red"]["ref"] == rec["pin"]["pairing_ref"])
    # 8  the red is CAUSE-LABELLED from server-side evidence (F15(i)) — never unattributed.
    #    M29: `expected_causes` (labels), never `expected_signatures`.
    ok &= conj("cause-unattributed",
               rec["red"]["cause"] in CAUSE_CLASSES and rec["red"]["cause"] != "unattributed")
    ok &= conj("cause-not-expected",
               rec["red"]["cause"] in rec["selection"]["expected_causes"])
    # 9  the red ran the SAME selection FILE LIST, not merely the same selection name (F4a).
    ok &= conj("red-file-list-differs", rec["red"]["same_file_list"])
    # 10 the red record is DIGEST-BOUND to (manifest digest, red-ref tree object, tool blob sha) (F4b).
    #    M1: `red.ref_tree_object` is now a DECLARED field (`git rev-parse <ref>^{tree}`).
    ok &= conj("record-digest-not-bound",
               rec["red"]["record_digest"] == digest(rec["manifest"]["digest"],
                                                      rec["red"]["ref_tree_object"],
                                                      rec["tool_version"]))
    # 11 RATE CHANGE, not "did not fire" (F18): the red was ALSO attempted at the FIXED commit
    #    and did not appear in the same band — or the mutation re-introduced the fix line and the
    #    red RETURNED. C8: the mutation disjunct requires BOTH the return (`mutation_red_returned`)
    #    AND the red not to have appeared without it; `bool(mutation)` alone closed on a fix that
    #    demonstrably did not work (`appeared: true` with a mutation id).
    #    D24 (R2): the mutation disjunct is accepted ONLY with a declared operator whose target is the
    #    fix's OWN population/edit branch — `statement-deletion:<path>:<lines>`. A mutation id with no
    #    declared, correct-operator target does not close (the #3888 population-branch class).
    ok &= conj("no-rate-change",
               (rec["red"]["at_fixed_commit"]["attempted"]
                and not rec["red"]["at_fixed_commit"]["appeared"]
                and rec["red"]["at_fixed_commit"]["rate_change"])
               or (bool(rec["red"]["at_fixed_commit"]["mutation"])
                   and rec["red"]["at_fixed_commit"]["mutation_operator"].startswith("statement-deletion:")
                   and rec["red"]["at_fixed_commit"]["mutation_target_is_fix_branch"]
                   and not rec["red"]["at_fixed_commit"]["appeared"]
                   and rec["red"]["at_fixed_commit"]["mutation_red_returned"]))
    # 12 the receipt declares itself a closing record (F16) — a historical attestation can never close.
    ok &= conj("record-role-not-closing", rec["record_role"] == "closing")
    # 13 the red's own load band is the declared band (F17); M1: read the ALREADY-DECLARED
    #    `load.red_band` — there is no third `red.band` spelling.
    ok &= conj("red-band-not-in-declared-band",
               rec["load"]["red_band"] == rec["load"]["declared_band"])
    # 14 the ENV lane shape is the embedded lane (C18's single definition). `lane-red` is the env lane
    #    being wrong — NOT an out-of-root provenance (that is conjunct 4's `unattributable`).
    ok &= conj("environment-lane-wrong",
               rec["environment"]["lane"]["uri_unset"]
               and rec["environment"]["lane"]["carve_out"] == "1"
               and not rec["environment"]["lane"]["expect_uri"])
    # 15 R1 (D23): the certification binds to the SHIPPING surface a consumer reaches. A rate-change or
    #    mutation proof asserted only on an internal helper does not close — the record must name the
    #    consumer surface (`tortoise_search` / `tortoise_recall`) and carry its resolving test-ID. The
    #    standard does not require this (R1 is ours); #3888 is the measurement that produced it.
    ok &= conj("certification-not-on-shipping-surface",
               rec["red"]["at_fixed_commit"]["surface"] in SHIPPING_SURFACES
               and bool(rec["red"]["at_fixed_commit"]["surface_assertion"]))
    # 16 R2 (D24): the certificate is bound to the REVIEWED head SHA and is invalidated by any
    #    post-review edit. `pin.head_sha` must equal the measured `pin.commit`, and
    #    `pin.post_review_dirty` must be false; a later head change requires a re-run (ours; the
    #    standard does not require the mutation proof against the FINAL head SHA).
    ok &= conj("certificate-not-bound-to-review-head",
               rec["pin"]["head_sha"] == rec["pin"]["commit"]
               and not rec["pin"]["post_review_dirty"])
    return ok, reasons
```

**Alternatives.** (a) "A signature substring match in `expected_causes`" — byte-identical across two
mechanically distinct causes (F15's P0): would close an issue while the dominant cause is untouched.
(b) `red.ref == green.commit^` unconditionally — F19: the family is 3–4 causes across 3 issues, so at the
parent of the *third* fix causes 1–2 are already fixed and `commit^` has no referent.
(c) Record the red only at the parent — F18: cannot distinguish "fixed" from "did not fire".
(d) `all(... for r in runs)` over an empty list — M3: vacuous truth closes with zero runs; the empty case
is now conjunct `runs-empty`. (e) Treat an all-skipped junitxml as green — M2: no test executed the code;
conjunct 1b requires `executed >= 1` per run. (f) Leave `reasons` unappended while returning a false
verdict — M1: the receipt's human-readable explanation was always empty.

**Why.** #3847(b) §1 verbatim: *both halves required, so a bug that merely did not fire cannot pass as
fixed.* Each conjunct closes a specific false-`true` class; `conj()` records which one failed, so a false
verdict is never unexplained (M1).

### D10 — cause classes for the red (F15(i))

**Decision.** `CAUSE_CLASSES` is a declared constant mapping a **cause label** to server-side evidence
rules read from the embedded daemon's `redis.log` (plus the **absence** of a termination marker):

```python
CAUSE_PRECEDENCE = ("module-fork-hang", "aof-rewrite-fork", "save-child-slot")  # M30

CAUSE_CLASSES: dict[str, dict] = {
    "save-child-slot": {        # the `--save ''` asymmetry Step 4's tripwire encodes
        "requires_lines":  [r"Background saving (started|terminated)"],
        # M30: the discriminating ABSENCE markers — a save-class line co-occurring with the
        # module/wedge lines is NOT this class.
        "requires_absent": [r"Can't fork for module: File exists",
                            r"Starting BGREWRITEAOF"],
    },
    "aof-rewrite-fork": {       # the second fork source F15 found (appendonly yes)
        "requires_lines":  [r"(Starting BGREWRITEAOF|Background AOF rewrite (started|finished))"],
        "requires_absent": [r"Can't fork for module: File exists"],
    },
    "module-fork-hang": {       # #3845's dominant cause
        "requires_lines":  [r"Can't fork for module: File exists",
                            r"There is a module fork child\. Killing it!"],
        "requires_absent": [r"Module fork exited pid:"],
    },
    "unattributed": {           # NO cause evidence -> can never close (D9 conjunct 8)
        "requires_lines":  [], "requires_absent": [],
    },
}
```

- **Discriminative + precedence (M30).** `save-child-slot.requires_lines`
  (`Background saving (started|terminated)`) and `aof-rewrite-fork.requires_lines` are normal daemon
  lines that can co-occur, so `_label_cause(lines)` iterates `CAUSE_PRECEDENCE` and returns the **first**
  class whose `requires_lines` all match **and** whose `requires_absent` none match — deterministically
  `module-fork-hang` > `aof-rewrite-fork` > `save-child-slot`; anything else is `unattributed`. A
  multi-match fixture (all three line families in one captured log) asserts the precedence, so the
  conflation F15 exists to kill cannot reappear at the labelling step.
- The pytest message (`GRAPH.COPY failed, could not fork`) is **recorded but never used for
  attribution** — it is byte-identical across `save-child-slot` and `module-fork-hang`.
- `redis_log_cause` is written per run; the red's `cause` is the class matched at the failure.
- **[GAP-5]** the exact regexes must be pinned against a **captured real `redis.log`** (task 8 step 1),
  not assumed; until pinned, `unattributed` is the only safe verdict and it cannot close.

**Alternatives.** Match on the pytest message (rejected: the F15 P0). Match on `save` config alone
(rejected: it names the *mechanism*, not the cause of *this* red). Skip attribution (rejected: D9
conjunct 8 makes it non-closing). First-`requires_lines`-wins with no precedence (rejected: M30 — a
multi-match log would label by declaration order, not by evidence).

### D11 — commit and tree pinning

**Decision.** `worktree_clean` ∧ `porcelain_digest = sha256(git status --porcelain=v2 | git diff-index HEAD)`
— **never `git write-tree`** (which hashes the index and reports clean for an unstaged edit). A dirty tree
at run 1, or a digest change between run *k*, makes `tree_moved: true` for that run, keeps the offending
run in `runs[]`, and exits **1**. The `--record-out` path is **not** excluded from the digest; it must lie
**outside every measured tree** or the invocation is a usage error (exit 2, no record) — #4572 option (a),
which supersedes the M5 exclusion. `pin` no longer carries `record_out_excluded`.

**`run --ref` actually pins the measured code (C12).** `--ref` is load-bearing (the `reproduce` string
embeds it and Task 14 runs at `37d5ef00c` **after** Tasks 1–13's commits, so HEAD ≠ the pinned ref), so its
resolution is defined, not advisory: `run` resolves `--ref` with `git rev-parse <ref>^{commit}` and
**reuses `red`'s temp-worktree mechanism** — `git worktree add --detach <mkdtemp> <ref>` — running every
child with `cwd=<mkdtemp>` (F1: `cwd`/`sys.path` wins over the editable finder, so the child imports the
ref's `tortoise/`). `pin.requested_ref` is the resolved sha and `pin.commit == requested_ref` **by
construction**; the `mkdtemp` is removed by the same `try/finally` as `red` (D12). With `--ref` omitted,
`pin.commit = HEAD` and `pin.requested_ref = null`. A ref that does **not** resolve (non-existent,
ambiguous short sha, pruned object) ⇒ **exit 2** with no traceback, no partial `.git/worktrees` entry. Task
6 pins `test_run_ref_pins_the_measured_commit` (the child's `tortoise.__file__` is under the ref worktree
and `pin.commit == pin.requested_ref`) + `test_nonexistent_run_ref_is_exit_2`.

**Fail-closed on a git error (M18).** `git status --porcelain=v2` / `git diff-index HEAD` exiting **non-zero**
(or `git` unavailable) means the pin **cannot be computed** — a naive implementation treating empty/None
output as "clean" would make the pin airtight by accident (plausible on a 262-worktree box: index.lock
contention with a concurrent fleet session, a corrupted repo, a pruned worktree). The runner records the
failure, sets **no** `worktree_clean:true`, and exits **2**. `test_git_error_fails_closed` injects a git
runner returning rc≠0 and one returning empty stdout with rc 0, asserting **exit 2**, a record carrying the
failure, and that `worktree_clean` is never `true`; rc≠0 is added to D8's exit-2 enumeration.

**Alternatives.** `git write-tree` (SCOPE (f): falsifiable — an unstaged edit reports clean);
`git diff --quiet` (no digest, so "changed and changed back" is invisible); treat a failed `git` as clean
(rejected: M18 — fails open).

### D12 — the `red` mechanism and its stated limitation

**Decision.** `red` = `git worktree add --detach <mkdtemp> <ref>` (the guard's README:548/297 **allows**
`worktree add`) → run the **same invocation path** as the green half (the "same lane" claim requires
exactly that) — and the green half's `run --ref` **reuses this same mechanism** (C12/D11), so a pinned
receipt's `pin.commit` is the code actually measured → `import_provenance` **fails closed** if the child's `tortoise.__file__` is not under
`<mkdtemp>` → `try/finally` + `git worktree remove --force` + `shutil.rmtree` so a failed or dirty `red`
leaves nothing behind. **A `git worktree add` or ref-resolution failure is exit 2** — an environment error
(the ref or the worktree cannot be resolved), never a red: the failure is recorded, the `mkdtemp` is
removed, and no partial `.git/worktrees/<id>` registration remains (M46/M57). **The green N-run loop
carries the same `try/finally` + signal handling as `red` (M59):** SIGINT/SIGTERM tears down the live
child, removes the run's `mkdtemp`, writes an `aborted` ledger note, and exits **130** — the loop has no
unguarded window in which an interrupt can leak a child, a worktree, or a half-written ledger.

**Known limitation, stated in the receipt, not hidden:** the red half runs the ref's **source** with the
**current** environment (the rejected `uv run --project --frozen` variant would pin the ref's own
`uv.lock` at the cost of a fresh venv + network install per invocation). Recorded as
`pin.environment_pinned: false`.

**Alternatives.** `git archive` + a fresh venv (F1: cost, and its premise — that the editable finder forces
this worktree's `tortoise/` — is **false**: `_EditableFinder` is appended at `sys.meta_path[5]`, after
`PathFinder[4]`, so `cwd`/`sys.path` wins).

### D13 — the N contract

**Decision.** `DEFAULT_N = 10` — a **DECLARED LOCAL** number, **not borrowed** (D3827-b). **No source
makes any N canonical:** the four independent practitioner sources the verdict names (Google Testing Blog
2016; Slack Engineering; TestRail; TestMu) each describe one pipeline — detect by repeated runs → quarantine
→ fix the root cause — and **none fixes N**. We use N to **CERTIFY** a fix where the field uses N to **DENY**
a flaky test: stricter, compatible, but **ours**. It is labelled here so a future reader can tell it is a
declared choice rather than a standard. The loop never contains a literal. `--n <int>` and `--confidence <C> [--failure-probability <p>]` are mutually
exclusive; both ⇒ exit 2. Derived form:
`n = ceil(ln(1 - C) / ln(1 - p))`, with `p` supplied by a **named pilot run** (`--pilot-run`, recorded
`n.pilot_run = true`) or explicitly; derived `n < 2` ⇒ exit 2. The record states
`observed_failure_rate` and never prints "fixed". **[GAP-3]** `--max-runs` cap: `DEFAULT_MAX_RUNS = 50`,
refusal (exit 2) when the derivation exceeds it — the formula is uncapped (`p=0.02, C=0.95 → n=149`).
**The cap is UNIFORM (M55):** an explicit `--n` is bounded by the same `--max-runs` value — explicit
`n > max_runs` (`--n 100 --max-runs 50`, or `--n 51` against the default) ⇒ exit 2, so `--n 10000` cannot
launch an unbounded multi-day run. The cap is checked for both derivations, not only the formula's.

**Explicit `--n` is validated too (M3).** `DEFAULT_MAX_RUNS` is tied to the *derivation* today, so
`--n 0` produces an empty `runs[]` (vacuously `all(...) == True`) and `--n 1` passes an N=1 bar #3847(b)
requires to be N *consecutive*. Explicit **`--n < 2` ⇒ exit 2**, and the empty-`runs` case is an explicit
violation in `closes_issue()` (D9 conjunct `runs-empty`), never a vacuous truth.

**The derivation's domain is declared and validated (M20).** `n = ceil(ln(1-C)/ln(1-p))` crashes outside it:
`C = 1.0`/`C > 1` → `math domain error`; `p = 0.0` → `ZeroDivisionError`; `p < 0`/`p > 1` → domain error or
negative `n`; `C = 0.0` → `n = 0`. Valid domain is **`0 < p < C < 1`**; anything else ⇒ exit 2 with a usage
error and **no traceback**. `test_usage_errors_exit_2` parametrises `C ∈ {0.0, 1.0, -0.1, 1.1}`,
`p ∈ {0.0, 1.0, -0.1, 1.1}` and `p > C`.

**Alternatives.** Hardcoding 10 (rejected: the body's own "N=10 is a policy act"); "10 consecutive green ⇒
fixed" (rejected: green-only is unfalsifiable); rely on the derived-`n<2` refusal to catch bad inputs
(rejected: M20 — it only incidentally catches `p > C`).

### D14 — load governs the red (F17)

**Decision.** A **fail-closed pre-flight load ceiling** — `--load-ceiling`, **operator-supplied with a
recorded default `DEFAULT_LOAD_CEILING = 60.0`** (6× the 10-CPU count) — refuses to start above it; the
**per-run** `load1` and a discrete **band** are recorded in `runs[]`; the **green half** and the **red half**
must land **in one declared band** (`load.overlap`), else exit 1. `host_lock_pinned: false` is recorded
because the host variable cannot be held (the box sat at load 38–49 on 10 CPUs; B7 measured the red at
load 80).

**The bands actually contain the red's regime (M4).** Three half-open bands, boundaries deterministic:
`L-A` = `x < 12.0`, `L-B` = `12.0 <= x < 24.0`, `L-C` = `x >= 24.0` — so a `load1` of exactly `12.0` is
`L-B` and exactly `24.0` is `L-C`. The default ceiling is **derived from the bands it must not contradict**:
the old `24.0`/`L-B` model refused Task 14's own evidence run on this host and left a genuine red's load in
**no** band, so `load.overlap` and D9 conjunct 13 could never both hold. `60.0` admits L-C (this host at
38–49) and still refuses the measured-red regime at 80 — fail-closed where F17 needs it. `--load-ceiling`
must be numeric and `> 0`; non-numeric / `0` / negative ⇒ exit 2 with a usage error (M4/M61 boundary
tests: `load1 ∈ {11.999, 12.0, 24.0, 24.001}`).

**How Task 14's evidence run is expected to run on this host (M4).** It passes **no** `--load-ceiling` and
relies on the recorded default `60.0`; its per-run loads (38–49) are `L-C`, and the red attestation is
recorded in the same band. The D7 example was corrected accordingly (`load1: 41.2` → `L-C`).

**Alternatives.** Ignore load (rejected: red-loaded + green-idle is a claim about the hour, not the code);
pin the host (impossible); measure and record without requiring overlap (rejected: overlap is what makes
the red/green comparison meaningful); keep the `24.0` ceiling with two bands (rejected: M4 — self-defeating
on the stated host and unable to band a genuine red).

### D15 — rate change or mutation (F18)

**Decision.** The red is **also attempted at the fixed commit** and must **not appear** (a rate change,
recorded `at_fixed_commit.rate_change = true`), **or** a **mutation** re-introduces the fix line and the
red must **return** — recorded as `at_fixed_commit.mutation = "<mutation id>"` **and**
`at_fixed_commit.mutation_red_returned = true`, with `appeared` still false (C8: `bool(mutation)` alone
let `{attempted: true, appeared: true, rate_change: false, mutation: "m1"}` close on a fix that
demonstrably did not work). D9 conjunct 11 requires `mutation ∧ mutation_red_returned ∧ ¬appeared`. A fix
that changes nothing can no longer pass on a long idle window.

**R1/R2 amendment (verdict 2026-09-18 · D23/D24).** Either disjunct is accepted **only** with a declared
`at_fixed_commit.surface` in `SHIPPING_SURFACES` and a non-empty `surface_assertion` (R1 — the mutation is
asserted where the consumer looks, per #3888's `sessionId:''`), and the **mutation** disjunct additionally
requires `mutation_operator` to be a `statement-deletion:` operator whose target is the fix's **own
population/edit branch** (`mutation_target_is_fix_branch: true`) (R2 — the #3888 proof was against a staged
blob and the population branch was never in the mutant set).

### D16 — the pairing ref (F19 / C1)

**Decision.** The pairing ref is **an explicit, validated input**: `run --pairing-ref <sha>`, recorded as
`pin.pairing_ref`. It is **required** for `--record-role closing` (without it there is no ref to pair
against, so the closing path was unreachable — the sole documented closing command passed neither the
pairing ref nor the role); it must be a **strict ancestor of `pin.commit`**; equal, descendant or
unrelated ⇒ **exit 2**. The semantics the ref carries are F19's: it is **the last commit before the FIRST
family fix landed**, or the pairing is **per-cause** (one record per cause, each naming the cause, when
`--cause` is also supplied). `ref_role` is **derived** from `(--pairing-ref, pin.commit, ancestry)` by the
tool — never a caller-declared label: absent ⇒ `pinned-head-pre-fix` (non-closing); strict ancestor ⇒
`last-before-first-family-fix` / `per-cause`. For PR #1's cold receipt, no `--pairing-ref` is passed and
`ref_role = "pinned-head-pre-fix"` — which is **not** a closing role (D9 conjunct 7, which also requires
`red.ref == pin.pairing_ref`). The identity of the first fix is unknown today; the ref is resolved at green
time from the fix PR (`--ref <fix-commit> --pairing-ref <last-commit-before-first-family-fix>`).

### D17 — hermeticity is computed per run (F9 / F24)

**Decision.** Before **and after each** run the runner takes a census and computes the **per-run orphan
delta**, scoped to **this run's `mkdtemp` root / own pid tree** — never a host-global delta (F24: 262
worktrees and live concurrent suites make a host-global delta simultaneously fail-open and
fail-closed-poisoning). A census that **cannot run** (the `census()` raise → the tool's exit 2 path) is a
distinct **`env-red`**, consistently **exit 2** (M13: the measurement is *impossible*; not a violation of
the code), not an abort of the green half.

**C4 — the census's OWN fail-closed verdict is consumed, not just its raise.** `tools/embedded_orphans.py::census()`
returns `live_servers / orphans / orphan_details / protected / unclassified / stale_socket_dirs`; the
fail-closed keys `inconclusive`, `within_budget` and `max_orphans` are added **only in `main()`** (`:202–207`),
and the runner calls the imported `census()` and filters afterwards. So when `unclassified == live_servers > 0`
(every server unclassifiable — the fail-open #3599 added `inconclusive` to refuse), the imported call returns
normally with `orphans: 0`, the runner's filter yields an empty delta, and `hermeticity.status == "clean"` ⇒
D9 conjunct 5 true **on a census that attributed nothing**. The runner therefore **re-derives or consumes the
census's own verdict** — `inconclusive = live_servers > 0 and unclassified == live_servers`;
`within_budget = orphans <= max_orphans` (or the CLI `--json` output verbatim) — and routes either failure to
**`env-red` / exit 2, never `clean`**, recording `hermeticity.census_inconclusive` /
`hermeticity.census_within_budget` / `max_orphans` (D7). Pinned by `test_inconclusive_census_is_env_red` and
`test_census_over_max_orphans_is_env_red` (Task 10 Step 1).

**C3 — the run-root scoping itself has a test.** F24's required behaviour is two-part: the delta is scoped to
this run's `mkdtemp` root, **and** a census that cannot run is `env-red`. Only the raise half had a test. Two
more pin the pivot: `test_census_delta_is_scoped_to_the_run_root` and
`test_foreign_orphan_outside_run_root_is_not_attributed` (inject a `census()` payload with socket paths
**inside** and **outside** the run root; assert only in-root entries move `per_run_orphan_delta` /
`hermeticity`, and `foreign` is recorded but never attributed). On this box (265 worktrees) the difference
between a meaningful and a vacuous delta is exactly this filter.

**Residual — the source-scoped census is filed, not done here (M27).** `tools/embedded_orphans.py`
(`census(*, deep=False, jobs=8)`, `:84`) runs a **host-global** `pgrep -f` with a **5 s** timeout; the
runner filters its output afterwards, which still pays the enumeration cost and can still time out on a
loaded box. The real fix — scoping at source and raising the timeout — is **filed as #3869** (OPEN).
`Deferred: filter census() output in the runner — Good alternative: source-scoped census
(census(*, deep, jobs, root=None) + raised pgrep timeout) — Cost: one module edit + ~2 unit tests + 1 AC10
diff path — Rationale: PR #1 scope; filed as #3869.`

**Alternatives.** One pre-run census (rejected: cannot support the claim — SCOPE-Findings 4: redislite
creates exactly one server and cleans up on exit, so run N ≠ run 1 without explicit teardown).

### D18 — evidence, not a gate (F21)

**Decision.** Nothing auto-blocks a merge. `commit-workflow` wiring stays **off**. CI's **main** lane
(docker) shows the signature zero times — the corrected statement (F21; the falsified one was "CI shows it
zero times", which is false: #3750 documents the file *selected into the tier-2 embedded fast/slow legs and
failing there*). **#3814's embedded lane is the producer of record** for anything claimed as *evidence*;
#3827 is the local **diagnostic + the permanent tripwire**.

### D19 — the tripwire's two axes, and its site-discovery rule (F8 / F15(ii))

**Decision.** `tests/test_embedded_save_tripwire.py` asserts a **two-axis** invariant. The docker axis is
**two-dimensional** (RDB `--save` **and** AOF `auto-aof-rewrite`, M9), and its site set is a **declared
registry**, not "the count of what the scanner happens to read" (M8).

1. **Live axis (real embedded server, subprocess via the `tests/test_tripwire.py` pattern):** the embedded
   lane retains automatic fork sources — `CONFIG GET save` is non-empty (redislite default `save 900 1 /
   300 100 / 60 200 / 15 1000`) **and**, with AOF enabled, the `auto-aof-rewrite` axis is live
   (`auto-aof-rewrite-percentage` > 0).
2. **Docker axis (static scan, two-dimensional, unified image-based universe + declared classifier).**
   The **universe** is **every compose `services:` entry whose `image:` is redis/falkordb + every workflow
   `services:` image line** — the image-based site set, so a *new* `image: redis` service with **no**
   override (exactly the `apps/graph-viz` / `integrations/crm/twenty` shape, whose default saves are live)
   is **counted** rather than escaping the registry (C15: the prior universe *was* the `--save`-line scan,
   so a service that declares no key was neither counted nor recorded and the tripwire went false green).
   Every universe member must be **save-disabled** (its `REDIS_ARGS:`/`--save` line, or its image default
   proven disabled) **or** an **explicitly recorded exclusion with a reason**; the universe is enumerated
   in the test, not inferred.
   - **The `--save`-line scan is the CLASSIFIER over that universe, never the universe itself (C15/M7).**
     A *site* is a **non-comment** line carrying the YAML key `REDIS_ARGS:` or a `--save` token; the
     classifier maps each universe member to its site line (or to `no-site-declared`, which is a recorded
     exclusion/`save-live-by-default` finding, never an omission). YAML comments are stripped before
     matching (`#…`), and the escaped compose spelling `--save \"\"` is normalized to `--save ""` before
     matching. Without the comment strip the naive matcher `--save\s+["']{0,2}["']{0,2}` matches **16**
     lines (9 real + 7 comment/prose lines, e.g. `docker-compose.yml:54`,
     `python-ci.yml:386,924,1276,1478`) and `test_declared_site_count_is_pinned` (= **9**) fails on its own
     fixture. The scanner test carries a **negative fixture** (a commented `--save` line that must NOT be
     counted).
   - The **9** lane sites (8 workflow: `post-merge-validation.yml:253,269`;
     `python-ci.yml:389,405,927,943,1279,1481`; 1 compose: `docker-compose.yml:68`) all declare
     `--save ''`/`--save ""` (saves OFF) after normalization.
   - **AOF dimension (M9).** F15(ii) requires the fork-source invariant to cover **both** `save` and
     `auto-aof-rewrite`; the AOF axis was asserted **nowhere** (it appeared only as a recorded measurement
     on the embedded lane), so the tripwire could not fail on it. The docker axis now scans the AOF
     dimension too: a lane site declaring `appendonly yes` **without** a bounded
     `--auto-aof-rewrite-min-size` is a **FAILURE** (an unbounded rewrite fork source); a lane site
     declaring **no** AOF args is recorded as `aof_unmeasured` — a declared residual, never asserted true.
     Step 1 measures `CONFIG GET appendonly` / `auto-aof-rewrite-percentage` on both lanes; a **live**
     `auto-aof-rewrite` on a lane site (`appendonly yes` ∧ `auto-aof-rewrite-percentage > 0`) is a **test
     FAILURE**, not merely a recorded value. (`docker-compose.yml:68` declares `appendonly yes
     --auto-aof-rewrite-min-size 1gb`, so it is bounded; the workflow sites declare no AOF args and are
     recorded `aof_unmeasured`.)
3. **Recorded exclusions — by function, not by omission (M8).** `graph-scripts/setup.py:647`
   (`--appendonly yes --save 60 1000`, saves **ON**) is a **setup script, not a lane server**; two further
   first-party compose stacks start redis/falkordb servers with periodic saves live and were silent:
   `apps/graph-viz/docker-compose.yml:7–16` (falkordb image, `FALKORDB_ARGS` only, no `--save` override)
   and `integrations/crm/twenty/docker-compose.yml:49–54` (`redis:7-alpine`, default
   `save 3600 1 300 100 60 10000`). All three are **recorded exclusions with a stated reason** (non-lane
   setup/dev stacks, not lane servers), never silently skipped; a future `docker-compose.ci.yml`
   therefore lands in the registry and either classifies or fails the pin, instead of escaping it.
4. **Mutation:** a test-local mutation that flips one declared site's spelling (or drops `--save ''`)
   makes assertion (2) fire — the falsifiability form the scoping research names as canonical.

**Alternatives.** Scan only `.github/workflows/*.yml` with a literal `--save ''` (rejected: misreads the
compose spelling and misses the compose site). Count only the scanner's own output (rejected: M8 — the
invariant is circular; a site the scanner does not read cannot move the count). Assert the `save` axis
only and record the AOF state (rejected: M9 — the tripwire could not fail on the AOF axis F15(ii) names).
A bare "CONFIG GET save is non-empty" assertion without docker (no asymmetry, no invariant).

### D20 — honest limits (DA §5.4)

**Decision.** The adversarial surface no longer claims a false `closes_issue:true` is *impossible*. It
claims a false `closes_issue:true` cannot be produced **accidentally**. The record is **content-bound and
self-declared**, not authenticated (`attestation: "self-declared"`); tamper-*proofing* is out of scope.
Bar (b) (#3847 §2) is **only as strong as the reviewer who re-runs the recorded `reproduce` command**,
because nothing consumes `closes_issue` but a human.

### D21 — reachability (F12)

**Decision.** Add `tools/embedded_evidence.py`, `tools/testdb_canary_classify.py` **and
`tools/lane_contract.py`** to `TOOL_CARVEOUTS` in `tools/ci_selection.py` (C14: the third new `tools/`
module the revision created was the ONE lane vocabulary/registry read by four consumers, yet it matched no
`SOURCE_PATTERNS` entry and was swallowed by the flat `"tools/"` prefix in `NON_PYTHON_PREFIXES` — a
lane-vocabulary-only PR yielded `changed == []` → docs-only early return → tier-1 smoke only, so
`test_lane_vocabularies_agree` never ran on the PR that changes the shared contract; exactly the
#3261/#1349 class this decision closes for the other two paths). Verified today:
`select(["tools/embedded_evidence.py"], "pull_request", manifest)` → `surfaces == []`, tier-1 smoke only
(`tools/` sits in `NON_PYTHON_PREFIXES` — `tools/ci_selection.py:200`, the `"tools/"` literal on `:203` —
and matches no `SOURCE_PATTERNS`), so a tool-only PR would run
neither the tool's guard tests nor any surface — the documented #3261/#1349 class. Both paths then take
the unknown-path branch → **full matrix, fail closed**.

**Alternatives.** Promote `tools/` out of `NON_PYTHON_PREFIXES` (rejected: unrelated tools changes must
keep failing closed); add a `SOURCE_PATTERNS` entry (none of the named surfaces owns this tool).

### D22 — registration and redirect exemption (F3 / F22 / F30)

**Decision.** Both new test files are (a) registered in a **surface** (`core` — where `test_canary_classify`,
`test_ci_selection` and `test_collision_preflight` already live), (b) added to `carve_out:` (both spawn
real embedded servers → **not** the docker fast matrix), and (c) added to `TEST_NO_REDIRECT_STEMS` in
`tests/_embedded.py` **and** to the hardcoded exact-set pin in `tests/test_markers.py`. Without (a) the
merge-blocking `manifest-integrity` job reds; without (c) a URI lane silently redirects the tripwire's
server to docker and its `CONFIG GET` assertion becomes vacuous.

**Alternatives.** Skip `carve_out:` (#3846: the carve-out selection contains none of the family's
reproducers, but membership is about *which job runs the file*, not about evidence — C19: the family's
**mandatory** reproducer is absent, two of the three family files are present); exempt only the
tripwire (F30 says both files spawn servers).

### D23 — R1: certification binds to the SHIPPING surface, not the internal seam (verdict 2026-09-18)

**Decision (OURS — the standard does not reach it; D3827-d).** Every mutation / removed-behaviour piece of
evidence the runner records must be asserted at the **surface a consumer actually reaches**, not at an
internal helper. For the family fix the shipping surface is the **wire output of `tortoise_search` /
`tortoise_recall`** (the `sessionId` field of the returned record); an internal-helper assertion is **not**
sufficient and does not close. The record carries `red.at_fixed_commit.surface` (a consumer-surface id) and
`red.surface_assertion` (the resolving test-ID that pins that surface). `closes_issue()` (D9) gains
**conjunct 15**: the rate-change/mutation evidence must name a declared shipping surface, and the
`surface_assertion` must be non-empty.

**Why — the #3888 measurement that produced R1.** Issue #3888 shipped 17 tests, a clean recorded review and
a VGATE PASS, yet deleting the write-side population branch (`sdk.py:13202`, `:13239-13245`) left the suite
GREEN while **`tortoise_search` and `tortoise_recall` both returned `sessionId:''`**. The manual mutation
proof covered the READ path only; the 17 tests pinned the ask lane and the hosted `/v1/search` field —
neither pinned search/recall. Mutation testing alone (the adopted standard) would have caught the surviving
mutant; **R1 is the extra rule that the mutation must be asserted where the consumer looks**, because a
per-item traceability row on an internal seam is a *true row about the wrong surface*.

**Alternatives.** (a) suite-level mutation score only — rejected: every named source stops there, and #3888
is exactly the residue that rule misses. (b) Require a browser/E2E assertion — rejected: the surfaces here
are library wire returns, not a UI (`UX=low`). (c) Accept any public SDK method — rejected: `TortoiseSDK.search`
is the internal seam the 17 tests already pinned; the surface is the agent-facing
`tortoise_search`/`tortoise_recall` result.

### D24 — R2: the certificate is bound to a head SHA, re-run after any post-review edit, and the mutator includes statement deletion of the fix's own population/edit branch (verdict 2026-09-18)

**Decision (OURS — the standard does not reach it; D3827-d).** Two rules, one certificate:

1. **Head-SHA binding + post-review invalidation.** `pin.commit` is the **reviewed head SHA**;
   `pin.head_sha_verified_at` records when the mutation/red evidence was taken. Any post-review edit to the
   measured tree (`pin.post_review_dirty: true` — a fixer commit after the review round, an uncommitted
   edit, a rebase) **invalidates** the certificate: `closes_issue:false`, exit 1, and the evidence must be
   **re-run at the new head SHA**. A certificate is never inherited across a head change.
2. **Declared mutation operator; statement deletion is REQUIRED.** `at_fixed_commit.mutation_operator` names
the operator and its target (e.g. `statement-deletion:sdk.py:13239-13245`). `closes_issue()` (D9 conjunct
11) accepts the mutation disjunct **only** when the operator starts with `statement-deletion:` **and** the
target is the fix's **own population/edit branch** (never a test, never an unrelated helper). A
rate-change-only disjunct still requires a declared `surface` (D23).

**Why — the #3888 measurement.** #3888 shipped 17 tests + a clean recorded review + VGATE PASS, yet
deleting the population branch at `sdk.py:13202`/`:13239-13245` left the suite GREEN while
`tortoise_search` and `tortoise_recall` both returned `sessionId:''`. The manual proof was against a
**staged blob**; the population branch was **never inside the mutant set**, so the certificate was bound to
a code version that no longer existed. R2 closes that class: the SHA is part of the claim, and the mutation
must delete the fix's own branch.

**Alternatives.** (a) Re-run automatically after every edit — rejected: the runner is local and on-demand;
the rule is an invalidation contract plus a required operator, not a background job. (b) Accept any mutation
operator (bit-flip, conditional negation) — rejected: statement deletion is the operator that reproduces
#3888; a bit-flip in a helper can survive while the surface is broken. (c) Bind only `pin.commit` and trust
the reviewer to notice drift — rejected: #3888 *was* a clean review; the binding must be mechanical.

---

## Task list — the RED half, RED-first ordering

> **RED-half-first ordering is explicit.** Every task below belongs to **PR #1 (the RED half)**. The
> deterministic red half (task 11, the tripwire) is *not* gated on any green commit; the environmental red
> half (tasks 6/8) is measured at the **pinned HEAD** and produces a **non-closing** receipt (task 14).
> There are **no tasks for the GREEN half** — it ships no code (D1).
>
> **Dependency edges (concurrency map, M36 — NOT a chain, and CORRECTED by C20).** The declared edges now
> match what the steps actually import/read: `1 → 4` (Task 4's
> `test_divergence_is_unreachable_without_a_divergence_log` calls `classify(lane=…)`), `1 → 6` (Task 6
> needs `lane_contract.CHILD_LANE_VARS`), `2 → 4` (the reset-semantics pin only — the loop work does not
> wait), `3 → 5` (Task 5 reads `embedded_family:`), `4 → 12` (Task 12 imports `BUCKET_MAP`), and
> `4 → 5 → 6 → 7 → 8 → 9 → 10 → 14`, `{1,2,3,11} → 12`, `12 → 13`. **One-file seams, serialized rather
> than hidden (C20):** (a) Task 3 and Task 4 BOTH write `config/ci-surfaces.yml` — Task 4's commit
> auto-registers `tests/test_embedded_evidence.py` through `.husky/pre-commit --register`, so the manifest
> write is ordered `3 → 4`; (b) Task 1 and Task 2 both edit `tools/testdb_canary_classify.py`, so their
> **edits** are serialized (`1 → 2`) even though the analysis is independent. **Genuinely concurrent:**
> Task 1's classifier work, Task 3's YAML declaration, Task 11's tripwire (an independent file) and
> `{2,11}` analysis — plus Task 4's loop/skeleton work once `1` and `3` have landed.

### Task 1: `lane`-aware classifier contract

**Intent:** make the classifier's population gate **lane-aware** so the runner reuses the classifier
instead of defeating its gate (F2/F23) — the reuse path the scope record mandated (`--lane` param).
**Acceptance:** `classify()` requires `lane` (keyword-only) — omitting it raises `TypeError`; an unknown
lane raises `ValueError`; all **16** existing call sites updated to `lane="docker-half-b"` reproduce HEAD
behaviour unchanged; the returned streak record carries `lane` (**M35**: Task 1's `_settle` change emits
this one-line field — Task 2 *declares* it in `SCHEMA_FIELDS`; the `rec["lane"]` assertion is therefore
not orphaned); the CLI `main()` (`:409`) gains `--lane` (parser default `docker-half-b`) and threads
it; `lane="embedded-local"` refuses a missing/`CANARY_DROP_THRESHOLD` threshold **or one
`!= n.requested`** (C5), derives its gate from a runner-computed `lane_shape` **and** a `run_provenance`
record (C18), and a wrong env lane settles `lane-red`; `tools/lane_contract.py` exists and is the ONE lane
vocabulary + child-env registry, read by all four consumers (M24); `"os.environ" not in code` still holds.

**Files:**
- Modify: `tools/testdb_canary_classify.py` (`classify()` at `:267`; gate at `:286–296`;
  **`main()` CLI + the `classify(...)` call at `:409`** — M6)
- Modify: `tests/test_canary_classify.py` (16 call sites at `:82,92,108,124,146,160,175,188,199,214,222,239,250,262,273,284`)
- Create: `tools/lane_contract.py` (M24) — `LANE_AXIS`, `LANE_ALIASES`, `axis()`, `CHILD_LANE_VARS`
- Modify: `tests/_embedded.py` (`BackendIdentity` reads `axis()`), `tools/ask_recall_bench.py` (reads
  `LANE_AXIS`) — M24

**Step 1 — write the failing tests** (`tests/test_canary_classify.py`):

```python
def test_lane_is_a_required_keyword(inputs):
    with pytest.raises(TypeError):
        classify(inputs["junitxml"], inputs["manifest"], inputs["step_wall"],
                 inputs["divergence_log"], None, RUN)

def test_unknown_lane_is_rejected(inputs):
    with pytest.raises(ValueError):
        classify(inputs["junitxml"], inputs["manifest"], inputs["step_wall"],
                 inputs["divergence_log"], None, RUN, lane="embedded")

def test_embedded_lane_requires_explicit_non_drop_threshold(inputs):
    for bad in (None, CANARY_DROP_THRESHOLD):
        with pytest.raises(ValueError):
            classify(inputs["junitxml"], inputs["manifest"], inputs["step_wall"],
                     inputs["divergence_log"], None, RUN,
                     lane="embedded-local", threshold=bad,
                     run_provenance={"measured_root": "/r", "porcelain_digest": "sha256:x",
                                     "import_provenance": {"tortoise_file": "/r/tortoise/__init__.py"}})

def test_embedded_lane_gate_requires_a_valid_lane_shape(inputs):
    """C18: `lane-red` is the ENV LANE being wrong — not an out-of-root provenance (that is gate 1's
    UNATTRIBUTABLE, exit 1, D8/D9 conjunct 4)."""
    rec = classify(inputs["junitxml"], inputs["manifest"], inputs["step_wall"],
                   inputs["divergence_log"], None, RUN, lane="embedded-local", threshold=10,
                   lane_shape={"uri_unset": True, "carve_out": "1", "expect_uri": False},
                   run_provenance={"measured_root": "/r", "porcelain_digest": "sha256:x",
                                   "import_provenance": {"tortoise_file": "/r/tortoise/__init__.py"}})
    assert rec["last"]["bucket"] != "lane-red"

def test_wrong_env_lane_is_lane_red(inputs):
    """C18: the ONE definition of `lane-red` (URI set / carve_out != 1 / expect_uri set)."""
    rec = classify(inputs["junitxml"], inputs["manifest"], inputs["step_wall"],
                   inputs["divergence_log"], None, RUN, lane="embedded-local", threshold=10,
                   lane_shape={"uri_unset": False, "carve_out": "1", "expect_uri": False},
                   run_provenance={"measured_root": "/r", "porcelain_digest": "sha256:x",
                                   "import_provenance": {"tortoise_file": "/r/tortoise/__init__.py"}})
    assert rec["last"]["bucket"] == "lane-red"
```

Plus `test_docker_lane_unchanged_at_all_16_call_sites`: the 16 updated calls keep their existing
assertions **verbatim**, and a new assertion `rec["lane"] == "docker-half-b"` (the `lane` field is emitted
by Task 1's `_settle` change — M35 — even though its *declaration* in `SCHEMA_FIELDS` is Task 2's).

Plus the two recurrence/consistency tests the F2/F23 correction needs (M6/M24):

```python
def test_every_classify_call_site_passes_a_lane():
    """M6/C10: a predicate, scoped to the CLASSIFIER's own callable — NOT every `classify(` in the repo.
    `tests/test_analyze.py` imports an unrelated `from tortoise.analyze import classify` and calls it bare
    9 times (`:12–61`); an unscoped `\bclassify\(` sweep reds on a file AC10 forbids touching, so
    Task 1 Step 4's "PASS" was unreachable."""
    import re
    for path in (list((REPO_ROOT / "tools").glob("*.py")) + list((REPO_ROOT / "tests").glob("*.py"))):
        text = path.read_text()
        if "testdb_canary_classify" not in text and path.name != "testdb_canary_classify.py":
            continue                       # C10: only files that consume THIS classifier's callable
        for m in re.finditer(r"\bclassify\(", text):
            if "def " in text[max(0, m.start() - 5):m.start()]:
                continue                       # the DEFINITION, not a call
            call = text[m.end():m.end() + 400]
            assert "lane=" in call, f"{path}: classify() call without lane="

def test_classifier_cli_threads_lane(monkeypatch):
    """M6: the production caller (tools/testdb_canary_classify.py:409) is covered."""
    from tools.testdb_canary_classify import main
    rc = main(["--run-id", "1", "--junitxml", jx, "--manifest", mf,
               "--step-wall", sw, "--producer-marker", pm])   # no --lane => parser default
    assert rc == 0 and json.loads(OUT.read_text())["lane"] == "docker-half-b"

def test_lane_vocabularies_agree():
    """M24: ask_recall_bench, BackendIdentity, LANES and the runner share ONE axis."""
    from tools.lane_contract import LANE_AXIS, LANE_ALIASES, axis
    assert set(LANE_AXIS) == {"embedded", "docker"}
    assert {axis(v) for v in LANE_ALIASES} == set(LANE_AXIS)
    assert {axis(b) for b in ("embedded", "server")} == set(LANE_AXIS)   # BackendIdentity
    import tools.ask_recall_bench as arb
    assert set(arb.LANES) == set(LANE_AXIS)
```

**Step 2 — run to verify it fails:**
`env -u TORTOISE_DB_URI uv run pytest tests/test_canary_classify.py -v`
Expected: FAIL — `TypeError: classify() missing 1 required keyword-only argument: 'lane'`.

**Step 3 — implement** the D3 signature and the lane-aware gate; update all 16 call sites with
`lane="docker-half-b"`; have `_settle` set the record's `lane` (the M35 one-line field); add `--lane` to
`main()` (parser default `docker-half-b`, per M6) and thread
`lane=args.lane` into the `:409` call; create `tools/lane_contract.py` and point `ask_recall_bench`,
`BackendIdentity`, `LANES` and the runner at it (M24).

**Step 4 — run to verify it passes:** same command. Expected: **PASS**, and
`test_classifier_reads_only_declared_files` (`:289`) still green.

**Step 5 — commit** `feat(classify): lane-aware population gate + shared lane tag (#3827)`.

### Task 2: shared streak-schema declaration + cross-writer assertion

**Intent:** one contract, two writers (F6/F13) — the streak schema stops being implicit in `_settle`'s
return.
**Acceptance:** `SCHEMA_FIELDS` declares every field with its lanes; `streak_record()` is the only
constructor **for NEW records** and `streak_from_prev()` is the only constructor for the READ path
(`_load_prev_streak` routes through it, and every dict it returns validates against `SCHEMA_FIELDS` for
its lane — M25); `_settle` delegates to `streak_record` **and consumes `BUCKET_RESETS_STREAK`** instead of
its inline `reset_buckets` literal, and **emits `reached_n` as `consecutive_green >= threshold`** (C5); every `resets_streak: yes` bucket (including `lane-red`/`env-red`/
`timeout-red`/`selection-red`/`unexpected-bucket`) zeroes `consecutive_green`, and `slow-run` **advances**
it (M11/M12); a cross-writer test validates a docker record and an embedded record and asserts the per-lane
required/forbidden subsets; `canary_dropped` never appears in an embedded record.

**Files:**
- Modify: `tools/testdb_canary_classify.py` (`_settle` at `:356`, `_write_atomic` at `:384`,
  `_load_prev_streak` at `:95–111` — M25)
- Modify: `tests/test_canary_classify.py` (new tests)

**Step 1 — write the failing tests:**

```python
def test_schema_fields_declares_every_emitted_field():
    from tools.testdb_canary_classify import SCHEMA_FIELDS, CANARY_DROP_THRESHOLD
    assert set(SCHEMA_FIELDS) >= {"run_id", "lane", "runs", "consecutive_green", "last",
                                  "canary_dropped", "reached_n", "resets", "drop_exemption"}

def test_per_lane_required_forbidden_subsets():
    from tools.testdb_canary_classify import required_fields, forbidden_fields
    assert forbidden_fields("embedded-local") == {"canary_dropped"}
    assert forbidden_fields("docker-half-b") == {"reached_n", "resets", "drop_exemption"}

def test_cross_writer_streak_schema_agrees(inputs):
    from tools.testdb_canary_classify import streak_record, validate_streak
    docker = classify(inputs["junitxml"], inputs["manifest"], inputs["step_wall"],
                      inputs["divergence_log"], None, RUN, lane="docker-half-b")
    embedded = streak_record(lane="embedded-local", run_id=RUN, runs=[RUN],
                             consecutive_green=1, last_bucket="green", last_detail="",
                             reached_n=False, resets=0, drop_exemption=False)
    assert validate_streak(docker) == [] and validate_streak(embedded) == []
    assert "canary_dropped" not in embedded


def test_lane_red_zeroes_the_streak(inputs):
    """M11: `lane-red` is NOT in the old `reset_buckets` literal — the new bucket must reset."""
    from tools.testdb_canary_classify import _settle
    prev = {"runs": [1, 2], "consecutive_green": 2, "canary_dropped": False}
    assert _settle(prev, "lane-red", "lane shape changed", 3, threshold=10)["consecutive_green"] == 0


def test_slow_run_advances_the_streak(inputs):
    """M12: 2x green + 1x slow-run + 7x green == 10 at n=10 (a slow PASS still counts)."""
    from tools.testdb_canary_classify import _settle
    prev = {"runs": [], "consecutive_green": 0, "canary_dropped": False}
    for i, bucket in enumerate(["green", "green", "slow-run", *(["green"] * 7)], start=1):
        prev = _settle(prev, bucket, "", i, threshold=10)
    assert prev["consecutive_green"] == 10


def test_reached_n_is_true_for_a_full_n_green_run(inputs):
    """C5: `_settle`/`streak_record` PRODUCE `reached_n = consecutive_green >= threshold`; with D3
    rule 8 pinning `threshold == n.requested`, a full N-green run must record `reached_n is True`.
    Task 2's prior fixture only ever set it False, so the field had no positive producer test."""
    from tools.testdb_canary_classify import _settle
    prev = {"runs": [], "consecutive_green": 0, "canary_dropped": False}
    for i in range(1, 11):
        prev = _settle(prev, "green", "", i, threshold=10)
    assert prev["consecutive_green"] == 10 and prev["reached_n"] is True
    assert prev["reached_n"] is (prev["consecutive_green"] >= 10)


def test_bucket_reset_semantics_are_declared_once(inputs):
    """M11: `_settle` reads BUCKET_RESETS_STREAK; an unknown bucket is a ValueError (fail closed)."""
    from tools.testdb_canary_classify import BUCKET_RESETS_STREAK, _settle
    assert BUCKET_RESETS_STREAK["lane-red"] is True and BUCKET_RESETS_STREAK["slow-run"] is False
    with pytest.raises(ValueError):
        _settle({"runs": [], "consecutive_green": 0}, "not-a-bucket", "", 1, threshold=10)


def test_load_prev_streak_records_validate(inputs, tmp_path):
    """M25: the READ path is not a second unvalidated constructor."""
    from tools.testdb_canary_classify import streak_from_prev, validate_streak
    for raw in ({}, {"runs": [1], "consecutive_green": 1}, {"runs": [], "consecutive_green": 0}):
        assert validate_streak(streak_from_prev(raw, lane="docker-half-b")) == []
```

**Step 2 — run to verify it fails:** `env -u TORTOISE_DB_URI uv run pytest tests/test_canary_classify.py -v`
Expected: FAIL — `ImportError: cannot import name 'SCHEMA_FIELDS'`.

**Step 3 — implement** D4: `SCHEMA_FIELDS`, `required_fields()`, `forbidden_fields()`, `streak_record()`,
`streak_from_prev()`, `validate_streak()`, `BUCKET_RESETS_STREAK`/`BUCKET_ADVANCES_STREAK`,
`_settle` delegating to `streak_record` and consuming the reset map, and `_load_prev_streak` routing
through `streak_from_prev` (M25).

**Step 4 — run to verify it passes** (same command). **Step 5 — commit.**

### Task 3: `config/ci-surfaces.yml` — the `embedded_family:` declaration

**Intent:** ONE declaration site for the named selections (D5), inert to `select()`, with the relation it
can actually assert (M23).
**Acceptance:** the key exists with `family_reproducers` containing `test_dr_endpoints.py`; every declared
file is a real `tests/` module and a member of a registered surface; the deliberate lane mix is recorded
(`lane_mix`); `select()` output is unchanged for an `embedded_family`-only manifest edit; `integrity()`
stays `[]`.

**Files:**
- Modify: `config/ci-surfaces.yml` (new top-level key after `carve_out:` / before `durations:` at `:894`)
- Modify: `tests/test_ci_selection.py`

**Step 1 — write the failing tests:**

```python
def test_embedded_family_declaration_is_inert_to_selection():
    m = load_manifest()
    assert m["embedded_family"]["family_reproducers"]
    before = select(["tortoise/projection/__init__.py"], "pull_request", m)
    m2 = deepcopy(m)
    m2["embedded_family"]["family_reproducers"] = ["test_dr_endpoints.py"]
    assert select(["tortoise/projection/__init__.py"], "pull_request", m2) == before

def test_embedded_family_reproducers_exist_and_pin_the_mandatory_file():
    m = load_manifest()
    fam = m["embedded_family"]["family_reproducers"]
    assert "test_dr_endpoints.py" in fam
    for f in fam:
        assert (TESTS_DIR / f).is_file(), f

def test_family_reproducers_relation_is_asserted():
    """M23: a real membership relation, today — not the comment F7 called a false invariant.
    The cross-language carve_out <-> TEST_NO_REDIRECT_STEMS relation stays #3862 (F14)."""
    m = load_manifest()
    fam = m["embedded_family"]["family_reproducers"]
    registered = set().union(*m["surfaces"].values())
    carve_out = set(ci_selection.carve_out_files(m))
    assert set(fam) <= (registered | carve_out), sorted(set(fam) - (registered | carve_out))
    mix = m["embedded_family"]["lane_mix"]
    assert mix["redirect_non_exempt"] == ["test_dr_endpoints.py"]
    assert "test_dr_endpoints.py" not in carve_out
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    assert "test_dr_endpoints" not in TEST_NO_REDIRECT_STEMS   # the recorded mix, not a silent one
```

**Step 2 — run to verify it fails:** `env -u TORTOISE_DB_URI uv run pytest tests/test_ci_selection.py -v`
Expected: FAIL — `KeyError: 'embedded_family'`.
**Step 3 — implement** the YAML block (D5), including `lane_mix` and `declared_sets.relation` (M23).
**Step 4 — run to verify it passes** + `python3 tools/ci_selection.py --integrity`. Expected: `[]` / rc 0.
**Step 5 — commit.**

### Task 4: tool skeleton — `BUCKET_MAP`, the closed set, the exit contract

**Intent:** lock the vocabulary and the exit codes **before** any loop code (SCOPE Step 0).
**Acceptance:** `BUCKET_MAP` has exactly 11 entries, the four `carried` names exist in the classifier's
ladder, and each entry's `resets_streak` equals `BUCKET_RESETS_STREAK` (D4/M11); a bucket outside the
closed set is `unexpected-bucket`; `exit_code()` implements 2→1→3→0 and its branches are persisted-tested
(M1); `DEFAULT_N = 10` with no hardcoded literal (AC3); the CLI is
`python3 tools/embedded_evidence.py run|red` (no `[project.scripts]`).

**Files:**
- Create: `tools/embedded_evidence.py`
- Create: `tests/test_embedded_evidence.py`

**Step 1 — write the failing tests:**

```python
def test_bucket_map_is_the_11_bucket_closed_set():
    from tools.embedded_evidence import BUCKET_MAP
    assert len(BUCKET_MAP) == 11
    assert set(BUCKET_MAP) == {"green","guard-red","manifest-red","unexpected-divergence",
        "env-red","timeout-red","divergence","lane-red","selection-red","unexpected-bucket","slow-run"}

def test_bucket_map_carried_names_exist_in_the_classifier_ladder():
    from tools.embedded_evidence import BUCKET_MAP
    src = (REPO_ROOT / "tools/testdb_canary_classify.py").read_text()
    for name, spec in BUCKET_MAP.items():
        if spec["origin"] == "carried":
            assert f'bucket = "{spec["ci_bucket"]}"' in src, name

def test_bucket_map_ci_bucket_column_equals_the_declared_ladder():
    """M38/C16: the ladder constant is declared in its PRODUCER, not the consumer — a rename in the
    classifier must break this, not assert the runner against itself."""
    from tools.embedded_evidence import BUCKET_MAP
    from tools.testdb_canary_classify import CI_LADDER_BUCKETS
    declared = {spec["ci_bucket"] for spec in BUCKET_MAP.values() if spec["ci_bucket"]}
    assert declared == set(CI_LADDER_BUCKETS)

def test_every_ci_bucket_name_exists_in_the_producer_source():
    """C16: the string-match pin covers ALL `ci_bucket` values, not `origin == "carried"` only."""
    from tools.embedded_evidence import BUCKET_MAP
    src = (REPO_ROOT / "tools/testdb_canary_classify.py").read_text()
    for name, spec in BUCKET_MAP.items():
        if spec["ci_bucket"]:
            assert f'bucket = "{spec["ci_bucket"]}"' in src, name

def test_divergence_is_unreachable_without_a_divergence_log(inputs):
    """M38/F9: the reachability claim, pinned — `divergence` requires a `divergence_log`."""
    from tools.testdb_canary_classify import classify
    rec = classify(inputs["junitxml"], inputs["manifest"], inputs["step_wall"],
                   None, None, RUN, lane="docker-half-b")
    assert rec["last"]["bucket"] != "divergence"

def test_bucket_map_reset_semantics_match_the_shared_declaration():
    """M11: BUCKET_MAP is not a second home for the reset rule."""
    from tools.embedded_evidence import BUCKET_MAP
    from tools.testdb_canary_classify import BUCKET_RESETS_STREAK
    for name, spec in BUCKET_MAP.items():
        assert spec["resets_streak"] == BUCKET_RESETS_STREAK[name], name

def test_default_n_is_not_hardcoded():
    """AC3 — assigned here (M10: the test was named but authored by no task)."""
    from tools.embedded_evidence import DEFAULT_N, BUCKET_MAP
    assert DEFAULT_N == 10
    src = (REPO_ROOT / "tools/embedded_evidence.py").read_text()
    assert "range(10)" not in src and "for _ in range(DEFAULT_N)" in src

def test_exit_code_precedence():
    from tools.embedded_evidence import exit_code
    assert exit_code({"violations": [], "closes_issue": True},  usage_error=True) == 2
    assert exit_code({"violations": ["UNATTRIBUTABLE"], "closes_issue": False}) == 1
    assert exit_code({"violations": ["x"], "closes_issue": False},
                     env_error=True) == 2                      # 2 beats 1
    assert exit_code({"violations": [], "closes_issue": False}) == 3
    assert exit_code({"violations": [], "closes_issue": True}) == 0

def test_cli_entry_is_direct_python():
    assert not (REPO_ROOT / "pyproject.toml").read_text().count("[project.scripts]")
```

**Step 2 — run to verify it fails:** `env -u TORTOISE_DB_URI uv run pytest tests/test_embedded_evidence.py -v`
Expected: FAIL — `ModuleNotFoundError: tools.embedded_evidence`.
**Step 3 — implement** `BUCKET_MAP` (D2), `CI_LADDER_BUCKETS` (**declared in the producer
`tools/testdb_canary_classify.py` and imported — C16/M38**), `VIOLATION_CONJUNCTS` (C7, the declared
violation set D8's `violations` derivation reads), `buckets_outside_closed_set()`, `exit_code()` (D8), the
`Usage:`/`Exit codes:` docstring in the `tools/drift-guard.py:24–28` style (F26), and
`sys.path.insert(0, str(REPO_ROOT))` before `from tools.testdb_canary_classify import ...` (F27). The
docstring + `BUCKET_MAP` are the **authoritative home** of the exit/bucket vocabulary; the runbook
quotes them and a drift test compares (M32, Task 13).
**Step 4 — run to verify it passes.** Add `test_module_imports_sibling_classifier_via_repo_root`.
**Step 5 — commit.**

### Task 5: selection resolution, manifest, and the fail-closed selection gate

**Intent:** resolve `--selection` from the ONE declaration (D5), produce the manifest through the reused
`skip-guard.py`, and never green on a vacuous selection.
**Acceptance:** `family` includes `test_dr_endpoints.py` (hard assert); `carve-out`/`whole-suite` cannot
close; `selection-red` (exit 1) on empty/missing/malformed manifest, `<2` nodeids, collect rc ≠ 0; the
selector record is emitted.

**Files:** Modify `tools/embedded_evidence.py`, `tests/test_embedded_evidence.py`.

**Step 1 — write the failing tests:** `test_family_requires_reproducer` (hard assert, exit 1);
`test_selection_red_on_missing_manifest` / `_on_malformed_manifest` / `_on_single_nodeid` /
`_on_empty_manifest` / `_on_zero_nodeids` / `_on_collect_failure` / `_on_duplicate_nodeid` (C9: the empty
and zero-nodeid half had NO test — `emit_manifest` writes a header-only file when the marker filters
everything out and `_read_manifest` treats that as a **valid empty set** (its fail-closed contract covers
only a malformed line), so only the runner's `<2` gate catches it; both ⇒ `selection-red`, exit 1, **no run
launched**; M58: a manifest whose `count` exceeds its nodeid-set size — a duplicated nodeid — is likewise
`selection-red`, exit 1); `test_selector_record_is_emitted` (asserts `selector.fn_version ==
ci_selection.SELECTION_FN_VERSION` and `selector.per_file` keys == the declared files, **and the recorded
VALUES**: `per_file["test_dr_endpoints.py"]["pull_request"]["full"] is False` with `"api" in surfaces` —
M22, so a vacuously full-matrix record cannot pass);
`test_run_marker_matches_manifest_marker` (the D5 rule-6 default `not track_b and not live` equals the
manifest marker — assigned here per M10); `test_carve_out_and_whole_suite_never_close`;
`test_family_receipt_records_the_embedded_lane_shape` (**C18**: the family receipt's
`environment.lane == {"uri_unset": True, "carve_out": "1", "expect_uri": False}` — the recorded shape the
env-lane conjunct reads).
**Step 2 — run to fail.** `env -u TORTOISE_DB_URI uv run pytest tests/test_embedded_evidence.py -k selection -v`
**Step 3 — implement:** `resolve_selection(name, manifest)`, `emit_manifest_via_skip_guard(files, marker)`
(calls `emit_manifest(files, marker, output)` from the importlib-loaded `tools/skip-guard.py`), the
selection gate.
**Step 4 — run to pass.** **Step 5 — commit.**

### Task 6: the N-run loop + per-run provenance capture

**Intent:** the first of the three new code pieces — one fresh subprocess per run, at a pinned commit,
with `tortoise.__file__` captured **from the child**.
**Acceptance:** `_run_once(..., runner=None)` is injectable (mirroring `emit_manifest(runner=…)`); each run
gets a fresh `mkdtemp`, the child env pops `lane_contract.CHILD_LANE_VARS` (the registry the tripwire's
`_CHILD_LANE_VARS` at `tests/test_tripwire.py:42–51` now also reads — M24, including
`TORTOISE_TEST_CARVE_OUT`, the 9th var; **that edit makes `tests/test_tripwire.py` the seventh `tests/`
diff path — C11/AC10/GAP-1**); `TORTOISE_TEST_CARVE_OUT=1` is set; `import_provenance` is
recorded per run; a dirty tree or a digest change is `tree_moved: true` and exit 1; a git error (rc≠0) is
exit 2 (M18); a partial child failure (`TimeoutExpired`/OOM/rc≠0 with no junitxml) is retained as a run
with a red bucket, a record written and the `mkdtemp` removed (M19); the per-run measured wall is passed
as `classify()`'s `step_wall` **path** (whole seconds) and recorded as the same number in
`runs[].step_wall_s` (M53); the N-run loop carries the `try/finally` + signal handling that `red` has
(M59).

**Files:** Modify `tools/embedded_evidence.py`, `tests/test_embedded_evidence.py`.

**Step 1 — write the failing tests:** `test_run_once_is_injectable`;
`test_child_env_pops_lane_vars_and_sets_carve_out`;
`test_import_provenance_is_captured_per_run`;
`test_real_subprocess_child_import_provenance` (one **real** `subprocess.run([sys.executable, "-m",
"pytest", …])` child whose `import_provenance` is read back — M21, so the Verification Plan's
"real subprocess pytest child" claim is delivered, not just claimed);
`test_dirty_tree_refuses_exit_1` (touch a tracked file);
`test_git_error_fails_closed` (inject a git runner returning rc≠0, and one returning empty stdout with
rc 0 — assert exit 2, a record carrying the failure, `worktree_clean` never `true` — M18);
`test_tree_move_between_runs_sets_tree_moved` (edit between run 1 and 2 via the injected runner);
`test_porcelain_digest_uses_status_porcelain_v2_not_write_tree` (an **unstaged** edit must change the
digest);
`test_child_timeout_is_retained_and_cleaned` (inject `subprocess.TimeoutExpired`; assert the run is
retained with `timeout-red`, a record is written, exit 1, the per-run `mkdtemp` is removed — M19);
`test_child_kill_and_truncated_junitxml_are_retained` (inject rc=137 with no junitxml and a truncated
junitxml; assert `env-red`/`infra-flake`-mapped red bucket, record written, exit 1 or 2, `mkdtemp`
removed — M19);
`test_step_wall_path_and_seconds_agree` (M53: the integer written under the passed `step_wall` path equals
`int(runs[].step_wall_s)`);
`test_timeout_expired_maps_to_timeout_red` (M53: the `TimeoutExpired` → `timeout-red` mapping, distinct
from the `env-red` rc≠0/no-junitxml path);
`test_sigint_mid_loop_leaves_no_child_or_mkdtemp` (M59: SIGINT during run 2 of 3 ⇒ no live child, the
run's `mkdtemp` removed, the documented exit code (130), and an `aborted` ledger note — or the documented
absence of a record);
`test_in_invocation_streak_accumulates` (C6: a synthetic 3-green invocation records
`consecutive_green == 3` — the runner's own fold over `runs[]`, with `prev_streak=None` passed only on
run 1; the prior text left the N-run value ownerless);
`test_run_ref_pins_the_measured_commit` (C12: with `--ref <sha>` the child's `tortoise.__file__` is under
the ref worktree and `pin.commit == pin.requested_ref`; the worktree is removed afterwards);
`test_nonexistent_run_ref_is_exit_2` (C12: `run --ref <unknown>` ⇒ exit 2, no traceback, no
`.git/worktrees` entry).
**Step 2 — run to fail** (`-k loop`). **Step 3 — implement** `_run_once`, `_loop_runs`, `_tree_digest`, the `try/finally` that always removes
the per-run `mkdtemp`, the `TimeoutExpired`/OOM/truncated-junitxml catch that maps to a red bucket
with a retained record (M19), the `step_wall` path writer (M53), and the SIGINT/SIGTERM handler +
`try/finally` on the N-run loop (M59).
**Step 4 — run to pass.** **Step 5 — commit.**

### Task 7: the two fail-closed gates and the enforced exit contract

**Intent:** F5 — `import_provenance` is **enforced**, not merely recorded; a bucket outside the closed set
is a parity violation.
**Acceptance:** out-of-root `tortoise.__file__` for **any** run ⇒ exit 1 `UNATTRIBUTABLE`; a sentinel
bucket from the classifier ⇒ exit 1 `unexpected-bucket`; a clean-but-non-closing run ⇒ exit 3; every case
in the D8 table is pinned.

**Files:** Modify `tools/embedded_evidence.py`, `tests/test_embedded_evidence.py`.

**Step 1 — write the failing tests:** `test_gate_1_out_of_root_is_unattributable_exit_1`;
`test_gate_2_unknown_bucket_is_exit_1`; `test_exit_3_on_non_closing_clean_run`;
**`test_violations_derivation_covers_every_conjunct`** (C7: one **synthetic** record per D9 conjunct and
per
pre-loop hard gate, asserting `violations == [r for r in reasons if r in VIOLATION_CONJUNCTS]` and exit 1
vs exit 3 — the whole 1-vs-3 distinction branches on this field; synthetic, so it does not depend on
Task 9's persisted-writer, which the C20 move puts later);
`test_usage_errors_exit_2` (`--n` **and** `--confidence`; **explicit `--n ∈ {0,-1,1}`**; derived `n < 2`;
**explicit `--n > --max-runs`** (`--n 100 --max-runs 50` ⇒ exit 2 — M55: the cap applies uniformly, not
only to the derivation); **`--max-runs` exceeded by the derivation** (`--confidence 0.95
--failure-probability 0.02` ⇒ `n=149 > 50` ⇒ exit 2 — M50); **`--lane nope` at the tool CLI ⇒ exit 2**
(M50: the classifier's `test_unknown_lane_is_rejected` pins the `ValueError`, the tool must translate it;
this is not the classifier `ValueError`);
**`C ∈ {0.0,1.0,-0.1,1.1}`, `p ∈ {0.0,1.0,-0.1,1.1}`, `p > C` with no traceback** — M3/M20; `--run-timeout
3301`; `--selection nope`); **and the C20 move: the record-out / persistence tests now live in Task 9**
(`test_exit_code_equals_persisted_and_process_returncode`, `test_in_tree_record_out_is_refused`,
`test_tracked_record_out_is_exit_2`, `test_unwritable_record_out_parent_is_exit_2` — they need Task 9's
writer, and Task 7 precedes it, so their "run to pass" could not pass where they were).

**D8 exit-table coverage (M50).** Every row of D8's exit-2 list now has a named case: `--n`/`--confidence`
both given, explicit `--n < 2`, explicit `--n > --max-runs` (M55), derived `n > --max-runs`, the derivation
domain, `--run-timeout > 3300`, unknown `lane`, unknown `--selection`, unreadable/unwritable/tracked
`--record-out` parent, relative `--ledger-root` inside the measured root, `TMPDIR` unusable, a census
that cannot run **or is `inconclusive`/over `max_orphans` (C4)**, the load-ceiling refusal, and a non-zero
`git status`/absent `git` — the last two asserted in
Task 10 and Task 6 respectively.
**Step 2 — run to fail.** **Step 3 — implement** `gate_provenance()`, `gate_bucket_set()`, and wire
`exit_code()` into `main()`.
**Step 4 — run to pass.** **Step 5 — commit.**

### Task 8: `red` — the paired-red demonstration

**Intent:** the second new code piece — a red **for the same selection, in the same lane**, at a named
pre-fix ref, **cause-labelled from server-side evidence**.
**Acceptance:** `git worktree add --detach` + the same invocation path; a GREEN ref ⇒ exit 1
`UNPAIRED-GREEN-AT-REF`; the cause is read from `redis.log` (`CAUSE_CLASSES`), not from the pytest message;
an unrelated nodeid with the same message does **not** match; `try/finally` + `worktree remove --force` +
`mkdtemp` leave nothing behind; the explicit `--pairing-ref` input resolves (strict ancestor) and
`ref_role` is **derived** from it (D16/C1).

**Files:** Modify `tools/embedded_evidence.py`, `tests/test_embedded_evidence.py`.

**Step 1 — measure and capture the real evidence (mandatory, [GAP-5]).** Run the family selection once at
`37d5ef00c` under the carve-out lane; capture the embedded daemon's `redis.log`; pin the
`CAUSE_CLASSES` regexes against the captured lines. Record the capture path in the test fixture.
**Contrary-measurement branch (M16).** If the capture **refutes** the assumed patterns — e.g.
`Can't fork for module: File exists` appears together with `Module fork exited pid:` (so `requires_absent`
can never match), the save-class red emits no `Background saving (started|terminated)` line, or every
`requires_lines` set misses — then: record the capture **verbatim** in the fixture, **do not pin a refuted
regex set**, file a residual + issue, and revise the tripwire's D19 invariant, because every red would
otherwise be `unattributed` (D9 conjunct 8 never satisfied → the tool can never close). In that case
`unattributed` is the only safe verdict and it is non-closing. `test_unattributed_log_is_non_closing`
asserts a log matching **none** of the declared `requires_lines` yields `cause == "unattributed"`,
`closes_issue: false`, exit **3** — never green, never a crash.
**Step 2 — write the failing tests:** `test_red_uses_the_same_invocation_path_as_green`;
`test_green_ref_exits_1_unpaired_green_at_ref`;
`test_red_cause_is_labelled_from_redis_log_not_the_pytest_message` (two fixtures with **identical** pytest
messages and different `redis.log` ⇒ different `cause`);
`test_multi_match_log_obeys_cause_precedence` (all three line families present ⇒ `module-fork-hang`, the
highest precedence — M30);
`test_unrelated_nodeid_with_same_message_does_not_match_expected_causes`;
`test_record_digest_binds_manifest_ref_and_tool` (threat row 6 — assigned here per M10; the digest binds
`red.ref_tree_object`, obtained with `git rev-parse <ref>^{tree}`);
`test_redis_log_missing_is_unattributed_not_a_crash` (path absent),
`test_redis_log_unreadable_is_unattributed` (chmod 000),
`test_redis_log_truncated_is_unattributed` (only the first assumed line),
`test_redis_log_rotated_mid_run_is_unattributed` — each asserts `cause == "unattributed"`, exit 3, a
recorded `cause_evidence.redis_log`, no exception (M17);
`test_red_leaves_no_worktree_behind` (a failing run still `git worktree list`-clean);
`test_pairing_ref_rule_is_recorded` (**C1: asserts the DERIVATION, not set membership** — given
`--pairing-ref <sha>` a strict ancestor of the measured commit, `ref_role` is
`last-before-first-family-fix` (or `per-cause` with `--cause`); absent `--pairing-ref` it is
`pinned-head-pre-fix`; same-as-measured / descendant / unrelated ⇒ exit 2. Set membership alone made
threat row 5's "a ref that is not the pairing ref ⇒ `closes_issue=false`" unfalsifiable);
`test_failure_digest_is_recorded_and_nodeid_sensitive` (M51: every red run carries a non-null
`runs[].failure_digest`; two nodeids with the **same** pytest message get **different** digests, and a
green run is `null` — this is the field's reader);
`test_nonexistent_ref_is_exit_2`, `test_ambiguous_short_sha_is_exit_2`, `test_tag_ref_is_resolved` (M57:
`--ref` edges — each asserting exit 2 for the two failures, `git worktree list` unchanged, the `mkdtemp`
removed);
`test_worktree_add_failure_leaves_no_registration` (M57: inject a failing `git worktree add`; assert exit
2, the `mkdtemp` removed, `git worktree list` unchanged, and **no** new `.git/worktrees/<id>` entry).
**Step 3 — run to fail.** **Step 4 — implement** `_demonstrate_red`, `_label_cause` (precedence-ordered,
D10), the digest binder, and the ref-resolution/worktree-add failure path (exit 2, cleanup — M57).
**Step 5 — run to pass.** **Step 6 — commit.**

### Task 9: the record writer, ledger, `TMPDIR`, and the streak file

**Intent:** the third new code piece — build the record through a **collision-safe** writer and the shared
streak declaration, and land the ledger outside the repo.
**Acceptance:** the `embedded-evidence/1` schema validates field-by-field (D7); the ledger is under
`tempfile.gettempdir()/pi-embedded-evidence/{selection}/{commit12}/{invocation_id}/` with named per-run
files (M15/C13);
`TMPDIR` is resolved in Python and recorded with its source; the streak filename is **not**
`config/testdb-canary-streak.json` and is **namespaced** under `{selection}/{commit12}/`; the embedded lane
reads it **never as authority** (M14); the writer is collision-safe under concurrency **including two
invocations of the same selection+commit** (M15/C13); the recorded
`reproduce` string is self-contained (M28); ledger writes leave the repo tree clean; the `record_role`
producer is defined and positive-tested (`--record-role`, `closing` **only** with a same-invocation paired
red **and an explicit `--pairing-ref`** — M52/C1/C2); every write-path failure edge exits 2 with no
traceback (relative `--ledger-root` inside the
measured root, unwritable ledger root, `--record-out` a directory, `TMPDIR` empty/unwritable — M56); an
omitted `--record-out` defaults to `LEDGER_ROOT/record.json` and is recorded as such (M46); the per-run
`mkdtemp` roots are removed after the invocation and the ledger history is bounded
(`LEDGER_HISTORY_BOUND = 20`, mirroring the docker streak's `runs[:20]` — M60); the record envelope agrees
with **`tools/ci_timing.py`'s generator**, not its 43-byte committed fixture (C17/M62).

**C20(a): the record-out / ledger / persistence tests moved here from Task 7** — they need this task's
writer (`_write_record`) and could not "run to pass" at Task 7's position.

**Files:** Modify `tools/embedded_evidence.py`, `tests/test_embedded_evidence.py`.

**Step 1 — write the failing tests:** `test_record_schema_fields` (every D7 key);
`test_ledger_path_is_under_tmpdir_and_not_the_repo`;
`test_tmpdir_resolved_in_python_not_shell` (monkeypatch `TMPDIR=""` ⇒ the ledger root is `/tmp/...`, never
`/pi-embedded-evidence`); `test_streak_name_is_not_the_ci_file`;
`test_streak_is_never_read_as_authority` (threat row 9 — assigned here per M10): two sequential `n=2`
invocations with the shared file present assert the second verdict derives only from its own `runs[]`, and
a selection/commit change starts a fresh chain (M14);
`test_concurrent_writers_do_not_collide` (**C13: rewritten for the same-namespace case** — two
simultaneous `run` invocations of the SAME selection+commit ⇒ distinct `INVOCATION_DIR`s, neither raises,
the shared artifacts parse, and **each invocation's record carries its own `run_id` and `invocation_id`**;
the prior version asserted that property against one path, where it could not hold);
`test_prune_does_not_remove_a_live_run_file` (C13: a prune with a live invocation present never removes
its `run-*.json`/`record.json`, and never raises `FileNotFoundError`);
`test_reproduce_command_is_self_contained` (re-execute the recorded string and compare `pin.commit` and
`manifest.digest` against the original record — M28/M10);
`test_clean_tree_untouched_by_ledger_writes` (`git status --porcelain` empty after a full run);
`test_record_role_defaults_to_historical_attestation` (F16);
`test_record_role_closing_requires_same_invocation_pair` (M52/C1: `--pairing-ref` + a same-invocation
paired red ⇒ `record_role == "closing"`; `--record-role closing` without `--pairing-ref`, or without the
paired red ⇒ exit 2, no `closing` record written);
`test_documented_closing_invocation_yields_closing_role` (**C2: the EXACT documented GREEN-half command**
— `run --selection family --n 10 --ref <fix-commit> --pairing-ref <sha> --record-role closing
--record-out …` — yields `record_role == "closing"` end-to-end, against a synthetic fixed-commit fixture,
so the plan's documented closing path is executed by a test rather than asserted in prose);
`test_ref_role_is_derived_not_declared` (**C1**: `ref_role` follows from `(--pairing-ref, pin.commit,
ancestry)` — labelling a pinned HEAD or a commit after the fix `last-before-first-family-fix` no longer
passes);
`test_pairing_ref_must_be_strict_ancestor` (**C1**: same-as-measured / descendant / unrelated ref ⇒
exit 2);
`test_record_out_defaults_to_ledger_record_json` (M46: omitted `--record-out` ⇒ `LEDGER_ROOT/record.json`,
recorded);
`test_exit_code_equals_persisted_and_process_returncode` (**moved from Task 7 — C20(a)**: persist a record
for each of the 0/1/2/3 branches; assert `exit_code(persisted_verdict) == persisted exit_code ==
subprocess returncode` — M1);
`test_in_tree_record_out_is_refused` (**moved from Task 7 — C20(a)**; superseded by #4572: an in-tree
`--record-out` is exit 2 with NO record, and the message names the offending path, states why it matters,
and gives the default outside the tree — M5);
`test_tracked_record_out_is_exit_2` and `test_unwritable_record_out_parent_is_exit_2` (**moved from
Task 7 — C20(a)**);
`test_relative_ledger_root_inside_measured_root_is_exit_2` (a relative `--ledger-root` resolving inside
the measured root ⇒ exit 2, no traceback, `git status --porcelain` empty afterwards — M56);
`test_unwritable_ledger_root_is_exit_2` (M56);
`test_record_out_directory_is_exit_2` (M56: `--record-out <a directory>` ⇒ exit 2);
`test_tmpdir_empty_or_unwritable_is_exit_2` (M56: `TMPDIR=""` falls back, `TMPDIR` at a non-writable
path ⇒ exit 2 with no traceback);
`test_write_edges_leave_the_tree_clean` (every M56 edge above leaves `git status --porcelain` empty);
`test_per_run_mkdtemp_is_removed_after_invocation` (M60: after a full 3-run invocation no per-run root
survives under the ledger/tmp root);
`test_ledger_history_is_bounded` (M60: > 20 retained outputs prune to the last `LEDGER_HISTORY_BOUND`);
`test_record_envelope_matches_ci_timing_vocabulary` (**C17/M62: asserted against
`tools/ci_timing.py`'s generator** — `SCHEMA_VERSION`, `COUNT_KEYS`, `MAX_HISTORY_DEFAULT` and the
`failed_tests` field its record writer builds — **not** the 43-byte `docs/ci-timing.json` fixture, which
contains none of them; the shared envelope is exactly three items: a schema-version field, a closed outcome
vocabulary, a bounded history — the "nodeid-keyed field" claim is dropped).
**Step 2 — run to fail.** **Step 3 — implement** `_write_record`, `ledger_path()`, the collision-safe
`_write_atomic` (mkstemp + `os.replace`, D6), the **per-invocation namespace + `LOCK_FILE` flock (C13)**, the
namespaced streak path, the self-contained
`reproduce` string (`--ref <pin.commit>` + `--marker`), the `--pairing-ref` / `--record-role` producer
(C1/C2/M52), `ref_role` derivation from ancestry (C1), the omitted
`--record-out` default, the `LEDGER_HISTORY_BOUND` prune (M60/C13) and the envelope-vocabulary export
(M62).
**Step 4 — run to pass.** **Step 5 — commit.**

### Task 10: the `closes_issue` conjunction — load, rate change, hermeticity

**Intent:** make the closing rule distinguish **fixed** from **did not fire** (F17/F18) and make
hermeticity computable (F24).
**Acceptance:** the D9 truth table (green-only → false; + valid paired red → true; + `carve-out` red →
false; + non-overlapping bands → false; + `unattributed` cause → false; + wrong file list → false;
+ `historical-attestation` role → false; **+ empty `runs[]` → false** (M3); **+ all-skipped junitxml → not
green, false, exit 1** (M2); + `slow-run` advances the streak — M12; + wrong env lane → false (C18)); a
**fail-closed pre-flight load ceiling** refuses above it; per-run load bands are recorded and the two halves
must overlap;
the red is re-attempted at the fixed commit and must not appear (rate change) **or the mutation must bring
it back AND the red must not have appeared without it** (C8); the per-run orphan delta is scoped to the
run's `mkdtemp` root (C3) and a census that cannot run **or whose own verdict is
`inconclusive`/`over max_orphans`** is `env-red` **exit 2** (M13/C4).

**Files:** Modify `tools/embedded_evidence.py`, `tests/test_embedded_evidence.py`.

**Step 1 — write the failing tests:** `test_closes_issue_truth_table` (parametrised, incl. the M2/M3 rows
above **and C8's two mutation rows — `{attempted:true, appeared:true, mutation:"m1"}` ⇒ false/exit 1 and
`{mutation:"m1", mutation_red_returned:true, appeared:false}` ⇒ true**);
`test_empty_runs_is_not_closing` (M3: `--n 0`/`--n 1` are refused exit 2 **and** a
synthetic empty-runs record is a violation, never a vacuous pass);
`test_all_skipped_junitxml_is_not_green` (M2: inject a junitxml whose every testcase is
`<skipped message="embedded unavailable">` ⇒ not `green`, `closes_issue:false`, exit 1);
`test_load_ceiling_refuses_before_starting` (exit 2, record written);
`test_load_band_boundaries_are_deterministic` (`load1 ∈ {11.999, 12.0, 24.0, 24.001}` ⇒ `L-A`, `L-B`,
`L-C`, `L-C` — M4); `test_bad_load_ceiling_is_exit_2` (non-numeric / `0` / negative ⇒ usage error, no
traceback — M4);
`test_load_bands_must_overlap` (green L-C / red L-A ⇒ exit 1);
`test_at_fixed_commit_rate_change_required` (not appeared + rate_change False + no mutation ⇒ false);
`test_mutation_path_brings_the_red_back` (mutation id **and** `mutation_red_returned: true` **and**
`appeared: false` ⇒ true);
`test_mutation_without_a_returning_red_is_not_closing` (**C8**: `{attempted: true, appeared: true,
rate_change: false, mutation: "m1", mutation_red_returned: false}` ⇒ **false, exit 1** — the red still
appears at the fixed commit, the fix demonstrably ineffective, and the old `bool(mutation)` disjunct closed
on it);
`test_hermeticity_is_computed_not_declared` (a per-run orphan delta > 0 ⇒ `degraded` ⇒ not closing);
`test_real_census_invocation` (one **real** `embedded_orphans.census()` call, asserting the per-run delta
is computed from real output — M21);
`test_census_that_cannot_run_is_env_red_not_an_abort` (**asserts `env-red` AND exit 2** — M13);
`test_census_delta_is_scoped_to_the_run_root` and `test_foreign_orphan_outside_run_root_is_not_attributed`
(**C3**: inject a `census()` payload with socket paths inside AND outside the run root; assert only in-root
ones move `per_run_orphan_delta`/`hermeticity` and `foreign` is recorded, never attributed);
`test_inconclusive_census_is_env_red` (**C4**: `unclassified == live_servers > 0` ⇒ `env-red`, exit 2,
`hermeticity.census_inconclusive` true — the imported `census()` returns no such key, so the runner must
re-derive it or consume the CLI JSON);
`test_census_over_max_orphans_is_env_red` (**C4**: `orphans > max_orphans` ⇒ `env-red`, exit 2,
`hermeticity.census_within_budget` false);
`test_red_half_receipt_is_non_closing` (a non-closing verdict exits 1 and never carries the closing status
string — assigned here per M10, not added by Task 14).
**Step 2 — run to fail.** **Step 3 — implement** `closes_issue()` (D9, including C8's `mutation_red_returned` conjunct and C18's
`environment-lane-wrong` conjunct), `load_band()` with the D14
half-open boundaries, `preflight_load_ceiling()` + `--load-ceiling` validation, `_hermeticity()` (D17,
including C3's run-root scoping and C4's census-verdict re-derivation).
**Step 4 — run to pass.** **Step 5 — commit.**

### Task 11: the committed tripwire — `tests/test_embedded_save_tripwire.py`

**Intent:** the deterministic red half (SCOPE (c)) — a falsifiable pin of the fork-source asymmetry, in the
`tests/test_tripwire.py` subprocess style, **existing assertions untouched**.
**Acceptance:** (i) a real embedded server's `CONFIG GET save` is non-empty **and** the `auto-aof-rewrite`
axis is live (a **measured** value, not an assertion-by-omission); (ii) **every member of the image-based
universe** — every compose `services:` entry whose `image:` is redis/falkordb + every workflow `services:`
image line (C15), of which the 8 workflow + 1 compose lane sites = **9** carry a `--save` line — is either
save-disabled after comment-stripping + quote normalization, **or** a **recorded exclusion with a reason**
(including the `no-site-declared` members, whose image defaults are live), with the count pinned; (iii) the
AOF dimension is asserted — a lane site with
`appendonly yes` and no bounded `--auto-aof-rewrite-min-size` is a failure, and a **live** docker
`auto-aof-rewrite` is a **test failure** (M9); (iv) a mutation flips one site and the tripwire fires;
(v) no `xfail`/`skip`.
**Files:**
- Create: `tests/test_embedded_save_tripwire.py`
- (registration in task 12)

**Step 1 — measure (mandatory, [GAP-6]).** On a real embedded redislite daemon:
`CONFIG GET save`, `CONFIG GET appendonly`, `CONFIG GET auto-aof-rewrite-percentage`,
`CONFIG GET auto-aof-rewrite-min-size` — with `TORTOISE_EMBEDDED_AOF` unset **and** `=1`. Record the
measured values; they are the pinned expectations. Also measure the **docker** lane’s AOF state: a live
`auto-aof-rewrite` on a lane site (`appendonly yes` ∧ `auto-aof-rewrite-percentage > 0`) is a **FAILURE**,
not a recorded value (M9); a workflow site with no declared AOF args is recorded as `aof_unmeasured` (a
declared residual — the falkordb image default is unmeasured).
**Step 2 — write the failing tests:** `test_embedded_lane_retains_save_scheduling`;
`test_embedded_lane_aof_rewrite_axis_is_recorded`;
`test_every_declared_lane_site_disables_periodic_saves` (scan the **image-based universe** — every compose
`services:` entry whose image is redis/falkordb, every workflow `services:` image line — and map each to its
`REDIS_ARGS:`/`--save` site line (or `no-site-declared`); **strip YAML comments first**, then normalize
`''`/`\"\"`/`""`; assert the 9 lane sites and each matches `--save\s+["']{0,2}["']{0,2}` — C15/M7/M8);
`test_declared_site_count_is_pinned` (= **9** — and the universe is enumerated, not inferred);
`test_no_site_declared_member_is_recorded` (**C15**: a universe member with no `--save` line — the
`apps/graph-viz` / `integrations/crm/twenty` shape — is a **recorded finding with a reason**
(`save-live-by-default`), never an omission);
`test_commented_save_line_is_not_a_site` (the **negative fixture** — a commented `--save` line must NOT be
counted; without the comment strip the matcher yields 16 lines and this pin fails on its own fixture —
M7); `test_declared_aof_config_is_bounded` (a `appendonly yes` lane site without
`--auto-aof-rewrite-min-size` fails; the compose site passes — M9);
`test_exclusions_are_recorded_with_reasons` (the module names `graph-scripts/setup.py:647`,
`apps/graph-viz/docker-compose.yml`, `integrations/crm/twenty/docker-compose.yml` as recorded exclusions
— M8); `test_mutation_of_a_declared_site_fires_the_tripwire` (rewrite a copy of a workflow in `tmp_path`
and run the scanner against it ⇒ violation).
**Step 3 — run to fail:** `env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embedded_save_tripwire.py -v`
**Step 4 — implement** the scanner + the measured pins.
**Step 5 — run to pass.** **Step 6 — commit.**

### Task 12: registration + reachability

**Intent:** make the new files **classify** (F3), **reach** selection (F12) and **run embedded** (F22).
**Acceptance:** `integrity(load_manifest()) == []`; both new files are in a surface **and** in `carve_out:`;
both stems are in `TEST_NO_REDIRECT_STEMS` and the exact-set pin; `select(["tools/embedded_evidence.py"])`,
`select(["tools/testdb_canary_classify.py"])` **and `select(["tools/lane_contract.py"])`** are **full matrix,
fail closed** (C14); the runbook/diff gates
are pinned (M10); the runner consumes `lane_contract.CHILD_LANE_VARS` (M24), whose shared read edits
`tests/test_tripwire.py` — the **seventh** `tests/` path (C11).

**Files:**
- Modify: `config/ci-surfaces.yml` (`core:` add 2 names; `carve_out:` add 2 names)
- Modify: `tests/_embedded.py` (`TEST_NO_REDIRECT_STEMS`)
- Modify: `tests/test_markers.py` (the `expected` frozenset at `:411`)
- Modify: `tests/test_tripwire.py` (the shared `CHILD_LANE_VARS` read — C11, the 7th `tests/` path)
- Modify: `tools/ci_selection.py` (`TOOL_CARVEOUTS`)
- Modify: `tests/test_ci_selection.py`, `tests/test_embedded_evidence.py`

**Step 1 — write the failing tests:**

```python
def test_embedded_evidence_tool_change_fails_closed_to_full():
    for path in ("tools/embedded_evidence.py", "tools/testdb_canary_classify.py",
                 "tools/lane_contract.py"):     # C14: the third new tools/ module is carved out too
        r = _sel([path])
        assert r["full"] is True and r["test_files"] == "ALL" and "core" in r["surfaces"], path

def test_testdb_canary_classify_tool_change_fails_closed_to_full():
    """M10/M6: named explicitly — the classifier's own production surface."""
    r = _sel(["tools/testdb_canary_classify.py"])
    assert r["full"] is True and r["test_files"] == "ALL"

def test_lane_contract_tool_change_fails_closed_to_full():
    """C14: a lane-vocabulary-only PR must NOT drop to tier-1 smoke (`test_lane_vocabularies_agree`
    must run on the PR that changes the shared contract)."""
    r = _sel(["tools/lane_contract.py"])
    assert r["full"] is True and r["test_files"] == "ALL"

def test_tripwire_reads_the_shared_lane_registry():
    """C11: the shared read is real (`_CHILD_LANE_VARS` is built from `lane_contract.CHILD_LANE_VARS`),
    which is why `tests/test_tripwire.py` is in the allowlist below."""
    src = (REPO_ROOT / "tests/test_tripwire.py").read_text()
    assert "lane_contract" in src and "CHILD_LANE_VARS" in src

def test_git_diff_allowlist():
    """AC10 — the SOLE enforcement of the SEVEN-path tests/ allowlist (M10: named but unauthored).
    C11: `tests/test_tripwire.py` is the seventh — the M24 shared-read edit."""
    changed = subprocess.run(["git", "diff", "--name-only", "origin/main...HEAD"],
                             capture_output=True, text=True, cwd=REPO_ROOT).stdout.split()
    tests_changed = {p for p in changed if p.startswith("tests/")}
    assert tests_changed <= {
        "tests/test_embedded_evidence.py", "tests/test_embedded_save_tripwire.py",
        "tests/_embedded.py", "tests/test_markers.py", "tests/test_ci_selection.py",
        "tests/test_canary_classify.py", "tests/test_tripwire.py"}, sorted(tests_changed)
    assert not any(p.startswith(".github/") for p in changed)
    assert not any(p.startswith("graph-scripts/") for p in changed)
    assert ".gitignore" not in changed

def test_new_embedded_tests_are_carve_out_and_core():
    m = load_manifest()
    for f in ("test_embedded_evidence.py", "test_embedded_save_tripwire.py"):
        assert f in m["surfaces"]["core"] and f in m["carve_out"]

def test_bucket_reset_semantics_agree_across_modules():
    """M11: the runner's BUCKET_MAP is not a second home for the reset rule."""
    from tools.embedded_evidence import BUCKET_MAP
    from tools.testdb_canary_classify import BUCKET_RESETS_STREAK
    for name, spec in BUCKET_MAP.items():
        assert spec["resets_streak"] == BUCKET_RESETS_STREAK[name], name
```

**Step 2 — run to fail:** `env -u TORTOISE_DB_URI uv run pytest tests/test_ci_selection.py tests/test_markers.py -v`
**Step 3 — implement** the five edits (surface registration, `carve_out:` membership,
`TEST_NO_REDIRECT_STEMS` + the `test_markers.py` exact-set pin, `tests/test_tripwire.py`'s shared
`CHILD_LANE_VARS` read — C11, and `TOOL_CARVEOUTS` **including `tools/lane_contract.py`** — C14); point the
runner at `lane_contract.CHILD_LANE_VARS` (M24).
**Step 4 — run to pass** + `python3 tools/ci_selection.py --integrity` (rc 0, `[]`).
**Step 5 — commit.**

### Task 13: the runbook and the docs index row

**Intent:** F26 — exit 3 and the receipt schema get a documentation home outside `tools/`.
**Acceptance:** `docs/ops/embedded-lane-evidence.md` documents the 11-bucket closed set, the
`embedded-evidence/1` schema, the 0/1/2/3 contract, the `reproduce` command, **the `record_role`
semantics (which roles can close — and the exact CLOSING invocation,
`run --selection family --n 10 --ref <fix-commit> --pairing-ref <sha> --record-role closing`, with the
strict-ancestor rule — C1/C2)**, **`attestation: self-declared`** and **the honest limit
(tamper-evident, not tamper-proof; re-run the command and treat `exit_code` as advisory)** (M31/M33);
`docs/00_index.md` has a row linking it; tests assert the row exists **and that the runbook’s bucket list
and exit codes match the tool’s constants** (M32).
**Files:** Create `docs/ops/embedded-lane-evidence.md`; Modify `docs/00_index.md`.
**Step 1 — write the failing tests**
(`tests/test_embedded_evidence.py::test_runbook_registered_in_docs_index`): read `docs/00_index.md`,
assert `docs/ops/embedded-lane-evidence.md` appears;
`test_runbook_matches_tool_constants` (M32): the runbook’s bucket list equals `set(BUCKET_MAP)` and the runbook
states `0`, `1`, `2` and `3` as exit codes; `test_runbook_states_record_role_and_honest_limit` (M31): the
runbook text contains the record-role rule ("only `closing` can close; `historical-attestation` never
can"), the closing invocation with `--pairing-ref` and `--record-role closing`, the string `self-declared`,
and the honest-limit sentence.
**Step 2 — run to fail.** **Step 3 — write the runbook + the index row.** The tool’s `Usage:`/`Exit codes:`
docstring and `BUCKET_MAP` are the **authoritative** home; the runbook quotes them (M32). **Step 4 — run to
pass.** **Step 5 — commit.**

### Task 14: the PR #1 RED-half receipt (evidence run, non-closing)

**Intent:** ship the cold half (SCOPE Step 6 / F16) — a **historical attestation**, never
`PAIRED-RED-DEMONSTRATED`.
**Acceptance:** the record says `record_role: "historical-attestation"`,
`verdict.closes_issue == false`, `verdict.status == "RED-AT-PINNED-REF"` (the string
`PAIRED-RED-DEMONSTRATED` appears **nowhere**), `exit_code == 1`; `--pairing-ref` is **not** passed (C1), so
`pin.pairing_ref is null` and `ref_role == "pinned-head-pre-fix"` — the non-closing role; plus a diagnostic
`carve-out` record; the
PR body states the split and that #3827 is **not** closed.

**Files:** none — this is an evidence run + the PR body (the Step-4 test is authored in Task 10).
**Step 1 — the family RED run (recorded, ~2–5 min/run):**
```bash
env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
  python3 tools/embedded_evidence.py run --selection family --n 3 --ref 37d5ef00c \
  --record-out /tmp/3827-red-half.json
```
(`--ref` pins the measured commit so the recorded `reproduce` string is self-contained — M28. No
`--load-ceiling` is passed: the recorded default `DEFAULT_LOAD_CEILING = 60.0` admits this host’s 38–49
regime as band `L-C`; M4.)
Expected: `exit_code: 1`; `verdict.status: "RED-AT-PINNED-REF"`; `verdict.closes_issue: false`;
`record_role: "historical-attestation"`; at least one run with a bucket whose `is_red` is true; the
per-run load band is `L-C` and the red attestation is recorded in the same band.
**Step 2 — the diagnostic `carve-out` record** (documented as *incapable* of being the family's evidence).
**Step 3 — the `red` historical attestation:** `python3 tools/embedded_evidence.py red --selection family
--ref 37d5ef00c --record-out /tmp/3827-red-at-ref.json`; expect `UNPAIRED-GREEN-AT-REF` handling to be
exercised or a red cause recorded (either outcome is recorded verbatim).
**Step 4 — assert the receipt shape:** re-run the already-authored
`test_red_half_receipt_is_non_closing` (Task 10 Step 1 — M10: moved there so it has a test-first Step 1; a
non-closing verdict must exit 1 and must never carry the closing status string).
**Step 5 — commit the receipt's `reproduce` line into the PR body** (the ledger itself stays in
`$TMPDIR`).

---

### Task 15: R1 — bind the certification to the shipping surface (D23)

**Intent:** every mutation / rate-change proof the record accepts must be asserted at the surface a
consumer reaches, not an internal helper. The #3888 measurement (`tortoise_search` and `tortoise_recall`
both returned `sessionId:''` while the suite stayed green) is the reason.
**Acceptance:** `SHIPPING_SURFACES` is a declared constant (`tortoise_search`, `tortoise_recall`);
`at_fixed_commit.surface` must be a member and `surface_assertion` non-empty; an internal-helper-only proof
is non-closing (exit 1); the runbook states the rule.
**Files:** Modify `tools/embedded_evidence.py`, `tests/test_embedded_evidence.py`.

**Step 1 — write the failing tests:** `test_certification_binds_to_the_shipping_surface` (a record whose
`surface`/`surface_assertion` name the consumer surface ⇒ conjunct 15 true);
`test_internal_seam_only_mutation_is_non_closing` (a record with `surface: "TortoiseSDK.search"` or an empty
`surface_assertion` ⇒ `closes_issue:false`, exit 1, reason `certification-not-on-shipping-surface`);
`test_shipping_surfaces_are_declared` (the constant is exactly the two agent-facing names).
**Step 2 — run to fail.** **Step 3 — implement** `SHIPPING_SURFACES` + D9 conjunct 15 (D23).
**Step 4 — run to pass.** **Step 5 — commit.**

### Task 16: R2 — head-SHA binding, post-review invalidation, statement-deletion operator (D24)

**Intent:** the certificate is bound to the reviewed **head SHA** and re-run after any post-review edit; the
mutator includes **statement deletion of the fix's own population/edit branch** (the #3888 class).
**Acceptance:** `pin.head_sha == pin.commit` and `pin.post_review_dirty` false ⇒ conjunct 16 true;
`post_review_dirty: true` ⇒ `closes_issue:false`, exit 1; the mutation disjunct requires
`mutation_operator` to start with `statement-deletion:` **and** `mutation_target_is_fix_branch: true`; the
#3888 rationale (`sdk.py:13202`, `:13239-13245`) is recorded in the tool docstring and the runbook.
**Files:** Modify `tools/embedded_evidence.py`, `tests/test_embedded_evidence.py`.

**Step 1 — write the failing tests:** `test_certificate_is_bound_to_head_sha`;
`test_certificate_invalidated_by_post_review_edit`;
`test_population_branch_deletion_mutation_is_required` (a `mutation` id with `mutation_operator: "bit-flip"`
or `mutation_target_is_fix_branch: false` ⇒ false/exit 1); `test_3888_population_branch_is_recorded` (the
docstring and runbook carry the `sdk.py:13202`/`:13239-13245` rationale).
**Step 2 — run to fail.** **Step 3 — implement** the head-SHA fields, D9 conjunct 16, and the operator rule
(D24). **Step 4 — run to pass.** **Step 5 — commit.**

---

## GREEN half — deferred (PR #1 ships NO code for it)

**The GREEN half cannot be delivered by #3827 until a fix commit exists** (SCOPE correction (i):
`a59adeaa8` is not an ancestor of HEAD; #3845/#3813 are OPEN). This section is a **handoff**, not a task
list, and it mandates **no new code** — if executing it requires a tool change, that is a bug in PR #1.

```bash
# AT THE FAMILY FIX'S PR (a fixed commit exists), same lane, same selection:
# #4572: --record-out is OMITTED — it defaults to
# $TMPDIR/pi-embedded-evidence/record.json, OUTSIDE the measured tree. An in-tree
# --record-out is a usage error (exit 2, no record written).
python3 tools/embedded_evidence.py run --selection family --n 10 --ref <fix-commit> \
  --pairing-ref <last-commit-before-first-family-fix> --record-role closing \
  --surface tortoise_search --surface-assertion <resolving-test-id>
# Then copy the receipt into the fixed commit's PR and commit it there:
cp "$(python3 -c 'import tempfile,pathlib;print(pathlib.Path(tempfile.gettempdir())/"pi-embedded-evidence"/"record.json")')" \
  docs/evidence/3827-green.json
```

(`--surface`/`--surface-assertion` are R1/D23 and are **required for closing**: the conjunct
`certification-not-on-shipping-surface` admits only a `SHIPPING_SURFACES` member with a non-empty
resolving test-ID, and the tool cannot infer which test exercises the agent-facing surface — so the
closing invocation must declare them (#4203; a caller cannot certify a binding it never declared).

(`--ref` makes the recorded `reproduce` string self-contained — M28. `--pairing-ref` is the explicit,
validated pre-fix ref (C1: a **strict ancestor of the measured commit**; equal/descendant/unrelated ⇒
exit 2) from which `ref_role` is **derived**, and `--record-role closing` is what makes the receipt a
closing one (C2 — the previous command passed neither, so it could only ever emit
`historical-attestation`, which D9 conjunct 12 forbids from closing). `--record-out` is OMITTED: it
defaults to `$TMPDIR/pi-embedded-evidence/record.json`, **outside the measured tree**, and the receipt is
copied into the fix PR's `docs/evidence/3827-green.json` afterwards. An in-tree `--record-out` is a
**usage error (exit 2, no record)** — #4572 option (a), which supersedes the M5 exclusion and its
`pin.record_out_excluded` field.)

There is **no `--paired-red-record` input** (F15): the tool **re-runs `red` itself** at the **pairing ref just
declared by `--pairing-ref`** (D16/C1: *the last commit before the FIRST family fix landed*, **or** the
pairing is **per-cause**, one record per cause) **within the same invocation**, so a caller-supplied red is
neither needed nor accepted. An immediate-parent (`commit^`) rule is **not** used (F19: at the parent of
the third fix, causes 1–2 are already fixed and `commit^` has no referent); D9 conjunct 7 accepts only
`ref_role ∈ {last-before-first-family-fix, per-cause}` **and** requires `red.ref == pin.pairing_ref`. A
hand-written record cannot close (D20: content-bound by digest, not authenticated).

**Cost model (F30, corrected).** The "15–18 min" figure is the **26-file `test-carve-out` job**, not the
`family` selection. `family` ≈ **2–5 min/run** → N=10 ≈ **20–50 min** (1–2 h under 4–5× contention). A
`carve-out`/`whole-suite` N=10 is not operationally realistic and is already forbidden as family evidence.

**Handoff obligations (no code; owner action — F20):**

1. **The green-half obligation is ALREADY FILED as issue #3867** (OPEN) — M26: `#3827` no longer depends on
   this document surviving. Title: *"test(embedded): run the N=10 GREEN half of #3827 at the family fix
   commit (paired red re-run at the pairing ref)"*. The re-check mechanism is the issue’s own existence
   plus the `Blocks:` link below — **not** prose in this document.
2. Request a `Blocks: #3827` link on #3845, #3813, #3685 (the family issues) so the green-half obligation
   is visible where the fix lands — **the remaining handoff action**.
3. `#3827` closes **only** on the paired, rate-changed record (D9) — never on a green-only certificate and
   never on a green against `carve-out`/`whole-suite`.
4. **#3867's body is now CORRECT on the record-out point — do not re-issue it for that reason.**
   Its text says an in-repo `--record-out` is *"refused by design"*; #4572 option (a) **restored**
   exactly that behaviour (a resolved `--record-out` landing inside the measured tree is a usage error —
   exit 2, no record written — and there is **no** path-based exclusion). The M5 ruling that superseded
   it ("the in-repo receipt is allowed and excluded from the pin") was itself reversed, because the
   exclusion over-matched in five spellings; the class was removed rather than guarded. #3867 does still
   need re-issuing, for a different reason: the closing invocation must carry
   `--surface <SHIPPING_SURFACES member> --surface-assertion <resolving-test-id>` (#4203) in addition to
   `--pairing-ref` and `--record-role closing` — i.e.
   `run --selection family --n 10 --ref <fix-commit> --pairing-ref <sha> --record-role closing --surface <member> --surface-assertion <test-id>`
   with `--record-out` omitted (it defaults outside the measured tree) — along with the `--pairing-ref`
   strict-ancestor rule and the `closes_issue`/exit-code contract, so the issue that owns the GREEN half
   does not instruct its assignee to run an unreachable path.

---

## Acceptance criteria

Re-derived from SCOPE + PLAN; each is falsifiable by a named test.

1. **Composed, not rebuilt** — `run`/`red` only; the five primitives are *called*, not copied; no
   `[project.scripts]`. (`test_cli_entry_is_direct_python`)
2. **Three named selections, fail-closed** — `family` asserted to contain `tests/test_dr_endpoints.py`;
   `carve-out`/`whole-suite` cannot close. (`test_family_requires_reproducer`,
   `test_carve_out_and_whole_suite_never_close`)
3. **N is a parameter** — `DEFAULT_N = 10` with no hardcoded literal in the loop; probability derivation
   supported **within the declared domain `0 < p < C < 1`** (outside ⇒ exit 2, no traceback — M20);
   `--n` + `--confidence` ⇒ exit 2; **explicit `--n < 2`** and derived `n < 2` ⇒ exit 2 (M3);
   `observed_failure_rate` recorded; the word "fixed" is never printed.
   (`test_default_n_is_not_hardcoded`, `test_usage_errors_exit_2`)
4. **Pin airtight** — `worktree_clean` + sha256 over `git status --porcelain=v2` (**not**
   `git write-tree`); dirty or moved ⇒ exit 1, offending run retained.
   (`test_porcelain_digest_uses_status_porcelain_v2_not_write_tree`, `test_dirty_tree_refuses_exit_1`)
5. **Attribution enforced** — `import_provenance` per run; out-of-root ⇒ **exit 1** `UNATTRIBUTABLE`.
   (`test_gate_1_out_of_root_is_unattributable_exit_1`)
6. **Paired RED = committed tripwire AND record** — `xfail`/`skip` forbidden; a GREEN ref ⇒ exit 1; the
   red's **cause** must be in `expected_causes` and its record digest-bound; the pairing ref is an explicit
   `--pairing-ref` (a strict ancestor of the measured commit) from which `ref_role` is derived, and the
   closing role is reachable **only** with it (C1/C2).
   (`test_green_ref_exits_1_unpaired_green_at_ref`, `test_red_cause_is_labelled_from_redis_log_not_the_pytest_message`,
   `test_pairing_ref_must_be_strict_ancestor`, `test_documented_closing_invocation_yields_closing_role`)
7. **Red restarts the streak to 0, retained** — `drop_exemption == false`; `CANARY_DROP_THRESHOLD = 5`
   appears nowhere in the tool as an embedded threshold; the streak lives in a new name, never
   `config/testdb-canary-streak.json`. (`test_streak_name_is_not_the_ci_file`, `test_per_lane_required_forbidden_subsets`)
8. **The split is explicit** — PR #1 = RED half (`closes_issue:false`); GREEN half deferred; #3827 not
   closed by PR #1. (task 14 receipt + PR body)
9. **`closes_issue` is machine-readable AND enforced** — exit 3 when false for a non-violation reason;
   exit 1 when false because of a violation. (`test_exit_code_precedence`,
   `test_exit_3_on_non_closing_clean_run`)
10. **No scope creep.** `.gitignore` and `.github/**` untouched; the `tests/` diff touches **only these
    seven paths** — `tests/test_embedded_evidence.py`, `tests/test_embedded_save_tripwire.py`,
    `tests/_embedded.py`, `tests/test_markers.py`, `tests/test_ci_selection.py`,
    `tests/test_canary_classify.py`, `tests/test_tripwire.py` (**C11** — the M24 shared-registry read, which
    makes the six-path count seven) — and no existing assertion is weakened (the 16 `classify(...)` calls
    are updated only to add `lane="docker-half-b"`; `tests/test_tripwire.py`'s added assertion only
    replaces its local literal with `lane_contract.CHILD_LANE_VARS`); both new test files are **registered**;
    `graph-scripts/setup.py` and `.github/workflows/*` are unmodified; the rail's fail-open fix
    (#1168/#3715) is **not** delivered; no new third-party dependency. The **`tools/`** diff additionally
    creates `tools/lane_contract.py`, points `tools/ask_recall_bench.py` at it, and adds it to
    `TOOL_CARVEOUTS` (M24/C14) — none is a `tests/` path, but the tripwire read is, which is why the
    allowlist grew by one.
    (`test_git_diff_allowlist` over `git diff --name-only`, added to `tests/test_embedded_evidence.py`;
    assigned to Task 12 per M10)
11. **Bucket vocabulary is closed** — 11 entries; the four `carried` names exist in the classifier's ladder;
    a bucket outside the set is exit 1. (`test_bucket_map_is_the_11_bucket_closed_set`,
    `test_bucket_map_carried_names_exist_in_the_classifier_ladder`)
12. **Reachable** — both `tools/` paths **and `tools/lane_contract.py`** select the full matrix (fail closed).
    (`test_embedded_evidence_tool_change_fails_closed_to_full`,
    `test_lane_contract_tool_change_fails_closed_to_full` — C14)
13. **R1 — certification binds to the SHIPPING surface** (D23): the record names a `SHIPPING_SURFACES`
    member and its resolving test-ID; an internal-seam-only proof is non-closing.
    (`test_certification_binds_to_the_shipping_surface`,
    `test_internal_seam_only_mutation_is_non_closing`)
14. **R2 — certificate bound to the reviewed head SHA** (D24): `pin.head_sha == pin.commit`,
    `post_review_dirty:false`; any post-review edit invalidates it and requires a re-run; the mutation
    operator is a `statement-deletion:` of the fix's own population/edit branch.
    (`test_certificate_is_bound_to_head_sha`, `test_certificate_invalidated_by_post_review_edit`,
    `test_population_branch_deletion_mutation_is_required`)
15. **N=10 is DECLARED LOCAL** (D3827-b): the plan and the tool state it as **ours**, with the one-line
    note that no source makes any N canonical — never presented as a borrowed standard.
    (`test_default_n_is_not_hardcoded` plus the D13 declaration and the runbook text pin)

## Adversarial threat surface — per-item traceability matrix (29119-3)

**Adversarial** — correctness is "a false `closes_issue:true` cannot be produced **accidentally**" (D20).
Bound: the declared surface, **2 cycles** (`proportional-gates` adversarial domain). Acceptance =
**every declared class covered by a test + green CI**; residual findings from cycle 1 are filed, not
chased. **This merge rests on threat-list coverage, not a literal `NO ISSUES FOUND`.**

> ⛔ **Traceability rule (ISO/IEC/IEEE 29119-3 traceability matrix; 29119-2 per-item evidence — adopted
> 2026-09-18, verdict D3827-a).** A coverage claim is stated **per item, never as a count**. Every
> declared class row below carries a **resolving test-ID** — a test name that also appears in a Task step
> in this plan, and that the implementation PR's `code-review` confirms exists at the **reviewed head
> SHA** (R2). A row with no resolving, **reviewed** test-ID is marked a **residual** and named in the
> residual table below. **A matrix row asserting coverage with no resolving test-ID is a FALSE ROW** and
> must never be reported as covered. The prior `covered=9` was a bare total traceable to nothing; it is
> replaced by the named lists in this section.

**Boundary (named, not counted):**
`[ADVERSARIAL-BOUND] cycles=2 threats=15 covered=11 residuals=4` —
`covered_classes=reproducer-less-family,tree-move,out-of-root-import,paired-red-identity,forged-record,green-ref,unknown-bucket,forged-streak,load-band-overlap,internal-seam-only,post-review-edit`,
`residual_classes=zero-nodeid-manifest,mutation-disjunct,self-declared-ref-role,inconclusive-census`.

This is an **`adversarial-capped` escalation exit, not a clean completion**: the four residual classes
remain open obligations (review home: the implementation PR's `code-review`, tracker vehicle **#3879**).

| # | Class (name) | Adversarial input | Required behaviour | Resolving test-ID | Status |
|---|---|---|---|---|---|
| 1 | `reproducer-less-family` | `family` without the reproducer; green on `carve-out`/`whole-suite` | hard membership assert; `closes_issue=false` for the other two **regardless of colour**; exit 3 | `test_family_requires_reproducer` | covered |
| 2 | `vacuous-selection` | zero/one nodeid, **empty manifest**, **zero nodeids**, all-skipped junitxml, guarded skip | `selection-red` / `guard-red`, exit 1 — never 0 | `test_selection_red_on_single_nodeid`; `test_selection_red_on_empty_manifest`; `tests/test_canary_classify.py::test_guard_red_resets`; `test_all_skipped_junitxml_is_not_green` | **residual** — the **`zero-nodeid-manifest`** sub-class has no resolving reviewed test (see residuals) |
| 3 | `tree-move` | edit a test / `git checkout` between runs | `tree_moved: true` ⇒ exit 1, run retained | `test_tree_move_between_runs_sets_tree_moved` | covered |
| 4 | `out-of-root-import` | `import tortoise` resolving outside the measured root | exit 1 `UNATTRIBUTABLE` (enforced) | `test_gate_1_out_of_root_is_unattributable_exit_1` | covered |
| 5 | `paired-red-identity` | paired red for a different selection, a **different file list**, a ref that is not the pairing ref, an **empty cause label**, **an unrelated failure's cause label**, or a **conflated cause** (the `save`-class `could not fork` vs #3845's module-fork hang) | `closes_issue=false`, exit 3 | `test_closes_issue_truth_table`; `test_red_cause_is_labelled_from_redis_log_not_the_pytest_message`; `test_multi_match_log_obeys_cause_precedence`; `test_pairing_ref_must_be_strict_ancestor` | covered |
| 6 | `forged-record` | a **hand-written** record with a recomputed digest | content-bound to (manifest digest, red **ref tree object**, tool version) ⇒ cannot close **accidentally**; tamper-proofing is *out of scope* | `test_record_digest_binds_manifest_ref_and_tool` | covered |
| 7 | `green-ref` | `red` against a ref that is green for the selection | exit 1 `UNPAIRED-GREEN-AT-REF` | `test_green_ref_exits_1_unpaired_green_at_ref` | covered |
| 8 | `unknown-bucket` | `classify()` returns a bucket outside the closed set | exit 1 `unexpected-bucket` | `test_gate_2_unknown_bucket_is_exit_1` | covered |
| 9 | `forged-streak` | a forged ledger/streak file | the streak is derived in-memory, never read as authority (`prev_streak=None`, M14); closing also needs the digest-bound red | `test_streak_is_never_read_as_authority` | covered |
| 10 | `load-band-overlap` | **(F17)** a red measured at load 80 and a green at load 3 | the two halves must overlap in one declared band ⇒ exit 1 | `test_load_bands_must_overlap` | covered |
| 11 | `no-rate-change` | **(F18)** a "fix" that changes nothing, run in a long idle window | the red is re-attempted at the fixed commit and must **not** appear (rate change) — or a **declared mutation operator** must bring it back | `test_at_fixed_commit_rate_change_required`; `test_mutation_path_brings_the_red_back` | **residual** — the **`mutation-disjunct`** sub-class has no resolving reviewed test (see residuals) |
| 12 | `pairing-ref` | **(F19)** a fix PR split across 3 issues | the pairing ref is **an explicit validated input** — the last commit before the **first** fix (a strict ancestor of the measured commit), or per-cause records; `ref_role` is **derived**, never caller-declared | `test_pairing_ref_rule_is_recorded`; `test_ref_role_is_derived_not_declared`; `test_pairing_ref_must_be_strict_ancestor` | **residual** — the **`self-declared-ref-role`** sub-class has no resolving reviewed test (see residuals) |
| 13 | `host-global-census` | **(F24)** a host-global census delta on a 262-worktree box | the delta is scoped to this run's `mkdtemp` root; a census that cannot run **or is `inconclusive`/over `max_orphans`** is `env-red` | `test_census_that_cannot_run_is_env_red_not_an_abort`; `test_census_delta_is_scoped_to_the_run_root`; `test_foreign_orphan_outside_run_root_is_not_attributed`; `test_inconclusive_census_is_env_red`; `test_census_over_max_orphans_is_env_red` | **residual** — the **`inconclusive-census`** sub-class has no resolving reviewed test (see residuals) |
| 14 | `internal-seam-only` | **(R1, D23 — new)** a certificate whose mutation is asserted against an internal helper, not the surface a consumer reaches | certification must bind to the **shipping surface** (the `tortoise_search`/`tortoise_recall` wire output); an internal-helper-only mutation is non-closing | `test_certification_binds_to_the_shipping_surface`; `test_internal_seam_only_mutation_is_non_closing` | covered (declared this revision) |
| 15 | `post-review-edit` | **(R2, D24 — new)** a certificate valid at a staged blob, edited after review, never re-run | the certificate is bound to a **head SHA** and re-run after any post-review edit; the mutator includes **statement deletion of the fix's own population/edit branch** | `test_certificate_is_bound_to_head_sha`; `test_certificate_invalidated_by_post_review_edit`; `test_population_branch_deletion_mutation_is_required` | covered (declared this revision) |

### Residual classes — named, not counted (4)

These four declared classes have **no resolving, reviewed test-ID** at the plan's reviewed head. Each is a
**would-survive mutant** and a tracked obligation — a **NAMED GAP** under 29119-3, never a count. **Review
home: the implementation PR's `code-review` (§6.6 second-model gate + the 10-cycle loop); the tracker
vehicle is #3879.**

| Residual class | Row | What is uncovered | Why it is unresolved (not "no test") | Tracking |
|---|---|---|---|---|
| `zero-nodeid-manifest` | 2 | a **zero-nodeid resolved manifest** (distinct from an empty manifest file) | `test_selection_red_on_zero_nodeids` was authored in the **UNREVIEWED C1–C20 pass**, so it does not resolve at a reviewed head; and a zero-nodeid manifest vs a 1-nodeid manifest can both produce `selection-red`, so the distinguishing case is unproven | impl-PR `code-review` · #3879 |
| `mutation-disjunct` | 11 | the **mutation** disjunct — `mutation ∧ mutation_red_returned` | `test_mutation_path_brings_the_red_back` exercises the conjunct, but the **mutation operator itself was undeclared** (no `mutation_operator` field); "a mutation brought the red back" is unfalsifiable without knowing what was deleted. **D24 now declares the operator, but the class stays residual until the impl-PR review confirms the test resolves at the reviewed head SHA** | impl-PR `code-review` · #3879 |
| `self-declared-ref-role` | 12 | `ref_role` **derived**, never caller-declared | `test_ref_role_is_derived_not_declared` is stated, but its derivation input set (`(--pairing-ref, pin.commit, ancestry)`) was amended in the unreviewed C-pass | impl-PR `code-review` · #3879 |
| `inconclusive-census` | 13 | the census's **own fail-closed verdict** (`inconclusive` / over `max_orphans`) | `test_inconclusive_census_is_env_red` / `test_census_over_max_orphans_is_env_red` were added in the unreviewed C-pass, and the keys they read are produced only in `embedded_orphans.main()`, not by the imported `census()` (D17) | impl-PR `code-review` · #3879 |

**Disposition rule.** A residual does not block PR #1's merge *as coverage of that class* — it blocks any
claim that the class is **covered**. The implementation PR must not report a residual class as covered.
The residual list's own review is part of that PR's `code-review`; the tracker vehicle is **#3879**.
Rows 14–15 are declared **this revision** (2026-09-18, verdict) and their tests are authored by Tasks 15–16
below; like every other row they resolve only when the implementation PR's review confirms them at the
reviewed head SHA.

**Out of scope (declared, not silently skipped):** (a) tamper-**proofing** (hash chains / signatures /
append-only verification — A3's surface); (b) CI wiring / merge gating (D18); (c) the rail's shape-blind
fail-open fix (#1168/#3715); (d) fixing the family causes (#3845/#3813/#3685) or reaping orphans;
(e) `--import-mode=importlib` (deleted from the spec — it appears only in a docstring at
`tests/test_entity_delete_rebuild.py:26`); (f) #3814's CI lane; (g) multi-host aggregation;
(h) `graph-scripts/setup.py:647` (`--save 60 1000` — a setup script, a different axis; recorded as an
explicit exclusion in the tripwire); (i) the `carve_out` ↔ `TEST_NO_REDIRECT_STEMS` cross-language relation
assertion (**#3862** only — F14).

**Honest limit:** the receipt is tamper-**evident** (re-derivable via its recorded `reproduce` command),
not tamper-**proof**.

---

## Under-specified points — decisions taken (highest risk)

These are the places where the SCOPE/PLAN records did **not** determine the answer and a design decision
was required. They are the plan's highest-risk items and the plan-review gate should scrutinise them first.

| # | Under-specified point | Decision taken | Why it is risky |
|---|---|---|---|
| **GAP-1** | **The `tests/` diff allowlist count.** F22 says "a 4th `tests/` edit (`tests/test_markers.py`)"; F25 says "the allowlist is **four** paths" but then lists five; the task brief lists four *config/module* edits. | The real required set is **seven** `tests/` paths: the 2 new files, `tests/_embedded.py`, `tests/test_markers.py`, `tests/test_ci_selection.py`, `tests/test_canary_classify.py`, **and `tests/test_tripwire.py`** (C11 — the M24 shared-read edit to `_CHILD_LANE_VARS`). Adding a `TEST_NO_REDIRECT_STEMS` entry requires editing **both** `tests/_embedded.py:157` (the tuple) **and** `tests/test_markers.py:411` (the hardcoded exact-set `frozenset`) — F22's "4th edit" undercounts by one; the M24 shared read adds the seventh. AC10 is therefore an enumerated **seven-path allowlist**. | A reviewer checking AC10 against F22/F25 would flag a false AC10 violation — or, worse, an implementer following F22 literally would red `test_no_redirect_stems_registry_exact` (or, omitting `tests/test_tripwire.py`, red `test_git_diff_allowlist`). |
| **GAP-2** | **`lane` required vs default.** F23 (Phase 7, later) says "`lane` becomes a **required keyword** (no default)". F14/F28 (earlier) say a default `lane="docker-half-b"` "is positionally backward-compatible" and that "a 7th-positional `lane` is backward-compatible". | **F23 governs** (later correction): `lane` is **keyword-only and required**; unknown lane ⇒ `ValueError`; all 16 existing call sites are updated to `lane="docker-half-b"` and keep their existing assertions verbatim, plus a new `lane` tag assertion. F14/F28's positional-compat claim is recorded as superseded. | If F14/F28 were intended (a default), the gate becomes **omittable** — a tautology. If F23 is intended, the 16 call sites are a `tests/` diff an AC10 reviewer must expect. Both cannot hold; the plan picks the stronger gate. |
| **GAP-3** | **The `--max-runs` cap value.** F29 requires "a `--max-runs` cap with an explicit refusal" and gives the formula's failure mode (`p=0.02, C=0.95 → n=149`) but **no value**. | `DEFAULT_MAX_RUNS = 50` (≈4 h worst case at the measured 2–5 min/run), refusal ⇒ exit 2 **for both the derived and the explicit `--n` form (M55)**. | Too low blocks a legitimate high-confidence derivation; too high permits a 149-run overnight job nobody sanctioned. |
| **GAP-4** | **`--run-timeout` default and the `slow-run` factor.** F29 gives the ceiling (`--run-timeout > 3300` ⇒ exit 2) and the measured baseline (family's mandatory file **70.57 s**, `test_hosted_backup` 13.1 s), but no default or factor. | `DEFAULT_RUN_TIMEOUT_S = 900` (3× the per-test `--timeout=300`, ~12× the measured baseline); `SLOW_RUN_FACTOR = 2.0` × the recorded baseline (baseline 300 s until a pilot measures it); `slow-run` is recorded and non-resetting. | A wrong timeout turns a slow host into a wave of `timeout-red`s (which reset the streak) or hides genuine hangs. |
| **GAP-5** | **The exact `redis.log` cause-class discriminators.** F15 gives the shape ("line class + the absence of `Module fork exited pid:`") and names the causes in prose, not the patterns. | `CAUSE_CLASSES` declares 4 labels (`save-child-slot`, `aof-rewrite-fork`, `module-fork-hang`, `unattributed`) with `requires_lines` / `requires_absent` and a declared `CAUSE_PRECEDENCE` (M30); task 8 step 1 **measures and captures a real `redis.log`** at the pinned ref and pins the regexes; until pinned, `unattributed` cannot close. **Contrary-measurement branch (M16):** if the capture refutes the assumed patterns, record it verbatim in the fixture, **do not pin a refuted regex set**, file a residual + issue, and revise the D19 invariant — otherwise every red is `unattributed` and the tool can never close. | Guessing the patterns would re-create the F15 P0 (a conflated cause) one level down. The measurement step is what makes this safe — **it must not be skipped**. |
| **GAP-6** | **The AOF auto-rewrite axis in the tripwire.** F15(ii) demands the tripwire cover `save` **and** `auto-aof-rewrite`; but whether the docker lanes leave `auto-aof-rewrite` live is **unmeasured** (the falkordb image's `appendonly` default vs compose's `--auto-aof-rewrite-min-size 1gb` vs the workflow sites' `--save ''`-only). | The docker axis is now **two-dimensional** (M9): a lane site declaring `appendonly yes` without a bounded `--auto-aof-rewrite-min-size` **fails**, and a **live** docker `auto-aof-rewrite` measured in task 11 step 1 is a **test FAILURE**, not a recorded value; a workflow site with no declared AOF args is recorded `aof_unmeasured` (a declared residual — the image default is unmeasured). | The plan asserts an invariant it has not measured for the workflow sites; assuming it holds would make the tripwire a false green. The `aof_unmeasured` residual is honest; the live-failure rule means the axis can now **fail**. |
| **GAP-7** | **The exit code for a fail-closed pre-flight load refusal.** F11's precedence list has no entry for it (F17 only requires the refusal). | **Exit 2** (environment error) with the record still written; the *band-overlap* failure is **exit 1** (violation). | A refusal could equally be argued as exit 1; the distinction matters because a reviewer must be able to tell "the host was unusable" from "the code failed". |
| **GAP-8** | **Who scopes the orphan census.** F24 requires the census "scoped to this run's `mkdtemp` root / own pid tree" **and** "the pgrep timeout is raised", but the scope's module-edit list does **not** include `tools/embedded_orphans.py`. | The **runner** filters `census()` output to socket paths under the run's `mkdtemp` root (**C3**: `test_census_delta_is_scoped_to_the_run_root`, `test_foreign_orphan_outside_run_root_is_not_attributed`), **re-derives or consumes the census's OWN fail-closed verdict** (`inconclusive`/`within_budget`/`max_orphans` — the imported `census()` returns none of them; **C4**: `test_inconclusive_census_is_env_red`, `test_census_over_max_orphans_is_env_red`), and treats a `census()` raise as `env-red` (exit 2); the source-scoped edit (root filter + raised 5 s pgrep timeout) is **filed as #3869** (OPEN). `Deferred: filter census() output in the runner — Good alternative: source-scoped census (`census(*, deep, jobs, root=None)` + raised pgrep timeout) — Cost: one module edit + ~2 unit tests + 1 AC10 diff path — Rationale: PR #1 scope; filed as #3869.` | Filtering after a host-global `pgrep -f` still pays the enumeration cost and can still time out on a loaded box — the filed edit is the real fix. |
| **GAP-9** | **Does the *evidence* test file need `carve_out` + redirect exemption?** F22 names only "the tripwire"; F30 says "the two new test files". | Both new files: registered in `core`, added to `carve_out:`, added to `TEST_NO_REDIRECT_STEMS` (+ the pin). | A superset is safe for correctness but widens the `tests/` diff (GAP-1) and moves the evidence test out of the docker fast matrix. |
| **GAP-10** | **F20's green-half issue.** The plan requires it to be filed "as its own issue". | **Already filed as #3867** (OPEN — M26): "test(embedded): run the N=10 GREEN half of #3827 at the family fix commit (paired red re-run at the pairing ref)". The remaining action is the `Blocks: #3827` link on #3845/#3813/#3685. | F20's whole point was that the green half must not depend on this plan document surviving — #3867 is the durable home; the re-check is the issue's own existence + the `Blocks:` link. |

---

## Review cycle log

- **problem-verify:** cycle 1 = P1×1 + P2×3 + P3×2 + P4×1 (all fixed/re-derived by the controller);
  cycle 2 = **NO P0/P1** → gate passes (SCOPE).
- **solution-verify:** cycle 1 = P1×3 + P2×6 → fixed (F1–F4, F9, F10); cycle 2 re-dispatched.
- **duplication / architecture (advisory):** P0×1 (arch) + P1×4 → fixed (F2, F5, F6, F7, F8); F7's false
  invariant filed as **#3862**; Tortoise source unavailable (DEGRADED) — recorded.
- **Phase 7 (codebase/docs + Devil's-Advocate):** F15–F30 applied; F15 (P0 causal defect) is also
  load-bearing for B5's fix direction and is escalated.
- **`parallel_check_plan` gate:** `CHECKOUT_GUARD_ENFORCE=1 bash
  /Users/danielospina/Documents/GitHub/agent-infra/scripts/parallel_work_check.sh plan`
  → **`C3: CLEAR  no-board-skip: no board session — open-PR overlap check skipped` (rc 0)** on
  2026-09-17. ⚠️ The gate only passes with a **literal** absolute path; invoking it through
  `"$AGENT_INFRA_PATH/…"` is blocked by the checkout guard (`script-indirection`, fail-closed) — recorded
  because the skill's own guidance says to "run the absolute path".
- **plan-review:** **cycle 1 IN PROGRESS** — the gate has run (5 parallel reviewers: structural,
  integration, coherence, failure-mode, duplication/architecture) and its merged issues (P0 M1–M6,
  P1 M7–M34) are being fixed in place; `doc_status: draft`. The plan MUST NOT proceed to Execution Handoff
  until the loop exits clean, or a capped/stalled exit is surfaced to the user (see `writing-plans` →
  `workflow/05-review-handoff.md`).
- **plan-review:** cycle 1 → M1–M34 fixes applied (see CHANGELOG).
- **plan-review:** cycle 1 → M35–M62 (P2) fixes applied (see CHANGELOG).
- **plan-review:** cycle 2 (FINAL — adversarial cap reached) → C1–C20 deep-fix applied; P2 residuals carried to the human — filed as **#3878** (the gate exited `adversarial-capped`, NOT clean: `[ADVERSARIAL-BOUND] cycles=2 threats=13 covered=9 residuals=mutation-disjunct,inconclusive-census,self-declared-ref-role,zero-nodeid-manifest`; the C1–C20 deep-fix items applied after the final cycle are UNREVIEWED and must not be treated as verified).

## Research verdict revision — 2026-09-18 (D3827-a · D3827-b · D3827-c · D3827-d)

**The plan un-halts.** The certification question was researched and a convergent **standard** answers it —
so it is **adopted** (a question a standard answers is a defect in the brief, not an owner decision), not
reopened. Full record: `~/.pi/agent/state/lane-reports/W0-3827-RESEARCH-VERDICT-2026-09-18.md` and the
2026-09-18 comment on #3827.

- **Ruling:** ADOPT paired-RED certification (SWE-bench `FAIL_TO_PASS`/`PASS_TO_PASS`; Defects4J; TDD's
  red→green→refactor). It **CONFIRMS** owner decision **#3847** and the `AGENTS.md` adversarial bound — it
  is stricter in the same direction, so no decision is contradicted (the contradiction test was run FIRST).
- **D3827-a — paired-RED standard adopted** for the family fix's certification.
- **D3827-b — N=10 is DECLARED LOCAL.** Flakiness does **not** converge on "N consecutive greens": four
  independent practitioner sources describe detect → quarantine → fix, and **no source makes any N
  canonical**. N=10 is stated as **ours** (D13, AC15) so a future reader can tell it is a declared choice.
- **D3827-c — mutation testing adopted** as the "demonstrated failure" mechanism, with **statement deletion
  of the fix's own branch** as a required operator (D24/Task 16).
- **D3827-d — R1 and R2 adopted as OUR additions** (the standard does not reach them): bind certification
  to the **shipping surface** (D23/Task 15), and bind the certificate to a **head SHA** with re-run after any
  post-review edit (D24/Task 16).

**The three mandated plan changes, applied here:**
1. **Per-item traceability matrix** — every declared class row carries a resolving test-ID or is a **named
   residual**; the residual list states **which classes** it counted (`zero-nodeid-manifest`,
   `mutation-disjunct`, `self-declared-ref-role`, `inconclusive-census`). The bare `covered=9` is replaced
   by named `covered_classes`. (§ Adversarial threat surface.)
2. **R1 — bind to the shipping surface** — D23 + D9 conjunct 15 + Task 15. Rationale: the #3888 measurement
   (search/recall returned `sessionId:''` while the suite stayed green; the 17 tests pinned an internal seam).
3. **R2 — head-SHA binding + population-branch deletion** — D24 + D9 conjunct 16 + Task 16. Rationale: #3888's
   manual proof was against a staged blob and the population branch was never in the mutant set.

**Review state of this revision.** These edits are **not** plan-reviewed by a fresh-context cycle (the
plan-review loop had already exited `adversarial-capped`); they therefore inherit the same review home as
the residuals — the **implementation PR's `code-review`** — and the tracker vehicle **#3879**. This
revision does **not** claim a clean plan-review exit.

### Verification numbers recorded during drafting

| Fact | Value | Source |
|---|---|---|
| Pinned commit | `37d5ef00c` | `git rev-parse` in `.worktrees/w0-substrate` |
| Branch / worktree | `chore/w0-substrate` / `.worktrees/w0-substrate` | same |
| Existing `classify(...)` call sites in `tests/test_canary_classify.py` | **16** | `grep -c` (`:82…:284`) — confirms F28 |
| `--save ''` sites in `.github/workflows/*.yml` | **8** (`post-merge-validation.yml:253,269`; `python-ci.yml:389,405,927,943,1279,1481`) | `grep -rn -- "--save"` |
| `docker-compose.yml` site | **1** (`:68`, escaped spelling `--save \"\"`) | `grep` |
| Declared-site pin | **9** | 8 + 1 |
| Recorded exclusion | `graph-scripts/setup.py:647` (`--save 60 1000`) | `sed -n '640,655p'` |
| `carve_out:` size | **26** (`config/ci-surfaces.yml:866`, next key `durations:` at `:894`) | `sed` |
| `TEST_NO_REDIRECT_STEMS` registry / pin | `tests/_embedded.py:157` / `tests/test_markers.py:411` | `grep` |
| Classifier source-inspection pin | `tests/test_canary_classify.py:302` (`"os.environ" not in code`) | `grep` |
| `TOOL_CARVEOUTS` | `tools/ci_selection.py:230–264`; neither new path present | `sed` |
| `NON_PYTHON_PREFIXES` includes `"tools/"` | `tools/ci_selection.py:200` (the `"tools/"` literal at `:203`) | `sed` |
| `_run_session` (the tripwire's subprocess-session helper) | `tests/test_tripwire.py:125` | `rg` |
| `SELECTION_FN_VERSION` | `tools/ci_selection.py:47` (`"1.3.0"`) | `rg` |
| `_CHILD_LANE_VARS` → the ONE registry `lane_contract.CHILD_LANE_VARS` (M24) | `tools/lane_contract.py`; the tripwire's tuple at `tests/test_tripwire.py:42–51` | `grep` |
| redislite defaults | `save = ['900 1','300 100','60 200','15 1000']`, `auto-aof-rewrite-percentage '100'`, `auto-aof-rewrite-min-size '64mb'` | `.venv/.../redislite/configuration.py:40,22,23` |
| `--import-mode=importlib` | **nowhere** in CI/config (only a docstring, `tests/test_entity_delete_rebuild.py:26`) | `grep -rn` |
| Family baseline (per-run) | mandatory file **70.57 s**; `test_hosted_backup` **13.1 s** | SCOPE F29 |
| `STEP_WALL_GATE_SECONDS` | `3300` | `tools/testdb_canary_classify.py:72` |

<!-- plan-review: cycles=2, status=capped, version=2.4.0, verdict=2026-09-18 adopted paired-RED + R1/R2 + declared-local-N -->
