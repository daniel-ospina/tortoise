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
- Pins: `tests/test_ci_import_closure.py` (41 tests, incl. the harness mutation check)
  ⚠️ **Qualified, and the qualification is the point.** The test file is registered on `core`, which
  is **classification only**: a diff that changes *this instrument* and nothing else
  (`tools/ci_import_closure.py`-only) is a non-Python change — `tools/` is in
  `NON_PYTHON_PREFIXES`, the file is in no `TOOL_CARVEOUTS`/`CORE_ALSO` entry and matches no
  `SOURCE_PATTERNS` row — so it selects tier-1 smoke and **this pin does not run**. Adding the
  `CORE_ALSO` entry plus its mutation-checked arm is a separate, evidence-backed
  `tools/ci_selection.py` change (excluded here by #10132's scope clause), and
  `config/ci-surfaces.yml`'s own comment states the same boundary.
- Reproduce: `uv run python tools/ci_import_closure.py` · `… validate --window 40`
- **Measurement basis:** the head of PR #10143 — the commit that adds this line. Two things make a
  re-run differ from these tables: `main` keeps adding `core` registrations (**742** here), and the
  file enumeration has two paths — `git ls-files` (used whenever git is available) and a
  pruning directory walk of the WHOLE repo (the fallback). They enumerate the same package set; a
  fallback that walked a fixed root list (`tortoise`, `tests`, `battery`, `tools`) used to differ
  by **150** tracked files and changed one core closure's answer — an import naming a package
  outside those roots was silently read as third-party, with no `UNKNOWN` recorded (#10143 review
  P2). The only remaining difference is a file git does not track, which cannot happen in CI.

## 1. What was derived

Static `ast` analysis of every tracked `*.py`: for each of the **742** `core` test files, the
transitive set of `tortoise/**` modules it imports. Modules under analysis are never imported
(that has side effects and measures the wrong thing); every edge comes from the source text, and
`tests/`-side helpers are traversed but not reported.

| Measure | Value |
|---|---|
| core test files | **742** (741 before this lane registered its own test) |
| `tortoise/**` modules in the tree | 258 |
| **distinct closure sets** (a split's real granularity) | **59** |
| closure size — median / mean / min / max | **186** / 158.9 / 0 / 207 |
| files sharing the single largest closure (186 modules) | **425** |
| files with an EMPTY closure (no `tortoise/**` import at all) | **114** |
| files reaching `tortoise/__init__.py` | **628 (84.6 %)** |
| files whose closure is `UNKNOWN`-tainted (reported, never dropped) | 141 (19.0 %) |

### 1.1 Function-local imports are the whole ballgame (and the trap)

The same derivation restricted to **module-level** imports only — the naive reader — reports a
median core closure of **21 modules** and a `tortoise/__init__.py` closure of **6**. That looks like
a triumphant 88 % reduction and it is exactly the false green: `tortoise/sdk.py` has **89** in-repo
import targets, of which **28 carry a module-level edge** and **61 arrive only from a function
body**, and `tortoise/config.py`'s cycle-breaking `from tortoise.sdk import _derive_graph_name` is
itself deferred. Measured both ways on the same tree:

| | module-level only | all nesting (this tool) |
|---|---|---|
| `closure(tortoise/__init__.py)` | **6** | **186** |
| core closure median / max | **21** / 86 | **186** / 207 |

A closure that reads only top-level imports would have selected **299** of 742 files for a change to
`tortoise/sdk.py`, against **628** with every nesting walked — and its median closure is 21 modules
where this tool's is 186. The gap is the **329** core files that reach `tortoise.sdk` only from a
function body; 329 files carry a direct `tortoise.sdk` import at all, and 233 of those do so at
module level. This is the reason the tool walks the whole tree and tags every edge `in_function`.

**59 distinct sets sounds like granularity. It is not.** 425 + 114 = **539 of 742 (72.6 %)** core
files sit in one of two degenerate buckets — "the entire 186-module block" or "nothing at all" —
and a change to any module in that block still selects 628 files, i.e. **84.6 % of the surface**.
The best reduction a closure-based split could deliver for those changes is 742 → 628 (15 %).

## 2. Why the block exists: ONE deferred import

`tortoise/__init__.py`'s own closure is **186 modules**. Removing a single edge drops it to **7**:

```text
tortoise/__init__.py        line 39   from tortoise.config import RELATIVE_PATH_ERROR   (module level)
tortoise/config.py          line 135  from tortoise.sdk import _derive_graph_name       (function-local)
tortoise/sdk.py             42 230 lines, 89 in-repo modules (28 module-level, 61 function-local only)
                            → 186-module closure for every test that imports ANY tortoise.* submodule
```

Because importing a submodule imports its package first, that chain lands in 84.6 % of the core
surface. So the answer to the issue's own question is yes — **most core tests transitively depend
on `tortoise/__init__.py`** — but the mechanism is not "the shim re-exports everything". The shim
imports four modules (`config`, `embedded_lifecycle`, `fork_safety`, and one function-local), and
`config.py`'s *cycle-breaking deferred* import of `sdk` is what drags the hub in. Measured:

```text
closure(tortoise/__init__.py)                                  = 186 modules
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
- **keep-non-python** — the same, plus core files whose closure is **empty**: 114 files import
  nothing from the package because they assert an invariant of a CONFIG/WORKFLOW/DOC file (or
  drive a script as a subprocess). No import edge can ever exist for them, so only a hand-written
  registration carries that dependency.

**Window: the 40 newest merged PRs** (`/pulls?state=closed&merged_at≠null`; `gh pr list --state
merged` was measured to under-report — it returned 24 PRs while #10102/#10103/#10104/#10106 were
absent). The window is a MOVING set, so the exact one these numbers come from is listed here:
10167, 10162, 10153, 10151, 10144, 10140, 10135, 10134, 10129, 10128, 10120, 10118, 10111, 10110,
10106, 10104, 10103, 10102, 10101, 10100, 10098, 10097, 10096, 10094, 10092, 10091, 10089, 10087,
10086, 10081, 10079, 10078, 10075, 10071, 10070, 10069, 10068, 10066, 10055, 10053. A re-run
`--window 40` after further merges reports different totals — that is the window moving, not a
contradiction. Coverage: **11 PRs carried ≥ 1 test-level failure**, **22 failing test files (17
distinct)**; **5 artifacts, across 4 PRs, were unreadable**, so this window is
**PARTIAL** and `validate` exits **2** to say so rather than reporting a clean window it could not
read. An unreadable artifact can only UNDER-count misses, so the two miss counts below are lower
bounds (the 40-PR count includes the `init`-level checks only; a green PR contributes nothing here).

| mapping | misses | PRs | median selected tests (today: 974) |
|---|---|---|---|
| naive | **10** | 4 (#10151, #10144, #10134, #10070) | 482 |
| keep-non-python | **9** | 3 (#10151, #10144, #10134) | 596 |

**A single miss means the derivation is not safe. There are 9** (10 for the naive reader). They fall
in three classes, and only one of them is an analysis limitation that better parsing would fix:

| class | count | what it is |
|---|---|---|
| `non-python-dependency` (naive only) | 1 | the test imports nothing from `tortoise/**`; its subject is a manifest, workflow, doc or a script it runs (#10070 `test_ci_check_repair.py`). **Fixed by the keep-non-python refinement** — this is the one refinement the data *proves* is required. |
| `tainted-closure` | 3 | the file's own imports are `UNKNOWN` (`importlib` with a computed name / `setattr`-style access), so the derivation could not see the edge (#10151 `test_dry_run_preview.py`; #10134 `test_9350_capture_provenance_journalled.py` and `test_reaper.py`). |
| `no-closure-intersection` | 6 | the closure is complete and nothing in it intersects the diff. Two sub-causes, both outside any import graph: a diff that changes **workflows/docs/config only** (#10151 `test_derived_names.py`, #10144 `test_derived_names.py`/`test_embedded_concurrency.py`), where the failing test guards that non-Python subject, and a diff that changes **only test files** — today's surface rule is what keeps the rest of the surface running, and no import edge couples one test to another (most of #10134's five). |

### 3.1 What the harness would need to be safe

The three classes are additive requirements, not bugs to patch:

1. keep the hand-written registrations for non-Python subjects (`SOURCE_PATTERNS` / `CORE_ALSO` —
   which already exist and are the reason `core` is blunt);
2. keep a surface-wide minimum when a **test file** changes (today's rule);
3. resolve the 141 tainted files, or exclude them from any narrowing.

Requirement (1) alone means the closure can never *replace* the hand-written map — only narrow the
part of `core` that is genuinely import-driven. That is a smaller change than the issue anticipated,
and its ceiling is the 15 % in §1.

### 3.2 Every shape that silently under-selects, and the decision taken

| shape | decision | why |
|---|---|---|
| function-local imports | **dependency** (`in_function: true`) | the suite uses them heavily; `tortoise/sdk.py` has 61 of its 89 in-repo import targets only inside function bodies. A top-level-only reader reports a 6-module closure for the package shim instead of 186 and a median core closure of 21 instead of 186 (§1.1) — the sharpest false-green in this whole measurement. |
| `TYPE_CHECKING` blocks | **dependency** (tagged `type_checking`) | the opposite of `tach`, which skips them because it asks about *runtime coupling*. Our question is *would a change here be observable*, where over-approximation is the safe direction and one extra test beats a false green. `--exclude-type-checking` reproduces tach's polarity. |
| `from __future__` | **not a dependency**, counted and named | no `tortoise/**` target can match it; excluding it silently would make the exclusion invisible. |
| re-export shims | `tortoise/__init__.py` modelled via an explicit **`package-init` edge** on every `import tortoise.x`; `tortoise/__all__.py` reported when imported (1 test) | the package-init edge is what answers "do most tests depend on the shim" — and it is where 84.6 % of the surface comes from. `__all__.py` is a *data* module of approved names, not a re-export surface. |
| dynamic imports | constant argument **resolved**; computed argument → `UNKNOWN` with reason | `importlib.import_module("tortoise.x")` is still static information; only a computed name is a blind spot. |
| `getattr(module, ...)` | in-repo module + literal attr → resolve (or no edge); computed attr → `UNKNOWN`; third-party module → **no taint** | taint must mean "may be MISSING a `tortoise` module" or it says nothing: over-tainting marks the whole connected component and the signal collapses (measured: 89 % tainted before this rule, 19 % after). |
| package/module name collision | **package wins** (CPython's `FileFinder` order) | `tortoise/projection/__init__.py` and `tortoise/projection.py` share a name; resolving to the module under-selected by 7 modules and is reported in `shadowed_modules`. |
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

`validate` needs `gh` (auth) the first time; it caches every junit artifact and its per-PR ground
truth under `.ci-selection/closure-cache/` (gitignored), so re-runs are offline and instant. It
exits **2** when the window is partial — an artifact it could not read, an unparseable or missing
`junit.xml`, a red leg whose artifact listing was **empty** (artifacts expire after 90 days by
default, and deleting a run deletes its artifacts, so "nothing uploaded" is an ordinary state, not
a clean one), or a window with no PRs at all — **1** when it found a miss, and **0** only for a
complete, miss-free window. A `core` member that resolves to no test file is named on stdout and
carried in the JSON as `unresolved_core`, because every percentage here is a ratio over that count.

## 6. Verdict

**Is a file-level split of `surfaces.core` justified by the data? No.**

1. **The granularity is not there.** 59 distinct closure sets, but 72.6 % of core files are in the
   two degenerate buckets (one 186-module set, or empty), and the median core closure is **186 of
   258 modules (72 %)**. A change to any module in that block still selects 628 files — 84.6 % of
   the surface — so the ceiling of a split is a 15 % reduction, for a change that would narrow a
   guard.
2. **The derivation is not safe.** 9 historical misses (10 naive) over 40 merged PRs — a lower
   bound, since 5 artifacts were unreadable — in three classes, and only one class is an analysis
   limitation. A mapping that would not have run 9 tests that actually failed is not a mapping to
   build selection on.
3. **The lever is import hygiene, not surface bookkeeping.** In order of measured leverage:
   - the **one deferred `config → sdk` import** (§2): worth 179 of the 186 modules in the shim's
     closure, i.e. it is the entire 84.6 % block;
   - **`tortoise/sdk.py`** (42 230 lines, 89 in-repo import targets) and **`tortoise/hosted_api.py`**
     (88, of which 51 only in function bodies): splitting *these* is what gives a closure something
     to differentiate on — 329 of 742 core files import `tortoise.sdk` directly;
   - **test-side hygiene** where a test imports the whole package for one constant.

   Doing (a) or (b) makes this measurement worth re-running; nothing in `ci-surfaces.yml` needs to
   move first. The measurement is cheap to repeat (`tools/ci_import_closure.py`, ~30 s) and the
   harness is cached and offline on a re-run.

**What would flip this verdict** (stated so it can be falsified rather than believed): median
closure ≤ ~20 % of `tortoise/**`, no single module in more than half the closures, **and** zero
misses from `validate` with the keep-non-python refinement over the same window.
