---
title: "The core surface derived from imports — and why a file-level split is NOT justified (#10132)"
type: engineering
domain: capability
doc_status: live
created: 2026-10-27
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# The `core` surface, derived from imports — and why a file-level split is NOT justified (#10132)

**Verdict: NO.** The data does not support splitting `surfaces.core` by import closure. The
derivation is coarse for a reason that is one import edge away from being fixed, and the
validation harness shows the derived mapping is **not safe** even with the one refinement that
is provably necessary. The real lever is import hygiene in three places, not surface bookkeeping.

This document changes nothing about selection: `config/ci-surfaces.yml` semantics and
`tools/ci_selection.py::SOURCE_PATTERNS` are untouched, and the unknown-path arm still fails
closed to `_full_selection()`. Selection is deliberate; this is measurement.

- Instrument: `tools/ci_import_closure.py` (`closure` / `validate` / `report`)
- Pins: `tests/test_ci_import_closure.py` (99 test functions / 101 collected, incl. the harness
  mutation check and the two verdict-rule pins — re-derive with
  `grep -c '^def test_' tests/test_ci_import_closure.py` and
  `pytest --collect-only -q`; a count written here without that command is a snapshot, not a fact)
  ⚠️ **Qualified, and the qualification is the point.** The test file is registered on `core`, which
  is **classification only**: a diff that changes *this instrument* and nothing else
  (`tools/ci_import_closure.py`-only) is a non-Python change — `tools/` is in
  `NON_PYTHON_PREFIXES`, the file is in no `TOOL_CARVEOUTS`/`CORE_ALSO` entry and matches no
  `SOURCE_PATTERNS` row — so it selects tier-1 smoke and **this pin does not run**. Adding the
  `CORE_ALSO` entry plus its mutation-checked arm is a separate, evidence-backed
  `tools/ci_selection.py` change (excluded here by #10132's scope clause), and
  `config/ci-surfaces.yml`'s own comment states the same boundary.
- Reproduce: `uv run python tools/ci_import_closure.py` · `… validate --window 40`
- **Measurement basis:** `427037b36` — the sha every figure below was re-derived at, with
  `uv run python tools/ci_import_closure.py`. ⛔ It is a SHA, and deliberately **not** a branch tip:
  that spelling is how these numbers went wrong once already. An earlier round published
  them against a moving ref, the final `merge(10143)` then brought in **four** `core` registrations,
  four test files and one `tortoise/` module, and §1 was left describing the parent tree while the
  text claimed the commit that carried it (#10143 review P2). `main` also keeps adding `core`
  registrations — **756** at `427037b36`, 755 on main alone at `7ff42c0d5` (the one-file gap is
  this lane's own registration), and 745 at `3934648ae` — so an older count is a snapshot, not a
  contradiction, and
  `tests/test_ci_import_closure.py::test_the_published_counts_match_the_tree_of_the_sha_they_name`
  re-derives all three from the tree of the sha named here. A re-run can also differ for a second
  reason: the file enumeration has two paths — `git ls-files` (used whenever git is available) and a
  pruning directory walk of the WHOLE repo (the fallback). They enumerate the same package set; a
  fallback that walked a fixed root list (`tortoise`, `tests`, `battery`, `tools`) used to differ
  by **150** tracked files and changed one core closure's answer — an import naming a package
  outside those roots was silently read as third-party, with no `UNKNOWN` recorded (#10143 review
  P2). The only remaining difference is a file git does not track, which cannot happen in CI.

## 1. What was derived

Static `ast` analysis of every tracked `*.py`: for each of the **756** `core` test files, the
transitive set of `tortoise/**` modules it imports. Modules under analysis are never imported
(that has side effects and measures the wrong thing); every edge comes from the source text, and
`tests/`-side helpers are traversed but not reported.

| Measure | Value |
|---|---|
| core test files | **756** (755 before this lane registered its own test) |
| `tortoise/**` modules in the tree | 259 |
| **distinct closure sets** (a split's real granularity) | **59** |
| closure size — median / mean / min / max | **187** / 158.7 / 0 / 208 |
| files sharing the single **most common** closure (187 modules; the largest is 208) | **432** |
| files with an EMPTY closure (no `tortoise/**` import at all) | **120** |
| files reaching `tortoise/__init__.py` | **636 (84.1 %)** |
| files whose closure is `UNKNOWN`-tainted (reported, never dropped) | 144 (19.0 %) |

### 1.1 Function-local imports are the whole ballgame (and the trap)

The same derivation restricted to **module-level** imports only — the naive reader — reports a
median core closure of **19 modules** and a `tortoise/__init__.py` closure of **6**. That looks like
a triumphant 90 % reduction and it is exactly the false green: `tortoise/sdk.py` has **89** in-repo
import targets, of which **28 carry a module-level edge** and **61 arrive only from a function
body** — and **13 of those 28 also arrive from a function body** (`tests/test_ci_import_closure.py`,
`test_function_local_imports_are_walked_and_tagged`), which is a refinement of the 28, not a third
bucket: 28 + 61 = 89 either way, so the two statements are two views of one measurement. And
`tortoise/config.py`'s cycle-breaking `from tortoise.sdk import _derive_graph_name` is itself
deferred. Measured both ways on the same tree:

| | module-level only | all nesting (this tool) |
|---|---|---|
| `closure(tortoise/__init__.py)` | **6** | **187** |
| core closure median / max | **19** / 86 | **187** / 208 |

A closure that reads only top-level imports would have selected **302** of 756 files for a change to
`tortoise/sdk.py`, against **636** with every nesting walked — and its median closure is 19 modules
where this tool's is 187. Three different populations are in play here, and the closure gap and the
direct-importer set are close in size (334 and 332) without being the same set — their overlap is 77
files:

| quantity | value | definition |
|---|---|---|
| closure gap | **334** | files whose closure CONTAINS `tortoise.sdk` with every nesting walked and does not under the module-level reader (`636 − 302`) |
| direct importers | **332** | of 756 core files, those with any `tortoise.sdk` edge of their own |
| direct, module-level | **236** | of those 332, the ones with a module-level `tortoise.sdk` edge — so **96** import it only inside a function body |

This is the reason the tool walks the whole tree and tags every edge `in_function`: a reader that
kept only module-level edges would lose `tortoise.sdk` from 334 closures, and only **77** of those
files import `sdk` inside a function themselves — the other **257** reach it transitively, through
another module's deferred edge. (A file with a module-level `sdk` edge cannot be in the gap at all:
its module-level closure would already contain `tortoise.sdk`. So the only direct importers inside
the gap are the function-only ones — bounded by the 96 in the table above and measured as 77.)

**59 distinct sets sounds like granularity. It is not.** 432 + 120 = **552 of 756 (73.0 %)** core
files sit in one of two degenerate buckets — "the entire 187-module block" or "nothing at all" —
and a change to any module in that block still selects 636 files, i.e. **84.1 % of the surface**.
The best reduction a closure-based split could deliver for those changes is 756 → 636 (15.9 %).

## 2. Why the block exists: ONE deferred import

`tortoise/__init__.py`'s own closure is **187 modules**. Removing a single edge drops it to **7**:

```text
tortoise/__init__.py        line 39   from tortoise.config import RELATIVE_PATH_ERROR   (module level)
tortoise/config.py          line 135  from tortoise.sdk import _derive_graph_name       (function-local)
tortoise/sdk.py             42 535 lines, 89 in-repo modules (28 module-level, 61 function-local only,
                            13 of the 28 reached BOTH ways)
                            → 187-module closure for every test that imports ANY tortoise.* submodule
```

Because importing a submodule imports its package first, that chain lands in 84.1 % of the core
surface. So the answer to the issue's own question is yes — **most core tests transitively depend
on `tortoise/__init__.py`** — but the mechanism is not "the shim re-exports everything". The shim
imports four modules (`config`, `embedded_lifecycle`, `fork_safety`, and one function-local), and
`config.py`'s *cycle-breaking deferred* import of `sdk` is what drags the hub in. Measured:

```text
closure(tortoise/__init__.py)                                  = 187 modules
closure(tortoise/__init__.py)  without the config → sdk edge    =   7 modules
```

Removing that edge is not this lane's call (it exists to break an import cycle and is documented
in the file), but it is the highest-leverage single change in the repository for CI selection:
with it gone, the closure of a test that imports `tortoise.ids` stops containing `tortoise.sdk`.

## 3. The validation harness — and it says UNSAFE

The question that matters: *given a historical diff, would the derived mapping have selected every
test that ACTUALLY failed on it?* Ground truth is each run's `junit.xml` artifact (a red job is NOT
a failing test — see §3.3). Two derived mappings are scored:

- **naive** — a core file stays selected only if a changed `tortoise/**` module is in its closure.
- **keep-non-python** — the same, plus core files whose closure is **empty**: 120 files import
  nothing from the package because they assert an invariant of a CONFIG/WORKFLOW/DOC file (or
  drive a script as a subprocess). No import edge can ever exist for them, so only a hand-written
  registration carries that dependency.

**Window: the 40 newest merged PRs, ranked by MERGE time** (`/pulls?state=closed`, `merged_at≠null`,
re-ranked by `merged_at`; `gh pr list --state merged` was measured to under-report — it returned 24
PRs while #10102/#10103/#10104/#10106 were absent — and `/pulls` has **no** merge-time sort: the
documented values are `created` (the default), `updated`, `popularity` and `long-running`, and
`sort=merged` is silently ignored (measured 2026-10-29: it returns the `created` order verbatim), so
the walk asks by `updated` and ranks client-side — ranking by *creation* omitted 11 PRs that merged
AFTER entries the window contained, e.g. #10108). The window is a MOVING set, so the exact one these
numbers come from is listed here:
10167, 10162, 10153, 10151, 10144, 10140, 10135, 10134, 10129, 10128, 10120, 10118, 10111, 10110,
10106, 10104, 10103, 10102, 10101, 10100, 10098, 10097, 10096, 10094, 10092, 10091, 10089, 10087,
10086, 10081, 10079, 10078, 10075, 10071, 10070, 10069, 10068, 10066, 10055, 10053 (`--prs` that list
reproduces the tables below exactly; a re-run `--window 40` after further merges reports different
totals — that is the window moving, not a contradiction). Coverage: **9 PRs carried ≥ 1 test-level
failure**, **25 failing test files (18 distinct)**; **8 artifact problems, across 5 PRs, made ground
truth partial** — **5 legs whose OWN `pytest-log-*` artifact was absent while a sibling leg's was
present** (2 PRs: #10111 with 1, #10096 with 4), and **3 artifacts that carried no `junit.xml`**
(3 PRs: #10144, #10134, #10098). So this window is **PARTIAL** and `validate` exits **2** to say so
rather than reporting a clean window it could not read. A partial artifact set can only UNDER-count
misses, so the two miss counts below are lower bounds (the 40-PR count includes the `init`-level
checks only; a green PR contributes nothing here). 7 further PRs went red with **no test-level
failure at all** — a post-test hygiene step, a run whose junit described zero testcases, a run with
no readable artifact — and every one of those is recorded rather than scored as clean (§3.4).

| mapping | misses | PRs | median selected tests (973 on that window, a tree figure — re-derive with the tool) |
|---|---|---|---|
| naive | **13** | 4 (#10151, #10144, #10134, #10104) | 488 |
| keep-non-python | **13** | 4 (#10151, #10144, #10134, #10104) | 606 |

**A single miss means the derivation is not safe. There are 13**, under BOTH mappings — the
empty-closure refinement changes no file in this window (see the class table: the class it repairs has
no scored instance here, because #10070's failing run was unreadable — §3.4 — so its one historical
win cannot be re-scored). They fall in three classes, and only one of them is an analysis limitation
that better parsing would fix:

| class | count | what it is |
|---|---|---|
| `non-python-dependency` (naive only) | **0 here** | the test imports nothing from `tortoise/**`; its subject is a manifest, workflow, doc or a script it runs. **Fixed by the keep-non-python refinement** — measured 1 (`#10070 test_ci_check_repair.py`) on the window as it stood at `3934648ae`, and unscorable now because that PR's red run uploaded no readable junit (§3.4). The refinement stays: it is the direction that cannot over-select, and the class is real whenever the truth is readable. |
| `tainted-closure` | 3 | the file's own imports are `UNKNOWN` (`importlib` with a computed name / `setattr`-style access), so the derivation could not see the edge (#10151 `test_dry_run_preview.py`; #10134 `test_9350_capture_provenance_journalled.py` and `test_reaper.py`). |
| `no-closure-intersection` | 10 | the closure is complete and nothing in it intersects the diff. Two sub-causes, both outside any import graph: a diff that changes **workflows/docs/config only** (#10151 `test_derived_names.py`, #10144 `test_derived_names.py`/`test_embedded_concurrency.py`/`test_9209_handle_non_cypher_wipe.py`, #10104's three), where the failing test guards that non-Python subject, and a diff that changes **only test files** — today's surface rule is what keeps the rest of the surface running, and no import edge couples one test to another (most of #10134's five). |

⚠️ **One of the 14 misses the pre-`a746139ba` reader scored is not a miss at all.** #10120's
`test_9078_record_written_by_production_path.py` is one of `ALWAYS_ON_LEG_FILES` (run on EVERY PR by
`test-record-postgres`), and it was counted as `tainted-closure` because `derived` subtracted every
`core` member — including the unconditional legs — before adding back only the closure-derived ones.
On a workflow-only diff no import edge can exist, so an unconditional leg could never be derived, and
its failure was scored against the derivation. The union into `derived` is now real (F2, `a746139ba`),
which is why the totals read **13** here and **14** at that head: same window, same ground truth, one
unmappable row removed.

### 3.1 What the harness would need to be safe

The three classes are additive requirements, not bugs to patch:

1. keep the hand-written registrations for non-Python subjects (`SOURCE_PATTERNS` / `CORE_ALSO` —
   which already exist and are the reason `core` is blunt);
2. keep a surface-wide minimum when a **test file** changes (today's rule);
3. resolve the 144 tainted files, or exclude them from any narrowing.

Requirement (1) alone means the closure can never *replace* the hand-written map — only narrow the
part of `core` that is genuinely import-driven. That is a smaller change than the issue anticipated,
and its ceiling is the 15 % in §1.

### 3.2 Every shape that silently under-selects, and the decision taken

| shape | decision | why |
|---|---|---|
| function-local imports | **dependency** (`in_function: true`) | the suite uses them heavily; `tortoise/sdk.py` has 61 of its 89 in-repo import targets only inside function bodies. A top-level-only reader reports a 6-module closure for the package shim instead of 187 and a median core closure of 19 instead of 187 (§1.1) — the sharpest false-green in this whole measurement. |
| `TYPE_CHECKING` blocks | **dependency** (tagged `type_checking`) | the opposite of `tach`, which skips them because it asks about *runtime coupling*. Our question is *would a change here be observable*, where over-approximation is the safe direction and one extra test beats a false green. `--exclude-type-checking` reproduces tach's polarity. |
| `if not TYPE_CHECKING:` | **runtime** dependency (`type_checking: false`) | the NEGATED guard is the standard idiom for a runtime import a type checker cannot see. `ast.walk` found the `TYPE_CHECKING` name anywhere in the test, so the negated body was tagged type-only and `--exclude-type-checking` DELETED a real runtime edge — the same axis inversion the `_dedupe_deps` key removes, one level up. |
| decorators, default values, annotations, return annotation | **import-time** (`in_function: false`) | they are evaluated where the `def` EXECUTES, not when the body is called. Tagging them function-local made `--import-time-only` drop real import-time edges — an under-selection inside the reader that exists to *demonstrate* under-selection, which skews the 21-module figure it publishes. |
| `from __future__` | **not a dependency**, counted and named | no `tortoise/**` target can match it; excluding it silently would make the exclusion invisible. |
| re-export shims | `tortoise/__init__.py` modelled via an explicit **`package-init` edge** on every `import tortoise.x`; `tortoise/__all__.py` reported when imported (1 test) | the package-init edge is what answers "do most tests depend on the shim" — and it is where 84.1 % of the surface comes from. `__all__.py` is a *data* module of approved names, not a re-export surface. |
| dynamic imports | constant argument **resolved**; computed argument → `UNKNOWN` with reason | `importlib.import_module("tortoise.x")` is still static information; only a computed name is a blind spot. |
| `getattr(module, ...)` | in-repo module + literal attr → resolve (or no edge); computed attr → `UNKNOWN`; third-party module → **no taint** | the candidate is built from the binding's **dotted target**, never its local name: `from tortoise import projection` + `getattr(projection, "in_memory")` resolves `tortoise.projection.in_memory`, where a local-name candidate (`projection.in_memory`) is not a module at all and would drop a real submodule with neither an edge nor an `UNKNOWN`. Taint must mean "may be MISSING a `tortoise` module" or it says nothing: over-tainting marks the whole connected component and the signal collapses (measured: 89 % tainted before this rule, 19 % after). |
| package/module name collision | **package wins** (CPython's `FileFinder` order) | `tortoise/projection/__init__.py` and `tortoise/projection.py` share a name; resolving to the module **used to** under-select by 7 modules. On this tree it changes no closure's module count (measured: 636 core closures swap `tortoise/projection/__init__.py` for `tortoise/projection.py` and back, 0 net), so the pin for the precedence rule is the SYNTHETIC case in the test file, and the pair is still reported in `shadowed_modules` rather than discarded. |
| unparseable / unresolvable | `UNKNOWN` + reason (`syntax`, `unresolved-name`, `dynamic-import-call`, `dynamic-module-attr`, `module-loader`) | reported, never dropped. |

### 3.3 Ground truth is the junit, not the job

A red job is not a failing test. Measured on #10070: `test (f)` concluded `failure` at a post-test
hygiene step ("Assert no redislite orphans") while `junit.xml` recorded **2 907 tests and zero
failures**. Scoring the job as a failure would inject phantom misses and condemn a derivation that
is fine, so the harness reports `job_failed_without_test_failure` separately and only a
`<failure>`/`<error>` testcase counts. (Related: `gh api …/check-runs` keeps every attempt, so a
retried-and-green job exists beside its red attempt — the harness reads every artifact of the
failed runs and unions the failures. A red run it cannot read at all is never scored as clean: it
is counted and the window exits **2**, see §5.)

The failing FILE comes from the xunit1 `file` attribute, with a `classname` fallback for legs that
omit the repo's `-o junit_family=xunit1` override (`xunit2` is pytest's ini default, and it drops
`file`/`line` entirely). The fallback resolves the module by trying every prefix of the dotted
`classname` against the checkout, longest first — `tests.eval.harness.test_x` →
`tests/eval/harness/test_x.py`, never the sibling module `tests/eval/harness.py`. Taking
`classname.rpartition(".")` instead produced `tests.py` for every module-level test: a path that
intersects nothing, so the failure is scored `no-closure-intersection` — i.e. as evidence the
derivation is SAFE. A failure whose file neither key can attribute blocks the cache and makes the
window PARTIAL.

The ground-truth caches are keyed by **repo and head sha** — the ground truth by
`truth-v<N>-<repo>-<sha>.json`, the diff list by `files-<repo>-<sha>.json`, the merged PR's metadata
by `meta-<repo>-<pr>.json` (merged only: see §5): `--repo` is a first-class flag, PR numbers collide
across repos, and a fork shares the commit sha — so a sha-only key let one repo's truth be paired
with another's diff, a state that can only HIDE a miss and records no error.

### 3.4 Every way "I did not read this" could have read as "nothing failed"

The harness is only worth its verdict if its own blind spots are enumerated, so all of them are
closed the same way: the state is recorded, the truth is **not cached**, and the window exits **2**.

| state | why it is reachable | closed by |
|---|---|---|
| artifact present, no `junit.xml` (pytest died before writing it) | upload step is `if: always()` + `if-no-files-found: ignore` | `artifact_errors` entry per artifact |
| `junit.xml` unparseable | corrupt/truncated zip payload | `parse_junit_failures`' `error` is now read, not ignored |
| failing test whose FILE neither key can attribute | `xunit2` legs drop `file`/`line` | `unresolved` → `artifact_errors` |
| red run whose artifact listing is **empty** | artifacts expire in 90 days; deleting a run deletes them | `artifact_errors` entry per run |
| **the red leg's own artifact is absent but a SIBLING leg's is present** | `rid` is the workflow **run**, and `test`/`test-slow`/`test-carve-out`/the always-on legs are matrix jobs inside one run; a leg that early-exits uploads nothing | **measured 5 times across 2 PRs in the §3 window** — the wide `pytest-log-*` fallback (a rename mitigation, kept) now also records the substitution, so a sibling's clean junit can no longer stand in for the leg that failed |
| **the head sha yielded no check run at all** | completed runs are deletable; a `GITHUB_TOKEN`-created run can complete with zero check runs | `runs_seen` / `test_legs_seen` are recorded, a zero is never cached, and the window is PARTIAL |
| **runs exist but NONE of them is a `test*` leg** (`test_legs_seen == 0`) | the leg names change in `python-ci.yml`, or a PR's only check runs are lint/typecheck | counted and never cached; `validate` treats a zero as UNASSESSED and exits **2**. This state was recorded for exactly this reason and gated nowhere, so `jobs: []` / `artifacts: 0` / `artifact_errors: []` read as a clean PR |
| **a red `test*` leg whose `details_url` carries no run id** | a check run not created by a workflow run, or a url shape change | recorded in `artifact_errors` per leg. The leg never reached the artifact loop, and beside a readable red sibling `artifacts > 0` also disabled the unread-PR guard |
| **a junit that parses but contains ZERO `<testcase>` elements** | the suite did not run (collection aborted, a marker selected nothing, exit 5) | `parse_junit_failures` reports `executed` and the caller reads it **per ARTIFACT**, not per run: a red leg that described 0 tests is an unread leg, not "no test-level failure". Per-run it would have been masked by a sibling leg in the same run that described 500 — matrix legs share a run |
| **a red `test*` leg whose run uploads no `pytest-log-*` at all** (e.g. `ci.yml`'s `test-isolation`) | that leg is outside the junit-carrying set, so its failures cannot be attributed to files | recorded in `artifact_errors` and the window is PARTIAL. This is deliberate and it is the honest state: the harness cannot read that leg, and an unread leg can only UNDER-count misses. Each red leg is checked against **its own run's** listing, so a leg in one workflow is never re-checked against another's artifacts |
| **the manifest's `core` surface resolves to ZERO test files** | a renamed/removed `core` key, or a failed `_surface_members` fallback | `core_empty` is flagged and every verb that publishes a number REFUSES (exit **2**). An empty surface makes `derived = current`, so nothing can ever miss and the window reports "TOTAL MISSES 0" from an analysis that examined nothing |
| **a failure lands outside the modelled selection** | `select()` models the surface legs only, while ground truth covers every leg — so `test-record-postgres`'s four files (run on EVERY PR, `python-ci.yml:3733`, literals `:3819-3822`) and `test-track-b`'s one (`:2568`, `FILES=` `:2627`) were outside `current_rel` | those legs are unioned into today's selection AND into the derived one (`ALWAYS_ON_LEG_FILES` — a `core` member is otherwise subtracted out of `derived`), and any residual entry is reported as a HOLE IN THE MODEL and makes the window PARTIAL — never as an absolution |
| **the PR's diff list was cached against the PR, while the ground truth is keyed by the head SHA** | `GET /pulls/{pr}/files` has **no ref selector** (params: `pull_number`, `per_page`, `page`), so the list belongs to the PR's CURRENT head. `--prs` accepts any PR id, and a lane checking its OWN open PR re-runs at a new head — head A's list was read back and paired with head B's truth, so `derived` was computed over a diff the truth never saw: it can only UNDER-count misses and records nothing | the diff cache is keyed by the head sha it describes (`files-<repo>-<sha>.json`, the truth cache's own identity), and a call with no sha caches nothing rather than falling back to a PR key |

`test-d14-hosted-api` (`pytest tests/ -m embedded_only`) is deliberately **not** modelled: its set is
a marker over the whole tree, and a wider guess is the DANGEROUS direction, because everything in
`current` is also in `derived` — over-covering a leg would hide misses. A failure it contributes
lands in the residual arm above, which is loud.

## 4. Prior art, and why the choices above differ from it

Probed 2026-10-27 from primary sources. Rung used: **3** (`web_fetch`); rung 1
(`perplexity_search`) was unavailable — the MCP server is not configured on this machine — and
rung 2 (`web_search`, `model="sonar"`) failed on a missing `PERPLEXITY_API_KEY`, as did `exa`
(401). Queries attempted 4 / returned 4 (tach FAQ, tach README, testmon, grimp; bazel.build BE;
pants docs 404 ×3 and are therefore **unprobed**).

- **`tach`** (AST-based, Rust): *"Tach will check all imports in your source files, including those
  called conditionally. The only exceptions are imports made within `TYPE_CHECKING` conditional
  blocks"* — skipped **by default**, configurable via `ignore_type_checking_imports: false`. And:
  *"dynamic imports … and dynamic names (e.g. `setattr`, `locals`, `globals`) are not supported"* —
  i.e. the same blind spot, acknowledged rather than solved. We invert the `TYPE_CHECKING` default
  because our question is selection safety, not runtime coupling.
- **`grimp` / `import-linter`**: a queryable static import graph, external packages excluded by
  default (`include_external_packages=False`) — same shape as our "only reported root" rule.
- **`pytest-testmon`**: the opposite strategy — coverage-based, no static inference, *"limits and
  reliability … the same as limits of coverage.py"*. It is the honest alternative to static
  closure, and it is why our harness scores against **observed** failures instead of trusting the
  derivation.
- **Bazel `py_binary`/`py_library`**: `deps` is an explicit declared attribute — no import
  inference at all, so a missing edge is a build error rather than a silently narrower test set.
  That is the fail-closed-by-construction alternative this repository already approximates with
  `core` + `CORE_ALSO`.
- **Pants** dependency inference: not probed (three documentation URLs 404'd); no claim is made
  about it. The issue named it; the evidence here does not cover it, and this section says so
  instead of citing it from memory.

## 5. Reproduction

```bash
uv run python tools/ci_import_closure.py                       # summary table + verdict
uv run python tools/ci_import_closure.py --module tortoise/sdk.py
uv run python tools/ci_import_closure.py closure --json /tmp/closure.json
uv run python tools/ci_import_closure.py validate --window 40 --json /tmp/validate.json
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_ci_import_closure.py -q
```

`validate` needs `gh` (auth) the first time; it caches every junit artifact, its per-PR ground
truth, the PR's diff list and a **merged** PR's metadata under `.ci-selection/closure-cache/`
(gitignored), so a re-run of an explicit `--prs` window is offline. Two reads are deliberately left
uncached because they describe something LIVE: the window listing itself (`--window` has to see
merges that happened since, so it is re-paged), and an **open** PR's head — which moves on every
push, so a lane validating its own PR always re-reads it (§3.4). The ground truth is keyed by the
head **sha** and the diff list by the same sha — never by the PR number, which names a stream of
different trees (§3.4). It
exits **2** when the window is partial — an artifact it could not read, an unparseable or missing
`junit.xml`, a red leg whose artifact listing was **empty** (artifacts expire after 90 days by
default, and deleting a run deletes its artifacts, so "nothing uploaded" is an ordinary state, not
a clean one), a red leg whose **own** artifact was absent while a sibling leg's stood in for it, a
head sha that yielded **no check run**, a failing test whose **file** neither the xunit1 `file`
attribute nor the `classname` fallback can attribute to a file in the checkout, a failure that
falls **outside the modelled selection** (§3.4), or a window with no PRs at all — **1** when it
found a miss, and **0** only for a complete, miss-free window. A `core` member that resolves to no
test file is named on stdout and carried in the JSON as `unresolved_core`, because every percentage
here is a ratio over that count.

## 6. Verdict

**Is a file-level split of `surfaces.core` justified by the data? No.**

1. **The granularity is not there.** 59 distinct closure sets, but 73.0 % of core files are in the
   two degenerate buckets (one 187-module set, or empty), and the median core closure is **187 of
   259 modules (72 %)**. A change to any module in that block still selects 636 files — 84.1 % of
   the surface — so the ceiling of a split is a 15.9 % reduction, for a change that would narrow a
   guard.
2. **The derivation is not safe.** 13 historical misses (13 under BOTH mappings) over 40 merged PRs
   — a lower bound, since 8 artifact problems in that window left its ground truth partial — in two
   scored classes, and only one class is an analysis limitation. A mapping that would not have run
   13 tests that actually failed is not a mapping to build selection on.
3. **The lever is import hygiene, not surface bookkeeping.** In order of measured leverage:
   - the **one deferred `config → sdk` import** (§2): worth 180 of the 187 modules in the shim's
     closure, i.e. it is the entire 84.1 % block;
   - **`tortoise/sdk.py`** (42 535 lines, 89 in-repo import targets) and **`tortoise/hosted_api.py`**
     (88, of which 51 only in function bodies): splitting *these* is what gives a closure something
     to differentiate on — 332 of 756 core files import `tortoise.sdk` directly;
   - **test-side hygiene** where a test imports the whole package for one constant.

   Doing (a) or (b) makes this measurement worth re-running; nothing in `ci-surfaces.yml` needs to
   move first. The measurement is cheap to repeat (`tools/ci_import_closure.py`, ~30 s) and the
   harness caches every artifact, ground truth, diff list and merged PR's metadata, so a `--prs`
   re-run is offline.

**What would flip this verdict** (stated so it can be falsified rather than believed): median
closure ≤ ~20 % of `tortoise/**`, no single module in more than half the closures, **and** zero
misses from `validate` over the same window — under BOTH mappings, since on this window the
keep-non-python refinement moves no file and therefore proves nothing on its own.
