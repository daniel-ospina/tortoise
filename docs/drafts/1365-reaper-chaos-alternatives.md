---
title: "#1365 — Reaper chaos tests: alternative solution approaches (draft)"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-17
subjects.team: epistemic-team
aboutSubjects: tortoise-embedded
aboutObjects: reaper, chaos-tests, test-isolation, redislite
---
# #1365 — Reaper chaos tests: alternative solution approaches

> Scope: alternatives only. No winner selected. Confirmed problem + relevant
> code per issue #1365; evidence verified against the worktree.

## Ground truth verified in code

| Claim | Where |
|---|---|
| Test 1 asserts `candidates[0]` (ambient), not own spawned pid | `test_embedded_concurrency.py:343-368` |
| Test 2 asserts global "no candidates remaining after second run" | `test_embedded_concurrency.py:370-410` |
| Test 3 (`kills_only_idle_of_20`) ends with global `pkill -f redislite/bin/redis-server` | `test_embedded_concurrency.py:290` (cleanup, line ~341) |
| `reap()` fail-closed skips: dead pid → skip; probe None → skip; clients>0 → skip | `embedded_reaper.py:935-965` |
| Dead-pid dirs classify as `candidate` but are unreapable (phantom) | `_classify` → `_cooldown_check` (`embedded_reaper.py:775-842`); `reap()` liveness-first skip at 935 |
| Probe fail-closed: raw RESP 0.5s timeout, redis-cli fallback may not exist on ubuntu-24.04 | `_raw_resp_client_list` / `_client_list` (`embedded_reaper.py:392-490`) |
| Tier-2 half-a FILES built from `test_files` with NO SLOW_FILES filter | `python-ci.yml:316-322` |
| `test_embedded_concurrency.py` is in SLOW_FILES → runs in test-slow (75m cap) | `python-ci.yml:127` |
| Delta-based fixture pattern already exists and passes | `test_reaper.py:31-93` (`_clean_redislite_residue`, `_sweep_stale_residue`) |

---

## Approach A — Delta-isolation test redesign (port the `test_reaper.py` pattern)

**Description.** Rewrite the three chaos tests (and the lock test) to track
only the orphans THEY spawn: snapshot ambient redislite pids+dirs before,
capture spawned orphan pids (new socket dir → `redis.pid`), assert on
spawned-pid membership in `candidates` and on `reap()`'s `acted` list —
never `candidates[0]`, never a global "no candidates remaining". Remove the
global `pkill`; teardown kills only the delta via `killpg` (children spawned
with `start_new_session=True`) with SIGTERM→SIGKILL escalation. Replace the
blind `time.sleep(1.5)` mid-sweep SIGKILL with a sweep-start sentinel
(reaper CLI prints a marker; test reads stdout) or make the second-run
assertion robust to wherever the kill landed (delta-scoped: own pids dead +
own socket dirs gone, regardless of how far the first sweep got). Each test
sets its own short flat `TMPDIR` under `/tmp` (AF_UNIX ~107-byte limit; never
nested `tmp_path`). Optionally relocate the tests into `test_reaper.py`
where the fixtures already live, instead of duplicating them.

**Files touched.** `tests/test_embedded_concurrency.py` (rewrite 3-4 tests;
drop global pkill); optionally `tests/conftest.py` (shared delta fixture) or
move tests into `tests/test_reaper.py`.

**Architecture.** Test-side only. Per-test lifecycle: (1) snapshot ambient
pids+dirs; (2) spawn N orphans via process-group children, resolve each
orphan's real server pid from its `redis.pid`; (3) `discover()` → assert own
pids ∈ candidates (still proves discovery finds freshly-SIGKILLed-parent
servers); (4) `reap(candidates)` → assert own pids ∈ `acted` and `os.kill(pid,0)`
raises; assert own socket dir removed; (5) teardown: `killpg` only the delta.
Mid-sweep test: reaper subprocess in own group; poll stdout for sweep-start
marker; SIGKILL the group; second run; delta-scoped final assert.

**Risks.**
- `test` (tier-2 fast leg) and `test-slow` run the SAME file concurrently in
  CI today (line 127 + tier-2 leak). A concurrent run's orphan can land
  between snapshot and spawn → pollutes the delta. Mitigate with (C) or
  tolerate (own-pid asserts degrade to false-fail on foreign-pid collision,
  rare in practice).
- "Second test flakes standalone" evidence: if the standalone flake's true
  cause is the fail-closed probe path (no redis-cli / busy socket → skip →
  orphan survives), delta-scoping turns it into a *correct* test failure —
  good (real product bug now caught) but it will keep failing until (B) or
  an equivalent lands.
- More test churn; the sentinel needs a CLI marker (small production touch,
  can be a print already present or added behind the `--no-dry-run` path).

**Tradeoffs.** Smallest product blast radius (production semantics untouched);
fixes the intent gap (tests prove their own chaos claim); precedented
in-repo (`test_reaper.py` is the exact pattern). Leaves the
phantom-candidate + probe fail-closed warts in place — ambient-state
assertions elsewhere remain vulnerable to them. Does not, by itself, remove
the tests from the hostile 45-min leg.

**Best-fit-if.** This is the *necessary* component regardless of approach —
the current assertions cannot fail on the tests' own spawn. Take it if the
goal is deterministic tests that preserve intent with minimal production
risk, and pair it with (C) to remove the hostile-leg exposure.

---

## Approach B — Fix `discover()`/`reap()` semantics (production side)

**Description.** Three production changes. (1) **Dead-pid classification:**
in `_classify`/`_cooldown_check`, when the registry pidfile records a dead
pid, classify the dir as `stale_socket` (or a new `stale` class) instead of
`candidate` — phantom candidates (classified but unreapable, because `reap()`
liveness-first skips them) disappear from `discover()` output entirely.
(2) **Probe robustness:** make the raw-RESP probe retry-once with short
backoff before the fail-closed skip (transient mid-sweep busy sockets are the
likely ubuntu-24.04 failure), and treat redis-cli as a true fallback rather
than a required path. (3) Optionally: `reap()` may rmtree socket dirs for
`dir_missing` records even when the pid is dead (owning suite ended →
leftover dir is removable by definition, matching `_sweep_stale_residue`).

**Files touched.** `tortoise/embedded_reaper.py` (classification, probe,
`reap()`); `tests/test_reaper.py` (new unit tests: dead-pid dir → stale,
probe retry, dir-gone cleanup); `tests/test_embedded_concurrency.py` (only
the ambient assertions, if kept — likely superseded by (A)).

**Architecture.** Production semantics. `discover()` output becomes honest:
every `candidate` is reapable-in-principle, so "no candidates remaining" is a
meaningful global assertion again. `reap()`'s acted list becomes the source
of truth (already is, per the problem statement's hint). Probe retry bounded
(2 attempts × 0.5s) so the hot sweep path doesn't stall on hundreds of
failures. This is the fix that makes ambient-state assertions *safe to
believe* rather than merely removing them.

**Risks.**
- Blast radius: `discover()`/`reap()` are load-bearing for the sweep path and
  the `only_safe` concurrency guard (#1005, #1115). Reclassifying dead-pid
  dirs changes CLI output and what `reap()` rmtree's — a server mid-restart
  (stale pidfile, live process about to return) could have its dir deleted.
  Needs its own review + test matrix before any chaos-test change.
- Per-record retry adds latency to the sweep path; must stay bounded.
- Touching reaper core to fix a test flake is exactly the "bigger blast
  radius" trap — the fix must be justified on product terms (it is: reaper
  genuinely leaves orphans unreapable on probe failure and reports phantom
  candidates) and shipped behind its own tests.

**Tradeoffs.** Real production value: reaper stops producing unreapable
candidates and stops stranding orphans when a probe times out — fixes the
class of bug, not the instance. But slowest to land, highest review cost,
and it does NOT by itself make the chaos tests deterministic (they still
assert ambient state, blind-kill mid-sweep, and global-pkill). Must be
combined with (A).

**Best-fit-if.** We're willing to fund a production fix that makes the
reaper's contract honest, and we accept sequencing (B after or alongside A).
If the standalone flake trace points at the probe path, (B) is the actual
root-cause fix.

---

## Approach C — Selection-leak fix only (exclude SLOW_FILES from tier-2 half-a)

**Description.** In `python-ci.yml` tier-2 branch (line ~316), filter
`needs.changes.outputs.test_files` against the SLOW_FILES block before
building `$FILES` for half-a — subtract SLOW_FILES in the JSON parse step, or
`grep -v` against the block. Optionally, if only the chaos tests are the
problem (not the whole file), keep the file in fast but `--deselect` the
three chaos tests (or gate them `@pytest.mark.slow` and add the marker to
test-slow's invocation). Shared leak with #1371 — the two fixes must be
coordinated, not double-applied.

**Files touched.** `.github/workflows/python-ci.yml` (tier-2 FILES
construction; possibly the test-slow marker set).

**Architecture.** CI-only. The fast leg (45m cap, hostile: tier-2 runs the
file while `test-slow` runs the same file in parallel) never executes these
tests; they run only in test-slow where they already pass (evidence: same
tests passed in test-slow in the same failing run). Flake becomes
unreachable in the leg where it manifests; the file stays covered.

**Risks.**
- Masking: "second test flakes standalone" — if the standalone flake has a
  real root cause (probe path / phantom candidates), (C) hides it; it can
  still flake test-slow (lower load, maxfail=20 tolerance) or local runs.
- Removing the whole file from fast tier-2 also removes the tier-2 *intent*
  (changed-file full-suite coverage in the fast gate) — acceptable because
  the file is deliberately in SLOW_FILES and runs in test-slow anyway.
- Inline bash+python-in-YAML selection code is fiddly; must not break the
  existing `full=true` path or half-b.

**Tradeoffs.** Cheapest, zero test/product churn, green CI immediately; and
(C) removes the *concurrent double-run* hazard that (A)'s delta snapshot
can't fully defend against. But it treats the symptom (hostile environment),
leaves the ambient-assertion design and the fail-closed probe in place, and
the tests remain time-bombs wherever they run.

**Best-fit-if.** We need the fast leg green now and are landing #1371's
selection fix in the same change; as the stopgap that buys time for (A)/(B).
Not a complete solution on its own.

---

## Approach D — Process-namespace / session isolation (unshare + killpg)

**Description.** Run each chaos test's spawns + reaper CLI in an isolated
process group (`start_new_session=True`) and, on Linux CI (ubuntu-24.04),
in a user/mount namespace (`unshare -Urm` via `unshare` CLI wrapper) with a
per-test flat `TMPDIR` under `/tmp`. Cleanup is `killpg(-pgid, SIGKILL)` in
teardown — scoped, no global pkill; namespace teardown guarantees the test's
own redislite trees die even on assertion failure. Local macOS runs fall
back to session groups (namespaces unavailable).

**Files touched.** `tests/test_embedded_concurrency.py` (spawn/teardown
helpers, per-test TMPDIR), `tests/conftest.py` (fixture), possibly a tiny
`unshare` wrapper module.

**Architecture.** OS-level containment: namespace + process group + isolated
TMPDIR makes a test's orphans unreachable by any other test's
`discover()`/`pkill`, and makes teardown deterministic. It is the only
approach that *guarantees* cross-test isolation even under concurrent
suites, because the ambient state the tests collide over (shared /tmp,
pgrep-visible processes) is partitioned per test.

**Risks.**
- Complexity/cost: namespaces are Linux-only (CI-only guarantee; macOS dev
  behaves differently → CI-only test divergence), `unshare -Urm` can be
  blocked by runner seccomp profiles, slower test startup, hard to debug
  when the namespace setup itself fails.
- Overkill for the actual defect: the bug is assertion design + selection
  leak, not a leaky kernel. Doesn't fix phantom-candidate/probe product
  issues; doesn't fix the ambient-assertion design (tests would still need
  (A)'s own-pid assertions to be meaningful, though the namespace makes even
  ambient asserts mostly deterministic).
- Adding isolation mechanics can *introduce* flakiness (namespace teardown
  races, unshare availability).

**Tradeoffs.** Strongest isolation guarantees; highest complexity and
maintenance cost; narrow platform coverage. Not the first tool for an
assertion-design bug.

**Best-fit-if.** A last resort: if evidence shows persistent cross-suite
leakage (test + test-slow concurrent runs of the same file) that delta
snapshots can't tame, and (A)+(C) prove insufficient.

---

## Honest evaluation (no winner)

- **(A) is likely necessary regardless.** The tests cannot fail on their own
  spawn today — `candidates[0]` and the global "no candidates remaining" are
  ambient by construction. Whatever else lands, own-pid/`acted`-list
  assertions are the only way the tests prove their stated intent. The
  pattern is already proven in-repo (`test_reaper.py`).
- **(C) is the cheapest high-leverage move and is shared with #1371** — the
  two selection fixes should land together. It makes the flake unreachable
  in the leg where it manifests and removes the concurrent double-run that
  even (A) can't fully defend against. It does not address the standalone
  flake.
- **(B) is the real root-cause candidate for the standalone flake** (fail-closed
  probe on ubuntu-24.04 → orphan survives → ambient assert fails) and has
  genuine production value (phantom candidates are a real reaper wart:
  `reap()` can never clear a dead-pid dir classified `candidate`). It has
  the biggest blast radius and should ship behind its own tests — evaluate
  its scope separately from the test fix.
- **(D) is disproportionate** to an assertion-design + selection bug; keep it
  as a fallback only if cross-suite leakage is demonstrated after (A)+(C).

Recommended sequencing if pushed for one: **C now (with #1371) → A (deterministic
tests) → B (production semantics, own test suite) → D only on evidence.**

## Comparison matrix

| | A delta tests | B reaper semantics | C selection fix | D namespaces |
|---|---|---|---|---|
| Fixes test intent | Yes (own-pid) | Partial (makes ambient honest) | No | No (isolates) |
| Kills global pkill | Yes | No | No | Yes (killpg) |
| Kills blind SIGKILL race | Yes (sentinel/robust assert) | No | No | No |
| Fixes phantom candidates | No | Yes | No | No |
| Fixes probe fail-closed | No | Yes | No | No |
| Removes hostile-leg exposure | No | No | Yes | No |
| Product blast radius | None | High | None | None |
| CI-only guarantee | No | No | Yes | Linux-only |
| Precedented in-repo | Yes (`test_reaper.py`) | Partially | No | No |
