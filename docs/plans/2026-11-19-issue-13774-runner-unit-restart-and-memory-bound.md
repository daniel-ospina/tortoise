<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/13774#issuecomment-7011594614 -->
<!-- plan-review: cycles=3, status=fixes-applied-at-cap, version=2.3.0 -->

# Runner unit: a death is retried, and one unit's memory is bounded — Implementation Plan (v3)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
> v2 applied PLAN-VERIFY round 1; v3 applies round 2 (8 findings). Fixes are marked **[R2-fix ...]**.

**Goal:** make a self-hosted runner's abnormal death recoverable and one runner's memory bounded, by having
`tools/install-ci-runner-x64.sh` write (and re-assert) a systemd drop-in on every runner unit it installs, with
**six** keys: `[Unit] StartLimitIntervalSec=`, `[Unit] StartLimitBurst=`, `[Service] Restart=on-failure`,
`[Service] RestartSec=`, `[Service] OOMPolicy=stop`, `[Service] MemoryMax=` (the last **omitted** when the sizing
sentinel is 0 — see R9).

**Team:** organisation-design-team

**Architecture:** the installer keeps delegating unit creation to GitHub's `svc.sh` and adds **one** step,
executed **immediately after the `fi` at `:1691` — outside the `[ ! -f "$UNIT_DIR/$(unit_for "$name")" ]` guard**
**[R2-fix: P1 — "between install and start" is positionally ambiguous; as an indented line it lands INSIDE the
guard, which is the branch this fleet's already-installed boxes never take]**, writing
`<UNIT_DIR>/<unit>.d/10-restart-and-memory.conf` idempotently (`cmp` before `install`, `daemon-reload` only on
change).

⛔ **`daemon-reload` applies `MemoryMax` to an ALREADY-RUNNING unit immediately** — measured on pi-cell-4
(systemd 255.4): a running unit's runtime `memory.max` flipped `max` → `1073741824` with `MainPID` unchanged, no
restart **[R2-fix: P1 — v2's "applies at its next start" was FALSE]**. Two consequences, both designed for:
(a) the bound is live from the install, which is what we want; (b) **a too-tight value kills in-flight jobs at
install time**, so the default must sit ABOVE the worst case we have (it does: 8 G) and the `0` sentinel must
never become `MemoryMax=0G` (R9). For an already-serving runner this is the only moment the limit is applied, and
that is deliberate: a serving runner is never restarted by this installer (R3).

**What this does NOT bound:** one unit only. Docker containers run in `docker-*.scope`, a SIBLING of the unit's
cgroup (confirmed on this box: `memory.max=2147483648` on the docker scope, `CgroupDriver=systemd`/v2), so the
box-level arbiter still chooses between co-tenants. The bound narrows the victim set; it does not close the
aggregate. Stated here, in R4 and in the runbook step.

## Integration Surface Map

| Surface | What it is | Test layer | Why this layer |
|---|---|---|---|
| install loop, after the `fi` at `:1691` | where the drop-in write is called (outside the guard) | installer unit test (hermetic `CI_RUNNER_UNIT_DIR` seam, `:158`) | the only observable place without touching `/etc` |
| the **guard-false** branch (`svc.sh install` skipped, unit already present) | the branch every already-installed box takes | own arm: unit pre-created, assert drop-in written + `svc.sh install` ABSENT from the argv log | step 4 of v2 was negative-only and the mutation "indent into the guard" survived all arms **[R2-fix]** |
| `<UNIT_DIR>/<unit>.d/10-restart-and-memory.conf` | the artifact systemd reads | content + **section-position** assertion | a key in the wrong section is silently ignored |
| `systemctl daemon-reload` | applies the drop-in (including to a live unit) | `sudo`-stub argv log **and** a `systemctl` stub on PATH (the sudo stub execs its argv, `tests/…:2376`) | must fire on change, not on every pass |
| uninstall loop (`:1515-1642`) **and** the orphan-unit branch (`:1558-1561`) | remove the unit today, the drop-in never | new arms: our file removed, **foreign files survive**, the printed orphan remedy names it **[R2-fix: P1/P2]** | a surviving drop-in governs a future unit of the same name |
| `docs/ops/ci-runner-x64.md` (map bullet at `:534`) | operator-facing refs | existing pin, new section placed **below** the map | the pin picks the FIRST bullet carrying a ref (`tests/…:2773`) |
| converger (`runner_autoscale.py`) | NOT touched | — | it counts listeners, not units |

**Tech Stack:** bash (installer), pytest (tests), systemd 255 (cgroup v2).

## Failure Modes

- `StartLimitIntervalSec` under `[Service]` → interval ignored, 10 s window, 20 restarts/10 s → positional assertion.
- Non-idempotent write → rewrite + reload every pass → two-run arm (one reload, unchanged content).
- Too-tight bound applied to a live unit at install → jobs killed by US → default above the recorded worst case +
  R9's sentinel guard + the value validator (R11).
- Runner wrapper exits **0** for listener codes 0/1/5 and unknown codes
  (`/opt/gh-runner-x64/run-helper.sh.template:38-46` for codes 0 and 1, `:75-80` for code 5 and unknown) → `on-failure` does not retry, and that is correct (code 5
  is the engine's own "no retry needed"); the unit goes `inactive` → **accepted residual**, documented in the
  runbook + R10, not claimed as covered.

---

## Implementation steps (each with the check that proves it)

1. **Fixture** — a new install-mode helper: `CI_RUNNER_UNIT_DIR` pinned, `sudo` stub (logs argv, execs), a
   `systemctl` stub earlier on PATH, `.runner` pre-existing (so no download), `svc.sh` stubs,
   **`--fallback off`** (a `gh` stub that echoes makes `fallback_engaged` die exit 2 at `:533`), and
   **`CI_RUNNER_MEM_PER_RUNNER_GB="2"` explicitly** — `_NEUTRAL_HEADROOM` pins it to `0` for every arm
   (`tests/…:168`), which under R9 OMITS `MemoryMax=` and would make step 2's six-key assertion unpassable by
   construction. Step 8's arm overrides it to `0` deliberately.
   **[cycle-3 fixes: the fixture gap, and R9's collision with the shared headroom dict]**.
2. **Test** — absent-unit arm: `svc.sh` stub writes the unit; assert drop-in path, **all six keys**, both `[Unit]`
   keys' POSITIONS, and that `10-restart-and-memory.conf` is installed **before** `svc.sh start` in the argv log.
3. **Test** — **guard-false arm** (the fleet's real branch): pre-create `<UNIT_DIR>/<unit>`; assert rc=0, the
   drop-in written, `svc.sh start` ran, and `svc.sh install` NEVER appears in the argv log **[R2-fix: P1]**.
4. **Test** — idempotency: two runs → identical content, exactly one `daemon-reload`.
5. **Test** — no-restart: no `systemctl restart|stop` for a pre-existing (serving) runner.
6. **Test** — failure: a failing drop-in write exits 3 and leaves install + registration intact.
7. **Test** — uninstall: our file is removed, the `.d/` dir is removed only via
   `rmdir --ignore-fail-on-non-empty`, and a **foreign file** in `.d/` (a stand-in for this box's hand
   `restart.conf`) SURVIVES **[R2-fix: P1 — `rm -rf .d/` would delete the hand drop-in keeping four runners
   restarting]**; plus the **orphan-unit branch** remedy names our file **[R2-fix: P2]**.
8. **Test** — sentinel: `CI_RUNNER_MEM_PER_RUNNER_GB=0` → the `MemoryMax=` key is **absent** (0 means "no sizing",
   never "no memory") **[R2-fix: P1 — `MemoryMax=0G` is a value that ONLY LOOKS like a bound]**.
9. **Test** — validator: a non-integer `CI_RUNNER_MEM_MAX_FACTOR` and a malformed `CI_RUNNER_START_LIMIT_INTERVAL`
   each refuse with exit 1 **[R2-fix: P2]**.
10. **Implement** — knobs next to the sizing knobs, each with its rationale comment; add the three INTEGER knobs to
    the existing validator list (`:268-273`, which fails on non-digits) and validate the interval separately with a
    timespan pattern (`^[0-9]+(s|min|h|d)+$`) because `1h` is not an integer **[R2-fix: P2]**.
11. **Implement** — `write_unit_dropin()` near `unit_for()`, called **immediately after the `fi` at `:1691`**;
    **not** in `--repair-labels`, which never installs and may run against a REMOTE install
    (`:524`, `:1244`) **[R2-fix: P2 — v2 named a non-existent `--repair` path]**.
12. **Implement** — dry run prints the drop-in path and the keys it would write.
13. **Implement** — uninstall loop, guarded and VERIFIED: `[ -d "$UNIT_DIR/$unit.d" ] && { sudo rm -f
    "$UNIT_DIR/$unit.d/10-restart-and-memory.conf"; sudo rmdir --ignore-fail-on-non-empty "$UNIT_DIR/$unit.d";
    [ -e "$UNIT_DIR/$unit.d/10-restart-and-memory.conf" ] && { say "WARNING: could not remove the drop-in for
    $unit — remove it by hand"; rc=3; }; }` — the `[ -d ]` guard is NOT what makes rc=3 unreachable, and
    an earlier draft of this plan said otherwise: every path through the function ends in `return 0`
    (measured — delete the guard and the uninstall still reports 0; delete the guard AND the
    `2>/dev/null || true` and it STILL reports 0), so the guard only saves a `sudo` on a target with
    nothing of ours, while `2>/dev/null || true` swallows `rmdir`'s ENOENT for a `.d/` that is already gone; and the verify step is
    what stops the same flag from swallowing our own failed `rm`. The orphan-unit branch (`:1558-1561`) does the same
    and its printed remedy (`sudo rm -f $UNIT_DIR/$u`) is extended to name the drop-in
    **[cycle-3 fix: `ENOENT` is not ignored by that flag; and add the no-`.d/` case to step 7's arm]**.
14. **Docs** — `docs/ops/ci-runner-x64.md`, section placed **below the map bullet**: the six keys, the `[Unit]`
    trap, `daemon-reload` applying the bound to a live unit (with the measured line), the multiplier, the
    aggregate caveat, the accepted `exit 0` death class, the drop-in ordering rule (a hand `restart.conf` sorts
    AFTER `10-…conf` and wins for shared keys — the one-time removal on pi-cell-4 must be recorded), and the
    re-measured line map.
15. **Prove** — new arms + whole file; mutation per branch; paste output.

## Review Scope Contract

**Class:** ops-infra

**Risk surface** — what this change can break, each with the check that would catch it:

- R1 a key in the wrong SECTION → systemd ignores it silently → positional assertion (step 2).
- R2 non-idempotent write → rewrite + reload every pass → step 4.
- R3 installing over a serving runner restarts it → step 5 (no `restart`/`stop` anywhere in the argv log).
- R4 the bound is **live on the running unit as soon as `daemon-reload` runs** (measured), so a too-tight value
  kills in-flight jobs; and it bounds ONE unit only — docker scopes are outside the cgroup, so the box arbiter
  remains → step 2 asserts the default and its **direction** (≥ the recorded worst case, recorded in the test's
  own comment), R9 guards the sentinel, R11 the input, and the aggregate caveat is in the contract AND a runbook
  step.
- R5 drop-in failure after registration half-installs a box → step 6 (rc=3, install + registration intact).
- R6 runbook line-map drift → the existing pin, new section below the map, stable bullet anchor.
- R7 the drop-in outlives an uninstall and governs a future unit of the same name → step 7 (our file removed,
  with a post-removal existence check rather than a swallowed exit status), and the orphan-unit branch is covered
  too; a target that never had a `.d/` dir is an arm of its own (the flag's `ENOENT` behaviour).
- R8 a hand-written drop-in sorts after ours and wins for shared keys → documented ordering rule + step 7's
  foreign-file-survives assertion proves we do not "clean" it away.
- R9 `MEM_PER_RUNNER_GB=0` is a REAL seam (the test harness exports it) and would compute `MemoryMax=0G`, which
  is a value that only LOOKS like a bound → the key is OMITTED for 0 → step 8.
- R10 the wrapper's `exit 0` deaths (codes 0/1/5, unknown) are not retried by `on-failure` → accepted, documented
  residual; `always` would restart session-conflict in a loop.
- R11 the new knobs are unvalidated by the existing digits-only validator, and one is a systemd TIMESPAN → the
  integer knobs join `:268-273`'s list, the interval gets its own pattern → step 9.

**Out of scope — must NOT be raised** (note, file, do not chase): changing `MEM_PER_RUNNER_GB`/`MEM_PER_RUNNER_MB`;
the runner's job-loss reporting latency (#10591); Docker/FalkorDB sidecar memory; per-cell CPU ceilings; units
installed by any path other than this installer; the `exit 0` death class's own fix.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared surface.

## Corrections recorded after the build (so a later lane does not inherit a refuted claim)

- ⛔ **`MemoryMax=0G` does NOT "admit nothing" and does NOT kill a runner.** MEASURED on systemd 255: a
  throwaway unit with `MemoryMax=0G` + `daemon-reload` reads back `MemoryMax: infinity` and cgroup
  `memory.max` = `max`. The decision to OMIT the key for a zero sizing unit stands, and its reason is
  narrower and true: a written `0G` reads as a bound to every later reader while the omission says
  plainly that none is set. (The code, the runbook and the test docstrings were corrected in the review
  round; this note is the same correction in the plan.)
- **The timespan validator is a split magnitude/suffix, not a glob.** The first version's `[0-9]*s`
  admitted `30sm`/`30secs`, which systemd DROPS — so `RestartSec` fell back to 100 ms and the burst
  burned out in seconds. A glob is not a validation.
- **`MEM_MAX_GB` is computed once with `10#`** (a leading zero turned `$(( 08 * 4 ))` into an octal
  error that silently dropped the bound), and a positive sizing unit that yields a non-positive product
  is REFUSED.
- **The headroom coercion is fixed here, at the root, rather than deferred.** This was recorded as
  "not fixed here, filed instead": the pre-existing headroom gates
  (`mem_need_gb=$(( COUNT * MEM_PER_RUNNER_GB + MEM_RESERVE_GB ))` and its fork/disk siblings) coerced
  without `10#`, so a leading zero made `$(( ))` ERROR — and the measured consequence is a LOUD abort:
  `mem_need_gb` is left UNSET and the next line's reference to it is a `set -u` violation, so the
  installer exits **1** (usage) with no `REFUSED:` line (bash 5.2.21, this file's own
  `set -uo pipefail`). ⛔ An earlier round of this plan recorded that as "every later refusal silently
  SKIPPED while the run exited 0"; that is **refuted** and must not be reinstated. What the deferral
  actually cost was the memory refusal never being reached and a wrong exit code (a usage error where
  the contract promises a guard refusal), not a hidden pass. The validated sizing knobs are now
  rebuilt in base 10 ONCE, after the digits/length checks — one place, every consumer — instead of
  hardening four `$(( ))` sites one review at a time. `CI_RUNNER_MEM_AVAILABLE_GB` was added as a seam
  so the refusal can be observed at all, and `test_an_unreadable_memory_reading_is_refused_rather_than_sized_against`
  pins it. ⛔ IT PINNED NOTHING UNTIL THE REVIEW FOUND IT: the seam was set only to a NUMERIC value
  (`1024` file-wide, plus one leading-zero headroom case), so the `''|*[!0-9]*` arm could be deleted
  with no red while this sentence and the installer's comment both claimed an arm existed.
